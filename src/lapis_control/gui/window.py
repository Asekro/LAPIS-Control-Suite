"""Desktop workspace for the LAPIS laboratory instruments."""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from queue import Queue
from threading import Event
import time

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu,
                               QMessageBox, QScrollArea, QStackedWidget, QVBoxLayout, QWidget)

from lapis_control.configuration import Device, load_devices
from lapis_control.devices.bender.iseg_nhs import ChannelStatus
from lapis_control.devices.vacuum.edwards_tic import EdwardsTicError
from lapis_control.workflows.bender_voltage import hold_bender_voltage, regulation_targets
from lapis_control.workflows.laboratory import BenderControl, address, bender_status, capture, laser_status, pump_status
from lapis_control.workflows.laser_shot import acquire_shot, ShotTraceError
from lapis_control.workflows.measurement import Measurement, export_csv, load_measurement, save_measurement
from .jobs import Jobs, error_text
from .pages import BenderPage, DataPage, LaserPage, MODELS, NAMES, Overview, ScopePage, VacuumPage
from .profiles import Profiles
from .widgets import button, icon, label, row

log = logging.getLogger(__name__)

TITLES = {'overview': 'Laborübersicht', **NAMES, 'data': 'Messdaten'}
SUBTITLES = {'overview': 'Geräteverbindungen und Messbetrieb', **MODELS,
             'data': 'Kurven untersuchen und lokal speichern'}


