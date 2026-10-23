"""Electro (electro-funk): the traits that define the genre, measured on what `schedule` plays (through a recording
voice set) and on the rendered voices. The rendering contract (clean, fast, deterministic, both backends) lives in
tests/test_genres_contract.py."""
import numpy as np
import pytest

from gentle_stream import genres
from gentle_stream.composer import Composer
from gentle_stream.genres.base import Piece
from gentle_stream.synth import SR
from gentle_stream.style import Style

G = genres.get("electro")
STYLE = Style(genre="electro", key="A", mode="minor", bpm=116, chords="piano", lead="piano", drums="lofi",
              energy=0.5, lead_density=0.8)
DRUMS = {"kick", "snare", "clap", "hat", "cowbell", "rim"}


class _Voices:
    """Stands in for the genre's voices: every call is recorded and returns a token that `_Recorder.add` turns into
    an event (name, positional args, keyword args)."""

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *args, **kw: (name, args, kw)


class _Recorder(Piece):
    def __init__(self, style, ton, seed=5):
        super().__init__(style, np.random.default_rng(seed), _Voices(), ton, SR, np.zeros((10, 2)))
        self.events = []

    def add(self, sig, t, pan=0.0, g=1.0):
        name, args, kw = sig
        self.events.append((name, args, kw, t, g))


def _bars(style=STYLE, n=48, seed=7):
    piece = _Recorder(style, 45, seed)
    G.start(piece)
    comp = Composer(style, seed, 45, G.chord)
    out = []
    for bar in range(n):
        plan = comp.plan(bar)
        piece.events = []
        G.schedule(piece, plan)
        out.append((plan, piece.events))
    return out


def _beats(events, name, style=STYLE):
    beat = 60 / style.bpm
    return sorted(round(e[3] / beat, 3) for e in events if e[0] == name)


def _section(bars, *names):
    found = [(p, ev) for p, ev in bars if p.section in names]
    assert found, f"no {names} bars"
    return found


def _offgrid(beats, step=1.0):
    """Positions that do not fall on a multiple of `step` beats."""
    return [b for b in beats if abs(b / step - round(b / step)) > 1e-6]


# ---------------------------------------------------------------- prompt hints and master
def test_prompt_hints():
    lo, hi = G.bpm
    assert 100 <= lo and hi <= 132
    assert "808" in G.description and "four-on-the-floor" in G.description
    assert "square" in G.voices["piano"] and "string" in G.voices["pad"]


def test_master_is_tight_dry_and_clean():
    lofi = genres.get("lofi")
    for s in (STYLE, Style(genre="electro", vinyl=1, space=1, brightness=0)):
        m, ref = G.master(s), lofi.master(s)
        assert m.vinyl == 0 and m.wow == 0                   # no vinyl, no tape, whatever the style asks
        assert m.reverb < 0.5 * ref.reverb and m.space <= 0.3 and m.room < ref.room
        assert m.tone >= 8000                                # crisp top end
        assert m.ratio >= 3 and m.threshold <= -20           # tight bus compression


# ---------------------------------------------------------------- drums
@pytest.mark.parametrize("seed", [1, 2, 3, 4, 7, 11])
def test_kicks_are_syncopated_breakbeats_never_four_on_the_floor(seed):
    drum_bars = [(p, ev) for p, ev in _bars(seed=seed) if "drums" in p.layers]
    assert drum_bars
    for p, ev in drum_bars:
        kicks = _beats(ev, "kick")
        assert kicks[0] == 0                                 # the one is always there
        assert kicks != [0, 1, 2, 3]
        assert _offgrid(kicks)                               # at least one kick off the quarter grid
        assert all(abs(k * 4 - round(k * 4)) < 1e-6 for k in kicks)   # on the 16th grid


def test_some_kicks_land_on_off_eighth_sixteenths():
    found = False
    for seed in range(1, 9):
        for p, ev in _bars(seed=seed, n=20):
            if "drums" in p.layers and _offgrid(_beats(ev, "kick"), 0.5):
                found = True
    assert found


def test_kick_pattern_varies_over_a_two_bar_phrase():
    bars = [(p, ev) for p, ev in _section(_bars(), "A", "B")]
    patterns = {tuple(_beats(ev, "kick")) for _, ev in bars}
    assert len(patterns) >= 2


def test_snare_and_clap_together_on_two_and_four():
    for p, ev in _section(_bars(), "A", "B", "A'", "B'"):
        assert _beats(ev, "snare") == [1, 3]
        assert _beats(ev, "clap") == [1, 3]


