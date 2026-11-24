import json

import numpy as np
import pytest
from rich.text import Text
from textual.widgets import Button

from gentle_stream import main
from tests.test_app import FakeShell, make_app, text


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    config = tmp_path / "gentle-stream"
    config.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return config


@pytest.fixture
def inert_app(monkeypatch):
    shell = FakeShell()
    app, player = make_app(shell)
    polls = []

    def spectrum(bands):
        polls.append(bands)
        return np.resize([0.0, 0.25, 0.75, 1.0], bands)

    monkeypatch.setattr(player, "spectrum", spectrum)
    tick = app._tick
    monkeypatch.setattr(app, "_tick", lambda: None if polls else tick())
    return app, player, shell, polls


@pytest.mark.asyncio
@pytest.mark.parametrize("size,visible", [((72, 24), False), ((95, 24), False), ((96, 24), True), ((139, 40), True)])
async def test_settings_discovery_and_clean_close(size, visible, inert_app, isolated_config):
    app, _, _, polls = inert_app
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        bar = app.query_one("#controls").region
        play, next_piece = [app.query_one(f"#{name}").region for name in ("play-pause", "next")]
        assert abs(play.x + next_piece.right - (2 * bar.x + bar.width)) <= 1
        settings = app.query_one("#settings", Button)
        assert settings.display is visible
        if visible:
            assert settings.region.right == bar.right
            assert settings.content_size.width == len("Settings")
            assert app.query_one("#volume-up").region.right + 2 == settings.region.x
            await pilot.click("#settings")
            assert text(app.screen, "#appearance-title") == "Appearance Settings"
            await pilot.click("#appearance-close")
        else:
            assert app.query_one("#volume-up").region.right == bar.right
            assert app.query_one("#volume-down").region.x == bar.right - 24
        await pilot.press("f2")
        assert text(app.screen, "#appearance-title") == "Appearance Settings"
        assert str(app.screen.query_one("#appearance-close", Button).label) == "Close"
        await pilot.press("escape", "ctrl+p")
        await pilot.press(*"appearance settings")
        await pilot.pause(0.2)
        await pilot.press("enter")
        assert text(app.screen, "#appearance-title") == "Appearance Settings"
        await pilot.press("escape")
        assert app.query_one("#play-pause").region == play
        assert len(polls) == 1
        assert list(isolated_config.iterdir()) == []


@pytest.mark.asyncio
async def test_draft_defaults_discard_save_and_reopen(inert_app, isolated_config):
    app, player, shell, polls = inert_app
    path = isolated_config / "appearance.json"
    presets = isolated_config / "presets.json"
    presets.write_bytes(b'{"version":1,"presets":[]}')
    async with app.run_test(size=(72, 24)) as pilot:
        spectrum = app.query_one("#spectrum")
        bars = str(spectrum.render())
        assert bars.splitlines()[0] == ("   █" * ((polls[0] + 3) // 4))[:polls[0]]
        await pilot.press("f2")
        preview = text(app.screen, "#appearance-preview")
        await pilot.press("down", "enter")
        wave = text(app.screen, "#appearance-preview")
        assert wave != preview and str(spectrum.render()) == bars
        assert str(app.screen.query_one("#appearance-save", Button).label) == "Save and Close"
        await pilot.click("#appearance-defaults")
        assert text(app.screen, "#appearance-preview") == preview
        assert app.screen.query_one("#appearance-save", Button).disabled
        await pilot.press("down", "down", "enter", "escape")
        assert text(app.screen, "#appearance-status") == "Discard unsaved changes?"
        await pilot.press("escape")
        assert text(app.screen, "#appearance-status") == "Preview only until saved."
        await pilot.click("#appearance-close")
        await pilot.click("#appearance-discard")
        assert str(spectrum.render()) == bars and not path.exists()
        await pilot.press("f2", "down", "down", "enter")
        mirror = text(app.screen, "#appearance-preview")
        assert mirror not in (wave, preview)
        await pilot.click("#appearance-save")
        assert json.loads(path.read_text(encoding="utf-8"))["preferences"]["style"] == "mirror"
        assert str(spectrum.render()) != bars
        await pilot.press("f2")
        assert text(app.screen, "#appearance-preview") == mirror
        assert app.screen.query_one("#appearance-save", Button).disabled
        await pilot.press("escape")
        assert len(polls) == 1 and player.playing and player.volume == 0.8 and shell.calls == []
        assert presets.read_bytes() == b'{"version":1,"presets":[]}'
    existing = json.loads(path.read_text(encoding="utf-8"))
    existing["preferences"].update(palette="analyzer", compact=True)
    path.write_text(json.dumps(existing), encoding="utf-8")
    reopened, _ = make_app(FakeShell())
    async with reopened.run_test(size=(139, 40)) as pilot:
        await pilot.press("f2")
        assert text(reopened.screen, "#appearance-preview") == mirror
        await pilot.click("#appearance-defaults")
        await pilot.click("#appearance-save")
        existing["preferences"]["style"] = "gentle"
        assert json.loads(path.read_text(encoding="utf-8")) == existing


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [b"{", b'{"version":1,"preferences":[]}', b'\xff'])
async def test_bad_appearance_is_not_overwritten(bad, inert_app, isolated_config):
    app, _, _, polls = inert_app
    path = isolated_config / "appearance.json"
    path.write_bytes(bad)
    async with app.run_test(size=(72, 24)) as pilot:
        await pilot.press("f2")
        assert text(app.screen, "#appearance-status") == "Cannot read appearance.json. Fix or move it before saving."
        await pilot.press("down", "enter")
        await pilot.click("#appearance-save")
        assert text(app.screen, "#appearance-status") == "Cannot read appearance.json. Fix or move it before saving."
        assert path.read_bytes() == bad
        assert len(polls) == 1


@pytest.mark.parametrize("style, expected", [
    ("gentle", "  ▂█\n  ██\n ▆██"),
    ("wave", "  ▁▇\n  ██\n ▆██"),
    ("mirror", "  ▄█\n  ██\n  ▀█"),
    ("segmented", "    ▪ ▪\n    ▪ ▪\n  ▪ ▪ ▪"),
    ("dots", "  ••\n  ••\n •••"),
])
def test_desktop_renderer_glyphs_and_palette(style, expected):
    from gentle_stream.appearance import Preferences
    from gentle_stream.visualizer import render_spectrum

    frame = render_spectrum(np.array([0.0, 0.25, 0.75, 1.0]), 3, Preferences(style=style))
    assert frame == Text("\n").join(Text(line, style=color) for line, color in zip(
        expected.splitlines(), ("#ff76c3", "#f095c8", "#a78e9b"), strict=True))


def test_help_discovers_settings_without_starting_audio(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out, err = capsys.readouterr()
    assert "  F2       open Appearance Settings (also in Ctrl+P commands)\n" in out
    assert err == ""
