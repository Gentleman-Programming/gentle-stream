import json
from dataclasses import asdict

import numpy as np
import pytest
from rich.text import Text
from textual.widgets import Button

from gentle_stream import main
from gentle_stream.appearance import Preferences
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
@pytest.mark.parametrize("width", [52, 60, 64, 72, 95, 96])
async def test_volume_up_remains_visible_and_clear_of_center(width, inert_app, isolated_config):
    app, player, shell, polls = inert_app
    async with app.run_test(size=(width, 24)) as pilot:
        await pilot.pause()
        bar = app.query_one("#controls").region
        play, next_piece = [app.query_one(f"#{id}", Button).region for id in ("play-pause", "next")]
        down, up = [app.query_one(f"#volume-{side}", Button) for side in ("down", "up")]
        assert abs(play.x + next_piece.right - (2 * bar.x + bar.width)) <= 1
        assert up.region.right <= bar.right
        assert next_piece.right + 2 <= down.region.x
        assert up.region.width == down.region.width
        assert up.content_size.width >= 1 and down.content_size.width >= 1
        assert app.query_one("#settings", Button).display is (width >= 96)
        assert await pilot.click("#volume-up")
        assert player.volume == pytest.approx(0.85)
        assert player.playing and shell.calls == [] and len(polls) == 1
        assert list(isolated_config.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('width', [40, 44])
async def test_split_pane_puts_the_full_volume_group_on_its_own_row(width, inert_app):
    app, player, shell, _ = inert_app
    async with app.run_test(size=(72, 24)) as pilot:
        await pilot.resize_terminal(width, 24)
        await pilot.pause()
        bar = app.query_one('#controls').region
        play, next_piece = [app.query_one(f'#{name}', Button).region for name in ('play-pause', 'next')]
        down, value, up = [app.query_one(f'#{name}') for name in ('volume-down', 'volume', 'volume-up')]
        assert abs(play.x + next_piece.right - (2 * bar.x + bar.width)) <= 1
        assert next_piece.bottom <= down.region.y == up.region.y
        assert bar.x <= down.region.x < value.region.x < up.region.x < up.region.right <= bar.right
        assert up.region.bottom <= bar.bottom
        assert await pilot.click('#volume-up')
        assert player.volume == pytest.approx(0.85) and shell.calls == []
        await pilot.resize_terminal(52, 24)
        await pilot.pause()
        assert app.query_one('#play-pause').region.y == up.region.y


@pytest.mark.asyncio
async def test_style_save_preserves_unknown_preferences(inert_app, isolated_config):
    app, _, shell, polls = inert_app
    path = isolated_config / "appearance.json"
    sibling = isolated_config / "presets.json"
    sibling.write_bytes(b'{"version":1,"presets":[]}')
    record = asdict(Preferences(style="wave", palette="analyzer", compact=True))
    record["vendor_extension"] = {"theme": ["夜", {"contrast": 2, "flags": [True, None]}]}
    original = {"version": 1, "preferences": record}
    before = json.dumps(original, ensure_ascii=False).encode("utf-8")
    path.write_bytes(before)
    async with app.run_test(size=(72, 24)) as pilot:
        spectrum = app.query_one("#spectrum")
        live = str(spectrum.render())
        await pilot.press("f2")
        await pilot.press("down", "enter")
        assert path.read_bytes() == before and str(spectrum.render()) == live
        await pilot.click("#appearance-save")
        assert not app.screen.query("#appearance-title")
        assert str(spectrum.render()) != live
        record["style"] = "mirror"
        assert json.loads(path.read_text(encoding="utf-8")) == original
        assert sibling.read_bytes() == b'{"version":1,"presets":[]}'
        assert set(isolated_config.iterdir()) == {path, sibling}
        assert shell.calls == [] and len(polls) == 1


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
@pytest.mark.parametrize("bad", [
    b"{", b'{"version":1,"preferences":[]}', b'\xff',
    *(json.dumps({"version": version, "preferences": asdict(Preferences())}).encode()
      for version in (2, True)),
    json.dumps({"version": 1, "preferences": {**asdict(Preferences()), "compact": 1}}).encode(),
    json.dumps({"version": 1, "preferences": {"style": "gentle"}}).encode(),
])
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
