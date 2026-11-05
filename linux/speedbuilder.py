#!/usr/bin/env python3
"""SpeedBuilder's two-pixel Hekili reader for X11 and Plasma Wayland."""

import argparse
import json
import math
import os
import signal
import tempfile
import time
from contextlib import closing, nullcontext
from pathlib import Path

from Xlib import X, XK, display, error
from Xlib.ext import xtest
from Xlib.protocol import event

from catalog import (
    active_bindings, collision_grid, color_owners, merge_batch, resolved_collisions,
    validate_collisions, validate_spells,
)
from companion import decode_marker, marker_region
from toggle import MouseToggle, mouse_button


MODIFIERS = {
    "Ctrl": "Control_L", "Alt": "Alt_L", "Shift": "Shift_L", "Super": "Super_L",
}


def parse_chord(binding, keycode):
    *modifiers, key = binding.split("+")
    if len(set(modifiers)) != len(modifiers) or any(mod not in MODIFIERS for mod in modifiers):
        raise ValueError(f"Invalid modifiers in {binding!r}. Use Ctrl, Alt, Shift or Super.")
    return tuple(keycode(MODIFIERS[mod]) for mod in modifiers) + (keycode(key),)


class X11:
    def __init__(self):
        self.connection = display.Display()
        self.toggle = None
        self.toggle_event = False
        self.toggle_down = False
        self.frame_number = 0
        self.keymap = []
        self.root = self.connection.screen().root
        self.name_atom = self.connection.intern_atom("_NET_WM_NAME")
        self.utf8_atom = self.connection.intern_atom("UTF8_STRING")
        info = self.connection.display.info
        self.formats = {entry.depth: entry for entry in info.pixmap_formats}
        self.byte_order = "little" if info.image_byte_order == X.LSBFirst else "big"
        self.visuals = {
            visual.visual_id: visual
            for depth in self.connection.screen().allowed_depths
            for visual in depth.visuals
        }

    def window_title(self, window):
        name = window.get_full_property(self.name_atom, self.utf8_atom)
        return name.value.decode("utf-8", errors="replace") if name else window.get_wm_name()

    def named_windows(self):
        atom = self.connection.intern_atom("_NET_CLIENT_LIST")
        clients = self.root.get_full_property(atom, X.AnyPropertyType)
        windows = ([self.connection.create_resource_object("window", int(wid))
                    for wid in clients.value] if clients is not None
                   else self.root.query_tree().children)
        type_atom = self.connection.intern_atom("_NET_WM_WINDOW_TYPE")
        normal = self.connection.intern_atom("_NET_WM_WINDOW_TYPE_NORMAL")
        for window in windows:
            try:
                if clients is None:
                    attrs = window.get_attributes()
                    if attrs.override_redirect or attrs.map_state == X.IsUnmapped:
                        continue
                types = window.get_full_property(type_atom, X.AnyPropertyType)
                if types is not None and len(types.value):
                    if types.value[0] != normal:
                        continue
                elif window.get_wm_transient_for() is not None:
                    continue
                title = self.window_title(window)
                if title and title.strip():
                    yield title, window
            except error.BadWindow:
                continue

    def window_titles(self):
        return [title for title, _ in self.named_windows()]

    def request_activation(self, title, from_title=None):
        windows = [window for caption, window in self.named_windows() if caption == title]
        if len(windows) != 1:
            raise ValueError(f"Expected one open window titled {title!r}, found {len(windows)}.")
        if from_title is not None and self.focused_window(from_title) is None:
            return
        message = event.ClientMessage(
            window=windows[0], client_type=self.connection.intern_atom("_NET_ACTIVE_WINDOW"),
            # This request represents the user's action in the desktop app.
            data=(32, [2, X.CurrentTime, 0, 0, 0]),
        )
        self.root.send_event(message, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        self.connection.flush()

    def focused_window(self, title):
        window = self.connection.get_input_focus().focus
        try:
            while hasattr(window, "id") and window.id != self.root.id:
                if self.window_title(window) == title:
                    return window
                window = window.query_tree().parent
        except error.BadWindow:
            pass
        return None

    def keycode(self, name):
        code = self.connection.keysym_to_keycode(XK.string_to_keysym(name))
        if not code:
            raise ValueError(f"Unknown X11 key {name!r}")
        return code

    def start(self, shortcut, track_pointer, send):
        if send and not self.connection.has_extension("XTEST"):
            raise ValueError("This X server does not support XTEST keyboard events.")
        if shortcut:
            self.toggle = self.keycode(shortcut)
            self.toggle_down = key_down(self.connection.query_keymap(), self.toggle)
        self.modifier_codes = [
            code for name in ("Control_L", "Control_R", "Shift_L", "Shift_R",
                              "Alt_L", "Alt_R", "Super_L", "Super_R")
            if (code := self.connection.keysym_to_keycode(XK.string_to_keysym(name)))
        ]

    def poll(self):
        self.frame_number += 1
        if self.toggle is not None:
            self.keymap = self.connection.query_keymap()
            pressed = key_down(self.keymap, self.toggle)
            self.toggle_event = pressed and not self.toggle_down
            self.toggle_down = pressed

    @staticmethod
    def identity(window):
        return window.id

    @staticmethod
    def window_identity(window):
        return window.id

    @staticmethod
    def size(window):
        geometry = window.get_geometry()
        return [geometry.width, geometry.height]

    @staticmethod
    def pointer(window):
        pointer = window.query_pointer()
        return pointer.win_x, pointer.win_y

    def tap(self, codes, title, window_id):
        focused = self.focused_window(title)
        if focused is None or focused.id != window_id:
            return False
        current_keys = self.connection.query_keymap()
        if (self.toggle is not None and key_down(current_keys, self.toggle)
                or any(key_down(current_keys, code) for code in (*self.modifier_codes, *codes))):
            return False
        for code in codes:
            xtest.fake_input(self.connection, X.KeyPress, code)
        for code in reversed(codes):
            xtest.fake_input(self.connection, X.KeyRelease, code)
        self.connection.sync()
        return True

    def close(self):
        self.connection.close()

    def sample(self, window, points, refinements=None):
        if len(points) == 2 and points[0][0] == points[1][0]:
            (left, top), (_, bottom) = points
            width, height = 1, bottom - top + 1
        else:
            xs, ys = zip(*points)
            left, top = min(xs), min(ys)
            width, height = max(xs) - left + 1, max(ys) - top + 1
        pixels = self._capture_pixels(window, left, top, width, height)
        base = self._read_colors(pixels, points, left, top)
        extra = refinements.get(base) if refinements else None
        if not extra:
            return base
        if all(left <= x < left + width and top <= y < top + height for x, y in extra):
            return f"{base}:{self._read_colors(pixels, extra, left, top)}"
        # Read both the base and extra points together to avoid matching across an icon change.
        refined = self.sample(window, (*points, *extra))
        return refined if refined.startswith(f"{base}:") else None

    def _capture_pixels(self, window, left, top, width, height):
        image = window.get_image(left, top, width, height, X.ZPixmap, 0xFFFFFFFF)
        pixel_format = self.formats[image.depth]
        pixel_bytes = pixel_format.bits_per_pixel // 8
        stride = ((width * pixel_format.bits_per_pixel + pixel_format.scanline_pad - 1)
                  // pixel_format.scanline_pad) * (pixel_format.scanline_pad // 8)
        visual = self.visuals[image.visual]
        if visual.visual_class != X.TrueColor or not pixel_bytes:
            raise ValueError("The game window needs a TrueColor X11 visual.")
        return image.data, pixel_bytes, stride, (visual.red_mask, visual.green_mask, visual.blue_mask)

    def _read_colors(self, pixels, points, left, top):
        data, pixel_bytes, stride, masks = pixels
        colors = []
        for x, y in points:
            start = (y - top) * stride + (x - left) * pixel_bytes
            pixel = int.from_bytes(data[start:start + pixel_bytes], self.byte_order)
            rgb = []
            for mask in masks:
                unit = mask & -mask
                rgb.append(((pixel & mask) // unit) * 255 // (mask // unit))
            colors.append(bytes(rgb).hex())
        return ":".join(colors)

    def snapshot(self, window, region=None):
        x, y, width, height = region if region else (0, 0, *self.size(window))
        image = window.get_image(x, y, width, height, X.ZPixmap, 0xFFFFFFFF)
        fmt = self.formats[image.depth]
        visual = self.visuals[image.visual]
        masks = (visual.red_mask, visual.green_mask, visual.blue_mask)
        if (visual.visual_class != X.TrueColor or fmt.bits_per_pixel not in (24, 32)
                or sorted(masks) != [0xff, 0xff00, 0xff0000]):
            raise ValueError("The preview requires an 8-bit RGB display.")
        pixel_bytes = fmt.bits_per_pixel // 8
        stride = ((width * fmt.bits_per_pixel + fmt.scanline_pad - 1)
                  // fmt.scanline_pad) * (fmt.scanline_pad // 8)
        offsets = [mask.bit_length() // 8 - 1 for mask in masks]
        if self.byte_order == "big":
            offsets = [pixel_bytes - 1 - offset for offset in offsets]
        rgb = bytearray(width * height * 3)
        for y in range(height):
            row = image.data[y * stride:y * stride + width * pixel_bytes]
            start = y * width * 3
            for channel, offset in enumerate(offsets):
                rgb[start + channel:start + width * 3:3] = row[offset::pixel_bytes]
        return width, height, rgb


def key_down(keymap, keycode):
    return bool(keymap[keycode // 8] & (1 << (keycode % 8)))


def read_profile(path):
    with path.open(encoding="utf-8") as source:
        profile = json.load(source)
    if not isinstance(profile, dict):
        raise ValueError("Invalid calibration profile. Run setup with a new profile path.")
    width, height = profile.get("window_size", (0, 0))
    points = profile.get("points", [])
    bindings = profile.get("bindings")
    if (not profile.get("window") or len(points) != 2 or
            not all(isinstance(x, int) and isinstance(y, int)
                    and 0 <= x < width and 0 <= y < height for x, y in points) or
            points[0][0] != points[1][0] or points[0][1] >= points[1][1] or
            not isinstance(bindings, dict) or not all(isinstance(key, str) for key in bindings.values())):
        raise ValueError("Invalid calibration profile. Run setup with a new profile path.")
    validate_spells(profile.get("spells", {}))
    validate_collisions(profile.get("collisions", {}), width, height, points)
    return profile


def write_profile(path, profile):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as destination:
        temporary = Path(destination.name)
        try:
            json.dump(profile, destination, indent=2, sort_keys=True)
            destination.write("\n")
            destination.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def start_capture(desktop, args, title, *, shortcut=None, track_pointer=False, send=False):
    desktop.start(shortcut, track_pointer=track_pointer, send=send)
    if not getattr(args, "activate_window", False):
        return
    desktop.request_activation(title)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        desktop.poll()
        if desktop.focused_window(title) is not None:
            return
        time.sleep(.02)
    raise ValueError(f"The desktop could not bring {title!r} to the front.")


def wait_for_window(desktop, title, delay, instructions):
    print(f"{instructions} Sampling in {delay:g} seconds.", flush=True)
    deadline = time.monotonic() + delay
    while True:
        desktop.poll()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(remaining, .02))
    window = desktop.focused_window(title)
    if window is None:
        raise ValueError(f"Focus the {title!r} window before the timer ends.")
    return window


def setup(desktop, args):
    if args.preview is not None:
        start_capture(desktop, args, args.window)
        instruction = ("Keep Hekili’s first icon visible." if args.activate_window
                       else "Switch to the WoW window.")
        window = wait_for_window(desktop, args.window, args.wait, instruction)
        width, height, rgb = desktop.snapshot(window)
        with args.preview.open("wb") as output:
            output.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
            output.write(rgb)
        if args.return_to:
            try:
                if args.backend == "x11" and default_backend() == "wayland":
                    # The Qt window can be native Wayland even with XWayland game capture.
                    from wayland import request_activation
                else:
                    request_activation = desktop.request_activation
                request_activation(args.return_to, from_title=args.window)
            except (ImportError, OSError, ValueError, error.XError):
                pass  # The preview is still usable if the desktop refuses to raise it.
        return
    if args.config.exists():
        raise ValueError("Profile already exists. Use --config with a new file to recalibrate.")
    start_capture(desktop, args, args.window, track_pointer=True)
    window = wait_for_window(desktop, args.window, args.wait, "Hover the center of the Hekili icon.")
    width, height = desktop.size(window)
    offset = args.size // 4
    x, y = desktop.pointer(window)
    if not (offset <= x < width - offset and offset <= y < height - offset):
        raise ValueError("The icon center must be inside the game window.")
    points = [[x - offset, y - offset], [x - offset, y + offset]]
    write_profile(args.config, {
        "backend": args.backend,
        "window": args.window,
        "window_size": [width, height],
        "points": points,
        "bindings": {},
    })
    print(f"Saved window-relative sample points {points} to {args.config}.")


def learn(desktop, args, profile):
    parse_chord(args.key, desktop.keycode)
    start_capture(desktop, args, profile["window"])
    window = wait_for_window(
        desktop, profile["window"], args.wait,
        "Show the desired Hekili recommendation and move the pointer off the icon.",
    )
    if desktop.size(window) != profile["window_size"]:
        raise ValueError("The game window size changed. Restore it or create a new profile.")
    signature = desktop.sample(window, profile["points"])
    if signature is None:
        raise ValueError("No fresh video frame was available. Try again while the game is visible.")
    spells = profile.get("spells", {})
    owners = color_owners(spells).get(signature, set())
    if len(owners) > 1 or signature in profile.get("collisions", {}):
        raise ValueError("These icon colors are shared by multiple learned spells.")
    spell = spells[next(iter(owners))] if owners else None
    current = spell["key"] if spell else profile["bindings"].get(signature)
    if current and current != args.key and not args.replace:
        raise ValueError(f"{signature} is already bound to {current}. Use --replace to change it.")
    if spell:
        spell["key"] = args.key
    else:
        profile["bindings"][signature] = args.key
    write_profile(args.config, profile)
    print(f"Saved {signature} → {args.key}.")


def learn_batch(desktop, args, profile):
    reload_helper = args.reload_helper
    command = "/sbf reload" if reload_helper else "/sbf"
    title = profile["window"]
    width, height = profile["window_size"]
    if width < 640 or height < 360:
        raise ValueError("Batch learning requires a game window of at least 640×360 pixels.")
    region = marker_region(width, height)
    points = profile["points"]
    if any(x < region[2] and y < region[3] for x, y in points):
        raise ValueError("The icon overlaps the helper marker. Move it away from the upper-left corner and recalibrate.")
    start_capture(desktop, args, title, send=args.send)
    if getattr(args, "activate_window", False):
        if args.send:
            instruction = f"Waiting for the helper to accept {command}."
        else:
            instruction = "Type /sbf in WoW within 30 seconds."
    else:
        instruction = ("Switch to WoW with chat closed" if args.send else "Switch to WoW and type /sbf")
        instruction = f"{instruction} within 30 seconds. Keep WoW focused."
    print(instruction, flush=True)
    deadline = time.monotonic() + 30

    def read_marker(window):
        stride, _, rgb = desktop.snapshot(window, region)
        return decode_marker(rgb, stride, width, height)

    samples = []
    grid = ()
    grid_bounds = None
    candidate = None
    last_frame = None
    window_id = None
    # Slash opens command chat without submitting any existing chat text. Never
    # press Enter until two fresh frames confirm the entire intended command.
    chat_keys = ("slash", *command[1:])
    chat_step = 0
    chat_session = None
    command_candidate = None
    command_submitted = False
    while time.monotonic() < deadline:
        desktop.poll()
        window = desktop.focused_window(title)
        focused_id = desktop.identity(window) if window is not None else None
        waiting_for_reload = reload_helper and command_submitted
        if window_id is not None and focused_id != window_id and not waiting_for_reload:
            raise ValueError("WoW lost focus during calibration. Start a new batch.")
        if window is None or desktop.frame_number == last_frame:
            time.sleep(0.05)
            continue
        last_frame = desktop.frame_number
        if desktop.size(window) != [width, height]:
            raise ValueError("The game window size changed. Restore it or recalibrate.")

        entry = read_marker(window) if args.backend == "x11" or desktop.fresh_frame() else None
        signature = None
        if entry is not None and entry.index and not reload_helper:
            if samples:
                first = samples[0][0]
                if (entry.session, entry.count, entry.bounds) != (first.session, first.count, first.bounds):
                    raise ValueError("The helper restarted or the icon moved. Start a new batch.")
            if entry.bounds != grid_bounds:
                x0, y0, x1, y1 = entry.bounds
                if not all(x0 < x * 65535 / width < x1 and y0 < y * 65535 / height < y1
                           for x, y in points):
                    raise ValueError("The calibrated points are outside Hekili's Primary icon. Recalibrate its position.")
                grid_bounds = entry.bounds
                grid = collision_grid(points, grid_bounds, width, height)
            signature = desktop.sample(window, (*points, *grid))
            if signature is None:
                entry = None
        if entry is not None and args.backend == "x11":
            # X11 reads the marker and the icon separately. Check that the marker didn't change.
            if read_marker(window) != entry:
                entry = None
        if args.send and not command_submitted and not samples and (entry is None or entry.index == entry.count == 0):
            if reload_helper and entry is not None and entry.key != "setup":
                raise ValueError("The currently loaded helper requires a manual /reload. Reload WoW's UI, then try again.")
            if chat_step < len(chat_keys):
                code = desktop.keycode("space" if chat_keys[chat_step] == " " else chat_keys[chat_step])
                if not desktop.tap((code,), title, focused_id):
                    raise ValueError(f"Could not type {command} safely. Keep WoW focused and release other keys.")
                window_id = focused_id
                chat_step += 1
                deadline = time.monotonic() + 4
            elif entry is not None and entry.action == (12 if reload_helper else 6):
                if command_candidate == entry:
                    if not desktop.tap((desktop.keycode("Return"),), title, focused_id):
                        raise ValueError(f"Could not send {command} safely. Keep WoW focused and release other keys.")
                    chat_session = entry.session
                    command_submitted = True
                    deadline = time.monotonic() + (30 if reload_helper else 4)
                else:
                    command_candidate = entry
            else:
                command_candidate = None
            time.sleep(0.05)
            continue
        if reload_helper:
            if entry is None or entry.index or entry.count:
                time.sleep(0.05)
                continue
            if waiting_for_reload:
                if entry.action == 1 and entry.session != chat_session and entry.key == "setup":
                    print("WoW reloaded and the helper is ready. Choose Learn from Hekili to continue.", flush=True)
                    return
                time.sleep(0.05)
                continue
            if entry.key != "setup":
                raise ValueError("The currently loaded helper requires a manual /reload. Reload WoW's UI, then try again.")
        if entry is None:
            candidate = None
        elif entry.index == 0:
            candidate = None
            if entry.count == 1:
                if (samples and len(samples) == samples[0][0].count
                        and entry.session == samples[0][0].session and entry.bounds == samples[0][0].bounds):
                    break
                if samples:
                    raise ValueError("The helper finished before every icon was captured.")
                continue
            if samples:
                raise ValueError("The helper stopped before the batch completed.")
        elif (entry, signature) != candidate:
            candidate = entry, signature
        elif entry.index == len(samples) + 1:
            focused = desktop.focused_window(title)
            if focused is None or desktop.identity(focused) != focused_id:
                raise ValueError("WoW lost focus during calibration. Start a new batch.")
            window_id = focused_id
            colors = signature.split(":")
            samples.append((entry, ":".join(colors[:2]), colors[2:]))
            print(f"Captured Hekili icon {entry.index}/{entry.count}.", flush=True)
            deadline = time.monotonic() + 4
        elif entry.index > len(samples) + 1:
            raise ValueError("An icon was missed. Start listening before typing /sbf and keep WoW focused.")
        time.sleep(0.05)
    else:
        if reload_helper:
            raise ValueError("Could not confirm the helper reload. Use /reload in WoW when it is safe.")
        raise ValueError("The Hekili batch did not finish. Keep WoW focused with chat closed and start a new learning pass.")

    updated, captured = merge_batch(profile, samples, grid, lambda key: parse_chord(key, desktop.keycode),
                                   refresh_keys=not getattr(args, "keep_keys", False))
    if updated != profile:
        if read_profile(args.config) != profile:
            raise ValueError("The profile changed during calibration. Start a new batch.")
        write_profile(args.config, updated)
    unbound = sum(not spell["key"] for spell in captured.values())
    resolved = len(resolved_collisions(updated))
    shared = len(updated["collisions"])
    print(f"Learned {len(captured)} spells. {unbound} without keys, {resolved} of {shared} shared pairs have extra samples.",
          flush=True)


def run(desktop, args, profile):
    bindings = {
        signature: parse_chord(key, desktop.keycode)
        for signature, key in active_bindings(profile).items()
    }
    if not bindings:
        raise ValueError("Record at least one recommendation with learn.")
    button = mouse_button(args.toggle)
    shortcut = args.toggle if button is None else None
    if shortcut is not None:
        toggle = desktop.keycode(shortcut)
        if any(toggle in chord for chord in bindings.values()):
            raise ValueError("Use a toggle key that is separate from the learned action keys.")

    with closing(X11()) if button is not None else nullcontext() as mouse_desktop:
        if mouse_desktop and args.backend == "wayland":
            if profile["window"] not in mouse_desktop.window_titles():
                raise ValueError("Mouse toggles require an XWayland game window. Use F1–F12 for native Wayland.")
        mouse = MouseToggle(mouse_desktop, button) if mouse_desktop else None
        start_capture(desktop, args, profile["window"], shortcut=shortcut, send=args.send)
        if mouse:
            mouse.presses()
        run_rotation(desktop, args, profile, bindings, mouse)


def run_rotation(desktop, args, profile, bindings, mouse):
    key_names = active_bindings(profile) if not args.send else {}
    refinements = {base: rule["points"] for base, rule in resolved_collisions(profile).items()}
    print(f"{'Sending keys' if args.send else 'Dry run'} | {args.toggle} toggles | Ctrl+C quits",
          flush=True)
    armed_window = None
    active_window = None
    suspended = False
    identify_window = getattr(desktop, "window_identity", desktop.identity)
    previous_signature = None
    previous_frame = None
    printed_signature = None
    stable = False
    next_press = 0.0
    previous_window = None
    while True:
        time.sleep(1 / 60)
        desktop.poll()
        window = desktop.focused_window(profile["window"])
        window_id = desktop.identity(window) if window is not None else None
        window_key = identify_window(window) if window is not None else None
        toggle_event = desktop.toggle_event
        if mouse:
            # Discard presses queued across focus changes, including the click that focused the game.
            toggle_event = (bool(mouse.presses() % 2) and window_id is not None
                            and previous_window == window_id)
        previous_window = window_id
        if toggle_event and (window_id is not None or armed_window is not None):
            armed_window = window_key if armed_window is None else None
            active_window = None
            suspended = False
            previous_signature = printed_signature = previous_frame = None
            stable = False
            next_press = 0.0
            print("On." if armed_window is not None else "Off.", flush=True)
        if armed_window is None:
            continue

        if window is None:
            if not suspended:
                print("Suspended (game lost focus).", flush=True)
            suspended = True
            active_window = None
            previous_signature = printed_signature = previous_frame = None
            stable = False
            continue
        if window_key != armed_window:
            armed_window = active_window = None
            suspended = False
            print("Off (game window changed).", flush=True)
            continue
        if window_id != active_window:
            # Focus generations may change even when an away-and-back switch
            # happens between polls. Never reuse recognition from the old focus.
            active_window = window_id
            previous_signature = printed_signature = previous_frame = None
            stable = False
            if suspended:
                print("On (game regained focus).", flush=True)
                suspended = False

        try:
            if desktop.size(window) != profile["window_size"]:
                armed_window = active_window = None
                print("Off (game window size changed).", flush=True)
                continue
            signature = desktop.sample(window, profile["points"], refinements)
        except (error.BadDrawable, error.BadMatch, error.BadWindow):
            armed_window = active_window = None
            print("Off (game window cannot be captured).", flush=True)
            continue

        if signature is None:
            previous_signature = None
            stable = False
            continue
        # Two distinct frames must agree before a recommendation is used.
        if desktop.frame_number != previous_frame:
            stable = signature == previous_signature
            previous_signature = signature
            previous_frame = desktop.frame_number
        if not stable:
            continue
        chord = bindings.get(signature)
        if not args.send:
            if signature != printed_signature:
                key = key_names.get(signature, "(unmapped)")
                print(f"{signature} → {key}", flush=True)
                printed_signature = signature
            continue
        if chord is not None and time.monotonic() >= next_press:
            if desktop.tap(chord, profile["window"], window_id):
                next_press = time.monotonic() + args.interval / 1000


def default_backend():
    session = os.environ.get("XDG_SESSION_TYPE")
    uses_wayland = session == "wayland" or (not session and os.environ.get("WAYLAND_DISPLAY"))
    return "wayland" if uses_wayland else "x11"


def window_titles(backend):
    if backend == "wayland":
        try:
            from wayland import list_window_titles
        except (ImportError, ValueError) as problem:
            raise ValueError("Wayland requires PyGObject, GStreamer and its video typelibs.") from problem
        return list_window_titles()
    desktop = X11()
    try:
        return desktop.window_titles()
    finally:
        desktop.close()


def default_profile(backend):
    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_root / "speedbuilder" / f"forever-{backend}.json"


def default_toggle(backend):
    return "F8" if backend == "wayland" else "grave"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("auto", "x11", "wayland"), default="auto")
    parser.add_argument("--config", type=Path, help="calibration profile path")
    parser.add_argument("--activate-window", action="store_true", help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)

    calibration = commands.add_parser("setup", help="record the position of the Hekili icon")
    calibration.add_argument("--window", default="World of Warcraft", help="exact game window title")
    calibration.add_argument("--size", type=int, default=50, help="icon width in pixels")
    calibration.add_argument("--wait", type=float, default=5)
    calibration.add_argument("--preview", type=Path, help=argparse.SUPPRESS)
    calibration.add_argument("--return-to", help=argparse.SUPPRESS)

    learning = commands.add_parser("learn", help="record colors for an icon held on screen")
    learning.add_argument("key", help="key or chord, such as 2, Shift+2 or Ctrl+q")
    learning.add_argument("--wait", type=float, default=5)
    learning.add_argument("--replace", action="store_true")

    batch = commands.add_parser("learn-batch", help="learn a batch from the in-game calibration helper")
    batch.add_argument("--send", action="store_true", help="start the helper by sending /sbf in WoW chat")
    batch.add_argument("--reload-helper", action="store_true", help="send /sbf reload and wait for the helper to return")
    batch.add_argument("--keep-keys", action="store_true", help="keep saved key assignments instead of refreshing them from WoW")

    rotation = commands.add_parser("run", help="run in dry-run mode unless --send is supplied")
    rotation.add_argument("--send", action="store_true")
    rotation.add_argument("--toggle", help="toggle key or mouse button, such as Mouse5 "
                          "(defaults to grave on X11 and F8 on Wayland)")
    rotation.add_argument("--interval", type=int, default=100, help="minimum milliseconds between keys")
    args = parser.parse_args()
    if args.command == "learn-batch" and args.reload_helper and not args.send:
        parser.error("--reload-helper requires explicit --send.")
    if args.backend == "auto":
        args.backend = default_backend()
    if args.config is None:
        args.config = default_profile(args.backend)
    if args.command == "run" and args.toggle is None:
        args.toggle = default_toggle(args.backend)
    wait = getattr(args, "wait", 0)
    if not math.isfinite(wait) or wait < 0 or getattr(args, "size", 4) < 4:
        parser.error("Use a nonnegative wait and an icon width of at least 4 pixels.")
    if getattr(args, "interval", 17) < 17:
        parser.error("Use a repeat interval of at least 17 milliseconds.")

    def terminate(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    desktop = None
    handled_errors = (OSError, ValueError, error.DisplayError, error.XError)
    try:
        if args.command != "setup":
            try:
                profile = read_profile(args.config)
            except (TypeError, ValueError) as problem:
                raise ValueError(f"Invalid calibration profile {args.config}: {problem}") from problem
            if profile.get("backend") != args.backend:
                raise ValueError("This profile was calibrated with a different capture backend.")
        if args.backend == "wayland":
            try:
                from wayland import GLib, Wayland
            except (ImportError, ValueError) as problem:
                raise ValueError("Wayland requires PyGObject, GStreamer and its video typelibs.") from problem
            handled_errors += (GLib.Error,)
            desktop = Wayland()
        else:
            desktop = X11()
        if args.command == "setup":
            setup(desktop, args)
        elif args.command == "learn":
            learn(desktop, args, profile)
        elif args.command == "learn-batch":
            learn_batch(desktop, args, profile)
        else:
            run(desktop, args, profile)
    except KeyboardInterrupt:
        print("\nStopped.")
    except handled_errors as problem:
        parser.exit(1, f"SpeedBuilder: {problem}\n")
    finally:
        if desktop is not None:
            desktop.close()


if __name__ == "__main__":
    main()
