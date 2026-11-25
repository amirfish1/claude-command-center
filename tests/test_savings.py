"""Tests for ccc_server/savings.py — the /api/savings engine (lane L12).

Covers the contract shape, list-price math, resume dedupe, free-run
accounting, plan fee accrual, and the perf budget: an unchanged transcript
must never be re-read (the sqlite ledger keyed by (mtime,size) is the
persistent cache).
"""
import json
import os
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ccc_server import savings


NOW = datetime.now().astimezone()
TODAY = NOW.date().isoformat()
YESTERDAY = (NOW.date() - timedelta(days=1)).isoformat()
LAST_MONTH = (NOW.date() - timedelta(days=31)).isoformat()


def _assistant(msg_id, model, day=TODAY, sid="sess-1", inp=1000, cw=0, cr=0, out=0,
               sidechain=False, ts=None):
    return {
        "type": "assistant",
        "sessionId": sid,
        "isSidechain": sidechain,
        "timestamp": ts or f"{day}T12:00:00+00:00",
        "message": {
            "id": msg_id,
            "model": model,
            "usage": {
                "input_tokens": inp,
                "cache_creation_input_tokens": cw,
                "cache_read_input_tokens": cr,
                "output_tokens": out,
            },
        },
    }


def _user(day=TODAY, sid="sess-1"):
    return {"type": "user", "sessionId": sid, "timestamp": f"{day}T11:00:00+00:00",
            "message": {"role": "user", "content": "hi"}}


def _write_transcript(root, project, name, events):
    pdir = root / project
    pdir.mkdir(parents=True, exist_ok=True)
    f = pdir / name
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return f


@pytest.fixture(autouse=True)
def _clean_savings(tmp_path, monkeypatch):
    savings._reset_for_tests()
    monkeypatch.setattr(savings, "SAVINGS_PLANS_DB", tmp_path / "plans.sqlite3")
    monkeypatch.setattr(savings, "SAVINGS_FREE_IDS_FILE", tmp_path / "free-ids.json")
    yield
    savings._reset_for_tests()


def _payload(tmp_path, range_key="today", **kw):
    kw.setdefault("projects_root", tmp_path / "projects")
    kw.setdefault("ledger_db", tmp_path / "savings.sqlite3")
    kw.setdefault("free_ids", set())
    kw.setdefault("analytics_fn", lambda r: None)
    kw.setdefault("now", NOW)
    kw.setdefault("scan_budget_s", None)
    return savings.savings_payload(range_key, **kw)


def test_rollup_batches_share_one_snapshot(tmp_path, monkeypatch):
    ledger = tmp_path / "savings.sqlite3"
    reader = savings._connect_ledger(ledger)
    writer = savings._connect_ledger(ledger)
    try:
        with writer:
            writer.executemany(
                "INSERT INTO rollup (day, model, session_id, input_tokens) VALUES (?, ?, ?, ?)",
                [(YESTERDAY, "", f"s-{i:04d}", i) for i in range(2050)]
                + [(TODAY, "", "outside-window", 9)],
            )
        fetch_rows = savings._sqlite_fetch_rows
        batches = []

        def fetch_then_update(*args):
            rows = fetch_rows(*args)
            batches.append(len(rows))
            if len(batches) == 1:
                with writer:
                    writer.execute("UPDATE rollup SET input_tokens = -1 WHERE session_id = 's-2049'")
                    writer.execute("INSERT INTO rollup (day, model, session_id) VALUES (?, '', 'z')",
                                   (YESTERDAY,))
            return rows

        monkeypatch.setattr(savings, "_sqlite_fetch_rows", fetch_then_update)
        rows = savings._rollup(reader, YESTERDAY, TODAY)
        assert len(batches) > 1
        assert [(row[2], row[3]) for row in rows] == [(f"s-{i:04d}", i) for i in range(2050)]
        assert not reader.in_transaction
        assert savings._rollup(reader, YESTERDAY, TODAY)[-2][3] == -1
    finally:
        reader.close()
        writer.close()


