"""Hold a common voltage magnitude on the four LAPIS bender electrodes."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
import logging
import time

from lapis_control.devices.bender.iseg_nhs import (
    Bender, ChannelStatus, DeviceError, NHSError, PartialUpdateError, VoltageSample, finite,
)

log = logging.getLogger(__name__)
STABLE_SAMPLES = 3
TRIAL_MAX_VOLTAGE_V = 100.0


def _switch_off_and_restore(bender: Bender, original: dict[str, float]) -> None:
    bender.disable()  # Attempts all four OFF commands even after a partial failure.
    deadline = time.monotonic() + 10.0
    while True:
        readings = bender.read_all()
        pending = [key for key, r in readings.items()
                   if r.status & (ChannelStatus.ON | ChannelStatus.RAMPING) or abs(r.voltage) >= 1.0]
        if not pending:
            break
        if time.monotonic() >= deadline:
            raise DeviceError(f'Ramp-down not confirmed on {pending}; check residual voltage')
        time.sleep(0.5)
    restored, failures = [], {}
    for key, (module, channel) in bender.channels.items():
        if abs(original[key]) > min(TRIAL_MAX_VOLTAGE_V, module.max_voltage):
            log.warning('%s: previous setpoint %g V exceeds trial limit; leaving %g V stored',
                        key, original[key], readings[key].voltage_set)
            continue
        try:
            if readings[key].voltage_set != original[key]:
                module.set_voltage(channel, original[key])
            restored.append(key)
        except Exception as exc:
            failures[key] = exc
    if failures:
        raise PartialUpdateError(restored, failures, 'Failed to restore previous bender setpoints')


@contextmanager
def _enabled_for_trial(bender: Bender, targets: dict[str, float]) -> Iterator[None]:
    for module in (bender.positive, bender.negative):
        module.check_regulation_module_status()
    initial = bender.read_all()
    for key, reading in initial.items():
        if (reading.status & ~(ChannelStatus.POSITIVE | ChannelStatus.LOW_CURRENT_RANGE)
                or abs(reading.voltage) >= 1.0):
            raise DeviceError(f'{key}: expected an idle, discharged channel; '
                              f'status {reading.status!r}, measured {reading.voltage:g} V')
    original = {key: reading.voltage_set for key, reading in initial.items()}
    try:
        bender.set_voltages(**targets)
        bender.enable()
        yield
    except BaseException:
        try:
            _switch_off_and_restore(bender, original)
        except Exception:
            log.exception('Additional failure during bender shutdown/restoration; inspect all four channels')
        raise
    else:
        _switch_off_and_restore(bender, original)


def hold_bender_voltage(
    bender: Bender, magnitude_v: float, *, duration_s: float | None,
    initial_settle_s: float = 20.0, settle_interval_s: float = 5.0, hold_interval_s: float = 30.0,
    tolerance_v: float = 0.2, max_step_v: float = 1.0, convergence_timeout_s: float = 90.0,
    on_sample: Callable[[str, VoltageSample, bool], None] | None = None,
) -> bool:
    """Explicitly enable, regulate, then ramp down all four initially idle channels.

    The caller owns the open connection. A finite duration includes initial_settle_s
    of monitoring after all channels are enabled. The setpoint magnitude may range
    from zero to twice the target, up to the common application and trial limits.
    None holds until interrupted. on_sample receives the channel key, signed
    feedback and whether that channel qualified for slow polling. A normal return
    is True if every channel qualified at its most recent observation.
    """
    magnitude = finite(magnitude_v, 'magnitude_v')
    initial_settle = finite(initial_settle_s, 'initial_settle_s')
    fast = finite(settle_interval_s, 'settle_interval_s')
    slow = finite(hold_interval_s, 'hold_interval_s')
    tolerance = finite(tolerance_v, 'tolerance_v')
    step = finite(max_step_v, 'max_step_v')
    timeout = finite(convergence_timeout_s, 'convergence_timeout_s')
    duration = None if duration_s is None else finite(duration_s, 'duration_s')
    if initial_settle < 0:
        raise ValueError('initial_settle_s must be non-negative')
    if not 0 < fast <= slow or timeout <= fast or min(tolerance, step) <= 0:
        raise ValueError('Require 0 < settle interval <= hold interval, timeout > settle interval, '
                         'and positive tolerance and step')
    if duration is not None and duration <= fast:
        raise ValueError('duration_s must exceed the settling poll interval')
    limit = min(TRIAL_MAX_VOLTAGE_V, bender.positive.max_voltage, bender.negative.max_voltage)
    if not 0 < magnitude <= limit:
        raise ValueError(f'Target magnitude must be positive and at most {limit:g} V')
    setpoint_range = (0.0, min(2.0 * magnitude, limit))
    targets = {key: module.sign * magnitude for key, (module, _) in bender.channels.items()}
    consecutive = dict.fromkeys(targets, 0)
    with _enabled_for_trial(bender, targets):
        start = time.monotonic()
        feedback = {
            key: module.regulate_voltage(channel, targets[key], interval_s=None, tolerance_v=tolerance,
                                         max_step_v=step, setpoint_range_v=setpoint_range,
                                         measured_limit_v=limit, observe_until=start + initial_settle,
                                         convergence_timeout_s=timeout)
            for key, (module, channel) in bender.channels.items()
        }
        end = None if duration is None else start + duration
        due = dict.fromkeys(targets, start + fast)
        while True:
            key = min(due, key=due.__getitem__)
            wake = due[key] if end is None else min(due[key], end)
            delay = wake - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            if end is not None and time.monotonic() >= end:
                break
            try:
                sample = next(feedback[key])
            except NHSError as exc:
                log.error('Voltage regulation failed on %s: %s', key, exc)
                raise
            consecutive[key] = consecutive[key] + 1 if sample.in_tolerance else 0
            holding = consecutive[key] >= STABLE_SAMPLES
            due[key] = time.monotonic() + (slow if holding else fast)
            if on_sample is not None:
                on_sample(key, sample, holding)
    return all(count >= STABLE_SAMPLES for count in consecutive.values())
