"""Offline regression tests: no hardware access or pulse generation."""
from collections import deque
import struct
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import numpy as np
import pyvisa

src_dir = str(Path(__file__).resolve().parents[4])
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from lapis_control.devices.oscilloscope.keysight_dsox1204g import KeysightDSOX1204G, ScopeError, AcquisitionTimeout
from lapis_control.devices.oscilloscope.lapis_workflows import experimental_wavegen_gate
from lapis_control.devices.oscilloscope.tests import scope_cli

class Instrument:
    def __init__(self):
        self.commands=[]; self.closed=False; self.timeout=5000
        self.read_termination='\n'; self.write_termination='\n'
        self.values={
            '*IDN?':'KEYSIGHT TECHNOLOGIES,DSOX1204G,MY12345678,02.12.0000',
            ':ACQuire:TYPE?':'NORM', ':ACQuire:MODE?':'RTIM', ':TIMebase:MODE?':'MAIN',
            ':OPERegister:CONDition?':'0', ':TER?':'0', ':AER?':'0', '*OPC?':'1',
            ':SYSTem:ERRor?':'0,"No error"', ':WGEN:OUTPut?':'0', ':WGEN:FUNCtion?':'PULS',
            ':WGEN:OUTPut:LOAD?':'ONEM', ':WGEN:OUTPut:POLarity?':'NORM',
            ':WGEN:MODulation:STATe?':'0', ':WGEN:MODulation:NOISe?':'0',
            ':WGEN:FREQuency?':'1', ':WGEN:FUNCtion:PULSe:WIDTh?':'0.0001',
            ':WGEN:VOLTage:LOW?':'0', ':WGEN:VOLTage:HIGH?':'5'}
        self.values.update({':ACQuire:SRATe?':'5e8', ':TIMebase:SCALe?':'1e-6',
                            ':TIMebase:POSition?':'0', ':TRIGger?':':TRIG:MODE EDGE;SWE NORM'})
        for ch in range(1,5): self.values[f':CHANnel{ch}?']=':CHAN:DISP 1;COUP DC'
        self.preambles={1:'1,0,5,1,2e-9,-4e-9,0,0.01,-0.3,32768',
                        2:'1,0,5,1,2e-9,-4e-9,0,0.02,1.5,32768'}
        self.channel=1
        self.samples=np.array([0,1,32768,33034,65535],dtype=np.uint16)
        self.ascii=np.array([-0.8,-0.3,0,1.1,3.2])
        self.binary_options=None; self.fail_query=None; self.fail_binary=False
        self.fail_enable=False; self.cleared=False
        self.events_after_single=deque(['0','1'])
    def write(self, command):
        self.commands.append(command)
        if command==':STOP': self.values[':OPERegister:CONDition?']='0'
        elif command==':SINGle':
            self.values[':TER?']=self.events_after_single.copy(); self.values[':AER?']='1'
        elif command.startswith(':WAVeform:SOURce'): self.channel=int(command[-1])
        elif command.startswith(':WGEN:OUTPut '):
            self.values[':WGEN:OUTPut?']='1' if command.endswith(('1','ON')) else '0'
            if self.fail_enable and self.values[':WGEN:OUTPut?']=='1': self.timeout_error()
        return len(command)
    @staticmethod
    def timeout_error():
        raise pyvisa.errors.VisaIOError(pyvisa.constants.StatusCode.error_timeout)
    def query(self, command):
        self.commands.append(command)
        if command==self.fail_query: self.timeout_error()
        if command==':WAVeform:PREamble?': return self.preambles[self.channel]
        if command.startswith(':CHANnel') and command.endswith(':DISPlay?'): return '1'
        if command not in self.values: raise AssertionError(f'Unerwartete Abfrage: {command}')
        value=self.values[command]
        if isinstance(value,deque): return value.popleft() if len(value)>1 else value[0]
        return value
    def query_binary_values(self, command, **kwargs):
        self.commands.append(command)
        self.binary_options=dict(kwargs,read_termination=self.read_termination)
        if self.fail_binary: self.timeout_error()
        # Real IEEE block: 33034 = 0x810A contains a newline in the payload.
        payload=struct.pack('<'+kwargs['datatype']*len(self.samples),*self.samples)
        size=str(len(payload)).encode()
        block=b'#'+str(len(size)).encode()+size+payload+b'\n'
        return pyvisa.util.from_ieee_block(block,datatype=kwargs['datatype'],
                    is_big_endian=kwargs['is_big_endian'],container=np.array)
    def query_ascii_values(self,command,**kwargs):
        self.commands.append(command); return self.ascii
    def close(self): self.closed=True
    def clear(self): self.cleared=True; self.fail_query=None

