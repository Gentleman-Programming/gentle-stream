"""Electro house: the traits that define the genre, measured on what `schedule` plays and on the rendered voices."""
import numpy as np

from gentle_stream import genres
from gentle_stream.composer import Composer
from gentle_stream.genres import electrohouse
from gentle_stream.genres.base import Piece
from gentle_stream.synth import SR
from gentle_stream.style import Style

G = genres.get("electrohouse")
STYLE = Style(genre="electrohouse", key="A", mode="minor", bpm=128, chords="piano", lead="piano", drums="lofi",
              energy=0.5, lead_density=0.8)
STRONG = ("B", "B'")
TONAL = ("pad", "stab", "bass")


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
    """Records (name, args, t, pan, g, duck depth); a sidechained voice arrives wrapped in a `pump` token."""

    def __init__(self, style, inst, ton):
        super().__init__(style, np.random.default_rng(5), inst, ton, SR, np.zeros((10, 2)))
        self.events = []

    def add(self, sig, t, pan=0.0, g=1.0):
        name, args = sig
        depth = None
        if name == "pump":
            (name, inner), _, _, depth = args
            args = inner
        self.events.append((name, args, t, pan, g, depth))


def _bars(style=STYLE, n=48, seed=7):
    ton = 45
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


def _beats(events, name, style=STYLE):
    beat = 60 / style.bpm
    return sorted(round(t / beat, 3) for n, _, t, *_ in events if n == name)


def _section(bars, name):
    found = [(p, ev) for p, ev in bars if p.section == name]
    assert found, f"no {name} bars"
    return found


def _is_build(p):
    """The last two bars before a strong section: the end of A and of the breakdown."""
    return (p.section == "A" and p.section_bar >= 6) or (p.section == "breakdown" and p.section_bar >= 2)


# ---------------------------------------------------------------- prompt hints and master
def test_prompt_hints():
    assert G.bpm == (124, 132)
    assert G.modes[0] == "minor"
    assert "supersaw" in G.voices["pad"] and "supersaw" in G.voices["piano"]


def test_master_is_loud_clean_and_compressed():
    lofi = genres.get("lofi")
    for s in (STYLE, Style(genre="electrohouse", vinyl=1, space=1, brightness=0)):
        m, ref = G.master(s), lofi.master(s)
        assert m.vinyl == 0 and m.wow == 0                   # no vinyl, no tape wow, whatever the style asks
        assert m.drive > ref.drive and m.drive >= 2.2         # louder, more saturated than lo-fi
        assert m.threshold < ref.threshold and m.ratio > ref.ratio   # harder bus compression
        assert m.tone >= 9000 and m.tone >= ref.tone          # open top end
        assert m.reverb <= ref.reverb + 0.05                  # a tight club mix, not a washed room


# ---------------------------------------------------------------- drums
def test_four_on_the_floor_kick_in_every_drum_bar():
    for p, ev in _bars():
        if "drums" in p.layers:
            assert _beats(ev, "kick") == [0, 1, 2, 3]
        else:
            assert not _beats(ev, "kick")


def test_clap_on_two_and_four():
    for p, ev in _bars():
        assert _beats(ev, "clap") == ([1, 3] if "drums" in p.layers else [])


def test_open_hats_on_the_offbeats_and_sixteenth_closed_hats_in_strong_sections():
    bars = _bars()
    for name in STRONG:
        for p, ev in _section(bars, name):
            assert _beats(ev, "open_hat") == [0.5, 1.5, 2.5, 3.5]
            hats = _beats(ev, "hat")
            assert len(hats) == 16 and np.allclose(np.diff(hats), 0.25)
    for name in ("A", "A'"):
        for p, ev in _section(bars, name):
            assert _beats(ev, "open_hat") == [0.5, 1.5, 2.5, 3.5]
            assert not _beats(ev, "hat")
    for p, ev in _bars(Style(**(STYLE.to_dict() | {"drums": "none"}))):
        assert not {"kick", "clap", "hat", "open_hat", "snare", "crash"} & {e[0] for e in ev}


def test_crash_marks_every_drop():
    for p, ev in _bars():
        assert bool(_beats(ev, "crash")) == (p.section in STRONG and p.section_bar == 0)


