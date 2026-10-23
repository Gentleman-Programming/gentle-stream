"""Sampled instruments: the FluidR3_GM soundfont rendered note by note with FluidSynth.

The soundfont is never shipped with gentle-stream. `gentle-stream setup-samples` downloads Debian's
`fluid-soundfont-gm` package once, checks it against a pinned SHA-256, extracts `FluidR3_GM.sf2` into the user
cache and writes a LICENSES.txt next to it (FluidR3_GM is MIT licensed by Frank Wen and Toby Smithe). Without the
soundfont, or without FluidSynth and its `pyfluidsynth` binding (the `samples` extra), the engine keeps its
procedural instruments.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import tarfile
import tempfile
import threading
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np

DEB_URL = "https://deb.debian.org/debian/pool/main/f/fluid-soundfont/fluid-soundfont-gm_3.1-5.3_all.deb"
DEB_SHA256 = "6f531493ac4e4d9772fd96b2488ea1790af81c196135fbdd25997da0781fc60e"
SF2_NAME = "FluidR3_GM.sf2"
LICENSES_NAME = "LICENSES.txt"
SF2_MEMBER = "usr/share/sounds/sf2/FluidR3_GM.sf2"
COPYRIGHT_MEMBER = "usr/share/doc/fluid-soundfont-gm/copyright"
CHUNK = 1 << 20


class SetupError(Exception):
    """The soundfont could not be installed."""


class ChecksumError(SetupError):
    """The downloaded package does not match the pinned SHA-256."""


# ---------------------------------------------------------------- install
def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "gentle-stream"


def soundfont_path(cache: Path | None = None) -> Path | None:
    """The installed soundfont, or None when `setup-samples` has not been run (or did not finish)."""
    cache = cache_dir() if cache is None else Path(cache)
    sf2 = cache / SF2_NAME
    return sf2 if sf2.is_file() and (cache / LICENSES_NAME).is_file() else None


def setup_samples(cache: Path | None = None, url: str | None = None, sha256: str | None = None,
                  progress: Callable[[int, int | None], None] | None = None) -> Path:
    """Download, verify and extract FluidR3_GM into `cache`; does nothing when it is already installed.

    On any failure nothing is left in the cache.
    """
    cache = cache_dir() if cache is None else Path(cache)
    url, sha256 = url or DEB_URL, sha256 or DEB_SHA256
    if (done := soundfont_path(cache)) is not None:
        return done
    created = not cache.exists()
    cache.mkdir(parents=True, exist_ok=True)
    temps: list[Path] = []
    try:
        deb = _download(url, sha256, cache, temps, progress)
        sf2_tmp, copyright = _extract(deb, cache, temps)
        lic_tmp = _temp(cache, temps)
        lic_tmp.write_text(_licenses(url, sha256, copyright))
        for p in (sf2_tmp, lic_tmp):
            p.chmod(0o644)
        os.replace(sf2_tmp, cache / SF2_NAME)
        os.replace(lic_tmp, cache / LICENSES_NAME)   # written last: its presence marks a complete install
        return cache / SF2_NAME
    except BaseException:
        for p in (*temps, cache / SF2_NAME):
            p.unlink(missing_ok=True)
        if created and not any(cache.iterdir()):
            cache.rmdir()
        raise
    finally:
        for p in temps:
            p.unlink(missing_ok=True)


def _temp(cache: Path, temps: list[Path]) -> Path:
    fd, name = tempfile.mkstemp(dir=cache, prefix=".partial-")
    os.close(fd)
    temps.append(Path(name))
    return temps[-1]


def _download(url, sha256, cache, temps, progress) -> Path:
    path = _temp(cache, temps)
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(url) as r, open(path, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0) or None
            done = 0
            while chunk := r.read(CHUNK):
                f.write(chunk); digest.update(chunk); done += len(chunk)
                if progress:
                    progress(done, total)
    except OSError as e:
        raise SetupError(f"download failed: {e}") from e
    if digest.hexdigest() != sha256:
        raise ChecksumError(f"checksum mismatch for {url}: expected {sha256}, got {digest.hexdigest()}")
    if progress and total is None:
        progress(done, done)
    return path


class _Slice:
    """Read-only view of `size` bytes of a file from its current position (one `ar` member)."""

    def __init__(self, f, size: int):
        self.f, self.left = f, size

    def read(self, n: int = -1) -> bytes:
        n = self.left if n < 0 else min(n, self.left)
        data = self.f.read(n)
        self.left -= len(data)
        return data


def _extract(deb: Path, cache: Path, temps: list[Path]) -> tuple[Path, str]:
    """Stream `data.tar.*` out of the .deb (an `ar` archive) and pull the soundfont and copyright file."""
    with open(deb, "rb") as f:
        if f.read(8) != b"!<arch>\n":
            raise SetupError("not a Debian package")
        while header := f.read(60):
            if len(header) < 60:
                break
            name, size = header[:16].decode().strip().rstrip("/"), int(header[48:58])
            if not name.startswith("data.tar"):
                f.seek(size + (size & 1), os.SEEK_CUR)
                continue
            sf2, copyright = None, None
            with tarfile.open(fileobj=_Slice(f, size), mode="r|*") as tf:
                for m in tf:
                    member = m.name.removeprefix("./")
                    if member == COPYRIGHT_MEMBER and m.isfile():
                        copyright = tf.extractfile(m).read().decode("utf-8")
                    elif member == SF2_MEMBER and m.isfile():
                        sf2 = _temp(cache, temps)
                        src = tf.extractfile(m)
                        with open(sf2, "wb") as out:
                            while chunk := src.read(CHUNK):
                                out.write(chunk)
            if sf2 is None or copyright is None:
                raise SetupError(f"the package has no {SF2_NAME} or no copyright file")
            return sf2, copyright
    raise SetupError("the package has no data archive")


def _licenses(url: str, sha256: str, copyright: str) -> str:
    return f"""{SF2_NAME} — third-party sound asset used by gentle-stream (not distributed with it)

