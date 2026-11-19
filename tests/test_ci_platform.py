"""Device-free checks of the CI gate, with no real git or CLI subprocesses."""

import os
from pathlib import Path
import subprocess

import pytest

from scripts import check_ci_platform as check


SHA = "a" * 40
KERNEL = "6.6.87.2-microsoft-standard-WSL2"


@pytest.fixture
def host(monkeypatch):
    def configure(system, machine, release="6.8.0-generic", distro=("ubuntu", "24.04"),
                  sha=SHA, head=SHA, root=Path.cwd(), git_exit=0):
        monkeypatch.setattr(check.sys, "platform", system)
        monkeypatch.setattr(check.platform, "machine", lambda: machine)
        monkeypatch.setattr(check.platform, "release", lambda: release)
        monkeypatch.setattr(check.platform, "freedesktop_os_release",
                            lambda: dict(zip(("ID", "VERSION_ID"), distro)))
        if sha is None:
            monkeypatch.delenv("GITHUB_SHA", raising=False)
        else:
            monkeypatch.setenv("GITHUB_SHA", sha)
        calls = []

        def git(argv, *, cwd, **kwargs):
            calls.append(argv)
            assert argv == ["git", "rev-parse", "--show-toplevel", "HEAD"]
            assert cwd == Path.cwd()
            return subprocess.CompletedProcess(argv, git_exit, f"{root}\n{head}\n", "")

        monkeypatch.setattr(check.subprocess, "run", git)
        return calls

    return configure


@pytest.mark.parametrize(("target", "arch", "system", "machine", "release"), [
    ("Linux", "x64", "linux", "x86_64", "6.8.0-generic"),
    ("macOS", "arm64", "darwin", "arm64", "24.6.0"),
    ("Windows", "x64", "win32", "AMD64", "10"),
    ("WSL", "x64", "linux", "x86_64", KERNEL),
])
def test_verify_reports_actual_platform_and_head(host, capsys, target, arch, system, machine, release):
    calls = host(system, machine, release)
    assert check.main(["verify", target, arch]) == 0
    platform_line = f"{target}/{arch}: sys.platform={system} machine={machine} kernel={release}"
    if target == "WSL":
        platform_line += " distro=ubuntu:24.04"
    assert capsys.readouterr() == (f"{platform_line}\nCheckout HEAD matches GITHUB_SHA: {SHA}\n", "")
    assert len(calls) == 1


@pytest.mark.parametrize(("target", "arch", "system", "machine", "release", "distro", "error"), [
    ("Windows", "x64", "linux", "x86_64", "6.8.0", ("ubuntu", "24.04"), "expected sys.platform=win32, got linux"),
    ("macOS", "arm64", "darwin", "x86_64", "24.6.0", ("ubuntu", "24.04"), "expected architecture arm64, got x86_64"),
    ("Linux", "x64", "linux", "i686", "6.8.0", ("ubuntu", "24.04"), "expected architecture x64, got i686"),
    ("Linux", "x64", "linux", "x86_64", KERNEL, ("ubuntu", "24.04"), "expected native Linux, not WSL"),
    ("WSL", "x64", "linux", "x86_64", "6.8.0-generic", ("ubuntu", "24.04"), "expected a Microsoft WSL2 kernel"),
    ("WSL", "x64", "linux", "x86_64", "4.4.0-19041-Microsoft", ("ubuntu", "24.04"), "expected a Microsoft WSL2 kernel"),
    ("WSL", "x64", "linux", "x86_64", "6.6.0-WSL2", ("ubuntu", "24.04"), "expected a Microsoft WSL2 kernel"),
    ("WSL", "x64", "linux", "x86_64", KERNEL, ("ubuntu", "22.04"), "expected distro ubuntu:24.04"),
    ("WSL", "x64", "linux", "x86_64", KERNEL, ("debian", "24.04"), "expected distro ubuntu:24.04"),
])
def test_verify_rejects_wrong_target(host, capsys, target, arch, system, machine, release, distro, error):
    calls = host(system, machine, release, distro)
    assert check.main(["verify", target, arch]) == 1
    assert capsys.readouterr() == ("", f"CI check failed: {error}\n")
    assert calls == []