def test_concurrent_ranges_share_a_scan_through_backlog(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    ledger = tmp_path / "savings.sqlite3"
    _write_transcript(root, "p", "a.jsonl", [_assistant("m1", "claude-sonnet-4-6")])
    started = threading.Event()
    other_root = tmp_path / "other-projects"
    other_root.mkdir()
    run_foreground = threading.Event()
    backlog_started = threading.Event()
    run_backlog = threading.Event()
    backlog_threads = []
    real_scan = savings._scan_incremental

    def slow_scan(conn, scan_root, deadline):
        if scan_root == other_root:
            return real_scan(conn, scan_root, deadline)
        if deadline is not None:
            started.set()
            assert run_foreground.wait(5)
            return real_scan(conn, scan_root, time.monotonic() - 1)
        backlog_threads.append(threading.current_thread())
        # The range reader must still respond while the scan owns the write lock.
        conn.execute("BEGIN IMMEDIATE")
        try:
            backlog_started.set()
            assert run_backlog.wait(5)
        finally:
            conn.rollback()
        return real_scan(conn, scan_root)

    monkeypatch.setattr(savings, "_scan_incremental", slow_scan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        owner = pool.submit(_payload, tmp_path, scan_budget_s=2)
        try:
            assert started.wait(5)
            follower = pool.submit(_payload, tmp_path, "week", scan_budget_s=2)
            payload, status = follower.result(timeout=3)
            assert status == 200 and payload["partial"]
            run_foreground.set()
            assert backlog_started.wait(5)
            assert owner.result(timeout=3)[0]["partial"]
            follower = pool.submit(_payload, tmp_path, "all", scan_budget_s=2)
            payload, status = follower.result(timeout=3)
            assert status == 200 and payload["partial"]
            assert len(backlog_threads) == 1
            other_ledger = tmp_path / "other.sqlite3"
            assert savings._refresh_ledger_sync(2, other_root, other_ledger)["remaining"] == 0
            assert other_ledger.is_file()
        finally:
            run_foreground.set()
            run_backlog.set()
            for thread in backlog_threads:
                thread.join(timeout=5)
                assert not thread.is_alive()

    monkeypatch.setattr(savings, "_scan_incremental", real_scan)
    payload, status = _payload(tmp_path, "all")
    assert status == 200 and not payload["partial"]
    assert payload["sessions"] == 1
    with sqlite3.connect(ledger) as conn:
        assert conn.execute("SELECT COUNT(*) FROM backlog").fetchone()[0] == 0


def test_savings_scan_releases_ledger_on_failure(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _write_transcript(root, "p", "a.jsonl", [_assistant("m1", "claude-sonnet-4-6")])
    ledger = tmp_path / "savings.sqlite3"
    real_scan = savings._scan_incremental

    def broken_scan(*args):
        raise sqlite3.OperationalError("test scan failure")

    monkeypatch.setattr(savings, "_scan_incremental", broken_scan)
    assert savings._refresh_ledger_sync(2, root, ledger)["scanned"] == 0
    monkeypatch.setattr(savings, "_scan_incremental", real_scan)
    assert savings._refresh_ledger_sync(None, root, ledger)["scanned"] == 1


class TestScan:
    def test_parses_usage_buckets_and_session(self, tmp_path):
        f = _write_transcript(tmp_path, "p", "a.jsonl", [
            _user(),
            _assistant("m1", "claude-sonnet-4-6", inp=10, cw=20, cr=30, out=40),
        ])
        sid, rows = savings.savings_scan_transcript(f)
        assert sid == "sess-1"
        assert rows == [("m1", TODAY, "claude-sonnet-4-6", 10, 20, 30, 40, 0)]

    def test_dedupes_replayed_message_ids_within_a_file(self, tmp_path):
        f = _write_transcript(tmp_path, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6"),
            _assistant("m1", "claude-sonnet-4-6"),  # resume replay
        ])
        _sid, rows = savings.savings_scan_transcript(f)
        assert len(rows) == 1

    def test_skips_malformed_and_non_assistant_lines(self, tmp_path):
        f = _write_transcript(tmp_path, "p", "a.jsonl", [
            {"type": "system", "timestamp": f"{TODAY}T10:00:00Z"},
            "not json at all",
            _assistant("m1", "claude-sonnet-4-6"),
        ])
        _sid, rows = savings.savings_scan_transcript(f)
        assert len(rows) == 1


class TestPayload:
    def test_contract_keys_and_pricing(self, tmp_path):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", sid="s1",
                       inp=1_000_000, out=1_000_000),  # $3 + $15 = $18
        ])
        _write_transcript(root, "p", "b.jsonl", [
            _assistant("m9", "claude-opus-4-6", sid="s2",
                       inp=1_000_000, out=0),  # $5
        ])
        payload, status = _payload(tmp_path)
        assert status == 200
        for key in ("api_value_usd", "plan_cost_usd", "roi_x", "free_tokens",
                    "free_saved_usd", "free_runs", "sessions", "updated_at"):
            assert key in payload, f"missing contract key {key}"
        assert payload["api_value_usd"] == 23.0
        assert payload["sessions"] == 2
        assert payload["range"] == "today"

    def test_resume_replay_dedupes_across_files(self, tmp_path):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", sid="s1", inp=1_000_000),
        ])
        # Resumed session: new file continues under the same session id and
        # replays message m1 before recording new turns.
        _write_transcript(root, "p", "b.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", sid="s1", inp=1_000_000),
            _assistant("m2", "claude-sonnet-4-6", sid="s1", inp=1_000_000),
        ])
        payload, _ = _payload(tmp_path)
        # m1 counted once across both files: 2 x 1M input at Sonnet $3/MTok.
        assert payload["api_value_usd"] == 6.0
        assert payload["sessions"] == 1

    def test_sidechain_usage_counts_toward_value(self, tmp_path):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", inp=1_000_000, sidechain=True),
        ])
        payload, _ = _payload(tmp_path)
        assert payload["api_value_usd"] == 3.0

    def test_unknown_model_priced_at_fallback_and_flagged(self, tmp_path):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "some-free-router-model", inp=1_000_000),
        ])
        payload, _ = _payload(tmp_path)
        assert payload["api_value_usd"] == 3.0          # Sonnet fallback
        assert payload["estimated_share_usd"] == 3.0

    def test_ranges_filter_by_local_day(self, tmp_path):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", day=TODAY, inp=1_000_000),
        ])
        _write_transcript(root, "p", "b.jsonl", [
            _assistant("m2", "claude-sonnet-4-6", day=LAST_MONTH, sid="s2", inp=1_000_000),
        ])
        today, _ = _payload(tmp_path, "today")
        assert today["api_value_usd"] == 3.0
        assert today["sessions"] == 1
        alltime, _ = _payload(tmp_path, "all")
        assert alltime["api_value_usd"] == 6.0
        assert alltime["sessions"] == 2

    def test_bad_range_rejected(self, tmp_path):
        payload, status = _payload(tmp_path, "fortnight")
        assert status == 400
        assert payload["ok"] is False

    def test_empty_corpus_returns_zeros(self, tmp_path):
        payload, status = _payload(tmp_path)
        assert status == 200
        assert payload["api_value_usd"] == 0.0
        assert payload["sessions"] == 0
        assert payload["free_runs"] == 0