def test_crisp_sixteenth_hats_with_accents():
    for p, ev in _section(_bars(), "B", "B'"):
        hats = [e for e in ev if e[0] == "hat"]
        beats = _beats(ev, "hat")
        assert len(beats) == 16 and np.allclose(np.diff(beats), 0.25)
        levels = {round(e[4], 4) for e in hats}
        assert len(levels) >= 2                              # accented and ghost hats
        on_beat = [e[4] for e in hats if abs(e[3] / (60 / STYLE.bpm) % 1) < 1e-6]
        off = [e[4] for e in hats if abs((e[3] / (60 / STYLE.bpm) * 4) % 2 - 1) < 1e-6]
        assert np.mean(on_beat) > np.mean(off)               # the 16th offbeats are ghosted
    soft = Style(**(STYLE.to_dict() | {"drums": "soft"}))
    for p, ev in _section(_bars(soft), "B"):
        assert len(_beats(ev, "hat")) == 8                   # soft drums: eighths


def test_cowbell_accents_are_syncopated_in_high_energy_sections():
    bars = _bars()
    for p, ev in _section(bars, "B", "B'"):
        bells = _beats(ev, "cowbell")
        assert bells and len(_offgrid(bells)) == len(bells)   # never on the quarter grid
    soft = Style(**(STYLE.to_dict() | {"drums": "soft"}))
    for p, ev in _bars(soft):
        assert not _beats(ev, "cowbell") and not _beats(ev, "rim")


def test_no_drums_without_the_drum_layer():
    bars = _bars()
    for p, ev in _section(bars, "intro", "breakdown"):
        assert not DRUMS & {e[0] for e in ev}
    for p, ev in _bars(Style(**(STYLE.to_dict() | {"drums": "none"}))):
        assert not DRUMS & {e[0] for e in ev}


# ---------------------------------------------------------------- bass, stabs, arpeggio, lead
def test_funky_syncopated_bass_with_octave_jumps():
    beat = 60 / STYLE.bpm
    for p, ev in _section(_bars(), "A", "B"):
        bass = sorted((e[3] / beat, e[1][0], e[1][1] / beat) for e in ev if e[0] == "bass")
        assert len(bass) >= 5
        assert _offgrid([b for b, _, _ in bass], 0.5)        # 16th syncopation
        notes = [m for _, m, _ in bass]
        assert any(abs(a - b) == 12 for a, b in zip(notes, notes[1:]))   # octave jumps
        assert all(ln <= 0.75 for _, _, ln in bass)          # short, funky notes
        pcs = {n.midi % 12 for n in p.bass}
        assert all(m % 12 in pcs for m in notes)             # follows the composer's line


def test_bass_holds_the_line_in_quiet_sections():
    beat = 60 / STYLE.bpm
    for p, ev in _section(_bars(), "intro", "breakdown"):
        bass = sorted((round(e[3] / beat, 3), e[1][0]) for e in ev if e[0] == "bass")
        assert bass == sorted((round(n.beat, 3), n.midi) for n in p.bass)


def test_short_syncopated_chord_stabs():
    beat = 60 / STYLE.bpm
    for p, ev in _section(_bars(), "A", "B"):
        stabs = [e for e in ev if e[0] == "stab"]
        assert stabs and all(e[1][0] == "piano" for e in stabs)
        assert all(e[1][2] <= 0.5 * beat for e in stabs)     # short stabs
        onsets = sorted({round(e[3] / beat * 4) for e in stabs})
        assert any(o % 4 for o in onsets)                    # off the quarter grid
        assert len({e[1][1] for e in stabs}) >= 2            # chords, not single notes
    pad = Style(**(STYLE.to_dict() | {"chords": "pad"}))
    for p, ev in _bars(pad, n=24):
        assert not [e for e in ev if e[0] == "stab"]
        assert len([e for e in ev if e[0] == "pad"]) >= 3     # held string-synth chords


def test_sixteenth_arpeggio_in_high_energy_sections_and_breakdown():
    bars = _bars()
    for p, ev in _section(bars, "B", "B'", "breakdown"):
        arp = _beats(ev, "arp")
        assert len(arp) == 16 and np.allclose(np.diff(arp), 0.25)
        assert len({e[1][0] for e in ev if e[0] == "arp"}) >= 3
    for p, ev in _section(bars, "intro", "A"):
        assert not _beats(ev, "arp")


