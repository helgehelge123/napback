# Napback unter Linux

Napback prüft vorhandene TrueNAS-Snapshots und holt neue Generationen auf Deinen
PC. Standardmäßig jede Minute; das Intervall lässt sich bei der ersten
Einrichtung und später im Tray ändern. Neue Snapshots werden ohne 24-Stunden-Wartezeit
gesichert. Unveränderte Snapshots erzeugen keine weitere lokale Version.

## Installieren

Quellarchiv von den [GitHub-Releases](https://github.com/helgehelge123/napback/releases)
herunterladen und entpacken. Im entpackten Ordner als normaler Benutzer ausführen:

```sh
./install.sh
```

Alternativ mit installiertem Git:

```sh
git clone https://github.com/helgehelge123/napback.git
cd napback
./install.sh
```

Der Installer ergänzt fehlende Pakete automatisch: Python samt venv, rsync,
OpenSSH-Client, systemd und die benötigten Qt-Systembibliotheken. Unterstützt
werden Arch/CachyOS (`pacman`), Debian/Ubuntu (`apt-get`) und Fedora (`dnf`). Dafür
kann `sudo` Dein Passwort verlangen. Python muss mindestens Version 3.11 haben;
ein zu altes Distributions-Python musst Du vorher aktualisieren. Auf anderen
Distributionen die Voraussetzungen selbst installieren.

Napback und PyQt6 liegen danach getrennt vom System-Python unter
`~/.local/share/napback/venv`. Der Programmstarter liegt in
`~/.local/bin/napback`; dieser Ordner muss im `PATH` stehen. Bestehende
Installationen und ersetzte Konfigurationsdateien werden daneben gesichert.

Das Tray-Symbol startet sofort in der grafischen Sitzung und künftig beim
Desktop-Login. KDE Plasma unterstützt es direkt. Andere Desktops benötigen eine
System-Tray-Unterstützung; unter GNOME kann dafür eine Erweiterung nötig sein.
Ohne Sicherungskonfiguration zeigt Napback „Noch nicht eingerichtet“ bzw.
„Not configured“. Die Installation allein aktiviert keine Sicherung.

Für eine Installation ohne Tray und Qt: `./install.sh --cli-only`.

## Einrichten

```sh
napback setup
```

Der Assistent fragt SSH-Zugang, Datasets, einen neuen oder leeren Zielordner und
das Prüfintervall in Minuten ab. Enter übernimmt eine Minute; erlaubt sind
1 bis 1440 Minuten. Ein vorhandener SSH-Alias kann den Schlüssel und weitere
SSH-Einstellungen enthalten. Den Hostschlüssel vorher prüfen. SSH und ggf.
`sudo -n` auf dem NAS müssen ohne Passwortabfrage funktionieren.

TrueNAS muss bereits regelmäßige Snapshots anlegen, bei Kind-Datasets gemeinsam
als rekursive Snapshots. `auto-` ist lediglich der standardmäßige Namensanfang,
nach dem Napback sucht, beispielsweise `auto-2026-09-20_12-00`. Napback erstellt
selbst keine NAS-Snapshots. Den Filter änderst Du mit `snapshot_prefix` in der
Konfiguration; ein leerer Wert erlaubt jeden Namensanfang. Standardmäßig darf
die gewählte Generation höchstens 48 Stunden alt sein.

Als Docker-Image kann `napback-source:0.1.0` verwendet werden. Es wird auf dem
NAS mit `docker compose -f docker/compose.yaml build` erstellt. Das Sender-Image
bleibt auch mit Napback 0.2.0 bei Version 0.1.0. Alternativ verwendet Napback das
auf dem NAS installierte rsync. Es wird kein dauerhaft laufender Container
benötigt und kein zusätzlicher Port geöffnet.

```sh
napback plan          # Welche Snapshots werden gelesen?
napback run           # Jetzt prüfen und neue Generation sichern
napback status        # Letzter Erfolg und letzter Versuch
napback verify        # Lokale Sicherung vollständig prüfen
napback restore ~/Wiederhergestellt
```

Der Hintergrund-Timer läuft über systemd und prüft im gewählten Intervall. Für
Sicherungen bereits vor der Anmeldung einmal `loginctl enable-linger "$USER"`
ausführen. Das kann abhängig von Deiner Distribution eine lokale Autorisierung
verlangen. War der PC aus oder im Standby, holt der nächste fällige Check die
neueste gemeinsame Snapshot-Generation ab. Zwischenstände aus der Offline-Zeit
werden nicht alle einzeln nachgeladen. Die Prüfung weckt den PC nicht auf.
Die tatsächliche Reaktion kann durch den Minutentakt etwas später erfolgen.

## Im Tray

Das Symbol zeigt Prüfung, laufende Sicherung, Erfolg oder Fehler. Sein Menü
enthält „Jetzt prüfen“, Sicherungsordner, Konfiguration, Einrichtung, Protokoll
und „Prüfintervall…“. Änderungen am Minutenintervall gelten ohne Neuinstallation.
„Jetzt prüfen“ überspringt die Wartezeit, sichert denselben Snapshot aber nicht
erneut. Dafür wäre ausdrücklich `napback run --force` nötig.

Das Tray kann beendet werden; der separate Sicherungsdienst läuft weiter.
Automatische Sicherungen deaktivierst Du mit:

```sh
systemctl --user disable --now napback.timer
```

## Daten und Verschlüsselung auf dem PC

Auf dem PC entstehen normale Verzeichnisversionen, kein natives ZFS-Dataset:

```text
Dein-Zielordner/
  latest -> snapshots/20260920T120000Z-…/data
  snapshots/
    20260920T120000Z-…/
      manifest.json
      inventory.jsonl
      data/
        dataset-1/
        dataset-2/
```

**Die ZFS-Verschlüsselung wird nicht übernommen.** SSH verschlüsselt die
Übertragung. Napback liest auf dem NAS bereits entschlüsselte Dateien und legt
sie auf dem PC lesbar ab. Das NAS-Dataset muss dafür entsperrt sein. Für
Verschlüsselung auf dem PC wähle einen Zielpfad auf einem bereits verschlüsselten
Dateisystem, etwa einem eingebundenen LUKS-Laufwerk. Napback formatiert und
entsperrt keine Laufwerke und übernimmt keine ZFS-Schlüssel.

Das Ziel benötigt Hardlinks und erweiterte Attribute, etwa ext4, XFS oder Btrfs;
ZFS auf dem PC ist nicht nötig. Unveränderte Dateien teilen Speicher über
Hardlinks. Standard sind 30 erfolgreiche Versionen. In `config.json` verhindert
`"keep": 0` das automatische Löschen alter Versionen.

Dateien aus `latest` herauskopieren, nicht dort bearbeiten: eine Änderung könnte
mehrere Versionen betreffen. Für Symlinks die Wiederherstellungsfunktion
verwenden. Eigentümer und besondere Metadaten speichert rsync in erweiterten
Attributen. Vollständige Metadaten-Wiederherstellung und Grenzen stehen in
[recovery.md](recovery.md).

## Update von Version 0.1

`./install.sh` erneut ausführen. Bestehende Installation wird daneben gesichert.
Bei reinen ZFS-Quellen gilt danach automatisch die neue Snapshot-Erkennung.
Die erste Sicherung nach dem Update ergänzt die bisher fehlenden ZFS-GUIDs;
danach werden gleiche Generationen übersprungen. Für das alte 24-Stunden-Verhalten
explizit `"trigger": "interval"` setzen. Normale Verzeichnisquellen behalten
automatisch den Intervallmodus.

Prüfdetails und Grenzen der Tests stehen im [Testbericht](testing.md).
