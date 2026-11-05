#!/bin/bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "$0")/../.." && pwd)
output_dir=$(realpath -m -- "${1:?Usage: build.sh OUTPUT_DIRECTORY [WORK_DIRECTORY]}")
work_dir=$(realpath -m -- "${2:-$output_dir/work}")
version=$(cat "$project_dir/VERSION")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]]
mkdir -p "$output_dir" "$work_dir/downloads" "$work_dir/dependency-sources"
bundle_dir="$work_dir/SpeedBuilderForever.AppDir"
rm -rf -- "$bundle_dir"
fetch() {
    local url=$1 target=$2 checksum=${3:-}
    if [[ ! -f $target ]]; then
        curl --fail --location --retry 3 "$url" -o "$target.partial"
        mv -- "$target.partial" "$target"
    fi
    if [[ -n $checksum ]]; then echo "$checksum  $target" | sha256sum --check; fi
}
fetch https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage \
    "$work_dir/downloads/appimagetool.AppImage" 62aebc77c2149e96b593b94da9bbd1881e0984a56399ff6d7a03b7ddd7c9ce68
fetch https://github.com/AppImage/type2-runtime/releases/download/continuous/runtime-x86_64 \
    "$work_dir/downloads/runtime-x86_64" 617aa77314d57c8b8e4fae9c3f0b35af7b30d5529cb69e98f64f92510ed98a46
for module in qtbase qtwayland qtsvg; do
    fetch "https://download.qt.io/archive/qt/6.8/6.8.3/submodules/$module-everywhere-src-6.8.3.tar.xz" \
        "$work_dir/dependency-sources/$module-everywhere-src-6.8.3.tar.xz"
done
fetch https://github.com/pyside/pyside-setup/archive/refs/tags/v6.8.3.tar.gz "$work_dir/dependency-sources/pyside-setup-6.8.3.tar.gz"
fetch https://github.com/AppImage/type2-runtime/archive/fedfad51acc2860b2916a1eef32ba4a500ff31ef.tar.gz "$work_dir/dependency-sources/appimage-runtime-fedfad51.tar.gz"
fetch https://github.com/libfuse/libfuse/releases/download/fuse-3.15.0/fuse-3.15.0.tar.xz "$work_dir/dependency-sources/fuse-3.15.0.tar.xz" 70589cfd5e1cff7ccd6ac91c86c01be340b227285c5e200baa284e401eea2ca0
fetch https://github.com/vasi/squashfuse/archive/0.5.2.tar.gz "$work_dir/dependency-sources/squashfuse-0.5.2.tar.gz" db0238c5981dabbd80ee09ae15387f390091668ca060a7bc38047912491443d3
fetch https://musl.libc.org/releases/musl-1.2.5.tar.gz "$work_dir/dependency-sources/musl-1.2.5.tar.gz"
fetch https://zlib.net/fossils/zlib-1.3.2.tar.gz "$work_dir/dependency-sources/zlib-1.3.2.tar.gz"
fetch https://github.com/facebook/zstd/releases/download/v1.5.6/zstd-1.5.6.tar.gz "$work_dir/dependency-sources/zstd-1.5.6.tar.gz"
fetch https://github.com/microsoft/mimalloc/archive/refs/tags/v2.1.7.tar.gz "$work_dir/dependency-sources/mimalloc-2.1.7.tar.gz"
fetch https://github.com/void-linux/musl-fts/archive/refs/tags/v1.2.7.tar.gz "$work_dir/dependency-sources/musl-fts-1.2.7.tar.gz"
fetch https://github.com/alpinelinux/aports/archive/8f01bd5af18e4f66b7df105bdd7cab10de2ee1fa.tar.gz "$work_dir/dependency-sources/alpine-runtime-recipes-8f01bd5.tar.gz"
fetch https://github.com/GStreamer/gstreamer/archive/0a7d7071207d40cc003f6cd9f2f123f759fdfce1.tar.gz "$work_dir/dependency-sources/gstreamer-1.24.13.tar.gz"
gstreamer_stamp=$(sha256sum "$work_dir/dependency-sources/gstreamer-1.24.13.tar.gz" "$project_dir/packaging/appimage/build.sh" | sha256sum | cut -d' ' -f1)
if [[ ! -f $work_dir/gstreamer-prefix/.build-stamp || $(cat "$work_dir/gstreamer-prefix/.build-stamp") != "$gstreamer_stamp" ]]; then
    rm -rf -- "$work_dir/gstreamer-build" "$work_dir/gstreamer-prefix" "$work_dir/gstreamer-source"
    mkdir -p "$work_dir/gstreamer-source"
    tar -xf "$work_dir/dependency-sources/gstreamer-1.24.13.tar.gz" --strip-components=1 -C "$work_dir/gstreamer-source"
    meson setup "$work_dir/gstreamer-build" "$work_dir/gstreamer-source" \
        --prefix=/usr --libdir=lib --buildtype=release --auto-features=disabled --wrap-mode=nofallback \
        -Dbase=enabled -Dgood=disabled -Dbad=disabled -Dugly=disabled -Dorc=enabled -Dorc-source=system \
        -Dtests=disabled -Dtools=enabled -Dexamples=disabled -Dintrospection=disabled -Ddoc=disabled \
        -Dgst-plugins-base:app=enabled -Dgst-plugins-base:videoconvertscale=enabled -Dgst-plugins-base:videotestsrc=enabled
    ninja -C "$work_dir/gstreamer-build" -j2
    DESTDIR="$work_dir/gstreamer-prefix" meson install -C "$work_dir/gstreamer-build" --no-rebuild
    sed -i "s|^prefix=/usr$|prefix=$work_dir/gstreamer-prefix/usr|" "$work_dir/gstreamer-prefix/usr/lib/pkgconfig/"*.pc
    echo "$gstreamer_stamp" > "$work_dir/gstreamer-prefix/.build-stamp"
