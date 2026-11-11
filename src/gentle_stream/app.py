"""Interactive TUI: what is playing, a live spectrum, play/pause/next, and a mood prompt.

The player is usually the background player (`client.PlayerClient`), so `b` leaves with the music still playing and
`q` stops it; an in-process `Player` works the same way, except that it always stops with the app.

The mood is interpreted by Gentle Shell in a thread worker while the music keeps playing; the new style starts at
the next bar. Everything shown comes from `player.now`, i.e. the bar that is audible right now. What is audible can
be saved under a name and applied later, instantly, without asking Gentle Shell again.
"""
from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.markup import escape
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from .bridge import GentleShell, InterpretError, Usage
from .player import Player
from .playlist_library import PlaylistLibrary
from .playlists import default_playlist
from .presets import NAME_MAX, Preset, PresetError, PresetStore, normalize_name
from .style import Style

DEFAULT_MODEL_LABEL = "Gentle Shell default"
BLOCKS = " ▁▂▃▄▅▆▇█"

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
# spectrum rows, top to bottom: rose peaks over a muted base
SPECTRUM_COLORS = ("#ff76c3", "#f095c8", "#ffb1dd", "#a78e9b", "#76616b")


def model_label(model: str | None) -> str:
    return model or DEFAULT_MODEL_LABEL


