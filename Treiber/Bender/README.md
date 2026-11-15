# NHS-Treiber für den LAPIS-Bender

Python 3.10 oder neuer. Zwei iseg NHS 6040, positiv und negativ, jeweils Kanal 0 und 1.
Version 1.0, 15.11.2026. Implementiert und offline geprüft; noch nicht an realen NHS-Modulen getestet.

## Installation und erster Test

ZIP entpacken, Terminal in diesem Ordner öffnen:

```powershell
python -m pip install -r requirements.txt
python test_verbindung.py --positive COM7 --negative COM8
```

COM7/COM8 sind Platzhalter: Im Windows-Gerätemanager nachsehen und die beiden
Module eindeutig zuordnen. Der erste Test fragt ausschließlich Gerätekennung,
Polarität, Messwerte und Status ab. Er verändert keine Sollwerte und schaltet
keinen Ausgang. Falls erforderlich, FTDI-VCP-Treiber installieren.
Hersteller-GUI vor dem Test schließen, damit sie den COM-Port nicht belegt.
Bei Problemen mit `--debug` erneut ausführen; das protokolliert Befehle und Antworten.

## Verwendung in VS Code / Python

`iseg_nhs.py` neben dein Skript legen. Anschlussparameter stehen beim Erzeugen
**eines** Bender-Objekts:

```python
from iseg_nhs import Bender

with Bender("COM7", "COM8", max_voltage=100) as bender:
    print(bender.positive.identification)
    print(bender.negative.identification)
    print(bender.read_all())
```

Der Konstruktor selbst öffnet keine Verbindung. Alternativ `bender.open()` und
später `bender.close()` verwenden. `with` schließt beide Verbindungen auch bei
Ausnahmen. **Schließen verändert die Ausgänge nicht; aktive Spannungen bleiben aktiv.**

Folgende Aufrufe verändern Sollwerte/Ausgänge und sind bewusst nicht Teil des
Verbindungstests:

```python
# Innerhalb eines geöffneten Bender-Objekts:
bender.set_voltages(p0=10, p1=12, n0=-10, n1=-12)
# Bereits eingeschaltete Kanäle fahren dabei unmittelbar zum neuen Sollwert.
# Ausgeschaltete Kanäle bleiben aus.
bender.enable()       # schaltet genau p0, p1, n0, n1 ein
print(bender.read_all())
bender.disable()      # fordert Abschalten mit Rampe an
```

`p0` und `p1` sind Kanal 0/1 des positiven Moduls, `n0`/`n1` die entsprechenden
Kanäle des negativen Moduls. Die Zuordnung zu geometrischen Elektroden erfolgt
im Backend. Einzelzugriff beispielsweise:

```python
bender.negative.set_voltage(1, -15)
reading = bender.negative.read_channel(1)
print(reading.voltage_set, reading.voltage, reading.current)
```

Spannungen werden an der Python-Schnittstelle mit Vorzeichen angegeben. Der
Treiber sendet Beträge an die fest gepolten NHS-Ausgänge und normiert die
Spannungsrückgaben auf die geprüfte Hardwarepolarität. Für Ströme bleibt das
Gerätevorzeichen unverändert. Beim ersten Schreibtest muss diese Umsetzung
auch mit Anzeige und Istspannung der realen Module bestätigt werden.

## Funktionen

| Methode am NHSModule | Bedeutung |
|---|---|
| `set_voltage(ch, volts)` | Sollspannung setzen und Sollwert zurücklesen |
| `read_voltage_set(ch)` / `read_voltage(ch)` | Soll-/Istspannung in V |
| `read_current_set(ch)` / `read_current(ch)` | Strom-Sollwert / Messstrom in A |
| `set_current_limit(ch, amperes)` | ISET setzen; Regel-/Trip-Verhalten folgt der Gerätekonfiguration |
| `enable(ch)` / `disable(ch)` | Kanal einschalten / mit Rampe ausschalten |
| `emergency_off(ch)` | Ohne programmierte Rampe abschalten; Zustand bleibt verriegelt |
| `clear_emergency(ch)` | Verriegelung aufheben; schaltet nicht ein |
| `read_status(ch)` / `read_events(ch)` | Dekodierbare Status-/Ereignisregister |
| `read_module_status()` / `read_module_events()` | Modulregister |
| `clear_events(ch)` / `clear_events()` | Kanalereignisse / Modulereignisse explizit löschen |
| `read_channel(ch)` / `read_module()` | Zusammengefasste Messwerte inkl. Zeitstempel |
| `read_voltage_ramp()` / `set_voltage_ramp(value)` | Modulweite Spannungsrampe in %/s |
| `query(command)` | Einzelne rohe SCPI-Abfrage für weitere dokumentierte Werte |

`read_module()` enthält Gerätekennung, Temperatur in °C, Register und Rampe.
Zum Beispiel zusätzliche Abfragen:

```python
m = bender.positive
print(m.query(":READ:FIRMWARE:RELEASE?"))
print(m.query(":READ:VOLT:LIM?"))  # Hardware-Modullimit in Prozent
print(m.query(":READ:CURR:LIM?"))  # Hardware-Modullimit in Prozent
print(m.query(":READ:CHAN:CONTROL? (@0)"))
print(m.query(":READ:CHAN:EVENT:MASK? (@0)"))
```

Funktionen für andere Familien (etwa umschaltbare NHR-Polarität) wurden nicht
als NHS-Funktionen übernommen. Die Rohabfrage prüft Syntax/Transport, aber nicht
die fachliche Bedeutung eines selbst gewählten Befehls.

