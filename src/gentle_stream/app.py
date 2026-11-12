"""Interactive TUI: what is playing, a live spectrum, play/pause/next, and a mood prompt.

The player is usually the background player (`client.PlayerClient`), so `b` leaves with the music still playing and
`q` stops it; an in-process `Player` works the same way, except that it always stops with the app.

The mood is interpreted by Gentle Shell in a thread worker while the music keeps playing; the new style starts at
the next bar. Everything shown comes from `player.now`, i.e. the bar that is audible right now. What is audible can
be saved under a name and applied later, instantly, without asking Gentle Shell again.
"""
from __future__ import annotations

from collections.abc import Callable
import time

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.markup import escape
from textual.containers import Horizontal, HorizontalScroll, Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Footer, Header, Input, OptionList, Select, Static, Tab, Tabs
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from .appearance import Preferences, PreferenceStore
from .appearance_ui import SettingsScreen
from .visualizer import Spectrum
from .volume_meter import render_compact_volume_meter, render_volume_meter
from .bridge import GentleShell, InterpretError, Usage
from .client import PlayerError
from .player import Player
from .playlist_library import PlaylistLibrary
from .playlists import default_playlist
from .presets import NAME_MAX, Preset, PresetError, PresetStore, normalize_name
from .style import Style

DEFAULT_MODEL_LABEL = "Gentle Shell default"

# gentlemanprogramming.com palette
GENTLEMAN_THEME = Theme(
    name="gentleman",
    primary="#ff76c3",        # rose
    secondary="#f095c8",
    accent="#ffb1dd",         # pink
    foreground="#f6eff3",
    background="#140a12",
    surface="#241822",
    panel="#342230",
    success="#b4e7c7",
    warning="#f2b86d",        # peach
    error="#ff718f",
    dark=True,
    variables={
        "text-muted": "#a78e9b",
        "border": "#ff76c3",
        "border-blurred": "#342230",
        "block-cursor-foreground": "#140a12",
        "block-cursor-background": "#ff76c3",
        "block-cursor-blurred-background": "#342230",
        "block-hover-background": "#342230",
        "input-cursor-background": "#ff76c3",
        "input-cursor-foreground": "#140a12",
        "input-selection-background": "#ff76c3 30%",
        "footer-background": "#1a1218",
        "footer-key-foreground": "#ff76c3",
        "footer-description-foreground": "#a78e9b",
        "scrollbar": "#342230",
        "scrollbar-hover": "#76616b",
        "scrollbar-active": "#ff76c3",
        "scrollbar-background": "#1a1218",
    },
)
def model_label(model: str | None) -> str:
    return model or DEFAULT_MODEL_LABEL


class ModelPicker(ModalScreen[str | None]):
    """Choose the model Gentle Shell uses to interpret moods."""

    BINDINGS = [Binding("escape", "dismiss_picker", "Cancel")]
    DEFAULT_CSS = """
    ModelPicker { align: center middle; }
    ModelPicker > Vertical { width: 60; max-width: 100%; height: auto; max-height: 80%;
                             border: round $primary; padding: 1 2; background: $surface; }
    ModelPicker .heading { color: $foreground; text-style: bold; }
    ModelPicker #model-description { color: $text-muted; }
    ModelPicker #picker-status { color: $accent; margin-top: 1; }
    ModelPicker OptionList, ModelPicker OptionList:focus { height: auto; max-height: 20; border: none;
                                                          background: $surface; }
    """

    def __init__(self, shell: GentleShell):
        super().__init__()
        self.shell = shell

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Choose a mood model", classes="heading")
            yield Static("For future moods · music keeps playing", id="model-description")
            yield Static("Loading models…", id="picker-status")
            yield OptionList(Option(DEFAULT_MODEL_LABEL, id=""), id="models")

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self._load()

    @work(thread=True, exclusive=True)
    def _load(self) -> None:
        try:
            models = self.shell.list_models()
        except InterpretError as e:
            self.app.call_from_thread(self.query_one("#picker-status", Static).update, f"Could not list models: {escape(str(e))}")
            return
        self.app.call_from_thread(self._show, models)

    def _show(self, models: list[str]) -> None:
        options = self.query_one(OptionList)
        options.add_options([Option(Text(m), id=m) for m in models])
        options.highlighted = 0
        self.query_one("#picker-status", Static).update(Text(f"{len(models)} models · current: {model_label(self.shell.model)}"))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id or None)

    def action_dismiss_picker(self) -> None:
        self.dismiss(self.shell.model)


