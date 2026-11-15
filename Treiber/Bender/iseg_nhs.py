"""iseg NHS USB/SCPI driver. Python >=3.10, pyserial >=3.5.

Reference: iseg SCPI Programmers Guide, 2026-10-23, chapters 2-4, 6, 9.
No output changes on open/close. No automatic retry after ambiguous I/O errors.
All voltages supplied to set_voltage are SIGNED volts. NHS commands use
magnitudes; the fixed hardware polarity is checked through channel status bit 0.
"""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntFlag
import logging
import math
import re
import threading
import time
from typing import Callable
import serial

log = logging.getLogger(__name__)

class NHSError(Exception):
    """Base driver error."""
class CommunicationError(NHSError):
    """Outcome may be unknown. Close and reopen before further requests."""
class ProtocolError(CommunicationError):
    pass
class DeviceError(NHSError):
    pass
class VerificationError(DeviceError):
    pass
class PartialUpdateError(NHSError):
    def __init__(self, completed, failed, cause):
        self.completed = tuple(completed)
        self.failed = failed
        self.cause = cause
        super().__init__(f'Confirmed: {self.completed}; failed/uncertain: {failed}; {cause}')

class ChannelStatus(IntFlag):
    POSITIVE = 1 << 0
    ARC = 1 << 1
    INPUT_ERROR = 1 << 2
    ON = 1 << 3
    RAMPING = 1 << 4
    EMERGENCY_OFF = 1 << 5
    CONSTANT_CURRENT = 1 << 6
    CONSTANT_VOLTAGE = 1 << 7
    LOW_CURRENT_RANGE = 1 << 8
    ARC_NUMBER_EXCEEDED = 1 << 9
    CURRENT_BOUNDS = 1 << 10
    VOLTAGE_BOUNDS = 1 << 11
    EXTERNAL_INHIBIT = 1 << 12
    CURRENT_TRIP = 1 << 13
    CURRENT_LIMIT = 1 << 14
    VOLTAGE_LIMIT = 1 << 15

class ChannelEvent(IntFlag):
    ARC = 1 << 1
    INPUT_ERROR = 1 << 2
    ON_TO_OFF = 1 << 3
    END_OF_VOLTAGE_RAMP = 1 << 4
    EMERGENCY_OFF = 1 << 5
    CONSTANT_CURRENT = 1 << 6
    CONSTANT_VOLTAGE = 1 << 7
    ARC_NUMBER_EXCEEDED = 1 << 9
    CURRENT_BOUNDS = 1 << 10
    VOLTAGE_BOUNDS = 1 << 11
    EXTERNAL_INHIBIT = 1 << 12
    CURRENT_TRIP = 1 << 13
    CURRENT_LIMIT = 1 << 14
    VOLTAGE_LIMIT = 1 << 15

class ModuleStatus(IntFlag):
    FINE_ADJUSTMENT = 1 << 0
    HIGH_VOLTAGE_ON = 1 << 3
    SERVICE = 1 << 4
    INPUT_ERROR = 1 << 6
    NO_SUM_ERROR = 1 << 8
    NO_RAMP = 1 << 9
    SAFETY_LOOP_GOOD = 1 << 10
    EVENT_ACTIVE = 1 << 11
    MODULE_GOOD = 1 << 12
    SUPPLY_GOOD = 1 << 13
    TEMPERATURE_GOOD = 1 << 14
    KILL_ENABLE = 1 << 15

class ModuleEvent(IntFlag):
    SERVICE = 1 << 4
    INPUT_ERROR = 1 << 6
    SAFETY_LOOP_NOT_GOOD = 1 << 10
    SUPPLY_NOT_GOOD = 1 << 13
    TEMPERATURE_NOT_GOOD = 1 << 14

@dataclass(frozen=True)
class ChannelReading:
    channel: int
    voltage_set: float
    voltage: float
    current_set: float
    current: float
    status: ChannelStatus
    events: ChannelEvent
    timestamp: float

@dataclass(frozen=True)
class ModuleReading:
    identification: str
    temperature_c: float
    status: ModuleStatus
    events: ModuleEvent
    voltage_ramp_percent_s: float
    timestamp: float

_NUMBER = re.compile(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)(.*)')
def parse_number(text: str, unit: str = '') -> float:
    m = _NUMBER.fullmatch(text.strip())
    if not m or m[2].strip() not in ('', unit):
        raise ProtocolError(f'Invalid numeric response for {unit!r}: {text!r}')
    value = float(m[1])
    if not math.isfinite(value):
        raise ProtocolError(f'Non-finite device response: {text!r}')
    return value

def finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be a finite number')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite')
    return value

