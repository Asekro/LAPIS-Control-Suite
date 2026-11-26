"""Offline tests. The fake models protocol behavior, not physical hardware."""
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import sys

src_dir = str(Path(__file__).resolve().parents[4])
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from lapis_control.devices.bender.iseg_nhs import (NHSModule, Bender, CommunicationError, DeviceError,
                      PartialUpdateError, ProtocolError, VerificationError, parse_number, ChannelStatus)

class FakeSerial:
    def __init__(self, port, **kwargs):
        self.timeout = kwargs['timeout']
        self.positive = port == 'P'
        self.sent = []
        self.buffer = b''
        self.closed = False
        self.echo = True
        self.drop = False
        self.volts = [0.] * 6
        self.measurement_offset = 0.0
        self.extra_status = ChannelStatus(0)
        self.on = [False] * 6
        self.mod_error = False
        self.reject_voltage = False
        self.fail_write = False
    def close(self): self.closed = True
    def reset_input_buffer(self): self.buffer = b''
    def write(self, data):
        if self.fail_write: raise OSError('USB disconnected')
        cmd = data.decode().strip()
        self.sent.append(cmd)
        if self.drop: return len(data)
        import re
        match = re.search(r'\(@(\d)\)', cmd)
        ch = int(match[1]) if match else 0
        if cmd.endswith(';*OPC?'):
            if cmd.startswith(':VOLT ') and not self.reject_voltage:
                value = cmd.split(' ', 1)[1].split(',')[0]
                if value == 'ON': self.on[ch] = True
                elif value in ('OFF', 'EMCY OFF'): self.on[ch] = False
                elif value != 'EMCY CLR': self.volts[ch] = float(value)
            if self.reject_voltage: self.mod_error = True
            response = '1'
        elif cmd == '*IDN?': response = 'iseg Spezialelektronik GmbH,NHS,12345,1.23'
        elif cmd == ':READ:FIRMWARE:NAME?': response = 'N06C2'
        elif cmd == ':READ:MODULE:CHANNELNUMBER?': response = '6'
        elif cmd.startswith(':READ:CHAN:STAT?'):
            status = self.extra_status | (ChannelStatus.POSITIVE if self.positive else 0)
            if self.on[ch]:
                status |= ChannelStatus.ON | ChannelStatus.CONSTANT_VOLTAGE
            response = str(int(status))
        elif cmd == ':READ:MODULE:STAT?': response = str(64 if self.mod_error else 30464)
        elif 'EVENT:STAT?' in cmd: response = '0'
        elif cmd.startswith(':READ:VOLT:NOM?'): response = '4.00000E3V'
        elif cmd.startswith(':READ:VOLT?'): response = f'{self.volts[ch]}V'
        elif cmd.startswith(':MEAS:VOLT?'): response = f'{self.volts[ch] + self.measurement_offset}V'
        elif cmd.startswith(':READ:CURR?'): response = '1.0E-3A'
        elif cmd.startswith(':MEAS:CURR?'): response = '1.2E-9A'
        elif cmd == ':READ:RAMP:VOLT?': response = '20.0%/s'
        elif cmd == ':READ:MODULE:TEMPERATURE?': response = '30.0C'
        else: raise AssertionError(f'Unmodeled command {cmd}')
        self.buffer += (data if self.echo else b'') + (response + '\r\n').encode()
        return len(data)
    def read_until(self, terminator, size):
        if not self.buffer: return b''
        pos = self.buffer.find(terminator)
        n = min(pos + len(terminator) if pos >= 0 else len(self.buffer), size)
        data, self.buffer = self.buffer[:n], self.buffer[n:]
        return data

class Firmware220Serial(FakeSerial):
    """Captured 2.20 behavior: signed VNOM, unset polarity status bit."""
    nominal_override = None

    def write(self, data):
        result = super().write(data)
        command = data.decode().strip()
        if command == '*IDN?':
            self.buffer = self.buffer.replace(b',1.23\r\n', b',2.20\r\n')
        elif command.startswith(':READ:CHAN:STAT?'):
            self.buffer = (data if self.echo else b'') + b'0\r\n'
        elif command.startswith(':READ:VOLT:NOM?'):
            nominal = self.nominal_override
            if nominal is None:
                nominal = 4000 if self.positive else -4000
            self.buffer = (data if self.echo else b'') + f'{nominal}V\r\n'.encode()
        elif command == ':READ:RAMP:VOLT?':
            self.buffer = (data if self.echo else b'') + b'1.000%\r\n'
        return result


