"""Run the LAPIS desktop application."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QLocale, QTranslator, QLibraryInfo, QStandardPaths
from PySide6.QtWidgets import QApplication

from .diagnostics import session_log
from .widgets import set_style
from .window import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description='LAPIS Control Suite')
    parser.add_argument('--config', type=Path, help='Gerätekonfiguration (*.json)')
    parser.add_argument('--log-dir', type=Path, help='Ordner für die Logdatei')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('LAPIS Control Suite')
    QLocale.setDefault(QLocale(QLocale.Language.German))
    translator = QTranslator(app)
    if translator.load(QLocale(), 'qtbase', '_', QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
        app.installTranslator(translator)
    local = Path('config/devices.local.json')
    config = args.config or (local if local.is_file() else None)
    directory = args.log_dir or Path(QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppLocalDataLocation)) / 'logs'
    with ExitStack() as stack:
        errors = []
        log_path = None
        for candidate in (directory, Path(tempfile.gettempdir()) / 'LAPIS Control Suite' / 'logs'):
            try:
                log_path = stack.enter_context(session_log(candidate))
                break
            except OSError as exc:
                errors.append(f'{candidate} · {exc}')
        set_style(app)
        window = MainWindow(config, log_path=log_path)
        if errors:
            message = 'Logdatei konnte hier nicht angelegt werden · ' + ' / '.join(errors)
            if log_path:
                message += f' · Protokollierung unter {log_path}'
            window.notice(message, problem=True)
        window.show()
        return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
