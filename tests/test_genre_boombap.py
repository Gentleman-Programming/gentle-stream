"""Boom bap: the traits that define the genre (measured on what `schedule` plays through a spy voice set) and the
genre contract (clean, seamless, fast, deterministic, both backends, moments), with the genre registered for the
test only."""
import time

import numpy as np
import pytest

from gentle_stream import genres, moments, samples
from gentle_stream.composer import Composer
from gentle_stream.engine import SR, Engine
from gentle_stream.genres import boombap
from gentle_stream.genres.base import Genre, Master, Piece
from gentle_stream.style import CHORD_INSTRUMENTS, KEYS, MODES, Style

G = boombap.GENRE
REAL = samples.soundfont_path()
HAS_SAMPLES = REAL is not None and samples.load_renderer(REAL) is not None
BACKENDS = ["procedural", pytest.param("samples", marks=pytest.mark.skipif(
    not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)"))]
TOL = 0.02   # beats: allowed humanization around a grid position
PEAK = 10 ** (-1 / 20)


@pytest.fixture
def registered(monkeypatch):
    """Boom bap in the registry for the engine and moments (the parent registers it for good)."""
    monkeypatch.setitem(genres.REGISTRY, "boombap", G)


class Spy:
    """Stands in for the genre's voices: every call returns a distinct marker array that `add` maps back."""

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


def _run(bars=40, seed=7, **style):
    s = Style(genre="boombap", **({"key": "A", "mode": "minor", "bpm": 90, "drums": "lofi", "energy": 0.6,
                                   "chords": "piano", "lead": "rhodes", "lead_density": 0.9} | style))
    ton = 48 + KEYS.index(s.key)
    ton = ton - 12 if ton > 54 else ton
    composer, spy = Composer(s, seed, ton, G.chord), Spy()
    piece = Piece(s, np.random.default_rng(seed), spy, ton, SR, np.zeros((16, 2)))
    events, now, beat = [], {}, 60 / s.bpm

    def add(sig, t, pan=0.0, g=1.0):
        name, args = spy.made.get(id(sig), ("unknown", ()))
        events.append((now["bar"], name, t / beat, g, args))
    piece.add = add
    G.start(piece)
    plans = []
    for bar in range(bars):
        now["bar"] = bar
        plans.append(composer.plan(bar))
        G.schedule(piece, plans[-1])
    return plans, events, ton


def _of(events, voice, bar):
    return sorted((e for e in events if e[1] == voice and e[0] == bar), key=lambda e: e[2])


def _near(x, grid):
    return any(abs(x - g) <= TOL for g in grid)


# ---------------------------------------------------------------- prompt hints and harmony
def test_prompt_hints_describe_a_90s_boom_bap_genre():
    assert isinstance(G, Genre) and G.name == "boombap"
    assert G.description and "\n" not in G.description and "boom bap" in G.description
    lo, hi = G.bpm
    assert 50 <= lo <= 85 and 95 <= hi <= 140 and lo < hi
    assert G.modes and set(G.modes) <= set(MODES) and G.modes[0] == "minor"
    assert set(G.voices) == set(CHORD_INSTRUMENTS) and all(G.voices.values())


@pytest.mark.parametrize("mode", G.modes)
def test_chord_hook_keeps_the_sevenths_and_adds_a_soul_ninth(mode):
    plain, hooked = Composer(Style(mode=mode), 3, 50), Composer(Style(mode=mode), 3, 50, G.chord)
    for degree in range(1, 8):
        for raised in (False, True):
            a, b = plain._chord(degree, raised), hooked._chord(degree, raised)
            assert b.tones[:len(a.tones)] == a.tones                 # never reshapes the composer's chord
            extra = [t - b.tones[0] for t in b.tones[len(a.tones):]]
            assert extra in ([], [14])                               # only a major ninth, never a b9
            assert G.chord(a, MODES[mode]) == b                      # deterministic


# ---------------------------------------------------------------- drums
def test_hard_kick_on_the_one_and_cracking_snare_on_two_and_four():
    plans, events, _ = _run(bars=40)
    drum_bars = [p.bar for p in plans if "drums" in p.layers]
    assert len(drum_bars) > 20
    for i in drum_bars:
        kicks = _of(events, "kick", i)
        assert kicks and abs(kicks[0][2]) <= TOL, (i, kicks)
        loud = [e[2] for e in _of(events, "snare", i) if e[3] >= 0.3]
        assert len(loud) == 2 and _near(loud[0], [1]) and _near(loud[1], [3]), (i, loud)
        ghosts = [e for e in _of(events, "snare", i) if e[3] < 0.3]
        assert all(g[3] < 0.5 * min(e[3] for e in _of(events, "snare", i) if e[3] >= 0.3) for g in ghosts)


def test_hats_swing_their_sixteenths():
    plans, events, _ = _run(bars=32, drums="busy", energy=0.8, swing=0.1)
    offs = []
    for p in plans:
        if "drums" not in p.layers:
            continue
        for e in _of(events, "hat", p.bar):
            f = e[2] % 0.5
            if f > TOL:                                              # an off-sixteenth
                offs.append(f)
    assert len(offs) > 30
    assert all(0.27 <= f <= 0.35 for f in offs), sorted(offs)[:5]    # late: swung, never straight (0.25)


def test_soft_drums_trade_the_snare_for_a_rim_and_no_drums_without_the_layer():
    plans, events, _ = _run(bars=24, drums="soft")
    drum_bars = [p.bar for p in plans if "drums" in p.layers]
    assert drum_bars and not [e for e in events if e[1] == "snare"]
    assert all(_of(events, "rim", i) for i in drum_bars)
    _, events, _ = _run(bars=8, drums="none")
    assert not [e for e in events if e[1] in ("kick", "snare", "hat", "open_hat", "rim")]


# ---------------------------------------------------------------- chops, bass and hook
def test_chords_are_short_chopped_stabs_of_the_chord():
    plans, events, ton = _run(bars=40)
    beat = 60 / 90
    for p in plans:
        stabs = [e for e in _of(events, "keys", p.bar) if e[4][0] == "piano"]   # the counter plays vibes
        assert stabs, p.bar
        if "drums" in p.layers and p.section != "breakdown":
            onsets = {round(e[2], 2) for e in stabs}
            assert len(onsets) >= 3                                  # chopped: several hits per bar
            assert all(e[4][2] <= 1.05 * beat for e in stabs)        # each stab a beat or shorter
        for e in stabs:
            chord = p.passing if p.passing is not None and e[2] >= 2 - TOL else p.chord
            pcs = {(ton + t + p.key_offset) % 12 for t in chord.tones}
            assert e[4][1] % 12 in pcs, (p.bar, e)


def test_bass_follows_the_composers_bass_line():
    plans, events, _ = _run(bars=32)
    for p in plans:
        got = [e[4][0] for e in _of(events, "bass", p.bar)]
        assert got == [n.midi for n in sorted(p.bass, key=lambda n: n.beat)], p.bar


def test_the_hook_is_sparse_and_plays_the_composers_melody():
    plans, events, _ = _run(bars=48)
    planned = played = 0
    for p in plans:
        if "lead" not in p.layers:
            continue
        lead = _of(events, "lead", p.bar)
        planned += len(p.melody)
        played += len(lead)
        assert {e[4][1] for e in lead} <= {n.midi for n in p.melody}
        if lead:
            assert boombap.hook_bar(p)
    assert 0 < played < 0.8 * planned


# ---------------------------------------------------------------- master and voices
def test_master_is_dusty_and_punchy():
    for s in (Style(genre="boombap"), Style(genre="boombap", vinyl=1, space=1, brightness=1), Style(vinyl=0)):
        m, jazz = G.master(s), genres.get("jazz").master(s)
        assert isinstance(m, Master)
        assert 0 <= m.space <= 1 and 0 <= m.vinyl <= 1 and 0 <= m.wow <= 1
        assert m.reverb >= 0 and m.room >= 0 and 200 <= m.tone <= 20000 and m.drive > 0
        assert m.vinyl > jazz.vinyl and m.wow > jazz.wow                 # vinyl and tape colour, even at vinyl 0
        assert m.ratio >= 3 and m.space <= 0.5                           # punchy, small room


def _check_voices(v):
    for colour in ("piano", "rhodes", "nylon", "pad"):
        for x in (v.keys(colour, 62, 0.4, 0.8), v.lead(colour, 72, 0.6, 0.8)):
            assert np.all(np.isfinite(x)) and 0.01 < np.max(np.abs(x)) < 2
    for x in (v.bass(36, 0.5, 0.8, "piano"), v.bass(36, 0.5, 0.8, "rhodes"), v.kick(0.9), v.snare(0.9),
              v.hat(0.7), v.open_hat(0.7), v.rim(0.7)):
        assert np.all(np.isfinite(x)) and 0.001 < np.max(np.abs(x)) < 2


def test_procedural_voices():
    _check_voices(G.instruments(np.random.default_rng(0), SR, None))


def test_sampled_voices_use_gm_programs_the_recorded_library_covers():
    v = G.instruments(np.random.default_rng(0), SR, object())
    assert {c: p.program for c, p in v.colours.items()} == {"piano": 0, "rhodes": 4, "pad": 11, "nylon": 26}
    assert v.upright.program == 32 and v.finger.program == 33
    assert v.hits["snare"].key == 38 and v.hits["kick"].key == 36


@pytest.mark.skipif(not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)")
def test_sampled_voices():
    shared = samples.SampleInstruments(samples.load_renderer(REAL), np.random.default_rng(0))
    _check_voices(G.instruments(np.random.default_rng(0), SR, shared))


# ---------------------------------------------------------------- the genre contract
def _engine(backend, seed, **style):
    s = Style(genre="boombap", **({"drums": "busy", "energy": 0.9, "lead_density": 0.9} | style))
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
    assert len(x) / SR / elapsed > 10, f"only {len(x) / SR / elapsed:.1f}x real time"
    again = _engine(backend, seed=31)
    assert np.array_equal(x, np.concatenate([again.render_bar() for _ in range(8)]))


def test_quiet_styles_and_genre_switches(registered):
    e = _engine("procedural", seed=32, drums="none", lead="none", energy=0.1, vinyl=0, space=0)
    x = np.concatenate([e.render_bar() for _ in range(4)])
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.0
    e = Engine(Style(drums="soft"), seed=33, samples=False)
    bars = [e.render_bar() for _ in range(3)]
    e.queue_style(Style(genre="boombap", drums="soft"))
    bars += [e.render_bar() for _ in range(3)]
    assert e.genre is G
    _seamless(bars)


def _loudest(x, seconds=0.5, step=0.05):
    w, s = int(seconds * SR), int(step * SR)
    csum = np.concatenate([[0.0], np.cumsum(np.mean(x.astype(float) ** 2, axis=1))])
    starts = np.arange(0, len(x) - w, s)
    return float(starts[int(np.argmax(csum[starts + w] - csum[starts]))] / SR)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("kind", moments.KINDS)
def test_moments_render_with_the_hit_loudest(registered, kind, backend):
    m = moments.render(kind, 12, style=moments.default_style(kind, genre="boombap"), seed=8,
                       samples=REAL if backend == "samples" else False)
    x = m.audio
    assert m.backend == backend and m.style.genre == "boombap"
    assert x.shape == (12 * SR, 2) and np.all(np.isfinite(x)) and np.all(x[-1] == 0)
    assert np.max(np.abs(x)) == pytest.approx(PEAK, abs=1e-6)
    hit, bar = m.plan.hit_seconds, m.plan.bar_samples / SR
    assert abs(_loudest(x) - hit) <= bar, f"{kind}: loudest at {_loudest(x):.2f}, hit {hit:.2f}"