class NHSModule:
    """One USB COM port. Constructor does not connect; use with or open().

    polarity: '+' or '-', expected fixed hardware polarity.
    max_voltage: mandatory application limit in volts (positive magnitude).
    serial_factory: dependency injection for offline tests.
    """
    def __init__(self, port: str, *, polarity: str, max_voltage: float,
                 timeout: float = 2.0, expected_serial: str | None = None,
                 serial_factory: Callable = serial.Serial):
        if polarity not in ('+', '-'):
            raise ValueError("polarity must be '+' or '-'")
        self.port = port
        self.polarity = polarity
        self.sign = 1 if polarity == '+' else -1
        self.max_voltage = finite(max_voltage, 'max_voltage')
        self.timeout = finite(timeout, 'timeout')
        if self.max_voltage <= 0 or self.timeout <= 0:
            raise ValueError('max_voltage and timeout must be positive')
        self.expected_serial = expected_serial
        self._factory = serial_factory
        self._serial = None
        self._lock = threading.RLock()
        self._broken = False
        self.identification = ''
        self.channel_count = 0

    def open(self):
        with self._lock:
            if self._serial is not None:
                raise NHSError('Already open; close before reopening')
            try:
                self._serial = self._factory(port=self.port, baudrate=9600,
                    bytesize=8, parity='N', stopbits=1, timeout=self.timeout,
                    write_timeout=self.timeout, xonxoff=False, rtscts=False, dsrdtr=False)
                self._broken = False
                self._serial.reset_input_buffer()
                self.identification = self.query('*IDN?')
                parts = [p.strip() for p in self.identification.split(',')]
                if len(parts) < 4 or 'iseg' not in parts[0].lower():
                    raise DeviceError(f'Unexpected identification: {self.identification}')
                if self.expected_serial is not None and parts[2] != self.expected_serial:
                    raise DeviceError(f'Serial mismatch: {parts[2]} != {self.expected_serial}')
                firmware = self.query(':READ:FIRMWARE:NAME?')
                if firmware != 'N06C2':
                    raise DeviceError(f'Expected NHS firmware N06C2, received {firmware!r}')
                self.channel_count = self._integer(':READ:MODULE:CHANNELNUMBER?')
                if self.channel_count != 6:
                    raise DeviceError(f'Expected 6 NHS channels, received {self.channel_count}')
                for ch in range(self.channel_count):
                    self._check_polarity(ch)
            except BaseException:
                self.close()
                raise
        return self

    def close(self):
        """Close USB connection only. HV state is unchanged."""
        with self._lock:
            connection, self._serial = self._serial, None
            if connection is not None:
                connection.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _exchange(self, command):
        """One request/reply transaction; handles echo on or off without changing it."""
        if self._serial is None or self._broken:
            raise CommunicationError('Connection closed or desynchronized; close and reopen')
        if not command or any(c in command for c in '\r\n'):
            raise ValueError('Expected one ASCII command line')
        wire = (command + '\r\n').encode('ascii')
        if len(wire) > 80:
            raise ValueError('NHS receive buffer is 80 bytes including terminator')
        try:
            log.debug('%s TX %s', self.port, command)
            if self._serial.write(wire) != len(wire):
                raise CommunicationError('Incomplete write; command outcome unknown')
            deadline = time.monotonic() + self.timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CommunicationError(f'Timeout: {command}')
                self._serial.timeout = remaining
                raw = self._serial.read_until(b'\r\n', 202)
                if not raw.endswith(b'\r\n') or len(raw) > 200:
                    raise CommunicationError(f'Missing/truncated response to {command}: {raw!r}')
                answer = raw[:-2].decode('ascii')
                log.debug('%s RX %s', self.port, answer)
                if answer == command or answer == '':
                    continue
                return answer.strip()
        except (OSError, serial.SerialException, UnicodeError, CommunicationError) as exc:
            self._broken = True
            raise CommunicationError(f'{self.port}: {exc}') from exc
        finally:
            self._serial.timeout = self.timeout

    def query(self, command: str) -> str:
        """Advanced raw single query; caller owns semantics. No automatic retries."""
        if command.count('?') != 1 or ';' in command:
            raise ValueError('query accepts exactly one query and no command chaining')
        with self._lock:
            return self._exchange(command)

    def _integer(self, command):
        result = self.query(command)
        if not re.fullmatch(r'\d+', result):
            raise ProtocolError(f'Expected unsigned register/integer: {result!r}')
        return int(result)

    def _number(self, command, unit):
        return parse_number(self.query(command), unit)

    def _channel(self, channel):
        if type(channel) is not int or not 0 <= channel < self.channel_count:
            raise ValueError(f'Channel must be integer 0..{self.channel_count - 1}')
        return f'(@{channel})'

    def _check_polarity(self, channel):
        status = self.read_status(channel)
        if bool(status & ChannelStatus.POSITIVE) != (self.sign > 0):
            raise DeviceError(f'{self.port} channel {channel}: unexpected hardware polarity')
        return status

    def _check_input_error(self, channel=None):
        module = self.read_module_status()
        status = self.read_status(channel) if channel is not None else ChannelStatus(0)
        if module & ModuleStatus.INPUT_ERROR or status & ChannelStatus.INPUT_ERROR:
            raise DeviceError(f'Input error: module=0x{int(module):X}, channel=0x{int(status):X}; inspect events')

    def _write(self, command, channel=None, *, recovery=False):
        with self._lock:
            if not recovery:
                self._check_input_error(channel)
            answer = self._exchange(command + ';*OPC?')
            if answer != '1':
                self._broken = True
                raise ProtocolError(f'Unexpected operation-complete reply: {answer!r}')
            if not recovery:
                self._check_input_error(channel)

    def validate_voltage(self, channel, voltage):
        self._channel(channel)
        value = finite(voltage, 'voltage')
        if value * self.sign < 0:
            raise ValueError(f'{self.port} requires {self.polarity} voltage (or zero)')
        if abs(value) > self.max_voltage:
            raise ValueError(f'Voltage exceeds application limit {self.max_voltage} V')
        with self._lock:
            self._check_polarity(channel)
            nominal = abs(self._number(f':READ:VOLT:NOM? {self._channel(channel)}', 'V'))
            if abs(value) > nominal:
                raise ValueError(f'Voltage exceeds nominal voltage {nominal} V')
        return value

    def set_voltage(self, channel: int, voltage: float):
        """Set signed volts, verify SETPOINT. Does not enable HV or wait for ramp."""
        with self._lock:
            value = self.validate_voltage(channel, voltage)
            self._write(f':VOLT {abs(value):.12g},{self._channel(channel)}', channel)
            actual = self.read_voltage_set(channel)
            if not math.isclose(actual, value, abs_tol=0.01, rel_tol=1e-6):
                raise VerificationError(f'Voltage setpoint {actual} V differs from requested {value} V')

    def read_voltage_set(self, channel):
        return self.sign * abs(self._number(f':READ:VOLT? {self._channel(channel)}', 'V'))

    def read_voltage(self, channel):
        return self.sign * abs(self._number(f':MEAS:VOLT? {self._channel(channel)}', 'V'))

    def read_current(self, channel):
        """Measured amperes exactly as reported (no synthetic polarity sign)."""
        return self._number(f':MEAS:CURR? {self._channel(channel)}', 'A')

    def read_current_set(self, channel):
        return self._number(f':READ:CURR? {self._channel(channel)}', 'A')

    def set_current_limit(self, channel, amperes):
        """Set ISET in amperes; effect on trip/regulation depends on existing configuration."""
        with self._lock:
            ch = self._channel(channel)
            value = finite(amperes, 'amperes')
            nominal = abs(self._number(f':READ:CURR:NOM? {ch}', 'A'))
            if not 0 <= value <= nominal:
                raise ValueError(f'Current must be between 0 and {nominal} A')
            self._write(f':CURR {value:.12g},{ch}', channel)
            if not math.isclose(self.read_current_set(channel), value, rel_tol=1e-5, abs_tol=1e-12):
                raise VerificationError('Current setpoint readback differs')

    def enable(self, channel):
        with self._lock:
            self.validate_voltage(channel, self.read_voltage_set(channel))
            self._write(f':VOLT ON,{self._channel(channel)}', channel)
            if not self.read_status(channel) & ChannelStatus.ON:
                raise DeviceError('Channel did not switch on; inspect channel/module events')

    def disable(self, channel):
        """Request ramped off; completion is not proof of zero measured voltage."""
        self._write(f':VOLT OFF,{self._channel(channel)}', channel, recovery=True)

    def emergency_off(self, channel):
        self._write(f':VOLT EMCY OFF,{self._channel(channel)}', channel, recovery=True)

    def clear_emergency(self, channel):
        self._write(f':VOLT EMCY CLR,{self._channel(channel)}', channel, recovery=True)

    def clear_events(self, channel=None):
        command = ':CONF:EVENT CLEAR' if channel is None else f':EVENT CLEAR,{self._channel(channel)}'
        self._write(command, channel, recovery=True)

    def read_status(self, channel):
        return ChannelStatus(self._integer(f':READ:CHAN:STAT? {self._channel(channel)}'))

    def read_events(self, channel):
        return ChannelEvent(self._integer(f':READ:CHAN:EVENT:STAT? {self._channel(channel)}'))

    def read_module_status(self):
        return ModuleStatus(self._integer(':READ:MODULE:STAT?'))

    def read_module_events(self):
        return ModuleEvent(self._integer(':READ:MODULE:EVENT:STAT?'))

    def read_voltage_ramp(self):
        return self._number(':READ:RAMP:VOLT?', '%/s')

    def set_voltage_ramp(self, percent_per_second):
        """MODULE-wide ramp in % of nominal voltage per second, all six channels."""
        with self._lock:
            value = finite(percent_per_second, 'percent_per_second')
            if value <= 0:
                raise ValueError('Ramp speed must be positive')
            self._write(f':CONF:RAMP:VOLT {value:.12g}')
            if not math.isclose(self.read_voltage_ramp(), value, abs_tol=0.01, rel_tol=1e-5):
                raise VerificationError('Ramp readback differs')

    def read_channel(self, channel):
        """Sequential readings, not an atomic hardware snapshot."""
        with self._lock:
            return ChannelReading(channel, self.read_voltage_set(channel),
                self.read_voltage(channel), self.read_current_set(channel),
                self.read_current(channel), self.read_status(channel),
                self.read_events(channel), time.time())

    def read_module(self):
        with self._lock:
            return ModuleReading(self.identification,
                self._number(':READ:MODULE:TEMPERATURE?', 'C'), self.read_module_status(),
                self.read_module_events(), self.read_voltage_ramp(), time.time())

