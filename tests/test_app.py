import threading

import pytest

from gentle_stream.app import ModelPicker, StreamApp
from gentle_stream.bridge import InterpretError
from gentle_stream.engine import Engine
from gentle_stream.player import Player
from gentle_stream.style import Style
from tests.test_player import FakeStream


class FakeShell:
    def __init__(self, answer=None, error=None, models=("claude-bridge/claude-haiku-5-5", "openai-codex/gpt-6-luna")):
        self.answer, self.error, self.models = answer, error, list(models)
        self.model = None
        self.calls = []
        self.release = threading.Event()
        self.release.set()

    def interpret(self, mood, current):
        self.calls.append((mood, current))
        self.release.wait(5)
        if self.error:
            raise InterpretError(self.error)
        return Style.from_dict({**self.answer, "mood": mood}, current)

    def list_models(self):
        return self.models


def make_app(shell, **kw):
    player = Player(Engine(Style(name="Midnight Rain"), seed=1), stream_factory=lambda **k: FakeStream(**k),
                    prebuffer=0.3)
    return StreamApp(player, shell, **kw), player


def text(app, selector):
    return str(app.query_one(selector).render())


@pytest.mark.asyncio
async def test_shows_what_is_playing_and_the_controls():
    app, player = make_app(FakeShell())
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        assert "Midnight Rain" in text(app, "#title")
        assert "D minor" in text(app, "#details")
        assert "Playing" in text(app, "#state")
    assert not player._producer.is_alive()        # quitting closes the audio


@pytest.mark.asyncio
async def test_space_pauses_and_resumes():
    app, player = make_app(FakeShell())
    async with app.run_test() as pilot:
        await pilot.press("space")
        assert not player.playing
        await pilot.pause(0.1)
        assert "Paused" in text(app, "#state")
        await pilot.press("space")
        assert player.playing


@pytest.mark.asyncio
async def test_n_starts_the_next_piece():
    app, player = make_app(FakeShell())
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await pilot.press("n")
        await pilot.pause(0.2)
        assert player.engine.piece == 2 or player.engine._pending_next


@pytest.mark.asyncio
async def test_a_mood_is_interpreted_and_queued():
    shell = FakeShell(answer={"name": "Summer Storm", "mode": "mixolydian", "bpm": 78})
    app, player = make_app(shell)
    async with app.run_test() as pilot:
        await pilot.press("slash")
        await pilot.press(*"summer storm")
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert shell.calls[0][0] == "summer storm"
        assert "Summer Storm" in text(app, "#status")
        queued = player.engine._pending_style or player.engine.style
        assert (queued.name, queued.mode, queued.mood) == ("Summer Storm", "mixolydian", "summer storm")
        assert app.query_one("#mood").value == ""


@pytest.mark.asyncio
async def test_the_status_says_which_model_is_interpreting():
    shell = FakeShell(answer={"name": "x"})
    shell.model = "openai-codex/gpt-6-luna"
    shell.release.clear()
    app, _ = make_app(shell)
    async with app.run_test() as pilot:
        await pilot.press("slash", *"calm", "enter")
        await pilot.pause(0.1)
        assert "openai-codex/gpt-6-luna" in text(app, "#status")
        shell.release.set()
        await app.workers.wait_for_complete()


@pytest.mark.asyncio
async def test_an_interpretation_error_is_shown_and_the_music_keeps_playing():
    app, player = make_app(FakeShell(error='Model "nope" not found.'))
    async with app.run_test() as pilot:
        await pilot.press("slash", *"x", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert 'Model "nope" not found.' in text(app, "#status")
        assert player.playing and player.engine.style.name == "Midnight Rain"


@pytest.mark.asyncio
async def test_escape_leaves_the_mood_input_so_keys_control_playback():
    app, player = make_app(FakeShell())
    async with app.run_test() as pilot:
        await pilot.press("slash")
        await pilot.press("space")                 # typed into the input
        assert player.playing
        await pilot.press("escape", "space")
        assert not player.playing


@pytest.mark.asyncio
async def test_m_picks_the_model_gentle_shell_uses():
    shell = FakeShell()
    app, _ = make_app(shell)
    async with app.run_test() as pilot:
        await pilot.press("m")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ModelPicker)
        await pilot.press("down", "down", "enter")   # default, haiku, gpt-6-luna
        await pilot.pause(0.1)
        assert shell.model == "openai-codex/gpt-6-luna"
        assert "openai-codex/gpt-6-luna" in text(app, "#model")


@pytest.mark.asyncio
async def test_volume_keys_change_the_volume():
    app, player = make_app(FakeShell())
    async with app.run_test() as pilot:
        before = player.volume
        await pilot.press("minus")
        assert player.volume == pytest.approx(before - 0.1)
        await pilot.press("plus")
        assert player.volume == pytest.approx(before)


@pytest.mark.asyncio
async def test_an_initial_mood_is_interpreted_on_start():
    shell = FakeShell(answer={"name": "Opening"})
    app, _ = make_app(shell, initial_mood="foggy harbour")
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert shell.calls[0][0] == "foggy harbour"


@pytest.mark.asyncio
async def test_the_app_uses_the_gentleman_programming_theme():
    app, _ = make_app(FakeShell())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        assert app.theme == "gentleman"
        theme = app.current_theme
        assert (theme.primary, theme.background, theme.error) == ("#ff76c3", "#140a12", "#ff718f")
        assert app.query_one("#title").styles.color.hex.lower() == "#ff76c3"