class SaveDialog(ModalScreen[str | None]):
    """Name the style that is audible now; Enter saves, Esc cancels."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    SaveDialog { align: center middle; }
    SaveDialog > Vertical { width: 60; max-width: 100%; height: auto; max-height: 100%; overflow-y: auto;
                            border: round $primary; padding: 1 2; background: $surface; }
    SaveDialog .heading { color: $foreground; text-style: bold; margin-bottom: 1; }
    SaveDialog .field-label { color: $accent; }
    SaveDialog #save-submit { height: 1; min-height: 1; border: none; background: $primary;
                             color: $background; text-style: bold; margin-top: 1; }
    SaveDialog #save-submit:focus { text-style: bold; }
    SaveDialog #save-playlist, SaveDialog #save-playlist-label { display: none; }
    SaveDialog #save-status { color: $text-muted; margin-top: 1; }
    SaveDialog Input { border: round $panel; background: $surface; }
    SaveDialog Input:focus { border: round $primary; }
    """

    def __init__(self, name: str, library: PlaylistLibrary | None = None,
                 save: Callable[[str | None, str], None] | None = None):
        super().__init__()
        self.initial, self.save = name, save
        self.groups = {f"group-{i}": p.name for i, p in enumerate(library.list())} if library else {}
        self.error = library.error if library else None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Save what is playing", classes="heading")
            yield Static("Song name", id="save-name-label", classes="field-label")
            yield Input(self.initial, placeholder="Song name", max_length=NAME_MAX, id="save-name")
            yield Static("Destination", id="save-destination-label", classes="field-label")
            yield Select([(Text("Saved prompts (legacy)"), "legacy"), (Text("New playlist…"), "new"),
                          *[(Text(name), key) for key, name in self.groups.items()]],
                         value="legacy", allow_blank=False, id="save-destination")
            yield Static("Playlist name", id="save-playlist-label", classes="field-label")
            yield Input(placeholder="New playlist name", max_length=NAME_MAX, id="save-playlist")
            yield Button("Save (Enter)", id="save-submit")
            yield Static(Text(self.error or "Enter opens · ↑↓ choose · Enter selects · Enter again saves · Esc cancels"),
                         id="save-status")

    def on_mount(self) -> None:
        self.query_one("#save-name", Input).focus()

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        is_new = event.value == "new"
        playlist_name = self.query_one("#save-playlist", Input)
        playlist_name.display = is_new
        self.query_one("#save-playlist-label", Static).display = is_new
        if is_new:
            playlist_name.focus()
        elif event.value in self.groups:
            # After choosing an existing group, Enter should use the dialog's
            # Save action rather than reopening the focused Select dropdown.
            self.query_one("#save-submit", Button).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()  # otherwise it bubbles to the app and is taken as a mood
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._save()

    def _save(self) -> None:
        try:
            name = normalize_name(self.query_one("#save-name", Input).value)
            destination = self.query_one("#save-destination", Select).value
            group = (normalize_name(self.query_one("#save-playlist", Input).value) if destination == "new"
                     else self.groups.get(destination))
            if self.save:
                self.save(group, name)
            self.dismiss(name)
        except PresetError as e:
            self.query_one("#save-status", Static).update(Text(f"Could not save: {e}"))

    def action_cancel(self) -> None:
        self.dismiss(None)


