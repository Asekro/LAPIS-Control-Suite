"""Load explicit setup files and build disconnected device instances.

Protocol fields are checked against the drivers' supported profiles. Runtime
settings are passed to constructors. No fallback file or automatic connection
is used; malformed or unsupported settings fail before any hardware is opened.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import serial

from .devices.bender import iseg_nhs
from .devices.laser import flare_nx
from .devices.oscilloscope import keysight_dsox1204g

Device = iseg_nhs.Bender | flare_nx.FlareNx | keysight_dsox1204g.KeysightDSOX1204G


class ConfigurationError(ValueError):
    """A setup file is malformed or requests unsupported communication settings."""


def _section(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path}: expected a JSON object.")
    missing, unknown = keys - value.keys(), value.keys() - keys
    if missing or unknown:
        raise ConfigurationError(f"{path}: missing keys {sorted(missing)}, unknown keys {sorted(unknown)}.")
    return value


def _text(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ConfigurationError(f"{path}: expected a {'possibly empty ' if allow_empty else 'non-empty '}string.")
    return value


def _positive(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{path}: expected a positive, finite number.")
    return float(value)


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{path}: expected true or false.")
    return value


def _protocol(settings: dict[str, Any], expected: dict[str, Any], path: str) -> None:
    for key, required in expected.items():
        value = settings[key]
        if value != required or isinstance(value, bool) != isinstance(required, bool):
            raise ConfigurationError(f"{path}.{key}: supported value is {required!r}, received {value!r}.")


def _serial_profile(baudrate: int, terminator: bytes) -> dict[str, Any]:
    return {
        'baudrate': baudrate, 'bytesize': serial.EIGHTBITS,
        'parity': serial.PARITY_NONE, 'stopbits': serial.STOPBITS_ONE,
        'xonxoff': False, 'rtscts': False, 'dsrdtr': False,
        'terminator': terminator.decode('ascii'),
    }


def _serial_number(value: Any, path: str) -> str | None:
    return None if value is None else _text(value, path)


def _build_bender(value: Any) -> iseg_nhs.Bender:
    data = _section(value, {'positive_port', 'negative_port', 'positive_serial',
                            'negative_serial', 'max_voltage', 'communication'}, 'bender')
    fixed = _serial_profile(iseg_nhs.BAUDRATE, iseg_nhs.TERMINATOR)
    comm = _section(data['communication'], set(fixed) | {'timeout_s', 'echo_handshake'},
                    'bender.communication')
    _protocol(comm, fixed, 'bender.communication')
    return iseg_nhs.Bender(
        positive_port=_text(data['positive_port'], 'bender.positive_port'),
        negative_port=_text(data['negative_port'], 'bender.negative_port'),
        positive_serial=_serial_number(data['positive_serial'], 'bender.positive_serial'),
        negative_serial=_serial_number(data['negative_serial'], 'bender.negative_serial'),
        max_voltage=_positive(data['max_voltage'], 'bender.max_voltage'),
        timeout=_positive(comm['timeout_s'], 'bender.communication.timeout_s'),
        echo_handshake=_boolean(comm['echo_handshake'], 'bender.communication.echo_handshake'),
    )


def _build_scope(value: Any) -> keysight_dsox1204g.KeysightDSOX1204G:
    data = _section(value, {'resource', 'communication'}, 'oscilloscope')
    comm = _section(data['communication'], {'visa_backend', 'timeout_ms', 'terminator'},
                    'oscilloscope.communication')
    _protocol(comm, {'terminator': keysight_dsox1204g.TERMINATOR}, 'oscilloscope.communication')
    return keysight_dsox1204g.KeysightDSOX1204G(
        resource=_text(data['resource'], 'oscilloscope.resource'),
        timeout_ms=_positive(comm['timeout_ms'], 'oscilloscope.communication.timeout_ms'),
        visa_backend=_text(comm['visa_backend'], 'oscilloscope.communication.visa_backend', allow_empty=True),
    )


def _build_laser(value: Any) -> flare_nx.FlareNx:
    data = _section(value, {'port', 'communication'}, 'laser')
    fixed = _serial_profile(flare_nx.BAUDRATE, flare_nx.CRLF)
    fixed.update(minimum_command_delay_s=flare_nx.MIN_COMMAND_DELAY_S, command_handshake=True)
    comm = _section(data['communication'], set(fixed) | {'timeout_s', 'write_timeout_s', 'handshake_interface'},
                    'laser.communication')
    _protocol(comm, fixed, 'laser.communication')
    interface = comm['handshake_interface']
    if interface is not None:
        interface = _text(interface, 'laser.communication.handshake_interface')
    return flare_nx.FlareNx(
        port=_text(data['port'], 'laser.port'), handshake_interface=interface,
        timeout=_positive(comm['timeout_s'], 'laser.communication.timeout_s'),
        write_timeout=_positive(comm['write_timeout_s'], 'laser.communication.write_timeout_s'),
    )


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(f"Duplicate JSON key: {key!r}.")
        result[key] = value
    return result


def load_devices(path: str | Path) -> dict[str, Device]:
    """Read a UTF-8 JSON file and construct only its listed, disconnected devices.

    Missing files raise OSError; invalid contents raise ConfigurationError.
    Ports, addresses, and physical wiring still require checking at the setup.
    """
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=_unique_keys)
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ConfigurationError(f"Invalid setup file {path}: {exc}") from exc
    builders = {'bender': _build_bender, 'oscilloscope': _build_scope, 'laser': _build_laser}
    if not isinstance(data, dict) or not data:
        raise ConfigurationError('Expected a non-empty JSON object containing device sections.')
    unknown = data.keys() - builders.keys()
    if unknown:
        raise ConfigurationError(f"Unknown device sections: {sorted(unknown)}.")
    devices = {}
    for name, settings in data.items():
        try:
            devices[name] = builders[name](settings)
        except ValueError as exc:
            raise ConfigurationError(f"{name}: {exc}") from exc
    return devices
