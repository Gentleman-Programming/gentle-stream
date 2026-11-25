"""Terminal-only appearance dialog; its preview never talks to the player."""
from __future__ import annotations

import numpy as np
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from .appearance import PreferenceError, Preferences, PreferenceStore, SPECTRUM_STYLES
from .visualizer import render_spectrum

MODES = ('gentle', 'wave', 'mirror')
PREVIEW_LEVELS = np.array([.12, .37, .81, .95, .58, .25, .35, .72, .58, .32, .2, .1])


class AppearanceSettings(ModalScreen[str | None]):
    BINDINGS = [Binding('escape', 'close_settings', 'Close')]
    DEFAULT_CSS = """
    AppearanceSettings { align: center middle; }
    AppearanceSettings > VerticalScroll {
        width: 60; max-width: 100%; height: auto; max-height: 100%;
        background: $surface; border: round $primary; padding: 0 1;
    }
    #appearance-title { color: $primary; text-style: bold; }
    #appearance-modes, #appearance-modes:focus { height: auto; border: none; background: $surface; }
    #appearance-preview { height: 5; margin: 1 0; }
    #appearance-status { color: $text-muted; }
    AppearanceSettings Horizontal { height: 3; }
    AppearanceSettings Button { width: 1fr; min-width: 0; padding: 0; }
    """

    def __init__(self, store: PreferenceStore, style: str):
        super().__init__()
        self.store = store
        self.saved = self.draft = style
        self.confirming = False
        self.error = store.error

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static('Appearance Settings', id='appearance-title')
            yield OptionList(id='appearance-modes')
            yield Static(id='appearance-preview')
            yield Static(id='appearance-status', markup=False)
            with Horizontal(id='appearance-actions'):
                yield Button('Defaults', id='appearance-defaults')
                yield Button('Save and Close', id='appearance-save', variant='primary')
                yield Button('Close', id='appearance-close')
            with Horizontal(id='appearance-confirm'):
                yield Button('Keep Editing', id='appearance-keep')
                yield Button('Discard Changes', id='appearance-discard', variant='error')

    def on_mount(self) -> None:
        self._preview()
        self._confirm(False)

    def _preview(self) -> None:
        options = self.query_one(OptionList)
        options.clear_options().add_options([
            Option(f"{'●' if mode == self.draft else '○'}  {SPECTRUM_STYLES[mode]}", id=mode)
            for mode in MODES
        ])
        options.highlighted = MODES.index(self.draft) if self.draft in MODES else 0
        self.query_one('#appearance-preview', Static).update(
            render_spectrum(PREVIEW_LEVELS, 5, Preferences(style=self.draft), columns=32))
        self.query_one('#appearance-save', Button).disabled = self.draft == self.saved

    def _confirm(self, confirming: bool) -> None:
        self.confirming = confirming
        self.query_one('#appearance-actions').display = not confirming
        self.query_one('#appearance-confirm').display = confirming
        self.query_one(OptionList).disabled = confirming
        hint = 'Preview only until saved.'
        if self.draft not in MODES:
            hint = f'Current legacy style: {SPECTRUM_STYLES[self.draft]}. {hint}'
        self.query_one('#appearance-status', Static).update(
            'Discard unsaved changes?' if confirming else self.error or hint)
        self.query_one('#appearance-keep' if confirming else '#appearance-modes').focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id in MODES:
            self.draft = event.option.id
            self._preview()

    def action_close_settings(self) -> None:
        if self.confirming:
            self._confirm(False)
        elif self.saved == self.draft:
            self.dismiss(None)
        else:
            self._confirm(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        match event.button.id:
            case 'appearance-defaults':
                self.draft = Preferences().style
                self._preview()
                self.query_one(OptionList).focus()
            case 'appearance-close':
                self.action_close_settings()
            case 'appearance-keep':
                self._confirm(False)
            case 'appearance-discard':
                self.dismiss(None)
            case 'appearance-save':
                try:
                    self.store.save_style(self.draft)
                except PreferenceError as error:
                    self.error = str(error)
                    self._confirm(False)
                else:
                    self.dismiss(self.draft)
