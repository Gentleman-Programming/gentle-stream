"""Native, keyboard-accessible settings with explicit preview and persistence."""
from dataclasses import asdict

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, Switch

from .appearance import Preferences, PreferenceError

CHOICES = {'style': ('gentle', 'segmented', 'dots'), 'palette': ('pink', 'analyzer', 'mono'),
           'theme': ('gentle', 'black', 'charcoal')}
LABELS = {'height': 'Minimum bar height (5–30 rows)', 'bands': 'Bands (8–128)',
          'refresh_hz': 'Refresh (5–30 Hz)', 'smoothing': 'Smoothing (0–1)',
          'peak_hold': 'Peak hold (0–3 seconds)', 'icon_gap': 'Icon-label gap (1–3 cells)',
          'background': 'Custom background (#RRGGBB, gentle theme)', 'accent': 'Accent (#RRGGBB)',
          'show_visualizer': 'Show visualizer', 'show_controls': 'Show controls',
          'show_input': 'Show mood input', 'show_footer': 'Show footer',
          'show_details': 'Show track details', 'show_model': 'Show model line',
          'compact': 'Compact layout'}


class SettingsScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]
    DEFAULT_CSS = '''
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical { width: 68; max-width: 100%; height: 90%;
        border: round $primary; padding: 1 2; background: $surface; }
    SettingsScreen VerticalScroll { height: 1fr; }
    SettingsScreen .setting-row { height: auto; min-height: 3; }
    SettingsScreen .setting-row Static { width: 1fr; padding-top: 1; }
    SettingsScreen Input, SettingsScreen Select { width: 25; }
    SettingsScreen Switch { width: auto; }
    SettingsScreen #settings-actions { height: auto; }
    SettingsScreen Button { min-width: 8; width: 1fr; height: 3; }
    SettingsScreen #settings-status { height: auto; min-height: 2; }
    SettingsScreen #settings-title { color: $foreground; text-style: bold; }
    SettingsScreen #settings-description { color: $text-muted; }
    SettingsScreen #settings-model { background: $panel; color: $foreground; border: none; }
    SettingsScreen #settings-actions Button { background: $panel; color: $foreground; border: none; }
    SettingsScreen #settings-actions #settings-save { background: $primary; color: $background;
                                                      text-style: bold; }
    SettingsScreen Button:focus { background: $primary; color: $background; text-style: bold; }
    '''

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.baseline = owner.preferences

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static('Settings · Appearance and model', id='settings-title')
            yield Static('Visual only — music is unchanged. Fonts belong to your terminal.',
                         id='settings-description')
            yield Button('Choose mood model…', id='settings-model')
            with VerticalScroll():
                for key, value in asdict(self.baseline).items():
                    with Horizontal(classes='setting-row'):
                        yield Static(LABELS.get(key, key.title()))
                        if key in CHOICES:
                            yield Select([(s.title(), s) for s in CHOICES[key]], value=value,
                                         allow_blank=False, id='pref-' + key)
                        elif type(value) is bool:
                            yield Switch(value, id='pref-' + key)
                        else:
                            yield Input(str(value), id='pref-' + key)
            yield Static(Text(self.owner.preference_store.error or
                              'Preview is temporary. Save keeps changes. Cancel restores the last save.'),
                         id='settings-status')
            with Horizontal(id='settings-actions'):
                for label, key in [('Preview', 'preview'), ('Save', 'save'),
                                   ('Defaults', 'defaults'), ('Cancel', 'cancel')]:
                    yield Button(label, id='settings-' + key)

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

    async def submit(self, action: str) -> None:
        try:
            if action == 'cancel':
                self.action_cancel()
                return
            if action == 'model':
                self.owner.action_pick_model()
                return
            preferences = Preferences() if action == 'defaults' else self.read_preferences()
            self.owner.apply_preferences(preferences, persist=action == 'save')
            if action == 'save':
                self.baseline = preferences
                message = 'Appearance saved.'
            elif action == 'defaults':
                for key, value in asdict(preferences).items():
                    widget = self.query_one('#pref-' + key)
                    widget.value = value if isinstance(widget, (Select, Switch)) else str(value)
                message = 'Defaults preview — Save to keep; Cancel restores.'
            else:
                message = 'Preview only — Save to keep; Cancel restores.'
            self.query_one('#settings-status', Static).update(Text(message))
        except PreferenceError as e:
            self.query_one('#settings-status', Static).update(Text(str(e)))

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        await self.submit(event.button.id.removeprefix('settings-'))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()  # settings Enter must never bubble into mood generation

    def action_cancel(self) -> None:
        self.owner.apply_preferences(self.baseline)
        self.dismiss()