class Spectrum(Static):
    """Block-character bars filling the widget, one column per log-spaced band, shaded from rose (top) to muted."""

    MIN_ROWS = 5

    def __init__(self, player: Player, **kw):
        super().__init__(**kw)
        self.player = player

    def tick(self) -> None:
        width = max(8, self.size.width or 48)
        rows_n = max(self.MIN_ROWS, self.size.height or self.MIN_ROWS)
        levels = self.player.spectrum(width) * rows_n * 8
        rows = []
        for i, r in enumerate(range(rows_n - 1, -1, -1)):    # i = 0 is the top row
            color = SPECTRUM_COLORS[min(len(SPECTRUM_COLORS) - 1, i * len(SPECTRUM_COLORS) // rows_n)]
            cell = (levels - r * 8).clip(0, 8).astype(int)
            rows.append(Text("".join(BLOCKS[c] for c in cell), style=color))
        self.update(Text("\n").join(rows))


class ModelPicker(ModalScreen[str | None]):
    """Choose the model Gentle Shell uses to interpret moods."""

    BINDINGS = [Binding("escape", "dismiss_picker", "Cancel")]
    DEFAULT_CSS = """
    ModelPicker { align: center middle; }
    ModelPicker > Vertical { width: 60; height: auto; max-height: 80%; border: round $primary; padding: 0 1;
                             background: $surface; }
    ModelPicker .heading { color: $primary; text-style: bold; }
    ModelPicker #picker-status { color: $text-muted; }
    ModelPicker OptionList, ModelPicker OptionList:focus { height: auto; max-height: 20; border: none;
                                                          background: $surface; }
    """

    def __init__(self, shell: GentleShell):
        super().__init__()
        self.shell = shell

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Model for mood interpretation", classes="heading")
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
        options.add_options([Option(m, id=m) for m in models])
        options.highlighted = 0
        self.query_one("#picker-status", Static).update(f"{len(models)} models · current: {model_label(self.shell.model)}")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id or None)

    def action_dismiss_picker(self) -> None:
        self.dismiss(self.shell.model)


class SaveDialog(ModalScreen[str | None]):
    """Name the style that is audible now; Enter saves, Esc cancels."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    SaveDialog { align: center middle; }
    SaveDialog > Vertical { width: 60; height: auto; border: round $primary; padding: 0 1; background: $surface; }
    SaveDialog .heading { color: $primary; text-style: bold; }
    SaveDialog #save-status { color: $text-muted; }
    SaveDialog Input { border: round $panel; background: $surface; }
    SaveDialog Input:focus { border: round $primary; }
    """

    def __init__(self, name: str):
        super().__init__()
        self.initial = name

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Save what is playing", classes="heading")
            yield Input(self.initial, placeholder="Name", max_length=NAME_MAX, id="save-name")
            yield Static("Enter to save · Esc to cancel", id="save-status")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()  # otherwise it bubbles to the app and is taken as a mood
        try:
            self.dismiss(normalize_name(event.value))
        except PresetError as e:
            self.query_one("#save-status", Static).update(f"[$error]{escape(str(e))}[/]")

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
    #title { text-style: bold; color: $primary; }
    #details, #model { color: $text-muted; }
    #mood-line { color: $accent; text-style: italic; }
    #spectrum { height: 1fr; min-height: 5; margin: 1 0; }
    #state { color: $text-muted; }
    #status { margin-top: 1; }
    #mood { dock: bottom; margin: 0 1 1 1; border: round $panel; background: $surface; }
    #mood:focus { border: round $primary; }
    """
    BINDINGS = [
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
                 presets: PresetStore | None = None):
        super().__init__()
        self.player, self.shell, self.initial_mood = player, shell, initial_mood
        self.presets = presets if presets is not None else PresetStore()
        self.session_usage: Usage | None = None  # tokens and cost of every successful interpretation so far
        self._keep_playing = False                # `b` leaves the music playing, `q` stops it
        self.register_theme(GENTLEMAN_THEME)
        self.theme = GENTLEMAN_THEME.name

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="panel"):
            yield Static(id="title")
            yield Static(id="details")
            yield Static(id="mood-line")
            yield Spectrum(self.player, id="spectrum")
            yield Static(id="state")
            yield Static(id="model")
            yield Static("Press / to describe a mood.", id="status")
        yield Input(placeholder="Describe a feeling, a concept, a place… then Enter (Esc to leave)", id="mood")
        yield Footer()

    def on_mount(self) -> None:
        # keep references: the timer also fires while a modal is on top or the app is shutting down,
        # when queries would search another screen
        self._w = {name: self.query_one(f"#{name}") for name in ("title", "details", "mood-line", "spectrum",
                                                                 "state", "model", "status")}
        self.player.start()
        self._show_model()
        self._tick()
        self.set_interval(1 / 15, self._tick)
        if self.initial_mood:
            self._request(self.initial_mood)

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
        w["title"].update(f"♪ {now.style.name}")
        w["details"].update(now.style.summary())
        w["mood-line"].update(f"“{now.style.mood}”" if now.style.mood else "")
        state = "[b $success]▶ Playing[/]" if p.playing else "[b $warning]⏸ Paused[/]"
        w["state"].update(f"{state} · piece {now.piece} · bar {now.bar + 1} · vol {round(p.volume * 100)}%")

    def _show_model(self) -> None:
        session = f" · session: {self.session_usage.format()}" if self.session_usage else ""
        self._w["model"].update(f"model: {escape(model_label(self.shell.model))}{session}")

    def _status(self, text: str) -> None:
        self._w["status"].update(text)

    # ------------------------------------------------------------ actions
    def action_back(self) -> None:
        self._keep_playing = True
        self.exit()

    def action_toggle(self) -> None:
        self.player.toggle()
        self._tick()

    def action_next(self) -> None:
        self.player.next()
        self._status("Next piece, same mood.")

    def action_volume(self, percent: int) -> None:
        """Change the volume by whole percentage points, so repeated steps never drift."""
        self.player.set_volume((round(self.player.volume * 100) + percent) / 100)
        self._tick()

    def action_focus_mood(self) -> None:
        self.query_one("#mood", Input).focus()

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

        def named(name: str | None) -> None:
            if name is None:
                return
            try:
                preset, replaced = self.presets.save(name, style)
            except PresetError as e:
                self._status(f"[$error]Could not save:[/] {escape(str(e))}")
                return
            self._status(f"{'Replaced' if replaced else 'Saved'} “{escape(preset.name)}”")

        self.push_screen(SaveDialog(style.name), named)

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
        self._status(f"Interpreting “{mood}” with {model_label(self.shell.model)}…")
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
        if worker.is_cancelled:
            return
        self.player.set_style(style)
        self.call_from_thread(self._interpreted, style, usage)

    def _interpreted(self, style: Style, usage: Usage | None) -> None:
        self._selection = None  # an ordinary mood exits playlist navigation as well as rotation
        cost = f" · {usage.format()}" if usage else ""
        self._status(f"Up next: [b]{escape(style.name)}[/b] · {escape(style.summary())}{cost}")
        if usage:
            self.session_usage = usage if self.session_usage is None else self.session_usage + usage
            self._show_model()
