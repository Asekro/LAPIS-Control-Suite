# Keysight DSOX1204G für LAPIS

Python-Treiber für das **Keysight DSOX1204G**, über USB/VISA oder eine explizite
LAN-VISA-Adresse. Getestetes Gerät: **CN58467706**, Firmware **02.00.2018101202**.
Der DSOX4054A gehört zu einem anderen Aufbau und wird von dieser Klasse abgewiesen.

**Stand 17.11.2026:** Verbindung, Status, Kurvendownload, Single-Aufnahme sowie
WaveGen-Konfiguration und Ein-/Ausschalten am Gerät erfolgreich geprüft.
28 Offline-Regressionstests ergänzen die Hardwaretests. Messwerte und verbleibende
Grenzen stehen in [VALIDIERUNG.md](VALIDIERUNG.md).

Die Desktop-Anwendung verwendet `timed_ttl_window()` für ein zeitbegrenztes
WaveGen-Fenster. Vor dem Einschalten prüft der Treiber die zurückgelesenen
Parameter. Beim ersten frischen Triggerereignis fordert er AUS an und bewahrt
das Ereignis für die anschließende Single-Prüfung auf. Die zusätzliche
Abschaltfrist schließt die Zeit für die Einschaltbestätigung ein.
Die tatsächlich registrierte Pulszahl liefert anschließend der Laser.
Die zeitliche Begrenzung wurde offline getestet. Bedienung, Kurvenexport und
Auswertung sind in der [Projektanleitung](../../../../README.md#am-arbeitsplatz) beschrieben.

Beim Scharfstellen wartet der Laser-Tab auf die Bereitschaft der neuen
Single-Aufnahme (`:AER?`). Das Lesen löscht dieses Ereignis. Direkt vor der
Laserfreigabe und vor dem WaveGen-Start prüft `require_untriggered_single()`,
ob die Aufnahme noch läuft (RUN) und ob bereits eine Flanke eingetroffen ist
(`:TER?`). Bei einer vorzeitigen Flanke oder einer gestoppten Aufnahme zeigt
die Fehlermeldung den Trigger- und Registerzustand an. [Keysight-Programmierhandbuch, Abschnitte AER, OPERegister:CONDition und TER](https://www.keysight.com/us/en/assets/9018-07747/programming-guides/9018-07747.pdf)

## Ordnerstruktur

| Datei | Aufgabe |
|---|---|
| `keysight_dsox1204g.py` | Eigentlicher Gerätetreiber; unabhängig von Notebook und Testskripten |
| `requirements.txt` | Laufzeitabhängigkeiten: NumPy und PyVISA |
| `test_oscilloscope.ipynb` | Hauptzugang für interaktive Tests direkt neben dem Treiber |
| `requirements_plot.txt` | Zusätzlich Matplotlib für Kurvenplots |
| `tests/test_driver.py` | Automatische Regressionstests mit simuliertem Gerät |
| `tests/scope_cli.py` | Optionale Terminalalternative: Status, Download, Single und Plot |
| `tests/wavegen_cli.py` | Optionaler Terminaltest für WaveGen-Konfiguration und Ausgabe |
| `lapis_workflows.py` | Optionale, experimentelle Ablaufhilfe für einen kurzen WaveGen-Ein/Aus-Versuch |
| `VALIDIERUNG.md` | Nachweis der getesteten Funktionen und Messgrenzen |

Für die Geräteansteuerung werden der Treiber und seine Laufzeitabhängigkeiten
benötigt. Das Notebook ist für die manuelle Prüfung gedacht; die Offline-Tests
prüfen auch Fehlerfälle ohne Gerät. Die beiden CLI-Skripte sind optionale
Alternativen. `lapis_workflows.py` wird nur für den experimentellen Ablauf benötigt.
Notebook und CLI-Tests erzeugen keine Messdateien; Speichern/Auswerten für spätere
Messreihen gehört in die Messanwendung.

## Einrichtung

Python ab 3.10 und eine funktionierende VISA-Laufzeit verwenden. Falls noch keine
vorhanden ist: [Keysight IO Libraries Suite](https://www.keysight.com/zz/en/lib/software-detail/computer-software/io-libraries-suite-downloads-2175637.html).
USBTMC verwendet keinen COM-Port. Eine bestehende VISA-Installation kann weiterverwendet werden.

Aus diesem Treiberordner:

```powershell
python -m pip install -r requirements_plot.txt
```

Das installiert auch die Abhängigkeiten aus `requirements.txt`. Ohne Plots reicht
`requirements.txt`. Für das Notebook einen Jupyter-Python-Kernel mit diesen Paketen
wählen. Nach Änderungen an Python-Dateien den Kernel neu starten.

## Tests im Notebook

Aufbau: **Gen Out → CH1**, Flankentrigger auf CH1. Das eingebaute WaveGen liefert
die Pulse; ein separater externer Trigger ist nicht erforderlich. Das Notebook
verwendet CH1, 0–5 V und 20 µs. `HIGHZ` gilt für den direkten 1-MΩ-Eingang ohne
externen 50-Ω-Abschluss; CH1 mit Tastkopffaktor 1 und DC-Kopplung verwenden.

| Punkt | Inhalt |
|---|---|
| 0 | Konfiguration, VISA-Adresse und Freigaben |
| 1–4 | Ressourcenliste, Kennung, Status und zehn Lesezyklen |
| 5 | Vorhandene Kurve herunterladen; stoppt die Aufnahme |
| 6 / 6b | Plot sowie TTL-Pegel und Pulsbreite aus CH1 bestimmen |
| 7 | Neue Single-Aufnahme mit dem WaveGen-Signal auf CH1 |
| 8 | WaveGen konfigurieren und zurücklesen, Ausgang bleibt aus |
| 9 | WaveGen für zwei Sekunden einschalten, danach ausschalten |
| 10 | Offline-Regressionstests aus `tests/` |

Die Freigaben stehen für einen neuen Durchlauf auf `False`; gespeicherte Ausgaben
stammen aus den erfolgreichen Testläufen. Zuerst 0–4 ausführen. In Punkt 0 die
VISA-Adresse aus Punkt 1 übernehmen. Danach nach Bedarf freigeben:

- **Download/Single:** `ALLOW_ACQUISITION_CHANGES = True`. Für eine Aufnahme
  **0 → 3 → 7 → 6 → 6b** ausführen. Der am Gerät eingestellte Generator darf laufen;
  Punkt 7 verändert dessen Ausgangszustand nicht. Trigger, Skalen und Tastkopffaktor
  vorher am Gerät prüfen. Beispiel: steigende CH1-Flanke bei 2,5 V, 10 µs/div,
  Trigger mittig und vollständiger Spannungsbereich 0–5 V sichtbar.
- **WaveGen-Konfiguration:** `ALLOW_WAVEGEN_CONFIG = True`. Vor Punkt 8 einen
  bereits laufenden Generator am Gerät ausschalten. Testparameter sind 0–5 V,
  20 µs und 1 Hz; diese Wiederholrate ist ein Testwert, keine Anwendungsvorgabe.
- **WaveGen-Ausgabe:** zusätzlich `ALLOW_WAVEGEN_OUTPUT = True` und
  `CONFIRM_ELECTRICAL_LOOPBACK = True` für Gen Out ausschließlich am Scope.
  Punkt 9 benötigt die Hilfsfunktion aus Punkt 8. Zum Aufzeichnen zuerst am
  Frontpanel Single auf CH1 scharfstellen, dann **9 → 5 → 6 → 6b** ausführen.
  Es wird eine periodische Pulsfolge erzeugt; die Zelle schaltet danach wieder aus.

Download/Single lässt das Oszilloskop auf STOP. Single setzt MAIN/RTIME/NORMAL und
aktiviert die gewählten Kanäle. Zum Weiterbeobachten RUN am Frontpanel wählen.
Erneutes Ausführen von Punkt 0 erhält vorhandene Kurven mit ihren ursprünglichen
Metadaten. Ein neuer Versuch in 5/7 leert diese vorher, damit nach einem Fehler
keine alte Aufnahme als neues Ergebnis erscheint. Ein Kernel-Neustart leert den Speicher.
Mit **Strg+S** bleiben dargestellte Ausgaben und Plots im Notebook erhalten.

## Tests im Terminal

Alle folgenden Aufrufe erfolgen aus dem Treiberordner. Die Skripte öffnen ohne
expliziten Aufruf kein Gerät. `USB0::...::INSTR` durch die eigene Adresse ersetzen.

```powershell
python -m unittest discover -s tests -v
python tests/scope_cli.py --list
python tests/scope_cli.py --resource "USB0::...::INSTR"
python tests/scope_cli.py --resource "USB0::...::INSTR" --read --channels 1 --plot
python tests/scope_cli.py --resource "USB0::...::INSTR" --capture --channels 1 --plot
```

`--capture` wartet auf die eingestellte Triggerquelle; der Generator wird dabei
nicht eingeschaltet. Die beiden Aufnahmemodi geben Punktzahl und Qualität aus,
zeigen optional den Plot und schreiben keine Dateien.

```powershell
# Nur konfigurieren, Ausgang aus:
python tests/wavegen_cli.py --resource "USB0::...::INSTR" --load HIGHZ --width-us 20
# Explizit drei Sekunden periodische Pulse:
python tests/wavegen_cli.py --resource "USB0::...::INSTR" --load HIGHZ --width-us 20 --mode continuous --seconds 3
```

## Verwendung des Treibers

Für Paketimporte zuerst im Repository-Root `python -m pip install -e .` ausführen.

Für den beschriebenen CH1-Aufbau mit bereits am Gerät eingestelltem WaveGen:

```python
from lapis_control.devices.oscilloscope.keysight_dsox1204g import KeysightDSOX1204G

with KeysightDSOX1204G("USB0::...::INSTR") as scope:
    scope.configure_channel(1, enabled=True, probe=1, coupling="DC", scale_v=1.0, offset_v=2.5)
    scope.configure_timebase(scale_s=10e-6, position_s=0, reference="CENTER")
    scope.configure_edge_trigger(1, level_v=2.5, slope="POSITIVE")
    scope.configure_acquisition("NORMAL")
    scope.arm_single()
    scope.wait_until_armed()
    # Der laufende WaveGen liefert die Triggerflanke an CH1.
    scope.wait_for_single(timeout_s=30)
    wave = scope.read_waveform(1, points=10000, stop=False)
    time_s, voltage_v = wave.time_s, wave.voltage_v
```

`configure_ttl(load="HIGHZ", frequency_hz=1, pulse_width_s=20e-6, low_v=0, high_v=5)`
konfiguriert den Generator mit Ausgang AUS. Erst `wavegen_output(True)` startet die
Pulsfolge. Im selben Verbindungsobjekt konfigurieren, und in einem `finally`-Block
mit `wavegen_output(False)` abschalten. `close()` versucht ebenfalls abzuschalten,
wenn der Generator über die Treibermethoden verwendet wurde. Reines Verbinden/Lesen
lässt einen bereits laufenden Generator unverändert.

Weitere Funktionen: `status()`, `run()`, `stop()`, `configure_acquisition("AVERAGE", averages=16)`,
`read_waveforms((1, 2))`, `digitize(...)`, `read_errors()` und `recover()`.
`read_errors()` entfernt gelesene Fehlereinträge. `recover()` leert die Schnittstelle
und stoppt die Aufnahme; nach I/O-Fehlern erfolgt keine automatische Wiederholung.

## Grenzen und optionale Abläufe

- Eine Single-Aufnahme kann mehrere Pulse im Zeitfenster enthalten.
  `experimental_wavegen_gate()` in `lapis_workflows.py` schaltet eine langsame
  Pulsfolge kurz ein/aus. Es können null, ein oder mehrere Pulse entstehen;
  das ist kein garantierter Einzelpuls. Optional über `tests/wavegen_cli.py --mode gate`.
- `HIGHZ`/`50OHM` beschreibt die angeschlossene Last. Die Laser-Eingangsimpedanz
  muss vor dem Einsatz dort geklärt werden; der CH1-Test belegt nur diesen Aufbau.
  Rückgelesene Sollwerte ersetzen keine Messung der tatsächlichen Pegel.
- Bei Kabelverlust oder hartem Prozessabbruch kann WaveGen weiterlaufen.
  `finally` und `close()` sind Abschaltversuche, kein Hardware-Watchdog.
- WORD, BYTE und ASCII werden unterstützt. WORD-Übertragung bedeutet keine
  16-Bit-ADC-Auflösung. Tatsächliche Punktzahl und Zeitachse kommen aus der Preamble;
  die angeforderte Punktzahl ist keine Garantie. AVERAGE nutzt höchstens angeforderte
  1000 Punkte; Peak-Detect und segmentierte Aufnahmen werden nicht unterstützt.
- Rohcode 0 markiert fehlende Werte, 1 bzw. der Maximalcode Clipping. ASCII enthält
  keine gleichwertigen Clipping-Markierungen. TTL-Auswertung in 6b nutzt eine feste
  2,5-V-Schwelle mit Interpolation und keine festgelegten Bestehensgrenzen.
- `digitize()` blockiert bis zur Aufnahme. Zum Starten des WaveGen nach dem Scharfstellen
  den getrennten Single-Ablauf verwenden. Während Treiberabläufen keine konkurrierenden
  Befehle aus einer zweiten Anwendung senden. Blockierende Aufrufe im GUI-Worker ausführen.
- Physikalische TOF-Kalibrierung, Messreihen und dauerhafte Datenspeicherung gehören
  in die übergeordnete Messanwendung.

## Referenzen

Die ursprüngliche SCPI-Implementierung wurde mit dem Keysight Programmer’s Guide,
Publication 9018-07747, Version 02.12.0000 (September 2021), abgeglichen.

- [Keysight Programmer’s Guide](https://www.keysight.com/us/en/assets/9018-07747/programming-guides/9018-07747.pdf)
- [PyVISA-Kommunikation](https://pyvisa.readthedocs.io/en/latest/introduction/communication.html)
- [PyVISA-Binärtransfer](https://pyvisa.readthedocs.io/en/latest/introduction/rvalues.html)

Projektspezifischer Treiber, kein offizieller Keysight-Treiber.
