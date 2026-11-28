"""Read-only Edwards TIC monitoring over point-to-point RS232."""
from __future__ import annotations

import logging
import math
import re
from threading import RLock
import time
from types import TracebackType

import serial

BAUDRATE = 9600
TERMINATOR = b"\r"
log = logging.getLogger(__name__)
_FRAME = re.compile(rb"([=*])V([0-9]{1,5}) ([\x20-\x7e]+)\r")
_FRAME_LIMIT = 256
_POLL_S = 0.005
_READ_OBJECTS = frozenset({802, 808, 852, 859, 904, 905, 906, 907, 908, 909, 910,
                           911, 912, 913, 914, 915, 919, 920, 924})


def _finite_value(field: str) -> float:
    value = float(field)
    if not math.isfinite(value):
        raise ValueError("Expected a finite reading.")
    return value


# ==============================================================================
# custom exceptions
# ==============================================================================
class EdwardsTicError(Exception):
    """Base exception for all Edwards TIC and nEXT 300 driver errors."""
    pass


class EdwardsTicConnectionError(EdwardsTicError):
    """Raised when the serial connection cannot be established or is closed."""
    pass


class EdwardsTicTimeoutError(EdwardsTicError):
    """Raised when a serial read or write operation times out."""
    pass


class EdwardsTicProtocolError(EdwardsTicError):
    """Raised when the response frame is malformed or cannot be parsed."""
    pass


class EdwardsTicCommandError(EdwardsTicError):
    """Raised when the device replies with an error response (*V... )."""

    def __init__(self, object_id: int, error_code: int, message: str):
        self.object_id = object_id
        self.error_code = error_code
        self.message = message
        super().__init__(
            f"Device command error for object {object_id}: "
            f"code {error_code} ({message})"
        )


class EdwardsTicPumpFaultError(EdwardsTicError):
    """
    Raised when the Edwards nEXT 300 turbomolecular pump or TIC controller
    reports an active fault, alarm priority, or fault braking state.
    """

    def __init__(
        self,
        message: str,
        object_id: int | None = None,
        alert_id: int = 0,
        priority: int = 0,
        status_word_hex: str | None = None,
        active_faults: dict[int, str] | None = None,
    ):
        self.object_id = object_id
        self.alert_id = alert_id
        self.priority = priority
        self.status_word_hex = status_word_hex
        self.active_faults = active_faults or {}
        super().__init__(message)


