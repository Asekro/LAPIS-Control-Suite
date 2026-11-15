"""Optionale Ablaufhilfe; bewusst getrennt vom Gerätetreiber."""
import logging
import math
import time

from keysight_dsox1204g import ScopeError

log = logging.getLogger(__name__)


def experimental_wavegen_gate(scope, *, on_time_s=0.2):
    """Manuellen Ein/Aus-Trick nachbilden. KEINE Einzelpuls-Garantie!

    Voraussetzung: configure_ttl(load=..., frequency_hz=1, ...) war erfolgreich.
    Der Generator muss aus sein. Diese Funktion stellt das Oszilloskop nicht
    scharf und wartet nicht auf OptoSync. Koordination gehört ins Mess-Backend.
    Ein unbekannter Phasenstart kann null, einen oder mehrere Pulse ergeben;
    USB-/OS-Latenzen, Schaltflanken und Abbruch können das Ergebnis verändern.
    on_time_s ist nur die Schlafzeit zwischen den Steueraufrufen.
    """
    if isinstance(on_time_s, bool) or not math.isfinite(on_time_s) or on_time_s <= 0:
        raise ValueError("on_time_s muss endlich und positiv sein.")
    state = scope.wavegen_state()
    if state["output"]:
        raise ScopeError("WaveGen läuft bereits; zuerst bewusst ausschalten.")
    period = 1 / state["frequency_hz"]
    if state["frequency_hz"] > 1 or not state["pulse_width_s"] < on_time_s < period / 2:
        raise ValueError("Für diesen Versuch f <= 1 Hz und Pulsbreite < on_time_s < halbe Periode wählen.")
    log.warning("Experimenteller WaveGen-Ein/Aus-Trick: Pulszahl und Abschaltzeit sind nicht garantiert.")
    try:
        scope.wavegen_output(True)
        time.sleep(on_time_s)
    except BaseException:
        # Auch bei Unterbrechung des Einschaltens; ursprünglichen Fehler behalten.
        try:
            scope.wavegen_output(False)
        except Exception:
            log.exception("WaveGen-Abschaltung nicht bestätigt; Gerät prüfen")
        raise
    else:
        scope.wavegen_output(False)
