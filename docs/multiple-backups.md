# Mehrere Kopien und Einstellungen sichern

## Mehrere Aufträge

Oben in der Weboberfläche wählst Du den aktuellen **Sicherungsauftrag** aus.
Jeder Auftrag hat einen Namen, eine Datenauswahl, einen Zielordner, eine Speicherart
und einen eigenen Zeitplan. Ein Fehler bei einem Auftrag hält die anderen nicht an.

- **Neuer Auftrag** übernimmt den NAS-Zugang. Daten und Ziel wählst Du neu.
- **Auftrag kopieren** übernimmt die gespeicherten Einstellungen. Gib der Kopie
  einen passenden Namen und wähle einen neuen, leeren Zielordner.
- Prüfe die Zusammenfassung und speichere. Für neue Aufträge ist die Automatik
  zunächst aus; schalte sie ein, wenn diese Kopie regelmäßig laufen soll.

Eine Kopie wird erst beim Speichern eingerichtet. Zwei Aufträge dürfen nicht
in denselben oder ineinander liegende Zielordner schreiben. Unterschiedliche
Unterordner derselben Platte funktionieren; gegen einen Plattenausfall hilft
aber nur eine Kopie auf einem anderen Laufwerk.

Du kannst dieselben Daten mehrfach sichern oder die Auswahl aufteilen. Beispiel:
„Private Dokumente“ als verschlüsselte ZFS-Archive und „Medien“ als lesbare Dateien.
Die Speicherart gilt jeweils für den gesamten Auftrag. Bereits gespeicherte
Archive werden beim Kopieren eines Auftrags nicht dupliziert: Das neue Ziel
bekommt beim ersten Lauf eine unabhängige Sicherung direkt vom NAS.

Für verschlüsselte ZFS-Archive müssen alle eingeschlossenen Datasets auf dem NAS
verschlüsselt sein. Lesbare Kopien liegen auf dem PC unverschlüsselt; die Quellen
müssen dafür entsperrt und gemountet sein. Das Programm wechselt die Speicherart
nie automatisch. Zum Ändern der Speicherart eines vorhandenen Archivs brauchst
Du einen neuen Zielordner.

Das Tray zeigt den Zustand aller gespeicherten Aufträge. Bei mehreren Aufträgen
öffnet „Aufträge verwalten“ die Oberfläche. Dort wählst Du den jeweiligen Auftrag
für Prüfung, Einstellungen oder einen manuellen Lauf.

## Einstellungen mitsichern

In Schritt **Auf dem PC speichern** gibt es drei unabhängige Haken:

- **Diesen Napback-Auftrag mitsichern:** die Konfigurationsdatei dieses Auftrags,
  also NAS-Zugangsdaten als Adresse und Schlüsselpfad, Datenauswahl, Ziel und
  Prüfintervall. Private SSH-Schlüssel werden nicht eingepackt.
- **TrueNAS-Systemkonfiguration mitsichern:** der offizielle TrueNAS-Export mit
  Datenbank, Passwort-Secret-Seed und vorhandenen Administrator-SSH-Zugangslisten.
  Das kann Zugangsdaten enthalten. Es ersetzt weder Dataset-Backups noch separat
  gesicherte ZFS-Schlüssel und Passphrasen oder eine bootfähige Installation.
- **App-Einrichtung mitsichern:** TrueNAS-App-Einstellungen und vorhandene
  Versionsverzeichnisse, Container-Einstellungen, Image-Versionen und Digests,
  Netzwerk- und Volume-Beschreibungen. Bei zusätzlichen Compose-Projekten werden
  die zugeordneten Compose-Dateien, vorhandene `.env`-Dateien, referenzierte
  Umgebungs-/Secret-/Konfigurationsdateien und die aufgelöste Compose-Konfiguration
  mitgesichert. Voraussetzung sind TrueNAS mit Docker-Apps und Docker Compose
  mit `config --no-env-resolution`. Enthaltene Zugangsdaten bleiben verschlüsselt.

Alle Konfigurationskopien sind immer zusätzlich verschlüsselt, auch in Aufträgen
mit lesbaren Dateien. Dafür nutzt Napback die etablierte Fernet-Verschlüsselung
von `cryptography`. Der Installer installiert die Python-Abhängigkeit mit.
Die ZFS-Datenarchive behalten weiterhin ihre ursprüngliche ZFS-Verschlüsselung.

1. Gewünschte Haken setzen.
2. **Wiederherstellungsschlüssel herunterladen** anklicken.
3. Den Schlüssel zusätzlich sicher außerhalb des PCs aufbewahren, beispielsweise
   auf einem geschützten USB-Stick, und dies bestätigen.
4. Zusammenfassung prüfen und speichern.

Der Schlüssel bleibt zusätzlich mit Dateirechten 600 lokal verfügbar, damit
Napback automatisch sichern kann. Er liegt außerhalb der Sicherungsordner und
wird nicht in die Archive aufgenommen. Wer Zugriff auf Dein Benutzerkonto oder
Administratorrechte auf dem PC hat, kann auch diesen lokalen Schlüssel lesen.
Die Verschlüsselung schützt eine Kopie des Backup-Ordners ohne den Schlüssel;
sie schützt nicht vor einem kompromittierten PC. Lade den Schlüssel nicht
ungeschützt in ein öffentliches Repository hoch.

