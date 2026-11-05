import contextlib
import io
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from Xlib import error

from speedbuilder import X11, run_rotation
from wayland import Wayland

SIG = '112233:aabbcc'
PROFILE = {'window': 'WoW', 'window_size': [100, 100], 'points': [[10, 10], [10, 14]], 'bindings': {SIG: '1'}}


class Finished(Exception):
    pass


class Capture:
    def __init__(self, backend, frames):
        self.backend = X11.__new__(X11) if backend == 'x11' else Wayland.__new__(Wayland)
        self.native = backend == 'wayland'
        self.frames = iter(frames)
        self.step = self.frame_number = 0
        self.presses = []

    def poll(self):
        try:
            self.frame = next(self.frames)
        except StopIteration:
            raise Finished()
        self.step += 1
        self.frame_number = self.frame.get('number', self.step)
        self.backend.focus_generation = self.frame.get('generation', 1)
        wid = self.frame.get('window', 101)
        self.window = ({'id': wid} if self.native else SimpleNamespace(id=wid)) if wid is not None else None
        self.toggle_event = self.frame.get('toggle', False)

    def identity(self, window): return self.backend.identity(window)
    def window_identity(self, window): return self.backend.window_identity(window)
    def focused_window(self, title): return self.window
    def size(self, window): return self.frame.get('size', [100, 100])
    def sample(self, window, points, refinements):
        if self.frame.get('failure'):
            raise error.BadWindow.__new__(error.BadWindow)
        return self.frame.get('signature', SIG) if self.frame.get('fresh', True) else None
    def tap(self, chord, title, identity):
        if self.window is None or identity != self.identity(self.window):
            raise AssertionError('Input reached a window without the current focus token')
        self.presses.append(self.step)
        return True


class FocusTests(unittest.TestCase):
    def run_frames(self, backend, frames, *, send=True, mouse=False):
        capture = Capture(backend, frames)
        monitor = SimpleNamespace(presses=lambda: capture.frame.get('mouse', 0)) if mouse else None
        output = io.StringIO()
        args = SimpleNamespace(send=send, toggle='F8', interval=100)
        with patch('speedbuilder.time.sleep'), \
                patch('speedbuilder.time.monotonic', side_effect=lambda: capture.frame.get('time', capture.step)), \
                contextlib.redirect_stdout(output), self.assertRaises(Finished):
            run_rotation(capture, args, PROFILE, {SIG: (10,)}, monitor)
        return capture.presses, output.getvalue()

    def check_both(self, frames, expected, **kwargs):
        for backend in ('x11', 'wayland'):
            with self.subTest(backend=backend):
                presses, output = self.run_frames(backend, frames, **kwargs)
                self.assertEqual(presses, expected)
                yield output

    def test_focus_return_resumes_only_after_two_new_matching_frames(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'window': None}, {'generation': 2}, {'generation': 2}]
        for output in self.check_both(frames, [2, 6]):
            self.assertEqual(output.count('Suspended (game lost focus).'), 1)
            self.assertIn('On (game regained focus).', output)

    def test_initially_paused_stays_paused_across_focus_changes(self):
        list(self.check_both([{}, {'window': None}, {'generation': 2}, {'generation': 2}], []))

    def test_manual_pause_stays_paused_after_return(self):
        frames = [{'toggle': True}, {}, {'toggle': True}, {'window': None}, {'generation': 2}, {'generation': 2}]
        list(self.check_both(frames, [2]))

    def test_keyboard_pause_while_away_cancels_automatic_resume(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'window': None, 'toggle': True}, {'generation': 2}, {'generation': 2}]
        list(self.check_both(frames, [2]))

    def test_toggle_on_return_pauses_instead_of_rearming(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'generation': 2, 'toggle': True}, {'generation': 2}]
        list(self.check_both(frames, [2]))

    def test_replacement_window_disarms_until_explicitly_rearmed(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'window': 202, 'generation': 2},
                  {'window': 202, 'generation': 2}, {'generation': 3}, {'generation': 3}]
        for output in self.check_both(frames, [2]):
            self.assertIn('Off (game window changed).', output)

    def test_resize_on_return_disarms_even_if_size_is_later_restored(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'generation': 2, 'size': [120, 100]},
                  {'generation': 2}, {'generation': 2}]
        for output in self.check_both(frames, [2]):
            self.assertIn('Off (game window size changed).', output)

    def test_capture_failure_cancels_resume(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'generation': 2, 'failure': True},
                  {'generation': 2}, {'generation': 2}]
        for output in self.check_both(frames, [2]):
            self.assertIn('Off (game window cannot be captured).', output)

    def test_stale_and_duplicate_frames_cannot_resume_input(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'generation': 2, 'fresh': False},
                  {'generation': 2, 'number': 5}, {'generation': 2, 'number': 5}, {'generation': 2, 'number': 6}]
        list(self.check_both(frames, [2, 7]))

    def test_unknown_recommendations_on_return_are_skipped(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'generation': 2, 'signature': '000000:000000'},
                  {'generation': 2, 'signature': '000000:000000'}, {'generation': 2}, {'generation': 2}]
        list(self.check_both(frames, [2, 7]))

    def test_resume_preserves_minimum_press_interval(self):
        frames = [{'toggle': True, 'time': 0}, {'time': 1}, {'window': None, 'time': 1.01},
                  {'generation': 2, 'time': 1.02}, {'generation': 2, 'time': 1.03}, {'generation': 2, 'time': 1.11}]
        list(self.check_both(frames, [2, 6]))

    def test_mouse_click_that_returns_focus_does_not_toggle_saved_state(self):
        frames = [{}, {'mouse': 1}, {}, {'window': None, 'mouse': 1}, {'generation': 2, 'mouse': 1}, {'generation': 2}]
        list(self.check_both(frames, [3, 6], mouse=True))

    def test_mouse_click_outside_or_on_focus_return_cannot_arm_a_paused_session(self):
        frames = [{}, {'window': None, 'mouse': 1}, {'generation': 2, 'mouse': 1}, {'generation': 2}]
        list(self.check_both(frames, [], mouse=True))

    def test_dry_run_resumes_reporting_without_key_output(self):
        frames = [{'toggle': True}, {}, {'window': None}, {'generation': 2}, {'generation': 2}]
        for output in self.check_both(frames, [], send=False):
            self.assertEqual(output.count(f'{SIG} → 1'), 2)

    def test_wayland_focus_generation_change_between_polls_resets_recognition(self):
        presses, _ = self.run_frames('wayland', [{'toggle': True}, {}, {'generation': 3}, {'generation': 3}])
        self.assertEqual(presses, [2, 4])
