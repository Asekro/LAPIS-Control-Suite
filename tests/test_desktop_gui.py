"""Click through the desktop application using isolated device doubles."""
from __future__ import annotations

import os
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import unittest
from unittest.mock import Mock, patch

from lapis_control.devices.laser.flare_nx import FlareNx, SystemFault, SystemStatus
from lapis_control.devices.bender.iseg_nhs import Bender
from lapis_control.workflows.laboratory import BenderControl
from tests.test_bender_voltage import TrialSerial
from lapis_control.devices.oscilloscope.keysight_dsox1204g import KeysightDSOX1204G
from lapis_control.devices.oscilloscope.tests.test_driver import Instrument, Manager
from lapis_control.devices.vacuum.edwards_tic import EdwardsTIC, EdwardsTicProtocolError

try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog
    from lapis_control.gui.diagnostics import session_log
    from lapis_control.gui.window import MainWindow
except ModuleNotFoundError as exc:
    if exc.name != 'PySide6':
        raise
    QApplication = None


class AcquisitionInstrument(Instrument):
    """Generate a fresh scope trigger and laser count only when output turns on."""
    def __init__(self):
        super().__init__()
        self.counter = 700

    def write(self, command):
        size = super().write(command)
        if command == ':SINGle':
            self.values[':TER?'] = '0'
            self.values[':OPERegister:CONDition?'] = '40'
        elif command == ':WGEN:OUTPut 1':
            self.counter += 1
            self.values[':TER?'] = '1'
            self.values[':OPERegister:CONDition?'] = '0'
        elif command.startswith((':WGEN:FREQuency ', ':WGEN:FUNCtion:PULSe:WIDTh ')):
            key, value = command.split()
            self.values[key+'?'] = value
        return size

    def query(self, command):
        answer = super().query(command)
        if command == ':AER?' and answer == '1':
            self.values[':AER?'] = '0'
            # Model a device where Wait Trig clears with AER while Single keeps running.
            status = int(self.values[':OPERegister:CONDition?'])
            self.values[':OPERegister:CONDition?'] = str(status & ~32)
        return answer


