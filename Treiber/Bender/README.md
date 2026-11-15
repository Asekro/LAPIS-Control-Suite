# NHS-Treiber für den LAPIS-Bender

Python 3.10 oder neuer. Zwei iseg NHS, positiv und negativ, jeweils Kanal 0 und 1.
Stand 15.11.2026: 26 Offline-Tests bestanden. Verbindung, Modul-/Kanalabfragen,
zehn Messzyklen, Sollwertsetzen auf ±10 V sowie Ein- und Ausschalten wurden mit
den angeschlossenen N06C2-Modulen mit Firmwareversion 2.20 im Notebook geprüft.
Das ist ein Funktionstest, keine vollständige Prüfung aller Funktionen oder
der Spannungsgenauigkeit über den gesamten Arbeitsbereich.

## Installation und erster Test

Terminal in diesem Ordner öffnen und die Treiberabhängigkeit installieren:

```powershell
python -m pip install -r requirements.txt
```

Für manuelle Hardwaretests `test_bender.ipynb` mit einem Python-Jupyter-Kernel
öffnen. Nach Treiberänderungen den Kernel neu starten. Die Konfigurationszelle
und Punkte 1–4 ausführen; 1a/1b und 2a sind optionale Diagnoseabschnitte.
Schreib- und Einschaltfreigaben sind standardmäßig `False`. Sie für die
optionalen Punkte 5/6 ausdrücklich aktivieren. Der Treiber benötigt nur pyserial;
zum Ausführen des Notebooks zusätzlich eine Jupyter-Umgebung mit IPython.

Aktuelle Zuordnung am Testaufbau:

| Modul | COM-Port | USB-Seriennummer | Geräte-Seriennummer aus `*IDN?` |
|---|---|---|---|
| Positiv | COM12 | AI05M8IUA | 8000311 |
| Negativ | COM11 | A600C0IIA | 8000312 |

COM-Ports können nach dem Umstecken wechseln. Die Treiberparameter
`positive_serial` und `negative_serial` erwarten Geräte-Seriennummern, keine USB-IDs.
Die normalen Notebook-Tests verwenden die konfigurierten Ports und prüfen die
Polarität; die zusätzlichen Diagnosezellen finden die Ports über ihre USB-IDs.

Alternativ ohne Jupyter rein lesend testen:

```powershell
python test_verbindung.py --positive COM12 --negative COM11
```

Dieser Test verändert keine Sollwerte und schaltet keinen Ausgang.
Falls erforderlich, FTDI-VCP-Treiber installieren.
Hersteller-GUI vor dem Test schließen, damit sie den COM-Port nicht belegt.
Bei Problemen mit `--debug` erneut ausführen; das protokolliert Befehle und Antworten.

## Verwendung in VS Code / Python

`iseg_nhs.py` neben dein Skript legen. Anschlussparameter stehen beim Erzeugen
**eines** Bender-Objekts:

