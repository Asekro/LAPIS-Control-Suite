"""Portable operator settings. Applying a profile only changes input widgets."""
from __future__ import annotations

import json
import math
from pathlib import Path

from PySide6.QtCore import QIODevice, QSaveFile
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QWidget

from .pages import BenderPage, ScopePage, VacuumPage


class Profiles:
    def __init__(self, bender: BenderPage, scope: ScopePage, vacuum: VacuumPage) -> None:
        self.fields: dict[str, QWidget] = {
            **{f'bender.{k}': w for k, w in bender.editors.items()},
            **{f'regulation.{k}': w for k, w in bender.options.items()},
            'scope.timebase_us': scope.timebase, 'scope.position_us': scope.position,
            'scope.trigger': scope.trigger, 'scope.trigger_level_v': scope.level,
            'scope.slope': scope.slope, 'scope.points': scope.points, 'scope.acquisition': scope.kind,
            'vacuum.gauge': vacuum.gauge, 'vacuum.interval_s': vacuum.interval,
        }
        for ch, widgets in enumerate(scope.channels, 1):
            for key, widget in zip(('enabled','signal','scale_v','offset_v','probe','coupling'), widgets):
                self.fields[f'scope.ch{ch}.{key}'] = widget

    def save(self, path: Path) -> None:
        values = {}
        for name, w in self.fields.items():
            if isinstance(w, QComboBox):
                values[name] = w.currentText()
            elif isinstance(w, QCheckBox):
                values[name] = w.isChecked()
            elif isinstance(w, QLineEdit):
                values[name] = w.text()
            else:
                values[name] = w.value()
        payload = json.dumps(dict(lapis_profile=1, settings=values), ensure_ascii=False, indent=2).encode('utf-8')
        file = QSaveFile(str(path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly) or file.write(payload) != len(payload) or not file.commit():
            raise OSError(file.errorString())

    def load(self, path: Path) -> None:
        profile = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(profile, dict) or profile.get('lapis_profile') != 1:
            raise ValueError('Unknown operator profile.')
        values = profile.get('settings')
        if not isinstance(values, dict) or values.keys() != self.fields.keys():
            raise ValueError('Incomplete operator profile.')
        # Validate the whole file against the current setup before changing any field.
        for name, w in self.fields.items():
            value = values[name]
            if isinstance(w, QComboBox):
                valid = isinstance(value, str) and w.findText(value) >= 0
            elif isinstance(w, QDoubleSpinBox):
                valid = type(value) in (int, float) and math.isfinite(value) and w.minimum() <= value <= w.maximum()
            elif isinstance(w, QCheckBox):
                valid = type(value) is bool
            else:
                valid = isinstance(value, str) and len(value) <= w.maxLength()
            if not valid:
                raise ValueError(f'Invalid profile setting {name}: {value!r}')
        for name, w in self.fields.items():
            value = values[name]
            if isinstance(w, QComboBox):
                w.setCurrentText(value)
            elif isinstance(w, QCheckBox):
                w.setChecked(value)
            elif isinstance(w, QLineEdit):
                w.setText(value)
            else:
                w.setValue(value)
