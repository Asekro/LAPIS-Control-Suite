# Projektanweisungen

Diese Regeln gelten für das gesamte LAPIS-Projekt. Architekturentscheidungen
stehen unter `docs/architecture/`; lokale Ergänzungen präzisieren die unverändert
übernommenen ADRs.

## Architektur

- Gerätetreiber unter `src/lapis_control/devices/` enthalten ausschließlich
  hardwarenahe Funktionen und gerätebezogene Komfortmethoden.
- Übergeordnete Messabläufe, Auswertung und geräteübergreifende Logik gehören ins
  Backend, insbesondere nach `src/lapis_control/workflows/`.
- Die GUI wird mit PySide6 unter `src/lapis_control/gui/` entwickelt. Treiber
  benötigen kein Qt. Blockierende Hardwarezugriffe gehören nicht in den GUI-Thread.
- Einfache, verständliche Klassen bevorzugen. Keine verpflichtende Basisklasse
  oder zusätzliche Abstraktion ohne konkreten Nutzen einführen.

## Programmierstil

- Python mit Type Hints für neue oder geänderte Schnittstellen verwenden.
- Bezeichner, Kommentare, Docstrings und Treiberfehlermeldungen auf Englisch;
  Dokumentation und Notebook-Erklärungen auf Deutsch schreiben.
- Fehler durch geeignete Exceptions sichtbar machen. Keine Fehler stillschweigend
  verschlucken; beim Aufräumen die ursprüngliche Fehlerursache erhalten.
- Logging statt `print()` für Diagnosedaten im Treiber verwenden. Bewusste
  Ergebnisanzeigen in Notebooks und Kommandozeilenwerkzeugen dürfen `print()` nutzen.

## Hardware und Schnittstellen

- Konstruktoren und Imports öffnen keine Hardwareverbindungen.
- Treiber bieten `connect()` und `close()` sowie einen Kontextmanager an.
  `connect()` gibt die Instanz zurück; `with` verbindet und schließt anschließend.
  Wiederholtes Verbinden einer intakten Verbindung erzeugt keine neue Verbindung.
- Serielle Verbindungen bevorzugt mit PySerial, VISA-Geräte bevorzugt mit PyVISA
  ansprechen. SCPI über einen seriellen COM-Port darf PySerial verwenden.
- Vom unterstützten Geräteprotokoll vorgegebene Werte wie Baudrate, Datenformat,
  Terminatoren und notwendige Befehlsabstände sind benannte Konstanten im Treiber,
  keine frei einstellbaren Konstruktorparameter. Bei Gerätevarianten erst das
  Manual prüfen und die unterstützte Variante ausdrücklich modellieren.
- COM-Ports, VISA-Adressen, Gerätezuordnung und Anwendungslimits kommen von außen.
  Timeouts sind Wartezeiten und dürfen bei Bedarf konfigurierbar sein. Beim NHS
  während einer Transaktion keine seriellen Portparameter neu setzen.
- Aufbaudaten und einstellbare Kommunikationsparameter stehen zentral unter
  `config/`. Feste Protokollwerte gehören in den Treiber.
  `lapis_control.configuration.load_devices()` validiert die gewählte Datei
  und erzeugt unverbundene Instanzen. Treiber lesen selbst keine Dateien;
  es gibt keinen stillen Rückfall auf Beispielwerte. Details stehen in ADR-008 und ADR-009.
- Keine Hardwarebefehle oder Protokollannahmen ohne Grundlage im Manual erfinden.
  Referenzen und beobachtete Firmwareabweichungen nachvollziehbar dokumentieren.

## Sicherheit

- Hochspannung und Laser dürfen weder beim Import noch beim Erzeugen oder
  Verbinden einer Instanz unbeabsichtigt aktiviert werden. Ausgangsfreigaben
  erfolgen ausdrücklich durch den aufrufenden Ablauf.
- Hardwarezugriffe und Zustandsänderungen müssen kontrolliert und nachvollziehbar
  sein. Nach unklarem Ergebnis keine Schaltbefehle automatisch wiederholen.
- Das Abschaltverhalten von `close()` ist gerätespezifisch und dokumentiert.
  Ressourcen auch bei Fehlern freigeben; eine fehlgeschlagene Abschaltung nicht
  als erfolgreich melden. Software-Abschaltung ersetzt keine Hardwareverriegelung.

## Tests und Dokumentation

- Manuelle Hardwaretests als `.ipynb` neben dem jeweiligen Treiber halten;
  automatische Gerätetests in dessen Unterordner `tests/`.
- Automatische Tests verwenden Geräte-Doubles und greifen nicht auf reale
  Hardware zu. Relevante Fehlerfälle und geändertes Verhalten gezielt prüfen.
- Gespeicherte Notebook-Messausgaben erhalten. Hardwaretests und Ausgangsfreigaben
  nur im beauftragten Umfang ausführen; Offline-Prüfungen klar davon unterscheiden.
- Bei Schnittstellenänderungen Beispiele, Notebooks und Dokumentation anpassen.
  Änderungen an Architekturentscheidungen durch eine neue ADR nachvollziehbar machen.
