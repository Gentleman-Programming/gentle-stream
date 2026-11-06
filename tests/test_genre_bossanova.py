"""Bossa nova: the genre's traits measured on what `schedule` plays through a spy voice set (the batida, the
root-fifth upright bass, the cross-stick clave and shaker, the extended harmony and the lead colours), plus the
genre contract of tests/test_genres_contract.py and a moment of each kind, with the genre registered for the test.
"""
import time
from dataclasses import dataclass

import numpy as np
import pytest

from gentle_stream import genres, moments, samples
from gentle_stream.composer import Chord, Composer
from gentle_stream.engine import SR, Engine
from gentle_stream.genres import bossanova
from gentle_stream.genres.base import Genre, Master, Piece
from gentle_stream.style import CHORD_INSTRUMENTS, KEYS, MODES, Style

G = bossanova.GENRE
REAL = samples.soundfont_path()
HAS_SAMPLES = REAL is not None and samples.load_renderer(REAL) is not None
BACKENDS = ["procedural", pytest.param("samples", marks=pytest.mark.skipif(
    not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)"))]
TOL = 0.02
EIGHTHS = {i / 2 for i in range(8)}
PERCUSSION = {"rim", "shaker", "kick", "tap", "swirl"}


@pytest.fixture
def registered(monkeypatch):
    """The parent registers the genre in genres/__init__.py; until then the engine finds it through the registry."""
    monkeypatch.setitem(genres.REGISTRY, "bossanova", G)


@dataclass(frozen=True)
class Event:
    bar: int
    voice: str
    beat: float
    gain: float
    args: tuple


class Spy:
    """Stands in for the genre's voices: every call returns a distinct marker array that `Piece.add` maps back."""

    def __init__(self):
        self.made, self._keep = {}, []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def make(*args, **kwargs):
            x = np.zeros(4)
            self._keep.append(x)
            self.made[id(x)] = (name, args)
            return x
        return make


def _style(**kw):
    return Style(genre="bossanova", **({"key": "D", "mode": "major", "bpm": 130, "drums": "lofi", "energy": 0.6,
                                        "chords": "nylon", "lead": "pad", "lead_density": 0.9} | kw))


def _run(bars=40, seed=7, **kw):
    s = _style(**kw)
    ton = 48 + KEYS.index(s.key)
    ton = ton - 12 if ton > 54 else ton
    composer, spy = Composer(s, seed, ton, G.chord), Spy()
    piece = Piece(s, np.random.default_rng(seed), spy, ton, SR, np.zeros((16, 2)))
    events, now, beat = [], {}, 60 / s.bpm

    def add(sig, t, pan=0.0, g=1.0):
        name, args = spy.made.get(id(sig), ("unknown", ()))
        events.append(Event(now["bar"], name, t / beat, g, args))
    piece.add = add
    G.start(piece)
    plans = []
    for bar in range(bars):
        now["bar"] = bar
        plans.append(composer.plan(bar))
        G.schedule(piece, plans[-1])
    return plans, events, ton


def _of(events, voice, bar):
    return sorted((e for e in events if e.voice == voice and e.bar == bar), key=lambda e: e.beat)


def _onsets(events, voice, bar):
    """Distinct onsets (a chord's tones are plucked a few ms apart), rounded to the eighth-note grid."""
    return sorted({round(e.beat * 2) / 2 for e in _of(events, voice, bar)})


def _pcs(ton, chord, key):
    return [(ton + t + key) % 12 for t in chord.tones]


# ---------------------------------------------------------------- prompt hints, harmony and master
def test_prompt_hints():
    assert isinstance(G, Genre) and G.name == "bossanova"
    assert G.description and "\n" not in G.description and "bossa" in G.description
    lo, hi = G.bpm
    assert 50 <= lo < hi <= 140 and lo >= 110
    assert G.modes and set(G.modes) <= set(MODES) and "major" in G.modes
    assert set(G.voices) == set(CHORD_INSTRUMENTS) and all(G.voices.values())
    assert "nylon" in G.voices["nylon"] and "flute" in G.voices["pad"]


@pytest.mark.parametrize("mode", G.modes)
def test_chord_hook_extends_every_diatonic_chord(mode):
    plain = Composer(Style(mode=mode), 3, 50)
    hooked = Composer(Style(mode=mode), 3, 50, G.chord)
    for degree in range(1, 8):
        for raised in (False, True):
            a, b = plain._chord(degree, raised), hooked._chord(degree, raised)
            assert b.tones[:4] == a.tones[:4]                      # the melody's chord tones are untouched
            iv = [t - b.tones[0] for t in b.tones]
            third, fifth, seventh = iv[1], iv[2], iv[3]
            if fifth == 6:                                         # half-diminished: the eleventh
                assert 17 in iv
            elif third == 4 and seventh == 10:                     # dominant: a ninth and a thirteenth
                assert {13, 14} & set(iv) and {20, 21} & set(iv)
            else:                                                  # maj7 and m7: the ninth
                assert 14 in iv


def test_chord_hook_colours_triads_and_leaves_suspensions():
    major = MODES["major"]
    assert G.chord(Chord(1, (0, 4, 7, 12)), major).tones == (0, 4, 7, 12, 14)     # the final chord: add9
    assert G.chord(Chord(1, (0, 5, 7, 12)), major).tones == (0, 5, 7, 12)         # moments' suspension
    c = Chord(2, (2, 5, 9, 12))
    assert G.chord(c, major) == G.chord(c, major)                                  # deterministic


def test_master_is_warm_intimate_and_clean():
    for s in (_style(), _style(vinyl=1, space=1, brightness=1), _style(brightness=0, space=0)):
        m = G.master(s)
        assert isinstance(m, Master)
        assert m.vinyl <= 0.1 and m.wow <= 0.15                   # clean, steady pitch
        assert 0 <= m.space <= 0.7 and m.reverb <= 0.2 and 0 <= m.room <= 1.2   # a small, close room
        assert 1.0 <= m.drive <= 1.6 and m.ratio <= 2.5            # soft dynamics, not squashed
        assert 200 <= m.tone <= 20000
    assert G.master(_style(brightness=0)).tone <= 5000                           # warm...
    assert G.master(_style(brightness=1)).tone <= 16000                          # ...never harsh


# ---------------------------------------------------------------- the batida
def test_thumb_plays_root_on_one_and_fifth_on_three():
    plans, events, ton = _run(bars=40)
    seen = 0
    for p in plans:
        thumb = _of(events, "thumb", p.bar)
        assert [round(e.beat, 2) for e in thumb] == [0, 2], (p.bar, thumb)
        assert all(40 <= e.args[0] <= 52 for e in thumb)
        assert thumb[0].args[0] % 12 == _pcs(ton, p.chord, p.key_offset)[0]
        second = _pcs(ton, p.passing, p.key_offset)[0] if p.passing is not None else \
            (_pcs(ton, p.chord, p.key_offset)[0] + 7) % 12
        assert thumb[1].args[0] % 12 == second
        seen += 1
    assert seen == 40


def test_batida_plucks_are_syncopated_on_the_eighth_grid():
    plans, events, _ = _run(bars=40)
    busy = [p.bar for p in plans if "drums" in p.layers and p.energy >= bossanova.LOW_ENERGY]
    assert len(busy) > 20
    for i in busy:
        onsets = _onsets(events, "keys", i)
        assert set(onsets) <= EIGHTHS and len(onsets) >= 3
    for a, b in zip(busy, busy[1:]):
        if b == a + 1:
            cell = _onsets(events, "keys", a) + _onsets(events, "keys", b)
            assert any(x % 1 for x in cell)                       # off-beat plucks in every two-bar cell
            assert _onsets(events, "keys", a) != _onsets(events, "keys", b)   # the cell answers itself


def test_plucks_are_upper_chord_voices_of_the_bar():
    plans, events, ton = _run(bars=24)
    for p in plans:
        for e in _of(events, "keys", p.bar):
            assert e.args[0] == "nylon" and 50 <= e.args[1] <= 80
            chord = p.passing if p.passing is not None and e.beat >= 2 - TOL else p.chord
            planed = e.beat >= 3.5 - TOL                          # a chromatic approach chord may slide in
            assert planed or e.args[1] % 12 in _pcs(ton, chord, p.key_offset), (p.bar, e)


def test_a_chromatic_approach_chord_slides_into_the_next_bar_sometimes():
    plans, events, ton = _run(bars=64)
    slides = 0
    for p in plans:
        late = [e for e in _of(events, "keys", p.bar) if e.beat >= 3.5 - TOL]
        chord = p.passing if p.passing is not None else p.chord
        if late and any(e.args[1] % 12 not in _pcs(ton, chord, p.key_offset) for e in late):
            slides += 1
    assert slides >= 2


# ---------------------------------------------------------------- bass and percussion
def test_upright_bass_root_fifth_with_pickups():
    plans, events, ton = _run(bars=48)
    main = [p for p in plans if "drums" in p.layers and p.energy >= bossanova.LOW_ENERGY]
    assert len(main) > 20
    for p in main:
        bass = {round(e.beat, 2): e.args[0] for e in _of(events, "bass", p.bar)}
        assert set(bass) == {0, 1.5, 2, 3.5}, (p.bar, bass)
        assert all(28 <= m <= 52 for m in bass.values())
        root = _pcs(ton, p.chord, p.key_offset)[0]
        second = _pcs(ton, p.passing, p.key_offset)[0] if p.passing is not None else (root + 7) % 12
        assert bass[0] % 12 == root and bass[2] % 12 == second and bass[1.5] % 12 == second
    for p in plans:
        if "drums" not in p.layers:                                # in two, no pickups
            assert [round(e.beat, 2) for e in _of(events, "bass", p.bar)] == [0, 2]


def test_cross_stick_plays_the_two_bar_bossa_clave():
    plans, events, _ = _run(bars=32)
    cells = {(0.0, 1.5, 3.0), (1.0, 2.5)}
    drums = [p.bar for p in plans if "drums" in p.layers]
    for i in drums:
        assert tuple(round(e.beat, 2) for e in _of(events, "rim", i)) in cells
    for a in drums:
        if a + 1 in drums:
            assert {tuple(round(e.beat, 2) for e in _of(events, "rim", b)) for b in (a, a + 1)} == cells


def test_shaker_plays_steady_eighths_accented_on_the_beat():
    plans, events, _ = _run(bars=32)
    for p in plans:
        if "drums" not in p.layers:
            continue
        sh = _of(events, "shaker", p.bar)
        beats = [round(e.beat, 2) for e in sh]
        assert set(EIGHTHS) <= set(beats) and np.allclose(np.diff(beats), np.diff(beats)[0])
        on = np.mean([e.gain for e in sh if e.beat % 1 == 0])
        off = np.mean([e.gain for e in sh if e.beat % 1 == 0.5])
        assert on > off


def test_soft_kick_follows_the_bass_and_no_percussion_without_drums():
    plans, events, _ = _run(bars=32)
    for p in plans:
        names = {e.voice for e in events if e.bar == p.bar}
        if "drums" in p.layers:
            kick = [round(e.beat, 2) for e in _of(events, "kick", p.bar)]
            assert {0, 2} <= set(kick) <= {0, 1.5, 2, 3.5}
        else:
            assert not PERCUSSION & names
    _, events, _ = _run(bars=24, drums="none")
    assert not PERCUSSION & {e.voice for e in events}


# ---------------------------------------------------------------- lead and colours
@pytest.mark.parametrize("lead, voice", [("pad", "flute"), ("rhodes", "vibes"), ("piano", "sax"),
                                         ("nylon", "guitar")])
def test_the_melody_plays_on_the_leads_colour(lead, voice):
    plans, events, _ = _run(bars=32, lead=lead)
    total = 0
    for p in plans:
        notes = [e for e in events if e.bar == p.bar and e.voice == "lead"]
        assert len(notes) == (len(p.melody) if "lead" in p.layers else 0)
        assert sorted(e.args[1] for e in notes) == sorted(n.midi for n in p.melody) if notes else True
        assert all(e.args[0] == voice for e in notes)
        total += len(notes)
    assert total > 0


def test_counter_melody_plays_softly():
    plans, events, _ = _run(bars=48, energy=0.9)
    seen = 0
    for p in plans:
        c = [e for e in events if e.bar == p.bar and e.voice == "counter"]
        assert len(c) == (len(p.counter) if "counter" in p.layers else 0)
        seen += len(c)
    assert seen > 0


def test_pad_colour_adds_a_soft_string_bed_under_the_guitar():
    plans, events, _ = _run(bars=16, chords="pad")
    assert any(e.voice == "pad" for e in events)
    assert all(e.args[0] == "nylon" for e in events if e.voice == "keys")


def test_procedural_flute_is_a_breathy_sine_with_delayed_vibrato():
    v = bossanova.Voices(np.random.default_rng(3), SR)
    x = v.flute(69, 1.5, 0.9)
    assert np.all(np.isfinite(x)) and 0.01 < np.max(np.abs(x)) < 1.5
    spec = np.abs(np.fft.rfft(x[int(0.3 * SR):int(1.3 * SR)]))
    freqs = np.fft.rfftfreq(SR, 1 / SR)
    assert freqs[np.argmax(spec)] == pytest.approx(440, rel=0.03)
    env = np.sqrt(np.convolve(x ** 2, np.ones(441) / 441, "same"))
    assert env[int(0.005 * SR)] < 0.5 * env[int(0.2 * SR)]                 # a soft, blown attack


def test_samples_backend_uses_the_gm_programs_and_kits():
    calls = []

    class Fake:
        def play(self, p, m, dur, vel=1.0):
            calls.append(("play", p.program, p.bank))
            return np.zeros(64)

        def hit(self, h, g=1.0):
            calls.append(("hit", h.kit, h.key))
            return np.zeros(64)

    def run(style):
        calls.clear()
        inst = G.instruments(np.random.default_rng(4), SR, Fake())
        piece = Piece(style, np.random.default_rng(5), inst, 50, SR, np.zeros((SR * 30, 2)))
        G.start(piece)
        comp = Composer(style, 7, 50, G.chord)
        for bar in range(32):
            G.schedule(piece, comp.plan(bar))
        return {c[1] for c in calls if c[0] == "play"}, {(c[1], c[2]) for c in calls if c[0] == "hit"}

    programs, hits = run(_style())
    assert {24, 32, 73} <= programs                              # nylon guitar, upright bass, flute
    assert (0, 37) in hits and (0, 70) in hits                   # cross-stick and shaker
    programs, _ = run(_style(lead="rhodes", chords="pad"))
    assert {11, 24, 48} <= programs                              # vibraphone over the guitar and strings
    programs, _ = run(_style(lead="piano", chords="piano"))
    assert {0, 66} <= programs and 24 in programs                # piano batida (thumb on the guitar), soft sax


# ---------------------------------------------------------------- the genre contract
def _engine(backend, seed, **kw):
    s = Style(genre="bossanova", **({"drums": "busy", "energy": 0.9, "lead_density": 0.9} | kw))
    e = Engine(s, seed=seed, samples=REAL if backend == "samples" else False)
    assert e.backend == backend and e.genre is G
    return e


def _seamless(bars):
    limit = np.max(np.concatenate([np.abs(np.diff(b, axis=0)) for b in bars]))
    for a, b in zip(bars, bars[1:]):
        assert np.all(np.abs(b[0] - a[-1]) <= limit)


@pytest.mark.parametrize("backend", BACKENDS)
def test_renders_cleanly_deterministically_and_fast(registered, backend):
    e = _engine(backend, seed=31)
    t0 = time.perf_counter()
    bars = [e.render_bar() for _ in range(8)]
    elapsed = time.perf_counter() - t0
    x = np.concatenate(bars)
    assert np.all(np.isfinite(x)) and 0.01 < np.max(np.abs(x)) < 1.0
    _seamless(bars)
    assert len(x) / SR / elapsed > 10, f"{backend}: only {len(x) / SR / elapsed:.1f}x real time"
    again = _engine(backend, seed=31)
    assert np.array_equal(x, np.concatenate([again.render_bar() for _ in range(8)]))


def test_renders_quiet_styles_and_joins_other_genres_seamlessly(registered):
    e = _engine("procedural", seed=32, drums="none", lead="none", energy=0.1, vinyl=0, space=0)
    x = np.concatenate([e.render_bar() for _ in range(4)])
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.0
    e = Engine(Style(drums="soft"), seed=33, samples=False)
    bars = [e.render_bar() for _ in range(3)]
    e.queue_style(Style(genre="bossanova", drums="soft"))
    bars += [e.render_bar() for _ in range(3)]
    assert e.genre is G
    e.queue_style(Style(drums="soft"))
    bars += [e.render_bar() for _ in range(2)]
    _seamless(bars)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("kind", moments.KINDS)
def test_a_moment_of_each_kind_hits_loudest_on_its_cue(registered, kind, backend):
    style = moments.default_style(kind, genre="bossanova")
    m = moments.render(kind, 12, style=style, seed=8, samples=REAL if backend == "samples" else False)
    x, p = m.audio, m.plan
    assert m.backend == backend and m.style.genre == "bossanova"
    assert moments.STYLE_BPM[0] <= p.bpm <= moments.STYLE_BPM[1]   # the genre's range when the bars fit it
    assert x.shape == (12 * SR, 2) and np.all(np.isfinite(x)) and np.all(x[-1] == 0)
    w, s = int(0.5 * SR), int(0.05 * SR)
    power = np.concatenate([[0.0], np.cumsum(np.mean(x.astype(float) ** 2, axis=1))])
    starts = np.arange(0, len(x) - w, s)
    loudest = starts[int(np.argmax(power[starts + w] - power[starts]))] / SR
    assert abs(loudest - p.hit_seconds) <= p.bar_samples / SR, (loudest, p.hit_seconds)
