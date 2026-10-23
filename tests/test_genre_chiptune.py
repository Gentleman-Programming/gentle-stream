"""Chiptune / 8-bit: the traits that define the genre, measured on its schedule and on its rendered voices.

Four console channels: a pulse lead (12.5/25/50% duty) with delayed vibrato, a pulse arpeggio that plays the chords
as fast single notes, a stepped triangle bass in octave-jumping eighths and noise drums; a dry, bright, clean master.
"""
import numpy as np
import pytest

from gentle_stream import genres
from gentle_stream.composer import BarPlan, Chord, Note
from gentle_stream.genres import chiptune
from gentle_stream.genres.base import Piece
from gentle_stream.style import Style
from gentle_stream.synth import SR, mtof

G = genres.get("chiptune")
TON = 50                                   # D
FULL = frozenset({"chords", "bass", "drums", "lead", "counter"})


class _Spy:
    """Wraps the genre's voices and remembers the last voice call, so each `add` can be attributed to it."""

    def __init__(self, inner):
        self.inner, self.last = inner, None

    def __getattr__(self, name):
        fn = getattr(self.inner, name)
        if not callable(fn):
            return fn

        def call(*a, **kw):
            self.last = (name, a, kw)
            return fn(*a, **kw)
        return call


class _Recorder(Piece):
    def __init__(self, style, seed=5, sampled=None):
        rng = np.random.default_rng(seed)
        spy = _Spy(G.instruments(rng, SR, sampled))
        super().__init__(style, rng, spy, TON, SR, np.zeros((int(24 * SR), 2)))
        self.events = []

    def add(self, sig, t, pan=0.0, g=1.0):
        name, a, kw = self.inst.last
        self.events.append((name, a, kw, t, g))
        super().add(sig, t, pan, g)

    def of(self, name):
        return [e for e in self.events if e[0] == name]


def _plan(energy=0.85, layers=FULL, section="B", passing=None, bass=None):
    return BarPlan(bar=13, section=section, section_bar=1, energy=energy, chord=Chord(1, (0, 3, 7, 10)),
                   passing=passing, key_offset=0, layers=layers,
                   melody=(Note(0, 74, 1.5), Note(1.5, 77, 0.5), Note(2, 76, 2)),
                   counter=(Note(0, 65, 2), Note(2, 62, 2)),
                   bass=bass or (Note(0, 38, 2.5), Note(2.5, 45, 1), Note(3.5, 42, 0.5)))


def _bar(style=None, seed=5, **plan):
    s = style or Style(genre="chiptune", bpm=120, drums="lofi", energy=0.8)
    piece = _Recorder(s, seed)
    G.start(piece)
    G.schedule(piece, _plan(**plan))
    return piece, 60 / s.bpm


# ---------------------------------------------------------------- master and voices
def test_master_is_dry_clean_and_bright_whatever_the_style():
    for s in (Style(genre="chiptune", vinyl=1, space=1, brightness=0), Style(genre="chiptune", vinyl=0.5)):
        m = G.master(s)
        assert m.vinyl == 0 and m.wow == 0          # no crackle, no tape: a console is steady and clean
        assert m.reverb <= 0.05 and m.room <= 0.4   # at most a tiny room
        assert m.tone >= 8000                       # bright


def test_both_backends_play_the_procedural_chip_voices():
    procedural = G.instruments(np.random.default_rng(0), SR, None)
    sampled = G.instruments(np.random.default_rng(0), SR, object())   # a sample set is ignored on purpose
    assert isinstance(procedural, chiptune.Chip) and isinstance(sampled, chiptune.Chip)


def _harmonics(x, f, k):
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    return np.array([spec[np.argmin(np.abs(freqs - f * h))] for h in range(1, k + 1)])


def test_lead_is_a_pulse_wave_with_the_requested_duty():
    ins = G.instruments(np.random.default_rng(0), SR, None)
    f = mtof(57)
    square = _harmonics(ins.lead(57, 0.1, 1.0, 0.5)[:int(0.08 * SR)], f, 4)
    quarter = _harmonics(ins.lead(57, 0.1, 1.0, 0.25)[:int(0.08 * SR)], f, 4)
    assert square[1] < 0.1 * square[0] and square[2] > 0.2 * square[0]   # 50%: odd harmonics only
    assert quarter[1] > 0.5 * quarter[0] and quarter[3] < 0.15 * quarter[0]   # 25%: strong 2nd, no 4th


def test_lead_vibrato_starts_after_the_attack():
    ins = G.instruments(np.random.default_rng(0), SR, None)
    x = ins.lead(57, 1.2, 1.0, 0.5)

    def period_spread(a, b):
        seg = x[int(a * SR):int(b * SR)]
        z = np.flatnonzero((seg[:-1] < 0) & (seg[1:] >= 0))
        return np.std(np.diff(z))
    assert period_spread(0.01, 0.09) < 0.5 * period_spread(0.5, 1.1)


