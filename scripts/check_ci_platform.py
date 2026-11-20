#!/usr/bin/env python3
"""Check a CI runner and its checkout, or smoke the installed console script."""

import argparse
import os
from pathlib import Path
import platform
import re
import subprocess
import sys


SYSTEMS = {"Linux": "linux", "macOS": "darwin", "Windows": "win32", "WSL": "linux"}
MACHINES = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
NO_SESSION = '{"ok": true, "running": false}\n'


class CheckFailed(Exception):
    pass


def verify(target: str, arch: str) -> None:
    system, machine, kernel = sys.platform, platform.machine(), platform.release()
    if system != SYSTEMS[target]:
        raise CheckFailed(f"expected sys.platform={SYSTEMS[target]}, got {system}")
    if MACHINES.get(machine.lower()) != arch:
        raise CheckFailed(f"expected architecture {arch}, got {machine}")

    evidence = f"{target}/{arch}: sys.platform={system} machine={machine} kernel={kernel}"
    if target == "Linux" and "microsoft" in kernel.lower():
        raise CheckFailed("expected native Linux, not WSL")
    if target == "WSL":
        # The Windows list is logged separately; its state labels are localized.
        if not re.search(r"\bmicrosoft(?:-standard)?-wsl2\b", kernel, re.IGNORECASE):
            raise CheckFailed("expected a Microsoft WSL2 kernel")
        distro = platform.freedesktop_os_release()
        if (distro.get("ID"), distro.get("VERSION_ID")) != ("ubuntu", "24.04"):
            raise CheckFailed("expected distro ubuntu:24.04")
        evidence += " distro=ubuntu:24.04"

    sha = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise CheckFailed("GITHUB_SHA is missing or is not a full commit SHA")
    source = Path.cwd()
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_NAMESPACE"):
        env.pop(key, None)
    git = subprocess.run(["git", "rev-parse", "--show-toplevel", "HEAD"], cwd=source,
                         env=env, capture_output=True, text=True, encoding="utf-8", timeout=15)
    lines = git.stdout.splitlines()
    if git.returncode or len(lines) != 2:
        raise CheckFailed("could not read checkout root and HEAD")
    root, head = lines
    if Path(root).resolve() != source.resolve():
        raise CheckFailed("run the check at the actual checkout root")
    if head != sha.lower():
        raise CheckFailed("checkout HEAD does not match GITHUB_SHA")
    print(evidence)
    print(f"Checkout HEAD matches GITHUB_SHA: {head}")


def smoke(executable: Path, scratch: Path) -> None:
    executable, scratch = executable.absolute(), scratch.absolute()
    scratch.mkdir(mode=0o700)  # Require a new directory, including a new runtime namespace.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "GENTLE_STREAM_"))}
    for key, name in {"HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CACHE_HOME": "cache",
                      "XDG_RUNTIME_DIR": "run", "TMPDIR": "tmp"}.items():
        directory = scratch / name
        directory.mkdir(mode=0o700)
        env[key] = str(directory)
    # status only attaches. If it regresses to ensure(), refuse audio before any spawn.
    env.update(GENTLE_STREAM_AUDIO_OUTPUT="ci-no-player-start", PYTHONDONTWRITEBYTECODE="1", LC_ALL="C")

    def run(*args: str) -> subprocess.CompletedProcess:
        # Keep bytes so universal-newline conversion cannot hide a framing change.
        return subprocess.run([str(executable), *args], cwd=scratch, env=env,
                              capture_output=True, timeout=15)

    help_result = run("--help")
    if (help_result.returncode or help_result.stderr or not help_result.stdout.startswith(b"usage: gentle-stream ")
            or b"ctl" not in help_result.stdout or b"setup-samples" not in help_result.stdout):
        raise CheckFailed("installed gentle-stream --help did not print clean help")
    status = run("ctl", "--json", "status")
    if status.returncode or status.stderr or status.stdout != NO_SESSION.encode("ascii"):
        raise CheckFailed("installed no-session status did not return the exact success envelope")
    print(f"Installed CLI help OK; no-session status: {NO_SESSION}", end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify_parser = commands.add_parser("verify", help="check platform, architecture and GITHUB_SHA")
    verify_parser.add_argument("platform", choices=SYSTEMS)
    verify_parser.add_argument("architecture", choices=("x64", "arm64"))
    smoke_parser = commands.add_parser("smoke", help="run the installed CLI in fresh isolation")
    smoke_parser.add_argument("executable", type=Path)
    smoke_parser.add_argument("scratch", type=Path, help="new directory beneath the runner scratch root")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            verify(args.platform, args.architecture)
        else:
            smoke(args.executable, args.scratch)
    except (CheckFailed, OSError, subprocess.TimeoutExpired) as exc:
        print(f"CI check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