class ByteEchoSerial(FakeSerial):
    """Require the previous byte echo to be consumed before accepting another."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pending = b''
        self.bad_echo = False

    def write(self, data):
        if len(data) != 1 or self.buffer:
            raise AssertionError('Expected one byte after consuming its predecessor echo')
        self.pending += data
        if self.echo:
            self.buffer += b'!' if self.bad_echo else data
        if self.pending.endswith(b'\r\n'):
            command, self.pending = self.pending, b''
            echo, self.echo = self.echo, False
            try:
                super().write(command)
            finally:
                self.echo = echo
        return 1

    def read(self, size):
        answer, self.buffer = self.buffer[:size], self.buffer[size:]
        return answer


class DriverTests(unittest.TestCase):
    def module(self, polarity='+'):
        return NHSModule('P' if polarity == '+' else 'N', polarity=polarity,
                         max_voltage=100, serial_factory=FakeSerial)
    def bender(self):
        return Bender('P','N',max_voltage=100,serial_factory=FakeSerial)
    def test_constructor_and_close_do_not_open_ports(self):
        factory = Mock(side_effect=AssertionError('Unexpected hardware access'))
        bender = Bender('P', 'N', max_voltage=100, serial_factory=factory)
        bender.close()
        factory.assert_not_called()
    def test_connect_aliases_reuse_connection_and_reconnect_after_close(self):
        factory = Mock(side_effect=FakeSerial)
        bender = Bender('P', 'N', max_voltage=100, serial_factory=factory)
        self.assertIs(bender.connect(), bender)
        connections = (bender.positive._serial, bender.negative._serial)
        commands = [list(connection.sent) for connection in connections]
        self.assertIs(bender.open(), bender)
        self.assertIs(bender.positive.open(), bender.positive)
        self.assertEqual(factory.call_count, 2)
        self.assertEqual([connection.sent for connection in connections], commands)
        bender.close()
        bender.close()
        self.assertTrue(all(connection.closed for connection in connections))
        with bender:
            self.assertEqual(factory.call_count, 4)
    def test_connect_does_not_reopen_desynchronized_module(self):
        with self.module() as module:
            connection = module._serial
            connection.fail_write = True
            with self.assertRaises(CommunicationError):
                module.query('*IDN?')
            with self.assertRaises(CommunicationError):
                module.connect()
            self.assertIs(module._serial, connection)
    def test_open_close_read_only(self):
        with self.module() as m:
            connection = m._serial
            m.read_channel(0)
            m.read_module()
        self.assertTrue(connection.closed)
        self.assertTrue(all('?' in c and ';' not in c for c in connection.sent))
    def test_echo_on_and_off(self):
        with self.module() as m:
            self.assertIn('iseg',m.query('*IDN?'))
            m._serial.echo = False
            self.assertIn('iseg',m.query('*IDN?'))
            m.set_voltage(0,12)
            self.assertEqual(m.read_voltage_set(0),12)
    def test_signed_negative_mapping(self):
        with self.module('-') as m:
            m.set_voltage(1,-17.5)
            self.assertIn(':VOLT 17.5,(@1);*OPC?',m._serial.sent)
            self.assertEqual(m.read_voltage_set(1),-17.5)
    def test_invalid_input_never_sets_output(self):
        with self.module() as m:
            for ch,v in [(0,-1),(0,101),(0,float('nan')),(0,float('inf')),(6,5),(True,5)]:
                with self.assertRaises(ValueError): m.set_voltage(ch,v)
            self.assertFalse(any(';*OPC?' in c for c in m._serial.sent))
    def test_wrong_polarity_closes_connection(self):
        made=[]
        def factory(**kw):
            s=FakeSerial(**kw); made.append(s); return s
        m=NHSModule('N',polarity='+',max_voltage=100,serial_factory=factory)
        with self.assertRaises(DeviceError): m.open()
        self.assertTrue(made[0].closed)
    def test_serial_mismatch(self):
        m=NHSModule('P',polarity='+',max_voltage=100,expected_serial='other',serial_factory=FakeSerial)
        with self.assertRaises(DeviceError): m.open()
        self.assertIsNone(m._serial)
    def test_timeout_blocks_later_commands(self):
        with self.module() as m:
            m._serial.drop=True
            with self.assertRaises(CommunicationError): m.query('*IDN?')
            count=len(m._serial.sent)
            with self.assertRaises(CommunicationError): m.disable(0)
            self.assertEqual(count,len(m._serial.sent))
    def test_malformed_line_marks_desynchronized(self):
        with self.module() as m:
            m._serial.drop=True
            m._serial.buffer=b'12V\n'
            with self.assertRaises(CommunicationError): m.read_voltage(0)
            self.assertTrue(m._broken)
    def test_device_rejection(self):
        with self.module() as m:
            m._serial.reject_voltage=True
            with self.assertRaises(DeviceError): m.set_voltage(0,10)
    def test_preexisting_error_blocks_set_but_not_off(self):
        with self.module() as m:
            m._serial.mod_error=True
            with self.assertRaises(DeviceError): m.set_voltage(0,10)
            m.disable(0)
            self.assertIn(':VOLT OFF,(@0);*OPC?',m._serial.sent)
    def test_bender_prevalidates_every_voltage(self):
        with self.bender() as b:
            with self.assertRaises(ValueError): b.set_voltages(p0=10,p1=10,n0=-10,n1=10)
            self.assertFalse(any(';*OPC?' in c for c in b.positive._serial.sent))
    def test_partial_failure_reports_confirmed_channels(self):
        with self.bender() as b:
            b.negative._serial.reject_voltage=True
            with self.assertRaises(PartialUpdateError) as caught:
                b.set_voltages(p0=10,p1=11,n0=-12,n1=-13)
            self.assertEqual(caught.exception.completed,('p0','p1'))
            self.assertEqual(caught.exception.failed,'n0')
            self.assertEqual(b.positive.read_voltage_set(1),11)
    def test_off_attempts_other_module_after_disconnect(self):
        with self.bender() as b:
            b.positive._serial.fail_write=True
            with self.assertRaises(PartialUpdateError) as caught: b.disable()
            self.assertEqual(caught.exception.completed,('n0','n1'))
    def test_bender_mapping_enable_and_read(self):
        with self.bender() as b:
            b.set_voltages(p0=10,p1=20,n0=-30,n1=-40)
            b.enable()
            values=b.read_all()
            self.assertEqual([r.voltage_set for r in values.values()],[10,20,-30,-40])
            self.assertTrue(all(r.status & ChannelStatus.ON for r in values.values()))
    def test_parsing_units_and_bad_values(self):
        self.assertEqual(parse_number('1.23E-6A','A'),1.23e-6)
        self.assertEqual(parse_number('20.0%/s','%/s'),20)
        for text in ['nan','1e999V','12A','1,2V','error']:
            with self.assertRaises(ProtocolError): parse_number(text,'V')
    def test_setpoint_mismatch_is_not_silently_accepted(self):
        from lapis_control.devices.bender.iseg_nhs import VerificationError
        with self.module() as m:
            original=m._serial.write
            def clipped(data):
                result=original(data)
                if data.startswith(b':VOLT 20,'): m._serial.volts[0]=19
                return result
            m._serial.write=clipped
            with self.assertRaises(VerificationError): m.set_voltage(0,20)
    def test_second_open_failure_closes_first(self):
        made=[]
        def factory(**kw):
            if kw['port']=='N': raise OSError('port unavailable')
            s=FakeSerial(**kw); made.append(s); return s
        b=Bender('P','N',max_voltage=100,serial_factory=factory)
        with self.assertRaises(OSError): b.open()
        self.assertTrue(made[0].closed)
    def test_partial_write_marks_connection_broken(self):
        with self.module() as m:
            m._serial.write=lambda data: 2
            with self.assertRaises(CommunicationError): m.query('*IDN?')
            self.assertTrue(m._broken)

    def test_interrupt_during_exchange_blocks_further_commands(self):
        with self.module() as m:
            with patch.object(m._serial, 'read_until', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    m.read_voltage(0)
            sent = list(m._serial.sent)
            with self.assertRaises(CommunicationError):
                m.disable(0)
            self.assertEqual(m._serial.sent, sent)
    def test_command_length_and_injection(self):
        with self.module() as m:
            for cmd in ['*IDN?\r\n:VOLT ON','X'*80+'?','*IDN?;*IDN?']:
                with self.assertRaises(ValueError): m.query(cmd)

    def test_firmware_220_uses_signed_nominal_for_both_polarities(self):
        with Bender('P', 'N', max_voltage=100, serial_factory=Firmware220Serial) as b:
            self.assertEqual(b.positive.firmware_version, '2.20')
            self.assertFalse(b.positive.read_status(0) & ChannelStatus.POSITIVE)
            b.set_voltages(p0=10, p1=11, n0=-12, n1=-13)
            self.assertEqual(b.negative.read_voltage_set(0), -12)

    def test_ramp_percent_unit_from_real_firmware_220(self):
        with NHSModule('P', polarity='+', max_voltage=100,
                       serial_factory=Firmware220Serial) as module:
            self.assertEqual(module.read_voltage_ramp(), 1.0)
            self.assertEqual(module.read_module().voltage_ramp_percent_s, 1.0)
        with self.module() as module:
            self.assertEqual(module.read_voltage_ramp(), 20.0)
            module._serial.drop = True
            module._serial.buffer = b'1.000V\r\n'
            with self.assertRaises(ProtocolError): module.read_voltage_ramp()

    def test_firmware_220_rejects_wrong_or_zero_nominal_polarity(self):
        for port, polarity in [('N', '+'), ('P', '-')]:
            with self.subTest(port=port):
                m = NHSModule(port, polarity=polarity, max_voltage=100,
                              serial_factory=Firmware220Serial)
                with self.assertRaises(DeviceError): m.open()
                self.assertIsNone(m._serial)
        class ZeroNominalSerial(Firmware220Serial):
            nominal_override = 0
        m = NHSModule('P', polarity='+', max_voltage=100, serial_factory=ZeroNominalSerial)
        with self.assertRaises(DeviceError): m.open()

    def test_corrupted_echo_is_not_returned_as_an_answer(self):
        with self.module() as m:
            m._serial.drop = True
            m._serial.buffer = b'TIDN?\r\n'
            with self.assertRaises(CommunicationError): m.query('*IDN?')
            self.assertTrue(m._broken)

    def test_byte_handshake_consumes_crlf_echo_before_answer(self):
        with Bender('P', 'N', max_voltage=100, echo_handshake=True,
                    serial_factory=ByteEchoSerial) as b:
            b.set_voltages(p0=10, p1=11, n0=-12, n1=-13)
            self.assertEqual(b.read_all()['n1'].voltage_set, -13)
            self.assertEqual(b.positive._serial.buffer, b'')

    def test_bad_or_missing_byte_echo_stops_without_retry(self):
        for missing in [False, True]:
            with self.subTest(missing=missing):
                with NHSModule('P', polarity='+', max_voltage=100, echo_handshake=True,
                               serial_factory=ByteEchoSerial) as m:
                    m._serial.echo = not missing
                    m._serial.bad_echo = True
                    with self.assertRaises(CommunicationError): m.query('*IDN?')
                    self.assertEqual(m._serial.pending, b'*')
                    self.assertTrue(m._broken)
                    with self.assertRaises(CommunicationError): m.disable(0)
                    self.assertEqual(m._serial.pending, b'*')

    def test_no_timeout_reconfiguration_during_serial_transactions(self):
        class FixedTimeout:
            @property
            def timeout(self):
                return self._fixed_timeout

            @timeout.setter
            def timeout(self, value):
                if hasattr(self, '_fixed_timeout'):
                    raise AssertionError('Timeout assignment would reconfigure a live Windows port')
                self._fixed_timeout = value

        for handshake, base in [(False, FakeSerial), (True, ByteEchoSerial)]:
            factory = type('FixedTimeoutSerial', (FixedTimeout, base), {})
            with self.subTest(handshake=handshake):
                with NHSModule('P', polarity='+', max_voltage=100,
                               echo_handshake=handshake, serial_factory=factory) as m:
                    self.assertIn('iseg', m.query('*IDN?'))
                    m.read_channel(0)
                    m.set_voltage(0, 12)
                    self.assertEqual(m.read_voltage_set(0), 12)
                    m._serial.drop = True
                    with self.assertRaises(CommunicationError): m.query('*IDN?')
                    self.assertTrue(m._broken)
                    with self.assertRaises(CommunicationError): m.disable(0)

class RegulationTests(unittest.TestCase):
    def setUp(self):
        clock = Mock(return_value=0.0)

        def advance(seconds):
            clock.return_value += seconds

        for name, replacement in [('sleep', advance), ('monotonic', clock)]:
            timer = patch('lapis_control.devices.bender.iseg_nhs.time.' + name, replacement)
            timer.start()
            self.addCleanup(timer.stop)

    def pilot(self, module, **overrides):
        options = dict(tolerance_v=0.2, max_step_v=1.0, max_offset_v=5.0,
                       interval_s=5.0, convergence_timeout_s=40.0)
        return module.regulate_voltage(0, module.sign * 15.0, **(options | overrides))

    def prepared_module(self, polarity='+', limit=100):
        module = NHSModule('P' if polarity == '+' else 'N', polarity=polarity,
                           max_voltage=limit, serial_factory=FakeSerial).connect()
        self.addCleanup(module.close)
        module.set_voltage(0, module.sign * 15.0)
        module.enable(0)
        module._serial.sent.clear()
        return module

    def test_offset_convergence_and_later_drift_on_both_polarities(self):
        for polarity in '+-':
            with self.subTest(polarity=polarity):
                module = self.prepared_module(polarity)
                device = module._serial
                device.measurement_offset = -3.0
                loop = self.pilot(module)
                first = next(loop)
                self.assertEqual(first.voltage, module.sign * 12.0)
                self.assertEqual(first.requested_voltage, module.sign * 16.0)
                for _ in range(5):
                    sample = next(loop)
                self.assertLessEqual(abs(module.sign * 15.0 - sample.voltage), 0.2)
                self.assertIsNone(sample.requested_voltage)
                device.measurement_offset = -1.0
                drift = next(loop)
                self.assertLess(abs(drift.requested_voltage), abs(drift.voltage_set))
                writes = [cmd for cmd in device.sent if ';' in cmd]
                self.assertTrue(all(cmd.startswith(':VOLT ') and cmd.endswith(',(@0);*OPC?')
                                    and 'ON' not in cmd and 'OFF' not in cmd for cmd in writes))

    def test_ramping_waits_and_times_out_without_writing(self):
        module = self.prepared_module()
        module._serial.extra_status = ChannelStatus.RAMPING
        loop = self.pilot(module, convergence_timeout_s=10)
        self.assertIsNone(next(loop).requested_voltage)
        with self.assertRaisesRegex(VerificationError, 'did not settle'):
            next(loop)
        self.assertFalse(any(';' in cmd for cmd in module._serial.sent))

    def test_decimal_band_edges_count_as_in_tolerance_without_a_write(self):
        for polarity in '+-':
            module = self.prepared_module(polarity)
            module.set_voltage(0, module.sign * 5.0)
            loop = module.regulate_voltage(0, module.sign * 5.0, tolerance_v=0.2,
                                           max_step_v=1.0, max_offset_v=5, interval_s=None)
            module._serial.sent.clear()
            for offset in (-0.2, 0.2):
                with self.subTest(polarity=polarity, offset=offset):
                    module._serial.measurement_offset = offset
                    self.assertTrue(next(loop).in_tolerance)
            self.assertFalse(any(';' in cmd for cmd in module._serial.sent))
            module._serial.measurement_offset = 0.21
            self.assertFalse(next(loop).in_tolerance)

    def test_measured_excursion_from_p0_trial_stops_before_second_correction(self):
        for polarity in '+-':
            with self.subTest(polarity=polarity):
                module = self.prepared_module(polarity)
                device = module._serial
                device.measurement_offset = -1.21
                loop = self.pilot(module)
                first = next(loop)
                self.assertAlmostEqual(abs(first.voltage), 13.79)
                self.assertAlmostEqual(abs(first.requested_voltage), 15.605)
                device.volts[0] = 15.60
                device.measurement_offset = 14.43
                with self.assertRaisesRegex(VerificationError, r'30\.03 V exceeds 20 V'):
                    next(loop)
                self.assertEqual(sum(';' in cmd for cmd in device.sent), 1)

    def test_limit_ends_regulation_instead_of_repeating_command(self):
        for polarity in '+-':
            for limit, expected in [(100, 20), (16, 16)]:
                with self.subTest(polarity=polarity, limit=limit):
                    module = self.prepared_module(polarity, limit)
                    module._serial.measurement_offset = -10.0
                    loop = self.pilot(module)
                    with self.assertRaisesRegex(VerificationError, 'trim limit'):
                        list(loop)
                    self.assertEqual(module.read_voltage_set(0), module.sign * expected)
                    self.assertEqual(sum(';' in cmd for cmd in module._serial.sent), expected - 15)

    def test_p0_range_with_recorded_readback_and_both_upper_limits(self):
        for polarity in '+-':
            for application_limit in (60, 120):
                with self.subTest(polarity=polarity, application_limit=application_limit):
                    module = self.prepared_module(polarity, application_limit)
                    device = module._serial
                    options = dict(max_offset_v=None, setpoint_range_v=(10, 100))
                    device.volts[0] = 15.60
                    device.measurement_offset = 14.43
                    loop = self.pilot(module, **options)
                    self.assertAlmostEqual(next(loop).requested_voltage, module.sign * 14.60)
                    with self.assertRaisesRegex(VerificationError, 'trim limit'):
                        list(loop)
                    self.assertEqual(device.volts[0], 10)

                    upper = min(100, application_limit)
                    device.volts[0] = upper - 0.5
                    device.measurement_offset = 13.0 - device.volts[0]
                    loop = self.pilot(module, **options)
                    self.assertEqual(next(loop).requested_voltage, module.sign * upper)
                    device.measurement_offset = 0.01
                    writes = sum(';' in cmd for cmd in device.sent)
                    with self.assertRaisesRegex(VerificationError, f'exceeds {upper} V'):
                        next(loop)
                    self.assertEqual(sum(';' in cmd for cmd in device.sent), writes)

    def test_off_fault_and_current_regulation_never_correct(self):
        module = self.prepared_module()
        device = module._serial
        device.measurement_offset = -3
        for on, status, mod_error in [(False, 0, False), (True, ChannelStatus.CURRENT_TRIP, False),
                                      (True, ChannelStatus.CONSTANT_CURRENT, False), (True, 0, True)]:
            with self.subTest(on=on, status=status, module_error=mod_error):
                device.on[0], device.extra_status, device.mod_error = on, status, mod_error
                with self.assertRaises(DeviceError):
                    next(self.pilot(module))
                self.assertFalse(any(';' in cmd for cmd in device.sent))

    def test_external_setpoint_change_stops_loop(self):
        module = self.prepared_module()
        loop = self.pilot(module)
        self.assertIsNone(next(loop).requested_voltage)
        module._serial.volts[0] = 16
        with self.assertRaisesRegex(VerificationError, 'changed during regulation'):
            next(loop)
        module._serial.volts[0] = 5
        with self.assertRaisesRegex(VerificationError, 'outside trim limits'):
            next(self.pilot(module))
        self.assertFalse(any(';' in cmd for cmd in module._serial.sent))

    def test_invalid_parameters_and_ambiguous_write_stop_loop(self):
        module = self.prepared_module()
        device = module._serial
        for options in [dict(tolerance_v=0), dict(max_step_v=float('nan')), dict(max_offset_v=-1),
                        dict(interval_s=0), dict(convergence_timeout_s=5), dict(max_offset_v=None),
                        dict(setpoint_range_v=(10, 100)), dict(max_offset_v=None, setpoint_range_v=(16, 100))]:
            with self.assertRaises(ValueError):
                next(self.pilot(module, **options))
        device.measurement_offset = -3
        write = device.write

        def lose_ack(data):
            result = write(data)
            if data.startswith(b':VOLT '):
                device.buffer = b''
            return result

        device.write = lose_ack
        loop = self.pilot(module)
        with self.assertRaises(CommunicationError):
            next(loop)
        self.assertEqual(list(loop), [])
        self.assertEqual(sum(';' in cmd for cmd in device.sent), 1)


if __name__ == '__main__': unittest.main()
