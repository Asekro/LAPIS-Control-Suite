"""Scope setup, trace download, local storage and numerical analysis."""
from __future__ import annotations

import csv
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import IO

import numpy as np
from pyvisa.errors import VisaIOError

from lapis_control.devices.oscilloscope.keysight_dsox1204g import KeysightDSOX1204G, ScopeError, Waveform


@dataclass(frozen=True)
class ChannelSetup:
    channel: int
    enabled: bool
    label: str
    scale_v: float
    offset_v: float
    probe: float
    coupling: str


@dataclass(frozen=True)
class ScopeSetup:
    channels: tuple[ChannelSetup, ...]
    timebase_s: float
    trigger: int
    level_v: float
    slope: str
    points: int
    position_s: float = 0.0
    acquisition: str = 'NORMAL'

    @property
    def selected(self) -> dict[int, str]:
        return {c.channel: c.label.strip() or f'CH{c.channel}' for c in self.channels if c.enabled}

    def apply(self, scope: KeysightDSOX1204G) -> None:
        if self.trigger not in self.selected:
            raise ValueError('The trigger channel must be enabled.')
        for c in self.channels:
            if c.enabled:
                scope.configure_channel(c.channel, enabled=True, scale_v=c.scale_v, offset_v=c.offset_v,
                                        probe=c.probe, coupling=c.coupling)
            else:
                scope.configure_channel(c.channel, enabled=False)
        scope.configure_timebase(scale_s=self.timebase_s, position_s=self.position_s)
        scope.configure_edge_trigger(self.trigger, level_v=self.level_v, slope=self.slope)
        scope.configure_acquisition(self.acquisition)


@dataclass
class Measurement:
    traces: dict[int, Waveform]
    labels: dict[int, str]
    metadata: dict

    @classmethod
    def download(cls, scope: KeysightDSOX1204G, setup: ScopeSetup, source: str, *,
                 stop: bool, shot_counts: tuple[int, int] | None = None) -> Measurement:
        selected = setup.selected
        if not selected:
            raise ValueError('Select at least one channel.')
        traces = scope.read_waveforms(tuple(selected), points=setup.points, stop=stop)
        metadata = dict(transferred_utc=datetime.now(timezone.utc).isoformat(), source=source, instrument=scope.idn)
        try:
            metadata['scope_status'] = scope.status()
        except (ScopeError, VisaIOError) as exc:
            # Preserve a completed transfer if the subsequent status query fails.
            metadata['scope_status_error'] = str(exc)
        if not stop:
            metadata['requested_setup'] = asdict(setup)
        if shot_counts is not None:
            metadata['laser_counts'] = shot_counts
        return cls(traces, selected, metadata)


@contextmanager
def _atomic_file(path: Path, binary: bool) -> Iterator[IO]:
    """Keep an existing measurement intact on full disks and interrupted writes."""
    options = {} if binary else dict(encoding='utf-8', newline='')
    with NamedTemporaryFile(mode='wb' if binary else 'w', dir=path.parent, delete=False,
                            prefix=f'.{path.name}.', **options) as file:
        temp = Path(file.name)
        try:
            yield file
            file.close()  # Required before replacing an open file on Windows.
            os.replace(temp, path)
        finally:
            file.close()
            temp.unlink(missing_ok=True)


_ARRAYS = ('time_s', 'voltage_v', 'raw', 'missing', 'clipped_low', 'clipped_high')


def save_measurement(path: Path, measurement: Measurement) -> None:
    channels = {ch: dict(label=measurement.labels[ch], preamble=w.preamble)
                for ch, w in measurement.traces.items()}
    header = dict(version=1, metadata=measurement.metadata, channels=channels)
    arrays = {f'ch{ch}_{name}': getattr(w, name) for ch, w in measurement.traces.items()
              for name in _ARRAYS}
    with _atomic_file(path, True) as file:
        np.savez_compressed(file, header=json.dumps(header, ensure_ascii=False), **arrays)


def load_measurement(path: Path) -> Measurement:
    with np.load(path, allow_pickle=False) as data:
        header = json.loads(str(data['header'].item()))
        if header['version'] != 1:
            raise ValueError('Unsupported LAPIS measurement format.')
        traces, labels = {}, {}
        for key, info in header['channels'].items():
            ch = int(key)
            if ch not in (1, 2, 3, 4) or ch in traces:
                raise ValueError('Invalid scope channel.')
            arrays = {name: data[f'ch{ch}_{name}'] for name in _ARRAYS}
            t = arrays['time_s']
            if (t.ndim != 1 or not 1 <= t.size <= 2_000_000 or any(a.shape != t.shape for a in arrays.values())
                    or any(arrays[k].dtype.kind != 'b' for k in _ARRAYS[3:])
                    or any(arrays[k].dtype.kind not in 'fiu' for k in _ARRAYS[:3])
                    or not np.isfinite(t).all() or np.any(t[1:] <= t[:-1])):
                raise ValueError(f'Invalid waveform arrays on CH{ch}.')
            traces[ch] = Waveform(channel=ch, preamble=info['preamble'], **arrays)
            labels[ch] = str(info['label'])
        metadata = header['metadata']
        if not traces or not isinstance(metadata.get('source'), str):
            raise ValueError('Incomplete measurement header.')
        if datetime.fromisoformat(metadata['transferred_utc']).utcoffset() is None:
            raise ValueError('The measurement timestamp must specify a time zone.')
        return Measurement(traces, labels, metadata)


def export_csv(path: Path, measurement: Measurement) -> None:
    """Long format retains independent time axes and flags on every channel."""
    with _atomic_file(path, False) as file:
        writer = csv.writer(file)
        writer.writerow(('channel', 'signal', 'time_s', 'voltage_V', 'raw', 'missing',
                         'clipped_low', 'clipped_high'))
        for ch, w in measurement.traces.items():
            label = measurement.labels[ch]
            writer.writerows((ch, label, t, v, raw, int(missing), int(low), int(high))
                             for t, v, raw, missing, low, high in zip(
                                 w.time_s, w.voltage_v, w.raw, w.missing, w.clipped_low, w.clipped_high))


def waveform_statistics(wave: Waveform, selection: slice = slice(None)) -> dict:
    voltage = wave.voltage_v[selection]
    valid = ~wave.missing[selection] & np.isfinite(voltage)
    values = voltage[valid]
    result = dict(samples=voltage.size, missing=int(np.count_nonzero(~valid)),
                  clipped=int(np.count_nonzero(wave.clipped_low[selection] | wave.clipped_high[selection])))
    if values.size:
        result.update(minimum=float(values.min()), maximum=float(values.max()),
                      peak_to_peak=float(np.ptp(values)), mean=float(values.mean()),
                      rms=float(np.sqrt(np.mean(np.square(values, dtype=float)))))
    return result
