"""Qt desktop interface for SpeedBuilderForever."""

import math
import queue
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

from PySide6.QtCore import QEvent, QLockFile, QPointF, QProcess, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QAbstractItemView, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QGraphicsScene, QGraphicsView, QGroupBox,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
    QMessageBox, QPushButton, QSpinBox, QSystemTrayIcon, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from catalog import active_bindings, color_owners, resolved_collisions
from installer import find_forever, forever_folder, install_helper, running_clients
from profiles import Profiles
from toggle import WAYLAND_KEYS

DEFAULT_WINDOW = "World of Warcraft"
APP_ICON = Path(__file__).resolve().parent / "assets" / "speedbuilderforever.svg"
BUTTONS = QDialogButtonBox.StandardButton
KEY_NAMES = {
    " ": "space", "Space": "space", "`": "grave", "-": "minus", "=": "equal",
    "[": "bracketleft", "]": "bracketright", "\\": "backslash", ";": "semicolon",
    "'": "apostrophe", ",": "comma", ".": "period", "/": "slash", "+": "plus",
    "Esc": "Escape", "Del": "Delete", "Ins": "Insert", "PgUp": "Page_Up",
    "PgDown": "Page_Down", "Backspace": "BackSpace", "CapsLock": "Caps_Lock",
    "NumLock": "Num_Lock", "ScrollLock": "Scroll_Lock",
}


def label(text="", *, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    return widget


def dialog_buttons(dialog, text):
    box = QDialogButtonBox(BUTTONS.Cancel, parent=dialog)
    action = box.addButton(text, QDialogButtonBox.ButtonRole.AcceptRole)
    action.setDefault(True)
    box.rejected.connect(dialog.reject)
    return box, action


def key_symbol(event):
    key = event.key()
    if key == Qt.Key.Key_Return:
        return "Return"
    if key == Qt.Key.Key_Enter:
        return "KP_Enter"
    symbol = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText)
    if event.modifiers() & Qt.KeyboardModifier.KeypadModifier and len(symbol) == 1 and symbol in "0123456789":
        return f"KP_{symbol}"
    return symbol


def key_name(symbol):
    return KEY_NAMES.get(symbol, symbol.lower() if len(symbol) == 1 else symbol)


def toggle_label(binding):
    return {
        "grave": "backtick (`)", "Mouse1": "Mouse1 (left click)",
        "Mouse2": "Mouse2 (right click)", "Mouse3": "Mouse3 (middle click)",
    }.get(binding, binding)


class ToggleRecorder(QDialog):
    def __init__(self, parent, backend, engine):
        super().__init__(parent)
        self.backend = backend
        self.engine = engine
        self.binding = ""
        self.setWindowTitle("Record in-game toggle")
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        prompt = ("Press F1–F12 or click a mouse button in this window." if backend == "wayland"
                  else "Press a key or click a mouse button in this window.")
        layout.addWidget(label(prompt, wrap=True))
        self.hint = label("Mouse buttons work with XWayland game windows." if backend == "wayland"
                          else "Press Esc to cancel.", wrap=True)
        layout.addWidget(self.hint)
        cancel = QPushButton("Cancel", clicked=self.reject)
        cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignRight)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.reject()
            elif not event.isAutoRepeat():
                name = key_name(key_symbol(event))
                if self.backend == "wayland" and (name not in WAYLAND_KEYS or event.modifiers()):
                    self.hint.setText("On Wayland, press F1–F12 or use a mouse button for an XWayland game.")
                elif (event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
                      or not self.engine.XK.string_to_keysym(name)):
                    self.hint.setText("Press one keyboard key or click a mouse button.")
                else:
                    self.record(name)
            return True
        return super().event(event)

    def mousePressEvent(self, event):
        button = event.button().value
        if button:
            # Qt uses one bit per mouse button, in WoW button order.
            self.record(f"Mouse{button.bit_length()}")
        else:
            super().mousePressEvent(event)

    def record(self, binding):
        self.binding = binding
        self.accept()


