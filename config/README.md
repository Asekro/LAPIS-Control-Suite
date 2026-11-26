# Aufbau und Kommunikation konfigurieren

`devices.example.json` hält Anschlussdaten und Kommunikationsparameter zentral
fest. Eingetragen sind Bender COM12/COM11, der bisher getestete Scope und der
Flare NX an COM10 mit USB-Handshake. Der Edwards TIC ist im aktuellen Aufbau
an COM13 angeschlossen (Abschnitt `vacuum`).

Für lokale Änderungen die Datei nach `devices.local.json` kopieren. Diese Kopie
wird von Git ignoriert. Der Dateipfad wird ausdrücklich angegeben; es gibt keinen
automatischen Rückfall auf die Beispieldatei.

```python
from lapis_control.configuration import load_devices

devices = load_devices("config/devices.local.json")  # Validate and construct; no hardware access.
scope = devices["oscilloscope"]
with scope:  # Connect here; close when leaving the block.
    print(scope.idn)
```

Relative Pfade beziehen sich auf das Arbeitsverzeichnis. Die Notebooks bestimmen
den Projektpfad und wählen die Datei über `CONFIG_PATH` in der Konfigurationszelle.
Nach Dateiänderungen diese Zelle erneut ausführen. Die Datei darf auch nur das
benötigte Gerät enthalten; pro vorhandenem Gerät sind die Felder aus dem Beispiel
vollständig anzugeben. Nicht benötigte Geräteabschnitte können entfallen.

| Parameter | Bedeutung / Änderung |
|---|---|
| Ports, `resource` | Aufbauspezifisch, frei anpassbar |
| `positive_serial`, `negative_serial` | NHS-Geräte-Seriennummern, keine USB-IDs; `null` deaktiviert die optionale Serienprüfung |
| `max_voltage` | Anwendungslimit in Volt, keine Nennspannung |
| `timeout_s` | Serielle Wartezeit in Sekunden; beim NHS für Lesen und Schreiben |
| `write_timeout_s` | Separater Sendetimeout von Laser und TIC in Sekunden |
| `timeout_ms` | VISA-Wartezeit in Millisekunden |
| `visa_backend` | `""` für die vorhandene Standard-VISA-Bibliothek |
| `handshake_interface` | `null`, `"SERIAL"` oder `"USB"`; bezeichnet die Schnittstelle am Laser |
| `echo_handshake` | NHS-Byte-Echo-Modus; `false` ist der am Aufbau geprüfte Blockversand |

Baudrate, Datenformat, Abschlusszeichen und feste Handshake-Vorgaben legt der
jeweilige Treiber fest. Ältere lokale Dateien lassen sich weiter laden, wenn
die dort eingetragenen Protokollwerte zum Treiber passen. Zum Umstellen können
die gegenüber der neuen Vorlage entfallenen Felder einfach gelöscht werden.
Bei abweichenden Werten nennt der Lader das betroffene Feld.

Der Lader öffnet weder Ports noch VISA-Verbindungen. Import oder Dateiänderung
bewirken keine Geräteaktion. Er schaltet auch keine Ausgänge ein. Direkte Nutzung
der Treiber ohne Konfigurationsdatei bleibt möglich. Siehe
[ADR-009](../docs/architecture/ADR-009-adjustable-configuration.md).
