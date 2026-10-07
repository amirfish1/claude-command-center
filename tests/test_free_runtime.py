"""Tests for the $0 spawn runtime (CCC-managed free router).

Covers ccc_server/free_runtime.py (state parsing, env overlays, readiness,
paid-credential scrubbing) and the server-side stamps that carry the runtime
through markers, registry rows, and worker-skew refusals.

All fixtures are fake state files, tmp dirs, and loopback sockets -- nothing
here touches the real router or the real ~/.claude/command-center state.
"""

import importlib
import json
import os
import socket
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from ccc_server import free_runtime


def _fresh_server():
    """Import server.py fresh so module state doesn't leak between tests."""
    for name in ("server", "morning", "morning_store"):
        sys.modules.pop(name, None)
    return importlib.import_module("server")


def _write_state(path, **fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"port": 3017, "unified_key": "ccc-free-test-key"}
    data.update(fields)
    path.write_text(json.dumps(data))


class _FreeStateCase(unittest.TestCase):
    """Base: point the runtime at a tmp state file, never the real one.

    ccc_server.free_router exists in this checkout, so spawn_env/readiness
    would delegate to its HTTP probes by default. These fixtures exercise the
    state-file fallback path, so the owner module is patched away; the
    delegate path gets its own case below.
    """

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.state = Path(self._tmpdir.name) / "free-router.json"
        self._old = os.environ.get("CCC_FREE_ROUTER_STATE")
        os.environ["CCC_FREE_ROUTER_STATE"] = str(self.state)
        self._owner_patch = mock.patch.object(
            free_runtime, "_free_router_module", return_value=None)
        self._owner_patch.start()
        self.addCleanup(self._owner_patch.stop)

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CCC_FREE_ROUTER_STATE", None)
        else:
            os.environ["CCC_FREE_ROUTER_STATE"] = self._old
        self._tmpdir.cleanup()


class TestRouterState(_FreeStateCase):
    def test_no_state_file_is_not_ready(self):
        ok, reason = free_runtime.readiness("claude")
        self.assertFalse(ok)
        self.assertIn("not installed", reason)

    def test_missing_key_is_not_ready(self):
        self.state.write_text(json.dumps({"port": 3017}))
        ok, reason = free_runtime.readiness("claude")
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_unsupported_engine_never_ready(self):
        _write_state(self.state)
        ok, reason = free_runtime.readiness("codex")
        self.assertFalse(ok)
        self.assertIn("does not support", reason.lower())

    def test_spawn_env_empty_when_router_down(self):
        _write_state(self.state, port=1)  # port 1 never listens
        self.assertEqual(free_runtime.spawn_env("claude"), {})

    def test_spawn_env_empty_for_unsupported_engine(self):
        self.assertEqual(free_runtime.spawn_env("codex"), {})


