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
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.markup import escape
from textual.containers import Horizontal, HorizontalScroll, Vertical
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Footer, Header, Input, OptionList, Select, Static, Tab, Tabs
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from .volume_meter import render_compact_volume_meter, render_volume_meter
from .bridge import GentleShell, InterpretError, Usage
from .client import PlayerError
from .player import Player
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
    """Saved prompts: Enter applies the highlighted one, d deletes it, Esc closes."""

    BINDINGS = [
        Binding("escape", "cancel", "Close"),
        Binding("d,delete", "delete", "Delete"),
    ]
    DEFAULT_CSS = """
    PresetPicker { align: center middle; }
    PresetPicker > Vertical { width: 72; height: auto; max-height: 80%; border: round $primary; padding: 0 1;
                              background: $surface; }
    PresetPicker .heading { color: $primary; text-style: bold; }
    PresetPicker #presets-status { color: $text-muted; }
    PresetPicker OptionList, PresetPicker OptionList:focus { height: auto; max-height: 20; border: none;
                                                            background: $surface; }
    """
    EMPTY = "No saved prompts yet — press s to save what is playing"

    def __init__(self, store: PresetStore):
        super().__init__()
        self.store = store

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Saved prompts", classes="heading")
            yield Static(id="presets-status")
            yield OptionList(id="presets")

    def on_mount(self) -> None:
        options = self.query_one(OptionList)
        options.add_options([Option(Text(f"{p.name} — {p.mood or p.style.summary()}"), id=p.name)
                             for p in self.store.list()])
        if options.option_count:
            options.highlighted = 0
        self._show_status(self.store.error)
        options.focus()

    def _show_status(self, error: str | None = None) -> None:
        if error:
            text = f"[$error]{escape(error)}[/]"
        elif self.query_one(OptionList).option_count:
            text = "Enter to apply · d to delete · Esc to close"
        else:
            text = self.EMPTY
        self.query_one("#presets-status", Static).update(text)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        preset = self.store.get(event.option.id)
        if preset is None:
            self._show_status(self.store.error or f"“{event.option.id}” is no longer saved.")
            return
        self.dismiss(preset)

    def action_delete(self) -> None:
        options = self.query_one(OptionList)
        if options.highlighted is None:
            return
        option = options.get_option_at_index(options.highlighted)
        try:
            self.store.delete(option.id)
        except PresetError as e:
            self._show_status(str(e))
            return
        options.remove_option(option.id)
        self._show_status()

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
        Binding("space", "toggle", "Play/Pause"),
        Binding("n", "next", "Next"),
        Binding("slash", "focus_mood", "Mood"),
        Binding("m", "pick_model", "Model"),
        Binding("s", "save_preset", "Save"),
        Binding("p", "pick_preset", "Saved"),
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
        self.library = PlaylistLibrary()
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

    # ------------------------------------------------------------ actions
    def action_back(self) -> None:
        self._keep_playing = True
        self.exit()

    def action_toggle(self) -> None:
        self.player.toggle()
        self._tick()

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
        self.player.set_volume((round(self.player.volume * 100) + percent) / 100)
        self._tick()

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
        def chosen(preset: Preset | None) -> None:
            if preset is None:
                return
            self.workers.cancel_group(self, "interpret")  # a late interpretation must not override the choice
            self.player.set_style(preset.style)
            self._status(f"Up next: [b]{escape(preset.name)}[/b] (saved)")

        self.push_screen(PresetPicker(self.presets), chosen)

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
        cost = f" · {usage.format()}" if usage else ""
        self._status(f"Up next: [b]{escape(style.name)}[/b] · {escape(style.summary())}{cost}")
        if usage:
            self.session_usage = usage if self.session_usage is None else self.session_usage + usage
            self._show_model()