## Status, Fehler und Grenzen

- `max_voltage` ist ein ausdrücklich anzugebendes Softwarelimit (Betrag in V),
  hier passend zum geplanten Scan 100 V. Der Treiber prüft außerdem die
  Nennspannung und liest gesetzte Sollwerte zurück. Hardwarelimits bleiben wirksam.
  Bereits vorhandene höhere Sollwerte werden beim Verbinden weder verändert
  noch versteckt, beim Einschalten über den Treiber aber abgelehnt.
- Die Hardwarepolarität wird beim Öffnen an allen sechs Kanälen geprüft.
  Erwartet werden sechs Kanäle und Firmwarefamilie N06C2. Bei abweichender
  Geräteantwort wird die Verbindung geschlossen und ein Fehler gemeldet.
- Optional `positive_serial="..."` und `negative_serial="..."` am Bender angeben.
  Das bindet die Zuordnung zusätzlich an das dritte Feld der jeweiligen `*IDN?`-Antwort.
- Statusflags beschreiben den aktuellen Zustand. Ereignisflags bleiben gespeichert,
  bis sie explizit gelöscht werden. Manche Ereignisse sind normal, etwa Rampenende.
  Der Treiber löscht Fehlerhistorien nicht automatisch.
- `ChannelStatus`, `ChannelEvent`, `ModuleStatus`, `ModuleEvent` sind `IntFlag`-Typen.
  Unbekannte Bits bleiben im numerischen Register erhalten. Beispiel:

```python
from iseg_nhs import ChannelStatus
if reading.status & ChannelStatus.CURRENT_TRIP:
    print("Strom-Trip erkannt")
```

- Modulstatus enthält mehrere **Good-Bits**: Eine Null kann dort einen Fehler
  bedeuten. Deshalb ist `status != 0` kein allgemeiner Fehlertest.
- Eingabefehler werden bei normalen Schreiboperationen vorher/nachher geprüft.
  Aus- und Löschbefehle dürfen auch bei bestehendem Eingabefehler versucht werden.
- `CommunicationError`: Timeout, USB-Abbruch oder unvollständige Antwort. Der
  Ausgangszustand kann unbekannt sein. Es gibt keinen automatischen Schreib-Retry.
  Verbindung schließen/neu öffnen und Zustand abfragen. Bei verlorener USB-
  Verbindung ist auch ein Abschaltbefehl nicht garantiert ausführbar.
- `DeviceError`: Geräteidentität, Polarität oder Eingabefehler. `VerificationError`:
  zurückgelesener Sollwert passt nicht zur Anforderung.
- `PartialUpdateError`: `.completed` enthält bestätigte Kanäle, `.failed` den
  fehlgeschlagenen/unklaren Kanal. Beim Abschalten ist `.failed` ein Dictionary
  mit allen Fehlschlägen; das andere Modul wird trotzdem angesprochen.
  Kein automatisches Zurücksetzen zuvor geänderter Kanäle.
- **Vier Spannungen werden nacheinander gesetzt.** Das ist keine atomare oder
  zeitgleiche Änderung über beide COM-Ports. Vor dem ersten Schreiben werden
  alle vier Werte validiert. Messwertsammlungen sind ebenfalls sequenziell.
- `*OPC?` bestätigt die Befehlsbearbeitung, nicht das Erreichen der Istspannung.
  Das Backend muss vor dem Laserschuss alle Istspannungen und Rampen-/Fehlerflags
  mit Toleranz und Zeitlimit prüfen. Scans, Spannungsverhältnisse, Warteabläufe
  und Auswertung gehören wie besprochen ins Backend.
- NHS-Rampen sind **modulweit**: `set_voltage_ramp()` betrifft auch Kanal 2–5.
  Bei 4000 V Nennspannung entspricht 1 %/s einer Rampe von 40 V/s.
- Off-/Emergency-Off-Bestätigung beweist keine vollständig entladenen Ausgänge.
  Istspannungen separat überwachen. Andere Schutzparameter werden nicht beim
  Verbindungsaufbau umkonfiguriert.

## Transport und Tests

9600 Baud, 8N1, ASCII, CRLF; keine Hardware-/Softwareflusssteuerung. Vorhandenes
Echo wird konsumiert, ohne die Echo-Einstellung zu verändern. Jede Transaktion
ist durch einen Lock geschützt. Die NHS-Puffergrenzen werden berücksichtigt.

```powershell
python -m unittest -v test_iseg_nhs.py
```

19 Offline-Tests decken Echo an/aus, signed mapping, ungültige Werte,
Geräteverwechslung, Timeout, beschädigte Antworten, partielle Writes,
Geräteablehnung, Sollwertabweichung, Teilfehler über beide Module und Aufräumen ab.
Diese Tests bestätigen Softwareverhalten anhand eines simulierten Protokolls.
Sie ersetzen nicht den Verbindungstest und einen kontrollierten Schreibtest an
den tatsächlichen Modulen mit ihrer Firmware.

## Dokumentationsgrundlage

- NHS Hardwaremanual, 20.12.2022, insbesondere Abschnitt 5.4/5.5:
  https://iseg-hv.com/download/SYSTEM/NHS/iseg_manual_NHS_en.pdf
- SCPI Programmers Guide, Stand 23.10.2026; Kapitel 2–4, 6 und 9:
  https://iseg-hv.com/download/SOFTWARE/isegSCPI/SCPI_Programmers_Guide_en.pdf
