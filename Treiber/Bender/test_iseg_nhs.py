"""Offline tests. The fake models protocol behavior, not physical hardware."""
import unittest
from iseg_nhs import (NHSModule, Bender, CommunicationError, DeviceError,
                      PartialUpdateError, ProtocolError, parse_number, ChannelStatus)

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
            response = str(int(self.positive) + (8 if self.on[ch] else 0))
        elif cmd == ':READ:MODULE:STAT?': response = str(64 if self.mod_error else 30464)
        elif 'EVENT:STAT?' in cmd: response = '0'
        elif cmd.startswith(':READ:VOLT:NOM?'): response = '4.00000E3V'
        elif cmd.startswith((':READ:VOLT?', ':MEAS:VOLT?')): response = f'{self.volts[ch]}V'
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

class DriverTests(unittest.TestCase):
    def module(self, polarity='+'):
        return NHSModule('P' if polarity == '+' else 'N', polarity=polarity,
                         max_voltage=100, serial_factory=FakeSerial)
    def bender(self):
        return Bender('P','N',max_voltage=100,serial_factory=FakeSerial)
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
        from iseg_nhs import VerificationError
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
    def test_command_length_and_injection(self):
        with self.module() as m:
            for cmd in ['*IDN?\r\n:VOLT ON','X'*80+'?','*IDN?;*IDN?']:
                with self.assertRaises(ValueError): m.query(cmd)

if __name__ == '__main__': unittest.main()
