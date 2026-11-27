"""Output selection through Textual, with no PortAudio or daemon dependency."""
from dataclasses import dataclass
from threading import Event, Lock

import pytest
from textual.widgets import Input, OptionList

from gentle_stream import main
from tests.test_ui_toolbar_contracts import shown, ui  # noqa: F401


@dataclass(frozen=True)
class Device:
    """Public audio_devices.Device shape, independent of the backend port."""

    index: int
    name: str
    default: bool


@pytest.fixture
def output_ui(ui):
    app, player, *_ = ui
    player.audio_output = 'sounddevice'
    player.device = player.device_missing = None
    player.devices.return_value = [Device(3, 'Speakers', True), Device(7, 'USB [b] DAC', False)]
    return app, player


@pytest.mark.asyncio
@pytest.mark.parametrize('launch', ['shortcut', 'settings'])
async def test_switch_and_system_default_without_restarting(output_ui, launch):
    app, player = output_ui
    player.set_device.side_effect = lambda value: setattr(player, 'device', value)
    async with app.run_test(size=(80, 24)) as pilot:
        root = app.screen
        previous_calls = [call for call in player.mock_calls if call[0] != 'spectrum']
        if launch == 'settings':
            await pilot.press('f2')
            origin = app.screen
            origin.query_one('#appearance-accent', Input).value = '#123456'
            assert await pilot.click('#appearance-audio')
        else:
            origin = root
            await pilot.press('o')
        await app.workers.wait_for_complete()
        options = app.screen.query_one('#devices', OptionList)
        assert options.has_focus and options.highlighted == 0
        assert [options.get_option_at_index(i).prompt.plain for i in range(options.option_count)] == [
            'System default', 'Speakers (system default)', 'USB [b] DAC',
        ]
        assert shown(app, '#devices-status') == 'Current: System default'
        await pilot.press('end', 'enter')
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen is origin
        assert str(root.query_one('#status').render()) == 'Output: USB [b] DAC'
        if launch == 'settings':
            assert shown(app, '#appearance-status') == 'Output: USB [b] DAC'
            assert origin.query_one('#appearance-accent', Input).value == '#123456'
            await pilot.press('escape')
            assert await pilot.click('#appearance-discard')
        await pilot.press('o')
        await app.workers.wait_for_complete()
        assert app.screen.query_one('#devices', OptionList).highlighted == 2
        assert shown(app, '#devices-status') == 'Current: USB [b] DAC'
        await pilot.press('home', 'enter')
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen is root
        assert shown(app, '#status') == 'Output: System default'
        assert player.device is None and player.device_missing is None and player.playing
        assert player.set_device.call_args_list == [((7,), {}), ((None,), {})]
        assert [call for call in player.mock_calls if call[0] not in ('spectrum', 'devices', 'set_device')] == previous_calls
        assert not app.preference_store.path.exists()


@pytest.mark.asyncio
async def test_listing_can_be_cancelled_with_shared_client_lock(output_ui):
    app, player = output_ui
    root_status = 'Keep this status'
    rpc = Lock()
    entered, release = Event(), Event()
    previous_spectrum = player.spectrum.side_effect

    def spectrum(width):
        with rpc:
            return previous_spectrum(width)

    def devices():
        with rpc:
            entered.set()
            assert release.wait(3), 'listing blocked the event loop'
            raise RuntimeError('late result after cancellation')

    player.spectrum.side_effect = spectrum
    player.devices.side_effect = devices
    async with app.run_test() as pilot:
        root = app.screen
        root.query_one('#status').update(root_status)
        try:
            await pilot.press('o')
            assert entered.is_set()
            assert shown(app, '#devices-status') == 'Loading devices…'
            await pilot.press('enter')
            await pilot.press('escape')
            await pilot.pause(.15)  # span multiple polling intervals while the RPC is still blocked
            assert app.screen is root and not release.is_set()
            assert shown(app, '#status') == root_status
            assert app.is_running
        finally:
            release.set()
        await app.workers.wait_for_complete()
        assert shown(app, '#status') == root_status
        player.set_device.assert_not_called()
        assert player.device is None and player.playing


