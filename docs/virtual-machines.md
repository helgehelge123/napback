# Virtuelle Maschinen sichern und wiederherstellen

Ab Napback 0.7 lassen sich **verschlüsselte virtuelle Festplatten (Zvols)** sichern.
Auf dem PC liegen verschlüsselte ZFS-Archive. Der PC braucht kein ZFS.

## Einrichten

1. Öffne den Auftrag in Napback. Wähle alle virtuellen Festplatten der VM im
   Datenbaum aus. Ein gewählter Elternbereich nimmt sie ebenfalls mit, sofern
   sie nicht ausdrücklich ausgeschlossen sind.
2. Wähle **Verschlüsselt aufbewahren**. Die Festplatten müssen auf dem NAS bereits
   ZFS-verschlüsselt sein. Unverschlüsselte Zvols und der Modus **Als lesbare
   Dateien speichern** werden für virtuelle Festplatten nicht unterstützt.
3. Aktiviere **VM-Einrichtung mitsichern**. Speichere den angebotenen
   Wiederherstellungsschlüssel separat. Diese Option sichert CPU, RAM,
   Gerätezuordnungen, Netzwerkeinstellungen sowie vorhandene UEFI- und TPM-Dateien
   aller unter „Virtual Machines“ eingerichteten VMs. Die Option allein sichert
   keine Festplatten.
4. Prüfe die Zusammenfassung. Bestehende Ausschlüsse bleiben beim Update erhalten.
   Entferne einen früheren Ausschluss der gewünschten VM-Platte in der Auswahl.
5. Auf TrueNAS müssen regelmäßige Snapshots aller gewählten Platten vorhanden sein.
   Unter einem gemeinsam gewählten Elternbereich verlangt Napback denselben
   Snapshot-Namen. Nach einem neuen Snapshot folgt die Sicherung bei der nächsten
   Prüfung auf dem eingeschalteten PC.

Der erste Lauf überträgt einen vollständigen ZFS-Datenstrom. Weitere Läufe
übertragen Änderungen. Die Größe des Archivs richtet sich nach belegten Blöcken,
nicht allein nach der eingerichteten maximalen Plattengröße.

## Was dieser Stand bedeutet

Napback liest vorhandene Snapshots. Es stoppt, pausiert oder friert keine VM ein
und sichert keinen Arbeitsspeicher. Ein Snapshot eines laufenden Gasts ist wie
ein Stand nach Stromausfall: Das Gastdateisystem und Datenbanken können beim
Start eine Wiederherstellung benötigen. Für einen sauber heruntergefahrenen
Stand musst Du die VM vor dem Snapshot herunterfahren. Anwendungseigene
Datenbank-Backups bleiben sinnvoll.

Bei mehreren Platten müssen alle Platten vom selben konsistenten Zeitpunkt
stammen. Verschiedene getrennte Snapshot-Aufträge oder gleich benannte, einzeln
erzeugte Snapshots garantieren das nicht. Napback kann die Gastkonsistenz aus
Snapshot-Namen nicht feststellen. Verwende einen gemeinsamen rekursiven
Snapshot-Auftrag oder einen Snapshot bei ausgeschalteter VM.

VM-Einrichtung und Firmware werden **zum Exportzeitpunkt** gelesen. Sie können
neuer als der gewählte Festplatten-Snapshot sein. Das Exportdatum steht im Archiv.
Der Export bricht bei geänderter Einrichtung, fehlenden erwarteten UEFI-/TPM-Dateien
oder laufenden Gästen mit emuliertem TPM ab. Für TPM-Gäste ist ein gemeinsamer
Offline-Stand von Platten, Einrichtung und TPM-Dateien erforderlich. Ein
Windows-/BitLocker-/Secure-Boot-Restore wurde nicht getestet; bewahre auch die
Wiederherstellungsschlüssel des Gastbetriebssystems separat auf.

Legacy-VMs unter „Containers/Instances“, durchgereichte physische Geräte und
dateibasierte RAW-/QCOW2-Platten werden nicht automatisch in Zvol-Backups
umgewandelt. Die Einrichtungssicherung ersetzt diese Datenträger nicht.

