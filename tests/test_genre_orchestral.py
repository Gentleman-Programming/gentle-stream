"""Orchestral (cinematic / epic): the traits that define the genre, measured on what `schedule` plays (through a
recording voice set) and on the master chain. The rendering contract (clean, fast, deterministic, both backends)
lives in tests/test_genres_contract.py."""
import numpy as np
import pytest

from gentle_stream import genres, samples
from gentle_stream.composer import FIRST_FORM, Composer
from gentle_stream.engine import SR, Engine
from gentle_stream.genres.base import Piece
from gentle_stream.style import Style

ORCH = genres.get("orchestral")
REAL = samples.soundfont_path()
HAS_SAMPLES = REAL is not None and samples.load_renderer(REAL) is not None
BPM = 120.0
B = 60 / BPM
TON = 45
PERCUSSION = {"timpani", "boom", "taiko", "cymbal", "snare", "swell"}
SECTION_BARS = dict(FIRST_FORM)


class Spy:
    """The orchestral voice set, recording every call; each returned signal is a fresh tagged array."""

    def __init__(self):
        self.made: list[tuple[np.ndarray, str, dict]] = []

    def _sig(self, kind, **info):
        sig = np.zeros(8)
        self.made.append((sig, kind, info))
        return sig

    def note(self, part, m, dur, vel=1.0):
        return self._sig(part, midi=m, dur=dur, vel=vel)

    def hit(self, name, vel=1.0):
        return self._sig(name, vel=vel)

    def swell(self, dur):
        return self._sig("swell", dur=dur)


def _events(style, bars, seed=5):
    """{bar: (plan, [(kind, t, pan, info)])} for the composer's plans of `bars`, scheduled through the spy."""
    spy, acc = Spy(), np.zeros((int(30 * SR), 2))
    piece = Piece(style, np.random.default_rng(seed), spy, TON, SR, acc)
    ORCH.start(piece)
    comp = Composer(style, seed, TON, ORCH.chord)
    out = {}
    for bar in range(max(bars) + 1):
        plan = comp.plan(bar)
        log = []
        piece.add = lambda sig, t, pan=0.0, g=1.0: log.append((sig, t, pan))
        ORCH.schedule(piece, plan)
        if bar in bars:
            tags = {id(s): (k, i) for s, k, i in spy.made}
            out[bar] = (plan, [(tags[id(s)][0], t, pan, tags[id(s)][1]) for s, t, pan in log])
    return out


def _style(**kw):
    return Style(**({"genre": "orchestral", "bpm": BPM, "mode": "minor", "drums": "lofi", "energy": 0.6} | kw))


def _bars(style, sections, n=44, seed=5):
    comp = Composer(style, seed, TON, ORCH.chord)
    return [b for b in range(n) if comp.plan(b).section in sections]


def _on_grid(t, step):
    return abs(t / step - round(t / step)) < 0.02


def _pcs(plan, beat=0.0):
    chord = plan.passing if plan.passing is not None and beat >= 2 else plan.chord
    return {(TON + x + plan.key_offset) % 12 for x in chord.tones}


# ---------------------------------------------------------------- identity, harmony and master chain
def test_orchestral_prompt_hints():
    assert "orchestral" in ORCH.description and "epic" in ORCH.description
    assert ORCH.modes[0] == "minor" and 60 <= ORCH.bpm[0] and ORCH.bpm[1] <= 140
    assert "horn" in ORCH.voices["piano"] and "choir" in ORCH.voices["pad"] and "harp" in ORCH.voices["nylon"]


def test_chords_are_plain_triads():
    comp = Composer(_style(), 3, TON, ORCH.chord)
    for bar in range(40):
        c = comp.plan(bar).chord
        assert len(c.tones) == (4 if c.degree == 0 else 3)   # secondary dominants keep their seventh


def test_master_is_clean_and_a_big_hall():
    lofi, rock = genres.get("lofi"), genres.get("rock")
    for s in (_style(vinyl=1.0, space=0.5, brightness=0.5), _style(vinyl=0.3, space=1.0, brightness=1.0)):
        m = ORCH.master(s)
        assert m.vinyl == 0 and m.wow == 0
        assert m.reverb >= lofi.master(s).reverb and m.reverb > rock.master(s).reverb
        assert m.space >= s.space and m.room > rock.master(s).room


