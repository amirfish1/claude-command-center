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
