# SpeedBuilderForever

SpeedBuilderForever reads two pixels from the first Hekili recommendation and maps
the color pair to a key or chord. The Linux entry point is
`linux/speedbuilder.py`.

- **X11** uses window capture and XTEST. The same backend also works for
  XWayland windows when selected explicitly with `--backend x11`.
- **Wayland** uses KDE Plasma 6 for focus and the toggle, the desktop portal
  for permission, and PipeWire for capture. The game can use native Wayland
  or XWayland. When prompted, choose the WoW window. Key output requires
  a separate keyboard-control permission. This backend requires KWin.
- The backend defaults to your session type. Put `--backend x11` or
  `--backend wayland` before the subcommand to override it.

### Setup

Use the distribution Python for the virtual environment so it can load the
system Wayland bindings. On Fedora, native Wayland mode uses
`python3-gobject-base`, `gstreamer1`, `gstreamer1-plugins-base`,
`pipewire-gstreamer`, and `xdg-desktop-portal-kde`.

```sh
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r linux/requirements.txt
```

Set Hekili's Primary display to one fixed-size icon. Keep it fully visible and
leave the UI scale and window size fixed after calibration. Effects such as a
cooldown sweep or a range tint can change the sampled colors. Disable effects
that interfere or learn the additional color pairs.

```sh
.venv/bin/python linux/speedbuilder.py setup --size 50
.venv/bin/python linux/speedbuilder.py learn 1
.venv/bin/python linux/speedbuilder.py learn Shift+2
.venv/bin/python linux/speedbuilder.py run
```

- During `setup`, focus WoW and hover the center of the icon before the
  countdown ends. Pass the actual icon width in captured pixels to `--size`.
- During each `learn`, focus WoW with the desired recommendation visible.
  Move the pointer off the icon before the countdown ends. Repeat for each
  key. Multiple color pairs can point to the same key.
- While WoW has focus, press **F8** on Wayland or **backtick** on X11 to
  toggle. Dry run prints detected keys. Press **Ctrl+C** in the terminal
  to exit. Set a different toggle with `run --toggle F9`.
- Use `run --send` to enable key output. The default minimum time between
  presses is 100 ms. Losing game focus turns the tool off, and an unmatched
  or stale capture produces no key press.
- Profiles live in `~/.config/speedbuilder/forever-x11.json` and
  `~/.config/speedbuilder/forever-wayland.json`, or under `$XDG_CONFIG_HOME`.
  For a different layout or spec, put `--config PATH` before the subcommand.
  Pass `--window TITLE` to `setup` if the exact game window title differs
  from `World of Warcraft`.

## Original SpeedBuilder

SpeedBuilderForever ports the two-pixel Hekili matching from
[SpeedBuilder](https://github.com/Xeltor/SpeedBuilder), the original AutoHotkey
project. The port used the
[source at `04ba6de`](https://github.com/Xeltor/SpeedBuilder/tree/04ba6de7cb72328bb69a913e950f621209756569)
as its reference. For the original profiles, definitions, and Windows setup,
see that repository.
