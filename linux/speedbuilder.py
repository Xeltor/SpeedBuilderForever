#!/usr/bin/env python3
"""SpeedBuilder's two-pixel Hekili reader for X11 and Plasma Wayland."""

import argparse
import json
import math
import os
import signal
import tempfile
import time
from pathlib import Path

from Xlib import X, XK, display, error
from Xlib.ext import xtest


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

    def focused_window(self, title):
        window = self.connection.get_input_focus().focus
        try:
            while hasattr(window, "id") and window.id != self.root.id:
                name = window.get_full_property(self.name_atom, self.utf8_atom)
                value = name.value.decode("utf-8", errors="replace") if name else window.get_wm_name()
                if value == title:
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
        if any(key_down(current_keys, code) for code in (*self.modifier_codes, *codes, self.toggle)):
            return False
        for code in codes:
            xtest.fake_input(self.connection, X.KeyPress, code)
        for code in reversed(codes):
            xtest.fake_input(self.connection, X.KeyRelease, code)
        self.connection.sync()
        return True

    def close(self):
        self.connection.close()

    def sample(self, window, points):
        (x, top), (_, bottom) = points
        image = window.get_image(x, top, 1, bottom - top + 1, X.ZPixmap, 0xFFFFFFFF)
        pixel_format = self.formats[image.depth]
        pixel_bytes = pixel_format.bits_per_pixel // 8
        stride = ((pixel_format.bits_per_pixel + pixel_format.scanline_pad - 1)
                  // pixel_format.scanline_pad) * (pixel_format.scanline_pad // 8)
        visual = self.visuals[image.visual]
        if visual.visual_class != X.TrueColor or not pixel_bytes:
            raise ValueError("The game window needs a TrueColor X11 visual.")
        colors = []
        for row in (0, bottom - top):
            start = row * stride
            pixel = int.from_bytes(image.data[start:start + pixel_bytes], self.byte_order)
            rgb = []
            for mask in (visual.red_mask, visual.green_mask, visual.blue_mask):
                unit = mask & -mask
                rgb.append(((pixel & mask) // unit) * 255 // (mask // unit))
            colors.append(bytes(rgb).hex())
        return ":".join(colors)


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
    if args.config.exists():
        raise ValueError("Profile already exists. Use --config with a new file to recalibrate.")
    desktop.start(None, track_pointer=True, send=False)
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
    desktop.start(None, track_pointer=False, send=False)
    window = wait_for_window(
        desktop, profile["window"], args.wait,
        "Show the desired Hekili recommendation and move the pointer off the icon.",
    )
    if desktop.size(window) != profile["window_size"]:
        raise ValueError("The game window size changed. Restore it or create a new profile.")
    signature = desktop.sample(window, profile["points"])
    if signature is None:
        raise ValueError("No fresh video frame was available. Try again while the game is visible.")
    current = profile["bindings"].get(signature)
    if current and current != args.key and not args.replace:
        raise ValueError(f"{signature} is already bound to {current}. Use --replace to change it.")
    profile["bindings"][signature] = args.key
    write_profile(args.config, profile)
    print(f"Saved {signature} → {args.key}.")


def run(desktop, args, profile):
    bindings = {
        signature: parse_chord(key, desktop.keycode)
        for signature, key in profile["bindings"].items()
    }
    if not bindings:
        raise ValueError("Record at least one recommendation with learn.")
    toggle = desktop.keycode(args.toggle)
    if any(toggle in chord for chord in bindings.values()):
        raise ValueError("Use a toggle key that is separate from the learned action keys.")
    desktop.start(args.toggle, track_pointer=False, send=args.send)
    print(f"{'Sending keys' if args.send else 'Dry run'} | {args.toggle} toggles | Ctrl+C quits",
          flush=True)
    armed_window = None
    previous_signature = None
    previous_frame = None
    printed_signature = None
    stable = False
    next_press = 0.0
    while True:
        time.sleep(1 / 60)
        desktop.poll()
        window = desktop.focused_window(profile["window"])
        window_id = desktop.identity(window) if window is not None else None
        if armed_window is not None and window_id != armed_window:
            armed_window = None
            print("Off (game lost focus).", flush=True)
        if desktop.toggle_event and window_id is not None:
            armed_window = window_id if armed_window is None else None
            previous_signature = printed_signature = previous_frame = None
            next_press = 0.0
            print("On." if armed_window is not None else "Off.", flush=True)
        if armed_window is None:
            continue

        try:
            if desktop.size(window) != profile["window_size"]:
                armed_window = None
                print("Off (game window size changed).", flush=True)
                continue
            signature = desktop.sample(window, profile["points"])
        except (error.BadDrawable, error.BadMatch, error.BadWindow):
            armed_window = None
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
                key = profile["bindings"].get(signature, "(unmapped)")
                print(f"{signature} → {key}", flush=True)
                printed_signature = signature
            continue
        if chord is not None and time.monotonic() >= next_press:
            if desktop.tap(chord, profile["window"], armed_window):
                next_press = time.monotonic() + args.interval / 1000


def main():
    config_root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("auto", "x11", "wayland"), default="auto")
    parser.add_argument("--config", type=Path, help="calibration profile path")
    commands = parser.add_subparsers(dest="command", required=True)

    calibration = commands.add_parser("setup", help="record the position of the Hekili icon")
    calibration.add_argument("--window", default="World of Warcraft", help="exact game window title")
    calibration.add_argument("--size", type=int, default=50, help="icon width in pixels")
    calibration.add_argument("--wait", type=float, default=5)

    learning = commands.add_parser("learn", help="record colors for the displayed recommendation")
    learning.add_argument("key", help="key or chord, such as 2, Shift+2 or Ctrl+q")
    learning.add_argument("--wait", type=float, default=5)
    learning.add_argument("--replace", action="store_true")

    rotation = commands.add_parser("run", help="run in dry-run mode unless --send is supplied")
    rotation.add_argument("--send", action="store_true")
    rotation.add_argument("--toggle", help="toggle key (defaults to grave on X11 and F8 on Wayland)")
    rotation.add_argument("--interval", type=int, default=100, help="minimum milliseconds between keys")
    args = parser.parse_args()
    if args.backend == "auto":
        session = os.environ.get("XDG_SESSION_TYPE")
        uses_wayland = session == "wayland" or (not session and os.environ.get("WAYLAND_DISPLAY"))
        args.backend = "wayland" if uses_wayland else "x11"
    if args.config is None:
        args.config = config_root / "speedbuilder" / f"forever-{args.backend}.json"
    if args.command == "run" and args.toggle is None:
        args.toggle = "F8" if args.backend == "wayland" else "grave"
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
