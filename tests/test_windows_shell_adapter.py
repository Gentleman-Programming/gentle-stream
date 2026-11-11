"""Device-free WSL adapter checks; fake Node never invokes Windows or a model."""
import json
import os
from pathlib import Path
from pathlib import PureWindowsPath
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ADAPTER = Path(__file__).resolve().parents[1] / "scripts" / "gentle-shell-windows"
FAKE_WINDOWS_BUNDLE = PureWindowsPath(r"C:\Users\Example User\.gentle-shell-bundle")


class WindowsShellAdapterTests(unittest.TestCase):
    def setUp(self):
        # The isolated runner retains its scratch directory for readback.
        self.bundle = Path(tempfile.mkdtemp(prefix="gentle-stream-adapter-")) / "bundle with spaces"
        self.bundle.mkdir()
        (self.bundle / "current").write_text("4.4.0-1.2.0\n")
        self.node = self.bundle / "runtime" / "node-24.21.0" / "node.exe"
        self.node.parent.mkdir(parents=True)
        self.node.write_text(
            f"#!{sys.executable}\n"
            "import json, sys\n"
            "print(json.dumps(sys.argv[1:], ensure_ascii=False))\n"
            "sys.stderr.write('fake node stderr\\n')\n"
            "sys.exit(23)\n"
        )
        self.node.chmod(0o755)
        self.install_entrypoint("4.4.0-1.2.0")

    def install_entrypoint(self, version):
        entrypoint = self.bundle / "versions" / version / "node_modules/gentle-pi/bin/gentle-shell.mjs"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text("// Fake entrypoint; not executed.\n")

    def run_adapter(self, *args):
        # Explicit fake bundle and converter; execute a fake Node as a child process
        # to exercise the adapter's public streams, arguments and exit status.
        harness = (
            "import os, pathlib, runpy, sys; "
            "from pathlib import PureWindowsPath; "
            "ns = runpy.run_path(sys.argv[1]); "
            "main = ns['main']; g = main.__globals__; "
            "bundle = pathlib.Path(sys.argv[2]); "
            "g['BUNDLE'] = bundle; "  # supports the pre-portability RED test without real profile access
            "g['WINDOWS_BUNDLE'] = PureWindowsPath(r'C:\\Users\\Example User\\.gentle-shell-bundle'); "
            "g['to_windows'] = lambda path: str(g['WINDOWS_BUNDLE'].joinpath(*pathlib.Path(path).relative_to(bundle.resolve()).parts)); "
            "os.environ['GENTLE_STREAM_WINDOWS_SHELL_BUNDLE'] = str(bundle); "
            "sys.exit(main(sys.argv[3:]))"
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", harness, str(ADAPTER), str(self.bundle), *args],
            capture_output=True, text=True, timeout=10,
        )

    def assert_forwarded(self, result, version, args):
        script = str(FAKE_WINDOWS_BUNDLE / "versions" / version / "node_modules/gentle-pi/bin/gentle-shell.mjs")
        self.assertEqual(result.returncode, 23)
        self.assertEqual(result.stdout, json.dumps([script, *args], ensure_ascii=False) + "\n")
        self.assertEqual(result.stderr, "fake node stderr\n")

    def test_preserves_arguments_streams_and_exit_code(self):
        args = ["--", "-p", "--mode", "json", "--model", "provider/model", "",
                'lluvia ☔\n"quotes" \\ trailing\\', "$(echo unsafe); & | %PATH% `whoami`"]
        self.assert_forwarded(self.run_adapter(*args), "4.4.0-1.2.0", args)

    def test_reads_current_on_each_invocation_and_preserves_discovery(self):
        self.install_entrypoint("next-version")
        (self.bundle / "current").write_text("next-version\r\n")
        args = ["--", "--no-mcp", "--no-skill-registry", "--list-models"]
        self.assert_forwarded(self.run_adapter(*args), "next-version", args)

    def test_discovers_bundled_node_by_name_not_machine_version(self):
        alternate = self.node.parent.parent / "node-25.0.0" / "node.exe"
        alternate.parent.mkdir()
        self.node.rename(alternate)
        self.assert_forwarded(self.run_adapter("--version"), "4.4.0-1.2.0", ["--version"])

    def test_rejects_ambiguous_bundled_node_without_execution(self):
        other = self.node.parent.parent / "node-other" / "node.exe"
        other.parent.mkdir()
        other.write_bytes(self.node.read_bytes())
        before = (self.bundle / "current").read_bytes()
        result = self.run_adapter("--version")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "gentle-shell-windows: ambiguous bundled Windows Node runtimes\n")
        self.assertEqual((self.bundle / "current").read_bytes(), before)

    def test_rejects_pointer_symlink_outside_bundle_without_executing(self):
        outside = Path(tempfile.mkdtemp(prefix="gentle-stream-outside-")) / "current"
        outside.write_text("private-placeholder\n")
        (self.bundle / "current").unlink()
        (self.bundle / "current").symlink_to(outside)
        result = self.run_adapter("--version")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "gentle-shell-windows: bundle path escapes configured root\n")
        self.assertEqual(outside.read_bytes(), b"private-placeholder\n")

    def test_discovers_each_device_profile_without_scanning_other_users(self):
        ns = runpy.run_path(str(ADAPTER))
        profile = ("USERPROFILE_ALT=C:\\Users\\Wrong Account\r\n"
                   "USERPROFILE=C:\\Users\\José & Device\r\n").encode("utf-16-le")
        responses = [subprocess.CompletedProcess([], 0, profile, b""),
                     subprocess.CompletedProcess([], 0, "/mnt/c/Users/José & Device\n", "")]
        with patch.dict(os.environ, {"GENTLE_STREAM_WINDOWS_SHELL_BUNDLE": ""}), \
             patch("subprocess.run", side_effect=responses) as run:
            self.assertEqual(ns["discover_bundle"](), Path("/mnt/c/Users/José & Device/.gentle-shell-bundle"))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], ["cmd.exe", "/u", "/d", "/c", "set USERPROFILE"])
        self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/wslpath", "-u", r"C:\Users\José & Device"])

    def test_rejects_missing_duplicate_or_unconvertible_profile(self):
        ns = runpy.run_path(str(ADAPTER))
        cases = [
            ([subprocess.CompletedProcess([], 0, b"\x00", b"")], 1),
            ([subprocess.CompletedProcess([], 0, b"", b"")], 1),
            ([subprocess.CompletedProcess([], 0, "USERPROFILE_ALT=C:\\Users\\Wrong\r\n".encode("utf-16-le"), b"")], 1),
            ([subprocess.CompletedProcess([], 0, ("USERPROFILE=C:\\Users\\One\r\n"
                                               "USERPROFILE=C:\\Users\\Two\r\n").encode("utf-16-le"), b"")], 1),
            ([subprocess.CompletedProcess([], 0, "USERPROFILE=C:\\Users\\One\r\n".encode("utf-16-le"), b""),
              subprocess.CalledProcessError(1, ["/usr/bin/wslpath", "-u"])], 2),
        ]
        for replies, count in cases:
            with self.subTest(count=count, records=len(replies)), \
                 patch.dict(os.environ, {"GENTLE_STREAM_WINDOWS_SHELL_BUNDLE": ""}), \
                 patch("subprocess.run", side_effect=replies) as run:
                with self.assertRaisesRegex(ValueError, "Windows profile discovery unavailable"):
                    ns["discover_bundle"]()
                self.assertEqual(run.call_count, count)

    def test_rejects_runtime_symlink_outside_bundle_without_executing(self):
        outside = Path(tempfile.mkdtemp(prefix="gentle-stream-outside-node-")) / "node.exe"
        outside.write_bytes(self.node.read_bytes())
        outside_before = outside.read_bytes()
        self.node.unlink()
        self.node.symlink_to(outside)
        before = (self.bundle / "current").read_bytes()
        result = self.run_adapter("--version")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "gentle-shell-windows: bundle path escapes configured root\n")
        self.assertEqual((self.bundle / "current").read_bytes(), before)
        self.assertEqual(outside.read_bytes(), outside_before)

    def test_rejects_invalid_version_without_executing_node(self):
        (self.bundle / "current").write_text("../escape")
        result = self.run_adapter("--version")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "gentle-shell-windows: invalid bundle version in current\n")
        self.assertEqual((self.bundle / "current").read_text(), "../escape")


if __name__ == "__main__":
    unittest.main()
