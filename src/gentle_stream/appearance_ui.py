"""Terminal appearance editor with a synthetic, draft-only preview."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, OptionList, Select, Static
from textual.widgets.option_list import Option

from .appearance import (BOUNDS, CHOICES, SPECTRUM_STYLES, PreferenceError, Preferences, PreferenceStore,
                         background_color)
from .appearance_preview import appearance_frame
from .modal_header import ModalHeader

NUMBERS = {'height': 'Minimum height (rows)', 'bands': 'Bands', 'refresh_hz': 'Refresh (Hz)',
           'smoothing': 'Smoothing', 'peak_hold': 'Peak hold (seconds)', 'icon_gap': 'Icon gap'}
TOGGLES = {'show_visualizer': 'Show spectrum', 'show_controls': 'Show toolbar',
           'show_input': 'Show mood input', 'show_footer': 'Show playback footer',
           'show_details': 'Show track details', 'show_model': 'Show model', 'compact': 'Compact layout'}
COLORS = {'background': 'Background (Gentle theme)', 'accent': 'Accent'}
MODES = tuple(SPECTRUM_STYLES)


class SettingsScreen(ModalScreen[Preferences | None]):
    BINDINGS = [Binding('escape', 'close_settings', 'Close')]
    DEFAULT_CSS = """
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical {
        width: 60; max-width: 100%; height: 100%; max-height: 38;
        background: $surface; border: round $primary; padding: 0 1;
    }
    #appearance-fields { height: 1fr; min-height: 1; }
    #appearance-fields .appearance-section { color: $primary; text-style: bold; margin-top: 1; }
    #appearance-modes, #appearance-modes:focus { height: auto; border: none; background: $surface; }
    #appearance-preview { height: auto; margin: 1 0; border: round $panel; }
    #appearance-status { color: $text-muted; height: auto; max-height: 3; }
    #appearance-status.error { color: $error; }
    SettingsScreen Horizontal { height: 3; }
    #settings-sections > Button, #appearance-actions > Button, #appearance-confirm > Button {
        width: 1fr; min-width: 0; height: 3; padding: 0;
    }
    #appearance-fields Select, #appearance-fields Input, #appearance-fields Checkbox { width: 1fr; }
    #appearance-fields .appearance-swatch { width: 4; text-align: center; margin-top: 1; }
    #appearance-actions > #appearance-save { width: 2fr; }
    """

    def __init__(self, store: PreferenceStore, saved: Preferences, model_label: Callable[[], str],
                 pick_model: Callable[[], None], open_audio: Callable[[], None]):
        super().__init__()
        self.store = store
        self.saved = self.draft = saved
        self.model_label = model_label
        self.pick_model = pick_model
        self.open_audio = open_audio
        self.confirming = False
        self.invalid: str | None = None
        self.notice = store.error
        self.write_error = False
        self._mode = saved.style

    def compose(self) -> ComposeResult:
        with Vertical():
            yield ModalHeader(Static('Appearance Settings', id='appearance-title'), self.action_close_settings)
            with VerticalScroll(id='appearance-fields'):
                yield OptionList(*(Option(label, id=mode) for mode, label in SPECTRUM_STYLES.items()),
                                 id='appearance-modes')
                yield Static(id='appearance-preview')
                yield Static('Colors', classes='appearance-section')
                for key in ('palette', 'theme'):
                    yield Static(key.title())
                    yield Select([(value.title(), value) for value in CHOICES[key]], allow_blank=False,
                                 value=getattr(self.saved, key), id=f'appearance-{key}')
                for key, label in COLORS.items():
                    yield Static(f'{label} · #RRGGBB')
                    with Horizontal():
                        yield Static(classes='appearance-swatch', id=f'appearance-swatch-{key}')
                        yield Input(getattr(self.saved, key), id=f'appearance-{key}')
                yield Static('Spectrum and spacing', classes='appearance-section')
                for key, label in NUMBERS.items():
                    lo, hi = BOUNDS[key]
                    yield Static(f'{label} · {lo}–{hi}')
                    yield Input(str(getattr(self.saved, key)), id=f'appearance-{key}')
                yield Static('Layout', classes='appearance-section')
                for key, label in TOGGLES.items():
                    yield Checkbox(label, value=getattr(self.saved, key), id=f'appearance-{key}')
            yield Static(id='appearance-status', markup=False)
            with Horizontal(id='settings-sections'):
                yield Button('Models', id='appearance-models', tooltip='Choose the model for future moods')
                yield Button('Audio → Output', id='appearance-audio', tooltip='Open the audio output picker (o)')
            with Horizontal(id='appearance-actions'):
                yield Button('Defaults', id='appearance-defaults')
                yield Button('Save and Close', id='appearance-save', variant='primary')
                yield Button('Close', id='appearance-close')
            with Horizontal(id='appearance-confirm'):
                yield Button('Keep Editing', id='appearance-keep')
                yield Button('Discard Changes', id='appearance-discard', variant='error')

    def on_mount(self) -> None:
        self._preview()
        self.query_one(OptionList).highlighted = MODES.index(self.saved.style)
        self._confirm(False)
        self._show_model()

    def on_resize(self) -> None:
        if self.is_mounted:
            self._preview()

    def on_screen_resume(self) -> None:
        if self.is_mounted:
            self._show_model()

    def _show_model(self) -> None:
        self.query_one('#appearance-models', Button).label = Text(
            f'Models · {self.model_label()}', no_wrap=True, overflow='ellipsis')

    def show_status(self, message: str, *, error: bool = False) -> None:
        self.notice = message
        self.write_error = error
        self._status()

    def _status(self) -> None:
        hint = self.invalid or self.notice or 'Preview only until saved.'
        status = self.query_one('#appearance-status', Static)
        status.update('Discard unsaved changes?' if self.confirming else hint)
        status.set_class(not self.confirming and bool(self.invalid or self.write_error or self.store.error), 'error')
        self.query_one('#appearance-save', Button).disabled = self.invalid is not None or self.draft == self.saved

    def _preview(self) -> None:
        p = self.draft
        options = self.query_one(OptionList)
        for mode, label in SPECTRUM_STYLES.items():
            options.replace_option_prompt(mode, f"{'●' if mode == p.style else '○'}  {label}")
        for key in COLORS:
            self.query_one(f'#appearance-swatch-{key}', Static).update(Text('██', style=getattr(p, key)))
        preview = self.query_one('#appearance-preview', Static)
        preview.styles.background = background_color(p)
        preview.styles.border = ('round', p.accent)
        preview.styles.padding = (0, 0) if p.compact else (0, 1)
        preview.update(appearance_frame(p, min(32, max(1, self.size.width - 12))))

    def _read_fields(self) -> None:
        values = {'style': self._mode}
        try:
            for key in ('palette', 'theme'):
                values[key] = self.query_one(f'#appearance-{key}', Select).value
            for key in COLORS:
                values[key] = self.query_one(f'#appearance-{key}', Input).value
            for key in NUMBERS:
                raw = self.query_one(f'#appearance-{key}', Input).value
                parse = float if key in ('smoothing', 'peak_hold') else int
                try:
                    values[key] = parse(raw)
                except ValueError:
                    raise PreferenceError(f'Invalid {key}.') from None
            for key in TOGGLES:
                values[key] = self.query_one(f'#appearance-{key}', Checkbox).value
            draft = replace(self.saved, **values).validate()
        except PreferenceError as error:
            self.invalid = str(error)
        else:
            self.invalid = None
            if draft != self.draft:
                self.draft = draft
                self._preview()
        self._status()

    def _confirm(self, confirming: bool) -> None:
        self.confirming = confirming
        self.query_one('#appearance-actions').display = not confirming
        self.query_one('#settings-sections').display = not confirming
        self.query_one('#appearance-confirm').display = confirming
        self.query_one('#appearance-fields').disabled = confirming
        self._status()
        self.query_one('#appearance-keep' if confirming else '#appearance-modes').focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id in MODES:
            self._mode = event.option.id
            self._read_fields()

    def on_select_changed(self, event: Select.Changed) -> None:
        self._read_fields()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self._read_fields()

    def on_input_changed(self, event: Input.Changed) -> None:
        self._read_fields()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._read_fields()

    def action_close_settings(self) -> None:
        if self.confirming:
            self._confirm(False)
            return
        self._read_fields()
        if self.saved == self.draft and self.invalid is None:
            self.dismiss(None)
        else:
            self._confirm(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        match event.button.id:
            case 'appearance-defaults':
                defaults = Preferences()
                self._mode = defaults.style
                for key in ('palette', 'theme'):
                    self.query_one(f'#appearance-{key}', Select).value = getattr(defaults, key)
                for key in NUMBERS | COLORS:
                    self.query_one(f'#appearance-{key}', Input).value = str(getattr(defaults, key))
                for key in TOGGLES:
                    self.query_one(f'#appearance-{key}', Checkbox).value = getattr(defaults, key)
                self._read_fields()
                self.query_one(OptionList).highlighted = MODES.index(self._mode)
                self.query_one(OptionList).focus()
            case 'appearance-close':
                self.action_close_settings()
            case 'appearance-models':
                self.pick_model()
            case 'appearance-audio':
                self.open_audio()
            case 'appearance-keep':
                self._confirm(False)
            case 'appearance-discard':
                self.dismiss(None)
            case 'appearance-save':
                self._read_fields()
                if self.invalid is not None or self.draft == self.saved:
                    return
                try:
                    saved = self.store.save_changes(self.saved, self.draft)
                except PreferenceError as error:
                    self.show_status(str(error), error=True)
                else:
                    self.dismiss(saved)
