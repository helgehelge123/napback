# Napback unter Linux

Napback prüft vorhandene TrueNAS-Snapshots und holt neue Generationen auf Deinen
PC. Seit Version 0.3 bleibt dabei die native ZFS-Verschlüsselung erhalten.
LUKS und ZFS auf dem PC sind nicht nötig. Standardmäßig jede Minute; das Intervall lässt sich bei der ersten
Einrichtung und später im Tray ändern. Neue Snapshots werden ohne 24-Stunden-Wartezeit
gesichert. Unveränderte Snapshots erzeugen keine weitere lokale Version.

Für mehrere Kopien, verschiedene Speicherarten und die optionale TrueNAS-
Konfigurationssicherung siehe [Mehrere Aufträge und Einstellungen](multiple-backups.md).

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

## Einrichten – jetzt im Browser

Öffne **Napback** im Anwendungsmenü oder doppelklicke auf sein Symbol im Tray.
Alternativ:

```sh
napback setup
```

Es öffnet sich eine lokale Webseite. Sie läuft auf Deinem PC und schreibt die
Einstellungen direkt in Napback. Du musst keine JSON-Datei bearbeiten.

1. **NAS verbinden:** Adresse und Benutzer getrennt eintragen. Den SSH-Schlüssel
   auf Deinem PC auswählen. Dann „Verbindung prüfen & Daten anzeigen“ anklicken.
2. **Daten auswählen:** Gewünschte Bereiche anhaken. Unterbereiche lassen sich
   aufklappen und einzeln abwählen. Die Seite zeigt echte Snapshot-Namen und Datum.
   Napback sucht den passenden neuesten Stand selbst; Du brauchst keinen
   Namensanfang mehr einzugeben.
3. **Auf dem PC speichern:** Zielordner auswählen, Verschlüsselung und Intervall
   lesen und einstellen. Standard: vorhandene ZFS-Verschlüsselung erhalten und
   jede Minute nach neuen Snapshots schauen.
4. **Prüfen & speichern:** Die Zusammenfassung zeigt Quellen, ausgelassene Bereiche,
   Zielordner und Aufbewahrung. Erst der Speichern-Knopf übernimmt die Einstellungen.

Fehlende Snapshots oder unverschlüsselte Bereiche werden konkret genannt. Ein
Bereich mit Fehlern wird nicht stillschweigend ausgelassen. Du kannst seine
Auswahl ändern oder die Ursache auf TrueNAS beheben. Für verschlüsselte Archive
müssen alle eingeschlossenen Bereiche schon auf dem NAS verschlüsselt sein.
Virtuelle Festplatten (Zvols) und eine bootfähige Bootplatten-Wiederherstellung
werden nicht unterstützt.

Unter **Meine Backups** siehst Du den letzten Stand. Dort kannst Du eine neue
Prüfung starten oder die letzte Sicherung vollständig auf Schäden prüfen lassen.
Das Schließen der Webseite beendet keine eingerichteten automatischen Backups.
Unter **Wiederherstellung verstehen** stehen Voraussetzungen und der Rückweg.

Die Seite wird nur auf `127.0.0.1` bereitgestellt. Öffne sie über Napback; fremde
Webseiten dürfen ihre Einstellungen nicht ändern. Detaillierte Bedienung:
[Weboberfläche](web-interface.md).

Den bisherigen Terminal-Assistenten startest Du bei Bedarf ausdrücklich mit
`napback setup --terminal`, auf Englisch mit zusätzlichem `--language en`.

## Was passiert automatisch?

TrueNAS muss Snapshots bereits anlegen. Dein PC schaut im eingestellten
Minutentakt nach und holt nur neue Stände ab. War der PC aus oder im Standby,
holt er beim nächsten Check den neuesten passenden Stand. Zwischenstände aus
der Offline-Zeit werden nicht alle nachträglich übertragen. Napback weckt den
PC nicht auf und ändert keine NAS-Snapshot-Aufträge.

Die Automatik startet bei Deiner Anmeldung. Für Sicherungen schon vor dem Login
kannst Du optional `loginctl enable-linger "$USER"` aktivieren. Das ist für den
normalen Betrieb nach Anmeldung nicht nötig.

Im Tray gibt es zusätzlich „Jetzt prüfen“, den Sicherungsordner und das
Prüfintervall. Ein Doppelklick öffnet die Weboberfläche. Das Tray kann beendet
werden; ein aktivierter Backup-Timer läuft unabhängig weiter.

## Daten und Verschlüsselung auf dem PC

Auf dem PC liegt ein **verschlüsseltes ZFS-Archiv in einem normalen Ordner**:

