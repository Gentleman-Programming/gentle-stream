"""Validated UI-only preferences and native settings flows."""
from dataclasses import replace

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, Input, Switch

from gentle_stream.appearance import Preferences, PreferenceError, PreferenceStore
from gentle_stream.appearance_ui import SettingsScreen
from tests.test_ui_toolbar_contracts import ui, shown  # noqa: F401


@pytest.mark.parametrize("key,value", [("refresh_hz", 4), ("refresh_hz", 31), ("bands", 7),
    ("bands", 129), pytest.param("bands", 10 ** 400, id="oversized-integer"),
    ("height", 4), ("height", 31), ("smoothing", -1), ("smoothing", float('nan')),
    ("smoothing", 1.1), ("peak_hold", 4), ("accent", "red; color: blue"),
    ("background", "#xyzxyz"), ("style", "unknown"), ("show_controls", 1),
    ("icon_gap", 0), ("theme", "terminal")])
def test_rejected_preferences_leave_bytes_unchanged(tmp_path, key, value):
    store = PreferenceStore(tmp_path / "appearance.json")
    store.save(Preferences())
    before = store.path.read_bytes()
    with pytest.raises(PreferenceError, match=f"Invalid {key}\\."):
        store.save(replace(Preferences(), **{key: value}))
    assert store.path.read_bytes() == before
    assert store.load() == Preferences()


def test_roundtrip_defaults_bounds_and_corrupt_record(tmp_path):
    store = PreferenceStore(tmp_path / "appearance.json")
    assert store.load() == Preferences() and not store.path.exists()
    for hz in (5, 30):
        prefs = replace(Preferences(), refresh_hz=hz, bands=128, height=30,
                        smoothing=1., peak_hold=3., icon_gap=3)
        store.save(prefs)
        assert store.load() == prefs
    store.path.write_text('{oops')
    assert store.load() == Preferences()
    assert store.error.startswith("Cannot read appearance preferences:")
    with pytest.raises(PreferenceError, match="Cannot read appearance preferences:"):
        store.save(Preferences())
    assert store.path.read_text() == '{oops'


@pytest.mark.parametrize('record', ['{}', '{"version": 2, "preferences": {}}',
    '{"version": 1, "preferences": {"refresh_hz": 200}}', '[]'])
def test_invalid_record_is_not_rewritten(tmp_path, record):
    store = PreferenceStore(tmp_path / 'appearance.json')
    store.path.write_text(record)
    assert store.load() == Preferences() and store.error
    with pytest.raises(PreferenceError):
        store.save(Preferences())
    assert store.path.read_text() == record


def test_atomic_write_failure_keeps_previous_record(tmp_path, monkeypatch):
    import gentle_stream.appearance as appearance
    store = PreferenceStore(tmp_path / 'appearance.json')
    store.save(Preferences())
    before = store.path.read_bytes()
    def refuse(*args):
        raise OSError('test replacement refused')
    monkeypatch.setattr(appearance.os, 'replace', refuse)
    with pytest.raises(PreferenceError, match='Cannot write appearance preferences: test replacement refused'):
        store.save(replace(Preferences(), bands=8))
    assert store.path.read_bytes() == before
    assert store.load() == Preferences()


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [(80, 24), (180, 50)])
async def test_settings_keeps_fields_and_actions_legible(ui, size):
    app, *_ = ui
    async with app.run_test(size=size) as pilot:
        await pilot.press('f2')
        screen = app.screen
        scroll = screen.query_one(VerticalScroll)
        for key in ('theme', 'background', 'accent', 'style', 'palette', 'height'):
            assert scroll.content_region.contains_region(screen.query_one('#pref-' + key).region), key
        actions = screen.query_one('#settings-actions')
        assert [str(button.label) for button in actions.query(Button)] == [
            'Preview', 'Defaults', 'Close', 'Save & close',
        ]
        for button in actions.query(Button):
            assert actions.content_region.contains_region(button.region)
            assert button.content_region.width >= len(str(button.label))


