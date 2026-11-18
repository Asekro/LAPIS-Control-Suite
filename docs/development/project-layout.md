# Zuständigkeiten und Umstellung der Ordner

| Bisher | Neuer Ort |
|---|---|
| `Treiber/Bender/` | `src/lapis_control/devices/bender/` |
| `Treiber/Laser/` | `src/lapis_control/devices/laser/` |
| `Treiber/Oszilloskop/` | `src/lapis_control/devices/oscilloscope/` |
| `test_oszilloskop.ipynb` | `oscilloscope/test_oscilloscope.ipynb` |
| Bender-Testskripte direkt im Geräteordner | `bender/tests/` |

Die Python-Treiberdateien und Hardwareabläufe bleiben erhalten. Geräteübergreifende
Imports verwenden den Paketnamen, zum Beispiel:

```python
from lapis_control.devices.bender.iseg_nhs import Bender
from lapis_control.devices.oscilloscope.keysight_dsox1204g import KeysightDSOX1204G
```

Für eigene Skripte das Projekt einmal mit `python -m pip install -e .` installieren.
Die Geräte-Testskripte können zusätzlich direkt aus dem Checkout aufgerufen werden;
ihre Pfaderkennung bezieht sich auf den Ort der Skriptdatei, nicht auf einen
bestimmten Arbeitsplatzpfad.

## Trennung der Verantwortlichkeiten

- **devices:** Verbindungen, Protokoll, gerätespezifische Grenzwerte und Operationen.
  Keine Qt-Abhängigkeit und kein automatisches Verbinden beim Import.
- **workflows:** künftige Abläufe über mehrere Geräte, einschließlich Reihenfolge,
  Warten, Abbruch und definierten Ausgangszuständen.
- **gui:** Darstellung und Benutzereingaben; blockierende Geräteoperationen laufen
  später in Workern. Die GUI implementiert keine SCPI-Kommandos.
- **config:** Zentrale Kommunikationsparameter und lokale Aufbaudaten.
  `lapis_control/configuration.py` prüft eine ausdrücklich gewählte Datei und
  erzeugt unverbundene Geräteobjekte; die Treiber lesen selbst keine Dateien.
- **docs:** gemeinsame Architektur und Einrichtung. Die gerätespezifischen
  Anleitungen bleiben direkt bei den Geräten.
- **tests im Geräteordner:** Regressionen und explizite Hardware-Diagnoseskripte.
  **tests im Repository-Root:** Integrationstests des Konfigurationsladers und
  künftig weiterer Komponenten.

`base_device.py` speichert lediglich einen Namen. Es schreibt keinen Lebenszyklus
vor und verpflichtet bestehende Treiber nicht zur Vererbung. Der bestehende
Scope-Ein/Aus-Versuch bleibt als optionale gerätespezifische Hilfe beim Scope.

## Vorhandene Notebooks weiterverwenden

Alle gespeicherten Zellenausgaben bleiben erhalten; darin sichtbare alte Dateipfade
gehören zum damaligen Testlauf. Die ausführbaren Imports und Diagnosepfade wurden
auf die neue Struktur angepasst. Kernel neu starten und das Notebook am neuen Ort
öffnen. Hardwarezellen werden bei der Migration nicht ausgeführt.

Windows kann leere alte Ordner noch offen halten, solange ein Notebook-Kernel sie
als Arbeitsverzeichnis verwendet. Sie enthalten nach der Migration keinen Quellcode
mehr und können nach Beenden dieses Kernels entfernt werden.

## Prüfung der Migration am 18.11.2026

- Alle 54 vorhandenen Offline-Tests gemeinsam erfolgreich: 26 Bender, 28 Scope.
- Editierbare Paketinstallation und Wheel-Build in einer temporären Umgebung geprüft.
  Das Wheel enthält auch Notebooks, Geräte-Tests und die Windows/.NET-Diagnose.
- Beide Notebooks mit `nbformat` validiert. Konfiguration aus Arbeitsbereich,
  Repository-Root und Geräteordner ausgeführt; Gerätezugriff dabei ausdrücklich blockiert.
- Gespeicherte Notebook-Ausgaben gegen den ursprünglichen Commit verglichen und erhalten.
- Alle drei Treiberquellen bleiben bis auf mögliche Zeilenende-Darstellung identisch.
- Die fünf übernommenen ADR-Dateien stimmen mit den Git-Blob-Hashes des Quellcommits überein.
- CLI-Hilfen funktionieren als Paketmodule sowie beim direkten Aufruf aus dem Checkout.

Dies ist eine Struktur-/Importprüfung. Bei der Migration wurden keine realen
Geräte verbunden oder neu konfiguriert. Die temporäre Validierungsumgebung und
Build-Dateien wurden nach der Prüfung entfernt.

Anschließend wurden die Verbindungs-APIs und die Konfigurationsanbindung als
separate Änderungen umgesetzt, siehe [ADR-007](../architecture/ADR-007-driver-lifecycle-and-protocol-settings.md)
und [ADR-008](../architecture/ADR-008-central-communication-configuration.md).
Die Aussage zur Quellcodegleichheit oben bezieht sich auf die reine Migration.
