# LAPIS Control Suite

Python-Projekt zur Geräteansteuerung und späteren Automatisierung des LAPIS-Aufbaus.
Aktuell vorhanden sind Treiber für den iseg-NHS-Bender, den Coherent Flare NX,
das Keysight DSOX1204G und den Edwards TIC mit nEXT 300 und nXDS.
Der TIC-Treiber liest den Druck und die Pumpendiagnose aus. Das Vakuum-Notebook
enthält die am Aufbau über COM13 erfassten Messwerte.
Bender und Oszilloskop wurden am Gerät getestet; der
Laser-Treiber ist noch nicht vollständig am Gerät validiert.

Die fünf [Architekturentscheidungen](docs/architecture/README.md) aus
`Asekro/LA-TOF-MS-ControlSuite` gelten auch hier. Sie wurden unverändert übernommen.
Die [Bewertung vom 18.11.2026](docs/architecture/review-2026-11-18.md) beschreibt,
was bereits passt und welche Umsetzungsschritte noch offen sind.

## Projektstruktur

```text
LAPIS-Control-Suite/
├── pyproject.toml                 Paket und optionale Abhängigkeiten
├── config/                       Beispiel für aufbauspezifische Anschlussdaten
├── docs/
│   ├── architecture/             ADRs, Herkunft und Bewertung
│   └── development/              Einrichtung und Struktur
├── src/lapis_control/
│   ├── devices/
│   │   ├── base_device.py        Optionale minimale Basis, keine Pflichtvererbung
│   │   ├── bender/               Treiber, Notebook, Dokumentation und tests/
│   │   ├── laser/                Treiber, Notebook, Dokumentation und tests/
│   │   ├── oscilloscope/         Treiber, Notebook, Dokumentation und tests/
│   │   └── vacuum/               Edwards TIC, Notebook, Dokumentation und tests/
│   ├── gui/                     Bereich für die spätere PySide6-Oberfläche
│   └── workflows/               Messabläufe wie die Vierkanalregelung am Bender
└── tests/                       Tests der Gerätekonfiguration und Bender-Regelung
```

Der erste Messablauf hält einen gemeinsamen Spannungsbetrag auf vier
Bender-Elektroden. Die GUI ist geplant.
Die Geräteordner enthalten weiterhin ihre Test-Notebooks. Die
[Strukturbeschreibung](docs/development/project-layout.md) erläutert Zuständigkeiten
und die Zuordnung der früheren Pfade unter `Treiber/`.

## Entwicklungsumgebung

Python ab 3.10 verwenden. Aus dem Repository-Root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[notebook]"
```

In VS Code `.venv\Scripts\python.exe` als Interpreter und Notebook-Kernel wählen.
Für reine Geräteansteuerung genügt `pip install -e .`; Matplotlib/Jupyter sind
optional. PySide6 wird erst für GUI-Arbeit mit `pip install -e ".[gui]"` benötigt.
Eine funktionierende VISA-Laufzeit für das Scope ist unabhängig von der
Python-Paketinstallation erforderlich. Details: [Einrichtung](docs/development/setup.md).

## Geräte und Tests

| Gerät | Dokumentation | Interaktiver Test |
|---|---|---|
| Bender / iseg NHS | [README](src/lapis_control/devices/bender/README.md) | [test_bender.ipynb](src/lapis_control/devices/bender/test_bender.ipynb) |
| Keysight DSOX1204G | [README](src/lapis_control/devices/oscilloscope/README.md) | [test_oscilloscope.ipynb](src/lapis_control/devices/oscilloscope/test_oscilloscope.ipynb) |
| Coherent Flare NX | [README](src/lapis_control/devices/laser/README.md) | [test_laser.ipynb](src/lapis_control/devices/laser/test_laser.ipynb) |
| Edwards TIC / nEXT 300 / nXDS | [README](src/lapis_control/devices/vacuum/README.md) | [test_vacuum.ipynb](src/lapis_control/devices/vacuum/test_vacuum.ipynb) |

Alle vorhandenen Offline-Regressionstests gemeinsam, ohne Gerätezugriff:

```powershell
python -m unittest discover -s src -t src -v
python -m unittest discover -s tests -t . -v
```

Einzelne Geräteordner unterstützen weiterhin `python -m unittest discover -s tests -v`.
Hardwaretests werden bewusst über das zugehörige Notebook oder CLI gestartet.
Schreib-/Ausgabefreigaben in den Notebooks stehen zunächst auf `False`.
Nach der Strukturumstellung vorhandene Kernel neu starten und die Notebooks am
neuen Pfad öffnen; gespeicherte Messausgaben bleiben erhalten.

Beispiel für einen Paketimport nach der Installation:

```python
from lapis_control.devices.bender.iseg_nhs import Bender
from lapis_control.devices.oscilloscope.keysight_dsox1204g import KeysightDSOX1204G
from lapis_control.devices.laser.flare_nx import FlareNx
```

Import und Instanziierung öffnen bei keinem Treiber eine Verbindung. Einheitlich
gilt: `device.connect()` verbindet, `device.close()` schließt. Alternativ übernimmt
ein `with`-Block beides:

```python
scope = KeysightDSOX1204G("USB0::...::INSTR")  # Settings only; use the actual address.
with scope:
    print(scope.idn)
```

`connect()` gibt die Instanz zurück und verwendet eine bereits intakte Verbindung
weiter. `close()` lässt beim Bender HV unverändert, versucht beim Laser OFF und
schaltet beim Scope WaveGen ab, sofern die Instanz ihn verwendet hat. Details und
Kompatibilitätsaliase stehen in [ADR-007](docs/architecture/ADR-007-driver-lifecycle-and-protocol-settings.md).

## Anschlussdaten und Entwicklung

[config/devices.example.json](config/devices.example.json) enthält Aufbaudaten und
Kommunikationsparameter. Eine lokale Kopie `config/devices.local.json` wird von Git
ignoriert. `lapis_control.configuration.load_devices(path)` prüft die gewählte
Datei und erzeugt unverbundene Geräteobjekte. Die JSON enthält ausschließlich
einstellbare Werte. Das feste Kommunikationsprotokoll legt der jeweilige Treiber fest.
Die Geräte-Notebooks wählen die Datei über `CONFIG_PATH`. Details und ein Beispiel
stehen in der [Konfigurationsanleitung](config/README.md).

Dokumentation und Notebook-Erklärungen sind Deutsch; Programmbezeichner,
Kommentare und Docstrings sind Englisch. Gerätekommunikation bleibt in den
Treibern, GUI-Zustand in `gui/` und geräteübergreifende Logik in `workflows/`.
Ein späterer Simulationsmodus ist vorgesehen, aktuell existieren nur die
gezielten Geräte-Doubles der Offline-Tests.

Verbindliche Projektanweisungen stehen in [AGENTS.md](AGENTS.md).
