"""Recorded instruments: real CC0 / public-domain recordings played instead of FluidR3_GM where they exist.

The committed manifest (`recorded.json`) lists every file of the library: its URL and pinned SHA-256, the source it
comes from (name, licence and pinned ref in `sources`), and how it maps to General MIDI (instrument programs as
`[bank, program]`, drums as `[kit, key]` with kit `"*"` for any kit) with its root key, key and velocity range and
take, and optionally what the source's SFZ says about it: `member` (the file inside a zip or tar archive at `url`),
`offset` (frames skipped at the start), `volume` (dB), `tune` (cents) and `loop` (`[start, end]` frames of the
source file, repeated while a note is held). An instrument marked `sustain` (organs, strings, winds: notes the
player holds) loops the steady part of recordings without loop points, so a long note never ends early.
`gentle-stream setup-samples` downloads each file once (`install`), verifies it, converts it to mono 44.1 kHz 16-bit
WAV normalised to full scale under `<cache>/recorded/`, writes `SOURCES.txt` (provenance and licence per file) and
finally `index.json`, whose presence marks a complete install. Nothing is ever committed or shipped.

At run time `Layered` wraps the FluidR3_GM `samples.Renderer`: a program or drum the library covers renders from
its recordings (nearest root key, the velocity layer that holds the velocity, one of several takes), anything else
from FluidR3_GM. Without the library the engine renders exactly as before.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import urllib.parse
import zipfile
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
from functools import lru_cache
from math import gcd
from pathlib import Path
from typing import Callable

import numpy as np

from . import samples

MANIFEST = Path(__file__).with_name("recorded.json")
DIR, INDEX, SOURCES = "recorded", "index.json", "SOURCES.txt"
FILE_SR = 44100
LICENSES = frozenset({"CC0-1.0", "public-domain"})   # anything else (CC-BY, NC, ND...) is rejected
WORKERS = 8
NORM = 0.99                                          # converted files peak here; the index gain restores the level
_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")

Fetch = Callable[[str, str, Path], Path]   # (url, sha256, work dir) -> verified file inside the work dir


class ManifestError(samples.SetupError):
    """The recorded-instrument manifest is malformed or lists a source that is not CC0 / public domain."""


# ---------------------------------------------------------------- manifest
def load_manifest(path: Path | str = MANIFEST) -> dict:
    return validate(json.loads(Path(path).read_text()))


def validate(m: dict) -> dict:
    """`m` when it is a well-formed manifest; raises ManifestError otherwise."""
    def need(ok, what):
        if not ok:
            raise ManifestError(f"recorded manifest: {what}")

    def midi(v, lo=0, hi=127):
        return type(v) is int and lo <= v <= hi

    need(isinstance(m, dict) and isinstance(m.get("sources"), dict) and isinstance(m.get("instruments"), list)
         and isinstance(m.get("drums"), list), "needs `sources`, `instruments` and `drums`")
    for sid, s in m["sources"].items():
        need(_NAME.match(sid) and isinstance(s, dict), f"bad source {sid!r}")
        need(all(isinstance(s.get(k), str) for k in ("name", "url", "license", "license_url", "ref")),
             f"source {sid!r} needs name, url, license, license_url and ref")
        need(s["license"] in LICENSES, f"source {sid!r}: licence {s['license']!r} is not CC0 or public domain")
    names, covered = set(), set()
    for kind in ("instruments", "drums"):
        for g in m[kind]:
            need(isinstance(g, dict) and isinstance(g.get("name"), str) and _NAME.match(g["name"])
                 and g["name"] not in names, f"bad or repeated {kind} name {g.get('name')!r}")
            names.add(g["name"])
            need(isinstance(g.get("gm"), list) and g["gm"], f"{g['name']}: needs `gm`")
            for gm in g["gm"]:
                need(isinstance(gm, list) and len(gm) == 2, f"{g['name']}: bad gm {gm!r}")
                a, b = gm
                if kind == "instruments":
                    need(midi(a, 0, 128) and midi(b), f"{g['name']}: bad [bank, program] {gm!r}")
                else:
                    need((a == "*" or midi(a)) and midi(b), f"{g['name']}: bad [kit, key] {gm!r}")
                need((kind, a, b) not in covered, f"{g['name']}: {gm!r} is covered twice")
                covered.add((kind, a, b))
            if kind == "instruments":
                need(type(g.get("tail")) in (int, float) and g["tail"] >= 0, f"{g['name']}: needs `tail` >= 0")
                need(type(g.get("sustain", False)) is bool, f"{g['name']}: `sustain` is true or false")
            else:
                need("sustain" not in g, f"{g['name']}: a drum is a one-shot, it has no `sustain`")
            need(isinstance(g.get("zones"), list) and g["zones"], f"{g['name']}: needs zones")
            for z in g["zones"]:
                need(isinstance(z, dict) and isinstance(z.get("url"), str) and _SHA.match(str(z.get("sha256"))),
                     f"{g['name']}: zone needs url and a lowercase hex sha256")
                need(z.get("source") in m["sources"], f"{g['name']}: unknown source {z.get('source')!r}")
                need(m["sources"][z["source"]]["ref"] != "", f"source {z['source']!r} has no pinned ref")
                need(all(midi(z.get(k)) for k in ("root", "lokey", "hikey")) and z["lokey"] <= z["hikey"],
                     f"{g['name']}: bad key range in {z['url']}")
                need(all(midi(z.get(k), 1) for k in ("lovel", "hivel")) and z["lovel"] <= z["hivel"],
                     f"{g['name']}: bad velocity range in {z['url']}")
                need(midi(z.get("take"), 0, 1 << 16), f"{g['name']}: bad take in {z['url']}")
                need(isinstance(z.get("member", ""), str), f"{g['name']}: bad member in {z['url']}")
                need(midi(z.get("offset", 0), 0, 1 << 31), f"{g['name']}: bad offset in {z['url']}")
                need(type(z.get("volume", 0)) in (int, float) and -60 <= z.get("volume", 0) <= 60,
                     f"{g['name']}: bad volume in {z['url']}")
                need(midi(z.get("tune", 0), -1200, 1200), f"{g['name']}: bad tune in {z['url']}")
                loop = z.get("loop", [0, 1])
                need(isinstance(loop, list) and len(loop) == 2 and all(midi(v, 0, 1 << 31) for v in loop)
                     and loop[0] < loop[1], f"{g['name']}: bad loop in {z['url']}")
    return m


def _digest(m: dict) -> str:
    return hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- install
def installed(cache: Path, m: dict | None = None) -> Path | None:
    """The installed library, or None when it is missing, incomplete or (with `m`) from another manifest."""
    root = Path(cache) / DIR
    try:
        index = json.loads((root / INDEX).read_text())
    except (OSError, ValueError):
        return None
    return root if m is None or index.get("manifest") == _digest(m) else None


def install(cache: Path, manifest: dict | None = None, fetch: Fetch | None = None,
            progress: Callable[[int, int | None], None] | None = None, workers: int = WORKERS) -> Path:
    """Download, verify and convert every file of `manifest` (the committed one by default) into `cache`/recorded.

    Does nothing when that manifest is already installed. Everything is built in a hidden work directory that only
    replaces `recorded/` once complete; on any failure nothing is left in the cache.
    """
    cache = Path(cache)
    m = load_manifest() if manifest is None else validate(manifest)
    if (done := installed(cache, m)) is not None:
        return done
    fetch = fetch or _fetch
    created = not cache.exists()
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=cache, prefix=".partial-recorded-"))
    old: Path | None = None
    try:
        files: dict[str, dict] = {}                  # relative wav path -> the first zone that uses it
        for kind in ("instruments", "drums"):
            for g in m[kind]:
                for z in g["zones"]:
                    files.setdefault(_file(g["name"], z), z)
        peaks = _convert_all(files, work, fetch, progress, workers)
        index = {"version": 2, "manifest": _digest(m), "sr": FILE_SR, "instruments": [], "drums": []}
        for kind in ("instruments", "drums"):
            for g in m[kind]:
                index[kind].append({"name": g["name"], "gm": g["gm"], **({"tail": g["tail"]} if "tail" in g else {}),
                                    **({"sustain": True} if g.get("sustain") else {}),
                                    "zones": [_indexed(_file(g["name"], z), z, *peaks[_file(g["name"], z)])
                                              for z in g["zones"]]})
        lines = ["# Recorded instruments used by gentle-stream (downloaded, never distributed with it).",
                 "# path\turl\tsha256\tsource\tlicense\tlicense_url"]
        for rel, z in sorted(files.items()):
            s = m["sources"][z["source"]]
            url = z["url"] + (f"#{z['member']}" if z.get("member") else "")
            lines.append("\t".join((rel, url, z["sha256"], s["name"], s["license"], s["license_url"])))
        (work / SOURCES).write_text("\n".join(lines) + "\n")
        (work / INDEX).write_text(json.dumps(index, indent=1))   # written last: its presence marks a complete install
        work.chmod(0o755)
        root = cache / DIR
        if root.exists():
            old = Path(tempfile.mkdtemp(dir=cache, prefix=".old-recorded-"))
            os.replace(root, old / DIR)
        os.replace(work, root)
        return root
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        if old is not None and not (cache / DIR).exists():
            os.replace(old / DIR, cache / DIR)
        if created and not any(cache.iterdir()):
            cache.rmdir()
        raise
    finally:
        if old is not None:
            shutil.rmtree(old, ignore_errors=True)


def _fetch(url: str, sha256: str, work: Path) -> Path:
    return samples._download(url, sha256, work, [], None)


def _file(group: str, z: dict) -> str:
    """The converted file of zone `z`: one per downloaded file, archive member and start offset."""
    name = (z.get("member") or z["url"]).rsplit("/", 1)[-1]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(urllib.parse.unquote(name)).stem)[:60]
    key = hashlib.sha256(f"{z['sha256']}|{z.get('member', '')}|{z.get('offset', 0)}".encode()).hexdigest()
    return f"{group}/{stem}-{key[:8]}.wav"


def _indexed(rel: str, z: dict, peak: float, rate: int) -> dict:
    """The index entry of zone `z`: its file, ranges, gain (undoes the normalisation, applies `volume`), tuning and
    loop in library frames from the start of the converted file."""
    out = {"file": rel, **{k: z[k] for k in ("root", "lokey", "hikey", "lovel", "hivel", "take")},
           "gain": peak * 10 ** (z.get("volume", 0) / 20), "tune": z.get("tune", 0)}
    if "loop" in z:
        start, end = (round((v - z.get("offset", 0)) * FILE_SR / rate) for v in z["loop"])
        if 0 <= start < end:
            out["loop"] = [start, end]
    return out


def _decode(raw: Path, z: dict):
    import soundfile   # setup only: the run time reads the converted WAVs with scipy
    try:
        if not z.get("member"):
            return soundfile.read(str(raw), dtype="float64", always_2d=True)
        return soundfile.read(io.BytesIO(_member(raw, z["member"])), dtype="float64", always_2d=True)
    except (RuntimeError, ValueError, TypeError, KeyError, OSError, tarfile.TarError, zipfile.BadZipFile) as e:
        raise samples.SetupError(f"cannot decode {z['url']} {z.get('member', '')}: {e}") from e


def _member(archive: Path, name: str) -> bytes:
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            return zf.read(name)
    with tarfile.open(archive) as tf:
        for m in tf:
            if m.isfile() and m.name.removeprefix("./") == name:
                return tf.extractfile(m).read()
    raise KeyError(f"no member {name}")


def _convert_all(files: dict[str, dict], work: Path, fetch: Fetch, progress, workers: int) -> dict[str, tuple]:
    """Download every distinct file once, convert each of its zones' files; (peak, source rate) per file."""
    total, done, lock = len(files), [0], threading.Lock()
    downloads: dict[tuple[str, str], list[str]] = {}
    for rel, z in files.items():
        downloads.setdefault((z["url"], z["sha256"]), []).append(rel)
    peaks: dict[str, tuple[float, int]] = {}

    def one(url: str, sha256: str, rels: list[str]) -> None:
        raw = fetch(url, sha256, work)
        try:
            for rel in rels:
                z = files[rel]
                data, rate = _decode(raw, z)
                x = _resample(data[z.get("offset", 0):].mean(axis=1), rate, FILE_SR)
                peak = float(np.max(np.abs(x))) if len(x) else 0.0
                out = work / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                from scipy.io import wavfile
                wavfile.write(out, FILE_SR, _pcm16(x / peak * NORM if peak > 0 else x))
                out.chmod(0o644)
                with lock:
                    peaks[rel] = (peak / NORM if peak > 0 else 1.0, int(rate))
                    done[0] += 1
                    if progress:
                        progress(done[0], total)
        finally:
            Path(raw).unlink(missing_ok=True)

    if not files:
        return peaks
    with ThreadPoolExecutor(max(1, min(workers, len(downloads)))) as pool:
        futures = [pool.submit(one, url, sha, rels) for (url, sha), rels in downloads.items()]
        wait(futures, return_when=FIRST_EXCEPTION)
        for f in futures:
            if f.done() and f.exception() is not None:
                pool.shutdown(wait=True, cancel_futures=True)
                raise f.exception()
    return peaks


