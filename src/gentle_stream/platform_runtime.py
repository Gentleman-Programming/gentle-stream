"""Platform mechanisms behind daemon.Paths/prepare and the existing JSON IPC."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

WINDOWS = os.name == "nt"
FAMILY = "AF_PIPE" if WINDOWS else "AF_UNIX"


def address(paths):
    if not WINDOWS:
        return str(paths.sock)
    from ._windows_runtime import user_sid
    identity = user_sid() + "\0" + os.path.normcase(str(paths.dir.resolve()))
    return "\\\\.\\pipe\\gentle-stream-" + hashlib.sha256(identity.encode()).hexdigest()


def default_runtime():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "gentle-stream" / "run"


def make_private(path):
    from ._windows_runtime import make_private as make
    make(path)


def check_private(path):
    from ._windows_runtime import check_private as check
    check(path)


def check_file(path):
    if WINDOWS:
        from ._windows_runtime import check_private as check
        check(path, directory=False)


def lock(file):
    if WINDOWS:
        import msvcrt
        file.seek(0)
        try:
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            if e.errno in (13, 36):
                raise BlockingIOError from e
            raise
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
