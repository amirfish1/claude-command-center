"""`ccc spawn --continue-from` / `ccc send --new-if-large-and-stale` CLI
branching (MEMO-FIX-lineage). No live server, no live claude/gh/codex --
_get_json/_post_json/_resolve_target are mocked, matching
tests/test_ccc_doctor_freshness.py's pattern for exercising the `ccc`
script directly.
"""

import argparse
import importlib.machinery
import importlib.util
import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


def _load_ccc_cli():
    ccc_path = Path(__file__).resolve().parent.parent / "ccc"
    loader = importlib.machinery.SourceFileLoader("ccc_cli_continuation", str(ccc_path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _spawn_args(**overrides):
    base = dict(
        server="http://127.0.0.1:8099", prompt=["keep", "going"], continue_from=None,
        dry_run=False, engine=None, model=None, effort=None, name=None,
        cwd=None, worktree=False, report_to=None, confirm_blocked_model=False,
        json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _send_args(**overrides):
    base = dict(
        server="http://127.0.0.1:8099", target="old-sid", text=["keep", "going"],
        steer=False, queue=False, sender=None, new_if_large_and_stale=False,
        large_threshold=150_000, stale_seconds=3600, dry_run=False,
        model=None, effort=None, report_to=None, json=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class ContinuationCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ccc = _load_ccc_cli()

    def test_spawn_continue_from_posts_expected_payload(self):
        ccc = self.ccc
        posted = {}

        def fake_post_json(base, path, payload, timeout=60):
            posted["path"] = path
            posted["payload"] = payload
            return 200, {
                "ok": True, "dry_run": False, "continue_from": "old-sid",
                "latest_session_id": "old-sid", "new_session_id": "new-sid",
                "engine": "claude", "rebound": ["rr_abc"],
            }

        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
             mock.patch.object(ccc, "_resolve_target", return_value=("old-sid", None)), \
             mock.patch.object(ccc, "_post_json", side_effect=fake_post_json):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = ccc.cmd_spawn(_spawn_args(continue_from="old-sid", prompt=["keep", "going"]))
        self.assertEqual(rc, 0)
        self.assertEqual(posted["path"], "/api/sessions/spawn-continue-from")
        self.assertEqual(posted["payload"]["continue_from"], "old-sid")
        self.assertEqual(posted["payload"]["prompt"], "keep going")
        self.assertNotIn("dry_run", posted["payload"])
        self.assertIn("new session new-sid", out.getvalue())

    def test_spawn_continue_from_forwards_dedupe_identity(self):
        ccc = self.ccc
        result = {"ok": True, "continue_from": "old", "latest_session_id": "old",
                  "new_session_id": "child", "engine": "codex"}
        with mock.patch.object(ccc, "_post_json", return_value=(200, result)) as post, \
             redirect_stdout(io.StringIO()):
            ccc._spawn_continue_from("http://localhost:8099", "old", "finish",
                                     _spawn_args(name="worker", task_key="task", dedupe=True))
        self.assertEqual(post.call_args.args[2], {
            "continue_from": "old", "prompt": "finish", "name": "worker",
            "task_key": "task", "dedupe": True,
        })

    def test_spawn_continue_from_dry_run_forwards_flag(self):
        ccc = self.ccc
        posted = {}

        def fake_post_json(base, path, payload, timeout=60):
            posted["payload"] = payload
            return 200, {"ok": True, "dry_run": True, "continue_from": "old-sid",
                         "latest_session_id": "old-sid", "engine": "claude"}

        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
             mock.patch.object(ccc, "_resolve_target", return_value=("old-sid", None)), \
             mock.patch.object(ccc, "_post_json", side_effect=fake_post_json):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = ccc.cmd_spawn(_spawn_args(continue_from="old-sid", dry_run=True))
        self.assertEqual(rc, 0)
        self.assertTrue(posted["payload"]["dry_run"])
        self.assertIn("[dry-run]", out.getvalue())

    def test_spawn_continue_from_error_is_nonzero(self):
        ccc = self.ccc
        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
             mock.patch.object(ccc, "_resolve_target", return_value=("old-sid", None)), \
             mock.patch.object(ccc, "_post_json", return_value=(400, {
                 "ok": False, "error": "no session found for 'old-sid'",
             })):
            out = io.StringIO()
            with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", out):
                rc = ccc.cmd_spawn(_spawn_args(continue_from="old-sid"))
        self.assertEqual(rc, 1)
        self.assertIn("continue-from failed", out.getvalue())

    def test_send_new_if_large_and_stale_takes_new_path_when_decided(self):
        ccc = self.ccc
        calls = []

        def fake_get_json(base, path, timeout=10):
            calls.append(("get", path))
            return {"path": "new", "reason": "big and stale", "context_tokens": 200000,
                    "idle_seconds": 7200, "resolved_session_id": "old-sid",
                    "latest_session_id": "old-sid"}

        def fake_post_json(base, path, payload, timeout=60):
            calls.append(("post", path, payload))
            return 200, {"ok": True, "dry_run": False, "continue_from": "old-sid",
                         "latest_session_id": "old-sid", "new_session_id": "new-sid",
                         "engine": "claude", "rebound": []}

        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
             mock.patch.object(ccc, "_resolve_target", return_value=("old-sid", None)), \
             mock.patch.object(ccc, "_get_json", side_effect=fake_get_json), \
             mock.patch.object(ccc, "_post_json", side_effect=fake_post_json):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = ccc.cmd_send(_send_args(new_if_large_and_stale=True))
        self.assertEqual(rc, 0)
        self.assertEqual(calls[0][0], "get")
        self.assertIn("/api/sessions/continuation-decision/", calls[0][1])
        self.assertEqual(calls[1], ("post", "/api/sessions/spawn-continue-from", {
            "continue_from": "old-sid", "prompt": "keep going",
        }))
        self.assertIn("decision: new", out.getvalue())

    def test_send_new_if_large_and_stale_sends_normally_when_not_warranted(self):
        ccc = self.ccc
        calls = []

        def fake_get_json(base, path, timeout=10):
            return {"path": "normal", "reason": "still warm", "context_tokens": 2000,
                    "idle_seconds": 5, "resolved_session_id": "old-sid",
                    "latest_session_id": "old-sid"}

        def fake_post_json(base, path, payload, timeout=60):
            calls.append((path, payload))
            return 200, {"ok": True, "effect": "delivered"}

        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
             mock.patch.object(ccc, "_resolve_target", return_value=("old-sid", None)), \
             mock.patch.object(ccc, "_get_json", side_effect=fake_get_json), \
             mock.patch.object(ccc, "_post_json", side_effect=fake_post_json):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = ccc.cmd_send(_send_args(new_if_large_and_stale=True))
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "/api/inject-input")
        self.assertEqual(calls[0][1]["session_id"], "old-sid")
        self.assertIn("decision: normal", out.getvalue())

    def test_send_dry_run_alone_never_sends_or_spawns(self):
        ccc = self.ccc

        def fake_get_json(base, path, timeout=10):
            return {"path": "new", "reason": "big and stale", "context_tokens": 200000,
                    "idle_seconds": 7200, "resolved_session_id": "old-sid",
                    "latest_session_id": "old-sid"}

        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
             mock.patch.object(ccc, "_resolve_target", return_value=("old-sid", None)), \
             mock.patch.object(ccc, "_get_json", side_effect=fake_get_json), \
             mock.patch.object(ccc, "_post_json") as post_json:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = ccc.cmd_send(_send_args(dry_run=True, new_if_large_and_stale=False))
        self.assertEqual(rc, 0)
        post_json.assert_not_called()
        self.assertIn("decision: new", out.getvalue())

    def test_send_dry_run_with_new_if_large_and_stale_previews_spawn(self):
        ccc = self.ccc

        def fake_get_json(base, path, timeout=10):
            return {"path": "new", "reason": "big and stale", "context_tokens": 200000,
                    "idle_seconds": 7200, "resolved_session_id": "old-sid",
                    "latest_session_id": "old-sid"}

        def fake_post_json(base, path, payload, timeout=60):
            self.assertTrue(payload["dry_run"])
            return 200, {"ok": True, "dry_run": True, "continue_from": "old-sid",
                         "latest_session_id": "old-sid", "engine": "claude"}

        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8099"), \
             mock.patch.object(ccc, "_resolve_target", return_value=("old-sid", None)), \
             mock.patch.object(ccc, "_get_json", side_effect=fake_get_json), \
             mock.patch.object(ccc, "_post_json", side_effect=fake_post_json):
            out = io.StringIO()
            with redirect_stdout(out):
                rc = ccc.cmd_send(_send_args(dry_run=True, new_if_large_and_stale=True))
        self.assertEqual(rc, 0)
        self.assertIn("[dry-run] would continue", out.getvalue())

    def test_argparse_wires_new_flags(self):
        ccc = self.ccc
        import sys
        old_argv = sys.argv
        try:
            sys.argv = ["ccc"]
            with mock.patch.object(ccc, "cmd_spawn") as spawn_fn, \
                 mock.patch.object(ccc, "cmd_send") as send_fn:
                ccc.main(["spawn", "--continue-from", "abc123", "keep going"])
                spawn_fn.assert_called_once()
                got = spawn_fn.call_args[0][0]
                self.assertEqual(got.continue_from, "abc123")
                self.assertEqual(got.prompt, ["keep going"])

                ccc.main([
                    "send", "old-sid", "hi", "--new-if-large-and-stale",
                    "--large-threshold", "99000", "--stale-seconds", "42", "--dry-run",
                ])
                send_fn.assert_called_once()
                got = send_fn.call_args[0][0]
                self.assertTrue(got.new_if_large_and_stale)
                self.assertEqual(got.large_threshold, 99000)
                self.assertEqual(got.stale_seconds, 42)
                self.assertTrue(got.dry_run)
        finally:
            sys.argv = old_argv

    def test_unknown_subcommand_fails_fast_instead_of_execing_run_sh(self):
        ccc = self.ccc
        err = io.StringIO()
        with mock.patch.object(ccc.os, "execv") as execv, \
             redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                ccc.main(["sesssion", "--help"])
        self.assertEqual(ctx.exception.code, 2)
        execv.assert_not_called()
        self.assertIn("invalid choice: 'sesssion'", err.getvalue())

    def test_launcher_flags_and_empty_argv_exec_run_sh(self):
        ccc = self.ccc
        for argv in ([], ["--install-service"], ["--uninstall-service"], ["--service-status"], ["--app", "--size", "1600x1000"]):
            with mock.patch.object(ccc.os, "execv") as execv:
                execv.side_effect = SystemExit(0)
                with self.assertRaises(SystemExit):
                    ccc.main(argv)
                execv.assert_called_once()
                self.assertTrue(execv.call_args[0][0].endswith("run.sh"))
                self.assertEqual(execv.call_args[0][1][1:], argv)

    def test_session_singular_alias_routes_to_cmd_sessions(self):
        ccc = self.ccc
        with mock.patch.object(ccc, "cmd_sessions", return_value=0) as sessions_fn:
            rc = ccc.main(["session", "--since", "5h"])
        self.assertEqual(rc, 0)
        sessions_fn.assert_called_once()
        self.assertEqual(sessions_fn.call_args[0][0].since, 18000)

    def test_sessions_default_24h_tree_and_continuation_folding(self):
        ccc = self.ccc
        requested_paths = []
        census_payload = {
            "ok": True,
            "sessions": [
                {
                    "session_id": "cont-head",
                    "name": "Verify Sonia $100 FSH flex registration",
                    "state": "ended",
                    "last_event_age_s": 300,
                    "engine": "claude",
                    "model": "claude-opus-5-5",
                    "effort": "high",
                    "repo_path": "/home/hermes/Apps/BYM+Finie",
                    "parent_session_id": None,
                    "continued_from_session_id": "cont-origin",
                    "spawned_via": "ui",
                    "has_conversation_row": True,
                },
                {
                    "session_id": "cont-origin",
                    "name": "Verify Sonia $100 FSH flex registration",
                    "state": "idle",
                    "last_event_age_s": 1200,
                    "engine": "codex",
                    "model": "gpt-6.1-sol",
                    "effort": "medium",
                    "repo_path": "/home/hermes/Apps/BYM+Finie",
                    "parent_session_id": None,
                    "continued_from_session_id": None,
                    "spawned_via": "terminal",
                    "has_conversation_row": True,
                },
                {
                    "session_id": "child-sub",
                    "name": '↳ ⤴︎ "Subagent audit"',
                    "state": "ended",
                    "last_event_age_s": 400,
                    "engine": "codex",
                    "model": "gpt-6.1-sol",
                    "effort": "medium",
                    "repo_path": "/home/hermes/Apps/BYM+Finie",
                    "parent_session_id": "cont-origin",
                    "continued_from_session_id": None,
                    "spawned_via": "subagent",
                    "has_conversation_row": True,
                },
                {
                    "session_id": "wt-empty-poll",
                    "name": "Empty CCC Queue Drain Check",
                    "state": "ended",
                    "last_event_age_s": 60,
                    "engine": "claude",
                    "model": "claude-opus-5-5",
                    "effort": "medium",
                    "repo_path": "/home/hermes/Apps/claude-command-center",
                    "empty_worker_poll": True,
                    "has_conversation_row": True,
                },
            ],
        }

        def fake_get_json(base, path, timeout=10.0):
            requested_paths.append(path)
            return census_payload

        out = io.StringIO()
        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8090"), \
             mock.patch.object(ccc, "_get_json", side_effect=fake_get_json), \
             redirect_stdout(out):
            rc = ccc.main(["sessions"])
        self.assertEqual(rc, 0)
        self.assertEqual(requested_paths, ["/api/sessions/census?since=86400"])
        rendered = out.getvalue()
        # Continuation head shows ↱ 1 earlier and folds cont-origin out of top-level rows.
        self.assertIn("cont-head", rendered)
        self.assertIn("↱ 1 earlier", rendered)
        self.assertNotIn("cont-origin", rendered)
        # Child of earlier continuation leg is inherited and nested under cont-head.
        self.assertIn("child-sub", rendered)
        self.assertIn('  ↳ "Subagent audit"', rendered)
        # Empty WatchTower poll is hidden by default and summarized in footer.
        self.assertNotIn("wt-empty-poll", rendered)
        self.assertIn("1 empty queue-drain poll hidden", rendered)

    def test_sessions_live_flag_queries_live_only_and_filters_ended(self):
        ccc = self.ccc
        requested_paths = []
        census_payload = {
            "ok": True,
            "sessions": [
                {
                    "session_id": "live-1",
                    "name": "Active task",
                    "state": "working",
                    "last_event_age_s": 5,
                    "engine": "claude",
                    "model": "claude-opus-5-5",
                    "effort": "high",
                    "has_conversation_row": True,
                },
                {
                    "session_id": "ended-1",
                    "name": "Finished task",
                    "state": "ended",
                    "last_event_age_s": 50,
                    "engine": "claude",
                    "model": "claude-opus-5-5",
                    "effort": "high",
                    "has_conversation_row": True,
                },
            ],
        }

        out = io.StringIO()
        with mock.patch.object(ccc, "_resolve_server", return_value="http://127.0.0.1:8090"), \
             mock.patch.object(ccc, "_get_json", side_effect=lambda base, path, timeout=10.0: requested_paths.append(path) or census_payload), \
             redirect_stdout(out):
            rc = ccc.main(["sessions", "--live"])
        self.assertEqual(rc, 0)
        self.assertEqual(requested_paths, ["/api/sessions/census"])
        rendered = out.getvalue()
        self.assertIn("live-1", rendered)
        self.assertNotIn("ended-1", rendered)
        self.assertIn("1 live session", rendered)

    def test_resolve_target_prefers_continuation_head_over_superseded_leg(self):
        ccc = self.ccc
        census_payload = {
            "ok": True,
            "sessions": [
                {
                    "session_id": "origin-sid",
                    "name": "Verify Sonia $100 FSH flex registration",
                    "state": "idle",
                    "continued_from_session_id": None,
                },
                {
                    "session_id": "head-sid",
                    "name": "Verify Sonia $100 FSH flex registration",
                    "state": "ended",
                    "continued_from_session_id": "origin-sid",
                },
            ],
        }
        with mock.patch.object(ccc, "_get_json", return_value=census_payload) as get_json:
            sid, err = ccc._resolve_target("http://127.0.0.1:8090", "Sonia $100 FSH")
        get_json.assert_called_once_with("http://127.0.0.1:8090", "/api/sessions/census?since=604800")
        self.assertEqual(sid, "head-sid")
        self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
