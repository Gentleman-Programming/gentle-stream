"""Dubstep: the traits that define the genre, measured on what `schedule` plays and on the rendered voices."""
import numpy as np
import pytest

from gentle_stream import genres
from gentle_stream.composer import Composer
from gentle_stream.engine import Engine
from gentle_stream.genres.base import Piece
from gentle_stream.synth import SR, hp
from gentle_stream.style import Style

G = genres.get("dubstep")
STYLE = Style(genre="dubstep", key="F", mode="minor", bpm=140, chords="piano", lead="piano", drums="lofi",
              energy=0.5, lead_density=0.8)
BEAT = 60 / STYLE.bpm
RATES = {1.0, 0.5, 0.25, 1 / 3, 2 / 3}     # LFO periods in beats: 1/4, 1/8, 1/16, 1/8 triplet, 1/4 triplet


class _Voices:
    """Stands in for the genre's voices (or for the shared FluidR3_GM set): every call is recorded and returns
    a token that `_Recorder.add` turns into an event."""

    def __init__(self, real=None):
        self.real = real

    def __getattr__(self, name):
        if name.startswith("_") or name == "real":
            raise AttributeError(name)
        if self.real is not None and not callable(getattr(self.real, name)):
            return getattr(self.real, name)
        return lambda *args, **kw: (name, args)


class _Recorder(Piece):
    def __init__(self, style, inst, ton):
        super().__init__(style, np.random.default_rng(5), inst, ton, SR, np.zeros((10, 2)))
        self.events = []

    def add(self, sig, t, pan=0.0, g=1.0):
        name, args = sig
        self.events.append((name, args, t, pan, g))


def _bars(style=STYLE, n=40, seed=7):
    ton = 41
    piece = _Recorder(style, _Voices(G.instruments(np.random.default_rng(1), SR, None)), ton)
    G.start(piece)
    comp = Composer(style, seed, ton, G.chord)
    out = []
    for bar in range(n):
        plan = comp.plan(bar)
        piece.events = []
        G.schedule(piece, plan)
        out.append((plan, piece.events))
    return out


def _beats(events, name, beat=BEAT):
    return sorted(round(t / beat, 3) for n, _, t, _, _ in events if n == name)


def _names(events):
    return {e[0] for e in events}


def _section(bars, *names):
    found = [(p, ev) for p, ev in bars if p.section in names]
    assert found, f"no {names} bars"
    return found


def _builds(bars):
    """The bars right before a strong section (the last bar of A and of the breakdown)."""
    return [(p, ev) for (p, ev), (q, _) in zip(bars, bars[1:]) if q.section in ("B", "B'") and q.section_bar == 0]


# ---------------------------------------------------------------- prompt hints and master
def test_prompt_hints():
    assert G.bpm == (136, 140)
    assert G.modes[0] == "minor" and "phrygian" in G.modes
    assert "wobble" in " ".join(G.voices.values()) and "growl" in G.voices["rhodes"]


def test_master_is_gritty_but_controlled_and_clean():
    lofi = genres.get("lofi")
    for s in (STYLE, Style(genre="dubstep", vinyl=1, space=1, brightness=0)):
        m, ref = G.master(s), lofi.master(s)
        assert m.vinyl == 0 and m.wow == 0                 # no vinyl, no tape wow, whatever the style asks
        assert 1.8 <= m.drive <= 3.0                       # moderate drive: gritty, not crushed
        assert m.ratio >= 3 and m.threshold <= -18         # a firm bus compressor holds the drops
        assert m.tone >= 8000 and m.tone >= ref.tone       # open top end
        assert m.space <= 0.7 and m.reverb < ref.reverb + 0.05


# ---------------------------------------------------------------- drums
def test_half_time_kick_on_one_snare_on_three():
    bars = _bars()
    builds = {p.bar for p, _ in _builds(bars)}
    for p, ev in _section(bars, "A", "B", "A'", "B'", "release"):
        if p.bar in builds:
            continue
        kicks = _beats(ev, "kick")
        assert kicks[0] == 0 and 2 not in kicks and len(kicks) <= 3   # one, plus sparse ghost kicks
        assert _beats(ev, "snare") == [2]                             # the half-time backbeat on beat 3
        ghosts = [g for n, _, t, _, g in ev if n == "kick" and t > 0]
        main = [g for n, _, t, _, g in ev if n == "kick" and t == 0]
        assert all(g < main[0] for g in ghosts)


def test_hats_in_eighths_sixteenths_and_triplets():
    bars = _bars()
    builds = {p.bar for p, _ in _builds(bars)}
    grids = set()
    for p, ev in _section(bars, "A", "B", "A'", "B'"):
        if p.bar in builds:
            continue
        hats = _beats(ev, "hat")
        assert hats
        for b in hats:
            on16 = abs(b * 4 - round(b * 4)) < 1e-3
            on_triplet = abs(b * 3 - round(b * 3)) < 1e-2
            assert on16 or on_triplet
            grids.add("16" if on16 and abs(b * 2 - round(b * 2)) > 1e-3 else
                      "triplet" if not on16 else "8")
    assert grids == {"8", "16", "triplet"}


