"""Rock / metal: the traits that define the genre, measured on what `schedule` plays (through a recording voice
set) and on the master chain. The rendering contract (clean, fast, deterministic, both backends) lives in
tests/test_genres_contract.py."""
from collections import Counter

import numpy as np
import pytest

from gentle_stream import genres, samples
from gentle_stream.composer import Composer
from gentle_stream.engine import SR, Engine
from gentle_stream.genres.base import Piece
from gentle_stream.style import Style

ROCK = genres.get("rock")
REAL = samples.soundfont_path()
HAS_SAMPLES = REAL is not None and samples.load_renderer(REAL) is not None
BPM = 120.0
B = 60 / BPM


class Spy:
    """The rock voice set, recording every call; each returned signal is a fresh tagged array."""

    def __init__(self):
        self.made: list[tuple[np.ndarray, str, dict]] = []

    def _sig(self, kind, **info):
        sig = np.zeros(8)
        self.made.append((sig, kind, info))
        return sig

    def guitar(self, notes, dur, vel=1.0, tone="piano", mute=False, take=0):
        return self._sig("guitar", notes=tuple(notes), dur=dur, tone=tone, mute=mute, take=take)

    def lead(self, m, dur, vel=1.0, tone="piano", vibrato=0.0):
        return self._sig("lead", midi=m, dur=dur, tone=tone, vibrato=vibrato)

    def strings(self, m, dur, vel=1.0):
        return self._sig("strings", midi=m, dur=dur)

    def bass(self, m, dur, vel=1.0):
        return self._sig("bass", midi=m, dur=dur)

    def __getattr__(self, drum):          # kick, snare, hat, open_hat, ride, crash, china, tom
        if drum.startswith("_"):
            raise AttributeError(drum)
        return lambda *a, **k: self._sig(drum)


def _events(style, bars, seed=5):
    """{bar index: [(kind, t, pan, info)]} for the composer's plans of `bars`, scheduled through the spy."""
    spy, acc = Spy(), np.zeros((int(30 * SR), 2))
    piece = Piece(style, np.random.default_rng(seed), spy, 45, SR, acc)
    ROCK.start(piece)
    comp = Composer(style, seed, 45, ROCK.chord)
    out = {}
    for bar in range(max(bars) + 1):
        plan = comp.plan(bar)
        log = []
        piece.add = lambda sig, t, pan=0.0, g=1.0: log.append((sig, t, pan))
        ROCK.schedule(piece, plan)
        if bar in bars:
            tags = {id(s): (k, i) for s, k, i in spy.made}
            out[bar] = (plan, [(tags[id(s)][0], t, pan, tags[id(s)][1]) for s, t, pan in log])
    return out


def _style(**kw):
    return Style(genre="rock", bpm=BPM, mode="minor", **({"drums": "lofi", "energy": 0.6} | kw))


def _bars(style, sections, n=40, seed=5):
    """Bars of the given sections (first cycle of the form)."""
    comp = Composer(style, seed, 45, ROCK.chord)
    return [b for b in range(n) if comp.plan(b).section in sections]


def _on_grid(t, step):
    return abs(t / step - round(t / step)) < 0.02


# ---------------------------------------------------------------- identity and master chain
def test_rock_prompt_hints():
    assert ROCK.bpm[1] <= 140 and ROCK.bpm[0] >= 85
    assert {"minor", "phrygian"} <= set(ROCK.modes) and "major" not in ROCK.modes[:1]
    assert "guitar" in ROCK.voices["piano"] and "metal" in ROCK.description


def test_flavor_is_metal_only_for_busy_energetic_styles():
    assert ROCK.flavor(_style(drums="busy", energy=0.7)) == "metal"
    assert ROCK.flavor(_style(drums="busy", energy=0.69)) == "rock"
    assert ROCK.flavor(_style(drums="lofi", energy=1.0)) == "rock"


