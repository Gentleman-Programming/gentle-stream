import numpy as np
import pytest

from gentle_stream.composer import (COUNTER_ENERGY, FIRST_FORM, FORM, MAIN, Composer, augment, invert,
                                    sequence)
from gentle_stream.style import MODES, Style

TON = 50
CYCLE1 = sum(n for _, n in FIRST_FORM)
TWO_CYCLES = CYCLE1 + sum(n for _, n in FORM)


def plans(style=Style(), seed=1, bars=TWO_CYCLES, ton=TON):
    c = Composer(style, seed, ton)
    return [c.plan(b) for b in range(bars)]


def sections(ps):
    """[(name, [plans])] in order of appearance."""
    out = []
    for p in ps:
        if not out or out[-1][0] != p.section or p.section_bar == 0:
            out.append((p.section, []))
        out[-1][1].append(p)
    return out


def tones(p, beat):
    chord = p.passing if p.passing is not None and beat >= 2 else p.chord
    return {(TON + t + p.key_offset) % 12 for t in chord.tones[:4]}


def sounding(ps, i, beat):
    """The lead pitch sounding at `beat` of bar i (falling back to earlier bars of the plan)."""
    for j in range(i, -1, -1):
        notes = [n for n in ps[j].melody if j < i or n.beat <= beat]
        if notes:
            return notes[-1].midi
    return None


def test_form_has_distinct_sections_with_rising_and_falling_energy():
    secs = sections(plans())
    names = [n for n, _ in secs]
    assert names[:7] == ["intro", "A", "B", "A'", "breakdown", "B'", "release"]
    assert names[7:] == ["A", "B", "A'", "breakdown", "B'", "release"]   # the form loops without the intro
    e = {n: ps[0].energy for n, ps in secs[:7]}
    assert e["intro"] < e["A"] < e["B"] and e["breakdown"] < e["A'"] < e["B'"] and e["release"] < e["B'"]
    assert all(len({p.energy for p in ps}) == 1 for _, ps in secs)


def test_style_energy_scales_the_whole_curve():
    calm, hot = plans(Style(energy=0.1), bars=CYCLE1), plans(Style(energy=0.9), bars=CYCLE1)
    assert all(0 <= c.energy <= h.energy <= 1 for c, h in zip(calm, hot))
    assert sum(c.energy for c in calm) < sum(h.energy for h in hot)


def test_drums_follow_section_energy():
    for _, ps in sections(plans(Style(drums="lofi"))):
        for p in ps:
            assert ("drums" in p.layers) == (p.section not in ("intro", "breakdown"))
    assert all("drums" not in p.layers for p in plans(Style(drums="none")))


@pytest.mark.parametrize("mode", list(MODES))
def test_section_b_harmony_differs_from_a(mode):
    for seed in range(6):
        secs = dict((n, ps) for n, ps in sections(plans(Style(mode=mode), seed, CYCLE1)))
        assert [p.chord.degree for p in secs["A"]] != [p.chord.degree for p in secs["B"]]


@pytest.mark.parametrize("mode", list(MODES))
def test_main_sections_end_with_a_cadence_on_the_tonic(mode):
    passing = 0
    for seed in range(4):
        for name, ps in sections(plans(Style(mode=mode), seed)):
            if name in MAIN:
                assert ps[-1].chord.degree == 1 and ps[-2].chord.degree != 1
            passing += sum(p.passing is not None for p in ps[-3:-1])
    assert passing > 0   # occasional passing / secondary chords before the cadence


@pytest.mark.parametrize("mode", list(MODES))
def test_strong_beat_melody_notes_are_chord_tones(mode):
    for seed in range(3):
        for p in plans(Style(mode=mode), seed):
            for n in p.melody:
                if n.beat in (0, 2):
                    assert n.midi % 12 in tones(p, n.beat), (p.bar, p.section, n)


def test_phrases_ask_and_answer():
    for seed in range(6):
        for name, ps in sections(plans(seed=seed)):
            if name not in MAIN:
                continue
            tonic = (TON + ps[0].key_offset) % 12
            assert ps[-1].melody[-1].midi % 12 == tonic          # the answer resolves
            if len(ps) == 8:
                assert ps[3].melody[-1].midi % 12 != tonic       # the question stays open


