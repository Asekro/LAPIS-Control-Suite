# Entwicklungsumgebung

Alle Projektbefehle werden aus dem Repository-Root `LAPIS-Control-Suite` ausgeführt.
Python ab 3.10 ist die gemeinsame Untergrenze der Treiber. Die letzten lokalen
Treiberprüfungen liefen mit Python 3.15 unter Windows.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[notebook]"
```

Die editierbare Installation stellt `lapis_control` bereit; Änderungen im
Quellcode sind ohne Neuinstallation sichtbar. Nach Änderungen bereits importierter
Module muss der Notebook-Kernel neu gestartet werden. Eine Aktivierung der Umgebung
ist optional; die oben gezeigten expliziten Interpreterpfade funktionieren ohne
Änderung der PowerShell-ExecutionPolicy.

In VS Code den Interpreter `.venv\Scripts\python.exe` wählen und denselben
Interpreter als Notebook-Kernel verwenden. Die Notebooks liegen unter
`src/lapis_control/devices/bender/`, `src/lapis_control/devices/laser/` und `src/lapis_control/devices/oscilloscope/`.
Ihre Konfiguration erkennt den Projekt-Checkout auch aus dem Geräteordner.

Für die Desktop-Anwendung:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
.\.venv\Scripts\python.exe -m lapis_control.gui
```

Die [Qt-Anleitung](https://doc.qt.io/qtforpython-6/gettingstarted.html) beschreibt
die Installation in einer virtuellen Umgebung. Unter Windows startet auch
`LAPIS starten.cmd` im Projektordner die Oberfläche. Das installierte Startprogramm
heißt `lapis-control`. Die GUI-Tests verwenden die Qt-Offscreen-Plattform.

Alle Offline-Gerätetests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s src -t src -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

Beispiel für eine CLI-Hilfe ohne Hardwarezugriff:

```powershell
.\.venv\Scripts\python.exe -m lapis_control.devices.oscilloscope.tests.scope_cli --help
```

Python-Pakete ersetzen nicht die VISA-Laufzeit oder gegebenenfalls benötigte
USB-/COM-Treiber. Gerätespezifische Voraussetzungen und Hardwaretests stehen in
den jeweiligen Geräte-READMEs. Die dortigen kleinen `requirements.txt` bleiben
für eine getrennte Installation einzelner Treiber verfügbar.
