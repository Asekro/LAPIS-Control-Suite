# NHS-Treiber für den LAPIS-Bender

Python 3.10 oder neuer. Zwei iseg NHS, positiv und negativ, jeweils Kanal 0 und 1.
Stand 15.11.2026: 26 Offline-Tests bestanden. Verbindung, Modul-/Kanalabfragen,
zehn Messzyklen, Sollwertsetzen auf ±10 V sowie Ein- und Ausschalten wurden mit
den angeschlossenen N06C2-Modulen mit Firmwareversion 2.20 im Notebook geprüft.
Das ist ein Funktionstest, keine vollständige Prüfung aller Funktionen oder
der Spannungsgenauigkeit über den gesamten Arbeitsbereich.
Die Versuche vom 26.11.2026 zur Regelung von p0, zum Halten aller vier
Elektroden und zum Festwertvergleich auf n0 sind in den Notebook-Abschnitten
6a bis 6c protokolliert.

## Installation und erster Test

Terminal in diesem Ordner öffnen und die Treiberabhängigkeit installieren:

```powershell
python -m pip install -r requirements.txt
```

Für manuelle Hardwaretests `test_bender.ipynb` mit einem Python-Jupyter-Kernel
öffnen. Nach Treiberänderungen den Kernel neu starten. Die Konfigurationszelle
und Punkte 1–4 ausführen; 1a/1b und 2a sind optionale Diagnoseabschnitte.
Schreib- und Einschaltfreigaben sind standardmäßig `False`. Sie für die
gewünschten Abschnitte 5 bis 6c ausdrücklich aktivieren. Der Treiber benötigt nur pyserial;
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

Offline-Tests aus diesem Geräteordner: `python -m unittest discover -s tests -v`.

Falls erforderlich, FTDI-VCP-Treiber installieren.
Hersteller-GUI vor dem Test schließen, damit sie den COM-Port nicht belegt.
Die Diagnoseabschnitte im Notebook zeigen bei Bedarf Befehle und Antworten.

## Verwendung in VS Code / Python

Für Paketimporte das Projekt einmal im Repository-Root mit `python -m pip install -e .` installieren. Anschlussparameter stehen beim Erzeugen
**eines** Bender-Objekts:

```python
from lapis_control.devices.bender.iseg_nhs import Bender

with Bender("COM12", "COM11", max_voltage=100,
            positive_serial="8000311", negative_serial="8000312") as bender:
    print(bender.positive.identification)
    print(bender.negative.identification)
    print(bender.read_all())
```

Der Konstruktor selbst öffnet keine Verbindung. Alternativ `bender.connect()` und
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

### Multimetervergleich

Das NHS liefert mit `:READ:VOLT?` den programmierten Sollwert (VSET).
`NHSModule.read_voltage()` liest mit `:MEAS:VOLT?` die intern gemessene Spannung (VMEAS).
Bei der Gegenmessung am LAPIS-Aufbau am **28.11.2026** stimmte VMEAS bei den
geprüften Spannungen gut mit der Anzeige des Multimeters überein. Das Ergebnis
ist als qualitativer Vergleich dokumentiert.

