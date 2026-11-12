"""Playlist behavior with no audio device or model calls."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from gentle_stream.app import PresetPicker, StreamApp
from gentle_stream.genres import NAMES, REGISTRY
from gentle_stream.presets import PresetStore
from gentle_stream.style import DEFAULT, Style


class PlaylistTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="gentle-stream-playlists-"))
        print(f"Retained test scratch: {cls.root}", flush=True)

    def setUp(self):
        self.store = PresetStore(self.root / f"{self._testMethodName}.json")
        self.player = Mock(now=SimpleNamespace(style=DEFAULT, piece=1, bar=0), playing=True, volume=0.8,
                           continuous=False)
        self.player.spectrum.side_effect = lambda width: np.zeros(width)
        self.shell = Mock(model=None)
        self.app = StreamApp(self.player, self.shell, presets=self.store)

    def assert_no_ai(self):
        self.shell.interpret_with_usage.assert_not_called()
        self.shell.interpret.assert_not_called()
        self.shell.list_models.assert_not_called()

    async def test_builtin_content_is_registry_based_and_includes_actual_default(self):
        from gentle_stream.playlists import default_playlist
        styles = default_playlist()
        self.assertEqual(tuple(s.genre for s in styles), NAMES)
        self.assertEqual(styles[0], DEFAULT)
        self.assertEqual(styles, default_playlist())
        for style in styles[1:]:
            genre = REGISTRY[style.genre]
            self.assertEqual(style.mood, genre.description)
            self.assertEqual(style.bpm, sum(genre.bpm) / 2)
            self.assertEqual(style.mode, genre.modes[0])

    async def test_main_click_opener_empty_saved_switch_and_click_default(self):
        from gentle_stream.playlists import default_playlist
        async with self.app.run_test(size=(80, 24)) as pilot:
            self.assertTrue(await pilot.click("#playlist"))
            await pilot.pause()
            self.assertIsInstance(self.app.screen, PresetPicker)
            self.assertIn("No saved prompts yet", str(self.app.screen.query_one("#presets-status").render()))
            self.assertTrue(await pilot.click("#default-playlist"))
            options = self.app.screen.query_one("OptionList")
            styles = sorted(default_playlist(), key=lambda s: s.name.casefold())
            self.assertEqual(options.option_count, len(NAMES))
            for index, style in enumerate(styles):
                self.assertIn(style.name, str(options.get_option_at_index(index).prompt))
            self.assertEqual(str(self.app.screen.query_one("#my-playlist").label), "My playlist")
            self.assertEqual(str(self.app.screen.query_one("#default-playlist").label), "Default playlist")
            await pilot.press("d", "delete")
            self.assertEqual(options.option_count, len(NAMES))
            self.assertFalse(self.store.path.exists())
            self.assertTrue(await pilot.click("#presets", offset=(2, 1)))
            self.player.set_style.assert_called_once_with(styles[0])
            self.assertFalse(self.store.path.exists())
            self.assert_no_ai()

    async def test_saved_switch_click_apply_and_default_operations_do_not_write(self):
        saved, _ = self.store.save("My rain", Style(mood="quiet rain"))
        before = self.store.path.read_bytes()
        async with self.app.run_test() as pilot:
            await pilot.press("p")
            await pilot.pause()
            self.assertTrue(await pilot.click("#default-playlist"))
            await pilot.press("d", "delete")
            self.assertEqual(self.store.path.read_bytes(), before)
            self.assertTrue(await pilot.click("#my-playlist"))
            await pilot.press("enter")  # explicit legacy group
            await pilot.pause()
            self.assertIn("My rain", str(self.app.screen.query_one("OptionList").get_option_at_index(0).prompt))
            self.assertTrue(await pilot.click("#presets", offset=(2, 0)))
            self.player.set_style.assert_called_once_with(saved.style)
            self.assertEqual(self.store.path.read_bytes(), before)
            self.assert_no_ai()

    async def test_left_right_switch_playlists_from_list_and_tabs_without_applying(self):
        for populated in (False, True):
            with self.subTest(populated=populated):
                if populated:
                    self.store.save("My rain", Style(mood="quiet rain"))
                before = self.store.path.read_bytes() if populated else None
                app = StreamApp(self.player, self.shell, presets=self.store)
                async with app.run_test() as pilot:
                    await pilot.press("p")
                    await pilot.pause()
                    options = app.screen.query_one("OptionList")
                    tabs = app.screen.query_one("Tabs")
                    self.assertIs(app.focused, options)
                    self.assertEqual(tabs.active, "my-playlist")
                    self.assertEqual(options.option_count, 1)  # Saved prompts, even when empty
                    await pilot.press("right")
                    self.assertEqual(tabs.active, "default-playlist")
                    self.assertEqual(options.option_count, len(NAMES))
                    self.assertIs(app.focused, options)
                    await pilot.press("down")
                    self.assertEqual(options.highlighted, 1)
                    await pilot.press("up")
                    self.assertEqual(options.highlighted, 0)
                    await pilot.press("right")
                    self.assertEqual(tabs.active, "default-playlist")
                    await pilot.press("left")
                    self.assertEqual(tabs.active, "my-playlist")
                    self.assertEqual(options.option_count, 1)
                    await pilot.press("left")
                    self.assertEqual(options.option_count, 1)
                    tabs.focus()
                    await pilot.press("right")
                    self.assertEqual(tabs.active, "default-playlist")
                    tabs.focus()
                    await pilot.press("left")
                    self.assertEqual(tabs.active, "my-playlist")
                    self.player.set_style.assert_not_called()
                    self.assert_no_ai()
                    self.assertEqual(self.player.volume, 0.8)
                    self.player.set_volume.assert_not_called()
                    if populated:
                        self.assertEqual(self.store.path.read_bytes(), before)
                    else:
                        self.assertFalse(self.store.path.exists())

    async def test_keyboard_saved_apply_delete_and_escape_are_preserved(self):
        saved, _ = self.store.save("My rain", Style(mood="quiet rain"))
        async with self.app.run_test() as pilot:
            await pilot.press("p")
            await pilot.pause()
            self.assertIsInstance(self.app.screen, PresetPicker)
            await pilot.press("enter")
            await pilot.pause()
            options = self.app.screen.query_one("OptionList")
            self.assertEqual(options.option_count, 1)
            self.assertEqual(options.highlighted, 0)
            await pilot.press("enter")
            self.player.set_style.assert_called_once_with(saved.style)
            await pilot.press("p")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("d")
            self.assertEqual(self.store.list(), [])
            await pilot.press("escape")
            self.assertNotIsInstance(self.app.screen, PresetPicker)
            self.assert_no_ai()

    async def test_save_still_uses_audible_style_and_existing_format(self):
        async with self.app.run_test() as pilot:
            await pilot.press("s")
            await pilot.pause()
            self.app.screen.query_one("Input").value = "My rain"
            await pilot.press("enter")
            self.assertEqual(self.store.get("My rain").style,
                             Style.from_dict({"name": "My rain"}, DEFAULT))
            self.assert_no_ai()

    async def test_default_keyboard_apply_and_small_terminal(self):
        from gentle_stream.playlists import default_playlist
        async with self.app.run_test(size=(60, 20)) as pilot:
            await pilot.press("p")
            await pilot.pause()
            self.assertTrue(await pilot.click("#default-playlist"))
            await pilot.press("down", "enter")
            self.player.set_style.assert_called_once_with(sorted(default_playlist(), key=lambda s: s.name.casefold())[1])
            self.assertFalse(self.store.path.exists())
            self.assert_no_ai()