Die Einstellungen werden bei jedem erfolgreichen Backup erneuert. Nach 24 Stunden
wird auch ohne neuen Dataset-Snapshot ein neuer Stand angelegt; unveränderte
ZFS-Streams werden dabei lokal weiterverwendet. Automatik, NAS-Erreichbarkeit und
gültige Quell-Snapshots sind weiterhin erforderlich. Schlägt der gewählte
Konfigurationsexport fehl, wird der gesamte neue Stand nicht als erfolgreich
veröffentlicht. Ältere vollständige Sicherungen bleiben erhalten.

## Einstellungen wiederherstellen

Die verschlüsselten Dateien findest Du im jeweiligen Stand unter
`data/.napback-settings/`. Sie werden von der normalen Integritätsprüfung und
Aufbewahrung mit erfasst.

Mit vollständigen Pfaden ausführen:

```sh
napback decrypt-config /BACKUP/data/.napback-settings/truenas-config.tar.fernet /SICHERER-ORDNER/truenas-config.tar --key /USB/napback-recovery.key
napback decrypt-config /BACKUP/data/.napback-settings/napback-config.json.fernet /SICHERER-ORDNER/napback-config.json --key /USB/napback-recovery.key
napback decrypt-config /BACKUP/data/.napback-settings/truenas-apps.tar.fernet /SICHERER-ORDNER/truenas-apps.tar --key /USB/napback-recovery.key
```

Napback schreibt nur eine neue Datei mit Dateirechten 600 und überschreibt nichts.
Ein falscher Schlüssel oder ein beschädigtes Archiv wird abgewiesen. Die Ausgabe
ist anschließend **unverschlüsselt**. Wähle dafür einen geschützten Ordner.

Die TrueNAS-Datei lässt sich über die TrueNAS-Funktion **Upload Config** wieder
laden. Dabei kann TrueNAS neu starten. Napback führt diesen Schritt nicht aus.
Nach einer kaputten Bootplatte zuerst TrueNAS neu installieren, dann die passende
Konfiguration wieder einspielen. Dataset-Daten und ZFS-Schlüssel bleiben eigene
Bestandteile der Wiederherstellung.

Die Napback-Datei enthält genau einen Auftrag. Prüfe besonders Zielordner,
Repository-ID und SSH-Schlüsselpfad, bevor Du ihn auf einem neuen PC übernimmst.
Timer musst Du anschließend erneut einrichten. Ein exportierter Schlüsselpfad
ersetzt nicht die dazugehörige private SSH-Schlüsseldatei.

## App-Einrichtung zurückholen

Das entschlüsselte App-Archiv ist ein gzip-komprimiertes Tar-Archiv. Lege einen
neuen privaten Ordner an und entpacke es dort, niemals direkt über die laufende
TrueNAS-Installation:

```sh
mkdir -m 700 /SICHERER-ORDNER/apps-zurueckgeholt
tar -xzf /SICHERER-ORDNER/truenas-apps.tar -C /SICHERER-ORDNER/apps-zurueckgeholt
```

Unter `files/mnt/.ix-apps/app_configs` liegen die TrueNAS-App-Einstellungen,
Versionsverzeichnisse und erzeugten Compose-Dateien. `docker/` enthält die
Container-, Image-, Netzwerk- und Volume-Beschreibungen. `compose/projects.json`
ordnet zusätzliche Compose-Projekte ihren Originalpfaden und aufgelösten
Konfigurationen zu. `manifest.json` enthält die SHA-256-Prüfsummen sämtlicher
Dateien; Napback prüft sie vor dem verschlüsselten Speichern.

Auf einem Ersatz-NAS zuerst die Dataset-Daten und dann die Apps mit passenden
Versionen, Pfaden, Benutzern und gespeicherten Einstellungen wieder einrichten.
Das Archiv unterstützt diese Rekonstruktion; Napback spielt es nicht automatisch
in TrueNAS ein und startet daraus keine Container. Vorhandene Volume-Inhalte,
Container-Images, Quellcode für eigene Builds und Dateien aus Bind-Mounts sind
keine Bestandteile dieses Konfigurationsarchivs. Sie benötigen weiterhin passende
Datenbackups beziehungsweise verfügbare Images. Docker-Beschreibungen für
Container ohne Compose-Datei dienen der manuellen Rekonstruktion.

Die App-Einrichtung wird zum Zeitpunkt des Exports gelesen. Dieser Zeitpunkt
steht in `system.json` und kann neuer als der gesicherte Dataset-Snapshot sein.
Vor einem Restore deshalb die App- und Datenbankversion mit dem gewünschten
Datenstand abgleichen; ältere vorhandene App-Versionsverzeichnisse werden erhalten.
Ändern sich beobachtete Konfigurationsdateien, Container-Definitionen oder
aufgelöste Compose-Einstellungen während des Exports, bricht die Sicherung ab.
Fehlende erforderliche Dateien werden nicht stillschweigend übersprungen.

Die App-Sicherung liest `ix-apps` nur. Sie verändert weder das Dataset noch
laufende Apps. Ein verschlüsselter Dataset-Auftrag kann diese separat
verschlüsselte Konfigurationskopie aufnehmen, obwohl `ix-apps` selbst
unverschlüsselt ist.

Technische Grundlage: [TrueNAS config.save](https://api.truenas.com/v25.10/api_methods_config.save.html),
[TrueNAS core.download](https://api.truenas.com/v25.10/api_methods_core.download.html),
[Fernet](https://cryptography.io/en/latest/fernet/).