class TestPerfBudget:
    """Perf-gate mirrors test_perf_budget.py: counts WORK, not just output."""

    def test_transcript_stat_walk_keeps_symlinks_and_shallow_sorted_order(self, tmp_path):
        root = tmp_path / "projects"
        transcript = _write_transcript(root, "z", "b.jsonl", [])
        project = root / "a"
        project.mkdir()
        (project / "linked.jsonl").symlink_to(transcript)
        (project / "broken.jsonl").symlink_to(tmp_path / "missing")
        (project / "directory.jsonl").mkdir()
        (root / "z" / "subagents").mkdir()
        (root / "z" / "subagents" / "child.jsonl").touch()
        (root / "project-link").symlink_to(root / "z", target_is_directory=True)

        rows = list(savings._transcript_stats(root))
        assert [path for path, _ in rows] == [
            str(project / "linked.jsonl"),
            str(root / "project-link" / "b.jsonl"),
            str(transcript),
        ]
        assert all(file_stat == transcript.stat() for _, file_stat in rows)

    def test_existing_ledger_adds_file_claim_lookup_without_rebuilding(self, tmp_path):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", inp=1_000_000),
        ])
        _payload(tmp_path)
        ledger = tmp_path / "savings.sqlite3"
        conn = savings._connect_ledger(ledger)
        # Simulate an existing ledger written before the file lookup index.
        conn.execute("DROP INDEX IF EXISTS idx_claims_file")
        conn.commit()
        tables = ("files", "events", "claims", "rollup")
        before = {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")]
                  for t in tables}
        conn.close()
        conn = savings._connect_ledger(ledger)
        try:
            assert {t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t}")]
                    for t in tables} == before
            plan = conn.execute(
                "EXPLAIN QUERY PLAN DELETE FROM claims WHERE file_path=?",
                ("a.jsonl",),
            ).fetchall()
            assert any("SEARCH claims USING INDEX idx_claims_file" in r[3] for r in plan)
        finally:
            conn.close()

    def test_unchanged_transcripts_are_never_reparsed(self, tmp_path, monkeypatch):
        root = tmp_path / "projects"
        for i in range(6):
            _write_transcript(root, "p", f"{i}.jsonl", [
                _assistant(f"m{i}", "claude-sonnet-4-6"),
            ])
        calls = []
        original = savings.savings_scan_transcript

        def counting(path):
            calls.append(str(path))
            return original(path)

        monkeypatch.setattr(savings, "savings_scan_transcript", counting)
        _payload(tmp_path)
        assert len(calls) == 6           # cold: every file parsed once
        calls.clear()
        _payload(tmp_path)
        assert calls == []               # warm: zero re-reads

    def test_only_the_changed_file_reparses(self, tmp_path, monkeypatch):
        root = tmp_path / "projects"
        f1 = _write_transcript(root, "p", "a.jsonl", [_assistant("m1", "claude-sonnet-4-6")])
        _write_transcript(root, "p", "b.jsonl", [_assistant("m2", "claude-sonnet-4-6")])
        calls = []
        original = savings.savings_scan_transcript
        monkeypatch.setattr(
            savings, "savings_scan_transcript",
            lambda p: calls.append(str(p)) or original(p),
        )
        _payload(tmp_path)
        calls.clear()
        time.sleep(0.01)
        f1.write_text(f1.read_text() + json.dumps(
            _assistant("m3", "claude-sonnet-4-6")) + "\n")
        os.utime(f1, None)
        _payload(tmp_path)
        assert calls == [str(f1)]

    def test_deleted_transcript_drops_its_events(self, tmp_path):
        root = tmp_path / "projects"
        f1 = _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", inp=1_000_000),
        ])
        payload, _ = _payload(tmp_path)
        assert payload["api_value_usd"] == 3.0
        f1.unlink()
        payload, _ = _payload(tmp_path)
        assert payload["api_value_usd"] == 0.0

    def test_deleting_the_claimant_transfers_to_a_replay_copy(self, tmp_path):
        # a.jsonl claims m1; b.jsonl holds a replay copy. Deleting a.jsonl
        # must keep m1 counted exactly once, now under b.jsonl.
        root = tmp_path / "projects"
        f1 = _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", sid="s1", inp=1_000_000),
        ])
        _write_transcript(root, "p", "b.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", sid="s1", inp=1_000_000),
        ])
        payload, _ = _payload(tmp_path)
        assert payload["api_value_usd"] == 3.0   # deduped to one claim
        f1.unlink()
        payload, _ = _payload(tmp_path)
        assert payload["api_value_usd"] == 3.0   # surviving copy takes over


