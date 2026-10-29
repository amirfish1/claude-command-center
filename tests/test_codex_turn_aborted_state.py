"""CCC-248: a Codex turn ended by a native ``turn_aborted`` receipt must stop
reading as "working", and a late terminal receipt from another turn must not
mark a newer, genuinely active turn idle.

Rollout fixtures are synthetic; their event shapes mirror real rollouts (an
interrupted turn, a resumed turn, nested sub-turns, a forked history).
"""
import http.server
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ev(ts, ev_type, payload):
    return {"timestamp": _iso(ts), "type": ev_type, "payload": payload}


def task_started(ts, turn_id):
    return _ev(ts, "event_msg", {"type": "task_started", "turn_id": turn_id})


def user(ts, text="do the thing"):
    return _ev(ts, "response_item", {
        "type": "message", "role": "user",
        "content": [{"type": "input_text", "text": text}],
    })


def tool_call(ts, call_id="call-1"):
    return _ev(ts, "response_item", {
        "type": "function_call", "name": "exec_command", "call_id": call_id,
        "arguments": json.dumps({"cmd": "sleep 600"}),
    })


def aborted(ts, turn_id, reason="interrupted"):
    return _ev(ts, "event_msg", {"type": "turn_aborted", "turn_id": turn_id, "reason": reason})


def completed(ts, turn_id, text="done"):
    return _ev(ts, "event_msg", {"type": "task_complete", "turn_id": turn_id, "last_agent_message": text})


