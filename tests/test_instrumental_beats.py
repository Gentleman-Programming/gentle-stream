"""Instrumental plugins: unittest-only, no devices, downloads or temporary files."""
import importlib
import unittest
from dataclasses import replace
from unittest.mock import Mock

import numpy as np

from gentle_stream import genres
from gentle_stream.bridge import GentleShell, genre_lines
from gentle_stream.composer import BarPlan, Chord, Composer, Note
from gentle_stream.engine import Engine, SR
from gentle_stream.genres.base import Piece
from gentle_stream.playlists import default_playlist
from gentle_stream.style import Style

OLD = ("lofi", "ambient", "chiptune", "synthwave", "jazz", "rock", "electro", "electrohouse", "cumbia", "dubstep")
NEW = ("hiphop", "trap", "chill_electronic", "lofi_trap", "electro_trap")


class RecordingVoices:
    """Tag audio calls so the public scheduling surface exposes rhythm and pitches."""
    def __getattr__(self, name):
        return lambda *args, **kwargs: (name, args)


def events(name, drums="busy", lead="piano", layers=None, energy=0.9):
    style = Style(genre=name, bpm=120, drums=drums, lead=lead, energy=energy)
    piece = Piece(style, np.random.default_rng(19), RecordingVoices(), 50, SR, np.zeros((1, 2)))
    recorded = []
    piece.add = lambda sig, t, pan=0, g=1: recorded.append((sig[0], t * 2, g, sig[1]))
    genre = genres.get(name)
    genre.start(piece)
    plan = BarPlan(5, "A", 1, energy, Chord(1, (0, 3, 7, 10)), Chord(4, (5, 8, 12, 15)),
                   2, frozenset(layers or ("chords", "bass", "drums", "lead", "counter")),
                   (Note(1, 76, 1),), (Note(2, 64, 2),), (Note(0, 40, 2), Note(2, 45, 2)))
    genre.schedule(piece, plan)
    return recorded