@pytest.mark.asyncio
@pytest.mark.parametrize('dismissal', ['close', 'escape'])
async def test_close_discards_preview_and_unapplied_edits(ui, dismissal):
    app, player, shell, legacy, library = ui
    saved = replace(Preferences(), accent='#123456')
    app.preference_store.save(saved)
    app.preferences = app.preference_store.load()
    stored = app.preference_store.path.read_bytes()
    async with app.run_test(size=(80, 24)) as pilot:
        root = app.screen
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2')
        for key in ('show_visualizer', 'show_controls', 'show_input', 'show_footer',
                    'show_details', 'show_model'):
            app.screen.query_one('#pref-' + key, Switch).value = False
        assert await pilot.click('#settings-preview')
        assert shown(app, '#settings-status') == 'Preview applied. Close or Esc discards it.'
        assert not root.query_one('#spectrum').display
        assert not root.query_one('#commands').display
        assert root.query_one('#panel').styles.height.is_auto
        app.screen.query_one('#pref-accent', Input).value = 'invalid'
        if dismissal == 'close':
            assert await pilot.click('#settings-close')
        else:
            await pilot.press('escape')
        assert app.screen is root
        assert app.preferences == saved and app.preference_store.path.read_bytes() == stored
        assert root.query_one('#spectrum').display and root.query_one('#commands').display
        await pilot.press('f2')
        assert app.screen.query_one('#pref-accent', Input).value == saved.accent
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not legacy.path.exists() and not library.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [(60, 20), (80, 24), (120, 30)])
async def test_save_persists_then_dismisses_and_defaults_can_be_saved(ui, size):
    app, player, shell, _, _ = ui
    async with app.run_test(size=size) as pilot:
        root = app.screen
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2')
        app.screen.query_one('#pref-show_controls', Switch).value = False
        app.screen.query_one('#pref-show_input', Switch).value = False
        assert await pilot.click('#settings-save')
        assert app.screen is root
        expected = replace(Preferences(), show_controls=False, show_input=False)
        assert app.preference_store.load() == app.preferences == expected
        assert not root.query_one('#commands').display and not root.query_one('#mood').display
        await pilot.press('slash')
        assert root.query_one('#mood').display and root.query_one('#mood').has_focus
        await pilot.press('f2')  # priority binding works even inside an Input
        assert isinstance(app.screen, SettingsScreen)
        assert not app.screen.query_one('#pref-show_input', Switch).value
        stored = app.preference_store.path.read_bytes()
        assert await pilot.click('#settings-defaults')
        assert shown(app, '#settings-status') == 'Defaults previewed. Close or Esc discards them.'
        assert app.screen.query_one('#pref-show_input', Switch).value
        assert app.preferences == Preferences() and app.preference_store.path.read_bytes() == stored
        assert await pilot.click('#settings-save')
        assert app.screen is root and app.preference_store.load() == Preferences()
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('key,error', [('accent', 'Invalid accent.'), ('bands', 'Invalid bands.')])
async def test_invalid_save_keeps_modal_open_and_changes_nothing(ui, key, error):
    app, player, shell, _, _ = ui
    app.preference_store.save(Preferences())
    stored = app.preference_store.path.read_bytes()
    async with app.run_test(size=(80, 24)) as pilot:
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2')
        screen = app.screen
        field = screen.query_one('#pref-' + key, Input)
        field.value = 'invalid'
        assert await pilot.click('#settings-save')
        assert app.screen is screen and field.value == 'invalid'
        assert shown(app, '#settings-status') == error
        assert app.preferences == Preferences() and app.preference_store.path.read_bytes() == stored
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_write_failure_keeps_preview_and_allows_retry(ui, monkeypatch):
    import gentle_stream.appearance as appearance
    app, player, shell, _, _ = ui
    app.preference_store.save(Preferences())
    stored = app.preference_store.path.read_bytes()

    def refuse(*args):
        raise OSError('test replacement refused')

    async with app.run_test(size=(80, 24)) as pilot:
        root = app.screen
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2')
        screen = app.screen
        field = screen.query_one('#pref-accent', Input)
        field.value = '#123456'
        assert await pilot.click('#settings-preview')
        field.value = '#00ff00'
        with monkeypatch.context() as patch:
            patch.setattr(appearance.os, 'replace', refuse)
            assert await pilot.click('#settings-save')
        assert app.screen is screen and field.value == '#00ff00'
        assert shown(app, '#settings-status') == 'Cannot write appearance preferences: test replacement refused'
        assert app.current_theme.primary == '#123456'
        assert app.preferences == replace(Preferences(), accent='#123456')
        assert app.preference_store.path.read_bytes() == stored
        await pilot.pause(screen.query_one('#settings-save', Button).active_effect_duration)
        assert await pilot.click('#settings-save')
        assert app.screen is root
        assert app.preference_store.load() == app.preferences == replace(Preferences(), accent='#00ff00')
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_corrupt_record_is_reported_without_leaving_settings(ui):
    app, p, shell, _, _ = ui
    app.preference_store.path.parent.mkdir(parents=True)
    app.preference_store.path.write_text('{oops')
    app.preference_store.load()
    async with app.run_test() as pilot:
        await pilot.press('f2')
        screen = app.screen
        error = shown(app, '#settings-status')
        assert error.startswith('Cannot read appearance preferences:')
        before = app.preferences
        app.screen.query_one('#pref-accent', Input).value = '#ffffff'
        assert await pilot.click('#settings-save')
        assert app.screen is screen
        assert shown(app, '#settings-status') == error + '; fix or move appearance.json before saving.'
        assert app.preferences == before
        assert app.preference_store.path.read_text() == '{oops'
        p.set_style.assert_not_called()
        p.toggle.assert_not_called()
        shell.list_models.assert_not_called()