T0 = 1_793_270_000.0
TURN_A = "01a18737-9789-70c2-bfce-27e64b9ee509"
TURN_B = "01a18744-bb73-7460-9b7d-0ff63588e32d"


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        cls.server = importlib.import_module("server")
        from ccc_server import codex
        cls.codex = codex

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        srv = self.server
        saved = {
            "_codex_pool_alive": srv._codex_pool_alive,
            "_live_engine_session_ids": srv._live_engine_session_ids,
        }
        srv._codex_pool_alive = lambda now=None: True
        srv._live_engine_session_ids = lambda: frozenset()

        def restore():
            for k, v in saved.items():
                setattr(srv, k, v)
        self.addCleanup(restore)

    def rollout(self, events, name="rollout.jsonl"):
        path = Path(self.tmp.name) / name
        with open(path, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        self._forget(path)
        return path

    def append(self, path, events):
        with open(path, "a") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        # Force a distinct mtime so the mtime cache misses and the
        # incremental resume path runs.
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    def _forget(self, path):
        srv = self.server
        with srv._conv_meta_cache_lock:
            srv._conv_meta_cache.pop(str(path), None)
            srv._codex_tail_resume.pop(str(path), None)

    def meta(self, path):
        return self.codex._extract_codex_tail_meta(path)

    def state(self, path, sid="ccc248-sid", now=None, mtime=None):
        class St:
            pass
        st = St()
        st.st_mtime = mtime if mtime is not None else path.stat().st_mtime
        st.st_size = path.stat().st_size
        st.st_mtime_ns = int(st.st_mtime * 1e9)
        return self.server._codex_state_fields(
            sid, now=now if now is not None else st.st_mtime + 360,
            note_writer_transition=False, rollout_path=path, rollout_stat=st,
        )


class TestTailTerminalReceipts(_Base):
    def test_observed_interrupted_native_turn_is_terminal(self):
        # Projection worker: prompted turn running a tool, interrupted.
        path = self.rollout([
            task_started(T0, TURN_A), user(T0 + 1), tool_call(T0 + 5),
            aborted(T0 + 500, TURN_A),
        ])
        m = self.meta(path)
        self.assertEqual(m["last_event_type"], "result")
        self.assertIsNone(m["pending_tool"])
        self.assertEqual(m["turn_started_ts"], 0)
        self.assertEqual(m["last_terminal_turn_id"], TURN_A)
        self.assertEqual(m["last_terminal_reason"], "interrupted")
        self.assertEqual(m["current_turn_id"], "")

    def test_observed_interrupted_turn_without_pending_tool(self):
        # Filing worker: the abort lands after assistant output, no tool open.
        path = self.rollout([
            task_started(T0, TURN_A), user(T0 + 1),
            _ev(T0 + 2, "response_item", {"type": "reasoning", "summary": []}),
            aborted(T0 + 400, TURN_A),
        ])
        self.assertEqual(self.meta(path)["last_event_type"], "result")

    def test_normal_completion_and_failed_abort(self):
        done = self.rollout([task_started(T0, TURN_A), user(T0 + 1), completed(T0 + 9, TURN_A)], "done.jsonl")
        m = self.meta(done)
        self.assertEqual((m["last_event_type"], m["last_terminal_reason"]), ("result", "completed"))
        failed = self.rollout([task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 9, TURN_A, reason="failed")], "failed.jsonl")
        m = self.meta(failed)
        self.assertEqual((m["last_event_type"], m["last_terminal_reason"]), ("result", "failed"))

    def test_resumed_turn_after_abort_is_working(self):
        path = self.rollout([
            task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 500, TURN_A),
            task_started(T0 + 900, TURN_B), user(T0 + 901),
        ])
        m = self.meta(path)
        self.assertEqual(m["last_event_type"], "user")
        self.assertEqual(m["turn_started_ts"], T0 + 900)

    def test_late_receipt_for_unknown_turn_does_not_end_newer_turn(self):
        path = self.rollout([
            task_started(T0, TURN_B), user(T0 + 1), tool_call(T0 + 2),
            aborted(T0 + 3, "turn-from-elsewhere"),
        ])
        m = self.meta(path)
        self.assertEqual(m["last_event_type"], "assistant")
        self.assertTrue(m["pending_tool"])
        self.assertEqual(m["current_turn_id"], TURN_B)

    def test_unknown_receipt_before_new_turn_prompt_keeps_turn_open(self):
        # The receipt lands between B's task_started and B's prompt.
        path = self.rollout([
            task_started(T0, TURN_A), user(T0 + 1), completed(T0 + 50, TURN_A),
            task_started(T0 + 100, TURN_B), aborted(T0 + 101, "turn-from-elsewhere"),
        ])
        m = self.meta(path)
        self.assertEqual(m["current_turn_id"], TURN_B)
        self.assertEqual(m["turn_started_ts"], T0 + 100)
        self.assertEqual(m["last_terminal_turn_id"], TURN_A)
        self.append(path, [user(T0 + 102), tool_call(T0 + 103)])
        m = self.meta(path)
        self.assertEqual(m["current_turn_id"], TURN_B)
        self.assertEqual(m["turn_started_ts"], T0 + 100)
        self.assertTrue(m["pending_tool"])

    def test_late_receipt_across_incremental_resume(self):
        path = self.rollout([task_started(T0, TURN_B), user(T0 + 1)])
        self.assertEqual(self.meta(path)["last_event_type"], "user")
        self.append(path, [aborted(T0 + 2, "turn-from-elsewhere")])
        self.assertEqual(self.meta(path)["last_event_type"], "user")
        self.append(path, [aborted(T0 + 3, TURN_B)])
        self.assertEqual(self.meta(path)["last_event_type"], "result")

    def test_delayed_duplicate_receipt_for_ended_turn_keeps_newer_turn(self):
        # Verifier case: A aborts, B starts with its own prompt and tool, then
        # a delayed duplicate abort for A (original timestamp) lands.
        path = self.rollout([
            task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 10, TURN_A),
            task_started(T0 + 20, TURN_B), user(T0 + 21), tool_call(T0 + 22),
        ])
        self.assertEqual(self.meta(path)["last_event_type"], "assistant")
        self.append(path, [aborted(T0 + 10, TURN_A)])
        m = self.meta(path)
        self.assertEqual(m["last_event_type"], "assistant")
        self.assertEqual(m["current_turn_id"], TURN_B)
        self.assertTrue(m["pending_tool"])
        self.assertEqual(m["turn_started_ts"], T0 + 20)
        self.assertEqual(m["last_terminal_turn_id"], TURN_A)
        # B's own receipt still ends it.
        self.append(path, [aborted(T0 + 60, TURN_B)])
        m = self.meta(path)
        self.assertEqual((m["last_event_type"], m["last_terminal_turn_id"]), ("result", TURN_B))

    def test_duplicate_receipt_in_one_full_parse(self):
        path = self.rollout([
            task_started(T0, TURN_A), user(T0 + 1), completed(T0 + 10, TURN_A),
            task_started(T0 + 20, TURN_B), user(T0 + 21),
            completed(T0 + 10, TURN_A),
        ])
        self.assertEqual(self.meta(path)["last_event_type"], "user")

    def test_outer_receipt_ends_turn_with_prompted_nested_subturn(self):
        # Real shape: a nested sub-turn starts (with its own prompt) and never
        # closes; the outer turn's task_complete still ends the session turn.
        path = self.rollout([
            task_started(T0, TURN_A), user(T0 + 1),
            task_started(T0 + 20, "f4d6c532-3810-4d18-90a7-57d66cfcbb99"), user(T0 + 21, "sub"),
            completed(T0 + 800, TURN_A),
        ])
        self.assertEqual(self.meta(path)["last_event_type"], "result")

    def test_forked_history_orphans_do_not_pin_working(self):
        path = self.rollout([
            task_started(T0, "parent-outer"), user(T0),
            task_started(T0, "parent-inner"), aborted(T0, "parent-inner"),
            task_started(T0, "parent-orphan"),
            task_started(T0 + 1, TURN_B), completed(T0 + 100, TURN_B),
        ])
        self.assertEqual(self.meta(path)["last_event_type"], "result")

    def test_concurrent_status_reads_agree(self):
        path = self.rollout([task_started(T0, TURN_A), user(T0 + 1), tool_call(T0 + 2)])
        self.meta(path)
        self.append(path, [aborted(T0 + 300, TURN_A)])
        results, errors = [], []

        def read():
            try:
                for _ in range(20):
                    results.append(self.state(path, sid="ccc248-concurrent")["codex_state"])
            except Exception as exc:  # pragma: no cover - surfaced below
                errors.append(exc)
        threads = [threading.Thread(target=read) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(set(results), {"idle"})


class TestStateFieldsDecision(_Base):
    def test_aborted_turns_read_idle_before_stale_threshold(self):
        path = self.rollout([task_started(T0, TURN_A), user(T0 + 1), tool_call(T0 + 5), aborted(T0 + 500, TURN_A)])
        fields = self.state(path, now=path.stat().st_mtime + 360)
        self.assertEqual(fields["codex_state"], "idle")
        self.assertNotIn("working_since", fields)
        # The consuming decision (perf-pane queue health) agrees.
        stats = self.server._queue_health_stats([dict(fields, session_id="ccc248-sid")])
        self.assertEqual(stats.get("unfinished_turns"), 0)

    def test_working_native_session_without_tty_stays_working(self):
        # No live process / TTY, codex_fresh false (rollout 60s quiet): still
        # mid-turn, so still working, never inferred dead.
        path = self.rollout([task_started(T0, TURN_B), user(T0 + 1), tool_call(T0 + 2)])
        fields = self.state(path, now=path.stat().st_mtime + 60)
        self.assertEqual(fields["codex_state"], "working")
        self.assertFalse(fields["codex_fresh"])

    def test_timeout_alone_is_not_terminal(self):
        path = self.rollout([task_started(T0, TURN_B), user(T0 + 1), tool_call(T0 + 2)])
        fields = self.state(path, now=path.stat().st_mtime + 3600)
        self.assertEqual(fields["codex_state"], "stuck")

    def test_app_server_active_turn_survives_older_terminal_receipt(self):
        srv = self.server
        sid = "ccc248-app-active"
        path = self.rollout([task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 500, TURN_A)])
        mtime = path.stat().st_mtime
        srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
            "thread_id": sid, "active_turn_id": TURN_B, "active_writer": "ccc",
            "status": "active", "turn_started_at": T0 + 600, "last_activity_at": T0 + 10,
        }
        self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)
        fields = self.state(path, sid=sid, now=mtime + 5, mtime=mtime)
        self.assertEqual(fields["codex_state"], "working")
        self.assertEqual(fields["working_since"], T0 + 600)

    def test_app_server_active_turn_survives_later_stamped_other_receipt(self):
        # Verifier case: app state says B is active (started t+20); the tail's
        # newest receipt is for A but stamped t+40, after B started.
        srv = self.server
        sid = "ccc248-app-later"
        path = self.rollout([task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 40, TURN_A)])
        mtime = path.stat().st_mtime
        srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
            "thread_id": sid, "active_turn_id": TURN_B, "active_writer": "ccc",
            "status": "active", "turn_started_at": T0 + 20, "last_activity_at": T0 + 21,
        }
        self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)
        fields = self.state(path, sid=sid, now=mtime + 5, mtime=mtime)
        self.assertEqual(fields["codex_state"], "working")
        self.assertEqual(fields["working_since"], T0 + 20)
        stats = srv._queue_health_stats([dict(fields, session_id=sid)])
        self.assertEqual(stats.get("unfinished_turns"), 1)

    def test_app_server_turn_ended_by_its_own_receipt(self):
        srv = self.server
        sid = "ccc248-app-same"
        path = self.rollout([task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 500, TURN_A)])
        mtime = path.stat().st_mtime
        srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
            "thread_id": sid, "active_turn_id": TURN_A, "active_writer": "ccc",
            "status": "active", "turn_started_at": T0, "last_activity_at": T0 + 10,
        }
        self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)
        fields = self.state(path, sid=sid, now=mtime + 5, mtime=mtime)
        self.assertNotEqual(fields["codex_state"], "working")

    def test_exact_receipt_ends_app_turn_regardless_of_timestamp_gap(self):
        srv = self.server
        for delta in (0, 1, 2):
            with self.subTest(app_activity_before_abort=delta):
                sid = "ccc248-app-exact-%d" % delta
                path = self.rollout(
                    [task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 500, TURN_A)],
                    "exact-%d.jsonl" % delta,
                )
                mtime = path.stat().st_mtime
                srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
                    "thread_id": sid, "active_turn_id": TURN_A, "active_writer": "ccc",
                    "status": "active", "turn_started_at": T0,
                    "last_activity_at": T0 + 500 - delta,
                }
                self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)
                fields = self.state(path, sid=sid, now=mtime + 360, mtime=mtime)
                self.assertNotEqual(fields["codex_state"], "working")
                stats = srv._queue_health_stats([dict(fields, session_id=sid)])
                self.assertEqual(stats.get("unfinished_turns"), 0)


