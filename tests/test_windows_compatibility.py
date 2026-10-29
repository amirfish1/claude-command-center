"""Unit tests for Windows compatibility (CCC-GH-116)."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestWindowsCompatibility(unittest.TestCase):
    def test_fcntl_shim_exports_required_symbols(self):
        import fcntl
        self.assertTrue(hasattr(fcntl, "flock"))
        self.assertTrue(hasattr(fcntl, "fcntl"))
        self.assertTrue(hasattr(fcntl, "ioctl"))
        self.assertTrue(hasattr(fcntl, "LOCK_SH"))
        self.assertTrue(hasattr(fcntl, "LOCK_EX"))
        self.assertTrue(hasattr(fcntl, "LOCK_NB"))
        self.assertTrue(hasattr(fcntl, "LOCK_UN"))
        self.assertTrue(hasattr(fcntl, "F_GETFL"))
        self.assertTrue(hasattr(fcntl, "F_SETFL"))

    def test_watchtower_msg_af_unix_guard(self):
        src = (REPO_ROOT / "ccc_server" / "watchtower_msg.py").read_text(encoding="utf-8")
        self.assertIn('not hasattr(socket, "AF_UNIX")', src)

    def test_ccc_peer_uds_af_unix_guard(self):
        src = (REPO_ROOT / "ccc_peer_uds.py").read_text(encoding="utf-8")
        self.assertIn('not hasattr(socket, "AF_UNIX")', src)

    def test_run_ps1_sets_utf8_env(self):
        src = (REPO_ROOT / "run.ps1").read_text(encoding="utf-8")
        self.assertIn('$env:PYTHONUTF8 = "1"', src)
        self.assertIn('$env:PYTHONIOENCODING = "utf-8"', src)

    def test_codex_app_server_detached_on_windows(self):
        src = (REPO_ROOT / "ccc_server" / "codex.py").read_text(encoding="utf-8")
        self.assertIn('sys.platform == "win32"', src)
        self.assertIn("DETACHED_PROCESS", src)
        self.assertIn("popen_kwargs", src)

    def test_is_pid_alive_posix_and_invalid(self):
        from ccc_server.paths import _is_pid_alive
        self.assertTrue(_is_pid_alive(os.getpid()))
        self.assertFalse(_is_pid_alive(-1))
        self.assertFalse(_is_pid_alive(0))
        self.assertFalse(_is_pid_alive("invalid"))
        self.assertFalse(_is_pid_alive(None))

    def test_is_pid_alive_windows_mock(self):
        from unittest import mock
        from ccc_server.paths import _is_pid_alive
        mock_kernel32 = mock.MagicMock()
        mock_ctypes = mock.MagicMock()
        mock_ctypes.windll.kernel32 = mock_kernel32
        with mock.patch("sys.platform", "win32"), mock.patch.dict("sys.modules", {"ctypes": mock_ctypes}):
            # OpenProcess fails, GetLastError() == 5 (access denied -> alive)
            mock_kernel32.OpenProcess.return_value = 0
            mock_kernel32.GetLastError.return_value = 5
            self.assertTrue(_is_pid_alive(12345))

            # OpenProcess fails, GetLastError() != 5 (not alive)
            mock_kernel32.GetLastError.return_value = 87
            self.assertFalse(_is_pid_alive(12345))

            # OpenProcess succeeds, WaitForSingleObject == 258 (WAIT_TIMEOUT -> alive)
            mock_kernel32.OpenProcess.return_value = 999
            mock_kernel32.WaitForSingleObject.return_value = 258
            self.assertTrue(_is_pid_alive(12345))
            mock_kernel32.CloseHandle.assert_called_with(999)

            # OpenProcess succeeds, WaitForSingleObject == 0 (WAIT_OBJECT_0 -> terminated)
            mock_kernel32.WaitForSingleObject.return_value = 0
            self.assertFalse(_is_pid_alive(12345))

    def test_find_wt_cli_windows_rejects_windowsapps(self):
        from unittest import mock
        from ccc_server.watchtower_msg import _find_wt_cli
        with mock.patch("sys.platform", "win32"), \
             mock.patch("shutil.which", return_value=r"C:\Users\foo\AppData\Local\Microsoft\WindowsApps\wt.exe"), \
             mock.patch("os.path.isfile", side_effect=lambda p: "Python" in p and p.endswith("wt.exe")), \
             mock.patch.dict(os.environ, {"APPDATA": r"C:\Users\foo\AppData\Roaming"}):
            found = _find_wt_cli()
            self.assertNotIn("WindowsApps", found)
            self.assertTrue(found.endswith("wt.exe"))

    def test_has_project_marker_rejects_drive_roots(self):
        from ccc_server.repo_paths import _has_project_marker
        self.assertFalse(_has_project_marker(Path("C:/")))
        self.assertFalse(_has_project_marker(Path("C:\\")))
        self.assertFalse(_has_project_marker(Path("/")))

    def test_claude_desktop_sessions_root_windows(self):
        import server
        from unittest import mock
        from ccc_server.session_graph import _claude_desktop_sessions_root
        fake_appdata = Path("/mock/users/test/AppData/Roaming")
        with mock.patch("sys.platform", "win32"), \
             mock.patch.dict(os.environ, {"APPDATA": str(fake_appdata)}):
            p = _claude_desktop_sessions_root()
            self.assertEqual(
                p,
                fake_appdata / "Claude" / "claude-code-sessions",
            )

    def test_make_stdin_fifo_missing_mkfifo(self):
        from unittest import mock
        from ccc_server.engines import _make_stdin_fifo
        orig_hasattr = hasattr
        def mock_hasattr(obj, name):
            if obj is os and name == "mkfifo":
                return False
            return orig_hasattr(obj, name)
        with mock.patch("builtins.hasattr", side_effect=mock_hasattr):
            fifo, fd = _make_stdin_fifo("sid-mock-test")
            self.assertIsNone(fifo)
            self.assertIsNone(fd)

    def test_hook_script_names_includes_session_start_and_notify(self):
        import server
        self.assertIn("session-start.py", server.CCC_HOOK_SCRIPT_NAMES)
        self.assertIn("_notify.py", server.CCC_HOOK_SCRIPT_NAMES)

    def test_codex_rollout_day_dirs_honors_codex_home(self):
        import server
        with tempfile.TemporaryDirectory() as td:
            custom_codex = Path(td) / "custom_codex"
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(custom_codex)}):
                day_dirs = server._codex_rollout_day_dirs()
                for d in day_dirs:
                    self.assertTrue(str(d).startswith(str(custom_codex / "sessions")), f"{d} not in {custom_codex}")

    def test_archive_codex_extra_keys_honors_codex_home(self):
        import server
        with tempfile.TemporaryDirectory() as td:
            custom_codex = Path(td) / "custom_codex"
            with mock.patch.dict(os.environ, {"CODEX_HOME": str(custom_codex)}):
                keys = server._archive_codex_extra_keys()
                self.assertIn(str(custom_codex / "sessions"), keys)

    def test_sync_hook_scripts_anchors_to_redirected_home(self):
        import server
        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td) / "fake_home"
            fake_home.mkdir()
            with mock.patch.object(server.Path, "home", return_value=fake_home):
                server._sync_hook_scripts()
                expected = fake_home / ".claude" / "command-center" / "hooks"
                self.assertTrue(expected.is_dir())
                self.assertTrue((expected / "session-start.py").is_file())

    def test_config_consent_default_ctx_anchors_to_redirected_home(self):
        from ccc_server import config_consent
        with tempfile.TemporaryDirectory() as td:
            fake_home = Path(td) / "fake_home"
            with mock.patch.object(config_consent.Path, "home", return_value=fake_home):
                ctx = config_consent.default_ctx()
                self.assertTrue(str(ctx.state_dir).startswith(str(fake_home)))
                self.assertTrue(str(ctx.hook_scripts_dir).startswith(str(fake_home)))


if __name__ == "__main__":
    unittest.main()
