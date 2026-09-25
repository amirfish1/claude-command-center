"""Warm Mazkir pool: reuse, /clear isolation, idle timeout, crash recovery,
busy fallback; plus the propose/confirm action store behind Mazkir's hands."""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ccc_server import assistant_actions as aa  # noqa: E402
from ccc_server import mazkir  # noqa: E402
from ccc_server import mazkir_warm as mw  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "fixtures" / "fake_stream_claude.py")
ARGV = [sys.executable, FAKE]


def _pid(res):
    return int(res["answer"].split("pid=")[1])


class WarmPoolTest(unittest.TestCase):
    def setUp(self):
        self.pool = mw.WarmPool(idle_sec=60, max_requests=20, clear_async=False)
        self.addCleanup(self.pool.shutdown)

    def test_reuses_one_process_and_clears_between_requests(self):
        a = self.pool.ask(ARGV, "first", 10)
        b = self.pool.ask(ARGV, "second", 10)
        self.assertEqual(_pid(a), _pid(b))
        self.assertFalse(a["warm"])
        self.assertTrue(b["warm"])
        # n=0 both times: the second question never saw the first.
        self.assertTrue(a["answer"].startswith("n=0"))
        self.assertTrue(b["answer"].startswith("n=0"))
        self.assertIsNotNone(b["ttft_ms"])
        self.assertEqual(self.pool.stats["spawns"], 1)

    def test_unconfirmed_clear_retires_the_process(self):
        with mock.patch.dict(os.environ, {"FAKE_NO_RESET": "1"}):
            a = self.pool.ask(ARGV, "first", 10, env=dict(os.environ))
            b = self.pool.ask(ARGV, "second", 10, env=dict(os.environ))
        self.assertNotEqual(_pid(a), _pid(b))
        self.assertTrue(b["answer"].startswith("n=0"))
        self.assertEqual(self.pool.stats["retired_clear_failed"], 2)

    def test_crash_mid_request_raises_then_recovers(self):
        first = self.pool.ask(ARGV, "hello", 10)
        with self.assertRaises(mw.WarmError):
            self.pool.ask(ARGV, "CRASH", 10)
        again = self.pool.ask(ARGV, "hello again", 10)
        self.assertNotEqual(_pid(first), _pid(again))
        self.assertEqual(self.pool.stats["retired_error"], 1)

    def test_dead_process_is_replaced_before_the_next_request(self):
        first = self.pool.ask(ARGV, "hello", 10)
        self.pool._proc.proc.kill()
        self.pool._proc.proc.wait()
        again = self.pool.ask(ARGV, "hello", 10)
        self.assertNotEqual(_pid(first), _pid(again))
        self.assertFalse(again["warm"])

    def test_timeout_retires_the_process(self):
        with self.assertRaises(mw.WarmTimeout):
            self.pool.ask(ARGV, "HANG", 0.5)
        self.assertFalse(self.pool.status()["alive"])

    def test_idle_timeout_retires_the_process(self):
        pool = mw.WarmPool(idle_sec=0.2, clear_async=False)
        self.addCleanup(pool.shutdown)
        pool.ask(ARGV, "hello", 10)
        deadline = time.time() + 5
        while pool.status()["alive"] and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(pool.status()["alive"])
        self.assertEqual(pool.stats["retired_idle"], 1)

    def test_recycles_after_max_requests(self):
        pool = mw.WarmPool(max_requests=2, clear_async=False)
        self.addCleanup(pool.shutdown)
        pids = [_pid(pool.ask(ARGV, f"q{i}", 10)) for i in range(3)]
        self.assertEqual(pids[0], pids[1])
        self.assertNotEqual(pids[1], pids[2])

    def test_concurrent_request_gets_busy(self):
        started = threading.Event()

        def hog():
            started.set()
            try:
                self.pool.ask(ARGV, "HANG", 1.5)
            except mw.WarmError:
                pass

        t = threading.Thread(target=hog)
        t.start()
        started.wait()
        time.sleep(0.2)
        with mock.patch.object(mw, "BUSY_WAIT_SEC", 0.1):
            with self.assertRaises(mw.WarmBusy):
                self.pool.ask(ARGV, "me too", 5)
        t.join()

    def test_warm_boots_ahead_of_the_first_ask(self):
        self.assertTrue(self.pool.warm(ARGV))
        pid = self.pool.status()["pid"]
        res = self.pool.ask(ARGV, "hi", 10)
        self.assertEqual(_pid(res), pid)
        self.assertTrue(res["warm"])

    def test_stream_argv(self):
        argv = mw.stream_argv(["claude", "-p", "--output-format", "json"])
        self.assertEqual(argv[3], "stream-json")
        self.assertIn("--include-partial-messages", argv)
        self.assertEqual(argv[argv.index("--input-format") + 1], "stream-json")