```text
Dein-Zielordner/
  latest -> snapshots/GENERATION/data
  snapshots/GENERATION/
    manifest.json
    inventory.jsonl
    data/QUELLEN-ID/
      000000-GUID.zfs    # Verschlüsselte Vollsicherung
      000001-GUID.zfs    # Verschlüsselte Änderung
```

**Die ursprüngliche ZFS-Verschlüsselung bleibt erhalten.** Napback schreibt keine
entschlüsselten Dateien und speichert keinen Entschlüsselungsschlüssel auf dem
PC. SSH schützt zusätzlich die Übertragung. Weder ein LUKS-Laufwerk noch ein
ZFS-Pool auf dem PC sind nötig. Dataset-/Snapshot-Namen, Zeiten, Größen und einige
Eigenschaften bleiben als Metadaten sichtbar.

Nach einer Vollsicherung werden nach Möglichkeit nur Änderungen übertragen.
Basisdateien werden über Hardlinks zwischen Versionen geteilt. Wenn eine alte
Quellgeneration fehlt oder die Kette 30 Streams erreicht, folgt eine neue
Vollsicherung. `raw_full_every` ändert diese Grenze. Standardmäßig bleiben
30 erfolgreiche lokale Versionen erhalten; `"keep": 0` deaktiviert die Löschung.
Benötigte Basisstreams bleiben bei der Aufbewahrung stets erhalten.

Das Ziel benötigt Hardlinks und erweiterte Attribute, etwa ext4, XFS oder Btrfs.
Archivdateien nicht bearbeiten. `napback verify` prüft alle Dateien und die
Abhängigkeiten ohne NAS und ohne Schlüssel. Prüfsummen erkennen Schäden, können
sie aber nicht reparieren; ein beschädigter Basisstream betrifft auch abhängige
Versionen. Eine weitere unabhängige Sicherung bleibt nötig.

## Verschlüsselte Sicherung wiederherstellen

```sh
napback verify
napback restore-zfs tank/wiederhergestellt --source dataset-1
```

Das Ziel ist ein **neues Dataset auf dem NAS aus der Konfiguration**, kein
PC-Ordner. Sein übergeordnetes Dataset muss existieren. Das Ziel selbst darf noch
nicht existieren. Gewählte Quelle und Kind-Datasets werden aus Voll- und
Änderungsstreams wiederhergestellt. Vorhandene Datasets werden nicht überschrieben.
Mit nur einer Hauptquelle kann `--source` entfallen. `napback list` zeigt die Namen.

Die empfangenen Datasets bleiben zunächst ungemountet. Auf TrueNAS mit dem
ursprünglichen ZFS-Schlüssel entsperren und sichere Mountpoints zuweisen.
Empfangene Kinder können eigene Verschlüsselungswurzeln sein und denselben
Originalschlüssel nochmals benötigen. Bei manuellen Mountpoints TrueNAS'
Pool-`altroot` beachten: `/mnt` wird häufig bereits automatisch vorangestellt.

**Originalschlüssel bzw. Passphrase separat sichern.** Napback exportiert sie
nicht. Ohne Schlüssel kann auch Napback nichts entschlüsseln. Dateien lassen
sich auf einem PC ohne ZFS nicht direkt aus diesen Archiven herauslesen; dafür
auf TrueNAS oder einem anderen kompatiblen ZFS-System wiederherstellen.
Für einen Ersatz-NAS die SSH-Verbindung in einer Kopie der Konfiguration ändern.
Details stehen in [encryption.md](encryption.md).

## Update von Version 0.1 oder 0.2

`./install.sh` erneut ausführen. Bestehende Installation wird daneben gesichert.

**Bisherige Klartext-Sicherungen bleiben Klartext.** Bestehende Konfigurationen
behalten `files` als Modus. Für Verschlüsselung einen neuen Zielordner mit
`napback setup` und `zfs_raw` einrichten. Die Ordner dürfen nicht vermischt werden;
Napback prüft dafür unterschiedliche Repository-Markierungen. Alte Sicherungen
werden nicht automatisch gelöscht oder nachträglich verschlüsselt.

Wer ausdrücklich lesbare, unverschlüsselte Dateiversionen möchte, kann weiter
`files` wählen. Nur dafür gilt `napback restore ~/Wiederhergestellt`.
Bei reinen ZFS-Quellen gilt danach automatisch die neue Snapshot-Erkennung.
Die erste Sicherung nach dem Update ergänzt die bisher fehlenden ZFS-GUIDs;
danach werden gleiche Generationen übersprungen. Für das alte 24-Stunden-Verhalten
explizit `"trigger": "interval"` setzen. Normale Verzeichnisquellen behalten
automatisch den Intervallmodus.

Prüfdetails und Grenzen der Tests stehen im [Testbericht](testing.md).
