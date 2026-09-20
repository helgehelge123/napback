"""Local browser interface. NAS discovery is read-only; settings require review."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import webbrowser
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__, core, integrity
from .wizard import Guide

ASSETS = Path(__file__).with_name("web")


def config_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def load_settings(data):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        core.write_json(path, data)
        return core.Config.load(path)


def connection(data):
    guide = Guide("de")
    host = guide.host(str(data.get("host", "")).strip())
    key = guide.key(str(data.get("key", "")).strip())
    sudo = data.get("sudo", True)
    if not isinstance(sudo, bool):
        raise core.BackupError("Bitte die Rechte-Einstellung prüfen.")
    options = ["-i", key, "-oIdentitiesOnly=yes"] if key else []
    return core.Config(
        Path("/"), str(uuid.uuid4()), Path("/"), [], host=host, ssh_options=options, sudo=sudo
    )


def discover(probe):
    """Return metadata only. Never retrieve key bytes or dataset file contents."""
    rows = probe.remote(
        [
            "zfs",
            "list",
            "-H",
            "-p",
            "-t",
            "filesystem,volume",
            "-o",
            "name,type,used,referenced,encryption,mountpoint",
        ]
    )
    datasets = {}
    for line in rows.splitlines():
        name, kind, used, referenced, encryption, mountpoint = line.split("\t")
        datasets[name] = {
            "name": name,
            "type": kind,
            "used": int(used),
            "referenced": int(referenced),
            "encrypted": encryption != "off",
            "mountpoint": mountpoint,
            "snapshots": [],
        }
    rows = probe.remote(["zfs", "list", "-H", "-p", "-t", "snapshot", "-o", "name,creation,guid"])
    for line in rows.splitlines():
        full, created, guid = line.split("\t")
        name, tag = full.rsplit("@", 1)
        if name in datasets:
            datasets[name]["snapshots"].append({"name": tag, "created": int(created), "guid": guid})
    for dataset in datasets.values():
        dataset["snapshots"].sort(key=lambda s: (s["created"], s["name"]), reverse=True)
    tasks = []
    task_note = ""
    try:
        for task in json.loads(probe.remote(["midclt", "call", "pool.snapshottask.query"])):
            tasks.append(
                {
                    key: task.get(key)
                    for key in (
                        "dataset",
                        "recursive",
                        "exclude",
                        "enabled",
                        "naming_schema",
                        "schedule",
                    )
                }
            )
    except (core.BackupError, ValueError, TypeError, subprocess.TimeoutExpired):
        task_note = (
            "Snapshot-Zeitpläne konnten nicht gelesen werden. Vorhandene Snapshots sind sichtbar."
        )
    return {
        "datasets": list(datasets.values()),
        "tasks": tasks,
        "task_note": task_note,
        "read_at": time.time(),
    }


def public_catalog(catalog):
    result = copy.deepcopy(catalog)
    for dataset in result["datasets"]:
        dataset["snapshot_count"] = len(dataset["snapshots"])
        dataset["snapshots"] = dataset["snapshots"][:3]
    return result


def sources_from_selection(catalog, selected):
    if (
        not isinstance(selected, list)
        or not selected
        or not all(isinstance(x, str) for x in selected)
    ):
        raise core.BackupError("Wähle mindestens einen Datenbereich aus.")
    available = {d["name"]: d for d in catalog["datasets"]}
    selected = set(selected)
    if selected - available.keys():
        raise core.BackupError(
            "Die Datenbereiche haben sich geändert. Bitte die NAS-Liste neu laden."
        )
    if any(available[name]["type"] != "filesystem" for name in selected):
        raise core.BackupError("Virtuelle Festplatten (Zvols) werden noch nicht unterstützt.")
    roots = sorted(
        name for name in selected if name.rsplit("/", 1)[0] not in selected or "/" not in name
    )
    sources = []
    for root in roots:
        exclusions = []
        for name in sorted(available, key=lambda n: (n.count("/"), n)):
            if not name.startswith(root + "/") or name in selected:
                continue
            if not any(name.startswith(x + "/") for x in exclusions):
                exclusions.append(name)
        sources.append(
            {
                "name": "dataset-" + hashlib.sha256(root.encode()).hexdigest()[:12],
                "dataset": root,
                "recursive": True,
                "exclude": exclusions,
            }
        )
    return sources


def analyze(catalog, sources, storage="zfs_raw", prefix="", now=None):
    now = time.time() if now is None else now
    available = {d["name"]: d for d in catalog["datasets"]}
    result = {"groups": [], "issues": [], "excluded": [], "count": 0, "referenced": 0}
    all_selected = set()
    for source in sources:
        root = source["dataset"]
        excluded = source.get("exclude", [])
        names = [
            name
            for name in available
            if (name == root or name.startswith(root + "/"))
            and not any(name == x or name.startswith(x + "/") for x in excluded)
        ]
        result["excluded"].extend(excluded)
        if root not in names:
            result["issues"].append(f"{root}: nicht mehr auf dem NAS vorhanden.")
            continue
        members = [available[name] for name in names]
        all_selected.update(names)
        group = {
            "dataset": root,
            "count": len(names),
            "members": names,
            "snapshot": None,
            "examples": available[root]["snapshots"][:3],
            "issues": [],
            "unavailable": [],
        }
        volumes = [d["name"] for d in members if d["type"] != "filesystem"]
        if volumes:
            group["issues"].append(
                "Virtuelle Festplatten werden noch nicht unterstützt: " + ", ".join(volumes)
            )
        unencrypted = [d["name"] for d in members if not d["encrypted"]]
        if storage == "zfs_raw" and unencrypted:
            group["issues"].append(
                "Auf dem NAS nicht verschlüsselt: "
                + ", ".join(unencrypted)
                + ". Wähle diese Bereiche ab oder bewusst die unverschlüsselte Dateikopie."
            )
        missing = [
            d["name"]
            for d in members
            if not any(s["name"].startswith(prefix) for s in d["snapshots"])
        ]
        if missing:
            group["unavailable"] = missing
            group["issues"].append(
                "Kein passender Snapshot für: "
                + ", ".join(missing)
                + ". Wähle die Bereiche ab oder richte dafür Snapshots auf TrueNAS ein."
            )
        else:
            rows = [
                [d["name"] + "@" + s["name"], s["created"], s["guid"]]
                for d in members
                for s in d["snapshots"]
            ]
            try:
                tag, times = core.choose_common_snapshot(names, rows, prefix, now, 48 * 3600)
                group["snapshot"] = {
                    "name": tag,
                    "created": min(times[name][tag] for name in names),
                }
            except core.BackupError as error:
                if "too old" in str(error):
                    message = "Der neueste gemeinsame Snapshot ist älter als zwei Tage. Prüfe den Snapshot-Auftrag auf TrueNAS."
                elif "future" in str(error):
                    message = "Die Uhrzeit von NAS und PC passt nicht zusammen. Prüfe die Uhren."
                else:
                    message = "Es gibt noch keinen Snapshot, der alle gewählten Unterbereiche zusammen abdeckt. Neu angelegte Bereiche brauchen erst einen gemeinsamen Snapshot."
                    root_snapshots = [
                        s for s in available[root]["snapshots"] if s["name"].startswith(prefix)
                    ]
                    if root_snapshots:
                        latest = root_snapshots[0]["name"]
                        group["unavailable"] = [
                            d["name"]
                            for d in members
                            if not any(s["name"] == latest for s in d["snapshots"])
                        ]
                        message += (
                            " Für " + latest + " fehlen: " + ", ".join(group["unavailable"]) + "."
                        )
                group["issues"].append(message)
        result["groups"].append(group)
        result["issues"].extend(f"{root}: {issue}" for issue in group["issues"])
    result["count"] = len(all_selected)
    result["referenced"] = sum(available[name]["referenced"] for name in all_selected)
    result["excluded"] = sorted(set(result["excluded"]))
    return result


def friendly_error(error):
    detail = str(error)
    if "No common ZFS snapshot" in detail:
        message = "Für die gewählten Bereiche fehlt ein gemeinsamer Snapshot. Öffne die Einstellungen und prüfe die Hinweise bei der Datenauswahl."
    elif "Newest common ZFS snapshot is too old" in detail:
        message = "Die vorhandenen Snapshots sind zu alt. Prüfe auf TrueNAS, ob der regelmäßige Snapshot-Auftrag noch läuft."
    elif "is not encrypted" in detail:
        message = "Ein gewählter Bereich ist auf dem NAS nicht verschlüsselt. Öffne die Datenauswahl und prüfe, welche Bereiche eingeschlossen sind."
    elif "Expected target filesystem is not mounted" in detail:
        message = "Das Backup-Laufwerk ist nicht am erwarteten Ort eingebunden. Schließe es an und prüfe den Zielordner."
    elif "Another backup" in detail:
        message = "Eine Sicherung oder Prüfung läuft bereits. Warte, bis sie abgeschlossen ist."
    elif "Host key verification failed" in detail:
        message = "Dein PC kennt den NAS-Schlüssel noch nicht oder er hat sich geändert. Prüfe den Fingerabdruck und bestätige die SSH-Verbindung einmal im Terminal."
    elif "Permission denied" in detail or "Authentication failed" in detail:
        message = "Die Anmeldung wurde abgelehnt. Prüfe NAS-Benutzer und SSH-Schlüssel. Der öffentliche Schlüssel muss beim TrueNAS-Benutzer eingetragen sein."
    elif "password is required" in detail or "not allowed to execute" in detail:
        message = "Der NAS-Benutzer darf die ZFS-Befehle noch nicht ohne Passwort ausführen. Prüfe seine Sudo-Rechte in TrueNAS."
    elif "timed out" in detail or isinstance(error, subprocess.TimeoutExpired):
        message = "Das NAS antwortet nicht rechtzeitig. Prüfe, ob es eingeschaltet und im Netzwerk erreichbar ist."
    elif (
        "Connection refused" in detail
        or "No route to host" in detail
        or "resolve hostname" in detail
    ):
        message = (
            "Keine Verbindung zum NAS. Prüfe die Adresse und ob der SSH-Dienst auf TrueNAS läuft."
        )
    elif isinstance(error, (core.BackupError, ValueError)):
        message = detail
    else:
        message = "Der Vorgang konnte nicht abgeschlossen werden. Die technischen Details stehen darunter."
    return {"error": message, "detail": detail[-2500:]}


class Application:
    def __init__(self, config_path):
        self.config_path = Path(config_path).absolute()
        self.probe = None
        self.catalog = None
        self.review = None
        self.jobs = {}
        self.lock = threading.Lock()
        self.operation = threading.Lock()

    def timer_unit(self):
        from .cli import default_config

        if self.config_path == default_config().absolute():
            return "napback"
        return "napback-" + hashlib.sha256(str(self.config_path).encode()).hexdigest()[:12]

    def job(self, function):
        ident = secrets.token_urlsafe(12)
        with self.lock:
            if len(self.jobs) > 50:
                self.jobs = {
                    key: value for key, value in self.jobs.items() if value["state"] == "running"
                }
            self.jobs[ident] = {"state": "running"}

        def run():
            try:
                if not self.operation.acquire(blocking=False):
                    raise core.BackupError(
                        "Ein Vorgang läuft bereits. Bitte warte, bis er fertig ist."
                    )
                try:
                    result = function()
                finally:
                    self.operation.release()
                value = {"state": "done", "result": result}
            except Exception as error:
                value = {"state": "failed", **friendly_error(error)}
            with self.lock:
                self.jobs[ident] = value

        threading.Thread(target=run, daemon=True).start()
        return {"job": ident}

    def initial(self):
        defaults = {
            "host": "",
            "key": "",
            "sudo": True,
            "selected": [],
            "target": str(Path.home() / "NAS-Backup"),
            "storage": "zfs_raw",
            "check_interval_minutes": 1,
            "keep": 30,
            "automatic": False,
        }
        defaults_file = self.config_path.with_name("ui-defaults.json")
        if defaults_file.exists():
            saved = core.read_json(defaults_file)
            defaults.update({key: value for key, value in saved.items() if key in defaults})
        existing = None
        if self.config_path.exists():
            config = core.Config.load(self.config_path)
            existing = asdict(config)
            defaults.update({key: existing[key] for key in defaults if key in existing})
            defaults["target"] = str(config.target)
            defaults["selected_sources"] = config.sources
            options = config.ssh_options or []
            if "-i" in options and options.index("-i") + 1 < len(options):
                defaults["key"] = options[options.index("-i") + 1]
            defaults["automatic"] = (
                subprocess.run(
                    ["systemctl", "--user", "is-enabled", "--quiet", self.timer_unit() + ".timer"],
                    capture_output=True,
                    timeout=10,
                ).returncode
                == 0
            )
        keys = []
        ssh = Path.home() / ".ssh"
        if ssh.is_dir():
            for public in ssh.glob("*.pub"):
                private = public.with_suffix("")
                if private.is_file():
                    keys.append(str(private))
        return {
            "version": __version__,
            "configured": existing is not None,
            "defaults": defaults,
            "keys": sorted(keys),
            "config_path": str(self.config_path),
        }

    def connect(self, payload):
        self.review = None
        self.probe = None
        self.catalog = None
        probe = connection(payload)
        # Keep advanced SSH options when editing an existing connection unchanged.
        if self.config_path.exists():
            old = core.Config.load(self.config_path)
            options = old.ssh_options or []
            old_key = (
                options[options.index("-i") + 1]
                if "-i" in options and options.index("-i") + 1 < len(options)
                else ""
            )
            if old.host == probe.host and old_key == payload.get("key", ""):
                probe.ssh_options = old.ssh_options
        catalog = discover(probe)
        self.probe, self.catalog = probe, catalog
        return public_catalog(catalog)

    def selection(self, payload):
        if self.catalog is None or self.probe is None:
            raise core.BackupError("Verbinde Dich zuerst mit Deinem NAS.")
        sources = sources_from_selection(self.catalog, payload.get("selected"))
        storage = Guide("de").storage(str(payload.get("storage", "zfs_raw")))
        return sources, storage

    def preview(self, payload):
        sources, storage = self.selection(payload)
        return analyze(self.catalog, sources, storage)

    def settings(self, payload, sources, storage):
        if not payload.get("target"):
            raise core.BackupError("Wähle einen Zielordner auf Deinem PC.")
        target_path = Path(str(payload["target"])).expanduser()
        if not target_path.is_absolute():
            raise core.BackupError(
                "Wähle den Zielordner mit „Ordner auswählen“ oder gib einen vollständigen Pfad mit / oder ~/ am Anfang ein."
            )
        target = str(target_path)
        existing = core.Config.load(self.config_path) if self.config_path.exists() else None
        if existing and str(existing.target) == target:
            if existing.storage != storage:
                raise core.BackupError(
                    "Für einen anderen Speichermodus brauchst Du einen neuen, leeren Zielordner. Alte Backups bleiben erhalten."
                )
            core.check_repository(existing)
            data = core.read_json(self.config_path)
            # Preserve existing source IDs so changing the interval does not rename archives.
            old_names = {s.get("dataset"): s["name"] for s in existing.sources}
            sources = [{**s, "name": old_names.get(s["dataset"], s["name"])} for s in sources]
        else:
            Guide("de").destination(target)
            data = {"target": target, "mountpoint": "/", "repository_id": str(uuid.uuid4())}
        minutes = Guide("de").interval(str(payload.get("check_interval_minutes", 1)))
        keep = payload.get("keep", 30)
        if isinstance(keep, bool) or not isinstance(keep, int) or keep < 0:
            raise core.BackupError(
                "Die Anzahl gespeicherter Stände muss 0 oder größer sein. 0 bedeutet alle behalten."
            )
        data.update(
            host=self.probe.host,
            sudo=self.probe.sudo,
            ssh_options=self.probe.ssh_options,
            sources=sources,
            storage=storage,
            snapshot_prefix="",
            trigger="new_snapshot",
            check_interval_minutes=minutes,
            keep=keep,
        )
        load_settings(data)
        return data

    def review_settings(self, payload):
        self.review = None
        if self.probe is None:
            raise core.BackupError("Verbinde Dich zuerst mit Deinem NAS.")
        fresh = discover(self.probe)
        # Dataset changes require a deliberate new selection, not silent inclusions.
        before = {d["name"] for d in self.catalog["datasets"]}
        after = {d["name"] for d in fresh["datasets"]}
        if before != after:
            raise core.BackupError(
                "Auf dem NAS wurden Datenbereiche angelegt oder entfernt. Lade die Auswahl neu, damit Du sie prüfen kannst."
            )
        self.catalog = fresh
        sources, storage = self.selection(payload)
        report = analyze(self.catalog, sources, storage)
        if report["issues"]:
            return {"ok": False, "report": report}
        data = self.settings(payload, sources, storage)
        checked = load_settings(data)
        planned = core.plan_sources(checked, time.time())
        target = Path(data["target"])
        parent = target
        while not parent.exists():
            parent = parent.parent
        free = shutil.disk_usage(parent).free
        if free < checked.min_free_bytes:
            raise core.BackupError(
                "Auf dem Ziellaufwerk ist weniger als 1 GiB frei. Wähle ein anderes Laufwerk oder schaffe Platz."
            )
        automatic = payload.get("automatic", False)
        if not isinstance(automatic, bool):
            raise core.BackupError("Ungültige Einstellung für automatische Backups.")
        token = secrets.token_urlsafe(24)
        self.review = {
            "token": token,
            "data": data,
            "automatic": automatic,
            "old_hash": config_hash(self.config_path),
            "time": time.time(),
            "planned": sorted(s.dataset for s in planned),
        }
        return {
            "ok": True,
            "review_id": token,
            "report": report,
            "target": data["target"],
            "host": data["host"],
            "storage": storage,
            "free": free,
            "check_interval_minutes": checked.check_interval_minutes,
            "keep": checked.keep,
            "automatic": automatic,
        }

    def save(self, payload):
        from .cli import backup_existing, install_timer

        review = self.review
        if not review or not secrets.compare_digest(
            str(payload.get("review_id", "")), review["token"]
        ):
            raise core.BackupError("Prüfe Deine Auswahl zuerst in der Zusammenfassung.")
        if time.time() - review["time"] > 900:
            raise core.BackupError(
                "Die Prüfung ist älter als 15 Minuten. Bitte prüfe die Auswahl erneut."
            )
        if config_hash(self.config_path) != review["old_hash"]:
            raise core.BackupError(
                "Die Konfiguration wurde inzwischen geändert. Bitte lade die Seite neu."
            )
        checked = load_settings(review["data"])
        planned = core.plan_sources(checked, time.time())
        if sorted(s.dataset for s in planned) != review["planned"]:
            raise core.BackupError(
                "Die Datenbereiche haben sich geändert. Bitte prüfe die Auswahl erneut."
            )
        if config_hash(self.config_path) != review["old_hash"]:
            raise core.BackupError(
                "Die Konfiguration wurde während der NAS-Prüfung geändert. Bitte lade die Seite neu."
            )
        data = copy.deepcopy(review["data"])
        target = Path(data["target"])
        if not (target / core.MARKER).exists():
            data.update(core.initialize(target, storage=data["storage"]))
        else:
            core.check_repository(checked)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_hash(self.config_path) != review["old_hash"]:
            raise core.BackupError(
                "Die Konfiguration wurde inzwischen geändert. Bitte lade die Seite neu."
            )
        backup_existing(self.config_path)
        core.write_json(self.config_path, data)
        self.review = None
        warning = ""
        try:
            if review["automatic"]:
                install_timer(self.config_path, unit_prefix=self.timer_unit())
            else:
                # Disable scheduling only; never terminate a running transfer.
                unit = self.timer_unit() + ".timer"
                scheduled = any(
                    subprocess.run(
                        ["systemctl", "--user", state, "--quiet", unit],
                        capture_output=True,
                        timeout=10,
                    ).returncode
                    == 0
                    for state in ("is-enabled", "is-active")
                )
                if scheduled:
                    subprocess.run(
                        ["systemctl", "--user", "disable", "--now", unit],
                        check=True,
                        capture_output=True,
                        timeout=20,
                    )
        except (core.BackupError, OSError, subprocess.SubprocessError):
            warning = "Die Konfiguration ist gespeichert. Der Hintergrund-Timer konnte nicht umgestellt werden. Prüfe seinen Zustand in der Übersicht."
        return {"saved": True, "target": str(target), "warning": warning}

    def status(self):
        if not self.config_path.exists():
            return {"configured": False}
        config = core.Config.load(self.config_path)
        status = core.status(config)
        timer = (
            subprocess.run(
                ["systemctl", "--user", "is-active", "--quiet", self.timer_unit() + ".timer"],
                capture_output=True,
                timeout=10,
            ).returncode
            == 0
        )
        return {
            "configured": True,
            "status": status,
            "target": str(config.target),
            "storage": config.storage,
            "minutes": config.check_interval_minutes,
            "sources": config.sources,
            "automatic": timer,
            "problem": friendly_error(core.BackupError(status["last_check"]["error"]))
            if (status.get("last_check") or {}).get("error")
            else None,
        }

    def action(self, payload):
        config = core.Config.load(self.config_path)
        if payload.get("action") == "run":
            subprocess.run(
                [
                    "systemd-run",
                    "--user",
                    "--collect",
                    "--quiet",
                    "--unit=napback-web-" + secrets.token_hex(6),
                    sys.executable,
                    "-m",
                    "napback",
                    "--config",
                    str(self.config_path),
                    "run",
                ],
                check=True,
                capture_output=True,
                timeout=15,
            )
            return {"message": "Prüfung gestartet. Der Hintergrunddienst übernimmt die Sicherung."}
        if payload.get("action") == "verify":
            with core.locked(config):
                entries = core.completed(config)
                if not entries:
                    raise core.BackupError(
                        "Es gibt noch keine abgeschlossene Sicherung zum Prüfen."
                    )
                path, manifest = entries[-1]
                count = integrity.verify(path, manifest)
                if manifest.get("storage") == "zfs_raw":
                    from .raw import validate_streams

                    validate_streams(path, manifest)
            return {
                "message": f"Die letzte Sicherung wurde vollständig geprüft: {count} Einträge sind intakt."
            }
        raise core.BackupError("Unbekannte Aktion.")


def folders(value):
    path = Path(value or str(Path.home())).expanduser().absolute()
    core.no_symlink(path)
    if not path.is_dir():
        raise core.BackupError("Dieser Ordner existiert nicht oder ist nicht lesbar.")
    children = []
    for item in sorted(path.iterdir(), key=lambda p: p.name.lower()):
        with contextlib.suppress(OSError):
            if not item.name.startswith(".") and item.is_dir() and not item.is_symlink():
                children.append({"name": item.name, "path": str(item)})
    return {
        "path": str(path),
        "parent": str(path.parent),
        "folders": children,
        "free": shutil.disk_usage(path).free,
    }


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, application, port=0):
        self.application = application
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    server_version = "Napback"

    def log_message(self, *_):
        pass  # Never log request URLs or session tokens.

    def response(self, code, value, mime="application/json; charset=utf-8"):
        data = (
            json.dumps(value, ensure_ascii=False).encode()
            if not isinstance(value, bytes)
            else value
        )
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        expected = self.server.origin.removeprefix("http://")
        if self.headers.get("Host") != expected:
            return False
        origin = self.headers.get("Origin")
        if origin and origin != self.server.origin:
            return False
        return hmac.compare_digest(self.headers.get("X-Napback-Token", ""), self.server.token)

    def do_GET(self):
        if self.path in ("/", "/app.js", "/style.css"):
            if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
                return self.response(403, {"error": "Zugriff verweigert."})
            name, mime = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/style.css": ("style.css", "text/css; charset=utf-8"),
            }[self.path]
            return self.response(200, (ASSETS / name).read_bytes(), mime)
        if not self.authorized():
            return self.response(
                403, {"error": "Öffne die Oberfläche über das Napback-Tray oder napback ui."}
            )
        try:
            if self.path == "/api/initial":
                result = self.server.application.initial()
            elif self.path == "/api/status":
                result = self.server.application.status()
            elif self.path == "/api/ping":
                result = {"ok": True, "pid": os.getpid(), "version": __version__}
            elif self.path.startswith("/api/jobs/"):
                with self.server.application.lock:
                    result = self.server.application.jobs.get(self.path.rsplit("/", 1)[1])
                if result is None:
                    return self.response(404, {"error": "Vorgang nicht gefunden."})
            else:
                return self.response(404, {"error": "Nicht gefunden."})
            self.response(200, result)
        except Exception as error:
            self.response(400, friendly_error(error))

    def do_POST(self):
        if not self.authorized():
            return self.response(403, {"error": "Zugriff verweigert."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if (
                not 0 < length <= 256 * 1024
                or self.headers.get("Content-Type") != "application/json"
            ):
                return self.response(400, {"error": "Ungültige Anfrage."})
            self.connection.settimeout(15)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Ungültige Anfrage.")
            app = self.server.application
            if self.path == "/api/folders":
                return self.response(200, folders(payload.get("path")))
            functions = {
                "/api/connect": app.connect,
                "/api/preview": app.preview,
                "/api/review": app.review_settings,
                "/api/save": app.save,
                "/api/action": app.action,
            }
            if self.path not in functions:
                return self.response(404, {"error": "Nicht gefunden."})
            function = functions[self.path]
            self.response(202, app.job(lambda: function(payload)))
        except Exception as error:
            self.response(400, friendly_error(error))


def runtime_file(config_path):
    root = Path(os.environ.get("XDG_RUNTIME_DIR", str(Path.home() / ".cache"))) / "napback"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    core.no_symlink(root)
    key = hashlib.sha256(str(Path(config_path).absolute()).encode()).hexdigest()[:16]
    return root / ("web-" + key + ".json")


def serve(config_path, port=0):
    server = Server(Application(config_path), port)
    runtime = runtime_file(config_path)
    core.write_json(runtime, {"url": server.origin, "token": server.token, "pid": os.getpid()})
    os.chmod(runtime, 0o600)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        with contextlib.suppress(OSError, ValueError):
            if core.read_json(runtime).get("pid") == os.getpid():
                runtime.unlink()
    return {}


def open_ui(config_path, *, browser=True):
    runtime = runtime_file(config_path)

    def existing():
        try:
            data = core.read_json(runtime)
            address = urlsplit(data["url"])
            if (
                address.scheme != "http"
                or address.hostname != "127.0.0.1"
                or address.username
                or address.password
                or not address.port
                or address.path
                or address.query
                or address.fragment
            ):
                return None
            request = urllib.request.Request(
                data["url"] + "/api/ping", headers={"X-Napback-Token": data["token"]}
            )
            with urllib.request.urlopen(request, timeout=2) as result:
                ping = json.load(result)
            return data if ping.get("pid") == data["pid"] else None
        except (OSError, ValueError, KeyError, core.BackupError):
            return None

    data = existing()
    if data is None:
        logs = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "napback"
        logs.mkdir(mode=0o700, parents=True, exist_ok=True)
        log = logs / "web.log"
        with log.open("ab") as output:
            os.chmod(log, 0o600)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "napback",
                    "--config",
                    str(Path(config_path).absolute()),
                    "serve",
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        for _ in range(100):
            data = existing()
            if data:
                break
            if process.poll() is not None:
                raise core.BackupError(f"Die Oberfläche konnte nicht starten. Protokoll: {log}")
            time.sleep(0.1)
        if data is None:
            raise core.BackupError("Die Oberfläche braucht zu lange zum Starten.")
    if browser:
        webbrowser.open(data["url"] + "/#" + data["token"])
    return {"status": "web_opened", "url": data["url"]}
