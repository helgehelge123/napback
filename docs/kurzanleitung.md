# Napback unter Linux

Napback prüft vorhandene TrueNAS-Snapshots und holt neue Generationen auf Deinen
PC. Seit Version 0.3 bleibt dabei die native ZFS-Verschlüsselung erhalten.
LUKS und ZFS auf dem PC sind nicht nötig. Standardmäßig jede Minute; das Intervall lässt sich bei der ersten
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

Der Assistent erklärt jeden Schritt auf Deutsch direkt vor der Eingabe. `?`
öffnet bei Bedarf die ausführliche Hilfe zum aktuellen Feld; Enter übernimmt die Vorgabe in eckigen
Klammern. Falsche Eingaben kannst Du direkt korrigieren. Englisch ist mit
`napback setup --language en` verfügbar.

| Eingabe | Bedeutung und Beispiel |
| --- | --- |
| TrueNAS-Zugang / SSH-Host | `backup@192.168.1.10`: vor `@` der Benutzer auf TrueNAS, dahinter die NAS-Adresse. Kein `https://` und kein Ordnerpfad. Ein vorhandener SSH-Alias geht ebenfalls. |
| SSH-Schlüsseldatei | Pfad zum privaten Schlüssel auf Deinem PC, etwa `~/.ssh/id_ed25519_nas`. Nicht den Schlüsselinhalt eingeben. Der öffentliche Schlüssel aus der passenden `.pub`-Datei gehört in TrueNAS zum Benutzer. |
| Erhöhte Rechte / sudo | Ja, wenn Dein NAS-Benutzer ZFS-Befehle mit `sudo -n` ohne Passwortabfrage ausführen darf. |
| Datasets | Vollständige Namen aus der angezeigten Liste, etwa `tank/dokumente,tank/fotos`. **Ohne `/mnt/` eingeben:** `Apps` statt `/mnt/Apps`. Kinder werden mitgesichert. |
| Snapshot-Namensanfang | `auto-` passt zu `auto-2026-09-20_12-00`; bei `autosnap_...` entsprechend `autosnap_`. `*` erlaubt alle Namen. |
| Zielordner | Neuer oder leerer Ordner auf dem PC, etwa `~/NAS-Backup` oder auf einer bereits eingebundenen Festplatte. |
| Speichermodus | `zfs_raw` erhält die vorhandene ZFS-Verschlüsselung; `files` speichert unverschlüsselte Dateien. |
| Minutenintervall | Standard `1`: jede Minute nach neuen Snapshots schauen. Ohne neue Generation wird nichts kopiert. |

In TrueNAS findest Du den SSH-Dienst unter **System > Services > SSH**. Beim
Benutzer unter **Credentials > Users** (je nach Version **Local Users**) den
SSH-/Shell-Zugang und den **Public SSH Key** prüfen. Die Oberfläche unterscheidet
sich etwas zwischen TrueNAS-Versionen. Bei Verbindungsfehlern zeigt der Assistent
konkrete Hinweise und einen passenden SSH-Testbefehl. Den Server-Fingerabdruck vor
der ersten Bestätigung über die NAS-Konsole prüfen.

Offizielle TrueNAS-Hilfe: [SSH-Dienst](https://www.truenas.com/docs/scale/25.10/scaletutorials/systemsettings/services/sshservicescale/)
und [Benutzereinstellungen](https://www.truenas.com/docs/scale/25.10/scaleuireference/credentials/usersscreen/).

Übernimm `zfs_raw`, um die vorhandene ZFS-Verschlüsselung zu erhalten. Alle
gewählten Datasets einschließlich ihrer Kinder müssen verschlüsselt sein.
Unverschlüsselte Quellen werden abgewiesen; es gibt keinen stillen Klartext-Fallback. Enter übernimmt eine Minute; erlaubt sind
1 bis 1440 Minuten. Ein vorhandener SSH-Alias kann den Schlüssel und weitere
SSH-Einstellungen enthalten. Den Hostschlüssel vorher prüfen. SSH und ggf.
`sudo -n` auf dem NAS müssen ohne Passwortabfrage funktionieren.

TrueNAS muss bereits regelmäßige Snapshots anlegen, bei Kind-Datasets gemeinsam
als rekursive Snapshots. `auto-` ist lediglich der standardmäßige Namensanfang,
nach dem Napback sucht, beispielsweise `auto-2026-09-20_12-00`. Napback erstellt
selbst keine NAS-Snapshots. Unter **Data Protection > Periodic Snapshot Tasks**
einen passenden Auftrag prüfen oder anlegen, bei Kindern **Recursive** aktivieren.
Vor dem Fortfahren muss bereits eine passende Generation existieren.
Den Filter wählst Du im Assistenten oder später mit `snapshot_prefix` in der
Konfiguration; ein leerer JSON-Wert erlaubt jeden Namensanfang. Standardmäßig darf
die gewählte Generation höchstens 48 Stunden alt sein.

Nur beim alternativen Modus `files` kann als Docker-Image `napback-source:0.1.0` verwendet werden. Es wird auf dem
NAS mit `docker compose -f docker/compose.yaml build` erstellt. Das Sender-Image
bleibt auch mit Napback 0.3.2 bei Version 0.1.0. Alternativ verwendet Napback das
auf dem NAS installierte rsync. Es wird kein dauerhaft laufender Container
benötigt und kein zusätzlicher Port geöffnet. Im verschlüsselten Modus nutzt
Napback direkt `zfs send -w -p` auf TrueNAS; Docker und rsync werden dafür nicht
gebraucht. Auch gesperrte Datasets lassen sich so sichern.

```sh
napback plan          # Welche Snapshots werden gelesen?
napback run           # Jetzt prüfen und neue Generation sichern
napback status        # Letzter Erfolg und letzter Versuch
napback verify        # Lokale Sicherung vollständig prüfen
napback restore-zfs tank/wiederhergestellt --source dataset-1
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
