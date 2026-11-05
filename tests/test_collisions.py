import contextlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from Xlib import X

from catalog import active_bindings, merge_batch, pick_refinement
from profiles import Profiles
from speedbuilder import X11, read_profile, run_rotation, write_profile


BASE = "112233:aabbcc"
POINTS = [[10, 10], [10, 14]]
EXTRA = (10, 12)
RED, BLUE = "ff0000", "0000ff"
GRID = (EXTRA, (12, 12))


def spell(action, key):
    return SimpleNamespace(action=action, name=f"Spell {action}", key=key)


def learned_profile():
    initial = {"window": "WoW", "window_size": [100, 100], "points": POINTS, "bindings": {}}
    samples = [(spell(1, "1"), BASE, (RED, BLUE)), (spell(2, "2"), BASE, (BLUE, RED))]
    return merge_batch(initial, samples, GRID, str)[0]


class CollisionTests(unittest.TestCase):
    def test_prefers_one_capture_even_if_it_needs_two_extra_pixels(self):
        grid = (EXTRA, (10, 11), (11, 12))
        observations = [
            ("1", (RED, RED, "111111")),
            ("2", (RED, BLUE, "222222")),
            ("3", (BLUE, RED, "333333")),
            ("4", (BLUE, BLUE, "444444")),
        ]
        points, matches = pick_refinement(observations, grid, POINTS)
        self.assertEqual(points, list(grid[:2]))
        self.assertEqual(len(matches), 4)

    def test_identical_icons_remain_blocked_and_edits_keep_the_rules(self):
        profile = learned_profile()
        profile["spells"]["1"]["key"] = "3"
        profile["spells"]["2"]["key"] = ""
        self.assertEqual(active_bindings(profile), {f"{BASE}:{RED}": "3"})

        samples = [(spell(1, "1"), BASE, (RED, BLUE)), (spell(2, "2"), BASE, (RED, BLUE))]
        refreshed, _ = merge_batch(profile, samples, GRID, str)
        self.assertEqual(active_bindings(refreshed), {})
        self.assertEqual(refreshed["spells"]["1"]["key"], "3")

        del refreshed["spells"]["2"]
        self.assertEqual(active_bindings(refreshed), {})

    def test_recalibration_preserves_keys_and_blocks_stale_shared_samples(self):
        original = learned_profile()
        original["bindings"]["010203:040506"] = "4"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            profiles = Profiles(SimpleNamespace(default_profile=lambda _: path, write_profile=write_profile))
            profiles.save(path, original)
            self.assertFalse(profiles.save_calibration(path, original, "WoW", [100, 100], POINTS))
            self.assertEqual(read_profile(path), original)

            points = [[20, 20], [20, 24]]
            self.assertTrue(profiles.save_calibration(path, original, "WoW", [100, 100], points))
            updated = read_profile(path)
            self.assertEqual(updated["points"], points)
            self.assertEqual(updated["spells"], original["spells"])
            self.assertEqual(updated["collisions"][BASE], {"owners": ["1", "2"], "points": [], "matches": {}})
            self.assertEqual(active_bindings(updated), original["bindings"])
            with self.assertRaisesRegex(ValueError, "changed during calibration"):
                profiles.save_calibration(path, original, "WoW", [100, 100], POINTS)
            self.assertEqual(read_profile(path), updated)

    def test_x11_reuses_strip_and_rejects_a_changed_base(self):
        desktop = X11.__new__(X11)
        desktop.formats = {24: SimpleNamespace(bits_per_pixel=32, scanline_pad=32)}
        desktop.visuals = {0: SimpleNamespace(visual_class=X.TrueColor, red_mask=0xff0000,
                                               green_mask=0xff00, blue_mask=0xff)}
        desktop.byte_order = "little"
        rectangles = []
        colors = {(10, 10): "112233", (10, 14): "aabbcc", EXTRA: RED, (12, 12): BLUE}

        def get_image(x, y, width, height, *_):
            rectangles.append((x, y, width, height))
            data = b"".join(bytes.fromhex(colors.get((px, py), "000000"))[::-1] + b"\0"
                            for py in range(y, y + height) for px in range(x, x + width))
            return SimpleNamespace(data=data, depth=24, visual=0)

        window = SimpleNamespace(get_image=get_image)
        self.assertEqual(desktop.sample(window, POINTS, {BASE: [EXTRA]}), f"{BASE}:{RED}")
        self.assertEqual(rectangles, [(10, 10, 1, 5)])
        rectangles.clear()
        self.assertEqual(desktop.sample(window, POINTS, {BASE: [GRID[1]]}), f"{BASE}:{BLUE}")
        self.assertEqual(rectangles, [(10, 10, 1, 5), (10, 10, 3, 5)])

        def changed_image(x, y, width, height, *options):
            if width > 1:
                colors[(10, 10)] = "ffffff"
            return get_image(x, y, width, height, *options)

        window.get_image = changed_image
        self.assertIsNone(desktop.sample(window, POINTS, {BASE: [GRID[1]]}))

    def test_switch_with_same_base_requires_two_frames_for_the_new_spell(self):
        profile = learned_profile()
        frames = iter([f"{BASE}:{color}" for color in (RED, RED, BLUE, BLUE, "000000", "000000", RED)])
        presses = []

        class Desktop:
            frame_number = 0
            toggle_event = False

            def poll(self):
                self.signature = next(frames)
                self.frame_number += 1
                self.toggle_event = self.frame_number == 1

            def focused_window(self, title):
                return title

            def identity(self, window):
                return window

            def size(self, window):
                return profile["window_size"]

            def sample(self, window, points, refinements):
                if refinements != {BASE: [list(EXTRA)]}:
                    raise AssertionError("The collision points were not passed to capture")
                return self.signature

            def tap(self, chord, title, window):
                presses.append((self.frame_number, chord))
                return True

        args = SimpleNamespace(send=True, toggle="F8", interval=100)
        with patch("speedbuilder.time.sleep"), patch("speedbuilder.time.monotonic", side_effect=range(20)):
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(StopIteration):
                run_rotation(Desktop(), args, profile, active_bindings(profile), None)
        self.assertEqual(presses, [(2, "1"), (4, "2")])
