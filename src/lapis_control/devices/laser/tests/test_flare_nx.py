"""Flare NX protocol and single-shot tests using a simulated serial link."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

src_dir = str(Path(__file__).resolve().parents[4])
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from lapis_control.devices.laser.flare_nx import (
    FlareNx, FlareNxCommandError, FlareNxProtocolError, FlareNxTimeoutError,
    SystemFault, SystemStatus,
)


class SerialTestCase(unittest.TestCase):
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


class ConnectionTests(SerialTestCase):
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

    def test_usb_status_session_with_open_interlock_on_v3024(self):
        self.port.read_until.side_effect = [
            b'OK\r\n', b'ON\r\n', b'000C0000\r\n', b'00000201\r\n', b'OFF\r\n',
            b'2.1322 * 10^4\r\n', b'OFF\r\n', b'OK\r\n', b'OFF\r\n',
        ]
        with FlareNx('COM_TEST', handshake_interface='USB') as laser:
            self.assertEqual(laser.read_system_faults(), SystemFault.INTERLOCK_OPEN | SystemFault.INTERLOCK_FAULT)
            self.assertEqual(int(laser.read_system_status()), 0x201)
            self.assertFalse(laser.read_laser_enabled())
            self.assertEqual(laser.read_pulse_count(), 21322)
        self.assertEqual(self.port.write.call_args_list, [
            call(b'SYST:COM:HAND:USB on\r\n'), call(b'SYST:COM:HAND:USB\r\n'),
            call(b'SYST:FAUL\r\n'), call(b'SYST:STAT\r\n'), call(b'LD:SET:ENA\r\n'),
            call(b'HEAD:PULS:COUNT\r\n'), call(b'LD:SET:REM\r\n'),
            call(b'LD:SET:ENA off\r\n'), call(b'LD:SET:ENA\r\n'),
        ])
        self.port.close.assert_called_once()

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


class SingleShotTests(SerialTestCase):
    def setUp(self):
        super().setUp()
        self.laser = FlareNx('COM_TEST').connect()
        self.port.write.reset_mock()

    def replies(self, *lines: bytes) -> None:
        self.port.read_until.side_effect = lines

    def commands(self) -> list[str]:
        return [args[0].decode('ascii').removesuffix('\r\n')
                for args, _ in self.port.write.call_args_list]

    def test_reset_closed_latched_interlock_once_and_report_remaining_faults(self):
        self.replies(b'00040001\r\n', b'OFF\r\n', b'0\r\n', b'OK\r\n', b'00000001\r\n')
        self.assertEqual(int(self.laser.reset_interlock_fault()), 1)
        self.assertEqual(self.commands(), ['SYST:FAUL', 'LD:SET:ENA', 'LD:SET:REM', 'SYST:INT RESET', 'SYST:FAUL'])

    def test_interlock_reset_skipped_if_clear_and_blocked_if_open_or_enabled(self):
        self.replies(b'00000000\r\n')
        self.assertEqual(self.laser.reset_interlock_fault(), 0)
        self.assertEqual(self.commands(), ['SYST:FAUL'])
        for replies in ((b'000C0000\r\n',), (b'00040000\r\n', b'ON\r\n'),
                        (b'00040000\r\n', b'OFF\r\n', b'ON\r\n')):
            with self.subTest(replies=replies), self.assertRaises(FlareNxCommandError):
                self.replies(*replies)
                self.laser.reset_interlock_fault()
        self.assertNotIn('SYST:INT RESET', self.commands())

    def test_interlock_reset_not_retried_on_ambiguous_or_negative_confirmation(self):
        for replies, error in (([b''], FlareNxTimeoutError),
                               ([b'OK\r\n', b'000C0000\r\n'], FlareNxCommandError)):
            with self.subTest(replies=replies):
                self.port.write.reset_mock()
                self.replies(b'00040000\r\n', b'OFF\r\n', b'OFF\r\n', *replies)
                with self.assertRaises(error):
                    self.laser.reset_interlock_fault()
                self.assertEqual(self.commands().count('SYST:INT RESET'), 1)

    def test_select_single_shot_from_continuous_checks_off_trigger_and_mode(self):
        self.replies(b'OFF\r\n', b'OK\r\n', b'0\r\n', b'12\r\n',
                     b'0000 0304\r\n', b'OK\r\n', b'3.0\r\n')
        self.laser.configure_single_shot()
        self.assertEqual(self.commands(), ['LD:SET:REM', 'LD:SET:ENA off', 'LD:SET:ENA',
                                           'LD:PULS:MOD', 'SYST:STAT', 'LD:PULS:MOD 3', 'LD:PULS:MOD'])

    def test_existing_mode_is_not_rewritten(self):
        self.replies(b'0\r\n', b'OK\r\n', b'OFF\r\n', b'3\r\n')
        self.laser.configure_single_shot()
        self.assertEqual(self.commands(), ['LD:SET:REM', 'LD:SET:ENA off', 'LD:SET:ENA', 'LD:PULS:MOD'])

    def test_service_modes_are_readable_and_not_reconfigured(self):
        for mode in (0, 1):
            with self.subTest(mode=mode):
                reply = f'{mode}\r\n'.encode()
                self.replies(reply, b'OFF\r\n', b'OK\r\n', b'OFF\r\n', reply)
                self.assertEqual(self.laser.read_pulse_mode(), mode)
                with self.assertRaisesRegex(FlareNxCommandError, 'service mode'):
                    self.laser.configure_single_shot()
                self.assertNotIn('LD:PULS:MOD 3', self.commands())

    def test_configuration_stops_on_unconfirmed_off_or_active_trigger(self):
        for replies, last in (
            ([b'ON\r\n', b'OK\r\n', b'1\r\n'], 'LD:SET:REM'),
            ([b'OFF\r\n', b'OK\r\n', b'ON\r\n'], 'LD:SET:ENA'),
            ([b'OFF\r\n', b'OK\r\n', b'OFF\r\n', b'2\r\n', b'00000344\r\n'], 'SYST:STAT'),
        ):
            with self.subTest(last=last):
                self.port.write.reset_mock()
                self.replies(*replies)
                with self.assertRaises(FlareNxCommandError):
                    self.laser.configure_single_shot()
                self.assertEqual(self.commands()[-1], last)
                self.assertNotIn('LD:PULS:MOD 3', self.commands())
                self.assertNotIn('LD:SET:ENA on', self.commands())

    def test_mode_rejection_and_readback_mismatch_are_not_retried(self):
        for response, code in (([b'ERR-200\r\n'], -200), ([b'OK\r\n', b'12\r\n'], None)):
            with self.subTest(response=response):
                self.port.write.reset_mock()
                self.replies(b'OFF\r\n', b'OK\r\n', b'OFF\r\n', b'12\r\n', b'00000304\r\n', *response)
                with self.assertRaises(FlareNxCommandError) as error:
                    self.laser.configure_single_shot()
                self.assertEqual(error.exception.code, code)
                if code is not None:
                    self.assertEqual(error.exception.command, 'LD:PULS:MOD 3')
                self.assertEqual(self.commands().count('LD:PULS:MOD 3'), 1)
                self.assertNotIn('LD:SET:ENA on', self.commands())

    def test_interlock_registers_preserve_all_bits(self):
        self.replies(b'000C 0001\r\n', b'0x00000304\r\n')
        faults = self.laser.read_system_faults()
        self.assertTrue(faults & SystemFault.INTERLOCK_OPEN)
        self.assertTrue(faults & SystemFault.INTERLOCK_FAULT)
        self.assertEqual(int(faults), 0xC0001)
        self.assertTrue(self.laser.read_system_status() & SystemStatus.STANDBY)
        self.assertEqual(self.commands(), ['SYST:FAUL', 'SYST:STAT'])

    def test_counter_with_and_without_units_is_read_only(self):
        self.replies(b'0\r\n', b'250 Shots\r\n', b'4294967295 [Shots]\r\n')
        self.assertEqual([self.laser.read_pulse_count() for _ in range(3)], [0, 250, 0xFFFFFFFF])
        self.assertEqual(self.commands(), ['HEAD:PULS:COUNT'] * 3)
        self.replies(b'4.294967295 * 10^9 [Shots]\r\n')
        self.assertEqual(self.laser.read_pulse_count(), 0xFFFFFFFF)
        for reply in (b'-1\r\n', b'4294967296\r\n', b'1.5\r\n', b'3 kHz\r\n',
                      b'2.13221 * 10^4\r\n', b'4.294967296 * 10^9\r\n', b'2.1322 * 10^\r\n'):
            with self.subTest(reply=reply), self.assertRaises(FlareNxProtocolError):
                self.replies(reply)
                self.laser.read_pulse_count()

    def test_arm_reads_current_conditions_and_confirms_enable(self):
        self.replies(b'3\r\n', b'OFF\r\n', b'0\r\n', b'OFF\r\n', b'00000000\r\n',
                     b'0000 0304\r\n', b'OK\r\n', b'1\r\n')
        self.laser.arm_single_shot()
        self.assertEqual(self.commands(), ['LD:PULS:MOD', 'LD:SET:ENA', 'LD:SET:REM', 'LD:SET:TRI',
                                           'SYST:FAUL', 'SYST:STAT', 'LD:SET:ENA on', 'LD:SET:ENA'])

    def test_arm_blocks_on_invalid_or_unsafe_readback_before_any_write(self):
        ready = [b'3\r\n', b'OFF\r\n', b'0\r\n', b'0\r\n', b'00000000\r\n', b'00000304\r\n']
        for index, bad in (
            (0, b'12\r\n'), (1, b'ON\r\n'), (2, b'1\r\n'), (3, b'1\r\n'),
            (4, b'00080000\r\n'), (4, b'00040000\r\n'), (4, b'00000001\r\n'),
            (5, b'00000302\r\n'), (5, b'00000344\r\n'), (5, b'00000305\r\n'),
            (5, b'00000310\r\n'),
        ):
            with self.subTest(index=index, bad=bad):
                self.port.write.reset_mock()
                self.replies(*ready[:index], bad)
                with self.assertRaises(FlareNxCommandError):
                    self.laser.arm_single_shot()
                self.assertTrue(all(' ' not in command for command in self.commands()))
        for bad, error in ((b'', FlareNxTimeoutError), (b'0000\r\n', FlareNxProtocolError),
                           (b'GGGG 0000\r\n', FlareNxProtocolError), (b'00000000', FlareNxProtocolError)):
            with self.subTest(bad=bad):
                self.port.write.reset_mock()
                self.replies(*ready[:4], bad)
                with self.assertRaises(error):
                    self.laser.arm_single_shot()
                self.assertTrue(all(' ' not in command for command in self.commands()))

    def test_ambiguous_enable_shuts_down_without_rearming(self):
        self.replies(b'3\r\n', b'OFF\r\n', b'0\r\n', b'0\r\n', b'00000000\r\n',
                     b'00000304\r\n', b'', b'OFF\r\n', b'OK\r\n', b'OFF\r\n')
        with self.assertRaises(FlareNxTimeoutError):
            self.laser.arm_single_shot()
        self.assertEqual(self.commands().count('LD:SET:ENA on'), 1)
        self.assertEqual(self.commands()[-3:], ['LD:SET:REM', 'LD:SET:ENA off', 'LD:SET:ENA'])
        self.port.close.assert_called_once()

    def test_arm_failure_and_unconfirmed_shutdown_report_both_errors(self):
        self.replies(b'3\r\n', b'OFF\r\n', b'0\r\n', b'0\r\n', b'00000000\r\n',
                     b'00000304\r\n', b'', b'OFF\r\n', b'OK\r\n', b'1\r\n')
        with self.assertLogs('lapis_control.devices.laser.flare_nx', level='ERROR') as logs:
            with self.assertRaises(FlareNxTimeoutError), self.laser:
                self.laser.arm_single_shot()
        self.assertIn('did not switch to OFF', logs.output[0])
        self.assertEqual(self.commands().count('LD:SET:ENA on'), 1)
        self.assertEqual(self.commands().count('LD:SET:ENA off'), 1)
        self.port.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
