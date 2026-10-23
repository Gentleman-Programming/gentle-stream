"""Jazz genre traits (S2): extended chords, rootless swung comping, walking bass, brushes and a spang-a-lang ride
with a strong triplet swing, and a warm, clean master. Measured from what `schedule` plays through a spy voice set.
"""
from dataclasses import dataclass

import numpy as np
import pytest

from gentle_stream import genres, samples
from gentle_stream.composer import Composer
from gentle_stream.engine import SR
from gentle_stream.genres.base import Piece
from gentle_stream.style import KEYS, MODES, Style

JAZZ = genres.get("jazz")
REAL = samples.soundfont_path()
HAS_SAMPLES = REAL is not None and samples.load_renderer(REAL) is not None
TOL = 0.03          # beats: allowed humanization around a grid position
SWUNG = (0.6, 0.7)  # where a swung off-beat lands inside its beat (2/3 is the triplet)


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


def _run(bars=40, seed=7, **style):
    s = Style(genre="jazz", **({"key": "F", "mode": "dorian", "bpm": 120, "drums": "lofi", "energy": 0.6,
                                "chords": "piano", "lead": "rhodes", "lead_density": 0.9} | style))
    ton = 48 + KEYS.index(s.key)
    ton = ton - 12 if ton > 54 else ton
    composer, spy = Composer(s, seed, ton, JAZZ.chord), Spy()
    piece = Piece(s, np.random.default_rng(seed), spy, ton, SR, np.zeros((16, 2)))
    events, now, beat = [], {}, 60 / s.bpm

    def add(sig, t, pan=0.0, g=1.0):
        name, args = spy.made.get(id(sig), ("unknown", ()))
        events.append(Event(now["bar"], name, t / beat, g, args))
    piece.add = add
    JAZZ.start(piece)
    plans = []
    for bar in range(bars):
        now["bar"] = bar
        plans.append(composer.plan(bar))
        JAZZ.schedule(piece, plans[-1])
    return plans, events, ton


def _of(events, voice, bar):
    return sorted((e for e in events if e.voice == voice and e.bar == bar), key=lambda e: e.beat)


def _near(x, grid):
    return any(abs(x - g) <= TOL for g in grid)


def _swung(x):
    return SWUNG[0] <= x % 1 <= SWUNG[1]


def _pcs(ton, chord, key):
    return [(ton + t + key) % 12 for t in chord.tones]


def _pc_dist(a, b):
    d = (a - b) % 12
    return min(d, 12 - d)


# ---------------------------------------------------------------- harmony
@pytest.mark.parametrize("mode", JAZZ.modes)
def test_chord_hook_extends_every_diatonic_chord(mode):
    plain = Composer(Style(mode=mode), 3, 50)
    hooked = Composer(Style(mode=mode), 3, 50, JAZZ.chord)
    for degree in range(1, 8):
        for raised in (False, True):
            a, b = plain._chord(degree, raised), hooked._chord(degree, raised)
            assert b.tones[:4] == a.tones[:4]                      # root, third, fifth, seventh untouched
            iv = [t - b.tones[0] for t in b.tones]
            assert len(iv) >= 5 and iv[3] in (10, 11)              # sevenths always, plus extensions
            third, seventh = iv[1], iv[3]
            if third == 3 and iv[2] == 7:                          # minor seventh: ninth and eleventh
                assert 14 in iv and 17 in iv
            elif third == 4 and seventh == 10:                     # dominant: a ninth and a thirteenth
                assert {13, 14} & set(iv) and {20, 21} & set(iv)
            elif third == 4 and seventh == 11:                     # major seventh: the ninth
                assert 14 in iv


def test_chord_hook_is_deterministic():
    from gentle_stream.composer import Chord
    c = Chord(2, (2, 5, 9, 12))
    assert JAZZ.chord(c, MODES["major"]) == JAZZ.chord(c, MODES["major"])


# ---------------------------------------------------------------- bass
def test_walking_bass_plays_one_quarter_note_per_beat_with_approach_tones():
    plans, events, ton = _run(bars=48)
    walking = [p.bar for p in plans if "drums" in p.layers]
    assert len(walking) > 30
    for i in walking:
        p, notes = plans[i], _of(events, "bass", i)
        assert len(notes) == 4 and all(abs(n.beat - k) <= TOL for k, n in enumerate(notes)), (i, notes)
        midi = [n.args[0] for n in notes]
        assert all(28 <= m <= 55 for m in midi)
        assert midi[0] % 12 == _pcs(ton, p.chord, p.key_offset)[0]                    # root on beat 1
        third_beat = p.passing if p.passing is not None else p.chord
        assert midi[2] % 12 in _pcs(ton, third_beat, p.key_offset)[:4]               # chord tone on beat 3
        if i + 1 in walking:                                                          # beat 4 leads in
            nxt = _of(events, "bass", i + 1)[0].args[0]
            assert 0 < _pc_dist(midi[3], nxt) <= 2, (i, midi, nxt)


def test_bass_plays_in_two_without_drums():
    plans, events, _ = _run(bars=8)
    intro = [p.bar for p in plans if p.section == "intro"]
    assert intro
    for i in intro:
        assert [round(n.beat) for n in _of(events, "bass", i)] == [0, 2]