fi
export PKG_CONFIG_PATH="$work_dir/gstreamer-prefix/usr/lib/pkgconfig"
export LD_LIBRARY_PATH="$work_dir/gstreamer-prefix/usr/lib"
fetch https://github.com/PipeWire/pipewire/archive/4287f31510771fc999f8a58a024c81ca10b8d55f.tar.gz "$work_dir/dependency-sources/pipewire-1.6.11.tar.gz"
pipewire_stamp=$(sha256sum "$work_dir/dependency-sources/pipewire-1.6.11.tar.gz" "$project_dir/packaging/appimage/build.sh" | sha256sum | cut -d' ' -f1)
if [[ ! -f $work_dir/pipewire-prefix/usr/lib/libpipewire-0.3.so.0 || ! -f $work_dir/pipewire-prefix/.build-stamp || $(cat "$work_dir/pipewire-prefix/.build-stamp") != "$pipewire_stamp" ]]; then
    rm -rf -- "$work_dir/pipewire-build" "$work_dir/pipewire-prefix" "$work_dir/pipewire-source"
    mkdir -p "$work_dir/pipewire-source"
    tar -xf "$work_dir/dependency-sources/pipewire-1.6.11.tar.gz" --strip-components=1 -C "$work_dir/pipewire-source"
    meson setup "$work_dir/pipewire-build" "$work_dir/pipewire-source" \
        --prefix=/usr --libdir=lib --buildtype=release --auto-features=disabled \
        -Dgstreamer=enabled -Dgstreamer-device-provider=disabled -Dspa-plugins=enabled \
        -Dsupport=enabled -Dvideoconvert=enabled -Ddbus=disabled -Dsession-managers=[] \
        -Dtests=disabled -Dexamples=disabled -Dman=disabled -Ddocs=disabled \
        -Dpipewire-jack=disabled -Dpipewire-v4l2=disabled -Dpipewire-alsa=disabled
    ninja -C "$work_dir/pipewire-build" -j2
    DESTDIR="$work_dir/pipewire-prefix" meson install -C "$work_dir/pipewire-build" --no-rebuild
    echo "$pipewire_stamp" > "$work_dir/pipewire-prefix/.build-stamp"
