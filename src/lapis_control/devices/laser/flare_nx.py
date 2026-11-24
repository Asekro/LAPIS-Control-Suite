"""
Driver for the Coherent Flare NX Laser System over RS232 / virtual USB COM port.

Revised: verified handshake transitions, OFF on close, single-shot mode selection.
USB status readout and OFF on close verified with firmware V3.0.24 and interlock open.

Fixed settings (according to Flare NX Laser Operator's Manual, chapter 7, pp. 59-64):
- 9600 baud (factory default)
- 8N1 (8 data bits, no parity, 1 stop bit)
- no RS232 hardware/software line flow control (only RXD, TXD, GND connected, p. 41)
- USB command handshake on the LAPIS controller: 'SYST:COM:HAND:USB on' (OEM section 8.1.5.1)
- Timeout: 1.0 s
- Command terminator: CR LF (0x0D 0x0A)
- Minimum delay between commands: 100 ms (manual p. 49)
- Laser emission enable/disable command: 'LD:SET:ENA on' / 'LD:SET:ENA off'
- Laser head (module) actual temperature query: 'TEMP:LMT:READ' in m°C (manual p. 64)
- Note on 'TEMP:LMT:SET': The 'TEMP:LMT:SET' functionality is implemented in this
  driver but intentionally commented out to prevent accidental modifications to
  the laser head temperature setpoint.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from decimal import Decimal
from enum import IntFlag
from typing import Final, Optional

import serial

log = logging.getLogger(__name__)

# Hardcoded serial connection parameters (manual section 7.1.1, page 59)
BAUDRATE: Final[int] = 9600
TIMEOUT: Final[float] = 1.0
WRITE_TIMEOUT: Final[float] = 1.0

# The manual requires a pause of at least 100 ms between commands (page 49)
MIN_COMMAND_DELAY_S: Final[float] = 0.1

# Protocol constants (manual section 7.1.2, page 60 & table 7-2, pages 62-64)
CRLF = b"\r\n"
ACK_RESPONSE = "OK"

CMD_HANDSHAKE = "SYST:COM:HAND"
CMD_LASER_ENABLE = "LD:SET:ENA"
CMD_REMOTE_ENABLE = "LD:SET:REM"
CMD_PULSE_MODE = "LD:PULS:MOD"
CMD_SOFTWARE_TRIGGER = "LD:SET:TRI"
CMD_HEAD_TEMP_READ = "TEMP:LMT:READ"
# CMD_HEAD_TEMP_SET = "TEMP:LMT:SET"  # Commented out to prevent accidental setpoint changes

STATE_ON = "on"
STATE_OFF = "off"

VALID_HANDSHAKE_INTERFACES = {"SERIAL", "USB"}


class SystemStatus(IntFlag):
    """Controller status bits used by the single-shot test (OEM table 8-2)."""

    FAULT = 0x01
    WARM_UP = 0x02
    STANDBY = 0x04
    READY = 0x08
    EMISSION = 0x10
    TEMPERATURE_FAULT = 0x20
    TRIGGER_INPUT = 0x40


class SystemFault(IntFlag):
    """Interlock indications in the controller fault register (OEM table 8-4)."""

    INTERLOCK_FAULT = 0x00040000
    INTERLOCK_OPEN = 0x00080000


# ---------------------------------------------------------------------------
# CUSTOM EXCEPTION HIERARCHY
# ---------------------------------------------------------------------------


# Coherent User Command Set, "SCPI Errors"; OEM 1306058 AE, table 8-6.
SCPI_ERRORS = {
    -600: "SCPI_ERROR_TIMEOUT",
    -610: "SCPI_ERROR_EXECUTION_TIMEOUT",
    -500: "SCPI_ERROR_NO_ANSWER_WANTED",
    -400: "SCPI_ERROR_QUERY_UNAVAILABLE",
    -350: "SCPI_ERROR_QUEUE_OVERFLOW",
    -321: "SCPI_ERROR_OUT_OF_MEMORY",
    -310: "SCPI_ERROR_SYSTEM_ERROR",
    -257: "SCPI_ERROR_FILE_NOT_NAMED",
    -256: "SCPI_ERROR_FILE_DOES_NOT_EXIST",
    -250: "SCPI_ERROR_BYTE_ORDER_ERROR",
    -241: "SCPI_ERROR_DEVICE_UNAVAILABLE",
    -221: "SCPI_ERROR_SETTINGS_CONFLICT",
    -220: "SCPI_ERROR_INVALID_PARAMETER",
    -203: "SCPI_ERROR_COMMAND_PROTECTED",
    -200: "SCPI_ERROR_EXECUTION_ERROR",
    -109: "SCPI_ERROR_PARAMETER_MISSING",
    -102: "SCPI_ERROR_SYNTAX_ERROR",
    -100: "SCPI_ERROR_COMMAND_ERROR",
    0: "SCPI_ERROR_NONE",
}


class FlareNxError(Exception):
    """Base class for all Coherent Flare NX driver errors."""


class FlareNxConnectionError(FlareNxError):
    """Raised when the serial connection cannot be opened or fails."""


class FlareNxTimeoutError(FlareNxError):
    """Raised when the laser controller does not respond in time."""


class FlareNxProtocolError(FlareNxError):
    """Raised when the controller returns an invalid or unexpected response."""


class FlareNxCommandError(FlareNxError):
    """Raised when the controller rejects a command or fails to change state."""

    def __init__(
        self,
        message: str,
        *,
        command: Optional[str] = None,
        code: Optional[int] = None,
    ) -> None:
        self.message = message.strip()
        self.command = command
        self.code = code
        self.error_name = SCPI_ERRORS.get(code, "UNKNOWN_SCPI_ERROR") if code is not None else None

        details = self.message
        if self.error_name is not None:
            details += f" [{self.error_name}, code={self.code}]"
        if command is not None:
            details += f" | Sent: {command!r}"
        super().__init__(f"Flare NX rejected the command: {details}")



# ---------------------------------------------------------------------------
# MAIN DRIVER CLASS
# ---------------------------------------------------------------------------


class FlareNx:
    """Serial interface to the Coherent Flare NX Laser System."""

    def __init__(
        self,
        port: str,
        handshake_interface: Optional[str] = None,
        *,
        timeout: float = TIMEOUT,
        write_timeout: float = WRITE_TIMEOUT,
    ) -> None:
        """
        Store the serial port and initialize internal state without opening
        the hardware connection yet.

        Args:
            port: Serial port name (e.g. 'COM7').
            handshake_interface: Optional interface selector for SYST:COM:HAND
                                 ('SERIAL' or 'USB' per table 7-2, p. 62).
                                 If None (default), uses 'SYST:COM:HAND on' (p. 49).
            timeout: Positive, finite read timeout in seconds.
            write_timeout: Positive, finite write timeout in seconds.
        """
        if not port or not isinstance(port, str):
            raise ValueError("A valid serial port must be provided (e.g. 'COM7').")

        for name, value in (("timeout", timeout), ("write_timeout", write_timeout)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive, finite number of seconds.")
        self.timeout = float(timeout)
        self.write_timeout = float(write_timeout)

        if handshake_interface is not None:
            normalized_if = handshake_interface.strip().upper()
            if normalized_if not in VALID_HANDSHAKE_INTERFACES:
                raise ValueError(
                    f"Invalid handshake_interface {handshake_interface!r}. "
                    f"Allowed values are 'SERIAL', 'USB', or None."
                )
            self.handshake_interface: Optional[str] = normalized_if
        else:
            self.handshake_interface = None

        self.port = port
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.RLock()
        self._last_command_time: float = 0.0
        self._handshake_enabled = False
        self._ready = False

    @property
    def is_connected(self) -> bool:
        """True only after the selected COM port and handshake are ready.

        This is local state, not a continuous hardware liveness check.
        """
        with self._lock:
            return (self._serial is not None and self._serial.is_open
                    and self._ready and self._handshake_enabled)

    def connect(self) -> "FlareNx":
        """Open the COM port, enable its protocol handshake and verify ON.

        Handshake is mandatory. SERIAL/USB refers to the laser-side interface,
        not the COM number; a USB-to-RS232 adapter still uses SERIAL at the laser.
        RTS/CTS, DSR/DTR and XON/XOFF flow control remain disabled (manual p. 59).
        """
        with self._lock:
            if self.is_connected:
                return self
            self._ready = False
            if self._serial is None or not self._serial.is_open:
                try:
                    self._serial = serial.Serial(
                        port=self.port,
                        baudrate=BAUDRATE,
                        bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE,
                        stopbits=serial.STOPBITS_ONE,
                        timeout=self.timeout,
                        write_timeout=self.write_timeout,
                        xonxoff=False,
                        rtscts=False,
                        dsrdtr=False,
                    )
                except serial.SerialException as exc:
                    self._serial = None
                    raise FlareNxConnectionError(
                        f"Could not open serial connection on {self.port}: {exc}"
                    ) from exc

            self._handshake_enabled = False
            try:
                # Always initialize the handshake on the port just opened.
                value = STATE_ON
                reply = self._exchange(
                    self.build_write_command(self._handshake_command(), value),
                    optional_reply=True,
                )
                if reply and reply.upper() != ACK_RESPONSE:
                    raise FlareNxProtocolError(
                        f"Unexpected handshake reply on {self.port}: {reply!r}"
                    )
                self._confirm_handshake(True)
                self._ready = True
            except BaseException as exc:
                self._close_after_error(exc)
                raise
        return self

    def close(self) -> None:
        """Send OFF and verify it before closing. Raise if OFF was not confirmed.

        This is a software shutdown attempt, not a hardware interlock or power cut.
        A lost connection cannot guarantee shutdown.
        """
        with self._lock:
            ser = self._serial
            if ser is None:
                return
            try:
                if not ser.is_open:
                    raise FlareNxConnectionError("Port already closed; laser OFF cannot be confirmed.")
                self.turn_off()
            finally:
                try:
                    ser.close()
                finally:
                    self._serial = None
                    self._handshake_enabled = False
                    self._ready = False

    def disconnect(self) -> None:
        """Compatibility alias for close()."""
        self.close()

    def __enter__(self) -> "FlareNx":
        """Support the 'with FlareNx(...) as laser:' context manager syntax."""
        return self.connect()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_value is None:
            self.close()
        else:
            self._close_after_error(exc_value)

    def _close_after_error(self, error: BaseException) -> None:
        """Attempt verified shutdown and preserve the error that caused it."""
        try:
            self.close()
        except Exception as cleanup_error:
            log.error("Laser OFF could not be confirmed during cleanup: %s", cleanup_error)
            if hasattr(error, "add_note"):
                error.add_note(f"Laser shutdown failed: {cleanup_error}")

    # -----------------------------------------------------------------------
    # COMMAND BUILDING AND PARSING
    # -----------------------------------------------------------------------

    @classmethod
    def build_query_command(cls, command: str) -> bytes:
        """
        Build a query frame in the format '<COMMAND>\\r\\n'
        (manual section 7.1.2, page 60, e.g. 'LD:SET:ENA\\r\\n').
        """
        if "\r" in command or "\n" in command:
            raise ValueError("Embedded command terminators are not allowed.")
        cleaned_command = command.strip()
        if not cleaned_command:
            raise ValueError("Command must not be empty.")

        return f"{cleaned_command}".encode("ascii") + CRLF

    @classmethod
    def build_write_command(cls, command: str, value: str) -> bytes:
        """
        Build a write frame in the format '<COMMAND> <VALUE>\\r\\n'
        (manual section 7.1.2, page 60, e.g. 'LD:SET:ENA on\\r\\n').
        """
        if "\r" in command or "\n" in command:
            raise ValueError("Embedded command terminators are not allowed.")
        cleaned_command = command.strip()
        if "\r" in str(value) or "\n" in str(value):
            raise ValueError("Embedded command terminators are not allowed.")
        cleaned_value = str(value).strip()
        if not cleaned_command or not cleaned_value:
            raise ValueError("Command and value must not be empty.")

        return f"{cleaned_command} {cleaned_value}".encode("ascii") + CRLF

    def _require_connection(self) -> serial.Serial:
        """Return the active serial object or raise a connection error."""
        if self._serial is None or not self._serial.is_open:
            raise FlareNxConnectionError(
                "Not connected. Call connect() first or use 'with FlareNx(...)'."
            )
        return self._serial

    def _wait_command_delay(self) -> None:
        """
        Enforce a minimum pause of 100 ms between two consecutive commands
        as required by the Flare NX manual (page 49).
        """
        elapsed = time.monotonic() - self._last_command_time
        remaining = MIN_COMMAND_DELAY_S - elapsed
        if remaining > 0:
            time.sleep(remaining)

    @classmethod
    def _parse_response(cls, response: bytes) -> str:
        """Validate the raw controller reply and return the decoded string."""
        if not response:
            raise FlareNxTimeoutError("No response received from Flare NX controller.")

        if not response.endswith(CRLF):
            raise FlareNxProtocolError(
                f"Missing CR LF (0x0D 0x0A) terminator in response: {response.hex(' ')}"
            )

        try:
            text = response[:-2].decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise FlareNxProtocolError(
                f"Response contains invalid ASCII characters: {response.hex(' ')}"
            ) from exc

        if not text:
            raise FlareNxProtocolError("Received an empty response line from controller.")

        # Check for controller error messages
        upper_text = text.upper()
        if upper_text.startswith("ERR") or "ERROR" in upper_text or "UNKNOWN" in upper_text:
            match = re.match(r"^ERR(?:OR)?\s*:?\s*([+-]?\d+)\b", text, re.IGNORECASE)
            code = None
            if match:
                # Some displays omit the minus sign; keep the raw reply in message.
                code = -abs(int(match.group(1)))
            raise FlareNxCommandError(text, code=code)

        return text

    # -----------------------------------------------------------------------
    # LOW-LEVEL SERIAL EXCHANGE
    # -----------------------------------------------------------------------

    def _exchange(self, frame: bytes, *, optional_reply: bool = False) -> str:
        """
        Send a raw command frame thread-safely and return the parsed response.
        """
        with self._lock:
            ser = self._require_connection()
            self._wait_command_delay()
            try:
                ser.reset_input_buffer()
                ser.write(frame)
                ser.flush()
                response = ser.read_until(CRLF)
            except serial.SerialTimeoutException as exc:
                raise FlareNxTimeoutError(
                    "Write timeout while sending command to Flare NX."
                ) from exc
            except serial.SerialException as exc:
                raise FlareNxConnectionError(
                    f"Serial communication error: {exc}"
                ) from exc
            finally:
                self._last_command_time = time.monotonic()

        if not response and optional_reply:
            return ""
        try:
            return self._parse_response(response)
        except FlareNxCommandError as exc:
            command = frame.decode("ascii").rstrip("\r\n")
            raise FlareNxCommandError(
                exc.message, command=command, code=exc.code
            ) from exc

    def query_parameter(self, command: str) -> str:
        """Send a query command (e.g. 'LD:SET:ENA') and return the answer string."""
        frame = self.build_query_command(command)
        return self._exchange(frame)

    def write_parameter(self, command: str, value: str) -> str:
        """Validate OK when handshake is enabled; no value-echo assumption.

        With handshake disabled an absent reply is expected. Callers must query
        the parameter to confirm acceptance. No write is automatically retried.
        """
        with self._lock:
            reply = self._exchange(self.build_write_command(command, value),
                                   optional_reply=not self._handshake_enabled)
            if reply and reply.upper() != ACK_RESPONSE:
                raise FlareNxProtocolError(f"Unexpected acknowledgement: {reply!r}")
            return reply

    # -----------------------------------------------------------------------
    # PROTOCOL HANDSHAKE CONTROL (SYST:COM:HAND)
    # -----------------------------------------------------------------------

    def read_handshake(self) -> str:
        """
        Query 'SYST:COM:HAND' to read the current handshake configuration
        (manual table 7-2, page 62).
        """
        return self.query_parameter(self._handshake_command())

    def _handshake_command(self) -> str:
        """Use the interface suffix documented in OEM 1306058 AE, section 8.1.5.1."""
        if self.handshake_interface:
            return f"{CMD_HANDSHAKE}:{self.handshake_interface}"
        return CMD_HANDSHAKE

    def set_handshake(
        self,
        enabled: bool = True,
        interface: Optional[str] = None,
    ) -> str:
        """
        Enable or disable the command handshake ('OK\\r\\n') via 'SYST:COM:HAND'.

        According to the Flare NX manual:
        - Page 49 (Terminal Start-Up): 'SYST:COM:HAND on'
        - OEM section 8.1.5.1 (explicit syntax): 'SYST:COM:HAND:SERIAL ON/OFF' or 'SYST:COM:HAND:USB ON/OFF'

        Args:
            enabled: True to enable handshake ('on'), False to disable ('off').
            interface: Optional target interface ('SERIAL' or 'USB'). If None,
                       sends 'SYST:COM:HAND on' / 'SYST:COM:HAND off'.
        """
        if not isinstance(enabled, bool):
            raise ValueError("Parameter 'enabled' must be a boolean (True or False).")

        state_str = STATE_ON if enabled else STATE_OFF

        if interface is not None:
            normalized_if = interface.strip().upper()
            if normalized_if not in VALID_HANDSHAKE_INTERFACES:
                raise ValueError(
                    f"Invalid interface {interface!r}. "
                    f"Allowed values are 'SERIAL', 'USB', or None."
                )
            value = state_str
        else:
            value = state_str

        # Only manage the interface used by this connection. The selector must
        # match the physical connection supplied by the caller.
        if interface is not None and normalized_if != self.handshake_interface:
            raise ValueError("Use the connection's configured handshake_interface.")
        with self._lock:
            # Firmware may apply ON/OFF before or after acknowledging this write.
            reply = self._exchange(self.build_write_command(self._handshake_command(), value),
                                   optional_reply=True)
            if reply and reply.upper() != ACK_RESPONSE:
                raise FlareNxProtocolError(f"Unexpected handshake reply: {reply!r}")
            self._confirm_handshake(enabled)
            self._ready = enabled
            return reply


    def _confirm_handshake(self, enabled: bool) -> None:
        """Read back the handshake setting for the configured interface."""
        state = self.read_handshake().upper().strip()
        if state in {"ON", "OFF", "1", "0"}:
            actual = state in {"ON", "1"}
        elif self.handshake_interface:
            match = re.search(r"\b" + self.handshake_interface + r"\s+(ON|OFF|1|0)\b", state)
            if not match:
                raise FlareNxProtocolError(f"Unknown handshake state format: {state!r}")
            actual = match.group(1) in {"ON", "1"}
        else:
            raise FlareNxProtocolError(f"Specify SERIAL/USB; handshake state: {state!r}")
        self._handshake_enabled = actual
        if actual != enabled:
            raise FlareNxCommandError("Handshake setting was not accepted.")

    def enable_handshake(self, interface: Optional[str] = None) -> str:
        """
        Enable the controller command handshake ('SYST:COM:HAND on').
        """
        return self.set_handshake(enabled=True, interface=interface)

    def disable_handshake(self, interface: Optional[str] = None) -> str:
        """
        Disable the controller command handshake ('SYST:COM:HAND off').
        """
        return self.set_handshake(enabled=False, interface=interface)

    # -----------------------------------------------------------------------
    # LASER ON / OFF CONTROL FUNCTIONS
    # -----------------------------------------------------------------------

    def read_laser_enabled(self) -> bool:
        """
        Query 'LD:SET:ENA' to check whether laser emission is currently enabled.

        According to table 7-2 (page 63), both the old format ('1'/'0') and
        the new format ('ON'/'OFF') may be used by the controller.
        """
        return self._read_switch(CMD_LASER_ENABLE)

    def set_laser_enabled(self, enabled: bool) -> None:
        """
        Enable or disable the laser diode ('LD:SET:ENA on' / 'LD:SET:ENA off')
        and query 'LD:SET:ENA' afterwards to confirm the command was accepted
        (as required by manual section 7.1.3, page 60).
        """
        if not isinstance(enabled, bool):
            raise ValueError("Parameter 'enabled' must be a boolean (True or False).")

        with self._lock:
            self._set_laser_enabled_locked(enabled)

    def read_remote_enabled(self) -> bool:
        """Read the electrical remote-enable setting (OEM manual section 6.3.3)."""
        return self._read_switch(CMD_REMOTE_ENABLE)

    def _read_switch(self, command: str) -> bool:
        reply = self.query_parameter(command).strip().lower()
        if reply in {"on", "1"}:
            return True
        if reply in {"off", "0"}:
            return False
        raise FlareNxProtocolError(
            f"Invalid switch state received for {command}: {reply!r}"
        )

    def _set_laser_enabled_locked(self, enabled: bool) -> None:
        # ENA must only be used with REM disabled (OEM manual section 6.3.3).
        if self.read_remote_enabled():
            self.write_parameter(CMD_REMOTE_ENABLE, STATE_OFF)
            if self.read_remote_enabled():
                raise FlareNxCommandError(
                    "Remote Enable remains ON after LD:SET:REM off; "
                    "laser enable/disable command was not sent."
                )

        self._write_laser_enabled(enabled)

    def _write_laser_enabled(self, enabled: bool) -> None:
        """Write ENA and verify it with the caller holding the lock and REM off."""
        target_value = STATE_ON if enabled else STATE_OFF
        self.write_parameter(CMD_LASER_ENABLE, target_value)

        # Manual page 60 NOTICE: "The command must be queried to confirm it was accepted."
        actual_state = self.read_laser_enabled()
        if actual_state != enabled:
            raise FlareNxCommandError(
                f"Laser did not switch to {target_value.upper()} "
                f"(current state is {'ON' if actual_state else 'OFF'}). "
                f"Check external interlock and controller warm-up status."
            )

    def turn_on(self) -> None:
        """Turn the laser emission ON ('LD:SET:ENA on')."""
        self.set_laser_enabled(True)

    def turn_off(self) -> None:
        """Turn the laser emission OFF ('LD:SET:ENA off')."""
        self.set_laser_enabled(False)

    def read_pulse_mode(self) -> int:
        reply = self.query_parameter(CMD_PULSE_MODE)
        try:
            value = float(reply)
        except ValueError as exc:
            raise FlareNxProtocolError(f"Invalid pulse mode: {reply!r}") from exc
        if value not in {0, 1, 2, 3, 5, 12}:
            raise FlareNxProtocolError(f"Unsupported pulse mode: {reply!r}")
        return int(value)

    def configure_single_shot(self) -> None:
        """Confirm OFF, then select and verify mode 3 if needed (OEM p. 54).

        Before calling this method, external trigger must be TTL low. Each external +5 V
        TTL pulse (>=20 us) then triggers one shot at utility pin 4. This method
        does not fire a shot. The supplied manual does not define a complete
        software single-shot sequence for LD:SET:TRI; none is guessed here.
        """
        with self._lock:
            self.turn_off()

            mode = self.read_pulse_mode()
            if mode == 3:
                return
            if mode in {0, 1}:
                raise FlareNxCommandError(f"Controller is in service mode {mode}; mode was not changed.")

            if self.read_system_status() & SystemStatus.TRIGGER_INPUT:
                raise FlareNxCommandError("Trigger input is active; mode was not changed.")
            self.write_parameter(CMD_PULSE_MODE, "3")
            if self.read_pulse_mode() != 3:
                raise FlareNxCommandError("Single-shot mode was not accepted.")

    def _read_register(self, command: str) -> int:
        reply = self.query_parameter(command)
        # The OEM examples display two four-digit words, e.g. "0000 0304".
        if not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{4}[ \t]*[0-9a-fA-F]{4}", reply):
            raise FlareNxProtocolError(f"Invalid 32-bit hex register for {command}: {reply!r}")
        return int("".join(reply.split()), 16)

    def read_system_status(self) -> SystemStatus:
        """Read SYST:STAT (OEM section 8.1.3.1), preserving all reported bits."""
        return SystemStatus(self._read_register("SYST:STAT"))

    def read_system_faults(self) -> SystemFault:
        """Read SYST:FAUL without acknowledging or clearing any fault."""
        return SystemFault(self._read_register("SYST:FAUL"))

    def reset_interlock_fault(self) -> SystemFault:
        """Acknowledge a latched interlock fault once after closure (OEM 5.2.4)."""
        with self._lock:
            if not self.is_connected:
                raise FlareNxConnectionError("Interlock reset requires a verified handshake.")
            faults = self.read_system_faults()
            if faults & SystemFault.INTERLOCK_OPEN:
                raise FlareNxCommandError("Interlock is still open; reset blocked.")
            if not faults & SystemFault.INTERLOCK_FAULT:
                return faults
            if self.read_laser_enabled() or self.read_remote_enabled():
                raise FlareNxCommandError("Interlock reset requires ENA and REM to be OFF.")
            self.write_parameter("SYST:INT", "RESET")
            faults = self.read_system_faults()
            if faults & (SystemFault.INTERLOCK_OPEN | SystemFault.INTERLOCK_FAULT):
                raise FlareNxCommandError(f"Interlock reset not confirmed (SYST:FAUL 0x{faults:08X}).")
            return faults

    def read_pulse_count(self) -> int:
        """Read the resettable uint32 shot counter without changing it (OEM 6.3.7)."""
        reply = self.query_parameter("HEAD:PULS:COUNT")
        # Firmware V3.0.24 reports e.g. "2.1322 * 10^4" for 21322 shots.
        match = re.fullmatch(
            r"([0-9]{1,10}(?:\.[0-9]{1,10})?)(?:\s*\*\s*10\^([0-9]{1,2}))?"
            r"(?:\s*(?:shots|\[shots\]))?", reply, re.IGNORECASE,
        )
        if match:
            count = Decimal(f"{match[1]}e{match[2] or '0'}")
            if count <= 0xFFFFFFFF and count == int(count):
                return int(count)
        raise FlareNxProtocolError(f"Invalid pulse counter: {reply!r}")

    def arm_single_shot(self) -> None:
        """Arm verified mode 3 for external TTL. Use inside a connected context.

        The operator must keep the trigger low until arming completes. Status
        queries are snapshots. Each subsequent qualifying edge can cause a shot.
        """
        with self._lock:
            if not self.is_connected:
                raise FlareNxConnectionError("Arming requires a verified command handshake.")
            if self.read_pulse_mode() != 3:
                raise FlareNxCommandError("Select and verify single-shot mode before arming.")
            if self.read_laser_enabled() or self.read_remote_enabled():
                raise FlareNxCommandError("Arming requires ENA and REM to be OFF.")
            if self._read_switch(CMD_SOFTWARE_TRIGGER):
                raise FlareNxCommandError("Software trigger is ON; external trigger test aborted.")
            faults = self.read_system_faults()
            if faults:
                raise FlareNxCommandError(f"Controller fault register 0x{faults:08X}; arming blocked.")
            status = self.read_system_status()
            blocking = (SystemStatus.FAULT | SystemStatus.WARM_UP | SystemStatus.READY
                        | SystemStatus.EMISSION | SystemStatus.TEMPERATURE_FAULT
                        | SystemStatus.TRIGGER_INPUT)
            if not status & SystemStatus.STANDBY or status & blocking:
                raise FlareNxCommandError(f"Expected standby with trigger low, got 0x{status:08X}.")
            try:
                self._write_laser_enabled(True)
            except BaseException as exc:
                # A missing acknowledgement can mean the enable was already applied.
                self._close_after_error(exc)
                raise

    # -----------------------------------------------------------------------
    # LASER HEAD TEMPERATURE FUNCTIONS (TEMP:LMT:READ)
    # -----------------------------------------------------------------------

    @classmethod
    def _parse_temperature_mc(cls, reply: str, command: str) -> int:
        """
        Parse a temperature response in millidegrees Celsius [m°C] (uint32).
        Handles responses both with and without unit suffix (depending on SYST:UNIT).
        """
        tokens = reply.strip().split()
        if not tokens:
            raise FlareNxProtocolError(
                f"Empty temperature value received for {command}."
            )

        try:
            value = int(tokens[0])
            if not 0 <= value <= 0xFFFFFFFF:
                raise ValueError("Temperature outside uint32 range")
            return value
        except ValueError as exc:
            raise FlareNxProtocolError(
                f"Invalid temperature value received for {command}: {reply!r}"
            ) from exc

    def read_head_temperature_mc(self) -> int:
        """
        Query 'TEMP:LMT:READ' to read the actual temperature of the laser
        module inside the laser head in millidegrees Celsius [m°C]
        (manual table 7-2, page 64).
        """
        reply = self.query_parameter(CMD_HEAD_TEMP_READ)
        return self._parse_temperature_mc(reply, CMD_HEAD_TEMP_READ)

    def read_head_temperature_c(self) -> float:
        """
        Query 'TEMP:LMT:READ' and return the actual temperature of the laser
        module inside the laser head in degrees Celsius [°C].
        """
        return self.read_head_temperature_mc() / 1000.0

    # --- Commented out to prevent accidental changes to the laser head setpoint ---
    # def read_head_set_temperature_mc(self) -> int:
    #     """
    #     Query 'TEMP:LMT:SET' to read the set-temperature of the laser module
    #     inside the laser head in millidegrees Celsius [m°C]
    #     (manual table 7-2, page 64).
    #     """
    #     reply = self.query_parameter(CMD_HEAD_TEMP_SET)
    #     return self._parse_temperature_mc(reply, CMD_HEAD_TEMP_SET)
    #
    # def read_head_set_temperature_c(self) -> float:
    #     """
    #     Query 'TEMP:LMT:SET' and return the set-temperature of the laser module
    #     inside the laser head in degrees Celsius [°C].
    #     """
    #     return self.read_head_set_temperature_mc() / 1000.0


def main() -> None:
    """Read-only demo; disconnect additionally sends OFF. Never auto-enable."""
    import argparse
    parser = argparse.ArgumentParser(description="Flare NX status; sends OFF before closing")
    parser.add_argument("port", help="e.g. COM7 or /dev/ttyUSB0")
    parser.add_argument("--interface", choices=["SERIAL", "USB"], default=None)
    args = parser.parse_args()
    with FlareNx(args.port, handshake_interface=args.interface) as laser:
        print(f"Head temperature: {laser.read_head_temperature_c():.3f} C")
        print(f"Pulse mode: {laser.read_pulse_mode()}")


if __name__ == "__main__":
    main()