Asset:      FluidR3_GM General MIDI soundfont ({SF2_NAME})
Source:     {url}
SHA-256:    {sha256} (Debian package as downloaded)
Copyright:  Copyright (c) 2000-2002, 2008 Frank Wen; Copyright (c) 2008 Toby Smithe
License:    MIT (Debian copyright file reproduced in full below)

----------------------------------------------------------------------------------------------------------------
{copyright}"""


# ---------------------------------------------------------------- FluidSynth
def fluidsynth_module():
    """pyfluidsynth, or None. Finds Homebrew's libfluidsynth on macOS even when HOMEBREW_PREFIX is not exported
    (pyfluidsynth only looks there through that variable when `ctypes.util.find_library` fails)."""
    try:
        import fluidsynth
        return fluidsynth
    except (ImportError, OSError):
        pass
    prefix = next((p for p in ("/opt/homebrew", "/usr/local") if Path(p, "lib", "libfluidsynth.dylib").exists()), None)
    if prefix is None or "HOMEBREW_PREFIX" in os.environ:
        return None
    os.environ["HOMEBREW_PREFIX"] = prefix
    try:
        import fluidsynth
        return fluidsynth
    except (ImportError, OSError):
        return None
    finally:
        del os.environ["HOMEBREW_PREFIX"]


class Renderer:
    """One FluidSynth instance with the soundfont loaded; renders isolated notes offline to mono float arrays.

    Thread-safe and shared by every engine using the same soundfont, so the 140 MB soundfont loads once.
    """

    DRUMS = 9
    BLOCK = 64  # FluidSynth's internal block: events apply at block boundaries, so every write is a multiple

    def __init__(self, fluidsynth, sf2: Path, sr: int):
        self.sr = sr
        self.synth = fluidsynth.Synth(gain=0.5, samplerate=float(sr), **{
            "synth.reverb.active": 0, "synth.chorus.active": 0, "synth.polyphony": 64})
        self.sfid = self.synth.sfload(str(sf2))
        if self.sfid < 0:
            raise OSError(f"cannot load {sf2}")
        self._write = fluidsynth._fl.fluid_synth_write_float
        self._write.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self._write.restype = ctypes.c_int
        self._programs: dict[int, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def _samples(self, seconds: float) -> np.ndarray:
        n = max(1, -(-int(seconds * self.sr) // self.BLOCK)) * self.BLOCK
        left, right = np.zeros(n, np.float32), np.zeros(n, np.float32)
        self._write(self.synth.synth, n, left.ctypes.data, 0, 1, right.ctypes.data, 0, 1)
        return (left.astype(np.float64) + right) * 0.5

    def note(self, chan: int, bank: int, program: int, key: int, vel: int, hold: float, tail: float) -> np.ndarray:
        """Press `key` for `hold` seconds, then let it ring for at most `tail` seconds."""
        with self._lock:
            if self._programs.get(chan) != (bank, program):
                self.synth.program_select(chan, self.sfid, bank, program)
                self._programs[chan] = (bank, program)
            # Some presets (modulated filters: GM 80, 81, 89, 90, 38...) carry voice state from the previous note
            # even after all_sounds_off; a fixed throw-away note first makes every render independent of history.
            self.synth.noteon(chan, 60, 64)
            self._samples(0.05)
            self.synth.noteoff(chan, 60)
            self.synth.all_sounds_off(chan)
            self._samples(0.01)
            self.synth.noteon(chan, key, vel)
            on = self._samples(hold)
            self.synth.noteoff(chan, key)
            off = self._samples(tail)
            self.synth.all_sounds_off(chan)
            self._samples(0.01)                              # flush anything left before the next note
        return np.concatenate([on, off])


@lru_cache(maxsize=4)
def load_renderer(sf2: Path | str, sr: int = 44100) -> Renderer | None:
    """The shared renderer for `sf2`, or None when FluidSynth, its binding or the file is unavailable."""
    fluidsynth = fluidsynth_module()
    if fluidsynth is None or not Path(sf2).is_file():
        return None
    try:
        return Renderer(fluidsynth, Path(sf2), sr)
    except (OSError, AttributeError):
        return None


# ---------------------------------------------------------------- instruments
def _finish(x: np.ndarray, sr: int, floor: float = 1e-4) -> np.ndarray:
    """Trim the silent end and fade the last few milliseconds so a note never ends in a click."""
    loud = np.flatnonzero(np.abs(x) > floor * max(np.max(np.abs(x)), 1e-9))
    x = x[:loud[-1] + 1 if len(loud) else 1].copy()
    k = min(len(x), int(0.01 * sr))
    x[len(x) - k:] *= np.linspace(1, 0, k)
    return x


@dataclass(frozen=True)
class Program:
    """A melodic General MIDI program for `SampleInstruments.play`. `rms` is the level a reference note (middle C,
    1 s, velocity 0.8) is scaled to; the lo-fi piano/rhodes are about 0.48, the pad 0.1 (`reference_rms`)."""

    program: int
    bank: int = 0
    velocity: tuple[int, int] = (40, 110)   # MIDI velocity for vel 0 .. 1
    tail: float = 1.0                       # release seconds rendered after the note off
    rms: float = 0.4


@dataclass(frozen=True)
class Hit:
    """A drum sound for `SampleInstruments.hit`: GM drum kit (0 Standard, 8 Room, 16 Power, 24 Electronic,
    25 TR-808, 32 Jazz, 40 Brush) and key (36 kick, 38 snare, 42 closed hat...). `rms` as in `Program`, measured
    on the first 0.5 s of the hit (lo-fi kick 0.27, brush 0.18, hat 0.12)."""

    kit: int
    key: int
    velocity: int = 100
    seconds: float = 0.5
    rms: float = 0.2


class SampleInstruments:
    """Same interface as `engine.Instruments`, voiced by FluidR3_GM (the sub bass stays a procedural sine).

    Rendered notes are cached by (instrument, key, duration bucket, velocity bucket); each instrument is scaled
    once so its level matches the procedural voice the engine's mix was balanced for. Genres play any other GM
    program or drum through `play(Program(...), ...)` and `hit(Hit(...))`, cached the same way.
    """

    # name: (bank, program, MIDI velocity range, release tail seconds)
    TONAL = {"piano": (0, 0, (40, 100), 1.2), "rhodes": (0, 4, (45, 110), 1.0),
             "nylon": (0, 24, (45, 110), 1.2), "pad": (0, 89, (50, 110), 1.5)}
    # name: (kit, key, MIDI velocity, seconds); drums on GM channel 10, kit 0 Standard, 40 Brush
    DRUM = {"kick": (0, 36, 100, 0.6), "brush": (40, 38, 70, 0.5), "rim": (0, 37, 70, 0.2), "hat": (40, 42, 60, 0.25)}
    DUR_STEP = 0.05
    VEL_STEPS = 8

    def __init__(self, renderer: Renderer, rng: np.random.Generator, sr: int = 44100, procedural=None):
        from .engine import Instruments
        self.r, self.rng, self.sr = renderer, rng, sr
        self.procedural = procedural or Instruments(rng, sr)
        self._cache: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._scale = _levels(renderer, sr)

    def _cached(self, key: tuple, make: Callable[[], np.ndarray]) -> np.ndarray:
        x = self._cache.get(key)
        if x is None:
            x = make(); x.flags.writeable = False
            self._cache[key] = x
            if len(self._cache) > 2048:
                self._cache.popitem(last=False)
        else:
            self._cache.move_to_end(key)
        return x

    def _tonal(self, name: str, m, dur: float, vel: float) -> np.ndarray:
        bank, prog, (v0, v1), tail = self.TONAL[name]
        d = max(1, round(dur / self.DUR_STEP)) * self.DUR_STEP
        vb = int(np.clip(round(vel * self.VEL_STEPS), 1, self.VEL_STEPS))
        key = int(round(m))
        return self._cached((name, key, d, vb), lambda: _finish(
            self.r.note(0, bank, prog, key, int(v0 + (v1 - v0) * vb / self.VEL_STEPS), d, tail), self.sr)
            * self._scale[name])

    def play(self, p: Program, m, dur: float, vel: float = 1.0) -> np.ndarray:
        """MIDI note `m` on program `p`, held `dur` seconds (bucketed) at velocity `vel` (0-1, bucketed)."""
        v0, v1 = p.velocity
        d = max(1, round(dur / self.DUR_STEP)) * self.DUR_STEP
        vb = int(np.clip(round(vel * self.VEL_STEPS), 1, self.VEL_STEPS))
        key = int(np.clip(round(m), 0, 127))
        return self._cached((p, key, d, vb), lambda: _finish(
            self.r.note(0, p.bank, p.program, key, int(v0 + (v1 - v0) * vb / self.VEL_STEPS), d, p.tail), self.sr)
            * _program_level(self.r, self.sr, p))

    def hit(self, h: Hit, g: float = 1.0) -> np.ndarray:
        """Drum `h` at `g` times its velocity (rounded to 0.05)."""
        g = round(g * 20) / 20
        return self._cached((h, g), lambda: _finish(
            self.r.note(Renderer.DRUMS, 128, h.kit, h.key, int(np.clip(h.velocity * g, 1, 127)), 0.05, h.seconds),
            self.sr) * _hit_level(self.r, self.sr, h))

    def piano(self, m, dur, vel=1.0):
        return self._tonal("piano", m, dur, vel)

    def rhodes(self, m, dur, vel=1.0):
        return self._tonal("rhodes", m, dur, vel)

    def nylon(self, m, dur, vel=1.0):
        return self._tonal("nylon", m, dur, vel)

    def pad(self, m, dur, vel=1.0):
        return self._tonal("pad", m, dur, vel)

    def sub(self, m, dur):
        return self.procedural.sub(m, dur)

    def _drum(self, name: str, g: float = 1.0) -> np.ndarray:
        kit, key, vel, secs = self.DRUM[name]
        return self._cached((name, g), lambda: _finish(
            self.r.note(Renderer.DRUMS, 128, kit, key, int(vel * g), 0.05, secs), self.sr) * self._scale[name])

    def kick(self):
        return self._drum("kick")

    def brush(self):
        return self._drum("brush")

    def rim(self):
        return self._drum("rim")

    def hat(self, g=1.0):
        return self._drum("hat", round(g, 2))


@lru_cache(maxsize=4)
def _levels(renderer: Renderer, sr: int) -> dict[str, float]:
    """Per-instrument gain that gives a reference note the RMS of the procedural instrument it replaces."""
    from .engine import Instruments
    proc = Instruments(np.random.default_rng(0), sr)
    rms = lambda x: float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)])))) + 1e-12
    out = {}
    for name, (bank, prog, (v0, v1), tail) in SampleInstruments.TONAL.items():
        s = _finish(renderer.note(0, bank, prog, 60, int(v0 + (v1 - v0) * 0.8), 1.0, tail), sr)
        out[name] = rms(getattr(proc, name)(60, 1.0, 0.8)) / rms(s)
    for name, (kit, key, vel, secs) in SampleInstruments.DRUM.items():
        s = _finish(renderer.note(Renderer.DRUMS, 128, kit, key, vel, 0.05, secs), sr)
        out[name] = rms(getattr(proc, name)()) / rms(s)
    return out


def _rms(x: np.ndarray, sr: int) -> float:
    return float(np.sqrt(np.mean(np.square(x[:int(0.5 * sr)])))) + 1e-12


def reference_rms(sr: int = 44100) -> dict[str, float]:
    """RMS of the procedural lo-fi voices' reference notes, the scale `Program.rms` and `Hit.rms` are set on."""
    from .engine import Instruments
    proc = Instruments(np.random.default_rng(0), sr)
    out = {name: _rms(getattr(proc, name)(60, 1.0, 0.8), sr) for name in SampleInstruments.TONAL}
    out.update({name: _rms(getattr(proc, name)(), sr) for name in SampleInstruments.DRUM})
    return out


@lru_cache(maxsize=256)
def _program_level(renderer: Renderer, sr: int, p: Program) -> float:
    v0, v1 = p.velocity
    ref = renderer.note(0, p.bank, p.program, 60, int(v0 + (v1 - v0) * 0.8), 1.0, p.tail)
    return p.rms / _rms(_finish(ref, sr), sr)


@lru_cache(maxsize=256)
def _hit_level(renderer: Renderer, sr: int, h: Hit) -> float:
    ref = renderer.note(Renderer.DRUMS, 128, h.kit, h.key, h.velocity, 0.05, h.seconds)
    return h.rms / _rms(_finish(ref, sr), sr)
