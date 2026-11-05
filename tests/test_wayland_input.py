from unittest import TestCase
from unittest.mock import Mock, call, patch

from wayland import Wayland


class WaylandInputTests(TestCase):
    def desktop(self):
        desktop = Wayland.__new__(Wayland)
        desktop.session_closed = False
        desktop.focused_window = Mock(return_value='game')
        desktop.identity = Mock(return_value=1)
        desktop.key_event = Mock()
        return desktop

    def test_holds_press_before_releasing_in_reverse_order(self):
        desktop = self.desktop()
        with patch('wayland.time.sleep') as hold:
            self.assertTrue(desktop.tap((10, 20), 'WoW', 1))
        hold.assert_called_once_with(.02)
        self.assertEqual(desktop.key_event.call_args_list,
                         [call(10, 1), call(20, 1), call(20, 0), call(10, 0)])

    def test_focus_loss_releases_already_pressed_modifier(self):
        desktop = self.desktop()
        desktop.focused_window.side_effect = ['game', None]
        with patch('wayland.time.sleep'):
            self.assertFalse(desktop.tap((10, 20), 'WoW', 1))
        self.assertEqual(desktop.key_event.call_args_list, [call(10, 1), call(10, 0)])

    def test_hold_failure_still_releases_the_key(self):
        desktop = self.desktop()
        with patch('wayland.time.sleep', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                desktop.tap((10,), 'WoW', 1)
        self.assertEqual(desktop.key_event.call_args_list, [call(10, 1), call(10, 0)])
