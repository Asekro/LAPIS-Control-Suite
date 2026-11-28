"""Check the desktop entry point, disk diagnostics and exception hooks offline."""
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from lapis_control.devices.vacuum import edwards_tic as tic
from lapis_control.devices.vacuum.tests.test_edwards_tic import StreamPort

try:
    from PySide6.QtCore import qInstallMessageHandler, qWarning
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QApplication
    from lapis_control.gui import __main__ as launcher
    from lapis_control.gui.diagnostics import session_log
    from lapis_control.gui.window import MainWindow
except ModuleNotFoundError as exc:
    if exc.name != 'PySide6':
        raise
    QApplication = None


@unittest.skipUnless(QApplication, 'PySide6 GUI extra is optional')
class SessionLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        cls.app = QApplication.instance() or QApplication([])

    def test_launcher_and_open_log_button_use_the_actual_log_including_fallback(self):
        with TemporaryDirectory() as folder:
            base = Path(folder)
            invalid = base / 'existing-file'
            invalid.touch()
            for directory in (base / 'custom', invalid):
                expected = (directory if directory != invalid else base / 'LAPIS Control Suite' / 'logs') / 'lapis-control.log'

                def exercise_window() -> int:
                    window = next(w for w in self.app.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible())
                    try:
                        self.assertTrue(window.overview.open_log.isEnabled())
                        self.assertEqual(window.overview.open_log.toolTip(), str(expected))
                        window.overview.open_log.click()
                        self.assertEqual(Path(opener.call_args.args[0].toLocalFile()), expected)
                        window.notice('Messröhre geprüft.')
                    finally:
                        window.close()
                    return 0

                with (self.subTest(directory=directory),
                      patch.object(sys, 'argv', ['lapis-control', '--log-dir', str(directory)]),
                      patch.object(launcher, 'QApplication', return_value=self.app),
                      patch.object(QApplication, 'exec', side_effect=exercise_window),
                      patch.object(QDesktopServices, 'openUrl', return_value=True) as opener,
                      patch.object(launcher.tempfile, 'gettempdir', return_value=str(base))):
                    self.assertEqual(launcher.main(), 0)
                    contents = expected.read_text(encoding='utf-8')
                    for message in ('Session started', 'Messröhre geprüft.', 'Session ended'):
                        self.assertIn(message, contents)
                    if directory == invalid:
                        self.assertIn('Logdatei konnte hier nicht angelegt werden', contents)

    def test_disk_log_includes_serial_bytes_qt_and_uncaught_python_errors(self):
        def fail() -> None:
            raise RuntimeError('test uncaught failure')

        qt_messages = []
        previous_qt = qInstallMessageHandler(lambda kind, ctx, msg: qt_messages.append(msg))
        self.addCleanup(qInstallMessageHandler, previous_qt)
        with (TemporaryDirectory() as folder, patch.object(sys, 'excepthook') as python_hook,
              patch.object(threading, 'excepthook') as thread_hook):
            with session_log(Path(folder)) as path:
                port = StreamPort()
                port.reply.return_value = (b'=V808 22;48\r', b'=V913 2.3e-6;59;11;0;0\r')
                with patch.object(tic.serial, 'Serial', return_value=port), tic.EdwardsTIC('TEST-V') as device:
                    self.assertEqual(device.read_pressure_pa(1), 2.3e-6)
                try:
                    fail()
                except RuntimeError:
                    sys.excepthook(*sys.exc_info())
                thread = threading.Thread(target=fail, name='test-worker')
                thread.start()
                thread.join(2)
                self.assertFalse(thread.is_alive())
                qWarning('test Qt diagnostic')
            self.assertIs(sys.excepthook, python_hook)
            self.assertIs(threading.excepthook, thread_hook)
            python_hook.assert_called_once()
            thread_hook.assert_called_once()
            qWarning('after session')
            self.assertEqual(qt_messages[-2:], ['test Qt diagnostic', 'after session'])
            contents = path.read_text(encoding='utf-8')
            for message in ("TX b'?V913\\r'", 'RX for 913 after', "b'=V808 22;48\\r'",
                            'RuntimeError: test uncaught failure', 'Unhandled application exception',
                            '[test-worker]', 'Unhandled thread exception', 'test Qt diagnostic'):
                self.assertIn(message, contents)
            self.assertNotIn('after session', contents)

    def test_large_log_rolls_over_and_next_session_appends(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / 'lapis-control.log'
            with path.open('wb') as old:
                old.seek(4 * 1024 * 1024)
                old.write(b'x')
            for _ in range(2):
                with session_log(directory):
                    pass
            self.assertTrue((directory / 'lapis-control.log.1').is_file())
            self.assertEqual(path.read_text(encoding='utf-8').count('Session started'), 2)
