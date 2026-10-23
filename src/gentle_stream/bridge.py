"""Mood interpretation through Gentle Shell.

Runs `gentle-shell -- -p --mode json` non-interactively (no session, no tools, no context files) with the model the
person chose, asks for a JSON object describing the music, and validates it into a `Style`. The JSONL events also
carry the token usage and cost of the call.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
from dataclasses import dataclass, field

from .genres import NAMES as GENRE_NAMES, REGISTRY
from .style import CHORD_INSTRUMENTS, DRUMS, KEYS, LEAD_INSTRUMENTS, MODES, Style

QUIET_FLAGS = ("--no-session", "-nt", "-ns", "-np", "-nc", "--no-mcp", "--no-themes", "--no-skill-registry")

PROMPT = """You are the composer of an endless, real-time generative music stream (lo-fi by default).
Translate the listener's request (a feeling, a concept, a place, a scene; any language) into musical parameters.
The music is always newly and originally composed: never reproduce existing melodies, songs, riffs or soundtracks,
even when the request names one; capture only its general feel (genre, tempo, colour, energy).

Listener request: {mood}

Current parameters (keep what the request does not ask to change):
{current}

Reply with ONLY one JSON object, no prose, with exactly these fields:
- "name": short evocative track title (max 4 words)
- "genre": one of {genre_names} (see Genres below)
- "key": one of {keys}
- "mode": one of {modes}
- "bpm": number 50-140
- "progression": 4 scale degrees (integers 1-7) that fit the mode and the feeling
- "chords": one of {chords}
- "lead": one of {leads}
- "drums": one of {drums}
- "energy": 0-1 (drum and re-hit intensity)
- "brightness": 0-1 (0 dark muffled tape, 1 open and clear)
- "swing": 0-0.3
- "vinyl": 0-1 (crackle)
- "space": 0-1 (room size)
- "lead_density": 0-1 (how often the melody plays)

Genres (lofi is the default: change the genre only when the request suggests another one, by naming it or by a
style, era, instrument or scene that clearly belongs to it; a request that only adjusts the music keeps the
current genre). "chords" and "lead" are tone colours each genre plays its own way:
{genres}
"""


def genre_lines() -> str:
    """One line per registered genre: description, tempo and mode hints, and what the tone colours mean."""
    lines = []
    for name, g in REGISTRY.items():
        colours = ", ".join(f"{k} = {v}" for k, v in g.voices.items())
        lines.append(f"- {name}: {g.description} (typical bpm {g.bpm[0]}-{g.bpm[1]}; modes {', '.join(g.modes)}; "
                     f"{colours})")
    return "\n".join(lines)



class InterpretError(RuntimeError):
    pass


@dataclass(frozen=True)
class Usage:
    """Tokens and cost (USD, as reported by Gentle Shell) of one or more model calls."""
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0
    cost_usd: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.input + other.input, self.output + other.output, self.cache_read + other.cache_read,
                     self.cache_write + other.cache_write, self.total + other.total, self.cost_usd + other.cost_usd)

    def format(self) -> str:
        if self.total >= 1_000_000:
            tokens = f"{self.total / 1_000_000:.3g}M"
        elif self.total >= 1000:
            tokens = f"{self.total / 1000:.1f}k"
        else:
            tokens = str(self.total)
        return f"{tokens} tokens · ${self.cost_usd:.4f}"

    @classmethod
    def from_event(cls, usage: dict) -> Usage:
        cost = usage.get("cost") or {}
        return cls(input=int(usage.get("input") or 0), output=int(usage.get("output") or 0),
                   cache_read=int(usage.get("cacheRead") or 0), cache_write=int(usage.get("cacheWrite") or 0),
                   total=int(usage.get("totalTokens") or 0), cost_usd=float(cost.get("total") or 0.0))


def parse_output(stdout: str) -> tuple[str, Usage | None]:
    """The answer text and usage of the last assistant message in `--mode json` output.

    Output that is not JSONL (an older Gentle Shell) is the answer itself, with unknown usage.
    """
    message = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return stdout, None
        if (isinstance(event, dict) and event.get("type") == "message_end"
                and isinstance(event.get("message"), dict) and event["message"].get("role") == "assistant"):
            message = event["message"]
    if message is None:
        return stdout, None
    if message.get("stopReason") == "error" and message.get("errorMessage"):
        raise InterpretError(str(message["errorMessage"]).strip().splitlines()[-1])
    content = message.get("content")
    if isinstance(content, str):
        text = content
    else:
        text = "".join(p.get("text", "") for p in content or [] if isinstance(p, dict) and p.get("type") == "text")
    usage = message.get("usage")
    return text, Usage.from_event(usage) if isinstance(usage, dict) else None


def extract_json(text: str) -> dict:
    """Return the first JSON object found in `text` (bare, fenced, or surrounded by prose)."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise InterpretError("the model answered with no JSON object")


