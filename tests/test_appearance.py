"""Validated UI-only preferences and native settings flows."""
from dataclasses import replace

import pytest
from textual.widgets import Input, Switch

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
@pytest.mark.parametrize("size", [(60, 20), (80, 24), (120, 30)])
async def test_live_preview_cancel_save_recovery_and_no_audio(ui, size):
    app, p, shell, legacy, library = ui
    async with app.run_test(size=size) as pilot:
        before = [c for c in p.mock_calls if c[0] != 'spectrum']
        await pilot.press('f2')
        assert isinstance(app.screen, SettingsScreen)
        assert "Settings" in shown(app, '#settings-title')
        for key in ('show_visualizer', 'show_controls', 'show_input', 'show_footer',
                    'show_details', 'show_model'):
            app.screen.query_one('#pref-' + key, Switch).value = False
        await app.screen.submit('preview')
        assert shown(app, '#settings-status') == 'Preview only — Save to keep; Cancel restores.'
        root = app.screen_stack[0]
        assert not root.query_one('#spectrum').display
        assert not root.query_one('#commands').display
        assert root.query_one('#panel').styles.height.is_auto
        await pilot.press('escape')
        assert root.query_one('#commands').display
        await pilot.press('f2')
        app.screen.query_one('#pref-show_controls', Switch).value = False
        app.screen.query_one('#pref-show_input', Switch).value = False
        await app.screen.submit('save')
        assert shown(app, '#settings-status') == 'Appearance saved.'
        await pilot.press('escape', 'slash')
        assert root.query_one('#mood').display and root.query_one('#mood').has_focus
        assert not app.preferences.show_input
        await pilot.press('f2')  # priority binding works even inside an Input
        assert isinstance(app.screen, SettingsScreen)
        app.screen.query_one('#pref-accent', Input).value = 'invalid'
        stored = app.preference_store.path.read_bytes()
        current = app.preferences
        await app.screen.submit('save')
        assert shown(app, '#settings-status') == 'Invalid accent.'
        assert app.preferences == current and app.preference_store.path.read_bytes() == stored
        await app.screen.submit('defaults')
        assert shown(app, '#settings-status') == 'Defaults preview — Save to keep; Cancel restores.'
        await app.screen.submit('save')
        assert app.preference_store.load() == Preferences()
        assert [c for c in p.mock_calls if c[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not legacy.path.exists() and not library.path.exists()
        svg_file = legacy.path.parent / f'settings-{size[0]}x{size[1]}.svg'
        (legacy.path.parent / f'settings-{size[0]}x{size[1]}.svg').write_text(app.export_screenshot(), encoding='utf-8')
        assert svg_file.read_text(encoding='utf-8').startswith('<svg')


@pytest.mark.asyncio
async def test_corrupt_and_failed_save_leave_live_preferences_unchanged(ui):
    app, p, shell, _, _ = ui
    app.preference_store.path.parent.mkdir(parents=True)
    app.preference_store.path.write_text('{oops')
    app.preference_store.load()
    async with app.run_test() as pilot:
        await pilot.press('f2')
        assert shown(app, '#settings-status').startswith('Cannot read appearance preferences:')
        before = app.preferences
        app.screen.query_one('#pref-accent', Input).value = '#ffffff'
        await app.screen.submit('save')
        assert shown(app, '#settings-status').startswith('Cannot read appearance preferences:')
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
            widget_color = app.screen_stack[0].query_one('#playlist').styles.color
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
        await app.screen.submit('model')
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
        await app.screen.submit('model')
        assert isinstance(app.screen, ModelPicker)
        await pilot.pause()
        await pilot.press('down', 'enter')
        assert shell.model == 'provider/chosen-model'
        await pilot.press('escape', 'm')
        assert isinstance(app.screen, ModelPicker)
        p.set_style.assert_not_called()
        p.set_playlist.assert_not_called()
