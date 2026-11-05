"""Native Wayland capture and input through the desktop portal on KDE Plasma."""

import json
import math
import os
import tempfile
import time
import uuid
from pathlib import Path
from xml.etree import ElementTree

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gio, GLib, Gst, GstVideo
from Xlib import XK

from toggle import WAYLAND_KEYS


PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SCREENCAST = "org.freedesktop.portal.ScreenCast"
REMOTE = "org.freedesktop.portal.RemoteDesktop"
KWIN = "org.kde.KWin"
RELAY = "org.speedbuilder.Focus"
RELAY_PATH = "/org/speedbuilder/Focus"

RELAY_XML = """<node><interface name="org.speedbuilder.Focus">
<method name="Update"><arg type="s" direction="in"/></method>
</interface></node>"""

RELAY_SCRIPT = """
function publish(toggle) {
    const window = workspace.activeWindow;
    const result = {toggle: toggle, id: "", title: ""};
    if (window) {
        const rect = window.frameGeometry;
        const cursor = workspace.cursorPos;
        result.id = String(window.internalId);
        result.title = window.caption;
        result.width = rect.width;
        result.height = rect.height;
        result.x = cursor.x - rect.x;
        result.y = cursor.y - rect.y;
    }
    callDBus(BUS, RELAY_OBJECT, "org.speedbuilder.Focus",
        "Update", JSON.stringify(result));
}
let active = null;
function update() { publish(false); }
function activated() {
    if (active) {
        active.frameGeometryChanged.disconnect(update);
        active.captionChanged.disconnect(update);
    }
    active = workspace.activeWindow;
    if (active) {
        active.frameGeometryChanged.connect(update);
        active.captionChanged.connect(update);
    }
    update();
}
workspace.windowActivated.connect(activated);
if (TRACK_POINTER) {
    let last = 0;
    workspace.cursorPosChanged.connect(() => {
        const now = Date.now();
        if (now - last >= 20) {
            last = now;
            update();
        }
    });
}
if (SHORTCUT) {
    registerShortcut("SpeedBuilderToggle", "Toggle SpeedBuilder", SHORTCUT, () => publish(true));
}
activated();
"""

WINDOW_QUERY_SCRIPT = """
const windows = workspace.windowList()
    .filter(window => window.normalWindow && !window.deleted && window.caption);
function report(result) {
    callDBus(BUS, RELAY_OBJECT, "org.speedbuilder.Focus", "Update", JSON.stringify(result));
}
"""

ACTIVATE_SCRIPT = """
const matches = windows.filter(window => window.caption === TITLE);
const active = workspace.activeWindow;
if (matches.length === 1 && (FROM_TITLE === null || (active && active.caption === FROM_TITLE))) {
    workspace.activeWindow = matches[0];
}
report(matches.length);
"""


class WaylandError(ValueError):
    pass


