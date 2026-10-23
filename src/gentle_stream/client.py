"""Talk to the background player (`daemon.py`): attach to it, start it when needed, and control it.

`PlayerClient` has the interface the TUI uses from an in-process `Player` (now, playing, volume, level, style,
spectrum, play/pause/toggle/next, set_style, set_volume, start, close), so the app does not know which one it has.
`close()` stops the music (the `q` key), `detach()` only hangs up (the `b` key).

State is cached: `spectrum()` polls it (the TUI calls it on every frame) and every control refreshes it, so `now`,
`playing` and `volume` are never more than one frame old and cost no round trip. When the player goes away,
controls do nothing and `spectrum()` raises `PlayerGone`, which the TUI takes as its cue to leave.
"""
from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
import time
from multiprocessing.connection import AuthenticationError, Client

import numpy as np

from . import daemon
from .player import NowPlaying, settle
from .style import Style

STARTUP_TIMEOUT = 30.0   # spawn to ready: imports, engine setup and the first rendered bar
STOP_TIMEOUT = 10.0


class PlayerError(Exception):
    """The background player could not be started or reached; the message is meant for the terminal."""


class AudioError(PlayerError):
    """The background player could not open the audio output."""


class NoPlayer(PlayerError):
    """No background player is running."""


class PlayerGone(ConnectionError):
    """The background player stopped while we were connected."""


def connect() -> PlayerClient:
    """Attach to the running player; raises NoPlayer when there is none (a stale socket counts as none)."""
    paths = daemon.Paths(daemon.runtime_dir())
    try:
        daemon.check_private(paths.dir)
        key = paths.key.read_bytes()
    except FileNotFoundError:
        raise NoPlayer("nothing is playing") from None
    except (OSError, daemon.RuntimeDirError) as e:
        raise PlayerError(str(e)) from None
    try:
        conn = Client(str(paths.sock), family="AF_UNIX", authkey=key)
    except (FileNotFoundError, ConnectionRefusedError, AuthenticationError, EOFError):
        raise NoPlayer("nothing is playing") from None
    except OSError as e:
        raise PlayerError(f"cannot reach the player: {e}") from None
    try:
        return PlayerClient(conn)
    except PlayerGone:
        raise NoPlayer("nothing is playing") from None


def ensure(timeout: float = STARTUP_TIMEOUT) -> PlayerClient:
    """Attach to the running player, or start one and attach to it."""
    try:
        return connect()
    except NoPlayer:
        pass
    deadline = time.monotonic() + timeout
    _spawn(deadline)
    while True:   # ready, or another player is starting: it listens once its audio is open
        try:
            return connect()
        except NoPlayer:
            if time.monotonic() > deadline:
                raise PlayerError("the player did not start in time") from None
            time.sleep(0.05)


def _spawn(deadline: float) -> None:
    """Start `gentle-stream daemon` detached from this terminal and wait until it reports its startup."""
    paths = daemon.prepare()
    r, w = os.pipe()
    try:
        with open(paths.log, "ab") as log:
            proc = subprocess.Popen([sys.executable, "-m", "gentle_stream", "daemon", "--ready-fd", str(w)],
                                    pass_fds=(w,), start_new_session=True, cwd="/", stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log)
    finally:
        os.close(w)
    threading.Thread(target=proc.wait, daemon=True).start()   # reap it if it ends while we still run
    try:
        line = b""
        while not line.endswith(b"\n"):
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([r], [], [], left)[0]:
                raise PlayerError(f"the player did not start in time (see {paths.log})")
            chunk = os.read(r, 4096)
            if not chunk:
                raise PlayerError(f"the player stopped while starting (see {paths.log})")
            line += chunk
    finally:
        os.close(r)
    message = json.loads(line)
    if "error" in message:
        raise (AudioError if message.get("audio") else PlayerError)(message["error"])


class PlayerClient:
    def __init__(self, conn):
        self._conn = conn
        self._lock = threading.Lock()   # the UI thread polls while a worker may send a style
        self._shown = np.zeros(0)
        self._update(self.request("state"))

    # ------------------------------------------------------------ transport
    def request(self, cmd: str, **args) -> dict | None:
        """One round trip; raises PlayerGone when the player is no longer there."""
        with self._lock:
            if self._conn is None:
                raise PlayerGone("the player stopped")
            try:
                self._conn.send_bytes(json.dumps({"cmd": cmd, **args}).encode())
                answer = json.loads(self._conn.recv_bytes())
            except (EOFError, OSError) as e:
                self._hang_up()
                raise PlayerGone("the player stopped") from e
        if not answer.get("ok"):
            raise PlayerError(answer.get("error", "the player refused the request"))
        return answer.get("state")

    def _hang_up(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _update(self, state: dict) -> None:
        now = state["now"]
        self.pid = state["pid"]
        self.playing, self.volume, self.level = state["playing"], state["volume"], state["level"]
        self.backend = state["backend"]
        self.now = NowPlaying(Style.from_dict(now["style"]), now["piece"], now["bar"])
        self.style = Style.from_dict(state["style"])   # rendered, maybe not audible yet: the base for a mood

    def _control(self, cmd: str, **args) -> None:
        try:
            self._update(self.request(cmd, **args))
        except PlayerGone:
            pass   # the next poll notices and the TUI leaves

    # ------------------------------------------------------------ Player interface
    @property
    def gone(self) -> bool:
        return self._conn is None

    def start(self) -> None:
        """Already playing: the player opened the audio before we could attach."""

    def spectrum(self, bands: int = 32) -> np.ndarray:
        state = self.request("state", bands=bands)
        self._update(state)
        self._shown = settle(np.asarray(state["spectrum"], dtype=float), self._shown)
        return self._shown

    def play(self) -> None:
        self._control("play")

    def pause(self) -> None:
        self._control("pause")

    def toggle(self) -> None:
        self._control("toggle")

    def next(self) -> None:
        self._control("next")

    def set_volume(self, volume: float) -> None:
        self._control("set_volume", volume=float(volume))

    def set_style(self, style: Style) -> None:
        self._control("set_style", style=style.to_dict())

    def detach(self) -> None:
        """Hang up and leave the music playing."""
        with self._lock:
            self._hang_up()

    def close(self) -> None:
        """Stop the music: the player exits, and this returns once it is gone (its connection ends)."""
        try:
            self.request("stop")
        except (PlayerGone, PlayerError):
            return
        with self._lock:
            conn = self._conn
            try:
                if conn is not None and conn.poll(STOP_TIMEOUT):
                    conn.recv_bytes()
            except (EOFError, OSError):
                pass
            self._hang_up()
