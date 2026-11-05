# Third-party components

No license is granted here for SpeedBuilderForever's own application code.
Third-party components retain their respective licenses and notices.

The AppImage includes CPython (PSF license), PySide6/Shiboken and the Qt modules
used by this Widgets application (LGPLv3), python-xlib (LGPLv2.1), six (MIT),
PyGObject and GStreamer (LGPL), PipeWire/SPA (MIT), and their runtime dependencies.
The AppImage runtime has its own MIT and bundled-library notices.

Exact versions and Debian copyright files are included under
`usr/share/licenses/` in the extracted AppImage. Python wheel metadata and license
files are preserved there as well. The matching dependency source archive is
published with each release; it includes the Debian source packages, Qt/PySide
sources, and AppImage runtime sources/build instructions. See the component's
own notices for its complete terms, including copyright and warranty notices.

The libraries are dynamically loaded and the application ships as readable
Python source. To use modified libraries, extract the AppImage with
`./SpeedBuilderForever.AppImage --appimage-extract`, replace/rebuild the relevant
library in `squashfs-root/usr`, and launch `squashfs-root/AppRun`. No signature or
license check prevents replacement or debugging. The AppImage runtime can also
be replaced when repackaging with the published build scripts.

Upstream sources:

- CPython: https://www.python.org/
- Qt/PySide6: https://download.qt.io/ and https://code.qt.io/pyside/pyside-setup.git/
- python-xlib: https://github.com/python-xlib/python-xlib
- PyGObject: https://gitlab.gnome.org/GNOME/pygobject
- GStreamer: https://gstreamer.freedesktop.org/
- PipeWire: https://pipewire.org/
- AppImage runtime: https://github.com/AppImage/type2-runtime

World of Warcraft and Hekili are not included. SpeedBuilderForever is an
independent application and is not affiliated with Blizzard Entertainment.
