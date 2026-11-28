"""File diagnostics for a running desktop session."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
from types import TracebackType

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QMessageLogContext, QtMsgType, qInstallMessageHandler

log = logging.getLogger(__name__)


@contextmanager
def session_log(directory: Path) -> Iterator[Path]:
    """Append session diagnostics, including Qt and uncaught Python exceptions."""
    directory.mkdir(parents=True, exist_ok=True)
    path = (directory / 'lapis-control.log').resolve()
    handler = RotatingFileHandler(path, maxBytes=4 * 1024 * 1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter(
        '%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'))
    root = logging.getLogger()
    app_log = logging.getLogger('lapis_control')
    tic_log = logging.getLogger('lapis_control.devices.vacuum.edwards_tic')
    app_level, tic_level = app_log.level, tic_log.level
    root.addHandler(handler)
    app_log.setLevel(logging.INFO)
    tic_log.setLevel(logging.DEBUG)
    previous_exception, previous_thread = sys.excepthook, threading.excepthook
    previous_qt = None

    def exception(kind: type[BaseException], error: BaseException, trace: TracebackType | None) -> None:
        log.error('Unhandled application exception', exc_info=(kind, error, trace))
        previous_exception(kind, error, trace)

    def thread_exception(args: threading.ExceptHookArgs) -> None:
        log.error('Unhandled thread exception', exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        previous_thread(args)

    def qt_message(kind: QtMsgType, context: QMessageLogContext, message: str) -> None:
        level = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
                 QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
                 QtMsgType.QtFatalMsg: logging.CRITICAL}[kind]
        log.log(level, 'Qt %s | %s', context.category, message)
        if previous_qt is not None:
            previous_qt(kind, context, message)
        elif level >= logging.WARNING and sys.stderr is not None:
            sys.stderr.write(message + '\n')

    previous_qt = qInstallMessageHandler(qt_message)
    sys.excepthook, threading.excepthook = exception, thread_exception
    try:
        log.info('Session started | PID %d | Python %s | PySide6 %s | %s',
                 os.getpid(), sys.version.split()[0], pyside_version, path)
        yield path
    except Exception:
        log.exception('Desktop session failed')
        raise
    finally:
        log.info('Session ended')
        qInstallMessageHandler(previous_qt)
        sys.excepthook, threading.excepthook = previous_exception, previous_thread
        app_log.setLevel(app_level)
        tic_log.setLevel(tic_level)
        root.removeHandler(handler)
        handler.close()
