import sqlite3
from datetime import datetime, timedelta, timezone

from productivity import (
    ProductivityStore,
    presence_summary,
    read_macos_idle_seconds,
)


UTC = timezone.utc


def test_payload_round_trip(tmp_path):
    store = ProductivityStore(tmp_path / "productivity.db")
    store.save_payload(
        {"ok": True, "summary": {"features": 2}}, generated_at=100.0
    )
    cached = store.load_payload()
    assert cached == {
        "generated_at": 100.0,
        "payload": {"ok": True, "summary": {"features": 2}},
    }


def test_malformed_payload_is_ignored(tmp_path):
    store = ProductivityStore(tmp_path / "productivity.db")
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cache(key, generated_at, payload_json) VALUES (?, ?, ?)",
            ("full-16-weeks", 100.0, "{not-json"),
        )
    assert store.load_payload() is None


def test_incompatible_schema_rebuilds_database(tmp_path):
    path = tmp_path / "productivity.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '999')")
        conn.execute("CREATE TABLE obsolete(value TEXT)")
    store = ProductivityStore(path)
    with sqlite3.connect(store.path) as conn:
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        obsolete = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'obsolete'"
        ).fetchone()
    assert version == str(ProductivityStore.SCHEMA_VERSION)
    assert obsolete is None


def test_idle_parser_reads_nanoseconds():
    assert read_macos_idle_seconds('"HIDIdleTime" = 1500000000') == 1.5
    assert read_macos_idle_seconds("no idle value") is None


def test_presence_rows_are_unique_by_minute_and_report_focus_hours(tmp_path):
    store = ProductivityStore(tmp_path / "productivity.db")
    base = datetime(2026, 7, 14, 8, tzinfo=UTC)
    for minute in range(45):
        store.record_presence(
            base + timedelta(minutes=minute), active=True, idle_seconds=0
        )
    store.record_presence(base, active=False, idle_seconds=600)
    rows = store.load_presence(base.date(), base.date(), tzinfo=UTC)
    summary = presence_summary(rows, tzinfo=UTC)
    assert len(rows) == 45
    assert summary["sample_minutes"] == 45
    assert summary["active_minutes"] == 44
    assert summary["focus_hours"] == 0
    store.record_presence(base, active=True, idle_seconds=0)
    summary = presence_summary(
        store.load_presence(base.date(), base.date(), tzinfo=UTC), tzinfo=UTC
    )
    assert summary["focus_hours"] == 1
    assert summary["first_sampled_at"] == base.isoformat()


def test_presence_pruning_removes_samples_older_than_18_weeks(tmp_path):
    store = ProductivityStore(tmp_path / "productivity.db")
    now = datetime(2026, 7, 14, tzinfo=UTC)
    store.record_presence(now - timedelta(weeks=19), active=True, idle_seconds=0)
    store.record_presence(now - timedelta(weeks=1), active=True, idle_seconds=0)
    assert store.prune_presence(now=now) == 1
    with sqlite3.connect(store.path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM presence").fetchone()[0]
    assert count == 1
