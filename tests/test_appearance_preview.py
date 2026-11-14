"""Settings drafts are a display-only full-player miniature."""
from dataclasses import asdict, replace
from unittest.mock import Mock

import numpy as np
import pytest
from textual.geometry import Region
from textual.widgets import Button, Input, Select, Static, Switch

from gentle_stream.appearance import Preferences
from gentle_stream.appearance_ui import SettingsScreen
from tests import test_ui_toolbar_contracts as toolbar

ui, shown = toolbar.ui, toolbar.shown


def rendered_segments(widget):
    """Resolved widget output, including content spans and blank canvas cells."""
    return [segment for strip in widget.render_lines(
        Region(0, 0, widget.size.width, widget.size.height)) for segment in strip
        if segment.text and not segment.control]


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [(120, 30), (140, 40), (80, 24), (60, 20)])
async def test_complete_read_only_command_and_footer_facsimiles(ui, size):
    app, player, shell, _, _ = ui
    shell.model = 'provider/chosen-model'
    async with app.run_test(size=size) as pilot:
        preferences, theme, timer = app.preferences, app.theme, app._ui_timer
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2')
        screen = app.screen
        screen.query_one('#pref-icon_gap', Input).value = '3'
        await pilot.pause()
        pane = screen.query_one('#appearance-preview')
        commands = pane.query_one('#preview-commands', Static)
        footer = pane.query_one('#preview-footer', Static)
        command_text = str(commands.render())
        for label in ('Panic', '💾', '↻   off', '✎', '☷', '⏮', '⏸', '⏭',
                      '80%', '▰', '⚙   provider/chosen-model'):
            assert label in command_text, label
        footer_text = str(footer.render())
        for binding in app.BINDINGS:
            if binding.show:
                assert binding.description in footer_text, binding.description
        for widget in (commands, footer):
            assert not widget.can_focus
            segments = rendered_segments(widget)
            assert segments
            # Wrapping must retain content, not ellipsize omitted command groups.
            resolved = ''.join(segment.text for segment in segments)
            assert '…' not in resolved
            assert all(strip.cell_length <= widget.size.width for strip in widget.render_lines(
                Region(0, 0, widget.size.width, widget.size.height)))
        assert not list(pane.query(Button)) and not list(pane.query(Input))
        screen.query_one('#pref-show_controls', Switch).value = False
        screen.query_one('#pref-show_footer', Switch).value = False
        await pilot.pause()
        assert not commands.display and not footer.display
        assert app.preferences == preferences and app.theme == theme and app._ui_timer is timer
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not app.preference_store.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('theme,expected', [('gentle', '#14312b'), ('black', '#000000'),
                                           ('charcoal', '#202020')])
async def test_preview_resolved_cell_backgrounds_match_draft_canvas(ui, theme, expected):
    app, player, shell, _, _ = ui
    player.spectrum.side_effect = lambda n: np.linspace(.1, .9, n)
    player.now.style = replace(player.now.style, mood='Quiet forest')
    app.preference_store.save(app.preferences)
    stored = app.preference_store.path.read_bytes()
    async with app.run_test(size=(140, 40)) as pilot:
        opening, owner_theme, timer = app.preferences, app.theme, app._ui_timer
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        await pilot.press('f2')
        screen = app.screen
        screen.query_one('#pref-theme', Select).value = theme
        screen.query_one('#pref-background', Input).value = '#14312b'
        screen.query_one('#pref-accent', Input).value = '#81d4fa'
        await pilot.pause(.2)
        pane = screen.query_one('#appearance-preview')
        for name in ('title', 'details', 'mood', 'spectrum', 'state', 'model', 'status',
                     'input', 'footer', 'commands'):
            widget = pane.query_one('#preview-' + name, Static)
            segments = rendered_segments(widget)
            assert segments, name
            background = '#000000' if name == 'commands' else expected
            actual = {segment.style.bgcolor.get_truecolor().hex
                      if segment.style and segment.style.bgcolor else None
                      for segment in segments}
            assert actual == {background}, (name, actual)
        spectrum_colors = {segment.style.color.get_truecolor().hex
                           for segment in rendered_segments(pane.query_one('#preview-spectrum'))
                           if segment.style and segment.style.color}
        assert '#81d4fa' not in spectrum_colors
        assert spectrum_colors & {'#ff76c3', '#f095c8', '#ffb1dd', '#a78e9b', '#76616b'}
        assert app.preferences == opening and app.theme == owner_theme and app._ui_timer is timer
        assert app.preference_store.path.read_bytes() == stored
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_settings_late_refresh_after_children_removed_keeps_owner_polling(ui):
    app, player, shell, legacy, library = ui
    async with app.run_test(size=(120, 30)) as pilot:
        timer = app._ui_timer
        preferences, theme = app.preferences, app.theme
        before = [call for call in player.mock_calls if call[0] != 'spectrum']
        for _ in range(3):
            await pilot.press('f2', 'f2')
            screen = app.screen
            assert isinstance(screen, SettingsScreen)
            assert sum(isinstance(s, SettingsScreen) for s in app.screen_stack) == 1
            polls = player.spectrum.call_count
            await pilot.pause(.15)
            assert player.spectrum.call_count > polls
            await pilot.press('escape')
            assert not isinstance(app.screen, SettingsScreen)
            screen.refresh_preview()  # a retained callback after ordinary dismissal
        await pilot.press('f2')
        screen = app.screen
        pane = screen.query_one('#appearance-preview')
        await screen.remove_children()  # deterministic child-before-screen teardown gap
        assert app.screen is screen and not pane.is_attached
        screen.refresh_preview()
        polls = player.spectrum.call_count
        app._tick()  # the same retained owner callback must remain safe in this gap
        assert player.spectrum.call_count == polls + 1
        await pilot.pause(.2)
        assert player.spectrum.call_count > polls + 1
        await pilot.press('escape')
        screen.refresh_preview()
        assert not isinstance(app.screen, SettingsScreen)
        assert app._ui_timer is timer
        assert app.preferences == preferences and app.theme == theme
        assert [call for call in player.mock_calls if call[0] != 'spectrum'] == before
        player.start.assert_called_once()
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        shell.cancel.assert_not_called()
        assert not app.preference_store.path.exists()
        assert not legacy.path.exists() and not library.path.exists()