class TestSessionStatusApi(_Base):
    """The real /api/session-status handler returns the same decision."""

    def test_api_reports_aborted_turn_idle_and_resumed_turn_working(self):
        srv = self.server
        sid = "ccc248-api-sid"
        path = self.rollout([task_started(T0, TURN_A), user(T0 + 1), tool_call(T0 + 5), aborted(T0 + 500, TURN_A)])
        # Keep the rollout inside the recency window but past the external
        # writer window, as observed (6 minutes after the abort).
        old = time.time() - 360
        os.utime(path, (old, old))
        saved = {
            name: getattr(srv, name) for name in (
                "_is_codex_session", "_resolve_codex_rollout_path", "find_session_cwd",
            )
        }
        srv._is_codex_session = lambda s: s == sid
        srv._resolve_codex_rollout_path = lambda s: path if s == sid else None
        srv.find_session_cwd = lambda s: self.tmp.name

        def restore():
            for k, v in saved.items():
                setattr(srv, k, v)
        self.addCleanup(restore)

        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), srv.CommandCenterHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        url = "http://127.0.0.1:%d/api/session-status?session_id=%s" % (httpd.server_address[1], sid)

        def get():
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.loads(r.read())

        def append_aged(events):
            # Age the rollout past the external-writer window after every
            # write so the decision comes from the turn receipts, not from
            # the mtime "something is writing" heuristic.
            self.append(path, events)
            nonlocal old
            old += 1
            os.utime(path, (old, old))
        body = get()
        self.assertEqual(body["codex_state"], "idle")
        self.assertNotEqual(body.get("state"), "working")

        append_aged([task_started(time.time() - 2, TURN_B), user(time.time() - 1), tool_call(time.time())])
        body = get()
        self.assertEqual(body["codex_state"], "working")

        # A delayed duplicate abort for the already-ended turn A must not
        # idle B, in the API or in the consuming queue-health decision.
        append_aged([aborted(T0 + 500, TURN_A)])
        body = get()
        self.assertEqual(body["codex_state"], "working")
        self.assertTrue(body.get("working_since"))
        stats = srv._queue_health_stats([dict(body, session_id=sid)])
        self.assertEqual(stats.get("unfinished_turns"), 1)

        append_aged([aborted(time.time(), TURN_B)])
        body = get()
        self.assertEqual(body["codex_state"], "idle")

    def _serve(self, sid, path):
        srv = self.server
        saved = {
            name: getattr(srv, name) for name in (
                "_is_codex_session", "_resolve_codex_rollout_path", "find_session_cwd",
            )
        }
        srv._is_codex_session = lambda s: s == sid
        srv._resolve_codex_rollout_path = lambda s: path if s == sid else None
        srv.find_session_cwd = lambda s: self.tmp.name

        def restore():
            for k, v in saved.items():
                setattr(srv, k, v)
        self.addCleanup(restore)
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), srv.CommandCenterHandler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        url = "http://127.0.0.1:%d/api/session-status?session_id=%s" % (httpd.server_address[1], sid)

        def get():
            with urllib.request.urlopen(url, timeout=30) as r:
                body = json.loads(r.read())
            unfinished = srv._queue_health_stats([dict(body, session_id=sid)])["unfinished_turns"]
            return body["codex_state"], unfinished
        return get

    def _aged_rollout(self, events, name):
        path = self.rollout(events, name)
        old = time.time() - 360
        os.utime(path, (old, old))
        return path

    def test_api_unknown_old_receipt_keeps_prompted_turn_working(self):
        sid = "ccc248-api-unknown"
        path = self._aged_rollout([
            task_started(T0, TURN_B), user(T0 + 1), tool_call(T0 + 2),
            aborted(T0 + 3, "turn-from-elsewhere"),
        ], "unknown.jsonl")
        self.assertEqual(self._serve(sid, path)(), ("working", 1))

    def test_api_nested_parent_completion_ends_turn(self):
        sid = "ccc248-api-nested"
        path = self._aged_rollout([
            task_started(T0, TURN_A), user(T0 + 1),
            task_started(T0 + 20, "f4d6c532-3810-4d18-90a7-57d66cfcbb99"), user(T0 + 21, "sub"),
            completed(T0 + 800, TURN_A),
        ], "nested.jsonl")
        self.assertEqual(self._serve(sid, path)(), ("idle", 0))

    def test_api_app_active_turn_survives_later_stamped_other_receipt(self):
        srv = self.server
        sid = "ccc248-api-app-later"
        path = self._aged_rollout([task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 40, TURN_A)], "later.jsonl")
        get = self._serve(sid, path)
        srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
            "thread_id": sid, "active_turn_id": TURN_B, "active_writer": "ccc",
            "status": "active", "turn_started_at": T0 + 20, "last_activity_at": T0 + 21,
        }
        self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)
        self.assertEqual(get(), ("working", 1))
        # The active turn's own receipt ends it.
        srv._CODEX_APP_SERVER_THREAD_STATE[sid]["active_turn_id"] = TURN_A
        self.assertNotEqual(get()[0], "working")

    def test_api_exact_receipt_ends_stale_app_turn_at_close_timestamps(self):
        srv = self.server
        for delta in (0, 1, 2):
            with self.subTest(app_activity_before_abort=delta):
                sid = "ccc248-api-exact-%d" % delta
                path = self._aged_rollout(
                    [task_started(T0, TURN_A), user(T0 + 1), tool_call(T0 + 2), aborted(T0 + 500, TURN_A)],
                    "api-exact-%d.jsonl" % delta,
                )
                get = self._serve(sid, path)
                srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
                    "thread_id": sid, "active_turn_id": TURN_A, "active_writer": "ccc",
                    "status": "active", "turn_started_at": T0,
                    "last_activity_at": T0 + 500 - delta,
                }
                self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)
                self.assertEqual(get(), ("idle", 0))

    def test_api_unknown_receipt_before_new_turn_prompt_keeps_working(self):
        sid = "ccc248-api-unknown-preprompt"
        path = self._aged_rollout([
            task_started(T0, TURN_A), user(T0 + 1), completed(T0 + 50, TURN_A),
            task_started(T0 + 100, TURN_B), aborted(T0 + 101, "turn-from-elsewhere"),
            user(T0 + 102), tool_call(T0 + 103),
        ], "unknown-preprompt.jsonl")
        self.assertEqual(self._serve(sid, path)(), ("working", 1))
        # With the app-server reporting B active, the unknown receipt does
        # not supersede it either.
        srv = self.server
        srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
            "thread_id": sid, "active_turn_id": TURN_B, "active_writer": "ccc",
            "status": "active", "turn_started_at": T0 + 100, "last_activity_at": T0 + 100,
        }
        self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)
        self.assertEqual(self._serve(sid, path)(), ("working", 1))

    def test_api_app_active_tool_survives_other_turn_receipt(self):
        srv = self.server
        sid = "ccc248-api-app-tool"
        path = self._aged_rollout([task_started(T0, TURN_A), user(T0 + 1), aborted(T0 + 40, TURN_A)], "tool.jsonl")
        get = self._serve(sid, path)
        srv._CODEX_APP_SERVER_THREAD_STATE[sid] = {
            "thread_id": sid, "active_turn_id": TURN_B, "active_writer": "ccc",
            "status": "active", "turn_started_at": T0 + 20, "last_activity_at": T0 + 39,
            "current_item": {"tool": "Bash", "started_at": T0 + 30},
        }
        self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)
        self.assertEqual(get(), ("working", 1))


