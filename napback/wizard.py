"""Guided TrueNAS setup. Explanations accompany inputs; credentials stay in SSH."""

import os
import re
import shlex
import tempfile
import time
import uuid
from pathlib import Path

from . import core


class Guide:
    def __init__(self, language):
        self.german = language == "de"

    def tr(self, german, english):
        return german if self.german else english

    def say(self, german, english):
        print(self.tr(german, english))

    def ask(self, prompt, explanation, validate=lambda value: value, default=None, *, intro=None):
        print("\n" + (intro if intro is not None else explanation))
        while True:
            suffix = f" [{default}]" if default is not None else ""
            value = input(prompt + suffix + ": ").strip()
            if value == "?":
                print(explanation)
                continue
            if not value and default is not None:
                value = default
            try:
                return validate(value)
            except (ValueError, core.BackupError) as error:
                print(self.tr("Bitte korrigieren: ", "Please correct: ") + str(error))

    def yes_no(self, value):
        if value.lower() in ("j", "ja", "y", "yes"):
            return True
        if value.lower() in ("n", "nein", "no"):
            return False
        raise ValueError(self.tr("ja oder nein eingeben.", "enter yes or no."))

    def host(self, value):
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*", value):
            raise ValueError(
                self.tr(
                    "Benutzer@NAS-Adresse eingeben, z. B. backup@192.168.1.10, ohne https:// oder Pfad.",
                    "Enter user@NAS-address, for example backup@192.168.1.10, without https:// or a path.",
                )
            )
        return value

    def key(self, value):
        if not value:
            return None
        path = Path(value).expanduser().absolute()
        if path.suffix == ".pub":
            raise ValueError(
                self.tr(
                    "Hier die private Schlüsseldatei ohne .pub wählen. Die .pub-Datei gehört in TrueNAS.",
                    "Choose the private key file without .pub here. The .pub file belongs in TrueNAS.",
                )
            )
        if not path.is_file() or not os.access(path, os.R_OK):
            raise ValueError(
                self.tr(
                    "Schlüsseldatei nicht gefunden oder nicht lesbar.",
                    "Key file not found or not readable.",
                )
            )
        return str(path)

    def destination(self, value):
        if not value:
            raise ValueError(
                self.tr("Bitte einen Zielordner angeben.", "Enter a destination folder.")
            )
        path = core.absolute(str(Path(value).expanduser()), "destination")
        core.no_symlink(path)
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError(
                self.tr(
                    "Der Zielordner muss neu oder leer sein.",
                    "The destination folder must be new or empty.",
                )
            )
        return str(path)

    def interval(self, value):
        if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 1440:
            raise ValueError(
                self.tr(
                    "Eine ganze Zahl von 1 bis 1440 Minuten eingeben.",
                    "Enter a whole number from 1 to 1440 minutes.",
                )
            )
        return int(value)

    def storage(self, value):
        value = {"1": "zfs_raw", "2": "files"}.get(value.lower(), value.lower())
        if value not in ("zfs_raw", "files"):
            raise ValueError(
                self.tr(
                    "zfs_raw oder files wählen (auch 1 oder 2).",
                    "Choose zfs_raw or files (also 1 or 2).",
                )
            )
        return value

    def prefix(self, value):
        if value == "*":
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", value):
            raise ValueError(
                self.tr(
                    "Nur den Namensanfang eingeben, z. B. auto- oder autosnap_.",
                    "Enter only the name prefix, such as auto- or autosnap_.",
                )
            )
        return value

    def datasets(self, value):
        names = [name.strip() for name in value.split(",") if name.strip()]
        if any(name.startswith("/") for name in names):
            raise ValueError(
                self.tr(
                    "Den Dataset-Namen ohne /mnt/ eingeben: z. B. Apps statt /mnt/Apps.\n"
                    "Verwende den Namen aus der ersten Spalte der Liste.",
                    "Enter the dataset name without /mnt/: for example Apps instead of /mnt/Apps.\n"
                    "Use the name from the first column of the list.",
                )
            )
        if not names or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", name)
            or any(part in ("", ".", "..") for part in name.split("/"))
            for name in names
        ):
            raise ValueError(
                self.tr(
                    "Dataset-Namen wie tank/dokumente eingeben, durch Kommas getrennt.",
                    "Enter dataset names such as tank/documents, separated by commas.",
                )
            )
        if len(names) != len(set(names)):
            raise ValueError(
                self.tr("Jedes Dataset nur einmal auswählen.", "Select each dataset only once.")
            )
        return [
            {"name": f"dataset-{i + 1}", "dataset": name, "recursive": True}
            for i, name in enumerate(names)
        ]


