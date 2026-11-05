"""Assemble a relocatable AppDir using Ubuntu 22.04 packages and pinned wheels."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

source, bundle, sources, pipewire, gstreamer = map(Path, sys.argv[1:])
usr = bundle / 'usr'
lib = usr / 'lib'
app = usr / 'share/speedbuilder'
licenses = usr / 'share/licenses'
for directory in (usr/'bin', lib, app, licenses, usr/'libexec', sources):
    directory.mkdir(parents=True, exist_ok=True)
packages = set()

def note(path):
    result = subprocess.run(['dpkg-query', '-S', str(path)], capture_output=True, text=True)
    if result.returncode == 0:
        packages.add(result.stdout.split(': /', 1)[0].splitlines()[0])

def copy(path, target):
    path = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path.resolve(), target)
    note(path)
    note(path.resolve())

copy('/usr/bin/python3.10', usr/'bin/python3.10')
shutil.copytree('/usr/lib/python3.10', lib/'python3.10', ignore=shutil.ignore_patterns('__pycache__', 'test', 'tests'), dirs_exist_ok=True)
for path in Path('/usr/lib/python3/dist-packages').glob('gi*'):
    if path.is_dir():
        shutil.copytree(path, lib/'python3/dist-packages'/path.name, ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)
for path in Path('/usr/lib/x86_64-linux-gnu/girepository-1.0').glob('*.typelib'):
    copy(path, lib/'girepository-1.0'/path.name)
for name in ('libgstapp.so', 'libgstvideoconvertscale.so', 'libgstcoreelements.so', 'libgstpipewire.so', 'libgstvideotestsrc.so'):
    origin = pipewire/'lib/gstreamer-1.0' if name == 'libgstpipewire.so' else gstreamer/'lib/gstreamer-1.0'
    copy(origin/name, lib/'gstreamer-1.0'/name)
copy(pipewire/'lib/libpipewire-0.3.so.0', lib/'libpipewire-0.3.so.0')
for path in (gstreamer/'lib').glob('libgst*.so*'):
    copy(path, lib/path.name)
scanner = next(gstreamer.glob('**/gst-plugin-scanner'))
copy(scanner, usr/'libexec/gst-plugin-scanner')
for path in (pipewire/'lib/spa-0.2').glob('**/*.so'):
    if path.parent.name in ('support', 'videoconvert', 'audioconvert'):
        copy(path, lib/'spa-0.2'/path.relative_to(pipewire/'lib/spa-0.2'))
for name in ('protocol-native', 'client-node', 'client-device', 'adapter', 'metadata', 'session-manager'):
    filename = f'libpipewire-module-{name}.so'
    origin = pipewire/'lib/pipewire-0.3'/filename
    if origin.exists():
        copy(origin, lib/'pipewire-0.3'/filename)
copy(pipewire/'share/pipewire/client.conf', usr/'share/pipewire/client.conf')
site = lib/'python3.10/site-packages'
subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-index', '--find-links=/opt/wheels', '--target', str(site), '-r', str(source/'packaging/appimage/requirements.txt')], check=True)
qt = site/'PySide6/Qt'
# Only the LGPL Qt modules used by this Widgets application and its platform plugins.
qt_modules = {'Core', 'Gui', 'Widgets', 'DBus', 'Network', 'OpenGL', 'XcbQpa', 'WaylandClient', 'WaylandEglClientHwIntegration', 'Svg'}
for path in (qt/'lib').glob('libQt6*.so*'):
    module = path.name.split('.so')[0].removeprefix('libQt6')
    if module not in qt_modules:
        path.unlink()
for path in (site/'PySide6').glob('Qt*.abi3.so'):
    if path.name.split('.')[0] not in ('QtCore', 'QtGui', 'QtWidgets'):
        path.unlink()
for path in (site/'PySide6').glob('libpyside6qml*'):
    path.unlink()
for path in (site/'PySide6').iterdir():
    if path.is_file() and '.so' not in path.name and path.read_bytes()[:4] == b'\x7fELF':
        path.unlink()
for child in list((qt/'plugins').iterdir()):
    if child.name not in ('platforms', 'wayland-graphics-integration-client', 'wayland-shell-integration', 'iconengines', 'imageformats'):
        shutil.rmtree(child) if child.is_dir() else child.unlink()
for path in (qt/'plugins/platforms').glob('*.so'):
    if path.name not in ('libqxcb.so', 'libqwayland-generic.so', 'libqwayland-egl.so', 'libqoffscreen.so', 'libqminimal.so'):
        path.unlink()
for path in (qt/'plugins/wayland-shell-integration').glob('*.so'):
    if path.name != 'libxdg-shell.so':
        path.unlink()
for path in (qt/'plugins/imageformats').glob('*.so'):
    if path.name not in ('libqsvg.so', 'libqjpeg.so', 'libqico.so', 'libqgif.so'):
        path.unlink()
for name in ('qml', 'translations', 'metatypes', 'libexec', 'bin'):
    shutil.rmtree(qt/name, ignore_errors=True)
for name in ('include', 'typesystems', 'examples', 'scripts', 'glue', 'support'):
    shutil.rmtree(site/'PySide6'/name, ignore_errors=True)

# Bundle the closure of native dependencies, leaving libc and graphics drivers to the host.
exclude = re.compile(r'^(ld-linux|lib(c|m|pthread|dl|rt|resolv|util|anl|nss_[^.]+|gcc_s|stdc\+\+)\.so|lib(GL|GLX|EGL|OpenGL|GLdispatch|gbm|drm|vulkan)[^.]*\.so)')
queue = [p for p in usr.rglob('*') if p.is_file() and ('.so' in p.name or p == usr/'bin/python3.10' or p == usr/'libexec/gst-plugin-scanner')]
visited = set()
while queue:
    path = queue.pop()
    if path in visited:
        continue
    visited.add(path)
    environment = {**os.environ, 'LD_LIBRARY_PATH': f'{lib}:{lib / "pipewire-0.3"}:{qt / "lib"}'}
    result = subprocess.run(['ldd', str(path)], capture_output=True, text=True, env=environment)
    for line in result.stdout.splitlines():
        match = re.match(r'\s*(\S+) => (/\S+)', line)
        if not match:
            if 'not found' in line:
                raise RuntimeError(f'Unresolved dependency of {path}: {line}')
            continue
        name, origin = match.groups()
        if exclude.match(name) or str(bundle) in origin:
            continue
        destination = lib/name
        if not destination.exists():
            copy(origin, destination)
            queue.append(destination)
# GI loads these via typelib metadata rather than ELF DT_NEEDED entries.
for name in ('libgirepository-1.0.so.1', 'libgstreamer-1.0.so.0', 'libgstapp-1.0.so.0', 'libgstvideo-1.0.so.0', 'libgstbase-1.0.so.0', 'libgio-2.0.so.0', 'libgmodule-2.0.so.0'):
    origin = Path('/usr/lib/x86_64-linux-gnu')/name
    if not (lib/name).exists():
        copy(origin, lib/name)

for name in ('linux', 'addon', 'packaging'):
    shutil.copytree(source/name, app/name, ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)
for name in ('README.md', 'VERSION', 'THIRD_PARTY_NOTICES.md'):
    shutil.copy2(source/name, app/name)
copy(source/'packaging/appimage/AppRun', bundle/'AppRun')
copy(source/'packaging/appimage/speedbuilderforever.desktop', bundle/'SpeedBuilderForever.desktop')
copy(source/'linux/assets/speedbuilderforever.svg', bundle/'speedbuilderforever.svg')
(bundle/'.DirIcon').symlink_to('speedbuilderforever.png')
(usr/'bin/SpeedBuilderForever').symlink_to('../../AppRun')
packages.update(('python3.10-minimal', 'libpython3.10-stdlib', 'python3-gi', 'python3-gst-1.0'))
manifest = []
source_packages = set()
for package in sorted(packages):
    fields = subprocess.check_output(['dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${source:Package}\t${source:Version}', package], text=True).strip().split('\t')
    binary, version, src, src_version = fields
    manifest.append(dict(package=binary, version=version, source=src or binary.split(':')[0], source_version=src_version or version))
    source_packages.add((src or binary.split(':')[0], src_version or version))
    doc = Path('/usr/share/doc')/binary.split(':')[0]/'copyright'
    if doc.exists():
        copy(doc, licenses/'debian'/binary.replace(':', '_')/'copyright')
    else:
        raise RuntimeError(f'Missing copyright file for bundled package {binary}')
(licenses/'debian-packages.json').write_text(json.dumps(manifest, indent=2)+'\n')
# Publish corresponding Debian source packages alongside the AppImage.
for package, version in sorted(source_packages):
    subprocess.run(['apt-get', 'source', '--download-only', f'{package}={version}'], cwd=sources, check=True)
referenced_sources = set()
for package, version in sorted(source_packages):
    control = next((path for path in sources.glob(f'{package}_*.dsc')
                    if f'Version: {version}\n' in path.read_text()), None)
    if control is None:
        raise RuntimeError(f'Missing source control file for {package}={version}')
    referenced_sources.add(control.name)
    referenced_sources.update(re.findall(r'^ [a-f0-9]{32} \d+ (\S+)$', control.read_text(), re.MULTILINE))
for path in sources.iterdir():
    if path.name.startswith('pipewire-') and '.orig' not in path.name and path.name.endswith('.tar.gz') and path.name != 'pipewire-1.6.11.tar.gz':
        path.unlink()
        continue
    if path.is_file() and (path.suffix == '.dsc' or '.orig' in path.name or '.debian.tar.' in path.name or path.name.endswith('.diff.gz')):
        if path.name not in referenced_sources:
            path.unlink()
# Preserve distribution metadata and its license files for all wheels.
for metadata in site.glob('*.dist-info'):
    shutil.copytree(metadata, licenses/'python'/metadata.name, dirs_exist_ok=True)
(licenses/'NOTICE.md').write_text((source/'THIRD_PARTY_NOTICES.md').read_text())
snapshot = (source/'BUILD-SNAPSHOT.txt').read_text().splitlines() if (source/'BUILD-SNAPSHOT.txt').exists() else []
revision = os.environ.get('SOURCE_REVISION') or (snapshot[0] if snapshot else 'unknown')
info = {'version': (source/'VERSION').read_text().strip(), 'source_revision': revision,
        'source_dirty': bool(snapshot[1:]), 'architecture': 'x86_64', 'build_base': 'Ubuntu 22.04',
        'python': sys.version.split()[0], 'qt': '6.8.3', 'pipewire': '1.6.11', 'gstreamer': '1.24.13'}
(app/'build-info.json').write_text(json.dumps(info, indent=2)+'\n')
# File managers need a raster thumbnail even though the application uses SVG.
subprocess.run([str(bundle/'AppRun'), '--python', '-c',
                'from PySide6.QtWidgets import QApplication; from PySide6.QtGui import QIcon; '
                'import sys; app=QApplication([]); '
                'assert QIcon(sys.argv[1]).pixmap(256, 256).save(sys.argv[2])',
                str(bundle/'speedbuilderforever.svg'), str(bundle/'speedbuilderforever.png')],
               env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, check=True)
for path in bundle.rglob('*'):
    if path.is_file() and not path.is_symlink():
        path.chmod(0o755 if os.access(path, os.X_OK) else 0o644)
for path in bundle.rglob('__pycache__'):
    shutil.rmtree(path)
print(f'Bundled {len(manifest)} Debian packages; retained corresponding source packages.')