class TestExactTurnPipeline(TestSessionStatusApi):
    """Cycle 4: the reviewer's witness and its controls, end to end through
    the real parser, the real writer attribution
    (_codex_thread_writer_snapshot is NOT stubbed), loopback
    /api/session-status and _queue_health_stats. Timestamps are relative to
    now so writer-window boundaries are realistic."""

    WRITERS = ("ccc", "external", "", "unknown")

    def setUp(self):
        super().setUp()
        self.now = time.time()
        self._n = 0

    def _sid(self, tag):
        self._n += 1
        return "ccc248-c4-%s-%d-%d" % (tag, self._n, int(self.now))

    def _file(self, events, mtime_age):
        self._n += 1
        path = self.rollout(events, "c4-%d.jsonl" % self._n)
        t = self.now - mtime_age
        os.utime(path, (t, t))
        return path

    def _app(self, sid, **state):
        srv = self.server
        state.setdefault("thread_id", sid)
        srv._CODEX_APP_SERVER_THREAD_STATE[sid] = state
        self.addCleanup(srv._CODEX_APP_SERVER_THREAD_STATE.pop, sid, None)

    def _witness(self, end=None):
        n = self.now
        return [
            task_started(n - 500, TURN_A), user(n - 499), tool_call(n - 11),
            end if end is not None else aborted(n - 10, TURN_A),
        ]

    def _both(self, sid, path):
        """(HTTP codex_state, unfinished) plus the in-process decision."""
        api = self._serve(sid, path)()
        st = path.stat()
        fields = self.server._codex_state_fields(
            sid, now=time.time(), note_writer_transition=False,
            rollout_path=path, rollout_stat=st,
        )
        unfinished = self.server._queue_health_stats(
            [dict(fields, session_id=sid)])["unfinished_turns"]
        return api, (fields["codex_state"], unfinished)

    def test_reviewer_witness_exact_turn_idle_for_every_writer(self):
        n = self.now
        for writer in self.WRITERS:
            for app_gap in (0, 1, 2, -5):
                with self.subTest(active_writer=writer, app_activity_vs_abort=app_gap):
                    sid = self._sid("wit")
                    path = self._file(self._witness(), mtime_age=60)
                    self._app(sid, active_turn_id=TURN_A, status="active",
                              active_writer=writer, turn_started_at=n - 500,
                              last_activity_at=n - 10 - app_gap)
                    api, direct = self._both(sid, path)
                    self.assertEqual(api, ("idle", 0))
                    self.assertEqual(direct, ("idle", 0))

    def test_exact_completion_also_ends_turn_for_every_writer(self):
        n = self.now
        for writer in self.WRITERS:
            with self.subTest(active_writer=writer):
                sid = self._sid("cmp")
                path = self._file(self._witness(completed(n - 10, TURN_A)), mtime_age=60)
                self._app(sid, active_turn_id=TURN_A, status="active",
                          active_writer=writer, last_activity_at=n - 10)
                self.assertEqual(self._serve(sid, path)(), ("idle", 0))

    def test_exact_receipt_fresh_mtime_explained_by_receipt(self):
        # The rollout's latest write IS the receipt (mtime within 2s of it):
        # no independent writer, so idle even inside the 20s writer window.
        n = self.now
        for writer in self.WRITERS:
            with self.subTest(active_writer=writer):
                sid = self._sid("rcpt")
                path = self.rollout(self._witness(aborted(n - 3, TURN_A)), "rcpt-%s.jsonl" % (writer or "none"))
                os.utime(path, (n - 3, n - 3))
                self._app(sid, active_turn_id=TURN_A, status="active",
                          active_writer=writer, last_activity_at=n - 3)
                self.assertEqual(self._serve(sid, path)(), ("idle", 0))

    def test_genuine_write_after_receipt_still_reads_external_writer(self):
        # Control: a non-CCC client wrote the rollout well after A's receipt.
        # That is independent evidence of a live writer and must not be
        # suppressed because A ended.
        n = self.now
        for writer in ("external", "", "unknown"):
            with self.subTest(active_writer=writer):
                sid = self._sid("gen")
                # Receipt and last app activity sit outside the 20s window
                # (recent app activity alone attributes writes to CCC).
                path = self._file(self._witness(aborted(n - 40, TURN_A)), mtime_age=1)
                self._app(sid, active_turn_id=TURN_A, status="active",
                          active_writer=writer, last_activity_at=n - 40)
                self.assertEqual(self._serve(sid, path)(), ("working", 1))

    def test_different_active_turn_survives_exact_receipt_of_older_turn(self):
        n = self.now
        for writer in self.WRITERS:
            with self.subTest(active_writer=writer):
                sid = self._sid("diff")
                path = self._file(self._witness(), mtime_age=60)
                self._app(sid, active_turn_id=TURN_B, status="active",
                          active_writer=writer, turn_started_at=n - 8,
                          last_activity_at=n - 8)
                self.assertEqual(self._serve(sid, path)(), ("working", 1))

    def test_pending_ccc_turn_start_is_not_ended_by_previous_receipt(self):
        n = self.now
        sid = self._sid("pend")
        path = self._file(self._witness(), mtime_age=60)
        self._app(sid, active_turn_id=TURN_A, status="active", active_writer="ccc",
                  ccc_turn_start_pending=True, ccc_turn_start_pending_at=n - 2,
                  last_activity_at=n - 2)
        self.assertEqual(self._serve(sid, path)(), ("working", 1))

    def test_unknown_status_without_turn_identity_stays_conservative(self):
        # No active_turn_id: nothing ties the active status to A, so another
        # client's active status keeps the thread busy (unknown ownership).
        n = self.now
        sid = self._sid("noid")
        path = self._file(self._witness(), mtime_age=60)
        self._app(sid, status="active", active_writer="external", last_activity_at=n - 5)
        self.assertEqual(self._serve(sid, path)(), ("working", 1))

    def _preprompt(self, tail_events=()):
        n = self.now
        return [
            task_started(n - 400, TURN_A), user(n - 399), completed(n - 300, TURN_A),
            task_started(n - 30, TURN_B), *tail_events,
        ]

    def test_preprompt_started_turn_is_working(self):
        n = self.now
        cases = {
            "first-ever B": [task_started(n - 30, TURN_B)],
            "B after result A": self._preprompt(),
            "B then unknown receipt": self._preprompt([aborted(n - 29, "turn-from-elsewhere")]),
            "B then duplicate A receipt": self._preprompt([aborted(n - 29, TURN_A)]),
        }
        for name, events in cases.items():
            for app_b in (False, True):
                with self.subTest(case=name, app_active_b=app_b):
                    sid = self._sid("pre")
                    path = self._file(events, mtime_age=60)
                    m = self.meta(path)
                    self.assertEqual(m["current_turn_id"], TURN_B)
                    self.assertAlmostEqual(m["turn_started_ts"], n - 30, delta=0.01)
                    self.assertNotEqual(m["last_event_type"], "result")
                    if app_b:
                        self._app(sid, active_turn_id=TURN_B, status="active",
                                  active_writer="ccc", turn_started_at=n - 30,
                                  last_activity_at=n - 30)
                    api, direct = self._both(sid, path)
                    self.assertEqual(api, ("working", 1))
                    self.assertEqual(direct, ("working", 1))

    def test_preprompt_turn_ends_only_on_its_own_receipt(self):
        n = self.now
        for app_b in (False, True):
            with self.subTest(app_active_b=app_b):
                sid = self._sid("preend")
                path = self._file(self._preprompt([aborted(n - 20, TURN_B)]), mtime_age=60)
                if app_b:
                    self._app(sid, active_turn_id=TURN_B, status="active",
                              active_writer="external", last_activity_at=n - 20)
                self.assertEqual(self._serve(sid, path)(), ("idle", 0))

    def test_quiet_preprompt_turn_is_never_idled_by_a_timer(self):
        # Paused B: started, then silent past the stale threshold. A timer
        # may flag it stuck, but only a receipt makes it idle.
        n = self.now
        sid = self._sid("quiet")
        path = self._file(self._preprompt(), mtime_age=0)
        st = path.stat()
        later = st.st_mtime + self.server._codex_stale_tool_threshold_s() + 60
        fields = self.server._codex_state_fields(
            sid, now=later, note_writer_transition=False,
            rollout_path=path, rollout_stat=st,
        )
        self.assertEqual(fields["codex_state"], "stuck")

    def test_witness_across_incremental_resume(self):
        # The cache resumes mid-file: the receipt arrives in a later append.
        n = self.now
        for writer in ("external", "ccc"):
            with self.subTest(active_writer=writer):
                sid = self._sid("inc")
                path = self._file(self._witness()[:-1], mtime_age=120)
                self.meta(path)
                self.append(path, [aborted(n - 10, TURN_A)])
                t = n - 60
                os.utime(path, (t, t))
                self._app(sid, active_turn_id=TURN_A, status="active",
                          active_writer=writer, last_activity_at=n - 10)
                self.assertEqual(self._serve(sid, path)(), ("idle", 0))


if __name__ == "__main__":
    unittest.main()