def connection_help(guide, probe):
    manual = ["ssh", "-oStrictHostKeyChecking=ask", *(probe.ssh_options or []), probe.host, "true"]
    return (
        guide.tr(
            "SSH-Verbindung prüfen:\n"
            "  TrueNAS: System > Services > SSH einschalten. Unter Credentials > Users\n"
            "  (je nach Version Local Users) beim Benutzer SSH-/Shell-Zugang und den\n"
            "  öffentlichen SSH-Schlüssel (.pub) hinterlegen. Der private Schlüssel bleibt auf dem PC.\n"
            "  Bei 'Host key verification failed': Server-Fingerabdruck mit der TrueNAS-Konsole\n"
            "  vergleichen, dann die erste Verbindung bewusst bestätigen. Geänderte Schlüssel\n"
            "  nicht ungeprüft übernehmen. PC-Testbefehl:\n  ",
            "Check SSH access:\n"
            "  TrueNAS: enable System > Services > SSH. Under Credentials > Users\n"
            "  (Local Users in some versions), enable SSH/shell access and set the public\n"
            "  SSH key (.pub). Keep the private key on the PC.\n"
            "  For 'Host key verification failed', compare the server fingerprint using the\n"
            "  TrueNAS console before accepting the first connection. Do not blindly accept\n"
            "  changed keys. Test on this PC:\n  ",
        )
        + shlex.join(manual)
        + guide.tr(
            "\n  Bei 'Permission denied': Benutzer und Schlüsselzuordnung prüfen.\n"
            "  Bei sudo-Passwortabfrage: TrueNAS-Sudo-Rechte ohne Passwort für den Backup-Zugang prüfen.\n"
            "  Port 22 ist Standard; abweichende Ports über einen SSH-Alias konfigurieren.",
            "\n  For 'Permission denied', check the user and key assignment.\n"
            "  If sudo asks for a password, check passwordless sudo rights for the backup account.\n"
            "  Port 22 is standard; configure custom ports through an SSH alias.",
        )
    )


def snapshot_help(guide):
    return guide.tr(
        "Ein Snapshot hält den Stand Deiner Daten auf dem NAS fest. Napback kopiert diesen Stand.\n"
        "Bei Namen wie auto-2026-09-20: Enter drücken. Bei anderen Namen: * eingeben.\n\n"
        "Noch keine Snapshots? Für jedes gewählte Dataset in der TrueNAS-Weboberfläche:\n"
        "1. Data Protection öffnen. Bei Periodic Snapshot Tasks auf Add klicken.\n"
        "2. Bei Dataset denselben Namen wie hier wählen. Recursive anhaken: Das nimmt Kinder mit.\n"
        "3. Schedule: Daily. Snapshot Lifetime: 7 DAYS. Enabled anhaken.\n"
        "4. Naming Schema: auto-%Y-%m-%d_%H-%M. Mit Save speichern.\n"
        "5. Für den ersten Stand: Data Protection > Snapshots > Add öffnen.\n"
        "   Dataset wieder wählen, Recursive anhaken, Name: auto-erste-sicherung. Save klicken.\n"
        "6. Hier auto- übernehmen. Nun kann Napback diesen ersten Stand finden.\n\n"
        "Ein bestehender Auftrag genügt, wenn er alle gewählten Kinder einschließt.\n"
        "Ausgeschlossene Kinder kann Napback nicht automatisch überspringen.\n"
        "Alle gewählten Datasets brauchen einen gemeinsamen Snapshot, höchstens 48 Stunden alt.",
        "A snapshot records the state of your data on the NAS. Napback copies that state.\n"
        "For names such as auto-2026-09-20, press Enter. For other names, enter *.\n\n"
        "No snapshots yet? For each selected dataset, in the TrueNAS web interface:\n"
        "1. Open Data Protection. Click Add under Periodic Snapshot Tasks.\n"
        "2. Select the same Dataset as here. Check Recursive to include children.\n"
        "3. Schedule: Daily. Snapshot Lifetime: 7 DAYS. Check Enabled.\n"
        "4. Naming Schema: auto-%Y-%m-%d_%H-%M. Click Save.\n"
        "5. For the first snapshot, open Data Protection > Snapshots > Add.\n"
        "   Choose the dataset again, check Recursive, Name: auto-first-backup. Click Save.\n"
        "6. Accept auto- here. Napback can now find that first snapshot.\n\n"
        "An existing task is enough if it includes all selected children.\n"
        "Napback cannot automatically skip excluded children.\n"
        "All selected datasets need a common snapshot, at most 48 hours old.",
    )