def test_no_drums_in_intro_breakdown_or_without_the_drum_layer():
    bars = _bars()
    builds = {p.bar for p, _ in _builds(bars)}
    for p, ev in _section(bars, "intro", "breakdown"):
        if p.bar not in builds:
            assert not {"kick", "snare", "hat"} & _names(ev)
    for p, ev in _bars(Style(**(STYLE.to_dict() | {"drums": "none"}))):
        assert not {"kick", "snare", "hat"} & _names(ev)


def test_build_up_riser_and_accelerating_snare_roll_before_strong_sections():
    bars = _bars()
    builds = _builds(bars)
    assert {p.section for p, _ in builds} == {"A", "breakdown"}
    for p, ev in builds:
        assert "riser" in _names(ev)
        roll = _beats(ev, "snare")
        assert len(roll) >= 8
        gaps = np.diff(roll)
        assert np.all(gaps[1:] <= gaps[:-1] + 1e-9) and gaps[-1] < gaps[0] / 2   # the roll accelerates
        gains = [g for n, _, t, _, g in sorted(ev, key=lambda e: e[2]) if n == "snare"]
        assert gains[-1] > gains[0]                                                # and swells
    build_bars = {p.bar for p, _ in builds}
    for p, ev in bars:
        if p.bar not in build_bars:
            assert "riser" not in _names(ev)


# ---------------------------------------------------------------- bass: sub, wobble, growl
def test_sub_follows_the_bass_line_in_every_section():
    for p, ev in _bars():
        subs = sorted((t, args[0]) for n, args, t, _, _ in ev if n == "sub")
        assert [round(t / BEAT, 3) for t, _ in subs] == [n.beat for n in p.bass]
        for (_, m), n in zip(subs, p.bass):
            assert (m - n.midi) % 12 == 0 and m <= 40                     # same note, deep octave


def test_heavy_drop_on_strong_sections_with_wobble_rates_changing_per_half_bar():
    bars = _bars()
    rates = set()
    for p, ev in _section(bars, "B", "B'"):
        assert p.energy > 0.7
        bass = [(n, args) for n, args, *_ in ev if n in ("wobble", "growl")]
        assert len(bass) == len(p.bass)                                  # one wobble per bass note
        for _, args in bass:
            assert set(args[4]) <= RATES
            rates.update(args[4])
        if p.section_bar == 0:
            assert "crash" in _names(ev)
    assert len(rates) >= 3
    # per half-bar choices vary inside a bar somewhere in the drop
    halves = {tuple(args[4]) for p, ev in _section(bars, "B", "B'") for n, args, *_ in ev
              if n in ("wobble", "growl") and len(args[4]) > 1}
    assert any(len(set(h)) > 1 for h in halves)


def test_growl_answers_the_wobble_and_takes_over_with_rhodes():
    bars = _bars()
    drop = _section(bars, "B", "B'")
    assert any("growl" in _names(ev) for _, ev in drop) and any("wobble" in _names(ev) for _, ev in drop)
    growl = [e for _, ev in _section(_bars(Style(**(STYLE.to_dict() | {"chords": "rhodes"}))), "B", "B'") for e in ev
             if e[0] in ("wobble", "growl")]
    assert sum(e[0] == "growl" for e in growl) > sum(e[0] == "wobble" for e in growl)


def test_quiet_sections_are_sparse_with_dark_pads_and_plucks():
    bars = _bars()
    for p, ev in _section(bars, "intro", "breakdown"):
        names = _names(ev)
        assert "pad" in names and "pluck" in names
        assert not {"wobble", "growl"} & names
    for p, ev in _section(bars, "A", "A'"):                              # wobble only lightly, slow LFO
        for n, args, *_ in ev:
            if n == "wobble":
                assert args[5] <= 0.5 and min(args[4]) >= 2 / 3


def test_low_energy_style_gets_no_full_drop():
    calm = Style(**(STYLE.to_dict() | {"energy": 0.2}))
    for p, ev in _section(_bars(calm), "B"):
        assert p.energy <= 0.7
        assert "growl" not in _names(ev)
        assert all(args[5] <= 0.5 for n, args, *_ in ev if n == "wobble")


def test_lead_plays_the_melody():
    for p, ev in _bars():
        leads = [e for e in ev if e[0] == "lead"]
        assert len(leads) == (len(p.melody) if "lead" in p.layers else 0)


# ---------------------------------------------------------------- voices
def _bright(x, a, b):
    """High-band RMS (the filter opening) of `x` between beats a and b."""
    y = hp(x, 900, SR)[int(a * BEAT * SR):int(b * BEAT * SR)]
    return float(np.sqrt(np.mean(y ** 2)))


