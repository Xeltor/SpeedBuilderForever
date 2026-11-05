import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion import Entry
from speedbuilder import learn_batch


def ready(state, session=1, key="setup"):
    return Entry(session, 0, 0, state, key, (30000, 30000, 40000, 40000), "")


class Capture:
    frame_number = 0

    def __init__(self, frames):
        self.frames = iter(frames)
        self.sent = []

    def poll(self):
        self.frame_number += 1
        self.focus, self.marker = next(self.frames, (True, None))

    def start(self, shortcut, track_pointer, send):
        assert send

    def focused_window(self, title):
        return title if self.focus else None

    def identity(self, window):
        return window

    def size(self, window):
        return [960, 540]

    def fresh_frame(self):
        return True

    def snapshot(self, window, region):
        return 0, 0, self.marker

    def keycode(self, key):
        return key

    def tap(self, keys, title, window_id):
        self.sent.extend(keys)
        return True


class ReloadTests(unittest.TestCase):
    typed = ["slash", *"sbf", "space", *"reload"]

    def reload_helper(self, capture, backend="wayland"):
        args = SimpleNamespace(reload_helper=True, send=True, backend=backend)
        profile = {"window": "WoW", "window_size": [960, 540], "points": [[470, 260], [470, 280]]}
        clock = iter(number / 100 for number in range(100000))
        with patch("speedbuilder.time.sleep"), patch("speedbuilder.time.monotonic", side_effect=lambda: next(clock)), \
                patch("speedbuilder.decode_marker", side_effect=lambda marker, *_: marker):
            with contextlib.redirect_stdout(io.StringIO()):
                learn_batch(capture, args, profile)

    def test_confirms_whole_command_twice_and_a_new_helper_session(self):
        for backend in ("x11", "wayland"):
            with self.subTest(backend=backend):
                frames = [(True, None)] * len(self.typed) + [(True, ready(12))] * 2
                frames += [(False, None), (True, ready(1, 2))]
                capture = Capture(frames)
                self.reload_helper(capture, backend)
                self.assertEqual(capture.sent, [*self.typed, "Return"])
                self.assertEqual(capture.frame_number, len(frames))

    def test_older_helper_never_receives_reload_keystrokes(self):
        capture = Capture([(True, ready(1, key="reload"))])
        with self.assertRaisesRegex(ValueError, "manual /reload"):
            self.reload_helper(capture)
        self.assertEqual(capture.sent, [])

    def test_unexpected_or_missing_chat_text_is_never_submitted(self):
        for marker in (None, ready(6)):
            with self.subTest(marker=marker):
                capture = Capture([(True, None)] * len(self.typed) + [(True, marker)] * 500)
                with self.assertRaisesRegex(ValueError, "Could not confirm"):
                    self.reload_helper(capture)
                self.assertEqual(capture.sent, self.typed)

    def test_unstable_command_is_not_submitted(self):
        capture = Capture([(True, None)] * len(self.typed)
                          + [(True, marker) for marker in (ready(12), None) for _ in range(1)] * 250)
        with self.assertRaisesRegex(ValueError, "Could not confirm"):
            self.reload_helper(capture)
        self.assertEqual(capture.sent, self.typed)

    def test_focus_loss_while_typing_aborts_without_enter(self):
        capture = Capture([(True, None), (False, None)])
        with self.assertRaisesRegex(ValueError, "lost focus"):
            self.reload_helper(capture)
        self.assertEqual(capture.sent, ["slash"])

    def test_same_session_cannot_confirm_reload(self):
        capture = Capture([(True, None)] * len(self.typed) + [(True, ready(12))] * 2
                          + [(True, ready(1))] * 4000)
        with self.assertRaisesRegex(ValueError, "Could not confirm"):
            self.reload_helper(capture)
        self.assertEqual(capture.sent, [*self.typed, "Return"])