def setup(config_path, language="de"):
    from .cli import backup_existing, install_timer

    g = Guide(language)
    g.say(
        "Napback – TrueNAS Schritt für Schritt einrichten.\n"
        "Enter übernimmt [Vorgaben]. Mehr Hilfe: ? eingeben. Abbrechen: Strg+C.",
        "Napback — step-by-step TrueNAS setup.\n"
        "Enter accepts [defaults]. Type ? for more help. Ctrl+C cancels.",
    )
    host = g.ask(
        g.tr(
            "TrueNAS-Zugang (Benutzer@Adresse oder SSH-Alias)",
            "TrueNAS login (user@address or SSH alias)",
        ),
        g.tr(
            "1. Verbindung zum NAS\n"
            "SSH ist die verschlüsselte Verbindung, über die Dein PC Daten von TrueNAS abholt.\n"
            "'SSH-Host' meint Benutzer und NAS-Adresse: z. B. backup@192.168.1.10.\n"
            "backup = Benutzer auf TrueNAS; 192.168.1.10 = Adresse aus der Browser-Adresszeile\n"
            "Deiner TrueNAS-Weboberfläche (ohne https:// und ohne /...).\n"
            "Den Benutzernamen findest Du unter Credentials > Users/Local Users.\n"
            "TrueNAS: Unter System > Services muss der Dienst SSH eingeschaltet sein.\n"
            "Ein bereits eingerichteter Name aus ~/.ssh/config, z. B. my-nas, geht ebenfalls.",
            "1. Connect to the NAS\n"
            "SSH is the encrypted connection your PC uses to pull data from TrueNAS.\n"
            "'SSH host' means the login and NAS address, for example backup@192.168.1.10.\n"
            "backup = TrueNAS user; 192.168.1.10 = address from the browser bar of your\n"
            "TrueNAS web interface (without https:// or a /path).\n"
            "Find the username under Credentials > Users/Local Users.\n"
            "TrueNAS: the SSH service must be enabled under System > Services.\n"
            "An existing ~/.ssh/config alias such as my-nas also works.",
        ),
        g.host,
        intro=g.tr(
            "Dein TrueNAS-Benutzer und die NAS-Adresse, mit @ dazwischen.\n"
            "Beispiel: backup@192.168.1.10 — ohne https://. Ein SSH-Alias geht auch.",
            "Your TrueNAS username and NAS address, joined with @.\n"
            "Example: backup@192.168.1.10 — without https://. An SSH alias also works.",
        ),
    )
    key = g.ask(
        g.tr(
            "SSH-Schlüsseldatei auf diesem PC (Enter = automatisch)",
            "SSH key file on this PC (Enter = automatic)",
        ),
        g.tr(
            "2. Anmeldung ohne Passwortabfrage\n"
            "Wähle den Pfad zu Deinem privaten SSH-Schlüssel, z. B. ~/.ssh/id_ed25519_nas.\n"
            "Nur den Dateipfad eingeben, niemals den Schlüsselinhalt. Die zugehörige .pub-Datei\n"
            "trägst Du in TrueNAS beim Benutzer unter 'Public SSH Key' ein (SSH Access aktivieren).\n"
            "Noch kein Schlüssel? In einem zweiten Terminal: ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_nas\n"
            "Einen vorhandenen Schlüssel dabei nicht überschreiben.\n"
            "Leer lassen, wenn SSH den passenden Schlüssel bereits automatisch oder über den Alias findet.\n"
            "Eine Schlüssel-Passphrase muss über einen SSH-Agenten auch dem Hintergrunddienst verfügbar sein.",
            "2. Log in without password prompts\n"
            "Enter the path to your private SSH key, for example ~/.ssh/id_ed25519_nas.\n"
            "Enter the file path only, never the key contents. Add the corresponding .pub\n"
            "file to the TrueNAS user's 'Public SSH Key' field and enable SSH Access.\n"
            "No key yet? In another terminal: ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_nas\n"
            "Do not overwrite an existing key.\n"
            "Leave empty if SSH already finds the key automatically or through the alias.\n"
            "For a key passphrase, the SSH agent must also be available to the background service.",
        ),
        g.key,
        intro=g.tr(
            "Pfad zu Deinem SSH-Schlüssel auf dem PC, z. B. ~/.ssh/id_ed25519_nas.\n"
            "Nur den Pfad eingeben. Falls SSH schon ohne Passwort funktioniert: Enter.",
            "Path to your SSH key on this PC, for example ~/.ssh/id_ed25519_nas.\n"
            "Enter the path only. If SSH already works without a password prompt, press Enter.",
        ),
    )
    use_sudo = g.ask(
        g.tr("Erhöhte Rechte auf dem NAS verwenden?", "Use elevated permissions on the NAS?"),
        g.tr(
            "3. Rechte für ZFS\n"
            "sudo erlaubt Deinem NAS-Benutzer die nötigen ZFS-Befehle. 'sudo -n' bedeutet:\n"
            "ohne Passwortabfrage, damit automatische Sicherungen funktionieren.\n"
            "Ja wählen, wenn Dein TrueNAS-Konto so eingerichtet ist; Nein bei direkt erteilten\n"
            "ZFS-Rechten. Die Einstellung findest Du beim TrueNAS-Benutzer unter Sudo Commands.",
            "3. ZFS permissions\n"
            "sudo gives your NAS user the required ZFS permissions. 'sudo -n' means no\n"
            "password prompt, which is necessary for unattended backups. Choose Yes for\n"
            "an account configured this way, or No for directly delegated ZFS permissions.\n"
            "TrueNAS user settings contain the Sudo Commands options.",
        ),
        g.yes_no,
        g.tr("j", "y"),
        intro=g.tr(
            "Mit einem TrueNAS-Admin-Konto normalerweise Enter drücken.\n"
            "Damit darf Napback die nötigen ZFS-Befehle auf dem NAS ausführen.",
            "With a TrueNAS admin account, usually press Enter.\n"
            "This allows Napback to run the required ZFS commands on the NAS.",
        ),
    )
    options = ["-i", key, "-oIdentitiesOnly=yes"] if key else None
    probe = core.Config(
        Path("/"), str(uuid.uuid4()), Path("/"), [], host=host, sudo=use_sudo, ssh_options=options
    )
    g.say("\nPrüfe Verbindung und lese Dataset-Liste …", "\nChecking access and reading datasets …")
    try:
        listing = probe.remote(
            ["zfs", "list", "-H", "-o", "name,used,encryption", "-t", "filesystem"]
        )
    except core.BackupError as error:
        raise core.BackupError(connection_help(g, probe) + "\n\n" + str(error)) from error
    g.say("\nDATASET\tBELEGT\tVERSCHLÜSSELUNG", "\nDATASET\tUSED\tENCRYPTION")
    print(listing)
    sources = g.ask(
        g.tr("Welche Datasets sichern?", "Which datasets should be backed up?"),
        g.tr(
            "4. Quelldaten auf TrueNAS\n"
            "Ein Dataset ist ein eigener Speicherbereich unter 'Datasets' in TrueNAS.\n"
            "Übernimm die vollständigen Namen aus der ersten Spalte, z. B. tank/dokumente.\n"
            "Mehrere Namen mit Kommas trennen: tank/dokumente,tank/fotos.\n"
            "Untergeordnete Datasets werden automatisch mitgesichert; den Elternnamen wählen.\n"
            "Beim verschlüsselten Modus müssen auch alle Kinder verschlüsselt sein (nicht 'off').",
            "4. Source data on TrueNAS\n"
            "A dataset is a separate storage area shown under 'Datasets' in TrueNAS.\n"
            "Copy the full names from the first column, such as tank/documents.\n"
            "Separate multiple names with commas: tank/documents,tank/photos.\n"
            "Child datasets are included automatically; choose their parent name.\n"
            "For encrypted storage, every child must also be encrypted (not 'off').",
        ),
        g.datasets,
        intro=g.tr(
            "Namen aus der ersten Spalte eingeben. Beispiel: Apps — nicht /mnt/Apps.\n"
            "Alles darunter wird mitgesichert. Mehrere Namen mit Kommas trennen.",
            "Enter names from the first column. Example: Apps — not /mnt/Apps.\n"
            "Everything below is included. Separate multiple names with commas.",
        ),
    )
    prefix = g.ask(
        g.tr("Welche Snapshot-Namen verwenden?", "Which snapshot names should be used?"),
        snapshot_help(g),
        g.prefix,
        "auto-",
        intro=g.tr(
            "Heißen Deine Snapshots z. B. auto-2026-09-20? Dann Enter drücken.\n"
            "Bei anderen Namen: * eingeben. Noch keine Snapshots? ? zeigt die Einrichtung.",
            "Are your snapshots named like auto-2026-09-20? Press Enter.\n"
            "For other names, enter *. No snapshots yet? Type ? for setup instructions.",
        ),
    )
    target = g.ask(
        g.tr("Neuer Zielordner auf diesem PC", "New destination folder on this PC"),
        g.tr(
            "5. Speicherort auf Deinem Linux-PC\n"
            "Hier landen die Sicherungen, z. B. /mnt/backup-disk/nas oder ~/NAS-Backup.\n"
            "Das ist ein PC-Pfad. Der Ordner muss neu oder leer sein; Napback formatiert nichts.\n"
            "Bei externer Festplatte diese vorher einbinden und ihren richtigen Pfad verwenden.",
            "5. Storage on your Linux PC\n"
            "Backups go here, for example /mnt/backup-disk/nas or ~/NAS-Backup.\n"
            "This is a PC path. The folder must be new or empty; Napback does not format disks.\n"
            "Mount an external disk first and use its correct path.",
        ),
        g.destination,
        intro=g.tr(
            "Hier landen die Backups auf Deinem PC. Beispiel: ~/NAS-Backup.\n"
            "Wähle einen neuen oder leeren Ordner. Eine externe Platte vorher einbinden.",
            "This is where backups will be stored on your PC. Example: ~/NAS-Backup.\n"
            "Choose a new or empty folder. Mount an external disk first.",
        ),
    )
    storage = g.ask(
        g.tr("Speichermodus", "Storage mode"),
        g.tr(
            "6. Verschlüsselung\n"
            "1 / zfs_raw: vorhandene ZFS-Verschlüsselung erhalten, ohne LUKS und ohne Schlüssel\n"
            "auf dem PC. Wiederherstellung braucht ZFS (z. B. TrueNAS) und den Originalschlüssel.\n"
            "Originalschlüssel separat sichern! Alle gewählten NAS-Datasets müssen verschlüsselt sein.\n"
            "2 / files: lesbare, UNVERSCHLÜSSELTE Dateikopien; ZFS-Verschlüsselung geht verloren.",
            "6. Encryption\n"
            "1 / zfs_raw: preserve native ZFS encryption without LUKS or a key on the PC.\n"
            "Restore needs ZFS (for example TrueNAS) and the original key. Keep that key separately!\n"
            "All selected NAS datasets must already be encrypted.\n"
            "2 / files: readable, UNENCRYPTED file copies; native ZFS encryption is lost.",
        ),
        g.storage,
        "zfs_raw",
        intro=g.tr(
            "Enter: Die Backups bleiben wie auf dem NAS verschlüsselt.\n"
            "Alle Quellen müssen verschlüsselt sein. Zum Wiederherstellen brauchst Du\n"
            "TrueNAS/ZFS und den Originalschlüssel; sichere den Schlüssel separat.\n"
            "files wählen: unverschlüsselte, direkt lesbare Dateien.",
            "Enter: backups keep the NAS encryption. All sources must be encrypted.\n"
            "Restore requires TrueNAS/ZFS and the original key; keep the key separately.\n"
            "Choose files for unencrypted, directly readable files.",
        ),
    )
    image = ""
    if storage == "files":
        image = g.ask(
            g.tr(
                "Docker-Sender-Image (Enter = NAS-rsync)", "Docker sender image (Enter = NAS rsync)"
            ),
            g.tr(
                "Optional nur für files: Leer lassen, wenn rsync auf TrueNAS vorhanden ist.\n"
                "napback-source:0.1.0 nur eingeben, wenn dieses Image vorher auf dem NAS gebaut wurde.",
                "Optional, files mode only: leave empty if TrueNAS has rsync installed.\n"
                "Enter napback-source:0.1.0 only if that image was built on the NAS beforehand.",
            ),
            intro=g.tr(
                "Normalerweise einfach Enter drücken.",
                "Normally, just press Enter.",
            ),
        )
    interval = g.ask(
        g.tr("Alle wie viele Minuten prüfen?", "Check every how many minutes?"),
        g.tr(
            "7. Automatische Prüfung\n"
            "1 bedeutet jede Minute nach neuen NAS-Snapshots suchen, nicht jede Minute ein Vollbackup.\n"
            "Ohne neue Generation wird nichts kopiert. Erlaubt: 1 bis 1440 Minuten.\n"
            "Die Prüfung läuft, solange der PC wach ist; verpasste Prüfungen werden nachgeholt.",
            "7. Automatic checks\n"
            "1 means check every minute for new NAS snapshots, not a full backup every minute.\n"
            "Nothing is copied without a new generation. Allowed: 1 to 1440 minutes.\n"
            "Checks run while the PC is awake and catch up after downtime.",
        ),
        g.interval,
        "1",
        intro=g.tr(
            "Enter: jede Minute nach neuen Snapshots schauen.\n"
            "Kopiert wird nur, wenn es etwas Neues gibt. Andere Minutenanzahl: 1 bis 1440.",
            "Enter: check for new snapshots every minute.\n"
            "Data is copied only when a snapshot changes. Other intervals: 1 to 1440 minutes.",
        ),
    )
    config = {
        "target": target,
        "repository_id": str(uuid.uuid4()),
        "mountpoint": "/",
        "host": host,
        "sudo": use_sudo,
        "sources": sources,
        "storage": storage,
        "snapshot_prefix": prefix,
        "check_interval_minutes": interval,
        "trigger": "new_snapshot",
    }
    if options:
        config["ssh_options"] = options
    if image:
        config["docker_image"] = image
    g.say(
        "\nPrüfe Auswahl und vorhandene Snapshots vor dem Speichern …",
        "\nValidating selection and existing snapshots before saving …",
    )
    with tempfile.TemporaryDirectory() as temporary:
        temporary_config = Path(temporary) / "config.json"
        core.write_json(temporary_config, config)
        checked = core.Config.load(temporary_config)
        try:
            core.plan_sources(checked, time.time())
        except core.BackupError as error:
            if "snapshot" in str(error).lower():
                print("\n" + snapshot_help(g))
            if "not encrypted" in str(error):
                g.say(
                    "\nDie gewählte Quelle ist nicht ZFS-verschlüsselt. zfs_raw bricht deshalb ab.\n"
                    "Wähle verschlüsselte Datasets oder bewusst den unverschlüsselten Modus files.",
                    "\nThe selected source is not ZFS-encrypted; zfs_raw therefore refuses it.\n"
                    "Choose encrypted datasets or explicitly select unencrypted files mode.",
                )
            raise
    config.update(core.initialize(target, storage=storage))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_existing(config_path)
    core.write_json(config_path, config)
    g.say(f"\nKonfiguration gespeichert: {config_path}", f"\nConfiguration saved: {config_path}")
    if g.ask(
        g.tr("Automatische Prüfung einschalten?", "Enable automatic checks?"),
        g.tr(
            "Der Hintergrunddienst startet bei Deiner Anmeldung und prüft im gewählten Intervall.\n"
            "Ja aktiviert ihn jetzt. Nein speichert nur die Einrichtung; manuell starten: napback run.",
            "The background service starts with your login and checks at the selected interval.\n"
            "Yes enables it now. No saves the setup only; run manually with: napback run.",
        ),
        g.yes_no,
        g.tr("j", "y"),
        intro=g.tr(
            "Enter: Backups ab jetzt automatisch abholen, solange Du angemeldet bist.\n"
            "n: nur speichern und später manuell mit napback run starten.",
            "Enter: start fetching backups automatically while you are logged in.\n"
            "n: save only and run napback run manually later.",
        ),
    ):
        install_timer(config_path)
    return {"status": "configured", "target": config["target"]}
