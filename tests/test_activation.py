from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from speedbuilder import start_capture


class ActivationTests(unittest.TestCase):
    def test_waits_for_capture_setup_and_game_focus(self):
        desktop = Mock()
        desktop.focused_window.side_effect = [None, "game"]

        with patch("speedbuilder.time.sleep"):
            start_capture(desktop, SimpleNamespace(activate_window=True), "WoW", send=True)

        self.assertEqual(desktop.mock_calls, [
            call.start(None, track_pointer=False, send=True),
            call.request_activation("WoW"),
            call.poll(),
            call.focused_window("WoW"),
            call.poll(),
            call.focused_window("WoW"),
        ])

    def test_refused_focus_aborts_capture_startup(self):
        desktop = Mock()
        desktop.focused_window.return_value = None

        with patch("speedbuilder.time.sleep"), patch("speedbuilder.time.monotonic", side_effect=[0, 1, 2]):
            with self.assertRaisesRegex(ValueError, "could not bring 'WoW' to the front"):
                start_capture(desktop, SimpleNamespace(activate_window=True), "WoW")

    def test_cli_default_leaves_focus_alone(self):
        desktop = Mock()
        start_capture(desktop, SimpleNamespace(activate_window=False), "WoW")
        self.assertEqual(desktop.mock_calls, [call.start(None, track_pointer=False, send=False)])
