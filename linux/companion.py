"""Decode the setup-only marker emitted by SpeedBuilderForeverHelper."""

from dataclasses import dataclass
import re
import struct


COLUMNS, ROWS = 32, 19
PACKET = struct.Struct(">4sIBBI12s4H40s2B")
MODIFIERS = {"C": "Ctrl", "A": "Alt", "S": "Shift",
             "CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift"}
KEY_NAMES = {
    "`": "grave", "-": "minus", "=": "equal", "[": "bracketleft", "]": "bracketright",
    "\\": "backslash", ";": "semicolon", "'": "apostrophe", ",": "comma", ".": "period",
    "/": "slash", "SPACE": "space", "TAB": "Tab", "ENTER": "Return", "ESCAPE": "Escape",
    "BKSP": "BackSpace", "BACKSPACE": "BackSpace",
}


@dataclass(frozen=True)
class Entry:
    session: int
    index: int
    count: int
    action: int
    key: str
    bounds: tuple[int, int, int, int]
    name: str


def marker_region(width, height):
    return (0, 0, (width * (COLUMNS + 1) + 319) // 320,
            (height * (ROWS + 1) + 179) // 180)


def decode_marker(rgb, stride, width, height):
    packet = bytearray()
    for row in range(ROWS):
        y = height * (2 * row + 3) // 360
        byte = 0
        for column in range(COLUMNS):
            x = width * (2 * column + 3) // 640
            start = (y * stride + x) * 3
            pixel = rgb[start:start + 3]
            if max(pixel) <= 40:
                bit = 0
            elif min(pixel) >= 215:
                bit = 1
            else:
                return None
            byte = (byte << 1) | bit
            if column % 8 == 7:
                packet.append(byte)
                byte = 0

    magic, session, index, count, action, raw_key, x0, y0, x1, y1, raw_name, high, low = PACKET.unpack(packet)
    if magic != b"SBF2" or not (session and x0 < x1 and y0 < y1):
        return None
    if not (0 < index <= count and action or index == count == 0 and 1 <= action <= 12
            or index == 0 and count == action == 1):
        return None
    a = b = 0
    for byte in packet[:-2]:
        a = (a + byte) % 255
        b = (b + a) % 255
    if (b, a) != (high, low):
        return None
    try:
        key = raw_key.rstrip(b"\0").decode("ascii")
        name = raw_name.rstrip(b"\0").decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(not " " <= character <= "~" for character in key) or (index and not name.isprintable()):
        return None
    if index == count == 0 and action > 6 and key not in ("reload", "setup"):
        return None
    return Entry(session, index, count, action, key, (x0, y0, x1, y1), name)


def linux_chord(key):
    if not key:
        return None
    if key.startswith("!"):
        # Hekili overrides are free text. Accept only unambiguous key names and explicit modifiers.
        match = re.fullmatch(r"((?:(?:CTRL|ALT|SHIFT)[+-])*)(.+)", key[1:].upper())
        if not match:
            return None
        prefix, base = match.groups()
        modifiers = re.findall(r"CTRL|ALT|SHIFT", prefix)
    else:
        match = re.fullmatch(r"([CAS]{0,3})(F(?:1[0-2]|[1-9])|SPACE|TAB|ENTER|ESCAPE|BKSP|.)", key.upper())
        if not match:
            return None
        prefixes, base = match.groups()
        modifiers = list(prefixes)
    if any(modifier not in MODIFIERS for modifier in modifiers) or len(set(modifiers)) != len(modifiers):
        return None
    name = KEY_NAMES.get(base)
    if re.fullmatch(r"[A-Z0-9]", base):
        name = base.lower()
    elif re.fullmatch(r"F(?:1[0-2]|[1-9])", base):
        name = base
    if name is not None:
        return "+".join([*(MODIFIERS[modifier] for modifier in modifiers), name])