## Wiederherstellen

1. Prüfe die lokale Sicherung:

   ```sh
   napback verify
   napback list
   ```

2. Spiele die gewünschte Platte unter einem **neuen** ZFS-Namen ein. Die
   Quellkennung steht in `napback list` bzw. im Manifest des Sicherungsstands:

   ```sh
   napback restore-zfs tank/recovered/vm-disk --source SOURCE_ID
   ```

   Der Elternbereich `tank/recovered` muss existieren. Napback lehnt bestehende
   Ziele ab. Es verwendet kein erzwungenes Zurücksetzen und überschreibt keine
   produktive Platte. Mit `--snapshot GENERATION` wählst Du einen älteren Stand.

3. Die Platte bleibt zunächst mit `volmode=none` verborgen. Lade auf dem NAS
   ihren ursprünglichen ZFS-Schlüssel. Aktiviere danach ausdrücklich den neuen
   Datenträger:

   ```sh
   sudo zfs load-key tank/recovered/vm-disk
   sudo zfs set volmode=dev tank/recovered/vm-disk
   ```

   `load-key` braucht je nach ursprünglichem Schlüsselformat eine passende
   Schlüsseldatei oder Passphrase. Der Napback-Konfigurationsschlüssel ist dafür
   kein Ersatz. Die Geräteadresse lautet anschließend
   `/dev/zvol/tank/recovered/vm-disk`.

4. Entschlüssele die VM-Einrichtung in eine neue private Datei:

   ```sh
   napback decrypt-config /PFAD/ZUM/STAND/data/.napback-settings/truenas-vms.json.fernet \
     /PRIVATER/PFAD/vm-einrichtung.json --key /PFAD/ZUM/napback-recovery.key
   ```

   Die JSON-Datei enthält vertrauliche Einstellungen. Nicht nach GitHub oder in
   öffentlich geteilte Ordner legen. Unter `vms` stehen CPU, RAM, Bootmodus und
   Geräte. Unter `firmware` stehen relative Pfade, Dateirechte, SHA-256 und Base64
   der Firmware-Dateien. Änderungen an VM-Namen/IDs können andere Zielnamen für
   UEFI-/TPM-Dateien erfordern; die alten Namen nicht blind über produktive
   Dateien kopieren.

5. Lege auf TrueNAS eine **neue VM** mit den gesicherten Einstellungen an. Ordne
   die wiederhergestellten Platten zu. Prüfe Bootmodus, Controller-Reihenfolge und
   gegebenenfalls UEFI-/TPM-Zustand. Eine vollständige TrueNAS-Systemkonfiguration
   kann zusätzlich beim Neuaufbau des gesamten NAS helfen.
6. Starte die wiederhergestellte VM zunächst **ohne Netzwerkkarte**. So gibt es
   keine doppelte IP-Adresse und keine unerwünschten Schreibzugriffe auf andere
   Dienste. Prüfe Gaststart und Anwendungen, bevor Du sie produktiv anschließt.

Napback legt keine VM automatisch an und startet sie nicht. Die Rücksicherung
einer virtuellen Festplatte und der Neuaufbau der VM sind getrennte Schritte.

## Praktisch geprüft

Auf TrueNAS 25.10 wurde eine verschlüsselte, bootfähige 64-MiB-Testplatte vollständig
und anschließend inkrementell gesichert. Nach Aufbewahrung nur einer Generation
blieben beide nötigen Datenströme vorhanden. Die Rücksicherung unter neuem Namen
war zunächst verborgen. Nach Laden des ursprünglichen Testschlüssels stimmte die
SHA-256-Prüfsumme der gesamten Platte mit der Quelle überein. Ein isolierter
QEMU-Gast startete von dieser Platte und meldete seinen erfolgreichen Start.

Das prüft den realen ZFS- und Bootpfad. Es ist kein Test jedes Gastbetriebssystems
oder der Anwendungen in Deinen produktiven VMs.

Quellen: [TrueNAS VM API](https://api.truenas.com/v25.10/api_methods_vm.query.html),
[OpenZFS receive](https://openzfs.github.io/openzfs-docs/man/master/8/zfs-receive.8.html).
