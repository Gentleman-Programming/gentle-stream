"""Cumbia: the traits that define the genre, measured on what `schedule` plays and on the rendered voices."""
import numpy as np
import pytest

from gentle_stream import genres
from gentle_stream.composer import Chord, Composer
from gentle_stream.genres.base import Piece
from gentle_stream.synth import SR
from gentle_stream.style import Style

G = genres.get("cumbia")
STYLE = Style(genre="cumbia", key="A", mode="minor", bpm=96, chords="piano", lead="pad", drums="lofi",
              energy=0.5, brightness=0.45, lead_density=0.8)
OFFBEATS = {0.5, 1.5, 2.5, 3.5}
PERCUSSION = {"scrape", "tambora", "conga", "cowbell", "shaker", "timbal"}


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


def _beat(t, style=STYLE):
    return round(t / (60 / style.bpm), 3)


def _beats(events, name, style=STYLE):
    return sorted(_beat(t, style) for n, _, t, _, _ in events if n == name)


def _section(bars, name):
    found = [(p, ev) for p, ev in bars if p.section == name]
    assert found, f"no {name} bars"
    return found


# ---------------------------------------------------------------- prompt hints, harmony and master
def test_prompt_hints():
    assert G.bpm == (85, 110)
    assert set(G.modes) <= {"minor", "major", "mixolydian", "dorian"} and "minor" in G.modes
    assert "accordion" in G.voices["pad"]


def test_chord_hook_keeps_plain_triads_but_dominant_sevenths():
    scale = (0, 2, 3, 5, 7, 8, 10)
    assert G.chord(Chord(1, (0, 3, 7, 10, 14)), scale).tones == (0, 3, 7)
    assert G.chord(Chord(0, (7, 11, 14, 17)), scale).tones == (7, 11, 14, 17)


def test_master_is_warm_danceable_with_a_light_room_and_no_vinyl():
    lofi = genres.get("lofi")
    for s in (STYLE, Style(genre="cumbia", vinyl=1, space=1, brightness=1), Style(genre="cumbia", brightness=0)):
        m, ref = G.master(s), lofi.master(s)
        assert m.vinyl == 0                                  # no vinyl, whatever the style asks
        assert m.wow <= 0.1                                  # steady pitch
        assert m.reverb <= ref.reverb and m.reverb <= 0.2    # light room
        assert m.room <= 1.0 and m.space <= 0.6
        assert 1.3 <= m.drive <= 2.2 and m.ratio >= 2.5     # glued, danceable
    assert 3500 <= G.master(Style(genre="cumbia", brightness=0)).tone <= 6000          # warm...
    assert 9000 <= G.master(Style(genre="cumbia", brightness=1)).tone <= 16000         # ...never harsh


# ---------------------------------------------------------------- percussion
def test_guacharaca_scrapes_steady_eighths_with_the_ch_chik_accent():
    for name in ("A", "B"):
        for p, ev in _section(_bars(), name):
            scrapes = sorted((_beat(t), args[0], g) for n, args, t, _, g in ev if n == "scrape")
            assert [b for b, *_ in scrapes] == [i / 2 for i in range(8)]
            for b, long_, g in scrapes:
                assert long_ == (b % 1 == 0)                 # long "ch" on the beat, short "chik" off it
            on = np.mean([g for b, _, g in scrapes if b % 1 == 0])
            off = np.mean([g for b, _, g in scrapes if b % 1])
            assert off > 1.15 * on                           # the accent falls on the "chik"


def test_tambora_marks_one_and_three_with_a_syncopation_when_energetic():
    bars = _bars()
    for p, ev in _section(bars, "A"):
        assert _beats(ev, "tambora") == [0, 2]
    for name in ("B", "B'"):
        for p, ev in _section(bars, name):
            hits = _beats(ev, "tambora")
            assert {0, 2} <= set(hits) and set(hits) - {0, 2} and set(hits) - {0, 2} <= OFFBEATS