class TestFreeRuns:
    def test_free_sessions_priced_at_sonnet_and_counted(self, tmp_path):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            # A real opus session would price $5/Mtok in; free => Sonnet $3.
            _assistant("m1", "claude-opus-4-6", sid="free-1", inp=1_000_000, out=1_000_000),
        ])
        _write_transcript(root, "p", "b.jsonl", [
            _assistant("m2", "claude-sonnet-4-6", sid="paid-1", inp=1_000_000),
        ])
        payload, _ = _payload(tmp_path, free_ids={"free-1"})
        assert payload["free_runs"] == 1
        assert payload["free_tokens"] == 2_000_000
        # Sonnet: 1M in * $3 + 1M out * $15.
        assert payload["free_saved_usd"] == 18.0
        assert payload["free_source"] == "sessions"
        # Free run still counts in total API-priced value and sessions.
        assert payload["api_value_usd"] == 18.0 + 3.0
        assert payload["sessions"] == 2

    def test_registry_marks_persist_across_calls(self, tmp_path, monkeypatch):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "some-router-model", sid="free-9", inp=1_000_000),
        ])
        monkeypatch.setattr(
            savings, "_spawn_registry_entries",
            lambda: [{"runtime": "free", "session_id": "free-9"}],
        )
        monkeypatch.setattr(savings, "SAVINGS_FREE_IDS_FILE", tmp_path / "free.json")
        payload, _ = _payload(tmp_path, free_ids=None)
        assert payload["free_runs"] == 1
        # Registry entry gone (pid pruned) — the persisted set still counts it.
        monkeypatch.setattr(savings, "_spawn_registry_entries", lambda: [])
        payload, _ = _payload(tmp_path, free_ids=None)
        assert payload["free_runs"] == 1

    def test_router_analytics_raises_the_free_floor(self, tmp_path):
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "some-router-model", sid="free-1", inp=1_000_000),
        ])
        analytics = lambda r: {"input_tokens": 5_000_000, "output_tokens": 2_000_000,
                               "requests": 40, "est_savings_usd": 12.0, "window": "24h"}
        payload, _ = _payload(tmp_path, free_ids={"free-1"}, analytics_fn=analytics)
        # Router saw more traffic than CCC sessions account for.
        assert payload["free_tokens"] == 7_000_000
        assert payload["free_source"] == "sessions+router"
        # Tracked 1M in at $3 + excess 4M in * $3 + 2M out * $15.
        assert payload["free_saved_usd"] == 3.0 + 12.0 + 30.0