class Bender:
    """Two NHS modules, channels 0/1 each. Scan/ratio logic belongs in backend.

    Keys p0, p1, n0, n1 identify module polarity and physical channel.
    Multi-channel operations are sequential and cannot be atomic across USB.
    """
    def __init__(self, positive_port, negative_port, *, max_voltage,
                 timeout=2.0, positive_serial=None, negative_serial=None,
                 serial_factory=serial.Serial):
        if positive_port.strip().casefold() == negative_port.strip().casefold():
            raise ValueError('Two different COM ports are required')
        self.positive = NHSModule(positive_port, polarity='+', max_voltage=max_voltage,
            timeout=timeout, expected_serial=positive_serial, serial_factory=serial_factory)
        self.negative = NHSModule(negative_port, polarity='-', max_voltage=max_voltage,
            timeout=timeout, expected_serial=negative_serial, serial_factory=serial_factory)
        self.channels = {'p0': (self.positive, 0), 'p1': (self.positive, 1),
                         'n0': (self.negative, 0), 'n1': (self.negative, 1)}
        self._lock = threading.RLock()

    def open(self):
        with self._lock:
            self.positive.open()
            try:
                self.negative.open()
            except BaseException:
                self.positive.close()
                raise
        return self

    def close(self):
        with self._lock:
            try:
                self.positive.close()
            finally:
                self.negative.close()

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def set_voltages(self, *, p0, p1, n0, n1):
        values = dict(p0=p0, p1=p1, n0=n0, n1=n1)
        # Lock module transactions too, so a monitoring thread cannot interleave.
        with self._lock, self.positive._lock, self.negative._lock:
            for key, (module, ch) in self.channels.items():
                values[key] = module.validate_voltage(ch, values[key])
            completed = []
            for key, (module, ch) in self.channels.items():
                try:
                    module.set_voltage(ch, values[key])
                except Exception as exc:
                    raise PartialUpdateError(completed, key, exc) from exc
                completed.append(key)

    def read_all(self):
        with self._lock:
            return {key: module.read_channel(ch) for key, (module, ch) in self.channels.items()}

    def enable(self):
        with self._lock, self.positive._lock, self.negative._lock:
            for module, ch in self.channels.values():
                module.validate_voltage(ch, module.read_voltage_set(ch))
            completed = []
            for key, (module, ch) in self.channels.items():
                try:
                    module.enable(ch)
                except Exception as exc:
                    raise PartialUpdateError(completed, key, exc) from exc
                completed.append(key)

    def disable(self, *, emergency=False):
        """Try every selected channel even if another channel/module fails.

        Returns normally only if every command was acknowledged. On error,
        .completed lists successes, .failed maps keys to exceptions.
        """
        with self._lock:
            completed, failures = [], {}
            for key, (module, ch) in self.channels.items():
                try:
                    (module.emergency_off if emergency else module.disable)(ch)
                    completed.append(key)
                except Exception as exc:
                    failures[key] = exc
            if failures:
                raise PartialUpdateError(completed, failures, 'One or more off commands failed')
