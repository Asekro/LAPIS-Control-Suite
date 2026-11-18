"""Connection and shutdown regression tests without laser hardware."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

src_dir = str(Path(__file__).resolve().parents[4])
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from lapis_control.devices.laser.flare_nx import FlareNx, FlareNxCommandError


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.port = Mock(is_open=True)
        self.port.read_until.side_effect = [b'OK\r\n', b'ON\r\n',
                                           b'OFF\r\n', b'OK\r\n', b'OFF\r\n']
        serial_patch = patch('lapis_control.devices.laser.flare_nx.serial.Serial',
                             return_value=self.port)
        self.create_serial = serial_patch.start()
        self.addCleanup(serial_patch.stop)
        delay_patch = patch.object(FlareNx, '_wait_command_delay')
        delay_patch.start()
        self.addCleanup(delay_patch.stop)

    def test_constructor_and_close_have_no_transport_side_effects(self):
        laser = FlareNx('COM_TEST')
        laser.close()
        self.create_serial.assert_not_called()

    def test_timeouts_applied_when_port_opens(self):
        with FlareNx('COM_TEST', timeout=1.75, write_timeout=2.5):
            settings = self.create_serial.call_args.kwargs
            self.assertEqual(settings['timeout'], 1.75)
            self.assertEqual(settings['write_timeout'], 2.5)
            self.assertEqual(settings['baudrate'], 9600)
            self.assertFalse(settings['rtscts'])

    def test_invalid_timeouts_fail_before_opening_port(self):
        for value in (0, -1, float('nan'), float('inf'), True, '1'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    FlareNx('COM_TEST', timeout=value)
                with self.assertRaises(ValueError):
                    FlareNx('COM_TEST', write_timeout=value)
        self.create_serial.assert_not_called()

    def test_connect_and_context_reuse_port_then_verify_off(self):
        laser = FlareNx('COM_TEST')
        self.assertIs(laser.connect(), laser)
        self.assertEqual(self.port.write.call_args_list,
                         [call(b'SYST:COM:HAND on\r\n'), call(b'SYST:COM:HAND\r\n')])
        with laser as connected:
            self.assertIs(connected, laser)
            self.assertTrue(laser.is_connected)
        self.create_serial.assert_called_once()
        self.assertIn(call(b'LD:SET:ENA off\r\n'), self.port.write.call_args_list)
        self.assertNotIn(call(b'LD:SET:ENA on\r\n'), self.port.write.call_args_list)
        self.port.close.assert_called_once()
        self.assertFalse(laser.is_connected)
        laser.close()
        self.port.close.assert_called_once()

    def test_disconnect_alias_keeps_shutdown_behavior(self):
        laser = FlareNx('COM_TEST').connect()
        laser.disconnect()
        self.port.close.assert_called_once()
        self.assertEqual(self.port.write.call_args_list[-1], call(b'LD:SET:ENA\r\n'))

    def test_unconfirmed_off_still_closes_and_raises(self):
        self.port.read_until.side_effect = [b'OK\r\n', b'ON\r\n',
                                           b'OFF\r\n', b'OK\r\n', b'ON\r\n']
        laser = FlareNx('COM_TEST').connect()
        with self.assertRaisesRegex(FlareNxCommandError, 'did not switch to OFF'):
            laser.close()
        self.port.close.assert_called_once()
        self.assertFalse(laser.is_connected)

    def test_failed_handshake_attempts_off_and_closes(self):
        self.port.read_until.side_effect = [b'OK\r\n', b'OFF\r\n',
                                           b'OFF\r\n', b'OK\r\n', b'OFF\r\n']
        laser = FlareNx('COM_TEST')
        with self.assertRaisesRegex(FlareNxCommandError, 'Handshake'):
            laser.connect()
        self.port.close.assert_called_once()
        self.assertFalse(laser.is_connected)
        self.assertNotIn(call(b'LD:SET:ENA on\r\n'), self.port.write.call_args_list)


if __name__ == '__main__':
    unittest.main()
