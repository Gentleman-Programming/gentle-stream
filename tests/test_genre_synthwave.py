"""Synthwave: the traits that define the genre, measured on what `schedule` plays and on the rendered voices."""
import numpy as np
import pytest

from gentle_stream import genres
from gentle_stream.composer import Composer
from gentle_stream.genres.base import Piece
from gentle_stream.synth import SR, lp
from gentle_stream.style import Style

G = genres.get("synthwave")
STYLE = Style(genre="synthwave", key="A", mode="minor", bpm=100, chords="pad", lead="piano", drums="lofi",
              energy=0.5, lead_density=0.8)


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
    return sorted(round(t / beat, 3) for n, _, t, _, _ in events if n == name)


def _section(bars, name):
    found = [(p, ev) for p, ev in bars if p.section == name]
    assert found, f"no {name} bars"
    return found


# ---------------------------------------------------------------- prompt hints and master
def test_prompt_hints():
    assert G.bpm == (80, 118)
    assert G.modes == ("minor", "dorian", "major")
    assert "pad" in G.voices["pad"] and "saw" in G.voices["pad"]


def test_master_is_clean_open_and_roomy():
    lofi = genres.get("lofi")
    for s in (STYLE, Style(genre="synthwave", vinyl=1, space=0, brightness=0)):
        m, ref = G.master(s), lofi.master(s)
        assert m.vinyl == 0                                  # no vinyl, whatever the style asks
        assert 0 < m.wow < 0.5                              # a hint of tape, far from lo-fi's wow
        assert m.reverb > ref.reverb and m.space > ref.space * 0.99 and m.space >= 0.4
        assert m.tone >= ref.tone and m.tone >= 6000        # open tone
        assert 1.2 <= m.drive <= 2.5                        # moderate drive


# ---------------------------------------------------------------- drums
def test_four_on_the_floor_in_b_sections_and_one_three_elsewhere():
    bars = _bars()
    for name in ("B", "B'"):
        for p, ev in _section(bars, name):
            assert _beats(ev, "kick") == [0, 1, 2, 3]
    for p, ev in _section(bars, "A"):
        assert _beats(ev, "kick") == [0, 2]


def test_big_snare_on_two_and_four_and_sixteenth_hats():
    for p, ev in _section(_bars(), "B"):
        assert _beats(ev, "snare") == [1, 3]
        hats = _beats(ev, "hat")
        assert len(hats) == 16 and np.allclose(np.diff(hats), 0.25)


def test_no_drums_without_the_drum_layer():
    bars = _bars()
    for name in ("intro", "breakdown"):
        for p, ev in _section(bars, name):
            assert not {"kick", "snare", "hat"} & {e[0] for e in ev}
    for p, ev in _bars(Style(**(STYLE.to_dict() | {"drums": "none"}))):
        assert not {"kick", "snare", "hat"} & {e[0] for e in ev}


def test_snare_has_a_gated_reverb_tail():
    inst = G.instruments(np.random.default_rng(2), SR, None)
    x = inst.snare()
    rms = lambda a, b: float(np.sqrt(np.mean(x[int(a * SR):int(b * SR)] ** 2)))
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) < 1.5
    assert len(x) >= 0.3 * SR
    assert rms(0.12, 0.22) > 0.25 * rms(0.0, 0.05)          # a long, dense tail (not a dry snap)...
    assert rms(0.12, 0.22) > 20 * rms(len(x) / SR - 0.03, len(x) / SR)   # ...cut off abruptly by the gate


# ---------------------------------------------------------------- bass and arpeggio
def test_pulsing_octave_bass_in_eighths():
    for name in ("A", "B"):
        for p, ev in _section(_bars(), name):
            bass = sorted((t, args[0]) for n, args, t, _, _ in ev if n == "bass")
            beat = 60 / STYLE.bpm
            assert np.allclose([t / beat for t, _ in bass], np.arange(8) * 0.5)
            low, high = bass[0][1], bass[1][1]
            assert high - low == 12                          # root, then its octave
            assert all(m % 12 == p.bass[0].midi % 12 for _, m in bass[:4])


def test_arpeggio_runs_in_sixteenths_only_in_high_energy_sections():
    bars = _bars()
    for name in ("B", "B'"):
        for p, ev in _section(bars, name):
            arp = _beats(ev, "arp")
            assert len(arp) == 16 and np.allclose(np.diff(arp), 0.25)
            assert len({args[0] for n, args, *_ in ev if n == "arp"}) >= 3   # chord tones, not one note
    for name in ("intro", "A", "breakdown"):
        for p, ev in _section(bars, name):
            assert not _beats(ev, "arp")


