"""Moments: finite pieces with an arc and an exact duration, rendered offline to a WAV file.

A moment is music for one occasion, orthogonal to the genre (any registered genre, both backends):

- epic (an announcement, trailer music): a build whose energy rises into a roll, the hit on a downbeat at about
  two thirds of the duration (tutti plus an impact), the climax, then a final stinger chord that rings out;
- suspense (a reveal): sustained tension over a tonic pedal with unresolved harmony, a short near-silence, then the
  reveal (a bright tonic with a major third plus an impact) and its decay.

`plan` fits the bars to the duration by choosing the tempo inside the genre's range (closest to the style's), so the
hit falls on a downbeat and the final chord leaves time to ring; `render` plays the plan through the engine's finite
form (no audio device, no background player), shapes the gap and the final fade, and peak-normalises to -1 dBFS;
`write_wav` writes 16-bit stereo at the engine's sample rate. Deterministic per seed.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from . import genres
from .composer import Section
from .style import Style
from .synth import SR

KINDS = ("epic", "suspense")
MIN_SECONDS, MAX_SECONDS = 5.0, 300.0
DEFAULT_SECONDS = 30.0
DEFAULT_GENRE = "orchestral"
PEAK_DBFS = -1.0
STYLE_BPM = (50.0, 140.0)   # Style's tempo bounds: the fallback when the genre's range cannot fit the duration
HIT_AT = 0.65               # epic: the hit, as a fraction of the duration
GAP_DUCK = 0.003            # suspense: the gain of the near-silence before the reveal (about -50 dB)
# mix automation before the hit or reveal, in dB (start, end): the master's bus compressor flattens the sections'
# energies, so the arc is also drawn with gain
ARC_DB = {"epic": (-9.0, 0.0), "suspense": (-10.0, -7.0)}
DEFAULT_STYLES = {
    "epic": Style(name="Epic Announcement", genre=DEFAULT_GENRE, key="D", mode="minor", bpm=120.0,
                  progression=(1, 6, 3, 7), chords="piano", lead="piano", drums="busy", energy=0.9, brightness=0.7,
                  swing=0.0, vinyl=0.0, space=0.7, lead_density=0.6),
    "suspense": Style(name="The Reveal", genre=DEFAULT_GENRE, key="C", mode="minor", bpm=70.0,
                      progression=(1, 6, 4, 5), chords="piano", lead="none", drums="soft", energy=0.3,
                      brightness=0.25, swing=0.0, vinyl=0.0, space=0.85, lead_density=0.3),
}


def default_style(kind: str, genre: str | None = None) -> Style:
    """The style a moment uses without a mood: epic is minor, ~120 bpm, bright and loud; suspense minor, slow, dark."""
    style = DEFAULT_STYLES[_kind(kind)]
    return style if genre is None else replace(style, genre=genre)


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown moment kind {kind!r} (choose from {', '.join(KINDS)})")
    return kind


def check_seconds(seconds: float) -> float:
    s = float(seconds)
    if not (MIN_SECONDS <= s <= MAX_SECONDS):   # also rejects nan
        raise ValueError(f"duration must be between {MIN_SECONDS:g} and {MAX_SECONDS:g} seconds")
    return s


@dataclass(frozen=True)
class Plan:
    kind: str
    seconds: float
    sr: int
    bpm: float
    sections: tuple[Section, ...]
    hit_bar: int                       # epic: the hit; suspense: the reveal
    end_bar: int                       # the final chord (stinger or reveal)
    gap: tuple[float, float] | None    # suspense: start and end (the reveal) of the near-silence, seconds

    @property
    def bar_samples(self) -> int:      # exactly what the engine renders per bar
        return int(round(240.0 / self.bpm * self.sr))

    @property
    def hit_seconds(self) -> float:
        return self.hit_bar * self.bar_samples / self.sr

    @property
    def end_seconds(self) -> float:
        return self.end_bar * self.bar_samples / self.sr


def _fit(body: float, target: float, lo: float, hi: float, min_bars: int) -> int:
    """Bars before the final chord: the count whose tempo, filling `body` seconds, is closest to `target`."""
    for a, b in ((lo, hi), STYLE_BPM):
        options = [n for n in range(min_bars, int(body * b / 240) + 1) if a <= 240 * n / body <= b]
        if options:
            return min(options, key=lambda n: abs(240 * n / body - target))
    return min_bars


def plan(kind: str, seconds: float, style: Style, sr: int = SR) -> Plan:
    """Sections, tempo and the hit of a `kind` moment lasting `seconds` in `style`'s genre."""
    kind, seconds = _kind(kind), check_seconds(seconds)
    lo, hi = genres.get(style.genre).bpm
    lo, hi = max(lo, STYLE_BPM[0]), min(hi, STYLE_BPM[1])
    target = min(hi, max(lo, style.bpm))
    # time from the final chord to the end: it rings, then fades
    ring = min(seconds / 2, max(2.5, 0.2 * seconds), 8.0) if kind == "epic" else \
        min(seconds / 2, max(3.0, 0.3 * seconds), 10.0)
    body = seconds - ring
    bars = _fit(body, target, lo, hi, 2)
    bpm = min(STYLE_BPM[1], 240 * bars / body)
    bar_s = 240 / bpm
    if kind == "epic":
        hit = min(bars - 1, max(1, round(HIT_AT * seconds / bar_s)))
        build = [Section("B", hit, (0.45, 0.85), "build", melody=True, last="roll")]
        if hit >= 4:   # sparse at first (no drums), then the drums and the melody join
            build = [Section("intro", hit // 2, (0.2, 0.45), "build", drums=False),
                     Section("B", hit - hit // 2, (0.6, 0.85), "build", melody=True, last="roll")]
        sections = (*build, Section("B'", bars - hit, (1.0, 1.0), "climax", melody=True, first="hit"),
                    Section("release", 1, (0.75, 0.75), "tonic", drums=False, first="stinger"))
        gap = None
    else:
        tension = [Section("intro", bars, (0.2, 0.4), "tension", drums=False, last="gap")]
        if bars >= 4:
            tension = [Section("intro", bars // 2, (0.15, 0.3), "tension", drums=False),
                       Section("A", bars - bars // 2, (0.3, 0.45), "tension", last="gap")]
        sections = (*tension, Section("release", 1, (1.0, 1.0), "reveal", drums=False, first="stinger"))
        hit = bars
    p = Plan(kind, seconds, sr, bpm, sections, hit, bars, None)
    if kind == "suspense":
        reveal = p.hit_seconds
        gap = (reveal - min(2 * 60 / bpm, 1.0), reveal)
    return replace(p, gap=gap)


@dataclass(frozen=True)
class Moment:
    audio: np.ndarray   # float32 stereo, (n, 2), peak at -1 dBFS
    sr: int
    plan: Plan
    style: Style
    seed: int
    backend: str        # procedural or samples


def _envelope(p: Plan, n: int) -> np.ndarray:
    """The arc's gain before the hit, the gap's near-silence (suspense) and the final fade to silence."""
    sr = p.sr
    t = np.arange(n) / sr
    lo, hi = ARC_DB[p.kind]
    env = np.where(t < p.hit_seconds, 10 ** ((lo + (hi - lo) * t / p.hit_seconds) / 20), 1.0)
    if p.gap is not None:
        start, end = p.gap
        down = np.clip((t - start) / 0.06, 0, 1)                    # 60 ms down into the gap
        up = np.clip((t - (end - 0.005)) / 0.005, 0, 1)             # 5 ms back up, ending on the reveal
        env *= 1 - (1 - GAP_DUCK) * down * (1 - up)
    fade = p.end_seconds + 0.4 * (p.seconds - p.end_seconds)
    x = np.clip((t - fade) / (p.seconds - fade), 0, 1)
    env *= np.cos(0.5 * np.pi * x) ** 2
    env[-1] = 0.0
    return env


def render(kind: str, seconds: float = DEFAULT_SECONDS, style: Style | None = None, seed: int = 0,
           samples: bool | str | Path = True, sr: int = SR) -> Moment:
    """Render a `kind` moment of exactly `seconds` in `style` (default: `default_style(kind)`). `samples` as in
    `Engine`: the installed soundfont (and recorded instruments) when available, else procedural."""
    from .engine import Engine   # imported late: the CLI only needs it when it renders

    style = style or default_style(kind)
    p = plan(kind, seconds, style, sr)
    style = replace(style, bpm=p.bpm)
    engine = Engine(style, seed=seed, sr=sr, samples=samples, form=p.sections)
    n = int(round(p.seconds * sr))
    chunks, got = [], 0
    while got < n:
        chunks.append(engine.render_bar())
        got += len(chunks[-1])
    x = np.concatenate(chunks)[:n].astype(np.float64) * _envelope(p, n)[:, None]
    peak = float(np.max(np.abs(x)))
    if peak > 0:
        x *= 10 ** (PEAK_DBFS / 20) / peak
    return Moment(x.astype(np.float32), sr, p, style, seed, engine.backend)


def write_wav(path: str | Path, moment: Moment) -> Path:
    """16-bit stereo PCM at the moment's sample rate."""
    from scipy.io import wavfile

    path = Path(path)
    data = np.round(np.clip(moment.audio, -1, 1) * 32767).astype(np.int16)
    wavfile.write(path, moment.sr, data)
    return path
