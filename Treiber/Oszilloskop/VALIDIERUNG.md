# Validierung · 15.11.2026

**28 Offline-Tests bestanden. Kein Test am realen DSOX1204G erfolgt.**
Umgebung: Python 3.12.14, PyVISA 1.16.2, NumPy 2.3.5, Linux.
Windows/VISA/USB und die konkrete Geräte-Firmware sind noch zu prüfen.

Reproduzierbar im entpackten Ordner:

```powershell
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Zusätzlich wurden die vier Python-Programme kompiliert und beide CLI-Hilfen aufgerufen.

## Was die Tests belegen

- Modellprüfung und Schließen der Verbindung; ein externer ResourceManager bleibt offen.
- Verbinden und Statusskript senden keine Befehle, die einen Ausgang ändern.
- WORD/BYTE-Werte werden korrekt skaliert, inklusive negativer Zeit-/Spannungsoffsets.
- IEEE-Binärblöcke werden mit dem echten PyVISA-Parser decodiert; ein 0x0A im Payload
  wird nicht mit dem Nachrichtenende verwechselt. Die VISA-Hardware selbst ist simuliert.
- ASCII-Spannungen werden nicht ein zweites Mal skaliert.
- Fehlende Punkte und Clipping-Codes werden gekennzeichnet; unvollständige Downloads
  und defekte Preambles werden abgewiesen.
- Mehrere Kanäle verwenden ihre jeweilige Preamble und eine gemeinsam gestoppte Aufnahme.
- Mittelung wählt den Messdatensatz; Peak-Detect wird nicht mit falscher Zeitachse ausgegeben.
- Alte Triggerereignisse reichen nicht für eine neue Aufnahme. Ein frischer Trigger ohne
  abgeschlossene Aufnahme reicht ebenfalls nicht. Fehlende Trigger führen zum Timeout.
- Nach Transfer-/DIGitize-I/O-Fehlern erfolgen keine automatischen Wiederholungen;
  Timeouts und Leseterminierung werden zurückgesetzt, Recovery ist explizit.
- Fehlertexte mit Kommas bleiben erhalten. Ungültige Zahlen werden vor Konfiguration erkannt.
- TTL benötigt eine explizite Lastangabe und erfolgreiche Konfiguration. Geänderte oder
  begrenzte zurückgelesene Pegel/Frequenzen blockieren das Einschalten.
- Der Ein/Aus-Versuch schaltet nach KeyboardInterrupt ab; nach einem I/O-Fehler wird
  OFF bestmöglich versucht. Das belegt keine Abschaltgarantie bei realem Kabelverlust.
- Status-, Read- und Capture-Testskript laufen gegen das simulierte Gerät;
  NPZ-Daten und JSON-Metadaten enthalten die erwarteten Inhalte. Kein impliziter WaveGen-Start.

Die Simulation beweist keine korrekte Firmwareantwort, USB-Latenz, elektrische
Signalqualität oder Laser-Pulszahl. Die SCPI-Syntax wurde separat mit dem in README
verlinkten offiziellen Keysight-Handbuch abgeglichen.

## Erster Hardwaretest

1. `--list`, dann Statusabfrage: Gerätekennung, Firmware, VISA-Adresse festhalten.
2. Bekannte gespeicherte Kurve lesen. Punktzahl, Zeitintervall, DC-Pegel und Amplitude
   mit dem Oszilloskop vergleichen; WORD, bei Bedarf BYTE/ASCII gegentesten.
3. Single scharfstellen, erst ohne Trigger den Timeout prüfen, danach einen bekannten
   Trigger zuführen und CH1/CH2 gemeinsam auslesen.
4. Laserlast klären. WaveGen zunächst elektrisch am Oszilloskop testen: Low/High,
   Pulsbreite, Wiederholrate und Aus-Zustand messen.
5. Den experimentellen Ein/Aus-Trick mehrfach elektrisch aufzeichnen. Null-/Mehrfach-
   oder abgeschnittene Pulse sind möglich. Daraus keine garantierte Einzelpulsfunktion ableiten.

Konfiguration des Arbeitsplatzes, Lasersteuerung und physikalische TOF-Kalibrierung
bleiben Aufgaben der übergeordneten Messsoftware.