@pytest.mark.asyncio
async def test_switch_is_responsive_and_failed_switch_does_not_claim_success(output_ui):
    app, player = output_ui
    rpc = Lock()
    entered, release = Event(), Event()
    previous_spectrum = player.spectrum.side_effect

    def spectrum(width):
        with rpc:
            return previous_spectrum(width)

    def reject(index):
        with rpc:
            entered.set()
            assert release.wait(3), 'switch blocked the event loop'
            raise RuntimeError('device busy [b]')

    player.spectrum.side_effect = spectrum
    player.set_device.side_effect = reject
    async with app.run_test() as pilot:
        root = app.screen
        await pilot.press('o')
        await app.workers.wait_for_complete()
        picker = app.screen
        try:
            await pilot.press('end', 'enter')
            assert entered.is_set()
            assert shown(app, '#devices-status') == 'Switching to USB [b] DAC…'
            await pilot.press('enter', 'escape')
            await pilot.pause(.15)
            assert app.screen is picker and shown(app, '#devices-status') == 'Switching to USB [b] DAC…'
        finally:
            release.set()
        await app.workers.wait_for_complete()
        assert shown(app, '#devices-status') == 'Could not switch the output: device busy [b]'
        assert player.device is None and player.playing
        player.set_device.assert_called_once_with(7)
        await pilot.press('escape')
        assert app.screen is root and shown(app, '#status') == ''


@pytest.mark.asyncio
@pytest.mark.parametrize('scenario,status', [
    ('missing', 'Output device “Unplugged [b]” not found; using System default.'),
    ('empty', 'Current: System default. No named output devices found.'),
    ('listing-error', 'Could not list devices: unavailable [b]'),
    ('bridge', 'Output selection is unavailable with the Windows audio bridge.'),
])
async def test_picker_reports_missing_empty_failed_or_unsupported_output(output_ui, scenario, status):
    app, player = output_ui
    if scenario == 'missing':
        player.device_missing = 'Unplugged [b]'
    elif scenario == 'empty':
        player.devices.return_value = []
    elif scenario == 'listing-error':
        player.devices.side_effect = RuntimeError('unavailable [b]')
    else:
        player.audio_output = 'windows'
    async with app.run_test() as pilot:
        root = app.screen
        if scenario == 'missing':
            assert shown(app, '#status') == status
        await pilot.press('o')
        await app.workers.wait_for_complete()
        assert shown(app, '#devices-status') == status
        options = app.screen.query_one('#devices', OptionList)
        if scenario in ('listing-error', 'bridge'):
            assert options.option_count == 0
            await pilot.press('enter')
            assert shown(app, '#devices-status') == status
        elif scenario == 'empty':
            assert options.option_count == 1 and options.highlighted == 0
        else:
            assert options.highlighted == 0
        await pilot.press('escape')
        assert app.screen is root
        player.set_device.assert_not_called()
        assert player.device is None and player.playing
        if scenario == 'bridge':
            player.devices.assert_not_called()


@pytest.mark.asyncio
async def test_o_still_types_in_mood_input(output_ui):
    app, player = output_ui
    async with app.run_test() as pilot:
        root = app.screen
        await pilot.press('slash', 'o')
        assert app.screen is root and root.query_one('#mood', Input).value == 'o'
        player.devices.assert_not_called()
        player.set_device.assert_not_called()


def test_help_shows_output_picker_without_new_commands(capsys):
    with pytest.raises(SystemExit) as exited:
        main(['--help'])
    assert exited.value.code == 0
    stdout, stderr = capsys.readouterr()
    assert stderr == ''
    assert 'o        choose this app\'s audio output (also in Settings)' in stdout
    assert 'stop an existing player before selecting a different output' not in stdout.lower()