def test_wobble_filter_opens_and_closes_at_the_lfo_rate():
    inst = G.instruments(np.random.default_rng(3), SR, None)
    x = inst.wobble(41, 4 * BEAT, BEAT, 0.0, (1.0, 1.0), 1.0)
    assert np.all(np.isfinite(x)) and 0.05 < np.max(np.abs(x)) < 1.5
    for k in range(3):     # quarter-note LFO: closed on the beat, open half-way between beats
        assert _bright(x, k + 0.4, k + 0.6) > 4 * _bright(x, k + 0.95, k + 1.05)
    fast = inst.wobble(41, 2 * BEAT, BEAT, 0.0, (0.25,), 1.0)
    assert _bright(fast, 0.1, 0.15) > 3 * _bright(fast, 0.24, 0.26)


def test_lfo_is_continuous_across_half_bars_and_bars():
    from gentle_stream.genres import dubstep
    for a in RATES:
        for b in RATES:
            beats = np.linspace(0, 4, 40001)
            lfo = dubstep.lfo(beats, (a, b))
            assert np.all((lfo >= 0) & (lfo <= 1))
            assert np.max(np.abs(np.diff(lfo))) < 0.01                  # no jump where the rate changes
            assert lfo[0] == pytest.approx(0) and lfo[-1] == pytest.approx(0, abs=1e-3)   # closed at bar lines


def test_growl_is_distorted_and_formant_filtered():
    inst = G.instruments(np.random.default_rng(3), SR, None)
    w = inst.wobble(41, 2 * BEAT, BEAT, 0.0, (0.5,), 1.0)
    g = inst.growl(41, 2 * BEAT, BEAT, 0.0, (0.5,), 1.0)
    assert np.all(np.isfinite(g)) and 0.05 < np.max(np.abs(g)) < 1.5
    crest = lambda x: np.max(np.abs(x)) / np.sqrt(np.mean(x ** 2))
    assert crest(g) < crest(w)                                           # squashed by the drive
    spec = np.abs(np.fft.rfft(g)); f = np.fft.rfftfreq(len(g), 1 / SR)
    assert spec[(f > 400) & (f < 1400)].sum() > spec[(f > 3000) & (f < 6000)].sum() * 3   # vowel-like bands


def test_sub_is_a_clean_deep_sine():
    inst = G.instruments(np.random.default_rng(3), SR, None)
    x = inst.sub(33, 1.0)
    spec = np.abs(np.fft.rfft(x)); f = np.fft.rfftfreq(len(x), 1 / SR)
    assert f[np.argmax(spec)] == pytest.approx(55, abs=2)
    assert spec[f > 200].sum() < 0.05 * spec.sum()
    assert abs(x[0]) < 1e-3 and abs(x[-1]) < 1e-3


def test_samples_backend_uses_standard_or_power_kits_and_keeps_bass_procedural():
    calls = []

    class Fake:
        def play(self, p, m, dur, vel=1.0):
            calls.append(("play", p.program, p.bank))
            return np.zeros(64)

        def hit(self, h, g=1.0):
            calls.append(("hit", h.kit, h.key))
            return np.zeros(64)

    inst = G.instruments(np.random.default_rng(4), SR, Fake())
    piece = Piece(STYLE, np.random.default_rng(5), inst, 41, SR, np.zeros((SR * 30, 2)))
    G.start(piece)
    comp = Composer(STYLE, 7, 41, G.chord)
    for bar in range(40):
        G.schedule(piece, comp.plan(bar))
    programs = {c[1] for c in calls if c[0] == "play"}
    kits = {c[1] for c in calls if c[0] == "hit"}
    assert programs and not programs & set(range(32, 40))               # no GM bass: wobble and sub are synthesized
    assert kits and kits <= {0, 16}
    proc = G.instruments(np.random.default_rng(4), SR, None)
    assert np.array_equal(inst.wobble(41, 1.0, BEAT, 0.0, (0.5,), 1.0), proc.wobble(41, 1.0, BEAT, 0.0, (0.5,), 1.0))


# ---------------------------------------------------------------- rendered drop
def test_drop_renders_seamlessly_and_louder_than_the_intro():
    e = Engine(Style(**(STYLE.to_dict() | {"drums": "busy", "energy": 0.7})), seed=21, samples=False)
    bars = [e.render_bar() for _ in range(22)]
    sections = [e.composer.plan(i).section for i in range(22)]
    x = np.concatenate(bars)
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.0
    limit = np.max(np.concatenate([np.abs(np.diff(b, axis=0)) for b in bars]))
    for a, b in zip(bars, bars[1:]):
        assert np.all(np.abs(b[0] - a[-1]) <= limit)
    rms = lambda i: float(np.sqrt(np.mean(bars[i] ** 2)))
    drop = [i for i, s in enumerate(sections) if s == "B"]
    intro = [i for i, s in enumerate(sections) if s == "intro"][1:]
    assert drop and np.mean([rms(i) for i in drop]) > 1.6 * np.mean([rms(i) for i in intro])
