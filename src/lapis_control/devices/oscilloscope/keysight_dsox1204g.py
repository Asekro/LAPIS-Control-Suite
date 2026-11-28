"""Keysight DSOX1204G: USB/LAN, SCPI via PyVISA. Python >= 3.10.

Device driver without GUI, plotting, file export, or laser measurement workflows.
Reference: Keysight 9018-07747, Rev. 02.12.0000, September 2021.
All quantities use SI units: s, V, Hz. No automatic reset or generator start.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import math
from threading import Event, RLock
import time
from typing import Final

import numpy as np
import pyvisa

log = logging.getLogger(__name__)

TERMINATOR: Final[str] = "\n"


class ScopeError(RuntimeError):
    """Invalid device state, SCPI error, or unexpected response."""


class AcquisitionTimeout(TimeoutError):
    """A new acquisition or trigger readiness was not confirmed before timeout."""


@dataclass
class Waveform:
    """One waveform, preserving raw codes and transfer metadata."""
    channel: int
    time_s: np.ndarray
    voltage_v: np.ndarray
    raw: np.ndarray
    preamble: dict
    missing: np.ndarray
    clipped_low: np.ndarray
    clipped_high: np.ndarray


def _number(value, name, minimum=None, maximum=None):
    if isinstance(value, (bool, str)):
        raise ValueError(f"{name}: provide a finite number.")
    v = float(value)
    if not math.isfinite(v) or (minimum is not None and v < minimum) or (
        maximum is not None and v > maximum
    ):
        raise ValueError(f"{name}: invalid value {value!r}.")
    return v


def _channel(value):
    if isinstance(value, bool) or not isinstance(value, int) or value not in (1, 2, 3, 4):
        raise ValueError("Channel must be an integer from 1 to 4.")
    return value


def _choice(value, allowed, name):
    v = str(value).upper()
    if v not in allowed:
        raise ValueError(f"{name}: choose one of: {', '.join(allowed)}")
    return v


def _boolean(value):
    if not isinstance(value, bool):
        raise ValueError("Provide True or False.")
    return int(value)


class KeysightDSOX1204G:
    """An explicit VISA resource, optionally using a shared resource manager.

    The constructor stores settings; connect explicitly or use `with`.
    Call `close()` to release an explicit connection.
    `*IDN?` must identify a DSOX1204G. Other models are not supported.
    The lock serializes command sequences within this instance.
    Use one driver per device and avoid concurrent front-panel operations.
    """
    def __init__(self, resource: str, *, timeout_ms: float = 5000,
                 visa_backend: str = "",
                 resource_manager: pyvisa.ResourceManager | None = None) -> None:
        timeout_ms = _number(timeout_ms, "timeout_ms", 1, 2**31 - 1)
        if not isinstance(resource, str) or not resource.strip():
            raise ValueError("Provide a non-empty VISA resource address.")
        self.resource = resource
        self.idn = ""
        self.timeout_ms = int(timeout_ms)
        self.visa_backend = visa_backend
        self._lock = RLock()
        self._owns_rm = resource_manager is None
        self._rm = resource_manager
        self._inst = None
        self._io_failed = False
        self._single_pending = False
        self._trigger_seen = False
        self._wavegen_touched = False
        self._ttl_config = None

    def connect(self) -> "KeysightDSOX1204G":
        """Open and identify the scope without changing acquisition or outputs.

        Reusing a healthy connection is a no-op. After an I/O error, recover
        explicitly or close before reconnecting. A new session requires fresh
        TTL configuration before enabling WaveGen.
        """
        with self._lock:
            if self._inst is not None:
                self._check_connection()
                return self
            self._io_failed = False
            self._single_pending = False
            self._trigger_seen = False
            self._wavegen_touched = False
            self._ttl_config = None
            self.idn = ""
            try:
                if self._owns_rm:
                    self._rm = pyvisa.ResourceManager(self.visa_backend)
                self._inst = self._rm.open_resource(self.resource)
                self._inst.timeout = self.timeout_ms
                self._inst.write_termination = TERMINATOR
                self._inst.read_termination = TERMINATOR
                self._inst.chunk_size = 1024 * 1024
                idn = self.query_scpi("*IDN?")
                self._validate_idn(idn)
                self.idn = idn
            except BaseException:
                try:
                    self._release_resources()
                except Exception:
                    log.exception("Additional error while cleaning up a failed connection")
                raise
        return self

    def _release_resources(self) -> None:
        """Release transports under the instance lock, preserving shared managers."""
        inst, self._inst = self._inst, None
        try:
            if inst is not None:
                inst.close()
        finally:
            if self._owns_rm and self._rm is not None:
                rm, self._rm = self._rm, None
                rm.close()

    @staticmethod
    def _validate_idn(idn):
        fields = idn.split(",")
        if len(fields) < 4 or fields[1].strip().upper() != "DSOX1204G":
            raise ScopeError(f"Expected DSOX1204G, received: {idn!r}")

    @staticmethod
    def list_resources(visa_backend=""):
        """List resources without opening instruments or changing outputs."""
        rm = pyvisa.ResourceManager(visa_backend)
        try:
            return rm.list_resources()
        finally:
            rm.close()

    def __enter__(self) -> "KeysightDSOX1204G":
        return self.connect()

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise
            log.exception("Additional error while closing the oscilloscope")
        return False

    def close(self) -> None:
        """Disable WaveGen if used by this instance; otherwise leave it unchanged.

        After connection loss, OFF is an attempt, not a shutdown guarantee.
        Any error is propagated after resources have been released.
        """
        with self._lock:
            if self._inst is None:
                return
            inst = self._inst
            try:
                if self._wavegen_touched:
                    inst.write(":WGEN:OUTPut OFF")
                    if self._io_failed or inst.query(":WGEN:OUTPut?").strip() != "0":
                        raise ScopeError("WaveGen shutdown not confirmed; check the device.")
            finally:
                self._release_resources()

    def _check_connection(self):
        if self._inst is None:
            raise ScopeError("Connection is closed. Call connect() or use a with block.")
        if self._io_failed:
            raise ScopeError("Communication blocked after an I/O error. Call recover() or reconnect.")

    def _io(self, method, *args, **kwargs):
        self._check_connection()
        try:
            return getattr(self._inst, method)(*args, **kwargs)
        except pyvisa.errors.VisaIOError:
            self._io_failed = True
            self._single_pending = False
            self._ttl_config = None
            raise

    def write_scpi(self, command: str):
        """Unchecked SCPI access for extensions; may change the device state.

        Do not mix custom SCPI changes with active driver workflows.
        Call check_errors() afterwards. Always read responses completely.
        """
        with self._lock:
            log.debug("SCPI >> %s", command)
            self._io("write", command)

    def query_scpi(self, command: str):
        with self._lock:
            log.debug("SCPI >> %s", command)
            result = self._io("query", command).strip()
            log.debug("SCPI << %s", result)
            return result

    def recover(self):
        """Explicitly clear I/O buffers, stop acquisition, and disable WaveGen if used.

        Also aborts a pending DIGitize. An existing acquisition is not accepted
        as a new acquisition. TTL must be configured again afterwards.
        """
        with self._lock:
            if self._inst is None:
                raise ScopeError("Connection is closed.")
            self._io_failed = True
            self._single_pending = False
            self._ttl_config = None
            self._inst.clear()
            self._inst.write(":STOP")
            if self._wavegen_touched:
                self._inst.write(":WGEN:OUTPut OFF")
            self._validate_idn(self._inst.query("*IDN?").strip())
            self._io_failed = False
            self.check_errors()

    @contextmanager
    def _timeout(self, seconds):
        self._check_connection()
        previous = self._inst.timeout
        self._inst.timeout = max(1, math.ceil(seconds * 1000))
        try:
            yield
        finally:
            self._inst.timeout = previous

    def read_errors(self):
        """Read and drain the error queue, querying at most 31 entries."""
        with self._lock:
            errors = []
            for _ in range(31):
                answer = self.query_scpi(":SYSTem:ERRor?")
                try:
                    code, message = answer.split(",", 1)
                    code = int(code)
                except ValueError as exc:
                    raise ScopeError(f"Invalid error response: {answer!r}") from exc
                if code == 0:
                    return errors
                errors.append((code, message.strip().strip('"')))
            raise ScopeError(f"Error queue did not empty: {errors}")

    def check_errors(self):
        errors = self.read_errors()
        if errors:
            raise ScopeError(f"SCPI errors (possibly pre-existing): {errors}")

    def _commands(self, commands):
        for command in commands:
            self.write_scpi(command)
        self.check_errors()

    def configure_channel(self, channel, *, scale_v=None, offset_v=None,
                          probe=None, coupling=None, enabled=None, invert=None,
                          bandwidth_limit=None):
        """Change only supplied settings. scale_v is V/div; probe is e.g. 1 or 10.

        The DSOX1204G has only 1-megohm channel inputs. The 50-ohm WaveGen
        load setting is independent of the channel input impedance.
        """
        ch = _channel(channel)
        values = []
        for key, value, minimum, maximum in (
            ("PROBe", probe, 0.1, 10000), ("SCALe", scale_v, 1e-12, None),
            ("OFFSet", offset_v, None, None),
        ):
            if value is not None:
                values.append((key, f"{_number(value, key, minimum, maximum):.12g}"))
        if coupling is not None:
            values.append(("COUPling", _choice(coupling, ("AC", "DC"), "coupling")))
        for key, value in (("DISPlay", enabled), ("INVert", invert),
                           ("BWLimit", bandwidth_limit)):
            if value is not None:
                values.append((key, _boolean(value)))
        with self._lock:
            self._commands([f":CHANnel{ch}:{key} {value}" for key, value in values])

    def configure_timebase(self, *, scale_s, position_s=0.0, reference="CENTer"):
        """Set MAIN timebase: scale_s in s/div, position_s relative to the trigger."""
        scale = _number(scale_s, "scale_s", 1e-15)
        position = _number(position_s, "position_s")
        ref = _choice(reference, ("LEFT", "CENTER", "RIGHT"), "reference")
        with self._lock:
            self._commands([":TIMebase:MODE MAIN", f":TIMebase:REFerence {ref}",
                            f":TIMebase:SCALe {scale:.12g}",
                            f":TIMebase:POSition {position:.12g}"])

    def configure_edge_trigger(self, channel, *, level_v, slope="POSitive",
                               sweep="NORMal", coupling="DC", noise_reject=False,
                               holdoff_s=None):
        """Set an edge trigger on CH1-CH4; NORMAL prevents automatic triggers."""
        ch = _channel(channel)
        level = _number(level_v, "level_v")
        slope = _choice(slope, ("POSITIVE", "NEGATIVE", "EITHER", "ALTERNATE"), "slope")
        sweep = _choice(sweep, ("NORMAL", "AUTO"), "sweep")
        coupling = _choice(coupling, ("DC", "AC", "LFREJECT"), "coupling")
        noise = _boolean(noise_reject)
        commands = [":TRIGger:MODE EDGE", f":TRIGger:EDGE:SOURce CHANnel{ch}",
                    f":TRIGger:EDGE:LEVel {level:.12g},CHANnel{ch}",
                    f":TRIGger:EDGE:SLOPe {slope}", ":TRIGger:EDGE:REJect OFF",
                    f":TRIGger:EDGE:COUPling {coupling}",
                    f":TRIGger:NREJect {noise}", f":TRIGger:SWEep {sweep}"]
        if holdoff_s is not None:
            holdoff = _number(holdoff_s, "holdoff_s", 60e-9, 10)
            commands.append(f":TRIGger:HOLDoff {holdoff:.12g}")
        with self._lock:
            self._commands(commands)

    def configure_acquisition(self, kind="NORMal", *, averages=16):
        """Select NORMAL, HRESOLUTION, or AVERAGE in non-segmented real-time mode.

        PEAK is unsupported because min/max pairs need their own time axes.
        Use digitize() for AVERAGE; Single is intended for one acquisition.
        """
        kind = _choice(kind, ("NORMAL", "HRESOLUTION", "AVERAGE"), "kind")
        commands = [":ACQuire:MODE RTIMe", f":ACQuire:TYPE {kind}"]
        if kind == "AVERAGE":
            if isinstance(averages, bool) or not isinstance(averages, int) or not 2 <= averages <= 65536:
                raise ValueError("averages: integer from 2 to 65536.")
            commands.append(f":ACQuire:COUNt {averages}")
        with self._lock:
            self._commands(commands)

    def run(self):
        with self._lock:
            self._single_pending = False
            self._commands([":RUN"])

    def stop(self):
        with self._lock:
            self._single_pending = False
            self._commands([":STOP"])

    def operation_status(self):
        value = int(self.query_scpi(":OPERegister:CONDition?"))
        return {"register": value, "running": bool(value & 8),
                "waiting_for_trigger": bool(value & 32)}

    def arm_single(self):
        """Stop, clear stale trigger events, and arm a new acquisition.

        Returns without waiting for the trigger. Then call wait_until_armed(),
        supply the trigger signal if needed, wait_for_single(), and read_waveforms().
        """
        with self._lock:
            if self.query_scpi(":ACQuire:TYPE?") not in ("NORM", "HRES"):
                raise ScopeError("Single acquisition requires NORMAL or HRESOLUTION.")
            if self.query_scpi(":ACQuire:MODE?") != "RTIM":
                raise ScopeError("Single acquisition requires RTIME, not segmented mode.")
            self.stop()
            self._commands([":TRIGger:SWEep NORMal"])
            self.query_scpi(":TER?")  # Clear on read; do not accept stale triggers.
            self.query_scpi(":AER?")
            self._trigger_seen = False
            self.write_scpi(":SINGle")
            self._single_pending = True
            self.check_errors()

    def _wait_single(self, timeout_s, poll_s, armed_only, cancel=None):
        timeout = _number(timeout_s, "timeout_s", 0.001)
        poll = _number(poll_s, "poll_s", 0.001, 1)
        with self._lock:
            if not self._single_pending:
                raise ScopeError("Call arm_single() first.")
            deadline = time.monotonic() + timeout
            try:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("Acquisition cancelled.")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AcquisitionTimeout("No new acquisition or trigger readiness before timeout.")
                    with self._timeout(min(remaining, 1.0)):
                        if armed_only:
                            if int(self.query_scpi(":AER?")):
                                return
                        else:
                            self._trigger_seen |= bool(int(self.query_scpi(":TER?")))
                            status = self.operation_status()
                            if self._trigger_seen and not status["running"]:
                                self._single_pending = False
                                self.check_errors()
                                return
                    time.sleep(min(poll, max(0, deadline - time.monotonic())))
            except BaseException:
                self._single_pending = False
                if not self._io_failed:
                    try:
                        self.stop()
                    except Exception:
                        log.exception("Acquisition stop not confirmed after an error")
                raise

    def wait_until_armed(self, timeout_s: float = 5.0, poll_s: float = 0.01, *, cancel: Event | None = None) -> None:
        """Wait for a fresh AER event; this does not guarantee external signal timing."""
        self._wait_single(timeout_s, poll_s, True, cancel)

    def wait_for_single(self, timeout_s: float = 10.0, poll_s: float = 0.02, *, cancel: Event | None = None) -> None:
        """Require a fresh TER event AND a stopped device; never accept stale data."""
        self._wait_single(timeout_s, poll_s, False, cancel)

    def digitize(self, channels=(1, 2), *, timeout_s=10.0):
        """Blocking acquisition, including averaging; waits for the configured trigger.

        The device buffers further SCPI commands during DIGitize. Do not expect
        a WaveGen start over the same connection while waiting. Use arm_single()
        to separate arming from signal generation. Call recover() after an I/O timeout.
        """
        channels = self._channels(channels)
        timeout = _number(timeout_s, "timeout_s", 0.001)
        with self._lock:
            self._single_pending = False
            self._commands([":TIMebase:MODE MAIN", ":ACQuire:MODE RTIMe",
                            ":TRIGger:SWEep NORMal"])
            sources = ",".join(f"CHANnel{ch}" for ch in channels)
            with self._timeout(timeout):
                self.write_scpi(f":DIGitize {sources}")
                if self.query_scpi("*OPC?") != "1":
                    raise ScopeError("Unexpected OPC response.")
            self.check_errors()

    @staticmethod
    def _channels(channels):
        result = tuple(_channel(ch) for ch in channels)
        if not result or len(result) != len(set(result)):
            raise ValueError("Provide at least one channel, with no duplicates.")
        return result

    def read_waveform(self, channel, **kwargs):
        return self.read_waveforms((channel,), **kwargs)[channel]

    def read_waveforms(self, channels=(1, 2), *, points=2000000, format="WORD",
                       points_mode="AUTO", stop=True):
        """Read waveforms from the same stopped acquisition without acquiring again.

        Supports WORD (default), BYTE, or ASCII. AUTO selects RAW for NORMAL/HRES
        and NORMAL for averaging. points is a request; the actual length comes
        from the preamble. A transfer of two million points is not guaranteed.
        stop=False requires an already stopped device. Enable channels BEFORE
        acquisition; this method does not enable additional channels.
        """
        channels = self._channels(channels)
        fmt = _choice(format, ("WORD", "BYTE", "ASCII"), "format")
        mode = _choice(points_mode, ("AUTO", "RAW", "NORMAL"), "points_mode")
        allowed = (100, 250, 500, 1000, 2000, 5000, 10000, 20000,
                   50000, 100000, 200000, 500000, 1000000, 2000000)
        if isinstance(points, bool) or not isinstance(points, int) or points not in allowed:
            raise ValueError(f"points must be one of: {allowed}")
        _boolean(stop)
        with self._lock:
            if self._single_pending:
                raise ScopeError("New acquisition not yet confirmed: call wait_for_single() first.")
            if stop:
                self.stop()
            if self.operation_status()["running"]:
                raise ScopeError("The device must be stopped for consistent data.")
            kind = self.query_scpi(":ACQuire:TYPE?")
            if kind not in ("NORM", "HRES", "AVER"):
                raise ScopeError("PEAK data contains min/max pairs and cannot be read as a regular waveform.")
            if self.query_scpi(":ACQuire:MODE?") != "RTIM":
                raise ScopeError("This method does not support segmented data.")
            if self.query_scpi(":TIMebase:MODE?") != "MAIN":
                raise ScopeError("Select TIMebase MAIN before acquisition.")
            mode = ("NORMAL" if kind == "AVER" else "RAW") if mode == "AUTO" else mode
            if mode == "RAW" and kind == "AVER":
                raise ScopeError("RAW is unavailable for AVERAGE; use points_mode='NORMAL'.")
            count = min(points, 1000) if mode == "NORMAL" else points
            result = {}
            for ch in channels:
                if self.query_scpi(f":CHANnel{ch}:DISPlay?") != "1":
                    raise ScopeError(f"CH{ch} is not displayed. Enable it before acquisition.")
                self._commands([f":WAVeform:SOURce CHANnel{ch}",
                                f":WAVeform:FORMat {fmt}", ":WAVeform:UNSigned ON",
                                ":WAVeform:BYTeorder LSBFirst",
                                f":WAVeform:POINts:MODE {mode}",
                                f":WAVeform:POINts {count}"])
                fields = self.query_scpi(":WAVeform:PREamble?").split(",")
                if len(fields) != 10:
                    raise ScopeError(f"Invalid preamble: {fields}")
                vals = [float(v) for v in fields]
                if not all(math.isfinite(v) for v in vals):
                    raise ScopeError("Non-finite preamble values.")
                keys = ("format", "type", "points", "count", "x_increment", "x_origin",
                        "x_reference", "y_increment", "y_origin", "y_reference")
                p = dict(zip(keys, vals))
                if p["format"] != {"BYTE": 0, "WORD": 1, "ASCII": 4}[fmt] or p["type"] == 1:
                    raise ScopeError("Unexpected data format or acquisition type.")
                if p["x_increment"] <= 0 or p["points"] < 1 or not p["points"].is_integer():
                    raise ScopeError("Invalid timebase or point count in the preamble.")
                if fmt == "ASCII":
                    raw = np.asarray(self._io("query_ascii_values", ":WAVeform:DATA?",
                                              container=np.array), dtype=float)
                    voltage = raw.copy()  # ASCII values are already in volts.
                    missing = ~np.isfinite(raw) | (np.abs(raw) > 1e30)
                    low = np.zeros(raw.size, dtype=bool)
                    high = low.copy()  # ASCII provides no unambiguous clipping codes.
                else:
                    old_term = self._inst.read_termination
                    self._inst.read_termination = None  # Do not treat 0x0A in binary data as termination.
                    try:
                        raw = np.asarray(self._io(
                            "query_binary_values", ":WAVeform:DATA?",
                            datatype="H" if fmt == "WORD" else "B", is_big_endian=False,
                            container=np.array, header_fmt="ieee", expect_termination=True,
                        ), dtype=np.uint16 if fmt == "WORD" else np.uint8)
                    finally:
                        self._inst.read_termination = old_term
                    voltage = (raw.astype(float) - p["y_reference"]) * p["y_increment"] + p["y_origin"]
                    missing, low = raw == 0, raw == 1
                    high = raw == (65535 if fmt == "WORD" else 255)
                if raw.ndim != 1 or raw.size != int(p["points"]):
                    raise ScopeError(f"Incomplete transfer: {raw.size} instead of {p['points']:.0f} points.")
                voltage[missing] = np.nan
                time_s = (np.arange(raw.size) - p["x_reference"]) * p["x_increment"] + p["x_origin"]
                if np.any(low | high):
                    log.warning("CH%s contains values clipped at the display range limits.", ch)
                p.update(transfer_format=fmt, points_mode=mode, acquisition_type=kind)
                result[ch] = Waveform(ch, time_s, voltage, raw, p, missing, low, high)
            self.check_errors()
            return result

    def wavegen_state(self):
        """Read back settings, not a measurement of the actual output voltage."""
        with self._lock:
            return {
                "output": bool(int(self.query_scpi(":WGEN:OUTPut?"))),
                "function": self.query_scpi(":WGEN:FUNCtion?"),
                "load": self.query_scpi(":WGEN:OUTPut:LOAD?"),
                "polarity": self.query_scpi(":WGEN:OUTPut:POLarity?"),
                "modulation": bool(int(self.query_scpi(":WGEN:MODulation:STATe?"))),
                "noise_percent": float(self.query_scpi(":WGEN:MODulation:NOISe?")),
                "frequency_hz": float(self.query_scpi(":WGEN:FREQuency?")),
                "pulse_width_s": float(self.query_scpi(":WGEN:FUNCtion:PULSe:WIDTh?")),
                "low_v": float(self.query_scpi(":WGEN:VOLTage:LOW?")),
                "high_v": float(self.query_scpi(":WGEN:VOLTage:HIGH?")),
            }

    def configure_ttl(self, *, load, frequency_hz=1.0, pulse_width_s=100e-6,
                      low_v=0.0, high_v=5.0):
        """Configure a periodic WaveGen pulse train, leaving the output OFF.

        load must be 'HIGHZ' or '50OHM': verify the actual connected load first.
        5 V peak-to-peak with a 2.5 V offset gives 0..5 V at the specified load.
        Settings are read back and checked for clamping or rounding.
        This is not a hardware burst or a guaranteed single pulse.
        """
        load = _choice(load, ("HIGHZ", "50OHM"), "load")
        freq = _number(frequency_hz, "frequency_hz", 0.1, 10e6)
        width = _number(pulse_width_s, "pulse_width_s", 20e-9, 1 / freq - 20e-9)
        low = _number(low_v, "low_v", 0, 5)
        high = _number(high_v, "high_v", 0, 5)
        if high <= low:
            raise ValueError("high_v must be greater than low_v.")
        config = dict(frequency_hz=freq, pulse_width_s=width, low_v=low, high_v=high,
                      load="ONEM" if load == "HIGHZ" else "FIFT")
        with self._lock:
            self._ttl_config = None
            self._wavegen_touched = True
            self.wavegen_output(False)  # Confirm OFF before changing settings.
            self._commands([
                ":WGEN:MODulation:STATe OFF",
                ":WGEN:MODulation:NOISe 0", ":WGEN:OUTPut:POLarity NORMal",
                f":WGEN:OUTPut:LOAD {'ONEMeg' if load == 'HIGHZ' else 'FIFTy'}",
                ":WGEN:FUNCtion PULSe", ":WGEN:FREQuency 0.1",
                ":WGEN:FUNCtion:PULSe:WIDTh 2e-8", f":WGEN:FREQuency {freq:.12g}",
                f":WGEN:FUNCtion:PULSe:WIDTh {width:.12g}",
                ":WGEN:VOLTage:OFFSet 0", ":WGEN:VOLTage 0.1",
                f":WGEN:VOLTage {high-low:.12g}",
                f":WGEN:VOLTage:OFFSet {(high+low)/2:.12g}",
            ])
            actual = self.wavegen_state()
            self._verify_ttl(actual, config)
            if actual["output"]:
                raise ScopeError("WaveGen should be OFF after configuration.")
            self.check_errors()
            self._ttl_config = config
            return actual

    @staticmethod
    def _verify_ttl(actual, expected):
        if (actual["function"] != "PULS" or actual["polarity"] != "NORM"
                or actual["modulation"] or actual["noise_percent"] != 0):
            raise ScopeError(f"Unexpected WaveGen configuration: {actual}")
        for key, target in expected.items():
            if isinstance(target, str):
                ok = actual[key] == target
            else:
                # Resolution: frequency 0.1 Hz; pulse width 10 ns; voltage 1 mV.
                abs_tol = {"frequency_hz": 0.051, "pulse_width_s": 5.1e-9,
                           "low_v": 0.0011, "high_v": 0.0011}[key]
                ok = math.isclose(actual[key], target, rel_tol=1e-4, abs_tol=abs_tol)
            if not ok:
                raise ScopeError(f"WaveGen {key}: requested {target}, received {actual[key]}")

    def wavegen_output(self, enabled: bool):
        """True starts a pulse train; False disables output. Not a single-pulse command."""
        value = _boolean(enabled)
        with self._lock:
            if enabled:
                if self._ttl_config is None:
                    raise ScopeError("Complete configure_ttl(load=...) successfully first.")
                self._verify_ttl(self.wavegen_state(), self._ttl_config)
                self.check_errors()
            self._wavegen_touched = True
            try:
                self.write_scpi(f":WGEN:OUTPut {value}")
                if bool(int(self.query_scpi(":WGEN:OUTPut?"))) != enabled:
                    raise ScopeError("WaveGen output state not confirmed.")
                self.check_errors()
            except BaseException:
                if enabled:
                    # Attempt OFF immediately after a failed enable, even with
                    # regular I/O blocked. Do not retry pulse output.
                    try:
                        self._inst.write(":WGEN:OUTPut OFF")
                    except Exception:
                        log.exception("WaveGen shutdown attempt failed; check the device")
                raise

    def timed_ttl_window(self, max_on_s: float, cancel: Event, *, deadline: float | None = None) -> bool:
        """Stop the periodic source at the first fresh scope trigger or gate deadline.

        Preserve the clear-on-read trigger event for wait_for_single(). An
        independent time limit also applies if the capture input sees no edge.
        Host timing cannot guarantee a physical pulse count.
        """
        with self._lock:
            if self._ttl_config is None or not self._single_pending:
                raise ScopeError("Configure TTL and arm a fresh acquisition before gating.")
            state = self.wavegen_state()
            self._verify_ttl(state, self._ttl_config)
            period = 1 / state["frequency_hz"]
            duration = min(_number(max_on_s, "max_on_s"), 0.8 * period)
            if state["output"] or period < 1 or state["pulse_width_s"] >= duration:
                raise ScopeError("Pulse window requires an idle, slow generator and a sub-period gate.")
            self.check_errors()
            if self._trigger_seen or int(self.query_scpi(":TER?")):
                raise ScopeError("Premature trigger; WaveGen was not started.")
            if cancel.is_set():
                raise InterruptedError("Trigger cancelled.")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("Operator arming window expired before WaveGen start.")
            # Finish validation before ON. Bound each I/O during the gate and
            # schedule OFF from before the write, including acknowledgement latency.
            self._wavegen_touched = True
            with self._timeout(min(0.25, 0.05 * period, self.timeout_ms / 1000)):
                start = time.monotonic()
                cutoff = start + duration
                if deadline is not None:
                    cutoff = min(cutoff, deadline)
                if cutoff-start <= state["pulse_width_s"]:
                    raise TimeoutError("Insufficient time left to open the TTL window.")
                try:
                    self.write_scpi(":WGEN:OUTPut 1")
                    if self.query_scpi(":WGEN:OUTPut?") != "1":
                        raise ScopeError("WaveGen ON not confirmed.")
                    while not cancel.is_set() and time.monotonic() < cutoff:
                        if int(self.query_scpi(":TER?")):
                            self._trigger_seen = True
                            cancel.wait(min(state["pulse_width_s"], max(0.0, cutoff-time.monotonic())))
                            break
                        cancel.wait(min(0.025, max(0.0, cutoff-time.monotonic())))
                finally:
                    # Even if the ON acknowledgement was lost and regular I/O
                    # is blocked, issue OFF. A poisoned session cannot verify it.
                    try:
                        self._inst.write(":WGEN:OUTPut OFF")
                        if self._io_failed or self.query_scpi(":WGEN:OUTPut?") != "0":
                            raise ScopeError("WaveGen OFF unconfirmed; inspect the physical output.")
                    except BaseException:
                        self._io_failed = True
                        self._ttl_config = None
                        raise
            if time.monotonic() - start >= period:
                raise ScopeError("Late WaveGen shutdown; pulse count needs inspection.")
            self.check_errors()
            return self._trigger_seen

    def status(self):
        """Read basic status and measurement settings without resetting or changing outputs."""
        with self._lock:
            return dict(
                idn=self.idn, operation=self.operation_status(),
                acquisition=self.query_scpi(":ACQuire:TYPE?"),
                sample_rate_hz=float(self.query_scpi(":ACQuire:SRATe?")),
                timebase_scale_s=float(self.query_scpi(":TIMebase:SCALe?")),
                timebase_position_s=float(self.query_scpi(":TIMebase:POSition?")),
                trigger_setup=self.query_scpi(":TRIGger?"),
                channel_setups={ch: self.query_scpi(f":CHANnel{ch}?") for ch in range(1, 5)},
                wavegen=self.wavegen_state(),
            )
