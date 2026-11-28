"""Instrument panels and data inspection views."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QGridLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QVBoxLayout, QWidget)

from lapis_control.workflows.laser_shot import DEFAULT_ARM_TIMEOUT_S
from lapis_control.workflows.measurement import ChannelSetup, Measurement, ScopeSetup, waveform_statistics
from .plot import TracePlot
from .widgets import Card, Details, button, combo, fields, icon, label, number, row


NAMES = {'vacuum': 'Vakuum', 'bender': 'Bender', 'laser': 'Laser', 'oscilloscope': 'Oszilloskop'}
MODELS = {'vacuum': 'Edwards TIC · nEXT 300 / nXDS', 'bender': 'iseg NHS · 4 Elektroden',
          'laser': 'Coherent Flare NX', 'oscilloscope': 'Keysight DSOX1204G'}
ELECTRODES = ('p0', 'p1', 'n0', 'n1')


class Page(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('canvas')
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(2, 2, 8, 8)
        self.box.setSpacing(14)
        self.box.setAlignment(Qt.AlignmentFlag.AlignTop)


class PressureCard(Card):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName('pressure')
        self.value = label('— mbar', 'pressureValue')
        self.age = label('Kein Messwert', 'light')
        self.plot = TracePlot(pressure=True)
        self.box.addWidget(label('Kammerdruck', 'light'))
        line = row(self.value)
        line.addWidget(self.age)
        self.box.addLayout(line)
        self.box.addWidget(self.plot)


class Overview(Page):
    def __init__(self) -> None:
        super().__init__()
        self.pressure = PressureCard()
        self.box.addWidget(self.pressure)
        grid = QGridLayout()
        grid.setSpacing(12)
        self.buttons, self.states, self.summaries = {}, {}, {}
        for i, (key, name) in enumerate(NAMES.items()):
            card = Card()
            mark = label('')
            mark.setPixmap(icon(key, '#386bb0', 23).pixmap(23, 23))
            head = row(mark, label(name, 'title'))
            state = label('Offline', 'muted')
            head.addWidget(state)
            card.box.addLayout(head)
            summary = label(MODELS[key], 'muted', wrap=True)
            card.box.addWidget(summary)
            self.summaries[key] = summary
            action = button('Gerät öffnen  →')
            card.box.addLayout(row(action))
            self.buttons[key], self.states[key] = action, state
            grid.addWidget(card, i//2, i%2)
        self.box.addLayout(grid)
        events = Card()
        heading = row(label('Sitzungsprotokoll', 'title'))
        self.open_log = button('Logdatei öffnen')
        heading.addWidget(self.open_log)
        events.box.addLayout(heading)
        self.events = QListWidget()
        self.events.setMinimumHeight(83)
        events.box.addWidget(self.events)
        self.box.addWidget(events)


class InstrumentPage(Page):
    def __init__(self) -> None:
        super().__init__()
        self.connection = button('Verbinden')
        self.refresh = button('Status lesen')
        self.location = label('Kein Aufbau gewählt', 'muted', wrap=True)
        line = row(self.location)
        line.addWidget(self.refresh)
        line.addWidget(self.connection)
        self.box.addLayout(line)
        self.detail = Details()

    def add_details(self) -> None:
        card = Card('Gerätedetails')
        card.box.addWidget(self.detail)
        self.box.addWidget(card)


class VacuumPage(InstrumentPage):
    def __init__(self) -> None:
        super().__init__()
        self.pressure = PressureCard()
        self.box.addWidget(self.pressure)
        controls = Card('Druckmessung & Pumpenmonitoring')
        self.gauge = combo(['Messröhre 1', 'Messröhre 2', 'Messröhre 3'])
        self.interval = number(5, 2, 120, 0)
        self.monitor = button('Pumpenmonitoring starten', 'primary')
        controls.box.addLayout(fields([('Druckkanal', self.gauge), ('Pumpenintervall · s', self.interval)]))
        controls.box.addLayout(row(self.monitor))
        self.box.addWidget(controls)
        self.pump_labels = {}
        cards = QHBoxLayout()
        for key, name in [('turbo', 'Turbopumpe · nEXT 300'), ('backing', 'Vorpumpe · nXDS')]:
            card = Card(name)
            values = label('Noch keine Diagnose', 'muted', wrap=True)
            card.box.addWidget(values)
            self.pump_labels[key] = values
            cards.addWidget(card)
        self.box.addLayout(cards)
        self.add_details()


class BenderPage(InstrumentPage):
    def __init__(self) -> None:
        super().__init__()
        manual = Card('Elektrodenspannungen')
        self.editors, self.readbacks = {}, {}
        grid = QGridLayout()
        for col, text in enumerate(['Elektrode', 'Vorgabe · V', 'VSET · V', 'VMEAS · V', 'Messstrom · µA', 'Zustand']):
            grid.addWidget(label(text, 'muted'), 0, col)
        for i, key in enumerate(ELECTRODES, 1):
            positive = key.startswith('p')
            edit = number(15 if positive else -15, 0 if positive else -100, 100 if positive else 0)
            edit.setAccessibleName(f'{key} Zielspannung')
            grid.addWidget(label(key.upper(), 'title'), i, 0)
            grid.addWidget(edit, i, 1)
            self.editors[key] = edit
            readings = [label('—') for _ in range(4)]
            self.readbacks[key] = readings
            for col, value in enumerate(readings, 2):
                grid.addWidget(value, i, col)
        self.apply, self.enable = button('Sollwerte setzen'), button('HV einschalten', 'primary')
        self.disable = button('HV abfahren', 'stop')
        manual.box.addLayout(grid)
        manual.box.addLayout(row(self.apply, self.enable, self.disable))
        self.box.addWidget(manual)
        reg = Card('Automatische Nachregelung')
        reg.box.addWidget(label('Neue Vorgaben während der Regelung mit „Regelziele übernehmen“ bestätigen. '
                                'Stoppen fährt alle vier Elektroden ab.', 'muted', wrap=True))
        self.options = {}
        items = []
        for key, caption, value, lo, hi, digits in [
            ('tolerance_v','Toleranz · V',.2,.01,10,2), ('max_step_v','Max. Stellschritt · V',1,.01,10,2),
            ('initial_settle_s','Einschwingzeit · s',20,0,300,1),
            ('settle_interval_s','Prüftakt · s',5,.2,120,1), ('hold_interval_s','Haltetakt · s',30,.2,600,1),
            ('convergence_timeout_s','Einregelzeitlimit · s',90,1,1800,1)]:
            spin = number(value, lo, hi, digits, .1 if digits == 2 else 1)
            items.append((caption, spin))
            self.options[key] = spin
        reg.box.addLayout(fields(items))
        self.regulate = button('Regelung mit HV starten', 'primary')
        self.stop = button('Stoppen und abfahren', 'stop')
        reg.box.addLayout(row(self.regulate, self.stop))
        self.box.addWidget(reg)
        self.add_details()

    def targets(self) -> dict[str, float]:
        return {key: widget.value() for key, widget in self.editors.items()}


class LaserPage(InstrumentPage):
    def __init__(self) -> None:
        super().__init__()
        card = Card('Controllerstatus')
        self.interlock, self.emission, self.count = [label('—', 'value') for _ in range(3)]
        card.box.addLayout(fields([('Labor-Interlock', self.interlock), ('Laserfreigabe', self.emission),
                                   ('Kopfzähler', self.count)]))
        self.reset = button('Interlockfehler quittieren')
        self.off = button('Laser ausschalten', 'stop')
        card.box.addLayout(row(self.reset, self.off))
        self.box.addWidget(card)
        shot = Card('Einzelschuss')
        self.source = combo(['Oszilloskop · WaveGen', 'Externe Einzelpulsquelle'])
        self.arm_timeout = number(DEFAULT_ARM_TIMEOUT_S, 1, 60, digits=0)
        self.arm_timeout.setSuffix(' s')
        self.arm_timeout.setToolTip('Zeit ab bestätigter Laserfreigabe bis zur automatischen Abschaltung.')
        shot.box.addLayout(fields([('Triggerquelle', self.source), ('Scharfstellzeit', self.arm_timeout)], 2))
        self.hint = label('', 'muted', wrap=True)
        shot.box.addWidget(self.hint)
        self.phase = label('Laser nicht scharfgestellt', 'title', wrap=True)
        self.arm = button('Scharfstellen', 'primary')
        self.fire = button('TTL auslösen', 'primary')
        self.abort = button('Abbrechen', 'stop')
        shot.box.addWidget(self.phase)
        shot.box.addLayout(row(self.arm, self.fire, self.abort))
        self.box.addWidget(shot)
        self.add_details()


class ScopePage(InstrumentPage):
    def __init__(self) -> None:
        super().__init__()
        panel = Card('Kanalbelegung')
        grid = QGridLayout()
        for col, title in enumerate(['Kanal', 'Signal am Eingang', 'V / div', 'Offset · V', 'Tastkopf', 'Kopplung']):
            grid.addWidget(label(title, 'muted'), 0, col)
        self.channels = []
        for ch in range(1, 5):
            enabled = QCheckBox(f'CH{ch}')
            enabled.setChecked(ch == 1)
            name = QLineEdit()
            name.setPlaceholderText('Signalname')
            scale, offset = number(1, .001, 1000, 3, .1), number(0, -1000, 1000, 3, .1)
            probe = combo(['1', '10', '100', '1000'])
            coupling = combo(['DC', 'AC'])
            widgets = (enabled, name, scale, offset, probe, coupling)
            self.channels.append(widgets)
            for col, widget in enumerate(widgets):
                grid.addWidget(widget, ch, col)
        panel.box.addLayout(grid)
        self.box.addWidget(panel)
        settings = Card('Zeitbasis & Flankentrigger')
        self.timebase = number(10, .001, 1e8, 3)
        self.position = number(0, -1e8, 1e8, 3)
        self.trigger = combo(['CH1','CH2','CH3','CH4'])
        self.level = number(2.5, -1000, 1000, 3, .1)
        self.slope = combo(['Steigend', 'Fallend'])
        self.kind = combo(['Normal', 'Hohe Auflösung'])
        self.points = combo(['1000','10000','100000','1000000','2000000'])
        self.points.setCurrentIndex(1)
        settings.box.addLayout(fields([('Zeitbasis · µs/div', self.timebase), ('Zeitposition · µs', self.position),
                                      ('Aufnahmemodus', self.kind), ('Triggerquelle', self.trigger),
                                      ('Triggerschwelle · V', self.level), ('Flanke', self.slope),
                                      ('Angeforderte Punkte / Kanal', self.points)]))
        self.apply = button('Einstellungen übernehmen')
        self.wave_off = button('WaveGen ausschalten', 'stop')
        settings.box.addLayout(row(self.apply, self.wave_off))
        self.box.addWidget(settings)
        cap = Card('Kurvenübertragung')
        cap.box.addWidget(label('Neue Aufnahmen verwenden die obigen Einstellungen. Beim Abruf einer vorhandenen Kurve stoppt die Erfassung am Gerät.', 'muted', wrap=True))
        self.single = button('Neue Single-Aufnahme', 'primary')
        self.download = button('Vorhandene Kurve abrufen')
        self.cancel = button('Aufnahme abbrechen', 'stop')
        cap.box.addLayout(row(self.single, self.download, self.cancel))
        self.box.addWidget(cap)
        self.add_details()

    def setup(self) -> ScopeSetup:
        channels = tuple(ChannelSetup(i, enabled.isChecked(), name.text(), scale.value(), offset.value(),
                                      float(probe.currentText()), coupling.currentText())
                         for i, (enabled, name, scale, offset, probe, coupling) in enumerate(self.channels, 1))
        return ScopeSetup(channels, self.timebase.value()*1e-6, self.trigger.currentIndex()+1,
                          self.level.value(), ('POSITIVE', 'NEGATIVE')[self.slope.currentIndex()],
                          int(self.points.currentText()), self.position.value()*1e-6,
                          ('NORMAL', 'HRESOLUTION')[self.kind.currentIndex()])


class DataPage(Page):
    def __init__(self) -> None:
        super().__init__()
        self.measurement: Measurement | None = None
        self.open = button('Messdatei öffnen')
        self.save = button('NPZ speichern', 'primary')
        self.csv = button('CSV exportieren')
        self.remove = button('Messung schließen')
        self.box.addLayout(row(self.open, self.save, self.csv, self.remove))
        graph = Card('Signalverlauf')
        self.record = combo([])
        self.channel = combo([])
        self.reset = button('Gesamte Kurve')
        graph.box.addLayout(fields([('Messung', self.record), ('Kanal / Signal', self.channel)], 2))
        self.plot = TracePlot()
        graph.box.addWidget(self.plot)
        graph.box.addLayout(row(self.reset, label('Zeitbereich zum Auswerten mit der Maus aufziehen.', 'muted', wrap=True)))
        self.box.addWidget(graph)
        info = Card('Auswertung im sichtbaren Zeitbereich')
        self.summary = label('Noch keine Messung', wrap=True)
        self.quality = label('—', 'muted', wrap=True)
        info.box.addWidget(self.summary)
        info.box.addWidget(self.quality)
        self.box.addWidget(info)
        meta = Card('Aufnahmedetails')
        self.detail = Details()
        meta.box.addWidget(self.detail)
        self.box.addWidget(meta)
        self.reset.clicked.connect(self.plot.reset)
        self.channel.currentIndexChanged.connect(self.show_wave)
        self.plot.range_changed.connect(self.describe)

    def display(self, measurement: Measurement) -> None:
        self.measurement = measurement
        self.channel.blockSignals(True)
        self.channel.clear()
        for ch, name in measurement.labels.items():
            self.channel.addItem(f'CH{ch} · {name}', ch)
        self.channel.blockSignals(False)
        self.detail.display({**measurement.metadata, 'Preambles': {f'CH{ch}': w.preamble
                             for ch, w in measurement.traces.items()}})
        self.show_wave()

    def clear_measurement(self) -> None:
        self.measurement = None
        self.channel.clear()
        self.detail.clear()
        self.plot.clear()
        self.summary.setText('Noch keine Messung')
        self.quality.setText('—')

    def show_wave(self) -> None:
        if self.measurement and self.channel.currentData() is not None:
            wave = self.measurement.traces[self.channel.currentData()]
            self.plot.set_data(wave.time_s, wave.voltage_v)

    def describe(self, span: slice) -> None:
        if not self.measurement:
            return
        wave = self.measurement.traces[self.channel.currentData()]
        s = waveform_statistics(wave, span)
        self.summary.setText('    ·    '.join(f'{title}  {s[key]:.5g} V' for key, title in
                             [('minimum','Min'), ('maximum','Max'), ('peak_to_peak','Spitze–Spitze'),
                              ('mean','Mittel'), ('rms','RMS')] if key in s) or 'Keine gültigen Werte')
        self.quality.setText(f'{s["samples"]:,} Punkte   ·   {s["missing"]} fehlend   ·   {s["clipped"]} geclippt')