```python
from iseg_nhs import Bender

with Bender("COM12", "COM11", max_voltage=100,
            positive_serial="8000311", negative_serial="8000312") as bender:
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
Gerätevorzeichen unverändert. Die Messspannungen werden mit `sign * abs(raw)`
auf die geprüfte Modulpolarität normiert. Bei Messwerten nahe null bleibt
das rohe Vorzeichen daher nicht erhalten; die Diagnosezelle 2a zeigt Rohantworten.

Beim ±10-V-Test wurden am positiven Modul etwa 8,7 V und am negativen Modul
etwa −10,05 V gemeldet. Laut Einschätzung des Anwenders könnte dies mit dem
unteren zuverlässigen Arbeitsbereich ab etwa 40 V zusammenhängen. Die Ursache
und die erreichbare Genauigkeit darunter sind noch nicht quantifiziert.
Da kleinere Spannungen benötigt werden, führt der Treiber keine 40-V-Untergrenze
ein und verändert die angeforderten Sollwerte nicht durch eine Kompensation.

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

26 Offline-Tests decken Echo an/aus, signed mapping, ungültige Werte,
Geräteverwechslung, Timeout, beschädigte Antworten, partielle Writes,
Geräteablehnung, Sollwertabweichung, Teilfehler über beide Module und Aufräumen ab.
Diese Tests bestätigen Softwareverhalten anhand eines simulierten Protokolls.
Sie ersetzen nicht den Verbindungstest und einen kontrollierten Schreibtest an
den tatsächlichen Modulen mit ihrer Firmware.

| Datei | Zweck | Einordnung |
|---|---|---|
| `test_bender.ipynb` | Interaktive Hardwaretests und Messreihen | Bevorzugter manueller Einstieg |
| `test_iseg_nhs.py` | Automatische Tests mit simulierten Geräten, einschließlich Fehlerfällen und Windows-Regression | Behalten; wird auch von Notebook-Punkt 8 importiert |
| `test_verbindung.py` | Kurzer lesender Verbindungstest ohne Jupyter | Optional; funktional durch das Notebook abgedeckt |
| `test_usb_dotnet.ps1` | Unabhängiger Windows/.NET-Vergleich | Diagnosehilfe; wird von Notebook-Punkt 1b verwendet |

Das Notebook ersetzt die Offline-Tests nicht: Kommunikationsabbrüche,
Teilfehler und ungültige Eingaben lassen sich dort ohne reale Ausgangsänderungen
reproduzierbar prüfen. `test_verbindung.py` kann entfallen, wenn der Einstieg
über die Kommandozeile nicht benötigt und seine Dokumentation angepasst wird.
Das PowerShell-Skript nur zusammen mit dem zugehörigen Notebook-Abschnitt entfernen.

## Bekannte Firmwarebesonderheiten und Windows-Korrektur

### Korrektur des Windows-Verbindungsfehlers

Im installierten pyserial-Windows-Backend ruft jede Zuweisung von `serial.timeout`
die vollständige Portkonfiguration einschließlich `SetCommState` auf. Der Treiber
änderte diese Eigenschaft während des laufenden Echo-/Antwortempfangs. Diese
Zuweisungen wurden entfernt; Timeout und Leitungsparameter bleiben während
einer Transaktion unverändert. Die Zeitgrenze wird zwischen Antwortzeilen geprüft;
ein einzelner blockierender Leseaufruf kann noch bis zu seinem festen Timeout warten.

Der unabhängige Windows/.NET-Lesetest bestand 30 Abfragen pro Modul ohne Fehler.
Nach der Korrektur bestand auch der NHS-Treiber zehn vollständige Lesezyklen mit
beiden Modulstatusabfragen und allen vier Bender-Kanälen im Blockmodus, ohne
zusätzliche Pausen, Wiederholungen oder geänderte Echo-Einstellungen. Alle 26
Offline-Tests bestehen, einschließlich einer Regression gegen Timeout-Zuweisungen
auf einem bereits geöffneten Port. Sollwerte und Ausgänge blieben unverändert.

Das Notebook enthält den Windows-Vergleich unter Punkt 1b; dafür ist kein Download
des Herstellerprogramms nötig. Frühere gespeicherte Fehlerausgaben sind historisch.
Nach dem Laden des geänderten Notebooks den Kernel neu starten.

### Erkenntnisse aus dem Verbindungstest mit Firmware 2.20

COM12 (positiv, Geräte-Seriennummer `8000311`) liefert `+4000 V` als
Nennspannung, COM11 (negativ, Geräte-Seriennummer `8000312`) liefert `-4000 V`.
Beide Module melden Statusbit 0 als null. Bei Firmwareversion `2.20` prüft der
Treiber deshalb die Polarität anhand des Vorzeichens von `:READ:VOLT:NOM?`
für jeden Kanal. Null oder ein unerwartetes Vorzeichen führen weiterhin zum
Abbruch. Für andere Firmwareversionen bleibt die Prüfung von Statusbit 0 bestehen.
Das ist eine Anpassung an die beobachteten Geräteantworten, keine allgemeine
Aussage zu allen NHS-Firmwareversionen.

Optional kann
`Bender(..., echo_handshake=True)` jedes Zeichen einschließlich CR/LF einzeln
senden und vor dem nächsten Zeichen dessen exaktes Echo prüfen. Dafür muss
das Geräteecho eingeschaltet sein; der Treiber verändert dessen Einstellung nicht.
Das Notebook und der Treiber verwenden standardmäßig Blockversand. Bei fehlendem
oder falschem Echo wird abgebrochen, die Verbindung als unsynchronisiert
markiert und kein Befehl automatisch wiederholt. Der Byte-Handshake war keine
Lösung für den damaligen Timeout-/Rekonfigurationsfehler und ist für den
aktuellen Aufbau nicht erforderlich.

Nach Änderungen an `iseg_nhs.py` den Notebook-Kernel neu starten, damit der
aktualisierte Treiber geladen wird. Die 26 Offline-Tests prüfen unter anderem
die vorzeichenbehaftete Nennspannung und den zeichenweisen Echo-Handshake.

Der abschließende Notebook-Lauf am Hub bestand fünf kurze Kommunikationsrunden
pro Modul, den .NET-Vergleich mit je 30 Abfragen, die vollständigen Diagnose-
und Kanalabfragen sowie zehn Messzyklen ohne Fehler. Die davor beobachteten
beschädigten Echos wurden nach Entfernung der Timeout-Zuweisungen nicht mehr
reproduziert. Ein Hub- oder Kabeldefekt ist durch die Tests nicht belegt.

N06C2/2.20 liefert die Rampenrate als `1.000%` statt `%/s`. Der Treiber akzeptiert
für diese einzelne Rampenabfrage beide Einheitenschreibweisen; bei anderen
Größen bleiben die Einheitenprüfungen unverändert. Ereignisregister werden bei
den Lesetests nicht gelöscht, Ausgänge und Sollwerte nicht verändert.

## Dokumentationsgrundlage

- NHS Hardwaremanual, 20.12.2022, insbesondere Abschnitt 5.4/5.5:
  https://iseg-hv.com/download/SYSTEM/NHS/iseg_manual_NHS_en.pdf
- SCPI Programmers Guide, Stand 23.10.2026; Kapitel 2–4, 6 und 9:
  https://iseg-hv.com/download/SOFTWARE/isegSCPI/SCPI_Programmers_Guide_en.pdf
