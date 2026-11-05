"""Named profiles managed by the desktop app."""

import json
from uuid import uuid4


class Profiles:
    def __init__(self, engine):
        self.engine = engine
        self.directory = engine.default_profile("x11").parent / "profiles"
        self.settings_path = self.directory.parent / "desktop.json"
        self.entries = {}
        self.warnings = []

        for path in sorted(self.directory.glob("*.json")):
            try:
                profile = self.read(path)
                if not isinstance(profile.get("name"), str) or not profile["name"].strip():
                    raise ValueError("Missing profile name.")
                self.entries[path] = profile
            except (OSError, ValueError):
                self.warnings.append(f"Could not read a saved profile ({path.name}).")

        self.active = None
        self.settings = {}
        try:
            self.settings = self.read(self.settings_path)
            active = self.settings.get("active")
            self.active = next((path for path in self.entries if path.name == active), None)
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.warnings.append("Could not read the last profile selection.")

    @staticmethod
    def read(path):
        with path.open(encoding="utf-8") as source:
            data = json.load(source)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def remember(self, path):
        self.save_settings({**self.settings, "active": path.name if path else None})
        self.active = path

    def toggle(self, backend):
        value = self.settings.get(f"toggle_{backend}")
        return value if isinstance(value, str) and value else self.engine.default_toggle(backend)

    def remember_toggle(self, backend, binding):
        self.save_settings({**self.settings, f"toggle_{backend}": binding})

    def save_settings(self, settings):
        self.engine.write_profile(self.settings_path, settings)
        self.settings = settings

    def validate_name(self, name, exclude=None):
        name = name.strip()
        if not name or not name.isprintable():
            raise ValueError("Enter a profile name.")
        if len(name) > 60:
            raise ValueError("Use 60 characters or fewer.")
        if any(path != exclude and profile["name"].casefold() == name.casefold()
               for path, profile in self.entries.items()):
            raise ValueError("That profile name is already in use.")
        return name

    def create(self, name, backend):
        path = self.directory / f"{uuid4().hex}.json"
        self.save(path, {"name": self.validate_name(name), "backend": backend})
        return path

    def save(self, path, profile):
        self.engine.write_profile(path, profile)
        self.entries[path] = profile

    def save_calibration(self, path, previous, window, size, points):
        if self.read(path) != previous:
            raise ValueError("The profile changed during calibration. Recalibrate the icon again.")
        calibration = {"window": window, "window_size": size, "points": points}
        updated = {"bindings": {}, **previous, **calibration}
        collisions = previous.get("collisions", {})
        relearn = any(rule["matches"] for rule in collisions.values()) and any(
            previous.get(field) != value for field, value in calibration.items()
        )
        if relearn:
            # Extra samples belong to the old position. Keep shared colors blocked.
            updated["collisions"] = {color: {**rule, "points": [], "matches": {}}
                                     for color, rule in collisions.items()}
        self.save(path, updated)
        return relearn

    def rename(self, path, name):
        name = self.validate_name(name, exclude=path)
        self.save(path, {**self.read(path), "name": name})

    def delete(self, path):
        path.unlink()
        del self.entries[path]
