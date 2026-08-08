"""Regression tests for interrupt event deduplication.

Tests the 9-change design from the 4-round critique process:
  (a) seed parsing from the resume ledger
  (b) guard-off writes nothing AND does not mark seen
  (c) emit-once dedup across repeated calls
  (d) cache-hit emission + stale-ts toast skip
  (e) schema-16 payload dropped on load
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# Ensure the repo root is on sys.path so `import server` works
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))


@pytest.fixture(autouse=True)
def _isolate_ledger(monkeypatch, tmp_path):
    """Redirect the resume ledger to a temp file for each test."""
    import server
    ledger = tmp_path / "resume-ledger.jsonl"
    backup = tmp_path / "resume-ledger.1.jsonl"
    monkeypatch.setattr(server, "_RESUME_LEDGER_FILE", ledger)
    monkeypatch.setattr(server, "_RESUME_LEDGER_BACKUP", backup)
    # Clear dedup state between tests
    server._SEEN_INTERRUPTS.clear()
    server._INTERRUPT_EVENTS_ENABLED = False
    if hasattr(server, "_KILL_EVENTS"):
        server._KILL_EVENTS.clear()
    yield
    server._SEEN_INTERRUPTS.clear()
    server._INTERRUPT_EVENTS_ENABLED = False


# ── (a) test_seed_seen_interrupts_from_ledger ──────────────────────────────

def test_seed_seen_interrupts_from_ledger():
    """Seed extracts only sid:uuid pairs from interrupt lines."""
    import server

    ledger = server._RESUME_LEDGER_FILE
    ledger.write_text("\n".join([
        json.dumps({"event": "spawn", "sid": "s1", "ts": 1000}),
        json.dumps({"event": "interrupt", "sid": "s1", "uuid": "u1", "ts": 1001}),
        json.dumps({"event": "retire", "sid": "s2", "ts": 1002}),
        json.dumps({"event": "interrupt", "sid": "s2", "uuid": "u2", "ts": 1003}),
        json.dumps({"event": "exit", "sid": "s1", "ts": 1004}),
        json.dumps({"event": "interrupt", "sid": "s3", "uuid": "u3", "ts": 1005}),
    ]) + "\n")

    server._SEEN_INTERRUPTS.clear()
    server._seed_seen_interrupts_from_ledger()

    assert "s1:u1" in server._SEEN_INTERRUPTS
    assert "s2:u2" in server._SEEN_INTERRUPTS
    assert "s3:u3" in server._SEEN_INTERRUPTS
    # Non-interrupt events don't contribute entries
    assert len(server._SEEN_INTERRUPTS) == 3


def test_seed_handles_missing_file():
    """Seed with no ledger file → no crash, no entries."""
    import server
    server._SEEN_INTERRUPTS.clear()
    # _RESUME_LEDGER_FILE is a temp path that doesn't exist yet
    server._seed_seen_interrupts_from_ledger()
    assert len(server._SEEN_INTERRUPTS) == 0


def test_seed_handles_corrupt_lines():
    """Seed skips unparseable lines without crashing."""
    import server

    ledger = server._RESUME_LEDGER_FILE
    ledger.write_text("\n".join([
        "this is not json",
        json.dumps({"event": "interrupt", "sid": "s1", "uuid": "u1"}),
        "{broken json",
        json.dumps({"event": "interrupt", "sid": "s2", "uuid": "u2"}),
    ]) + "\n")

    server._SEEN_INTERRUPTS.clear()
    server._seed_seen_interrupts_from_ledger()

    assert "s1:u1" in server._SEEN_INTERRUPTS
    assert "s2:u2" in server._SEEN_INTERRUPTS
    assert len(server._SEEN_INTERRUPTS) == 2


def test_seed_reads_backup_file():
    """Seed reads the backup file too (rotation recovery)."""
    import server

    backup = server._RESUME_LEDGER_BACKUP
    backup.write_text("\n".join([
        json.dumps({"event": "interrupt", "sid": "old", "uuid": "old-uuid"}),
    ]) + "\n")

    server._SEEN_INTERRUPTS.clear()
    server._seed_seen_interrupts_from_ledger()

    assert "old:old-uuid" in server._SEEN_INTERRUPTS


# ── (b) test_interrupt_events_disabled_writes_nothing ──────────────────────

def test_interrupt_events_disabled_writes_nothing_and_doesnt_mark_seen():
    """When _INTERRUPT_EVENTS_ENABLED is False, no sinks fire and
    _SEEN_INTERRUPTS is NOT modified (guard-off must not mark seen)."""
    import server

    server._INTERRUPT_EVENTS_ENABLED = False
    server._SEEN_INTERRUPTS.clear()

    server._emit_interrupt_event(
        "test-sid", "test-uuid",
        source="test",
        agent_name="test-agent",
    )

    # No ledger entry written
    assert not server._RESUME_LEDGER_FILE.exists() or \
           server._RESUME_LEDGER_FILE.read_text().strip() == ""
    # No kill event in the ring buffer
    assert len(server._KILL_EVENTS) == 0
    # CRITICAL: _SEEN_INTERRUPTS must NOT contain the entry —
    # marking-seen-while-disabled would silently swallow the later
    # real emission when the dashboard eventually enables.
    assert "test-sid:test-uuid" not in server._SEEN_INTERRUPTS
    assert len(server._SEEN_INTERRUPTS) == 0


# ── (c) test_emit_interrupt_event_fires_once ───────────────────────────────

def test_emit_interrupt_event_fires_once():
    """Calling _emit_interrupt_event twice with the same sid:uuid
    fires the sinks exactly once."""
    import server

    server._INTERRUPT_EVENTS_ENABLED = True
    server._SEEN_INTERRUPTS.clear()
    server._KILL_EVENTS.clear()

    # Use a fresh event timestamp (now) so the 48h cutoff doesn't skip it
    now_ts = time.time()

    server._emit_interrupt_event(
        "dup-sid", "dup-uuid",
        source="test",
        agent_name="test-agent",
        event_ts=now_ts,
    )
    server._emit_interrupt_event(
        "dup-sid", "dup-uuid",
        source="test",
        agent_name="test-agent",
        event_ts=now_ts,
    )

    # Exactly one kill event (toast)
    assert len(server._KILL_EVENTS) == 1
    # Exactly one ledger entry
    ledger_text = server._RESUME_LEDGER_FILE.read_text().strip()
    assert ledger_text.count('"event": "interrupt"') == 1


# ── (d) test_emit_on_cache_hit + stale-ts toast skip ───────────────────────

def test_emit_interrupts_from_meta_fires_on_cache_hit():
    """_emit_interrupts_from_meta emits interrupts stored in cached meta."""
    import server

    server._INTERRUPT_EVENTS_ENABLED = True
    server._SEEN_INTERRUPTS.clear()
    server._KILL_EVENTS.clear()

    now_ts = time.time()
    meta = {
        "interrupted": [
            {"uuid": "cached-uuid-1", "ts": now_ts},
            {"uuid": "cached-uuid-2", "ts": now_ts},
        ],
        "agent_name": "cached-agent",
    }

    server._emit_interrupts_from_meta(meta, "cached-sid")

    assert len(server._KILL_EVENTS) == 2
    ledger_text = server._RESUME_LEDGER_FILE.read_text().strip()
    assert ledger_text.count('"event": "interrupt"') == 2


def test_emit_interrupts_from_meta_stale_ts_skips_all_sinks():
    """A stale event_ts (>48h) is marked seen but emits to NO sinks."""
    import server

    server._INTERRUPT_EVENTS_ENABLED = True
    server._SEEN_INTERRUPTS.clear()
    server._KILL_EVENTS.clear()

    stale_ts = time.time() - (49 * 3600)  # 49 hours ago
    meta = {
        "interrupted": [{"uuid": "stale-uuid", "ts": stale_ts}],
        "agent_name": "stale-agent",
    }

    server._emit_interrupts_from_meta(meta, "stale-sid")

    # No kill event (toast skipped — too old)
    assert len(server._KILL_EVENTS) == 0
    # No ledger entry (all sinks skipped for stale events)
    assert not server._RESUME_LEDGER_FILE.exists() or \
           server._RESUME_LEDGER_FILE.read_text().strip() == ""
    # But the interrupt IS marked seen (no re-check on every cache hit)
    assert "stale-sid:stale-uuid" in server._SEEN_INTERRUPTS


# ── (e) test_schema_16_payload_dropped ─────────────────────────────────────

def test_schema_16_payload_dropped_on_load():
    """A cache file with schema_version=16 is dropped because 16 is not
    in _CONV_META_COMPAT_SCHEMA_VERSIONS (which is now {17})."""
    import server

    # Write a fake cache file with schema 16
    cache_file = server._CONV_META_CACHE_FILE
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({
        "schema_version": 16,
        "entries": {
            "/fake/path.jsonl": {
                "cache_key": [123456, 100],
                "custom_title": "should be dropped",
            }
        }
    }))

    # Clear the in-memory cache
    server._conv_meta_cache.clear()

    # Load — should drop the schema-16 payload
    server._load_conv_meta_cache()

    # The cache should be empty (payload dropped, not loaded)
    assert len(server._conv_meta_cache) == 0

    # Cleanup
    try:
        cache_file.unlink()
    except OSError:
        pass