fi
python3 "$project_dir/packaging/appimage/assemble.py" "$project_dir" "$bundle_dir" "$work_dir/dependency-sources" "$work_dir/pipewire-prefix/usr" "$work_dir/gstreamer-prefix/usr"
mkdir -p "$bundle_dir/usr/share/licenses/qt" "$bundle_dir/usr/share/licenses/appimage"
python3 - "$work_dir/dependency-sources" "$bundle_dir/usr/share/licenses" <<'PY'
from pathlib import Path
import tarfile,sys
sources, target = map(Path, sys.argv[1:])
for archive in sources.glob('*'):
    if archive.name.startswith(('qt', 'pyside-', 'appimage-runtime-', 'fuse-', 'squashfuse-', 'musl-', 'zlib-', 'zstd-', 'mimalloc-', 'pipewire-', 'gstreamer-')):
        with tarfile.open(archive) as tar:
            for entry in tar:
                if Path(entry.name).is_absolute() or '..' in Path(entry.name).parts:
                    raise RuntimeError('Unsafe source archive member: '+entry.name)
                if entry.isfile() and (any(term in Path(entry.name).name.upper() for term in ('LICENSE', 'COPYING', 'COPYRIGHT', 'LGPL', 'GPL2')) or '/LICENSES/' in entry.name):
                    component='qt' if archive.name.startswith(('qt', 'pyside-')) else 'multimedia' if archive.name.startswith(('pipewire-', 'gstreamer-')) else 'appimage'
                    dest=target/component/archive.name/entry.name
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    dest.write_bytes(tar.extractfile(entry).read())
PY
cp "$project_dir/packaging/appimage/requirements.txt" "$bundle_dir/usr/share/licenses/python-requirements.txt"
rm -rf -- "$work_dir/dependency-sources/python-wheels"
# Source-form packages provide their own build/installation metadata.
python3 -m pip download --no-deps --no-binary=:all: --dest "$work_dir/dependency-sources" python-xlib==0.33 six==1.17.0
find "$work_dir/dependency-sources" -maxdepth 1 -type f -exec sha256sum {} + > "$bundle_dir/usr/share/licenses/dependency-source-sha256.txt"
desktop-file-validate "$bundle_dir/SpeedBuilderForever.desktop"
QT_QPA_PLATFORM=offscreen "$bundle_dir/AppRun" --self-test
xvfb-run -a env QT_QPA_PLATFORM=xcb "$bundle_dir/AppRun" --self-test --x11
"$bundle_dir/AppRun" --python -m unittest discover -s "$project_dir/tests"
(cd "$project_dir" && lua5.4 tests/helper_catalog.lua)
chmod +x "$work_dir/downloads/appimagetool.AppImage"
if [[ ! -d $work_dir/appimagetool ]]; then
    (cd "$work_dir" && ./downloads/appimagetool.AppImage --appimage-extract >/dev/null && mv squashfs-root appimagetool)
fi
artifact="SpeedBuilderForever-$version-x86_64.AppImage"
ARCH=x86_64 VERSION="$version" "$work_dir/appimagetool/AppRun" \
    --runtime-file "$work_dir/downloads/runtime-x86_64" \
    --comp zstd --mksquashfs-opt -processors --mksquashfs-opt 2 \
    "$bundle_dir" "$output_dir/$artifact"
chmod 755 "$output_dir/$artifact"
# Test the final artifact without relying on FUSE or a system Python installation.
QT_QPA_PLATFORM=offscreen "$output_dir/$artifact" --appimage-extract-and-run --self-test
(cd "$output_dir" && tar -czf "$artifact.tar.gz" "$artifact")
tar --zstd -cf "$output_dir/SpeedBuilderForever-$version-dependency-sources.tar.zst" -C "$work_dir" dependency-sources
cp "$bundle_dir/usr/share/licenses/debian-packages.json" "$output_dir/dependency-manifest.json"
cp "$bundle_dir/usr/share/speedbuilder/build-info.json" "$output_dir/build-info.json"
cp "$project_dir/THIRD_PARTY_NOTICES.md" "$output_dir/THIRD_PARTY_NOTICES.md"
(cd "$output_dir" && sha256sum "$artifact" "$artifact.tar.gz" "SpeedBuilderForever-$version-dependency-sources.tar.zst" dependency-manifest.json build-info.json THIRD_PARTY_NOTICES.md > SHA256SUMS)
