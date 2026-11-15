"""Keysight DSOX1204G: USB/LAN, SCPI über PyVISA. Python >= 3.10.

Gerätetreiber ohne GUI, Plotting, Dateiexport oder Laser-Messablauf.
Grundlage: Keysight 9018-07747, Rev. 02.12.0000, September 2021.
Alle Größen in SI: s, V, Hz. Kein automatischer Reset oder Generatorstart.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import logging
import math
from threading import RLock
import time

import numpy as np
import pyvisa

log = logging.getLogger(__name__)


class ScopeError(RuntimeError):
    """Gerätezustand, SCPI-Fehler oder unerwartete Antwort."""


class AcquisitionTimeout(TimeoutError):
    """Keine vollständig abgeschlossene neue Aufnahme innerhalb der Wartezeit."""


@dataclass
class Waveform:
    """Eine Kurve; Rohcodes und Übertragungsmetadaten bleiben erhalten."""
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
        raise ValueError(f"{name}: eine endliche Zahl angeben.")
    v = float(value)
    if not math.isfinite(v) or (minimum is not None and v < minimum) or (
        maximum is not None and v > maximum
    ):
        raise ValueError(f"{name}: unzulässiger Wert {value!r}.")
    return v


def _channel(value):
    if isinstance(value, bool) or not isinstance(value, int) or value not in (1, 2, 3, 4):
        raise ValueError("Kanal muss eine ganze Zahl von 1 bis 4 sein.")
    return value


def _choice(value, allowed, name):
    v = str(value).upper()
    if v not in allowed:
        raise ValueError(f"{name}: eine dieser Optionen wählen: {', '.join(allowed)}")
    return v


def _boolean(value):
    if not isinstance(value, bool):
        raise ValueError("True oder False angeben.")
    return int(value)


class KeysightDSOX1204G:
    """Eine explizit gewählte VISA-Ressource, optional ein gemeinsamer Manager.

    Verbindung im Konstruktor; per `with` oder `close()` schließen.
    `*IDN?` muss DSOX1204G melden. Andere Modelle sind bewusst nicht freigegeben.
    Der Lock verhindert verschachtelte Befehle innerhalb dieser Instanz.
    Einen Treiber pro Gerät verwenden; keine parallele Bedienung am Frontpanel.
    """
    def __init__(self, resource: str, *, timeout_ms=5000, visa_backend="",
                 resource_manager=None):
        timeout_ms = _number(timeout_ms, "timeout_ms", 1, 2**31 - 1)
        self._lock = RLock()
        self._owns_rm = resource_manager is None
        self._rm = resource_manager or pyvisa.ResourceManager(visa_backend)
        self._inst = None
        self._io_failed = False
        self._single_pending = False
        self._trigger_seen = False
        self._wavegen_touched = False
        self._ttl_config = None
        try:
            self._inst = self._rm.open_resource(resource)
            self._inst.timeout = int(timeout_ms)
            self._inst.write_termination = "\n"
            self._inst.read_termination = "\n"
            self._inst.chunk_size = 1024 * 1024
            self.resource = resource
            self.idn = self.query_scpi("*IDN?")
            self._validate_idn(self.idn)
        except BaseException:
            if self._inst is not None:
                self._inst.close()
                self._inst = None
            if self._owns_rm:
                self._rm.close()
            raise

    @staticmethod
    def _validate_idn(idn):
        fields = idn.split(",")
        if len(fields) < 4 or fields[1].strip().upper() != "DSOX1204G":
            raise ScopeError(f"DSOX1204G erwartet, erhalten: {idn!r}")

    @staticmethod
    def list_resources(visa_backend=""):
        """Ressourcen auflisten, keine Geräte öffnen oder Ausgänge verändern."""
        rm = pyvisa.ResourceManager(visa_backend)
        try:
            return rm.list_resources()
        finally:
            rm.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise
            log.exception("Zusätzlicher Fehler beim Schließen des Oszilloskops")
        return False

    def close(self):
        """Benutzten WaveGen ausschalten; eine unberührte Ausgabe bleibt unverändert.

        Bei Verbindungsverlust ist OFF nur ein Versuch, keine Abschaltgarantie.
        Ein Fehler wird nach dem Freigeben der Ressourcen weitergegeben.
        """
        with self._lock:
            if self._inst is None:
                return
            inst, self._inst = self._inst, None
            try:
                if self._wavegen_touched:
                    inst.write(":WGEN:OUTPut OFF")
                    if self._io_failed or inst.query(":WGEN:OUTPut?").strip() != "0":
                        raise ScopeError("WaveGen-Abschaltung nicht bestätigt; Gerät prüfen.")
            finally:
                try:
                    inst.close()
                finally:
                    if self._owns_rm:
                        self._rm.close()

    def _check_connection(self):
        if self._inst is None:
            raise ScopeError("Verbindung ist geschlossen.")
        if self._io_failed:
            raise ScopeError("Kommunikation nach I/O-Fehler gesperrt. recover() oder neu verbinden.")

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
        """Ungeprüfter SCPI-Zugriff für Erweiterungen; kann Gerätezustand ändern.

        Eigene SCPI-Änderungen nicht mit laufenden Treiberabläufen mischen.
        Danach check_errors() aufrufen. Rückantworten stets vollständig lesen.
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
        """Explizit I/O-Puffer leeren, Aufnahme stoppen, benutzten WaveGen abschalten.

        Bricht auch ein wartendes DIGitize ab. Vorhandene Aufnahme wird nicht als
        neue Aufnahme anerkannt. TTL muss anschließend neu konfiguriert werden.
        """
        with self._lock:
            if self._inst is None:
                raise ScopeError("Verbindung ist geschlossen.")
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
        """Fehlerqueue auslesen und dabei leeren; maximal 31 Einträge."""
        with self._lock:
            errors = []
            for _ in range(31):
                answer = self.query_scpi(":SYSTem:ERRor?")
                try:
                    code, message = answer.split(",", 1)
                    code = int(code)
                except ValueError as exc:
                    raise ScopeError(f"Ungültige Fehlerantwort: {answer!r}") from exc
                if code == 0:
                    return errors
                errors.append((code, message.strip().strip('"')))
            raise ScopeError(f"Fehlerqueue wird nicht leer: {errors}")

    def check_errors(self):
        errors = self.read_errors()
        if errors:
            raise ScopeError(f"SCPI-Fehler (ggf. bereits vorher vorhanden): {errors}")

    def _commands(self, commands):
        for command in commands:
            self.write_scpi(command)
        self.check_errors()

    def configure_channel(self, channel, *, scale_v=None, offset_v=None,
                          probe=None, coupling=None, enabled=None, invert=None,
                          bandwidth_limit=None):
        """Nur angegebene Werte ändern. scale_v ist V/div, probe z.B. 1 oder 10.

        DSOX1204G besitzt ausschließlich 1-MOhm-Kanaleingänge. Die 50-Ohm-
        WaveGen-Last ist eine davon unabhängige Einstellung.
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
        """MAIN-Zeitbasis; scale_s in s/div, position_s relativ zum Trigger."""
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
        """OptoSync auf CH1–CH4; NORMAL vermeidet künstliche Auto-Trigger."""
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
        """NORMAL, HRESOLUTION oder AVERAGE; Echtzeitmodus ohne Segmente.

        PEAK wird nicht angeboten, da Min/Max-Paare eigene Zeitachsen benötigen.
        AVERAGE mit digitize() aufnehmen; Single ist für einen Trigger gedacht.
        """
        kind = _choice(kind, ("NORMAL", "HRESOLUTION", "AVERAGE"), "kind")
        commands = [":ACQuire:MODE RTIMe", f":ACQuire:TYPE {kind}"]
        if kind == "AVERAGE":
            if isinstance(averages, bool) or not isinstance(averages, int) or not 2 <= averages <= 65536:
                raise ValueError("averages: ganze Zahl von 2 bis 65536.")
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
        """Stoppen, alte Triggerereignisse löschen, eine neue Aufnahme starten.

        Kehrt ohne Warten auf den Schuss zurück. Danach wait_until_armed(),
        ggf. externen Schuss auslösen, wait_for_single() und read_waveforms().
        """
        with self._lock:
            if self.query_scpi(":ACQuire:TYPE?") not in ("NORM", "HRES"):
                raise ScopeError("Single-Workflow benötigt NORMAL oder HRESOLUTION.")
            if self.query_scpi(":ACQuire:MODE?") != "RTIM":
                raise ScopeError("Single-Workflow benötigt RTIME, keine Segmente.")
            self.stop()
            self._commands([":TRIGger:SWEep NORMal"])
            self.query_scpi(":TER?")  # clear-on-read, keine alten Trigger akzeptieren
            self.query_scpi(":AER?")
            self._trigger_seen = False
            self.write_scpi(":SINGle")
            self._single_pending = True
            self.check_errors()

    def _wait_single(self, timeout_s, poll_s, armed_only):
        timeout = _number(timeout_s, "timeout_s", 0.001)
        poll = _number(poll_s, "poll_s", 0.001, 1)
        with self._lock:
            if not self._single_pending:
                raise ScopeError("Zuerst arm_single() aufrufen.")
            deadline = time.monotonic() + timeout
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise AcquisitionTimeout("Keine neue Aufnahme/Triggerbereitschaft innerhalb der Wartezeit.")
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
                        log.exception("Aufnahme nach Fehler nicht bestätigt gestoppt")
                raise

    def wait_until_armed(self, timeout_s=5.0, poll_s=0.01):
        """Wartet auf frisches AER-Ereignis. Kein Garantiezeitpunkt für externes Signal."""
        self._wait_single(timeout_s, poll_s, True)

    def wait_for_single(self, timeout_s=10.0, poll_s=0.02):
        """Erfordert frisches TER-Ereignis UND gestopptes Gerät; keine alten Daten."""
        self._wait_single(timeout_s, poll_s, False)

    def digitize(self, channels=(1, 2), *, timeout_s=10.0):
        """Blockierende Aufnahme, auch für Mittelung; wartet auf externe Trigger.

        Während DIGitize puffert das Gerät weitere SCPI-Befehle. Hier deshalb
        KEINEN WaveGen-Start über dieselbe Verbindung erwarten. Für den getrennten
        Arm/Schuss-Ablauf arm_single() verwenden. Nach I/O-Timeout recover().
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
                    raise ScopeError("Unerwartete OPC-Antwort.")
            self.check_errors()

    @staticmethod
    def _channels(channels):
        result = tuple(_channel(ch) for ch in channels)
        if not result or len(result) != len(set(result)):
            raise ValueError("Mindestens einen Kanal, jeden Kanal nur einmal angeben.")
        return result

    def read_waveform(self, channel, **kwargs):
        return self.read_waveforms((channel,), **kwargs)[channel]

    def read_waveforms(self, channels=(1, 2), *, points=2000000, format="WORD",
                       points_mode="AUTO", stop=True):
        """Kurven aus derselben gestoppten Aufnahme; keine erneute Aufnahme.

        WORD (Standard), BYTE oder ASCII. AUTO wählt RAW für NORMAL/HRES und
        NORMAL für Mittelung. points ist eine Anforderung; tatsächliche Länge
        kommt aus der Preamble. Keine Garantie für 2 Mio. übertragene Punkte.
        stop=False verlangt ein bereits gestopptes Gerät. Aktivierte Kanäle
        VOR der Aufnahme wählen; diese Methode schaltet keine Kanäle hinzu.
        """
        channels = self._channels(channels)
        fmt = _choice(format, ("WORD", "BYTE", "ASCII"), "format")
        mode = _choice(points_mode, ("AUTO", "RAW", "NORMAL"), "points_mode")
        allowed = (100, 250, 500, 1000, 2000, 5000, 10000, 20000,
                   50000, 100000, 200000, 500000, 1000000, 2000000)
        if isinstance(points, bool) or not isinstance(points, int) or points not in allowed:
            raise ValueError(f"points muss einer dieser Werte sein: {allowed}")
        _boolean(stop)
        with self._lock:
            if self._single_pending:
                raise ScopeError("Neue Aufnahme noch nicht bestätigt: zuerst wait_for_single().")
            if stop:
                self.stop()
            if self.operation_status()["running"]:
                raise ScopeError("Für konsistente Daten muss das Gerät gestoppt sein.")
            kind = self.query_scpi(":ACQuire:TYPE?")
            if kind not in ("NORM", "HRES", "AVER"):
                raise ScopeError("PEAK-Daten werden wegen ihrer Min/Max-Paare nicht als normale Kurve gelesen.")
            if self.query_scpi(":ACQuire:MODE?") != "RTIM":
                raise ScopeError("Segmentierte Daten werden von dieser Methode nicht unterstützt.")
            if self.query_scpi(":TIMebase:MODE?") != "MAIN":
                raise ScopeError("Vor der Aufnahme TIMebase MAIN wählen.")
            mode = ("NORMAL" if kind == "AVER" else "RAW") if mode == "AUTO" else mode
            if mode == "RAW" and kind == "AVER":
                raise ScopeError("RAW ist bei AVERAGE nicht verfügbar; points_mode='NORMAL' verwenden.")
            count = min(points, 1000) if mode == "NORMAL" else points
            result = {}
            for ch in channels:
                if self.query_scpi(f":CHANnel{ch}:DISPlay?") != "1":
                    raise ScopeError(f"CH{ch} ist nicht eingeblendet. Vor der Aufnahme aktivieren.")
                self._commands([f":WAVeform:SOURce CHANnel{ch}",
                                f":WAVeform:FORMat {fmt}", ":WAVeform:UNSigned ON",
                                ":WAVeform:BYTeorder LSBFirst",
                                f":WAVeform:POINts:MODE {mode}",
                                f":WAVeform:POINts {count}"])
                fields = self.query_scpi(":WAVeform:PREamble?").split(",")
                if len(fields) != 10:
                    raise ScopeError(f"Ungültige Preamble: {fields}")
                vals = [float(v) for v in fields]
                if not all(math.isfinite(v) for v in vals):
                    raise ScopeError("Nicht-endliche Preamble-Werte.")
                keys = ("format", "type", "points", "count", "x_increment", "x_origin",
                        "x_reference", "y_increment", "y_origin", "y_reference")
                p = dict(zip(keys, vals))
                if p["format"] != {"BYTE": 0, "WORD": 1, "ASCII": 4}[fmt] or p["type"] == 1:
                    raise ScopeError("Datenformat oder Acquisition-Typ unerwartet.")
                if p["x_increment"] <= 0 or p["points"] < 1 or not p["points"].is_integer():
                    raise ScopeError("Ungültige Zeitbasis oder Punktzahl in der Preamble.")
                if fmt == "ASCII":
                    raw = np.asarray(self._io("query_ascii_values", ":WAVeform:DATA?",
                                              container=np.array), dtype=float)
                    voltage = raw.copy()  # ASCII ist bereits in Volt!
                    missing = ~np.isfinite(raw) | (np.abs(raw) > 1e30)
                    low = np.zeros(raw.size, dtype=bool)
                    high = low.copy()  # ASCII liefert keine eindeutigen Clipping-Codes
                else:
                    old_term = self._inst.read_termination
                    self._inst.read_termination = None  # 0x0A im Binärpayload nicht als Ende deuten
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
                    raise ScopeError(f"Unvollständige Übertragung: {raw.size} statt {p['points']:.0f} Punkte.")
                voltage[missing] = np.nan
                time_s = (np.arange(raw.size) - p["x_reference"]) * p["x_increment"] + p["x_origin"]
                if np.any(low | high):
                    log.warning("CH%s enthält am Anzeigebereich abgeschnittene Werte.", ch)
                p.update(transfer_format=fmt, points_mode=mode, acquisition_type=kind)
                result[ch] = Waveform(ch, time_s, voltage, raw, p, missing, low, high)
            self.check_errors()
            return result

    def wavegen_state(self):
        """Zurückgelesene Einstellungen, keine Messung der realen Ausgangsspannung."""
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
        """Periodischen Pulse-WaveGen konfigurieren, Ausgang bleibt AUS.

        load zwingend 'HIGHZ' oder '50OHM': erst reale Laserlast klären!
        5 Vss plus 2.5 V Offset ergibt 0..5 V bei korrekt angegebener Last.
        Werte werden zurückgelesen; Begrenzung/Rundung wird geprüft.
        Dies ist kein Hardware-Burst und kein garantierter Einzelpuls.
        """
        load = _choice(load, ("HIGHZ", "50OHM"), "load")
        freq = _number(frequency_hz, "frequency_hz", 0.1, 10e6)
        width = _number(pulse_width_s, "pulse_width_s", 20e-9, 1 / freq - 20e-9)
        low = _number(low_v, "low_v", 0, 5)
        high = _number(high_v, "high_v", 0, 5)
        if high <= low:
            raise ValueError("high_v muss größer als low_v sein.")
        config = dict(frequency_hz=freq, pulse_width_s=width, low_v=low, high_v=high,
                      load="ONEM" if load == "HIGHZ" else "FIFT")
        with self._lock:
            self._ttl_config = None
            self._wavegen_touched = True
            self.wavegen_output(False)  # OFF bestätigen, bevor Parameter geändert werden
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
                raise ScopeError("WaveGen sollte nach Konfiguration AUS sein.")
            self.check_errors()
            self._ttl_config = config
            return actual

    @staticmethod
    def _verify_ttl(actual, expected):
        if (actual["function"] != "PULS" or actual["polarity"] != "NORM"
                or actual["modulation"] or actual["noise_percent"] != 0):
            raise ScopeError(f"WaveGen-Konfiguration unerwartet: {actual}")
        for key, target in expected.items():
            if isinstance(target, str):
                ok = actual[key] == target
            else:
                # Frequenzauflösung 0.1 Hz; Pulsbreite 10 ns; Pegel 1 mV.
                abs_tol = {"frequency_hz": 0.051, "pulse_width_s": 5.1e-9,
                           "low_v": 0.0011, "high_v": 0.0011}[key]
                ok = math.isclose(actual[key], target, rel_tol=1e-4, abs_tol=abs_tol)
            if not ok:
                raise ScopeError(f"WaveGen {key}: angefordert {target}, erhalten {actual[key]}")

    def wavegen_output(self, enabled: bool):
        """True startet eine Pulsfolge; False schaltet aus. Kein Einzelpulsbefehl."""
        value = _boolean(enabled)
        with self._lock:
            if enabled:
                if self._ttl_config is None:
                    raise ScopeError("Zuerst configure_ttl(load=...) erfolgreich ausführen.")
                self._verify_ttl(self.wavegen_state(), self._ttl_config)
                self.check_errors()
            self._wavegen_touched = True
            try:
                self.write_scpi(f":WGEN:OUTPut {value}")
                if bool(int(self.query_scpi(":WGEN:OUTPut?"))) != enabled:
                    raise ScopeError("WaveGen-Ausgangszustand nicht bestätigt.")
                self.check_errors()
            except BaseException:
                if enabled:
                    # Nach fehlgeschlagenem Einschalten sofort OFF versuchen,
                    # auch bei gesperrtem I/O. Keine erneute Trigger-Ausgabe.
                    try:
                        self._inst.write(":WGEN:OUTPut OFF")
                    except Exception:
                        log.exception("WaveGen-Abschaltversuch fehlgeschlagen; Gerät prüfen")
                raise

    def status(self):
        """Grundzustand und Messparameter lesen, ohne Reset oder Ausgabeänderung."""
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
