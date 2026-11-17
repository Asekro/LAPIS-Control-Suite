# Validierung · Stand 17.11.2026

**Grundfunktionen am realen DSOX1204G erfolgreich geprüft; 28 Offline-Tests bestanden.**
Belege sind die gespeicherten Notebook-Ausgaben und die vom Benutzer ausgeführten Tests.
Die nachfolgende Tabelle verwendet die Nummerierung nach Entfernen des Exportabschnitts.

## Gerät und Aufbau

- Keysight DSOX1204G, Seriennummer **CN58467706**, Firmware **02.00.2018101202**.
- VISA: `USB0::0x2A8D::0x0396::CN58467706::INSTR`, Windows-Bibliothek
  `C:\Windows\system32\visa32.dll`.
- Python 3.15, PyVISA 1.16.2, NumPy 2.5.5.
- **Gen Out → CH1**, Flankentrigger auf CH1; kein separater externer Trigger.
  Direkter 1-MΩ-Eingang, Tastkopffaktor 1, DC-Kopplung; WaveGen-Last HIGHZ/ONEM.
- Sollsignal 0–5 V, 20 µs. Kurvenaufnahme mit 250 Hz; späterer Generator-Steuertest mit 1 Hz.

## Ergebnis am Gerät

| Funktion | Beleg / Ergebnis |
|---|---|
| Ressourcenliste, Kennung, Status | Punkte 1–3 erfolgreich, Modell und Firmware korrekt erkannt |
| Wiederholte Kommunikation | Punkt 4: zehn Statuszyklen ohne Fehler; WaveGen aus |
| WORD-Download | Punkt 5: CH1, 10.000 Punkte, 2 µs Abstand, keine fehlenden/geclippten Werte |
| Plot und Pulsanalyse | Punkte 6/6b: fünf vollständige Pulse, ca. 19,88 µs Medianbreite, 250 Hz |
| Neue Single-Aufnahme | Punkt 7, Ausführung 9: erfolgreich, CH1 mit 10.000 Punkten ohne Datenverlust/Clipping |
| WaveGen-Konfiguration | Punkt 8 (zuvor 9), Ausführung 20: PULSE, ONEM, 0/5 V, 20 µs, 1 Hz; Ausgang aus |
| WaveGen Ein/Aus | Punkt 9 (zuvor 10), Ausführung 18: Ausgabe gestartet, danach `output=False`; Benutzer bestätigt Erfolg |
| Offline-Tests im Notebook | Punkt 10 (zuvor 11), Ausführung 19: 28 Tests erfolgreich |

Der erste Download auf CH2/CH4 war ebenfalls erfolgreich: je 10.000 Punkte bei
50 ns Abstand, keine fehlenden/geclippten Werte. Diese älteren Ausgaben wurden bei
den weiteren Durchläufen durch CH1-Ausgaben ersetzt.

Die gespeicherte CH1-Pulsanalyse stammt vom vorhandenen Datensatz vor der letzten
Single-Aufnahme. Low-Median −0,157 V, High-Median 4,868 V, Differenz ca. 5,025 V;
Breiten bei 2,5-V-Schwelle etwa 19,86–19,92 µs. Die spätere Single-Aufnahme meldet
einen Maximalwert von 4,968 V. Die absoluten Pegelabweichungen sind nicht kalibriert.
Bei 2 µs Abstand umfasst ein 20-µs-Puls nur etwa zehn Samples; interpolierte
Nachkommastellen sind keine belegte Messgenauigkeit.

Die 250-Hz-Kurve ist keine zusätzliche Messung des späteren 1-Hz-Steuertests.
Für diesen belegen die Ausgaben erfolgreiche Konfiguration, Ein-/Ausschalten und
den zurückgelesenen Aus-Zustand, ergänzt durch die Beobachtung des Benutzers.

## Offline-Prüfungen

```powershell
# Aus dem Treiberordner:
python -m unittest discover -s tests -v
```