@pytest.mark.asyncio
async def test_live_color_updates_and_command_palette(ui):
    app, p, shell, _, _ = ui
    async with app.run_test() as pilot:
        assert any(command.title == 'Settings' for command in app.get_system_commands(app.screen))
        await pilot.press('f2')
        for color in ('#00ff00', '#ff0000', '#123456'):
            app.screen.query_one('#pref-accent', Input).value = color
            await app.screen.submit('preview')
            await pilot.wait_for_scheduled_animations()
            await pilot.pause()
            assert app.current_theme.primary == color
            assert app.screen_stack[0].query_one('#title').styles.color.hex.lower() == color
            widget_color = app.screen_stack[0].query_one('#louder', Button).styles.color
            assert all(abs(actual - int(color[i:i + 2], 16)) <= 1
                       for actual, i in zip((widget_color.r, widget_color.g, widget_color.b), (1, 3, 5)))
        assert p.start.call_count == 1
        assert not app.preference_store.path.exists()


@pytest.mark.asyncio
async def test_settings_and_model_menus_explain_context_without_changing_music(ui):
    from gentle_stream.app import ModelPicker
    app, player, shell, store, library = ui
    async with app.run_test(size=(80, 24)) as pilot:
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2')
        assert isinstance(app.screen, SettingsScreen)
        assert 'music is unchanged' in shown(app, '#settings-description')
        assert await pilot.click('#settings-model')
        await pilot.pause()  # the pushed screen mounts on the next event-loop turn
        assert isinstance(app.screen, ModelPicker)
        assert 'Choose a mood model' in shown(app, '.heading')
        assert 'future moods' in shown(app, '#model-description')
        assert app.screen.query_one('#models').has_focus
        await pilot.press('down', 'enter')
        assert shell.model == 'provider/chosen-model'
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        assert not store.path.exists() and not library.path.exists()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_settings_model_is_explicit_and_m_stays_direct(ui):
    from gentle_stream.app import ModelPicker
    app, p, shell, _, _ = ui
    async with app.run_test() as pilot:
        await pilot.press('f2')
        assert await pilot.click('#settings-model')
        assert isinstance(app.screen, ModelPicker)
        await pilot.pause()
        await pilot.press('down', 'enter')
        assert shell.model == 'provider/chosen-model'
        await pilot.press('escape', 'm')
        assert isinstance(app.screen, ModelPicker)
        p.set_style.assert_not_called()
        p.set_playlist.assert_not_called()
