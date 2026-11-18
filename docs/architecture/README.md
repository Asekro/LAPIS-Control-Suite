# Architekturentscheidungen

ADR-001 bis ADR-005 wurden am **18.11.2026 unverändert** aus
[Asekro/LA-TOF-MS-ControlSuite](https://github.com/Asekro/LA-TOF-MS-ControlSuite/tree/01e430b93934e197204fc581cfde58206cd84669/docs/architecture)
übernommen. Quellstand: `01e430b93934e197204fc581cfde58206cd84669`.
Der ursprüngliche Status `Accepted` bleibt erhalten. `Accepted` beschreibt die
angenommene Entscheidung, nicht die vollständige Umsetzung sämtlicher Funktionen.

| ADR | Entscheidung | Herkunft |
|---|---|---|
| [001](ADR-001-gui-framework.md) | PySide6 für die GUI | Unverändert übernommen |
| [002](ADR-002-device-interface.md) | Einfache Geräteklassen, optionale Basis | Unverändert übernommen |
| [003](ADR-003-simulation-mode.md) | Spätere Simulation ermöglichen | Unverändert übernommen |
| [004](ADR-004-language.md) | Dokumentation Deutsch, Quellcode Englisch | Unverändert übernommen |
| [005](ADR-005-device-configuration.md) | Gerätestandards im Treiber, Aufbaudaten von außen | Unverändert übernommen |
| [006](ADR-006-project-layout.md) | Paketstruktur und gerätenahe Tests | Lokale Ergänzung für LAPIS |
| [007](ADR-007-driver-lifecycle-and-protocol-settings.md) | `connect()`/`close()`, feste Protokollwerte | Präzisiert 002; ersetzt die freie Baudratenwahl aus 005 |
| [008](ADR-008-central-communication-configuration.md) | Zentrale, validierte Kommunikationskonfiguration | Ergänzt 005 und 007; einstellbare Laser-Timeouts |

Die [Bewertung](review-2026-11-18.md) enthält Anwendungshinweise und offene Punkte
für dieses Projekt; sie verändert die übernommenen ADR-Texte nicht.
[import-manifest.json](import-manifest.json) enthält Repository, Commit und
Git-Blob-Prüfsummen der fünf Originaldateien. Die Prüfsummen wurden beim Abruf
gegen die tatsächlichen Datei-Bytes geprüft.

Neue Entscheidungen erhalten fortlaufende Nummern. Änderungen an einer bereits
angenommenen Entscheidung werden nachvollziehbar durch ein neues ADR dokumentiert.
