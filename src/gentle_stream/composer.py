"""Composition planner: song form, harmony, motif development, counter-melody and bass line.

The composer decides WHAT plays in every bar; the engine decides how it sounds. From a style, a seed and the tonic
it plans the piece one form cycle at a time (the stream is endless, so the form loops with fresh variation) and
answers `plan(bar)` with a `BarPlan`: section, energy, chord (and an optional passing chord on beats 3-4), key
lift, active layers and the notes of the lead, the counter-melody and the bass. It is pure and deterministic per
seed: it owns its random generator and never touches the audio one.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .style import MODES, PROGRESSIONS, Style

RHYTHMS = (((0, 1.5), (1.5, .5), (2, 2)), ((0.5, 1), (1.5, 1), (2.5, 1.5)), ((0, 1), (1, 1), (2, .5), (2.5, 1.5)),
           ((0, 3), (3, 1)), ((1, .5), (1.5, .5), (2, 2)), ((0, 2), (2.5, .5), (3, 1)))
# (section, bars); the intro opens a piece, then the form loops for as long as the piece plays
FIRST_FORM = (("intro", 4), ("A", 8), ("B", 8), ("A'", 8), ("breakdown", 4), ("B'", 8), ("release", 4))
FORM = FIRST_FORM[1:]
ENERGY = {"intro": .3, "A": .55, "B": .8, "A'": .62, "breakdown": .25, "B'": .95, "release": .4}
MAIN = ("A", "B", "A'", "B'", "release")   # sections that carry the melody and close on the tonic
COUNTER_ENERGY = 0.7
LIFT_CHANCE = 0.5
# cadence chords per mode: (pre-dominant, dominant) scale degrees; the dominant resolves to the tonic
CADENCES = {"minor": (4, 5), "major": (4, 5), "dorian": (4, 5), "phrygian": (7, 2), "lydian": (5, 2),
            "mixolydian": (4, 7)}
RAISED = ("minor", "dorian")   # these borrow the major dominant (raised leading tone) at cadences


@dataclass(frozen=True)
class Note:
    beat: float
    midi: int
    length: float   # in beats


@dataclass(frozen=True)
class Chord:
    degree: int              # 1-based scale degree; 0 for chromatic (secondary / pivot) dominants
    tones: tuple[int, ...]   # semitones above the tonic, root first


@dataclass(frozen=True)
class BarPlan:
    bar: int
    section: str
    section_bar: int
    energy: float
    chord: Chord
    passing: Chord | None    # replaces `chord` from beat 3 (0-based beat 2) on
    key_offset: int          # modulation lift in semitones, applied to chords and notes
    layers: frozenset[str]   # chords, bass, drums, lead, counter
    melody: tuple[Note, ...]   # the planned lead line (played when "lead" is in layers)
    counter: tuple[Note, ...]
    bass: tuple[Note, ...]


# ---------------------------------------------------------------- motif transformations
# a motif is a tuple of (beat, scale step, length) over two bars

def invert(motif):
    return tuple((b, -s, ln) for b, s, ln in motif)


def sequence(motif, k):
    return tuple((b, s + k, ln) for b, s, ln in motif)


def augment(motif):
    """The first bar at half speed, filling two bars."""
    return tuple((2 * b, s, 2 * ln) for b, s, ln in motif if b < 4)


def _identity(motif):
    return motif


# per main section: (question transform, answer transform, starting step); the release is a lone answer
DEVELOPMENT = {
    "A": (_identity, lambda m: sequence(m, -1), 0),
    "B": (invert, lambda m: sequence(invert(m), 1), 2),
    "A'": (_identity, augment, 0),
    "B'": (lambda m: sequence(invert(m), 1), lambda m: augment(invert(m)), 2),
    "release": (None, augment, 0),
}


@dataclass
class _Bar:
    section: str
    section_bar: int
    energy: float
    chord: Chord
    passing: Chord | None = None
    key_offset: int = 0
    melody: tuple = ()
    counter: tuple = ()
    lead: bool = False
    approach: float = 0.0   # random draw that picks the bass approach note


class Composer:
    def __init__(self, style: Style, seed: int, ton: int):
        self.style, self.ton = style, ton
        self.rng = np.random.default_rng([seed, 0xC0])
        self.scale_iv = MODES[style.mode]
        self.ninth = {d: bool(self.rng.random() < 0.55) for d in range(1, 8)}
        self.motif = self._make_motif()
        self.anchor = self._anchor()
        self.prog_b: tuple[int, ...] | None = None
        self.cycle = 0
        self._bars: list[_Bar] = []

    # ------------------------------------------------------------ public
    def plan(self, bar: int) -> BarPlan:
        while len(self._bars) < bar + 2:
            self._plan_cycle()
        b, nxt = self._bars[bar], self._bars[bar + 1]
        layers = {"chords", "bass"}
        if self.style.drums != "none" and b.section not in ("intro", "breakdown"):
            layers.add("drums")
        if b.lead:
            layers.add("lead")
        if b.counter:
            layers.add("counter")
        return BarPlan(bar, b.section, b.section_bar, b.energy, b.chord, b.passing, b.key_offset,
                       frozenset(layers), b.melody, b.counter, self._bass(bar, b, nxt))

    # ------------------------------------------------------------ harmony
    def _chord(self, degree: int, raised: bool = False) -> Chord:
        sc, d = self.scale_iv, degree - 1

        def deg(k):
            return sc[(d + k) % 7] + 12 * ((d + k) // 7)

        tones = [deg(0), deg(2), deg(4), deg(6)]
        if raised and tones[1] - tones[0] == 3:   # minor third -> major: the leading tone
            tones[1] += 1
        elif self.ninth[degree] and deg(4) - deg(0) != 6:  # no ninth on diminished chords
            tones.append(deg(8))
        return Chord(degree, tuple(tones))

    @staticmethod
    def _dominant7(root: int) -> Chord:
        r = root % 12
        return Chord(0, (r, r + 4, r + 7, r + 10))

    def _degrees(self, prog, n):
        return [prog[i % len(prog)] for i in range(n)]

    def _pick_b(self) -> tuple[int, ...]:
        """A contrasting progression of the same mode for section B; a fresh one every cycle."""
        a = self._degrees(self.style.progression, 6)
        options = [p for p in PROGRESSIONS[self.style.mode] if self._degrees(p, 6) != a]
        if len(options) > 1 and self.prog_b in options:
            options.remove(self.prog_b)
        if not options:
            p = self.style.progression
            return tuple(p[1:] + p[:1])
        return tuple(options[int(self.rng.integers(len(options)))])

    def _harmony(self, name: str, n: int):
        prog = self.prog_b if name in ("B", "B'") else self.style.progression
        pre, dom = CADENCES[self.style.mode]
        raised = self.style.mode in RAISED
        chords = [self._chord(d) for d in self._degrees(prog, n)]
        passing: list[Chord | None] = [None] * n
        if name in MAIN:   # authentic cadence: (IV-)V-I, sometimes prepared by a secondary dominant
            if self.rng.random() < 0.5:
                chords[-2], passing[-2] = self._chord(pre), self._chord(dom, raised)
            else:
                chords[-2] = self._chord(dom, raised)
                if self.rng.random() < 0.5:
                    passing[-3] = self._dominant7(self.scale_iv[dom - 1] + 7)
            chords[-1] = self._chord(1)
        else:              # intro and breakdown end open, on the dominant
            chords[-1] = self._chord(dom, raised)
        return chords, passing

    # ------------------------------------------------------------ form
    def _plan_cycle(self) -> None:
        form = FIRST_FORM if self.cycle == 0 else FORM
        if self.cycle > 0:
            self.motif = self._develop(self.motif)
        self.prog_b = self._pick_b()
        lift_at, lift = None, 0
        if self.rng.random() < LIFT_CHANCE:
            lift_at = ("A'", "B'")[int(self.rng.integers(2))]
            lift = int(self.rng.choice([1, 2]))
        scale = 0.5 + self.style.energy
        for name, n in form:
            energy = float(min(1.0, ENERGY[name] * scale))
            chords, passing = self._harmony(name, n)
            key = lift if name == lift_at else 0
            if key and self._bars:   # pivot: the dominant of the new key on the last beats before the lift
                self._bars[-1].passing = self._dominant7(7 + key)
            start = len(self._bars)
            for i in range(n):
                self._bars.append(_Bar(name, i, energy, chords[i], passing[i], key,
                                       approach=float(self.rng.random())))
            if name in MAIN:
                self._melody(start, name, n, key)
                if energy >= COUNTER_ENERGY and self.style.lead != "none":
                    self._counter(start, n, key)
        self.cycle += 1

    # ------------------------------------------------------------ melody
    def _make_motif(self):
        """Two bars of (beat, scale step, length): steps mostly down, at most one leap up."""
        r1, r2 = (RHYTHMS[i] for i in self.rng.choice(len(RHYTHMS), 2, replace=False))
        return self._line(((0, r1), (1, r2)), 0)

    def _line(self, bars, step):
        out, leap = [], False
        for bar, rh in bars:
            for beat, ln in rh:
                out.append((bar * 4 + beat, step, ln))
                d = int(self.rng.choice([-1, -1, -2, 1, 0]))
                if not leap and self.rng.random() < 0.18:
                    d, leap = 3, True
                step = int(np.clip(step + d, -5, 5))
        return tuple(out)

    def _develop(self, motif):
        """Keep the head (first bar), grow a new tail: the piece's identity stays, the phrase moves on."""
        head = tuple(x for x in motif if x[0] < 4)
        rh = RHYTHMS[int(self.rng.integers(len(RHYTHMS)))]
        return head + self._line(((1, rh),), head[-1][1] - 1)

    def _anchor(self) -> int:
        scale = self._scale(0)
        target = self.ton + 24 + self.scale_iv[int(self.rng.choice([0, 2, 4]))]
        return scale.index(min(scale, key=lambda m: abs(m - target)))

    def _scale(self, key: int) -> list[int]:
        return sorted(self.ton + key + 12 + o + 12 * k for o in self.scale_iv for k in range(4))

    def _tones(self, b: _Bar, beat: float) -> set[int]:
        chord = b.passing if b.passing is not None and beat >= 2 else b.chord
        return {(self.ton + t + b.key_offset) % 12 for t in chord.tones[:4]}

    @staticmethod
    def _nearest(m: int, pcs, avoid=()) -> int:
        for d in (0, -1, 1, -2, 2, -3, 3, -4, 4, -5, 5, -6, 6):
            if (m + d) % 12 in pcs and (m + d) % 12 not in avoid:
                return m + d
        return m

    def _melody(self, start: int, name: str, n: int, key: int) -> None:
        """Question/answer phrases from the motif; strong beats on chord tones, the answer ends on the tonic."""
        scale, s = self._scale(key), self.style
        q, a, lift = DEVELOPMENT[name]
        phrases = ((0, q), (4, a)) if n == 8 else ((4, a),)
        plays = (0, 1, 6, 7) if s.lead_density < 0.34 else (0, 1, 3, 4, 5, 7) if s.lead_density < 0.67 else range(8)
        tonic = (self.ton + key) % 12
        last = scale[self.anchor]
        for first, transform in phrases:
            line = transform(self.motif)
            body = (line, tuple((b, st - 1, ln) for b, st, ln in line if b < 4))   # bars 0-1, then a sequence
            for k in range(3):
                pos = first + k
                bar = self._bars[start + pos - (8 - n)]
                src = body[0] if k < 2 else body[1]
                notes = []
                for beat, st, ln in src:
                    if int(beat // 4) != (k if k < 2 else 0):
                        continue
                    b = beat % 4
                    m = scale[int(np.clip(self.anchor + lift + st, 0, len(scale) - 1))]
                    if b in (0, 2):
                        m = self._nearest(m, self._tones(bar, b))
                    notes.append(Note(b, m, ln))
                    last = m
                bar.melody, bar.lead = tuple(notes), s.lead != "none" and pos in plays
            # phrase end: the question holds a non-tonic chord tone, the answer lands on the tonic
            pos = first + 3
            bar = self._bars[start + pos - (8 - n)]
            pcs = self._tones(bar, 0)
            m = self._nearest(last, {tonic}) if first else self._nearest(last, pcs, avoid={tonic})
            bar.melody, bar.lead = (Note(0, m, 4),), s.lead != "none" and pos in plays

    def _sounding(self, i: int, beat: float) -> int | None:
        for j in range(i, -1, -1):
            notes = [x for x in self._bars[j].melody if j < i or x.beat <= beat]
            if notes:
                return notes[-1].midi
        return None

    def _counter(self, start: int, n: int, key: int) -> None:
        """A slower inner voice on chord tones, moving against the lead's direction."""
        lo, hi = self.ton + key + 5, self.ton + key + 21
        prev = None
        if start and self._bars[start - 1].counter:
            last = self._bars[start - 1].counter[-1]
            prev = (last.midi, self._sounding(start - 1, last.beat))
        for i in range(start, start + n):
            b = self._bars[i]
            beats = ((0, 4),) if b.section_bar == n - 1 or b.section_bar == 3 else ((0, 2), (2, 2))
            notes = []
            for beat, ln in beats:
                cands = [m for m in range(lo, hi + 1) if m % 12 in self._tones(b, beat)]
                lead = self._sounding(i, beat)
                if prev is None:
                    m = min(cands, key=lambda c: abs(c - (lo + hi) / 2))
                else:
                    want = 0 if lead is None or prev[1] is None else -np.sign(lead - prev[1])
                    ways = [c for c in cands if np.sign(c - prev[0]) == want] if want else []
                    m = min(ways or cands, key=lambda c: abs(c - prev[0]))
                notes.append(Note(beat, m, ln))
                prev = (m, lead)
            b.counter = tuple(notes)

    # ------------------------------------------------------------ bass
    def _bass_midi(self, chord: Chord, key: int) -> int:
        root = (chord.tones[0] + key) % 12
        return self.ton - 12 + root if self.ton - 12 + root < 46 else self.ton - 24 + root

    def _bass(self, i: int, b: _Bar, nxt: _Bar) -> tuple[Note, ...]:
        """Root on beat 1, the passing chord's root or a fifth on the back half, an approach into the next root."""
        root = self._bass_midi(b.chord, b.key_offset)
        target = self._bass_midi(nxt.chord, nxt.key_offset)
        moves = target % 12 != root % 12
        end = 3.5 if moves else 4
        notes = [Note(0, root, 2 if b.passing is not None else 2.5)]
        if b.passing is not None:
            notes.append(Note(2, self._bass_midi(b.passing, b.key_offset), end - 2))
        elif b.energy >= 0.4 and b.section != "breakdown":
            notes.append(Note(2.5, root + (7 if i % 2 else 0), end - 2.5))
        if moves:
            if b.approach < 0.4:
                a = target - 1
            elif b.approach < 0.6:
                a = target + 1
            else:   # diatonic step from below
                pcs = {(self.ton + o + nxt.key_offset) % 12 for o in self.scale_iv}
                a = target - 1 if (target - 1) % 12 in pcs else target - 2
            notes.append(Note(3.5, a, 0.5))
        return tuple(notes)
