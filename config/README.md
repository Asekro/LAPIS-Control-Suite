# Aufbau und Kommunikation konfigurieren

`devices.example.json` hält Anschlussdaten und Kommunikationsparameter zentral
fest. Eingetragen sind Bender COM12/COM11, der bisher getestete Scope und der vom
Benutzer eingetragene Laser-Port COM10. Den tatsächlichen Aufbau vor dem Verbinden prüfen.

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
| `write_timeout_s` | Separater Schreibtimeout des Lasers in Sekunden |
| `timeout_ms` | VISA-Wartezeit in Millisekunden |
| `visa_backend` | `""` für die vorhandene Standard-VISA-Bibliothek |
| `handshake_interface` | `null`, `"SERIAL"` oder `"USB"`; bezeichnet die Schnittstelle am Laser |
| `echo_handshake` | NHS-Byte-Echo-Modus; `false` ist der am Aufbau geprüfte Blockversand |
| Baudrate, Datenformat, `xonxoff`/`rtscts`/`dsrdtr` | Festes unterstütztes Profil: 9600 Baud, 8N1, Leitungs-Handshake aus |
| `terminator` | Sende-/Empfangsabschluss: seriell `"\r\n"`, Scope `"\n"` |
| `minimum_command_delay_s`, `command_handshake` | Festes Laserprofil: mindestens 100 ms Befehlsabstand, Protokoll-Handshake an |

Die festen Profilwerte sind sichtbar, aber keine frei unterstützten Alternativen.
Zum Beispiel wird `baudrate: 115200` vor dem Verbinden mit einer
`ConfigurationError` zurückgewiesen. Damit bleibt eine versehentliche Änderung
erkennbar. Ports, Timeouts und unterstützte Optionen werden an die Treiber übergeben.
Für eine andere Protokollvariante müssen Manual und Treiber gemeinsam angepasst werden.

Der Lader öffnet weder Ports noch VISA-Verbindungen. Import oder Dateiänderung
bewirken keine Geräteaktion. Er schaltet auch keine Ausgänge ein. Direkte Nutzung
der Treiber ohne Konfigurationsdatei bleibt möglich. Siehe
[ADR-008](../docs/architecture/ADR-008-central-communication-configuration.md).
