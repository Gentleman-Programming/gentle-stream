"""Versioned UI-only preferences compatible with the desktop appearance record."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path

from .presets import default_path

SPECTRUM_STYLES = {
    'gentle': 'Bars', 'segmented': 'Segmented', 'dots': 'Dots',
    'wave': 'Filled Wave', 'mirror': 'Mirrored',
}

READ_ERROR = 'Cannot read appearance.json. Fix or move it before saving.'


class PreferenceError(ValueError):
    """An honest, user-facing preference validation or storage failure."""


@dataclass(frozen=True)
class Preferences:
    style: str = 'gentle'
    palette: str = 'pink'
    theme: str = 'gentle'
    background: str = '#140a12'
    accent: str = '#ff76c3'
    height: int = 5  # minimum; expand into available space
    bands: int = 48
    refresh_hz: int = 15
    smoothing: float = 0.0
    peak_hold: float = 0.0  # seconds, visual only
    icon_gap: int = 2
    show_visualizer: bool = True
    show_controls: bool = True
    show_input: bool = True
    show_footer: bool = True
    show_details: bool = True
    show_model: bool = True
    compact: bool = False

    def validate(self) -> Preferences:
        enums = {'style': SPECTRUM_STYLES,
                 'palette': ('pink', 'analyzer', 'mono'),
                 'theme': ('gentle', 'black', 'charcoal')}
        bounds = {'height': (5, 30), 'bands': (8, 128), 'refresh_hz': (5, 30),
                  'icon_gap': (1, 3), 'smoothing': (0, 1), 'peak_hold': (0, 3)}
        for field in fields(self):
            key, value = field.name, getattr(self, field.name)
            if key in enums:
                valid = isinstance(value, str) and value in enums[key]
            elif key in ('background', 'accent'):
                valid = isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value)
            elif key in bounds:
                lo, hi = bounds[key]
                types = (int, float) if key in ('smoothing', 'peak_hold') else (int,)
                valid = type(value) in types and lo <= value <= hi
            else:
                valid = type(value) is bool
            if not valid:
                raise PreferenceError(f'Invalid {key}.')
        return self


def _unique_fields(pairs):
    values = dict(pairs)
    if len(values) != len(pairs):
        raise ValueError('duplicate preference fields')
    return values


def _invalid_constant(value):
    raise ValueError(f'invalid JSON constant: {value}')


class PreferenceStore:
    def __init__(self):
        self.path = default_path().with_name('appearance.json')
        self.error: str | None = None

    def _read(self) -> tuple[Preferences, dict]:
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'), object_pairs_hook=_unique_fields,
                              parse_constant=_invalid_constant)
        except FileNotFoundError:
            preferences = Preferences()
            return preferences, asdict(preferences)
        if not isinstance(data, dict) or set(data) != {'version', 'preferences'}:
            raise ValueError('unexpected format')
        if type(data['version']) is not int or data['version'] != 1:
            raise ValueError('unsupported version')
        record = data['preferences']
        known = {field.name for field in fields(Preferences)}
        if not isinstance(record, dict) or not known <= record.keys():
            raise ValueError('missing preference fields')
        return Preferences(**{key: record[key] for key in known}).validate(), record

    def _load_record(self) -> tuple[Preferences, dict]:
        self.error = None
        try:
            return self._read()
        except (OSError, ValueError, RecursionError):
            self.error = READ_ERROR
            return Preferences(), {}

    def load(self) -> Preferences:
        return self._load_record()[0]

    def save_style(self, style: str) -> None:
        # Re-read through the same validator, retaining fields this terminal does not own.
        preferences, record = self._load_record()
        if self.error:
            raise PreferenceError(self.error)
        record['style'] = replace(preferences, style=style).validate().style
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.appearance-', suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump({'version': 1, 'preferences': record}, stream, indent=2)
                    stream.write('\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)
        except OSError as e:
            raise PreferenceError('Cannot write appearance.json.') from e