class MazkirArgvTest(unittest.TestCase):
    def test_builtin_tools_off_and_ingest_denied(self):
        argv = mazkir.mazkir_argv("/x/claude", "http://x", None)
        self.assertNotIn("--session-id", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        denied = argv[argv.index("--disallowedTools"):]
        for tool in ("Bash", "CronCreate", "ScheduleWakeup", "Monitor", "mcp__claude-index__ingest"):
            self.assertIn(tool, denied)


class RunMazkirWarmTest(unittest.TestCase):
    def test_pipeline_through_the_warm_pool(self):
        tmp = tempfile.mkdtemp()
        fake_bin = os.path.join(tmp, "claude")
        with open(fake_bin, "w") as f:
            f.write(f"#!/bin/sh\nexec {sys.executable} {FAKE} \"$@\"\n")
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IEXEC)
        pool = mw.WarmPool(clear_async=False)
        self.addCleanup(pool.shutdown)
        prefetch = lambda argv, **kw: type("P", (), {"returncode": 0, "stdout": "[]"})()  # noqa: E731
        with mock.patch.object(mw, "_POOL", pool), \
                mock.patch.dict(os.environ, {"CCC_ASK_WARM": "1"}), \
                mock.patch.object(mazkir, "_live_ids", lambda: set()), \
                mock.patch.object(mazkir, "_mark_spawn", lambda sid: None), \
                mock.patch.object(mazkir, "_scratch_dir", lambda: tmp):
            kw = dict(base="http://x", claude_bin=fake_bin, fetch=lambda p: {"sessions": []},
                      prefetch_runner=prefetch, db_path="/nonexistent.db")
            one, s1 = mazkir.run_mazkir("first?", **kw)
            two, s2 = mazkir.run_mazkir("second?", **kw)
        self.assertEqual((s1, s2), (200, 200))
        self.assertEqual(one["process_mode"], "warm_boot")
        self.assertEqual(two["process_mode"], "warm")
        self.assertIsNotNone(two["ttft_ms"])
        self.assertTrue(two["answer"].startswith("n=0"))
        self.assertEqual(two["confirm_actions"], [])


