"""Exercise the real frame encoder and parsers against a simulated serial port."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from lapis_control.devices.vacuum import edwards_tic as tic


class EdwardsTICTests(unittest.TestCase):
    def setUp(self):
        self.port = Mock(is_open=True)
        self.port.write.side_effect = len
        factory = patch.object(tic.serial, 'Serial', return_value=self.port)
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        self.device = tic.EdwardsTIC('COM_TEST', timeout=0.5, write_timeout=0.75)

    def test_lifecycle_sends_no_frames(self):
        self.device.close()
        self.factory.assert_not_called()
        with self.device as device:
            self.assertIs(device.connect(), device)
            self.factory.assert_called_once_with(
                port='COM_TEST', baudrate=9600, bytesize=8, parity='N', stopbits=1,
                timeout=0.5, write_timeout=0.75, xonxoff=False, rtscts=False, dsrdtr=False)
        self.device.close()
        self.port.close.assert_called_once()
        self.port.write.assert_not_called()
        with self.assertRaises(tic.EdwardsTicConnectionError):
            self.device.query_value(904)

    def test_unapproved_ids_and_command_text_never_reach_port(self):
        class FormattedInt(int):
            def __format__(self, spec):
                return '904\r!C910 1'

        with self.device:
            for value in (True, 904.0, 933, '904', b'904', '904\r!C910 1', FormattedInt(904)):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    self.device.query_value(value)
        self.port.write.assert_not_called()

    def test_all_monitoring_queries_emit_only_expected_frames(self):
        cases = [
            (802, '42;0000;0000;0040;2000', self.device.read_nxds_status, 'frequency_hz', 42),
            (808, '-200;37', self.device.read_nxds_temperatures, 'pump_temperature_c', None),
            (904, '4;0;0', self.device.read_turbo_pump_state, 'state', 'Running'),
            (905, '99.2;0;0', self.device.read_turbo_speed_percent, 'speed_percent', 99.2),
            (906, '80;0;0', self.device.read_turbo_power, 'power_w', 80),
            (907, '4;0;0', self.device.read_turbo_normal, 'is_normal', True),
            (908, '0;0;0', self.device.read_turbo_standby, 'is_standby', False),
            (909, '36;4;0;0', self.device.read_turbo_cycle_time, 'cycle_time_hours', 36),
            (910, '4;0;0', self.device.read_backing_pump_state, 'state_code', 4),
            (911, '85;0;0', self.device.read_backing_speed, 'speed_percent', 85),
            (912, '222;0;0', self.device.read_backing_power, 'power_w', 222),
            (919, '294;0;0', self.device.read_ps_temperature, 'temperature_c', 20),
            (920, '298;0;0', self.device.read_internal_temperature, 'temperature_c', 24),
            (924, '4;0;0', self.device.read_air_cooler_state, 'is_on', True),
            (852, '1000;00000184', self.device.read_next_speed_and_status, 'speed_rpm', 60000),
            (859, '32;28', self.device.read_next_temperatures, 'motor_temperature_c', 32),
        ]
        with self.device:
            for obj, payload, read, key, expected in cases:
                with self.subTest(obj=obj):
                    self.port.read_until.return_value = f'=V{obj} {payload}\r'.encode()
                    self.assertEqual(read()[key], expected)
                    self.port.write.assert_called_with(f'?V{obj}\r'.encode())
            for channel, obj in enumerate((913, 914, 915), 1):
                self.port.read_until.return_value = f'=V{obj} 1.5e-2;59;11;0;0\r'.encode()
                self.assertEqual(self.device.read_pressure_pa(channel), 0.015)
                self.port.write.assert_called_with(f'?V{obj}\r'.encode())
        self.assertEqual(self.port.write.call_count, 19)

    def test_nxds_flags_unused_sensor_and_invalid_data(self):
        with self.device:
            self.port.read_until.return_value = b'=V802 42;1234;0000;0040;2001\r'
            status = self.device.read_nxds_status()
            self.assertEqual(set(status['warnings']), {6})
            self.assertEqual(status['faults'], {0: 'Unassigned bit', 13: 'Serial control interlock'})
            self.port.read_until.return_value = b'=V808 27;-200\r'
            self.assertEqual(self.device.read_nxds_temperatures(),
                             {'pump_temperature_c': 27, 'controller_temperature_c': None})
            for raw, read in [(b'=V808 151;0\r', self.device.read_nxds_temperatures),
                              (b'=V808 25\r', self.device.read_nxds_temperatures),
                              (b'=V802 50;0000;0000;0000\r', self.device.read_nxds_status)]:
                self.port.read_until.return_value = raw
                with self.subTest(raw=raw), self.assertRaises(tic.EdwardsTicProtocolError):
                    read()

    def test_invalid_or_incomplete_responses(self):
        with self.device:
            for raw in (b'=V905 4;0;0\r', b'*V905 1\r', b'*V904 bad\r',
                        b'=V904 \xff\r', b'!V904 0\r', b'=V904 4;0\r'):
                self.port.read_until.return_value = raw
                with self.subTest(raw=raw), self.assertRaises(tic.EdwardsTicProtocolError):
                    self.device.read_turbo_pump_state()
            for raw in (b'', b'=V904 4;0;0'):
                self.port.read_until.return_value = raw
                with self.assertRaises(tic.EdwardsTicTimeoutError):
                    self.device.read_turbo_pump_state()
            self.port.read_until.return_value = b'*V904 5\r'
            with self.assertRaises(tic.EdwardsTicCommandError) as caught:
                self.device.read_turbo_pump_state()
            self.assertEqual((caught.exception.object_id, caught.exception.error_code), (904, 5))
            self.port.write.side_effect = lambda frame: len(frame) - 1
            with self.assertRaises(tic.EdwardsTicTimeoutError):
                self.device.read_turbo_pump_state()

    def test_timeout_releases_port_without_extra_frames(self):
        self.port.read_until.side_effect = tic.serial.SerialTimeoutException('timeout')
        with self.assertRaises(tic.EdwardsTicTimeoutError):
            with self.device:
                self.device.read_backing_pump_state()
        self.port.write.assert_called_once_with(b'?V910\r')
        self.port.close.assert_called_once()

    def test_legacy_cycle_time_and_malformed_hours(self):
        with self.device:
            self.port.read_until.return_value = b'=V909 12.0;0;0\r'
            data = self.device.read_turbo_cycle_time()
            self.assertEqual(data['cycle_time_hours'], 12)
            self.assertIsNone(data['state_code'])
            for value in ('-1', '1.5', 'nan', '65536'):
                self.port.read_until.return_value = f'=V909 {value};4;0;0\r'.encode()
                with self.subTest(value=value), self.assertRaises(tic.EdwardsTicProtocolError):
                    self.device.read_turbo_cycle_time()

    def test_faults_and_unusable_readings(self):
        with self.device:
            self.port.read_until.side_effect = [b'=V904 6;32;2\r', b'=V852 0;00002001\r']
            with self.assertRaises(tic.EdwardsTicPumpFaultError) as caught:
                self.device.check_turbo_faults()
            self.assertEqual(set(caught.exception.active_faults), {0, 13})
            self.port.read_until.side_effect = None
            for raw in (b'=V913 0;59;5;0;0\r', b'=V913 5;66;11;0;0\r',
                        b'=V913 9.9e+09;59;0;0;0\r', b'=V913 1.5e-2;59;11;0;1\r',
                        b'=V913 nan;59;11;0;0\r', b'=V913 0;59;4;6;1\r'):
                self.port.read_until.return_value = raw
                with self.subTest(raw=raw), self.assertRaises(tic.EdwardsTicError):
                    self.device.read_pressure_pa(1)
            self.port.read_until.return_value = b'=V852 1000;100000000\r'
            with self.assertRaises(tic.EdwardsTicProtocolError):
                self.device.read_next_speed_and_status()
