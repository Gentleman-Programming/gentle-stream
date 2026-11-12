"""Own only the model process tree we launch; never terminate by name or search."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess


def spawn(argv, **kwargs):
    if os.name != "nt":
        return subprocess.Popen(argv, start_new_session=True, **kwargs)
    from ._windows_runtime import Job
    if kwargs.get("text"):
        kwargs.setdefault("encoding", "utf-8")  # Node JSONL uses UTF-8, not the Windows ANSI code page.
    executable = shutil.which(argv[0]) or argv[0]
    suffix = Path(executable).suffix.lower()
    if suffix in (".bat", ".cmd"):
        raise OSError("use a native executable or a .mjs entry point with node.exe on PATH, not a cmd/bat shim")
    if suffix in (".mjs", ".js"):
        node = shutil.which("node.exe")
        if not node:
            raise FileNotFoundError("node.exe not found")
        argv = [node, executable, *argv[1:]]
    job = Job()
    proc = None
    try:
        # CREATE_SUSPENDED is a Win32 flag not exported by subprocess.
        flags = kwargs.pop("creationflags", 0) | 0x00000004
        proc = subprocess.Popen(argv, creationflags=flags, **kwargs)
        job.attach_and_resume(proc)
        proc._gentle_job = job
        return proc
    except BaseException:
        if proc is not None:
            proc.kill()
            proc.wait()
        job.close()
        raise


def kill_tree(proc):
    if os.name == "nt":
        proc._gentle_job.kill()
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def release(proc, *, detach=False):
    if os.name == "nt":
        proc._gentle_job.close(detach=detach)


def is_alive(pid):
    if os.name == "nt":
        from ._windows_runtime import is_alive as alive
        return alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True
