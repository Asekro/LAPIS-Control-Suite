"""Four-electrode trial with simulated NHS modules and a monotonic test clock."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lapis_control.devices.bender.iseg_nhs import (
    Bender, ChannelStatus, CommunicationError, DeviceError, PartialUpdateError, VerificationError,
)
from lapis_control.devices.bender.tests.test_iseg_nhs import FakeSerial
from lapis_control.workflows.bender_voltage import hold_bender_voltage


class TrialSerial(FakeSerial):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.volts[:] = [11.0, 12.0, 75.0, 0.0, 0.0, 0.0]
        self.offsets = [0.0] * 6

    def write(self, data):
        if data.startswith(b':MEAS:VOLT?'):
            channel = int(data.split(b'(@')[1].split(b')')[0])
            self.measurement_offset = self.offsets[channel] if self.on[channel] else -self.volts[channel]
        return super().write(data)


class BenderVoltageTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        for target, replacement in [('time.monotonic', lambda: self.now), ('time.sleep', self.advance)]:
            timer = patch(target, replacement)
            timer.start()
            self.addCleanup(timer.stop)
        self.bender = Bender('P', 'N', max_voltage=100, serial_factory=TrialSerial).connect()
        self.addCleanup(self.bender.close)
        self.pos, self.neg = self.bender.positive._serial, self.bender.negative._serial

    def advance(self, seconds):
        self.assertGreater(seconds, 0.0)
        self.now += seconds

    def assert_restored(self):
        for device in (self.pos, self.neg):
            self.assertFalse(any(device.on))
            self.assertEqual(device.volts, [11, 12, 75, 0, 0, 0])
            writes = [cmd for cmd in device.sent if ';' in cmd]
            self.assertTrue(all(cmd.startswith(':VOLT ') and cmd.endswith((',(@0);*OPC?', ',(@1);*OPC?'))
                                for cmd in writes))

    def test_independent_acquisition_hold_and_negative_channel_drift(self):
        events = {key: [] for key in self.bender.channels}
        self.pos.offsets[0] = -1.0

        def record(key, sample, holding):
            events[key].append((self.now, holding, sample.requested_voltage))
            if key == 'n1' and holding and self.now == 15:
                self.neg.offsets[1] = -0.6

        stable = hold_bender_voltage(self.bender, 15, duration_s=80, initial_settle_s=0,
                                     convergence_timeout_s=20, on_sample=record)
        self.assertTrue(stable)
        self.assertEqual(self.now, 80)
        steady = [(5, False, None), (10, False, None), (15, True, None),
                  (45, True, None), (75, True, None)]
        self.assertEqual(events['n0'], steady)
        self.assertEqual(events['p1'], steady)
        self.assertEqual(events['p0'][0], (5, False, 15.5))
        self.assertEqual(events['n1'][3][:2], (45, False))
        self.assertAlmostEqual(events['n1'][3][2], -15.3)
        self.assertEqual(events['n1'][4][0], 50)
        self.assertTrue(events['n1'][-1][1])
        self.assert_restored()

    def test_10v_startup_observes_then_only_corrects_persistent_error(self):
        events = {key: [] for key in self.bender.channels}
        self.pos.offsets[0] = 15.0  # Startup readback above the 20 V trim ceiling.
        self.neg.offsets[1] = 2.0   # Persistent error requires |VSET| below 10 V.

        def record(key, sample, holding):
            events[key].append((self.now, sample, holding))
            if key == 'p0' and self.now == 15:
                self.pos.offsets[0] = 0.0

        self.assertTrue(hold_bender_voltage(self.bender, 10, duration_s=65, on_sample=record))
        for samples in events.values():
            self.assertEqual([t for t, s, _ in samples if s.observing], [5, 10, 15])
            for t, sample, holding in samples:
                if t < 20:
                    self.assertFalse(holding)
                    self.assertIsNone(sample.requested_voltage)
                    self.assertEqual(abs(sample.voltage_set), 10)
        self.assertEqual(events['p0'][0][1].voltage, 25)
        self.assertTrue(all(s.requested_voltage is None for _, s, _ in events['p0']))
        self.assertEqual([t for t, _, holding in events['p0'] if holding], [30, 60])
        first_change = next((t, s) for t, s, _ in events['n1'] if s.requested_voltage is not None)
        self.assertEqual((first_change[0], first_change[1].requested_voltage), (20, -9))
        self.assert_restored()

    def test_target_relative_and_absolute_setpoint_caps(self):
        for target, pos_cap, neg_cap, expected in [(5, 100, 100, 10), (65, 150, 150, 100), (45, 100, 60, 60)]:
            with self.subTest(target=target):
                self.bender.positive.max_voltage = pos_cap
                self.bender.negative.max_voltage = neg_cap
                self.pos.offsets[0] = -target
                commands = []

                def record(key, sample, holding):
                    if key == 'p0' and sample.requested_voltage is not None:
                        commands.append(sample.requested_voltage)
                        self.pos.offsets[0] = -sample.requested_voltage  # Readback stays zero.

                with self.assertLogs('lapis_control.workflows.bender_voltage', level='ERROR'):
                    with self.assertRaisesRegex(VerificationError, f'trim limit at {expected} V'):
                        hold_bender_voltage(self.bender, target, duration_s=60, initial_settle_s=0,
                                            max_step_v=50, on_sample=record)
                self.assertEqual(max(commands), expected)
                self.assert_restored()

    def test_absolute_readback_limit_protects_during_startup(self):
        self.neg.offsets[0] = 91
        with self.assertLogs('lapis_control.workflows.bender_voltage', level='ERROR'):
            with self.assertRaisesRegex(VerificationError, 'exceeds 100 V'):
                hold_bender_voltage(self.bender, 10, duration_s=60)
        self.assertEqual(self.now, 5)
        self.assert_restored()

    def test_invalid_or_energized_start_does_not_write(self):
        for magnitude, options in [(101, {}), (0, {}), (-10, {}), (15, {'initial_settle_s': -1}),
                                   (15, {'initial_settle_s': float('nan')}),
                                   (15, {'hold_interval_s': 2}), (15, {'tolerance_v': 0})]:
            with self.assertRaises(ValueError):
                hold_bender_voltage(self.bender, magnitude, duration_s=60, **options)
        self.neg.on[1] = True
        with self.assertRaisesRegex(DeviceError, 'n1'):
            hold_bender_voltage(self.bender, 15, duration_s=60)
        self.assertTrue(self.neg.on[1])
        self.assertFalse(any(';' in cmd for device in (self.pos, self.neg) for cmd in device.sent))

    def test_interruption_and_partial_enable_restore_all_four(self):
        def interrupt(*args):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            hold_bender_voltage(self.bender, 15, duration_s=None, on_sample=interrupt)
        self.assert_restored()
        with patch.object(self.bender.negative, 'enable', side_effect=DeviceError('cannot enable')):
            with self.assertRaises(PartialUpdateError):
                hold_bender_voltage(self.bender, 15, duration_s=60)
        self.assert_restored()

    def test_ramp_never_qualifies_and_times_out_without_correction(self):
        def ramp(key, sample, holding):
            if key == 'n1':
                self.pos.extra_status = ChannelStatus.RAMPING
            if key == 'p0' and sample.status & ChannelStatus.RAMPING:
                self.assertFalse(holding)
                self.assertIsNone(sample.requested_voltage)

        # The simulated ramp flag clears when OFF is requested.
        disable = self.bender.disable
        def disable_and_clear_ramp():
            disable()
            self.pos.extra_status = ChannelStatus(0)

        with patch.object(self.bender, 'disable', side_effect=disable_and_clear_ramp):
            with self.assertLogs('lapis_control.workflows.bender_voltage', level='ERROR'):
                with self.assertRaisesRegex(VerificationError, 'did not settle'):
                    hold_bender_voltage(self.bender, 15, duration_s=60,
                                        convergence_timeout_s=12, on_sample=ramp)
        self.assertGreaterEqual(self.now, 32)
        self.assert_restored()

    def test_communication_loss_preserves_error_and_turns_off_reachable_module(self):
        def disconnect(key, sample, holding):
            if key == 'n0':
                self.neg.fail_write = True

        with self.assertLogs('lapis_control.workflows.bender_voltage', level='ERROR') as messages:
            with self.assertRaises(CommunicationError):
                hold_bender_voltage(self.bender, 15, duration_s=60, on_sample=disconnect)
        self.assertTrue(any('shutdown' in msg for msg in messages.output))
        self.assertFalse(any(self.pos.on))
        self.assertTrue(self.neg.on[0])
