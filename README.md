# LAPIS Control Suite

Desktop-Anwendung für Bender, Kammerdruck, Pumpendiagnose, Laser und Oszilloskop am LAPIS-Aufbau. PySide6 übernimmt die Bedienung. Gerätezugriffe und Dateiübertragungen laufen im Hintergrund.

## Desktop-Anwendung starten

Unter Windows im Projektordner **LAPIS starten.cmd** doppelklicken. Bei einer neuen Installation zuerst die Projektumgebung einrichten:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[gui]"
.\.venv\Scripts\python.exe -m lapis_control.gui
```

Über **Aufbau wählen** die JSON mit den COM-Ports, Seriennummern, Spannungsgrenzen und der VISA-Adresse laden. Als Vorlage dient [devices.example.json](config/devices.example.json). Eine lokale Kopie unter `config/devices.local.json` lädt die Anwendung beim Start aus dem Projektordner. Alternativ eine Datei mit `--config "Pfad\zum\aufbau.json"` angeben. **Geräte verbinden** öffnet die konfigurierten Verbindungen. Jedes Gerät lässt sich auch einzeln verbinden und bedienen. Eine vorhandene NPZ-Messung lässt sich direkt offline öffnen.

## Am Arbeitsplatz

- **Sitzungsprotokoll.** Unter **Laborübersicht → Sitzungsprotokoll → Logdatei öffnen** lässt sich das laufende Protokoll einsehen. Unter Windows liegt es standardmäßig in `%LOCALAPPDATA%\LAPIS Control Suite\logs\lapis-control.log`. Es enthält Bedienmeldungen, Gerätefehler und Ausnahmeursachen mit Zeitstempeln. Beim TIC stehen auch gesendete Abfragen, empfangene Telegramme, Antwortzeiten und verworfene Fragmente darin. Beim Neustart wird die Datei fortgeschrieben. Ab 4 MiB beginnt eine neue Datei, die letzten drei Vorgänger bleiben im selben Ordner. Mit `--log-dir "D:\LAPIS-Logs"` lässt sich der Ordner wählen.
- **Geräte trennen.** Ein Klick während einer Statusabfrage merkt das Trennen vor. Das Gerät schließt, sobald die laufende Abfrage zurückkehrt. Die übrigen Geräte können währenddessen bereits getrennt werden. Während einer Aufnahme, Regelung oder eines Schaltvorgangs schützt die bestehende Gerätesperre den laufenden Vorgang.
- **Vakuum.** Nach dem Verbinden liest die Anwendung etwa sekündlich die gewählte Messröhre. Der Druck in mbar bleibt beim Seitenwechsel sichtbar. Ein Lesefehler oder ein Alter ab vier Sekunden ersetzt die Zahl durch einen Strich. Das separate Pumpenmonitoring liest Zustand, Drehzahl bzw. Motorfrequenz, Leistung, Temperaturen und Meldungen von nEXT und nXDS im gewählten Intervall.
- **Bender.** Für P0, P1, N0 und N1 vorzeichenrichtige Vorgaben eingeben. Mit **Sollwerte setzen** lassen sich die Spannungen auch bei aktiver HV ändern. Läuft gerade eine Statusabfrage, führt die Anwendung den Auftrag nach erfolgreichem Lesen aus. **HV einschalten** setzt die angezeigten Vorgaben und aktiviert die vier zuvor ausgeschalteten Kanäle. **Regelung mit HV starten** kann bereits aktive Kanäle übernehmen, wenn alle vier Sollwerte passen. Während der Nachregelung gelten neue Eingaben ab **Regelziele übernehmen**. Geänderte Elektroden durchlaufen erneut die eingestellte Einschwingzeit. Die Stromanzeige zeigt den gemessenen Strom in µA. Die Strombegrenzung folgt der vorhandenen Geräteeinstellung. Stoppen fährt die Kanäle ab und prüft Status und interne Restspannung. Auch beim Trennen fährt die Anwendung von ihr übernommene HV ab.
- **Oszilloskop.** Für CH1–CH4 Signalnamen, Aufnahmeauswahl, Skalierung, Offset, Tastkopf und Kopplung einstellen. Triggerquelle, Schwelle, Flanke, Zeitbasis, Aufnahmemodus und Punktzahl wählen. **Neue Single-Aufnahme** übernimmt diese Einstellungen und wartet auf einen frischen Trigger. **Vorhandene Kurve abrufen** stoppt die Erfassung und übernimmt die gewählten, bereits am Gerät angezeigten Kanäle mit ihren eigenen Zeitachsen.
- **Laser.** Im Labor den Interlock schließen und den zur Verdrahtung passenden Scope-Trigger einstellen. Einen gespeicherten Interlockfehler bei Bedarf über die separate Taste quittieren. **Scharfstellen** prüft Laserstatus und Scope-Bereitschaft. Bei verbundenem Bender werden alle vier Elektroden auf Zustand und Spannungsabweichung geprüft. **TTL auslösen** startet den WaveGen mit 0 V Ruhepegel und 5 V Pulspegel, 50 µs Pulsbreite und 0,1 Hz. Der Laser-Tab verwendet für den direkten Oszi-Eingang und den Lasertrigger dieselbe High-Z-Einstellung. An einem tatsächlichen 50-Ω-Abschluss ergibt sich damit ein Pulspegel von etwa 2,5 V. [WaveGen-Last und Pegelanzeige bei Keysight](https://helpfiles.keysight.com/Standalone_BenchVueSoftware_PW_HelpFiles/PWScopeApp/Content/GUI/WaveGen%20Option/Waveform%20Configuration/Output%20Load%20Impedance.htm)

  Unter **Scharfstellzeit** für den nächsten Versuch 1 bis 60 Sekunden wählen (Vorgabe 15 Sekunden). Die Zeit beginnt mit der bestätigten Laserfreigabe und gilt für beide Triggerquellen. Die Restzeit ist auch beim Wechsel auf einen anderen Tab sichtbar.

  Sobald das Scope den ersten frischen Trigger meldet, fordert das Backend WaveGen AUS an. Bei ausbleibender Flanke läuft die Abschaltfrist spätestens nach 8 s ab, zwei Sekunden vor Ablauf einer nominellen Periode. Der verbleibende Teil des Scharfstellfensters kann diese Frist verkürzen. Vor dem Kurvendownload prüft der Ablauf WaveGen-AUS, Laser-AUS und den Kopfzähler. Eine fehlende Kurve, eine abweichende Pulszahl oder eine unbestätigte Abschaltung erscheint als Fehler. Eine erneute Ausgabe braucht eine neue Bedienfreigabe.

- **Externer Einzeltrigger.** Für eine hardwareseitig begrenzte Einzelausgabe die externe Einzelpulsquelle wählen. Die GUI wartet nach dem Scharfstellen auf den externen Puls und prüft den Zähler. Nach Ablauf der gewählten Scharfstellzeit fordert sie Laser-AUS an. Ein verbundenes Scope wird für die Aufnahme reserviert. Bei getrenntem Scope ist ein reiner Lasertest möglich.
- **Messdaten.** Übertragene Messungen stehen einzeln zur Auswahl. Einen Zeitbereich mit der Maus aufziehen, um Min, Max, Spitze–Spitze, Mittelwert, RMS und die Zahl fehlender oder geclippter Samples zu sehen. **NPZ speichern** sichert Zeitachsen, Messwerte, Rohcodes, Qualitätsmasken, Signalnamen und Metadaten. **CSV exportieren** erzeugt eine Zeile je Sample mit Kanal, Signalname, Zeit in Sekunden, Spannung in Volt und Qualitätsflags. Der Zeitpunkt in `transferred_utc` bezeichnet die Übertragung. Ein mitgespeicherter Druckwert trägt seinen eigenen Lesezeitpunkt.
- **Messprofil.** Benderziele, Reglerparameter, Scope-Belegung und Monitoringeinstellungen können in einer eigenen JSON gespeichert werden. Laden übernimmt die Eingabefelder. Verbinden, Übernehmen, HV und Laserfreigabe bleiben eigene Bedienaktionen.

**Stand der Erprobung.** Die Geräte-Notebooks dokumentieren reale Einzelversuche, darunter einen extern ausgelösten Laserpuls. Beim Bender stimmte die intern gemessene Spannung (VMEAS) an den geprüften Punkten gut mit einem Multimeter überein. [Multimetervergleich der NHS-Module](src/lapis_control/devices/bender/README.md#multimetervergleich)

Die integrierte Desktop-Anwendung wurde mit simulierten Instrumenten geprüft. Bei der periodischen WaveGen-Ausgabe hängen Flankenzahl und Abschaltzeit auch vom Generator, Betriebssystem und USB ab. Die Zählerprüfung erkennt das Ergebnis nach dem Versuch. Zur Erprobung des neuen Zeitfensters am Scope zuerst den vorhandenen Loopback-Aufbau mit abgezogener Lasertriggerleitung verwenden. [Laser-Testaufbau](src/lapis_control/devices/laser/README.md) · [Scope-Validierung](src/lapis_control/devices/oscilloscope/VALIDIERUNG.md)

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
│   ├── gui/                     PySide6-Oberfläche, Gerätejobs und Kurvendarstellung
│   └── workflows/               Regelung, Lasermessung, Kurvenübertragung und Auswertung
└── tests/                       Offline-Tests der Konfiguration, Workflows und GUI
```

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
optional. PySide6 wird mit `pip install -e ".[gui]"` installiert.
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
