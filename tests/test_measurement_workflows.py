"""Cross-device regressions with simulated instruments and real local files."""
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

import numpy as np

from lapis_control.devices.laser.flare_nx import FlareNx, SystemFault, SystemStatus
from lapis_control.devices.oscilloscope.keysight_dsox1204g import AcquisitionTimeout, KeysightDSOX1204G, Waveform
from lapis_control.workflows.laser_shot import acquire_shot, ShotTraceError
from lapis_control.workflows.measurement import (ChannelSetup, Measurement, ScopeSetup, export_csv,
                                                load_measurement, save_measurement, waveform_statistics)


def scope_setup() -> ScopeSetup:
    return ScopeSetup(tuple(ChannelSetup(i, i <= 2, f'Signal {i}', 1, 0, 1, 'DC')
                            for i in range(1, 5)), 1e-5, 1, 2.5, 'POSITIVE', 10000)


def traces() -> dict[int, Waveform]:
    result = {}
    for ch in (1, 2):
        t = np.arange(5+ch) * ch * 2e-6
        v = np.arange(len(t), dtype=float)
        v[2] = np.nan
        result[ch] = Waveform(ch, t, v, np.arange(len(t), dtype=np.uint16),
                              {'x_increment': ch*2e-6}, np.isnan(v), v==0, v==4)
    return result


class ShotTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.fire, self.cancel = Event(), Event()
        self.count = 500
        self.enabled = False
        self.laser = Mock(spec=FlareNx, is_connected=True)
        self.laser.read_system_faults.return_value = SystemFault(0)
        self.laser.read_remote_enabled.return_value = False
        self.laser.read_system_status.side_effect = lambda: SystemStatus.EMISSION if self.enabled else SystemStatus.STANDBY
        self.laser.read_laser_enabled.side_effect = lambda: self.enabled
        self.laser.read_pulse_count.side_effect = lambda: self.count
        self.laser.arm_single_shot.side_effect = lambda: self.switch(True)
        self.laser.turn_off.side_effect = lambda: self.switch(False)
        self.scope = Mock(spec=KeysightDSOX1204G, idn='DSOX1204G,TEST')
        self.scope.wavegen_state.return_value = {'output': False}
        self.scope.operation_status.return_value = {'waiting_for_trigger': True}
        self.scope.wait_until_armed.side_effect = lambda **kw: self.events.append('scope ready')
        self.scope.timed_ttl_window.side_effect = lambda *a, **kw: self.pulses(1)
        self.scope.wait_for_single.side_effect = lambda **kw: self.events.append('fresh acquisition')
        self.scope.read_waveforms.return_value = traces()
        self.scope.status.return_value = {'idn': self.scope.idn}

    def switch(self, enabled):
        self.enabled = enabled
        self.events.append('laser on' if enabled else 'laser off')

    def pulses(self, count):
        self.assertTrue(self.enabled)
        self.count += count
        self.events.append('gate')
        return True

    def progress(self, stage):
        self.events.append(stage)
        if stage == 'armed':
            self.fire.set()

    def run_shot(self, **kwargs):
        options = dict(load='HIGHZ', cancel=self.cancel, fire=self.fire, progress=self.progress)
        return acquire_shot(self.laser, self.scope, scope_setup(), **{**options, **kwargs})

    def test_one_click_confirms_counter_and_fresh_trace(self):
        count, measurement = self.run_shot()
        self.assertEqual(self.events, ['scope ready', 'laser on', 'armed', 'gate', 'laser off', 'confirmed', 'fresh acquisition'])
        self.assertEqual(count, 501)
        self.assertEqual(measurement.metadata['laser_counts'], (500, 501))
        self.assertEqual(measurement.labels, {1:'Signal 1', 2:'Signal 2'})
        self.scope.configure_ttl.assert_called_once_with(load='HIGHZ', frequency_hz=.1, pulse_width_s=50e-6)
        self.scope.read_waveforms.assert_called_once_with((1, 2), points=10000, stop=False)

    def test_zero_multiple_and_reversed_counts_never_succeed_or_retry(self):
        for delta in (0, 3, -1):
            with self.subTest(delta=delta):
                self.scope.reset_mock()
                self.scope.timed_ttl_window.side_effect = lambda *a, **kw: self.pulses(delta)
                with self.assertRaisesRegex(RuntimeError, 'Unconfirmed single shot'):
                    self.run_shot()
                self.assertFalse(self.enabled)
                self.scope.timed_ttl_window.assert_called_once()
                self.scope.read_waveforms.assert_not_called()
                self.scope.stop.assert_called_once()

    def test_hardware_interlock_and_preexisting_wavegen_block_arming(self):
        self.laser.read_system_faults.return_value = SystemFault.INTERLOCK_OPEN
        with self.assertRaisesRegex(RuntimeError, 'closed interlock'):
            self.run_shot()
        self.laser.read_system_faults.return_value = SystemFault(0)
        self.scope.wavegen_state.return_value = {'output': True}
        with self.assertRaisesRegex(RuntimeError, 'WaveGen is running'):
            self.run_shot()
        self.laser.arm_single_shot.assert_not_called()
        self.scope.timed_ttl_window.assert_not_called()

    def test_interlock_loss_and_cancellation_during_arm_disable_laser(self):
        def fault(stage):
            self.laser.read_system_faults.return_value = SystemFault.INTERLOCK_OPEN
            self.fire.set()

        with self.assertRaisesRegex(RuntimeError, 'permission lost'):
            self.run_shot(progress=fault)
        self.assertFalse(self.enabled)
        self.laser.read_system_faults.return_value = SystemFault(0)
        with self.assertRaises(InterruptedError):
            self.run_shot(progress=lambda _: self.cancel.set())
        self.assertFalse(self.enabled)
        self.scope.timed_ttl_window.assert_not_called()

    def test_expired_arm_or_premature_external_pulse_never_starts_wavegen(self):
        with self.assertRaises(TimeoutError):
            self.run_shot(arm_timeout_s=1e-9)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected laser counter'):
            self.run_shot(progress=lambda _: self.pulses(1))
        self.scope.timed_ttl_window.assert_not_called()
        self.assertFalse(self.enabled)

    def test_transport_failure_and_missing_trigger_do_not_return_old_data(self):
        self.scope.timed_ttl_window.side_effect = OSError('OFF unconfirmed')
        with self.assertRaisesRegex(OSError, 'OFF unconfirmed'):
            self.run_shot()
        self.assertFalse(self.enabled)
        self.scope.timed_ttl_window.side_effect = lambda *a, **kw: self.pulses(1)
        self.scope.wait_for_single.side_effect = AcquisitionTimeout('missing trigger')
        with self.assertRaises(ShotTraceError) as missing:
            self.run_shot()
        self.assertIsInstance(missing.exception.__cause__, AcquisitionTimeout)
        self.assertEqual(missing.exception.count, self.count)
        self.scope.read_waveforms.assert_not_called()

    def test_stale_scope_or_changed_bender_block_trigger(self):
        self.scope.operation_status.side_effect = [{'waiting_for_trigger': True}, {'waiting_for_trigger': False}]
        with self.assertRaisesRegex(RuntimeError, 'no longer waiting'):
            self.run_shot()
        self.scope.operation_status.side_effect = None
        check = Mock(side_effect=[{'electrodes': {'p0': {'enabled': True,'target_v': 15}}},
                                  {'electrodes': {'p0': {'enabled': False,'target_v': 15}}}])
        with self.assertRaisesRegex(RuntimeError, 'electrode setting changed'):
            self.run_shot(bender_check=check)
        self.scope.timed_ttl_window.assert_not_called()
        self.assertFalse(self.enabled)

    def test_late_scope_trigger_cannot_be_attached_to_a_counted_pulse(self):
        def missing_edge(*args, **kwargs):
            self.pulses(1)
            return False

        self.scope.timed_ttl_window.side_effect = missing_edge
        with self.assertRaises(ShotTraceError) as missing:
            self.run_shot()
        self.assertEqual(missing.exception.count, 501)
        self.scope.wait_for_single.assert_not_called()
        self.scope.read_waveforms.assert_not_called()

    def test_external_source_works_without_scope(self):
        count, measurement = acquire_shot(self.laser, None, scope_setup(), load=None, fire=self.fire,
                                         cancel=self.cancel, progress=lambda stage: self.pulses(1) if stage=='armed' else None)
        self.assertEqual(count, 501)
        self.assertIsNone(measurement)
        self.assertFalse(self.enabled)


class TraceFileTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)/'measurement.npz'
        self.record = Measurement(traces(), {1:'OptoSync',2:'Detektor, links'},
                                  {'transferred_utc':'2026-11-26T13:01:02+00:00','source':'single'})

    def test_roundtrip_preserves_every_axis_flag_and_raw_code(self):
        save_measurement(self.path, self.record)
        other = load_measurement(self.path)
        self.assertEqual(other.labels, self.record.labels)
        self.assertEqual(other.metadata, self.record.metadata)
        for ch, original in self.record.traces.items():
            actual = other.traces[ch]
            self.assertEqual(actual.preamble, original.preamble)
            for field in ('time_s','voltage_v','raw','missing','clipped_low','clipped_high'):
                np.testing.assert_equal(getattr(original, field), getattr(actual, field))
        with patch('numpy.savez_compressed', side_effect=OSError('full')):
            with self.assertRaises(OSError):
                save_measurement(self.path, self.record)
        self.assertEqual(load_measurement(self.path).metadata, self.record.metadata)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_csv_independent_lengths_and_statistics_with_gaps(self):
        out = self.path.with_suffix('.csv')
        export_csv(out, self.record)
        with out.open(encoding='utf-8', newline='') as file:
            rows = list(csv.DictReader(file))
        self.assertEqual(len(rows), 13)
        self.assertEqual(rows[-1]['signal'], 'Detektor, links')
        self.assertEqual(float(rows[-1]['time_s']), self.record.traces[2].time_s[-1])
        self.assertEqual(rows[2]['missing'], '1')
        stats = waveform_statistics(self.record.traces[1], slice(1,5))
        self.assertEqual((stats['samples'], stats['missing'], stats['clipped'], stats['peak_to_peak']), (4,1,1,3))
        self.assertAlmostEqual(stats['mean'], 8/3)

    def test_old_curve_download_does_not_apply_draft_settings(self):
        scope = Mock(idn='test')
        scope.read_waveforms.return_value = traces()
        result = Measurement.download(scope, scope_setup(), 'memory', stop=True)
        self.assertNotIn('requested_setup', result.metadata)
        scope.configure_channel.assert_not_called()
        scope.arm_single.assert_not_called()
        scope.read_waveforms.assert_called_once_with((1,2), points=10000, stop=True)