class TestSpawnEnv(_FreeStateCase):
    """Real loopback listener stands in for the live router port."""

    def setUp(self):
        super().setUp()
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self.port = self._sock.getsockname()[1]

    def tearDown(self):
        self._sock.close()
        super().tearDown()

    def test_claude_overlay_shape(self):
        _write_state(self.state, port=self.port, default_model="router/auto")
        env = free_runtime.spawn_env("claude")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "ccc-free-test-key")
        self.assertEqual(env["ANTHROPIC_MODEL"], "router/auto")
        self.assertEqual(env["CCC_SESSION_RUNTIME"], "free")
        # The overlay itself never carries a paid credential.
        for paid in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SESSION_KEY"):
            self.assertNotIn(paid, env)

    def test_apply_to_env_strips_inherited_paid_credentials(self):
        _write_state(self.state, port=self.port, default_model="router/auto")
        child_env = {
            "ANTHROPIC_API_KEY": "sk-ant-test-XXXX",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test-XXXX",
            "PATH": "/usr/bin",
        }
        self.assertTrue(free_runtime.apply_to_env(child_env, "claude"))
        self.assertNotIn("ANTHROPIC_API_KEY", child_env)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", child_env)
        self.assertEqual(child_env["ANTHROPIC_BASE_URL"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(child_env["CCC_SESSION_RUNTIME"], "free")

    def test_opencode_overlay_uses_openai_shape(self):
        _write_state(self.state, port=self.port, default_model="router/auto")
        env = free_runtime.spawn_env("opencode")
        self.assertEqual(env["OPENAI_BASE_URL"], f"http://127.0.0.1:{self.port}/v1")
        self.assertEqual(env["OPENAI_API_KEY"], "ccc-free-test-key")
        self.assertEqual(env["CCC_SESSION_RUNTIME"], "free")

    def test_aider_overlay_uses_openai_shape(self):
        _write_state(self.state, port=self.port, default_model="router/auto")
        env = free_runtime.spawn_env("aider")
        self.assertTrue(env["OPENAI_BASE_URL"].endswith("/v1"))
        self.assertTrue(env["OPENAI_API_BASE"].endswith("/v1"))

    def test_readiness_reports_ready(self):
        _write_state(self.state, port=self.port)
        ok, reason = free_runtime.readiness("claude")
        self.assertTrue(ok, reason)


class TestOwnerDelegate(_FreeStateCase):
    """With ccc_server.free_router importable it owns can-serve: its
    spawn_env/status answers win and the state file is not a backdoor."""

    def _owner(self, env=None, status=None):
        class _Owner:
            def spawn_env(self, model=None):
                return dict(env) if env else {}
            def status(self):
                return dict(status or {})
        return _Owner()

    def test_claude_env_comes_from_owner(self):
        env = {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:3017",
            "ANTHROPIC_AUTH_TOKEN": "unified-x",
            "ANTHROPIC_MODEL": "router/auto",
        }
        with mock.patch.object(
            free_runtime, "_free_router_module",
            return_value=self._owner(env=env),
        ):
            out = free_runtime.spawn_env("claude")
        self.assertEqual(out["ANTHROPIC_AUTH_TOKEN"], "unified-x")
        self.assertEqual(out["CCC_SESSION_RUNTIME"], "free")

    def test_owner_refusal_beats_state_file(self):
        # Router says not-ready: refuse even though a state file with a
        # listening port exists. The owner's /readyz knows better.
        _write_state(self.state, port=1)
        with mock.patch.object(
            free_runtime, "_free_router_module", return_value=self._owner()
        ):
            self.assertEqual(free_runtime.spawn_env("claude"), {})
            self.assertEqual(free_runtime.spawn_env("opencode"), {})

    def test_openai_overlay_built_from_state_when_owner_ready(self):
        env = {
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:3017",
            "ANTHROPIC_AUTH_TOKEN": "unified-x",
        }
        _write_state(self.state, port=3017)
        with mock.patch.object(
            free_runtime, "_free_router_module",
            return_value=self._owner(env=env),
        ):
            out = free_runtime.spawn_env("opencode")
        self.assertEqual(out["OPENAI_BASE_URL"], "http://127.0.0.1:3017/v1")
        self.assertEqual(out["OPENAI_API_KEY"], "ccc-free-test-key")

    def test_readiness_uses_owner_status(self):
        with mock.patch.object(
            free_runtime, "_free_router_module",
            return_value=self._owner(status={"ready": True, "state": "ready"}),
        ):
            ok, _ = free_runtime.readiness("claude")
        self.assertTrue(ok)

    def test_readiness_maps_owner_state_reason(self):
        with mock.patch.object(
            free_runtime, "_free_router_module",
            return_value=self._owner(status={"ready": False, "state": "needs_key"}),
        ):
            ok, reason = free_runtime.readiness("claude")
        self.assertFalse(ok)
        self.assertIn("key", reason)


class TestModelResolution(_FreeStateCase):
    def test_explicit_model_wins(self):
        self.assertEqual(free_runtime.model_for("claude", "opus"), "opus")

    def test_env_model_then_default(self):
        self.assertEqual(
            free_runtime.model_for("claude", None, env={"ANTHROPIC_MODEL": "m1"}),
            "m1",
        )

    def test_claude_falls_back_to_auto(self):
        self.assertEqual(free_runtime.model_for("claude", None), "auto")

    def test_opencode_bare_model_gets_openai_prefix(self):
        self.assertEqual(free_runtime.model_for("opencode", "qwen3"), "openai/qwen3")
        self.assertEqual(
            free_runtime.model_for("opencode", "openai/qwen3"), "openai/qwen3"
        )


class TestScrubPaidEnv(unittest.TestCase):
    def test_scrub_removes_paid_only(self):
        env = {
            "ANTHROPIC_API_KEY": "sk-ant-test-XXXX",
            "CLAUDE_CODE_OAUTH_TOKEN": "oauth-test-XXXX",
            "CLAUDE_CODE_SESSION_KEY": "sess-test-XXXX",
            "PATH": "/usr/bin",
        }
        out = free_runtime.scrub_paid_env(dict(env))
        for paid in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_SESSION_KEY"):
            self.assertNotIn(paid, out)
        self.assertEqual(out["PATH"], "/usr/bin")


class TestUnavailableResult(unittest.TestCase):
    def test_shape(self):
        result = free_runtime.unavailable_result("claude", "extra detail")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "free_runtime_unavailable")
        self.assertEqual(result["runtime"], "free")
        self.assertIn("free", result["error"].lower())


class TestServerMarkers(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self.server = _fresh_server()
        self._old_dir = self.server.SPAWN_MARKERS_DIR
        self._old_pids = self.server.SPAWNED_PIDS_FILE
        self.server.SPAWN_MARKERS_DIR = tmp / "spawn-markers"
        self.server.SPAWNED_PIDS_FILE = tmp / "spawned-pids.json"
        self.server._SPAWN_MARKER_FILE_CACHE.clear()
        # _record_spawn_to_registry also logs a model-picker pick; keep that
        # file in tmp too so tests never touch real ~/.claude/command-center.
        # The _core proxy reads server.<name> first, so patch both homes.
        from ccc_server import log_parse

        self._old_picker = log_parse.MODEL_PICKER_HISTORY_FILE
        log_parse.MODEL_PICKER_HISTORY_FILE = tmp / "model-picker-history.json"
        if hasattr(self.server, "MODEL_PICKER_HISTORY_FILE"):
            self.server.MODEL_PICKER_HISTORY_FILE = log_parse.MODEL_PICKER_HISTORY_FILE

    def tearDown(self):
        from ccc_server import log_parse

        log_parse.MODEL_PICKER_HISTORY_FILE = self._old_picker
        self.server.SPAWN_MARKERS_DIR = self._old_dir
        self.server.SPAWNED_PIDS_FILE = self._old_pids
        self.server._SPAWN_MARKER_FILE_CACHE.clear()
        self._tmpdir.cleanup()

    def test_decode_runtime_only_marker(self):
        marker = self.server.SPAWN_MARKERS_DIR / "sess-1.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"runtime": "free"}))
        out = self.server._decode_spawn_marker_file(marker)
        self.assertEqual(out["runtime"], "free")

    def test_decode_drops_unknown_runtime(self):
        marker = self.server.SPAWN_MARKERS_DIR / "sess-2.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"runtime": "bogus", "caller": "hook"}))
        out = self.server._decode_spawn_marker_file(marker)
        self.assertNotIn("runtime", out)
        self.assertEqual(out["caller"], "hook")

    def test_merge_marker_keeps_both_writers_fields(self):
        # Session-start hook wrote caller/parent first; runtime merge must not
        # clobber it, and vice versa.
        self.server._merge_spawn_marker("sess-3", caller="hook", parent_session_id="p-1")
        self.server._merge_spawn_marker("sess-3", runtime="free")
        data = json.loads((self.server.SPAWN_MARKERS_DIR / "sess-3.json").read_text())
        self.assertEqual(data["caller"], "hook")
        self.assertEqual(data["parent_session_id"], "p-1")
        self.assertEqual(data["runtime"], "free")

    def test_merge_marker_preserves_existing_runtime(self):
        self.server._merge_spawn_marker("sess-4", runtime="free", caller="spawn")
        self.server._merge_spawn_marker("sess-4", caller="hook")
        data = json.loads((self.server.SPAWN_MARKERS_DIR / "sess-4.json").read_text())
        self.assertEqual(data["runtime"], "free")
        self.assertEqual(data["caller"], "hook")

    def test_apply_spawn_markers_stamps_row(self):
        self.server._merge_spawn_marker("sess-5", runtime="free")
        row = {"session_id": "sess-5"}
        self.server._apply_spawn_markers([row])
        self.assertEqual(row["runtime"], "free")

    def test_record_spawn_registry_writes_runtime(self):
        self.server._record_spawn_to_registry(
            12345,
            "t",
            "/tmp/t.log",
            "/tmp",
            0.0,
            "claude",
            session_id="sess-6",
            engine="claude",
            runtime="free",
        )
        rows = self.server._load_spawn_registry()
        match = [r for r in rows if r.get("session_id") == "sess-6"]
        self.assertTrue(match)
        self.assertEqual(match[0]["runtime"], "free")

    def test_record_spawn_registry_omits_runtime_when_empty(self):
        self.server._record_spawn_to_registry(
            12346,
            "t",
            "/tmp/t.log",
            "/tmp",
            0.0,
            "claude",
            session_id="sess-7",
            engine="claude",
        )
        rows = self.server._load_spawn_registry()
        match = [r for r in rows if r.get("session_id") == "sess-7"]
        self.assertTrue(match)
        self.assertNotIn("runtime", match[0])


class TestSpawnRefusals(unittest.TestCase):
    def setUp(self):
        self.server = _fresh_server()

    def test_free_claude_refuses_without_router(self):
        # Patch the router owner away too: a real free_router on this machine
        # would make the delegate check succeed and turn this into a pass.
        with tempfile.TemporaryDirectory() as td, mock.patch.object(
            free_runtime, "_free_router_module", return_value=None
        ):
            os.environ["CCC_FREE_ROUTER_STATE"] = str(Path(td) / "missing.json")
            os.environ["CCC_CONTROL_PLANE_ENGINES"] = "0"  # in-process path
            os.environ.pop("CCC_SSH_HOST", None)
            try:
                result = self.server.spawn_session("hello", runtime="free")
            finally:
                os.environ.pop("CCC_FREE_ROUTER_STATE", None)
                os.environ.pop("CCC_CONTROL_PLANE_ENGINES", None)
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "free_runtime_unavailable")

    def test_worker_runtime_rejection_never_runs_paid(self):
        """An older worker rejecting the runtime kwarg must fail the spawn --
        never retry without it (that would run the user's paid credentials)."""
        calls = []

        def fake_call(engine, operation, args, **kwargs):
            calls.append(dict(args))
            return {
                "ok": False,
                "error": "spawn_session() got an unexpected keyword argument 'runtime'",
            }

        old = self.server._control_plane_engine_call
        self.server._control_plane_engine_call = fake_call
        try:
            result = self.server.spawn_session("hello", runtime="free")
        finally:
            self.server._control_plane_engine_call = old
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "free_runtime_unavailable")
        # The kwarg was never shed: no retry dropped "runtime".
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].get("runtime"), "free")


