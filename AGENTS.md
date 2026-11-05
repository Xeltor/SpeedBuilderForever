# SpeedBuilderForever agent instructions

Read [README.md](README.md) for setup, calibration, and the pinned reference to
the original AutoHotkey SpeedBuilder. Keep this repository focused on the
standalone Linux tool for Hekili in WoW Forever.

## Supported desktop paths

- Preserve both X11 and native Wayland on KDE Plasma. X11 uses window-relative
  X11 capture and XTEST. Native Wayland uses the ScreenCast portal and PipeWire
  for window capture, the RemoteDesktop portal for keyboard permission, and KWin
  for focus and the toggle. Keep the X11 path usable for XWayland game windows.
- Keep the command-line workflow and existing calibration profiles compatible.
  Keep calibration data, machine-specific tooling, and local environments
  outside version control.

## Recognition and input

- Preserve the two-pixel matcher as the default. Match only learned signatures
  and handle ambiguous or unknown samples without sending a key. Check real
  Hekili icon variants before weakening matching rules.
- Start disarmed. Keep dry run as the default and require explicit `--send` for
  key output. Require a fresh, stable match and the calibrated game window in
  focus. Suspend input on focus loss and restore the previous armed state only
  when the same game window regains focus, after fresh stable recognition.
  Manual pauses must stay paused. Disarm on window replacement, capture failure,
  or window size changes. Preserve the user toggle, key-release cleanup, and
  configurable minimum interval between presses.
- Scope capture to the selected game window. Use the desktop's permission
  prompts for native Wayland capture and input.

## Performance and validation

- Prioritize correct recognition, then measure recognition delay and game frame
  times before changing polling, confirmation, or repeat timing. Keep the X11
  capture rectangle small. Measure the native Wayland path separately because
  the portal delivers window frames.
- Check CLI help and exercise calibration, dry run, key output, focus loss, and
  resize handling as relevant. Use a disposable test window for synthetic
  input. Verify both backends when a change affects shared recognition or input
  logic.
- Distinguish synthetic pixel-to-key latency from in-game timing. For FPS
  comparisons, measure WoW frame times with SpeedBuilder off and in dry-run
  mode under comparable conditions. A login screen or synthetic window does
  not establish combat recognition or in-game performance.