# ---------------------------------------------------------------- builds and drops
def test_riser_snare_roll_and_filter_sweep_build_into_every_drop():
    bars = _bars()
    builds = [(p, ev) for p, ev in bars if _is_build(p)]
    assert {p.section for p, _ in builds} == {"A", "breakdown"}
    for p, ev in bars:
        names = {e[0] for e in ev}
        if not _is_build(p):
            assert "riser" not in names and "snare" not in names
            continue
        assert ("riser" in names) == (p.section_bar in (6, 2))                   # one riser spans both bars
        pads = [args for n, args, *_ in ev if n == "pad"]
        assert pads and all(a[4] > a[3] for a in pads)       # the pad's low-pass opens through the bar
    for i, (p, ev) in enumerate(bars[:-1]):
        if _is_build(p) and p.section_bar in (6, 2):
            nxt = bars[i + 1][1]
            n1, n2 = len(_beats(ev, "snare")), len(_beats(nxt, "snare"))
            assert 4 <= n1 < n2                               # the roll speeds up into the drop
            g1 = [g for n, _, _, _, g, _ in ev if n == "snare"]
            g2 = [g for n, _, _, _, g, _ in nxt if n == "snare"]
            assert max(g1) <= min(g2) + 1e-9 or np.mean(g2) > np.mean(g1)   # and grows louder
            # the sweep keeps rising across the two bars, without a jump back
            c1 = max(a[4] for n, a, *_ in ev if n == "pad")
            c2 = min(a[3] for n, a, *_ in nxt if n == "pad")
            assert c2 >= c1 * 0.99


def test_breakdown_has_filtered_pads_and_no_bass_or_drums():
    for p, ev in _section(_bars(), "breakdown"):
        names = {e[0] for e in ev}
        assert not {"kick", "clap", "hat", "open_hat", "bass", "stab"} & names
        pads = [args for n, args, *_ in ev if n == "pad"]
        assert len(pads) >= 3
        if not _is_build(p):
            assert all(a[4] <= 1200 for a in pads)           # dark, filtered


# ---------------------------------------------------------------- dirty bass riff
def _active(plan, beat):
    return max((x for x in plan.bass if x.beat <= beat), key=lambda x: x.beat, default=plan.bass[0])


def test_dirty_bass_riff_in_sixteenths_on_drops_and_eighths_elsewhere():
    bars = _bars()
    for name in STRONG + ("A", "A'"):
        for p, ev in _section(bars, name):
            bass = sorted((t, args) for n, args, t, *_ in ev if n == "bass")
            steps = [round(t / (60 / STYLE.bpm) * 4, 3) for t, _ in bass]
            assert len(bass) >= 5 and all(s == int(s) for s in steps)
            if name in STRONG:
                assert any(int(s) % 2 for s in steps)            # 16th-note riff
            else:
                assert all(int(s) % 2 == 0 for s in steps)       # 8th-note riff
            for (t, args), s in zip(bass, steps):               # the riff follows the composer's bass line
                assert (args[0] - _active(p, s / 4).midi) in (0, 7, 12)


def test_bass_low_pass_moves_smoothly_across_bars():
    bars = [(p, ev) for p, ev in _bars() if p.section in STRONG]
    cuts = []
    for p, ev in bars:
        for n, args, t, *_ in sorted(ev, key=lambda e: e[2]):
            if n == "bass":
                cuts.append((p.bar * 4 + t / (60 / STYLE.bpm), args[3]))
    values = [c for _, c in cuts]
    assert len(set(values)) >= 4                                # the cutoff moves...
    for (b0, c0), (b1, c1) in zip(cuts, cuts[1:]):
        if b1 - b0 <= 1:                                        # ...gradually, bar joins included
            assert 1 / 1.35 <= c1 / c0 <= 1.35


# ---------------------------------------------------------------- sidechain pumping
def test_pads_chords_and_bass_pump_on_every_kick():
    bars = _bars()
    depth = {}
    for p, ev in bars:
        for n, args, t, pan, g, d in ev:
            if n in TONAL:
                if "drums" in p.layers:
                    assert d is not None and d > 0, (p.section, n)
                    depth.setdefault(p.section, set()).add(d)
                else:
                    assert d is None or d == 0
    assert min(depth["B"]) > max(depth["A"])                   # deeper pumping on the drops