@pytest.mark.asyncio
async def test_all_valid_fields_update_only_draft_and_invalid_retains_it(ui):
    app, player, shell, _, _ = ui
    app.preference_store.save(app.preferences)
    stored = app.preference_store.path.read_bytes()
    async with app.run_test(size=(120, 30)) as pilot:
        opening, theme, timer = app.preferences, app.theme, app._ui_timer
        root = app.screen
        before = [c for c in player.mock_calls if c[0] != 'spectrum']
        await pilot.press('f2')
        pane = app.screen.query_one('#appearance-preview')
        changes = dict(style='dots', palette='mono', theme='charcoal', background='#123456',
                       accent='#abcdef', height=8, bands=64, refresh_hz=20, smoothing=.5,
                       peak_hold=1., icon_gap=3, **{k: False for k, v in asdict(opening).items()
                                                  if type(v) is bool})
        draft = opening
        for key, value in changes.items():
            widget = app.screen.query_one('#pref-' + key)
            widget.value = value if isinstance(widget, (Select, Switch)) else str(value)
            await pilot.pause()
            draft = replace(draft, **{key: value})
            assert pane.preferences == draft, key
            assert app.preferences == opening and app.theme == theme
            assert app._ui_timer is timer and root.query_one('#commands').display
            assert app.preference_store.path.read_bytes() == stored
        for name in ('spectrum', 'commands', 'input', 'footer', 'details', 'mood', 'model'):
            assert not pane.query_one('#preview-' + name).display
        for key, value in [('bands', 'bad'), ('accent', 'red'), ('background', '#xyzxyz')]:
            widget = app.screen.query_one('#pref-' + key, Input)
            widget.value = value
            await pilot.pause()
            assert shown(app, '#settings-status') == f'Invalid {key}.'
            assert pane.preferences == draft
            widget.value = str(getattr(draft, key))
            await pilot.pause()
        assert [c for c in player.mock_calls if c[0] != 'spectrum'] == before
        shell.list_models.assert_not_called()
        shell.interpret_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_theme_blur_restores_selected_draft_and_invalid_close_keeps_last_valid(ui):
    app, _, _, _, _ = ui
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.press('f2')
        screen = app.screen
        pane = screen.query_one('#appearance-preview')
        theme = screen.query_one('#pref-theme', Select)
        theme.focus()
        await pilot.press('enter', 'down')
        assert pane.preferences.theme == 'black' and theme.value == 'gentle'
        screen.query_one('#pref-accent', Input).focus()
        await pilot.pause()
        assert not theme.expanded and pane.preferences.theme == 'gentle'
        screen.query_one('#pref-accent', Input).value = 'invalid'
        await pilot.pause()
        valid = pane.preferences
        theme.focus()
        await pilot.press('enter', 'down', 'enter')
        assert pane.preferences == valid
        assert shown(app, '#settings-status') == 'Invalid accent.'
        assert app.preferences == valid


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [(120, 30), (140, 40), (80, 24), (60, 20)])
async def test_full_facsimile_geometry_resize_and_read_only_polling(ui, size, monkeypatch):
    app, player, shell, _, _ = ui
    player.spectrum.side_effect = lambda n: np.linspace(.1, .9, n)
    async with app.run_test(size=size) as pilot:
        before = [c for c in player.mock_calls if c[0] != 'spectrum']
        await pilot.press('f2')
        screen = app.screen
        pane = screen.query_one('#appearance-preview')
        form = screen.query_one('#settings-form')
        if size[0] >= 120:
            assert form.region.right <= pane.region.x
        else:
            assert form.region.bottom <= pane.region.y
        for name in ('title', 'details', 'mood', 'spectrum', 'state', 'model', 'status',
                     'commands', 'input', 'footer'):
            widget = pane.query_one('#preview-' + name, Static)
            assert widget.display and not widget.can_focus
        commands = str(pane.query_one('#preview-commands').render())
        assert all(text in commands for text in ('⏮', '⏸', '⏭', '80%', '▰'))
        timer = app._ui_timer
        owner_tick = Mock(wraps=app._w['spectrum'].tick)
        monkeypatch.setattr(app._w['spectrum'], 'tick', owner_tick)
        polls = player.spectrum.call_count
        player.now.piece = 23
        player.playing = False
        player.volume = .37
        await pilot.pause(.3)
        assert player.spectrum.call_count > polls
        assert player.spectrum.call_count - polls == owner_tick.call_count
        assert '23' in str(pane.query_one('#preview-state').render())
        assert '37%' in str(pane.query_one('#preview-commands').render())
        assert app._ui_timer is timer
        screen.query_one('#settings-save', Button).focus()
        await pilot.pause()
        assert screen.query_one('#settings-save').region.bottom <= size[1]
        await pilot.press('tab', 'tab')
        assert screen.query_one('#settings-cancel').has_focus
        await pilot.resize_terminal(140 if size[0] < 120 else 60, 40 if size[0] < 120 else 20)
        await pilot.pause()
        assert screen.query_one('#settings-actions').region.bottom <= app.size.height
        screen.query_one('#preview-commands').scroll_visible(animate=False)
        await pilot.pause()
        assert await pilot.click('#preview-commands')
        await pilot.press('space', 'n', 'plus', 'enter')
        assert [c for c in player.mock_calls if c[0] != 'spectrum'] == before
        shell.interpret_with_usage.assert_not_called()
        player.start.assert_called_once()


