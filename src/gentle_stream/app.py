"""Interactive TUI: what is playing, a live spectrum, play/pause/next, and a mood prompt.

The mood is interpreted by Gentle Shell in a thread worker while the music keeps playing; the new style starts at
the next bar. Everything shown comes from `player.now`, i.e. the bar that is audible right now.
"""
from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from .bridge import GentleShell, InterpretError
from .player import Player

DEFAULT_MODEL_LABEL = "Gentle Shell default"
BLOCKS = " ▁▂▃▄▅▆▇█"


def model_label(model: str | None) -> str:
    return model or DEFAULT_MODEL_LABEL


class Spectrum(Static):
    """A few rows of block characters, one column per log-spaced band."""

    ROWS = 5

    def __init__(self, player: Player, **kw):
        super().__init__(**kw)
        self.player = player

    def tick(self) -> None:
        width = max(8, self.size.width or 48)
        levels = self.player.spectrum(width) * self.ROWS * 8
        rows = []
        for r in range(self.ROWS - 1, -1, -1):
            cell = (levels - r * 8).clip(0, 8).astype(int)
            rows.append("".join(BLOCKS[c] for c in cell))
        self.update("\n".join(rows))


class ModelPicker(ModalScreen[str | None]):
    """Choose the model Gentle Shell uses to interpret moods."""

    BINDINGS = [Binding("escape", "dismiss_picker", "Cancel")]
    DEFAULT_CSS = """
    ModelPicker { align: center middle; }
    ModelPicker > Vertical { width: 60; height: auto; max-height: 80%; border: round $accent; padding: 0 1;
                             background: $surface; }
    ModelPicker OptionList { height: auto; max-height: 20; }
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
            self.app.call_from_thread(self.query_one("#picker-status", Static).update, f"Could not list models: {e}")
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


class StreamApp(App):
    TITLE = "gentle-stream"
    SUB_TITLE = "real-time generative lo-fi"
    AUTO_FOCUS = None
    CSS = """
    #panel { padding: 1 2; height: 1fr; }
    #title { text-style: bold; color: $accent; }
    #details, #mood-line, #model { color: $text-muted; }
    #spectrum { height: 5; margin: 1 0; color: $accent; }
    #state { text-style: bold; }
    #status { margin-top: 1; }
    #mood { dock: bottom; margin: 0 1 1 1; }
    """
    BINDINGS = [
        Binding("space", "toggle", "Play/Pause"),
        Binding("n", "next", "Next"),
        Binding("slash", "focus_mood", "Mood"),
        Binding("m", "pick_model", "Model"),
        Binding("plus,equals_sign", "volume(0.1)", "Vol +"),
        Binding("minus", "volume(-0.1)", "Vol -"),
        Binding("escape", "leave_mood", show=False),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, player: Player, shell: GentleShell, initial_mood: str | None = None):
        super().__init__()
        self.player, self.shell, self.initial_mood = player, shell, initial_mood

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
        self.player.close()

    # ------------------------------------------------------------ display
    def _tick(self) -> None:
        now, p = self.player.now, self.player
        w = self._w
        w["title"].update(f"♪ {now.style.name}")
        w["details"].update(now.style.summary())
        w["mood-line"].update(f"“{now.style.mood}”" if now.style.mood else "")
        state = "▶ Playing" if p.playing else "⏸ Paused"
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

    def action_volume(self, delta: float) -> None:
        self.player.set_volume(round(self.player.volume + delta, 2))
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
                self.call_from_thread(self._status, f"[red]Could not interpret the mood:[/] {e}")
            return
        if worker.is_cancelled:
            return
        self.player.set_style(style)
        self.call_from_thread(self._status, f"Up next: [b]{style.name}[/b] · {style.summary()}")
