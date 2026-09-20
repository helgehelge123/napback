# Napback unter Linux

Napback holt vorhandene TrueNAS-Snapshots auf Deinen PC. Standardmäßig alle
24 Stunden seit der letzten erfolgreichen Sicherung. War der PC aus oder im
Standby, wird die Sicherung nachgeholt. Fehlgeschlagene Versuche zählen nicht.

## Einrichten

```sh
napback setup
```

Der Assistent fragt SSH-Zugang, Datasets und einen neuen oder leeren Zielordner
ab. Ein vorhandener SSH-Alias kann den Schlüssel und weitere SSH-Einstellungen
enthalten. Den Hostschlüssel vorher prüfen. TrueNAS muss regelmäßige, bei
Kind-Datasets rekursive Snapshots mit dem Namensanfang `auto-` anlegen.

Als Docker-Image kann `napback-source:0.1.0` verwendet werden. Es wird auf dem
NAS mit `docker compose -f docker/compose.yaml build` erstellt. Alternativ
verwendet Napback das dort installierte rsync.

```sh
napback plan          # Welche Snapshots werden gelesen?
napback run           # Jetzt sichern, sofern fällig
napback status        # Letzter Erfolg und letzter Versuch
napback verify        # Lokale Sicherung vollständig prüfen
napback restore ~/Wiederhergestellt
```

Der Hintergrund-Timer läuft über systemd. Für Sicherungen bereits vor der
Anmeldung einmal `loginctl enable-linger "$USER"` ausführen. Das kann abhängig
von Deiner Distribution eine lokale Autorisierung verlangen.

Ziel: Linux-Dateisystem mit Hardlinks und erweiterten Attributen, etwa ext4,
XFS oder Btrfs. Standard: 30 erfolgreiche Versionen. In `config.json` verhindert
`"keep": 0` das automatische Löschen alter Versionen.

Dateien aus `latest` herauskopieren, nicht dort bearbeiten: mehrere Versionen
können dieselben unveränderten Dateien über Hardlinks teilen. Für Symlinks die
Wiederherstellungsfunktion verwenden. Vollständige Metadaten-Wiederherstellung
und Grenzen stehen in [recovery.md](recovery.md).

47 Tests bestanden unter CachyOS und Ubuntu. Echte TrueNAS-Übertragungen,
Verbindungsabbruch, Wiederholung, Wiederherstellung und systemd-Timer geprüft.
Physischer Neustart und Suspend/Resume wurden nicht ausgelöst; Details im
[Testbericht](testing.md).