class ActionStoreTest(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.store = aa.ActionStore(ttl=60, clock=lambda: self.now[0])

    def test_validation(self):
        with self.assertRaises(aa.ActionError):
            aa.validate("delete_repo", {})
        with self.assertRaises(aa.ActionError):
            aa.validate("spawn_session", {"cwd": "/definitely/not/here", "prompt": "x"})
        with self.assertRaises(aa.ActionError):
            aa.validate("wt_add", {"queue": "OPS; rm -rf", "title": "t", "text": "x"})
        with self.assertRaises(aa.ActionError):
            aa.validate("wt_comment", {"ref": "not a ref", "text": "x"})
        p = aa.validate("spawn_session", {"cwd": tempfile.gettempdir(), "prompt": "go"})
        self.assertEqual(p["engine"], "claude")

    def test_propose_never_returns_the_token(self):
        out = self.store.propose("wt_add", {"queue": "OPS", "title": "Fix it", "text": "details"})
        self.assertNotIn("confirm_token", json.dumps(out))
        self.assertIn("[[action:confirm:" + out["action_id"] + "]]", out["note"])
        it = self.store.get(out["action_id"])
        self.assertEqual(it["effect"], "File a p2 ticket in OPS: Fix it")

    def test_confirm_needs_the_token_and_runs_once(self):
        aid = self.store.propose("wt_comment", {"ref": "OPS-12", "text": "done"})["action_id"]
        calls = []
        ex = lambda kind, p: calls.append((kind, p)) or {"ok": True}  # noqa: E731
        with self.assertRaises(PermissionError):
            self.store.confirm(aid, "wrong", ex)
        token = self.store.get(aid)["token"]
        self.assertEqual(self.store.confirm(aid, token, ex)["status"], "done")
        with self.assertRaises(aa.ActionError):
            self.store.confirm(aid, token, ex)
        self.assertEqual(len(calls), 1)

    def test_expired_and_dismissed_proposals_do_not_run(self):
        aid = self.store.propose("wt_comment", {"ref": "OPS-12", "text": "x"})["action_id"]
        token = self.store.get(aid)["token"]
        self.now[0] += 61
        with self.assertRaises(aa.ActionError):
            self.store.confirm(aid, token, lambda k, p: {"ok": True})
        aid2 = self.store.propose("wt_comment", {"ref": "OPS-13", "text": "x"})["action_id"]
        t2 = self.store.get(aid2)["token"]
        self.assertEqual(self.store.dismiss(aid2, t2)["status"], "dismissed")
        with self.assertRaises(aa.ActionError):
            self.store.confirm(aid2, t2, lambda k, p: {"ok": True})

    def test_executor_builds_wt_argv_without_a_shell(self):
        seen = {}

        def run(argv, **kw):
            seen["argv"] = argv
            return type("P", (), {"returncode": 0, "stdout": "added OPS-41", "stderr": ""})()

        with mock.patch.dict(os.environ, {"CCC_WT_CMD": "/opt/wt --home x"}):
            res = aa.make_executor("http://x", run=run)(
                "wt_add", {"queue": "OPS", "title": "a; b", "text": "$(x)", "priority": "p2"})
        self.assertEqual(seen["argv"][:3], ["/opt/wt", "--home", "x"])
        self.assertEqual(seen["argv"][3:], ["add", "-q", "OPS", "--title", "a; b", "--text", "$(x)",
                                             "--priority", "p2"])
        self.assertEqual((res["ok"], res["ref"]), (True, "OPS-41"))

    def test_executor_routes_spawn_and_inject_through_ccc(self):
        posts = []
        post = lambda base, path, body: posts.append((path, body)) or {"ok": True, "session_id": "s1"}  # noqa: E731
        ex = aa.make_executor("http://x", post=post)
        ex("spawn_session", {"cwd": "/r", "prompt": "go", "engine": "claude"})
        ex("inject", {"session_id": "abcdef12", "text": "steer"})
        self.assertEqual([p for p, _ in posts], ["/api/sessions/spawn", "/api/inject-input"])

    def test_collect_confirm_actions_only_this_asks_proposals(self):
        old = self.store.propose("wt_comment", {"ref": "OPS-1", "text": "old"})["action_id"]
        self.now[0] += 5
        t0 = self.now[0]
        new = self.store.propose("wt_comment", {"ref": "OPS-2", "text": "new"})["action_id"]
        out = mazkir.collect_confirm_actions(f"[[action:confirm:{old}]] [[action:confirm:{new}]]",
                                             t0, store=self.store)
        self.assertEqual([a["id"] for a in out], [new])
        self.assertTrue(out[0]["confirm_token"])


class BriefToolTest(unittest.TestCase):
    def test_daily_brief_reads_newest_and_degrades(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertFalse(mazkir.tool_daily_brief(tmp)["available"])
        (tmp / "brief-2026-09-24.json").write_text(json.dumps({"headline": "old", "generated_ts": 0}))
        (tmp / "brief-2026-09-25.json").write_text(json.dumps({
            "headline": "3 commits", "generated_ts": 1000, "totals": {"commits": 3},
            "stuck": [{"title": "s1: waiting", "severity": "high", "source": "session"}],
            "proposals": [{"queue": "OPS", "title": "Fix hotspot", "text": "t", "priority": "normal"}]}))
        b = mazkir.tool_daily_brief(tmp, now=1000 + 7200)
        self.assertEqual((b["headline"], b["age_h"]), ("3 commits", 2.0))
        self.assertEqual(b["proposals"][0]["n"], 1)

    def test_hunch_why_by_topic(self):
        repo = Path(tempfile.mkdtemp())
        (repo / ".hunch" / "decisions").mkdir(parents=True)
        (repo / ".hunch" / "constraints").mkdir(parents=True)
        (repo / ".hunch" / "decisions" / "d1.json").write_text(json.dumps(
            {"id": "d1", "title": "Warm process for Ask", "decision": "keep one warm", "status": "accepted"}))
        (repo / ".hunch" / "decisions" / "d2.json").write_text(json.dumps(
            {"id": "d2", "title": "Unrelated", "decision": "x", "status": "accepted"}))
        out = mazkir.tool_hunch_why(str(repo), topic="warm ask process")
        self.assertEqual([d["id"] for d in out["decisions"]], ["d1"])
        self.assertFalse(mazkir.tool_hunch_why("relative/path")["ok"])


if __name__ == "__main__":
    unittest.main()
