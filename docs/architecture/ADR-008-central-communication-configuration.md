# ADR-008: Zentrale Kommunikationskonfiguration

## Status
Accepted · 18.11.2026

Ergänzt ADR-005 und ADR-007 um eine zentrale, tatsächlich geladene Konfiguration.
Die festen Laser-Timeouts aus dem bisherigen Stand von ADR-007 werden durch
einstellbare Wartezeiten mit denselben Standardwerten ersetzt.

## Kontext

Die wichtigen Kommunikationsparameter sollen an einem Ort sichtbar sein:
Anschlussadresse, Baudrate, Datenformat, Handshake, Terminator und Timeouts.
Die bisherige Beispieldatei erfasste nur einen Teil der Einstellungen und wurde
noch nicht vom Programm verwendet. Gleichzeitig sollen versehentlich geänderte
Protokollwerte keine unkontrollierten Kommunikationsversuche auslösen.

## Entscheidung

- `config/devices.example.json` enthält pro Gerät die Aufbaudaten und einen
  `communication`-Abschnitt. Lokale Aufbauten verwenden eine ausdrücklich gewählte,
  von Git ignorierte `config/devices.local.json`.
- `lapis_control.configuration.load_devices(path)` liest und prüft genau die
  angegebene Datei und erzeugt unverbundene Geräteobjekte. Es gibt weder eine
  automatische Dateisuche noch einen stillen Rückfall auf Beispielwerte.
- Die Datei darf eine Teilmenge der bekannten Geräte enthalten. Aufrufe verbinden
  nur die tatsächlich benötigten Geräte über `connect()` oder `with`.
- Einstellbar sind Ports/Adressen, Zuordnungen, Anwendungslimits und Timeouts sowie
  ausdrücklich unterstützte Optionen wie VISA-Backend und Laser-Schnittstelle.
- Festgelegte Protokollwerte stehen ebenfalls sichtbar in der Datei. Der Lader
  vergleicht sie mit dem unterstützten Treiberprofil: 9600 Baud, 8N1, deaktivierter
  Leitungs-Handshake und die jeweiligen Terminatoren. Beim Laser prüft er zusätzlich
  den erforderlichen Befehlsabstand und den eingeschalteten Protokoll-Handshake.
- Feste Werte werden nicht als beliebige Parameter an die Treiber weitergegeben.
  Die Datei beschreibt die gewünschte Konfiguration; der Treiber definiert, was
  unterstützt ist. Abweichungen werden vor dem Öffnen eines Ports zurückgewiesen.
  Für weitere Gerätevarianten sind zuerst Manualprüfung und Treiberunterstützung nötig.
- Unbekannte oder fehlende Felder, doppelte JSON-Schlüssel, falsche Typen und
  ungültige Grenzen führen zu einer verständlichen `ConfigurationError`.
- Die Treiber selbst lesen keine Dateien. Direkte Nutzung mit ihren Standardwerten
  bleibt möglich; der Lader gehört zur Anwendungsebene. Beide Testnotebooks nutzen
  ihn in ihrer Konfigurationszelle. Schreibfreigaben bleiben ausdrücklich im Notebook.

## Folgen

Aufbau und Kommunikation lassen sich zentral prüfen. Protokollfelder in der Datei
und Treiberkonstanten haben bewusst verschiedene Aufgaben: gewünschtes Profil und
Unterstützungsgrenze. Sie werden beim Laden gegeneinander geprüft und können nicht
unbemerkt auseinanderlaufen. Die Offline-Tests prüfen auch die Übergabe der
einstellbaren Werte und den fehlenden Hardwarezugriff beim Laden.

Flare NX akzeptiert nun Lese- und Schreibtimeout im Konstruktor; der Standard
bleibt jeweils 1 s. Beim NHS gilt ein gemeinsamer Timeout für Lesen und Schreiben,
und die Portkonfiguration wird während einer Transaktion weiterhin nicht geändert.

Konfigurationsdateien ersetzen weder die Prüfung des angeschlossenen Geräts noch
die des elektrischen Aufbaus. Sie enthalten keine automatischen Ausgangsfreigaben
und aktivieren beim Laden keine Hochspannung, Laseremission oder WaveGen-Ausgabe.
