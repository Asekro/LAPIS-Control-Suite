"""Verify setup validation and device construction without hardware access."""
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from lapis_control.configuration import ConfigurationError, load_devices


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'devices.json'
        self.example = json.loads((ROOT / 'config/devices.example.json').read_text(encoding='utf-8'))
        serial_patch = patch('serial.Serial.open', side_effect=AssertionError('Unexpected serial access'))
        visa_patch = patch('pyvisa.ResourceManager', side_effect=AssertionError('Unexpected VISA access'))
        self.open_serial = serial_patch.start()
        self.open_visa = visa_patch.start()
        self.addCleanup(serial_patch.stop)
        self.addCleanup(visa_patch.stop)

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')
        return load_devices(self.path)

    def test_example_creates_disconnected_devices(self):
        devices = load_devices(ROOT / 'config/devices.example.json')
        self.assertEqual(set(devices), {'bender', 'laser', 'oscilloscope', 'vacuum'})
        self.assertFalse(devices['vacuum'].is_connected)
        self.assertEqual(devices['bender'].positive.port, self.example['bender']['positive_port'])
        self.assertEqual(devices['bender'].positive.expected_serial, self.example['bender']['positive_serial'])
        self.assertEqual(devices['laser'].port, self.example['laser']['port'])
        self.assertEqual(devices['laser'].handshake_interface, 'USB')
        self.assertEqual(devices['oscilloscope'].resource, self.example['oscilloscope']['resource'])
        for device in devices.values():
            device.close()
        self.open_serial.assert_not_called()
        self.open_visa.assert_not_called()

    def test_ports_and_runtime_settings_are_applied(self):
        data = copy.deepcopy(self.example)
        data['vacuum']['port'] = 'COM85'
        data['vacuum']['communication'].update(timeout_s=2.25, write_timeout_s=1.75)
        data['bender']['positive_port'] = 'COM90'
        data['bender']['communication']['timeout_s'] = 3.5
        data['bender']['communication']['echo_handshake'] = True
        data['laser']['communication'].update(timeout_s=1.75, write_timeout_s=2.5,
                                               handshake_interface='SERIAL')
        data['oscilloscope']['communication'].update(timeout_ms=1234, visa_backend='@py')
        devices = self.load(data)
        self.assertEqual(devices['vacuum'].port, 'COM85')
        self.assertEqual(devices['vacuum'].timeout, 2.25)
        self.assertEqual(devices['vacuum'].write_timeout, 1.75)
        self.assertEqual(devices['bender'].positive.port, 'COM90')
        self.assertEqual(devices['bender'].negative.timeout, 3.5)
        self.assertTrue(devices['bender'].negative.echo_handshake)
        self.assertEqual(devices['laser'].timeout, 1.75)
        self.assertEqual(devices['laser'].write_timeout, 2.5)
        self.assertEqual(devices['laser'].handshake_interface, 'SERIAL')
        instrument = Mock()
        instrument.query.return_value = 'KEYSIGHT TECHNOLOGIES,DSOX1204G,123,1.0'
        manager = Mock()
        manager.open_resource.return_value = instrument
        with patch('pyvisa.ResourceManager', return_value=manager) as create_manager:
            with devices['oscilloscope']:
                self.assertEqual(instrument.timeout, 1234)
                self.assertEqual(instrument.read_termination, '\n')
            create_manager.assert_called_once_with('@py')
            manager.close.assert_called_once()
            instrument.write.assert_not_called()

    def test_example_omits_fixed_fields_and_older_local_files_still_load(self):
        serial_profile = dict(baudrate=9600, bytesize=8, parity='N', stopbits=1,
                              xonxoff=False, rtscts=False, dsrdtr=False)
        legacy_fields = {
            'vacuum': dict(serial_profile, terminator='\r'),
            'bender': dict(serial_profile, terminator='\r\n'),
            'laser': dict(serial_profile, terminator='\r\n', minimum_command_delay_s=0.1,
                          command_handshake=True),
            'oscilloscope': {'terminator': '\n'},
        }
        data = copy.deepcopy(self.example)
        for name, fields in legacy_fields.items():
            self.assertTrue(fields.keys().isdisjoint(data[name]['communication']))
            data[name]['communication'].update(fields)
        data['vacuum']['port'] = 'COM83'
        self.assertEqual(self.load(data)['vacuum'].port, 'COM83')
        self.open_serial.assert_not_called()
        self.open_visa.assert_not_called()

    def test_unsupported_protocol_and_invalid_runtime_settings_fail(self):
        cases = [
            ('vacuum', 'terminator', '\r\n'), ('vacuum', 'write_timeout_s', 0),
            ('vacuum', 'baudrate', 19200), ('vacuum', 'allow_write', True),
            ('bender', 'baudrate', 115200), ('bender', 'baudrate', '9600'),
            ('bender', 'parity', 'E'), ('bender', 'stopbits', True),
            ('bender', 'rtscts', True), ('bender', 'echo_handshake', 'false'),
            ('bender', 'timeout_s', 0), ('bender', 'timeout_s', float('nan')),
            ('laser', 'command_handshake', False), ('laser', 'minimum_command_delay_s', 0),
            ('laser', 'handshake_interface', 42), ('laser', 'write_timeout_s', -1),
            ('oscilloscope', 'terminator', '\r'), ('oscilloscope', 'timeout_ms', True),
            ('oscilloscope', 'timeout_ms', 2**31), ('oscilloscope', 'visa_backend', 42),
        ]
        for name, key, value in cases:
            with self.subTest(device=name, key=key, value=value):
                data = copy.deepcopy(self.example)
                data[name]['communication'][key] = value
                with self.assertRaises(ConfigurationError):
                    self.load(data)
        self.open_serial.assert_not_called()
        self.open_visa.assert_not_called()

    def test_only_requested_devices_are_built(self):
        devices = self.load({'oscilloscope': self.example['oscilloscope']})
        self.assertEqual(list(devices), ['oscilloscope'])

    def test_unknown_missing_and_invalid_device_sections_fail(self):
        data = copy.deepcopy(self.example)
        data['bender']['communication']['baud_rate'] = 9600
        for invalid in (data, {'scope': {}}, {'bender': {}}, {}, [], {'laser': None}):
            with self.subTest(data=invalid):
                with self.assertRaises(ConfigurationError):
                    self.load(invalid)

    def test_malformed_json_and_duplicate_keys_fail(self):
        for contents in ('{', '{"laser": {}, "laser": {}}', '{"laser": {"port": "a", "port": "b"}}'):
            with self.subTest(contents=contents):
                self.path.write_text(contents, encoding='utf-8')
                with self.assertRaises(ConfigurationError):
                    load_devices(self.path)

    def test_missing_file_has_no_fallback(self):
        with self.assertRaises(FileNotFoundError):
            load_devices(self.path)


if __name__ == '__main__':
    unittest.main()
