"""Lightweight smoke tests that don't depend on optional plugins.

Anything Morning-specific lives in `tests/test_morning.py` which is
gitignored alongside the Morning plugin itself; CI never sees it.
"""
import importlib
import fcntl
import json
import os
import pathlib
import shutil
import socket
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

    def test_claude_append_prompt_discourages_blocking_recursive_grep(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        args = server._claude_session_state_args()

        self.assertEqual(args[0], "--append-system-prompt")
        self.assertIn("Do not run `grep -r`", args[1])
        self.assertIn(".claude/logs/*.stdin", args[1])
        self.assertIn("<session-state>", args[1])

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

    def test_page_annotation_is_bounded_and_persisted(self):
        """Browser annotations should store local context without requiring
        screenshot support or touching the real user state directory."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.ANNOTATIONS_FILE
            old_dir = server.ANNOTATION_SCREENSHOT_DIR
            server.ANNOTATIONS_FILE = pathlib.Path(td) / "annotations.json"
            server.ANNOTATION_SCREENSHOT_DIR = pathlib.Path(td) / "annotation-screenshots"
            try:
                result = server.create_annotation({
                    "note": "Check this button state",
                    "url": "http://127.0.0.1:8090/",
                    "title": "Claude Command Center",
                    "rect": {"x": 10, "y": 20, "width": 120, "height": 32},
                    "document_rect": {"x": 10, "y": 200, "width": 120, "height": 32},
                    "viewport": {"width": 1440, "height": 900, "device_pixel_ratio": 2},
                    "element": {
                        "tag": "button",
                        "selector": "#annotationStartBtn",
                        "text": "Annotate",
                    },
                    "nearby_text": "surrounding context",
                    "html_excerpt": "<button>" + ("x" * 9000) + "</button>",
                    "capture_screen": False,
                })
                self.assertTrue(result["ok"])
                saved = server.list_annotations(limit=10)
                self.assertEqual(saved["count"], 1)
                ann = saved["annotations"][0]
                self.assertEqual(ann["note"], "Check this button state")
                self.assertEqual(ann["element"]["selector"], "#annotationStartBtn")
                self.assertLessEqual(len(ann["html_excerpt"]), 8001)
                self.assertNotIn("screenshot_path", ann)
            finally:
                server.ANNOTATIONS_FILE = old_file
                server.ANNOTATION_SCREENSHOT_DIR = old_dir

    def test_screen_annotation_saves_local_screenshot(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")

        with tempfile.TemporaryDirectory() as td:
            old_file = server.ANNOTATIONS_FILE
            old_dir = server.ANNOTATION_SCREENSHOT_DIR
            server.ANNOTATIONS_FILE = pathlib.Path(td) / "annotations.json"
            server.ANNOTATION_SCREENSHOT_DIR = pathlib.Path(td) / "annotation-screenshots"
            try:
                result = server.create_annotation({
                    "note": "Look at this screen region",
                    "source": "screen-capture",
                    "screenshot_b64": "dGVzdC1pbWFnZS1ieXRlcw==",
                })
                self.assertTrue(result["ok"])
                ann = result["annotation"]
                self.assertEqual(ann["source"], "screen-capture")
                shot = pathlib.Path(ann["screenshot_path"])
                self.assertTrue(shot.is_file())
                self.assertEqual(shot.read_bytes(), b"test-image-bytes")
            finally:
                server.ANNOTATIONS_FILE = old_file
                server.ANNOTATION_SCREENSHOT_DIR = old_dir

    def test_annotation_notes_render_screenshots(self):
        app_js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text(encoding="utf-8")
        self.assertIn("ann-note-shot", app_js)
        self.assertIn("/api/local-image?path=", app_js)


class TestPrStateResolution(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.server._PR_STATE_CACHE.clear()

    def tearDown(self):
        self.server._PR_STATE_CACHE.clear()

    def test_pr_state_falls_back_to_gh_api(self):
        url = "https://github.com/octo-org/demo-repo/pull/25"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1:3] == ["pr", "view"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
            if cmd[1] == "api":
                self.assertIn("repos/octo-org/demo-repo/pulls/25", cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="MERGED\n", stderr="")
            raise AssertionError(f"unexpected command: {cmd}")

        with mock.patch.object(self.server.shutil, "which",
                               return_value="/opt/homebrew/bin/gh"), \
             mock.patch.object(self.server.subprocess, "run",
                               side_effect=fake_run):
            self.assertEqual(self.server._get_pr_state(url), "MERGED")
            self.assertEqual(self.server._get_pr_state(url), "MERGED")

        self.assertEqual(len(calls), 2, "second lookup should hit cache")
        cached = self.server._PR_STATE_CACHE[url]
        self.assertEqual(cached["state"], "MERGED")
        self.assertEqual(cached["ttl"], self.server._PR_STATE_TTL)

    def test_pr_state_failures_use_short_ttl(self):
        url = "https://github.com/octo-org/demo-repo/pull/25"

        def fail_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

        with mock.patch.object(self.server.shutil, "which",
                               return_value="/opt/homebrew/bin/gh"), \
             mock.patch.object(self.server.subprocess, "run",
                               side_effect=fail_run):
            self.assertIsNone(self.server._get_pr_state(url))

        cached = self.server._PR_STATE_CACHE[url]
        self.assertIsNone(cached["state"])
        self.assertEqual(cached["ttl"], self.server._PR_STATE_FAILURE_TTL)


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

    def test_session_registry_accepts_native_claude_binary_path(self):
        sid = "00000000-0000-4000-8000-000000000001"
        sessions_dir = pathlib.Path(self.server.SESSIONS_REGISTRY)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "123.json").write_text(json.dumps({
            "pid": 123,
            "sessionId": sid,
            "cwd": str(self.repo),
            "kind": "bg",
        }))
        native_bin = pathlib.Path(
            self.tmp_home,
            ".local",
            "share",
            "claude",
            "versions",
            "2.1.144",
        )

        def fake_run(args, **kwargs):
            if args == ["ps", "-A", "-o", "pid=,comm="]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=f"123 {native_bin}\n456 /usr/bin/python3\n",
                    stderr="",
                )
            raise AssertionError(f"unexpected command: {args}")

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run):
            registry = self.server._load_session_registry()

        self.assertIn(sid, registry)
        self.assertEqual(registry[sid]["pid"], 123)

    def test_daemon_socket_allows_claude_tmp_path(self):
        allowed = f"/tmp/cc-daemon-{os.getuid()}/abc/spare/session.pty.sock"
        denied = f"/tmp/not-cc-daemon-{os.getuid()}/session.pty.sock"

        self.assertTrue(self.server._daemon_socket_path_allowed(allowed))
        self.assertFalse(self.server._daemon_socket_path_allowed(denied))

    def test_background_agent_pty_inject_frames_paste_and_submit(self):
        base = pathlib.Path("/tmp", f"cc-daemon-{os.getuid()}")
        base.mkdir(parents=True, exist_ok=True)
        frames = []
        errors = []

        def recv_exact(conn, n):
            chunks = []
            remaining = n
            while remaining:
                chunk = conn.recv(remaining)
                if not chunk:
                    raise EOFError("socket closed")
                chunks.append(chunk)
                remaining -= len(chunk)
            return b"".join(chunks)

        with tempfile.TemporaryDirectory(dir=base) as td:
            sock_path = pathlib.Path(td, "test.pty.sock")
            server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server_sock.bind(str(sock_path))
            server_sock.listen(1)
            server_sock.settimeout(2)

            def accept_frames():
                try:
                    conn, _ = server_sock.accept()
                    with conn:
                        conn.settimeout(2)
                        for _ in range(2):
                            header = recv_exact(conn, 5)
                            size = int.from_bytes(header[:4], "big")
                            kind = header[4]
                            frames.append((kind, recv_exact(conn, size)))
                except Exception as exc:
                    errors.append(exc)

            thread = threading.Thread(target=accept_frames)
            thread.start()
            try:
                result = self.server._inject_bg_agent_via_pty_socket(
                    {"pid": 123, "sessionId": "sid", "ptySock": str(sock_path)},
                    "hi\x1b\nthere",
                )
                thread.join(timeout=2)
            finally:
                server_sock.close()

        self.assertFalse(errors)
        self.assertTrue(result["ok"])
        self.assertEqual(frames, [
            (0, b"\x1b[200~hi\nthere\x1b[201~"),
            (0, b"\r"),
        ])

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

    def test_live_background_agent_injects_via_daemon_pty(self):
        sid = "00000000-0000-4000-8000-000000000001"
        worker = {"pid": 12345, "sessionId": sid, "ptySock": "/tmp/cc-daemon-501/x.sock"}
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "kind": "bg",
                     "status": "busy",
                     "job_id": "00000000",
                     "pid": 54324,
                 },
             ), \
             mock.patch.object(self.server, "_bg_agent_ready_for_input", return_value=True), \
             mock.patch.object(
                 self.server,
                 "_find_live_bg_agent_entry_for_session",
                 return_value=worker,
             ) as find_worker, \
             mock.patch.object(
                 self.server,
                 "_inject_bg_agent_via_pty_socket",
                 return_value={"ok": True, "via": "bg-agent-pty"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_headless") as resume:
            result = self.server._inject_text_into_session(sid, "follow up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "bg-agent-pty")
        find_worker.assert_called_once_with(sid)
        inject.assert_called_once_with(worker, "follow up")
        resume.assert_not_called()

    def test_live_background_agent_queues_until_prompt_ready(self):
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
                         "tty": None,
                         "terminal_app": None,
                         "kind": "bg",
                         "status": "busy",
                         "job_id": "00000000",
                         "pid": 54324,
                     },
                 ), \
                 mock.patch.object(self.server, "_bg_agent_ready_for_input", return_value=False), \
                 mock.patch.object(self.server, "_inject_bg_agent_via_pty_socket") as inject:
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

    def test_codex_live_terminal_injects_via_tty(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": "ttys009",
                     "terminal_app": "Terminal",
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "inject_input_via_keystroke",
                 return_value={"ok": True, "via": "terminal-control"},
             ) as inject, \
             mock.patch.object(self.server, "resume_session_codex") as resume:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        inject.assert_called_once_with("ttys009", "Terminal", "hello")
        resume.assert_not_called()

    def test_codex_without_live_tty_uses_resume(self):
        sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
        with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={"live": False, "tty": None, "terminal_app": None},
             ), \
             mock.patch.object(
                 self.server,
                 "resume_session_codex",
                 return_value={"ok": True, "via": "codex-resume"},
             ) as resume, \
             mock.patch.object(self.server, "inject_input_via_keystroke") as inject:
            result = self.server._inject_text_into_session(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-resume")
        resume.assert_called_once_with(sid, "hello")
        inject.assert_not_called()

    def test_antigravity_resume_falls_back_to_app_when_cli_missing(self):
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(
            self.server,
            "_antigravity_cli_conversation_path",
            return_value=None,
        ), \
             mock.patch.object(
                 self.server,
                 "_resume_session_antigravity_app",
                 return_value={"ok": True, "via": "antigravity-app"},
             ) as app_resume:
            result = self.server.resume_session_antigravity(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "antigravity-app")
        app_resume.assert_called_once_with(sid, "hello")

    def test_antigravity_app_resume_records_interaction_on_success(self):
        sid = "00000000-0000-4000-8000-000000000001"
        user_config = {
            "plannerConfig": {
                "requestedModel": {"model": "MODEL_PLACEHOLDER_TEST"},
            },
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": True, "config": user_config},
             ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_app_rpc",
                 return_value={"ok": True, "port": 1234},
             ) as rpc, \
             mock.patch.object(self.server, "_record_interaction") as record:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertTrue(result["ok"])
        self.assertTrue(result["resumed"])
        self.assertEqual(result["via"], "antigravity-app")
        self.assertEqual(result["port"], 1234)
        record.assert_called_once_with(sid)
        rpc.assert_called_once_with(
            "SendUserCascadeMessage",
            {
                "cascadeId": sid,
                "items": [{"text": "hello"}],
                "cascadeConfig": user_config,
            },
            timeout=10,
        )

    def test_antigravity_app_resume_requires_model_config(self):
        """When trajectory loads but has no model picked, surface the
        'pick a model in Antigravity' error (not the RPC-failure error)."""
        sid = "00000000-0000-4000-8000-000000000001"
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": False, "rpc": None},
             ), \
             mock.patch.object(self.server, "_antigravity_app_rpc") as rpc:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "antigravity_app_model_config_missing")
        rpc.assert_not_called()

    def test_antigravity_app_resume_passes_through_rpc_failure(self):
        """When the trajectory RPC itself failed (app not running, etc.),
        surface the actual RPC error instead of the misleading
        'no reusable model config' message."""
        sid = "00000000-0000-4000-8000-000000000001"
        rpc_failure = {
            "ok": False,
            "error": "Antigravity app language server is not running. Open Antigravity, then retry.",
            "code": "antigravity_app_unavailable",
            "via": "antigravity-app",
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_conversation_path",
            return_value=pathlib.Path("/tmp/session.db"),
        ), \
             mock.patch.object(
                 self.server,
                 "_antigravity_latest_user_config",
                 return_value={"ok": False, "rpc": rpc_failure},
             ), \
             mock.patch.object(self.server, "_antigravity_app_rpc") as rpc:
            result = self.server._resume_session_antigravity_app(sid, "hello")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "antigravity_app_unavailable")
        rpc.assert_not_called()

    def test_antigravity_latest_user_config_reuses_last_valid_model(self):
        config = {
            "plannerConfig": {
                "requestedModel": {"model": "MODEL_PLACEHOLDER_TEST"},
            },
        }
        trajectory = {
            "steps": [
                {"userInput": {"userConfig": {"plannerConfig": {}}}},
                {"userInput": {"lastUserConfig": config}},
            ],
        }
        with mock.patch.object(
            self.server,
            "_antigravity_app_rpc",
            return_value={"ok": True, "response": {"trajectory": trajectory}},
        ) as rpc:
            result = self.server._antigravity_latest_user_config("sid")

        self.assertTrue(result["ok"])
        self.assertEqual(result["config"], config)
        self.assertIsNot(result["config"], config)
        rpc.assert_called_once_with(
            "GetCascadeTrajectory",
            {"cascadeId": "sid"},
            timeout=5,
        )

    def test_finished_spawn_poll_closes_log_handle(self):
        proc = mock.Mock()
        proc.poll.return_value = 0
        log_fh = mock.Mock()
        entry = {
            "pid": 12345,
            "proc": proc,
            "log_fh": log_fh,
            "fifo": None,
            "stdin_fd": None,
        }

        with mock.patch.object(self.server, "_remove_spawn_from_registry") as remove:
            self.assertEqual(self.server._poll_spawn_entry(entry), 0)

        log_fh.close.assert_called_once()
        remove.assert_called_once_with(12345)
        self.assertIsNone(entry["log_fh"])
        self.assertTrue(entry["_cleanup_done"])

    def test_live_headless_spawn_queues_when_tool_child_running(self):
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {"pid": 12345}
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
                         "tty": None,
                         "terminal_app": None,
                         "pid": 12345,
                     },
                 ), \
                 mock.patch.object(
                     self.server,
                     "_find_live_spawn_entry_for_session",
                     return_value=spawn,
                 ), \
                 mock.patch.object(
                     self.server,
                     "_spawn_entry_active_tool_child",
                     return_value={"pid": 23456, "command": "grep -r"},
                 ), \
                 mock.patch.object(self.server, "_write_stream_json_user_message") as write:
                result = self.server._inject_text_into_session(sid, "follow up")

            self.assertTrue(result["ok"])
            self.assertTrue(result["queued"])
            self.assertEqual(result["status"], "busy")
            self.assertEqual(result["via"], "terminal-queued")
            write.assert_not_called()
            with self.server._pending_terminal_input_lock:
                self.assertEqual(
                    self.server._pending_terminal_input_queue[sid],
                    ["follow up"],
                )
        finally:
            with self.server._pending_terminal_input_lock:
                self.server._pending_terminal_input_queue.clear()

    def test_live_headless_spawn_restarts_when_fifo_write_fails(self):
        sid = "00000000-0000-4000-8000-000000000001"
        spawn = {"pid": 12345}
        with mock.patch.object(self.server, "_is_codex_session", return_value=False), \
             mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
             mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
             mock.patch.object(
                 self.server,
                 "session_live_status",
                 return_value={
                     "live": True,
                     "tty": None,
                     "terminal_app": None,
                     "pid": 12345,
                 },
             ), \
             mock.patch.object(
                 self.server,
                 "_find_live_spawn_entry_for_session",
                 return_value=spawn,
             ), \
             mock.patch.object(self.server, "_spawn_entry_active_tool_child", return_value=None), \
             mock.patch.object(self.server, "_write_stream_json_user_message", return_value=False), \
             mock.patch.object(self.server, "_retire_unresponsive_spawn_entry") as retire, \
             mock.patch.object(
                 self.server,
                 "resume_session_headless",
                 return_value={"ok": True, "pid": 67890, "resumed": True},
             ) as resume:
            result = self.server._inject_text_into_session(sid, "follow up")

        self.assertTrue(result["ok"])
        self.assertEqual(result["pid"], 67890)
        retire.assert_called_once_with(spawn, terminate=True)
        resume.assert_called_once_with(sid, "follow up")

    def test_fifo_writer_open_does_not_block_without_reader(self):
        with tempfile.TemporaryDirectory() as td:
            fifo = pathlib.Path(td) / "stdin.fifo"
            os.mkfifo(fifo, 0o600)
            start = time.monotonic()
            fd = self.server._open_fifo_writer(str(fifo))
            elapsed = time.monotonic() - start

        self.assertIsNone(fd)
        self.assertLess(elapsed, 0.5)

    def test_stream_json_fifo_write_does_not_block_when_pipe_full(self):
        read_fd, write_fd = os.pipe()
        try:
            flags = fcntl.fcntl(write_fd, fcntl.F_GETFL)
            fcntl.fcntl(write_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
            chunk = b"x" * 8192
            while True:
                try:
                    os.write(write_fd, chunk)
                except BlockingIOError:
                    break
            entry = {"stdin_fd": write_fd, "fifo": None, "proc": None}
            start = time.monotonic()
            ok = self.server._write_stream_json_user_message(entry, "hello")
            elapsed = time.monotonic() - start
            write_fd = None

            self.assertFalse(ok)
            self.assertIsNone(entry["stdin_fd"])
            self.assertLess(elapsed, 0.5)
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)

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

    def test_live_claude_scan_skips_headless_processes_before_lsof(self):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            if args[:4] == ["ps", "-A", "-o", "pid=,comm="]:
                return subprocess.CompletedProcess(args, 0, stdout="100 claude\n101 claude\n102 node\n", stderr="")
            if args == ["ps", "-o", "pid,tty", "-p", "100,101"]:
                return subprocess.CompletedProcess(args, 0, stdout="  PID TTY\n  100 ??\n  101 ttys001\n", stderr="")
            raise AssertionError(f"unexpected command: {args}")

        cwd_calls = []

        def fake_cwd(pid):
            cwd_calls.append(pid)
            return "/tmp/demo"

        with mock.patch.object(self.server.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(self.server, "_proc_cwd", side_effect=fake_cwd), \
             mock.patch.object(self.server, "_proc_ancestor_terminal", return_value=("Terminal", 9)):
            procs = self.server.find_live_claude_processes()

        self.assertEqual([p["pid"] for p in procs], [101])
        self.assertEqual(cwd_calls, ["101"])

    def test_ask_user_question_tool_detail_surfaces_prompt(self):
        ev = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-question",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-question",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [{
                            "header": "Key flow",
                            "question": "How automated do you want this?",
                            "options": [
                                {"label": "Full auto"},
                                {"label": "Half auto"},
                                {"label": "Skip Whisper"},
                            ],
                        }]
                    },
                }],
            },
        }

        parsed = self.server._parse_conversation_event(ev, 7)

        self.assertEqual(parsed["type"], "assistant")
        detail = parsed["blocks"][0]["detail"]
        self.assertIn("How automated do you want this?", detail)
        self.assertIn("Full auto", detail)
        self.assertIn("Half auto", detail)

    def test_bash_tool_detail_strips_shell_wrapper(self):
        ev = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-bash",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-bash",
                    "name": "Bash",
                    "input": {
                        "command": (
                            "true && unsetopt NO_EXTENDED_GLOB 2>/dev/null || true && "
                            "setopt NO_EXTENDED_GLOB 2>/dev/null || true && "
                            "python3 render_short_slides.py 2>&1 | grep slide"
                        )
                    },
                }],
            },
        }

        parsed = self.server._parse_conversation_event(ev, 8)

        detail = parsed["blocks"][0]["detail"]
        self.assertEqual(detail, "python3 render_short_slides.py 2>&1 | grep slide")
        self.assertNotIn("NO_EXTENDED_GLOB", detail)

    def test_pending_ask_user_question_clears_after_answer(self):
        sid = "00000000-0000-4000-8000-000000000099"
        project_dir = pathlib.Path(self.tmp_home, ".claude", "projects", "-demo-repo")
        project_dir.mkdir(parents=True)
        jsonl = project_dir / f"{sid}.jsonl"
        ask_event = {
            "type": "assistant",
            "timestamp": "2026-05-15T00:00:00Z",
            "message": {
                "id": "msg-question",
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu-question",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [{
                            "header": "Key flow",
                            "question": "How automated do you want this?",
                            "options": [{"label": "Half auto"}],
                        }]
                    },
                }],
            },
        }
        jsonl.write_text(json.dumps(ask_event) + "\n", encoding="utf-8")

        pending = self.server._pending_ask_user_question_for_session(sid)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["question"], "How automated do you want this?")

        answer_event = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu-question",
                    "content": "answered",
                }],
            },
        }
        with jsonl.open("a", encoding="utf-8") as f:
            f.write(json.dumps(answer_event) + "\n")

        self.assertIsNone(self.server._pending_ask_user_question_for_session(sid))

    def test_inflight_ask_user_question_marks_row_waiting(self):
        sid = "00000000-0000-4000-8000-000000000100"
        self.server.SIDECAR_STATE_DIR.mkdir(parents=True, exist_ok=True)
        marker = {
            "session_id": sid,
            "tool": "AskUserQuestion",
            "file": "Key flow: How automated do you want this?",
            "question": "How automated do you want this?",
            "header": "Key flow",
            "options": ["Half auto"],
            "started_at": 1778813567.0,
        }
        (self.server.SIDECAR_STATE_DIR / f"{sid}_in_flight.json").write_text(
            json.dumps(marker),
            encoding="utf-8",
        )

        entry = {"session_id": sid, "is_live": True}
        self.server._add_sidecar_fields(entry)

        self.assertEqual(entry["sidecar_tool"], "AskUserQuestion")
        self.assertTrue(entry["question_waiting"])
        self.assertEqual(entry["question_text"], "How automated do you want this?")

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

    def test_unified_spawn_endpoint_accepts_engine(self):
        httpd = self.server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            self.server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            with mock.patch.object(
                self.server,
                "spawn_session_codex",
                return_value={"ok": True, "pid": 123, "name": "demo", "log": "/tmp/demo.log"},
            ) as spawn_codex:
                req = urllib.request.Request(
                    base + "/api/sessions/spawn",
                    data=json.dumps({
                        "prompt": "do the thing",
                        "engine": "Codex",
                        "model": "gpt-test",
                    }).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as res:
                    body = json.loads(res.read().decode("utf-8"))
            self.assertEqual(body["engine"], "codex")
            spawn_codex.assert_called_once_with(
                "do the thing",
                name=None,
                cwd=None,
                repo_path=None,
                model="gpt-test",
            )
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

    def test_resolve_gemini_bin_uses_common_candidates(self):
        """Gemini should be available when installed in a user bin dir that
        launchd did not put on PATH."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_gemini_bin"))

        with tempfile.NamedTemporaryFile(prefix="gemini-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = pathlib.Path(f.name)
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        try:
            with mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(server.shutil, "which", return_value=None), \
                 mock.patch.object(server, "_iter_common_cli_candidates", return_value=[fake_bin]):
                result = server._resolve_gemini_bin()
            self.assertEqual(result["bin"], str(fake_bin))
            self.assertEqual(result["source"], "candidate")
            self.assertTrue(result["available"])
        finally:
            os.unlink(fake_bin)

    def test_nextjs_turbo_workspace_uses_dev_filter(self):
        """Workspace Next.js apps should start with the scoped turbo command."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp).resolve()
            app = root / "apps" / "bookyourmat"
            finie = root / "apps" / "finie"
            app.mkdir(parents=True)
            finie.mkdir(parents=True)
            (root / "turbo.json").write_text(json.dumps({"tasks": {"dev": {"persistent": True}}}))
            (root / "package.json").write_text(json.dumps({"workspaces": ["apps/*"]}))
            (app / "package.json").write_text(json.dumps({
                "name": "bookyourmat",
                "scripts": {"dev": "next dev --port 39001"},
                "dependencies": {"next": "16.1.6"},
            }))
            (finie / "package.json").write_text(json.dumps({
                "name": "finie",
                "scripts": {"dev": "next dev --port 3000"},
                "dependencies": {"next": "16.1.6"},
            }))

            cmd, cwd = server._resolve_dev_invocation(app)
            (root / ".git").mkdir()
            status = server.nextjs_status(str(root), str(app))
            root_status = server.nextjs_status(str(root))

        self.assertEqual(cmd, ["npx", "turbo", "dev", "--filter=bookyourmat"])
        self.assertEqual(cwd, root)
        self.assertEqual(status["launch_cmd"], "npx turbo dev --filter=bookyourmat")
        self.assertEqual(root_status["target_path"], str(app))
        self.assertEqual(root_status["launch_cmd"], "npx turbo dev --filter=bookyourmat")

    def test_nextjs_process_match_ignores_prompt_text(self):
        """Process rediscovery must not match an agent command line that
        merely pasted the same ps/rg pattern in its prompt."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        info = {
            "target_path": pathlib.Path("/tmp/repo/apps/bookyourmat"),
            "run_cwd": pathlib.Path("/tmp/repo"),
            "package_name": "bookyourmat",
            "filter_expected": True,
            "ports": [3001],
        }
        self.assertTrue(server._nextjs_command_matches(
            "npx turbo dev --filter=bookyourmat", info))
        self.assertTrue(server._nextjs_command_matches(
            "node /tmp/repo/node_modules/.bin/next dev --port 3001", info))
        self.assertFalse(server._nextjs_command_matches(
            "/opt/homebrew/bin/codex exec --json -- prompt contains "
            "turbo dev --filter=bookyourmat and next dev --port 3001",
            info,
        ))

    def test_spawn_session_codex_exists(self):
        """`spawn_session_codex` must exist alongside `spawn_session`
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_codex"))
        import inspect
        sig = inspect.signature(server.spawn_session_codex)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "model"])

    def test_spawn_session_gemini_exists(self):
        """`spawn_session_gemini` must exist alongside the other engines
        and accept explicit cwd/repo context."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_gemini"))
        import inspect
        sig = inspect.signature(server.spawn_session_gemini)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd", "repo_path", "worktree", "model"])

    def test_orchestration_spawn_engine_normalization(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertEqual(server._normalize_orchestration_spawn_engine(None), "claude")
        self.assertEqual(server._normalize_orchestration_spawn_engine("Claude"), "claude")
        self.assertEqual(server._normalize_orchestration_spawn_engine("Codex"), "codex")
        self.assertEqual(server._normalize_orchestration_spawn_engine("antigravity"), "antigravity")
        self.assertEqual(server._normalize_orchestration_spawn_engine("gemini"), "antigravity")

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
                    session_id="known-session-id",
                )
                with registry_file.open() as f:
                    rows = json.load(f)
                self.assertEqual(rows[-1]["engine"], "codex")
                self.assertEqual(rows[-1]["session_id"], "known-session-id")
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

    def test_pid_is_engine_process_rejects_zombie(self):
        """A defunct reattached resume must not keep a Codex card live."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        with mock.patch.object(server, "_pid_is_zombie", return_value=True), \
             mock.patch.object(server.subprocess, "run") as run:
            self.assertFalse(server._pid_is_engine_process(22222, "codex"))
        run.assert_not_called()

    def test_reattached_proc_poll_treats_zombie_as_exited(self):
        """After an in-place server restart, a child may become a zombie
        without a Popen handle. Polling must release queued resumes."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        proc = server._ReattachedProc(22222)
        with mock.patch.object(server.os, "waitpid", side_effect=ChildProcessError), \
             mock.patch.object(server.os, "kill", return_value=None), \
             mock.patch.object(server, "_pid_is_zombie", return_value=True):
            self.assertEqual(proc.poll(), -1)
            self.assertEqual(proc.poll(), -1)

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

    def test_open_target_resolves_absolute_looking_project_relative(self):
        """`/foo/bar` inside a transcript should fall back to repo-relative when no FS-root match exists."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        sid = "11111111-2222-3333-4444-555555555555"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            repo = root / "repo"
            session_cwd = repo
            session_cwd.mkdir(parents=True)
            (repo / ".git").mkdir()
            sub = repo / "growth-machine" / "content" / "landing"
            sub.mkdir(parents=True)
            target_file = sub / "index.html"
            target_file.write_text("<!doctype html>\n")

            with mock.patch.object(server, "find_session_cwd", return_value=str(session_cwd)):
                result = server._resolve_open_target(
                    "/growth-machine/content/landing/index.html",
                    session_id=sid,
                    cwd=str(session_cwd),
                    repo_path=str(repo),
                )

        self.assertTrue(result["ok"], msg=result)
        self.assertEqual(pathlib.Path(result["path"]), target_file.resolve())

    def test_open_target_allows_files_outside_sandbox(self):
        """Post-sandbox-removal: any resolvable path is allowed through /api/open."""
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

        self.assertTrue(result["ok"])
        self.assertFalse(result["core_sandbox"])
        self.assertFalse(result["session_sandbox"])

    def test_open_launch_allowed_for_any_resolved_target(self):
        """Post-sandbox-removal: _open_launch_allowed returns True unconditionally."""
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
        self.assertTrue(server._open_launch_allowed(result))

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
        self.assertTrue(server._open_launch_allowed(result))

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

    def test_resume_antigravity_adds_pasted_image_dir(self):
        """AGY needs pasted-image folders in its repeatable --add-dir workspace."""
        server = self.server
        paste_dir = server.COMMAND_CENTER_PASTED_IMAGES_DIR
        paste_dir.mkdir(parents=True)
        image = paste_dir / "paste-123.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        sid = "00000000-0000-4000-8000-000000000004"
        conv = pathlib.Path(self.tmp_home) / "ag.pb"
        conv.write_bytes(b"pb")
        proc = mock.Mock(pid=4244)
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server, "_antigravity_cli_conversation_path", return_value=conv), \
                 mock.patch.object(server, "find_session_cwd", return_value=str(self.repo)), \
                 mock.patch.object(server, "_git_toplevel_for_existing_dir", return_value=str(self.repo)), \
                 mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.resume_session_antigravity(sid, f"look at {image}")
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        cmd = popen.call_args.args[0]
        add_dirs = [cmd[i + 1] for i, word in enumerate(cmd[:-1]) if word == "--add-dir"]
        self.assertIn(str(self.repo), add_dirs)
        self.assertIn(str(paste_dir.resolve()), add_dirs)

    def test_spawn_antigravity_writes_model_to_cli_settings(self):
        """AGY print mode reads its model from settings.json, not argv."""
        server = self.server
        settings_path = server.ANTIGRAVITY_CLI_SETTINGS
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({
            "colorScheme": "dark",
            "model": "Gemini 3.1 Pro (Low)",
        }))
        proc = mock.Mock(pid=4246)
        proc.poll.return_value = None
        original_spawns = list(server._spawned_sessions)
        server._spawned_sessions.clear()
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server.subprocess, "Popen", return_value=proc) as popen, \
                 mock.patch.object(server, "_record_spawn_to_registry"):
                result = server.spawn_session_antigravity(
                    "hello from agy",
                    name="agy model",
                    repo_path=str(self.repo),
                    model="gemini-3.5-flash-high",
                )
        finally:
            for entry in server._spawned_sessions:
                fh = entry.get("log_fh")
                if fh:
                    fh.close()
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "Gemini 3.5 Flash (High)")
        settings = json.loads(settings_path.read_text())
        self.assertEqual(settings["model"], "Gemini 3.5 Flash (High)")
        self.assertEqual(settings["colorScheme"], "dark")
        cmd = popen.call_args.args[0]
        self.assertNotIn("--model", cmd)
        self.assertIn("-p", cmd)

    def test_resume_antigravity_queues_when_resume_already_running(self):
        """A second AGY follow-up should queue instead of spawning parallel resumes."""
        server = self.server
        sid = "00000000-0000-4000-8000-000000000004"
        conv = pathlib.Path(self.tmp_home) / "ag.pb"
        conv.write_bytes(b"pb")
        original_spawns = list(server._spawned_sessions)
        with server._pending_resume_lock:
            original_queue = dict(server._pending_resume_queue)
            server._pending_resume_queue.clear()
        server._spawned_sessions[:] = [{
            "engine": "antigravity",
            "resumed_sid": sid,
            "pid": 4245,
        }]
        try:
            with mock.patch.object(
                server,
                "_resolve_antigravity_bin",
                return_value={"available": True, "bin": "/usr/bin/agy-test"},
            ), mock.patch.object(server, "_antigravity_cli_conversation_path", return_value=conv), \
                 mock.patch.object(server, "_poll_spawn_entry", return_value=None), \
                 mock.patch.object(server.subprocess, "Popen") as popen:
                result = server.resume_session_antigravity(sid, "second")
        finally:
            server._spawned_sessions.clear()
            server._spawned_sessions.extend(original_spawns)

        self.assertTrue(result["ok"])
        self.assertTrue(result["queued"])
        self.assertEqual(result["via"], "antigravity-resume-queued")
        popen.assert_not_called()
        with server._pending_resume_lock:
            self.assertEqual(server._pending_resume_queue.get(sid), ["second"])
            server._pending_resume_queue.clear()
            server._pending_resume_queue.update(original_queue)

    def test_open_target_allows_executable_session_cwd_files(self):
        """Post-sandbox-removal: scripts in the session cwd resolve cleanly."""
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

        self.assertTrue(result["ok"])
        self.assertTrue(server._open_launch_allowed(result))

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

    def test_pinned_conversations_roundtrip_and_sort_first(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = pathlib.Path(tmp)
            path = state_dir / "pinned-conversations.json"
            orig_file = server.PINNED_CONVERSATIONS_FILE
            orig_state = server.LOG_VIEWER_STATE_DIR
            server.PINNED_CONVERSATIONS_FILE = path
            server.LOG_VIEWER_STATE_DIR = state_dir
            try:
                server._save_pinned_conversations(["sid-2", "sid-1"])
                self.assertEqual(server._load_pinned_conversations(), ["sid-2", "sid-1"])
                rows = [
                    {"session_id": "sid-3", "modified": 30},
                    {"session_id": "sid-1", "modified": 10},
                    {"session_id": "sid-2", "modified": 20},
                ]
                server._apply_pinned_conversation_fields(rows)
                server._sort_pinned_conversations_first(rows)
                self.assertEqual([r["session_id"] for r in rows], ["sid-2", "sid-1", "sid-3"])
                self.assertTrue(rows[0]["pinned"])
                self.assertEqual(rows[0]["pin_rank"], 0)
            finally:
                server.PINNED_CONVERSATIONS_FILE = orig_file
                server.LOG_VIEWER_STATE_DIR = orig_state

    def test_pin_route_and_row_action_hooks_are_registered(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        src = pathlib.Path(server.__file__).read_text()
        js = pathlib.Path(PROJECT_ROOT, "static", "app.js").read_text()
        css = pathlib.Path(PROJECT_ROOT, "static", "app.css").read_text()
        self.assertIn("/api/conversations/[^/]+/pin", src)
        self.assertIn("class=\"conv-pin-btn", js)
        self.assertIn("mergeBtn + startBtn + pinBtn + archiveBtn", js)
        self.assertIn("Pinned to top", js)
        self.assertIn("_minPinnedRank", js)
        self.assertNotIn("conv-pinned-section", js)
        self.assertIn("if (c.pinned) return true", js)
        self.assertIn("c.pinned ||", js)
        self.assertIn("applyOptimisticOverrides(rowsForRender)", js)
        self.assertIn("function _restoreConversationListScrollTop", js)
        self.assertIn("const pinScrollTop = $convList ? $convList.scrollTop : null", js)
        self.assertIn("_restoreConversationListScrollTop($convList, pinScrollTop)", js)
        self.assertNotIn("scrollConversationRowIntoView(convId, data.pinned ? 'start' : 'nearest')", js)
        self.assertIn(".conv-item .conv-pin-btn", css)
        self.assertIn(".conv-item.is-pinned:not(:hover):not(:focus-within) .conv-row-actions:not(:empty)", css)
        self.assertIn(".conv-item.is-pinned:not(:hover):not(:focus-within) .conv-pin-btn.is-unpin", css)
        self.assertIn(".conv-item .conv-pin-btn.is-unpin:hover .conv-pin-glyph::before", css)
        self.assertIn(".conv-item .conv-pin-btn.is-unpin:hover .conv-pin-glyph::after", css)
        self.assertNotIn("#convList .conv-pinned-section", css)

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

    def test_extract_session_usage_uses_compact_post_tokens_until_next_turn(self):
        """A compact boundary is the first reliable post-compact signal.
        Use its postTokens count immediately instead of leaving the footer
        pinned to the pre-compact peak until another assistant turn lands."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-777777777777"
        big_turn = {
            "type": "assistant",
            "sessionId": sid,
            "isSidechain": False,
            "message": {
                "model": "claude-opus-4-7",
                "role": "assistant",
                "content": [{"type": "text", "text": "before compact"}],
                "usage": {
                    "input_tokens": 260_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 210_000,
                    "output_tokens": 500,
                },
            },
        }
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": sid,
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 470_054,
                "postTokens": 13_781,
                "durationMs": 130_502,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact-posttokens"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(
                json.dumps(big_turn) + "\n"
                + json.dumps(boundary) + "\n"
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

        self.assertEqual(usage["latest_input_tokens"], 13_781)
        self.assertEqual(usage["peak_input_tokens"], 13_781)
        self.assertEqual(usage["compact_count"], 1)
        self.assertEqual(usage["context_limit"], 1_000_000)

    def test_parse_conversation_surfaces_compact_boundary(self):
        """The transcript pane should show feedback when `/compact` finishes."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "11111111-2222-3333-4444-888888888888"
        boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": sid,
            "timestamp": "2026-05-25T01:54:30.071Z",
            "compactMetadata": {
                "trigger": "manual",
                "preTokens": 470_054,
                "postTokens": 13_781,
                "durationMs": 130_502,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            project = root / "-tmp-project-compact-event"
            project.mkdir()
            (project / f"{sid}.jsonl").write_text(json.dumps(boundary) + "\n")
            orig_root = server.PROJECTS_ROOT
            server.PROJECTS_ROOT = root
            try:
                with mock.patch.object(server, "_is_codex_session", return_value=False), \
                     mock.patch.object(server, "_is_gemini_session", return_value=False):
                    result = server.parse_conversation(sid, use_cache=False)
            finally:
                server.PROJECTS_ROOT = orig_root

        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["type"], "system")
        self.assertEqual(event["subtype"], "compact_boundary")
        self.assertEqual(event["session"], sid)
        self.assertEqual(event["compact"]["trigger"], "manual")
        self.assertEqual(event["compact"]["pre_tokens"], 470_054)
        self.assertEqual(event["compact"]["post_tokens"], 13_781)
        self.assertEqual(event["compact"]["duration_ms"], 130_502)

    def test_extract_antigravity_usage_rpc(self):
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server
        sid = "22222222-3333-4444-5555-777777777777"

        # Test case 1: RPC succeeds and returns usage metrics
        fake_response = {
            "trajectory": {
                "steps": [
                    {
                        "metadata": {
                            "modelUsage": {
                                "model": "gemini-1.5-pro",
                                "inputTokens": "5000",
                                "outputTokens": "150",
                                "cacheReadTokens": "1000",
                                "thinkingTokens": "400",
                            }
                        }
                    },
                    {
                        "metadata": {
                            "modelUsage": {
                                "model": "gemini-1.5-pro",
                                "inputTokens": "6000",
                                "outputTokens": "200",
                                "cacheReadTokens": "1200",
                                "cacheCreationTokens": 100,
                                "thinkingTokens": 600,
                            }
                        }
                    }
                ]
            }
        }

        with mock.patch.object(server, "_antigravity_app_rpc", return_value={"ok": True, "response": fake_response}), \
             mock.patch.object(server, "_is_antigravity_session", return_value=True), \
             mock.patch.object(server, "_antigravity_transcript_path", return_value=None), \
             mock.patch.object(server, "_get_session_override", return_value=None):

            usage = server.extract_session_usage(sid)

            # check stats for second step: input (6000) + cacheRead (1200) + cacheCreation (100) = 7300
            self.assertEqual(usage["latest_input_tokens"], 7300)
            self.assertEqual(usage["peak_input_tokens"], 7300)
            self.assertEqual(usage["total_input_tokens"], 5000 + 6000)
            self.assertEqual(usage["total_cache_read_tokens"], 1000 + 1200)
            self.assertEqual(usage["total_cache_creation_tokens"], 100)
            self.assertEqual(usage["total_output_tokens"], 150 + 200)
            # Per-turn thinking tokens are summed for the bottom-bar totals.
            self.assertEqual(usage["total_thinking_tokens"], 400 + 600)
            self.assertEqual(usage["model"], "gemini-1.5-pro")
            self.assertEqual(usage["engine"], "antigravity")
            self.assertEqual(usage["context_limit"], 1_000_000)

        # Test case 2: RPC fails, it should fall back to empty defaults
        with mock.patch.object(server, "_antigravity_app_rpc", return_value={"ok": False}), \
             mock.patch.object(server, "_is_antigravity_session", return_value=True), \
             mock.patch.object(server, "_antigravity_transcript_path", return_value=None), \
             mock.patch.object(server, "_get_session_override", return_value=None):

            usage = server.extract_session_usage(sid)
            self.assertEqual(usage["latest_input_tokens"], 0)
            self.assertEqual(usage["peak_input_tokens"], 0)
            self.assertEqual(usage["total_thinking_tokens"], 0)
            self.assertEqual(usage["model"], "")
            self.assertEqual(usage["engine"], "antigravity")

    def test_parse_antigravity_event_attaches_per_turn_tokens(self):
        """Assistant events should carry tokens_in/out/thinking when the
        trajectory's modelUsage covers the event's step_index. This is what
        feeds the per-turn chips in the conversation pane."""
        for mod in ("server",):
            sys.modules.pop(mod, None)
        import server

        usage_map = {
            13: {"in": 11200, "out": 2600, "thinking": 1000,
                 "cache_read": 0, "cache_create": 0, "model": "agy-1"},
        }
        ev_with_step = {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "step_index": 13,
            "created_at": "2026-05-22T10:00:00Z",
            "content": "Here is the plan.",
            "tool_calls": [],
        }
        out = server._parse_antigravity_event(ev_with_step, 99, usage_map=usage_map)
        self.assertIsNotNone(out)
        self.assertEqual(out["type"], "assistant")
        self.assertEqual(out["tokens_in"], 11200)
        self.assertEqual(out["tokens_out"], 2600)
        self.assertEqual(out["tokens_thinking"], 1000)

        # Step index with no matching trajectory entry → no token fields,
        # so the frontend falls back to the no-chip render path.
        ev_no_match = {
            "type": "PLANNER_RESPONSE",
            "source": "MODEL",
            "step_index": 999,
            "created_at": "2026-05-22T10:00:01Z",
            "content": "Another step.",
            "tool_calls": [],
        }
        out2 = server._parse_antigravity_event(ev_no_match, 100, usage_map=usage_map)
        self.assertIsNotNone(out2)
        self.assertNotIn("tokens_in", out2)
        self.assertNotIn("tokens_out", out2)
        self.assertNotIn("tokens_thinking", out2)

        # Old call shape (no usage_map kw) must still work.
        out3 = server._parse_antigravity_event(ev_with_step, 101)
        self.assertIsNotNone(out3)
        self.assertNotIn("tokens_in", out3)



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


class TestTemplateGallery(unittest.TestCase):
    def test_templates_json_parses_and_has_required_shape(self):
        """The New Session modal's template gallery is driven by
        static/templates.json. Every template must carry the fields the
        UI binds to — id, name, description, engine, worktree, prompt —
        and the JSON must be valid so the gallery doesn't render blank."""
        path = pathlib.Path(PROJECT_ROOT, "static", "templates.json")
        self.assertTrue(path.is_file(), "static/templates.json missing")
        data = json.loads(path.read_text(encoding="utf-8"))
        templates = data.get("templates")
        self.assertIsInstance(templates, list)
        self.assertGreaterEqual(
            len(templates), 5,
            "issue #46 ships with at least five starter templates",
        )
        seen_ids = set()
        for t in templates:
            for key in ("id", "name", "description", "engine", "worktree", "prompt"):
                self.assertIn(key, t, f"template missing {key!r}: {t.get('id')}")
            self.assertIsInstance(t["id"], str)
            self.assertNotIn(t["id"], seen_ids, "duplicate template id")
            seen_ids.add(t["id"])
            self.assertIn(t["engine"], ("claude", "codex", "gemini"))
            self.assertIsInstance(t["worktree"], bool)
            self.assertIsInstance(t["prompt"], str)
            self.assertGreater(len(t["prompt"].strip()), 0)


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