def test_arpeggio_filter_sweep_is_continuous_in_absolute_time():
    cuts = []
    for p, ev in _bars(n=48):
        arp = sorted((e[3], e[1][3]) for e in ev if e[0] == "arp")
        cuts.append((p.bar, [c for _, c in arp]))
    runs = []                                              # consecutive bars that all play the arpeggio
    for bar, cs in cuts:
        if len(cs) == 16 and runs and runs[-1][-1][0] == bar - 1:
            runs[-1].append((bar, cs))
        elif len(cs) == 16:
            runs.append([(bar, cs)])
    long_runs = [r for r in runs if len(r) >= 4]
    assert long_runs
    for run in long_runs:
        seq = np.concatenate([cs for _, cs in run])
        assert np.all((seq >= 0) & (seq <= 1))
        assert np.max(np.abs(np.diff(seq))) <= 0.11        # smooth, also across the bar joins
        assert np.ptp(seq) >= 0.4                           # it really sweeps
    # the phase depends on the absolute bar, not on where a section starts
    a = dict(cuts)
    for bar, cs in cuts:
        if len(cs) == 16 and bar + 4 in a and len(a[bar + 4]) == 16 and bar % 4 == 0:
            assert np.allclose(cs, a[bar + 4])


def test_lead_plays_the_melody_on_the_lead_colour():
    for p, ev in _bars():
        leads = [e for e in ev if e[0] == "lead"]
        assert len(leads) == (len(p.melody) if "lead" in p.layers else 0)
        assert all(e[1][3] == "piano" for e in leads)


# ---------------------------------------------------------------- voices
def _rms(x, a, b):
    return float(np.sqrt(np.mean(x[int(a * SR):int(b * SR)] ** 2)))


def _peak_freq(x, lo, hi):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    f = np.fft.rfftfreq(len(x), 1 / SR)
    band = (f >= lo) & (f <= hi)
    return f[band][np.argmax(spec[band])]


def test_808_kick_is_boomy_pitched_and_long():
    inst = G.instruments(np.random.default_rng(2), SR, None)
    x = inst.kick()
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.2
    assert len(x) >= 0.6 * SR
    assert _rms(x, 0.3, 0.45) > 0.15 * _rms(x, 0.0, 0.05)   # long boom, not a tight thud
    assert 38 <= _peak_freq(x[int(0.15 * SR):int(0.6 * SR)], 20, 400) <= 70


def test_808_clap_has_several_bursts_and_the_cowbell_two_tones():
    inst = G.instruments(np.random.default_rng(3), SR, None)
    clap = inst.clap()
    env = np.sqrt(np.convolve(clap[:int(0.04 * SR)] ** 2, np.ones(44) / 44, "same"))
    peaks = [i for i in range(1, len(env) - 1) if env[i] > env[i - 1] and env[i] >= env[i + 1]
             and env[i] > 0.3 * np.max(env)]
    groups = np.split(np.array(peaks), np.flatnonzero(np.diff(peaks) > 0.004 * SR) + 1)
    assert len(groups) >= 3                                 # the multi-burst hand clap
    bell = inst.cowbell()
    assert np.all(np.isfinite(bell)) and len(bell) < 0.6 * SR
    assert 500 <= _peak_freq(bell[:int(0.15 * SR)], 300, 650) <= 600
    assert 760 <= _peak_freq(bell[:int(0.15 * SR)], 700, 1000) <= 850


def test_arp_cut_opens_the_filter():
    inst = G.instruments(np.random.default_rng(4), SR, None)
    dark, bright = inst.arp(69, 0.15, 0.9, 0.0), inst.arp(69, 0.15, 0.9, 1.0)

    def hf(x):
        spec = np.abs(np.fft.rfft(x))
        f = np.fft.rfftfreq(len(x), 1 / SR)
        return spec[f > 3000].sum() / spec.sum()

    assert hf(bright) > 2 * hf(dark)


def test_samples_backend_uses_gm_synths_and_the_tr808_kit():
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
    for bar in range(32):
        G.schedule(piece, comp.plan(bar))
    programs = {c[1] for c in calls if c[0] == "play"}
    kits = {c[1] for c in calls if c[0] == "hit"}
    keys = {c[2] for c in calls if c[0] == "hit"}
    assert {81, 80, 38} <= programs                         # saw lead, square arpeggio, synth bass
    assert kits == {25}                                    # TR-808 kit
    assert {36, 38, 39, 42, 56} <= keys                    # kick, snare, clap, closed hat, cowbell
