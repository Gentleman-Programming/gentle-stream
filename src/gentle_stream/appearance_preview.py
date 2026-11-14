"""Read-only full-player facsimile; consumes the owner's already-polled sample."""
from __future__ import annotations

import time
from typing import Any

import numpy as np
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from .appearance import Preferences
from .visualizer import render_spectrum
from .volume_meter import render_volume_meter


class AppearancePreview(Vertical):
    DEFAULT_CSS = '''
    AppearancePreview { width: 1fr; max-width: 56; height: auto; padding: 0 1;
                        border: round $panel; }
    AppearancePreview Static { height: auto; width: 1fr; }
    AppearancePreview #preview-title { text-style: bold; }
    AppearancePreview #preview-mood { text-style: italic; }
    AppearancePreview #preview-spectrum { margin: 0; }
    AppearancePreview.dense { padding: 0; }
    AppearancePreview #preview-commands { background: #000000; }
    AppearancePreview #preview-input { color: $text-muted; }
    '''

    def __init__(self, preferences: Preferences):
        super().__init__(id='appearance-preview')
        self.preferences = preferences
        self._levels = self._peaks = self._deadlines = None
        self._snapshot: dict[str, Any] | None = None
        self._sample = None

    def compose(self) -> ComposeResult:
        for name in ('title', 'details', 'mood', 'spectrum', 'state', 'model',
                     'status', 'commands', 'input', 'footer'):
            yield Static(id='preview-' + name, markup=False)

    def configure(self, preferences: Preferences) -> None:
        if preferences != self.preferences:
            self._levels = self._peaks = self._deadlines = None
        self.preferences = preferences
        self.paint(advance=False)

    def refresh_snapshot(self, owner) -> None:
        now, player = owner.player.now, owner.player
        session = f' · session: {owner.session_usage.format()}' if owner.session_usage else ''
        playlist = (f' · {now.playlist_name} #{now.playlist_index + 1}'
                    if isinstance(getattr(now, 'playlist_name', None), str)
                    and isinstance(getattr(now, 'playlist_index', None), int) else '')
        key_labels = {'space': 'Space', 'slash': '/', 'plus,equals_sign': '+',
                      'minus': '−', 'up': '↑', 'down': '↓', 'f2': 'F2', 'f4': 'F4'}
        self._snapshot = {
            'title': f'♪ {now.style.name}', 'details': now.style.summary(),
            'mood': f'“{now.style.mood}”' if now.style.mood else '',
            'state': f'{"▶ Playing" if player.playing else "⏸ Paused"} · piece {now.piece} · bar {now.bar + 1}{playlist}',
            'model': f'model: {owner.shell.model or "Gentle Shell default"}{session}',
            'status': owner._w['status'].render(), 'playing': player.playing,
            'volume': round(player.volume * 100), 'repeat': player.continuous is True,
            # Copy existing chrome labels, never invoke model or control actions.
            'model_label': str(owner._buttons['model-command'].label).removeprefix('⚙').lstrip(),
            'footer': ' · '.join(f'{key_labels.get(binding.key, binding.key)} {binding.description}'
                                 for binding in owner.BINDINGS if binding.show),
        }
        self._sample = owner._w['spectrum'].cached_sample
        self.paint(advance=True)

    def on_resize(self) -> None:
        self.paint(advance=False)

    def paint(self, *, advance: bool) -> None:
        if not self.is_mounted or self._snapshot is None:
            return
        p, snapshot = self.preferences, self._snapshot
        background = {'black': '#000000', 'charcoal': '#202020'}.get(p.theme, p.background)
        self.styles.background = background
        self.styles.color = '#f6eff3'
        self.set_class(p.compact, 'dense')

        def update(name: str, value) -> None:
            widget = self.query_one('#preview-' + name, Static)
            canvas = '#000000' if name == 'commands' else background
            widget.styles.background = canvas
            widget.styles.text_wrap = 'wrap' if name in ('commands', 'footer') else 'nowrap'
            text = value.copy() if isinstance(value, Text) else Text(str(value))
            # Set both the widget canvas and the final span background. Rich text
            # converted to Textual Content can otherwise retain the owner's canvas.
            # This final background-only span preserves spectrum palette foregrounds.
            text.stylize(f'on {canvas}')
            widget.update(text)

        for name in ('title', 'details', 'mood', 'state', 'model', 'status'):
            widget = self.query_one('#preview-' + name, Static)
            value = snapshot[name]
            text = value.copy() if isinstance(value, Text) else Text(str(value))
            text.no_wrap, text.overflow = True, 'ellipsis'
            update(name, text)
            widget.styles.color = p.accent if name in ('title', 'mood') else '#a78e9b'
        for name, visible in {'details': p.show_details, 'mood': p.show_details,
                              'spectrum': p.show_visualizer, 'model': p.show_model,
                              'commands': p.show_controls, 'input': p.show_input,
                              'footer': p.show_footer}.items():
            self.query_one('#preview-' + name).display = visible
        gap = ' ' * p.icon_gap
        commands = Text(f'Panic{gap}💾{gap}↻{gap}{"on" if snapshot["repeat"] else "off"}'
                        f'{gap}✎{gap}☷\n⏮{gap}{"⏸" if snapshot["playing"] else "▶"}'
                        f'{gap}⏭{gap}− ', style=p.accent)
        commands.append_text(render_volume_meter(snapshot['volume'], p.accent))
        commands.append(f' +\n⚙{gap}{snapshot["model_label"]}')
        update('commands', commands)
        update('input', 'Describe a feeling… (read-only)')
        update('footer', snapshot['footer'])
        if self._sample is None:
            update('status', 'No spectrum sample yet · silent preview')
        if not p.show_visualizer:
            return
        raw = self._sample if self._sample is not None else np.zeros(1)
        levels = np.interp(np.linspace(0, 1, p.bands), np.linspace(0, 1, len(raw)), raw)
        if self._levels is None or len(self._levels) != p.bands:
            self._levels = levels.copy()
            self._peaks = levels.copy()
            self._deadlines = np.zeros(p.bands)
        assert self._peaks is not None and self._deadlines is not None
        if advance:
            self._levels = p.smoothing * self._levels + (1 - p.smoothing) * levels
            now = time.monotonic()
            reset = (self._levels >= self._peaks) | (now >= self._deadlines)
            self._peaks[reset] = self._levels[reset]
            self._deadlines[reset] = now + p.peak_hold
        update('spectrum', render_spectrum(
            self._levels, min(p.height, 8, max(3, self.screen.size.height - 24)),
            p, self._peaks if p.peak_hold else None,
            max(1, self.content_size.width or 40)))
