# Projektweite Tests

`test_configuration.py` prüft Dateivalidierung, Parameterübergabe und das Erzeugen
von Geräteobjekten ohne Hardwarezugriff.
`test_bender_voltage.py` prüft die Vierkanalregelung mit zwei simulierten
NHS-Modulen und einer simulierten Uhr, vom Einschwingen bis zum Abfahren.
`test_measurement_workflows.py` prüft die geräteübergreifende Schussfolge,
Fehler und Abbrüche sowie NPZ, CSV und Auswertung.
`test_desktop_gui.py` bedient die Qt-Oberfläche mit simulierten Geräten
einschließlich paralleler Regelung, Monitoring, Schuss und Dateitransfer.
Diese Tests benötigen das Extra `gui` und werden ohne PySide6 übersprungen.

Die bestehenden Geräte-Regressionstests bleiben unter
`src/lapis_control/devices/<device>/tests/`, damit Treiber, Notebook und Tests
beieinander liegen. Alle vorhandenen Tests aus dem Repository-Root ausführen:

```powershell
python -m unittest discover -s src -t src -v
python -m unittest discover -s tests -t . -v
```

Die Tests verwenden simulierte Verbindungen. Hardwaretests werden separat im
jeweiligen Notebook oder über die Diagnose-CLI gestartet.
