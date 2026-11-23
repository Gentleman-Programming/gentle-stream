"""Native, keyboard-accessible settings with explicit preview and persistence."""
from dataclasses import asdict

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, Switch

from .appearance import Preferences, PreferenceError

CHOICES = {'style': ('gentle', 'segmented', 'dots'), 'palette': ('pink', 'analyzer', 'mono'),
           'theme': ('gentle', 'black', 'charcoal')}
SECTIONS = (
    ('Appearance', ('theme', 'background', 'accent')),
    ('Visualizer', ('style', 'palette', 'height', 'bands', 'refresh_hz', 'smoothing', 'peak_hold')),
    ('Layout', ('compact', 'icon_gap', 'show_visualizer', 'show_controls', 'show_input',
                'show_footer', 'show_details', 'show_model')),
)
LABELS = {'height': 'Minimum bar height (5–30 rows)', 'bands': 'Bands (8–128)',
          'refresh_hz': 'Refresh (5–30 Hz)', 'smoothing': 'Smoothing (0–1)',
          'peak_hold': 'Peak hold (0–3 seconds)', 'icon_gap': 'Icon-label gap (1–3 cells)',
          'background': 'Custom background (#RRGGBB, gentle theme)', 'accent': 'Accent (#RRGGBB)',
          'show_visualizer': 'Show visualizer', 'show_controls': 'Show controls',
          'show_input': 'Show mood input', 'show_footer': 'Show footer',
          'show_details': 'Show track details', 'show_model': 'Show model line',
          'compact': 'Compact layout'}


class SettingSwitch(Switch):
    """Show a readable state per row even when adjacent switches share a value."""

    def render(self) -> Text:
        return Text('On' if self.value else 'Off')


class SettingsScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'close', 'Close')]
    DEFAULT_CSS = '''
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical { width: 68; max-width: 100%; height: 32; max-height: 92%;
        border: round $primary; padding: 0 2; background: $surface; }
    SettingsScreen #settings-header, SettingsScreen #settings-actions { height: 1; }
    SettingsScreen #settings-pickers { width: auto; height: 1; }
    SettingsScreen.narrow #settings-header, SettingsScreen.narrow #settings-pickers { height: 2; }
    SettingsScreen.narrow #settings-pickers { layout: vertical; align-horizontal: right; }
    SettingsScreen #settings-title { width: 1fr; color: $foreground; text-style: bold; }
    SettingsScreen #settings-description, SettingsScreen #settings-status { color: $text-muted; }
    SettingsScreen #settings-status { height: auto; margin: 1 0; }
    SettingsScreen #settings-status.error { color: $error; }
    SettingsScreen VerticalScroll { height: 1fr; min-height: 1; scrollbar-size-vertical: 1;
        padding-right: 1; }
    SettingsScreen .setting-section { height: 1; margin-top: 1; color: $foreground; text-style: bold; }
    SettingsScreen .setting-row { height: auto; margin-bottom: 1; }
    SettingsScreen .setting-label { width: 1fr; padding-right: 1; color: $text-muted; }
    SettingsScreen Input, SettingsScreen Select, SettingsScreen Switch { width: 16; }
    SettingsScreen Input.-textual-compact, SettingsScreen Select.-textual-compact > SelectCurrent {
        padding: 0 1; background: $panel; }
    SettingsScreen Switch, SettingsScreen Switch:focus {
        padding: 0 1; border: none; background: $panel; }
    SettingsScreen Switch:hover { background-tint: $foreground 10%; }
    SettingsScreen Switch:focus { background-tint: $foreground 15%; text-style: bold underline; }
    SettingsScreen Button { width: auto; min-width: 0; height: 1; min-height: 1; padding: 0 1;
        border: none; background: $panel; color: $foreground; }
    SettingsScreen #settings-actions { align-horizontal: right; }
    SettingsScreen #settings-actions Button { margin-left: 1; }
    SettingsScreen #settings-save { background: $primary; color: $background; text-style: bold; }
    SettingsScreen Button:hover { background-tint: $foreground 15%; }
    SettingsScreen Button:focus { text-style: bold underline; }
    '''

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.baseline = owner.preferences

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id='settings-header'):
                yield Static('Settings', id='settings-title')
                with Horizontal(id='settings-pickers'):
                    yield Button('Audio output…', id='settings-output', compact=True)
                    yield Button('Choose mood model…', id='settings-model', compact=True)
            yield Static('Appearance previews do not change the music.', id='settings-description')
            with VerticalScroll():
                for section, keys in SECTIONS:
                    yield Static(section, classes='setting-section')
                    for key in keys:
                        value = getattr(self.baseline, key)
                        with Horizontal(classes='setting-row'):
                            yield Static(LABELS.get(key, key.title()), classes='setting-label')
                            if key in CHOICES:
                                yield Select([(s.title(), s) for s in CHOICES[key]], value=value,
                                             allow_blank=False, compact=True, id='pref-' + key)
                            elif type(value) is bool:
                                yield SettingSwitch(value, animate=False, id='pref-' + key)
                            else:
                                yield Input(str(value), compact=True, id='pref-' + key)
            error = self.owner.preference_store.error
            yield Static(Text(error or 'Close or Esc discards unsaved appearance changes.'),
                         id='settings-status', classes='error' if error else None)
            with Horizontal(id='settings-actions'):
                for label, key in [('Preview', 'preview'), ('Defaults', 'defaults'),
                                   ('Close', 'close'), ('Save & close', 'save')]:
                    yield Button(label, id='settings-' + key, compact=True)

    def on_resize(self, event: events.Resize) -> None:
        # Eight title cells, 17 + 22 for the buttons, and six for the modal frame.
        self.set_class(event.size.width < 53, 'narrow')

    def read_preferences(self) -> Preferences:
        values = {}
        for key, default in asdict(Preferences()).items():
            widget = self.query_one('#pref-' + key)
            value = widget.value
            if type(default) in (int, float):
                try:
                    value = type(default)(value)
                except (ValueError, TypeError):
                    raise PreferenceError(f'Invalid {key}.') from None
            values[key] = value
        return Preferences(**values).validate()

    def show_status(self, message: str, *, error: bool = False) -> None:
        status = self.query_one('#settings-status', Static)
        status.set_class(error, 'error')
        status.update(Text(message))

    async def submit(self, action: str) -> None:
        if action == 'close':
            self.action_close()
            return
        if action == 'model':
            self.owner.action_pick_model()
            return
        if action == 'output':
            self.owner.action_pick_output()
            return
        try:
            preferences = Preferences() if action == 'defaults' else self.read_preferences()
            self.owner.apply_preferences(preferences, persist=action == 'save')
        except PreferenceError as e:
            self.show_status(str(e), error=True)
            return
        if action == 'save':
            self.dismiss()
        elif action == 'defaults':
            for key, value in asdict(preferences).items():
                widget = self.query_one('#pref-' + key)
                widget.value = value if isinstance(widget, (Select, Switch)) else str(value)
            self.show_status('Defaults previewed. Close or Esc discards them.')
        else:
            self.show_status('Preview applied. Close or Esc discards it.')

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        await self.submit(event.button.id.removeprefix('settings-'))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()  # settings Enter must never bubble into mood generation

    def action_close(self) -> None:
        if self.owner.preferences != self.baseline:
            self.owner.apply_preferences(self.baseline)
        self.dismiss()