def test_congas_slap_the_backbeat_and_open_tones_answer_off_the_beat():
    for name in ("A", "B", "A'"):
        for p, ev in _section(_bars(), name):
            congas = [(_beat(t), args[0]) for n, args, t, _, _ in ev if n == "conga"]
            assert sorted(b for b, k in congas if k == "slap") == [1, 3]
            opens = [b for b, k in congas if k == "open"]
            assert len(opens) >= 2 and set(opens) <= OFFBEATS
            assert {k for _, k in congas} <= {"slap", "open", "mute"}


def test_cowbell_and_shaker_only_in_the_high_energy_sections():
    bars = _bars()
    for name in ("B", "B'"):
        for p, ev in _section(bars, name):
            assert _beats(ev, "cowbell") == [0, 1, 2, 3]
            shaker = _beats(ev, "shaker")
            assert len(shaker) == 16 and np.allclose(np.diff(shaker), 0.25)
    for p, ev in _section(bars, "A"):
        assert not _beats(ev, "cowbell") and not _beats(ev, "shaker")


def test_no_percussion_without_the_drum_layer():
    bars = _bars()
    for name in ("intro", "breakdown"):
        for p, ev in _section(bars, name):
            assert not PERCUSSION & {e[0] for e in ev}
    for p, ev in _bars(Style(**(STYLE.to_dict() | {"drums": "none"}))):
        assert not PERCUSSION & {e[0] for e in ev}


# ---------------------------------------------------------------- bass and chords
def test_bass_alternates_root_and_fifth_on_one_and_three_with_pickups():
    bars = _bars()
    for name in ("A", "B", "B'"):
        for p, ev in _section(bars, name):
            bass = {_beat(t): args[0] for n, args, t, _, _ in ev if n == "bass"}
            root = p.bass[0].midi
            assert bass[0] % 12 == root % 12
            second = (p.passing.tones[0] + p.key_offset + 45) % 12 if p.passing is not None else (root + 7) % 12
            assert bass[2] % 12 == second
            assert {1.5, 3.5} <= set(bass) and set(bass) <= {0, 1.5, 2, 3.5}
    for p, ev in _section(bars, "breakdown"):
        bass = _beats(ev, "bass")
        assert bass and 1.5 not in bass and 3.5 not in bass  # no pickups when it breaks down


def test_keyboard_chords_sit_on_the_and_of_each_beat():
    for p, ev in _bars():
        keys = [(_beat(t), args) for n, args, t, _, _ in ev if n == "keys"]
        assert keys
        starts = {round(b * 2) / 2 for b, _ in keys}         # tones of one stab may be strummed a few ms apart
        assert starts <= OFFBEATS
        if p.section in ("A", "B"):
            assert starts == OFFBEATS
            assert len(keys) >= 3 * 4                        # stacked chord tones on every off-beat
        assert all(args[0] == STYLE.chords for _, args in keys)


# ---------------------------------------------------------------- lead
def test_accordion_plays_the_melody_and_turns_digital_when_bright():
    for p, ev in _bars():
        acc = [e for e in ev if e[0] == "accordion"]
        assert len(acc) == (len(p.melody) if "lead" in p.layers else 0)
        assert sorted(a[1][0] for a in acc) == sorted(n.midi for n in p.melody) if acc else True
        assert not [e for e in ev if e[0] == "synth"]
    bright = Style(**(STYLE.to_dict() | {"brightness": 0.85}))
    leads = 0
    for p, ev in _bars(bright):
        assert not [e for e in ev if e[0] == "accordion"]
        leads += len([e for e in ev if e[0] == "synth"])
        assert len([e for e in ev if e[0] == "synth"]) == (len(p.melody) if "lead" in p.layers else 0)
    assert leads > 0


def test_counter_melody_on_the_organ():
    seen = 0
    for p, ev in _bars():
        c = [e for e in ev if e[0] == "counter"]
        assert len(c) == (len(p.counter) if "counter" in p.layers else 0)
        seen += len(c)
    assert seen > 0


