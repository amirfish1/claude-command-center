"""Lightweight smoke tests that don't depend on optional plugins.

Anything Morning-specific lives in `tests/test_morning.py` which is
gitignored alongside the Morning plugin itself; CI never sees it.
"""
import importlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import unittest
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


class TestServerImports(unittest.TestCase):
    def test_server_imports_without_morning(self):
        """server.py must import cleanly even when the optional Morning
        plugin (morning.py, morning_store.py, etc.) isn't on disk. The
        plugin is gitignored — CI clones see no morning files at all."""
        # Ensure no stale module cached from a prior test run.
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "__version__"))
        self.assertIsInstance(server.__version__, str)
        self.assertRegex(server.__version__, r"^\d+\.\d+\.\d+")

    def test_open_session_in_claude_desktop_rejects_bad_input(self):
        """The helper exists and rejects empty / non-UUID session IDs
        without trying to spawn `open(1)`."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "open_session_in_claude_desktop"))
        # Empty
        r = server.open_session_in_claude_desktop("")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)
        # Not a UUID
        r = server.open_session_in_claude_desktop("not-a-uuid")
        self.assertFalse(r["ok"])
        self.assertIn("error", r)

    def test_open_session_in_codex_desktop_rejects_bad_input(self):
        """The helper exists and rejects empty / non-Codex session IDs
        without trying to spawn `open(1)`."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "open_session_in_codex_desktop"))
        with mock.patch.object(server.subprocess, "Popen") as popen:
            r = server.open_session_in_codex_desktop("")
            self.assertFalse(r["ok"])
            self.assertIn("error", r)
            with mock.patch.object(server, "_is_codex_session", return_value=False):
                r = server.open_session_in_codex_desktop("not-codex")
            self.assertFalse(r["ok"])
            self.assertIn("error", r)
            popen.assert_not_called()

    def test_morning_disabled_when_plugin_absent(self):
        """If morning.py isn't importable, MORNING_ENABLED must be False
        no matter what CCC_ENABLE_MORNING says."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        # Force-flag morning as missing by setting an env that doesn't
        # affect the actual import attempt — we just verify the gate
        # short-circuits when _MORNING_IMPORTABLE is False.
        server = importlib.import_module("server")
        if not server._MORNING_IMPORTABLE:
            self.assertFalse(server.MORNING_ENABLED,
                             "MORNING_ENABLED must be False when plugin missing")


class TestRunScript(unittest.TestCase):
    def test_run_script_syntax_is_valid(self):
        script = pathlib.Path(PROJECT_ROOT, "run.sh")
        result = subprocess.run(["bash", "-n", str(script)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_run_script_help_advertises_launchd_service(self):
        script = pathlib.Path(PROJECT_ROOT, "run.sh")
        result = subprocess.run(["bash", str(script), "--help"],
                                cwd=PROJECT_ROOT,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--install-service", result.stdout)
        self.assertIn("--uninstall-service", result.stdout)
        self.assertIn("--service-status", result.stdout)


class TestRepoContextHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="ccc-repo-context-home-")
        self._prev_home = os.environ.get("HOME")
        os.environ["HOME"] = str(pathlib.Path(self.tmp_home).resolve())
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.repo = pathlib.Path(self.tmp_home, "demo-repo").resolve()
        self.repo.mkdir()
        (self.repo / ".git").mkdir()

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._prev_home
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def test_valid_repo_path_is_accepted(self):
        self.assertEqual(self.server.resolve_repo_path(str(self.repo)), str(self.repo))

    def test_strips_ccc_session_state_instruction_from_visible_text(self):
        text = (
            "now to 00000000-0000-4000-8000-000000000001: "
            "/Users/example/.claude/command-center/pasted-images/paste-1.png\n\n"
            "Before your final reply, end with a block formatted EXACTLY like this "
            "(the Claude Command Center dashboard parses it):\n"
            "<session-state>\n"
            "DID: <one sentence>\n"
            "INSIGHT: <one sentence>\n"
            "NEXT_STEP_USER: <one sentence>\n"
            "</session-state>"
        )
        self.assertEqual(
            self.server._strip_ccc_session_state_instruction(text),
            "now to 00000000-0000-4000-8000-000000000001: "
            "/Users/example/.claude/command-center/pasted-images/paste-1.png",
        )

    def test_terminal_inject_strips_ccc_session_state_instruction(self):
        text = (
            "follow up\n\n"
            "Before your final reply, end with a block formatted EXACTLY like this:\n"
            "<session-state>\n"
            "DID: <one sentence>\n"
            "INSIGHT: <one sentence>\n"
            "NEXT_STEP_USER: <one sentence>\n"
            "</session-state>"
        )
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "/dev/ttys001",
                     "terminal_app": "Terminal",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "keystroke"},
             ) as inject:
            result = self.server._inject_text_into_session(
                "00000000-0000-4000-8000-000000000001",
                text,
            )
        self.assertTrue(result["ok"])
        inject.assert_called_once_with("/dev/ttys001", "Terminal", "follow up")

    def test_terminal_inject_queues_when_live_session_is_busy(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()
        try:
            with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
                 mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
                 mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(
                     self.server,
                     "session_live_status",
                     return_value={
                         "live": True,
                         "tty": "/dev/ttys001",
                         "terminal_app": "Terminal",
                         "status": "busy",
                         "pid": 123,
                     },
                 ), \
                 mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
                result = self.server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["via"], "terminal-queued")
            inject.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_terminal_inject_timeout_has_actionable_macos_error(self):
        timeout = subprocess.TimeoutExpired(cmd=["osascript", "-e", "secret"], timeout=5)
        with mock.patch.object(self.server.subprocess, "run", side_effect=timeout):
            result = self.server.inject_input_via_keystroke("/dev/ttys001", "Terminal", "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "macos_automation_timeout")
        self.assertIn("app_mode_loader", result["error"])
        self.assertIn("app_node", result["error"])
        self.assertNotIn("secret", result["error"])

    def test_terminal_inject_restores_focus_by_process_id(self):
        seen = {}

        def fake_run(args, **kwargs):
            seen["script"] = args[2]
            return subprocess.CompletedProcess(args, 0, stdout="ok\n", stderr="")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            result = self.server.inject_input_via_keystroke("/dev/ttys001", "Terminal", "hello")

        self.assertTrue(result["ok"])
        script = seen["script"]
        self.assertIn("unix id of first application process whose frontmost is true", script)
        self.assertIn("frontmost of first application process whose unix id is prevPid", script)
        self.assertNotIn("tell application prevApp", script)

    def test_spawn_session_preflights_missing_claude_cli(self):
        with mock.patch.object(
            self.server,
            "_resolve_claude_bin",
            return_value={
                "available": False,
                "bin": None,
                "code": "claude_unavailable",
                "reason": "Claude Code CLI not found",
            },
        ), mock.patch.object(
            self.server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)
        ), mock.patch.object(self.server.subprocess, "Popen") as popen:
            result = self.server.spawn_session(
                "do the thing",
                name="do the thing",
                cwd=str(self.repo),
                repo_path=str(self.repo),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        popen.assert_not_called()

    def test_spawn_session_accepts_unregistered_plain_cwd(self):
        scratch = pathlib.Path(self.tmp_home, "scratch-space").resolve()
        scratch.mkdir()
        with mock.patch.object(
            self.server,
            "_resolve_claude_bin",
            return_value={
                "available": False,
                "bin": None,
                "code": "claude_unavailable",
                "reason": "Claude Code CLI not found",
            },
        ), mock.patch.object(
            self.server, "_git_toplevel_for_existing_dir", return_value=None
        ), mock.patch.object(self.server.subprocess, "Popen") as popen:
            result = self.server.spawn_session(
                "do the thing",
                name="do the thing",
                cwd=str(scratch),
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        self.assertIn(str(scratch), self.server._load_custom_repos())
        popen.assert_not_called()

    def test_unknown_repo_path_is_rejected(self):
        unknown = pathlib.Path(self.tmp_home, "not-a-repo").resolve()
        unknown.mkdir()
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.resolve_repo_path(str(unknown))
        self.assertEqual(ctx.exception.code, "repo_not_allowed")

    def test_all_is_not_a_repo_path(self):
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.resolve_repo_path("ALL")
        self.assertEqual(ctx.exception.code, "invalid_repo_path")

    def test_ambiguous_context_returns_repo_required(self):
        with self.assertRaises(self.server.RepoContextError) as ctx:
            self.server.require_repo_context({}, {}, allow_session=False)
        self.assertEqual(ctx.exception.code, "repo_required")

    def test_session_id_resolves_repo_context(self):
        sid = "00000000-0000-4000-8000-000000000099"
        transcript = self.server._canonical_conversation_path(str(self.repo), sid)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(self.repo),
                "sessionId": sid,
                "message": {"role": "user", "content": "hello"},
            }) + "\n",
            encoding="utf-8",
        )

        ctx = self.server.repo_from_session(sid)
        self.assertEqual(ctx["repo_path"], str(self.repo))
        self.assertEqual(ctx["cwd"], str(self.repo))

    def test_session_cwd_relocates_after_folder_move(self):
        sid = "00000000-0000-4000-8000-000000000100"
        old_cwd = self.repo / "old folder" / "app"
        new_cwd = self.repo / "code" / "old-folder" / "app"
        moved_file = new_cwd / "src" / "main.py"
        moved_file.parent.mkdir(parents=True)
        moved_file.write_text("print('ok')\n", encoding="utf-8")

        transcript = self.server._canonical_conversation_path(str(self.repo), sid)
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            json.dumps({
                "type": "user",
                "timestamp": "2026-05-04T00:00:00.000Z",
                "cwd": str(old_cwd),
                "sessionId": sid,
                "gitBranch": "main",
                "message": {"role": "user", "content": "read src/main.py"},
            }) + "\n" +
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-05-04T00:00:01.000Z",
                "sessionId": sid,
                "message": {
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": str(old_cwd / "src" / "main.py")},
                    }],
                },
            }) + "\n",
            encoding="utf-8",
        )

        self.assertEqual(self.server.find_session_cwd(sid), str(new_cwd))
        ctx = self.server.repo_from_session(sid)
        self.assertEqual(ctx["repo_path"], str(self.repo))
        self.assertEqual(ctx["cwd"], str(new_cwd))
        row = next(r for r in self.server.find_conversations(str(self.repo))
                   if r["session_id"] == sid)
        self.assertEqual(row["session_cwd"], str(new_cwd))
        self.assertTrue(row["session_cwd_exists"])

    def test_cwd_context_uses_nearest_claude_marker_parent(self):
        project = pathlib.Path(self.tmp_home, "plain-project").resolve()
        cwd = project / "nested" / "tool"
        cwd.mkdir(parents=True)
        (project / ".claude").mkdir()

        ctx = self.server._resolve_cwd_context(str(cwd))
        self.assertEqual(ctx["repo_path"], str(project))
        self.assertEqual(ctx["cwd"], str(cwd))

    def test_repo_required_endpoint_and_switch_compatibility(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as missing:
                urllib.request.urlopen(base + "/api/term/cwd", timeout=5)
            self.assertEqual(missing.exception.code, 400)
            missing_body = missing.exception.read().decode("utf-8")
            missing.exception.close()
            self.assertIn("repo_required", missing_body)

            with urllib.request.urlopen(
                base + "/api/term/cwd?repo_path=" + urllib.parse.quote(str(self.repo)),
                timeout=5,
            ) as res:
                self.assertEqual(res.status, 200)
                self.assertIn(str(self.repo), res.read().decode("utf-8"))

            req = urllib.request.Request(
                base + "/api/repo/switch",
                data=json.dumps({"path": str(self.repo)}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as gone:
                urllib.request.urlopen(req, timeout=5)
            self.assertEqual(gone.exception.code, 410)
            gone_body = gone.exception.read().decode("utf-8")
            gone.exception.close()
            self.assertIn("repo_switch_removed", gone_body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

    def test_resolve_codex_bin_prefers_env_override(self):
        """`_resolve_codex_bin` must honour CCC_CODEX_BIN when it points
        at an executable file. Verifies the precedence head — env var
        always wins over `which codex` and the app-bundle fallback."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_codex_bin"))

        with tempfile.NamedTemporaryFile(prefix="codex-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = f.name
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {"CCC_CODEX_BIN": fake_bin}), \
                 mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"), \
                 mock.patch.object(server, "CODEX_APP_BUNDLE_PATH", "/sentinel/from/bundle"):
                result = server._resolve_codex_bin()
            # Env override must win over both the PATH lookup and the bundle path.
            self.assertEqual(result["bin"], fake_bin)
            self.assertEqual(result["source"], "env")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_resolve_codex_bin_returns_unavailable_when_missing(self):
        """When CCC_CODEX_BIN points at a non-existent path AND the
        Codex.app bundle is absent AND `which codex` finds nothing,
        the resolver must return {available: False, reason: ...}
        rather than raising."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.dict(os.environ, {"CCC_CODEX_BIN": "/definitely/does/not/exist/codex"}), \
             mock.patch.object(server.shutil, "which", return_value=None), \
             mock.patch.object(server, "CODEX_APP_BUNDLE_PATH", "/nope/does-not-exist"):
            result = server._resolve_codex_bin()
        self.assertFalse(result["available"])
        self.assertIn("reason", result)

    def test_resolve_claude_bin_prefers_env_override(self):
        """CCC_CLAUDE_BIN must win over PATH so launchd services can pin
        the same CLI path an interactive shell uses."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_claude_bin"))

        with tempfile.NamedTemporaryFile(prefix="claude-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = f.name
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {"CCC_CLAUDE_BIN": fake_bin}), \
                 mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"):
                result = server._resolve_claude_bin()
            self.assertEqual(result["bin"], fake_bin)
            self.assertEqual(result["source"], "env")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_resolve_claude_bin_returns_unavailable_for_bad_env_override(self):
        """A bad CCC_CLAUDE_BIN should fail clearly instead of falling
        through to another binary and hiding the service configuration error."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.dict(os.environ, {"CCC_CLAUDE_BIN": "/definitely/does/not/exist/claude"}), \
             mock.patch.object(server.shutil, "which", return_value="/sentinel/from/path"):
            result = server._resolve_claude_bin()
        self.assertFalse(result["available"])
        self.assertEqual(result.get("code"), "claude_unavailable")
        self.assertIn("CCC_CLAUDE_BIN", result.get("reason", ""))

    def test_spawn_session_codex_exists(self):
        """`spawn_session_codex` must exist alongside `spawn_session`
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_codex"))
        import inspect
        sig = inspect.signature(server.spawn_session_codex)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path"])

    def test_spawn_session_gemini_exists(self):
        """`spawn_session_gemini` must exist alongside the other engines
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_gemini"))
        import inspect
        sig = inspect.signature(server.spawn_session_gemini)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree"])

    def test_record_spawn_to_registry_persists_engine(self):
        """The on-disk spawn registry must round-trip an `engine` field
        so a CCC restart can branch claude-vs-codex reattach logic."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            registry_file = pathlib.Path(tmp) / "spawned-pids.json"
            orig = server.SPAWNED_PIDS_FILE
            server.SPAWNED_PIDS_FILE = registry_file
            try:
                server._record_spawn_to_registry(
                    pid=99999, name="t", log_path=pathlib.Path(tmp) / "x.log",
                    cwd=tmp, spawned_at="20260430T000000",
                    command_summary="test", fifo=None, engine="codex",
                )
                with registry_file.open() as f:
                    rows = json.load(f)
                self.assertEqual(rows[-1]["engine"], "codex")
            finally:
                server.SPAWNED_PIDS_FILE = orig

    def test_pid_is_engine_process_recognises_codex_and_gemini(self):
        """`_pid_is_engine_process` must accept an `engine` arg and match
        the right argv[0] basename for it."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_pid_is_engine_process"))

        def fake_run(args, **kw):
            class R: pass
            r = R(); r.returncode = 0; r.stdout = ""; r.stderr = ""
            if args[:2] == ["ps", "-p"]:
                pid = args[2]
                if pid == "11111":
                    r.stdout = "/usr/local/bin/claude -p --verbose\n"
                elif pid == "22222":
                    r.stdout = "/Applications/Codex.app/Contents/Resources/codex exec --json\n"
                elif pid == "33333":
                    r.stdout = "/usr/local/bin/node /usr/local/bin/gemini --output-format stream-json\n"
            return r

        with mock.patch.object(server.subprocess, "run", side_effect=fake_run):
            self.assertTrue(server._pid_is_engine_process(11111, "claude"))
            self.assertFalse(server._pid_is_engine_process(11111, "codex"))
            self.assertTrue(server._pid_is_engine_process(22222, "codex"))
            self.assertFalse(server._pid_is_engine_process(22222, "claude"))
            self.assertTrue(server._pid_is_engine_process(33333, "gemini"))
            self.assertFalse(server._pid_is_engine_process(33333, "codex"))

    def test_gemini_chat_parsing_usage_and_row_signals(self):
        """Gemini chat JSON should render as a first-class session row."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        chat = {
            "sessionId": sid,
            "startTime": "2026-05-04T01:00:00.000Z",
            "lastUpdated": "2026-05-04T01:02:00.000Z",
            "kind": "main",
            "messages": [
                {
                    "id": "u1",
                    "timestamp": "2026-05-04T01:00:00.000Z",
                    "type": "user",
                    "content": [{"text": "Create the probe file."}],
                },
                {
                    "id": "g1",
                    "timestamp": "2026-05-04T01:01:00.000Z",
                    "type": "gemini",
                    "content": "I created and committed the probe.",
                    "model": "gemini-test-model",
                    "tokens": {
                        "input": 1200,
                        "output": 30,
                        "cached": 200,
                        "thoughts": 5,
                        "tool": 0,
                        "total": 1235,
                    },
                    "toolCalls": [{
                        "id": "run_shell_command_1",
                        "name": "run_shell_command",
                        "args": {
                            "command": "printf 'ok\\n' > probe.txt && git add probe.txt && git commit -m \"probe: gemini\"",
                            "description": "Create and commit probe file.",
                        },
                        "status": "success",
                        "timestamp": "2026-05-04T01:01:10.000Z",
                        "result": [{
                            "functionResponse": {
                                "response": {
                                    "output": "Output: [feat/demo abc1234] probe: gemini\n 1 file changed, 1 insertion(+)"
                                }
                            }
                        }],
                    }],
                },
                {
                    "id": "g2",
                    "timestamp": "2026-05-04T01:02:00.000Z",
                    "type": "gemini",
                    "content": "Branch: feat/demo\nCommit: abc1234 probe: gemini\nWorktree: /tmp/example-worktree",
                    "model": "gemini-test-model",
                    "tokens": {
                        "input": 1300,
                        "output": 20,
                        "cached": 250,
                        "thoughts": 4,
                        "tool": 0,
                        "total": 1324,
                    },
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            gemini_home = pathlib.Path(tmp) / ".gemini"
            project = gemini_home / "tmp" / "example-project"
            chats = project / "chats"
            chats.mkdir(parents=True)
            (project / ".project_root").write_text("/tmp/example-repo")
            chat_path = chats / "session-2026-05-04T01-00-aaaaaaaa.json"
            chat_path.write_text(json.dumps(chat))
            orig_home = server.GEMINI_HOME
            server.GEMINI_HOME = gemini_home
            try:
                self.assertTrue(server._is_gemini_session(sid))
                parsed = server.parse_conversation(sid)
                usage = server.extract_session_usage(sid)
                rows = server.find_gemini_conversations(include_old=True, repo_only=False)
            finally:
                server.GEMINI_HOME = orig_home

        self.assertGreaterEqual(len(parsed["events"]), 4)
        self.assertEqual(usage["latest_input_tokens"], 1300)
        self.assertEqual(usage["total_cache_read_tokens"], 450)
        self.assertEqual(usage["model"], "gemini-test-model")
        row = rows[0]
        self.assertEqual(row["source"], "gemini")
        self.assertTrue(row["has_edit"])
        self.assertTrue(row["has_commit"])
        self.assertEqual(row["effective_branch"], "feat/demo")
        self.assertEqual(row["session_cwd"], "/tmp/example-worktree")

    def test_shell_command_signals_detect_real_git_subcommands(self):
        cases = [
            ("git push", {"push": True}),
            ("git -C /tmp/repo push origin HEAD", {"push": True, "external_cd": True}),
            ("git -c user.name=Bot commit -m ok && git push", {"commit": True, "push": True}),
            ("command git commit -m ok", {"commit": True}),
            ("env GIT_DIR=/tmp/repo/.git git push", {"push": True}),
            ("bash -lc 'git push'", {"push": True}),
            ("gh --repo owner/repo pr create --title ok", {"pr": True}),
        ]
        for cmd, expected in cases:
            with self.subTest(cmd=cmd):
                signals = self.server._shell_command_signals(cmd)
                for key, value in expected.items():
                    self.assertEqual(signals[key], value)

    def test_shell_command_signals_ignore_git_text_in_other_commands(self):
        cases = [
            'rg -n "git push" server.py',
            'grep "git commit" rollout.jsonl',
            'echo "git push"',
            'python3 - <<\'PY\'\nprint("git push")\nPY',
            'git status | rg "push"',
        ]
        for cmd in cases:
            with self.subTest(cmd=cmd):
                signals = self.server._shell_command_signals(cmd)
                self.assertFalse(signals["commit"])
                self.assertFalse(signals["push"])

        signals = self.server._shell_command_signals(
            'git commit -m "document git push workflow"'
        )
        self.assertTrue(signals["commit"])
        self.assertFalse(signals["push"])

    def test_shell_command_signals_resolve_relative_worktree_add(self):
        base = pathlib.Path(self.tmp_home, "repo").resolve()
        base.mkdir()
        expected = base.parent / "repo-wt-ui"

        signals = self.server._shell_command_signals(
            "git worktree add -b fix/worktree-ui ../repo-wt-ui origin/main",
            base_cwd=str(base),
        )

        self.assertEqual(signals["worktree_branch"], "fix/worktree-ui")
        self.assertEqual(signals["worktree_path"], str(expected.resolve()))

    def test_codex_tail_meta_resolves_relative_worktree_add_from_workdir(self):
        base = pathlib.Path(self.tmp_home, "repo").resolve()
        base.mkdir()
        expected = base.parent / "repo-wt-ui"
        event = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_worktree",
                "arguments": json.dumps({
                    "cmd": "git worktree add -b fix/worktree-ui ../repo-wt-ui origin/main",
                    "workdir": str(base),
                }),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rollout.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_codex_tail_meta(path)

        self.assertEqual(meta["tail_branch"], "fix/worktree-ui")
        self.assertEqual(meta["tail_worktree_path"], str(expected.resolve()))

    def test_workspace_uses_explicit_worktree_tail_hint(self):
        worktree = pathlib.Path(self.tmp_home, "repo-wt-ui").resolve()
        worktree.mkdir()
        (worktree / ".git").write_text("gitdir: /tmp/fake/worktrees/repo-wt-ui\n")

        with mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "_session_tail_worktree_hint",
                 return_value={
                     "path": str(worktree),
                     "branch": "fix/worktree-ui",
                     "source": "worktree-add",
                 },
             ), \
             mock.patch.object(self.server, "_infer_effective_repo") as infer:
            workspace = self.server.extract_session_workspace(
                "00000000-0000-4000-8000-000000000001"
            )

        infer.assert_not_called()
        self.assertEqual(workspace["effective_cwd"], str(worktree))
        self.assertEqual(workspace["effective_branch"], "fix/worktree-ui")
        self.assertEqual(workspace["effective_kind"], "worktree")
        self.assertEqual(workspace["effective_source"], "worktree-add")

    def test_tail_meta_ignores_bash_search_for_git_push(self):
        event = {
            "type": "assistant",
            "timestamp": "2026-05-11T12:00:00.000Z",
            "message": {
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": 'rg -n "git push" server.py'},
                }],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "s.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_tail_meta(path)

        self.assertFalse(meta["has_commit"])
        self.assertFalse(meta["has_push"])

    def test_codex_tail_meta_ignores_exec_search_for_git_push(self):
        event = {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call_1",
                "arguments": json.dumps({"cmd": 'rg -n "git push" server.py'}),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "rollout.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = self.server._extract_codex_tail_meta(path)

        self.assertFalse(meta["has_commit"])
        self.assertFalse(meta["has_push"])

    def test_reattach_spawned_orphans_defaults_legacy_rows_to_claude(self):
        """A registry row written before the `engine` field existed
        must reattach as engine='claude' — not raise KeyError, not
        silently drop the row."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            registry_file = pathlib.Path(tmp) / "spawned-pids.json"
            log_file = pathlib.Path(tmp) / "fake.log"
            log_file.write_text("")
            # Legacy row — no `engine` key. PID is the current process so
            # the os.kill(pid, 0) liveness check succeeds without faking.
            legacy = [{
                "pid": os.getpid(),
                "session_id": None,
                "name": "legacy",
                "log": str(log_file),
                "fifo": None,
                "cwd": tmp,
                "spawned_at": "20260101T000000",
                "command_summary": "old row",
            }]
            registry_file.write_text(json.dumps(legacy))
            orig_registry = server.SPAWNED_PIDS_FILE
            orig_sessions = list(server._spawned_sessions)
            server.SPAWNED_PIDS_FILE = registry_file
            server._spawned_sessions.clear()
            try:
                # Bypass the real ps-grep — current pid isn't a `claude`
                # process, so without a stub it would be dropped.
                with mock.patch.object(server, "_pid_is_engine_process", return_value=True):
                    server._reattach_spawned_orphans()
                self.assertEqual(len(server._spawned_sessions), 1)
                self.assertEqual(server._spawned_sessions[0]["engine"], "claude")
            finally:
                server.SPAWNED_PIDS_FILE = orig_registry
                server._spawned_sessions.clear()
                server._spawned_sessions.extend(orig_sessions)


    def test_reveal_file_route_registered(self):
        """Smoke check: POST /api/reveal-file branch present in do_POST."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn('"/api/reveal-file"', src)
        # Defense-in-depth: extension clamp must be referenced near the
        # endpoint. Cheap signal that the security control wasn't dropped.
        idx = src.find('"/api/reveal-file"')
        self.assertGreater(idx, 0)
        nearby = src[idx:idx + 2000]
        self.assertIn("FILE_EXT_TO_CATEGORY", nearby,
                      "extension clamp missing near /api/reveal-file route")

    def test_open_target_resolves_relative_session_cwd_markdown(self):
        """Inline transcript links should resolve from the selected session cwd."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            checkpoint = session_cwd / ".claude" / "team-checkpoint.md"
            checkpoint.write_text("# checkpoint\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    ".claude/team-checkpoint.md",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), checkpoint.resolve())
        self.assertFalse(result["core_sandbox"])
        self.assertTrue(result["session_sandbox"])

    def test_open_launch_allows_markdown_session_cwd_files(self):
        """Markdown transcript links may launch externally via macOS open."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            doc = session_cwd / "notes.md"
            doc.write_text("# notes\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "notes.md",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_open_target_strips_markdown_angle_wrapped_paths(self):
        """Markdown links to paths with spaces use <...>; /api/open should unwrap."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "non-code projects" / "ADS"
            repo.mkdir(parents=True)
            (repo / ".git").mkdir()
            report_dir = repo / "final" / "posthog-export"
            report_dir.mkdir(parents=True)
            report = report_dir / "paid-meta-posthog-analysis-20260512.md"
            report.write_text("# report\n")

            result = server._resolve_open_target(
                f"<{report}>",
                cwd=str(repo),
                repo_path=str(repo),
            )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), report.resolve())
        self.assertTrue(result["core_sandbox"])

    def test_open_target_falls_back_when_archive_repo_path_is_virtual(self):
        """Archive rows may pass a display slug as repo_path; cwd should win."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            doc = session_cwd / "notes.md"
            doc.write_text("# notes\n")

            result = server._resolve_open_target(
                str(doc),
                session_id="11111111-2222-3333-4444-555555555555",
                cwd=str(session_cwd),
                repo_path="-virtual-archive-folder",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(pathlib.Path(result["path"]), doc.resolve())

    def test_open_target_allows_exact_session_tool_file_outside_cwd(self):
        """Files explicitly touched by the selected session are revealable."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            external = root / "library" / "stack.md"
            external.parent.mkdir()
            external.write_text("# stack\n")

            with mock.patch.object(
                server,
                "_scan_session_tool_paths",
                return_value=([str(external)], []),
            ):
                result = server._resolve_open_target(
                    str(external),
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])
        self.assertTrue(result["session_file_sandbox"])
        self.assertTrue(server._open_launch_allowed(result))

    def test_open_target_blocks_unreferenced_file_outside_cwd(self):
        """The session tool exception is exact-path, not a directory escape."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo / "work"
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            external = root / "library" / "stack.md"
            external.parent.mkdir()
            external.write_text("# stack\n")

            with mock.patch.object(
                server,
                "_scan_session_tool_paths",
                return_value=([], []),
            ):
                result = server._resolve_open_target(
                    str(external),
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 403)
        self.assertIn("outside repo/session sandbox", result["error"])

    def test_open_launch_blocks_non_markdown_session_cwd_files(self):
        """Launching outside the repo/log sandbox stays limited to markdown."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            image = session_cwd / "screenshot.png"
            image.write_bytes(b"not really a png")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "screenshot.png",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(server._open_launch_allowed(result))

    def test_open_target_allows_command_center_pasted_images(self):
        """CCC-uploaded pasted images should be revealable from transcript links."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")

        result = server._resolve_open_target(
            str(image),
            cwd=str(self.repo),
            repo_path=str(self.repo),
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])
        self.assertTrue(result["pasted_image_sandbox"])
        self.assertFalse(server._open_launch_allowed(result))

    def test_spawn_codex_attaches_command_center_pasted_images(self):
        """Pasted image paths in Codex prompts should be sent as --image args."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        proc = mock.Mock(pid=4242)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_codex_bin",
                return_value={"available": True, "bin": "/usr/bin/codex-test"},
            ), mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.spawn_session_codex(
                    f"inspect this screenshot {image}",
                    name="image prompt",
                    repo_path=str(self.repo),
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("--image", cmd)
        self.assertEqual(cmd[cmd.index("--image") + 1], str(image))

    def test_resume_codex_attaches_command_center_pasted_images(self):
        """Resumed Codex sessions need the same pasted-image attachment path."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        sid = "00000000-0000-4000-8000-000000000003"
        proc = mock.Mock(pid=4243)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_codex_bin",
                return_value={"available": True, "bin": "/usr/bin/codex-test"},
            ), mock.patch.object(server, "_codex_thread_row", return_value={"cwd": str(self.repo)}), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.resume_session_codex(sid, f"look at {image}")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        self.assertIn("--image", cmd)
        self.assertEqual(cmd[cmd.index("--image") + 1], str(image))
        self.assertIn(sid, cmd)

    def test_open_target_blocks_executable_session_cwd_files(self):
        """Session-cwd fallback must not turn /api/open into script launch/reveal."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            repo.mkdir()
            (repo / ".git").mkdir()
            session_cwd = root / "session"
            (session_cwd / ".claude").mkdir(parents=True)
            script = session_cwd / "run.sh"
            script.write_text("#!/bin/sh\nexit 0\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "run.sh",
                    session_id="11111111-2222-3333-4444-555555555555",
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 403)
        self.assertIn("extension not allowed", result["error"])

    def test_markdown_path_links_request_external_open(self):
        """The transcript click handler asks /api/open to launch markdown."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("function _isMarkdownPath", js)
        self.assertIn("function normalizeMarkdownLinkTarget", js)
        self.assertIn("payload.launch = true", js)

    def test_archive_progress_does_not_replace_search_empty_state(self):
        """Background archive refresh must not clobber no-match search results."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn(".archive-loading-placeholder, .archive-loading-stages", js)
        self.assertIn("archive-empty-state archive-loading-placeholder", js)
        self.assertIn("No conversations match your filter.", js)
        self.assertNotIn(".archive-empty-state, .archive-loading-stages", js)

    def test_original_ask_renders_pasted_images_inline(self):
        """Pasted-image references should become images in the ask panels."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("PASTED_IMG_MD_LINK_RE", js)
        self.assertIn("function pastedImageTag", js)
        self.assertIn("/api/pasted-image?path=", js)
        self.assertIn("const imagesHtml = renderImageDescriptors(ev.images);", js)
        self.assertIn("h += imagesHtml;", js)

    def test_archive_search_refresh_preserves_scroll(self):
        """Periodic archive refreshes should not snap active search results."""
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        self.assertIn("let _lastArchiveRenderFilter = null;", js)
        self.assertIn("function _captureArchiveListScroll", js)
        self.assertIn("function _restoreArchiveListScroll", js)
        self.assertIn("_lastArchiveRenderFilter = q;", js)

    def test_files_endpoint_route_registered(self):
        """Smoke check: GET /api/conversations/<id>/files dispatcher
        branch must be present in the do_GET source. Route registration
        in this codebase is by literal regex string, so a substring grep
        is the cheapest assertion."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn("/api/conversations/[a-f0-9-]+/files", src)

    def test_session_initial_scan_keeps_recent_and_live_rows(self):
        """Initial /api/sessions scans should avoid cold history while keeping
        live sessions even when their transcript mtime is old."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        now = time.time()
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            files = []
            for name, age in (
                ("old-cold", 20_000),
                ("old-live", 20_000),
                ("recent-newest", 10),
                ("recent-older", 20),
            ):
                p = root / f"{name}.jsonl"
                p.write_text("{}\n")
                os.utime(p, (now - age, now - age))
                files.append(p)

            selected, meta = server._filter_conversation_jsonls(
                files,
                include_old=False,
                always_include_sids={"old-live"},
                cutoff_ts=now - 1000,
                max_files=2,
            )

        stems = [p.stem for p in selected]
        self.assertEqual(len(stems), 2)
        self.assertIn("old-live", stems)
        self.assertIn("recent-newest", stems)
        self.assertNotIn("old-cold", stems)
        self.assertTrue(meta["limited"])

    def test_session_usage_falls_back_to_diagnostic_context_sample(self):
        """Newer Claude transcripts can omit `message.usage` while still
        carrying a diagnostic context-size hint. The footer should use that
        instead of showing no context data at all."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        event = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
                "diagnostics": {
                    "cache_miss_reason": {
                        "type": "tools_changed",
                        "cache_missed_input_tokens": 57261,
                    },
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(json.dumps(event) + "\n")
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(usage["latest_input_tokens"], 57261)
        self.assertEqual(usage["peak_input_tokens"], 57261)
        self.assertEqual(usage["model"], "claude-opus-4-7")

    def test_tail_meta_extracts_assistant_model(self):
        """Conversation rows should carry the model even before the usage
        endpoint finishes, so the footer has a stable fallback."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        event = {
            "type": "assistant",
            "timestamp": "2026-05-03T12:00:00.000Z",
            "message": {
                "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "done"}],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "s.jsonl"
            path.write_text(json.dumps(event) + "\n")
            meta = server._extract_tail_meta(path)

        self.assertEqual(meta["model"], "claude-sonnet-4-6")


    def test_coordinate_sessions_helper_exists(self):
        """_coordinate_sessions must exist and reject empty topic."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_coordinate_sessions"))
        result = server._coordinate_sessions({"session_ids": ["abc"], "topic": ""})
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_group_chat_read_helper_exists_and_rejects_traversal(self):
        """_group_chat_read must exist and block path traversal outside group-chats/."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_group_chat_read"))
        result, forbidden = server._group_chat_read("/etc/passwd")
        self.assertIsNone(result)
        self.assertEqual(forbidden, "forbidden")

    def test_group_chat_post_helper_exists_and_rejects_traversal(self):
        """_group_chat_post must exist and block writes outside group-chats/."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_group_chat_post"))
        result = server._group_chat_post("/etc/passwd", "hacked")
        self.assertFalse(result["ok"])
        self.assertIn("forbidden", result.get("error", ""))


class TestModelPicker(unittest.TestCase):
    def test_short_model_alias_strips_claude_prefix_and_1m_suffix(self):
        """`/model` slash command takes the alias form, not the full id —
        the helper has to round-trip both shapes consistently."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        self.assertEqual(server._short_model_alias("claude-sonnet-4-6"), "sonnet-4-6")
        self.assertEqual(server._short_model_alias("claude-sonnet-4-6[1m]"), "sonnet-4-6")
        self.assertEqual(server._short_model_alias("opus-4-7"), "opus-4-7")
        self.assertEqual(server._short_model_alias("sonnet"), "sonnet")
        self.assertEqual(server._short_model_alias(""), "")
        self.assertEqual(server._short_model_alias(None), "")

    def test_build_slash_model_command_appends_1m_suffix_when_requested(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        self.assertEqual(server._build_slash_model_command("opus-4-7", False), "/model opus-4-7")
        self.assertEqual(server._build_slash_model_command("opus-4-7", True), "/model opus-4-7[1m]")
        self.assertEqual(server._build_slash_model_command("claude-sonnet-4-6", True), "/model sonnet-4-6[1m]")
        self.assertEqual(server._build_slash_model_command("", True), "")

    def test_session_override_roundtrip_through_sidecar(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "session-overrides.json"
            orig = server.SESSION_OVERRIDES_FILE
            server.SESSION_OVERRIDES_FILE = path
            try:
                self.assertIsNone(server._get_session_override("sid-1"))
                server._set_session_override("sid-1", "claude-sonnet-4-6", True, "claude")
                got = server._get_session_override("sid-1")
                self.assertEqual(got["model"], "claude-sonnet-4-6")
                self.assertTrue(got["context_1m"])
                self.assertEqual(got["engine"], "claude")
                server._clear_session_override("sid-1")
                self.assertIsNone(server._get_session_override("sid-1"))
            finally:
                server.SESSION_OVERRIDES_FILE = orig

    def test_session_model_route_registered_and_check_same_origin_gates_post(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        # Routes registered
        self.assertIn("/api/session/[a-zA-Z0-9-]+/model", src)
        self.assertIn("/api/session/[a-zA-Z0-9-]+/model/clear", src)
        # do_POST gates everything through _check_same_origin first
        post_idx = src.find("def do_POST")
        self.assertGreater(post_idx, 0)
        self.assertIn("_check_same_origin", src[post_idx:post_idx + 200])

    def test_extract_session_slash_commands_from_init_event(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "project"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps({
                    "type": "system",
                    "subtype": "init",
                    "slash_commands": [
                        "/compact",
                        {"name": "project:ship", "description": "Ship this repo"},
                        {"command": "/review", "purpose": "Review changes"},
                    ],
                }) + "\n"
            )
            orig = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                result = server.extract_session_slash_commands(sid)
            finally:
                server.PROJECTS_ROOT = orig

        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "transcript")
        commands = {c["name"]: c.get("description", "") for c in result["commands"]}
        self.assertIn("/compact", commands)
        self.assertIn("/mcp", commands)
        self.assertEqual(commands["/project:ship"], "Ship this repo")
        self.assertEqual(commands["/review"], "Review changes")

    def test_slash_command_files_and_skills_are_discovered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            command_dir = root / "commands"
            command_dir.mkdir()
            (command_dir / "ship.md").write_text("# Ship\n\nRun the release flow.\n")
            nested = command_dir / "commit-commands"
            nested.mkdir()
            (nested / "commit.md").write_text("---\ndescription: Commit current work\n---\n")
            (command_dir / "old.md.bak").write_text("# Ignore\n")

            skill_dir = root / "skills"
            skill = skill_dir / "screenshot"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("---\ndescription: Inspect screenshots\n---\n")

            commands = server._merge_slash_commands(
                server._slash_commands_from_command_dir(command_dir),
                server._slash_commands_from_command_dir(command_dir, prefix="plugin-name"),
                server._slash_commands_from_skill_dir(skill_dir),
            )

        names = {c["name"]: c.get("description", "") for c in commands}
        self.assertEqual(names["/ship"], "Ship")
        self.assertEqual(names["/commit-commands:commit"], "Commit current work")
        self.assertEqual(names["/plugin-name:ship"], "Ship")
        self.assertEqual(names["/screenshot"], "Inspect screenshots")
        self.assertNotIn("/old.md", names)

    def test_session_slash_commands_route_registered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        self.assertIn("/api/session/[a-zA-Z0-9-]+/slash-commands", src)

    def test_extract_session_usage_resets_at_compact_boundary(self):
        """`/compact` emits a `compact_boundary` system event; assistant
        turns before that boundary no longer contribute to the live
        context window. The pre-fix behavior accumulated peak across
        the whole file, overstating usage."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-666666666666"
        big_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "before compact"}],
                "usage": {
                    "input_tokens": 80_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 100_000,
                    "output_tokens": 500,
                },
            },
        }
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "manual", "preTokens": 180_500},
        }
        small_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "after compact"}],
                "usage": {
                    "input_tokens": 1_200,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 9_000,
                    "output_tokens": 80,
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps(big_turn) + "\n"
                + json.dumps(boundary) + "\n"
                + json.dumps(small_turn) + "\n"
            )
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False), \
                     mock.patch.object(server, "_load_desktop_app_metadata", return_value={}):
                    usage = server.extract_session_usage(sid)
            finally:
                server.PROJECTS_ROOT = orig_root
        # latest = post-compact small turn's window
        self.assertEqual(usage["latest_input_tokens"], 1_200 + 9_000)
        # peak resets at the boundary, so it's the post-compact peak — NOT the big pre-compact value
        self.assertEqual(usage["peak_input_tokens"], 1_200 + 9_000)
        self.assertEqual(usage["compact_count"], 1)


class TestGroupChatSidecarHelpers(unittest.TestCase):
    """Cover the small helpers that load/merge sidecar JSON, list chats,
    and flip the archived flag. Uses a tempdir-backed fake group-chats dir
    so we don't touch the user's real ~/.claude/group-chats."""

    def _setup_fake_dir(self, server, tmpdir):
        """Patch the helpers to look at a tempdir instead of ~/.claude/group-chats."""
        gcd = pathlib.Path(tmpdir) / "group-chats"
        gcd.mkdir()
        # Patch os.path.expanduser via monkeypatching os.path.expanduser only
        # for the specific path. Simpler: just write into the real ~ via a
        # subdirectory the helpers don't know about. The helpers all derive
        # the dir from os.path.expanduser("~/.claude/group-chats") — so
        # monkey-patch that lookup.
        return gcd

    def test_sidecar_round_trip(self):
        """_load_group_chat_sidecar / _update_group_chat_sidecar must merge
        fields atomically and survive missing files."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "demo.md")
            with open(md, "w") as fh:
                fh.write("# demo\n")
            # No sidecar yet → load returns {}
            self.assertEqual(server._load_group_chat_sidecar(md), {})
            # Update creates the sidecar
            ok = server._update_group_chat_sidecar(
                md, archived=True, archived_at=1234.5, topic="hi"
            )
            self.assertTrue(ok)
            data = server._load_group_chat_sidecar(md)
            self.assertEqual(data.get("topic"), "hi")
            self.assertIs(data.get("archived"), True)
            self.assertEqual(data.get("archived_at"), 1234.5)
            # Subsequent merge preserves prior fields
            server._update_group_chat_sidecar(md, archived=False)
            data2 = server._load_group_chat_sidecar(md)
            self.assertEqual(data2.get("topic"), "hi")
            self.assertIs(data2.get("archived"), False)

    def test_message_count_counts_h2_lines(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write("# header\n## one\nbody\n## two\n## three — author\n")
            self.assertEqual(server._group_chat_message_count(md), 3)

    def test_latest_message_snapshot_uses_latest_post(self):
        """The injected wake-up hint should show the latest post only."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write(
                    "# header\n"
                    "## 2026-05-13 10:00 PDT — aaaaaaaa: ALPHA\n\n"
                    "old body\n\n"
                    "## 2026-05-13 10:01 PDT — Human\n\n"
                    "new body\n"
                    "## markdown subheading inside the message\n"
                    "more detail\n"
                    "> _2026-05-13 10:02:00 PDT — system: pinged `ALPHA`_\n"
                )
            snapshot = server._group_chat_latest_message_snapshot(md)
            self.assertIn("Human", snapshot)
            self.assertIn("new body", snapshot)
            self.assertIn("markdown subheading inside the message", snapshot)
            self.assertNotIn("old body", snapshot)
            self.assertNotIn("system: pinged", snapshot)

    def test_group_chat_inject_text_includes_latest_snapshot(self):
        """Participants get a bounded advisory snapshot in the injection."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            md = os.path.join(tmp, "x.md")
            with open(md, "w") as fh:
                fh.write(
                    "# header\n"
                    "## 2026-05-13 10:01 PDT — Human\n\n"
                    "please respond\n"
                )
            text = server._group_chat_inject_text(
                md, 'topic with "quotes"', "topic", "abc12345-session"
            )
            self.assertIn(f'/group-chat chat="{md}"', text)
            self.assertIn('topic="topic with \\"quotes\\""', text)
            self.assertIn('sid="abc12345-session"', text)
            self.assertIn("CCC latest chat snapshot", text)
            self.assertIn("please respond", text)

    def test_resolve_group_chat_path_rejects_outside_dir(self):
        """The path validator must clamp to ~/.claude/group-chats/ and
        reject anything outside (no path traversal)."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        # Outside the group-chats dir → ""
        self.assertEqual(server._resolve_group_chat_path("/etc/passwd"), "")
        self.assertEqual(server._resolve_group_chat_path(""), "")
        self.assertEqual(server._resolve_group_chat_path("../../../tmp/x.md"), "")


class TestHealthcheck(unittest.TestCase):
    def test_healthcheck_returns_structured_result(self):
        """_run_healthcheck must always return a dict with 'checks' and
        'overall' keys, even on a fresh install with nothing configured."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        result = server._run_healthcheck()
        self.assertIn("checks", result)
        self.assertIn("overall", result)
        self.assertIn(result["overall"], ("ok", "warn", "error"))
        self.assertIsInstance(result["checks"], list)
        self.assertGreater(len(result["checks"]), 0)


if __name__ == "__main__":
    unittest.main()