def test_sidechain_curve_is_continuous_in_absolute_time():
    beat = 60 / 128
    n = int(round(4 * beat * SR))
    whole = electrohouse.sidechain(3 * n, 0.0, beat, SR, 0.6)
    bars = np.concatenate([electrohouse.sidechain(n, 0.0, beat, SR, 0.6) for _ in range(3)])
    assert np.allclose(whole, bars, atol=5e-3)                 # phase restarts nowhere but on the beat grid
    assert whole[0] == 1.0 and np.max(np.abs(np.diff(whole))) < 0.01
    k = int(beat * SR)
    assert np.min(whole[k:k + int(0.05 * SR)]) < 0.45          # ducks right after the kick...
    assert whole[int(1.9 * beat * SR)] > 0.95                  # ...and is back before the next one
    # a note starting off the beat is ducked on the same absolute grid
    ins = G.instruments(np.random.default_rng(2), SR, None)
    x = ins.pump(np.ones(int(2 * beat * SR)), 0.25 * beat, beat, 0.6)
    assert np.allclose(x, electrohouse.sidechain(len(x), 0.25 * beat, beat, SR, 0.6))
    assert x[0] < 0.95 and np.min(x[int(0.75 * beat * SR):int(0.85 * beat * SR)]) < 0.45


# ---------------------------------------------------------------- voices
def test_supersaw_pad_stab_and_dirty_bass_sound_right():
    ins = G.instruments(np.random.default_rng(3), SR, None)
    pad = ins.pad(57, 2.0, 0.8, 3000, 3000)
    assert np.all(np.isfinite(pad)) and np.max(np.abs(pad)) > 0.01
    env = np.sqrt(np.convolve(pad ** 2, np.ones(441) / 441, "valid"))[int(0.8 * SR):int(1.8 * SR)]
    assert np.std(env) / np.mean(env) > 0.03                   # detuned saws beat
    dark, open_ = ins.pad(57, 2.0, 0.8, 500, 500), ins.pad(57, 2.0, 0.8, 500, 8000)

    def centroid(x):
        s = np.abs(np.fft.rfft(x)); f = np.fft.rfftfreq(len(x), 1 / SR)
        return float(np.sum(s * f) / np.sum(s))
    half = int(0.9 * SR)
    assert centroid(open_[half:2 * half]) > 1.5 * centroid(dark[half:2 * half])   # the sweep opens the filter
    stab = ins.stab("piano", 69, 0.2, 0.9)
    assert np.all(np.isfinite(stab)) and len(stab) < 0.6 * SR
    bass = ins.bass(33, 0.5, 0.9, 900)
    assert np.all(np.isfinite(bass)) and np.max(np.abs(bass)) < 1.0
    seg = bass[int(0.1 * SR):int(0.4 * SR)]
    spec = np.abs(np.fft.rfft(seg)); f = np.fft.rfftfreq(len(seg), 1 / SR)
    assert np.sum(spec[(f > 200) & (f < 2000)]) > 0.5 * np.sum(spec[f < 120])   # dirty mids, not a clean sub
    assert centroid(bass[:int(0.06 * SR)]) > centroid(bass[int(0.3 * SR):int(0.45 * SR)])   # filter envelope
    kick = ins.kick()
    a = np.max(np.abs(kick[:int(0.02 * SR)]))
    assert a > 0.3 and np.max(np.abs(kick[int(0.3 * SR):])) < 0.1 * a    # punchy and tight


def test_samples_backend_uses_the_electronic_kit_gm_synths_and_a_procedural_bass():
    calls = []

    class Fake:
        def play(self, p, m, dur, vel=1.0):
            calls.append(("play", p.program, p.bank))
            return np.zeros(64)

        def hit(self, h, g=1.0):
            calls.append(("hit", h.kit, h.key))
            return np.zeros(64)

    inst = G.instruments(np.random.default_rng(4), SR, Fake())
    piece = Piece(STYLE, np.random.default_rng(5), inst, 45, SR, np.zeros((SR * 30, 2)))
    G.start(piece)
    comp = Composer(STYLE, 7, 45, G.chord)
    for bar in range(24):
        G.schedule(piece, comp.plan(bar))
    programs = {c[1] for c in calls if c[0] == "play"}
    kits = {c[1] for c in calls if c[0] == "hit"}
    assert {81, 90} <= programs                                # saw stabs/lead, polysynth pad
    assert not programs & set(range(32, 40))                   # no GM bass: the dirty bass stays procedural
    assert kits == {24}
    assert np.any(piece._acc)                                  # the procedural bass and riser still sound