# ==============================================================================
# Edwards TIC & nEXT 300 Driver Class (READ-only)
# ==============================================================================
class EdwardsTIC:
    """
    Serial driver for the Edwards Turbo and Instrument Controller (TIC)
    and the Edwards nEXT 300 turbomolecular pump (READ-only commands).

    Supported READ queries (?V):
        - 802: nXDS motor frequency and diagnostic registers [A735-01-860]
        - 808: nXDS pump and controller temperatures [A735-01-860]
        - 904: Turbo pump state (?V904)            [D397-30-880]
        - 905: Turbo speed in % (?V905)            [D397-30-880]
        - 906: Turbo power in W (?V906)            [D397-30-880]
        - 907: Turbo normal state (?V907)          [D397-30-880]
        - 908: Turbo standby state (?V908)         [D397-30-880]
        - 909: Turbo cycle time in hours (?V909)   [D397-30-880]
        - 910: Backing pump state (?V910)          [D397-30-880]
        - 911: Backing speed in % (?V911)          [D397-30-880]
        - 912: Backing power in W (?V912)          [D397-30-880]
        - 913: Gauge 1 pressure in Pa (?V913)      [D397-30-880]
        - 914: Gauge 2 pressure in Pa (?V914)      [D397-30-880]
        - 915: Gauge 3 pressure in Pa (?V915)      [D397-30-880]
        - 919: Power supply temperature (?V919)    [D397-30-880]
        - 920: TIC internal temperature (?V920)    [D397-30-880]
        - 924: External air cooler state (?V924)   [D397-30-880]
        - 852: nEXT 300 speed (RPS) & status word [B800-00-880]
        - 859: nEXT 300 motor & controller temps  [B800-00-880]
    """

    # TIC temperature offset (D397-30-880 Table 1: water freezes at 274 -> T_C = raw - 274.0)
    TIC_TEMP_OFFSET = 274.0

    # nEXT 300 specific nominal full rotational speed (B800-00-880)
    NEXT300D_NOMINAL_SPEED_RPS = 1000  # 1000 Hz (RPS) = 60,000 RPM

    # --------------------------------------------------------------------------
    # Object IDs (D397-30-880 Table 1 & B800-00-880)
    # --------------------------------------------------------------------------
    OBJ_TURBO_PUMP = 904
    OBJ_TURBO_SPEED = 905
    OBJ_TURBO_POWER = 906
    OBJ_TURBO_NORMAL = 907
    OBJ_TURBO_STANDBY = 908
    OBJ_TURBO_CYCLE_TIME = 909
    OBJ_BACKING_PUMP = 910
    OBJ_BACKING_SPEED = 911
    OBJ_BACKING_POWER = 912

    GAUGE_OBJECTS = {
        1: 913,
        2: 914,
        3: 915,
    }

    OBJ_PS_TEMPERATURE = 919
    OBJ_INTERNAL_TEMPERATURE = 920
    OBJ_AIR_COOLER = 924

    OBJ_NEXT_SPEED_STATUS = 852
    OBJ_NEXT_TEMPERATURES = 859

    # --------------------------------------------------------------------------
    # Unit codes (D397-30-880 Section 1.7.4)
    # --------------------------------------------------------------------------
    UNIT_CODES = {
        59: "Pa",     # Pressure in Pascals
        66: "V",      # Volts
        81: "%",      # Percent
    }

    # --------------------------------------------------------------------------
    # Priority codes (D397-30-880 Section 1.7.2)
    # --------------------------------------------------------------------------
    PRIORITY_CODES = {
        0: "OK",
        1: "Warning",
        2: "Alarm",
        3: "Alarm",
    }

    # --------------------------------------------------------------------------
    # General State codes 0-4 (D397-30-880 Section 1.7.6)
    # Used for the backing pump indication, air cooler, turbo normal and standby.
    # --------------------------------------------------------------------------
    GENERAL_STATES = {
        0: "Off State",
        1: "Off Going On State",
        2: "On Going Off Shutdown State",
        3: "On Going Off Normal State",
        4: "On State",
    }

    # Active gauge states, D397-30-880 section 1.7.7.
    GAUGE_STATES = {
        0: "Not connected", 1: "Connected", 2: "New gauge ID", 3: "Gauge change",
        4: "In alert", 5: "Off", 6: "Striking", 7: "Initialising", 8: "Calibrating",
        9: "Zeroing", 10: "Degassing", 11: "On", 12: "Inhibited",
    }

    TURBO_NORMAL_STATES = {
        0: "No",
        1: "Off Going On State",
        2: "On Going Off Shutdown State",
        3: "On Going Off Normal State",
        4: "Yes",
    }

    TURBO_STANDBY_STATES = {
        0: "Not in standby",
        1: "Off Going On State",
        2: "On Going Off Shutdown State",
        3: "On Going Off Normal State",
        4: "In standby",
    }

    # --------------------------------------------------------------------------
    # Pump State codes 0-7 (D397-30-880 Section 1.7.8)
    # Used for Turbo Pump (904) and Turbo Cycle Time (909)
    # --------------------------------------------------------------------------
    PUMP_STATES = {
        0: "Stopped",
        1: "Starting Delay",
        2: "Stopping Short Delay",
        3: "Stopping Normal Delay",
        4: "Running",
        5: "Accelerating",
        6: "Fault Braking",
        7: "Braking",
    }

    # --------------------------------------------------------------------------
    # Serial command error codes (*V<id> <err>, D397-30-880 Table 3, p. 14)
    # --------------------------------------------------------------------------
    ERROR_CODES = {
        0: "No error",
        1: "Invalid command for Object ID",
        2: "Invalid query/command",
        3: "Missing parameter",
        4: "Parameter out of range",
        5: "Invalid command in current state",
        6: "Data checksum error",
        7: "EEPROM read or write error",
        8: "Operation took too long",
        9: "Invalid config ID",
    }

    # --------------------------------------------------------------------------
    # TIC Alert IDs (D397-30-880 Section 1.7.3, pp. 15-16)
    # --------------------------------------------------------------------------
    ALERT_CODES = {
        0: "No Alert",
        1: "ADC Fault",
        2: "ADC Not Ready",
        3: "Over Range",
        4: "Under Range",
        5: "ADC Invalid",
        6: "No Gauge",
        7: "Unknown",
        8: "Not Supported",
        9: "New ID",
        10: "Over Range",
        11: "Under Range",
        12: "Over Range",
        13: "Ion Em Timeout",
        14: "Not Struck",
        15: "Filament Fail",
        16: "Mag Fail",
        17: "Striker Fail",
        18: "Not Struck",
        19: "Filament Fail",
        20: "Cal Error",
        21: "Initialising",
        22: "Emission Error",
        23: "Over Pressure",
        24: "ASG Cant Zero",
        25: "RampUp Timeout",
        26: "Droop Timeout",
        27: "Run Hours High",
        28: "SC Interlock",
        29: "ID Volts Error",
        30: "Serial ID Fail",
        31: "Upload Active",
        32: "DX / nEXT Fault",
        33: "Temp Alert",
        34: "SYSI Inhibit",
        35: "Ext Inhibit",
        36: "Temp Inhibit",
        37: "No Reading",
        38: "No Message",
        39: "NOV Failure",
        40: "Upload Timeout",
        41: "Download Failed",
        42: "No Tube",
        43: "Use Gauges 4-6",
        44: "Degas Inhibited",
        45: "IGC Inhibited",
        46: "Brownout/Short",
        47: "Service due",
    }

    # --------------------------------------------------------------------------
    # Edwards nEXT 300 System Status Word Bits (?V852, B800-00-880)
    # --------------------------------------------------------------------------
    NEXT300D_STATUS_BITS = {
        0: "Fail status condition active",
        1: "Below stopped speed",
        2: "Above normal speed",
        3: "Vent valve closed (energised)",
        4: "Start command active",
        5: "Serial enable active",
        6: "Standby speed active",
        7: "Above 50% full rotational speed",
        8: "Parallel control mode",
        9: "Serial control mode",
        10: "Invalid controller software (internal software mismatch)",
        11: "Controller upload incomplete (calibration/configuration failed)",
        12: "Timer expired (failed to reach or maintain 50% full speed in time)",
        13: "Hardware trip activated (overspeed or overcurrent trip)",
        14: "Thermistor error (internal temperature sensor disconnected or damaged)",
        15: "Serial enable inactive (interlock dropped after serial Start command)",
    }

    # Subset of nEXT 300 status bits that represent fault conditions
    NEXT300D_FAULT_BITS = {
        0: "Fail status condition active",
        10: "Invalid controller software (internal software mismatch)",
        11: "Controller upload incomplete (calibration/configuration failed)",
        12: "Timer expired (failed to reach or maintain 50% full speed in time)",
        13: "Hardware trip activated (overspeed or overcurrent trip)",
        14: "Thermistor error (internal temperature sensor disconnected or damaged)",
        15: "Serial enable inactive (interlock dropped after serial Start command)",
    }

    # ==========================================================================
    # initialization & connection management
    # ==========================================================================
    def __init__(self, port: str, *, timeout: float = 1.0, write_timeout: float = 1.0) -> None:
        if not isinstance(port, str) or not port.strip():
            raise ValueError("Expected a serial port name.")
        for delay in (timeout, write_timeout):
            if isinstance(delay, bool) or not math.isfinite(delay) or delay <= 0:
                raise ValueError("Timeouts must be positive and finite.")
        self.port = port
        self.timeout = timeout
        self.write_timeout = write_timeout
        self._serial: serial.Serial | None = None
        self._pending_queries: list[int] = []
        self._lock = RLock()

    def connect(self) -> EdwardsTIC:
        """Open the PC serial port without sending a controller command."""
        with self._lock:
            if not self.is_connected:
                try:
                    self._serial = serial.Serial(
                        port=self.port, baudrate=BAUDRATE, bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                        # Nonblocking reads let the entire reply share one monotonic deadline.
                        timeout=0, write_timeout=self.write_timeout,
                        xonxoff=False, rtscts=False, dsrdtr=False,
                    )
                except serial.SerialException as exc:
                    raise EdwardsTicConnectionError(f"Cannot open {self.port}: {exc}") from exc
                self._pending_queries.clear()
                log.info("TIC connected on %s; reply timeout %.3f s", self.port, self.timeout)
            return self

    def close(self) -> None:
        """Release only the serial port."""
        with self._lock:
            if self._serial is not None:
                self._serial.close()
                self._serial = None
                log.info("TIC disconnected from %s", self.port)

    def __enter__(self) -> EdwardsTIC:
        return self.connect()

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        try:
            self.close()
        except Exception:
            if exc is None:
                raise
            logging.getLogger(__name__).exception("Could not close TIC port %s", self.port)

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._serial is not None and self._serial.is_open

    def query_value(self, object_id: int) -> str:
        """Read one supported telemetry object while holding exclusive ownership of the stream."""
        if type(object_id) is not int or object_id not in _READ_OBJECTS:
            raise ValueError("Expected a supported integer telemetry object ID.")
        with self._lock:
            port = self._serial
            if port is None or not port.is_open:
                raise EdwardsTicConnectionError("Serial connection is not open.")
            try:
                if self._pending_queries or port.in_waiting:
                    self._drain_input(port)
                if object_id in self._pending_queries:
                    self._synchronize_for(port, object_id)
                self._pending_queries.append(object_id)
                try:
                    value = self._exchange(port, object_id)
                except EdwardsTicCommandError:
                    self._pending_queries.clear()  # A complete, correlated controller response.
                    raise
                self._pending_queries.clear()
                return value
            except serial.SerialTimeoutException as exc:
                raise EdwardsTicTimeoutError(f"Timeout querying {object_id}.") from exc
            except serial.SerialException as exc:
                raise EdwardsTicConnectionError(f"Communication failed on {self.port}.") from exc

    def _synchronize_for(self, port: serial.Serial, object_id: int) -> None:
        # Replies have no sequence number. A reply to a different, later query
        # establishes that an older reply for object_id can no longer be in flight.
        marker = self._pending_queries[-1]
        if marker == object_id:
            candidates = (920, 919, *sorted(_READ_OBJECTS))
            marker = next((i for i in candidates if i not in self._pending_queries), None)
            if marker is None:
                raise EdwardsTicProtocolError(f"{self.port}: TIC replies lost synchronization; reconnect the port.")
            self._pending_queries.append(marker)
        log.info("%s resynchronizing object %d with read-only ?V%d", self.port, object_id, marker)
        try:
            self._exchange(port, marker)
        except EdwardsTicCommandError as exc:
            # Even a controller rejection identifies the end of this exchange.
            log.info("%s synchronization reply: %s", self.port, exc)
        self._pending_queries[:] = [marker]  # A repeated marker may still have a reply in flight.

    def _drain_input(self, port: serial.Serial) -> None:
        """After a lost exchange, consume late bytes before sending the next query."""
        quiet_s = min(0.5, self.timeout)  # Edwards' suggested master timeout, §1.4.
        now = time.monotonic()
        quiet_until, deadline = now + quiet_s, now + self.timeout + quiet_s
        while now < deadline:
            waiting = port.in_waiting
            if waiting:
                raw = port.read(min(waiting, _FRAME_LIMIT))
                log.warning("%s discarded buffered RX; pending queries %s: %r", self.port, self._pending_queries, raw)
                quiet_until = time.monotonic() + quiet_s
            elif now >= quiet_until:
                return
            else:
                time.sleep(min(_POLL_S, quiet_until-now))
            now = time.monotonic()
        raise EdwardsTicProtocolError(f"{self.port}: no quiet interval while resynchronizing TIC input.")

    def _exchange(self, port: serial.Serial, object_id: int) -> str:
        frame = f"?V{object_id}".encode("ascii") + TERMINATOR
        log.debug("%s TX %r", self.port, frame)
        if port.write(frame) != len(frame):
            raise EdwardsTicTimeoutError(f"Incomplete query for object {object_id}.")
        start = time.monotonic()
        deadline = start + self.timeout
        buffer = bytearray()
        last_unexpected = b""
        while (remaining := deadline - time.monotonic()) > 0:
            waiting = port.in_waiting
            if not waiting:
                time.sleep(min(_POLL_S, remaining))
                continue
            buffer.extend(port.read(min(waiting, _FRAME_LIMIT)))
            while (end := buffer.find(TERMINATOR)) >= 0:
                raw = bytes(buffer[:end + 1])
                del buffer[:end + 1]
                elapsed = (time.monotonic() - start) * 1000
                log.debug("%s RX for %d after %.1f ms: %r", self.port, object_id, elapsed, raw)
                if len(raw) > _FRAME_LIMIT:
                    raise EdwardsTicProtocolError(f"Oversized reply for object {object_id}: {raw!r}")
                match = _FRAME.fullmatch(raw)
                if not match or int(match[2]) != object_id:
                    last_unexpected = raw
                    log.warning("%s discarded RX while waiting for object %d: %r", self.port, object_id, raw)
                    continue
                if buffer:
                    log.warning("%s discarded trailing RX after object %d: %r", self.port, object_id, bytes(buffer))
                payload = match[3].decode("ascii")
                if match[1] == b"*":
                    if not re.fullmatch(r"[0-9]{1,2}", payload):
                        raise EdwardsTicProtocolError(f"Malformed controller error: {raw!r}")
                    code = int(payload)
                    raise EdwardsTicCommandError(object_id, code, self.ERROR_CODES.get(code, "Unknown error"))
                return payload
            if len(buffer) >= _FRAME_LIMIT:
                raise EdwardsTicProtocolError(f"Unterminated reply for object {object_id}: {bytes(buffer)!r}")
        if last_unexpected and not buffer:
            raise EdwardsTicProtocolError(f"No matching reply for object {object_id} within {self.timeout:g} s; "
                                         f"last unexpected frame: {last_unexpected!r}")
        raise EdwardsTicTimeoutError(f"Timeout reading object {object_id} after {self.timeout:g} s; "
                                     f"partial reply: {bytes(buffer)!r}")

    # ==========================================================================
    # internal helper methods for TIC & nEXT payload parsing and fault checking
    # ==========================================================================
    def _check_tic_fault(
        self,
        object_id: int,
        alert_id: int,
        priority: int,
        state_code: int | None = None,
        is_pump_state: bool = False,
    ) -> None:
        """
        Check TIC alert ID, priority, and pump state for active faults and raise
        EdwardsTicPumpFaultError if an error condition is present.
        """
        alert_text = self.ALERT_CODES.get(alert_id, f"Unknown Alert ({alert_id})")
        priority_text = self.PRIORITY_CODES.get(
            priority, f"Unknown Priority ({priority})"
        )

        # Pump state 6 is 'Fault Braking' (D397-30-880 Section 1.7.8)
        if is_pump_state and state_code == 6:
            raise EdwardsTicPumpFaultError(
                message=(
                    f"Pump fault braking active on object {object_id}: "
                    f"State=6 (Fault Braking), Alert={alert_id} ({alert_text}), "
                    f"Priority={priority} ({priority_text})"
                ),
                object_id=object_id,
                alert_id=alert_id,
                priority=priority,
            )

        # Any non-zero alert ID or Alarm priority (>= 2) indicates an alert/fault
        if alert_id != 0 or priority >= 2:
            raise EdwardsTicPumpFaultError(
                message=(
                    f"TIC alert/fault reported on object {object_id}: "
                    f"Alert={alert_id} ({alert_text}), "
                    f"Priority={priority} ({priority_text})"
                ),
                object_id=object_id,
                alert_id=alert_id,
                priority=priority,
            )

    def _parse_tic_state_payload(
        self,
        object_id: int,
        payload: str,
        state_map: dict[int, str],
        is_pump_state: bool = False,
        raise_on_fault: bool = False,
    ) -> dict:
        """
        Parse a standard TIC state payload: '<state>;<alert_id>;<priority>'.
        Used by objects 904, 907, 908, 910, 924.
        """
        fields = payload.split(";")
        if len(fields) != 3:
            raise EdwardsTicProtocolError(
                f"Expected 3 fields in state reply for object {object_id}, "
                f"got {len(fields)}: '{payload}'"
            )

        try:
            state_code = int(fields[0])
            alert_id = int(fields[1])
            priority = int(fields[2])
        except ValueError as exc:
            raise EdwardsTicProtocolError(
                f"Could not parse state values from payload '{payload}'"
            ) from exc

        if raise_on_fault:
            self._check_tic_fault(
                object_id=object_id,
                alert_id=alert_id,
                priority=priority,
                state_code=state_code,
                is_pump_state=is_pump_state,
            )

        return {
            "object_id": object_id,
            "state_code": state_code,
            "state": state_map.get(state_code, f"Unknown ({state_code})"),
            "alert_id": alert_id,
            "alert": self.ALERT_CODES.get(alert_id, f"Unknown ({alert_id})"),
            "priority_code": priority,
            "priority": self.PRIORITY_CODES.get(priority, f"Unknown ({priority})"),
            "raw_payload": payload,
        }

    def _parse_tic_value_payload(
        self,
        object_id: int,
        payload: str,
        unit: str,
        raise_on_fault: bool = False,
    ) -> dict:
        """
        Parse a standard TIC numeric value payload: '<value>;<alert_id>;<priority>'.
        Used by objects 905, 906, 911, 912, 919, 920.
        """
        fields = payload.split(";")
        if len(fields) != 3:
            raise EdwardsTicProtocolError(
                f"Expected 3 fields in value reply for object {object_id}, "
                f"got {len(fields)}: '{payload}'"
            )

        try:
            value = _finite_value(fields[0])
            alert_id = int(fields[1])
            priority = int(fields[2])
        except ValueError as exc:
            raise EdwardsTicProtocolError(
                f"Could not parse numeric values from payload '{payload}'"
            ) from exc

        if raise_on_fault:
            self._check_tic_fault(
                object_id=object_id,
                alert_id=alert_id,
                priority=priority,
            )

        return {
            "object_id": object_id,
            "value": value,
            "unit": unit,
            "alert_id": alert_id,
            "alert": self.ALERT_CODES.get(alert_id, f"Unknown ({alert_id})"),
            "priority_code": priority,
            "priority": self.PRIORITY_CODES.get(priority, f"Unknown ({priority})"),
            "raw_payload": payload,
        }

    # ==========================================================================
    # TIC Object 904: Turbo Pump State (?V904)
    # ==========================================================================
    def read_turbo_pump_state(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 904 (?V904): Read turbomolecular pump operating state.

        Response format (D397-30-880 Table 1 & Section 1.7.8):
            =V904 <state>;<alert_id>;<priority>
            States: 0=Stopped, 1=Starting Delay, 2=Stopping Short Delay,
                    3=Stopping Normal Delay, 4=Running, 5=Accelerating,
                    6=Fault Braking, 7=Braking.

        Args:
            raise_on_fault: If True, raises EdwardsTicPumpFaultError if the pump
                            is in Fault Braking (state 6), has an active alert,
                            or has an alarm priority.
        """
        payload = self.query_value(self.OBJ_TURBO_PUMP)
        result = self._parse_tic_state_payload(
            object_id=self.OBJ_TURBO_PUMP,
            payload=payload,
            state_map=self.PUMP_STATES,
            is_pump_state=True,
            raise_on_fault=raise_on_fault,
        )
        result["is_fault_braking"] = result["state_code"] == 6
        result["has_fault"] = (
            result["state_code"] == 6
            or result["alert_id"] != 0
            or result["priority_code"] >= 2
        )
        return result

    # ==========================================================================
    # TIC Object 905: Turbo Speed (?V905)
    # ==========================================================================
    def read_turbo_speed_percent(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 905 (?V905): Read turbomolecular pump speed in % of full speed.

        Response format (D397-30-880 Table 1):
            =V905 <value>;<alert_id>;<priority> (value: 0.0 to 110.0 %)
        """
        payload = self.query_value(self.OBJ_TURBO_SPEED)
        result = self._parse_tic_value_payload(
            object_id=self.OBJ_TURBO_SPEED,
            payload=payload,
            unit="%",
            raise_on_fault=raise_on_fault,
        )
        result["speed_percent"] = result["value"]
        return result

    def read_turbo_speed_percent_value(self, raise_on_fault: bool = False) -> float:
        """Return only the numerical turbo pump speed in % (Object 905)."""
        return self.read_turbo_speed_percent(raise_on_fault=raise_on_fault)["speed_percent"]

    # ==========================================================================
    # TIC Object 906: Turbo Power (?V906)
    # ==========================================================================
    def read_turbo_power(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 906 (?V906): Read turbomolecular pump power in Watts (W).

        Response format (D397-30-880 Table 1):
            =V906 <value>;<alert_id>;<priority> (value: 0.0 to 300.0 W typ.)
        """
        payload = self.query_value(self.OBJ_TURBO_POWER)
        result = self._parse_tic_value_payload(
            object_id=self.OBJ_TURBO_POWER,
            payload=payload,
            unit="W",
            raise_on_fault=raise_on_fault,
        )
        result["power_w"] = result["value"]
        return result

    def read_turbo_power_watts(self, raise_on_fault: bool = False) -> float:
        """Return only the numerical turbo pump power in Watts (Object 906)."""
        return self.read_turbo_power(raise_on_fault=raise_on_fault)["power_w"]

    # ==========================================================================
    # TIC Object 907: Turbo Normal (?V907)
    # ==========================================================================
    def read_turbo_normal(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 907 (?V907): Read whether the turbo pump has reached normal speed.

        Response format (D397-30-880 Table 1):
            =V907 <state>;<alert_id>;<priority> (0 = No, 4 = Yes)
        """
        payload = self.query_value(self.OBJ_TURBO_NORMAL)
        result = self._parse_tic_state_payload(
            object_id=self.OBJ_TURBO_NORMAL,
            payload=payload,
            state_map=self.TURBO_NORMAL_STATES,
            is_pump_state=False,
            raise_on_fault=raise_on_fault,
        )
        result["is_normal"] = result["state_code"] == 4
        return result

    def is_turbo_normal(self, raise_on_fault: bool = False) -> bool:
        """Return True if the turbo pump is at normal operating speed (Object 907)."""
        return self.read_turbo_normal(raise_on_fault=raise_on_fault)["is_normal"]

    # ==========================================================================
    # TIC Object 908: Turbo Standby (?V908)
    # ==========================================================================
    def read_turbo_standby(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 908 (?V908): Read whether the turbo pump is in standby mode.

        Response format (D397-30-880 Table 1):
            =V908 <state>;<alert_id>;<priority> (4 = In standby, 0 = Not in standby)
        """
        payload = self.query_value(self.OBJ_TURBO_STANDBY)
        result = self._parse_tic_state_payload(
            object_id=self.OBJ_TURBO_STANDBY,
            payload=payload,
            state_map=self.TURBO_STANDBY_STATES,
            is_pump_state=False,
            raise_on_fault=raise_on_fault,
        )
        result["is_standby"] = result["state_code"] == 4
        return result

    def is_turbo_standby(self, raise_on_fault: bool = False) -> bool:
        """Return True if the turbo pump is in standby mode (Object 908)."""
        return self.read_turbo_standby(raise_on_fault=raise_on_fault)["is_standby"]

    # ==========================================================================
    # TIC Object 909: Turbo Cycle Time (?V909)
    # ==========================================================================
    def read_turbo_cycle_time(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 909 (?V909): Read the time in hours that the turbo pump
        has been switched on during the current cycle.

        Response format (D397-30-880 Table 1):
            =V909 <value>;<state>;<alert_id>;<priority>
            The shorter three-field form accepted by V7 is also supported.
            value: 0 - 65535 hours.
        """
        payload = self.query_value(self.OBJ_TURBO_CYCLE_TIME)
        fields = payload.split(";")
        if len(fields) not in (3, 4):
            raise EdwardsTicProtocolError(f"Invalid cycle time reply: {payload!r}")
        try:
            hours = _finite_value(fields[0])
            if not hours.is_integer() or not 0 <= hours <= 65535:
                raise ValueError("Expected whole hours in the TIC range.")
            cycle_hours = int(hours)
            state_code = int(fields[1]) if len(fields) == 4 else None
            alert_id, priority = map(int, fields[-2:])
        except ValueError as exc:
            raise EdwardsTicProtocolError(f"Invalid cycle time reply: {payload!r}") from exc
        state_text = None if state_code is None else self.PUMP_STATES.get(state_code, f"Unknown ({state_code})")

        if raise_on_fault:
            self._check_tic_fault(
                object_id=self.OBJ_TURBO_CYCLE_TIME,
                alert_id=alert_id,
                priority=priority,
                state_code=state_code,
                is_pump_state=(state_code is not None),
            )

        return {
            "object_id": self.OBJ_TURBO_CYCLE_TIME,
            "cycle_time_hours": cycle_hours,
            "unit": "h",
            "state_code": state_code,
            "state": state_text,
            "alert_id": alert_id,
            "alert": self.ALERT_CODES.get(alert_id, f"Unknown ({alert_id})"),
            "priority_code": priority,
            "priority": self.PRIORITY_CODES.get(priority, f"Unknown ({priority})"),
            "raw_payload": payload,
        }

    # ==========================================================================
    # TIC Object 910: Backing Pump State (?V910)
    # ==========================================================================
    def read_backing_pump_state(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 910 (?V910): Read the backing pump operating state.

        Response format (D397-30-880 Table 1):
            =V910 <state>;<alert_id>;<priority>
        """
        payload = self.query_value(self.OBJ_BACKING_PUMP)
        return self._parse_tic_state_payload(
            object_id=self.OBJ_BACKING_PUMP,
            payload=payload,
            state_map=self.GENERAL_STATES,
            raise_on_fault=raise_on_fault,
        )

    # ==========================================================================
    # Routed nXDS diagnostics (A735-01-860)
    # ==========================================================================
    def read_nxds_temperatures(self) -> dict[str, int | None]:
        """Read nXDS temperatures in °C. A sensor reported as unused yields None."""
        payload = self.query_value(808)
        try:
            pump, controller = map(int, payload.split(";"))
        except ValueError as exc:
            raise EdwardsTicProtocolError(f"Invalid nXDS temperatures: {payload!r}") from exc
        if any(t != -200 and not 0 <= t <= 150 for t in (pump, controller)):
            raise EdwardsTicProtocolError(f"Invalid nXDS temperatures: {payload!r}")
        return {"pump_temperature_c": None if pump == -200 else pump,
                "controller_temperature_c": None if controller == -200 else controller}

    def read_nxds_status(self) -> dict:
        """Decode the live warning and fault registers (A735-01-860, section 3.9.1)."""
        payload = self.query_value(802)
        match = re.fullmatch(r"([0-9]{1,3})(;[0-9A-Fa-f]{4}){4}", payload)
        if not match or int(match[1]) > 255:
            raise EdwardsTicProtocolError(f"Invalid nXDS status: {payload!r}")
        frequency, _, _, warning_hex, fault_hex = payload.split(";")
        warnings = {1: "Controller too cold", 6: "Controller thermal regulation active",
                    10: "Controller too hot", 15: "Self-test warning"}
        faults = {1: "Link overvoltage", 2: "Motor overcurrent", 3: "Controller overtemperature",
                  4: "Controller temperature sensor fault", 5: "Power stage failure",
                  8: "Hardware fault latch", 9: "EEPROM fault", 11: "Missing parameter set",
                  12: "Self-test fault", 13: "Serial control interlock", 14: "Overload timeout",
                  15: "Acceleration timeout"}

        def active(word: str, labels: dict[int, str]) -> dict[int, str]:
            bits = int(word, 16)
            return {bit: labels.get(bit, "Unassigned bit") for bit in range(16) if bits & (1 << bit)}

        return {"frequency_hz": int(frequency), "warnings": active(warning_hex, warnings),
                "faults": active(fault_hex, faults), "raw_payload": payload}

    def read_backing_speed(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 911 (?V911): Read the backing pump speed in %.

        Response format (D397-30-880 Table 1):
            =V911 <value>;<alert_id>;<priority> (value: 0.0 to 100.0 %)
        """
        payload = self.query_value(self.OBJ_BACKING_SPEED)
        result = self._parse_tic_value_payload(
            object_id=self.OBJ_BACKING_SPEED,
            payload=payload,
            unit="%",
            raise_on_fault=raise_on_fault,
        )
        result["speed_percent"] = result["value"]
        return result

    def read_backing_speed_percent(self, raise_on_fault: bool = False) -> float:
        """Return only the numerical backing pump speed in % (Object 911)."""
        return self.read_backing_speed(raise_on_fault=raise_on_fault)["speed_percent"]

    # ==========================================================================
    # TIC Object 912: Backing Power (?V912)
    # ==========================================================================
    def read_backing_power(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 912 (?V912): Read the backing pump power in Watts (W).

        Response format (D397-30-880 Table 1):
            =V912 <value in watts>;<alert_id>;<priority>
        """
        payload = self.query_value(self.OBJ_BACKING_POWER)
        result = self._parse_tic_value_payload(
            object_id=self.OBJ_BACKING_POWER,
            payload=payload,
            unit="W",
            raise_on_fault=raise_on_fault,
        )
        result["power_w"] = result["value"]
        return result

    def read_backing_power_watts(self, raise_on_fault: bool = False) -> float:
        """Return only the numerical backing pump power in Watts (Object 912)."""
        return self.read_backing_power(raise_on_fault=raise_on_fault)["power_w"]

    # ==========================================================================
    # TIC Objects 913, 914, 915: Gauge 1-3 Pressure (V3 methods preserved)
    # ==========================================================================
    def read_pressure_pa(self, channel: int) -> float:
        """
        Read the gauge pressure of channel 1, 2, or 3 in Pascal (Pa).

         Note:
            In this protocol, the TIC returns pressure in Pa (unit code 59).
        """
        data = self.read_pressure(channel, raise_on_fault=True)
        if data["priority"] != 0:
            raise EdwardsTicError(
                f"Gauge {channel} reported priority {data['priority']} ({data['priority_text']})."
            )
        if data["state_code"] != 11:
            raise EdwardsTicProtocolError(f"Gauge {channel} is not measuring: {data['state']}.")
        if data["unit_code"] != 59:
            raise EdwardsTicProtocolError(
                f"Gauge {channel} did not return pressure in Pa "
                f"(unit_code={data['unit_code']} / {data['unit']})."
            )
        return data["value"]

    def read_pressure(self, channel: int, raise_on_fault: bool = False) -> dict:
        """
        Read the full gauge information for channel 1, 2, or 3 (Objects 913-915).

        Expected payload format:
            <value>;<unit_code>;<state>;<alert_id>;<priority>
        """
        if type(channel) is not int or channel not in self.GAUGE_OBJECTS:
            raise ValueError(
                f"Invalid gauge channel {channel}. Allowed values are 1, 2, or 3."
            )

        object_id = self.GAUGE_OBJECTS[channel]
        payload = self.query_value(object_id)

        fields = payload.split(";")
        if len(fields) != 5:
            raise EdwardsTicProtocolError(
                f"Expected 5 fields in gauge reply, got {len(fields)}: '{payload}'"
            )

        try:
            value = _finite_value(fields[0])
            unit_code = int(fields[1])
            state_code = int(fields[2])
            alert_id = int(fields[3])
            priority = int(fields[4])
        except ValueError as exc:
            raise EdwardsTicProtocolError(
                f"Could not parse gauge values from payload '{payload}'"
            ) from exc

        if raise_on_fault:
            self._check_tic_fault(
                object_id=object_id,
                alert_id=alert_id,
                priority=priority,
            )

        return {
            "channel": channel,
            "object_id": object_id,
            "value": value,
            "unit_code": unit_code,
            "unit": self.UNIT_CODES.get(unit_code, f"unknown({unit_code})"),
            "state_code": state_code,
            "state": self.GAUGE_STATES.get(state_code, f"unknown({state_code})"),
            "alert_id": alert_id,
            "alert": self.ALERT_CODES.get(alert_id, f"Unknown ({alert_id})"),
            "priority": priority,
            "priority_text": self.PRIORITY_CODES.get(priority, f"Unknown ({priority})"),
            "raw_payload": payload,
        }

    # ==========================================================================
    # TIC Object 919: Power Supply Temperature (?V919)
    # ==========================================================================
    def read_ps_temperature(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 919 (?V919): Read the Power Supply (PS) temperature.

        Response format (D397-30-880 Table 1):
            =V919 <value>;<alert_id>;<priority>
            The TIC reports temperature in Celsius shifted by +274 (water freezes at 274).
            Therefore: temperature_c = raw_value - 274.0.
        """
        payload = self.query_value(self.OBJ_PS_TEMPERATURE)
        result = self._parse_tic_value_payload(
            object_id=self.OBJ_PS_TEMPERATURE,
            payload=payload,
            unit="°C",
            raise_on_fault=raise_on_fault,
        )
        raw_val = result["value"]
        temp_c = round(raw_val - self.TIC_TEMP_OFFSET, 2)
        result["raw_temperature"] = raw_val
        result["temperature_c"] = temp_c
        result["value"] = temp_c
        return result

    def read_ps_temperature_celsius(self, raise_on_fault: bool = False) -> float:
        """Return only the Power Supply (PS) temperature in °C (Object 919)."""
        return self.read_ps_temperature(raise_on_fault=raise_on_fault)["temperature_c"]

    # ==========================================================================
    # TIC Object 920: Internal Temperature of the TIC Controller (?V920)
    # ==========================================================================
    def read_internal_temperature(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 920 (?V920): Read the internal temperature of the TIC controller.

        Response format (D397-30-880 Table 1):
            =V920 <value>;<alert_id>;<priority>
            The TIC reports temperature in Celsius shifted by +274 (water freezes at 274).
            Therefore: temperature_c = raw_value - 274.0.
        """
        payload = self.query_value(self.OBJ_INTERNAL_TEMPERATURE)
        result = self._parse_tic_value_payload(
            object_id=self.OBJ_INTERNAL_TEMPERATURE,
            payload=payload,
            unit="°C",
            raise_on_fault=raise_on_fault,
        )
        raw_val = result["value"]
        temp_c = round(raw_val - self.TIC_TEMP_OFFSET, 2)
        result["raw_temperature"] = raw_val
        result["temperature_c"] = temp_c
        result["value"] = temp_c
        return result

    def read_internal_temperature_celsius(self, raise_on_fault: bool = False) -> float:
        """Return only the internal TIC controller temperature in °C (Object 920)."""
        return self.read_internal_temperature(raise_on_fault=raise_on_fault)["temperature_c"]

    # ==========================================================================
    # TIC Object 924: External Air Cooler (?V924)
    # ==========================================================================
    def read_air_cooler_state(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 924 (?V924): Read the external air cooler operating state.

        Response format (D397-30-880 Table 1 & Section 1.7.6):
            =V924 <state>;<alert_id>;<priority> (States 0=Off State .. 4=On State)
        """
        payload = self.query_value(self.OBJ_AIR_COOLER)
        result = self._parse_tic_state_payload(
            object_id=self.OBJ_AIR_COOLER,
            payload=payload,
            state_map=self.GENERAL_STATES,
            is_pump_state=False,
            raise_on_fault=raise_on_fault,
        )
        result["is_on"] = result["state_code"] == 4
        return result

    # ==========================================================================
    # Edwards nEXT 300 Object 852: Motor Speed & System Status Word (?V852)
    # ==========================================================================
    @classmethod
    def decode_next300d_status_word(cls, status_word: int) -> dict:
        """
        Decode the 32-bit System Status Word from the Edwards nEXT 300 (?V852)
        according to manual B800-00-880.
        """
        if type(status_word) is not int or not 0 <= status_word <= 0xFFFFFFFF:
            raise ValueError("Expected an unsigned 32-bit status word.")
        active_flags: dict[int, str] = {}
        active_faults: dict[int, str] = {}

        for bit, description in cls.NEXT300D_STATUS_BITS.items():
            if bool(status_word & (1 << bit)):
                active_flags[bit] = description
                if bit in cls.NEXT300D_FAULT_BITS:
                    active_faults[bit] = description

        return {
            "status_word": status_word,
            "status_word_hex": f"{status_word:08X}",
            "active_flags": active_flags,
            "active_faults": active_faults,
            "has_fault": len(active_faults) > 0,
        }

    def read_next_speed_and_status(self, raise_on_fault: bool = False) -> dict:
        """
        Query Object 852 (?V852): Read measured motor rotational speed (RPS)
        and the 32-bit hexadecimal System Status Word of the nEXT 300 pump.

        Response format (B800-00-880):
            =V852 <speed_rps>;<32bit_hex_status_word>  (e.g. '=V852 1000;00000184')

        Args:
            raise_on_fault: If True, raises EdwardsTicPumpFaultError if any fault
                            bit (Bits 0, 10, 11, 12, 13, 14, 15) is active.
        """
        payload = self.query_value(self.OBJ_NEXT_SPEED_STATUS)
        fields = payload.split(";")
        if len(fields) != 2:
            raise EdwardsTicProtocolError(
                f"Expected 2 fields in ?V852 reply, got {len(fields)}: '{payload}'"
            )

        try:
            speed_rps = int(fields[0])
            status_word = int(fields[1], 16)
        except ValueError as exc:
            raise EdwardsTicProtocolError(
                f"Could not parse speed or hex status word from ?V852 payload '{payload}'"
            ) from exc

        if not 0 <= speed_rps <= 1800 or not re.fullmatch(r"[0-9a-fA-F]{1,8}", fields[1]):
            raise EdwardsTicProtocolError(f"Invalid speed or 32-bit status word: {payload!r}")
        decoded = self.decode_next300d_status_word(status_word)
        speed_rpm = speed_rps * 60
        speed_percent = round(
            (speed_rps / self.NEXT300D_NOMINAL_SPEED_RPS) * 100.0, 2
        )

        if raise_on_fault and decoded["has_fault"]:
            fault_details = "; ".join(
                f"Bit {bit}: {desc}"
                for bit, desc in decoded["active_faults"].items()
            )
            raise EdwardsTicPumpFaultError(
                message=(
                    f"Edwards nEXT 300 fault active (?V852 status word "
                    f"0x{decoded['status_word_hex']}): {fault_details}"
                ),
                object_id=self.OBJ_NEXT_SPEED_STATUS,
                status_word_hex=decoded["status_word_hex"],
                active_faults=decoded["active_faults"],
            )

        return {
            "object_id": self.OBJ_NEXT_SPEED_STATUS,
            "speed_rps": speed_rps,
            "speed_rpm": speed_rpm,
            "speed_percent_of_nominal": speed_percent,
            "nominal_speed_rps": self.NEXT300D_NOMINAL_SPEED_RPS,
            "status_word": decoded["status_word"],
            "status_word_hex": decoded["status_word_hex"],
            "active_flags": decoded["active_flags"],
            "active_faults": decoded["active_faults"],
            "has_fault": decoded["has_fault"],
            "raw_payload": payload,
        }

    def read_next_speed_rps(self, raise_on_fault: bool = False) -> int:
        """Return only the nEXT 300 rotational speed in RPS / Hz (?V852)."""
        return self.read_next_speed_and_status(raise_on_fault=raise_on_fault)["speed_rps"]

    def read_next_speed_rpm(self, raise_on_fault: bool = False) -> int:
        """Return only the nEXT 300 rotational speed in RPM (?V852)."""
        return self.read_next_speed_and_status(raise_on_fault=raise_on_fault)["speed_rpm"]

    # ==========================================================================
    # Edwards nEXT 300 Object 859: Motor & Controller Temperatures (?V859)
    # ==========================================================================
    def read_next_temperatures(self) -> dict:
        """
        Query Object 859 (?V859): Read the motor temperature and controller
        temperature of the Edwards nEXT 300 turbomolecular pump in °C.

        Response format (B800-00-880):
            =V859 <motor_temp_c>;<controller_temp_c>
        """
        payload = self.query_value(self.OBJ_NEXT_TEMPERATURES)
        fields = payload.split(";")
        if len(fields) != 2:
            raise EdwardsTicProtocolError(
                f"Expected 2 fields in ?V859 reply, got {len(fields)}: '{payload}'"
            )

        try:
            motor_temp_c, controller_temp_c = map(int, fields)
            if not (0 <= motor_temp_c <= 100 and 0 <= controller_temp_c <= 100):
                raise ValueError("Temperature outside the nEXT protocol range.")
        except ValueError as exc:
            raise EdwardsTicProtocolError(
                f"Could not parse temperatures from ?V859 payload '{payload}'"
            ) from exc

        return {
            "object_id": self.OBJ_NEXT_TEMPERATURES,
            "motor_temperature_c": motor_temp_c,
            "controller_temperature_c": controller_temp_c,
            "unit": "°C",
            "raw_payload": payload,
        }

    def read_next_motor_temperature(self) -> int:
        """Return only the nEXT 300 motor temperature in °C (?V859)."""
        return self.read_next_temperatures()["motor_temperature_c"]

    def read_next_controller_temperature(self) -> int:
        """Return only the nEXT 300 controller temperature in °C (?V859)."""
        return self.read_next_temperatures()["controller_temperature_c"]

    # ==========================================================================
    # Turbomolecular Pump Error / Fault Catching & Diagnostics
    # ==========================================================================
    def read_turbo_faults(
        self,
        use_tic_904: bool = True,
        use_next_852: bool = True,
    ) -> dict:
        """
        Query and catch all active fault/error messages of the nEXT 300
        turbomolecular pump without raising an exception.

        Args:
            use_tic_904: Query TIC Object 904 (?V904) for pump state, alert ID,
                         and priority.
            use_next_852: Query nEXT Object 852 (?V852) for the 32-bit system
                          status word fault bits.

        Returns:
            Dictionary containing fault status, error messages, and raw diagnostics.
        """
        if not (use_tic_904 or use_next_852):
            raise ValueError("Select at least one source for fault monitoring.")
        messages: list[str] = []
        tic_904_data = None
        next_852_data = None

        if use_tic_904:
            tic_904_data = self.read_turbo_pump_state(raise_on_fault=False)
            if tic_904_data["is_fault_braking"]:
                messages.append("TIC Object 904: Pump is in Fault Braking state (State 6).")
            if tic_904_data["alert_id"] != 0:
                messages.append(
                    f"TIC Object 904 Alert {tic_904_data['alert_id']}: "
                    f"{tic_904_data['alert']} (Priority: {tic_904_data['priority']})."
                )
            elif tic_904_data["priority_code"] >= 2:
                messages.append(
                    f"TIC Object 904 Alarm Priority {tic_904_data['priority_code']} active."
                )

        if use_next_852:
            next_852_data = self.read_next_speed_and_status(raise_on_fault=False)
            for bit, desc in next_852_data["active_faults"].items():
                messages.append(
                    f"nEXT 300 ?V852 Fault Bit {bit} "
                    f"(0x{next_852_data['status_word_hex']}): {desc}."
                )

        return {
            "has_fault": len(messages) > 0,
            "fault_messages": messages,
            "tic_904": tic_904_data,
            "next_852": next_852_data,
        }

    def check_turbo_faults(
        self,
        use_tic_904: bool = True,
        use_next_852: bool = True,
    ) -> None:
        """
        Check the turbomolecular pump for active faults via ?V904 and/or ?V852
        and raise EdwardsTicPumpFaultError if any fault is detected.
        """
        report = self.read_turbo_faults(
            use_tic_904=use_tic_904,
            use_next_852=use_next_852,
        )
        if report["has_fault"]:
            combined_msg = " | ".join(report["fault_messages"])
            alert_id = report["tic_904"]["alert_id"] if report["tic_904"] else 0
            priority = report["tic_904"]["priority_code"] if report["tic_904"] else 0
            status_hex = (
                report["next_852"]["status_word_hex"] if report["next_852"] else None
            )
            active_faults = (
                report["next_852"]["active_faults"] if report["next_852"] else {}
            )
            raise EdwardsTicPumpFaultError(
                message=f"Turbomolecular pump fault detected: {combined_msg}",
                object_id=self.OBJ_TURBO_PUMP if use_tic_904 else self.OBJ_NEXT_SPEED_STATUS,
                alert_id=alert_id,
                priority=priority,
                status_word_hex=status_hex,
                active_faults=active_faults,
            )
