# ADR-007: Einheitlicher Verbindungsaufbau und feste Protokollparameter

## Status
Accepted · 18.11.2026

Präzisiert ADR-002 und ersetzt für LAPIS die Aussage aus ADR-005, dass die
Baudrate generell bei der Initialisierung überschreibbar sein soll. Die
übernommenen Originaldateien bleiben als Herkunftsnachweis unverändert.

## Kontext

Bender verwendete `open()`, der Laser `connect()`/`disconnect()`, und der Scope
öffnete bereits im Konstruktor eine Verbindung. Das erschwerte gemeinsame
Aufrufe und machte die Instanziierung des Scope-Treibers zu einem Hardwarezugriff.
Frei einstellbare Protokollwerte sind für die derzeit unterstützten Geräte nicht
erforderlich und können eine funktionierende Verbindung verhindern.

## Entscheidung

- Konstruktoren validieren und speichern Einstellungen ohne Hardwarezugriff.
- Alle Treiber bieten `connect()` und `close()`. `connect()` gibt die Instanz
  zurück und öffnet bei bereits intakter Verbindung keine weitere Verbindung.
- Der Kontextmanager verbindet beim Eintritt und schließt beim Verlassen,
  auch wenn der Block mit einem Fehler endet. Kontextmanager nicht verschachteln;
  für die Lebensdauer einer Verbindung ist ein aufrufender Ablauf verantwortlich.
- Verbindungsaufbau aktiviert weder Hochspannung, Laseremission noch WaveGen.
  Gerätekennung beziehungsweise erforderlicher Kommunikations-Handshake werden
  weiterhin geprüft. Beim Laser wird hierfür der Protokoll-Handshake eingeschaltet.
- `close()` kann wiederholt aufgerufen werden. Das gerätespezifische Verhalten
  bleibt ausdrücklich dokumentiert: Bender lässt HV unverändert, der Laser versucht
  OFF mit Bestätigung, der Scope schaltet von dieser Instanz verwendetes WaveGen ab.
  Ein Verbindungsabbruch kann die Abschaltung verhindern; Fehler bleiben sichtbar.
- Ein fehlgeschlagener Verbindungsaufbau gibt seine geöffneten Ressourcen frei.
  Vom Aufrufer bereitgestellte VISA-ResourceManager bleiben in dessen Besitz.
  Kommunikationsfehler führen nicht zu automatischen Wiederholungen von Schaltbefehlen.
- Baudrate, Datenformat, notwendige Befehlsabstände und Terminatoren bleiben
  benannte Konstanten im Treiber. Es gibt dafür keine freien Konstruktorparameter.
  Die Treiber unterstützen damit die dokumentierte und geprüfte Protokollvariante.
- COM-Port, VISA-Adresse, Gerätezuordnung und Anwendungslimits kommen von außen.
  Timeouts sind keine Protokollvorgabe und dürfen bedarfsgerecht konfigurierbar sein:
  NHS und Scope bieten dies bereits an; Flare NX behält vorerst seine festen
  Lese-/Schreibtimeouts. Eine erzwungene identische Parameterliste ist nicht nötig.
- Gerätespezifische, dokumentierte Protokolloptionen wie die Laser-Schnittstelle
  oder die NHS-Echo-Diagnose bleiben ausdrücklich modelliert. Sie sind keine freie
  Konfiguration von Baudrate oder Leitungs-Handshake.

## Folgen

Neue Aufrufer verwenden durchgehend `connect()`/`close()` oder `with`.
Benders `open()` und Flare NXs `disconnect()` bleiben kompatible Aliase.
Scope-Aufrufer außerhalb eines `with`-Blocks müssen jetzt ausdrücklich `connect()`
aufrufen. Die Notebooks verwenden Kontextmanager.

Eine gemeinsame Basisklasse ist dafür nicht erforderlich. Gerätefunktionen und
Abschaltverhalten bleiben unterschiedlich. Konstanten mit `Final` dokumentieren
die Vorgabe für Entwickler und Typprüfer; Python verhindert keine absichtliche
Manipulation von Modulvariablen zur Laufzeit.

Offline-Regressionstests prüfen den Verbindungsablauf und Fehlerfälle.
Gespeicherte Notebook-Ausgaben stammen aus früheren Hardwaretests; sie ersetzen
keinen erneuten Test des geänderten Verbindungsaufbaus am Gerät.