# ---------------------------------------------------------------- strings
def test_string_ostinato_plays_chord_tones_in_eighths_then_sixteenths_in_b_sections():
    s = _style()
    for bar, (plan, ev) in _events(s, _bars(s, ("A", "B", "B'"))).items():
        ost = [(t, info["midi"]) for k, t, _, info in ev if k == "spiccato"]
        step = B / 4 if plan.section.startswith("B") else B / 2
        assert len(ost) >= (12 if step < B / 2 else 8)
        assert all(_on_grid(t, step) for t, _ in ost)
        if step < B / 2:
            assert any(not _on_grid(t, B / 2) for t, _ in ost)
        assert all(m % 12 in _pcs(plan, t / B) for t, m in ost)
        assert all(info["dur"] <= B / 2 for k, _, _, info in ev if k == "spiccato")   # short, detached bows


def test_sustained_strings_hold_the_chord_every_bar():
    s = _style()
    for bar, (plan, ev) in _events(s, list(range(40))).items():
        held = [(t, info) for k, t, _, info in ev if k == "strings"]
        assert held and max(info["dur"] for _, info in held) >= 2 * B * 0.9
        assert all(info["midi"] % 12 in _pcs(plan, t / B) for t, info in held)


def test_breakdown_is_thin_and_b_prime_is_full_tutti():
    s = _style(lead="piano", lead_density=1.0)
    ev = _events(s, list(range(44)))
    thin = [e for plan, e in ev.values() if plan.section == "breakdown"]
    tutti = [e for plan, e in ev.values() if plan.section == "B'"]
    assert thin and tutti
    for e in thin:
        kinds = {k for k, *_ in e}
        assert not kinds & {"spiccato", "trombone", "tuba", "brass", "horn", "trumpet"} and not kinds & PERCUSSION - {
            "timpani", "swell"}
        assert len([k for k, *_ in e if k == "strings"]) <= 3
    for e in tutti:
        kinds = {k for k, *_ in e}
        assert {"spiccato", "strings", "trombone", "tuba", "timpani"} <= kinds
    tonal = lambda e: len([x for x in e if x[0] not in PERCUSSION])
    assert min(tonal(e) for e in tutti) > 3 * max(tonal(e) for e in thin)


# ---------------------------------------------------------------- brass, bass, melody
def test_low_brass_holds_the_roots_in_energetic_sections():
    s = _style()
    for bar, (plan, ev) in _events(s, _bars(s, ("B", "B'"))).items():
        low = [(t, info) for k, t, _, info in ev if k in ("trombone", "tuba")]
        assert low and max(info["dur"] for _, info in low) >= 2 * B * 0.9
        roots = {(TON + plan.chord.tones[0] + plan.key_offset) % 12}
        if plan.passing is not None:
            roots.add((TON + plan.passing.tones[0] + plan.key_offset) % 12)
        tuba = {info["midi"] % 12 for k, _, _, info in ev if k == "tuba"}
        assert tuba <= roots and all(info["midi"] < 60 for _, info in low)


def test_basses_play_the_composers_bass_line():
    s = _style()
    for bar, (plan, ev) in _events(s, list(range(30))).items():
        basses = sorted((round(t / B, 3), info["midi"]) for k, t, _, info in ev if k == "basses")
        assert basses == [(n.beat, n.midi) for n in plan.bass]


@pytest.mark.parametrize("lead,part", [("piano", "horn"), ("rhodes", "trumpet"), ("nylon", "violins"),
                                       ("pad", "choir")])
def test_the_melody_plays_on_the_lead_colour(lead, part):
    s = _style(lead=lead, lead_density=1.0)
    played = 0
    for bar, (plan, ev) in _events(s, list(range(36))).items():
        mel = sorted((t, info["midi"]) for k, t, _, info in ev if k == part and t < 4 * B)
        if "lead" in plan.layers and plan.section in ("A", "B"):
            assert [m for _, m in mel][:len(plan.melody)] == [n.midi for n in plan.melody]
            played += 1
    assert played


def test_no_melody_without_a_lead():
    ev = _events(_style(lead="none"), list(range(36)))
    assert not any(k in ("horn", "trumpet") for _, e in ev.values() for k, *_ in e)


