"""Ambient (elegant) genre: pads, celesta, no drums, a lot of space (S2)."""
import numpy as np
import pytest

from gentle_stream import genres
from gentle_stream.composer import Composer
from gentle_stream.engine import SR, Engine
from gentle_stream.genres.base import Piece
from gentle_stream.style import Style
from gentle_stream.synth import hp

GENRE = genres.get("ambient")
DRUM_CALLS = {"kick", "brush", "rim", "hat", "snare", "hit"}


class _Spy:
    """Wraps the genre's voices: records every voice call (bar, name, args) and returns a short silence."""

    def __init__(self, inner):
        self.inner, self.calls, self.bar = inner, [], 0

    def __getattr__(self, name):
        attr = getattr(self.inner, name)
        if not callable(attr):
            return attr

        def call(*args, **kwargs):
            self.calls.append((self.bar, name, args))
            return np.zeros(64)
        return call


def _schedule(bars=40, seed=7, **style):
    s = Style(genre="ambient", **({"drums": "busy", "energy": 0.9, "lead": "rhodes", "chords": "piano",
                                   "lead_density": 1.0, "bpm": 66} | style))
    rng = np.random.default_rng(seed)
    spy = _Spy(GENRE.instruments(rng, SR, None))
    piece = Piece(s, rng, spy, 50, SR, np.zeros((int(24 * SR), 2)))
    GENRE.start(piece)
    comp = Composer(s, seed, 50, GENRE.chord)
    plans = []
    for bar in range(bars):
        spy.bar = bar
        plans.append(comp.plan(bar))
        GENRE.schedule(piece, plans[-1])
    return spy.calls, plans, s


def _named(calls, name):
    return [c for c in calls if c[1] == name]


def test_never_plays_drums_even_when_the_style_asks_for_busy_drums():
    calls, plans, _ = _schedule()
    assert any("drums" in p.layers for p in plans)        # the composer offered drums...
    assert not [c for c in calls if c[1] in DRUM_CALLS]   # ...and the genre declined them


def test_a_sustained_pad_bed_spans_every_bar_whatever_the_chord_colour():
    for chords in ("piano", "rhodes", "nylon", "pad"):
        calls, plans, s = _schedule(bars=16, chords=chords)
        bar = 240 / s.bpm
        for i, p in enumerate(plans):
            pads = [c for c in _named(calls, "pad") if c[0] == i]
            assert len(pads) >= 3, f"{chords}: bar {i} has no pad chord"
            held = bar if p.passing is None else bar / 2
            assert all(c[2][1] >= 0.95 * held for c in pads)


def test_sub_bass_is_one_sustained_note_per_chord():
    calls, plans, s = _schedule()
    bar = 240 / s.bpm
    for i, p in enumerate(plans):
        subs = [c for c in _named(calls, "sub") if c[0] == i]
        assert len(subs) == (1 if p.passing is None else 2)
        assert subs[0][2][0] == p.bass[0].midi
        assert sum(c[2][1] for c in subs) >= 0.95 * bar


def test_celesta_lead_is_high_and_sparser_than_the_planned_melody():
    calls, plans, _ = _schedule(lead="rhodes", chords="pad")
    planned = sum(len(p.melody) for p in plans if "lead" in p.layers)
    lead = [c for c in _named(calls, "celesta") if "lead" in plans[c[0]].layers]
    assert planned and lead
    assert len(lead) < planned
    assert min(c[2][0] for c in lead) >= 72                # the celesta sings an octave above the plan


def test_counter_melody_is_a_slow_pad_line():
    calls, plans, s = _schedule(lead="rhodes")
    beat = 60 / s.bpm
    glass = _named(calls, "glass")
    assert glass and all(c[2][1] >= 1.9 * beat for c in glass)
    assert {c[0] for c in glass} <= {i for i, p in enumerate(plans) if "counter" in p.layers}


def test_quiet_sections_still_twinkle():
    calls, plans, _ = _schedule(lead="none")
    quiet = [i for i, p in enumerate(plans) if p.section in ("intro", "breakdown")]
    assert {c[0] for c in _named(calls, "celesta")} & set(quiet)


def test_master_is_clean_wide_and_gentle():
    lofi = genres.get("lofi")
    for s in (Style(genre="ambient"), Style(genre="ambient", vinyl=1, space=0, brightness=1)):
        m, ref = GENRE.master(s), lofi.master(s)
        assert m.vinyl == 0 and m.wow <= 0.15
        assert m.space >= 0.6 and m.reverb > ref.reverb
        assert m.drive < ref.drive
        assert 4000 <= m.tone <= 14000


def test_rendered_audio_is_soft_and_has_no_drum_transients():
    """No drums: above 3 kHz the level never jumps from one 10 ms window to the next the way hats, snares and
    brushes do (the lo-fi busy beat's 99.5th-percentile rise is about 1.4 in natural-log units)."""
    e = Engine(Style(genre="ambient", drums="busy", energy=0.9, bpm=66), seed=5, samples=False)
    x = hp(np.concatenate([e.render_bar() for _ in range(12)])[:, 0], 3000)
    w = int(0.01 * SR)
    rms = np.sqrt(np.mean(x[:len(x) // w * w].reshape(-1, w) ** 2, axis=1))
    rms = rms[len(rms) // 4:]                              # past the fade-in and intro
    assert np.percentile(np.diff(np.log(rms + 1e-9)), 99.5) < 0.7
