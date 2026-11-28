"""Operator-armed acquisition using a physical interlock and external TTL."""
from __future__ import annotations

from collections.abc import Callable
import math
from threading import Event
import time

from lapis_control.devices.laser.flare_nx import FlareNx, SystemStatus
from lapis_control.devices.oscilloscope.keysight_dsox1204g import KeysightDSOX1204G
from .measurement import Measurement, ScopeSetup

MAX_GATE_S = 8.0
DEFAULT_ARM_TIMEOUT_S = 15.0

class ShotTraceError(RuntimeError):
    """One physical pulse was counted, but its trace could not be returned."""

    def __init__(self, count: int) -> None:
        self.count = count
        super().__init__(f'One laser pulse confirmed (counter {count}); trace unavailable.')


def check_cancel(cancel: Event) -> None:
    if cancel.is_set():
        raise InterruptedError('Operation cancelled.')


def _check_armed(laser: FlareNx) -> None:
    faults = laser.read_system_faults()
    status = laser.read_system_status()
    blocking = SystemStatus.FAULT | SystemStatus.WARM_UP | SystemStatus.TEMPERATURE_FAULT
    if faults or status & blocking or not laser.read_laser_enabled():
        raise RuntimeError(f'Laser permission lost (faults 0x{faults:08X}, status 0x{status:08X}).')


def _wait_for_trigger(laser: FlareNx, scope: KeysightDSOX1204G | None, timed: bool,
                      before: int, fire: Event, cancel: Event, deadline: float,
                      check_electrodes: Callable[[], None]) -> bool:
    while True:
        check_cancel(cancel)
        if time.monotonic() >= deadline:
            raise TimeoutError('Arming window expired.')
        _check_armed(laser)
        delta = laser.read_pulse_count() - before
        if delta:
            if timed:
                raise RuntimeError(f'Unexpected laser counter change before WaveGen start: {delta:+d}.')
            return True
        if time.monotonic() >= deadline:
            raise TimeoutError('Arming window expired.')
        if timed and fire.is_set():
            check_electrodes()
            _check_armed(laser)
            if laser.read_pulse_count() != before:
                raise RuntimeError('External pulse detected before requested WaveGen start.')
            if laser.read_system_status() & SystemStatus.TRIGGER_INPUT:
                raise RuntimeError('Trigger input is already active.')
            check_cancel(cancel)
            return scope.timed_ttl_window(MAX_GATE_S, cancel, deadline=deadline)
        cancel.wait(0.1)


def acquire_shot(laser: FlareNx, scope: KeysightDSOX1204G | None, setup: ScopeSetup, *,
                 load: str | None, fire: Event, cancel: Event,
                 progress: Callable[[str], None], arm_timeout_s: float = DEFAULT_ARM_TIMEOUT_S,
                 bender_check: Callable[[], dict] | None = None) -> tuple[int, Measurement | None]:
    """Use one operator arm per attempt. load=None selects an external single-pulse source.

    A timed WaveGen window aims for one pulse; the counter verifies the actual
    result after confirmed OFF. Never repeat ON or reuse a previously armed trace.
    """
    if not math.isfinite(arm_timeout_s) or arm_timeout_s <= 0:
        raise ValueError('Arming timeout must be positive and finite.')
    if load is not None and (scope is None or load not in ('HIGHZ', '50OHM')):
        raise ValueError('Select the connected WaveGen load and connect the scope.')
    check_cancel(cancel)
    faults = laser.read_system_faults()
    if faults or laser.read_laser_enabled() or laser.read_remote_enabled():
        raise RuntimeError(f'Arming requires a closed interlock, zero faults, ENA and REM off (0x{faults:08X}).')
    if laser.read_system_status() & SystemStatus.TRIGGER_INPUT:
        raise RuntimeError('Trigger input must be low before setup.')
    pending = False
    electrode_checks = []

    def check_electrodes() -> None:
        if bender_check is None:
            return
        snapshot = bender_check()
        if electrode_checks:
            for key, reading in snapshot['electrodes'].items():
                old = electrode_checks[0]['electrodes'][key]
                if reading['enabled'] != old['enabled'] or reading['target_v'] != old['target_v']:
                    raise RuntimeError(f'{key}: electrode setting changed after arming.')
        electrode_checks.append(snapshot)

    try:
        if scope is not None:
            if scope.wavegen_state()['output']:
                raise RuntimeError('WaveGen is running. Switch it off before arming.')
            setup.apply(scope)
            if load is not None:
                scope.configure_ttl(load=load, low_v=0, high_v=5, frequency_hz=0.1, pulse_width_s=50e-6)
        laser.configure_single_shot()
        check_cancel(cancel)
        if scope is not None:
            pending = True
            scope.arm_single()
            scope.wait_until_armed(cancel=cancel)
        before = laser.read_pulse_count()
        check_electrodes()
        if scope is not None:
            scope.require_untriggered_single()
        check_cancel(cancel)
        try:
            laser.arm_single_shot()
            deadline = time.monotonic() + arm_timeout_s
            progress('armed')
            edge_seen = _wait_for_trigger(laser, scope, load is not None, before, fire, cancel,
                                          deadline, check_electrodes)
        finally:
            # Independent serial connection, also attempted after a VISA failure.
            # The driver closes the port itself on ambiguous laser enable.
            if laser.is_connected:
                laser.turn_off()
        after = laser.read_pulse_count()
        delta = after - before
        if cancel.is_set() and delta == 0:
            raise InterruptedError('Trigger cancelled; laser OFF and zero pulses confirmed.')
        if delta != 1:
            raise RuntimeError(f'Unconfirmed single shot: {before} → {after} ({delta:+d} pulses).')
        progress('confirmed')
        measurement = None
        if scope is not None:
            try:
                check_cancel(cancel)
                if not edge_seen:
                    raise TimeoutError('No fresh scope trigger inside the WaveGen window.')
                scope.wait_for_single(cancel=cancel)
                pending = False
                measurement = Measurement.download(scope, setup, 'laser', stop=False, shot_counts=(before, after))
            except Exception as exc:
                raise ShotTraceError(after) from exc
            measurement.metadata['trigger_source'] = 'external' if load is None else 'wavegen'
            if load is not None:
                measurement.metadata['gate_limit_s'] = MAX_GATE_S
            if electrode_checks:
                measurement.metadata['bender_checks'] = electrode_checks
        return after, measurement
    finally:
        if pending:
            scope.stop()
