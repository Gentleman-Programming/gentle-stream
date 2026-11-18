"""Native, keyboard-accessible settings with explicit preview and persistence."""
from dataclasses import asdict, replace

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Select, Static, Switch

from .appearance import Preferences, PreferenceError
from .appearance_preview import AppearancePreview

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


class ThemeSelect(Select[str]):
    """Preview the cursor without committing the dropdown's selected value."""

    class Preview(Message):
        def __init__(self, theme: str):
            super().__init__()
            self.theme = theme

    def on_mount(self) -> None:
        # Select's overlay consumes OptionHighlighted messages. Observe its public
        # cursor instead; keep Select's own Enter, search and Escape behavior.
        self.watch(self.query_one(OptionList), 'highlighted', self.preview_highlight,
                   init=False)
        self.watch(self, 'expanded', self.preview_selection, init=False)

    def preview_highlight(self, index: int | None) -> None:
        if self.expanded and index is not None:
            self.post_message(self.Preview(CHOICES['theme'][index]))

    def preview_selection(self, expanded: bool) -> None:
        if not expanded and isinstance(self.value, str):
            # Enter has already updated value; Escape/blur leave it untouched.
            self.post_message(self.Preview(self.value))


class SettingsScreen(ModalScreen):
    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]
    DEFAULT_CSS = '''
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical { width: 132; max-width: 100%; height: 96%;
        border: round $primary; padding: 0 1; background: $surface; }
    SettingsScreen #settings-body { height: 1fr; layout: horizontal; }
    SettingsScreen #settings-form { width: 62; height: 1fr; }
    SettingsScreen.narrow #settings-body { layout: vertical; }
    SettingsScreen.narrow #settings-form { width: 100%; height: auto; overflow-y: hidden; }
    SettingsScreen.narrow AppearancePreview { width: 100%; max-width: 100%; }
    SettingsScreen .setting-row { height: auto; min-height: 3; }
    SettingsScreen .setting-row Static { width: 1fr; padding-top: 1; }
    SettingsScreen Input, SettingsScreen Select { width: 25; }
    SettingsScreen Switch { width: auto; }
    SettingsScreen #settings-actions { height: auto; }
    SettingsScreen Button { min-width: 8; width: 1fr; height: 3; }
    SettingsScreen #settings-status { height: auto; min-height: 1; }
    SettingsScreen #settings-title { color: $foreground; text-style: bold; }
    SettingsScreen #settings-description { color: $text-muted; }
    SettingsScreen #settings-model { height: 1; min-height: 1; background: $panel; color: $foreground; border: none; }
    SettingsScreen #settings-actions Button { background: $panel; color: $foreground; border: none; }
    SettingsScreen #settings-actions #settings-save { background: $primary; color: $background;
                                                      text-style: bold; }
    SettingsScreen Button:focus { background: $primary; color: $background; text-style: bold; }
    SettingsScreen.short > Vertical { height: 100%; padding: 0; }
    SettingsScreen.short #settings-title, SettingsScreen.short #settings-description { display: none; }
    SettingsScreen.short #settings-status { height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    SettingsScreen.short #settings-actions { height: 1; }
    SettingsScreen.short #settings-actions Button { height: 1; min-height: 1; padding: 0; }
    SettingsScreen.short Input, SettingsScreen.short Select { width: 18; }
    '''

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.baseline = owner.preferences
        self.draft = self.baseline
        self._ready = False
        self._preview: AppearancePreview | None = None
        self._preview_parts: tuple[Static, ...] = ()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static('Settings · Appearance and model', id='settings-title')
            yield Static('Visual only — music is unchanged. Fonts belong to your terminal.',
                         id='settings-description')
            yield Button('Choose mood model…', id='settings-model')
            with VerticalScroll(id='settings-body'):
                with VerticalScroll(id='settings-form'):
                    for key, value in asdict(self.baseline).items():
                        with Horizontal(classes='setting-row'):
                            yield Static(LABELS.get(key, key.title()))
                            if key in CHOICES:
                                selector = ThemeSelect if key == 'theme' else Select
                                yield selector([(s.title(), s) for s in CHOICES[key]], value=value,
                                               allow_blank=False, id='pref-' + key)
                            elif type(value) is bool:
                                yield Switch(value, id='pref-' + key)
                            else:
                                yield Input(str(value), id='pref-' + key)
                yield AppearancePreview(self.draft)
            yield Static(Text(self.owner.preference_store.error or
                              'Read-only player preview · Save applies; Cancel discards draft.'),
                         id='settings-status')
            with Horizontal(id='settings-actions'):
                for label, key in [('Preview', 'preview'), ('Save', 'save'),
                                   ('Defaults', 'defaults'), ('Cancel', 'cancel')]:
                    yield Button(label, id='settings-' + key)

    def on_mount(self) -> None:
        pane = self.query_one(AppearancePreview)
        self._preview = pane
        self._preview_parts = tuple(part for part in pane.children if isinstance(part, Static))
        self._layout_size()
        # Initial widget messages and bulk value changes must settle before validation.
        self.call_after_refresh(self.enable_draft)
        self.refresh_preview()

    def _layout_size(self) -> None:
        self.set_class(self.size.width < 110, 'narrow')
        self.set_class(self.size.height < 20, 'short')

    def on_resize(self) -> None:
        self._layout_size()

    def on_unmount(self) -> None:
        self._ready = False
        self._preview = None
        self._preview_parts = ()

    def preview_attached(self) -> bool:
        pane = self._preview
        # Screen.is_mounted remains true during child-first teardown. Check the
        # retained pane and each of its render targets before any preview query.
        return bool(self.is_attached and pane is not None and pane.is_attached
                    and self._preview_parts
                    and all(part.parent is pane and part.is_attached
                            for part in self._preview_parts))

    def enable_draft(self) -> None:
        self._ready = self.preview_attached()

    def refresh_preview(self) -> None:
        pane = self._preview
        if pane is not None and self.preview_attached():
            pane.refresh_snapshot(self.owner)

    def update_draft(self) -> None:
        if not self._ready or not self.preview_attached():
            return
        try:
            preferences = self.read_preferences()
        except PreferenceError as e:
            self.query_one('#settings-status', Static).update(Text(str(e)))
            return
        self.draft = preferences
        self.query_one(AppearancePreview).configure(preferences)

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
            if action == 'save':
                self.owner.apply_preferences(preferences, persist=True)
                self.baseline = preferences
                message = 'Appearance saved.'
            elif action == 'defaults':
                self._ready = False
                for key, value in asdict(preferences).items():
                    widget = self.query_one('#pref-' + key)
                    widget.value = value if isinstance(widget, (Select, Switch)) else str(value)
                self.call_after_refresh(self.enable_draft)
                message = 'Defaults preview — Save to keep; Cancel restores.'
            else:
                message = 'Preview only — Save to keep; Cancel restores.'
            self.draft = preferences
            self.query_one(AppearancePreview).configure(preferences)
            self.query_one('#settings-status', Static).update(Text(message))
        except PreferenceError as e:
            self.query_one('#settings-status', Static).update(Text(str(e)))

    def on_theme_select_preview(self, event: ThemeSelect.Preview) -> None:
        event.stop()
        self.preview_theme(event.theme)

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        self.update_draft()

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.update_draft()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        event.stop()
        self.update_draft()

    def preview_theme(self, theme: str) -> None:
        if self._ready and self.preview_attached():
            pane = self.query_one(AppearancePreview)
            if self.query_one(ThemeSelect).expanded:
                # Cursor overlay never commits the selected draft or invalid edits.
                pane.configure(replace(self.draft, theme=theme))
            else:
                self.update_draft()
                pane.configure(self.draft)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        await self.submit(event.button.id.removeprefix('settings-'))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()  # settings Enter must never bubble into mood generation

    def action_cancel(self) -> None:
        self._ready = False
        self._preview = None
        self._preview_parts = ()
        self.dismiss()