class TestPlan:
    def test_default_plan_is_max_200(self, tmp_path):
        payload, _ = _payload(tmp_path)
        plan = payload["plan"]
        assert plan["source"] == "default"
        assert plan["monthly_usd"] == savings.DEFAULT_PLAN_MONTHLY_USD

    def test_today_plan_cost_prorates_the_day(self, tmp_path):
        payload, _ = _payload(tmp_path, "today")
        import calendar
        dim = calendar.monthrange(NOW.year, NOW.month)[1]
        day_frac = ((NOW - NOW.replace(hour=0, minute=0, second=0, microsecond=0))
                    .total_seconds() / 86400.0)
        expected = 200.0 * day_frac / dim
        assert payload["plan_cost_usd"] == round(expected, 2)

    def test_month_range_accrues_month_to_date(self, tmp_path):
        payload, _ = _payload(tmp_path, "month")
        import calendar
        dim = calendar.monthrange(NOW.year, NOW.month)[1]
        elapsed = NOW.day - 1 + (
            (NOW - NOW.replace(hour=0, minute=0, second=0, microsecond=0))
            .total_seconds() / 86400.0
        )
        assert payload["plan_cost_usd"] == round(200.0 * elapsed / dim, 2)

    def test_set_plan_writes_configured_row(self, tmp_path):
        plan, err = savings.savings_set_plan(monthly_usd=20.0, name="Pro")
        assert err is None
        assert plan["source"] == "configured"
        assert plan["monthly_usd"] == 20.0
        conn = sqlite3.connect(str(savings.SAVINGS_PLANS_DB))
        rows = conn.execute("SELECT name, monthly_fee FROM subscription_plans").fetchall()
        conn.close()
        assert rows == [("Pro", 20.0)]

    def test_panel_rows_replace_only_panel_rows(self, tmp_path):
        # A plan written by the throughput CLI (different note) is untouched.
        savings.SAVINGS_PLANS_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(savings.SAVINGS_PLANS_DB))
        from ccc_server.usage_db import schema as _udb_schema
        _udb_schema.migrate(conn)
        conn.execute(
            "INSERT INTO subscription_plans (name, engine, monthly_fee, currency, note) "
            "VALUES ('cli-plan', 'claude_code', 100.0, 'USD', 'via cli')"
        )
        conn.commit()
        conn.close()
        savings.savings_set_plan(monthly_usd=20.0)
        conn = sqlite3.connect(str(savings.SAVINGS_PLANS_DB))
        rows = {r[0] for r in conn.execute("SELECT name FROM subscription_plans")}
        conn.close()
        assert rows == {"cli-plan", "primary"}

    def test_reset_restores_default(self, tmp_path):
        savings.savings_set_plan(monthly_usd=20.0)
        plan, err = savings.savings_set_plan(reset=True)
        assert err is None
        assert plan["source"] == "default"

    def test_plan_post_validation(self, tmp_path):
        payload, status = savings.handle_plan_post({"monthly_usd": "lots"})
        assert status == 400
        payload, status = savings.handle_plan_post({"monthly_usd": -5})
        assert status == 400
        payload, status = savings.handle_plan_post({})
        assert status == 400

    @pytest.mark.parametrize("hour,minute", [(0, 1), (1, 30), (12, 0)])
    def test_roi_is_value_over_plan_cost(self, tmp_path, hour, minute):
        import calendar
        now = NOW.replace(hour=hour, minute=minute, second=0, microsecond=0)
        root = tmp_path / "projects"
        _write_transcript(root, "p", "a.jsonl", [
            _assistant("m1", "claude-sonnet-4-6", inp=10_000_000),  # $30 of value
        ])
        payload, _ = _payload(tmp_path, now=now)
        # roi_x is computed on unrounded internals; the cents rounding of
        # plan_cost_usd can shift the ratio substantially early in the day,
        # so use the unrounded accrual for an exact expectation.
        day_fraction = (hour * 3600 + minute * 60) / 86400
        plan_cost = 200.0 * day_fraction / calendar.monthrange(now.year, now.month)[1]
        assert payload["plan_cost_usd"] == round(plan_cost, 2)
        assert payload["roi_x"] == round(30.0 / plan_cost, 1)


