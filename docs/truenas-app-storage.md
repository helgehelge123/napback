# Was aus `ix-apps` gesichert werden muss

`ix-apps` ist der von TrueNAS verwaltete Speicher für Apps. Ein Neustart stellt
gelöschte Daten nicht wieder her. Bei einer Neuinstallation können die folgenden
Teile erneut entstehen:

- **Container-Images aus einer Registry:** TrueNAS/Docker kann sie erneut laden,
  solange die benötigte Version dort verfügbar ist. Eigene nur lokal gebaute
  Images brauchen ihren Quellcode samt Bauanleitung oder eine eigene Sicherung.
- **App-Katalog:** Er lässt sich wieder herunterladen.
- **Temporäre Daten:** Beispielsweise Transcodes und heruntergeladene Modelle
  lassen sich meistens neu erzeugen. Die jeweilige App entscheidet darüber.

Diese Teile müssen dagegen gesichert werden:

- **App-Einrichtung:** Welche App und Version, welche Ports, Ordner und
  Einstellungen verwendet werden. Napbacks Option „App-Einrichtung mitsichern“
  legt dafür einen verschlüsselten Export an.
- **`app_mounts` / ixVolumes:** Darin können Datenbanken, Benutzerdaten und
  Einstellungen liegen. Eine neu installierte App legt leere Ordner an;
  die bisherigen Inhalte kommen dadurch nicht zurück.
- **Docker-Volumes unter `docker/volumes`:** Das können sowohl entbehrliche
  Caches als auch wichtige Datenbanken sein. Der Volumename allein genügt nicht
  zur Entscheidung. Prüfe die Zuordnung zu Container und Zielpfad.
- **Host-Pfade außerhalb von `ix-apps`:** Die eigentlichen App-Daten liegen oft
  in anderen Datasets. Diese brauchen ihre eigene Dataset-Sicherung.

Die App-Einrichtungssicherung enthält **keine Inhalte** dieser Datenordner,
keine Container-Images und keinen Arbeitsspeicher.

Sichere persistente Daten aus einem konsistenten Stand. Kopiere aktive
Datenbanken nicht blind als laufend veränderte Dateien. Datenbankexporte oder
Snapshots bei ruhender Anwendung sind zuverlässiger. TrueNAS verwaltet den
Bereich selbst; beim Restore keine fremde `ix-apps`-Kopie über laufende Apps
legen. Napback verändert dessen Snapshot-Aufträge nicht automatisch.

Quelle: [TrueNAS: App Storage](https://apps.truenas.com/getting-started/app-storage/).
