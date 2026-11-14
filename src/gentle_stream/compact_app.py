"""Small player surface; no mood interpretation or preference persistence."""
from __future__ import annotations

from dataclasses import replace

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.theme import Theme
from textual.widgets import Button, Static

from .app import GENTLEMAN_THEME
from .appearance import Preferences, PreferenceStore
from .client import PlayerError
from .visualizer import Spectrum


class CompactStreamApp(App):
    TITLE = 'gentle-dot'
    AUTO_FOCUS = None
    CSS = """
    Screen { background: $background; overflow: hidden; }
    #spectrum { height: 1fr; min-height: 0; overflow: hidden; }
    #controls { height: 1; width: 100%; align-horizontal: center; overflow: hidden; }
    Button { height: 1; min-height: 1; width: 3; min-width: 3;
             border: none; padding: 0; color: $primary; background: $background; }
    Button:hover { background: $panel; }
    Button:focus { text-style: reverse; }
    #volume { width: 5; height: 1; color: $primary; }
    """
    BINDINGS = [
        Binding('space', 'toggle', 'Play/Pause', priority=True),
        Binding('n', 'next', 'Next'),
        Binding('plus,equals_sign', 'volume(5)', 'Volume +5'),
        Binding('minus', 'volume(-5)', 'Volume -5'),
        Binding('up', 'volume(1)', 'Volume +1'),
        Binding('down', 'volume(-1)', 'Volume -1'),
        Binding('b', 'back', 'Detach'),
        Binding('q', 'quit', 'Stop'),
    ]

    def __init__(self, player, preference_store: PreferenceStore | None = None):
        super().__init__()
        self.player = player
        self.preference_store = preference_store if preference_store is not None else PreferenceStore()
        self.preferences = self.preference_store.load()
        self._keep_playing = False
        self.register_theme(GENTLEMAN_THEME)
        self.theme = GENTLEMAN_THEME.name
        if self.preferences != Preferences():
            # Match StreamApp.apply_preferences' saved color semantics without
            # constructing its DOM or persisting this mode's forced layout.
            p = self.preferences
            background = {'black': '#000000', 'charcoal': '#202020'}.get(p.theme, p.background)
            theme = Theme(name='compact-appearance', primary=p.accent, secondary='#f095c8', accent=p.accent,
                          foreground='#f6eff3', background=background, surface=background,
                          panel='#342230', success='#b4e7c7', warning='#f2b86d', error='#ff718f', dark=True)
            self.register_theme(theme)
            self.theme = theme.name

    def compose(self) -> ComposeResult:
        yield Spectrum(self.player, id='spectrum')
        with Horizontal(id='controls'):
            yield Button('⏮', id='previous', tooltip='Previous playlist song (wraps); select a playlist in full UI first')
            yield Button('⏸', id='play', tooltip='Play / pause (Space)')
            yield Button('⏭', id='next', tooltip='Next piece (n); next playlist song when continuous')
            yield Button('-', id='quieter', tooltip='Volume down 5% (-); Down: 1%')
            yield Static(id='volume')
            yield Button('+', id='louder', tooltip='Volume up 5% (+); Up: 1%')

    def on_mount(self) -> None:
        self._spectrum = self.query_one(Spectrum)
        self._volume = self.query_one('#volume', Static)
        self._play = self.query_one('#play', Button)
        for button in self.query(Button):
            button.active_effect_duration = 0
            button.styles.color = self.preferences.accent
        self.player.start()
        self._tick()
        self.set_interval(1 / self.preferences.refresh_hz, self._tick)
        if self.preference_store.error:
            self.notify(self.preference_store.error, severity='warning')

    def _tick(self) -> None:
        # Spectrum normally honors the full UI's saved minimum height. Only this
        # instance gets a runtime override; never save the forced mini layout.
        rows = max(1, self.size.height - 1)
        runtime = replace(self.preferences, height=rows)
        if self._spectrum.preferences != runtime:
            self._spectrum.configure(runtime)
        try:
            self._spectrum.tick()
        except ConnectionError:
            self.exit(message='gentle-stream: the music was stopped')
            return
        percent = max(0, min(100, round(self.player.volume * 100)))
        filled = round(percent / 20)
        self._volume.update(Text('━' * filled + '─' * (5 - filled), style=self.preferences.accent))
        self._volume.tooltip = f'Volume {percent}% · +/-: 5%; Up/Down: 1%'
        self._play.label = '⏸' if self.player.playing else '▶'

    def _control(self, action) -> None:
        try:
            action()
        except (ValueError, PlayerError) as error:
            self.notify(f'Could not control playback: {error}', severity='error')
        except ConnectionError:
            self.exit(message='gentle-stream: the music was stopped')
            return
        self._tick()

    def action_toggle(self) -> None:
        self._control(self.player.toggle)

    def action_next(self) -> None:
        self._control(self.player.next)

    def action_previous(self) -> None:
        self._control(self.player.previous)

    def action_volume(self, percent: int) -> None:
        self._control(lambda: self.player.set_volume((round(self.player.volume * 100) + percent) / 100))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {'previous': self.action_previous, 'play': self.action_toggle, 'next': self.action_next,
                   'quieter': lambda: self.action_volume(-5), 'louder': lambda: self.action_volume(5)}
        if action := actions.get(event.button.id):
            action()

    def action_back(self) -> None:
        self._keep_playing = True
        self.exit()

    def on_unmount(self) -> None:
        if self._keep_playing:
            self.player.detach()
        else:
            self.player.close()