@unittest.skipUnless(QApplication, 'PySide6 GUI extra is optional')
class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        config = Path(__file__).resolve().parents[1]/'config'/'devices.example.json'
        self.w = MainWindow(config)
        self.w.timer.stop()
        self.inst = AcquisitionInstrument()
        self.w.devices['oscilloscope'] = KeysightDSOX1204G('TEST', resource_manager=Manager(self.inst))
        self.enabled = False
        self.laser = Mock(spec=FlareNx, port='TEST-L', is_connected=True)
        self.laser.read_system_faults.return_value = SystemFault(0)
        self.laser.read_system_status.side_effect = lambda: SystemStatus.EMISSION if self.enabled else SystemStatus.STANDBY
        self.laser.read_laser_enabled.side_effect = lambda: self.enabled
        self.laser.read_remote_enabled.return_value = False
        self.laser.read_pulse_mode.return_value = 3
        self.laser.read_pulse_count.side_effect = lambda: self.inst.counter
        self.laser.read_head_temperature_c.return_value = 25.1
        self.laser.query_parameter.return_value = 'test laser'
        self.laser.arm_single_shot.side_effect = lambda: setattr(self, 'enabled', True)
        self.laser.turn_off.side_effect = lambda: setattr(self, 'enabled', False)
        self.w.devices['laser'] = self.laser
        self.vac = Mock(spec=EdwardsTIC, port='TEST-V')
        self.vac.read_pressure_pa.return_value = 2.3e-6
        for name in ('read_turbo_pump_state', 'read_backing_pump_state'):
            getattr(self.vac, name).return_value = dict(state='Running')
        self.vac.read_next_speed_and_status.return_value = dict(speed_rpm=60000)
        self.vac.read_nxds_status.return_value = dict(frequency_hz=90)
        for name in ('read_turbo_power','read_backing_power'):
            getattr(self.vac, name).return_value = dict(power_w=50)
        for name in ('read_next_temperatures','read_nxds_temperatures'):
            getattr(self.vac, name).return_value = dict(controller_temperature_c=31)
        self.w.devices['vacuum'] = self.vac
        self.w.show()
        self.qt.processEvents()

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            self.qt.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail('Timed out waiting for the GUI. Status: '+self.w.footer.text())

    def idle(self):
        self.wait_for(lambda: not self.w.jobs.active)

    def connect(self, key):
        self.w.panels[key].connection.click()
        self.idle()
        self.assertIn(key, self.w.connected)

    def tearDown(self):
        self.w.unsaved.clear()
        self.w.close()
        if self.w.isVisible():
            self.w.timer.start(10)
            self.wait_for(lambda: not self.w.isVisible())
        self.qt.processEvents()

    def prepare_shot(self):
        self.connect('laser')
        self.connect('oscilloscope')
        self.w.scope.channels[0][1].setText('OptoSync')
        self.w.scope.channels[1][0].setChecked(True)
        self.w.scope.channels[1][1].setText('Detektor')
        self.w.nav['laser'].click()
        self.assertTrue(self.w.laser.arm.isEnabled())
        self.w.laser.arm.click()
        self.wait_for(self.w.laser.fire.isEnabled)
        self.assertTrue(self.enabled)
        self.assertFalse(self.w.scope.single.isEnabled())
        for command in (':WGEN:OUTPut:LOAD ONEMeg', ':WGEN:FUNCtion PULSe', ':WGEN:VOLTage 5',
                        ':WGEN:VOLTage:OFFSet 2.5', ':WGEN:FUNCtion:PULSe:WIDTh 5e-05'):
            self.assertIn(command, self.inst.commands)
        self.assertNotIn(':WGEN:OUTPut:LOAD FIFTy', self.inst.commands)
        self.assertNotIn(':WGEN:OUTPut 1', self.inst.commands)

    def test_monitoring_shot_transfer_local_export_and_offline_reopen(self):
        self.assertFalse(self.w.connected)
        self.assertFalse(self.w.laser.fire.isEnabled())
        self.assertFalse(self.inst.commands)
        self.connect('vacuum')
        self.vac.read_turbo_pump_state.assert_not_called()
        self.w.vacuum.monitor.click()
        self.prepare_shot()
        self.w.refresh('vacuum')
        self.wait_for(lambda: not self.w.jobs.busy('vacuum'))
        self.assertIn('2.300e-08', self.w.rail_pressure.text())
        self.assertIn('60000 U/min', self.w.vacuum.pump_labels['turbo'].text())
        self.vac.read_turbo_pump_state.assert_called_once_with()
        self.w.laser.fire.click()
        self.w.laser.fire.click()
        self.idle()
        self.assertEqual(self.w.selection, 'data')
        self.assertEqual(self.w.data.channel.count(), 2)
        self.assertIn('geclippt', self.w.data.quality.text())
        self.assertFalse(self.enabled)
        self.assertEqual(self.inst.commands.count(':WGEN:OUTPut 1'), 1)
        self.assertEqual(self.inst.values[':WGEN:OUTPut?'], '0')
        metadata = self.w.data.measurement.metadata
        self.assertEqual(metadata['laser_counts'], (700, 701))
        self.assertEqual(metadata['pressure_at_arm_request']['pressure_pa'], 2.3e-6)
        with TemporaryDirectory() as folder:
            path = Path(folder)/'trace.npz'
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(path), '')):
                self.w.data.save.click()
                self.idle()
            self.assertTrue(path.is_file())
            self.w.data.remove.click()
            self.assertFalse(self.w.records)
            self.assertIsNone(self.w.data.measurement)
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(path), '')):
                self.w.data.open.click()
                self.idle()
            self.w.data.channel.setCurrentIndex(1)
            self.assertEqual(self.w.data.channel.currentText(), 'CH2 · Detektor')
            self.assertFalse(self.w.unsaved)
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(path.with_suffix('.csv')), '')):
                self.w.data.csv.click()
                self.idle()
            self.assertIn('Detektor', path.with_suffix('.csv').read_text(encoding='utf-8'))

    def test_armed_banner_keeps_countdown_during_status_updates(self):
        self.prepare_shot()
        banner = self.w.shot_message
        self.assertRegex(banner.text(), r'^Laser scharf · noch \d+ s$')
        self.w.navigate('overview')
        with patch('lapis_control.gui.window.time', wraps=time) as clock:
            for seconds in (12, 11):
                clock.monotonic.return_value = self.w.arm_deadline - seconds + 0.25
                expected = f'Laser scharf · noch {seconds} s'
                self.w.tick()
                self.assertEqual(banner.text(), expected)
                self.w.jobs.changed.emit()
                self.assertEqual(banner.text(), expected)
                self.assertEqual(self.w.laser.phase.text(), expected)
        self.w.shot_stop.click()
        self.w.tick()
        self.assertIn('Abbruch angefordert', banner.text())
        self.idle()

    def test_close_while_armed_disarms_and_releases_without_trigger(self):
        timeout = self.w.laser.arm_timeout
        self.assertEqual(timeout.value(), 15)
        timeout.setValue(45)
        before = time.monotonic()
        self.prepare_shot()
        self.assertFalse(timeout.isEnabled())
        self.assertGreaterEqual(self.w.arm_deadline, before + 45)
        self.assertLessEqual(self.w.arm_deadline, time.monotonic() + 45)
        self.w.close()
        self.assertTrue(self.w.closing)
        self.w.timer.start(10)
        self.wait_for(lambda: not self.w.isVisible())
        self.assertFalse(self.enabled)
        self.assertFalse(self.w.connected)
        self.assertNotIn(':WGEN:OUTPut 1', self.inst.commands)
        self.laser.close.assert_called_once()
        self.assertTrue(self.inst.closed)

    def test_selected_external_arm_timeout_disarms_without_pulse(self):
        self.connect('laser')
        page = self.w.laser
        page.source.setCurrentIndex(1)
        page.arm_timeout.setValue(1)
        with self.assertLogs('lapis_control', 'ERROR'):
            page.arm.click()
            self.wait_for(lambda: self.w.armed)
            self.assertTrue(self.enabled)
            self.assertFalse(page.arm_timeout.isEnabled())
            self.idle()
        self.assertIn('Arming window expired', page.phase.text())
        self.laser.turn_off.assert_called_once_with()
        self.assertFalse(self.enabled)
        self.assertEqual(self.inst.counter, 700)
        self.assertTrue(page.arm_timeout.isEnabled())

    def test_laser_controls_stay_enabled_and_arm_once_after_all_reads(self):
        bender = Bender('P', 'N', max_voltage=100, serial_factory=TrialSerial)
        self.w.devices['bender'] = bender
        self.w.bender_control = BenderControl(bender)
        for key in ('laser', 'oscilloscope', 'bender'):
            self.connect(key)
        self.w.navigate('laser')
        page = self.w.laser
        buttons = (page.refresh, page.arm, page.off, page.reset, page.connection)
        releases = {key: Event() for key in ('laser', 'oscilloscope', 'bender')}

        def delayed(key, read):
            def wait_and_read():
                if not releases[key].wait(4):
                    raise TimeoutError(f'{key} read was not released')
                return read()
            return wait_and_read

        scope = self.w.devices['oscilloscope']
        with (patch.object(self.laser, 'read_head_temperature_c', side_effect=delayed('laser', lambda: 25.1)) as read,
              patch.object(scope, 'status', side_effect=delayed('oscilloscope', scope.status)),
              patch.object(bender, 'read_all', side_effect=delayed('bender', bender.read_all))):
            try:
                self.w.tick()
                for key in ('oscilloscope', 'bender'):
                    self.w.refresh(key, periodic=True)
                self.assertTrue(all(btn.isEnabled() for btn in buttons))
                page.refresh.click()
                page.arm.click()
                page.arm.click()
                self.assertFalse(page.arm.isEnabled())
                self.assertFalse(page.fire.isEnabled())
                self.assertTrue(page.abort.isEnabled())
                for key in ('laser', 'oscilloscope'):
                    releases[key].set()
                    self.wait_for(lambda: not any(j.name == 'read' and key in j.resources
                                                  for j in self.w.jobs.active.values()))
                    self.laser.arm_single_shot.assert_not_called()
            finally:
                for release in releases.values():
                    release.set()
            self.wait_for(page.fire.isEnabled)
            self.laser.arm_single_shot.assert_called_once_with()
            read.assert_called_once_with()
            page.abort.click()
            self.idle()
            self.assertNotIn(':WGEN:OUTPut 1', self.inst.commands)

    def test_laser_off_replaces_queued_action_even_if_status_read_fails(self):
        self.connect('laser')
        page = self.w.laser
        page.source.setCurrentIndex(1)
        for action in (page.arm, page.reset):
            release = Event()

            def unreadable():
                release.wait(4)
                raise OSError('Laser test read failed')

            self.laser.turn_off.reset_mock()
            with (patch.object(self.laser, 'read_head_temperature_c', side_effect=unreadable),
                  self.assertLogs('lapis_control', 'ERROR')):
                self.w.refresh('laser', periodic=True)
                try:
                    action.click()
                    self.assertFalse(action.isEnabled())
                    self.assertTrue(page.off.isEnabled())
                    page.off.click()
                    self.assertFalse(page.off.isEnabled())
                    self.laser.turn_off.assert_not_called()
                finally:
                    release.set()
                self.idle()
                self.laser.turn_off.assert_called_once_with()
                self.laser.arm_single_shot.assert_not_called()
                self.laser.reset_interlock_fault.assert_not_called()

    def test_failed_scope_read_drops_pending_arm(self):
        self.connect('laser')
        self.assertFalse(self.w.laser.arm.isEnabled())
        self.connect('oscilloscope')
        release = Event()

        def failed_read():
            release.wait(4)
            raise OSError('Scope test read failed')

        with (patch.object(self.w.devices['oscilloscope'], 'status', side_effect=failed_read),
              self.assertLogs('lapis_control', 'ERROR')):
            self.w.refresh('oscilloscope', periodic=True)
            try:
                self.w.laser.arm.click()
                self.assertTrue(self.w.jobs.running('shot'))
            finally:
                release.set()
            self.idle()
        self.assertIn('Scope test read failed', self.w.laser.phase.text())
        self.laser.arm_single_shot.assert_not_called()
        self.assertNotIn(':WGEN:OUTPut 1', self.inst.commands)

    def test_stale_pressure_and_gauge_switch_clear_the_live_value(self):
        self.connect('vacuum')
        stamp, pa = self.w.last_pressure
        self.w.last_pressure = (stamp-30, pa)
        self.w.show_pressure()
        self.assertEqual(self.w.rail_pressure.text(), '— mbar')
        self.assertIsNone(self.w.pressure_context())
        self.w.vacuum.gauge.setCurrentIndex(1)
        self.assertFalse(self.w.pressures)
        self.assertIsNone(self.w.last_pressure)

    def test_vacuum_status_button_stays_enabled_and_clicks_share_one_diagnostic(self):
        self.connect('vacuum')
        self.w.navigate('vacuum')
        button = self.w.vacuum.refresh
        self.vac.read_pressure_pa.reset_mock()
        release_pressure, release_pumps = Event(), Event()
        pump_started = Event()

        def slow_pressure(gauge):
            if not release_pressure.wait(4):
                raise TimeoutError('Pressure read was not released')
            return 2.3e-6

        def slow_pump():
            pump_started.set()
            if not release_pumps.wait(4):
                raise TimeoutError('Pump read was not released')
            return dict(state='Running')

        self.vac.read_pressure_pa.side_effect = slow_pressure
        self.vac.read_turbo_pump_state.side_effect = slow_pump
        states = []
        self.w.jobs.changed.connect(lambda: states.append(button.isEnabled()))
        try:
            self.w.tick()
            self.assertTrue(self.w.jobs.busy('vacuum'))
            self.assertTrue(button.isEnabled())
            button.click()
            button.click()
            self.vac.read_turbo_pump_state.assert_not_called()
            release_pressure.set()
            self.wait_for(pump_started.is_set)
            self.assertTrue(button.isEnabled())
            button.click()
            self.w.next_read['vacuum'] = 0
            self.w.tick()
        finally:
            release_pressure.set()
            release_pumps.set()
        self.idle()
        self.assertTrue(states and all(states))
        self.vac.read_pressure_pa.assert_called_once_with(1)
        self.vac.read_turbo_pump_state.assert_called_once_with()
        self.assertIn('60000 U/min', self.w.vacuum.pump_labels['turbo'].text())

    def test_disconnect_supersedes_vacuum_status_requested_during_poll(self):
        self.connect('vacuum')
        release = Event()

        def slow_pressure(gauge):
            release.wait(4)
            return 2.3e-6

        self.vac.read_pressure_pa.side_effect = slow_pressure
        self.w.refresh('vacuum', periodic=True)
        try:
            self.w.vacuum.refresh.click()
            self.w.vacuum.connection.click()
            self.assertFalse(self.w.vacuum.refresh.isEnabled())
        finally:
            release.set()
        self.idle()
        self.vac.close.assert_called_once_with()
        self.connect('vacuum')
        self.vac.read_turbo_pump_state.assert_not_called()

    def test_group_disconnect_during_scope_poll_waits_for_the_read_once(self):
        scope = self.w.devices['oscilloscope']
        self.w.devices = {'vacuum': self.vac, 'oscilloscope': scope}
        self.connect('vacuum')
        self.connect('oscilloscope')
        started, release = Event(), Event()
        status = scope.status

        def delayed_status() -> dict:
            started.set()
            if not release.wait(4):
                raise TimeoutError('Test did not release scope poll.')
            return status()

        with patch.object(scope, 'status', side_effect=delayed_status) as read:
            self.w.nav['oscilloscope'].click()
            self.w.tick()
            try:
                self.wait_for(started.is_set)
                self.assertTrue(self.w.scope.connection.isEnabled())
                self.assertEqual(self.w.connect_all.text(), 'Geräte trennen')
                self.assertTrue(self.w.connect_all.isEnabled())
                self.w.connect_all.click()
                self.wait_for(lambda: 'vacuum' not in self.w.connected)
                self.assertFalse(self.w.connect_all.isEnabled())
                self.assertEqual(self.w.connect_all.text(), 'Trennen läuft …')
                self.assertFalse(self.w.scope.connection.isEnabled())
                self.assertFalse(self.inst.closed)
                self.w.next_read['oscilloscope'] = 0
                self.w.tick()
            finally:
                release.set()
            self.idle()
            read.assert_called_once_with()
        self.assertFalse(self.w.connected)
        self.assertTrue(self.inst.closed)
        self.vac.close.assert_called_once_with()
        self.assertEqual(self.w.connect_all.text(), 'Geräte verbinden')
        self.assertTrue(self.w.connect_all.isEnabled())

    def test_disconnect_blocks_during_capture_and_accepts_a_click_during_a_read(self):
        scope = self.w.devices['oscilloscope']
        self.w.devices = {'oscilloscope': scope}
        self.connect('oscilloscope')
        release = Event()
        self.w.run_action('capture', ('oscilloscope',), lambda _: release.wait(4), lambda _: None, 'Testaufnahme')
        try:
            self.assertFalse(self.w.connect_all.isEnabled())
            self.assertFalse(self.w.scope.connection.isEnabled())
            self.w.connect_all.click()
            self.assertFalse(self.w.disconnect_pending)
        finally:
            release.set()
        self.idle()
        release.clear()

        def failed_read() -> dict:
            release.wait(4)
            raise OSError('Scope test read failed')

        with patch.object(scope, 'status', side_effect=failed_read), self.assertLogs('lapis_control', 'ERROR'):
            self.w.refresh('oscilloscope')
            try:
                self.assertTrue(self.w.scope.connection.isEnabled())
                self.w.scope.connection.click()
                self.assertIn('oscilloscope', self.w.disconnect_pending)
                self.assertFalse(self.inst.closed)
            finally:
                release.set()
            self.idle()
        self.assertFalse(self.w.connected)
        self.assertTrue(self.inst.closed)

    def test_log_records_repeated_pressure_errors_pump_diagnostics_and_worker_traceback(self):
        with TemporaryDirectory() as folder, session_log(Path(folder)) as path:
            self.vac.read_pressure_pa.side_effect = EdwardsTicProtocolError('Missing reply for object 913')
            self.connect('vacuum')
            self.vac.read_nxds_status.side_effect = EdwardsTicProtocolError('Invalid test pump frame')
            self.w.refresh('vacuum')
            self.idle()
            self.assertIsNone(self.w.last_pressure)
            self.assertIn('Diagnose unvollständig', self.w.vacuum.pump_labels['backing'].text())
            scope = self.w.devices['oscilloscope']
            with patch.object(scope, 'status', side_effect=OSError('Test VISA timeout')):
                self.connect('oscilloscope')
            contents = path.read_text(encoding='utf-8')
            self.assertEqual(contents.count('Pressure query on TEST-V, gauge 1 failed'), 2)
            self.assertIn('Pump diagnostic backing_drive on TEST-V failed', contents)
            self.assertIn('Invalid test pump frame', contents)
            self.assertIn("read on ('oscilloscope',) failed", contents)
            self.assertIn('Traceback (most recent call last)', contents)
            self.assertIn('OSError: Test VISA timeout', contents)
            self.assertIn('Oszilloskop · Status unbekannt', contents)

    def test_bender_controls_accept_one_snapshot_while_polling(self):
        bender = Bender('P', 'N', max_voltage=100, serial_factory=TrialSerial)
        self.w.devices['bender'] = bender
        self.w.bender_control = BenderControl(bender)
        self.connect('bender')
        page = self.w.bender
        release = Event()
        real_read = bender.read_all

        def slow_read():
            release.wait(3)
            return real_read()

        with patch.object(bender, 'read_all', side_effect=slow_read):
            self.w.refresh('bender', periodic=True)
            try:
                for btn in (page.apply, page.regulate, page.enable, page.disable, page.refresh):
                    self.assertTrue(btn.isEnabled())
                page.editors['p1'].setValue(18)
                page.apply.click()
                page.editors['p1'].setValue(19)
                page.apply.click()
                self.assertFalse(page.apply.isEnabled())
                self.assertEqual(bender.positive._serial.volts[1], 12)
            finally:
                release.set()
            self.idle()
        self.assertEqual(bender.positive._serial.volts[1], 18)
        self.assertEqual(page.editors['p1'].value(), 19)
        self.assertEqual(sum(cmd.startswith(':VOLT 18,') for cmd in bender.positive._serial.sent), 1)

    def test_queued_enable_is_superseded_by_off_and_dropped_on_read_failure(self):
        bender = Bender('P', 'N', max_voltage=100, serial_factory=TrialSerial)
        self.w.devices['bender'] = bender
        self.w.bender_control = control = BenderControl(bender)
        self.connect('bender')
        for request_off in (False, True):
            release = Event()

            def unreadable():
                release.wait(3)
                raise OSError('Test status read failed')

            with (patch.object(control, 'enable') as enable, patch.object(control, 'disable') as disable,
                  patch('lapis_control.gui.window.bender_status', side_effect=lambda _: unreadable()),
                  self.assertLogs('lapis_control', 'ERROR')):
                self.w.refresh('bender')
                try:
                    self.w.bender.enable.click()
                    if request_off:
                        self.assertTrue(self.w.bender.disable.isEnabled())
                        self.w.bender.disable.click()
                finally:
                    release.set()
                self.idle()
                enable.assert_not_called()
                self.assertEqual(disable.call_count, int(request_off))

    def test_manual_hv_takeover_regulation_shot_and_stop(self):
        bender = Bender('P', 'N', max_voltage=100, serial_factory=TrialSerial)
        self.w.devices['bender'] = bender
        self.w.bender_control = BenderControl(bender)
        self.connect('bender')
        page = self.w.bender
        page.editors['p1'].setValue(13)
        page.apply.click()
        self.idle()
        self.assertEqual(bender.positive.read_voltage_set(1), 13)
        page.enable.click()
        self.idle()
        self.assertTrue(bender.positive._serial.on[1])
        page.editors['p1'].setValue(14)
        page.apply.click()
        self.idle()
        self.assertEqual(bender.positive.read_voltage_set(1), 14)
        self.assertTrue(bender.positive._serial.on[1])
        page.options['initial_settle_s'].setValue(0)
        page.options['settle_interval_s'].setValue(.2)
        page.options['hold_interval_s'].setValue(.4)
        bender.positive._serial.measured_current[:2] = [1.25e-6, 0.]
        bender.negative._serial.measured_current[:2] = [-3.5e-6, 8e-9]
        page.regulate.click()
        self.wait_for(lambda: page.readbacks['n1'][3].text() == 'Halten')
        self.assertEqual([page.readbacks[k][2].text() for k in ('p0', 'p1', 'n0', 'n1')],
                         ['1.25', '0', '-3.5', '0.008'])
        bender.negative._serial.measured_current[0] = -2e-6
        self.wait_for(lambda: page.readbacks['n0'][2].text() == '-2')
        self.assertTrue(page.apply.isEnabled())
        self.assertEqual(page.apply.text(), 'Regelziele übernehmen')
        page.editors['n0'].setValue(0)
        with self.assertLogs('lapis_control', 'ERROR'):
            page.apply.click()
        self.assertIn('Ungültige Regelziele', self.w.footer.text())
        self.assertTrue(self.w.jobs.running('regulation'))
        page.editors['n0'].setValue(-11)
        page.editors['p1'].setValue(17)
        self.assertEqual(bender.positive.read_voltage_set(1), 14)
        page.apply.click()
        self.wait_for(lambda: self.w.regulation_targets['p1'] == 17)
        self.assertEqual(bender.negative.read_voltage_set(0), -11)
        self.assertTrue(all(bender.positive._serial.on[:2]) and all(bender.negative._serial.on[:2]))
        # Other device jobs can run while the regulator owns its channels.
        for key in ('laser', 'oscilloscope'):
            self.w.panels[key].connection.click()
            self.wait_for(lambda: key in self.w.connected and not self.w.jobs.busy(key))
        self.w.laser.arm.click()
        self.wait_for(self.w.laser.fire.isEnabled)
        self.assertFalse(page.apply.isEnabled())
        self.w.laser.fire.click()
        self.wait_for(lambda: bool(self.w.records))
        checks = self.w.records[0].metadata['bender_checks']
        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[1]['electrodes']['p1']['target_v'], 17)
        start = time.monotonic()
        page.stop.click()
        self.idle()
        self.assertLess(time.monotonic()-start, 3)
        self.assertFalse(any(bender.positive._serial.on) or any(bender.negative._serial.on))
        for port in (bender.positive._serial, bender.negative._serial):
            self.assertTrue(any(cmd.startswith(':MEAS:CURR?') for cmd in port.sent))
            self.assertFalse(any(cmd.startswith(':CURR ') for cmd in port.sent))

    @unittest.skipUnless(QApplication and hasattr(QTest, 'wheelEvent'), 'Native wheel simulation requires Qt Test 6.8+')
    def test_mouse_wheel_scrolls_page_over_numeric_and_choice_inputs(self):
        self.w.resize(940, 590)
        self.w.navigate('vacuum')
        self.qt.processEvents()
        page = self.w.vacuum
        page.gauge.setCurrentIndex(1)
        scroll = self.w.stack.currentWidget()
        bar = scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        for field in (page.interval, page.gauge):
            for focused in (False, True):
                with self.subTest(field=field.accessibleName(), focused=focused):
                    (field if focused else self.w.nav['vacuum']).setFocus()
                    scroll.ensureWidgetVisible(field)
                    self.qt.processEvents()
                    before = bar.value()
                    delta = -120 if before < bar.maximum() / 2 else 120
                    pos = field.mapTo(self.w, field.rect().center())
                    hit = self.w.childAt(pos)
                    self.assertTrue(hit is field or (hit is not None and field.isAncestorOf(hit)))
                    QTest.wheelEvent(self.w.windowHandle(), pos, QPoint(0, delta))
                    self.qt.processEvents()
                    self.assertEqual(page.interval.value(), 5)
                    self.assertEqual(page.gauge.currentIndex(), 1)
                    self.assertNotEqual(bar.value(), before)
        QTest.keyClick(page.interval, Qt.Key.Key_Up)
        self.assertEqual(page.interval.value(), 6)
        QTest.keyClick(page.gauge, Qt.Key.Key_Down)
        self.assertEqual(page.gauge.currentIndex(), 2)

    def test_profile_roundtrip_validates_before_touching_inputs(self):
        page = self.w.scope
        page.channels[0][1].setText('Ionensignal')
        self.w.bender.options['hold_interval_s'].setValue(42)
        with TemporaryDirectory() as folder:
            path = Path(folder)/'profile.json'
            self.w.profiles.save(path)
            page.channels[0][1].setText('')
            self.w.profiles.load(path)
            self.assertEqual(page.setup().selected[1], 'Ionensignal')
            profile = json.loads(path.read_text(encoding='utf-8'))
            profile['settings']['bender.p0'] = 10
            profile['settings']['scope.ch4.probe'] = 'invalid probe'
            path.write_text(json.dumps(profile), encoding='utf-8')
            with self.assertRaises(ValueError):
                self.w.profiles.load(path)
            self.assertEqual(self.w.bender.targets()['p0'], 15)
            self.assertFalse(self.inst.commands)
            self.laser.arm_single_shot.assert_not_called()
