"""Tango: the traits that define the genre, measured on what `schedule` plays (through a spy voice set) and on the
rendered audio; plus the genre contract (tests/test_genres_contract.py) applied to it, on both backends, and the
moments. The genre is registered for the engine tests through monkeypatch."""
import time

import numpy as np
import pytest

from gentle_stream import genres, moments, samples
from gentle_stream.composer import Chord, Composer
from gentle_stream.engine import SR, Engine
from gentle_stream.genres import tango
from gentle_stream.genres.base import Genre, Master, Piece
from gentle_stream.style import CHORD_INSTRUMENTS, MODES, Style

G = tango.GENRE
REAL = samples.soundfont_path()
HAS_SAMPLES = REAL is not None and samples.load_renderer(REAL) is not None
BACKENDS = ["procedural", pytest.param("samples", marks=pytest.mark.skipif(
    not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)"))]
STYLE = Style(genre="tango", key="D", mode="minor", bpm=66, chords="piano", lead="piano", drums="lofi",
              energy=0.6, brightness=0.45, lead_density=0.8)
TON = 50
MARCATO = (0.0, 0.5, 1.0, 1.5)    # beats inside a tango bar (two engine beats)
SINCOPA = (0.0, 0.75, 1.5)        # 3-3-2
PEAK = 10 ** (-1 / 20)


@pytest.fixture
def registered(monkeypatch):
    monkeypatch.setitem(genres.REGISTRY, "tango", G)


class _Voices:
    """Stands in for the genre's voices: every call returns a token that `_Recorder.add` turns into an event."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kw: (name, args)


class _Recorder(Piece):
    def __init__(self, style):
        super().__init__(style, np.random.default_rng(5), _Voices(), TON, SR, np.zeros((10, 2)))
        self.events = []

    def add(self, sig, t, pan=0.0, g=1.0):
        name, args = sig
        self.events.append((name, args, round(t / (60 / self.style.bpm), 3), pan, g))


def _bars(style=STYLE, n=48, seed=7):
    piece = _Recorder(style)
    G.start(piece)
    comp = Composer(style, seed, TON, G.chord)
    out = []
    for bar in range(n):
        plan = comp.plan(bar)
        piece.events = []
        G.schedule(piece, plan)
        out.append((plan, piece.events))
    return out


def _notes(events, part):
    return [(b, args, g) for n, args, b, _, g in events if n == "note" and args[0] == part]


def _section(bars, name):
    found = [(p, ev) for p, ev in bars if p.section == name]
    assert found, f"no {name} bars"
    return found


def _half(onsets, h):
    """The figure of the tango bar starting at beat `h`: onsets on the sixteenth grid (chord tones are rolled a few
    milliseconds apart)."""
    return tuple(sorted({round((b - h) * 4) / 4 for b in onsets if h <= b < h + 2}))


# ---------------------------------------------------------------- prompt hints, harmony and master
def test_prompt_hints():
    assert isinstance(G, Genre) and G.name == "tango"
    assert G.description and "\n" not in G.description and "tango" in G.description
    assert G.bpm == (60, 70)
    assert G.modes[0] == "minor" and set(G.modes) <= set(MODES)
    assert set(G.voices) == set(CHORD_INSTRUMENTS) and all(G.voices.values())
    assert "bandoneon" in " ".join(G.voices.values()) and "violin" in " ".join(G.voices.values())


def test_chord_hook_plain_triads_but_dominant_and_diminished_sevenths():
    scale = MODES["minor"]
    assert G.chord(Chord(1, (0, 3, 7, 10, 14)), scale).tones == (0, 3, 7)
    assert G.chord(Chord(5, (7, 11, 14, 17)), scale).tones == (7, 11, 14, 17)     # raised V7
    assert G.chord(Chord(7, (11, 14, 17, 20)), scale).tones == (11, 14, 17, 20)   # leading-tone dim7
    assert G.chord(Chord(0, (2, 6, 9, 12)), scale).tones == (2, 6, 9, 12)         # chromatic dominant


def test_master_keeps_the_dynamics_with_a_warm_room():
    for s in (STYLE, Style(genre="tango", space=1, vinyl=1, brightness=1), Style(genre="tango", brightness=0)):
        m = G.master(s)
        assert isinstance(m, Master)
        assert 0 <= m.space <= 1 and 0 <= m.vinyl <= 1 and 0 <= m.wow <= 0.1
        assert m.reverb >= 0 and m.room >= 0 and 200 <= m.tone <= 20000 and m.drive > 0
        assert m.ratio <= 2.5                                   # light bus compression: the swells survive


# ---------------------------------------------------------------- rhythm: marcato, sincopa, bass
def test_chords_play_marcato_in_four_or_the_3_3_2_sincopa_on_every_tango_bar():
    bars = _bars()
    seen = set()
    for name in ("A", "B", "A'", "B'"):
        for p, ev in _section(bars, name):
            onsets = [b for b, *_ in _notes(ev, "piano") if b < 4]
            for h in (0, 2):
                pattern = _half(onsets, h)
                assert pattern in (MARCATO, SINCOPA), (name, h, pattern)
                seen.add((name[0], pattern))
    assert {("A", MARCATO), ("B", SINCOPA)} <= seen


def test_marcato_accents_one_and_three_and_the_sincopa_its_first_two_strokes():
    for p, ev in _bars():
        if p.section not in ("A", "B"):
            continue
        vel = {}
        for b, args, _ in _notes(ev, "piano"):
            if args[1] >= 55:                                   # right-hand chord tones
                vel[b] = max(vel.get(b, 0), args[3])
        for h in (0, 2):
            pattern = _half(vel, h)
            v = {}
            for b in vel:
                if h <= b < h + 2:
                    q = round((b - h) * 4) / 4
                    v[q] = max(v.get(q, 0), vel[b])
            if pattern == MARCATO:
                assert min(v[0.0], v[1.0]) > max(v[0.5], v[1.5])
            else:
                assert min(v[0.0], v[0.75]) > v[1.5]


def test_double_bass_marks_one_and_three_on_the_chord_root():
    for name in ("A", "B", "B'"):
        for p, ev in _section(_bars(), name):
            bass = sorted((b, args[1]) for b, args, _ in _notes(ev, "bass"))
            assert bass[0][0] == 0 and bass[0][1] % 12 == p.bass[0].midi % 12
            assert all(28 <= m <= 55 for _, m in bass)
            chords = [b for b, *_ in _notes(ev, "piano")]
            for h in (0, 2):
                if _half(chords, h) == MARCATO:
                    assert {h, h + 1.0} <= {b for b, _ in bass}


def test_piano_doubles_the_bass_in_octaves_on_the_accents():
    for p, ev in _section(_bars(), "A"):
        low = sorted((b, args[1]) for b, args, _ in _notes(ev, "piano") if args[1] < 55)
        assert low
        by_beat = {}
        for b, m in low:
            by_beat.setdefault(round(b * 4) / 4, []).append(m)
        assert 0.0 in by_beat
        for ms in by_beat.values():
            assert len(ms) == 2 and ms[1] - ms[0] == 12


def test_a_chromatic_bass_line_closes_the_sections():
    lines = 0
    for name in ("A", "A'"):
        for p, ev in _section(_bars(n=80), name):
            if p.section_bar != tango.SECTION_BARS[name] - 1 or not [n for n in p.bass if n.beat == 3.5]:
                continue
            tail = [m for b, (_, m, *_), _ in sorted(_notes(ev, "bass")) if b >= 2]
            assert len(tail) == 4 and all(abs(b - a) == 1 for a, b in zip(tail, tail[1:]))
            lines += 1
    assert lines


def test_breakdown_holds_legato_chords_without_marcato():
    for p, ev in _section(_bars(), "breakdown"):
        held = _notes(ev, "strings")
        assert held and {round(b * 4) / 4 for b, *_ in held} <= {0.0, 2.0}
        assert all(args[2] >= 1.5 * 60 / STYLE.bpm for _, args, _ in held)
        assert not [b for b, args, _ in _notes(ev, "piano") if b % 1]


# ---------------------------------------------------------------- the ensemble is the percussion
def test_no_drum_kit_only_the_ensemble():
    for style in (STYLE, Style(**(STYLE.to_dict() | {"drums": "busy", "energy": 1.0}))):
        names = {e[0] for _, ev in _bars(style) for e in ev}
        assert names <= {"note", "drag", "chop"} and {"drag", "chop"} <= names


def test_arrastres_drag_into_a_tango_downbeat_and_stop_on_it():
    count = 0
    for p, ev in _bars():
        for n, args, b, _, _ in ev:
            if n == "drag":
                end = b + args[1] / (60 / STYLE.bpm)
                assert round(end, 3) in (2.0, 4.0) and args[1] > 0
                assert 36 <= args[0] <= 60
                assert "drums" in p.layers
                count += 1
    assert count


def test_string_chops_fall_off_the_beat_and_only_with_the_drum_layer():
    bars = _bars()
    for p, ev in bars:
        chops = [b for n, _, b, _, _ in ev if n == "chop"]
        if "drums" not in p.layers:
            assert not chops
        assert all(b % 1 == 0.5 for b in chops)
    assert any(n == "chop" for _, ev in _section(bars, "B'") for n, *_ in ev)
    quiet = Style(**(STYLE.to_dict() | {"drums": "none"}))
    assert not [e for _, ev in _bars(quiet) for e in ev if e[0] in ("drag", "chop")]


# ---------------------------------------------------------------- melody and dynamics
@pytest.mark.parametrize("lead, part", [("piano", "bandoneon"), ("rhodes", "bandoneon"), ("nylon", "violin"),
                                        ("pad", "violin")])
def test_the_melody_sings_on_bandoneon_or_violin(lead, part):
    style = Style(**(STYLE.to_dict() | {"lead": lead, "chords": "piano"}))
    for name in ("A", "B"):
        for p, ev in _section(_bars(style), name):
            got = sorted(args[1] for _, args, _ in _notes(ev, part))
            if "lead" in p.layers:
                assert set(n.midi for n in p.melody) <= set(got)
            else:
                assert not got or "counter" in p.layers


def test_chord_colours_pick_the_rhythm_section():
    for chords, part in tango.CHORD_PART.items():
        style = Style(**(STYLE.to_dict() | {"chords": chords}))
        a = _section(_bars(style), "A")
        assert all(_notes(ev, part) for _, ev in a)


def test_dynamics_swell_with_the_form():
    bars = _bars()
    level = lambda ev: np.mean([g for b, args, g in _notes(ev, "piano") if args[1] >= 55])
    first = [level(ev) for p, ev in bars if p.section == "B" and p.section_bar == 0]
    last = [level(ev) for p, ev in bars if p.section == "B" and p.section_bar == 7]
    assert np.mean(last) > 1.15 * np.mean(first)
    peak = np.mean([level(ev) for p, ev in _section(bars, "B'")])
    assert peak > 1.5 * np.mean([level(ev) for p, ev in _section(bars, "intro")])


# ---------------------------------------------------------------- moments hooks
def test_roll_impact_and_stinger_use_the_ensemble_not_a_drum_kit():
    plans = {p.cue: p for p in [Composer(STYLE, 3, TON, G.chord, moments.plan(
        "epic", 30, moments.default_style("epic", "tango")).sections).plan(i) for i in range(12)]}
    for hook, cue in ((G.roll, "roll"), (G.impact, "hit"), (G.stinger, "stinger")):
        piece = _Recorder(STYLE)
        G.start(piece)
        hook(piece, plans[cue])
        assert piece.events and {e[0] for e in piece.events} <= {"note", "drag", "chop"}
    piece = _Recorder(STYLE)
    G.roll(piece, plans["roll"])
    drags = [b + args[1] / (60 / STYLE.bpm) for n, args, b, _, _ in piece.events if n == "drag"]
    assert drags and round(max(drags), 3) == 4.0                # the roll drags into the hit


# ---------------------------------------------------------------- voices
def test_procedural_voices_are_finite_and_audible():
    v = G.instruments(np.random.default_rng(3), SR, None)
    for part in tango.PARTS:
        x = v.note(part, 60, 0.5, 0.8)
        assert np.all(np.isfinite(x)) and 0.01 < np.max(np.abs(x)) < 2.0, part
    d = v.drag(45, 0.4, 0.9)
    env = np.abs(d)
    assert len(d) == int(0.4 * SR)
    assert np.mean(env[:len(d) // 4]) < 0.5 * np.mean(env[-len(d) // 4:])   # it swells into the downbeat
    c = v.chop(1.0)
    assert np.all(np.isfinite(c)) and len(c) < 0.25 * SR and np.max(np.abs(c)) > 0.05


def test_samples_backend_uses_gm_programs_and_no_drum_kit():
    calls = []

    class Fake:
        def play(self, p, m, dur, vel=1.0):
            calls.append(("play", p.program))
            return np.ones(int(dur * SR) + 64) * 0.1

        def hit(self, h, g=1.0):
            calls.append(("hit", h.kit))
            return np.zeros(64)

    for chords, lead in (("piano", "piano"), ("rhodes", "nylon"), ("nylon", "pad"), ("pad", "rhodes")):
        style = Style(**(STYLE.to_dict() | {"chords": chords, "lead": lead}))
        inst = G.instruments(np.random.default_rng(4), SR, Fake())
        piece = Piece(style, np.random.default_rng(5), inst, TON, SR, np.zeros((SR * 30, 2)))
        G.start(piece)
        comp = Composer(style, 7, TON, G.chord)
        for bar in range(32):
            G.schedule(piece, comp.plan(bar))
    programs = {c[1] for c in calls if c[0] == "play"}
    assert not [c for c in calls if c[0] == "hit"]
    assert {0, 23, 24, 32, 40, 48} <= programs <= {0, 21, 23, 24, 32, 40, 42, 48}


# ---------------------------------------------------------------- the genre contract, both backends
def _engine(backend, seed, **style):
    s = Style(genre="tango", **({"drums": "busy", "energy": 0.9, "lead_density": 0.9} | style))
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
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.0 and np.max(np.abs(x)) > 0.01
    _seamless(bars)
    assert len(x) / SR / elapsed > 10, f"{backend}: only {len(x) / SR / elapsed:.1f}x real time"
    again = _engine(backend, seed=31)
    assert np.array_equal(x, np.concatenate([again.render_bar() for _ in range(8)]))


def test_renders_quiet_styles_too(registered):
    e = _engine("procedural", seed=32, drums="none", lead="none", energy=0.1, vinyl=0, space=0)
    x = np.concatenate([e.render_bar() for _ in range(4)])
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.0


def test_switching_to_tango_and_back_joins_seamlessly(registered):
    e = Engine(Style(drums="soft"), seed=33, samples=False)
    bars = [e.render_bar() for _ in range(3)]
    e.queue_style(Style(genre="tango", drums="soft"))
    bars += [e.render_bar() for _ in range(3)]
    assert e.genre is G
    e.queue_style(Style(drums="soft"))
    bars += [e.render_bar() for _ in range(2)]
    _seamless(bars)


def _loudest(x, seconds=0.5, step=0.05):
    w, s = int(seconds * SR), int(step * SR)
    power = np.mean(x.astype(float) ** 2, axis=1)
    csum = np.concatenate([[0.0], np.cumsum(power)])
    starts = np.arange(0, len(x) - w, s)
    return float(starts[int(np.argmax(csum[starts + w] - csum[starts]))] / SR)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("kind", moments.KINDS)
@pytest.mark.parametrize("seconds", [12, 20])
def test_moments_render_with_the_hit_or_reveal_loudest(registered, kind, backend, seconds):
    style = moments.default_style(kind, genre="tango")
    m = moments.render(kind, seconds, style=style, seed=8, samples=REAL if backend == "samples" else False)
    x = m.audio
    assert m.backend == backend and m.style.genre == "tango"
    assert x.shape == (seconds * SR, 2) and np.all(np.isfinite(x))
    assert np.max(np.abs(x)) == pytest.approx(PEAK, abs=1e-6) and np.all(x[-1] == 0)
    hit, bar = m.plan.hit_seconds, m.plan.bar_samples / SR
    assert abs(_loudest(x) - hit) <= bar, f"{kind}/{backend}: loudest at {_loudest(x):.2f}, hit {hit:.2f}"
