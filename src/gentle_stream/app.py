"""Interactive TUI: what is playing, a live spectrum, play/pause/next, and a mood prompt.

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

from .bridge import GentleShell, InterpretError
from .player import Player
from .presets import NAME_MAX, Preset, PresetError, PresetStore, normalize_name

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
    """A few rows of block characters, one column per log-spaced band, shaded from rose (top) to muted."""

    ROWS = len(SPECTRUM_COLORS)

    def __init__(self, player: Player, **kw):
        super().__init__(**kw)
        self.player = player

    def tick(self) -> None:
        width = max(8, self.size.width or 48)
        levels = self.player.spectrum(width) * self.ROWS * 8
        rows = []
        for r, color in zip(range(self.ROWS - 1, -1, -1), SPECTRUM_COLORS):
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
    SUB_TITLE = "real-time generative lo-fi"
    AUTO_FOCUS = None
    CSS = """
    Screen { background: $background; }
    Header { background: #1a1218; color: $primary; }
    #panel { padding: 1 2; margin: 1 1 0 1; height: 1fr; border: round $panel; background: $surface; }
    #title { text-style: bold; color: $primary; }
    #details, #model { color: $text-muted; }
    #mood-line { color: $accent; text-style: italic; }
    #spectrum { height: 5; margin: 1 0; }
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
        Binding("p", "pick_preset", "Saved"),
        Binding("plus,equals_sign", "volume(5)", "Vol +5"),
        Binding("minus", "volume(-5)", "Vol -5"),
        Binding("up", "volume(1)", "Vol +1"),
        Binding("down", "volume(-1)", "Vol -1"),
        Binding("escape", "leave_mood", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, player: Player, shell: GentleShell, initial_mood: str | None = None,
                 presets: PresetStore | None = None):
        super().__init__()
        self.player, self.shell, self.initial_mood = player, shell, initial_mood
        self.presets = presets if presets is not None else PresetStore()
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
        self.player.close()

    # ------------------------------------------------------------ display
    def _tick(self) -> None:
        now, p = self.player.now, self.player
        w = self._w
        w["title"].update(f"♪ {now.style.name}")
        w["details"].update(now.style.summary())
        w["mood-line"].update(f"“{now.style.mood}”" if now.style.mood else "")
        state = "[b $success]▶ Playing[/]" if p.playing else "[b $warning]⏸ Paused[/]"
        w["state"].update(f"{state} · piece {now.piece} · bar {now.bar + 1} · vol {round(p.volume * 100)}%")
        w["spectrum"].tick()

    def _show_model(self) -> None:
        self._w["model"].update(f"model: {model_label(self.shell.model)}")

    def _status(self, text: str) -> None:
        self._w["status"].update(text)

    # ------------------------------------------------------------ actions
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
        self._status(f"Interpreting “{mood}” with {model_label(self.shell.model)}…")
        self._interpret(mood)

    @work(thread=True, exclusive=True, group="interpret")
    def _interpret(self, mood: str) -> None:
        worker = get_current_worker()
        try:
            style = self.shell.interpret(mood, self.player.engine.style)
        except InterpretError as e:
            if not worker.is_cancelled:
                self.call_from_thread(self._status, f"[$error]Could not interpret the mood:[/] {escape(str(e))}")
            return
        if worker.is_cancelled:
            return
        self.player.set_style(style)
        self.call_from_thread(self._status, f"Up next: [b]{style.name}[/b] · {style.summary()}")
