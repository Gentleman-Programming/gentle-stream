import threading

import pytest

from gentle_stream.app import ModelPicker, PresetPicker, SaveDialog, StreamApp
from gentle_stream.bridge import InterpretError, Usage
from gentle_stream.engine import Engine
from gentle_stream.player import Player
from gentle_stream.presets import PresetStore
from gentle_stream.style import Style
from tests.test_player import FakeStream


class FakeShell:
    def __init__(self, answer=None, error=None, models=("claude-bridge/claude-haiku-5-5", "openai-codex/gpt-6-luna"),
                 usage=None):
        self.answer, self.error, self.models, self.usage = answer, error, list(models), usage
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

    def interpret_with_usage(self, mood, current):
        return self.interpret(mood, current), self.usage

    def list_models(self):
        return self.models

    def cancel(self):
        pass


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """No test may touch the real ~/.config, even through the default preset store."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


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
async def test_plus_and_minus_change_the_volume_in_steps_of_5():
    app, player = make_app(FakeShell())
    async with app.run_test() as pilot:
        assert player.volume == pytest.approx(0.8)
        await pilot.press("minus")
        assert player.volume == pytest.approx(0.75)
        await pilot.pause(0.1)
        assert "vol 75%" in text(app, "#state")
        await pilot.press("plus", "plus")
        assert player.volume == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_up_and_down_change_the_volume_in_steps_of_1():
    app, player = make_app(FakeShell())
    async with app.run_test() as pilot:
        await pilot.press("down")
        assert player.volume == pytest.approx(0.79)
        await pilot.pause(0.1)
        assert "vol 79%" in text(app, "#state")
        await pilot.press("up", "up")
        assert player.volume == pytest.approx(0.81)


@pytest.mark.asyncio
async def test_volume_steps_stop_at_0_and_100_percent():
    app, player = make_app(FakeShell())
    async with app.run_test() as pilot:
        player.set_volume(0.03)
        await pilot.press("minus")
        assert player.volume == 0.0
        player.set_volume(0.98)
        await pilot.press("plus")
        assert player.volume == 1.0
        await pilot.press("down")
        assert player.volume == pytest.approx(0.99)


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


@pytest.mark.asyncio
async def test_quitting_during_an_interpretation_cancels_it_right_away():
    import time

    shell = FakeShell(answer={"name": "Late"})
    shell.release.clear()
    shell.cancel = shell.release.set            # cancelling unblocks the in-flight call
    app, player = make_app(shell)
    async with app.run_test() as pilot:
        await pilot.press("slash", *"rain", "enter")
        await pilot.pause(0.1)
        t0 = time.monotonic()
    assert time.monotonic() - t0 < 2
    assert shell.release.is_set()


@pytest.mark.asyncio
async def test_an_error_with_brackets_is_shown_literally():
    app, _ = make_app(FakeShell(error="bad [model] id"))
    async with app.run_test() as pilot:
        await pilot.press("slash", *"x", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert "bad [model] id" in text(app, "#status")


# ------------------------------------------------------------ token usage
CALL = Usage(input=17000, output=700, cache_write=16000, total=17700, cost_usd=0.00075)


@pytest.mark.asyncio
async def test_each_interpretation_shows_its_usage_and_the_session_total_adds_up():
    shell = FakeShell(answer={"name": "Summer Storm"}, usage=CALL)
    shell.model = "openai-codex/gpt-6-luna"
    app, _ = make_app(shell)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        assert "session" not in text(app, "#model")       # nothing spent yet
        await pilot.press("slash", *"storm", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        status = text(app, "#status")
        assert "Up next: Summer Storm" in status and "17.7k tokens · $0.0008" in status
        assert text(app, "#model") == "model: openai-codex/gpt-6-luna · session: 17.7k tokens · $0.0008"
        await pilot.press("slash", *"again", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert "session: 35.4k tokens · $0.0015" in text(app, "#model")


@pytest.mark.asyncio
async def test_unknown_usage_shows_the_style_and_adds_nothing():
    app, _ = make_app(FakeShell(answer={"name": "Old Shell"}))
    async with app.run_test() as pilot:
        await pilot.press("slash", *"x", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert "Up next: Old Shell" in text(app, "#status") and "tokens" not in text(app, "#status")
        assert "session" not in text(app, "#model")


@pytest.mark.asyncio
async def test_a_failed_interpretation_adds_nothing_to_the_session(store):
    shell = FakeShell(answer={"name": "Ok"}, usage=CALL)
    app, _ = make_app(shell, presets=store)
    async with app.run_test() as pilot:
        await pilot.press("slash", *"ok", "enter")
        await app.workers.wait_for_complete()
        shell.error = "boom"
        await pilot.press("slash", *"bad", "enter")
        await app.workers.wait_for_complete()
        await pilot.pause(0.1)
        assert "boom" in text(app, "#status")
        assert "session: 17.7k tokens · $0.0008" in text(app, "#model")
        store.save("Rain", RAIN)
        await pilot.press("p", "enter")                  # applying a saved prompt is free
        await pilot.pause(0.1)
        assert "session: 17.7k tokens · $0.0008" in text(app, "#model")


# ------------------------------------------------------------ saved prompts
RAIN = Style(name="Rainy Café", mood="rainy night in a small café", key="F", mode="dorian", bpm=81.0,
             progression=(1, 4, 1, 4), chords="rhodes", lead="nylon", drums="soft")


@pytest.fixture
def store(tmp_path):
    return PresetStore(tmp_path / "presets.json")


@pytest.mark.asyncio
async def test_s_saves_the_audible_style_under_a_typed_name(store):
    app, player = make_app(FakeShell(), presets=store)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        audible = player.now.style
        await pilot.press("s")
        await pilot.pause(0.1)
        assert isinstance(app.screen, SaveDialog)
        field = app.screen.query_one("Input")
        assert field.value == audible.name               # prefilled with the style name
        field.value = ""
        await pilot.press(*"late night", "enter")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, SaveDialog)
        assert "Saved “late night”" in text(app, "#status")
        preset = store.get("late night")
        assert preset.style.to_dict() == {**audible.to_dict(), "name": "late night"}
        assert preset.mood == audible.mood
        assert app.query_one("#mood").value == ""        # the dialog's Enter is not a mood request


@pytest.mark.asyncio
async def test_saving_an_existing_name_says_it_replaced_it(store):
    store.save("Late Night", RAIN)
    app, _ = make_app(FakeShell(), presets=store)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause(0.1)
        app.screen.query_one("Input").value = "late night"
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert "Replaced “late night”" in text(app, "#status")
        assert [p.name for p in store.list()] == ["late night"]


@pytest.mark.asyncio
async def test_an_empty_name_is_rejected(store):
    shell = FakeShell()
    app, _ = make_app(shell, presets=store)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause(0.1)
        app.screen.query_one("Input").value = "   "
        await pilot.press("enter")
        await pilot.pause(0.1)
        assert isinstance(app.screen, SaveDialog)
        assert "empty" in text(app.screen, "#save-status").lower()
        assert store.list() == [] and shell.calls == []


@pytest.mark.asyncio
async def test_escape_cancels_the_save_dialog(store):
    app, player = make_app(FakeShell(), presets=store)
    async with app.run_test() as pilot:
        await pilot.press("s")
        await pilot.pause(0.1)
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, SaveDialog)
        assert store.list() == []
        await pilot.press("space")                     # keys control playback again
        assert not player.playing


@pytest.mark.asyncio
async def test_p_applies_a_saved_prompt_instantly_without_gentle_shell(store):
    store.save("Archive", Style(mood="old tapes"))
    saved, _ = store.save("Rainy Café", RAIN)
    shell = FakeShell(answer={"name": "unused"})
    app, player = make_app(shell, presets=store)
    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause(0.1)
        assert isinstance(app.screen, PresetPicker)
        listed = str(app.screen.query_one("OptionList").get_option_at_index(1).prompt)
        assert "Rainy Café" in listed and "rainy night in a small café" in listed
        await pilot.press("down", "enter")             # Archive, Rainy Café
        await pilot.pause(0.1)
        assert not isinstance(app.screen, PresetPicker)
        assert shell.calls == []
        queued = player.engine._pending_style or player.engine.style
        assert queued == saved.style
        assert (queued.name, queued.mood) == ("Rainy Café", "rainy night in a small café")
        assert "Up next: Rainy Café (saved)" in text(app, "#status")


@pytest.mark.asyncio
async def test_d_deletes_the_highlighted_preset(store):
    store.save("Archive", Style())
    store.save("Rainy Café", RAIN)
    app, _ = make_app(FakeShell(), presets=store)
    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause(0.1)
        await pilot.press("d")
        await pilot.pause(0.1)
        assert [p.name for p in store.list()] == ["Rainy Café"]
        assert app.screen.query_one("OptionList").option_count == 1
        await pilot.press("delete")
        await pilot.pause(0.1)
        assert store.list() == []
        assert "No saved prompts yet" in text(app.screen, "#presets-status")


@pytest.mark.asyncio
async def test_an_empty_store_shows_a_hint_and_escape_closes_the_presets(store):
    app, player = make_app(FakeShell(), presets=store)
    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause(0.1)
        assert "No saved prompts yet" in text(app.screen, "#presets-status")
        await pilot.press("enter")
        await pilot.press("escape")
        await pilot.pause(0.1)
        assert not isinstance(app.screen, PresetPicker)
        assert player.engine._pending_style is None


@pytest.mark.asyncio
async def test_a_corrupt_presets_file_is_reported_in_the_picker(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text("{oops", encoding="utf-8")
    app, _ = make_app(FakeShell(), presets=PresetStore(path))
    async with app.run_test() as pilot:
        await pilot.press("p")
        await pilot.pause(0.1)
        assert "presets.json" in text(app.screen, "#presets-status")
    assert path.read_text(encoding="utf-8") == "{oops"