iseg spezifiziert die Genauigkeit von VMEAS für Ausgangsbeträge **über 1 % der
Nennspannung** bis unterhalb der Nennspannung. Bei den 4-kV-Modulen sind 15 V
nur 0,375 %. Für die absolute Genauigkeit bei 15 V gibt dieses Datenblatt
keine Fehlergrenze an. [NHS-Handbuch, Kap. 4, S. 11](https://iseg-hv.com/files/media/docitems_file_20221221154739.pdf#page=11)

Am Display zeigt **VI-MEAS** die intern gemessene Spannung und den gemessenen
Strom des Kanals. **V-MS** zeigt dieselbe Messspannung zusammen mit dem
programmierten Spannungssollwert. [NHS-Handbuch, Abschn. 5.2.1, S. 15](https://iseg-hv.com/files/media/docitems_file_20221221154739.pdf#page=15)

### Nachregelung für eine Elektrode

Beim früheren ±10-V-Test meldete p0 bei 10,00 V Sollwert 8,72 V und n0 bei
−10,00 V Sollwert −10,05 V.

Notebook-Abschnitt **6a** enthält den vollständigen Versuch für p0. Nach der
Konfigurationszelle genügt dieser Abschnitt. Die separate Freigabe
`ALLOW_P0_TEST` steht standardmäßig auf `False`. Für den Einkanalversuch
bleiben `ALLOW_WRITES` und `ALLOW_HV_ON` auf `False`.

`module.regulate_voltage(...)` liefert pro Abfrage einen Zeitstempel, VSET,
VMEAS, den Messstrom in Ampere, den Kanalstatus und einen gegebenenfalls neu
angeforderten Spannungssollwert.
Erst die Iteration startet die Rückkopplung auf einem bereits eingeschalteten
Kanal. Nach Erreichen des Toleranzbands läuft die Messung weiter und korrigiert
spätere Drift. Der Aufrufer bestimmt Laufzeit und Abschaltung. Beispiel für
den bereits eingeschalteten p0 mit einem anfänglichen Sollwert von +15 V:

```python
feedback = module.regulate_voltage(0, 15.0, tolerance_v=0.2, max_step_v=1.0,
                                   setpoint_range_v=(10.0, 100.0), interval_s=5.0)
try:
    for sample in feedback:
        print(sample.voltage_set, sample.voltage, sample.requested_voltage)
finally:
    module.disable(0)
```

Zwischen zwei Abfragen und vor der ersten wartet der Regler hier fünf Sekunden.
Bei laufender Rampe und innerhalb des Toleranzbands schreibt er keinen Sollwert.
Sonst korrigiert er um die halbe vorzeichenbehaftete Abweichung, begrenzt auf
1 V je Schritt. Im Beispiel liegen alle Stellwerte zwischen 10 und 100 V.
Ein kleineres konfiguriertes Anwendungslimit hat Vorrang. Ein
interner Messwert mit einem Betrag über der wirksamen Obergrenze
bricht die Iteration bei der nächsten Abfrage vor einem weiteren Stellbefehl ab.
Polarität, Anwendungs- und Nennspannungslimit prüft der Treiber weiterhin.
Bei ausgeschaltetem Kanal, Gerätefehler, Stromregelung nach Rampenende,
fremder Sollwertänderung oder ausgeschöpftem Stellbereich bricht die Iteration
mit einer Exception ab. Dasselbe gilt bei ausbleibendem Einregeln (standardmäßig
40 s). Das Beenden der Iteration lässt den letzten Sollwert am Kanal stehen.
Abschnitt 6a übernimmt das Abfahren und prüft den Aus-Zustand.

Die eingebaute Funktion *Fine Adjustment* gleicht Drift anhand der internen
Messung modulweit aus. Der Einkanalversuch verwendet ausschließlich den
Sollwert von p0 als Stellgröße. [iseg SCPI Guide, Abschnitte 1.3.2 und 9.7](https://iseg-hv.com/download/SOFTWARE/isegSCPI/SCPI_Programmers_Guide_en.pdf#page=12)

**Erster Hardwareversuch am 26.11.2026:** Bei 15,00 V Sollwert meldete p0
zunächst 13,79 V. Nach der ersten Korrektur wurden 15,60 V als Sollwert
zurückgelesen. Das NHS meldete dazu 30,03 V Messspannung. Auch bei schrittweise
sinkendem Sollwert blieb die Anzeige erhöht (24,96 V bei 10,60 V Sollwert).
Der ursprüngliche Regler brach nach Ablauf seines 40-s-Zeitlimits ab. Beim
Abfahren meldete p0 0,67 V, die spätere Kontrollabfrage ergab Kanal aus,
10,00 V ursprünglichen Sollwert und 0,05 V interne Messspannung.
Dieser Lauf hatte eine Stellgrenze von 20 V. Im späteren, in Abschnitt 6a
gespeicherten Lauf mit 100-V-Grenze lagen die letzten fünf internen Messwerte
zwischen 15,12 und 15,13 V bei 15,12 V Stellwert (43,4 bis 64,1 s).
Für den Spannungssprung am 26.11.2026 liegt nur der interne NHS-Messwert vor.
Seine Ursache ist weiterhin offen.

Für eine quantitative Prüfung direkt an der Elektrode den freigegebenen Messpunkt gegen das
zugehörige Bezugspotential unter Betriebslast mit einem unabhängigen, für den
HV-Aufbau geeigneten Messsystem messen. Dessen Auflösung und Messunsicherheit
bei 15 V müssen zur gewünschten Toleranz passen. VSET, VMEAS und den zeitgleichen
externen Wert nach dem Einschwingen festhalten. Die Differenz zwischen
externem Messwert und VMEAS zeigt, welche Abweichung die Software beim Regeln
auf die interne Anzeige nicht erfassen kann.

### Vier Bender-Elektroden einregeln und halten

Notebook-Abschnitt **6b** startet mit 15 V Zielbetrag und 120 s Laufzeit.
Nach einem Kernel-Neustart die Konfigurationszelle mit
`ALLOW_FOUR_CHANNEL_TEST = True` und danach ausschließlich 6b ausführen.
Die Vorzeichen folgen der Zuordnung p0/p1 positiv und n0/n1 negativ. Der
Versuch prüft den Aus-Zustand aller vier Kanäle, setzt ihre Sollwerte und
schaltet sie nacheinander ein. Bei einem anderen Ziel genügt es,
`TARGET_MAGNITUDE_V` zu ändern. Für den positiven Zielbetrag `M` berechnet
`hold_bender_voltage` den Bereich der zulässigen Stellbeträge als
`0 … min(2 × M, Limit)`. Dabei ist `Limit` der kleinste Wert aus dem
Versuchslimit von 100 V und den konfigurierten `max_voltage`-Werten beider
Module. Bei 10 V Ziel und 100 V Limit sind 0 bis 20 V erlaubt, bei 15 V Ziel
0 bis 30 V. Der doppelte Zielbetrag ist die gewählte Stellreserve. Überschreitet
eine interne Messung das absolute Limit, bricht der Ablauf bei dieser Abfrage ab.

Jeder Kanal hat seinen eigenen nächsten Prüftermin. Nach dem Einschalten
des letzten Kanals lässt der Ablauf die Module zunächst 20 s bei festem
Sollwert einschwingen (`INITIAL_SETTLE_S`). In dieser Zeit prüft er
weiterhin alle 5 s Messwerte, Sollwerte und Gerätestatus. Erst danach darf
er eine anhaltende Abweichung korrigieren. Drei Messungen in Folge innerhalb
von ±0,2 V nach Einschwingzeit und Rampenende qualifizieren den Kanal für den 30-s-Haltetakt. Bei
erkennbarer Drift korrigiert der Regler sofort um die Hälfte der Abweichung
(höchstens 1 V) und prüft den Kanal anschließend wieder nach 5 s. Eine aktive
Rampe wird abgewartet. Die Frist von 90 s für andauernde Abweichungen oder Rampen
beginnt frühestens bei der ersten Regelmessung nach der Einschwingzeit. Die Notebook-Ausgabe zählt Messungen und
Korrekturen je Kanal und zeigt, ob am Ende alle vier zuletzt stabil waren.
Im gespeicherten 120-s-Lauf bei ±15 V erreichten alle vier Kanäle den Haltetakt.
Bei acht internen Messungen pro Kanal war keine Stellkorrektur nötig. Der
Ablauf bestätigte anschließend für alle vier Kanäle den Aus-Zustand und eine
interne Restspannung unter 1 V.

Für die spätere Software liegt der Ablauf in
`lapis_control.workflows.bender_voltage.hold_bender_voltage`. Er wartet
zwischen den Prüfterminen und führt die seriellen Transaktionen in einem
Ablauf aus. Bei vier stabilen Kanälen ergeben sich im Mittel rund acht
Kanalprüfungen pro Minute. Eine solche Prüfung umfasst fünf Leseabfragen
(VSET, VMEAS, Messstrom, Modul- und Kanalstatus). Die GUI zeigt den Messstrom
jedes Kanals im jeweiligen Prüf- oder Haltetakt in µA an. Im Zielband fallen keine Stellbefehle
an. Eine Drift kurz nach einer Prüfung kann ungefähr 30 s zuzüglich laufender
Kommunikation unentdeckt bleiben. Die Abstände in der Testzelle lassen sich
an die beobachtete Drift und die gewünschte Reaktionszeit anpassen.

Die Desktop-Anwendung kann alternativ vier vorzeichenbehaftete Zielwerte als
Mapping übergeben. Mit `allow_active=True` übernimmt der Ablauf bereits aktive
Kanäle nach Zustands- und Sollwertprüfung. Ein `threading.Event` über
`stop_event` weckt den Regler aus seiner Wartezeit und leitet das Abfahren ein.
Die Einzelversuche im Notebook verwenden weiterhin den gemeinsamen Zielbetrag.

Während der Regelung übernimmt **Regelziele übernehmen** die vier aktuell
eingegebenen Ziele. Der Regler prüft die Änderungen, schreibt die neuen
Spannungssollwerte für die betroffenen Elektroden und liest sie zur Bestätigung
zurück. Für diese Kanäle beginnen Einschwingzeit und schneller Prüftakt erneut.
Die maximale Stellschrittweite gilt für die anschließenden automatischen
Korrekturen. Die Bender-GUI liest Messstrom und Stromlimit ausschließlich aus.

Der Aufrufer übergibt die geöffnete Verbindung und besitzt während des Tests
den Schreibzugriff auf die vier Kanäle. `duration_s=None` erlaubt fortlaufenden
Betrieb. Normales Ende, Abbruch und Ausnahmen lösen das Abfahren aus. Nach
bestätigtem Aus-Zustand, Rampenende und intern weniger als 1 V stellt der
Ablauf die vorherigen zulässigen Sollwerte wieder her. Bei einem USB-Ausfall
kann die Abschaltung betroffener Kanäle unbestätigt bleiben.

**Festwertvergleich auf n0 (Notebook 6c, 26.11.2026, 19:04 Uhr):** Im
Vierkanalversuch hatte n0 bei −15,00 V Stellwert erst −14,95 V und dann
−29,98 V gemeldet. Beim anschließenden Einzelversuch wurden bei festem
−15,00-V-Sollwert 31 interne Messungen über 60 s aufgezeichnet. Die erste
Messung ergab −15,82 V. Zwischen 30 und 60 s lagen alle 16 Werte bei
−15,02 bis −14,98 V. Die anschließende Regelung meldete −14,99, −14,99
und −15,02 V und benötigte keinen Stellschritt. Beim Abschluss meldete n0
Status `0x0000`, −0,04 V Restspannung und den ursprünglichen Sollwert von
−10,00 V. Alle Spannungsangaben dieses Versuchs stammen aus der internen NHS-Messung.

## Funktionen

| Methode am NHSModule | Bedeutung |
|---|---|
| `set_voltage(ch, volts)` | Sollspannung setzen und Sollwert zurücklesen |
| `regulate_voltage(ch, target_v, ...)` | Einen aktiven Kanal fortlaufend auf seine intern gemessene Spannung nachregeln (Iterator) |
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
from lapis_control.devices.bender.iseg_nhs import ChannelStatus
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
- Strg+C während einer laufenden seriellen Transaktion sperrt die betroffene
  Verbindung für weitere Befehle. Nach dem Neuverbinden den Kanalzustand prüfen.
  Bei einer Unterbrechung zwischen Transaktionen kann der Vierkanalablauf die
  bestehenden Verbindungen zum Abfahren nutzen.
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
python -m unittest discover -s tests -v
```

Die Treibertests prüfen unter anderem Echo an/aus,
Polaritätszuordnung, Geräteverwechslung, beschädigte Antworten und Teilfehler
über beide Module. Zur Nachregelung prüfen sie auch spätere Drift,
Stellgrenzen, Messungen auf der Toleranzgrenze, die gemessene Überschreitung
aus dem p0-Versuch und Abbrüche bei Rampen-Timeout oder Kommunikationsverlust.
Die Vierkanaltests mit simulierten NHS-Modulen lassen sich im Repository-Root
mit `python -m unittest tests.test_bender_voltage -v` ausführen.
Diese Tests bestätigen Softwareverhalten anhand eines simulierten Protokolls.
Sie ersetzen nicht den Verbindungstest und einen kontrollierten Schreibtest an
den tatsächlichen Modulen mit ihrer Firmware.

| Datei | Zweck | Einordnung |
|---|---|---|
| `test_bender.ipynb` | Interaktive Hardwaretests und Messreihen | Bevorzugter manueller Einstieg |
| `tests/test_iseg_nhs.py` | Automatische Tests mit simulierten Geräten, einschließlich Fehlerfällen und Windows-Regression | Behalten; wird auch von Notebook-Punkt 8 importiert |
| `tests/test_usb_dotnet.ps1` | Unabhängiger Windows/.NET-Vergleich | Diagnosehilfe; wird von Notebook-Punkt 1b verwendet |

Das Notebook ersetzt die Offline-Tests nicht: Kommunikationsabbrüche,
Teilfehler und ungültige Eingaben lassen sich dort ohne reale Ausgangsänderungen
reproduzierbar prüfen. Der manuelle Einstieg erfolgt über das Notebook.
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
zusätzliche Pausen, Wiederholungen oder geänderte Echo-Einstellungen. Die damals 26
Offline-Tests bestanden, einschließlich einer Regression gegen Timeout-Zuweisungen
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
aktualisierte Treiber geladen wird. Die Treibertests prüfen unter anderem
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

- NHS Hardwaremanual, 20.12.2022, insbesondere Kapitel 4 und Abschnitt 5.4/5.5:
  https://iseg-hv.com/files/media/docitems_file_20221221154739.pdf
- SCPI Programmers Guide, Stand 23.10.2026; Kapitel 2–4, 6 und 9:
  https://iseg-hv.com/download/SOFTWARE/isegSCPI/SCPI_Programmers_Guide_en.pdf