Die Tests in `tests/test_driver.py` simulieren VISA und prüfen insbesondere:

- Modellprüfung, unveränderte Ausgänge beim Lesen und Freigabe der Verbindung.
- WORD/BYTE/ASCII-Skalierung, eigene Preamble je Kanal, IEEE-Binärtransfer einschließlich
  Newline im Payload sowie fehlende/geclippte/abgeschnittene Daten.
- Single benötigt einen frischen Trigger und eine abgeschlossene Aufnahme;
  fehlende Trigger führen zum Timeout, alte Kurven werden nicht als neu akzeptiert.
- I/O-Fehler sperren weitere reguläre Befehle; Wiederherstellung ist explizit.
- WaveGen benötigt bestätigte Last und Konfiguration. Falsche Rücklesewerte
  blockieren das Einschalten; Unterbrechung und Fehler lösen Abschaltversuche aus.
- Die CLI-Lesetests verändern keinen Ausgang; Aufnahme/Download liefern Kurven
  ohne Dateiexport und ohne impliziten WaveGen-Start.

Notebook zusätzlich mit simulierten Geräten geprüft: Freigaben, Download, Single,
Plot, Umgang mit Fehlversuchen sowie Erhalt vorhandener Kurven bei erneuter Konfiguration.
Punkt 6b wurde mit synthetischen Pulsen und ungültigen Kurven geprüft.
Die Warnungen zu Clipping in den Offline-Tests stammen aus absichtlich gewählten Testdaten.

## Aufgeräumter Testumfang

Der bisherige NPZ/JSON-Export wurde am Gerät erfolgreich ausgeführt. Vor dem
Entfernen wurden das exportierte CH1-Arraypaket (10.000 Punkte, alle sechs Felder)
und die Metadaten der Single-Aufnahme vom 17.11.2026, 22:20:47 UTC geprüft.
Auf Wunsch des Benutzers sind Exportzellen, Exportoptionen der CLI und der Ordner
`messungen` entfernt. Das Notebook behält die übrigen gespeicherten Ergebnisse.

Die optionalen Terminalskripte liegen jetzt zusammen mit den Regressionstests in
`tests/`. Die Geräteansteuerung bleibt unverändert. Treiber und optionale Ablaufhilfe
verwenden jetzt englische Docstrings, Kommentare sowie Fehler- und Logmeldungen.
Sämtliche Änderungsfreigaben im Notebook sind für einen späteren Neustart wieder `False`.
Nach der Umordnung wurden alle 28 Offline-Tests erfolgreich aus dem Repository-Root
ausgeführt und beide CLI-Hilfen aus dem übergeordneten Arbeitsordner aufgerufen.

## Weitere Tests nach Bedarf

Für den Abschluss der grundlegenden Treiberprüfung ist kein weiterer breiter
Testdurchlauf erforderlich. Zwei gezielte Ergänzungen sind vor präzisen oder
automatisierten Messreihen sinnvoll:

1. **Zeit-/Pegelauflösung:** den 20-µs-Puls mit engerem Zeitfenster, z. B. 10 µs/div,
   aufnehmen und Breite/Pegel mit der Scope-Anzeige vergleichen. Die gesamte
   Pulsform samt Low-Pegel vor/nach dem Puls muss sichtbar sein.
2. **Fehlender Puls / Abbruch:** ohne laufenden WaveGen den Single-Timeout prüfen;
   bei einer kontrollierten Unterbrechung des WaveGen-Tests den Aus-Zustand prüfen.
   Diese Wege sind bisher automatisiert mit simuliertem Gerät geprüft.

LAN, BYTE/ASCII am Gerät, Mittelung und große Datensätze erst bei tatsächlichem
Bedarf separat prüfen. Der experimentelle Ein/Aus-Ablauf in `lapis_workflows.py`
ist keine garantierte Einzelpulsfunktion. Laserlast, elektrische Pegel am
Laseranschluss und physikalische TOF-Kalibrierung bleiben aufbauspezifische Aufgaben.