class TestPendingInputs(unittest.TestCase):
    def setUp(self):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        self.server = importlib.import_module("server")
        self.tmp_dir = tempfile.mkdtemp(prefix="ccc-pending-inputs-")
        self.server.PENDING_INPUTS_FILE = pathlib.Path(self.tmp_dir) / "pending-inputs.json"
        
        # Clear locks/queues
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

    def test_save_and_load_pending_inputs(self):
        sid = "test-session-id"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["hello resume"]
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue[sid] = ["hello term"]

        # Save to disk
        self.server._save_pending_inputs()
        self.assertTrue(self.server.PENDING_INPUTS_FILE.is_file())

        # Clear memory queues
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue.clear()
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue.clear()

        # Load from disk
        self.server._load_pending_inputs()

        # Verify loaded correctly
        with self.server._pending_resume_lock:
            self.assertEqual(self.server._pending_resume_queue.get(sid), ["hello resume"])
        with self.server._pending_terminal_input_lock:
            self.assertEqual(self.server._pending_terminal_input_queue.get(sid), ["hello term"])

    def test_get_queued_events_for_session(self):
        sid = "test-session-id"
        with self.server._pending_resume_lock:
            self.server._pending_resume_queue[sid] = ["r1", "r2"]
        with self.server._pending_terminal_input_lock:
            self.server._pending_terminal_input_queue[sid] = ["t1"]

        events = self.server._get_queued_events_for_session(sid)
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["text"], "r1")
        self.assertTrue(events[0]["pending"])
        self.assertEqual(events[1]["text"], "r2")
        self.assertEqual(events[2]["text"], "t1")
        self.assertTrue(events[2]["pending"])


if __name__ == "__main__":
    unittest.main()