# ---------------------------------------------------------------- drums
def test_ride_plays_spang_a_lang_with_triplet_swing():
    plans, events, _ = _run(bars=40)
    drum_bars = [p.bar for p in plans if "drums" in p.layers]
    assert drum_bars
    for i in drum_bars:
        beats = [e.beat for e in _of(events, "ride", i)]
        assert all(any(abs(x - b) <= TOL for x in beats) for b in range(4)), (i, beats)
        assert all(any(_swung(x) and int(x) == b for x in beats) for b in (1, 3)), (i, beats)
        assert all(abs(x - round(x)) <= TOL or _swung(x) for x in beats), (i, beats)   # never straight eighths


def test_brushes_feathered_kick_and_pedal_hat_on_two_and_four():
    plans, events, _ = _run(bars=40)
    for i in (p.bar for p in plans if "drums" in p.layers):
        assert _of(events, "swirl", i)
        assert [round(e.beat) for e in _of(events, "pedal", i)] == [1, 3]
        kicks = _of(events, "kick", i)
        assert [round(e.beat, 2) for e in kicks] == [0, 1, 2, 3]
        assert max(e.gain for e in kicks) < 0.42 / 2                  # well under the lo-fi kick: feathered
        taps = [e.beat for e in _of(events, "tap", i)]
        assert _near(1, taps) and _near(3, taps)


def test_soft_drums_are_brushes_only():
    plans, events, _ = _run(bars=24, drums="soft")
    drum_bars = [p.bar for p in plans if "drums" in p.layers]
    assert drum_bars and not [e for e in events if e.voice == "ride"]
    assert all(_of(events, "swirl", i) for i in drum_bars)


def test_no_drums_without_the_drum_layer():
    plans, events, _ = _run(bars=8, drums="none")
    assert not [e for e in events if e.voice in ("ride", "swirl", "tap", "kick", "pedal")]


# ---------------------------------------------------------------- comping and lead
def test_comping_is_rootless_and_syncopated():
    plans, events, ton = _run(bars=40)
    onsets = []
    for p in plans:
        hits = {}
        for e in _of(events, "keys", p.bar):
            if e.args[0] == "piano":
                hits.setdefault(round(e.beat, 2), []).append(e.args[1])
        assert hits, p.bar
        for beat, midi in hits.items():
            chord = p.passing if p.passing is not None and beat >= 2 - TOL else p.chord
            pcs = _pcs(ton, chord, p.key_offset)
            got = {m % 12 for m in midi}
            assert pcs[0] not in got and {pcs[1], pcs[3]} <= got, (p.bar, beat, midi, pcs)
            assert 3 <= len(midi) <= 5
        if "drums" in p.layers:
            onsets += list(hits)
    assert sum(_swung(b) for b in onsets) >= 0.25 * len(onsets)       # Charleston-style pushes on the "ands"


def test_lead_swings_the_composers_melody():
    plans, events, _ = _run(bars=48)
    played = 0
    for p in plans:
        if "lead" not in p.layers:
            continue
        lead = _of(events, "lead", p.bar)
        assert len(lead) == len(p.melody)
        for n, e in zip(sorted(p.melody, key=lambda n: n.beat), lead):
            assert e.args[1] == n.midi
            if n.beat % 1 == 0.5:
                assert _swung(e.beat) and int(e.beat) == int(n.beat), (n, e)
            else:
                assert abs(e.beat - n.beat) <= TOL
            played += 1
    assert played > 20


def test_counter_melody_uses_another_colour_than_the_comping():
    plans, events, _ = _run(bars=48, energy=1.0)
    counter_bars = [p.bar for p in plans if "counter" in p.layers]
    assert counter_bars
    colours = {e.args[0] for i in counter_bars for e in _of(events, "keys", i)}
    assert colours - {"piano"}


# ---------------------------------------------------------------- master and voices
def test_master_is_warm_and_clean():
    lofi = genres.get("lofi")
    for s in (Style(genre="jazz"), Style(genre="jazz", vinyl=1, space=1, brightness=1)):
        m, ref = JAZZ.master(s), lofi.master(s)
        assert m.wow <= 0.2 and m.vinyl <= 0.3 * s.vinyl + 1e-9
        assert m.drive < ref.drive and m.room > 0


def _check_voices(v):
    for colour in ("piano", "rhodes", "nylon", "pad"):
        for x in (v.keys(colour, 62, 0.5, 0.7), v.lead(colour, 67, 0.4, 0.8)):
            assert np.all(np.isfinite(x)) and 0.01 < np.max(np.abs(x)) < 2
    for x in (v.bass(36, 0.45, 0.8), v.ride(0.8), v.swirl(1.0), v.tap(0.7), v.kick(0.5), v.pedal(0.6)):
        assert np.all(np.isfinite(x)) and 0.001 < np.max(np.abs(x)) < 2


def test_procedural_voices():
    _check_voices(JAZZ.instruments(np.random.default_rng(0), SR, None))


@pytest.mark.skipif(not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)")
def test_sampled_voices():
    shared = samples.SampleInstruments(samples.load_renderer(REAL), np.random.default_rng(0))
    _check_voices(JAZZ.instruments(np.random.default_rng(0), SR, shared))
