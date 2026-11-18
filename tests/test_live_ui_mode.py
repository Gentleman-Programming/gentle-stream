"""Session-only mode transitions through the real Textual interface."""
from dataclasses import replace
from threading import Event
from unittest.mock import Mock

import pytest
from textual.widgets import Input, Select

from gentle_stream.appearance_ui import SettingsScreen
from tests import test_ui_toolbar_contracts as toolbar

ui = toolbar.ui

SIZES = [(40, 8), (60, 12), (80, 24), (120, 30), (140, 40)]


def visible(app, selector):
    widget = app.screen_stack[0].query_one(selector)
    assert widget.display and widget.region.width and widget.region.height, selector
    assert app.screen_stack[0].region.contains_region(widget.region), (selector, widget.region)
    return widget


@pytest.mark.asyncio
async def test_button_and_r_retain_identity_state_and_have_no_side_effects(ui, monkeypatch):
    app, player, shell, store, library = ui
    app.preference_store.save(app.preferences)
    stored = app.preference_store.path.read_bytes()
    async with app.run_test(size=(80, 24)) as pilot:
        root, timer = app.screen, app._ui_timer
        # Footer regenerates its contextual hint children when focus changes;
        # the player DOM and Footer itself must retain their identities.
        owned = '#panel, #panel *, #commands, #commands *, #mood, #mode-toggle'
        widgets, footer = tuple(root.query(owned)), app._footer
        app._selection = ([player.now.style], 0, 'Chosen')
        selection = app._selection
        usage = app.session_usage = object()
        app.query_one('#mood', Input).value = 'unfinished forest'
        # Stop automatic polls, so transition-time calls cannot hide in timer work.
        timer.pause()
        player.reset_mock()
        shell.reset_mock()
        tick = Mock(wraps=app._tick)
        apply = Mock(wraps=app.apply_preferences)
        monkeypatch.setattr(app, '_tick', tick)
        monkeypatch.setattr(app, 'apply_preferences', apply)
        assert await pilot.click('#mode-toggle')
        await pilot.pause()
        assert app.ui_mode == 'mini'
        assert not app.query_one('#mood').display
        await pilot.press('slash')
        assert not app.query_one('#mood').display
        app.set_focus(None)
        await pilot.press('r')
        assert app.ui_mode == 'full'
        assert app.screen is root and app._ui_timer is timer
        assert tuple(root.query(owned)) == widgets and app._footer is footer
        assert app._selection is selection and app.session_usage is usage
        assert app.query_one('#mood', Input).value == 'unfinished forest'
        assert player.playing is True and player.volume == .8
        assert shell.model is None
        assert player.mock_calls == [] and shell.mock_calls == []
        tick.assert_not_called()
        apply.assert_not_called()
        assert app.preference_store.path.read_bytes() == stored
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
async def test_typing_r_and_dropdown_search_do_not_toggle(ui):
    app, _, _, _, _ = ui
    async with app.run_test() as pilot:
        await pilot.press('slash', 'r')
        assert app.query_one('#mood', Input).value == 'r'
        assert app.ui_mode == 'full'
        await pilot.press('f2')
        screen = app.screen
        theme = screen.query_one('#pref-theme', Select)
        theme.focus()
        await pilot.press('enter', 'r')
        assert theme.expanded and app.ui_mode == 'full'
        await pilot.press('escape', 'escape')
        app.set_focus(None)
        await pilot.press('r')
        assert app.ui_mode == 'mini'


