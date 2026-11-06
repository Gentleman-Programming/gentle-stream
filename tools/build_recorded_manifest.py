"""Build `src/gentle_stream/recorded.json`, the manifest of the recorded CC0 / public-domain instrument library.

    uv run python tools/build_recorded_manifest.py [--cache DIR] [--out PATH] [--workers N]

Every source is pinned (a git commit, or an archive checked against its SHA-256), its licence text is read at that
pin and must be CC0 or public domain, and each instrument is mapped from the source's own SFZ (sample, key and
velocity ranges, root key, round robins, start offset, volume and tuning) instead of from file names. Large sources
are thinned (sustains only, a few velocity layers and takes, fewer sampled notes) so the whole library stays a
reasonable download. Every listed file is downloaded once to compute its SHA-256 and size into a work cache outside
the repository (default `~/.cache/gentle-stream-build`), so a re-run only fetches what changed. Sources and
instruments that cannot be used are reported with the reason and left out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "gentle_stream" / "recorded.json"
CACHE = Path.home() / ".cache" / "gentle-stream-build"


# ---------------------------------------------------------------- SFZ
_NOTE = re.compile(r"([a-gA-G])([#b]?)(-?\d+)\Z")
_OPCODE = re.compile(r"([A-Za-z0-9_]+)=(.*?)(?=\s+[A-Za-z0-9_]+=|\s*<|\s*$)")
_HEADER = re.compile(r"<(\w+)>")
_LEVELS = ("global", "master", "group", "region")


def note_number(value) -> int:
    """A MIDI key from an SFZ key value: a number or a note name, middle C being c4 (60)."""
    s = str(value).strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    m = _NOTE.match(s)
    if not m:
        raise ValueError(f"not a key: {value!r}")
    return (int(m.group(3)) + 1) * 12 + "c d ef g a b".index(m.group(1).lower()) + {"#": 1, "b": -1, "": 0}[m.group(2)]


def parse_sfz(text: str, base: str = "", include: Callable[[str], str] | None = None) -> list[dict[str, str]]:
    """Every <region> of an SFZ as one dict: the opcodes it inherits from <global>, <master> and <group> merged
    under its own, `#define` variables substituted, `#include` files read through `include(path)`, comments
    dropped. `path` is the sample's repository path: <control> default_path + sample, relative to `base` (the SFZ's
    directory), with Windows separators normalised."""
    defines: dict[str, str] = {}
    lines: list[str] = []

    def expand(src: str) -> None:
        for line in re.sub(r"/\*.*?\*/", " ", src, flags=re.S).splitlines():
            line = re.sub(r"(^|\s)//.*", "", line)
            if m := re.match(r"\s*#define\s+(\$\w+)\s+(.*?)\s*$", line):
                defines[m.group(1)] = m.group(2)
            elif m := re.match(r'\s*#include\s+"([^"]+)"', line):
                if include is not None:
                    expand(include(m.group(1)))
            else:
                for k in sorted(defines, key=len, reverse=True):
                    line = line.replace(k, defines[k])
                lines.append(line)

    expand(text)
    control: dict[str, str] = {}
    levels: dict[str, dict] = {lvl: {} for lvl in _LEVELS}
    found: list[tuple[dict, ...]] = []
    current: dict | None = None
    for line in lines:
        pos = 0
        for h in _HEADER.finditer(line):
            _opcodes(line[pos:h.start()], current)
            name = h.group(1).lower()
            if name == "control":
                current = control
            elif name in _LEVELS:
                for lvl in _LEVELS[_LEVELS.index(name):]:
                    levels[lvl] = {}
                current = levels[name]
                if name == "region":
                    found.append((levels["global"], levels["master"], levels["group"], current, dict(control)))
            else:
                current = None                         # <curve>, <effect>, <midi>...: not needed
            pos = h.end()
        _opcodes(line[pos:], current)
    out = []
    for g, m, grp, reg, ctl in found:
        r = {**g, **m, **grp, **reg}
        if "sample" in r:
            path = (ctl.get("default_path", "") + r["sample"]).replace("\\", "/")
            r["path"] = posixpath.normpath(posixpath.join(base, path))
        out.append(r)
    return out


def _opcodes(chunk: str, into: dict | None) -> None:
    if into is not None:
        for k, v in _OPCODE.findall(chunk):
            into[k] = v.strip()


# ---------------------------------------------------------------- zones
def zones_from(regions: list[dict[str, str]], keep: Callable[[dict], bool] = lambda r: True) -> list[dict]:
    """The zones (path, root, key and velocity range, offset, volume dB, tune cents, loop, round-robin order) of the
    regions that sound on a plain note on with the sustain pedal up and pass `keep`; release triggers, pedal-down
    and sample-less regions are left out."""
    out = []
    for r in regions:
        if ("path" not in r or r.get("trigger", "attack") != "attack" or r["sample"].startswith("*")
                or int(r.get("locc64", 0)) > 0 or not keep(r)):
            continue
        if "key" in r:
            lokey = hikey = root = note_number(r["key"])
        else:
            lokey, hikey = note_number(r.get("lokey", 0)), note_number(r.get("hikey", 127))
            root = note_number(r.get("pitch_keycenter", 60))
        root -= int(float(r.get("transpose", 0)))
        z = {"path": r["path"], "root": root, "lokey": lokey, "hikey": hikey,
             "lovel": max(1, int(r.get("lovel", 1))), "hivel": int(r.get("hivel", 127)),
             "offset": int(float(r.get("offset", 0))), "volume": float(r.get("volume", 0)),
             "tune": int(round(float(r.get("tune", 0)))),
             "order": (int(r.get("seq_position", 1)), float(r.get("lorand", 0)), r["path"])}
        if r.get("loop_mode") in ("loop_continuous", "loop_sustain") and "loop_end" in r:
            z["loop"] = [int(r.get("loop_start", 0)), int(r["loop_end"])]
        if 0 <= lokey <= hikey <= 127 and 1 <= z["lovel"] <= z["hivel"] <= 127 and 0 <= root <= 127:
            out.append(z)
    return out


def window(zones: list[dict], lo: int, hi: int) -> list[dict]:
    """The zones whose root lies in keys lo..hi, their key ranges clipped to it (to split one instrument across
    several sources)."""
    return [{**z, "lokey": max(z["lokey"], lo), "hikey": min(z["hikey"], hi)} for z in zones if lo <= z["root"] <= hi]


def thin(zones: list[dict], layers: int = 3, takes: int = 2, every: int = 1) -> list[dict]:
    """At most `layers` velocity layers (spread from softest to loudest) and `takes` round robins per sampled note,
    and one sampled note in `every` (the kept notes' key ranges grow to meet halfway). Layers are re-ranged to cover
    velocity 1-127 with no gap; takes are numbered from 0 in round-robin order."""
    roots = sorted({z["root"] for z in zones})
    kept = roots[::every]
    if roots[-1] not in kept:
        kept.append(roots[-1])
    span = {}
    if every > 1:
        lo_all, hi_all = min(z["lokey"] for z in zones), max(z["hikey"] for z in zones)
        for i, r in enumerate(kept):
            lo = lo_all if i == 0 else (kept[i - 1] + r) // 2 + 1
            hi = hi_all if i == len(kept) - 1 else (r + kept[i + 1]) // 2
            span[r] = (min(lo, r), max(hi, r))
    notes: dict[tuple, list[dict]] = defaultdict(list)
    for z in zones:
        if z["root"] in kept:
            notes[(z["root"],) if every > 1 else (z["root"], z["lokey"], z["hikey"])].append(z)
    out = []
    for group in notes.values():
        ranges = sorted({(z["lovel"], z["hivel"]) for z in group}, key=lambda v: (v[1], v[0]))
        if len(ranges) > layers:
            ranges = [ranges[round(i * (len(ranges) - 1) / max(1, layers - 1))] for i in range(layers)] \
                if layers > 1 else ranges[-1:]
        new = {v: (1 if i == 0 else ranges[i - 1][1] + 1, 127 if i == len(ranges) - 1 else v[1])
               for i, v in enumerate(ranges)}
        cells: dict[tuple, list[dict]] = defaultdict(list)
        for z in group:
            if (z["lovel"], z["hivel"]) in new:
                cells[(z["lovel"], z["hivel"])].append(z)
        for v, cell in cells.items():
            for take, z in enumerate(sorted(cell, key=lambda z: z["order"])[:takes]):
                keys = span.get(z["root"], (z["lokey"], z["hikey"]))
                out.append({**z, "lokey": keys[0], "hikey": keys[1], "lovel": new[v][0], "hivel": new[v][1],
                            "take": take})
    return sorted(out, key=lambda z: (z["root"], z["lokey"], z["lovel"], z["take"]))


# ---------------------------------------------------------------- licences
_REJECT = ("attribution 4.0", "attribution 3.0", "noncommercial", "non-commercial", "noderivatives", "no derivatives",
           "sharealike", "share alike", "gnu general public license", "gnu lesser general public")


def license_of(text: str) -> str | None:
    """"CC0-1.0" or "public-domain" when the licence text dedicates the work to the public domain; None for any
    other licence (attribution, share-alike, non-commercial, no-derivatives, GPL...) or an unknown text."""
    t = " ".join(text.lower().split())
    if any(bad in t for bad in _REJECT):
        return None
    if "cc0 1.0 universal" in t or "publicdomain/zero/1.0" in t or "cc0 public domain dedication" in t:
        return "CC0-1.0"
    if "public domain" in t:
        return "public-domain"
    return None


# ---------------------------------------------------------------- fetching
def _get(url: str, tries: int = 4) -> bytes:
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "gentle-stream"}),
                                        timeout=120) as r:
                return r.read()
        except OSError:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
    raise AssertionError


class GitRepo:
    """A GitHub repository at a pinned commit, read through a blobless partial clone (no API calls)."""

    def __init__(self, slug: str, ref: str, cache: Path):
        self.slug, self.ref, self.cache = slug, ref, cache
        self.dir = cache / "git" / slug.replace("/", "__")
        if not (self.dir / ".git").exists():
            subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout",
                            f"https://github.com/{slug}.git", str(self.dir)], check=True, env=_GIT_ENV)
        if self._git("cat-file", "-t", ref, check=False).strip() != "commit":
            self._git("fetch", "-q", "origin", ref)
        self._blobs: dict[str, str] | None = None

    def _git(self, *args, check=True) -> str:
        p = subprocess.run(["git", "-C", str(self.dir), *args], capture_output=True, env=_GIT_ENV)
        if check and p.returncode:
            raise RuntimeError(f"git {' '.join(args)}: {p.stderr.decode(errors='replace')}")
        return p.stdout.decode("utf-8", errors="replace")

    def blobs(self) -> dict[str, str]:
        """path -> blob id of every file at the pinned commit."""
        if self._blobs is None:
            self._blobs = {}
            for line in self._git("ls-tree", "-r", "-z", self.ref).split("\0"):
                if line:
                    meta, path = line.split("\t", 1)
                    self._blobs[path] = meta.split()[2]
        return self._blobs

    def text(self, path: str) -> str:
        return self._git("show", f"{self.ref}:{path}")

    def url(self, path: str) -> str:
        return f"https://raw.githubusercontent.com/{self.slug}/{self.ref}/{urllib.parse.quote(path, safe='/')}"

    def fetch(self, path: str) -> bytes:
        local = self.cache / "files" / self.slug.replace("/", "__") / self.ref / path
        if not local.is_file():
            data = _get(self.url(path))
            local.parent.mkdir(parents=True, exist_ok=True)
            local.with_suffix(local.suffix + ".part").write_bytes(data)
            os.replace(local.with_suffix(local.suffix + ".part"), local)
        return local.read_bytes()


_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


class Archive:
    """A zip or tar archive pinned by SHA-256 and downloaded once; zones point at its members."""

    def __init__(self, url: str, sha256: str, cache: Path):
        local = cache / "archives" / f"{sha256[:16]}-{url.rsplit('/', 1)[-1]}"
        if not local.is_file():
            data = _get(url)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
        data = local.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise RuntimeError(f"{url}: SHA-256 {hashlib.sha256(data).hexdigest()}, expected {sha256}")
        self.size, self._members = len(data), _read_archive(local)

    def blobs(self) -> dict[str, str]:
        return {name: hashlib.sha1(data).hexdigest() for name, data in self._members.items()}

    def text(self, path: str) -> str:
        return self._members[path].decode("utf-8", errors="replace")

    def fetch(self, path: str) -> bytes:
        return self._members[path]


def _read_archive(path: Path) -> dict[str, bytes]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return {i.filename: z.read(i) for i in z.infolist() if not i.is_dir()}
    with tarfile.open(path) as t:
        return {m.name.removeprefix("./"): t.extractfile(m).read() for m in t.getmembers() if m.isfile()}


# ---------------------------------------------------------------- the library
@dataclass(frozen=True)
class Source:
    """A pinned source: a GitHub repository at commit `ref` (`sfz_ref`: the commit whose SFZ files map it, when
    they live on another branch; samples are only used where that branch has the same file), or an archive."""

    name: str
    home: str
    license_path: str
    repo: str = ""
    ref: str = ""
    sfz_ref: str = ""
    archive: str = ""
    sha256: str = ""
    tag: str = ""          # release tag of an archive, its pinned ref (else its SHA-256 is)

    @property
    def pin(self) -> str:
        return self.ref or self.tag or f"sha256:{self.sha256}"

    @property
    def license_url(self) -> str:
        if self.repo:
            return f"https://github.com/{self.repo}/blob/{self.ref}/{urllib.parse.quote(self.license_path)}"
        return f"{self.archive}#{self.license_path}"


@dataclass(frozen=True)
class Part:
    """One SFZ (`keys` window of it) feeding an instrument, thinned to `layers`, `takes` and one note in `every`."""

    sfz: str
    keys: tuple[int, int] = (0, 127)
    layers: int = 3
    takes: int = 2
    every: int = 1
    keep: Callable[[dict], bool] = field(default=lambda r: True, compare=False)


@dataclass(frozen=True)
class Instrument:
    name: str
    source: str
    gm: tuple                     # (bank, program) pairs, or (kit, key) pairs for a drum
    parts: tuple[Part, ...]
    tail: float = 1.0             # release seconds after the note off (instruments only)
    sustain: bool = False         # held by the player (organ, strings, winds): loop the steady part while held
    key: int | None = None        # drums: the SFZ key whose regions are the drum


def _left(r: dict) -> bool:
    return float(r.get("pan", 0)) <= 0          # stereo pairs as two panned mono files: keep the left one


def _gm(programs, bank=0):
    return tuple((bank, p) for p in programs)


def _kits(keys, kits=(0, 8, 16)):
    return tuple((k, key) for k in kits for key in keys)


VCSL_REF, VCSL_SFZ = "c1ea7bcc3c7309650ab0da9d15c9cd1fbc4a4c7e", "dfcf4a4918771eee884b96ad4493de82ef84daf6"
VSCO_REF = "6dd651d55dde97fd4028699be9d4481f26917891"
ZENVOID = "https://freepats.zenvoid.org/Organ"

SOURCES: dict[str, Source] = {
    "vcsl": Source("Versilian Community Sample Library (VCSL)", "https://github.com/sgossner/VCSL", "LICENSE",
                   repo="sgossner/VCSL", ref=VCSL_REF, sfz_ref=VCSL_SFZ),
    "vsco2ce": Source("Versilian Studios Chamber Orchestra 2 Community Edition (VSCO 2 CE)",
                      "https://github.com/sgossner/VSCO-2-CE", "LICENSE", repo="sgossner/VSCO-2-CE", ref=VSCO_REF),
    "fp-bass-yr": Source("FreePats Electric Bass YR", "https://github.com/freepats/electric-bass-YR", "LICENSE.txt",
                         repo="freepats/electric-bass-YR", ref="8dcb7ea9116f417273ef8c030d15e7b3aa654301"),
    "fp-guitar-clean": Source("FreePats Electric Guitar FSBS (clean)",
                              "https://github.com/freepats/electric-guitar-FSBS-clean", "LICENSE.txt",
                              repo="freepats/electric-guitar-FSBS-clean",
                              ref="192cf0d9bf2c4ba6ead8e3524ba3f78818e4fe91"),
    "fp-guitar-jazz": Source("FreePats Electric Guitar FSBS (jazz)",
                             "https://github.com/freepats/electric-guitar-FSBS-jazz", "LICENSE.txt",
                             repo="freepats/electric-guitar-FSBS-jazz", ref="9a5430a220725ee26f272f62de1381de8da982ed"),
    "fp-guitar-dist2": Source("FreePats Electric Guitar FSBS (dist2)",
                              "https://github.com/freepats/electric-guitar-FSBS-dist2", "LICENSE.txt",
                              repo="freepats/electric-guitar-FSBS-dist2",
                              ref="21261b8bcb02d1cbf52dc02f1b1636df52d4a947"),
    "fp-nylon": Source("FreePats Spanish Classical Guitar", "https://github.com/freepats/spanish-classical-guitar",
                       "LICENSE", archive="https://github.com/freepats/spanish-classical-guitar/releases/download/"
                       "v1.0.0/SpanishClassicalGuitar-20190618.zip", tag="v1.0.0",
                       sha256="173f775222991061291767724d57c7c21242a3c3254407b266aabd548df23dbb"),
    "fp-accordion": Source("FreePats Button Accordion HN", "https://github.com/freepats/button-accordion-HN",
                           "LICENSE.txt", repo="freepats/button-accordion-HN",
                           ref="d70d16456fd99305d1c24c612b205ab38846eb0f"),
    "fp-world-perc": Source("FreePats World Percussion", "https://github.com/freepats/world-percussion",
                            "LICENSE.txt", repo="freepats/world-percussion",
                            ref="e54eb2912a0d6d4444ab205d52f778e27da0fc96"),
    "fp-timpani": Source("FreePats Timpani", "https://github.com/freepats/timpani", "LICENSE.txt",
                         repo="freepats/timpani", ref="21e505407fc6c43bdaa801ce57ff6a3a16dbe8b0"),
    "fp-drawbar-organ": Source("FreePats Drawbar Organ Emulation", f"{ZENVOID}/electric-organ.html",
                               "DrawbarOrganEmulation-SFZ-20190712/readme.txt",
                               archive=f"{ZENVOID}/DrawbarOrganEmulation/DrawbarOrganEmulation-SFZ-20190712.tar.xz",
                               sha256="e2da18b0a4d13be7020037e18e4a719387433357e7603d0773990e794dcf5d0f"),
    "fp-percussive-organ": Source("FreePats Percussive Organ Emulation", f"{ZENVOID}/electric-organ.html",
                                  "PercussiveOrganEmulation-SFZ-20190715/readme.txt",
                                  archive=f"{ZENVOID}/PercussiveOrganEmulation/"
                                  "PercussiveOrganEmulation-SFZ-20190715.tar.xz",
                                  sha256="c4841f2e7f352692cf14a85a96bd1f40a9ea7a9e2a9ce3512606885d000978c8"),
    "fp-rock-organ": Source("FreePats Rock Organ Emulation", f"{ZENVOID}/electric-organ.html",
                            "RockOrganEmulation-SFZ-20190715/readme.txt",
                            archive=f"{ZENVOID}/RockOrganEmulation/RockOrganEmulation-SFZ-20190715.tar.xz",
                            sha256="07321d9498dc31982c44ea9483919df3f426e3d392a15bb2947b0e3fe247f718"),
    # checked at their pins and rejected by the licence filter (kept here so every run re-checks them)
    "fp-muldjordkit": Source("FreePats MuldjordKit", "https://github.com/freepats/muldjordkit", "LICENSE.txt",
                             repo="freepats/muldjordkit", ref="719fe72bc6693b94f1229674e202881145ab44ed"),
    "fp-colombo-adk": Source("FreePats ColomboADK", "https://github.com/freepats/colomboADK", "LICENSE.txt",
                             repo="freepats/colomboADK", ref="7aad6f18af3bc6c9927db3f5ac8379e652b14f94"),
}
# left out without download: no verifiable CC0 pin, or a non-CC0 licence at the origin
EXCLUDED = {
    "Meta Open Sound piano / nylon guitar": "no pinned origin repository or release could be verified",
    "Karoryfer Swirly / Unruly drums": "size, and the licence is not verified at the origin",
    "dsmolken double bass": "earlier releases are CC-BY, not CC0",
}

VC, VS = "Chordophones/Zithers/", "Membranophones/Struck Membranophones/"
ID = "Idiophones/Struck Idiophones/"
INSTRUMENTS: tuple[Instrument, ...] = (
    # keys
    Instrument("grand-piano", "vcsl", _gm((0, 1, 2)), (Part(VC + "Grand Piano, Steinway B.sfz", every=3),),
               tail=1.0),
    Instrument("drawbar-organ", "fp-drawbar-organ", _gm((16,)), (Part(
        "DrawbarOrganEmulation-SFZ-20190712/DrawbarOrganEmulation-20190712.sfz"),), tail=0.3, sustain=True),
    Instrument("percussive-organ", "fp-percussive-organ", _gm((17,)), (Part(
        "PercussiveOrganEmulation-SFZ-20190715/PercussiveOrganEmulation-20190715.sfz", keep=_left),), tail=0.3,
               sustain=True),
    Instrument("rock-organ", "fp-rock-organ", _gm((18,)), (Part(
        "RockOrganEmulation-SFZ-20190715/RockOrganEmulation-20190715.sfz", keep=_left),), tail=0.3, sustain=True),
    Instrument("church-organ", "vcsl", _gm((19,)), (Part("Aerophones/Edge-blown Aerophones/Pipe Organ - Loud.sfz",
                                                         every=2),), tail=1.0, sustain=True),
    Instrument("accordion", "fp-accordion", _gm((21, 23)), (Part("PRESET Button Accordion HN tuned.sfz"),), tail=0.2,
               sustain=True),
    Instrument("vibraphone", "vcsl", _gm((11,)), (Part(ID + "Vibraphone - Soft Mallets.sfz", layers=2, every=2),),
               tail=1.5),
    # guitars and basses
    Instrument("nylon-guitar", "fp-nylon", _gm((24,)), (Part("SpanishClassicalGuitar-20190618.sfz"),), tail=1.0),
    Instrument("jazz-guitar", "fp-guitar-jazz", _gm((26,)),
               (Part("EGuitarFSBS-jazz bridge 20260807.sfz", layers=2, every=2),), tail=0.6),
    Instrument("clean-guitar", "fp-guitar-clean", _gm((27,)),
               (Part("EGuitarFSBS-clean bridge 20260807.sfz", layers=2, every=2),), tail=0.6),
    Instrument("distortion-guitar", "fp-guitar-dist2", _gm((29, 30)),
               (Part("EGuitarFSBS-dist2 bridge 20220911.sfz", layers=2, every=2),), tail=0.4),
    Instrument("finger-bass", "fp-bass-yr", _gm((33,)), (Part("FingerBassYR 20190930.sfz"),), tail=0.3),
    Instrument("pick-bass", "fp-bass-yr", _gm((34,)), (Part("PickedBassYR 20190930.sfz"),), tail=0.3),
    Instrument("upright-bass", "vsco2ce", _gm((32,)), (Part("ContrabassPizz.sfz", layers=2, every=2),), tail=0.3),
    # orchestra
    Instrument("violin", "vsco2ce", _gm((40,)), (Part("SViolinVib.sfz", layers=2, every=2),), tail=0.5, sustain=True),
    Instrument("viola", "vsco2ce", _gm((41,)), (Part("ViolaEnsSusVib.sfz", layers=2, every=2),), tail=0.5,
               sustain=True),
    Instrument("cello", "vsco2ce", _gm((42,)), (Part("CelloEnsSusVib.sfz", layers=2, every=2),), tail=0.5,
               sustain=True),
    Instrument("contrabass", "vsco2ce", _gm((43,)), (Part("ContrabassSusVB.sfz", layers=2, every=2),), tail=0.5,
               sustain=True),
    Instrument("strings", "vsco2ce", _gm((48, 49)), (Part("CelloEnsSusVib.sfz", (0, 54), layers=2, every=2),
                                                    Part("ViolinEnsSusVib.sfz", (55, 127), layers=2, every=2)),
               tail=0.8, sustain=True),
    Instrument("harp", "vcsl", _gm((46,)), (Part("Chordophones/Composite Chordophones/Concert Harp.sfz", layers=2,
                                                 every=2),), tail=1.5),
    Instrument("timpani", "fp-timpani", _gm((47,)), (Part("Timpani 20240809.sfz"),), tail=1.5),
    Instrument("trumpet", "vsco2ce", _gm((56,)), (Part("TrumpetSus.sfz", layers=2, every=2),), tail=0.4, sustain=True),
    Instrument("trombone", "vsco2ce", _gm((57,)), (Part("TromboneSus.sfz", layers=2, every=2),), tail=0.4,
               sustain=True),
    Instrument("tuba", "vsco2ce", _gm((58,)), (Part("TubaSus.sfz", layers=2, every=2),), tail=0.4, sustain=True),
    Instrument("french-horn", "vsco2ce", _gm((60,)), (Part("FHornSus.sfz", layers=2, every=2),), tail=0.5,
               sustain=True),
    Instrument("brass-section", "vsco2ce", _gm((61,)), (Part("TromboneSus.sfz", (0, 52), layers=2, every=2),
                                                       Part("FHornSus.sfz", (53, 64), layers=2, every=2),
                                                       Part("TrumpetSus.sfz", (65, 127), layers=2, every=2)),
               tail=0.5, sustain=True),
    Instrument("tenor-sax", "vcsl", _gm((66,)), (Part("Aerophones/Reed Aerophones/Tenor Saxophone - Non-Vibrato.sfz",
                                                      layers=2, every=3),), tail=0.2, sustain=True),
    # drum kit (Standard, Room, Power): no CC0 kit kick or ride exists in these sources, those stay on FluidR3_GM
    Instrument("snare", "vcsl", _kits((38, 40)), (Part(VS + "Snare Drum, Modern 1.sfz"),), key=61),
    Instrument("side-stick", "vcsl", _kits((37,)), (Part(VS + "Snare Drum, Rope Tension.sfz"),), key=60),
    Instrument("hihat-closed", "vcsl", _kits((42,)), (Part(ID + "Hi-Hat Cymbal.sfz"),), key=42),
    Instrument("hihat-pedal", "vcsl", _kits((44,)), (Part(ID + "Hi-Hat Cymbal.sfz"),), key=44),
    Instrument("hihat-open", "vcsl", _kits((46,)), (Part(ID + "Hi-Hat Cymbal.sfz"),), key=46),
    # (0, 41) stays on FluidR3_GM: the orchestral genre plays it as a taiko-like drum
    Instrument("tom-low", "vcsl", _kits((43, 45)) + _kits((41,), (8, 16)), (Part(VS + "Tom 2.sfz"),), key=62),
    Instrument("tom-high", "vcsl", _kits((47, 48, 50)), (Part(VS + "Tom 1.sfz"),), key=62),
    Instrument("crash", "vcsl", _kits((49, 57)), (Part(ID + "Suspended Cymbal 2.sfz"),), key=66),
    Instrument("cowbell", "vcsl", _kits((56,)), (Part(ID + "Cowbells.sfz"),), key=62),
    # orchestra kit
    Instrument("concert-bass-drum", "vcsl", ((48, 35), (48, 36)), (Part(VS + "Bass Drum 1.sfz"),), key=60),
    Instrument("concert-snare", "vcsl", ((48, 38), (48, 40)), (Part(VS + "Snare Drum, Rope Tension.sfz"),), key=64),
    Instrument("clash-cymbals", "vcsl", ((48, 49), (48, 57)), (Part(ID + "Clash Cymbals 1.sfz"),), key=60),
    # Latin percussion, any kit
    Instrument("conga-mute", "fp-world-perc", (("*", 62),), (Part("WorldPercussion 20200905.sfz"),), key=65),
    Instrument("conga-high", "fp-world-perc", (("*", 63),), (Part("WorldPercussion 20200905.sfz"),), key=63),
    Instrument("conga-low", "fp-world-perc", (("*", 64),), (Part("WorldPercussion 20200905.sfz"),), key=64),
    Instrument("maracas", "fp-world-perc", (("*", 70),), (Part("WorldPercussion 20200905.sfz"),), key=67),
    Instrument("cabasa", "vcsl", (("*", 69),), (Part(ID + "Cabasa.sfz"),), key=60),
    Instrument("guiro-short", "vcsl", (("*", 73),), (Part(ID + "Guiro.sfz"),), key=61),
    Instrument("guiro-long", "vcsl", (("*", 74),), (Part(ID + "Guiro.sfz"),), key=62),
)


# ---------------------------------------------------------------- build
def _open(s: Source, cache: Path):
    if s.repo:
        return GitRepo(s.repo, s.ref, cache), (GitRepo(s.repo, s.sfz_ref, cache) if s.sfz_ref else None)
    return Archive(s.archive, s.sha256, cache), None


def plan(cache: Path, log=print) -> tuple[dict, list[tuple[Instrument, list[dict]]], dict]:
    """Licence-checked sources and every instrument's zones (paths, not yet hashed)."""
    opened, sources = {}, {}
    for sid, s in SOURCES.items():
        try:
            src, sfz = _open(s, cache)
            lic = license_of(src.text(s.license_path))
        except Exception as e:                                       # noqa: BLE001 - reported, never fatal
            log(f"REJECTED {sid}: cannot read the source or its licence ({e})")
            continue
        if lic is None:
            log(f"REJECTED {sid}: licence at {s.pin} ({s.license_path}) is not CC0 or public domain")
            continue
        log(f"ok       {sid}: {lic} at {s.pin}")
        opened[sid], sources[sid] = (src, sfz), {"name": s.name, "url": s.home, "license": lic,
                                                 "license_url": s.license_url, "ref": s.pin}
    for name, why in EXCLUDED.items():
        log(f"EXCLUDED {name}: {why}")
    planned = []
    for inst in INSTRUMENTS:
        if inst.source not in opened:
            log(f"skipped  {inst.name}: source {inst.source} rejected")
            continue
        src, sfz_src = opened[inst.source]
        reader = sfz_src or src
        zones = []
        for part in inst.parts:
            base = posixpath.dirname(part.sfz)
            regions = parse_sfz(reader.text(part.sfz), base, lambda p, b=base: reader.text(posixpath.join(b, p)))
            zs = zones_from(regions, part.keep)
            if inst.key is not None:
                zs = [z for z in zs if z["lokey"] <= inst.key <= z["hikey"]]
            zs = window(zs, *part.keys)
            if sfz_src is not None:                                  # SFZ from another branch: same file only
                same = [z for z in zs if src.blobs().get(z["path"]) == sfz_src.blobs().get(z["path"]) is not None]
                if len(same) < len(zs):
                    log(f"note     {inst.name}: {len(zs) - len(same)} zone(s) differ on the pinned commit, dropped")
                zs = same
            if zs:
                zones += thin(zs, part.layers, part.takes, 1 if inst.key is not None else part.every)
        if not zones:
            log(f"skipped  {inst.name}: no usable zones")
            continue
        planned.append((inst, zones))
    return sources, planned, opened


def build(cache: Path, workers: int = 8, log=print) -> tuple[dict, int]:
    """The manifest and the total download size in bytes."""
    sources, planned, opened = plan(cache, log)
    need = sorted({(inst.source, z["path"]) for inst, zones in planned for z in zones})
    log(f"hashing {len(need)} files...")

    def one(key):
        sid, path = key
        data = opened[sid][0].fetch(path)
        return key, (hashlib.sha256(data).hexdigest(), len(data))

    with ThreadPoolExecutor(workers) as pool:
        files = dict(pool.map(one, need))
    total, seen_archives = 0, set()
    for (sid, path), (_, size) in files.items():
        s = SOURCES[sid]
        if s.archive:
            if sid not in seen_archives:
                seen_archives.add(sid)
                total += opened[sid][0].size
        else:
            total += size
    m = {"sources": {sid: sources[sid] for sid in sorted({inst.source for inst, _ in planned})},
         "instruments": [], "drums": []}
    for inst, zones in planned:
        s, out = SOURCES[inst.source], []
        for z in zones:
            sha, _ = files[(inst.source, z["path"])]
            zone = {"url": s.archive or opened[inst.source][0].url(z["path"]),
                    "sha256": s.sha256 if s.archive else sha, "source": inst.source}
            if s.archive:
                zone["member"] = z["path"]
            root = inst.gm[0][1] if inst.key is not None else z["root"]
            zone.update(root=root, lokey=0 if inst.key is not None else z["lokey"],
                        hikey=127 if inst.key is not None else z["hikey"], lovel=z["lovel"], hivel=z["hivel"],
                        take=z["take"])
            for k in ("offset", "volume", "tune"):
                if z[k]:
                    zone[k] = round(z[k], 3) if k == "volume" else z[k]
            if "loop" in z and inst.key is None:
                zone["loop"] = z["loop"]
            out.append(zone)
        entry = {"name": inst.name, "gm": [list(g) for g in inst.gm]}
        if inst.key is None:
            entry["tail"] = inst.tail
        if inst.sustain:
            entry["sustain"] = True
        entry["zones"] = out
        m["drums" if inst.key is not None else "instruments"].append(entry)
    return m, total


def coverage(m: dict) -> list[str]:
    return [f"{g['name']:<18} {len(g['zones']):>3} zones  GM {', '.join(f'{a}:{b}' for a, b in g['gm'])}"
            for kind in ("instruments", "drums") for g in m[kind]]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cache", type=Path, default=CACHE, help="work cache outside the repository")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args(argv)
    m, total = build(args.cache, args.workers)
    args.out.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n")
    print("\n".join(coverage(m)))
    n = sum(len(g["zones"]) for kind in ("instruments", "drums") for g in m[kind])
    print(f"{len(m['instruments'])} instruments, {len(m['drums'])} drums, {n} zones, "
          f"download {total / 1e6:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
