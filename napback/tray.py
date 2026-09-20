"""Qt tray monitor. Transfers run outside the GUI event loop."""

from __future__ import annotations

import hashlib
import locale
import os
import shlex
import shutil
import signal
import sys
from pathlib import Path

try:
    from PyQt6.QtCore import QLockFile, QProcess, Qt, QTimer, QUrl
    from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
    from PyQt6.QtWidgets import QApplication, QInputDialog, QMenu, QMessageBox, QSystemTrayIcon
except ImportError as error:
    raise ImportError(
        "Tray support is missing. Re-run ./install.sh or install napback[tray]."
    ) from error

from . import core
from .cli import backup_existing


def state_label(state, german=False):
    labels = {
        "unconfigured": ("Not configured", "Noch nicht eingerichtet"),
        "checking": ("Checking for new snapshots", "Prüft auf neue Snapshots"),
        "running": ("Backing up", "Sicherung läuft"),
        "completed": ("Backup complete", "Sicherung abgeschlossen"),
        "no_new_snapshot": ("Up to date — no new snapshot", "Aktuell — kein neuer Snapshot"),
        "not_due": ("Ready — backup is not due", "Bereit — Sicherung noch nicht fällig"),
        "failed": ("Backup check failed", "Prüfung oder Sicherung fehlgeschlagen"),
        "interrupted": ("Previous run was interrupted", "Letzter Lauf unterbrochen"),
        "ready": ("Ready", "Bereit"),
    }
    return labels.get(state, labels["ready"])[int(german)]


def tray_icon(state):
    colors = {
        "unconfigured": "#9099a5",
        "checking": "#4f9cff",
        "running": "#4f9cff",
        "failed": "#ed665d",
        "interrupted": "#edaa50",
    }
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#788b9f"), 2))
    painter.setBrush(QColor("#e0e7ef"))
    painter.drawRoundedRect(3, 5, 24, 21, 4, 4)
    painter.drawLine(5, 19, 24, 19)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colors.get(state, "#43b581")))
    painter.drawEllipse(19, 20, 12, 12)
    painter.end()
    return QIcon(pixmap)