class InstrumentalBeatsTests(unittest.TestCase):
    def test_registry_style_variation_and_metadata(self):
        self.assertEqual(genres.NAMES, OLD + NEW + ("reggaeton",))
        for name in NEW:
            with self.subTest(name=name):
                genre = genres.get(name)
                self.assertEqual(type(genre).__module__, f"gentle_stream.genres.{name}")
                self.assertIs(importlib.import_module(type(genre).__module__).GENRE, genre)
                style = Style.from_dict({"genre": name})
                self.assertEqual(Style.from_dict(style.to_dict()), style)
                self.assertEqual(style.lead, "piano")  # instrumental does not mean melody-free
                self.assertNotIn("voice_lines", style.to_dict())
                self.assertEqual(style.variation(np.random.default_rng(3)).genre, name)
                self.assertEqual(set(genre.voices), {"piano", "rhodes", "nylon", "pad"})
                self.assertTrue(50 <= genre.bpm[0] < genre.bpm[1] <= 140)

    def test_hints_and_builtin_discovery_without_ai(self):
        styles = default_playlist()
        self.assertEqual(tuple(s.genre for s in styles), OLD + NEW + ("reggaeton",))
        shell = GentleShell()
        shell._run = Mock(return_value='{"genre":"trap"}')
        style, _ = shell.interpret_with_usage("beats de trap sin voz", Style())
        self.assertEqual(style.genre, "trap")
        prompt = shell._run.call_args.args[0][-1]
        for name in NEW:
            self.assertIn(f"- {name}:", genre_lines())
            self.assertIn(f"- {name}:", prompt)
        for word in ("rap", "hip-hop", "trap", "soft electronic", "lo-fi", "synth"):
            self.assertIn(word, genre_lines())
        self.assertEqual(styles, default_playlist())

    def test_backbeats_pulse_and_trap_rolls(self):
        for name, snares in (("hiphop", [1, 3]), ("trap", [2]), ("lofi_trap", [2]),
                             ("electro_trap", [2]), ("chill_electronic", [1, 3])):
            with self.subTest(name=name):
                ev = events(name)
                self.assertEqual([t for kind, t, _, _ in ev if kind == "snare"], snares)
                hats = [t for kind, t, _, _ in ev if kind == "hat"]
                self.assertTrue(all(0 <= t < 4 for t in hats))
                if "trap" in name:
                    self.assertTrue(any(0 < b - a < 0.25 for a, b in zip(hats, hats[1:])))
                if name == "chill_electronic":
                    self.assertEqual([t for kind, t, _, _ in ev if kind == "kick"], [0, 1, 2, 3])

    def test_none_soft_busy_and_layer_gates(self):
        for name in NEW:
            with self.subTest(name=name):
                silent = events(name, drums="none", lead="none")
                self.assertFalse(any(k in ("kick", "snare", "hat", "lead") for k, *_ in silent))
                gated = events(name, layers=("chords", "bass"))
                self.assertFalse(any(k in ("kick", "snare", "hat", "lead") for k, *_ in gated))
                soft, busy = events(name, drums="soft"), events(name)
                self.assertLess(sum(k == "hat" for k, *_ in soft), sum(k == "hat" for k, *_ in busy))
                self.assertLess(max(g for k, _, g, _ in soft if k == "kick"),
                                max(g for k, _, g, _ in busy if k == "kick"))
                self.assertTrue(any(k == "lead" for k, *_ in busy))

    def test_shared_harmony_bass_and_passing_chord(self):
        for name in NEW:
            with self.subTest(name=name):
                ev = events(name)
                bass = [(t, args[0]) for kind, t, _, args in ev if kind == "bass"]
                self.assertEqual(bass, [(0, 40), (2, 45)])
                self.assertTrue(any(kind == "lead" and args[0] == 76 and t == 1 for kind, t, _, args in ev))
                self.assertTrue(any(kind == "stab" and t >= 2 for kind, t, _, _ in ev))
                a = Composer(Style(genre=name), 3, 50).plan(13)
                b = Composer(Style(), 3, 50).plan(13)
                self.assertEqual(a, b)

    def test_render_determinism_bounds_controls_and_switching(self):
        for name in NEW:
            with self.subTest(name=name):
                style = Style(genre=name, bpm=120, energy=0.9, drums="busy", lead_density=1)
                def render():
                    engine = Engine(style, seed=31, samples=False)
                    return engine, [engine.render_bar() for _ in range(6)]
                engine, bars = render()
                _, again = render()
                self.assertTrue(np.array_equal(np.concatenate(bars), np.concatenate(again)))
                for bar in bars:
                    self.assertEqual(bar.shape, (SR * 2, 2))
                    self.assertTrue(np.isfinite(bar).all())
                    self.assertGreater(np.max(np.abs(bar)), 0.001)
                    self.assertLess(np.max(np.abs(bar)), 1)
                engine.queue_style(replace(style, genre="lofi"))
                switched = engine.render_bar()
                self.assertEqual(engine.genre.name, "lofi")
                limit = max(np.max(np.abs(np.diff(b, axis=0))) for b in bars + [switched])
                self.assertLessEqual(np.max(np.abs(switched[0] - bars[-1][-1])), limit)
                quiet = Engine(replace(style, drums="none", lead="none"), seed=2, samples=False)
                self.assertTrue(np.isfinite(quiet.render_bar()).all())

    def test_voice_textures_808_and_sample_adapter(self):
        class Samples:
            def __init__(self):
                self.calls = []

            def play(self, program, midi, dur, vel=1):
                self.calls.append(("play", midi))
                return np.ones(128) * 0.1

            def hit(self, hit, gain=1):
                self.calls.append(("hit", hit.key))
                return np.ones(128) * 0.1

            def piano(self, *args):
                return self.play(None, *args)

        for name in NEW:
            with self.subTest(name=name):
                g = genres.get(name)
                proc = g.instruments(np.random.default_rng(4), SR, None)
                notes = proc.bass(36, 1)
                self.assertTrue(np.isfinite(notes).all())
                self.assertGreater(np.max(np.abs(notes)), 0.1)
                self.assertLess(abs(notes[-1]), 0.001)
                sampled = Samples()
                adapted = g.instruments(np.random.default_rng(4), SR, sampled)
                self.assertTrue(np.isfinite(adapted.stab("piano", 60, 0.3)).all())
                self.assertTrue(np.isfinite(adapted.snare()).all())
                self.assertEqual([x[0] for x in sampled.calls], ["play", "hit"])
        warm = genres.get("lofi_trap").master(Style(vinyl=1))
        clean = genres.get("electro_trap").master(Style(vinyl=1))
        self.assertGreater(warm.wow, clean.wow)
        self.assertGreater(warm.vinyl, clean.vinyl)
        a = genres.get("lofi_trap").instruments(np.random.default_rng(4), SR, None)
        b = genres.get("electro_trap").instruments(np.random.default_rng(4), SR, None)
        self.assertFalse(np.array_equal(a.stab("piano", 60, 0.5), b.stab("piano", 60, 0.5)))

    def test_electro_trap_growth_keeps_one_composer_and_instrumental_lead(self):
        style = Style(genre="electro_trap", bpm=132, energy=0.9, drums="busy", lead_density=0.9,
                      chords="rhodes", lead="piano", brightness=0.75, vinyl=0, space=0.3)
        composer = Composer(style, 8, 50)
        intro, build, peak = (composer.plan(i) for i in (0, 4, 32))
        self.assertLess(intro.energy, build.energy)
        self.assertLess(build.energy, peak.energy)
        self.assertNotIn("drums", intro.layers)
        self.assertIn("drums", peak.layers)
        self.assertIn("lead", peak.layers)
        self.assertNotIn("voice_lines", style.to_dict())
        self.assertGreater(len(events("electro_trap", energy=0.9)),
                           len(events("electro_trap", energy=0.2)))

    def test_existing_genres_keep_styles_hints_and_seeded_audio(self):
        self.assertEqual(genres.NAMES[:10], OLD)
        for name in OLD:
            with self.subTest(name=name):
                style = Style(genre=name)
                self.assertEqual(Style.from_dict(style.to_dict()), style)
                self.assertIn(f"- {name}: {genres.get(name).description}", genre_lines())
                a = Engine(style, seed=7, samples=False).render_bar()
                b = Engine(style, seed=7, samples=False).render_bar()
                self.assertTrue(np.array_equal(a, b))


if __name__ == "__main__":
    unittest.main()
