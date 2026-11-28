"""Device diagnostics and operations shared by the desktop application."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import logging
from threading import Event
import time

from lapis_control.configuration import Device
from lapis_control.devices.bender.iseg_nhs import Bender, ChannelStatus, PartialUpdateError
from lapis_control.devices.laser.flare_nx import FlareNx, SystemFault
from lapis_control.devices.oscilloscope.keysight_dsox1204g import KeysightDSOX1204G
from lapis_control.devices.vacuum.edwards_tic import EdwardsTIC, EdwardsTicError
from .bender_voltage import ramp_down
from .measurement import Measurement, ScopeSetup

log = logging.getLogger(__name__)


def address(device: Device) -> str:
    if isinstance(device, Bender):
        return f'{device.positive.port} / {device.negative.port}'
    if isinstance(device, KeysightDSOX1204G):
        return device.resource
    return device.port


def laser_status(laser: FlareNx) -> dict:
    faults = laser.read_system_faults()
    status = laser.read_system_status()
    return dict(controller=laser.query_parameter('*IDN?'), interlock_open=bool(faults & SystemFault.INTERLOCK_OPEN),
                interlock_fault=bool(faults & SystemFault.INTERLOCK_FAULT), faults=f'0x{faults:08X}',
                status=f'0x{status:08X}', enabled=laser.read_laser_enabled(),
                mode=laser.read_pulse_mode(), count=laser.read_pulse_count(),
                temperature_c=laser.read_head_temperature_c(), remote=laser.read_remote_enabled())


def bender_status(bender: Bender) -> dict:
    return dict(channels=bender.read_all(), positive=asdict(bender.positive.read_module()),
                negative=asdict(bender.negative.read_module()))


def pump_status(tic: EdwardsTIC) -> dict:
    """Keep diagnostics from available objects when an optional pump is absent."""
    report = {}
    for name, reader in (
        ('turbo_state', tic.read_turbo_pump_state), ('turbo_drive', tic.read_next_speed_and_status),
        ('turbo_power', tic.read_turbo_power), ('turbo_temp', tic.read_next_temperatures),
        ('backing_state', tic.read_backing_pump_state), ('backing_drive', tic.read_nxds_status),
        ('backing_power', tic.read_backing_power), ('backing_temp', tic.read_nxds_temperatures),
    ):
        try:
            report[name] = reader()
        except EdwardsTicError as exc:
            log.warning("Pump diagnostic %s on %s failed", name, tic.port, exc_info=True)
            report[name] = {'error': str(exc)}
    return report


class BenderControl:
    """Track manually managed HV so releasing the application ramps it down."""

    def __init__(self, bender: Bender) -> None:
        self.bender = bender
        self.managed = False

    def set_targets(self, targets: dict[str, float]) -> None:
        try:
            self.bender.set_voltages(**targets)
        except PartialUpdateError:
            self.managed = True  # A failed write can still have reached the instrument.
            raise
        self.managed = True

    def enable(self, targets: dict[str, float], cancel: Event) -> None:
        for module in (self.bender.positive, self.bender.negative):
            module.check_regulation_module_status()
        readings = self.bender.read_all()
        if any(r.status & ~(ChannelStatus.POSITIVE | ChannelStatus.LOW_CURRENT_RANGE)
               or abs(r.voltage) >= 1 for r in readings.values()):
            raise RuntimeError('Manual enable requires four idle, discharged electrodes.')
        if cancel.is_set():
            raise InterruptedError('HV enable cancelled.')
        self.set_targets(targets)
        try:
            self.bender.enable(cancel=cancel)
        except Exception:
            self.disable()
            raise

    def disable(self) -> None:
        ramp_down(self.bender)
        self.managed = False

    def close(self) -> None:
        try:
            if self.managed:
                self.disable()
        finally:
            self.bender.close()

    def shot_snapshot(self, targets: dict[str, float] | None, tolerance_v: float,
                      cancel: Event) -> dict:
        """Read fresh electrode voltages and flags, including idle electrodes.

        During regulation the reference is the feedback target, which can differ
        from the programmed VSET. In manual operation the reference is VSET.
        """
        deadline = time.monotonic() + 5.0
        result = {}
        for key, (module, ch) in self.bender.channels.items():
            if cancel.is_set():
                raise InterruptedError('Bender check cancelled.')
            status = module.read_status(ch)
            enabled = bool(status & ChannelStatus.ON)
            if enabled:
                status = module.read_regulation_status(ch)
            elif targets or status & ~(ChannelStatus.POSITIVE | ChannelStatus.LOW_CURRENT_RANGE):
                raise RuntimeError(f'{key}: unexpected channel state (0x{status:04X}).')
            setpoint = module.read_voltage_set(ch)
            measured = module.read_voltage(ch)
            target = targets[key] if targets else setpoint
            if (status & ChannelStatus.RAMPING or (not enabled and abs(measured) >= 1.0)
                    or (enabled and abs(measured-target) > tolerance_v + 1e-9)):
                raise RuntimeError(f'{key}: voltage not settled ({measured:g} V, VSET {setpoint:g} V).')
            if time.monotonic() >= deadline:
                raise TimeoutError('Bender readiness check exceeded 5 s.')
            result[key] = dict(enabled=enabled, target_v=target, setpoint_v=setpoint,
                               measured_v=measured, status=int(status))
        return dict(utc=datetime.now(timezone.utc).isoformat(), electrodes=result)


def capture(scope: KeysightDSOX1204G, setup: ScopeSetup, cancel: Event) -> Measurement:
    setup.apply(scope)
    if cancel.is_set():
        raise InterruptedError('Acquisition cancelled.')
    try:
        scope.arm_single()
    except Exception:
        # The instrument may have accepted SINGLE before reporting an error.
        scope.stop()
        raise
    scope.wait_for_single(timeout_s=15, cancel=cancel)
    return Measurement.download(scope, setup, 'single', stop=False)
