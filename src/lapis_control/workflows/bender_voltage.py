"""Regulate and monitor the four LAPIS bender electrodes."""
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
import logging
import math
from queue import Empty, Queue
from threading import Event
import time

from lapis_control.devices.bender.iseg_nhs import (
    Bender, ChannelStatus, DeviceError, NHSError, PartialUpdateError, VoltageSample, finite,
)

log = logging.getLogger(__name__)
STABLE_SAMPLES = 3
TRIAL_MAX_VOLTAGE_V = 100.0


def ramp_down(bender: Bender) -> dict:
    """Request OFF on all electrodes and verify the measured discharge."""
    bender.disable()  # Attempts all four OFF commands even after a partial failure.
    deadline = time.monotonic() + 10.0
    while True:
        readings = bender.read_all()
        pending = [key for key, r in readings.items()
                   if r.status & (ChannelStatus.ON | ChannelStatus.RAMPING) or abs(r.voltage) >= 1.0]
        if not pending:
            return readings
        if time.monotonic() >= deadline:
            raise DeviceError(f'Ramp-down not confirmed on {pending}; check residual voltage')
        time.sleep(0.5)


def _switch_off_and_restore(bender: Bender, original: dict[str, float]) -> None:
    readings = ramp_down(bender)
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
def _enabled_for_trial(bender: Bender, targets: dict[str, float], allow_active: bool,
                       stop_event: Event | None, on_control: Callable[[bool], None] | None) -> Iterator[None]:
    for module in (bender.positive, bender.negative):
        module.check_regulation_module_status()
    initial = bender.read_all()
    active = allow_active and all(r.status & ChannelStatus.ON for r in initial.values())
    for key, reading in initial.items():
        if active:
            if not math.isclose(reading.voltage_set, targets[key], abs_tol=0.01):
                raise DeviceError(f'{key}: apply the target setpoints before enabling regulation')
            module, channel = bender.channels[key]
            module.read_regulation_status(channel)
            continue
        if (reading.status & ~(ChannelStatus.POSITIVE | ChannelStatus.LOW_CURRENT_RANGE)
                or abs(reading.voltage) >= 1.0):
            raise DeviceError(f'{key}: expected an idle, discharged channel; '
                              f'status {reading.status!r}, measured {reading.voltage:g} V')
    original = {key: reading.voltage_set for key, reading in initial.items()}
    if on_control:
        on_control(True)
    try:
        if not active and not (stop_event and stop_event.is_set()):
            bender.set_voltages(**targets)
            if not (stop_event and stop_event.is_set()):
                bender.enable(cancel=stop_event)
        yield
    except BaseException as exc:
        try:
            _switch_off_and_restore(bender, original)
            if on_control:
                on_control(False)
        except Exception as cleanup_error:
            log.exception('Additional failure during bender shutdown/restoration; inspect all four channels')
            raise exc from cleanup_error
        raise
    else:
        _switch_off_and_restore(bender, original)
        if on_control:
            on_control(False)


def regulation_targets(bender: Bender, values: Mapping[str, float]) -> dict[str, float]:
    """Validate the complete target group against its configured limits without device I/O."""
    if values.keys() != bender.channels.keys():
        raise ValueError('Supply one signed target for each electrode')
    limit = min(TRIAL_MAX_VOLTAGE_V, bender.positive.max_voltage, bender.negative.max_voltage)
    targets = {key: finite(values[key], key) for key in bender.channels}
    for key, (module, _) in bender.channels.items():
        if not 0 < targets[key] * module.sign <= limit:
            raise ValueError(f'{key}: require the correct polarity and a magnitude in (0, {limit:g}] V')
    return targets


def _apply_changed_targets(bender: Bender, changed: dict[str, float], expected: dict[str, float],
                           limit: float, stop: Event | None) -> None:
    """Preflight every changed electrode before the first live voltage write."""
    for key, target in changed.items():
        module, channel = bender.channels[key]
        module.validate_voltage(channel, target)
        module.read_regulation_status(channel)
        setpoint = module.read_voltage_set(channel)
        if not math.isclose(setpoint, expected[key], abs_tol=0.01, rel_tol=1e-6):
            raise DeviceError(f'{key}: VSET changed from {expected[key]:g} to {setpoint:g} V before target change')
        measured = module.read_voltage(channel)
        if abs(measured) > limit:
            raise DeviceError(f'{key}: measured {measured:g} V exceeds {limit:g} V magnitude limit')
    for key, target in changed.items():
        if stop and stop.is_set():
            return
        module, channel = bender.channels[key]
        module.set_voltage(channel, target)


