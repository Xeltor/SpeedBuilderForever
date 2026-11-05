# SpeedBuilderForever

SpeedBuilderForever reads two pixels from the first Hekili recommendation and maps
the color pair to a key or chord.

## AppImage download

Download the x86-64 AppImage from [GitHub Releases](https://github.com/Xeltor/SpeedBuilderForever/releases).
The `.AppImage.tar.gz` download preserves its executable permission when extracted.
For the standalone `.AppImage` download, enable **Allow executing file as program**
in its file properties, or run `chmod +x SpeedBuilderForever-*.AppImage`, then open it.
No root access or administrator setup is needed.

The bundle includes Python, Qt, PyGObject, GStreamer, the PipeWire capture plugin,
and the WoW helper. It targets Ubuntu 22.04+/Mint 21+ and newer x86-64 Linux
systems, including Fedora, CachyOS and Bazzite. Native Wayland needs **KDE Plasma 6**
with PipeWire and `xdg-desktop-portal-kde` running. GNOME/Cinnamon Wayland is not
supported; use an X11 session there. XWayland game windows can also use the X11 backend.
Your desktop's graphics drivers and portal services remain supplied by the OS.

The required permissions are:

| Access | How it is granted |
| --- | --- |
| Execute the application | The AppImage's executable file permission |
| Capture WoW on Wayland | Choose the WoW window in the desktop sharing prompt |
| Send keys on Wayland | Allow keyboard control in the desktop permission prompt |
| Focus and toggle on KDE | Normal session D-Bus access to KWin |
| Capture/input on X11 | Your current graphical session's X11 connection and XTEST |
| Install/update the helper | Your user must be able to write to WoW's `Interface/AddOns` |
| Save profiles | Your user's normal configuration folder |

The AppImage runs as your normal user. It does not install services, change group
membership, set elevated capabilities, or pre-grant capture/input permissions.
The desktop sharing and keyboard-control prompts still apply. AppImages are not
sandboxed, so these prompts do not act as a general filesystem sandbox.

If FUSE is unavailable, run `./SpeedBuilderForever-*.AppImage --appimage-extract-and-run`.
Use `--cli` before the regular command-line arguments, `--version` to print the
version, and `--self-test` for offline package diagnostics. `SHA256SUMS` accompanies
each release. Bundled dependency notices and matching source archives are also
provided; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No application-code
license has been selected.

## Open the desktop app

Open **SpeedBuilderForever.desktop** from your file manager. If asked, allow
the launcher to run. Keep the launcher with the rest of the project files.

The desktop app uses Qt 6 through PySide6 6.5 or later. On Fedora, install
`python3-pyside6` and `python3-xlib` through your package manager. Native Wayland
also needs `python3-gobject-base`, `gstreamer1`, `gstreamer1-plugins-base`,
`pipewire-gstreamer`, and `xdg-desktop-portal-kde`.

1. Set Hekili’s Primary display to one fixed-size icon. Keep it fully visible.
2. Select a profile or click **New** and give it a name. Click **Locate icon**,
   choose WoW from the list of open windows, and click
   **Continue**. On Wayland, choose the same window when the desktop asks
   what to share. SpeedBuilder brings WoW forward for capture. Keep the
   recommendation visible until the preview opens, then drag a box around
   the icon and click **Save icon position**. Keep the UI scale and window size
   fixed afterward.
3. Click **Learn from Hekili** to [preview and learn your spells](#learn-a-batch-from-hekili)
   out of combat. Review the suggested keys and use **Edit key** to assign or
   correct the key for a named spell. Editing a key uses the saved icon samples.
4. Choose **Run profile** to have SpeedBuilder press matching keys in WoW.
   SpeedBuilder brings WoW forward and the profile starts paused. Press the
   in-game toggle to turn it on or pause it again. Switching away suspends input;
   returning to the same WoW window resumes it if it was running. A manual pause
   stays paused. Changing the window size or replacing the game window pauses
   the profile until you re-arm it. The default repeat interval is 100 ms.

If you move Hekili’s Primary icon or resize WoW, restore the saved layout or
stop the session and choose **Recalibrate icon**. Keep the same icon size and
appearance to reuse learned colors. Recalibration keeps your key assignments.
If the app asks you to refresh shared icon samples, run **Learn from Hekili**.

On desktops with a system tray, closing the window hides SpeedBuilderForever
and keeps the current rotation session available. The tray shows when the
rotation is armed or paused. Click to reopen the window, or right-click and
choose **Stop session** or **Quit**. On desktops without a tray, closing exits.

The app picks **Wayland** on a Wayland session and **X11** on an X11 session.
Wayland needs KDE Plasma 6. When the desktop asks what to share, choose the
WoW window. Key output needs a separate keyboard-control permission.
To capture an XWayland game window directly, choose **X11** under **Settings**
before locating the icon for a new profile.
The default toggle is **F8** on Wayland and **backtick** on X11.
To change it, open **Settings → Record toggle**, press the key or mouse button
you want, then click **Apply settings**. Mouse buttons work with X11 and
XWayland game windows, including with Wayland capture of an XWayland window.
For a native Wayland game window, use **F1–F12**.

Use the **Profile** list to switch between characters, specs, or layouts.
Use **New**, **Rename**, or **Delete** to manage the list. The app remembers
your selection and saves profiles automatically in
`$XDG_CONFIG_HOME/speedbuilder/profiles/` (normally
`~/.config/speedbuilder/profiles/`).

## Learn a batch from Hekili

1. Locate Hekili Forever v0.5.0's **Classic** Primary icon in SpeedBuilderForever.
   Disable Masque and keep the icon clear of the upper-left corner. **Learn
   from Hekili** installs the bundled helper automatically. If several installs
   are found or the game is in an unusual location, select the WoW Forever
   folder once.
2. For a first install, restart WoW before selecting **Learn from Hekili** again.
   For updates to the same addon folder, the app can reload the UI if
   the running helper supports it, using `/sbf reload`. An older helper needs
   a manual `/reload`.
3. With WoW out of combat and chat closed, select **Learn from Hekili**.
   The app automatically sends the setup command and refreshes keybindings.
   On Wayland, allow keyboard control and share the WoW window
   when prompted. SpeedBuilder brings WoW forward and uses the slash key to open
   command chat. It only presses Enter after the helper confirms the entire
   command in two fresh frames.
4. Keep WoW focused until the batch completes. The helper temporarily previews
   the full class ability catalog supported by Hekili at the Primary position,
   including abilities learned at higher levels or through talents. Spell ranks
   share one entry and key assignment; they do not require separate learning
   when their icon is unchanged. Your character's available racial and item
   actions are included too. Missing keybindings are left blank. The helper
   reports suggested keys through a marker in the upper-left corner. The marker is hidden during
   normal play. It appears only for the preview, confirmation of a complete
   `/sbf` or `/sbf reload` command, and a brief setup completion acknowledgement.
   Type `/sbf` again to stop a preview.
5. Double-click a spell in the list to assign or change its **WoW key**.
   **Clear key** keeps its learned colors for later. **Learn from Hekili** updates
   saved keys from WoW, including changed or newly assigned bindings.
   Spells without a detected binding are disabled.
   A batch saves only after it finishes. **Recalibrate icon** updates the capture
   position; use **Learn from Hekili** to refresh keys.
6. Choose **Run profile** when ready. Cooldown sweeps and range tints can
   change the sampled colors. Icon variants with unknown colors are skipped.

Batch learning tries one or two extra pixels to distinguish spells that
share the usual two colors. The spell list marks these samples as
**resolved**. Unbound spells, unresolved shared colors, and unknown extra
colors send no key. Run a fresh batch to add collision handling to an
existing profile. If two actions still look identical, give one a different
icon in Hekili and run the batch again.

## Command-line reference

The GUI and CLI use the same capture, recognition, and profile code. If using
a virtual environment, create it with the distribution Python and
`--system-site-packages` so Wayland can load the system bindings. The desktop
launcher also uses that environment if it is present.

```sh
/usr/bin/python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r linux/requirements.txt
.venv/bin/python linux/speedbuilder.py setup --size 50
.venv/bin/python linux/speedbuilder.py learn-batch
.venv/bin/python linux/speedbuilder.py run
```

Put `--backend x11`, `--backend wayland`, or `--config PATH` before the
subcommand to override the defaults. Use `setup --window TITLE` for a
different exact window title and `run --toggle F9` or `run --toggle Mouse5`
to change the toggle.
Use `run --send` to enable key output and **Ctrl+C** to stop.
Use `learn-batch --send` to start the helper by sending `/sbf` in WoW chat.
With `learn-batch` alone, type `/sbf` yourself after capture starts.
Batch learning refreshes saved keybindings by default. Add `--keep-keys` to
preserve existing assignments and cleared keys.
The manual `learn KEY` command samples the displayed icon after a countdown.
Use it only when you can hold a known icon on screen throughout capture.
For a manual install when using the CLI, copy
[SpeedBuilderForeverHelper](addon/SpeedBuilderForeverHelper) into the Forever
client's `Interface/AddOns/` directory.

## Original SpeedBuilder

SpeedBuilderForever ports the two-pixel Hekili matching from
[SpeedBuilder](https://github.com/Xeltor/SpeedBuilder), the original AutoHotkey
project. The port used the
[source at `04ba6de`](https://github.com/Xeltor/SpeedBuilder/tree/04ba6de7cb72328bb69a913e950f621209756569)
as its reference. For the original profiles, definitions, and Windows setup,
see that repository.

## Build an AppImage

The portable build uses an Ubuntu 22.04 container and pinned Python wheels:

```sh
docker build -f packaging/appimage/Containerfile -t speedbuilder-appimage .
mkdir -p dist .appimage-work
docker run --rm -v "$PWD:/source:ro" -v "$PWD/dist:/out" \
  -v "$PWD/.appimage-work:/work" speedbuilder-appimage \
  bash /source/packaging/appimage/build.sh /out /work
```

Podman can be used instead. Build products and dependency sources go into `dist/`;
download caches and the extracted AppDir stay in `.appimage-work/`. The build runs
the Python/Lua suites, a bundled dependency/video check, a disposable X11 window
capture/input check, and a final AppImage self-test. Native Wayland portal behavior
requires a separate interactive check on a supported desktop.