def test_pad_holds_the_chords_and_the_lead_plays_the_melody():
    for p, ev in _bars():
        pads = [e for e in ev if e[0] == "pad"]
        assert len(pads) >= 3                                # stacked chord tones every bar
        leads = [e for e in ev if e[0] == "lead"]
        assert len(leads) == (len(p.melody) if "lead" in p.layers else 0)


# ---------------------------------------------------------------- voices
def test_pad_is_a_detuned_stack_and_the_lead_has_vibrato():
    inst = G.instruments(np.random.default_rng(3), SR, None)
    pad = inst.pad(57, 2.0, 0.8)
    assert np.all(np.isfinite(pad)) and np.max(np.abs(pad)) > 0.01
    # detuned voices beat: the slow amplitude envelope of the sustained part fluctuates
    env = np.sqrt(np.convolve(pad ** 2, np.ones(441) / 441, "valid"))[int(0.8 * SR):int(1.8 * SR)]
    assert np.std(env) / np.mean(env) > 0.03
    lead = inst.lead(69, 1.6, 0.9)
    seg = lead[int(0.5 * SR):int(1.5 * SR)]
    up = np.flatnonzero((seg[:-1] < 0) & (seg[1:] >= 0))
    periods = np.diff(up) / SR
    freqs = 1 / periods[periods > 0]
    assert np.median(freqs) == pytest.approx(440, rel=0.03)
    assert np.std(freqs) > 1.0                              # vibrato moves the pitch


@pytest.mark.parametrize(
    "midi,presence,upper,level,peak,release",
    [
        (60, 0.057060, 0.003122, 0.216961, 0.521828, 0.219692),
        (69, 0.080878, 0.005579, 0.213988, 0.508136, 0.216864),
        (81, 0.240841, 0.014274, 0.206624, 0.470231, 0.215616),
    ],
)
def test_saw_lead_tames_upper_harmonics_without_losing_presence(midi, presence, upper, level, peak, release):
    # Reference measurements are from the same 0.45 s, unity-velocity dry notes before reshaping.
    inst = G.instruments(np.random.default_rng(3), SR, None)
    x = inst.lead(midi, 0.45)
    assert len(x) == int(0.6 * SR)
    assert np.all(np.isfinite(x)) and np.max(np.abs(x)) <= peak + 1e-6
    rms = lambda a, b: np.sqrt(np.mean(x[int(a * SR):int(b * SR)] ** 2))
    assert rms(0, 0.6) == pytest.approx(level, rel=0.02)
    assert rms(0.55, 0.59) / rms(0.3, 0.4) == pytest.approx(release, rel=0.01)

    # Measure after attack and before vibrato onset; fractions discount any overall level change.
    seg = x[int(0.04 * SR):int(0.19 * SR)]
    power = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))) ** 2
    hz = np.fft.rfftfreq(len(seg), 1 / SR)
    fraction = lambda a, b: power[(hz >= a) & (hz < b)].sum() / power.sum()
    assert fraction(1500, 3000) >= 0.95 * presence
    assert fraction(5000, 12000) <= 0.6 * upper


def test_saw_lead_preserves_velocity_and_echo_taps():
    inst = G.instruments(np.random.default_rng(3), SR, None)
    x = inst.lead(69, 0.45)
    # Use a delay longer than the dry note to isolate the two repeat gains and onset times.
    delay = 0.65
    d = int(delay * SR)
    expected = np.zeros(len(x) + 2 * d)
    expected[:len(x)] = x
    wet = lp(x, 3000, SR)
    expected[d:d + len(x)] = 0.32 * wet
    expected[2 * d:] = 0.12 * wet
    np.testing.assert_array_equal(inst.lead(69, 0.45, 0.9, echo=delay), expected * 0.9)


def test_samples_backend_uses_gm_synths_and_the_electronic_kit():
    calls = []

    class Fake:
        def play(self, p, m, dur, vel=1.0):
            if p.program == 81:
                assert p.rms == pytest.approx(0.19000753476540946, abs=1e-12)
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
    assert {81, 90, 38} <= programs                         # saw lead, polysynth pad, synth bass
    assert kits == {24}                                    # electronic kit
