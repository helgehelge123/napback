"""Independent backup jobs; each owns a config, repository and timer."""

from __future__ import annotations

import copy
import re
import threading
import uuid
from pathlib import Path

from . import core


def paths(primary):
    primary = Path(primary).absolute()
    result = [("default", primary)]
    directory = primary.with_suffix(".profiles")
    core.no_symlink(directory)
    if directory.exists():
        result += [
            (p.stem, p)
            for p in sorted(directory.glob("*.json"))
            if re.fullmatch(r"[0-9a-f]{32}", p.stem)
        ]
    return result


class Profiles:
    def __init__(self, primary):
        self.primary = primary.config_path
        self.directory = self.primary.with_suffix(".profiles")
        self.key_path = self.primary.with_suffix(".recovery-key")
        self.apps = {"default": primary}
        self.lock = threading.RLock()
        primary.profiles = self
        primary.profile_id = "default"

    def get(self, ident="default"):
        with self.lock:
            if ident in self.apps:
                return self.apps[ident]
            if not re.fullmatch(r"[0-9a-f]{32}", ident):
                raise core.BackupError("Unbekannter Sicherungsauftrag.")
            path = self.directory / (ident + ".json")
            core.no_symlink(path)
            if not path.is_file():
                raise core.BackupError("Sicherungsauftrag nicht gefunden.")
            from .webapp import Application

            app = Application(path)
            app.profiles, app.profile_id = self, ident
            self.apps[ident] = app
            return app

    def listing(self):
        with self.lock:
            ids = dict(paths(self.primary))
            ids.update({ident: app.config_path for ident, app in self.apps.items()})
            result = []
            for ident, path in ids.items():
                core.no_symlink(path)
                app = self.get(ident)
                if path.exists():
                    try:
                        config = core.Config.load(path)
                        item = dict(
                            id=ident,
                            label=config.label,
                            target=str(config.target),
                            storage=config.storage,
                            configured=True,
                        )
                    except (core.BackupError, OSError):
                        item = dict(
                            id=ident, label="Auftrag mit Konfigurationsfehler", configured=True
                        )
                else:
                    item = dict(
                        id=ident,
                        label=app.draft_defaults.get("label", "Mein Backup"),
                        configured=False,
                    )
                result.append(item)
            return result

    def create(self, source=None):
        from .webapp import Application

        with self.lock:
            if len(self.listing()) >= 50:
                raise core.BackupError("Es sind bereits 50 Aufträge geöffnet oder gespeichert.")
            defaults = self.get(source or "default").initial()["defaults"]
            ident = uuid.uuid4().hex
            app = Application(self.directory / (ident + ".json"))
            app.profiles, app.profile_id = self, ident
            if source and self.get(source).config_path.exists():
                app.inherited_settings = core.read_json(self.get(source).config_path)
            app.draft_defaults = copy.deepcopy(defaults)
            app.draft_defaults.update(
                target="",
                automatic=False,
                label=(defaults["label"] + " – Kopie")[:100] if source else "Neues Backup",
            )
            if not source:
                app.draft_defaults.update(
                    selected=[],
                    selected_sources=[],
                    backup_napback_config=False,
                    backup_truenas_config=False,
                    backup_truenas_apps=False,
                )
            self.apps[ident] = app
            return {"profile": ident}

    def check_target(self, ident, target):
        target = Path(target)
        for other, path in paths(self.primary):
            if other == ident or not path.exists():
                continue
            config = core.Config.load(path)
            if config.config_key_file:
                key_path = Path(config.config_key_file)
                if target == key_path or target in key_path.parents:
                    raise core.BackupError(
                        "Der Wiederherstellungsschlüssel eines anderen Auftrags muss außerhalb des Zielordners bleiben."
                    )
            if (
                target == config.target
                or target in config.target.parents
                or config.target in target.parents
            ):
                raise core.BackupError(
                    "Dieser Zielordner überschneidet sich mit dem Auftrag „"
                    + config.label
                    + "“. Wähle einen eigenen Ordner für jede Kopie."
                )
        if target == self.key_path or target in self.key_path.parents:
            raise core.BackupError(
                "Der Wiederherstellungsschlüssel muss außerhalb aller Backup-Ziele liegen."
            )