def test_master_is_clean_tight_bright_and_driven():
    lofi = genres.get("lofi").master(Style(space=0.5, vinyl=0.8))
    for s in (_style(vinyl=1.0, space=0.5), _style(drums="busy", energy=0.9, vinyl=1.0, space=1.0)):
        m = ROCK.master(s)
        assert m.vinyl == 0 and m.wow == 0
        assert m.room < lofi.room and m.reverb <= lofi.reverb
        assert m.tone > lofi.tone and m.drive > lofi.drive and m.ratio > lofi.ratio
    assert ROCK.master(_style(drums="busy", energy=0.9)).drive > ROCK.master(_style()).drive


# ---------------------------------------------------------------- rhythm guitar
def test_rhythm_guitar_plays_double_tracked_power_chords():
    s = _style()
    for bar, (plan, ev) in _events(s, _bars(s, ("A", "B"))).items():
        gtr = [e for e in ev if e[0] == "guitar"]
        assert gtr
        for _, t, pan, info in gtr:
            r = info["notes"][0]
            assert info["notes"] == (r, r + 7, r + 12) and 38 <= r <= 52       # root, fifth, octave, low strings
        roots = {info["notes"][0] % 12 for *_, info in gtr}
        want = {(45 + plan.chord.tones[0] + plan.key_offset) % 12}
        if plan.passing is not None:
            want.add((45 + plan.passing.tones[0] + plan.key_offset) % 12)
        assert roots <= want
        takes = Counter((info["take"], np.sign(pan)) for _, _, pan, info in gtr)
        assert set(takes) == {(0, -1), (1, 1)} and takes[(0, -1)] == takes[(1, 1)]   # two takes, hard L/R


def test_rock_rhythm_is_palm_muted_eighths_with_open_chords_on_section_downbeats():
    s = _style()
    for bar, (plan, ev) in _events(s, _bars(s, ("A", "B", "A'"))).items():
        take0 = [(t, info) for k, t, _, info in ev if k == "guitar" and info["take"] == 0]
        muted = [t for t, info in take0 if info["mute"]]
        assert len(muted) >= 4 and all(_on_grid(t, B / 2) for t in muted)
        first = min(take0, key=lambda x: x[0])
        if plan.section_bar == 0:
            assert first[0] == pytest.approx(0, abs=0.02) and not first[1]["mute"]   # ringing open chord


def test_metal_rhythm_chugs_in_sixteenths():
    s = _style(drums="busy", energy=0.9)
    for bar, (plan, ev) in _events(s, _bars(s, ("B",))).items():
        muted = [t for k, t, _, info in ev if k == "guitar" and info["take"] == 0 and info["mute"]]
        assert len(muted) >= 9
        assert any(not _on_grid(t, B / 2) for t in muted) and all(_on_grid(t, B / 4) for t in muted)


def test_quiet_sections_let_open_chords_ring():
    s = _style()
    for bar, (plan, ev) in _events(s, _bars(s, ("intro", "breakdown"))).items():
        gtr = [info for k, _, _, info in ev if k == "guitar"]
        assert gtr and not any(info["mute"] for info in gtr)
        assert len(gtr) <= 8 and max(info["dur"] for info in gtr) >= 2 * B * 0.9


# ---------------------------------------------------------------- bass, drums, lead
def test_bass_follows_the_roots_in_eighths():
    s = _style()
    for bar, (plan, ev) in _events(s, _bars(s, ("A", "B"))).items():
        bass = sorted((t, info["midi"]) for k, t, _, info in ev if k == "bass")
        assert len(bass) == 8 and all(t == pytest.approx(i * B / 2, abs=1e-6) for i, (t, _) in enumerate(bass))
        assert bass[0][1] == plan.bass[0].midi and all(m <= 52 for _, m in bass)


def test_rock_beat_kick_one_three_snare_two_four_eighth_hats_crash_on_section_start():
    s = _style()
    for bar, (plan, ev) in _events(s, _bars(s, ("A",))).items():
        beats = lambda kind: sorted(round(t / B, 2) for k, t, *_ in ev if k == kind)
        assert {0, 2} <= set(beats("kick")) and len(beats("kick")) <= 4
        assert {1, 3} <= set(beats("snare"))
        assert len(beats("hat")) + len(beats("open_hat")) + len(beats("ride")) >= 8
        assert ("crash" in [k for k, *_ in ev]) == (plan.section_bar == 0)
        assert "china" not in [k for k, *_ in ev]


