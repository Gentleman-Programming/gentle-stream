"""Approved terminal toolbar behavior, using the target's device-free UI fixture."""
from dataclasses import replace

import pytest
from rich.cells import cell_len
from textual.widgets import Button, Input

from tests.test_ui_toolbar_contracts import ui


@pytest.mark.asyncio
@pytest.mark.parametrize('gap,pencil,mid,wide', [
    (2, False, 72, 84), (2, True, 84, 84), (3, False, 82, 94), (3, True, 96, 96),
])
async def test_controls_wrap_at_group_fit_boundaries(ui, gap, pencil, mid, wide):
    app, player, shell, store, library = ui
    app.apply_preferences(replace(app.preferences, icon_gap=gap, show_input=not pencil))
    right = ['quieter', 'volume-meter', 'louder'] + (['mood-pencil'] if pencil else []) + ['settings-command']
    groups = [['panic', 'save-command', 'continuous', 'playlist-command'], ['previous', 'play', 'next'], right]
    async with app.run_test(size=(120, 24)) as pilot:
        for width, height, rows in [(40, 16, 3), (mid - 1, 24, 3), (mid, 24, 2 if mid < wide else 1),
                                    (wide - 1, 24, 2 if mid < wide else 3), (wide, 24, 1), (110, 24, 1)]:
            await pilot.resize_terminal(width, height)
            bar = app.query_one('#commands').region
            assert (bar.width, bar.height) == (width - 2, rows * 3)
            assert bar.bottom <= height
            assert app.query_one('Footer').region.bottom <= height
            boxes = [[app.query_one(f'#{name}').region for name in group] for group in groups]
            left, center, right_boxes = boxes
            assert abs(center[0].x + center[-1].right - (2 * bar.x + bar.width)) <= 1
            for regions in boxes:
                assert all(bar.contains_region(box) for box in regions)
                assert all(b.x - a.right == gap for a, b in zip(regions, regions[1:]))
            if rows == 1:
                assert left[0].x == bar.x and right_boxes[-1].right == bar.right
                assert left[-1].right + gap <= center[0].x
            if rows <= 2:
                assert center[-1].right + gap <= right_boxes[0].x
            assert (left[0].y < center[0].y) == (rows > 1)
            assert (center[0].y < right_boxes[0].y) == (rows == 3)
        player.toggle.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not store.path.exists() and not library.path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [(40, 16), (110, 24)])
async def test_text_glyphs_and_mood_pencil(ui, size):
    app, player, shell, store, library = ui
    app.apply_preferences(replace(app.preferences, show_input=False))
    async with app.run_test(size=size) as pilot:
        for name, glyph in [('panic', '!↺'), ('save-command', '▣↓'), ('continuous', '⇄'),
                            ('playlist-command', '☰'), ('previous', '←'), ('play', 'Ⅱ'), ('next', '→'),
                            ('quieter', '−'), ('louder', '+'), ('mood-pencil', '✎'), ('settings-command', '⛭')]:
            button = app.query_one(f'#{name}', Button)
            assert str(button.label) == glyph and button.tooltip
            assert cell_len(glyph) <= button.content_size.width and button.content_size.height == 1
            assert await pilot.hover(f'#{name}') and button.mouse_hover
        assert await pilot.click('#mood-pencil')
        assert app.query_one('#mood', Input).has_focus
        assert not app.query_one('#mood-pencil').display
        await pilot.press('escape')
        assert not app.query_one('#mood').display and app.query_one('#mood-pencil').display
        assert not app.preferences.show_input and not app.preference_store.path.exists()
        player.toggle.assert_not_called()
        shell.interpret_with_usage.assert_not_called()
        assert not store.path.exists() and not library.path.exists()
