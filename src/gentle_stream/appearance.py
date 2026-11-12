"""Versioned UI-only preferences; never reads or writes music records."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path
import re
import tempfile


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
        enums = {'style': ('gentle', 'segmented', 'dots'),
                 'palette': ('pink', 'analyzer', 'mono'),
                 'theme': ('gentle', 'black', 'charcoal')}
        bounds = {'height': (5, 30), 'bands': (8, 128), 'refresh_hz': (5, 30),
                  'icon_gap': (1, 3), 'smoothing': (0, 1), 'peak_hold': (0, 3)}
        for field in fields(self):
            key, value = field.name, getattr(self, field.name)
            valid = True
            if key in enums:
                valid = isinstance(value, str) and value in enums[key]
            elif key in ('background', 'accent'):
                valid = isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value)
            elif key in bounds:
                lo, hi = bounds[key]
                types = (int, float) if key in ('smoothing', 'peak_hold') else (int,)
                # Ordered bounds also reject NaN/infinity without converting a huge int to float.
                valid = type(value) in types and lo <= value <= hi
            else:
                valid = type(value) is bool
            if not valid:
                raise PreferenceError(f'Invalid {key}.')
        return self


class PreferenceStore:
    def __init__(self, path: Path | str | None = None):
        base = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
        self.path = Path(path) if path is not None else base / 'gentle-stream' / 'appearance.json'
        self.error: str | None = None

    def load(self) -> Preferences:
        self.error = None
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or data.get('version') != 1 or set(data) != {'version', 'preferences'}:
                raise ValueError('unexpected format')
            record = data['preferences']
            if not isinstance(record, dict) or set(record) != set(asdict(Preferences())):
                raise ValueError('unexpected preference fields')
            return Preferences(**record).validate()
        except FileNotFoundError:
            return Preferences()
        except (OSError, UnicodeError, ValueError, TypeError) as e:
            self.error = f'Cannot read appearance preferences: {e}'
            return Preferences()

    def save(self, preferences: Preferences) -> None:
        preferences.validate()  # validation precedes any filesystem side effect
        self.load()
        if self.error:
            raise PreferenceError(self.error + '; fix or move appearance.json before saving.')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self.path.parent, prefix='.appearance-', suffix='.tmp')
            # Failed writes retain their owned staging file for diagnosis; no implicit deletion.
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'preferences': asdict(preferences)}, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        except OSError as e:
            raise PreferenceError(f'Cannot write appearance preferences: {e}') from e