class Wayland:
    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.context = GLib.MainContext.get_thread_default() or GLib.MainContext.default()
        self.relay_path = f"{RELAY_PATH}/{uuid.uuid4().hex}"
        self.sessions = []
        self.keyboard = None
        self.session_closed = False
        self.pipeline = None
        self.fd = None
        self.script_name = None
        self.registration = None
        self.subscriptions = []
        self.focus = None
        self.toggle_event = False
        self.pending_toggle = False
        self.focus_changed = time.monotonic()
        self.focus_generation = 0
        self.frame = None
        self.frame_time = 0
        self.frame_number = 0
        self.locked = False

    def call(self, service, path, interface, method, signature=None, args=()):
        parameters = GLib.Variant(signature, args) if signature else None
        return self.bus.call_sync(
            service, path, interface, method, parameters, None,
            Gio.DBusCallFlags.NONE, 5000, None,
        ).unpack()

    def pump(self):
        while self.context.pending():
            self.context.iteration(False)

    def register_relay(self, receive):
        try:
            owner, = self.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "GetNameOwner", "(s)", (KWIN,))
        except GLib.Error as problem:
            raise WaylandError("Native Wayland mode requires KDE Plasma's KWin.") from problem

        def relay_update(_bus, sender, _path, _interface, _method, values, invocation):
            if sender == owner:
                receive(json.loads(values.unpack()[0]))
            invocation.return_value(None)

        relay = Gio.DBusNodeInfo.new_for_xml(RELAY_XML).interfaces[0]
        self.registration = self.bus.register_object(self.relay_path, relay, relay_update, None, None)

    def start_focus(self, shortcut="", track_pointer=False):
        def receive(focus):
            previous = self.focus
            self.focus = focus
            if previous is None or previous["id"] != focus["id"]:
                self.focus_changed = time.monotonic()
                self.focus_generation += 1
                self.pending_toggle = False
            self.pending_toggle |= focus["toggle"]

        self.register_relay(receive)
        saver = "org.freedesktop.ScreenSaver"
        saver_path = "/ScreenSaver"
        def lock_changed(_bus, _sender, _path, _interface, _name, values):
            self.locked, = values.unpack()
            self.focus_generation += 1
            self.focus_changed = time.monotonic()
            self.pending_toggle = False

        self.subscriptions.append(self.bus.signal_subscribe(
            saver, saver, "ActiveChanged", saver_path, None, Gio.DBusSignalFlags.NONE,
            lock_changed,
        ))
        self.locked, = self.call(saver, saver_path, saver, "GetActive")

        header = (
            f"const SHORTCUT = {json.dumps(shortcut)};\n"
            f"const TRACK_POINTER = {str(track_pointer).lower()};\n"
        )
        self.run_relay(header + RELAY_SCRIPT, lambda: self.focus is not None,
                       "KWin did not report the active window.")

    def run_relay(self, body, received, missing_message):
        xml, = self.call(KWIN, "/Scripting", "org.freedesktop.DBus.Introspectable", "Introspect")
        occupied = {node.get("name") for node in ElementTree.fromstring(xml).findall("node")}
        # KWin can reuse an occupied D-Bus path after scripts close out of order.
        # Reserve colliding slots with inert scripts; never run the old object.
        reservations = []
        try:
            with tempfile.TemporaryDirectory(prefix="speedbuilder-") as directory:
                for attempt in range(len(occupied) + 1):
                    script = Path(directory) / f"{attempt}.js"
                    script.write_text("// Reserved script slot.\n", encoding="utf-8")
                    name = f"speedbuilder-{uuid.uuid4().hex}"
                    number, = self.call(KWIN, "/Scripting", "org.kde.kwin.Scripting", "loadScript",
                                        "(ss)", (str(script), name))
                    if number < 0:
                        raise WaylandError("KWin could not load SpeedBuilder's window script.")
                    if f"Script{number}" in occupied:
                        reservations.append(name)
                        continue
                    self.script_name = name
                    script.write_text(
                        f"const BUS = {json.dumps(self.bus.get_unique_name())};\n"
                        f"const RELAY_OBJECT = {json.dumps(self.relay_path)};\n" + body,
                        encoding="utf-8",
                    )
                    self.call(KWIN, f"/Scripting/Script{number}", "org.kde.kwin.Script", "run")
                    deadline = time.monotonic() + 3
                    while not received() and time.monotonic() < deadline:
                        self.pump()
                        time.sleep(.01)
                    break
                else:
                    raise WaylandError("KWin could not allocate a window script slot.")
        finally:
            for name in reversed(reservations):
                self.unload_relay(name)
        if not received():
            raise WaylandError(missing_message)

    def unload_relay(self, name):
        try:
            self.call(KWIN, "/Scripting", "org.kde.kwin.Scripting", "unloadScript", "(s)", (name,))
        except GLib.Error:
            pass

    def request(self, interface, method, signature, args, options):
        token = "speedbuilder_" + uuid.uuid4().hex
        options = {**options, "handle_token": GLib.Variant("s", token)}
        sender = self.bus.get_unique_name()[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        result = []

        def response(_bus, _sender, _path, _interface, _name, values):
            result.append(values.unpack())

        subscription = self.bus.signal_subscribe(
            PORTAL, "org.freedesktop.portal.Request", "Response", path, None,
            Gio.DBusSignalFlags.NONE, response,
        )
        try:
            handle, = self.call(PORTAL, PORTAL_PATH, interface, method, signature, (*args, options))
            if handle != path:
                raise WaylandError("The desktop portal returned an unexpected request handle.")
            deadline = time.monotonic() + 180
            while not result and time.monotonic() < deadline:
                self.pump()
                time.sleep(.02)
            if not result:
                self.call(PORTAL, path, "org.freedesktop.portal.Request", "Close")
                raise WaylandError(f"Desktop portal {method} timed out.")
            status, values = result[0]
            if status:
                raise WaylandError(f"Desktop portal {method} was cancelled or denied.")
            return values
        except KeyboardInterrupt:
            self.call(PORTAL, path, "org.freedesktop.portal.Request", "Close")
            raise
        finally:
            self.bus.signal_unsubscribe(subscription)

    @staticmethod
    def keycode(name):
        code = XK.string_to_keysym(name)
        if not code:
            raise WaylandError(f"Unknown key name {name!r}")
        return code

    def create_session(self, interface):
        created = self.request(interface, "CreateSession", "(a{sv})", (), {
            "session_handle_token": GLib.Variant("s", "speedbuilder_" + uuid.uuid4().hex),
        })
        session = created["session_handle"]
        self.sessions.append(session)
        self.subscriptions.append(self.bus.signal_subscribe(
            PORTAL, "org.freedesktop.portal.Session", "Closed", session, None,
            Gio.DBusSignalFlags.NONE,
            lambda *_: setattr(self, "session_closed", True),
        ))
        return session

    def start(self, shortcut, track_pointer, send):
        if shortcut and shortcut not in WAYLAND_KEYS:
            raise WaylandError("Use a function key (F1–F12) for the Wayland toggle.")
        self.start_focus(shortcut or "", track_pointer)
        Gst.init(None)
        if Gst.ElementFactory.find("pipewiresrc") is None:
            raise WaylandError("Install the GStreamer PipeWire plugin for Wayland capture.")

        # Plasma's RemoteDesktop shares outputs. Use a separate, window-only cast.
        if send:
            self.keyboard = self.create_session(REMOTE)
            self.request(REMOTE, "SelectDevices", "(oa{sv})", (self.keyboard,), {
                "types": GLib.Variant("u", 1),
            })
            print("Allow keyboard control in the desktop permission dialog.", flush=True)
            devices = self.request(REMOTE, "Start", "(osa{sv})", (self.keyboard, ""), {})
            if not devices.get("devices", 0) & 1:
                raise WaylandError("Keyboard permission was not granted.")

        capture = self.create_session(SCREENCAST)
        self.request(SCREENCAST, "SelectSources", "(oa{sv})", (capture,), {
            "types": GLib.Variant("u", 2),
            "multiple": GLib.Variant("b", False),
        })
        print("Choose the WoW window in the desktop sharing dialog.", flush=True)
        started = self.request(SCREENCAST, "Start", "(osa{sv})", (capture, ""), {})
        streams = started.get("streams", [])
        if len(streams) != 1:
            raise WaylandError("Select exactly one game window to share.")
        node, properties = streams[0]
        if properties.get("source_type", 2) != 2:
            raise WaylandError("Choose a single window instead of sharing a whole display.")
        result, fds = self.bus.call_with_unix_fd_list_sync(
            PORTAL, PORTAL_PATH, SCREENCAST, "OpenPipeWireRemote",
            GLib.Variant("(oa{sv})", (capture, {})), None,
            Gio.DBusCallFlags.NONE, 5000, None, None,
        )
        self.fd = fds.get(result.unpack()[0])
        self.pipeline = Gst.parse_launch(
            "pipewiresrc name=source ! videoconvert ! video/x-raw,format=RGBx ! "
            "appsink name=frames sync=false max-buffers=1 drop=true"
        )
        source = self.pipeline.get_by_name("source")
        source.set_property("fd", self.fd)
        source.set_property("path", str(node))
        self.sink = self.pipeline.get_by_name("frames")
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise WaylandError("Could not start the PipeWire video stream.")
        self.pump()
        self.pending_toggle = False

    def poll(self):
        self.pump()
        self.toggle_event = self.pending_toggle
        self.pending_toggle = False
        if self.session_closed:
            raise WaylandError("The desktop sharing session ended.")
        failure = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if failure:
            detail = failure.parse_error()[0].message if failure.type == Gst.MessageType.ERROR else "ended"
            raise WaylandError(f"PipeWire video stream: {detail}")
        frame = self.sink.emit("try-pull-sample", 0)
        if frame is not None:
            self.frame = frame
            self.frame_time = time.monotonic()
            self.frame_number += 1

    def layout(self):
        info = GstVideo.VideoInfo.new_from_caps(self.frame.get_caps())
        metadata = GstVideo.buffer_get_video_meta(self.frame.get_buffer())
        stride = metadata.stride[0] if metadata else info.stride[0]
        offset = metadata.offset[0] if metadata else info.offset[0]
        return [info.width, info.height], stride, offset

    def focused_window(self, title):
        self.pump()
        return (self.focus if not self.locked and self.focus and self.focus["title"] == title
                else None)

    def identity(self, window):
        return (window["id"], self.focus_generation)

    @staticmethod
    def window_identity(window):
        return window["id"]

    def size(self, window):
        if self.frame is None:
            return [int(window["width"]), int(window["height"])]
        return self.layout()[0]

    def pointer(self, window):
        if self.frame is None:
            raise WaylandError("The screen sharing stream has not delivered a frame yet.")
        width, height = self.size(window)
        return (math.floor(window["x"] * width / window["width"]),
                math.floor(window["y"] * height / window["height"]))

    def fresh_frame(self):
        return (self.frame is not None and self.frame_time >= self.focus_changed
                and time.monotonic() - self.frame_time <= .25)

    def sample(self, window, points, refinements=None):
        if not self.fresh_frame():
            return None
        size, stride, offset = self.layout()
        if not all(0 <= x < size[0] and 0 <= y < size[1] for x, y in points):
            raise WaylandError("The sample points are outside the shared window.")
        buffer = self.frame.get_buffer()
        base = ":".join(
            buffer.extract_dup(offset + y * stride + x * 4, 3).hex()
            for x, y in points
        )
        extra = refinements.get(base) if refinements else None
        if extra:
            # These points were validated against the calibrated frame size before the run.
            suffix = ":".join(buffer.extract_dup(offset + y * stride + x * 4, 3).hex()
                              for x, y in extra)
            return f"{base}:{suffix}"
        return base

    def snapshot(self, window, region=None):
        if not self.fresh_frame():
            raise WaylandError("No fresh preview was available. Keep WoW focused and try again.")
        size, stride, offset = self.layout()
        x, y, width, height = region if region else (0, 0, *size)
        if not (0 <= x < x + width <= size[0] and 0 <= y < y + height <= size[1]):
            raise WaylandError("The preview rectangle is outside the shared window.")
        offset += y * stride + x * 4
        buffer = self.frame.get_buffer()
        rgb = bytearray(width * height * 3)
        for y in range(height):
            row = buffer.extract_dup(offset + y * stride, width * 4)
            start = y * width * 3
            for channel in range(3):
                rgb[start + channel:start + width * 3:3] = row[channel::4]
        return width, height, rgb

    def tap(self, codes, title, window_id):
        pressed = []
        try:
            for code in codes:
                focus = self.focused_window(title)
                if focus is None or self.identity(focus) != window_id or self.session_closed:
                    break
                self.key_event(code, 1)
                pressed.append(code)
            if pressed:
                # Wine can miss a press released before its next input frame.
                time.sleep(.02)
        finally:
            for code in reversed(pressed):
                self.key_event(code, 0)
        return len(pressed) == len(codes)

    def key_event(self, code, state):
        self.call(PORTAL, PORTAL_PATH, REMOTE, "NotifyKeyboardKeysym",
                  "(oa{sv}iu)", (self.keyboard, {}, code, state))

    @staticmethod
    def request_activation(title, from_title=None):
        request_activation(title, from_title)

    def close(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        if self.fd is not None:
            os.close(self.fd)
        for session in self.sessions:
            try:
                self.call(PORTAL, session, "org.freedesktop.portal.Session", "Close")
            except GLib.Error:
                pass
        if self.script_name:
            self.unload_relay(self.script_name)
        for subscription in self.subscriptions:
            self.bus.signal_unsubscribe(subscription)
        if self.registration:
            self.bus.unregister_object(self.registration)


def query_windows(script, missing_message, *, activate=False):
    context = GLib.MainContext.new()
    context.push_thread_default()
    desktop = None
    try:
        desktop = Wayland()
        if activate:
            locked, = desktop.call("org.freedesktop.ScreenSaver", "/ScreenSaver",
                                   "org.freedesktop.ScreenSaver", "GetActive")
            if locked:
                raise WaylandError("Unlock the desktop before switching windows.")
        result = []
        desktop.register_relay(result.append)
        desktop.run_relay(WINDOW_QUERY_SCRIPT + script, lambda: bool(result), missing_message)
        return result[0]
    except GLib.Error as problem:
        raise WaylandError(missing_message) from problem
    finally:
        try:
            if desktop is not None:
                desktop.close()
        finally:
            context.pop_thread_default()


def list_window_titles():
    return query_windows("report(windows.map(window => window.caption));",
                         "KWin did not report open windows.")


def request_activation(title, from_title=None):
    script = (f"const TITLE = {json.dumps(title)};\n"
              f"const FROM_TITLE = {json.dumps(from_title)};\n" + ACTIVATE_SCRIPT)
    count = query_windows(script, f"KWin could not bring {title!r} to the front.", activate=True)
    if count != 1:
        raise WaylandError(f"Expected one open window titled {title!r}, found {count}.")