# ---------------------------------------------------------------- percussion
def test_timpani_on_downbeats_on_the_root():
    s = _style()
    for bar, (plan, ev) in _events(s, _bars(s, ("A", "B"))).items():
        timp = [(t, info["midi"]) for k, t, _, info in ev if k == "timpani"]
        down = [m for t, m in timp if t == pytest.approx(0, abs=1e-6)]
        assert down and down[0] % 12 == (TON + plan.chord.tones[0] + plan.key_offset) % 12
        assert all(36 <= m <= 55 for _, m in timp)


def test_timpani_roll_and_cymbal_swell_lead_into_every_section():
    s = _style()
    ev = _events(s, list(range(44)))
    lasts = [b for b, (plan, _) in ev.items() if plan.section_bar == SECTION_BARS[plan.section] - 1]
    assert len(lasts) >= 6
    for bar in lasts:
        plan, e = ev[bar]
        roll = [t for k, t, *_ in e if k == "timpani" and t >= 2 * B - 1e-6]
        assert len(roll) >= 4 and all(_on_grid(t, B / 4) for t in roll)
        sw = [(t, info["dur"]) for k, t, _, info in e if k == "swell"]
        assert sw and all(t + d == pytest.approx(4 * B, abs=0.01) for t, d in sw)


def test_crash_and_big_drums_in_b_sections():
    s = _style()
    sel = _events(s, _bars(s, ("B", "B'")))
    for bar, (plan, ev) in sel.items():
        kinds = [k for k, *_ in ev]
        assert ("cymbal" in kinds) == (plan.section_bar == 0)
        assert "boom" in kinds or "taiko" in kinds


def test_no_percussion_without_drums():
    ev = _events(_style(drums="none"), list(range(44)))
    assert not PERCUSSION & {k for _, e in ev.values() for k, *_ in e}


# ---------------------------------------------------------------- the real voices
PARTS = ("strings", "spiccato", "violins", "violas", "celli", "basses", "horn", "trumpet", "trombone", "tuba",
         "brass", "harp", "choir", "timpani")


def _check_voices(inst):
    for part in PARTS:
        m = 45 if part in ("timpani", "tuba", "basses", "trombone", "celli") else 64
        x = inst.note(part, m, 0.15 if part == "spiccato" else 1.0, 0.8)
        assert np.all(np.isfinite(x)) and 0.005 < np.max(np.abs(x)) < 2.0, part
        assert abs(x[-1]) < 2e-3 * max(1.0, np.max(np.abs(x))), part   # ends quietly: no click
    for name in ("boom", "taiko", "cymbal", "snare"):
        x = inst.hit(name)
        assert np.all(np.isfinite(x)) and 0.005 < np.max(np.abs(x)) < 2.0, name
    sw = inst.swell(2.0)
    rms = lambda a, b: float(np.sqrt(np.mean(sw[int(a * SR):int(b * SR)] ** 2)))
    assert len(sw) == pytest.approx(2.0 * SR, abs=2) and rms(1.7, 1.95) > 4 * rms(0.0, 0.5)   # it grows
    loud, soft = inst.note("horn", 60, 1.0, 1.0), inst.note("horn", 60, 1.0, 0.3)
    assert np.max(np.abs(loud)) > np.max(np.abs(soft))


def test_procedural_voices():
    _check_voices(ORCH.instruments(np.random.default_rng(1), SR, None))


@pytest.mark.skipif(not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)")
def test_sampled_voices():
    shared = samples.SampleInstruments(samples.load_renderer(REAL), np.random.default_rng(0))
    _check_voices(ORCH.instruments(np.random.default_rng(1), SR, shared))


def test_energy_follows_the_form():
    e = Engine(_style(bpm=130, lead="piano"), seed=9, samples=False)
    level: dict[str, list[float]] = {}
    for _ in range(44):
        x = e.render_bar()
        section = e.section   # the section of the bar just rendered
        level.setdefault(section, []).append(float(np.sqrt(np.mean(x ** 2))))
    mean = {k: np.mean(v) for k, v in level.items()}
    assert mean["B'"] > 1.6 * mean["breakdown"] and mean["B"] > mean["intro"]
