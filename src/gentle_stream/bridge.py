"""Mood interpretation through Gentle Shell.

Runs `gentle-shell -- -p` non-interactively (no session, no tools, no context files) with the model the person
chose, asks for a JSON object describing the music, and validates it into a `Style`.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from .style import CHORD_INSTRUMENTS, DRUMS, KEYS, LEAD_INSTRUMENTS, MODES, Style

QUIET_FLAGS = ("--no-session", "-nt", "-ns", "-np", "-nc", "--no-mcp", "--no-themes", "--no-skill-registry")

PROMPT = """You are the composer of an endless, real-time generative lo-fi music stream.
Translate the listener's request (a feeling, a concept, a place, a scene; any language) into musical parameters.

Listener request: {mood}

Current parameters (keep what the request does not ask to change):
{current}

Reply with ONLY one JSON object, no prose, with exactly these fields:
- "name": short evocative track title (max 4 words)
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
"""


class InterpretError(RuntimeError):
    pass


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

    def _run(self, args: list[str]) -> str:
        try:
            proc = subprocess.run([self.binary, "--", *args], capture_output=True, text=True, timeout=self.timeout,
                                  stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise InterpretError(f"{self.binary} not found: install Gentle Shell or pass --gentle-shell") from None
        except subprocess.TimeoutExpired:
            raise InterpretError(f"gentle-shell timed out after {self.timeout:g}s") from None
        if proc.returncode != 0:
            detail = (proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}").splitlines()
            raise InterpretError(detail[-1] if detail else f"exit code {proc.returncode}")
        return proc.stdout

    def command(self, prompt: str) -> list[str]:
        args = ["-p", *QUIET_FLAGS, "--thinking", "off"]
        if self.model:
            args += ["--model", self.model]
        return [*args, prompt]

    def interpret(self, mood: str, current: Style) -> Style:
        """Blocking: ask the model for the style of `mood`; fields it omits keep `current`."""
        cur = current.to_dict()
        cur.pop("mood", None)
        prompt = PROMPT.format(mood=mood, current=json.dumps(cur), keys=", ".join(KEYS), modes=", ".join(MODES),
                               chords=", ".join(CHORD_INSTRUMENTS), leads=", ".join(LEAD_INSTRUMENTS),
                               drums=", ".join(DRUMS))
        data = extract_json(self._run(self.command(prompt)))
        data["mood"] = mood
        return Style.from_dict(data, current)

    def list_models(self) -> list[str]:
        """`provider/model` ids Gentle Shell can use."""
        out = self._run(["--no-mcp", "--no-skill-registry", "--list-models"])
        models = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] != "provider":
                models.append(f"{parts[0]}/{parts[1]}")
        return models