# ---------------------------------------------------------------- voices
def _pitch(x, a, b):
    seg = x[int(a * SR):int(b * SR)]
    up = np.flatnonzero((seg[:-1] < 0) & (seg[1:] >= 0))
    return 1 / (np.diff(up) / SR)


def test_accordion_is_a_reedy_detuned_voice_with_vibrato_and_a_bellows_swell():
    inst = G.instruments(np.random.default_rng(3), SR, None)
    x = inst.accordion(69, 1.6, 0.9, "pad")
    assert np.all(np.isfinite(x)) and 0.01 < np.max(np.abs(x)) < 1.5
    env = np.sqrt(np.convolve(x ** 2, np.ones(441) / 441, "same"))
    assert env[int(0.005 * SR)] < 0.5 * env[int(0.15 * SR)]          # the bellows push in, no hard attack
    assert env[int(0.9 * SR)] > env[int(0.15 * SR)] * 0.8           # and keeps the note sustained
    sus = env[int(0.4 * SR):int(1.4 * SR)]
    assert np.std(sus) / np.mean(sus) > 0.02                         # detuned reeds beat
    spec = np.abs(np.fft.rfft(x[int(0.3 * SR):int(1.3 * SR)]))
    freqs = np.fft.rfftfreq(int(SR), 1 / SR)
    assert freqs[np.argmax(spec)] == pytest.approx(440, rel=0.03)
    assert spec[(freqs > 1200) & (freqs < 4000)].sum() > 0.2 * spec[(freqs > 300) & (freqs < 600)].sum()   # reedy


def test_guacharaca_is_bright_scraped_noise_long_ch_and_short_chik():
    inst = G.instruments(np.random.default_rng(4), SR, None)
    ch, chik = inst.scrape(True), inst.scrape(False)
    for x in (ch, chik):
        assert np.all(np.isfinite(x)) and 0.01 < np.max(np.abs(x)) < 1.5
        spec = np.abs(np.fft.rfft(x)); freqs = np.fft.rfftfreq(len(x), 1 / SR)
        assert np.sum(freqs * spec) / np.sum(spec) > 2500           # bright noise
    dur = lambda x: np.flatnonzero(np.abs(x) > 0.05 * np.max(np.abs(x)))[-1] / SR
    assert dur(ch) > 2 * dur(chik)
    env = np.sqrt(np.convolve(ch ** 2, np.ones(44) / 44, "valid"))[:int(0.08 * SR)]
    assert np.std(env) / np.mean(env) > 0.3                          # the ridges of the scraper


def test_samples_backend_uses_bass_accordion_and_standard_kit_percussion():
    calls = []

    class Fake:
        def play(self, p, m, dur, vel=1.0):
            calls.append(("play", p.program, p.bank))
            return np.zeros(64)

        def hit(self, h, g=1.0):
            calls.append(("hit", h.kit, h.key))
            return np.zeros(64)

    def run(style):
        calls.clear()
        inst = G.instruments(np.random.default_rng(4), SR, Fake())
        piece = Piece(style, np.random.default_rng(5), inst, 45, SR, np.zeros((SR * 30, 2)))
        G.start(piece)
        comp = Composer(style, 7, 45, G.chord)
        for bar in range(32):
            G.schedule(piece, comp.plan(bar))
        return {c[1] for c in calls if c[0] == "play"}, {c[1] for c in calls if c[0] == "hit"}, \
            {c[2] for c in calls if c[0] == "hit"}

    programs, kits, keys = run(STYLE)
    assert {33} <= programs and programs & {21, 23}             # finger bass, accordion
    assert kits == {0}                                          # Standard kit
    assert {62, 63, 64} & keys and {73, 74} <= keys and {69, 70} & keys
    programs, _, _ = run(Style(**(STYLE.to_dict() | {"brightness": 0.85})))
    assert programs & {80, 81} and not programs & {21, 23}      # digital cumbia lead
