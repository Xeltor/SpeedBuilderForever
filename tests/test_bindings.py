import contextlib
from copy import deepcopy
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from catalog import active_bindings, merge_batch
from companion import Entry
from speedbuilder import learn_batch, read_profile, write_profile

BASE = '112233:aabbcc'
BOUNDS = (30000, 30000, 40000, 40000)


def profile(key='1'):
    return {'window': 'WoW', 'window_size': [960, 540], 'points': [[470, 260], [470, 280]],
            'bindings': {}, 'spells': {'403': {'name': 'Lightning Bolt', 'key': key, 'colors': [BASE]}}}


def sample(key):
    return Entry(1, 1, 1, 403, key, BOUNDS, 'Lightning Bolt'), BASE, ()


class BindingRefreshTests(unittest.TestCase):
    def test_refresh_replaces_wrong_keys_and_fills_previous_blanks(self):
        for previous in ('1', ''):
            with self.subTest(previous=previous):
                updated, _ = merge_batch(profile(previous), [sample('C2')], (), str, refresh_keys=True)
                self.assertEqual(updated['spells']['403']['key'], 'Ctrl+2')
                self.assertEqual(active_bindings(updated), {BASE: 'Ctrl+2'})

    def test_missing_unsupported_or_ambiguous_binding_disables_old_key(self):
        for keys in ([''], ['MOUSEWHEELUP'], ['1', '2']):
            with self.subTest(keys=keys):
                updated, _ = merge_batch(profile(), [sample(key) for key in keys], (), str, refresh_keys=True)
                self.assertEqual(updated['spells']['403']['key'], '')
                self.assertEqual(active_bindings(updated), {})

    def test_rejected_keycode_disables_old_key(self):
        def reject(_):
            raise ValueError('Unavailable keycode')
        updated, _ = merge_batch(profile(), [sample('2')], (), reject, refresh_keys=True)
        self.assertEqual(active_bindings(updated), {})

    def test_keep_keys_preserves_manual_assignments_and_cleared_keys(self):
        for key in ('3', ''):
            with self.subTest(key=key):
                updated, _ = merge_batch(profile(key), [sample('2')], (), str, refresh_keys=False)
                self.assertEqual(updated['spells']['403']['key'], key)

    def test_refresh_supersedes_legacy_colors_without_changing_uncaptured_spells(self):
        previous = profile()
        previous['bindings'][BASE] = '9'
        previous['spells']['20594'] = {'name': 'Stoneform', 'key': '8', 'colors': ['010203:040506']}
        original = deepcopy(previous)
        updated, _ = merge_batch(previous, [sample('2')], (), str, refresh_keys=True)
        self.assertNotIn(BASE, updated['bindings'])
        self.assertEqual(updated['spells']['20594'], previous['spells']['20594'])
        self.assertEqual(previous, original)

    def test_actual_batch_refreshes_by_default_and_honors_keep_keys_on_both_backends(self):
        class Capture:
            frame_number = 0
            def __init__(self):
                self.frames = iter([sample('2')[0], sample('2')[0], Entry(1, 0, 1, 1, '', BOUNDS, '')])
            def start(self, shortcut, track_pointer, send):
                if send: raise AssertionError('This test must not send keys')
            def poll(self):
                self.frame_number += 1
                self.marker = next(self.frames)
            def focused_window(self, title): return title
            def identity(self, window): return window
            def size(self, window): return [960, 540]
            def fresh_frame(self): return True
            def snapshot(self, window, region): return 0, 0, self.marker
            def sample(self, window, points): return BASE
            def keycode(self, key): return key

        for backend in ('x11', 'wayland'):
            for keep in (False, True):
                with self.subTest(backend=backend, keep=keep), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'profile.json'
                    previous = profile()
                    write_profile(path, previous)
                    args = SimpleNamespace(reload_helper=False, send=False, backend=backend, config=path)
                    if keep: args.keep_keys = True
                    with patch('speedbuilder.time.sleep'), \
                            patch('speedbuilder.decode_marker', side_effect=lambda marker, *_: marker), \
                            contextlib.redirect_stdout(io.StringIO()):
                        learn_batch(Capture(), args, previous)
                    self.assertEqual(read_profile(path)['spells']['403']['key'], '1' if keep else '2')