def test_motif_transformations():
    m = ((0, 0, 1.5), (1.5, -1, .5), (2, 1, 2))
    assert invert(m) == ((0, 0, 1.5), (1.5, 1, .5), (2, -1, 2))
    assert sequence(m, 2) == ((0, 2, 1.5), (1.5, 1, .5), (2, 3, 2))
    assert augment(m) == ((0, 0, 3), (3, -1, 1), (4, 1, 4))


def test_motif_is_developed_across_sections():
    secs = dict((n, ps) for n, ps in sections(plans(seed=3, bars=CYCLE1)))

    def rhythm(ps):
        return [(n.beat, n.length) for p in ps for n in p.melody]

    assert rhythm(secs["A"][:2]) == rhythm(secs["A'"][:2])                      # repetition
    assert rhythm(secs["A"][:2]) == rhythm(secs["B"][:2])                       # inversion keeps the rhythm
    assert [n.midi for p in secs["A"][:2] for n in p.melody] != [n.midi for p in secs["B"][:2] for n in p.melody]
    head = [(2 * n.beat, 2 * n.length) for n in secs["A"][0].melody]
    answer = [(n.beat + 4 * k, n.length) for k, p in enumerate(secs["A'"][4:6]) for n in p.melody]
    assert answer == head                                                      # augmentation


def test_lead_density_controls_how_often_the_lead_plays():
    def lead_bars(d):
        return sum("lead" in p.layers for p in plans(Style(lead_density=d), bars=CYCLE1))

    assert 0 < lead_bars(0.1) < lead_bars(0.5) < lead_bars(0.9)
    assert all("lead" not in p.layers for p in plans(Style(lead="none"), bars=CYCLE1))


def test_counter_melody_plays_only_in_high_energy_sections_and_moves_against_the_lead():
    contrary = total = 0
    for seed in range(8):
        ps = plans(seed=seed)
        for i, p in enumerate(ps):
            assert ("counter" in p.layers) == (p.energy >= COUNTER_ENERGY)
            assert bool(p.counter) == ("counter" in p.layers)
            assert all(n.length >= 2 for n in p.counter)               # slower than the lead
        prev = None
        for i, p in enumerate(ps):
            if not p.counter:
                prev = None
                continue
            for n in p.counter:
                lead = sounding(ps, i, n.beat)
                if prev is not None and lead is not None and prev[1] is not None:
                    dc, dl = n.midi - prev[0], lead - prev[1]
                    if dc and dl:
                        total += 1
                        contrary += np.sign(dc) != np.sign(dl)
                prev = (n.midi, lead)
    assert total > 20 and contrary / total >= 0.6
    assert all(not p.counter for p in plans(Style(lead="none"), bars=CYCLE1))


def test_bass_plays_the_root_and_approaches_the_next_chord():
    ps = plans(seed=5)
    approaches = 0
    for p, nxt in zip(ps, ps[1:]):
        root = (TON + p.chord.tones[0] + p.key_offset) % 12
        assert p.bass[0].beat == 0 and p.bass[0].midi % 12 == root
        if nxt.bass[0].midi % 12 != root:
            last = p.bass[-1]
            assert last.beat >= 3 and 1 <= abs(last.midi - nxt.bass[0].midi) <= 2
            approaches += 1
    assert approaches > 20


def test_key_lift_happens_on_a_returning_section_for_some_seeds():
    lifted = 0
    for seed in range(10):
        ps = plans(seed=seed)
        for i, p in enumerate(ps):
            if p.key_offset:
                assert p.section in ("A'", "B'")
                if p.section_bar == 0:   # a pivot dominant of the new key prepares the lift
                    prev = ps[i - 1]
                    assert prev.passing is not None
                    assert (prev.passing.tones[0] - 7 - p.key_offset) % 12 == 0
        lifted += any(p.key_offset for p in ps)
    assert 0 < lifted < 10


def test_plans_are_deterministic_per_seed():
    assert plans(seed=11) == plans(seed=11)
    assert plans(seed=11) != plans(seed=12)
    c = Composer(Style(), 11, TON)       # random access gives the same plan as sequential access
    assert c.plan(70) == plans(seed=11)[70]
