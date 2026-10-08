import configparser
import contextlib
import io
import os
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from ccc_server import launcher
from ccc_server.import_closure import compute_closure


ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.probe = mock.patch.object(launcher, "_watchtower_available", return_value=True)
        self.available = self.probe.start()
        self.addCleanup(self.probe.stop)
        self.exec_patch = mock.patch.object(launcher.os, "execv")
        self.execute = self.exec_patch.start()
        self.addCleanup(self.exec_patch.stop)
        self.mask_patch = mock.patch.object(launcher.os, "umask")
        self.mask_patch.start()
        self.addCleanup(self.mask_patch.stop)

    def test_help_and_version_are_offline(self):
        server_before = sys.modules.get("server")
        for flag in ("--help", "--version"):
            with self.subTest(flag=flag), contextlib.redirect_stdout(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as exit_result:
                    launcher.main([flag])
                self.assertEqual(exit_result.exception.code, 0)
                self.assertIn("Claude Command Center", output.getvalue())
        self.available.assert_not_called()
        self.execute.assert_not_called()
        self.assertIs(sys.modules.get("server"), server_before)

    def test_bad_port_is_rejected_before_bootstrap(self):
        for value in ("bad", "0", "65536"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exit_result:
                    launcher.main(["--port", value])
                self.assertEqual(exit_result.exception.code, 2)
        self.available.assert_not_called()
        self.execute.assert_not_called()

    def test_launch_preserves_flags_cwd_and_normal_state_dirs(self):
        cwd = Path.cwd()
        with mock.patch.dict(os.environ, {
            "CCC_WORKER_PROCESS": "1", "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "test-session",
            "CLAUDE_CODE_MESSAGING_SOCKET": "test-socket",
            "CLAUDE_CODE_CHILD_SESSION": "1", "HOME": str(ROOT / "test-home"),
        }), mock.patch.object(launcher.subprocess, "run") as install:
            launcher.main(["--port", "9204", "--host", "127.0.0.1"])
            for key in launcher.INHERITED_SESSION_ENV:
                self.assertNotIn(key, os.environ)
            self.assertEqual(os.environ["HOME"], str(ROOT / "test-home"))
        install.assert_not_called()
        self.execute.assert_called_once_with(sys.executable, [
            sys.executable, str(ROOT / "server.py"),
            "--port", "9204", "--host", "127.0.0.1",
        ])
        self.assertEqual(Path.cwd(), cwd)

    def test_default_launch_leaves_server_env_defaults_intact(self):
        launcher.main([])
        self.execute.assert_called_once_with(
            sys.executable, [sys.executable, str(ROOT / "server.py")],
        )

    def test_bootstrap_reprobes_in_fresh_interpreter(self):
        self.available.side_effect = [False, True]
        with mock.patch.object(launcher.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(launcher.subprocess, "run", return_value=mock.Mock(returncode=0)) as install:
            launcher.main(["--port", "9204"])
        self.assertEqual(self.available.call_count, 2)
        args, kwargs = install.call_args
        self.assertEqual(args[0], ["/bin/bash", str(ROOT / "scripts" / "install-watchtower.sh")])
        self.assertEqual(kwargs["env"]["CCC_PYTHON"], sys.executable)
        self.assertEqual(kwargs["env"]["CCC_SKIP_WATCHTOWER_DAEMON"], "1")
        self.assertEqual(kwargs["timeout"], 180)
        self.execute.assert_called_once()

    def test_missing_watchtower_fails_closed_even_after_zero_exit(self):
        self.available.return_value = False
        with mock.patch.object(launcher.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(launcher.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(launcher.main([]), 1)
        self.assertIn("WatchTower", output.getvalue())
        self.assertIn("retry", output.getvalue().lower())
        self.assertNotIn("Traceback", output.getvalue())
        self.execute.assert_not_called()

    def test_missing_bash_and_install_timeout_are_actionable(self):
        self.available.return_value = False
        for bash, error in ((None, None), ("/bin/bash", subprocess.TimeoutExpired("bash", 180))):
            with self.subTest(bash=bash), \
                    mock.patch.object(launcher.shutil, "which", return_value=bash), \
                    mock.patch.object(launcher.subprocess, "run", side_effect=error), \
                    contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(launcher.main([]), 1)
                self.assertIn("WatchTower", output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())
        self.execute.assert_not_called()

    def test_probe_is_bounded_and_uses_the_launch_interpreter(self):
        self.probe.stop()
        with mock.patch.object(launcher.subprocess, "run", return_value=mock.Mock(returncode=0)) as probe:
            self.assertTrue(launcher._watchtower_available())
        probe.assert_called_once_with(
            [sys.executable, "-c", "import watchtower.queue"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        )
        with mock.patch.object(launcher.subprocess, "run", side_effect=subprocess.TimeoutExpired("python", 15)):
            self.assertFalse(launcher._watchtower_available())


@unittest.skipUnless(os.environ.get("CCC_TEST_WHEEL"), "set CCC_TEST_WHEEL to a built wheel")
class WheelTests(unittest.TestCase):
    def setUp(self):
        self.wheel = zipfile.ZipFile(os.environ["CCC_TEST_WHEEL"])
        self.addCleanup(self.wheel.close)
        self.names = set(self.wheel.namelist())

    def test_entrypoint_and_runtime_dependency_metadata(self):
        entry_name = next(name for name in self.names if name.endswith(".dist-info/entry_points.txt"))
        entry = configparser.ConfigParser()
        entry.read_string(self.wheel.read(entry_name).decode())
        self.assertEqual(entry["console_scripts"]["claude-command-center"], "ccc_server.launcher:main")
        metadata_name = next(name for name in self.names if name.endswith(".dist-info/METADATA"))
        metadata = self.wheel.read(metadata_name).decode()
        self.assertNotIn("Requires-Dist: hatchling", metadata)
        self.assertNotIn("Requires-Dist: watchtower", metadata)

    def test_all_public_assets_and_import_closure_are_in_wheel(self):
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        expected = {name for name in tracked if name.startswith(("static/", "hooks/", "skills/", "ccc_server/", "_history_index/"))}
        expected.add("scripts/install-watchtower.sh")
        expected.update(compute_closure(ROOT, ("server.py", "ccc_worker.py", "ccc_acp.py"))["files"])
        self.assertFalse(expected - self.names, f"missing runtime files: {sorted(expected - self.names)}")
        self.assertIn("ccc_server/launcher.py", self.names)
        self.assertIn("ccc_server/usage_db/rates.json", self.names)
        self.assertIn("static/onboarding/onboarding.js", self.names)
        self.assertIn("static/onboarding/onboarding.css", self.names)
        self.assertIn("hooks/_reorient_shared.py", self.names)
        self.assertIn("skills/ccc-orchestration.md", self.names)

    def test_private_state_and_build_caches_are_not_in_wheel(self):
        for name in self.names:
            self.assertFalse(name.startswith(("tests/", "docs/", "infra/", ".claude/", "static/morning/", "morning.py", "morning_store.py")), name)
            self.assertNotIn("__pycache__", name)
            self.assertFalse(name.endswith((".pyc", ".DS_Store", ".log")), name)


if __name__ == "__main__":
    unittest.main()