def _resample(x: np.ndarray, rate: int, sr: int) -> np.ndarray:
    if rate == sr:
        return x
    from scipy.signal import resample_poly
    g = gcd(int(rate), int(sr))
    return resample_poly(x, sr // g, int(rate) // g)


def _pcm16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)


# ---------------------------------------------------------------- run time
class Zone:
    """One recording: its key and velocity range, root key and take, gain, tuning (cents) and optional loop
    (`[start, end]` frames at the library rate); `sustain`: a held note (organ, strings, winds) loops the steady part
    of a recording that has no loop points. The audio loads on first use."""

    def __init__(self, path: Path, sr: int, root: int, lokey: int, hikey: int, lovel: int, hivel: int, take: int,
                 gain: float = 1.0, tune: int = 0, loop: list[int] | None = None, sustain: bool = False):
        self.path, self.sr = path, sr
        self.root, self.lokey, self.hikey, self.lovel, self.hivel, self.take = root, lokey, hikey, lovel, hivel, take
        self.gain, self.tune, self.loop, self.sustain = gain, tune, loop, sustain
        self._audio: np.ndarray | None = None
        self._steady: tuple[int, int] | None | bool = False      # False: not looked for yet

    @property
    def audio(self) -> np.ndarray:
        """Float samples at the library rate (read once; recordings never touched are never loaded)."""
        if self._audio is None:
            from scipy.io import wavfile
            rate, pcm = wavfile.read(self.path)
            x = _resample(pcm.astype(np.float64) / 32768, rate, self.sr).astype(np.float32)
            x.flags.writeable = False
            self._audio = x
        return self._audio

    def sustained(self, n: int) -> np.ndarray:
        """At least `n` frames: the recording, its loop repeated as long as needed (the last 10 ms of each pass
        crossfaded into the audio just before the loop start, so a seam never clicks even after resampling); the
        plain recording when it has no loop. A `sustain` zone without loop points loops its steady part instead,
        from the end of the attack to the player's release, with a 150 ms equal-power crossfade (its seam is not
        on a waveform cycle)."""
        x = self.audio
        if self.loop is not None:
            if n <= len(x):
                return x
            ratio = self.sr / FILE_SR
            start, end = (min(len(x), round(v * ratio)) for v in self.loop)
            fade, power = min(int(0.01 * self.sr), (end - start) // 4, start), False
        else:
            if not self.sustain or (steady := self.steady()) is None or n <= steady[1]:
                return x
            start, end = steady
            fade, power = min(int(0.15 * self.sr), (end - start) // 3, start), True
        if end - start < 2 or fade < 1:
            return x
        body = x[start:end].astype(np.float64)
        r = np.linspace(0, 1, fade + 1)[1:]
        a, b = (np.cos(r * np.pi / 2), np.sin(r * np.pi / 2)) if power else (1 - r, r)
        body[-fade:] = body[-fade:] * a + x[start - fade:start] * b
        reps = -(-(n - end) // len(body)) + 1
        head = x[:end].astype(np.float64)
        head[-fade:] = body[-fade:]
        return np.concatenate([head, np.tile(body, reps)])

    def steady(self) -> tuple[int, int] | None:
        """`[start, end)` frames of the recording's steady part: 200 ms after its level first reaches 3 dB under
        the sustained level, to the last 50 ms frame still there; None when that is shorter than a second."""
        if self._steady is False:
            x, hop = self.audio, max(1, self.sr // 20)
            frames = len(x) // hop
            self._steady = None
            if frames >= 20:
                env = np.sqrt(np.mean(np.square(x[:frames * hop].reshape(frames, hop), dtype=np.float64), axis=1))
                level = np.median(env[env >= 0.25 * env.max()])
                at = np.flatnonzero(env >= 0.7 * level)
                start, end = (at[0] + 4) * hop, at[-1] * hop
                if end - start >= self.sr:
                    self._steady = (int(start), int(end))
        return self._steady


class Sound:
    """One instrument (`tail` seconds of release after the note off) or drum (`tail` None: a one-shot)."""

    def __init__(self, name: str, zones: list[Zone], tail: float | None):
        self.name, self.zones, self.tail = name, zones, tail

    def layer(self, key: int, vel: int) -> list[Zone]:
        """The takes of the zone that plays `key` at `vel`: inside its key and velocity range when one is, nearest
        root key first, ordered by take."""
        pool = ([z for z in self.zones if z.lokey <= key <= z.hikey and z.lovel <= vel <= z.hivel]
                or [z for z in self.zones if z.lovel <= vel <= z.hivel] or self.zones)
        best = min(pool, key=lambda z: (abs(key - z.root), z.root, z.lovel))
        return sorted((z for z in pool if (z.root, z.lovel, z.hivel) == (best.root, best.lovel, best.hivel)),
                      key=lambda z: z.take)

    def reference_key(self) -> int:
        """Middle C when it lies between the lowest and highest recorded root, else the middle recorded root: the
        key levels are measured on, played from a recording rather than stretched past the end of the range."""
        roots = sorted(z.root for z in self.zones)
        return 60 if roots[0] <= 60 <= roots[-1] else roots[len(roots) // 2]

    def render(self, key: int, vel: int, hold: float, take: int | None, sr: int) -> np.ndarray:
        takes = self.layer(key, vel)
        z = takes[(take or 0) % len(takes)]
        gain = (vel / 127) ** 2 * z.gain          # SFZ's default velocity curve over layers levelled by `volume`
        if self.tail is None:
            return z.audio.astype(np.float64) * gain
        ratio = 2 ** ((key - z.root) / 12 + z.tune / 1200)
        want = int((hold + self.tail) * sr)
        audio = z.sustained(int(want * ratio) + 2)
        n = min(want, int((len(audio) - 1) / ratio) + 1)
        x = audio[:n].astype(np.float64) if ratio == 1 else np.interp(
            np.arange(n) * ratio, np.arange(len(audio)), audio)
        h, k = int(hold * sr), int(self.tail * sr)
        if h < n:
            fade = np.linspace(1, 0, k + 1)[1:] if k else np.zeros(0)
            x[h:h + k] *= fade[:n - h]
            x[h + k:] = 0
        return x * gain


class Library:
    """The installed recordings indexed by GM program and drum, at the engine's sample rate."""

    def __init__(self, root: Path, index: dict, sr: int):
        self.root, self.sr = root, sr
        self.programs: dict[tuple[int, int], Sound] = {}
        self.drums: dict[tuple[int | None, int], Sound] = {}
        for kind, table in (("instruments", self.programs), ("drums", self.drums)):
            for g in index.get(kind, []):
                zones = [Zone(root / z["file"], sr, z["root"], z["lokey"], z["hikey"], z["lovel"], z["hivel"],
                              z["take"], z.get("gain", 1.0), z.get("tune", 0), z.get("loop"), g.get("sustain", False))
                         for z in g["zones"]]
                sound = Sound(g["name"], zones, g["tail"] if kind == "instruments" else None)
                for a, b in g["gm"]:
                    table[(None if a == "*" else a, b)] = sound

    def __bool__(self) -> bool:
        return bool(self.programs or self.drums)

    def sound(self, chan: int, bank: int, program: int, key: int) -> Sound | None:
        """The recorded sound for a FluidSynth-style note (channel 9: drums, `program` is the kit), or None."""
        if chan == samples.Renderer.DRUMS:
            return self.drums.get((program, key)) or self.drums.get((None, key))
        return self.programs.get((bank, program))


def load_library(cache: Path | None = None, sr: int = 44100) -> Library | None:
    """The installed library at rate `sr`, or None when it is not installed or covers nothing."""
    root = (samples.cache_dir() if cache is None else Path(cache)) / DIR
    try:
        stamp = (root / INDEX).stat().st_mtime_ns
    except OSError:
        return None
    return _load(str(root), stamp, sr)


@lru_cache(maxsize=4)
def _load(root: str, stamp: int, sr: int) -> Library | None:
    try:
        lib = Library(Path(root), json.loads((Path(root) / INDEX).read_text()), sr)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return lib or None


class Layered:
    """`samples.Renderer` with recordings first: same `note` signature and `sr`, plus an optional `take`.

    Equal (and hashing equal) for the same renderer and library, so the level and note caches keyed on the renderer
    are shared by every engine.
    """

    DRUMS = samples.Renderer.DRUMS

    def __init__(self, renderer, library: Library):
        if library.sr != renderer.sr:
            raise ValueError(f"library at {library.sr} Hz, renderer at {renderer.sr} Hz")
        self.renderer, self.library, self.sr = renderer, library, renderer.sr

    def __eq__(self, other) -> bool:
        return (isinstance(other, Layered) and other.renderer is self.renderer
                and other.library is self.library)

    def __hash__(self) -> int:
        return hash((id(self.renderer), id(self.library)))

    def takes(self, chan: int, bank: int, program: int, key: int, vel: int) -> int:
        """How many takes the recording of this note has (0: not recorded, FluidR3_GM plays it)."""
        s = self.library.sound(chan, bank, program, key)
        return 0 if s is None else len(s.layer(key, vel))

    def reference_key(self, chan: int, bank: int, program: int) -> int:
        """The key a program's level is measured on: inside its recorded range, middle C for FluidR3_GM."""
        s = self.library.sound(chan, bank, program, 60)
        return 60 if s is None else s.reference_key()

    def note(self, chan: int, bank: int, program: int, key: int, vel: int, hold: float, tail: float,
             take: int | None = None) -> np.ndarray:
        s = self.library.sound(chan, bank, program, key)
        if s is None:
            return self.renderer.note(chan, bank, program, key, vel, hold, tail)
        return s.render(key, vel, hold, take, self.sr)
