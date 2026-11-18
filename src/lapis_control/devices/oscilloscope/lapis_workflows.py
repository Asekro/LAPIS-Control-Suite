"""Optional workflow helper, kept separate from the device driver."""
import logging
import math
import time

from .keysight_dsox1204g import ScopeError

log = logging.getLogger(__name__)


def experimental_wavegen_gate(scope, *, on_time_s=0.2):
    """Reproduce manual output gating. Does NOT guarantee a single pulse.

    Requires successful configure_ttl(load=..., frequency_hz=1, ...).
    The generator must be off. This function does not arm the oscilloscope
    or wait for OptoSync. Coordination belongs in the measurement backend.
    An unknown starting phase can produce zero, one, or multiple pulses;
    USB/OS latency, switching edges, and interruption can affect the result.
    on_time_s is only the sleep duration between control calls.
    """
    if isinstance(on_time_s, bool) or not math.isfinite(on_time_s) or on_time_s <= 0:
        raise ValueError("on_time_s must be finite and positive.")
    state = scope.wavegen_state()
    if state["output"]:
        raise ScopeError("WaveGen is already running; explicitly switch it off first.")
    period = 1 / state["frequency_hz"]
    if state["frequency_hz"] > 1 or not state["pulse_width_s"] < on_time_s < period / 2:
        raise ValueError("This experiment requires f <= 1 Hz and pulse width < on_time_s < half the period.")
    log.warning("Experimental WaveGen gating: pulse count and shutdown timing are not guaranteed.")
    try:
        scope.wavegen_output(True)
        time.sleep(on_time_s)
    except BaseException:
        # Also handle interruption while enabling; preserve the original error.
        try:
            scope.wavegen_output(False)
        except Exception:
            log.exception("WaveGen shutdown not confirmed; check the device")
        raise
    else:
        scope.wavegen_output(False)
