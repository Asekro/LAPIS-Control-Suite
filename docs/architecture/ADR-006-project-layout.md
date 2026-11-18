# ADR-006: Paketstruktur und gerätenahe Tests

## Status
Accepted · 18.11.2026 · lokale Ergänzung für LAPIS

## Kontext
Das Projekt enthält bisher eigenständige Treiber unter `Treiber/`. GUI und
Messabläufe sollen hinzukommen. Die Test-Notebooks neben den jeweiligen Treibern
haben sich im Labor bewährt und sollen dort bleiben.

## Entscheidung
- Importierbarer Quellcode liegt unter `src/lapis_control/` mit englischen Paketnamen.
- `devices/<device>/` enthält den Treiber, sein Notebook, seine Dokumentation
  und gerätespezifische Tests/Diagnosen unter `tests/`.
- `gui/` ist für PySide6 reserviert, `workflows/` für geräteübergreifende Abläufe.
  Beide Bereiche importieren Treiber bei Bedarf; Treiber hängen nicht von der GUI ab.
- Projektweite Architektur und Entwicklungsanleitungen liegen in `docs/`.
- Beispiele für aufbauspezifische Werte liegen in `config/`; lokale Werte werden ignoriert.
- Das Paket wird über `pyproject.toml` beschrieben. GUI und Notebook-Zusätze sind optional.
- Projektweite Integrationstests gehören nach `tests/`; Geräte-Regressionstests
  bleiben neben ihren Treibern und sind gemeinsam mit `unittest` auffindbar.

## Begründung
Die Struktur bietet stabile Paketimporte, erhält den angenehmen Labor-Testablauf
und trennt Gerätekommunikation, Bedienoberfläche und Messabläufe. Die geprüfte
Hardwarelogik muss dafür nicht umgeschrieben werden. Leere Bereiche erhalten nur
eine kurze Paketbeschreibung, keine vorgetäuschte GUI oder Simulation.