@pytest.mark.asyncio
async def test_cache_hidden_defensive_copy_and_model_picker_preservation(ui):
    app, player, shell, _, _ = ui
    player.spectrum.side_effect = lambda n: np.linspace(.1, .9, n)
    async with app.run_test(size=(120, 30)) as pilot:
        spectrum = app.query_one('#spectrum')
        spectrum.display = False
        await pilot.pause(.2)
        sample = spectrum.cached_sample
        assert sample.max() > 0
        sample[:] = 0
        assert spectrum.cached_sample.max() > 0
        await pilot.press('f2')
        screen = app.screen
        pane = screen.query_one('#appearance-preview')
        await screen.submit('model')
        await pilot.pause()
        player.now.piece = 29
        await pilot.pause(.2)
        assert '29' in str(pane.query_one('#preview-state').render())
        await pilot.press('down', 'enter')
        await pilot.pause()
        assert app.screen is screen and shell.model == 'provider/chosen-model'
        await pilot.pause(.2)
        assert 'provider/chosen-model' in str(pane.query_one('#preview-model').render())
        player.toggle.assert_not_called()
        player.set_style.assert_not_called()


@pytest.mark.asyncio
async def test_preview_defaults_and_save_transaction_apply_exactly_once(ui, monkeypatch):
    app, player, shell, _, _ = ui
    app.preference_store.save(app.preferences)
    stored = app.preference_store.path.read_bytes()
    async with app.run_test(size=(120, 30)) as pilot:
        original = app.apply_preferences
        apply = Mock(wraps=original)
        monkeypatch.setattr(app, 'apply_preferences', apply)
        await pilot.press('f2')
        screen = app.screen
        opening = app.preferences
        screen.query_one('#pref-bands', Input).value = '64'
        await pilot.pause()
        await screen.submit('preview')
        await screen.submit('defaults')
        await pilot.pause()
        assert screen.query_one('#appearance-preview').preferences == Preferences()
        assert screen.read_preferences() == Preferences()
        apply.assert_not_called()
        assert app.preference_store.path.read_bytes() == stored
        screen.query_one('#pref-accent', Input).value = '#123456'
        await pilot.pause()
        await screen.submit('save')
        saved = replace(opening, accent='#123456')
        apply.assert_called_once_with(saved, persist=True)
        assert app.screen is screen and screen.baseline == saved
        assert app.preferences == saved and app.preference_store.load() == saved
        screen.query_one('#pref-accent', Input).value = '#abcdef'
        await pilot.pause()
        await pilot.press('escape')
        apply.assert_called_once()
        assert app.preferences == saved and app.preference_store.load() == saved
        shell.interpret_with_usage.assert_not_called()
