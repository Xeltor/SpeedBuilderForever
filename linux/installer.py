"""Find the Forever client and install the bundled helper."""

from pathlib import Path
from tempfile import TemporaryDirectory


HELPER = "SpeedBuilderForeverHelper"
FILES = ("Calibration.lua", f"{HELPER}.toc")
SOURCE = Path(__file__).resolve().parents[1] / "addon" / HELPER
PRODUCTS = ("_forever_", "_classic_beta_")


def forever_folder(path):
    path = Path(path).expanduser().resolve()
    if path.name == "AddOns" and path.parent.name == "Interface":
        path = path.parent.parent
    elif path.name == "Interface":
        path = path.parent

    candidates = (path,) if path.name in PRODUCTS else tuple(path / name for name in PRODUCTS)
    for candidate in candidates:
        if not (candidate / "Wow.exe").is_file():
            continue
        if candidate.name == "_classic_beta_":
            toc = candidate / "Interface/AddOns/Hekili/Hekili.toc"
            # Battle.net used this name for other Classic betas as well.
            if not toc.is_file() or not any(line.startswith("## Interface: 16001")
                                            for line in toc.read_text(encoding="utf-8-sig").splitlines()):
                continue
        return candidate
    return None


def running_clients(proc=Path("/proc")):
    folders = set()
    running = False
    for process in proc.glob("[0-9]*"):
        try:
            if (process / "comm").read_text(errors="replace").strip().lower() != "wow.exe":
                continue
            running = True
            folder = forever_folder(process / "cwd")
            if folder:
                folders.add(folder)
        except (OSError, RuntimeError):
            continue
    return folders, running


def find_forever(remembered, running, home=None):
    if len(running) == 1:
        return next(iter(running))
    remembered = forever_folder(remembered) if isinstance(remembered, (Path, str)) and remembered else None
    if len(running) > 1:
        return remembered if remembered in running else None
    if remembered:
        return remembered

    home = home or Path.home()
    roots = (
        "World of Warcraft",
        "Games/World of Warcraft",
        "Games/*/drive_c/Program Files*/World of Warcraft",
        ".wine/drive_c/Program Files*/World of Warcraft",
        ".local/share/bottles/bottles/*/drive_c/Program Files*/World of Warcraft",
        ".var/app/com.usebottles.bottles/data/bottles/bottles/*/drive_c/Program Files*/World of Warcraft",
        ".local/share/Steam/steamapps/compatdata/*/pfx/drive_c/Program Files*/World of Warcraft",
        ".var/app/com.valvesoftware.Steam/data/Steam/steamapps/compatdata/*/pfx/drive_c/Program Files*/World of Warcraft",
    )
    found = {folder for pattern in roots for path in home.glob(pattern)
             if (folder := forever_folder(path))}
    return next(iter(found)) if len(found) == 1 else None


def install_helper(folder):
    addons = folder / "Interface/AddOns"
    target = addons / HELPER
    if not (addons / "Hekili/Hekili.toc").is_file():
        raise ValueError("Install Hekili Forever in that game folder first.")
    if target.is_symlink() or any((target / name).is_symlink() for name in FILES):
        raise ValueError("The helper folder or files point elsewhere. Replace the linked helper manually.")
    fresh = not (target / FILES[-1]).is_file()
    payload = {name: (SOURCE / name).read_bytes() for name in FILES}
    changed = [name for name, data in payload.items()
               if not (target / name).is_file() or (target / name).read_bytes() != data]
    if not changed:
        return "current"

    # Stage before publishing. The TOC is installed last for an existing folder.
    with TemporaryDirectory(prefix=".speedbuilder-helper-", dir=addons) as temporary:
        stage = Path(temporary) / HELPER
        stage.mkdir()
        for name in changed:
            (stage / name).write_bytes(payload[name])
        if target.exists():
            for name in changed:
                (stage / name).replace(target / name)
        else:
            stage.rename(target)
    return "installed" if fresh else "updated"
