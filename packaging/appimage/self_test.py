"""Offline package checks, with an explicit optional Wayland portal check."""
import json
import os
import ctypes
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication
from Xlib import display
import gi

gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gio, Gst, GstVideo
import gui
import installer
from speedbuilder import X11
import speedbuilder
from profiles import Profiles

app = QApplication([])
Gst.init(None)
pipewire = ctypes.CDLL('libpipewire-0.3.so.0')
pipewire.pw_init.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
pipewire.pw_main_loop_new.argtypes = [ctypes.c_void_p]
pipewire.pw_main_loop_new.restype = ctypes.c_void_p
pipewire.pw_main_loop_get_loop.argtypes = [ctypes.c_void_p]
pipewire.pw_main_loop_get_loop.restype = ctypes.c_void_p
pipewire.pw_context_new.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
pipewire.pw_context_new.restype = ctypes.c_void_p
pipewire.pw_context_find_protocol.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
pipewire.pw_context_find_protocol.restype = ctypes.c_void_p
pipewire.pw_context_load_spa_handle.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
pipewire.pw_context_load_spa_handle.restype = ctypes.c_void_p
pipewire.pw_unload_spa_handle.argtypes = [ctypes.c_void_p]
pipewire.pw_context_destroy.argtypes = [ctypes.c_void_p]
pipewire.pw_main_loop_destroy.argtypes = [ctypes.c_void_p]
pipewire.pw_init(None, None)
loop = pipewire.pw_main_loop_new(None)
assert loop, 'Cannot create PipeWire loop'
context = None
try:
    context = pipewire.pw_context_new(pipewire.pw_main_loop_get_loop(loop), None, 0)
    assert context, 'Cannot load bundled PipeWire client configuration'
    assert pipewire.pw_context_find_protocol(context, b'PipeWire:Protocol:Native'), 'Missing PipeWire native protocol module'
    adapter = pipewire.pw_context_load_spa_handle(context, b'video.convert.dummy', None)
    assert adapter, 'Missing PipeWire video conversion module'
    pipewire.pw_unload_spa_handle(adapter)
finally:
    if context:
        pipewire.pw_context_destroy(context)
    pipewire.pw_main_loop_destroy(loop)
for factory in ('pipewiresrc', 'videoconvert', 'appsink', 'videotestsrc'):
    assert Gst.ElementFactory.find(factory), f'Missing GStreamer element: {factory}'
pipeline = Gst.parse_launch('videotestsrc num-buffers=1 ! videoconvert ! video/x-raw,format=RGBx ! appsink name=check sync=false')
try:
    assert pipeline.set_state(Gst.State.PLAYING) != Gst.StateChangeReturn.FAILURE
    assert pipeline.get_by_name('check').emit('try-pull-sample', 5 * Gst.SECOND), 'No synthetic video frame'
finally:
    pipeline.set_state(Gst.State.NULL)
assert all((installer.SOURCE / name).is_file() for name in installer.FILES)
with tempfile.TemporaryDirectory() as directory:
    folder = Path(directory) / '_forever_'
    hekili = folder / 'Interface/AddOns/Hekili'
    hekili.mkdir(parents=True)
    (hekili / 'Hekili.toc').write_text('## Interface: 16001')
    assert installer.install_helper(folder) == 'installed'
    assert installer.install_helper(folder) == 'current'
    engine = SimpleNamespace(**vars(speedbuilder))
    engine.default_profile = lambda _backend: Path(directory)/'configuration/config.json'
    window = gui.SpeedBuilderWindow(engine, Profiles(engine))
    try:
        assert not window.windowIcon().pixmap(32, 32).isNull(), 'Bundled SVG icon cannot be rendered'
        assert window.path is None, 'Self-test accessed a real profile'
    finally:
        window.closing = True
        window.close()
    result = subprocess.run([sys.executable, str(Path(gui.__file__).with_name('speedbuilder.py')), '--help'], capture_output=True, text=True)
    assert result.returncode == 0 and 'learn-batch' in result.stdout, result.stderr
if '--x11' in sys.argv:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QColor, QPainter
    from PySide6.QtWidgets import QWidget
    from Xlib import X
    from Xlib.ext import xtest
    import time
    class TestWindow(QWidget):
        def __init__(self):
            super().__init__()
            self.keys = []
            self.setWindowTitle('SpeedBuilder package test')
            self.resize(100, 100)
        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor('#112233'))
        def keyPressEvent(self, event):
            self.keys.append(event.text())
    window = TestWindow()
    window.show()
    app.processEvents()
    desktop = X11()
    try:
        resource = desktop.connection.create_resource_object('window', int(window.winId()))
        resource.set_input_focus(X.RevertToParent, X.CurrentTime)
        desktop.connection.sync()
        desktop.start(None, False, True)
        app.processEvents()
        assert desktop.sample(resource, [[10, 10], [10, 14]]) == '112233:112233'
        assert desktop.tap((desktop.keycode('1'),), window.windowTitle(), resource.id)
        for _ in range(20):
            app.processEvents()
            time.sleep(.01)
        assert window.keys == ['1'], window.keys
    finally:
        desktop.close()
        window.close()
if '--wayland-check' in sys.argv:
    import time
    from wayland import Wayland
    desktop = Wayland()
    try:
        desktop.start(None, False, True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            desktop.poll()
            if desktop.fresh_frame():
                print(json.dumps({'wayland_portals': 'passed', 'capture_size': desktop.layout()[0], 'keyboard_permission': bool(desktop.keyboard), 'keys_sent': 0}))
                break
            time.sleep(.02)
        else:
            raise RuntimeError('No PipeWire frame received')
    finally:
        desktop.close()
print(json.dumps({'status': 'passed', 'qt': QApplication.platformName(), 'gstreamer': Gst.version_string(), 'x11': '--x11' in sys.argv, 'helper': 'bundled and installable', 'subprocess': 'working'}))