def test_metal_double_kick_and_accents():
    s = _style(drums="busy", energy=0.9)
    sel = _events(s, _bars(s, ("B",)))
    for bar, (plan, ev) in sel.items():
        kicks = [t for k, t, *_ in ev if k == "kick"]
        assert len(kicks) >= 14 and all(_on_grid(t, B / 4) for t in kicks)
    assert any(k == "china" for _, ev in sel.values() for k, *_ in ev)


def test_no_drums_without_the_drum_layer():
    s = _style(drums="none")
    drums = {"kick", "snare", "hat", "open_hat", "ride", "crash", "china", "tom"}
    for bar, (plan, ev) in _events(s, list(range(24))).items():
        assert not drums & {k for k, *_ in ev}


def test_lead_guitar_plays_the_melody_with_vibrato_in_b_sections():
    s = _style(lead="piano", lead_density=1.0)
    ev = _events(s, list(range(36)))
    vib = {"A": [], "B": []}
    for bar, (plan, e) in ev.items():
        lead = sorted((t, info) for k, t, _, info in e if k == "lead")
        if "lead" not in plan.layers:
            assert not lead
            continue
        assert [info["midi"] for _, info in lead] == [n.midi for n in plan.melody]
        assert all(info["tone"] == "piano" for _, info in lead)
        if plan.section in vib:
            vib[plan.section] += [info["vibrato"] for _, info in lead]
    assert vib["A"] and vib["B"] and max(vib["A"]) == 0 and max(vib["B"]) > 0


def test_lead_none_and_style_tone_reach_the_voices():
    ev = _events(_style(lead="none", chords="rhodes"), list(range(20)))
    kinds = [(k, info) for _, e in ev.values() for k, _, _, info in e]
    assert not any(k == "lead" for k, _ in kinds)
    assert {info["tone"] for k, info in kinds if k == "guitar"} == {"rhodes"}


# ---------------------------------------------------------------- the real voices
def _check_voices(inst):
    open_ = inst.guitar((40, 47, 52), 1.0, 0.9, "piano", mute=False, take=0)
    mute = inst.guitar((40, 47, 52), 1.0, 0.9, "piano", mute=True, take=0)
    other = inst.guitar((40, 47, 52), 1.0, 0.9, "piano", mute=False, take=1)
    for x in (open_, mute, other, inst.bass(33, 0.5), inst.lead(64, 1.0, 0.9, "piano", 1.0),
              inst.strings(60, 1.0), inst.kick(), inst.snare(), inst.hat(), inst.crash(), inst.china(), inst.ride()):
        assert np.all(np.isfinite(x)) and 0.005 < np.max(np.abs(x)) < 2.0
        assert abs(x[-1]) < 2e-3 * max(1.0, np.max(np.abs(x)))   # ends quietly: no click
    rms = lambda x, a, b: float(np.sqrt(np.mean(np.pad(x, (0, SR))[int(a * SR):int(b * SR)] ** 2)))
    assert rms(mute, 0.25, 0.5) < 0.25 * rms(open_, 0.25, 0.5)  # palm mute: short decay
    n = min(len(open_), len(other))
    assert not np.allclose(open_[:n], other[:n])                # the two takes differ


def test_procedural_voices():
    _check_voices(ROCK.instruments(np.random.default_rng(1), SR, None))


@pytest.mark.skipif(not HAS_SAMPLES, reason="FluidR3_GM or FluidSynth not installed (gentle-stream setup-samples)")
def test_sampled_voices():
    shared = samples.SampleInstruments(samples.load_renderer(REAL), np.random.default_rng(0))
    _check_voices(ROCK.instruments(np.random.default_rng(1), SR, shared))


def test_rendered_metal_is_loud_and_dense():
    e = Engine(_style(drums="busy", energy=0.9), seed=7, samples=False)
    x = np.concatenate([e.render_bar() for _ in range(16)])
    tail = x[int(len(x) * 0.5):]
    assert np.max(np.abs(x)) < 1.0 and float(np.sqrt(np.mean(tail ** 2))) > 0.1