class IconSelection(QGraphicsView):
    def __init__(self, pixmap, save):
        super().__init__()
        self.save = save
        self.origin = None
        self.points = None
        scene = QGraphicsScene(self)
        self.setScene(scene)
        scene.addPixmap(pixmap)
        self.bounds = QRectF(pixmap.rect())
        self.setSceneRect(self.bounds)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setAccessibleName("WoW preview. Drag a rectangle around the Hekili icon.")
        border = QPen(self.palette().highlight().color(), 2)
        border.setCosmetic(True)
        self.outline = scene.addRect(QRectF(), border)
        dot_pen = QPen(QColor("#1a1c22"), 1)
        dot_pen.setCosmetic(True)
        self.markers = [scene.addEllipse(-4, -4, 8, 8, dot_pen, QColor(color))
                        for color in ("#f7bf72", "#b8d5ff")]
        for marker in self.markers:
            marker.setFlag(marker.GraphicsItemFlag.ItemIgnoresTransformations)
            marker.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fitInView(self.bounds, Qt.AspectRatioMode.KeepAspectRatio)

    def image_position(self, event):
        point = self.mapToScene(event.position().toPoint())
        return QPointF(max(0, min(point.x(), self.bounds.width() - 1)),
                       max(0, min(point.y(), self.bounds.height() - 1)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = self.image_position(event)
            self.draw_selection(self.origin)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.draw_selection(self.image_position(event))

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.origin is not None:
            self.draw_selection(self.image_position(event))
            self.origin = None

    def draw_selection(self, end):
        rect = QRectF(self.origin, end).normalized()
        self.outline.setRect(rect)
        width, height = int(rect.width()), int(rect.height())
        valid = min(width, height) >= 8
        self.points = None
        if valid:
            x, y = int(rect.left()) + width // 4, int(rect.top()) + height // 4
            self.points = [[x, y], [x, y + height // 2]]
            for marker, point in zip(self.markers, self.points):
                marker.setPos(*point)
        for marker in self.markers:
            marker.setVisible(valid)
        self.save.setEnabled(valid)


class ShortcutRecorder(QLineEdit):
    """Translate a single Qt shortcut into the engine's Linux key names."""

    def __init__(self, engine):
        super().__init__()
        self.engine = engine
        self.setReadOnly(True)
        self.setPlaceholderText("Press a key or key combination")
        self.setAccessibleName("WoW key or key combination")

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            super().keyPressEvent(event)
            return
        event.accept()
        if event.isAutoRepeat() or key in (Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt,
                                           Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return
        mods = event.modifiers()
        flags = Qt.KeyboardModifier
        names = [name for flag, name in ((flags.ControlModifier, "Ctrl"),
                 (flags.AltModifier, "Alt"), (flags.ShiftModifier, "Shift"),
                 (flags.MetaModifier, "Super")) if mods & flag]
        symbol = key_symbol(event)

        if "Shift" in names and event.nativeScanCode() and len(symbol) == 1:
            # Qt reports the shifted glyph. Resolve its physical key on the XKB map.
            try:
                keyboard = self.engine.display.Display()
                try:
                    base = keyboard.keycode_to_keysym(event.nativeScanCode(), 0)
                finally:
                    keyboard.close()
                plain = self.engine.XK.keysym_to_string(base)
                if plain and plain.isascii() and plain.isprintable():
                    symbol = plain
            except self.engine.error.DisplayError:
                if not symbol.isalnum():
                    symbol = ""

        symbol = key_name(symbol)
        chord = "+".join([*names, symbol]) if symbol and self.engine.XK.string_to_keysym(symbol) else ""
        self.setText(chord)
        self.setPlaceholderText("Press a key or key combination" if chord else "Enter this binding by hand")


class WindowPicker(QDialog):
    def __init__(self, parent, backend, engine, preferred):
        super().__init__(parent)
        self.backend = backend
        self.engine = engine
        self.preferred = preferred
        self.counts = Counter()
        self.setWindowTitle("Choose the game window")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        self.picker = QComboBox()
        self.picker.setPlaceholderText("Select an open window")
        field = QLabel("&Open windows")
        field.setBuddy(self.picker)
        layout.addWidget(field)
        layout.addWidget(self.picker)
        self.hint = label(wrap=True)
        layout.addWidget(self.hint)
        buttons, self.continue_button = dialog_buttons(self, "Continue")
        self.refresh_button = buttons.addButton("Refresh list", QDialogButtonBox.ButtonRole.ActionRole)
        self.refresh_button.clicked.connect(self.refresh)
        self.continue_button.clicked.connect(self.accept)
        self.picker.currentIndexChanged.connect(self.update_choice)
        layout.addWidget(buttons)
        self.poller = QTimer(self, interval=35)
        self.poller.timeout.connect(self.read_windows)
        self.finished.connect(self.poller.stop)
        self.refresh()

    def refresh(self):
        self.preferred = self.picker.currentText() or self.preferred
        self.picker.setEnabled(False)
        self.continue_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.hint.setText("Finding open windows…")
        self.window_results = queue.SimpleQueue()
        threading.Thread(target=self.find_windows, args=(self.window_results,), daemon=True).start()
        self.poller.start()

    def find_windows(self, result):
        try:
            result.put(self.engine.window_titles(self.backend))
        except (OSError, ValueError, self.engine.error.DisplayError, self.engine.error.XError) as problem:
            result.put(str(problem))

    def read_windows(self):
        try:
            windows = self.window_results.get_nowait()
        except queue.Empty:
            return
        self.poller.stop()
        self.refresh_button.setEnabled(True)
        if isinstance(windows, str):
            self.hint.setText(windows)
            return
        excluded = {self.parentWidget().windowTitle(), self.windowTitle()}
        self.counts = Counter(title for title in windows if title not in excluded)
        titles = sorted(self.counts, key=lambda title: (title != DEFAULT_WINDOW, title.casefold()))
        self.picker.blockSignals(True)
        self.picker.clear()
        self.picker.addItems(titles)
        self.picker.setCurrentIndex(self.picker.findText(self.preferred))
        self.picker.blockSignals(False)
        self.picker.setEnabled(bool(titles))
        self.update_choice()
        self.picker.setFocus()

    def update_choice(self):
        count = self.counts[self.picker.currentText()]
        self.continue_button.setEnabled(count == 1)
        if count > 1:
            text = "More than one window has this title. Close the extra window and refresh."
        elif not self.counts:
            text = "Open WoW, then click Refresh list."
        elif not count:
            text = "Choose your game window from the list."
        elif self.backend == "wayland":
            text = "Choose the same window when the desktop asks what to share."
        else:
            text = "SpeedBuilder will bring this window forward for capture."
        self.hint.setText(text)


class SpeedBuilderWindow(QWidget):
    def __init__(self, engine, profiles):
        super().__init__()
        self.engine = engine
        self.profiles = profiles
        self.profile = None
        self.backend = engine.default_backend()
        self.path = None
        self.toggle = profiles.toggle(self.backend)
        self.interval = 100
        self.preview = None
        self.busy = False
        self.closing = False
        self.stopping = False
        self.tray_notice_shown = profiles.settings.get("tray_notice_shown") is True
        self.capture_deadline = None
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.process_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.process.started.connect(self.process_started)
        self.clock = QTimer(self, interval=100)
        self.clock.timeout.connect(self.update_countdown)

        self.setWindowTitle("SpeedBuilderForever")
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip(f"{self.windowTitle()} · Idle")
        self.armed_icon = self.rotation_icon(armed=True)
        self.paused_icon = self.rotation_icon(armed=False)
        menu = QMenu(self)
        self.tray_status = menu.addAction("Idle")
        self.tray_status.setEnabled(False)
        menu.addAction("Open SpeedBuilder", self.open_from_tray)
        self.tray_stop = menu.addAction("Stop session", self.stop)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_from_tray)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.tray.show()
        self.resize(610, 490)
        content = QVBoxLayout(self)
        content.setContentsMargins(20, 18, 20, 15)
        content.setSpacing(12)
        header = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(self.windowIcon().pixmap(42, 42))
        header.addWidget(mark)
        identity = QVBoxLayout()
        title = label("SpeedBuilderForever")
        title_font = QFont(title.font())
        title_font.setPointSize(title_font.pointSize() + 9)
        title_font.setBold(True)
        title.setFont(title_font)
        identity.addWidget(title)
        identity.addWidget(label("Hekili for WoW Forever"))
        header.addLayout(identity)
        header.addStretch()
        self.activity = label("Idle")
        font = self.activity.font()
        font.setBold(True)
        self.activity.setFont(font)
        header.addWidget(self.activity)
        content.addLayout(header)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        content.addWidget(line)

        profile = QGroupBox("Profile")
        profile_row = QHBoxLayout(profile)
        self.profile_picker = QComboBox()
        self.profile_picker.setAccessibleName("Active profile")
        self.profile_picker.setPlaceholderText("No profiles")
        self.profile_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.profile_picker.setMinimumContentsLength(14)
        self.profile_picker.activated.connect(self.switch_profile)
        profile_row.addWidget(self.profile_picker, 1)
        self.new_button = QPushButton("&New…", clicked=self.new_profile)
        self.rename_button = QPushButton("Re&name…", clicked=self.rename_profile)
        self.delete_button = QPushButton("&Delete…", clicked=self.delete_profile)
        self.settings_button = QPushButton("&Settings…", clicked=self.open_settings)
        for button in (self.new_button, self.rename_button, self.delete_button, self.settings_button):
            profile_row.addWidget(button)
        content.addWidget(profile)

        self.setup = QGroupBox("1 of 3  ·  Icon position")
        setup_row = QHBoxLayout(self.setup)
        self.icon_hint = label(wrap=True)
        setup_row.addWidget(self.icon_hint, 1)
        self.calibrate = QPushButton("&Locate icon…", clicked=self.locate_icon)
        setup_row.addWidget(self.calibrate)
        content.addWidget(self.setup)

        self.binding_panel = QGroupBox()
        bindings_layout = QVBoxLayout(self.binding_panel)
        self.empty = label(wrap=True)
        bindings_layout.addWidget(self.empty)
        self.bindings = QTableWidget(0, 3)
        self.bindings.setHorizontalHeaderLabels(["Spell", "WoW key", "Icon samples"])
        self.bindings.verticalHeader().hide()
        self.bindings.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bindings.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.bindings.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.bindings.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.bindings.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.bindings.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.bindings.setAlternatingRowColors(True)
        self.bindings.setMinimumHeight(112)
        self.bindings.setAccessibleName("Learned bindings")
        self.bindings.itemSelectionChanged.connect(self.update_selection)
        self.bindings.itemActivated.connect(self.edit_binding)
        bindings_layout.addWidget(self.bindings, 1)

        binding_actions = QHBoxLayout()
        self.remove = QPushButton("&Remove selected…", clicked=self.remove_binding)
        binding_actions.addWidget(self.remove)
        self.edit_key = QPushButton("&Edit key…", clicked=self.edit_binding)
        binding_actions.addWidget(self.edit_key)
        binding_actions.addStretch()
        self.learn_batch = QPushButton("Learn from &Hekili", clicked=self.start_learning_batch)
        binding_actions.addWidget(self.learn_batch)
        bindings_layout.addLayout(binding_actions)
        content.addWidget(self.binding_panel, 1)

        self.run_panel = QGroupBox("3 of 3  ·  Run your profile")
        run_layout = QVBoxLayout(self.run_panel)
        self.auto_button = QPushButton("&Run profile…", clicked=self.confirm_automatic)
        run_layout.addWidget(self.auto_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.stop_session = QPushButton("Stop profile", clicked=self.stop)
        run_layout.addWidget(self.stop_session, alignment=Qt.AlignmentFlag.AlignRight)
        self.run_hint = label(wrap=True)
        run_layout.addWidget(self.run_hint)
        content.addWidget(self.run_panel)

        footer = QHBoxLayout()
        self.status = label(wrap=True)
        footer.addWidget(self.status, 1)
        self.cancel = QPushButton("Cancel", clicked=self.stop)
        footer.addWidget(self.cancel)
        content.addLayout(footer)
        self.select_profile(self.profiles.active or self.fallback_profile(),
                            "\n".join(self.profiles.warnings), remember=False)

    def open_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings")
        form = QFormLayout(dialog)
        backend = QComboBox()
        backend.addItem("Plasma Wayland", "wayland")
        backend.addItem("X11 / XWayland", "x11")
        backend.setCurrentIndex(backend.findData(self.backend))
        backend.setEnabled(self.profile is None)
        target = label(wrap=True)
        toggle = self.toggle
        toggle_value = label(toggle_label(toggle), wrap=True)
        record = QPushButton("Record toggle…")
        interval = QSpinBox()
        interval.setRange(17, 2_147_483_647)
        interval.setValue(self.interval)
        interval.setSuffix(" ms")

        def show_target():
            profile = self.profile if backend.currentData() == self.backend else None
            target.setText(profile["window"] if profile else "Choose an open window during setup")

        def change_backend():
            nonlocal toggle
            toggle = self.profiles.toggle(backend.currentData())
            toggle_value.setText(toggle_label(toggle))
            show_target()

        def record_toggle():
            nonlocal toggle
            recorder = ToggleRecorder(dialog, backend.currentData(), self.engine)
            if recorder.exec() == QDialog.DialogCode.Accepted:
                toggle = recorder.binding
                toggle_value.setText(toggle_label(toggle))

        show_target()
        backend.currentIndexChanged.connect(change_backend)
        record.clicked.connect(record_toggle)
        form.addRow("&Capture method", backend)
        form.addRow("Game window", target)
        form.addRow("In-game toggle", toggle_value)
        form.addRow("", record)
        form.addRow("Repeat &interval", interval)
        form.addRow(label("Create a new profile to use another capture method." if self.profile else
                          "Set the capture method before locating the icon.", wrap=True))
        buttons, apply = dialog_buttons(dialog, "Apply settings")
        apply.clicked.connect(dialog.accept)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if backend.currentData() != self.backend:
            try:
                profile = self.profiles.read(self.path)
                if "points" in profile:
                    raise ValueError("Create a new profile to use another capture method.")
                self.profiles.save(self.path, {**profile, "backend": backend.currentData()})
            except (OSError, ValueError) as problem:
                self.status.setText(f"Could not change the capture method. {problem}")
                return
            self.backend = backend.currentData()
        try:
            if toggle != self.profiles.toggle(self.backend):
                self.profiles.remember_toggle(self.backend, toggle)
        except OSError as problem:
            self.status.setText(f"Could not save the in-game toggle. {problem}")
            self.toggle = self.profiles.toggle(self.backend)
            self.refresh_profile()
            return
        self.toggle = toggle
        self.interval = interval.value()
        self.status.setText("Settings applied.")
        self.refresh_profile()
        self.focus_next_action()

    def fallback_profile(self):
        return next((path for path, profile in self.profiles.entries.items()
                     if profile.get("backend") == self.backend),
                    next(iter(self.profiles.entries), None))

    def select_profile(self, path, status="", *, remember=True):
        self.path = path
        backend = self.profiles.entries.get(path, {}).get("backend")
        if backend not in ("x11", "wayland"):
            backend = self.engine.default_backend()
        if backend != self.backend:
            self.backend = backend
            self.toggle = self.profiles.toggle(backend)
        if remember:
            try:
                self.profiles.remember(path)
            except OSError as problem:
                status = f"{status} Could not remember the profile selection. {problem}".strip()
        self.status.setText(status)
        self.update_profile_picker()
        self.refresh_profile()
        self.focus_next_action()

    def update_profile_picker(self):
        self.profile_picker.clear()
        entries = self.profiles.entries
        for path in sorted(entries, key=lambda path: entries[path]["name"].casefold()):
            self.profile_picker.addItem(entries[path]["name"], path.name)
            self.profile_picker.setItemData(self.profile_picker.count() - 1, entries[path]["name"],
                                            Qt.ItemDataRole.ToolTipRole)
        self.profile_picker.setCurrentIndex(self.profile_picker.findData(self.path.name) if self.path else -1)

    def switch_profile(self, index):
        filename = self.profile_picker.itemData(index)
        if self.busy or filename is None:
            return
        path = self.profiles.directory / filename
        if path != self.path:
            self.select_profile(path)

    def ask_profile_name(self, title, path=None):
        name = self.profiles.entries[path]["name"] if path is not None else ""
        while True:
            name, accepted = QInputDialog.getText(self, title, "Profile name", text=name)
            if not accepted:
                return None
            try:
                return self.profiles.validate_name(name, exclude=path)
            except ValueError as problem:
                QMessageBox.warning(self, title, str(problem))

    def new_profile(self):
        name = self.ask_profile_name("New profile")
        if name is None:
            return
        try:
            path = self.profiles.create(name, self.backend)
        except (OSError, ValueError) as problem:
            self.status.setText(f"Could not create the profile. {problem}")
            return
        self.select_profile(path, "Profile created. Locate the icon to calibrate it.")

    def rename_profile(self):
        name = self.ask_profile_name("Rename profile", self.path)
        if name is None:
            return
        try:
            self.profiles.rename(self.path, name)
        except (OSError, ValueError) as problem:
            self.status.setText(f"Could not rename the profile. {problem}")
            return
        self.update_profile_picker()
        self.status.setText("Profile renamed.")

    def delete_profile(self):
        name = self.profiles.entries[self.path]["name"]
        dialog = QMessageBox(QMessageBox.Icon.Question, "Delete profile", "",
                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"Delete “{name}” and its saved icon position and bindings?")
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        try:
            self.profiles.delete(self.path)
        except OSError as problem:
            self.status.setText(f"Could not delete the profile. {problem}")
            return
        self.select_profile(self.fallback_profile(), "Profile deleted.")

    def refresh_profile(self):
        self.profile = None
        if self.path is not None:
            try:
                entry = self.profiles.read(self.path)
                if entry.get("backend") != self.backend:
                    raise ValueError("The profile has an invalid capture method.")
                if "points" in entry:
                    self.profile = self.engine.read_profile(self.path)
            except (OSError, TypeError, ValueError) as problem:
                self.status.setText(f"Could not read the calibration. {problem}")
        bindings = self.profile["bindings"] if self.profile else {}
        spells = self.profile.get("spells", {}) if self.profile else {}
        owners = color_owners(spells)
        collisions = self.profile.get("collisions", {}) if self.profile else {}
        resolved = resolved_collisions(self.profile, owners) if self.profile else {}
        matched = {color: set(rule["matches"].values()) for color, rule in resolved.items()}
        rows = []
        for action, spell in sorted(spells.items(), key=lambda item: item[1]["name"].casefold()):
            colors = spell["colors"]
            shared = sum(len(owners[color]) > 1 or color in collisions for color in colors)
            refined = sum(action in matched.get(color, ()) for color in colors)
            detail = f"{len(colors)} learned"
            if refined:
                detail += f" · {refined} resolved"
            if shared > refined:
                detail += f" · {shared - refined} shared"
            rows.append((spell["name"], spell["key"], detail, action))
        for signature, key in sorted(bindings.items(), key=lambda item: (item[1], item[0])):
            if signature not in owners and signature not in collisions:
                rows.append(("Manually learned", key, signature, None))
        self.bindings.setRowCount(len(rows))
        self.bindings.clearSelection()
        for row, (name, key, detail, action) in enumerate(rows):
            title = QTableWidgetItem(name)
            title.setData(Qt.ItemDataRole.UserRole, action)
            self.bindings.setItem(row, 0, title)
            self.bindings.setItem(row, 1, QTableWidgetItem(key or "Unbound"))
            self.bindings.setItem(row, 2, QTableWidgetItem(detail))
        self.setup.setVisible(self.path is not None)
        self.setup.setTitle("Icon position" if self.profile else "1 of 3  ·  Icon position")
        self.calibrate.setText("Re&calibrate icon…" if self.profile else "&Locate icon…")
        self.icon_hint.setText("Recalibrate after moving Hekili’s icon or resizing WoW." if self.profile
                               else "Show Hekili’s first recommendation in WoW.")
        self.binding_panel.setTitle(f"Learned spells and bindings ({len(rows)})" if rows else
                                    "2 of 3  ·  Learn spells" if self.path else "Get started")
        if self.path is None:
            self.empty.setText("Click New to create your first profile.")
        elif self.profile is None:
            self.empty.setText("Locate the Primary icon to start learning spells.")
        else:
            self.empty.setText("Learn from Hekili previews each spell in WoW. Then review or edit its WoW key here.")
        self.empty.setVisible(not rows)
        self.bindings.setVisible(bool(rows))
        self.learn_batch.setVisible(self.profile is not None)
        self.run_panel.setVisible(bool(self.profile and active_bindings(self.profile)))
        self.update_controls()

    def update_controls(self):
        self.new_button.setEnabled(not self.busy)
        selected = not self.busy and self.path is not None
        for control in (self.profile_picker, self.rename_button, self.delete_button, self.settings_button):
            control.setEnabled(selected)
        self.calibrate.setEnabled(selected)
        self.learn_batch.setEnabled(selected and self.profile is not None)
        self.cancel.setVisible(self.busy and self.command != "run")
        self.cancel.setEnabled(not self.stopping)
        self.update_selection()
        self.update_run_action()

    def update_selection(self):
        selected = bool(self.bindings.selectedItems())
        self.remove.setVisible(selected)
        self.remove.setEnabled(selected and not self.busy)
        self.edit_key.setEnabled(selected and not self.busy)

    def update_run_action(self):
        active = self.busy and self.command == "run"
        self.auto_button.setVisible(not active)
        self.auto_button.setEnabled(not self.busy)
        self.stop_session.setVisible(active)
        self.stop_session.setEnabled(not self.stopping)
        self.tray_stop.setVisible(active)
        self.tray_stop.setEnabled(active and not self.stopping)
        self.run_hint.setVisible(not active)
        self.run_hint.setText(f"The profile starts paused. Press {self.toggle_label()} in WoW to turn it on or pause it.")

    def rotation_icon(self, *, armed):
        icon = QIcon()
        for size in (16, 22, 32, 48, 64):
            pixmap = self.windowIcon().pixmap(size, size)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            logical_size = pixmap.deviceIndependentSize()
            painter.scale(logical_size.width() / 64, logical_size.height() / 64)
            ink = QColor("#11192a")
            painter.setPen(QPen(ink, 3))
            painter.setBrush(QColor("#b5e8af" if armed else "#ffd49a"))
            painter.drawEllipse(30, 30, 32, 32)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            if armed:
                painter.drawPolygon(QPolygonF([QPointF(41, 38), QPointF(41, 54), QPointF(54, 46)]))
            else:
                painter.drawRect(39, 38, 5, 16)
                painter.drawRect(48, 38, 5, 16)
            painter.end()
            icon.addPixmap(pixmap)
        return icon

    def set_rotation_status(self, state, detail=""):
        self.activity.setText(state)
        self.tray_status.setText(state)
        tooltip = f"{self.windowTitle()} · {state}"
        self.tray.setToolTip(f"{tooltip}\n{detail}" if detail else tooltip)
        if state == "Idle":
            self.tray.setIcon(self.windowIcon())
        else:
            self.tray.setIcon(self.armed_icon if state == "Armed" else self.paused_icon)

    def toggle_label(self):
        return toggle_label(self.toggle)

    def focus_next_action(self):
        if self.path is None:
            self.new_button.setFocus()
        elif self.profile is None:
            self.calibrate.setFocus()
        elif active_bindings(self.profile):
            self.auto_button.setFocus()
        elif self.profile.get("spells"):
            self.bindings.setCurrentCell(0, 0)
            self.bindings.setFocus()
        else:
            self.learn_batch.setFocus()

    def remove_binding(self):
        row = self.bindings.currentRow()
        if row < 0:
            return
        action = self.bindings.item(row, 0).data(Qt.ItemDataRole.UserRole)
        signature = self.bindings.item(row, 2).text()
        prompt = "Forget this spell and its learned colors?" if action else "Remove this color pair from the profile?"
        if QMessageBox.question(self, "Remove binding", prompt,
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
            return
        try:
            profile = self.engine.read_profile(self.path)
            if action:
                del profile["spells"][action]
            else:
                del profile["bindings"][signature]
            self.engine.write_profile(self.path, profile)
            self.status.setText("Removed the selected entry.")
        except (KeyError, OSError, TypeError, ValueError) as problem:
            self.status.setText(f"Could not remove the color pair. {problem}")
        self.refresh_profile()

    def edit_binding(self):
        row = self.bindings.currentRow()
        if self.busy or row < 0 or not self.bindings.selectedItems():
            return
        title = self.bindings.item(row, 0)
        action = title.data(Qt.ItemDataRole.UserRole)
        signature = self.bindings.item(row, 2).text()
        dialog = QDialog(self)
        dialog.setWindowTitle("Edit WoW key")
        layout = QVBoxLayout(dialog)
        prompt = (f"Press the key you use in WoW for {title.text()}." if action
                  else "Press the key you use in WoW for this saved icon sample.")
        layout.addWidget(label(prompt, wrap=True))
        recorder = ShortcutRecorder(self.engine)
        layout.addWidget(recorder)
        buttons, save = dialog_buttons(dialog, "Save key")
        save.setEnabled(False)
        recorder.textChanged.connect(lambda key: save.setEnabled(bool(key)))
        save.clicked.connect(dialog.accept)
        if action and self.profile["spells"][action]["key"]:
            clear = buttons.addButton("Clear key", QDialogButtonBox.ButtonRole.ResetRole)
            clear.clicked.connect(recorder.clear)
            clear.clicked.connect(dialog.accept)
        layout.addWidget(buttons)
        recorder.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        key = recorder.text()
        try:
            profile = self.engine.read_profile(self.path)
            if action:
                profile["spells"][action]["key"] = key
            else:
                profile["bindings"][signature] = key
            self.engine.write_profile(self.path, profile)
            self.status.setText(f"Saved {key}." if key else "Key cleared. The icon samples are still saved.")
        except (KeyError, OSError, TypeError, ValueError) as problem:
            self.status.setText(f"Could not save the key. {problem}")
        self.refresh_profile()
        self.bindings.setCurrentCell(row, 0)
        self.bindings.setFocus()

    def locate_icon(self):
        try:
            profile = self.profiles.read(self.path)
        except (OSError, ValueError) as problem:
            self.status.setText(f"Could not read the profile. {problem}")
            return
        dialog = WindowPicker(self, self.backend, self.engine, profile.get("window") or DEFAULT_WINDOW)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        title = dialog.picker.currentText()
        dialog.deleteLater()
        if not accepted:
            return
        self.preview = tempfile.TemporaryDirectory(prefix="speedbuilder-preview-")
        self.preview_profile = profile
        self.preview_title = title
        self.preview_image = Path(self.preview.name) / "window.ppm"
        self.launch("setup", "--window", title, "--preview", str(self.preview_image),
                    "--return-to", self.windowTitle(), status="Preparing the Hekili icon preview…")

    def save_preview(self):
        pixmap = QPixmap(str(self.preview_image))
        if pixmap.isNull():
            raise ValueError("The captured window preview could not be opened.")
        dialog = QDialog(self)
        dialog.setWindowTitle("Locate the Hekili icon")
        layout = QVBoxLayout(dialog)
        layout.addWidget(label("Drag a box around Hekili’s first recommendation icon.", wrap=True))
        if "points" in self.preview_profile:
            layout.addWidget(label("Keep the same icon size and appearance to reuse learned colors.", wrap=True))
        buttons, save = dialog_buttons(dialog, "Save icon position")
        save.setEnabled(False)
        save.clicked.connect(dialog.accept)
        selection = IconSelection(pixmap, save)
        layout.addWidget(selection, 1)
        layout.addWidget(buttons)
        available = self.screen().availableGeometry()
        dialog.resize(min(pixmap.width() + 32, available.width() - 70),
                      min(pixmap.height() + 100, available.height() - 85))
        if (dialog.exec() != QDialog.DialogCode.Accepted or selection.points is None
                or self.stopping or self.closing):
            self.status.setText("Kept the previous icon position." if "points" in self.preview_profile
                                else "Icon position was not saved.")
            return
        relearn = self.profiles.save_calibration(self.path, self.preview_profile, self.preview_title,
                                                [pixmap.width(), pixmap.height()], selection.points)
        self.status.setText("Icon position saved. Use Learn from Hekili to refresh shared icon samples."
                            if relearn else "Icon position saved.")

    def start_learning_batch(self):
        try:
            running, wow_running = running_clients()
            folder = find_forever(self.profiles.settings.get("wow_folder"), running)
            if folder is None:
                selection = QFileDialog.getExistingDirectory(self, "Choose your WoW Forever folder")
                if not selection:
                    return
                folder = forever_folder(selection)
                if folder is None:
                    raise ValueError("Choose World of Warcraft or its _forever_ folder.")
            result = install_helper(folder)
            if self.profiles.settings.get("wow_folder") != str(folder):
                self.profiles.save_settings({**self.profiles.settings, "wow_folder": str(folder)})
        except (OSError, RuntimeError, ValueError) as problem:
            self.status.setText(f"Could not prepare the helper. {problem}")
            return

        game_open = folder in running or (not running and wow_running)
        if result == "installed":
            action = "Restart WoW" if game_open else "Start WoW"
            self.status.setText(f"Helper installed. {action}, then choose Learn from Hekili again.")
            return
        if result == "updated" and game_open:
            dialog = QMessageBox(QMessageBox.Icon.Question, "Helper updated",
                                 "Reload WoW’s UI to use the new helper. Do this out of combat with chat closed. "
                                 "If the loaded helper is older, type /reload yourself.",
                                 QMessageBox.StandardButton.Cancel, self)
            reload_ui = dialog.addButton("Reload helper in WoW", QMessageBox.ButtonRole.AcceptRole)
            dialog.setDefaultButton(reload_ui)
            dialog.exec()
            if dialog.clickedButton() == reload_ui:
                self.launch("learn-batch", "--send", "--reload-helper", status="Preparing to reload WoW’s UI…")
            else:
                self.status.setText("Helper updated. Type /reload in WoW, then choose Learn from Hekili.")
            return
        if result == "updated":
            self.status.setText("Helper updated. Start WoW, then choose Learn from Hekili.")
            return

        self.launch("learn-batch", "--send", status="Preparing to learn from Hekili…")

    def confirm_automatic(self):
        dialog = QMessageBox(QMessageBox.Icon.Question, "Run profile",
                             "SpeedBuilder will press matching keys for you while WoW has focus.",
                             QMessageBox.StandardButton.Cancel, self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setInformativeText(f"Press {self.toggle_label()} in WoW to turn it on or pause it.")
        allow = dialog.addButton("Run profile", QMessageBox.ButtonRole.AcceptRole)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() == allow:
            self.launch("run", "--toggle", self.toggle, "--interval", str(self.interval), "--send",
                        status="Preparing to run the profile in WoW…")
            self.stop_session.setFocus()

    def launch(self, command, *options, status):
        self.output = b""
        self.last_message = status
        self.status.setText(status)
        self.command = command
        self.busy = True
        self.stopping = False
        if command == "run":
            self.set_rotation_status("Starting paused", status)
        else:
            self.activity.setText({"setup": "Locating icon", "learn-batch": "Learning from Hekili"}[command])
        self.update_controls()
        self.process.start(sys.executable, ["-u", str(Path(self.engine.__file__).resolve()),
                           "--backend", self.backend, "--config", str(self.path),
                           "--activate-window", command, *options])

    def process_started(self):
        self.process.closeWriteChannel()
        if self.stopping:
            self.process.terminate()

    def read_output(self):
        self.output += bytes(self.process.readAllStandardOutput())
        lines = self.output.split(b"\n")
        self.output = lines.pop()
        for line in lines:
            if line.strip():
                self.show_output(line.decode(errors="replace").rstrip())

    def process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.last_message = f"Could not start SpeedBuilder. {self.process.errorString()}"
            self.process_finished(1, QProcess.ExitStatus.NormalExit)

    def process_finished(self, result, exit_status):
        self.read_output()
        if self.output.strip():
            self.show_output(self.output.decode(errors="replace").rstrip())
        self.clock.stop()
        self.capture_deadline = None
        if self.preview is not None:
            try:
                if result == 0 and not self.stopping and not self.closing:
                    self.save_preview()
            except (OSError, ValueError) as problem:
                self.status.setText(f"Could not save the icon position. {problem}")
            finally:
                self.preview.cleanup()
                self.preview = None
        self.busy = False
        if self.closing:
            self.close()
            return
        self.set_rotation_status("Idle")
        if self.stopping:
            self.status.setText("Stopped.")
        elif exit_status == QProcess.ExitStatus.CrashExit:
            self.status.setText("The SpeedBuilder session ended unexpectedly.")
        elif result:
            self.status.setText(self.last_message.removeprefix("SpeedBuilder: "))
        self.stopping = False
        self.refresh_profile()
        self.focus_next_action()

    def update_countdown(self):
        remaining = max(0, math.ceil(self.capture_deadline - time.monotonic()))
        self.activity.setText(f"Capturing in {remaining} s" if remaining else "Capturing…")

    def show_output(self, line):
        if self.stopping:
            return
        self.clock.stop()
        self.capture_deadline = None
        instruction, timing, countdown = line.partition(" Sampling in ")
        if timing and countdown.endswith(" seconds."):
            self.capture_deadline = time.monotonic() + float(countdown.removesuffix(" seconds."))
            self.update_countdown()
            self.clock.start()
            line = instruction
        if line in ("On.", "On (game regained focus)."):
            line = f"Press {self.toggle_label()} in WoW to pause."
            self.set_rotation_status("Armed", line)
        elif line == "Suspended (game lost focus).":
            line = "Waiting for WoW focus. Return to WoW to resume automatically."
            self.set_rotation_status("Suspended", line)
        elif line == "Off." or line.startswith("Off ("):
            resume = f"Press {self.toggle_label()} in WoW to resume."
            if line == "Off (game window size changed).":
                width, height = self.profile["window_size"]
                line = (f"WoW window size changed. Restore {width} × {height} and press "
                        f"{self.toggle_label()} in WoW, or stop the session and choose Recalibrate icon.")
            elif line == "Off (game window changed).":
                line = f"The WoW window changed. {resume}"
            elif line == "Off (game window cannot be captured).":
                line = f"Could not capture the WoW window. Make it visible. {resume}"
            else:
                line = resume
            self.set_rotation_status("Paused", line)
        elif " toggles | " in line:
            line = f"Press {self.toggle_label()} in WoW to arm."
            self.set_rotation_status("Paused", line)
        self.last_message = line
        self.status.setText(line)

    def stop(self):
        if self.busy and not self.stopping:
            self.stopping = True
            self.clock.stop()
            self.capture_deadline = None
            self.process.terminate()
            if self.command == "run":
                self.set_rotation_status("Stopping")
            else:
                self.activity.setText("Stopping")
            self.update_controls()

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.open_from_tray()

    def open_from_tray(self):
        if not self.closing:
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
            self.show()
            self.activateWindow()

    def quit_from_tray(self):
        self.closing = True
        self.close()

    def closeEvent(self, event):
        if not self.closing and self.tray.isVisible() and self.tray.isSystemTrayAvailable():
            self.hide()
            if self.busy and self.command != "run":
                self.stop()
            if not self.tray_notice_shown and self.tray.supportsMessages():
                self.tray_notice_shown = True
                self.tray.showMessage(
                    self.windowTitle(),
                    "Closing the window keeps the rotation session available. "
                    "Right-click the tray icon to stop the session or quit.",
                )
                try:
                    self.profiles.save_settings({**self.profiles.settings, "tray_notice_shown": True})
                except OSError:
                    # A settings write failure should not prevent hiding the window.
                    pass
            event.ignore()
            return
        self.closing = True
        if self.busy:
            self.stop()
            event.ignore()
        else:
            self.tray.hide()
            super().closeEvent(event)
            QApplication.instance().quit()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SpeedBuilderForever")
    app.setDesktopFileName("SpeedBuilderForever")
    app.setWindowIcon(QIcon(str(APP_ICON)))
    app.setQuitOnLastWindowClosed(False)
    try:
        import speedbuilder
    except ImportError as problem:
        QMessageBox.critical(None, "SpeedBuilderForever",
                             f"Install python-xlib to run SpeedBuilder.\n\n{problem}")
        return 1
    directory = speedbuilder.default_profile("x11").parent
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as problem:
        QMessageBox.critical(None, "SpeedBuilderForever", f"Could not access saved profiles. {problem}")
        return 1
    lock = QLockFile(str(directory / "desktop.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        if lock.error() == QLockFile.LockError.LockFailedError:
            message = "SpeedBuilderForever is already running."
            if QSystemTrayIcon.isSystemTrayAvailable():
                message += " Check the system tray."
        else:
            message = "Could not lock the desktop profile folder."
        QMessageBox.critical(None, "SpeedBuilderForever", message)
        return 1
    try:
        window = SpeedBuilderWindow(speedbuilder, Profiles(speedbuilder))
        window.show()
        return app.exec()
    finally:
        lock.unlock()


if __name__ == "__main__":
    sys.exit(main())
