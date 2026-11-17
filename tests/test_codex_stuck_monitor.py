from tests.dashboard_source import dashboard_source
import types
import unittest
from pathlib import Path
from unittest import mock

import server


ROOT = Path(__file__).resolve().parents[1]


class CodexStuckSummaryTests(unittest.TestCase):
    def test_summary_prefilters_stale_tails_then_confirms_authoritative_state(self):
        now = 10_000.0
        rows = [
            {"id": "confirmed-stuck"},
            {"id": "active-writer"},
            {"id": "fresh-mid-turn"},
            {"id": "old-mid-turn"},
        ]
        paths = {
            "confirmed-stuck": mock.Mock(stat=lambda: types.SimpleNamespace(st_mtime=8_000.0)),
            "active-writer": mock.Mock(stat=lambda: types.SimpleNamespace(st_mtime=8_100.0)),
            "fresh-mid-turn": mock.Mock(stat=lambda: types.SimpleNamespace(st_mtime=9_950.0)),
            "old-mid-turn": mock.Mock(stat=lambda: types.SimpleNamespace(st_mtime=1.0)),
        }

        def rollout_path(row):
            return paths[row["id"]]

        tail_reads = []

        def tail_meta(path):
            tail_reads.append(path)
            return {"last_event_type": "assistant", "pending_tool": None}

        def authoritative_state(
            sid,
            _now,
            note_writer_transition=True,
            rollout_path=None,
            rollout_stat=None,
            rollout_tail=None,
        ):
            self.assertFalse(note_writer_transition)
            self.assertIs(rollout_path, paths[sid])
            self.assertEqual(rollout_stat.st_mtime, paths[sid].stat().st_mtime)
            self.assertEqual(rollout_tail["last_event_type"], "assistant")
            return {
                "codex_state": "working" if sid == "active-writer" else "stuck",
            }

        with (
            mock.patch.object(server, "_codex_fetch_threads", return_value=rows),
            mock.patch.object(server, "_codex_rollout_path_from_row", side_effect=rollout_path),
            mock.patch.object(server, "_extract_codex_tail_meta", side_effect=tail_meta),
            mock.patch.object(server, "_codex_pool_alive", return_value=True),
            mock.patch.object(server, "_live_engine_session_ids", return_value=set()),
            mock.patch.object(server, "_codex_recent_window_s", return_value=3_600.0),
            mock.patch.object(server, "_codex_stale_tool_threshold_s", return_value=900.0),
            mock.patch.object(server, "_codex_state_fields", side_effect=authoritative_state),
        ):
            summary = server.build_codex_stuck_summary(now=now, force=True)

        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["session_ids"], ["confirmed-stuck"])
        self.assertEqual(summary["candidates"], 2)
        self.assertEqual(summary["recent_sessions"], 3)
        self.assertEqual(summary["threshold_s"], 900)
        self.assertNotIn(paths["fresh-mid-turn"], tail_reads)


class CodexStuckMonitorUiTests(unittest.TestCase):
    def test_footer_polls_cached_stuck_summary_and_labels_the_heuristic(self):
        app_js = dashboard_source(ROOT / "static" / "app.js")
        self.assertIn("cccStuckPill", app_js)
        self.assertIn("/api/codex/stuck-summary", app_js)
        self.assertIn("sessions currently labeled Stuck", app_js)
        self.assertIn("stuck -", app_js)


if __name__ == "__main__":
    unittest.main()


def test_concurrent_cold_stuck_summary_builds_once(monkeypatch):
    """CCC-142: boot pollers missing the cache together share one scan."""
    import threading as _th
    import time as _time
    from ccc_server import codex as _cx

    monkeypatch.setitem(_cx._codex_stuck_summary_cache, "value", None)
    monkeypatch.setitem(_cx._codex_stuck_summary_cache, "ts", 0.0)
    calls = []

    def slow_fetch(**kwargs):
        calls.append(1)
        _time.sleep(0.2)
        return []

    monkeypatch.setattr(_cx._core, "_codex_fetch_threads", slow_fetch)
    monkeypatch.setattr(_cx._core, "_codex_pool_alive", lambda now: False)
    monkeypatch.setattr(_cx._core, "_live_engine_session_ids", lambda: set())
    out = []
    ts = [_th.Thread(target=lambda: out.append(_cx.build_codex_stuck_summary())) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(calls) == 1
    assert len(out) == 6 and all(o["count"] == 0 for o in out)
    _cx.build_codex_stuck_summary(force=True)
    assert len(calls) == 2


def test_summary_reads_all_candidates_without_prompt_payloads_or_row_callbacks(tmp_path, monkeypatch):
    import os
    import sqlite3

    from ccc_server import codex, codex_accounts

    now = 10_000.0
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("{}\n")
    os.utime(rollout, (9_000, 9_000))
    db = tmp_path / "state.sqlite"
    with sqlite3.connect(db) as con:
        con.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, "
                    "created_at INTEGER, updated_at INTEGER, updated_at_ms INTEGER, "
                    "first_user_message TEXT)")
        con.executemany("INSERT INTO threads VALUES (?, ?, ?, ?, ?, ?)", [
            (f"thread-{i:04}", str(rollout), 8_000, 9_000 + i // 2,
             None if i % 2 else (9_000 + i // 2) * 1000, "unused prompt" * 1_000)
            for i in range(1_300)
        ])

    callbacks, prompt_reads = [], []
    real_connect = sqlite3.connect

    class MetadataConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            self.row_factory = lambda cursor, row: (
                callbacks.append(1) or sqlite3.Row(cursor, row))
            return super().execute(sql, parameters)

    def connect(*args, **kwargs):
        con = real_connect(*args, **kwargs, factory=MetadataConnection)

        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and column == "first_user_message":
                prompt_reads.append(column)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        con.set_authorizer(authorize)
        return con

    monkeypatch.setattr(codex.sqlite3, "connect", connect)
    monkeypatch.setattr(codex_accounts, "homes", lambda: [tmp_path])
    monkeypatch.setattr(server, "_codex_home", lambda: tmp_path)
    monkeypatch.setattr(codex, "_codex_state_db_candidates", lambda *args: [db])
    monkeypatch.setattr(codex, "_CODEX_THREADS_CACHE", {})
    monkeypatch.setattr(server, "_codex_deleted_thread_ids", lambda: set())
    monkeypatch.setattr(server, "_codex_pool_alive", lambda now: False)
    monkeypatch.setattr(server, "_live_engine_session_ids", lambda: set())
    monkeypatch.setattr(server, "_extract_codex_tail_meta", lambda path: {})
    monkeypatch.setattr(server, "_codex_row_state", lambda *args: "stuck")
    monkeypatch.setattr(server, "_codex_state_fields", lambda sid, *a, **kw: {
        "codex_state": "stuck" if sid == "thread-0000" else "working",
    })
    monkeypatch.setattr(server, "_codex_recent_window_s", lambda: 3_600)

    result = server.build_codex_stuck_summary(now=now, force=True)

    assert result["session_ids"] == ["thread-0000"]
    assert result["candidates"] == result["recent_sessions"] == 1_300
    assert prompt_reads == []
    # Schema metadata plus one aggregated result, independent of fleet size.
    assert len(callbacks) < 20