class TrayController:
    def __init__(self, config_path, *, start_timer=True):
        self.config_path = Path(config_path).absolute()
        self.german = (locale.getlocale()[0] or os.environ.get("LANG", "")).lower().startswith("de")
        self.process = None
        self.output = bytearray()
        self.state = "unconfigured"
        self.config = None
        self.tray = QSystemTrayIcon(tray_icon(self.state))
        self.menu = QMenu()
        self.heading = self.menu.addAction("Napback")
        self.heading.setEnabled(False)
        self.detail = self.menu.addAction("")
        self.detail.setEnabled(False)
        self.menu.addSeparator()
        self.check_action = self.menu.addAction(
            self.tr("Check now", "Jetzt prüfen"), self.check_now
        )
        self.folder_action = self.menu.addAction(
            self.tr("Open backup folder", "Sicherungsordner öffnen"), self.open_folder
        )
        self.interval_action = self.menu.addAction(
            self.tr("Check interval…", "Prüfintervall…"), self.change_interval
        )
        self.menu.addAction(self.tr("Configuration…", "Konfiguration…"), self.open_config)
        self.menu.addAction(self.tr("Set up…", "Einrichten…"), self.setup)
        self.menu.addAction(self.tr("Show logs", "Protokoll anzeigen"), self.show_logs)
        self.menu.addSeparator()
        self.menu.addAction(
            self.tr("Quit tray (checks continue)", "Tray beenden (Prüfung läuft weiter)"),
            QApplication.instance().quit,
        )
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.activated)
        self.tray.show()
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        if start_timer:
            self.timer.start(2000)
        self.refresh()

    def tr(self, english, german):
        return german if self.german else english

    def refresh(self):
        details = ""
        try:
            if not self.config_path.exists():
                self.config = None
                self.state = "unconfigured"
            else:
                self.config = core.Config.load(self.config_path)
                status = core.status(self.config)
                check = status.get("last_check") or {}
                self.state = check.get("status", "ready")
                if status.get("running"):
                    self.state = "checking" if self.state == "checking" else "running"
                elif self.process and self.process.state() != QProcess.ProcessState.NotRunning:
                    self.state = "checking"
                details = check.get("error", "")
                if not details:
                    minutes = self.config.check_interval_minutes
                    details = self.tr(f"Check every {minutes} min", f"Prüfung alle {minutes} Min.")
        except (core.BackupError, OSError, ValueError) as error:
            self.state = "failed"
            details = str(error)
        label = state_label(self.state, self.german)
        self.heading.setText(label)
        self.detail.setText(details[:100])
        self.tray.setToolTip("Napback — " + label + ("\n" + details[:300] if details else ""))
        self.tray.setIcon(tray_icon(self.state))
        self.check_action.setEnabled(
            self.config is not None and self.state not in ("checking", "running")
        )
        self.folder_action.setEnabled(self.config is not None)
        self.interval_action.setEnabled(self.config is not None)

    def check_now(self):
        if self.process and self.process.state() != QProcess.ProcessState.NotRunning:
            return
        self.output = bytearray()
        self.process = QProcess()
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(lambda _: self.refresh())
        unit = "napback-manual-" + hashlib.sha256(str(self.config_path).encode()).hexdigest()[:12]
        self.process.start(
            "systemd-run",
            [
                "--user",
                "--collect",
                "--quiet",
                "--unit=" + unit,
                sys.executable,
                "-m",
                "napback",
                "--config",
                str(self.config_path),
                "run",
            ],
        )
        self.refresh()

    def read_output(self):
        self.output.extend(bytes(self.process.readAllStandardOutput()))
        self.output = self.output[-8000:]

    def finished(self, code, _status):
        self.read_output()
        self.refresh()
        if code:
            message = self.output.decode(errors="replace")[-1500:]
            self.tray.showMessage("Napback", message, QSystemTrayIcon.MessageIcon.Warning)

    def open_folder(self):
        if self.config:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config.target)))

    def open_config(self):
        if self.config_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.config_path)))
        else:
            self.setup()

    def change_interval(self):
        if not self.config:
            return
        minutes, accepted = QInputDialog.getInt(
            None,
            "Napback",
            self.tr("Check interval in minutes:", "Prüfintervall in Minuten:"),
            self.config.check_interval_minutes,
            1,
            1440,
        )
        if accepted:
            try:
                update_interval(self.config_path, minutes)
                self.refresh()
            except (core.BackupError, OSError, ValueError) as error:
                QMessageBox.warning(None, "Napback", str(error))

    def terminal(self, arguments):
        for program, prefix in (
            ("konsole", ["--hold", "-e"]),
            ("gnome-terminal", ["--"]),
            ("x-terminal-emulator", ["-e"]),
        ):
            if shutil.which(program):
                QProcess.startDetached(program, [*prefix, *arguments])
                return
        QMessageBox.information(
            None,
            "Napback",
            self.tr("Run in a terminal:\n", "Im Terminal ausführen:\n") + shlex.join(arguments),
        )

    def setup(self):
        self.terminal([sys.executable, "-m", "napback", "--config", str(self.config_path), "setup"])

    def show_logs(self):
        self.terminal(
            [
                "journalctl",
                "--user",
                "-u",
                "napback.service",
                "-u",
                "napback-manual-*.service",
                "-n",
                "80",
                "--no-pager",
            ]
        )

    def activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_folder()


def update_interval(config_path, minutes):
    config = core.Config.load(config_path)
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
        raise core.BackupError("check_interval_minutes must be an integer from 1 to 1440")
    data = core.read_json(config_path)
    data["check_interval_minutes"] = minutes
    backup_existing(config_path)
    core.write_json(config_path, data)
    return config


def main(config_path):
    app = QApplication.instance() or QApplication(["napback-tray"])
    app.setApplicationName("Napback")
    app.setDesktopFileName("napback")
    app.setQuitOnLastWindowClosed(False)
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", str(Path.home() / ".cache/napback")))
    runtime.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(str(Path(config_path).absolute()).encode()).hexdigest()[:16]
    lock = QLockFile(str(runtime / ("napback-tray-" + key + ".lock")))
    if not lock.tryLock(0):
        return 0
    controller = TrayController(config_path)
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    # Keep references alive until Qt exits.
    app._napback = controller
    code = app.exec()
    controller.tray.hide()
    lock.unlock()
    return code
