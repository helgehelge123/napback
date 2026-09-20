# Napback im Browser bedienen

Öffne Napback im Anwendungsmenü oder doppelklicke auf das Tray-Symbol. `napback ui`
oder `napback setup` öffnet dieselbe Oberfläche. Sie läuft nur auf Deinem PC.

## Mehrere Aufträge und zusätzliche Kopien

Oben wählst Du einen Auftrag oder legst über **Neuer Auftrag** einen weiteren an.
**Auftrag kopieren** übernimmt die gespeicherte Auswahl; wähle danach einen
eigenen Zielordner. Jeder Auftrag hat eine eigene Speicherart und Automatik.
[Ausführliche Anleitung mit Konfigurationssicherung](multiple-backups.md).

## 1. NAS verbinden

Die **NAS-Adresse** ist die Adresse Deiner TrueNAS-Weboberfläche, ohne `https://`.
Der **Benutzer** ist Dein Konto auf TrueNAS. Der **SSH-Schlüssel** ist eine Datei
auf Deinem PC, mit der sich Napback dort anmeldet. Angezeigt werden vorhandene
Schlüsseldateien, keine geheimen Schlüsselinhalte. Einen anderen Pfad kannst Du
bei Bedarf selbst wählen. Ein bereits eingerichteter SSH-Alias funktioniert über
die zusätzliche Hilfe ebenfalls.

„Verbindung prüfen & Daten anzeigen“ liest nur die Namen, Größen und Snapshots
auf dem NAS. Es erstellt noch keine Sicherung und verändert keine NAS-Daten.
Bei einem Fehler erklärt die Seite, ob beispielsweise das NAS nicht erreichbar
ist, der Schlüssel abgelehnt wurde oder die nötigen Rechte fehlen.

## 2. Daten auswählen

Ein **Dataset** ist ein eigener Datenbereich auf TrueNAS. Hake die Bereiche an,
die Du sichern möchtest. Mit dem Pfeil klappst Du Unterbereiche auf. Ein Haken
beim Elternbereich wählt seine unterstützten Kinder mit aus; einzelne Bereiche
lassen sich danach wieder abwählen. Virtuelle Festplatten und das Boot-System
sind ausdrücklich als nicht unterstützt markiert.

Neben den Bereichen siehst Du den Zeitpunkt des letzten Snapshots. Ein Klick
zeigt die tatsächlichen Namen der bis zu drei neuesten Snapshots. Ein Snapshot
ist ein festgehaltener Datenstand. Die Zusammenfassung zeigt den neuesten Stand,
der zu den ausgewählten Bereichen passt. Eine Eingabe wie `auto-` ist unnötig.

Fehlende oder zu alte Snapshots stehen direkt unter der Auswahl. Bei Kindern,
die den passenden Stand nicht haben, zeigt Napback ihre Namen. „Diese Bereiche
… abwählen“ ändert nur Deine Auswahl; es löscht nichts auf dem NAS. Überlege dabei,
ob Du diese Daten wirklich auslassen möchtest. Die abschließende Zusammenfassung
führt alle ausgelassenen Bereiche auf.

Wenn ein TrueNAS-Auftrag einzelne Kinder auslässt, übernimmt Napback das nicht
ungefragt. Deine Haken bestimmen die Sicherung. Neue Kinder werden in Zukunft
mitgesichert, sofern sie nicht unter einem ausdrücklich ausgeschlossenen Bereich
liegen. Für sie müssen ebenfalls passende Snapshots vorhanden sein.

## 3. Auf dem PC speichern

Mit **Ordner auswählen** kannst Du auf Deinem PC nach einem Speicherort suchen.
Du kannst im Dialog auch den Namen eines neuen Backup-Unterordners angeben. Er
wird erst beim Speichern angelegt. Eine externe Platte muss vorher eingebunden
sein. Bestehende fremde Dateien werden nicht überschrieben.

**Verschlüsselt aufbewahren** erhält die ZFS-Verschlüsselung des NAS. Dafür müssen
alle gewählten Datenbereiche schon auf dem NAS verschlüsselt sein. Auf dem PC
liegen verschlüsselte Archivdateien. Zum Wiederherstellen brauchst Du ein
ZFS-System und die ursprünglichen ZFS-Schlüssel; bewahre diese separat auf.

**Als lesbare Dateien speichern** erzeugt unverschlüsselte Kopien. Wähle das nur,
wenn Du diese Speicherung bewusst möchtest. Die Oberfläche wechselt bei Problemen
niemals selbst in den unverschlüsselten Modus.

Unter **Einstellungen mitsichern** kannst Du diesen Napback-Auftrag und/oder die
TrueNAS-Systemkonfiguration ergänzen. Beide werden separat verschlüsselt. Lade
den Wiederherstellungsschlüssel herunter und bewahre ihn zusätzlich außerhalb
des PCs auf. Ohne ihn sind diese Konfigurationskopien nicht wiederherstellbar.

Das **Minutenintervall** bestimmt, wie oft nach neuen Ständen geschaut wird. Ohne
neuen Snapshot wird nichts kopiert. **Gespeicherte Stände behalten** bestimmt die
Aufbewahrung auf dem PC: 30 bedeutet die letzten 30 erfolgreichen Stände; 0 heißt
alle. Alte Stände werden erst nach einer neuen erfolgreichen Sicherung entfernt.

## 4. Prüfen und speichern

Napback liest das NAS erneut und prüft die ausgewählten Snapshots sowie den
Zielordner. Es zeigt Quelle, Ziel, Verschlüsselung, Umfang, Ausschlüsse und
Aufbewahrung. Die Größen sind eine Orientierung und keine Vorhersage der späteren
Archivgröße. Neue Vollsicherungen können zusätzlichen Platz benötigen.

Erst **Einstellungen speichern** schreibt die echte Konfiguration. Eine bisherige
Konfiguration wird daneben gesichert. Wenn Du die Automatik gewählt hast, startet
der Timer. Er läuft unabhängig vom Browser. Ein Fehler beim Umstellen des Timers
wird angezeigt; die Übersicht zeigt seinen tatsächlichen Zustand.

Du kannst jederzeit über **Zurück & ändern** korrigieren. Hat ein anderes
Programm die Konfiguration zwischenzeitlich geändert, wird sie nicht überschrieben;
Du musst die Seite neu laden und erneut prüfen.

## Meine Backups

Hier siehst Du den letzten Erfolg, den Zustand und das Ziel. **Jetzt nach neuen
Daten schauen** startet den unabhängigen Hintergrunddienst. **Letzte Sicherung
prüfen** liest die letzte vollständige Sicherung und kontrolliert ihre Prüfsummen.
Eine Prüfung erkennt Schäden, repariert sie aber nicht.

Die Seite **Wiederherstellung verstehen** erklärt den Rückweg. Die tatsächlichen
Restore-Befehle bleiben vorerst in der Kommandozeile. Die Weboberfläche ersetzt
keinen Test einer vollständigen Wiederherstellung.

## Lokaler Zugriff

Die Oberfläche lauscht ausschließlich auf `127.0.0.1`. Sie verwendet keine Cloud,
externen Schriften oder externen Skripte. Eine lokale Sitzung schützt den Zugriff
auf Einstellungen; Anfragen anderer Webseiten werden abgewiesen. Starte Napback
über den Launcher oder das Tray, damit Dein Browser diese Sitzung erhält.