def test_bass_voice_is_a_stepped_triangle():
    ins = G.instruments(np.random.default_rng(0), SR, None)
    f = mtof(45)
    x = ins.bass(45, 0.5, 1.0)
    h = _harmonics(x[int(0.05 * SR):int(0.45 * SR)], f, 3)
    assert h[2] / h[0] == pytest.approx(1 / 9, abs=0.05) and h[1] < 0.05 * h[0]   # triangle: odd, 1/k^2
    body = x[int(0.1 * SR):int(0.4 * SR)]
    assert len(np.unique(np.round(body / np.max(np.abs(body)), 6))) <= 40           # 4-bit steps, few levels


def test_noise_drums_are_short_and_noisy():
    ins = G.instruments(np.random.default_rng(0), SR, None)

    def length(x, share=0.99):   # seconds holding `share` of the energy
        e = np.cumsum(x ** 2)
        return np.searchsorted(e, share * e[-1]) / SR

    def flatness(x):
        p = np.abs(np.fft.rfft(x)) ** 2 + 1e-12
        return np.exp(np.mean(np.log(p))) / np.mean(p)
    snare, hat, kick = ins.snare(), ins.hat(), ins.kick()
    assert length(hat) < 0.04 and length(snare) < 0.25 and length(kick) < 0.3
    assert flatness(snare) > 0.1 and flatness(hat) > 0.1          # white-noise bursts
    spec = np.abs(np.fft.rfft(kick)); freqs = np.fft.rfftfreq(len(kick), 1 / SR)
    assert freqs[np.argmax(spec)] < 200                            # the kick thumps low


# ---------------------------------------------------------------- schedule
def test_chords_play_as_a_fast_monophonic_arpeggio_of_chord_tones():
    piece, B = _bar()
    arp = piece.of("arp")
    assert len(arp) >= 16
    times = sorted(e[3] for e in arp)
    steps = np.diff(times)
    assert len(set(times)) == len(times)                           # one note at a time
    assert np.allclose(steps, steps[0]) and steps[0] in (pytest.approx(B / 4), pytest.approx(B / 8))
    assert {e[1][0] % 12 for e in arp} <= {(TON + x) % 12 for x in (0, 3, 7, 10)}
    assert len({e[1][0] for e in arp}) >= 3                        # it cycles through the chord


def test_passing_chord_takes_over_the_arpeggio_on_beat_three():
    piece, B = _bar(passing=Chord(4, (5, 8, 12, 15)))
    late = {e[1][0] % 12 for e in piece.of("arp") if e[3] >= 2 * B}
    assert late and late <= {(TON + x) % 12 for x in (5, 8, 12, 15)}


def test_bass_is_triangle_eighths_jumping_octaves():
    piece, B = _bar(bass=(Note(0, 38, 4),))
    bass = sorted(piece.of("bass"), key=lambda e: e[3])
    assert [e[3] for e in bass] == pytest.approx([i * B / 2 for i in range(8)])
    notes = [e[1][0] for e in bass]
    assert {n % 12 for n in notes} == {38 % 12}
    assert all(abs(a - b) == 12 for a, b in zip(notes, notes[1:]))


def test_bass_follows_the_planned_line():
    piece, B = _bar()
    by_time = {round(e[3] / B * 2): e[1][0] for e in piece.of("bass")}
    assert by_time[0] % 12 == 38 % 12 and by_time[5] % 12 == 45 % 12 and by_time[7] == 42


def test_quiet_sections_keep_a_calmer_bass_and_no_drums():
    piece, B = _bar(energy=0.3, section="intro", layers=frozenset({"chords", "bass"}))
    assert not (piece.of("kick") or piece.of("snare") or piece.of("hat"))
    assert len(piece.of("bass")) <= 4
    assert piece.of("arp")


def test_drums_are_a_noise_backbeat():
    piece, B = _bar()
    assert 0 in [e[3] for e in piece.of("kick")]
    snares = [round(e[3] / B, 3) for e in piece.of("snare")]
    assert 1.0 in snares and 3.0 in snares
    assert len(piece.of("hat")) >= 8


def test_busy_drums_tick_sixteenths():
    piece, _ = _bar(Style(genre="chiptune", bpm=120, drums="busy", energy=0.9), energy=0.95)
    assert len(piece.of("hat")) >= 16


@pytest.mark.parametrize("lead,duty", [("piano", 0.5), ("rhodes", 0.25), ("nylon", 0.125)])
def test_lead_color_picks_the_pulse_duty(lead, duty):
    piece, _ = _bar(Style(genre="chiptune", bpm=120, lead=lead))
    calls = piece.of("lead")
    assert len(calls) >= 3                                          # the melody plays
    assert all(e[1][3] == duty for e in calls[:3])


def test_lead_and_arpeggio_stay_silent_when_their_layer_is_off():
    piece, _ = _bar(layers=frozenset({"chords", "bass"}))
    assert not piece.of("lead") and piece.of("arp")