class PresetPicker(ModalScreen[Preset | None]):
    """Browse saved and built-in generative styles; only user saves can be deleted."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("d,delete", "delete", "Delete"),
        Binding("backspace", "groups", "Groups"),
        Binding("c", "continuous", "Continuous from selection"),
        Binding("left", "switch_playlist('my-playlist')", "My playlist", priority=True),
        Binding("right", "switch_playlist('default-playlist')", "Default playlist", priority=True),
    ]
    DEFAULT_CSS = """
    PresetPicker { align: center middle; }
    PresetPicker > Vertical { width: 72; max-width: 100%; height: 80%; border: round $primary; padding: 1 2;
                              background: $surface; }
    PresetPicker .heading { color: $foreground; text-style: bold; }
    PresetPicker #picker-description { color: $text-muted; }
    PresetPicker Tabs { background: $surface; }
    PresetPicker #presets-breadcrumb { color: $accent; }
    PresetPicker #presets-status { color: $text-muted; margin-top: 1; }
    PresetPicker #picker-actions { height: 1; }
    PresetPicker Button { height: 1; min-height: 1; width: auto; border: none; padding: 0 1; }
    PresetPicker Button:focus { text-style: bold reverse; }
    PresetPicker OptionList, PresetPicker OptionList:focus { height: 1fr; min-height: 1; border: none;
                                                            background: $surface; }
    """
    EMPTY = "No saved prompts yet — press s to save what is playing"

    def __init__(self, store: PresetStore, library: PlaylistLibrary | None = None):
        super().__init__()
        self.store = store
        self.library = library if library is not None else PlaylistLibrary()
        self.is_default = False
        self.group: str | None = None
        self.at_groups = True
        self.groups: list[str | None] = []
        self.songs: list[Preset] = []
        self.selected_index = 0
        self.continuous = False

    @property
    def selection_name(self) -> str:
        return "Default playlist" if self.is_default else self.group or "Saved prompts"

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Playlists", classes="heading")
            yield Static("Generative styles · not recordings", id="picker-description")
            yield Tabs(Tab("My playlist", id="my-playlist"),
                       Tab("Default playlist", id="default-playlist"), active="my-playlist")
            yield Static(id="presets-breadcrumb")
            yield OptionList(id="presets")
            yield Static(id="presets-status")
            with Horizontal(id="picker-actions"):
                yield Button("< My playlists", id="playlist-back", tooltip="Back to groups (Backspace)")
                yield Button("Repeat from selection", id="playlist-loop", tooltip="Continuous from selected song (c)")

    def on_mount(self) -> None:
        self._populate()

    def action_switch_playlist(self, tab_id: str) -> None:
        self.query_one(Tabs).active = tab_id

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        self.is_default = event.tab.id == "default-playlist"
        self.at_groups = not self.is_default
        self.group = None
        self._populate()

    def _populate(self) -> None:
        options = self.query_one(OptionList)
        options.clear_options()
        error = None
        if self.is_default:
            self.songs = [Preset(s.name, s.mood, s, "") for s in
                          sorted(default_playlist(), key=lambda s: s.name.casefold())]
        elif self.at_groups:
            legacy = self.store.list()
            named = self.library.list()
            error = self.store.error or self.library.error
            self.groups = [None, *[p.name for p in named]]
            options.add_options([Option(Text(f"{name or 'Saved prompts'} ({len(legacy) if name is None else len(named[i - 1].songs)})"),
                                        id=f"group-{i}") for i, name in enumerate(self.groups)])
            self.songs = []
        else:
            self.songs = self.store.list() if self.group is None else self.library.list_songs(self.group)
            error = self.store.error if self.group is None else self.library.error
        if not self.at_groups:
            rows = []
            for i, song in enumerate(self.songs):
                prompt = Text(f"🌸  {song.name} — {song.mood or song.style.summary()}")
                if i + 1 < len(self.songs):
                    prompt.append("\n ")  # Keep a blank display row even when a description wraps.
                rows.append(Option(prompt, id=f"song-{i}"))
            options.add_options(rows)
        if options.option_count:
            options.highlighted = 0
        self.query_one("#presets-breadcrumb", Static).update(
            Text("My playlists" if self.at_groups else f"My playlists / {self.selection_name}"))
        self.query_one("#playlist-back", Button).display = not self.at_groups and not self.is_default
        self.query_one("#playlist-loop", Button).disabled = self.at_groups or not self.songs
        self._show_status(error)
        options.focus()

    def _show_status(self, error: str | None = None) -> None:
        if error:
            text = f"[$error]{escape(error)}[/]"
        elif self.is_default:
            text = "Click / Enter to apply · c repeat from selection · read-only · Esc closes"
        elif self.at_groups:
            text = "Click / Enter opens a group · Esc closes"
            if not self.store.list() and len(self.groups) == 1:
                text = self.EMPTY + " · try Default playlist"
        elif self.query_one(OptionList).option_count:
            text = "Click / Enter to apply · c repeat · d deletes song · Backspace groups"
        else:
            text = self.EMPTY + " · try Default playlist"
        self.query_one("#presets-status", Static).update(text + " · Left/Right switch playlists")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if self.at_groups:
            self.group = self.groups[event.option_index]
            self.at_groups = False
            self._populate()
            return
        self._choose(event.option_index)

    def _choose(self, index: int, continuous: bool = False) -> None:
        self.selected_index, self.continuous = index, continuous
        self.dismiss(self.songs[index])

    def action_continuous(self) -> None:
        index = self.query_one(OptionList).highlighted
        if self.at_groups or not self.songs or index is None:
            self._show_status("Select a song inside a playlist first.")
            return
        self._choose(index, True)

    def action_groups(self) -> None:
        if not self.is_default:
            self.at_groups = True
            self._populate()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "playlist-back":
            self.action_groups()
        elif event.button.id == "playlist-loop":
            self.action_continuous()

    def action_delete(self) -> None:
        if self.is_default or self.at_groups:
            return
        options = self.query_one(OptionList)
        if options.highlighted is None:
            return
        song = self.songs[options.highlighted]
        try:
            if self.group is None:
                self.store.delete(song.name)
            else:
                self.library.delete(self.group, song.name)
        except PresetError as e:
            self._show_status(str(e))
            return
        self._populate()

    def action_cancel(self) -> None:
        self.dismiss(None)


class StreamApp(App):
    TITLE = "gentle-stream"
    SUB_TITLE = "real-time generative music"
    AUTO_FOCUS = None
    CSS = """
    Screen { background: $background; }
    Header { background: #1a1218; color: $primary; }
    #panel { padding: 1 2; margin: 1 1 0 1; height: 1fr; border: round $panel; background: $surface; }
    #title-row { height: 1; }
    #title { text-style: bold; color: $primary; width: 1fr; }
    #playlist { height: 1; min-height: 1; width: 12; border: none; background: $panel; color: $primary; }
    #playlist:focus { text-style: bold reverse; }
    #metadata { height: 1; }
    #details, #mood-line { width: 1fr; }
    #details, #model { color: $text-muted; }
    #mood-line { color: $accent; text-style: italic; }
    #spectrum { height: 1fr; min-height: 5; margin: 1 0; }
    #state { color: $text-muted; }
    #status { margin-top: 1; }
    #commands { height: 1; margin: 0 1; overflow: hidden; background: #000000; }
    #commands-left, #commands-right { width: 1fr; height: 1fr; overflow-x: auto; overflow-y: hidden;
                                      scrollbar-size-horizontal: 0; align-vertical: bottom; }
    #commands-right { align-horizontal: right; }
    #transport { width: 15; height: 1fr; align-vertical: bottom; }
    #volume-meter { width: 13; min-width: 13; height: 1; background: #000000; overflow: hidden; }
    #transport Button { width: 5; }
    #commands Button { height: 1; min-height: 1; min-width: 5; width: auto; border: none;
                       padding: 0 1; background: #000000; color: $primary; }
    #commands Button:focus { text-style: bold; color: $foreground; }
    #commands #play.paused, #commands #play.paused:focus { color: #00a6ed; }
    #commands #model-command { min-width: 13; }
    #commands #continuous { min-width: 7; }
    Screen.compact #panel { padding: 0 1; margin-top: 0; }
    Screen.compact #commands, Screen.compact #volume-meter { height: 1; }
    Screen.compact #spectrum { margin: 0; }
    Screen.compact #details, Screen.compact #mood-line, Screen.compact #state,
    Screen.compact #model, Screen.compact #status { height: 1; }
    Screen.compact #status { margin-top: 0; }
    #mood { margin: 0 1 1 1; border: round $panel; background: $surface; }
    #mood:focus { border: round $primary; }
    """
    BINDINGS = [
        Binding("f2", "settings", "Settings", priority=True),
        Binding("f4", "panic", "Panic restart", priority=True),
        Binding("space", "toggle", "Play/Pause"),
        Binding("n", "next", "Next"),
        Binding("slash", "focus_mood", "Mood"),
        Binding("m", "pick_model", "Model"),
        Binding("s", "save_preset", "Save"),
        Binding("p", "pick_preset", "Playlist"),
        Binding("c", "continuous", "Repeat list"),
        Binding("plus,equals_sign", "volume(5)", "Vol +5"),
        Binding("minus", "volume(-5)", "Vol -5"),
        Binding("up", "volume(1)", "Vol +1"),
        Binding("down", "volume(-1)", "Vol -1"),
        Binding("escape", "leave_mood", show=False),
        Binding("b", "back", "Back (keeps playing)"),
        Binding("q", "quit", "Quit (stops music)"),
    ]

    def __init__(self, player: Player, shell: GentleShell, initial_mood: str | None = None,
                 presets: PresetStore | None = None, preference_store: PreferenceStore | None = None):
        super().__init__()
        self.player, self.shell, self.initial_mood = player, shell, initial_mood
        self.presets = presets if presets is not None else PresetStore()
        self.library = PlaylistLibrary()
        self.preference_store = preference_store if preference_store is not None else PreferenceStore()
        self.preferences = self.preference_store.load()
        self._ui_timer = None
        self._selection: tuple[list[Style], int, str] | None = None
        self._buttons: dict[str, Button] = {}
        self.session_usage: Usage | None = None  # tokens and cost of every successful interpretation so far
        self._keep_playing = False                # `b` leaves the music playing, `q` stops it
        self.register_theme(GENTLEMAN_THEME)
        self.theme = GENTLEMAN_THEME.name

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="panel"):
            with Horizontal(id="title-row"):
                yield Static(id="title")
                yield Button("Playlist", id="playlist")
            with Horizontal(id="metadata"):
                yield Static(id="details")
                yield Static(id="mood-line")
            yield Spectrum(self.player, id="spectrum")
            yield Static(id="state")
            yield Static(id="model")
            yield Static("Press / to describe a mood.", id="status")
        with Horizontal(id="commands"):
            with HorizontalScroll(id="commands-left"):
                yield Button("Panic", id="panic", tooltip="Hard cut and restart current track (F4); no AI call")
                yield Button("💾", id="save-command", tooltip="Save audible song (s)")
                yield Button("↻ off", id="continuous", tooltip="Continuous playlist from selection (c)")
                yield Button("✎", id="mood-command", tooltip="Describe a mood (/); Enter sends, Esc leaves")
                yield Button("☷", id="playlist-command", tooltip="Browse playlists (p)")
            with Horizontal(id="transport"):
                yield Button("⏮", id="previous", tooltip="Previous song in playlist (wraps to last)")
                yield Button("⏸", id="play", tooltip="Play / pause (Space)")
                yield Button("⏭", id="next", tooltip="Next piece (n); next song when continuous")
            with HorizontalScroll(id="commands-right"):
                yield Button("-", id="quieter", tooltip="Volume down 5% (-); Down: 1%")
                yield Static(id="volume-meter")
                yield Button("+", id="louder", tooltip="Volume up 5% (+); Up: 1%")
                yield Button("⚙  default", id="model-command", tooltip="Settings (F2) · direct model picker (m)")
        yield Input(placeholder="Describe a feeling, a concept, a place… then Enter (Esc to leave)", id="mood")
        yield Footer()

    def on_mount(self) -> None:
        # keep references: the timer also fires while a modal is on top or the app is shutting down,
        # when queries would search another screen
        self._w = {name: self.query_one(f"#{name}") for name in ("title", "details", "mood-line", "spectrum",
                                                                 "state", "model", "status", "volume-meter")}
        self._buttons = {b.id: b for b in self.query("#commands Button")}
        # Textual ignores mouse and Enter presses while a button's active-effect
        # class persists; transport commands must accept consecutive presses.
        for name in ("previous", "play", "next", "panic"):
            self._buttons[name].active_effect_duration = 0
        self._root = self.screen
        self._mood_input = self.query_one('#mood', Input)
        self._footer = self.query_one(Footer)
        self._commands = self.query_one('#commands')
        self._panel = self.query_one('#panel')
        self._metadata = self.query_one('#metadata')
        self.apply_preferences(self.preferences)
        self.player.start()
        self._show_model()
        self._tick()
        if self.initial_mood:
            self._request(self.initial_mood)

    def on_resize(self, event: events.Resize) -> None:
        if self.screen_stack:
            self.screen_stack[0].set_class(self.preferences.compact or event.size.height <= 24, "compact")

    def on_unmount(self) -> None:
        self.shell.cancel()  # a running gentle-shell would otherwise keep the process alive after quit
        if self._keep_playing:
            self.player.detach()
        else:
            self.player.close()

    # ------------------------------------------------------------ display
    def _tick(self) -> None:
        w = self._w
        try:
            w["spectrum"].tick()   # first: polling a background player also refreshes what is shown below
        except ConnectionError:    # the background player was stopped elsewhere (`gentle-stream ctl stop`)
            self.exit(message="gentle-stream: the music was stopped")
            return
        now, p = self.player.now, self.player
        w["title"].update(Text(f"♪ {now.style.name}"))
        details = Text(now.style.summary())
        mood = Text(f"“{now.style.mood}”" if now.style.mood else "")
        w["details"].update(details)
        w["details"].tooltip = details
        w["mood-line"].update(mood)
        w["mood-line"].tooltip = mood
        state = "[b $success]▶ Playing[/]" if p.playing else "[b $warning]⏸ Paused[/]"
        audible_name, audible_index = getattr(now, "playlist_name", None), getattr(now, "playlist_index", None)
        audible = f" · {escape(audible_name)} #{audible_index + 1}" if isinstance(audible_name, str) and isinstance(audible_index, int) else ""
        w["state"].update(f"{state} · piece {now.piece} · bar {now.bar + 1}{audible}")
        meter = render_compact_volume_meter if self._root.has_class("compact") else render_volume_meter
        w["volume-meter"].update(meter(round(p.volume * 100), self.preferences.accent))
        self._buttons["play"].label = "⏸" if p.playing else "▶"
        self._buttons["play"].set_class(not p.playing, "paused")
        gap = ' ' * self.preferences.icon_gap
        self._buttons["continuous"].label = f"↻{gap}{'on' if p.continuous is True else 'off'}"

    def _show_model(self) -> None:
        session = f" · session: {self.session_usage.format()}" if self.session_usage else ""
        self._w["model"].update(f"model: {escape(model_label(self.shell.model))}{session}")
        self._w["model"].tooltip = Text(f"model: {model_label(self.shell.model)}{session}")
        label = self.shell.model or "default"
        short = label if len(label) <= 22 else label[:10] + "…" + label[-11:]
        button_label = f"⚙{' ' * self.preferences.icon_gap}{short}"
        button = self._buttons["model-command"]
        button.label = Text(button_label)
        button.styles.min_width = max(13, len(button_label) + 2)  # Include button padding; scroll if narrow.
        button.tooltip = Text(f"Settings (F2) · direct model picker (m) · {model_label(self.shell.model)}")

    def _status(self, text: str) -> None:
        self._w["status"].update(text)
        self._w["status"].tooltip = self._w["status"].render()

    def apply_preferences(self, preferences: Preferences, *, persist: bool = False) -> None:
        """The single UI-only apply path. Validate/save before changing any widget or timer."""
        preferences.validate()
        if persist:
            self.preference_store.save(preferences)
        self.preferences = preferences
        if not hasattr(self, '_root'):
            return
        p = preferences
        background = {'black': '#000000', 'charcoal': '#202020'}.get(p.theme, p.background)
        # Textual 8.2.8 doesn't invalidate CSS when the assigned theme name is unchanged.
        # Alternate two registered names, bounding the registry while forcing live refresh.
        theme_name = 'appearance-b' if self.theme == 'appearance-a' else 'appearance-a'
        theme = Theme(name=theme_name, primary=p.accent, secondary='#f095c8', accent=p.accent,
                      foreground='#f6eff3', background=background, surface=background,
                      panel='#342230', success='#b4e7c7', warning='#f2b86d', error='#ff718f', dark=True)
        if p == Preferences():
            self.theme = GENTLEMAN_THEME.name
        else:
            self.register_theme(theme)
            self.theme = theme.name
        self._root.set_class(p.compact or self.size.height <= 24, 'compact')
        # Keep the user-selected accent exact; Textual's theme-derived $primary may
        # differ by one channel after its internal palette conversion.
        self._w['title'].styles.color = p.accent
        self._metadata.display = p.show_details
        for name in ('details', 'mood-line'):
            self._w[name].display = p.show_details
        self._w['model'].display = p.show_model
        spectrum = self._w['spectrum']
        spectrum.configure(p)
        spectrum.styles.min_height = p.height
        spectrum.display = p.show_visualizer
        self._panel.styles.height = '1fr' if p.show_visualizer else 'auto'
        self._commands.display = p.show_controls
        self._mood_input.display = p.show_input
        self._footer.display = p.show_footer
        if self._ui_timer is not None:
            self._ui_timer.stop()
        self._ui_timer = self.set_interval(1 / p.refresh_hz, self._tick)
        self._show_model()

    def action_settings(self) -> None:
        if not isinstance(self.screen, SettingsScreen):
            self.push_screen(SettingsScreen(self))

    def get_system_commands(self, screen):
        from textual.app import SystemCommand
        yield from super().get_system_commands(screen)
        yield SystemCommand('Settings', 'Appearance and mood model (F2)', self.action_settings)
        yield SystemCommand('Panic restart', 'Hard cut and restart current track, no AI (F4)', self.action_panic)

    # ------------------------------------------------------------ actions
    def action_back(self) -> None:
        self._keep_playing = True
        self.exit()

    def _control(self, action: Callable[[], None]) -> bool:
        """Both keyboard and buttons report rejected controls without claiming success."""
        try:
            action()
        except (ValueError, PlayerError) as e:
            self._tick()  # A failed reopen may have stopped playback; never leave a stale Playing label.
            self._status(f"Could not control playback: {escape(str(e))}")
            return False
        self._tick()
        return True

    def action_panic(self) -> None:
        self.workers.cancel_group(self, "interpret")
        if self._control(self.player.panic):
            self._status("Current track restarted from the beginning · same settings · no AI call.")

    def action_toggle(self) -> None:
        self._control(self.player.toggle)

    def action_next(self) -> None:
        if self._control(self.player.next):
            self._status("Next playlist song." if self.player.continuous is True else "Next piece, same mood.")

    def action_previous(self) -> None:
        if self.player.playlist_index is None:
            if self._selection is None:
                self._status("Select a song in Playlist (p) before using Previous.")
                return
            styles, index, name = self._selection
            action = lambda: self.player.previous(styles, start_index=index, name=name)
        else:
            action = self.player.previous
        if self._control(action):
            self._status("Previous playlist song.")

    def action_volume(self, percent: int) -> None:
        """Change the volume by whole percentage points, so repeated steps never drift."""
        self._control(lambda: self.player.set_volume((round(self.player.volume * 100) + percent) / 100))

    def action_focus_mood(self) -> None:
        self._mood_input.display = True
        self._mood_input.focus()

    def action_leave_mood(self) -> None:
        self.set_focus(None)

    def action_pick_model(self) -> None:
        def chosen(model: str | None) -> None:
            self.shell.model = model
            self._show_model()

        self.push_screen(ModelPicker(self.shell), chosen)

    # ------------------------------------------------------------ saved prompts
    def action_save_preset(self) -> None:
        style = self.player.now.style  # what is audible, captured before the dialog opens

        def save(group: str | None, name: str) -> None:
            preset, replaced = (self.presets.save(name, style) if group is None
                                else self.library.save(group, name, style))
            self._status(f"{'Replaced' if replaced else 'Saved'} “{escape(preset.name)}”")

        self.push_screen(SaveDialog(style.name, self.library, save))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {"playlist": self.action_pick_preset, "play": self.action_toggle,
                   "next": self.action_next, "previous": self.action_previous, "panic": self.action_panic,
                   "mood-command": self.action_focus_mood,
                   "model-command": self.action_settings, "save-command": self.action_save_preset,
                   "playlist-command": self.action_pick_preset, "continuous": self.action_continuous,
                   "quieter": lambda: self.action_volume(-5), "louder": lambda: self.action_volume(5)}
        if action := actions.get(event.button.id):
            action()

    def action_pick_preset(self) -> None:
        picker = PresetPicker(self.presets, self.library)

        def chosen(preset: Preset | None) -> None:
            if preset is None:
                return
            self.workers.cancel_group(self, "interpret")  # a late interpretation must not override the choice
            selection = ([p.style for p in picker.songs], picker.selected_index, picker.selection_name)
            if picker.continuous:
                self._selection = selection
                self._start_continuous()
                return
            try:
                self.player.set_style(preset.style)
            except (ValueError, PlayerError) as e:
                self._status(f"[$error]Could not apply style:[/] {escape(str(e))}")
                return
            self._selection = selection
            source = "default" if picker.is_default else "saved"
            self._status(f"Up next: [b]{escape(preset.name)}[/b] ({source})")

        self.push_screen(picker, chosen)

    def _start_continuous(self) -> None:
        if self._selection is None:
            self._status("Select a song in Playlist (p), then enable repeat (c).")
            return
        styles, index, name = self._selection
        self.workers.cancel_group(self, "interpret")
        try:
            self.player.set_playlist(styles, start_index=index, name=name, continuous=True)
            self.player.play()
        except (ValueError, PlayerError) as e:
            self._status(f"Could not enable continuous playback: {escape(str(e))}")
            return
        self._status(f"Continuous from {escape(styles[index].name)} · {escape(name)}")
        self._tick()

    def action_continuous(self) -> None:
        if self.player.continuous is not True:
            self._start_continuous()
            return
        try:
            self.player.set_continuous(False)
        except (ValueError, PlayerError) as e:
            self._status(f"Could not disable continuous playback: {escape(str(e))}")
            return
        self._status("Continuous off · current audio continues.")
        self._tick()

    # ------------------------------------------------------------ mood interpretation
    def on_input_submitted(self, event: Input.Submitted) -> None:
        mood = event.value.strip()
        event.input.value = ""
        self.set_focus(None)
        if mood:
            self._request(mood)

    def _request(self, mood: str) -> None:
        self._status(f"Interpreting “{escape(mood)}” with {escape(model_label(self.shell.model))}…")
        self._interpret(mood)

    @work(thread=True, exclusive=True, group="interpret")
    def _interpret(self, mood: str) -> None:
        worker = get_current_worker()
        try:
            style, usage = self.shell.interpret_with_usage(mood, self.player.style)
        except InterpretError as e:
            if not worker.is_cancelled:
                self.call_from_thread(self._status, f"[$error]Could not interpret the mood:[/] {escape(str(e))}")
            return
        try:
            with self.player.stage_style(style) as commit:
                while not worker.is_cancelled:
                    if self.call_from_thread(self._apply_interpretation, worker, style, usage, commit):
                        break
                    time.sleep(.01)  # retry a busy render without blocking input
        except (ValueError, PlayerError, ConnectionError) as e:
            if not worker.is_cancelled:
                self.call_from_thread(self._status, f"[$error]Could not apply style:[/] {escape(str(e))}")

    def _apply_interpretation(self, worker, style: Style, usage: Usage | None, commit) -> bool:
        # UI actions (including Panic's cancellation) and application share one
        # event-loop turn. A worker cannot pass a check then apply after Panic.
        if worker.is_cancelled:
            return True
        try:
            committed = commit()  # never waits for preparation or a render lock
        except (ValueError, PlayerError, ConnectionError) as e:
            self._status(f"[$error]Could not apply style:[/] {escape(str(e))}")
            return True
        if committed:
            self._interpreted(style, usage)
        return committed is not False

    def _interpreted(self, style: Style, usage: Usage | None) -> None:
        self._selection = None  # an ordinary mood exits playlist navigation as well as rotation
        cost = f" · {usage.format()}" if usage else ""
        self._status(f"Up next: [b]{escape(style.name)}[/b] · {escape(style.summary())}{cost}")
        if usage:
            self.session_usage = usage if self.session_usage is None else self.session_usage + usage
            self._show_model()