class MainWindow(QMainWindow):
    def __init__(self, config: Path | None = None, *, log_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle('LAPIS · Control Suite')
        self.setWindowIcon(icon('brand'))
        self.resize(1200, 850)
        self.setMinimumSize(840, 580)
        self.devices: dict[str, Device] = {}
        self.connected: set[str] = set()
        self.disconnect_pending: set[str] = set()
        self.log_path = log_path
        self.issues: dict[str, str] = {}
        self.config_path: Path | None = None
        self.bender_control: BenderControl | None = None
        self.regulation_targets: dict[str, float] | None = None
        self.regulation_updates: Queue[dict[str, float]] | None = None
        self.updating_targets = False
        self.jobs = Jobs(self)
        self.pressures: deque[tuple[float, float]] = deque(maxlen=600)
        self.last_pressure: tuple[float, float] | None = None
        self.gauge_error = ''
        self.monitoring = False
        self.pump_read_requested = False
        self.last_pumps = 0.
        self.next_read: dict[str, float] = {}
        self.pump_report: dict = {}
        self.fire_request = Event()
        self.armed = False
        self.arm_deadline = 0.
        self.records: list[Measurement] = []
        self.unsaved: set[int] = set()
        self.closing = False
        self.shutdown_errors: list[str] = []
        self.selection = 'overview'
        self.overview, self.data = Overview(), DataPage()
        self.vacuum, self.bender, self.laser, self.scope = VacuumPage(), BenderPage(), LaserPage(), ScopePage()
        self.profiles = Profiles(self.bender, self.scope, self.vacuum)
        self.panels = {'vacuum': self.vacuum, 'bender': self.bender, 'laser': self.laser, 'oscilloscope': self.scope}
        self._build()
        self._bind()
        self.jobs.changed.connect(self.sync)
        self.jobs.finished.connect(self._disconnect_when_idle)
        self.jobs.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        if config is not None:
            self.load_setup(config)
        self.navigate('overview')
        self.sync()

    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        base = QHBoxLayout(root)
        base.setContentsMargins(0, 0, 0, 0)
        base.setSpacing(0)
        rail = QFrame()
        rail.setObjectName('rail')
        rail.setFixedWidth(205)
        sidebar = QVBoxLayout(rail)
        sidebar.setContentsMargins(14, 24, 14, 18)
        sidebar.setSpacing(5)
        logo = QLabel()
        logo.setPixmap(icon('brand', size=32).pixmap(32, 32))
        sidebar.addLayout(row(logo, label('LAPIS', 'brand')))
        sidebar.addWidget(label('Instrument Control Suite', 'railText'))
        self.nav = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for title, names in [('Arbeitsplatz', ('overview', 'data')), ('Instrumente', tuple(NAMES))]:
            sidebar.addSpacing(22)
            sidebar.addWidget(label(title, 'section'))
            for key in names:
                action = button(TITLES[key], 'nav')
                action.setCheckable(True)
                action.setIcon(icon(key))
                action.setIconSize(QSize(20, 20))
                action.clicked.connect(lambda checked=False, k=key: self.navigate(k))
                group.addButton(action)
                sidebar.addWidget(action)
                self.nav[key] = action
        sidebar.addStretch()
        sidebar.addWidget(label('Kammerdruck', 'section'))
        self.rail_pressure = label('— mbar', 'railValue')
        self.rail_age = label('Kein Messwert', 'railText', wrap=True)
        sidebar.addWidget(self.rail_pressure)
        sidebar.addWidget(self.rail_age)
        base.addWidget(rail)
        right = QWidget()
        right.setObjectName('canvas')
        body = QVBoxLayout(right)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        header = QHBoxLayout()
        header.setSpacing(9)
        header.setContentsMargins(26, 20, 26, 14)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.title_label, self.subtitle = label('', 'pageTitle'), label('', 'muted')
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle)
        header.addLayout(titles, 1)
        self.file_button = button('Aufbau wählen …')
        self.profile_button = button('Messprofil')
        menu = QMenu(self.profile_button)
        menu.addAction('Profil laden …', lambda: self.profile_dialog(False))
        menu.addAction('Profil speichern …', lambda: self.profile_dialog(True))
        self.profile_button.setMenu(menu)
        self.connect_all = button('Geräte verbinden', 'primary')
        header.addWidget(self.profile_button)
        header.addWidget(self.file_button)
        header.addWidget(self.connect_all)
        body.addLayout(header)
        self.shot_banner = QFrame()
        self.shot_banner.setStyleSheet('QFrame {background:#fff1d4;}')
        banner = QHBoxLayout(self.shot_banner)
        banner.setContentsMargins(24, 5, 24, 5)
        self.shot_message = label('', wrap=True)
        self.shot_stop = button('Laserablauf abbrechen', 'stop')
        self.shot_stop.clicked.connect(lambda: self.cancel_job('shot'))
        banner.addWidget(self.shot_message, 1)
        banner.addWidget(self.shot_stop)
        body.addWidget(self.shot_banner)
        self.stack = QStackedWidget()
        self.stack.setContentsMargins(22, 0, 15, 6)
        self.indices = {}
        for key, page in {'overview': self.overview, **self.panels, 'data': self.data}.items():
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.indices[key] = self.stack.addWidget(scroll)
        body.addWidget(self.stack, 1)
        self.footer = label('Aufbau-Datei wählen oder eine gespeicherte Messung öffnen.', 'message', wrap=True)
        self.footer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.footer)
        base.addWidget(right, 1)

    def _bind(self) -> None:
        self.file_button.clicked.connect(self.choose_setup)
        self.connect_all.clicked.connect(self.toggle_all)
        self.overview.open_log.setEnabled(self.log_path is not None)
        self.overview.open_log.setToolTip(str(self.log_path or 'Keine Logdatei verfügbar'))
        self.overview.open_log.clicked.connect(self.open_log)
        for key, panel in self.panels.items():
            panel.connection.clicked.connect(lambda checked=False, k=key: self.toggle_connection(k))
            panel.refresh.clicked.connect(lambda checked=False, k=key: self.refresh(k))
            self.overview.buttons[key].clicked.connect(lambda checked=False, k=key: self.navigate(k))
        self.vacuum.monitor.clicked.connect(self.toggle_monitor)
        self.vacuum.gauge.currentIndexChanged.connect(self.change_gauge)
        self.bender.apply.clicked.connect(lambda: self.manual_bender('set'))
        self.bender.enable.clicked.connect(lambda: self.manual_bender('enable'))
        self.bender.disable.clicked.connect(lambda: self.manual_bender('disable'))
        self.bender.regulate.clicked.connect(self.regulate)
        self.bender.stop.clicked.connect(lambda: self.manual_bender('disable'))
        self.laser.source.currentIndexChanged.connect(self.sync)
        self.laser.load.currentIndexChanged.connect(self.sync)
        self.laser.arm.clicked.connect(self.arm)
        self.laser.fire.clicked.connect(self.fire)
        self.laser.abort.clicked.connect(lambda: self.cancel_job('shot'))
        self.laser.off.clicked.connect(self.laser_off)
        self.laser.reset.clicked.connect(self.reset_interlock)
        self.scope.apply.clicked.connect(lambda: self.scope_action('apply'))
        self.scope.download.clicked.connect(lambda: self.scope_action('download'))
        self.scope.single.clicked.connect(lambda: self.scope_action('capture'))
        self.scope.wave_off.clicked.connect(lambda: self.scope_action('wave_off'))
        self.scope.cancel.clicked.connect(lambda: self.cancel_job('capture'))
        self.data.open.clicked.connect(self.open_measurement)
        self.data.save.clicked.connect(lambda: self.save_data(False))
        self.data.csv.clicked.connect(lambda: self.save_data(True))
        self.data.remove.clicked.connect(self.remove_record)
        self.data.record.currentIndexChanged.connect(self.select_record)

    def navigate(self, key: str) -> None:
        self.selection = key
        self.stack.setCurrentIndex(self.indices[key])
        self.nav[key].setChecked(True)
        self.title_label.setText(TITLES[key])
        self.subtitle.setText(SUBTITLES[key])
        if key in self.connected:
            self.next_read[key] = 0

    def notice(self, message: str, *, problem: bool = False, exc_info: bool = False) -> None:
        log.log(logging.ERROR if problem else logging.INFO, "%s", message, exc_info=exc_info)
        self.footer.setObjectName('problem' if problem else 'message')
        self.footer.setStyleSheet('')  # Refresh object-name based style selection.
        self.footer.style().unpolish(self.footer)
        self.footer.style().polish(self.footer)
        self.footer.setText(message)
        self.overview.events.insertItem(0, datetime.now().strftime('%H:%M:%S') + '   ' + message)
        if self.overview.events.count() > 100:
            self.overview.events.takeItem(100)

    def open_log(self) -> None:
        if self.log_path and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log_path))):
            self.notice(f'Logdatei konnte nicht geöffnet werden · {self.log_path}', problem=True)

    def choose_setup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Aufbau wählen', str(self.config_path or Path('config')),
                                               'Gerätekonfiguration (*.json)')
        if path:
            self.load_setup(Path(path))

    def profile_dialog(self, save: bool) -> None:
        dialog = QFileDialog.getSaveFileName if save else QFileDialog.getOpenFileName
        path, _ = dialog(self, 'Messprofil speichern' if save else 'Messprofil laden',
                         'LAPIS-Profil.json' if save else '', 'Messprofil (*.json)')
        if not path:
            return
        try:
            if save:
                self.profiles.save(Path(path))
            else:
                self.profiles.load(Path(path))
        except (ValueError, OSError, OverflowError) as exc:
            self.notice(f'Messprofil · {exc}', problem=True, exc_info=True)
            return
        self.notice(('Messprofil gespeichert · ' if save else 'Vorgaben geladen · ') + Path(path).name)

    def load_setup(self, path: Path) -> None:
        if self.connected or self.jobs.active:
            self.notice('Zum Wechseln des Aufbaus zuerst die verbundenen Geräte trennen.')
            return
        try:
            devices = load_devices(path)
        except (OSError, ValueError) as exc:
            self.notice(f'Aufbau konnte nicht geladen werden · {exc}', problem=True, exc_info=True)
            return
        self.devices, self.config_path = devices, path
        log.info("Loaded device configuration %s", path.resolve())
        self.issues.clear()
        self.bender_control = None
        if 'bender' in devices:
            b = devices['bender']
            self.bender_control = BenderControl(b)
            for key, edit in self.bender.editors.items():
                cap = b.channels[key][0].max_voltage
                edit.setRange(0 if key[0]=='p' else -cap, cap if key[0]=='p' else 0)
        self.file_button.setText(path.name)
        self.file_button.setToolTip(str(path.resolve()))
        for key, page in self.panels.items():
            page.location.setText(address(devices[key]) if key in devices else 'Im Aufbau nicht konfiguriert')
        self.notice(f'Aufbau geladen · {len(devices)} Geräte konfiguriert.')
        self.sync()

    def toggle_all(self) -> None:
        if self.closing or self.disconnect_pending:
            return
        keys = self.devices.keys() - self.connected
        if keys:
            for key in keys:
                self.toggle_connection(key)
        elif not self.jobs.busy(*self.connected, 'bender-shot', include_reads=False):
            self._request_disconnect(self.connected)

    def toggle_connection(self, key: str) -> None:
        if (key not in self.devices or key in self.disconnect_pending
                or self.jobs.busy(key, include_reads=False)
                or key == 'bender' and self.jobs.busy('bender-shot')):
            return
        if key in self.connected:
            self._request_disconnect({key})
        elif not self.closing:
            dev = self.devices[key]
            self.jobs.start('connect', (key,), lambda _: dev.connect(), lambda _: self.on_connected(key),
                            lambda e: self.failed((key,), e, 'Verbindung'))

    def _request_disconnect(self, keys: set[str]) -> None:
        log.info("Disconnect requested for %s", ', '.join(sorted(keys)))
        self.disconnect_pending.update(keys)
        self._disconnect_when_idle()
        self.sync()

    def _disconnect_when_idle(self) -> None:
        for key in tuple(self.disconnect_pending):
            if self.jobs.busy(key) or key == 'bender' and self.jobs.busy('bender-shot'):
                continue
            close = self.bender_control.close if key == 'bender' else self.devices[key].close
            self.jobs.start('disconnect', (key,), lambda _, fn=close: fn(),
                            lambda _, k=key: self.disconnected(k), lambda e, k=key: self.disconnected(k, e))

    def on_connected(self, key: str) -> None:
        self.connected.add(key)
        self.issues.pop(key, None)
        self.notice(f'{NAMES[key]} verbunden · {address(self.devices[key])}')
        if not self.closing:
            self.refresh(key, periodic=True)

    def disconnected(self, key: str, error: Exception | None = None) -> None:
        self.disconnect_pending.discard(key)
        self.connected.discard(key)
        self.overview.summaries[key].setText(MODELS[key])
        self.panels[key].detail.clear()
        if key == 'vacuum':
            self.monitoring = False
            self.change_gauge()
            self.pump_report = {}
            for widget in self.vacuum.pump_labels.values():
                widget.setText('Offline')
        elif key == 'laser':
            for widget in (self.laser.count, self.laser.emission, self.laser.interlock):
                widget.setText('—')
            self.laser.phase.setText('Laser getrennt')
        elif key == 'bender':
            for values in self.bender.readbacks.values():
                for widget in values:
                    widget.setText('—')
        if error:
            message = f'{NAMES[key]} · Trennen / Abschaltung ungeklärt · {error_text(error)}'
            self.issues[key] = message
            self.notice(message, problem=True)
            if self.closing:
                self.shutdown_errors.append(message)
        else:
            self.issues.pop(key, None)
            self.notice(f'{NAMES[key]} getrennt.')

    def failed(self, keys: tuple[str, ...], exc: Exception, operation: str) -> None:
        if 'bender' in keys and self.jobs.busy('bender-shot'):
            self.jobs.cancel('shot')
        interrupted = isinstance(exc, InterruptedError) and exc.__context__ is None
        message = f'{operation} · ' + ('Abgebrochen.' if interrupted else error_text(exc))
        if not interrupted:
            for key in keys:
                self.issues[key] = message
                if key in self.overview.summaries:
                    self.overview.summaries[key].setText('Vorgang prüfen · siehe Sitzungsprotokoll')
            if self.closing:
                self.shutdown_errors.append(message)
        self.notice(message, problem=not interrupted)

    def run_action(self, name: str, keys: tuple[str, ...], work: Callable, done: Callable,
                   title: str) -> bool:
        if self.closing or not self.connected.issuperset(keys) or self.disconnect_pending.intersection(keys):
            return False
        started = self.jobs.start(name, keys, work, done, lambda e: self.failed(keys, e, title),
                                  after_read=keys == ('bender',))
        if started:
            self.notice(title + ' …')
        return started

    def refresh(self, key: str, *, periodic: bool = False) -> None:
        if (self.closing or key not in self.connected or key in self.disconnect_pending
                or key == 'bender' and self.jobs.busy('bender-shot')):
            return
        if self.jobs.busy(key):
            if key == 'vacuum' and not periodic and self.jobs.running('vacuum-read'):
                self.pump_read_requested = True
            return
        if key == 'vacuum':
            self.poll_vacuum(include_pumps=not periodic or
                             self.monitoring and time.monotonic()-self.last_pumps >= self.vacuum.interval.value())
            return
        dev = self.devices[key]
        read = {'laser': laser_status, 'bender': bender_status, 'oscilloscope': lambda s: s.status()}[key]
        self.next_read[key] = time.monotonic() + (5 if key == 'bender' else 8)
        self.jobs.start('read', (key,), lambda _: read(dev), lambda report: self.device_report(key, report),
                        lambda e: self.read_error(key, e))

    def read_error(self, key: str, error: Exception) -> None:
        message = f'{NAMES[key]} · Status unbekannt · {error_text(error)}'
        if self.issues.get(key) != message:
            self.notice(message, problem=True)
        self.issues[key] = message
        self.overview.summaries[key].setText('Statusabfrage fehlgeschlagen')
        self.panels[key].detail.display({'Statusabfrage fehlgeschlagen': str(error)})
        if key == 'laser':
            for widget in (self.laser.interlock, self.laser.emission, self.laser.count):
                widget.setText('Unbekannt')
        if key == 'bender':
            for values in self.bender.readbacks.values():
                for widget in values:
                    widget.setText('—')

    def device_report(self, key: str, values: dict) -> None:
        self.issues.pop(key, None)
        stamp = datetime.now().strftime('%H:%M:%S')
        self.panels[key].detail.display({'Gelesen um': stamp, **values})
        if key == 'laser':
            p = self.laser
            p.interlock.setText('Offen' if values['interlock_open'] else 'Geschlossen')
            p.emission.setText('EIN' if values['enabled'] else 'AUS')
            p.count.setText(str(values['count']))
            self.overview.summaries[key].setText(f'Interlock {p.interlock.text().lower()} · Laser {p.emission.text()} · {values["count"]} Pulse')
        elif key == 'bender':
            active = sum(bool(r.status & ChannelStatus.ON) for r in values['channels'].values())
            self.overview.summaries[key].setText(f'{active} von 4 Elektroden eingeschaltet · Stand {stamp}')
            for name, r in values['channels'].items():
                state = 'Rampe' if r.status & ChannelStatus.RAMPING else ('Ein' if r.status & ChannelStatus.ON else 'Aus')
                for widget, value in zip(self.bender.readbacks[name], [f'{r.voltage_set:.2f}', f'{r.voltage:.2f}',
                                                                       f'{r.current*1e6:.3g}', state]):
                    widget.setText(value)
        elif key == 'oscilloscope':
            wavegen = 'ein' if values['wavegen']['output'] else 'aus'
            self.overview.summaries[key].setText(f'{values["sample_rate_hz"]/1e6:g} MSa/s · WaveGen {wavegen} · Stand {stamp}')

    def change_gauge(self) -> None:
        self.pressures.clear()
        self.last_pressure = None
        self.gauge_error = ''
        self.next_read['vacuum'] = 0
        self.draw_pressure()
        self.show_pressure()

    def toggle_monitor(self) -> None:
        self.monitoring = not self.monitoring
        self.last_pumps = 0
        self.next_read['vacuum'] = 0
        self.notice('Pumpenmonitoring ' + ('gestartet.' if self.monitoring else 'gestoppt.'))
        self.sync()

    def poll_vacuum(self, *, include_pumps: bool) -> None:
        dev = self.devices['vacuum']
        gauge = self.vacuum.gauge.currentIndex()+1
        self.next_read['vacuum'] = time.monotonic()+1
        self.pump_read_requested = include_pumps

        def read(_: Event) -> tuple[int, float, float, str]:
            try:
                pa, error = dev.read_pressure_pa(gauge), ''
            except EdwardsTicError as exc:
                log.warning("Pressure query on %s, gauge %d failed", dev.port, gauge, exc_info=True)
                pa, error = np.nan, str(exc)
            return gauge, time.monotonic(), pa, error

        def pressure_received(result: tuple[int, float, float, str]) -> None:
            self._progress('pressure', result)
            if self.pump_read_requested and not self.closing and 'vacuum' not in self.disconnect_pending:
                self.last_pumps = time.monotonic()
                self.jobs.start('vacuum-pumps', ('vacuum',), lambda _: pump_status(dev), self.pumps_received,
                                lambda e: self.read_error('vacuum', e))

        self.jobs.start('vacuum-read', ('vacuum',), read, pressure_received,
                        lambda e: self.read_error('vacuum', e))

    def pumps_received(self, report: dict) -> None:
        self.pump_report = report
        stamp = datetime.now().strftime('%H:%M:%S')
        for prefix in ('turbo', 'backing'):
            drive, state = report[prefix+'_drive'], report[prefix+'_state']
            speed = (f'{drive.get("speed_rpm", "—")} U/min' if prefix == 'turbo'
                     else f'{drive.get("frequency_hz", "—")} Hz')
            power = report[prefix+'_power'].get('power_w', '—')
            fault = bool(drive.get('active_faults') or drive.get('faults') or drive.get('warnings')
                         or state.get('alert_id') or state.get('has_fault'))
            partial = any('error' in v for k, v in report.items() if k.startswith(prefix))
            msg = f'{state.get("state", "Unbekannt")}  ·  {speed}  ·  {power} W'
            if fault:
                msg += '  ·  Alarm / Warnung'
            if partial:
                msg += '  ·  Diagnose unvollständig'
            self.vacuum.pump_labels[prefix].setText(msg + f'\nLetzte Abfrage {stamp}')
        self.overview.summaries['vacuum'].setText('Pumpenmonitoring aktiv' if self.monitoring else f'Pumpendiagnose · Stand {stamp}')
        self.vacuum.detail.display({'Gelesen um': stamp, **report})

    def draw_pressure(self) -> None:
        values = np.asarray(self.pressures, dtype=float)
        for card in (self.overview.pressure, self.vacuum.pressure):
            if values.size:
                card.plot.set_data(values[:,0]-values[-1,0], values[:,1])
            else:
                card.plot.set_data(np.array([]), np.array([]))

    def show_pressure(self) -> None:
        value, age_text = '— mbar', 'Kein Messwert'
        if self.last_pressure:
            stamp, pa = self.last_pressure
            age = int(time.monotonic()-stamp)
            if age < 4 and not self.gauge_error and 'vacuum' in self.connected:
                value = f'{pa/100:.3e} mbar'
                age_text = f'Röhre {self.vacuum.gauge.currentIndex()+1} · vor {age} s'
            else:
                age_text = f'Letzter gültiger Wert vor {age} s'
        if self.gauge_error:
            age_text = 'Messung gestört · ' + age_text
        self.rail_pressure.setText(value)
        self.rail_age.setText(age_text)
        for card in (self.overview.pressure, self.vacuum.pressure):
            card.value.setText(value)
            card.age.setText(age_text)
            card.age.setToolTip(self.gauge_error)

    def pressure_context(self) -> dict | None:
        if not self.last_pressure or self.gauge_error or 'vacuum' not in self.connected:
            return None
        stamp, pa = self.last_pressure
        age = time.monotonic() - stamp
        if age >= 4:
            return None
        read_utc = datetime.now(timezone.utc) - timedelta(seconds=age)
        return dict(gauge=self.vacuum.gauge.currentIndex()+1, pressure_pa=pa,
                    read_utc=read_utc.isoformat(), age_at_request_s=age)

    def manual_bender(self, action: str) -> None:
        controller = self.bender_control
        targets = self.bender.targets()
        if action != 'disable' and self.jobs.busy('bender-shot'):
            return
        if action == 'set' and self.jobs.running('regulation'):
            if not self.can_update_targets():
                return
            try:
                targets = regulation_targets(controller.bender, targets)
            except ValueError as exc:
                self.notice(f'Ungültige Regelziele · {exc}', problem=True)
                return
            self.updating_targets = True
            self.regulation_updates.put(targets)
            self.notice('Neue Regelziele angefordert …')
            self.sync()
            return
        if action == 'disable':
            self.jobs.discard_waiting_action('bender')
            if self.jobs.busy('bender-shot'):
                self.jobs.cancel('shot')
            if self.jobs.running('regulation'):
                self.cancel_job('regulation')
                return
        work = {'set': lambda _: controller.set_targets(targets), 'enable': lambda ev: controller.enable(targets, ev),
                'disable': lambda _: controller.disable()}[action]
        title = {'set': 'Sollwerte setzen', 'enable': 'HV einschalten', 'disable': 'HV abfahren'}[action]
        self.run_action(action, ('bender',), work, lambda _: self.action_done('bender', title), title)

    def action_done(self, key: str, title: str) -> None:
        self.notice(title + ' · bestätigt.')
        self.refresh(key)

    def regulate(self) -> None:
        if self.jobs.busy('bender-shot'):
            return
        targets = self.bender.targets()
        options = {k: w.value() for k, w in self.bender.options.items()}
        dev = self.devices['bender']
        updates: Queue[dict[str, float]] = Queue()

        def regulate(cancel: Event) -> bool:
            return hold_bender_voltage(dev, targets, duration_s=None, stop_event=cancel, allow_active=True,
                                       updates=updates, on_targets=lambda v: self.jobs.progress.emit('targets', (updates, v)),
                                       on_control=lambda owned: setattr(self.bender_control, 'managed', owned),
                                       on_sample=lambda *s: self.jobs.progress.emit('bender', s), **options)

        def done(_: bool) -> None:
            self.action_done('bender', 'Regelung beendet und HV abgefahren')

        if self.run_action('regulation', ('bender',), regulate, done, 'Vierkanalregelung'):
            self.regulation_targets = None
            self.regulation_updates = updates
            self.updating_targets = False
            self.overview.summaries['bender'].setText('Vierkanalregelung angefordert')
            self.sync()

    def can_update_targets(self) -> bool:
        return (not self.closing and self.regulation_targets is not None and not self.updating_targets
                and any(j.name == 'regulation' and not j.cancel.is_set() for j in self.jobs.active.values()))

    def cancel_job(self, name: str) -> None:
        self.jobs.cancel(name)
        if name == 'regulation' and self.jobs.busy('bender-shot'):
            self.jobs.cancel('shot')
        if name == 'shot':
            self.armed = False
            self.laser.phase.setText('Abbruch angefordert · Ausgänge werden geprüft')
        self.notice('Abbruch angefordert …')
        self.sync()

    def laser_off(self) -> None:
        if self.jobs.running('shot'):
            self.cancel_job('shot')
            return
        laser = self.devices['laser']
        self.run_action('laser-off', ('laser',), lambda _: laser.turn_off(),
                        lambda _: self.action_done('laser', 'Laser AUS'), 'Laser ausschalten')

    def reset_interlock(self) -> None:
        laser = self.devices['laser']
        self.run_action('interlock', ('laser',), lambda _: laser.reset_interlock_fault(),
                        lambda _: self.action_done('laser', 'Interlockquittierung'), 'Interlock quittieren')

    def arm(self) -> None:
        timed = self.laser.source.currentIndex() == 0
        load = (None, 'HIGHZ', '50OHM')[self.laser.load.currentIndex()] if timed else None
        if timed and (load is None or 'oscilloscope' not in self.connected):
            return
        scope = self.devices['oscilloscope'] if 'oscilloscope' in self.connected else None
        keys = ('laser', 'oscilloscope', 'bender-shot')
        if (self.closing or self.disconnect_pending.intersection(('laser', 'oscilloscope', 'bender'))
                or self.jobs.busy(*keys)):
            return
        control = self.bender_control if 'bender' in self.connected else None
        regulating = self.jobs.running('regulation')
        targets = self.regulation_targets if regulating else None
        tolerance = self.bender.options['tolerance_v'].value()
        if control:
            if self.jobs.busy('bender') and not self.can_update_targets():
                return
        laser = self.devices['laser']
        setup = self.scope.setup()
        pressure = self.pressure_context()
        self.armed = False
        self.fire_request = fire = Event()
        self.laser.phase.setText('Interlock, Trigger und Aufnahmebereitschaft werden geprüft …')

        def work(cancel: Event) -> tuple:
            return acquire_shot(laser, scope, setup, load=load, fire=fire, cancel=cancel,
                                bender_check=(lambda: control.shot_snapshot(targets, tolerance, cancel)) if control else None,
                                progress=lambda stage: self.jobs.progress.emit('shot', (stage, time.monotonic()+15)))

        def done(result: tuple) -> None:
            self.armed = False
            count, record = result
            self.laser.count.setText(str(count))
            self.laser.emission.setText('AUS')
            self.laser.phase.setText('Ein Puls bestätigt · Laser aus')
            self.overview.summaries['laser'].setText(f'Laser AUS · Kopfzähler {count}')
            if record:
                if pressure:
                    record.metadata['pressure_at_arm_request'] = pressure
                self.add_record(record)
            self.notice(f'Ein Laserpuls bestätigt · Kopfzähler {count}' + (' · Kurve übertragen.' if record else '.'))
            self.next_read['laser'] = 0

        def failure(error: Exception) -> None:
            self.armed = False
            if isinstance(error, ShotTraceError):
                self.laser.phase.setText('Ein Puls bestätigt · Scope-Kurve fehlt')
                self.laser.count.setText(str(error.count))
                self.laser.emission.setText('AUS')
            else:
                self.laser.phase.setText('Versuch beendet · ' + error_text(error))
                self.laser.emission.setText('Prüfen')
            self.failed(tuple(k for k in keys if k in NAMES), error, 'Laser / Messung')
            self.next_read['laser'] = 0

        self.jobs.start('shot', keys, work, done, failure)

    def fire(self) -> None:
        if self.armed and self.jobs.running('shot') and time.monotonic() < self.arm_deadline:
            self.fire_request.set()
            self.armed = False
            self.laser.phase.setText('Trigger angefordert · Pulsbestätigung ausstehend')
            self.sync()

    def scope_action(self, action: str) -> None:
        scope = self.devices['oscilloscope']
        setup = self.scope.setup()
        if action == 'apply':
            self.run_action(action, ('oscilloscope',), lambda _: setup.apply(scope),
                            lambda _: self.action_done('oscilloscope', 'Scope konfiguriert'), 'Scope konfigurieren')
        elif action == 'wave_off':
            self.run_action(action, ('oscilloscope',), lambda _: scope.wavegen_output(False),
                            lambda _: self.action_done('oscilloscope', 'WaveGen AUS'), 'WaveGen ausschalten')
        else:
            pressure = self.pressure_context() if action == 'capture' else None
            work = (lambda cancel: capture(scope, setup, cancel)) if action == 'capture' else (
                lambda _: Measurement.download(scope, setup, 'memory', stop=True))
            title = 'Neue Single-Aufnahme' if action == 'capture' else 'Kurve aus Gerätespeicher'

            def received(record: Measurement) -> None:
                if pressure:
                    record.metadata['pressure_at_capture_request'] = pressure
                self.add_record(record)

            self.run_action(action, ('oscilloscope',), work, received, title)

    def add_record(self, record: Measurement, filename: str | None = None) -> None:
        stamp = datetime.fromisoformat(record.metadata['transferred_utc']).astimezone().strftime('%d.%m. %H:%M:%S')
        source = {'single': 'Single', 'memory': 'Scopespeicher', 'laser': 'Lasermessung'}.get(
            record.metadata.get('source'), 'Messung')
        self.records.append(record)
        if filename is None:
            self.unsaved.add(id(record))
        self.data.record.addItem(filename or f'{stamp} · {source}')
        self.data.record.setCurrentIndex(len(self.records)-1)
        self.navigate('data')
        self.notice('Messung geöffnet.' if filename else 'Messkurve übertragen · zur lokalen Sicherung NPZ speichern.')

    def select_record(self, index: int) -> None:
        if 0 <= index < len(self.records):
            self.data.display(self.records[index])

    def remove_record(self) -> None:
        index = self.data.record.currentIndex()
        if index < 0:
            return
        record = self.records[index]
        if id(record) in self.unsaved:
            choice = QMessageBox.question(self, 'Messung schließen', 'Messung ohne NPZ-Sicherung schließen?',
                                          QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                                          QMessageBox.StandardButton.Cancel)
            if choice != QMessageBox.StandardButton.Discard:
                return
        self.unsaved.discard(id(record))
        self.records.pop(index)
        self.data.record.removeItem(index)
        if self.records:
            self.select_record(self.data.record.currentIndex())
        else:
            self.data.clear_measurement()
        self.sync()

    def open_measurement(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, 'Messdatei öffnen', '', 'LAPIS-Messung (*.npz)')
        if path:
            self.jobs.start('open', ('files',), lambda _: load_measurement(Path(path)),
                            lambda record: self.add_record(record, Path(path).name),
                            lambda e: self.failed((), e, 'Messdatei öffnen'))

    def save_data(self, csv_format: bool) -> None:
        record = self.data.measurement
        if record is None:
            return
        ext = 'csv' if csv_format else 'npz'
        suggested = 'LAPIS_' + datetime.fromisoformat(record.metadata['transferred_utc']).strftime('%Y%m%d_%H%M%S')
        path, _ = QFileDialog.getSaveFileName(self, 'Messung speichern', suggested+'.'+ext, f'{ext.upper()} (*.{ext})')
        if not path:
            return
        target = Path(path)
        export = export_csv if csv_format else save_measurement

        def done(_: object) -> None:
            if not csv_format:
                self.unsaved.discard(id(record))
            self.notice(f'Messung gespeichert · {target}')

        self.jobs.start('save', ('files',), lambda _: export(target, record), done,
                        lambda e: self.failed((), e, 'Speichern'))

    def _progress(self, kind: str, value: object) -> None:
        if kind == 'pressure':
            gauge, stamp, pa, error = value
            if gauge != self.vacuum.gauge.currentIndex()+1 or 'vacuum' not in self.connected:
                return
            if self.pressures and stamp - self.pressures[-1][0] >= 4:
                self.pressures.append((self.pressures[-1][0]+1, np.nan))
            self.pressures.append((stamp, pa/100))
            if error and error != self.gauge_error:
                self.notice(f'Druckmessung · {error}', problem=True)
            self.gauge_error = error
            if not error:
                self.last_pressure = stamp, pa
                self.issues.pop('vacuum', None)
            else:
                self.issues['vacuum'] = error
            self.draw_pressure()
            self.show_pressure()
        elif kind == 'targets':
            session, targets = value
            if session is self.regulation_updates and self.jobs.running('regulation'):
                previous = self.regulation_targets or {}
                for key, target in targets.items():
                    if previous.get(key) != target:
                        self.bender.readbacks[key][3].setText('Einregeln')
                self.regulation_targets = targets
                self.updating_targets = False
                self.notice('Regelziele bestätigt · ' + '   '.join(f'{k.upper()} {v:g} V' for k, v in targets.items()))
                self.sync()
        elif kind == 'bender':
            key, sample, stable = value
            fields = self.bender.readbacks[key]
            fields[0].setText(f'{sample.voltage_set:.2f}')
            fields[1].setText(f'{sample.voltage:.2f}')
            fields[2].setText('—' if sample.current is None else f'{sample.current*1e6:.3g}')
            fields[3].setText('Halten' if stable else 'Einregeln')
            fields[3].setToolTip(datetime.fromtimestamp(sample.timestamp).strftime('%H:%M:%S'))
            self.overview.summaries['bender'].setText('Regelung aktiv · ' + '   '.join(
                f'{k.upper()} {v[1].text()} V' for k, v in self.bender.readbacks.items()))
        elif kind == 'shot' and self.jobs.running('shot'):
            stage, deadline = value
            if stage == 'armed':
                self.armed = not self.closing and not any(j.cancel.is_set() for j in self.jobs.active.values() if j.name=='shot')
                self.arm_deadline = deadline
                self.laser.emission.setText('SCHARF')
                self.laser.interlock.setText('Geschlossen')
                if self.armed:
                    self.laser.phase.setText('Laser scharf · Triggerfenster offen')
                self.overview.summaries['laser'].setText('Laser scharf · Triggerfenster offen')
            else:
                self.armed = False
                self.laser.emission.setText('AUS')
                self.laser.phase.setText('Ein Puls bestätigt · Scope-Aufnahme wird geprüft')
            self.sync()

    def sync(self) -> None:
        online = self.connected - self.disconnect_pending

        def available(key: str) -> bool:
            return (key in online and not self.closing
                    and not self.jobs.busy(key, include_reads=key not in ('vacuum', 'bender'))
                    and not (key == 'bender' and self.jobs.busy('bender-shot')))
        self.file_button.setEnabled(not self.connected and not self.jobs.active and not self.closing)
        missing = self.devices.keys() - self.connected
        if self.disconnect_pending:
            self.connect_all.setText('Trennen läuft …')
            group_available = False
        elif missing or not self.devices:
            self.connect_all.setText('Geräte verbinden')
            group_available = any(not self.jobs.busy(k) and not (k == 'bender' and self.jobs.busy('bender-shot'))
                                  for k in missing)
        else:
            self.connect_all.setText('Geräte trennen')
            group_available = not self.jobs.busy(*self.connected, 'bender-shot', include_reads=False)
        self.connect_all.setEnabled(group_available and not self.closing)
        for key, p in self.panels.items():
            connected = key in self.connected
            pending = key in self.disconnect_pending
            p.connection.setText('Trennen läuft …' if pending else ('Trennen' if connected else 'Verbinden'))
            p.connection.setEnabled(key in self.devices and not pending and not self.closing
                                    and not self.jobs.busy(key, include_reads=False)
                                    and not (key == 'bender' and self.jobs.busy('bender-shot')))
            p.refresh.setEnabled(available(key))
            state = self.overview.states[key]
            status = 'Prüfen' if key in self.issues else ('Verbunden' if connected else 'Offline')
            if state.property('status') != status:
                fg, bg = {'Prüfen': ('#94451c', '#fff0df'), 'Verbunden': ('#166354','#e2f5ed'),
                          'Offline': ('#56637a', '#edf1f6')}[status]
                state.setText(status)
                state.setProperty('status', status)
                state.setStyleSheet(f'color:{fg}; background:{bg}; border-radius:7px; padding:2px 8px;')
            state.setToolTip(self.issues.get(key, ''))
        self.file_button.setToolTip('Zum Wechseln des Aufbaus zuerst Geräte trennen.' if self.connected else
                                    str(self.config_path or 'Gerätekonfiguration öffnen'))
        self.vacuum.monitor.setEnabled('vacuum' in online and not self.closing)
        self.vacuum.monitor.setText('Pumpenmonitoring stoppen' if self.monitoring else 'Pumpenmonitoring starten')
        for btn in (self.bender.enable, self.bender.regulate):
            btn.setEnabled(available('bender'))
        regulating = self.jobs.running('regulation')
        self.bender.apply.setText('Regelziele übernehmen' if regulating else 'Sollwerte setzen')
        self.bender.apply.setEnabled(available('bender') or (self.can_update_targets() and not self.jobs.busy('bender-shot')))
        self.bender.disable.setEnabled('bender' in online and not self.closing
                                       and (regulating or not self.jobs.busy('bender', include_reads=False)
                                            or self.jobs.waiting_action('bender') is not None))
        self.bender.stop.setEnabled(regulating and not self.closing)
        for editor in self.bender.editors.values():
            editor.setEnabled(not self.closing)
        for editor in self.bender.options.values():
            editor.setEnabled(not regulating and not self.closing)
        shot = self.jobs.running('shot')
        self.shot_banner.setVisible(shot)
        self.shot_message.setText(self.laser.phase.text())
        self.shot_stop.setEnabled(not self.closing)
        self.profile_button.setEnabled(not (self.closing or shot or regulating))
        timed = self.laser.source.currentIndex() == 0
        self.laser.hint.setText(
            'Interlock im Labor schließen und Anschlusslast prüfen. WaveGen läuft mit 0,1 Hz. Der PC fordert AUS beim ersten Scope-Trigger oder nach maximal 8 s an. Der Kopfzähler prüft, wie viele Pulse tatsächlich registriert wurden.'
            if timed else 'Interlock im Labor schließen. Nach dem Scharfstellen einen externen Einzelpuls zuführen. Ein verbundenes Scope zeichnet mit den gewählten Kanaleinstellungen auf.')
        self.laser.source.setEnabled(not shot and not self.closing)
        self.laser.load.setEnabled(timed and not shot and not self.closing)
        scope_ready = not self.jobs.busy('oscilloscope') and ('oscilloscope' in self.connected or not timed)
        bender_ready = not self.jobs.busy('bender') or self.can_update_targets()
        self.laser.arm.setEnabled(available('laser') and scope_ready and bender_ready
                                  and self.disconnect_pending.isdisjoint(('oscilloscope', 'bender'))
                                  and (not timed or self.laser.load.currentIndex()>0))
        self.laser.fire.setEnabled(timed and self.armed and shot and not self.closing and time.monotonic()<self.arm_deadline)
        self.laser.abort.setEnabled(shot and not self.closing)
        self.laser.off.setEnabled((available('laser') or shot) and not self.closing)
        self.laser.reset.setEnabled(available('laser'))
        for btn in (self.scope.apply, self.scope.single, self.scope.download, self.scope.wave_off):
            btn.setEnabled(available('oscilloscope'))
        self.scope.cancel.setEnabled(self.jobs.running('capture') and not self.closing)
        self.data.open.setEnabled(not self.jobs.busy('files') and not self.closing and not shot)
        for btn in (self.data.save, self.data.csv, self.data.remove):
            btn.setEnabled(bool(self.records) and self.data.open.isEnabled())

    def tick(self) -> None:
        if self.closing:
            for key in tuple(self.connected):
                if not self.jobs.busy(key):
                    self.toggle_connection(key)
            if not self.connected and not self.jobs.active:
                self.close()
            return
        now = time.monotonic()
        self.show_pressure()
        if self.armed:
            remaining = max(0, int(np.ceil(self.arm_deadline-now)))
            self.laser.phase.setText(f'Scharf · noch {remaining} s')
            self.shot_message.setText(f'Laser scharf · Triggerfenster noch {remaining} s')
            self.laser.fire.setEnabled(self.laser.source.currentIndex() == 0 and remaining > 0)
        for key in self.connected.copy():
            if now >= self.next_read.get(key, 0) and (key == 'vacuum' or key == self.selection):
                self.refresh(key, periodic=True)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.jobs.active or self.connected:
            event.ignore()
            if not self.closing:
                self.closing = True
                self.armed = False
                for job in self.jobs.active.values():
                    job.cancel.set()
                self.notice('Sitzung wird beendet · Abschaltung und laufende Übertragungen abwarten …')
                self.sync()
            return
        self.timer.stop()
        if self.shutdown_errors:
            QMessageBox.warning(self, 'Gerätezustand prüfen', '\n'.join(self.shutdown_errors))
            self.shutdown_errors.clear()
        if self.unsaved:
            answer = QMessageBox.question(self, 'Messdaten im Arbeitsspeicher',
                       f'{len(self.unsaved)} Messung(en) sind noch nicht als NPZ gespeichert. Ohne Speichern beenden?',
                       QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                       QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                self.closing = False
                self.navigate('data')
                self.timer.start()
                self.sync()
                event.ignore()
                return
        self.jobs.pool.shutdown(wait=False)
        event.accept()