class Manager:
    def __init__(self,inst): self.inst=inst; self.closed=False
    def open_resource(self,resource): return self.inst
    def close(self): self.closed=True

class DriverTests(unittest.TestCase):
    def setUp(self):
        self.inst=Instrument(); self.rm=Manager(self.inst)
        self.scope=KeysightDSOX1204G('USB0::TEST::INSTR',resource_manager=self.rm)
        self.scope.connect()
    def tearDown(self):
        self.inst.fail_enable=False; self.inst.fail_query=None
        self.scope._io_failed=False; self.scope.close()
    def test_connection_changes_no_output(self):
        self.scope.close()
        self.assertEqual(self.inst.commands,['*IDN?'])
        self.assertTrue(self.inst.closed); self.assertFalse(self.rm.closed)
    def test_wrong_model_rejected(self):
        self.inst.values['*IDN?']='KEYSIGHT,DSOX4054A,MY123,1.0'
        self.scope.close()
        with self.assertRaises(ScopeError): KeysightDSOX1204G('x',resource_manager=self.rm).connect()
        self.assertTrue(self.inst.closed)
        self.assertFalse(any(':WGEN:OUTPut ' in c for c in self.inst.commands))
    def test_constructor_has_no_transport_side_effects(self):
        with patch('pyvisa.ResourceManager') as create_manager:
            scope = KeysightDSOX1204G('USB0::TEST::INSTR')
            scope.close()
            create_manager.assert_not_called()
            with self.assertRaisesRegex(ScopeError, 'connect'):
                scope.status()
    def test_repeated_connect_preserves_session(self):
        self.assertIs(self.scope.connect(), self.scope)
        self.assertEqual(self.inst.commands, ['*IDN?'])
        self.inst.fail_query = '*IDN?'
        with self.assertRaises(pyvisa.errors.VisaIOError):
            self.scope.query_scpi('*IDN?')
        with self.assertRaisesRegex(ScopeError, 'blocked'):
            self.scope.connect()
        self.assertEqual(self.inst.commands, ['*IDN?', '*IDN?'])
    def test_owned_manager_recreated_after_close(self):
        first, second = Manager(Instrument()), Manager(Instrument())
        with patch('pyvisa.ResourceManager', side_effect=[first, second]) as create_manager:
            scope = KeysightDSOX1204G('USB0::TEST::INSTR', timeout_ms=1234)
            with scope as connected:
                self.assertIs(connected, scope)
                self.assertEqual(first.inst.timeout, 1234)
                scope.configure_ttl(load='HIGHZ')
            self.assertTrue(first.closed)
            with scope:
                with self.assertRaises(ScopeError):
                    scope.wavegen_output(True)
            self.assertTrue(second.closed)
            self.assertEqual(second.inst.commands, ['*IDN?'])
            self.assertEqual(create_manager.call_count, 2)
    def test_failed_connect_releases_owned_manager_and_can_retry(self):
        bad, good = Manager(Instrument()), Manager(Instrument())
        bad.inst.values['*IDN?'] = 'KEYSIGHT,WRONG,123,1.0'
        with patch('pyvisa.ResourceManager', side_effect=[bad, good]):
            scope = KeysightDSOX1204G('USB0::TEST::INSTR')
            with self.assertRaises(ScopeError):
                scope.connect()
            self.assertTrue(bad.inst.closed)
            self.assertTrue(bad.closed)
            with scope:
                self.assertIn('DSOX1204G', scope.idn)
            self.assertTrue(good.closed)
    def test_open_failure_closes_only_owned_manager(self):
        for owned in (False, True):
            with self.subTest(owned=owned):
                manager = Mock()
                manager.open_resource.side_effect = OSError('VISA open failed')
                with patch('pyvisa.ResourceManager', return_value=manager):
                    scope = KeysightDSOX1204G('x', resource_manager=None if owned else manager)
                    with self.assertRaisesRegex(OSError, 'VISA open failed'):
                        scope.connect()
                self.assertEqual(manager.close.call_count, int(owned))
    def test_context_releases_connection_after_body_error(self):
        instrument = Instrument()
        with self.assertRaisesRegex(ValueError, 'body failed'):
            with KeysightDSOX1204G('x', resource_manager=Manager(instrument)):
                raise ValueError('body failed')
        self.assertTrue(instrument.closed)
    def test_word_scale_origin_unsigned_holes_clipping(self):
        w=self.scope.read_waveform(1)
        np.testing.assert_allclose(w.time_s,[-4e-9,-2e-9,0,2e-9,4e-9])
        np.testing.assert_allclose(w.voltage_v[1:],[-327.97,-0.3,2.36,327.37])
        self.assertTrue(np.isnan(w.voltage_v[0]))
        np.testing.assert_array_equal(w.clipped_low,[0,1,0,0,0])
        np.testing.assert_array_equal(w.clipped_high,[0,0,0,0,1])
        self.assertEqual(self.inst.binary_options['datatype'],'H')
        self.assertFalse(self.inst.binary_options['is_big_endian'])
        self.assertIsNone(self.inst.binary_options['read_termination'])
        self.assertEqual(self.inst.read_termination,'\n')
    def test_byte_scale(self):
        self.inst.preambles[1]='0,0,3,1,1e-6,0,0,0.02,1,128'
        self.inst.samples=np.array([2,128,254],dtype=np.uint8)
        w=self.scope.read_waveform(1,format='BYTE')
        np.testing.assert_allclose(w.voltage_v,[-1.52,1,3.52])
        self.assertEqual(self.inst.binary_options['datatype'],'B')
    def test_ascii_not_scaled_twice(self):
        self.inst.preambles[1]='4,0,5,1,1e-6,0,0,0.01,4,128'
        w=self.scope.read_waveform(1,format='ASCII')
        np.testing.assert_array_equal(w.voltage_v,self.inst.ascii)
    def test_multi_channel_own_scaling_one_stop(self):
        waves=self.scope.read_waveforms([1,2])
        self.assertAlmostEqual(waves[1].voltage_v[2],-0.3)
        self.assertAlmostEqual(waves[2].voltage_v[2],1.5)
        self.assertEqual(self.inst.commands.count(':STOP'),1)
        self.assertNotIn(':SINGle',self.inst.commands)
    def test_short_transfer_rejected(self):
        self.inst.samples=self.inst.samples[:-1]
        with self.assertRaisesRegex(ScopeError,'Incomplete transfer'): self.scope.read_waveform(1)
    def test_invalid_preamble_rejected(self):
        self.inst.preambles[1]='1,2,3'
        with self.assertRaises(ScopeError): self.scope.read_waveform(1)
    def test_peak_not_given_wrong_time_axis(self):
        self.inst.values[':ACQuire:TYPE?']='PEAK'
        with self.assertRaisesRegex(ScopeError,'PEAK'): self.scope.read_waveform(1)
        self.assertNotIn(':WAVeform:DATA?',self.inst.commands)
    def test_average_uses_measurement_record(self):
        self.inst.values[':ACQuire:TYPE?']='AVER'; self.scope.read_waveform(1)
        self.assertIn(':WAVeform:POINts:MODE NORMAL',self.inst.commands)
        self.assertIn(':WAVeform:POINts 1000',self.inst.commands)
    def test_single_clears_old_event(self):
        self.inst.values[':TER?']='1'; self.scope.arm_single()
        self.scope.wait_until_armed(); self.scope.wait_for_single(timeout_s=.1,poll_s=.001)
        self.assertFalse(self.scope._single_pending)
        self.assertLess(self.inst.commands.index(':TER?'),self.inst.commands.index(':SINGle'))
        self.assertGreaterEqual(self.inst.commands.count(':TER?'),3)
    def test_trigger_without_completion_times_out(self):
        self.scope.arm_single(); self.inst.values[':TER?']='1'
        self.inst.values[':OPERegister:CONDition?']='8'
        with self.assertRaises(AcquisitionTimeout):
            self.scope.wait_for_single(timeout_s=.005,poll_s=.001)
        self.assertEqual(self.inst.values[':OPERegister:CONDition?'],'0')
    def test_no_trigger_rejects_stale_trace(self):
        self.inst.events_after_single=deque(['0']); self.scope.arm_single()
        with self.assertRaises(AcquisitionTimeout):
            self.scope.wait_for_single(timeout_s=.005,poll_s=.001)
        self.assertFalse(self.scope._single_pending)
        self.assertNotIn(':WAVeform:DATA?',self.inst.commands)
    def test_read_pending_single_rejected(self):
        self.scope.arm_single()
        with self.assertRaisesRegex(ScopeError,'wait_for_single'): self.scope.read_waveform(1)
    def test_binary_timeout_restores_termination_locks_io(self):
        self.inst.fail_binary=True
        with self.assertRaises(pyvisa.errors.VisaIOError): self.scope.read_waveform(1)
        self.assertEqual(self.inst.read_termination,'\n')
        with self.assertRaisesRegex(ScopeError,'recover'): self.scope.query_scpi('*IDN?')
        self.scope.recover(); self.assertTrue(self.inst.cleared)
        self.assertIn('DSOX1204G',self.scope.query_scpi('*IDN?'))
    def test_digitize_timeout_no_retry(self):
        self.inst.fail_query='*OPC?'
        with self.assertRaises(pyvisa.errors.VisaIOError): self.scope.digitize(timeout_s=.01)
        self.assertEqual(self.inst.timeout,5000)
        self.assertEqual(sum(c.startswith(':DIGitize') for c in self.inst.commands),1)
        self.scope.recover(); self.assertTrue(self.inst.cleared)
    def test_scpi_error_with_comma(self):
        self.inst.values[':SYSTem:ERRor?']=deque(['-222,"Data out of range, high"','0,"No error"'])
        with self.assertRaisesRegex(ScopeError,'Data out of range, high'): self.scope.check_errors()
    def test_validation_before_mutation(self):
        before=len(self.inst.commands)
        for options in ({'scale_v':float('nan')},{'probe':0},{'enabled':'OFF'}):
            with self.assertRaises(ValueError): self.scope.configure_channel(1,**options)
        with self.assertRaises(ValueError):
            self.scope.configure_edge_trigger(1,level_v=1,holdoff_s=float('inf'))
        self.assertEqual(before,len(self.inst.commands))
    def test_ttl_requires_load_and_configuration(self):
        with self.assertRaises(TypeError): self.scope.configure_ttl()
        with self.assertRaises(ScopeError): self.scope.wavegen_output(True)
    def test_ttl_off_amplitude_offset_and_close(self):
        self.assertFalse(self.scope.configure_ttl(load='HIGHZ')['output'])
        self.assertIn(':WGEN:FUNCtion:PULSe:WIDTh 0.0001',self.inst.commands)
        self.assertIn(':WGEN:VOLTage 5',self.inst.commands)
        self.assertIn(':WGEN:VOLTage:OFFSet 2.5',self.inst.commands)
        self.assertNotIn(':WGEN:OUTPut 1',self.inst.commands)
        self.scope.wavegen_output(True); self.assertEqual(self.inst.values[':WGEN:OUTPut?'],'1')
        self.scope.close(); self.assertEqual(self.inst.values[':WGEN:OUTPut?'],'0')
    def test_clamped_voltage_blocks_enable(self):
        self.inst.values[':WGEN:VOLTage:HIGH?']='2.5'
        with self.assertRaises(ScopeError): self.scope.configure_ttl(load='HIGHZ')
        with self.assertRaises(ScopeError): self.scope.wavegen_output(True)
        self.assertEqual(self.inst.values[':WGEN:OUTPut?'],'0')
    def test_changed_frequency_blocks_enable(self):
        self.scope.configure_ttl(load='HIGHZ'); self.inst.values[':WGEN:FREQuency?']='50'
        with self.assertRaises(ScopeError): self.scope.wavegen_output(True)
    def test_gate_disables_after_interrupt(self):
        self.scope.configure_ttl(load='HIGHZ')
        with patch('lapis_control.devices.oscilloscope.lapis_workflows.time.sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): experimental_wavegen_gate(self.scope)
        self.assertEqual(self.inst.values[':WGEN:OUTPut?'],'0')
    def test_gate_rejects_running_generator(self):
        self.inst.values[':WGEN:OUTPut?']='1'
        with self.assertRaises(ScopeError): experimental_wavegen_gate(self.scope)
    def test_close_off_after_transport_error(self):
        self.scope.configure_ttl(load='HIGHZ'); self.inst.fail_enable=True
        with self.assertRaises(pyvisa.errors.VisaIOError): self.scope.wavegen_output(True)
        with self.assertRaisesRegex(ScopeError,'shutdown not confirmed'): self.scope.close()
        self.assertEqual(self.inst.values[':WGEN:OUTPut?'],'0'); self.assertTrue(self.inst.closed)
    def test_status_script_does_not_change_hardware(self):
        with patch('sys.argv',['scope_cli.py','--resource','USB0::TEST::INSTR']), \
             patch.object(scope_cli,'KeysightDSOX1204G',return_value=self.scope), \
             patch('builtins.print'):
            scope_cli.main()
        self.assertTrue(all('?' in command for command in self.inst.commands))
    def test_read_script_returns_waveforms_without_files(self):
        with patch('sys.argv',['scope_cli.py','--resource','x','--read']), \
             patch.object(scope_cli,'KeysightDSOX1204G',return_value=self.scope), \
             patch('builtins.print'), \
             patch('numpy.savez_compressed') as save_arrays, \
             patch.object(Path,'write_text') as save_text:
            waves=scope_cli.main()
        self.assertAlmostEqual(waves[2].voltage_v[2],1.5)
        np.testing.assert_allclose(waves[1].time_s,[-4e-9,-2e-9,0,2e-9,4e-9])
        self.assertEqual(waves[2].preamble['transfer_format'],'WORD')
        save_arrays.assert_not_called(); save_text.assert_not_called()
        self.assertFalse(any(':WGEN:OUTPut ' in c for c in self.inst.commands))
    def test_capture_script_waits_and_does_not_fire_generator(self):
        with patch('sys.argv',['scope_cli.py','--resource','x','--capture']), \
             patch.object(scope_cli,'KeysightDSOX1204G',return_value=self.scope), \
             patch('builtins.print'):
            waves=scope_cli.main()
        self.assertEqual(set(waves),{1,2})
        self.assertLess(self.inst.commands.index(':SINGle'),self.inst.commands.index(':WAVeform:DATA?'))
        self.assertFalse(any(':WGEN:OUTPut ' in c for c in self.inst.commands))

if __name__=='__main__': unittest.main()