def hold_bender_voltage(
    bender: Bender, magnitude_v: float | Mapping[str, float], *, duration_s: float | None,
    initial_settle_s: float = 20.0, settle_interval_s: float = 5.0, hold_interval_s: float = 30.0,
    tolerance_v: float = 0.2, max_step_v: float = 1.0, convergence_timeout_s: float = 90.0,
    on_sample: Callable[[str, VoltageSample, bool], None] | None = None,
    stop_event: Event | None = None, allow_active: bool = False,
    on_control: Callable[[bool], None] | None = None,
    updates: Queue[dict[str, float]] | None = None,
    on_targets: Callable[[dict[str, float]], None] | None = None,
) -> bool:
    """Take control of four electrodes, regulate their voltages, then ramp down.

    The caller owns the open connection. A finite duration includes initial_settle_s
    of monitoring after all channels are enabled. The setpoint magnitude may range
    from zero to twice the target, up to the common application and trial limits.
    A mapping supplies individual signed targets. With allow_active, a fully
    enabled group can be taken over at its existing programmed setpoints.
    An event interrupts the polling wait and initiates verified ramp-down.
    None holds until interrupted. on_sample receives the channel key, signed
    feedback and whether that channel qualified for slow polling. A normal return
    is True if every channel qualified at its most recent observation. An optional
    queue supplies complete replacement targets. Changed electrodes restart their
    settling period. on_targets reports the goals after hardware acknowledgement.
    """
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
    if isinstance(magnitude_v, Mapping):
        targets = regulation_targets(bender, magnitude_v)
    else:
        magnitude = finite(magnitude_v, 'magnitude_v')
        targets = regulation_targets(bender, {key: module.sign * magnitude
                                              for key, (module, _) in bender.channels.items()})
    if stop_event and stop_event.is_set():
        raise InterruptedError('Regulation cancelled before taking control.')

    def feedback_for(key: str, target: float, now: float) -> Iterator[VoltageSample]:
        module, channel = bender.channels[key]
        return module.regulate_voltage(channel, target, interval_s=None, tolerance_v=tolerance,
                                       max_step_v=step, setpoint_range_v=(0.0, min(2 * abs(target), limit)),
                                       measured_limit_v=limit, observe_until=now + initial_settle,
                                       convergence_timeout_s=timeout)

    consecutive = dict.fromkeys(targets, 0)
    expected_set = targets.copy()
    with _enabled_for_trial(bender, targets, allow_active, stop_event, on_control):
        start = time.monotonic()
        feedback = {key: feedback_for(key, target, start) for key, target in targets.items()}
        if on_targets and not (stop_event and stop_event.is_set()):
            on_targets(targets.copy())
        end = None if duration is None else start + duration
        due = dict.fromkeys(targets, start + fast)
        while not (stop_event and stop_event.is_set()):
            key = min(due, key=due.__getitem__)
            wake = due[key] if end is None else min(due[key], end)
            delay = wake - time.monotonic()
            proposed = None
            if updates is not None:
                # A target submission wakes the worker even during a long hold interval.
                # Bound the wait to service the independent stop event promptly.
                try:
                    proposed = updates.get(timeout=max(0., min(delay, 0.1)))
                except Empty:
                    pass
            elif delay > 0:
                if stop_event is None:
                    time.sleep(delay)
                elif stop_event.wait(delay):
                    break
            if (stop_event and stop_event.is_set()) or (end is not None and time.monotonic() >= end):
                break
            if proposed is not None:
                replacement = regulation_targets(bender, proposed)
                changed = {k: v for k, v in replacement.items() if targets[k] != v}
                _apply_changed_targets(bender, changed, expected_set, limit, stop_event)
                if stop_event and stop_event.is_set():
                    break
                now = time.monotonic()
                for electrode, target in changed.items():
                    feedback[electrode] = feedback_for(electrode, target, now)
                    consecutive[electrode] = 0
                    due[electrode] = now + fast
                targets = replacement
                expected_set.update(changed)
                if on_targets:
                    on_targets(targets.copy())
                continue
            if time.monotonic() < wake:
                continue
            try:
                sample = next(feedback[key])
            except NHSError as exc:
                log.error('Voltage regulation failed on %s: %s', key, exc)
                raise
            consecutive[key] = consecutive[key] + 1 if sample.in_tolerance else 0
            expected_set[key] = sample.voltage_set if sample.requested_voltage is None else sample.requested_voltage
            holding = consecutive[key] >= STABLE_SAMPLES
            due[key] = time.monotonic() + (slow if holding else fast)
            if on_sample is not None:
                on_sample(key, sample, holding)
    return all(count >= STABLE_SAMPLES for count in consecutive.values())