@pytest.mark.parametrize(("overrides", "error", "calls_expected"), [
    ({"sha": None}, "GITHUB_SHA is missing or is not a full commit SHA", 0),
    ({"sha": "branch-name"}, "GITHUB_SHA is missing or is not a full commit SHA", 0),
    ({"head": "b" * 40}, "checkout HEAD does not match GITHUB_SHA", 1),
    ({"root": Path.cwd().parent}, "run the check at the actual checkout root", 1),
    ({"git_exit": 128}, "could not read checkout root and HEAD", 1),
])
def test_verify_fails_closed_on_provenance(host, capsys, overrides, error, calls_expected):
    calls = host("linux", "x86_64", **overrides)
    assert check.main(["verify", "Linux", "x64"]) == 1
    assert capsys.readouterr() == ("", f"CI check failed: {error}\n")
    assert len(calls) == calls_expected


HELP = "usage: gentle-stream [-h] command ...\n\n    setup-samples  download the soundfont\n    ctl            control the music\n"
STATUS = '{"ok": true, "running": false}\n'


@pytest.mark.parametrize(("help_result", "status_result", "failure"), [
    ((0, HELP, ""), (0, STATUS, ""), None),
    ((1, HELP, ""), None, "installed gentle-stream --help did not print clean help"),
    ((0, "unrelated tool\n", ""), None, "installed gentle-stream --help did not print clean help"),
    ((0, HELP, "warning\n"), None, "installed gentle-stream --help did not print clean help"),
    ((0, HELP, ""), (1, STATUS, ""), "installed no-session status did not return the exact success envelope"),
    ((0, HELP, ""), (0, STATUS, "warning\n"), "installed no-session status did not return the exact success envelope"),
    ((0, HELP, ""), (0, STATUS + "extra\n", ""), "installed no-session status did not return the exact success envelope"),
    ((0, HELP, ""), (0, STATUS.replace("\n", "\r\n"), ""),
     "installed no-session status did not return the exact success envelope"),
    ((0, HELP, ""), (0, STATUS.replace("\n", "\r"), ""),
     "installed no-session status did not return the exact success envelope"),
    ((0, HELP, ""), (0, '{"ok": true, "running": true}\n', ""),
     "installed no-session status did not return the exact success envelope"),
])
def test_installed_smoke_uses_empty_isolated_scratch(monkeypatch, capsys, help_result, status_result, failure):
    scratch = Path.cwd() / "fake-smoke"
    executable = Path.cwd() / "venv" / "bin" / "gentle-stream"
    mkdirs = []
    monkeypatch.setattr(Path, "mkdir", lambda path, *, mode: mkdirs.append((path, mode)))
    for key in ("PYTHONPATH", "PYTHONHOME", "GENTLE_STREAM_AUDIO_FACTORY", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(key, "must-not-leak")
    calls = []

    def cli(argv, *, cwd, env, **kwargs):
        assert cwd == scratch
        assert Path(argv[0]) == executable
        for key in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "TMPDIR"):
            assert (Path(env[key]), 0o700) in mkdirs
            assert Path(env[key]).parent == scratch
        assert len({env[k] for k in ("HOME", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR", "TMPDIR")}) == 5
        assert all(k not in env for k in ("PYTHONPATH", "PYTHONHOME", "GENTLE_STREAM_AUDIO_FACTORY"))
        assert env["GENTLE_STREAM_AUDIO_OUTPUT"] == "ci-no-player-start"
        assert not (scratch / "run" / "gentle-stream").exists()
        calls.append(argv[1:])
        code, out, err = help_result if argv[1:] == ["--help"] else status_result
        if kwargs.get("text"):
            # Match subprocess's universal-newline behavior in text mode.
            out = out.replace("\r\n", "\n").replace("\r", "\n")
            err = err.replace("\r\n", "\n").replace("\r", "\n")
        else:
            out, err = out.encode(), err.encode()
        return subprocess.CompletedProcess(argv, code, out, err)

    monkeypatch.setattr(check.subprocess, "run", cli)
    assert check.main(["smoke", os.fspath(executable), os.fspath(scratch)]) == int(failure is not None)
    assert mkdirs[0] == (scratch, 0o700)
    assert calls == [["--help"]] + ([["ctl", "--json", "status"]] if status_result else [])
    if failure:
        assert capsys.readouterr() == ("", f"CI check failed: {failure}\n")
    else:
        assert capsys.readouterr() == (f"Installed CLI help OK; no-session status: {STATUS}", "")
