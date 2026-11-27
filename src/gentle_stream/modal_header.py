"""Compact title row that delegates closing to the owning dialog."""
from collections.abc import Callable

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Static


class ModalHeader(Horizontal):
    DEFAULT_CSS = """
    ModalHeader#modal-header { height: auto; }
    ModalHeader > Static { width: 1fr; height: auto; color: $primary; text-style: bold; }
    ModalHeader #modal-close {
        width: 9; min-width: 0; height: 1; min-height: 1; margin: 0; border: none;
        padding: 0; background: $surface; color: $primary; text-style: bold;
    }
    ModalHeader #modal-close:hover { background: $panel; }
    ModalHeader #modal-close:focus { color: $text; text-style: bold underline; }
    ModalHeader #modal-close.-active { background: $primary; color: $surface; }
    """

    def __init__(self, title: Static, cancel: Callable[[], None]) -> None:
        super().__init__(id='modal-header')
        self.title = title
        self.cancel = cancel

    def compose(self) -> ComposeResult:
        yield self.title
        yield Button('X Close', id='modal-close', tooltip='Close this dialog (Esc)')

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == 'modal-close':
            event.stop()
            self.cancel()
