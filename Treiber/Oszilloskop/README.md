# DSOX1204G-Treiber für LAPIS

Version 0.1 · 15.11.2026 · Python ab 3.10

Für das **Keysight DSOX1204G** aus dem Chat „Treiberentwicklung Oszilloskop Manuals“:
USB/PyVISA, Detektordaten auf frei wählbaren Kanälen CH1–CH4, OptoSync als
Flankentrigger auf einem dieser Kanäle und WaveGen zur Laseransteuerung.
Auch eine in Connection Expert ermittelte LAN-VISA-Adresse kann übergeben werden.
Der DSOX4054A aus dem anderen Aufbau wird von dieser Klasse bewusst abgewiesen.

**Status: mit simulierten Antworten geprüft, noch nicht am realen Gerät getestet.**
Die unbekannte Eingangsimpedanz des Laser-TTL-Eingangs wird nicht geraten.
Die Einzelpuls-Annäherung über Ein/Aus ist experimentell und separat implementiert.

## Dateien

| Datei | Zweck |
|---|---|
| `keysight_dsox1204g.py` | Gerätetreiber; eine Hauptklasse, ein Datenbehälter, zwei Fehlertypen |
| `test_scope.py` | Hardwaretest: erst Status, optional vorhandene/neue Aufnahme speichern und plotten |
| `beispiel_wavegen.py` | Expliziter Test für TTL-Konfiguration, Pulsfolge oder Ein/Aus-Versuch |
| `lapis_workflows.py` | Experimenteller Ablauf; getrennt vom Gerätetreiber |
| `tests/test_driver.py` | Offline-Tests mit simulierten Geräteantworten |
| `requirements.txt` | PyVISA und NumPy |
| `requirements_plot.txt` | Zusätzlich Matplotlib für den optionalen Plot |
| `VALIDIERUNG.md` | Geprüfte Fälle und noch offene Hardwaretests |

## 1. Installation unter Windows / VS Code

