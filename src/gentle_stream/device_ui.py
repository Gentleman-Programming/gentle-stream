"""Keyboard-accessible selector for the player's audio output, not the system's."""
from __future__ import annotations

from collections import Counter

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

DEFAULT_DEVICE = 'System default'


def missing_output(label: str) -> str:
    return f'Output device “{label}” not found; using {DEFAULT_DEVICE}.'


class DevicePicker(ModalScreen[str | None]):
    """Pick by device index; dismiss with the switched output's label, or None on cancel."""

    BINDINGS = [Binding('escape', 'cancel', 'Cancel')]
    DEFAULT_CSS = '''
    DevicePicker { align: center middle; }
    DevicePicker > Vertical { width: 60; max-width: 100%; height: auto; max-height: 85%;
        border: round $primary; padding: 0 1; background: $surface; }
    DevicePicker #devices-heading { color: $foreground; text-style: bold; }
    DevicePicker #devices-status { color: $text-muted; }
    DevicePicker #devices-status.error { color: $error; }
    DevicePicker #devices-help { color: $text-muted; }
    DevicePicker OptionList, DevicePicker OptionList:focus { height: auto; max-height: 12;
        border: none; background: $surface; padding: 0; }
    DevicePicker OptionList.-textual-compact > .option-list--option { padding: 0 1; }
    '''

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.player = owner.player
        self.labels = {'default': DEFAULT_DEVICE}
        self.switching = False

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static('Audio output', id='devices-heading')
            yield Static('Loading devices…', id='devices-status')
            yield OptionList(id='devices', disabled=True, compact=True)
            yield Static('Esc closes', id='devices-help')

    def on_mount(self) -> None:
        if getattr(self.player, 'audio_output', None) == 'windows':
            self._status('Output selection is unavailable with the Windows audio bridge.')
        elif not callable(getattr(self.player, 'devices', None)) or not callable(getattr(self.player, 'set_device', None)):
            self._status('This player does not support selecting an audio output.')
        else:
            self.owner.run_output_operation(self.player.devices, self._listed)

    def _status(self, text: str, *, error: bool = False) -> None:
        status = self.query_one('#devices-status', Static)
        status.set_class(error, 'error')
        status.update(Text(text))

    def _listed(self, devices, error: Exception | None) -> None:
        if self.app.screen is not self:  # Esc may have closed the picker while the request ran
            return
        if error is not None:
            self._status(f'Could not list devices: {error}', error=True)
            return
        options = self.query_one(OptionList)
        entries = [Option(Text(DEFAULT_DEVICE), id='default')]
        current = self.player.device
        selected = None
        label = DEFAULT_DEVICE if current is None else current
        counts = Counter(device.name.casefold() for device in devices)
        ambiguous = isinstance(current, str) and counts[current.casefold()] > 1
        if ambiguous:
            label = f'{current} (index unknown)'
        for i, device in enumerate(devices, 1):
            key = str(device.index)
            display_name = f'{device.name} [{key}]' if counts[device.name.casefold()] > 1 else device.name
            self.labels[key] = display_name
            suffix = ' (system default)' if device.default else ''
            entries.append(Option(Text(display_name + suffix), id=key))
            if selected is None and (current == device.index or
                    not ambiguous and isinstance(current, str) and current.casefold() == device.name.casefold()):
                selected, label = i, display_name
        options.add_options(entries)
        missing = self.player.device_missing
        if isinstance(missing, str) and missing:
            self._status(missing_output(missing))
        else:
            empty = '. No named output devices found.' if not devices else ''
            self._status(f'Current: {label}{empty}')
        self.query_one('#devices-help', Static).update('Enter selects · Esc closes')
        options.disabled = False
        options.highlighted = 0 if current is None else selected
        options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if self.switching or event.option.id not in self.labels or event.option_list.disabled:
            return
        key = event.option.id
        label = self.labels[key]
        device = None if key == 'default' else int(key)
        self.switching = True
        event.option_list.disabled = True
        self._status(f'Switching to {label}…')
        self.query_one('#devices-help', Static).update('Wait for the switch to finish')

        def finished(_result, error: Exception | None) -> None:
            if error is None:
                self.dismiss(label)
                return
            self.switching = False
            self._status(f'Could not switch the output: {error}', error=True)
            self.query_one('#devices-help', Static).update('Enter selects · Esc closes')
            event.option_list.disabled = False
            event.option_list.focus()

        self.owner.run_output_operation(lambda: self.player.set_device(device), finished)

    def action_cancel(self) -> None:
        if not self.switching:  # once submitted, the player API cannot cancel a switch
            self.dismiss(None)