@dataclass
class GentleShell:
    binary: str = "gentle-shell"
    model: str | None = None
    timeout: float = 120.0
    _procs: set = field(default_factory=set, init=False, repr=False)
    _cancelled: set = field(default_factory=set, init=False, repr=False)
    _procs_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _run(self, args: list[str]) -> str:
        try:
            # own process group: gentle-shell is a launcher that spawns pi, and cancel() must stop both
            proc = subprocess.Popen([self.binary, "--", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, text=True, start_new_session=True)
        except FileNotFoundError:
            raise InterpretError(f"{self.binary} not found: install Gentle Shell or pass --gentle-shell") from None
        with self._procs_lock:
            self._procs.add(proc)
        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._kill(proc)
            proc.communicate()
            raise InterpretError(f"gentle-shell timed out after {self.timeout:g}s") from None
        finally:
            with self._procs_lock:
                self._procs.discard(proc)
                cancelled = proc in self._cancelled
                self._cancelled.discard(proc)
        if cancelled:
            raise InterpretError("cancelled")
        if proc.returncode != 0:
            detail = (stderr.strip() or stdout.strip() or f"exit code {proc.returncode}").splitlines()
            raise InterpretError(detail[-1] if detail else f"exit code {proc.returncode}")
        return stdout

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def cancel(self) -> None:
        """Stop every running gentle-shell call; each one raises InterpretError("cancelled")."""
        with self._procs_lock:
            procs = list(self._procs)
            self._cancelled.update(procs)
        for proc in procs:
            self._kill(proc)

    def command(self, prompt: str) -> list[str]:
        args = ["-p", "--mode", "json", *QUIET_FLAGS, "--thinking", "off"]
        if self.model:
            args += ["--model", self.model]
        return [*args, prompt]

    def interpret(self, mood: str, current: Style) -> Style:
        """Blocking: ask the model for the style of `mood`; fields it omits keep `current`."""
        return self.interpret_with_usage(mood, current)[0]

    def interpret_with_usage(self, mood: str, current: Style) -> tuple[Style, Usage | None]:
        """Like `interpret`, plus the tokens and cost of the call (None when Gentle Shell does not report them)."""
        cur = current.to_dict()
        cur.pop("mood", None)
        prompt = PROMPT.format(mood=mood, current=json.dumps(cur), keys=", ".join(KEYS), modes=", ".join(MODES),
                               chords=", ".join(CHORD_INSTRUMENTS), leads=", ".join(LEAD_INSTRUMENTS),
                               drums=", ".join(DRUMS), genre_names=", ".join(GENRE_NAMES), genres=genre_lines())
        text, usage = parse_output(self._run(self.command(prompt)))
        data = extract_json(text)
        data["mood"] = mood
        return Style.from_dict(data, current), usage

    def list_models(self) -> list[str]:
        """`provider/model` ids Gentle Shell can use."""
        out = self._run(["--no-mcp", "--no-skill-registry", "--list-models"])
        models = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] != "provider":
                models.append(f"{parts[0]}/{parts[1]}")
        return models