1. [Keysight IO Libraries Suite](https://www.keysight.com/zz/en/lib/software-detail/computer-software/io-libraries-suite-downloads-2175637.html) installieren, falls noch keine funktionierende VISA-Installation vorhanden ist.
2. Oszilloskop über seinen USB-Geräteanschluss mit dem PC verbinden.
3. In Keysight Connection Expert prüfen, ob das Gerät erkannt wird. Die Antwort auf `*IDN?` muss `DSOX1204G` enthalten.
4. ZIP entpacken, den enthaltenen Ordner in VS Code öffnen und im Terminal ausführen:

```powershell
python -m pip install -r requirements.txt
python test_scope.py --list
```

In VS Code denselben Python-Interpreter auswählen, in dem die Pakete installiert wurden.
Für Plotting zusätzlich `python -m pip install -r requirements_plot.txt` ausführen.
PySerial ist hier nicht nötig: USBTMC wird über VISA angesprochen, nicht über einen COM-Port.
Mit bestehender VISA-Installation muss nicht zusätzlich ein zweiter VISA-Anbieter installiert werden.

## 2. Erster Test: nur lesen

Die genaue Adresse aus `--list` oder Connection Expert übernehmen. `USB0::...::INSTR`
ist hier ein Platzhalter, keine verwendbare Geräteadresse.

```powershell
python test_scope.py --resource "USB0::...::INSTR"
```

Das Skript liest Kennung und Einstellungen. Es startet weder Aufnahme noch WaveGen,
führt keinen Reset aus und ändert keine Kanalskalen. Ein bereits am Gerät eingeschalteter
Generator bleibt bei diesem reinen Lesetest unverändert.

## 3. Vorhandene Aufnahme herunterladen

CH1 und CH2 vor der Aufnahme am Gerät einschalten. Anschließend:

```powershell
python test_scope.py --resource "USB0::...::INSTR" --read --channels 1 2 --plot --output messung_001.npz
```

`--read` stoppt die Aufnahme und liest beide Kanäle aus dem eingefrorenen Speicher.
Das Ergebnis enthält Zeitachsen in Sekunden, Spannungen in Volt, Rohcodes und
Markierungen für fehlende bzw. abgeschnittene Werte. Zusätzlich wird eine JSON-Datei
mit Einstellungen und Preambles geschrieben. Vorhandene Dateien werden nicht überschrieben.
Die NPZ-Datei lässt sich später mit `np.load("messung_001.npz")` öffnen.
Ohne `--plot` ist Matplotlib nicht erforderlich.

## 4. Neue Aufnahme mit externem/manuellem Schuss

Triggerquelle, Triggerpegel, Zeitbasis und Vertikalskalen zunächst passend am Gerät
wählen. Dann:

```powershell
python test_scope.py --resource "USB0::...::INSTR" --capture --channels 1 2 --timeout 30 --output messung_002.npz
```

Das Skript aktiviert diese Kanäle, wählt MAIN/RTIME/NORMAL, stellt Single scharf und
meldet die Bereitschaft. Nun manuell/extern einen Schuss auslösen. Der WaveGen wird
in diesem Testskript nicht eingeschaltet. Bei fehlendem Trigger folgt ein Timeout,
keine Rückgabe einer alten Kurve als neue Messung.

Für deinen Python-Workflow, mit beispielhaften Pegeln und Skalen:

```python
from keysight_dsox1204g import KeysightDSOX1204G

RESOURCE = "USB0::...::INSTR"  # eigene Adresse einsetzen

with KeysightDSOX1204G(RESOURCE) as scope:
    # Beispiel: OptoSync -> CH1, Detektor -> CH2. Auf reale Signale anpassen.
    scope.configure_channel(1, enabled=True, probe=1, coupling="DC", scale_v=1.0)
    scope.configure_channel(2, enabled=True, probe=1, coupling="DC", scale_v=0.1)
    scope.configure_timebase(scale_s=10e-6, position_s=20e-6)
    scope.configure_edge_trigger(1, level_v=1.0, slope="POSitive")
    scope.configure_acquisition("NORMAL")

    scope.arm_single()
    scope.wait_until_armed()
    # Hier löst die übergeordnete Messlogik den externen Schuss aus.
    scope.wait_for_single(timeout_s=30)
    curves = scope.read_waveforms((1, 2))
    t = curves[2].time_s
    detector = curves[2].voltage_v
```

`arm_single()` bedeutet **eine Oszilloskop-Aufnahme**, nicht einen Laser-Einzelpuls.
Der Sampleabstand wird aus der Preamble übernommen; `scale_s` gibt Sekunden pro
Bildschirmteilung an und stellt nicht direkt die Abtastrate ein.
Die Zeitachse folgt der Gerätezeitreferenz. OptoSync-/Kabel-/Detektorverzögerungen
werden hier nicht als physikalisches TOF-t0 korrigiert; das gehört in die Auswertung.

## 5. TTL mit WaveGen

`configure_ttl()` verlangt ausdrücklich `load="HIGHZ"` oder `load="50OHM"`.
Diese Auswahl beschreibt die **angeschlossene Last**, nicht einen umschaltbaren
Innenwiderstand. Falsche Lastangaben können die realen Pegel verfälschen.
Da die Laser-Eingangsimpedanz noch unbekannt ist, zunächst klären und die tatsächliche
Spannung am vorgesehenen Anschluss messen. Bei einem ersten elektrischen Test
WaveGen an einen Oszilloskopkanal anschließen, bevor er den Laser ansteuert.

Beispiel **für eine bestätigte hochohmige Last**:

```python
with KeysightDSOX1204G(RESOURCE) as scope:
    actual = scope.configure_ttl(
        load="HIGHZ", frequency_hz=1.0, pulse_width_s=100e-6,
        low_v=0.0, high_v=5.0,
    )
    print(actual)                 # Konfiguration fertig, Ausgang weiterhin AUS
    try:
        scope.wavegen_output(True)   # Startet eine PERIODISCHE Pulsfolge!
        # Messablauf / Wartezeit im Backend
    finally:
        scope.wavegen_output(False)
```

Die Beispiele 1 Hz und 100 µs sind konfigurierbare Startwerte. Die Kombination
0 V/5 V entspricht 5 Vss mit +2,5 V Offset. Der Treiber prüft zurückgelesene Einstellungen;
das ersetzt keine Messung am tatsächlichen Laser-Eingang. Gerätebegrenzungen werden
als SCPI- oder Rücklesefehler sichtbar, nicht stillschweigend akzeptiert.

Über das Terminal, ebenfalls nur bei bestätigter hochohmiger Last:

```powershell
# Nur konfigurieren, Ausgang bleibt aus:
python beispiel_wavegen.py --resource "USB0::...::INSTR" --load HIGHZ

# Explizit drei Sekunden periodische Pulse ausgeben:
python beispiel_wavegen.py --resource "USB0::...::INSTR" --load HIGHZ --mode continuous --seconds 3
```

## 6. Dein Ein/Aus-Trick

```python
from lapis_workflows import experimental_wavegen_gate

# Nach erfolgreichem configure_ttl(...), Generator zunächst aus:
experimental_wavegen_gate(scope, on_time_s=0.2)
```

Oder über `beispiel_wavegen.py ... --load HIGHZ --mode gate`.

Die Funktion schaltet die 1-Hz-Pulsfolge kurz ein und danach wieder aus. Sie versucht
auch bei einem Python-Abbruch abzuschalten. **Sie garantiert weder genau einen Puls
noch eine bestimmte reale Einschaltdauer.** Die Phase beim Einschalten, Schaltflanken,
USB-Kommunikation und Betriebssystemplanung sind nicht deterministisch. Es sind
null, ein oder mehrere Pulse möglich. `on_time_s` ist nur eine Software-Wartezeit.
Auch ein erkannter OptoSync-Trigger würde allein nicht beweisen, dass kein zweiter
Puls folgt. Vor der Nutzung am Laser mehrere Ein/Aus-Versuche elektrisch aufzeichnen.
Ein garantierter Einzelpuls erfordert eine entsprechende Hardwarefunktion.

Bei Verbindungsverlust oder hartem Prozessabbruch kann ein eingeschalteter Ausgang
weiterlaufen. `close()` und Fehlerbehandlung sind Abschaltversuche, kein Hardware-Watchdog.

## 7. Weitere Funktionen und Grenzen

| Ziel | Aufruf |
|---|---|
| Laufende Anzeige / stoppen | `run()` / `stop()` |
| Kanäle, Probe, Offset, Kopplung, Invertierung, Bandbreitenlimit | `configure_channel(...)` |
| Zeitbasis und Position | `configure_timebase(...)` |
| Flankentrigger, Pegel, Flanke, Holdoff, Rauschunterdrückung | `configure_edge_trigger(...)` |
| Einzelaufnahme vorbereiten / abschließen | `arm_single()` / `wait_for_single(...)` |
| Mittelung konfigurieren | `configure_acquisition("AVERAGE", averages=16)` |
| Blockierend Daten aufnehmen, z.B. bei periodischen externen Triggern | `digitize((1, 2), timeout_s=30)` |
| Eine / mehrere gespeicherte Kurven | `read_waveform(2)` / `read_waveforms((1, 2))` |
| Zustand und Fehler | `status()`, `wavegen_state()`, `read_errors()`, `check_errors()` |
| Nach I/O-Abbruch bewusst wiederherstellen | `recover()` |
| Nicht gewrappte Gerätefunktionen | `write_scpi(...)`, `query_scpi(...)` |

- Während `digitize()` puffert das Gerät weitere Befehle. Deshalb nicht anschließend
  über dieselbe wartende Verbindung den WaveGen starten wollen. Für die Koordination
  Scharfstellen → Schuss → Auslesen den Single-Ablauf verwenden.
- `WORD` ist Standard. `BYTE` ist kleiner, `ASCII` bereits in Volt skaliert.
  WORD-Transport bedeutet nicht, dass der ADC 16 Bit Auflösung hätte.
- Rohcode 0 wird als fehlender Wert mit NaN markiert; 1 und der Maximalcode werden
  als Clipping markiert. Die Rohcodes bleiben immer erhalten. ASCII hat hier keine
  gleichwertige Clipping-Erkennung.
- Die angeforderten 2.000.000 Punkte sind kein zugesicherter Downloadumfang. Der
  Treiber prüft die tatsächliche Punktzahl aus der Preamble gegen die empfangenen Daten.
  Für Mittelung wird der Messdatensatz mit maximal angeforderten 1000 Punkten gewählt.
  Das Handbuch enthält teils uneinheitliche Punktgrenzen; die reale Firmware muss
  beim ersten Hardwaretest überprüft werden.
- Peak-Detect und segmentierte Aufnahmen werden beim Download ausdrücklich abgewiesen.
  Sie benötigen eine eigene Daten-/Zeitachsenbehandlung. Der Treiber deckt die
  besprochenen Messaufgaben ab, nicht jeden SCPI-Befehl des Geräts.
- `close()` schaltet WaveGen aus, wenn er über die vorgesehenen Treibermethoden
  benutzt wurde. Reines Verbinden/Lesen lässt einen bestehenden Generatorzustand
  unverändert. Ungeprüfte SCPI-Befehle umgehen diese Zustandsverwaltung.
- Ein I/O-Fehler sperrt weitere reguläre Befehle, um Antworten nicht zu verwechseln.
  `recover()` leert die Schnittstelle und stoppt die Aufnahme. Danach Einstellungen
  prüfen; für WaveGen erneut `configure_ttl(...)` ausführen. Keine automatische
  Wiederholung eines Befehls, der weitere Pulse erzeugen könnte.
- Keine GUI, Spektrenkalibrierung, Massenzuordnung oder Messreihenlogik im Treiber.
  Export und Plotting liegen im Testskript. Das passt zur vorgesehenen Trennung von
  Gerätetreibern und Backend. In PySide6 die blockierenden Methoden in einem Worker
  ausführen; Warteschleifen sind über ihren Timeout begrenzt. Nicht gleichzeitig am
  Frontpanel oder aus einer zweiten Anwendung Einstellungen verändern.

## 8. Quellen

Befehle und Datenumrechnung wurden am offiziellen Keysight Programmer’s Guide,
Publication **9018-07747**, Version **02.12.0000**, September 2021, geprüft.
Relevante gedruckte Seiten: 146, 153, 161, 172, 175; 190, 199;
215–229; 605–609; 615–632; 670–690; 702–729.

- [Keysight Programmer’s Guide](https://www.keysight.com/us/en/assets/9018-07747/programming-guides/9018-07747.pdf)
- [Direktes Keysight-PDF](https://www.keysight.com/content/dam/keysight/en/doc/gate/programming-guides/9018-07747.pdf)
- [PyVISA: Kommunikation](https://pyvisa.readthedocs.io/en/latest/introduction/communication.html)
- [PyVISA: ASCII-/Binärtransfer](https://pyvisa.readthedocs.io/en/latest/introduction/rvalues.html)

Das Herstellerhandbuch wird nicht im ZIP vervielfältigt. Der Code ist ein eigener,
projektspezifischer Treiber und kein offizieller Keysight-Treiber.
