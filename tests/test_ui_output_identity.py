"""Ambiguous live output names must not identify a different physical device."""
import pytest
from textual.widgets import OptionList

from tests.test_ui_device_picker import Device, output_ui, ui  # noqa: F401


@pytest.mark.asyncio
async def test_duplicate_output_names_without_current_index(output_ui):
    app, player = output_ui
    player.device = 'USB Audio'
    player.devices.return_value = [Device(4, 'USB Audio', False), Device(7, 'USB Audio', False)]
    async with app.run_test() as pilot:
        await pilot.press('o')
        await app.workers.wait_for_complete()
        options = app.screen.query_one(OptionList)
        assert [options.get_option_at_index(i).prompt.plain for i in (1, 2)] == [
            'USB Audio [4]', 'USB Audio [7]']
        assert options.highlighted is None
        assert 'index unknown' in str(app.screen.query_one('#devices-status').render())
        await pilot.press('end', 'enter')
        await app.workers.wait_for_complete()
        player.set_device.assert_called_once_with(7)
