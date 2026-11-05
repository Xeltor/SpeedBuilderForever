"""Keyboard toggle limits and passive XInput2 mouse-button monitoring."""

import re

from Xlib.ext import ge, xinput
from Xlib.protocol import rq


WAYLAND_KEYS = {f"F{number}" for number in range(1, 13)}
MOUSE_TOGGLE = re.compile(r"Mouse(?:[1-9]|1[0-9]|2[0-7])", re.IGNORECASE)
RAW_BUTTON = rq.Struct(rq.Card16("deviceid"), rq.Card32("time"), rq.Card32("detail"))


def mouse_button(name):
    if MOUSE_TOGGLE.fullmatch(name):
        number = int(name[5:])
        # X11 numbers middle before right and reserves buttons 4–7 for scrolling.
        if number <= 3:
            return (1, 3, 2)[number - 1]
        return number + 4
    return None


class MouseToggle:
    def __init__(self, desktop, button):
        self.connection = desktop.connection
        self.button = button
        extension = self.connection.query_extension(xinput.extname)
        if extension is None or not self.connection.has_extension(ge.extname):
            raise ValueError("A mouse toggle requires XInput 2.1 or later.")
        self.opcode = extension.major_opcode
        version = xinput.XIQueryVersion(display=self.connection.display, opcode=self.opcode,
                                        major_version=2, minor_version=1)
        if (version.major_version, version.minor_version) < (2, 1):
            raise ValueError("A mouse toggle requires XInput 2.1 or later.")
        self.connection.ge_add_event_data(self.opcode, xinput.RawButtonPress, RAW_BUTTON)
        desktop.root.xinput_select_events([(xinput.AllMasterDevices, xinput.RawButtonPressMask)])
        self.connection.sync()

    def presses(self):
        count = 0
        while self.connection.pending_events():
            event = self.connection.next_event()
            if (event.type == ge.GenericEventCode and event.extension == self.opcode
                    and event.evtype == xinput.RawButtonPress and event.data.detail == self.button):
                count += 1
        return count
