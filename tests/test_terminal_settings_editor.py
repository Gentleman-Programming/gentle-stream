"""Approved draft editor, using an inert player and shell."""
import asyncio
import json
from dataclasses import asdict, replace
from threading import Event

import pytest
from textual.widgets import Button, Input, OptionList

from gentle_stream.app import ModelPicker
from gentle_stream.appearance import Preferences
from gentle_stream.bridge import InterpretError
from tests.test_ui_toolbar_contracts import shown, ui  # noqa: F401


@pytest.mark.asyncio
async def test_spectrum_selection_previews_only_and_discards_through_header(ui):
    app, player, shell, legacy, library = ui
    async with app.run_test(size=(60, 24)) as pilot:
        root = app.screen
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2')
        settings = app.screen
        assert shown(app, '#appearance-title') == 'Appearance Settings'
        modes = settings.query_one('#appearance-modes', OptionList)
        assert modes.has_focus and modes.highlighted == 0
        bars = shown(app, '#appearance-preview')
        await pilot.press('down')
        assert shown(app, '#appearance-preview') == bars
        assert settings.query_one('#appearance-save', Button).disabled
        await pilot.press('enter')
        wave = shown(app, '#appearance-preview')
        assert wave != bars and wave.startswith('♪ Example track\n')
        assert app.preferences == Preferences() and root.query_one('#spectrum').preferences == Preferences()
        assert not app.preference_store.path.exists()
        assert await pilot.click('#modal-close')
        assert shown(app, '#appearance-status') == 'Discard unsaved changes?'
        assert await pilot.click('#appearance-keep')
        assert shown(app, '#appearance-preview') == wave
        await pilot.press('escape')
        assert await pilot.click('#appearance-discard')
        assert app.screen is root and not app.preference_store.path.exists()
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not legacy.path.exists() and not library.path.exists()


@pytest.mark.asyncio
async def test_save_reopens_with_mirrored_spectrum_and_applies_band_count_only_after_save(ui):
    app, player, shell, *_ = ui
    async with app.run_test(size=(72, 24)) as pilot:
        root = app.screen
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2', 'down', 'down', 'enter')
        settings = app.screen
        settings.query_one('#appearance-bands', Input).value = '12'
        await pilot.pause()
        mirror = shown(app, '#appearance-preview')
        assert not app.preference_store.path.exists()
        assert (12,) not in [call.args for call in player.spectrum.call_args_list]
        assert await pilot.click('#appearance-save')
        await pilot.pause(.2)
        assert app.screen is root
        saved = Preferences(style='mirror', bands=12)
        assert root.query_one('#spectrum').preferences == app.preference_store.load() == saved
        assert (12,) in [call.args for call in player.spectrum.call_args_list]
        await pilot.press('f2')
        assert app.screen.query_one('#appearance-modes', OptionList).highlighted == 2
        assert shown(app, '#appearance-preview') == mirror
        assert app.screen.query_one('#appearance-save', Button).disabled
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_nested_audio_highlight_does_not_switch_or_commit_draft(ui):
    from tests.test_ui_device_picker import Device

    app, player, shell, *_ = ui
    player.audio_output = 'sounddevice'
    player.device = player.device_missing = None
    player.devices.return_value = [Device(3, 'Speakers', True)]
    async with app.run_test(size=(40, 24)) as pilot:
        await pilot.press('f2')
        settings = app.screen
        settings.query_one('#appearance-accent', Input).value = '#abcdef'
        await pilot.pause()
        assert await pilot.click('#appearance-audio')
        await app.workers.wait_for_complete()
        await pilot.press('end')
        assert app.screen.query_one('#devices', OptionList).highlighted == 1
        assert shown(app, '#devices-status') == 'Current: System default'
        await pilot.press('escape')
        assert app.screen is settings
        assert settings.query_one('#appearance-accent', Input).value == '#abcdef'
        player.devices.assert_called_once()
        player.set_device.assert_not_called()
        assert player.device is None and player.playing
        assert app.preferences == Preferences() and not app.preference_store.path.exists()
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_model_launcher_failure_stays_within_picker_and_can_retry(ui):
    app, player, shell, *_ = ui
    shell.model = 'current/model'
    shell.list_models.side_effect = [OSError('inert launcher failure'), ['new/model']]
    async with app.run_test(size=(72, 24)) as pilot:
        await pilot.press('m')
        await app.workers.wait_for_complete()
        assert isinstance(app.screen, ModelPicker)
        assert shown(app, '#picker-status') == 'Could not list models: check Gentle Shell and try again.'
        assert shell.model == 'current/model'
        assert await pilot.click('#modal-close')
        await pilot.press('m')
        await app.workers.wait_for_complete()
        await pilot.press('down', 'enter')
        assert shell.model == 'new/model' and player.playing
        assert not app.preference_store.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('fails', [False, True])
async def test_late_model_listing_cannot_replace_settings_or_lose_draft(ui, fails):
    app, player, shell, *_ = ui
    shell.model = 'current/model'
    started, release, finished = Event(), Event(), Event()

    def list_models():
        started.set()
        try:
            assert release.wait(5)
            if fails:
                raise InterpretError('late [offline]')
            return ['late/model']
        finally:
            finished.set()

    shell.list_models.side_effect = list_models
    async with app.run_test(size=(72, 24)) as pilot:
        await pilot.press('f2')
        settings = app.screen
        accent = settings.query_one('#appearance-accent', Input)
        accent.value = '#abcdef'
        await pilot.pause()
        try:
            assert await pilot.click('#appearance-models')
            assert started.is_set() and shown(app, '#picker-status') == 'Loading models…'
            await pilot.press('escape')
            assert app.screen is settings
        finally:
            release.set()
        assert await asyncio.to_thread(finished.wait, 5)
        await pilot.pause()
        assert app.screen is settings and accent.value == '#abcdef'
        assert shown(app, '#appearance-status') == 'Preview only until saved.'
        assert shell.model == 'current/model' and player.playing
        assert not app.preference_store.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_save_merges_untouched_preferences_but_rejects_an_edited_field_conflict(ui):
    app, player, shell, *_ = ui
    path = app.preference_store.path
    app.preference_store.save(Preferences())
    async with app.run_test(size=(72, 24)) as pilot:
        await pilot.press('f2')
        app.screen.query_one('#appearance-accent', Input).value = '#abcdef'
        record = {**asdict(Preferences(bands=16)), 'extension': {'nested': [1, 2]}}
        path.write_text(json.dumps({'version': 1, 'preferences': record}), encoding='utf-8')
        await pilot.pause()
        assert await pilot.click('#appearance-save')
        merged = Preferences(bands=16, accent='#abcdef')
        assert app.preferences == merged
        assert json.loads(path.read_text(encoding='utf-8')) == {
            'version': 1, 'preferences': {**record, 'accent': '#abcdef'}}

        await pilot.press('f2')
        settings = app.screen
        accent = settings.query_one('#appearance-accent', Input)
        accent.value = '#123456'
        await pilot.pause()
        app.preference_store.save(replace(merged, accent='#fedcba'))
        stored = path.read_bytes()
        assert await pilot.click('#appearance-save')
        assert app.screen is settings and accent.value == '#123456'
        assert shown(app, '#appearance-status') == 'Appearance changed elsewhere: accent. Reopen Settings and try again.'
        assert path.read_bytes() == stored and app.preferences == merged and player.playing
        player.set_style.assert_not_called()
        player.set_device.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