def _ledger_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER)")
    conn.executemany(
        "INSERT INTO files VALUES (?,?,?)",
        [("/a.jsonl", 1794636405.7928371, 10), ("/b.jsonl", 1794553028.6078525, 2048)],
    )
    return conn


def test_ledger_file_rows_match_the_ordinary_read_exactly():
    conn = _ledger_conn()
    conn.executemany("INSERT INTO files VALUES (?,?,?)", [
        ("/zero.jsonl", 0.0, 0),
        ("/future.jsonl", 32503680000.000004, 23),
        ("/past.jsonl", -1794636405.7928371, 45),
        ("/fraction.jsonl", -1.0000000000000002, 67),
    ])
    plain = [tuple(row) for row in conn.execute("SELECT path, mtime, size FROM files")]
    assert savings._ledger_file_rows(conn) == plain


@pytest.mark.parametrize("mtime", [0.125, -0.125, float(1 << 53)])
def test_ledger_file_rows_fall_back_for_stamps_outside_packed_range(mtime):
    conn = _ledger_conn()
    conn.execute("INSERT INTO files VALUES (?,?,?)", ("/edge.jsonl", mtime, 42))
    plain = [tuple(row) for row in conn.execute("SELECT path, mtime, size FROM files")]
    assert savings._ledger_file_rows(conn) == plain
