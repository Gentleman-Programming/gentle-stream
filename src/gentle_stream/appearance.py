"""Versioned UI-only preferences; never reads or writes music records."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from decimal import Decimal
import json
from math import isfinite
import os
from pathlib import Path
import re
import tempfile


SPECTRUM_STYLES = {
    'gentle': 'Bars', 'wave': 'Filled Wave', 'mirror': 'Mirrored',
    'segmented': 'Segmented', 'dots': 'Dots',
}
CHOICES = {'style': tuple(SPECTRUM_STYLES), 'palette': ('pink', 'analyzer', 'mono'),
           'theme': ('gentle', 'black', 'charcoal')}
BOUNDS = {'height': (5, 30), 'bands': (8, 128), 'refresh_hz': (5, 30),
          'icon_gap': (1, 3), 'smoothing': (0, 1), 'peak_hold': (0, 3)}


def background_color(preferences: Preferences) -> str:
    return {'black': '#000000', 'charcoal': '#202020'}.get(preferences.theme, preferences.background)


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
        for field in fields(self):
            key, value = field.name, getattr(self, field.name)
            if key in CHOICES:
                valid = isinstance(value, str) and value in CHOICES[key]
            elif key in ('background', 'accent'):
                valid = isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value)
            elif key in BOUNDS:
                lo, hi = BOUNDS[key]
                types = (int, float) if key in ('smoothing', 'peak_hold') else (int,)
                # Ordered bounds also reject NaN/infinity without converting a huge int to float.
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


class _LossyFloat(float):
    """A JSON number whose value the standard float encoder cannot round-trip."""


def _read_float(token: str) -> float:
    value = float(token)
    if value == 0:
        # Zero is exact at any exponent; a nonzero significand here underflowed.
        exact = not token.lower().partition('e')[0].strip('-0.')
    else:
        # Compare against the decimal the writer would emit, not the binary float.
        exact = isfinite(value) and Decimal(token) == Decimal(repr(value))
    return value if exact else _LossyFloat(value)


def _check_extension_numbers(record: dict) -> None:
    pending = [record]
    while pending:
        value = pending.pop()
        if isinstance(value, _LossyFloat):
            raise PreferenceError('Cannot save appearance preferences without changing an extension number. '
                                  'Fix or move appearance.json before saving.')
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)


class PreferenceStore:
    def __init__(self, path: Path | str | None = None):
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        self.path = Path(path) if path is not None else base / 'gentle-stream' / 'appearance.json'
        self.error: str | None = None

    def _load_record(self) -> tuple[Preferences, dict]:
        self.error = None
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'), object_pairs_hook=_unique_fields,
                              parse_constant=_invalid_constant, parse_float=_read_float)
            if (not isinstance(data, dict) or set(data) != {'version', 'preferences'}
                    or type(data['version']) is not int or data['version'] != 1):
                raise ValueError('unexpected format')
            record = data['preferences']
            known = {field.name for field in fields(Preferences)}
            if not isinstance(record, dict) or not known <= record.keys():
                raise ValueError('unexpected preference fields')
            # Known fields keep their v1 float validation and storage semantics.
            for key in known:
                if isinstance(record[key], _LossyFloat):
                    record[key] = float(record[key])
            return Preferences(**{key: record[key] for key in known}).validate(), record
        except FileNotFoundError:
            defaults = Preferences()
            return defaults, asdict(defaults)
        except (OSError, ValueError, TypeError, RecursionError) as e:
            self.error = f'Cannot read appearance preferences: {e}'
            return Preferences(), {}

    def load(self) -> Preferences:
        return self._load_record()[0]

    def _read_for_save(self) -> tuple[Preferences, dict]:
        current, record = self._load_record()
        if self.error:
            raise PreferenceError(self.error + '; fix or move appearance.json before saving.')
        return current, record

    def save(self, preferences: Preferences) -> None:
        preferences.validate()
        _, record = self._read_for_save()
        self._write({**record, **asdict(preferences)})

    def save_changes(self, baseline: Preferences, draft: Preferences) -> Preferences:
        draft.validate()
        current, record = self._read_for_save()
        changes = {key: value for key, value in asdict(draft).items() if value != getattr(baseline, key)}
        for key, value in changes.items():
            if getattr(current, key) not in (getattr(baseline, key), value):
                raise PreferenceError(f'Appearance changed elsewhere: {key}. Reopen Settings and try again.')
        merged = replace(current, **changes).validate()
        if merged != current:
            self._write({**record, **changes})
        return merged

    def _write(self, record: dict) -> None:
        _check_extension_numbers(record)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.appearance-', suffix='.tmp')
            # Failed writes retain their owned staging file for diagnosis; no implicit deletion.
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'preferences': record}, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        except OSError as e:
            raise PreferenceError(f'Cannot write appearance preferences: {e}') from e