class AiderFreeResumeTest(unittest.TestCase):
    """A free Aider session's next turn re-resolves the router overlay --
    resuming must never fall back to the dashboard's paid env."""

    def setUp(self):
        from ccc_server import aider
        self.aider = aider
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.sessions_dir = Path(self._tmp.name) / "aider-sessions"
        self.sessions_dir.mkdir()
        self.cwd = Path(self._tmp.name) / "repo"
        self.cwd.mkdir()
        self._patches = [
            mock.patch.object(aider, "_aider_sessions_dir", return_value=self.sessions_dir),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _write_session(self, sid, runtime):
        path = self.sessions_dir / f"{sid}.jsonl"
        meta = {
            "type": "aider_session_meta", "session_id": sid, "engine": "aider",
            "cwd": str(self.cwd), "repo_path": str(self.cwd), "name": "t",
            "model": "free-router-model", "created_at": "2026-10-05T00:00:00Z",
        }
        if runtime:
            meta["runtime"] = runtime
        with open(path, "w") as fh:
            fh.write(json.dumps(meta) + "\n")
        return path

    def test_resume_free_session_reuses_router_overlay(self):
        sid = str(uuid.uuid4())
        self._write_session(sid, "free")
        captured = {}

        def fake_start(session_id, text, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

        overlay = {"OPENAI_BASE_URL": "http://127.0.0.1:3017/v1", "OPENAI_API_KEY": "key"}
        with mock.patch.object(self.aider, "_aider_start_turn", side_effect=fake_start), \
             mock.patch.object(self.aider._free_runtime, "spawn_env", return_value=overlay):
            result = self.aider.resume_session_aider(sid, "next turn")
        self.assertTrue(result["ok"])
        self.assertEqual(captured.get("runtime"), "free")
        self.assertEqual(captured.get("env"), overlay)

    def test_resume_free_session_refuses_when_router_down(self):
        sid = str(uuid.uuid4())
        self._write_session(sid, "free")
        start_calls = []

        def fake_start(*args, **kwargs):
            start_calls.append(1)
            return {"ok": True}

        with mock.patch.object(self.aider, "_aider_start_turn", side_effect=fake_start), \
             mock.patch.object(self.aider._free_runtime, "spawn_env", return_value=None):
            result = self.aider.resume_session_aider(sid, "next turn")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "free_runtime_unavailable")
        self.assertEqual(start_calls, [])

    def test_resume_paid_session_untouched(self):
        sid = str(uuid.uuid4())
        self._write_session(sid, "")
        captured = {}

        def fake_start(session_id, text, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

        with mock.patch.object(self.aider, "_aider_start_turn", side_effect=fake_start), \
             mock.patch.object(self.aider._free_runtime, "spawn_env") as spawn_env:
            result = self.aider.resume_session_aider(sid, "next turn")
        self.assertTrue(result["ok"])
        self.assertEqual(captured.get("runtime"), "")
        self.assertIsNone(captured.get("env"))
        spawn_env.assert_not_called()


class FreeSpawnModelDefaultTests(unittest.TestCase):
    """A $0 spawn with no model lets the router pick; paid spawns keep the default."""

    def setUp(self):
        self.server = _fresh_server()

    def test_free_spawn_without_model_sends_no_paid_default(self):
        engine, model = self.server._spawn_request_engine_and_model(
            {"engine": "claude", "runtime": "free"})
        self.assertEqual(engine, "claude")
        self.assertIsNone(model)

    def test_free_spawn_keeps_an_explicit_model(self):
        _, model = self.server._spawn_request_engine_and_model(
            {"engine": "claude", "runtime": "free", "model": "sonnet-5"})
        self.assertTrue(model)

    def test_paid_spawn_still_gets_the_default(self):
        _, model = self.server._spawn_request_engine_and_model({"engine": "claude"})
        self.assertTrue(model)


if __name__ == "__main__":
    unittest.main()
