# ADR-009 · Einstellbare Werte in der Gerätekonfiguration

Angenommen am 26.11.2026. Diese Entscheidung ändert die Vorgabe zu festen
Protokollfeldern aus [ADR-008](ADR-008-central-communication-configuration.md).

Die bisherige Vorlage führte feste und einstellbare Werte nebeneinander auf.
Bei der Baudrate erweckte das den Eindruck, sie ließe sich über die JSON ändern.

Die Vorlage enthält nur Werte, die der Lader an die Geräte übergibt. Dazu
gehören Anschlussdaten, Anwendungslimits, Timeouts und unterstützte Optionen.
Jeder Treiber legt seine festen Protokollparameter selbst fest.

Bestehende Aufbaudateien dürfen die früher erforderlichen Protokollfelder
weiter enthalten. Der Lader prüft vorhandene Altfelder gegen das jeweilige
Treiberprofil. Widersprüchliche Werte und unbekannte Schlüssel führen vor dem
Verbindungsaufbau zu einer `ConfigurationError`.