@pytest.mark.asyncio
async def test_current_preferences_restore_and_settings_save_retains_mode(ui):
    app, player, shell, _, _ = ui
    preferences = replace(app.preferences, show_visualizer=False, show_controls=False,
                          show_input=False, show_footer=False, show_details=False,
                          show_model=False, height=12, compact=True)
    app.preference_store.save(preferences)
    app.preferences = preferences
    async with app.run_test(size=(80, 24)) as pilot:
        timer = app._ui_timer
        await pilot.click('#mode-toggle')
        for selector in ('#spectrum', '#commands', '#volume-meter', '#mode-toggle'):
            visible(app, selector)
        assert not app.query_one('#mood').display
        await pilot.press('f2')
        screen = app.screen
        assert isinstance(screen, SettingsScreen)
        screen.query_one('#pref-height', Input).value = '18'
        await pilot.pause()
        await screen.submit('save')
        assert app.ui_mode == 'mini' and app._ui_timer is not timer
        await pilot.press('escape')
        await pilot.click('#mode-toggle')
        assert app.ui_mode == 'full' and app.preferences.height == 18
        for selector in ('#spectrum', '#commands', '#mood', '#metadata', '#model'):
            assert not app.query_one(selector).display
        assert not app._footer.display
        assert app.query_one('#spectrum').runtime_height is None
        assert app.screen.has_class('compact')
        shell.interpret_with_usage.assert_not_called()
        player.start.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('size', SIZES)
async def test_geometry_actions_settings_and_resize_while_playing(ui, size):
    app, player, shell, _, _ = ui
    async with app.run_test(size=size) as pilot:
        timer = app._ui_timer
        for target in (size, (140, 40), size):
            await pilot.resize_terminal(*target)
            for mode in ('full', 'mini'):
                if app.ui_mode != mode:
                    app.set_focus(None)
                    await pilot.press('r')
                # Drain queued messages/layout without CPU-idle polling: the
                # live Settings preview intentionally keeps scheduling frames.
                await pilot.pause(0)
                visible(app, '#mode-toggle')
                for selector in ('#previous', '#play', '#next', '#quieter', '#volume-meter', '#louder'):
                    widget = app.query_one(selector)
                    widget.scroll_visible(animate=False, immediate=True)
                    await pilot.pause(0)
                    visible(app, selector)
                assert await pilot.click('#play')
                assert await pilot.click('#play')
                assert player.playing is True
                await pilot.press('f2')
                screen = app.screen
                assert isinstance(screen, SettingsScreen)
                # All fields and actions must be scroll-reachable with mouse and Tab.
                for widget in screen.query('Input, Select, Switch, Button'):
                    widget.focus()
                    widget.scroll_visible(animate=False, immediate=True)
                    await pilot.pause(0)
                    assert screen.region.contains_region(widget.region), (target, widget.id, widget.region)
                toggle = screen.query_one('#preview-mode-toggle')
                assert not toggle.can_focus
                stored = app.preference_store.path.read_bytes() if app.preference_store.path.exists() else None
                screen.query_one('#pref-height', Input).value = 'invalid'
                await pilot.pause(0)
                preferences, preview = app.preferences, screen.draft
                before = [c for c in player.mock_calls if c[0] != 'spectrum']
                await screen.submit('save')
                status = screen.query_one('#settings-status')
                assert status.display and screen.region.contains_region(status.region)
                assert str(status.render()) == 'Invalid height.'
                assert app.preferences == preferences and screen.draft == preview
                assert [c for c in player.mock_calls if c[0] != 'spectrum'] == before
                assert (app.preference_store.path.read_bytes() if app.preference_store.path.exists() else None) == stored
                await pilot.press('escape')
                assert app._ui_timer is timer and app.ui_mode == mode
        assert app.is_running and player.playing is True
        shell.interpret_with_usage.assert_not_called()
        shell.list_models.assert_not_called()
        player.start.assert_called_once()
        player.close.assert_not_called()


@pytest.mark.asyncio
async def test_pending_interpretation_finishes_in_same_mini_session(ui):
    app, player, shell, _, _ = ui
    entered, release = Event(), Event()
    style = replace(player.now.style, name='Forest')

    def interpret(*args):
        entered.set()
        assert release.wait(5)
        return style, None

    shell.interpret_with_usage.side_effect = interpret
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.press('slash', 'f', 'enter')
        try:
            for _ in range(50):
                if entered.is_set():
                    break
                await pilot.pause(.01)
            assert entered.is_set()
            await pilot.click('#mode-toggle')
            assert app.ui_mode == 'mini'
        finally:
            release.set()
        await app.workers.wait_for_complete()
        player.set_style.assert_called_once_with(style)
        shell.cancel.assert_not_called()
        assert app.ui_mode == 'mini'
