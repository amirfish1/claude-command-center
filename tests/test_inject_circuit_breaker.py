"""Inject circuit breaker — the blast-radius cap under CCC-863.

The incident: a leaked process running pre-fix code injected the literal
"continue" into one live Codex session 118 times in 114 minutes and burned a
weekly quota overnight. Two things make the cap actually hold, and both are
what these tests pin:

  * The counter is a FILE, not process memory. The injector was a *different
    process*, which is why CCC's own activity.log recorded 1 of the 118 pokes.
  * A trip is TERMINAL, not a delivery failure. The terminal-queue watcher
    requeues `ok:false` at the front of the queue and retries every tick, so a
    plain failure would have turned the rate limit into a hot loop.
"""
import importlib
import inspect
import json

import pytest

server = importlib.import_module("server")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Isolated ledger — the module default is process-wide under pytest."""
    path = tmp_path / "inject-budget.json"
    monkeypatch.setattr(server, "INJECT_BUDGET_FILE", path)
    monkeypatch.setattr(
        server, "_inject_blocked_memo", {"mtime": None, "value": []}
    )
    return path


def _poke(sid="s1", text="continue", source="usage-limit-watcher", now=None):
    return server._inject_budget_check(sid, text, source, now=now)


# ── The cap holds ────────────────────────────────────────────────────────────

def test_identical_text_trips_at_the_repeat_limit(ledger):
    """The incident shape: same text, same session, over and over."""
    limit = server._INJECT_REPEAT_LIMIT
    for i in range(limit):
        assert _poke(source="api", now=1000 + i) is None, f"poke {i} refused early"
    blocked = _poke(source="api", now=1000 + limit)
    assert blocked is not None
    assert blocked["ok"] is False
    assert blocked["blocked"] is True
    assert blocked["code"] == "inject_rate_limit"
    assert blocked["reason"] == "repeat"


def test_unattended_source_gets_the_tighter_leash(ledger):
    """Nobody is watching a usage-limit-watcher poke land."""
    limit = server._INJECT_UNATTENDED_HOURLY_LIMIT
    assert limit < server._INJECT_REPEAT_LIMIT
    for i in range(limit):
        # Distinct text each time, so only the unattended cap can fire.
        assert _poke(text=f"poke {i}", now=1000 + i) is None
    blocked = _poke(text="one more", now=1000 + limit)
    assert blocked is not None
    assert blocked["reason"] == "unattended_hourly"


def test_human_queued_text_is_not_rate_limited_as_unattended(ledger):
    """`terminal-queue-watcher` carries text a person typed and queued.

    Ten of those in a minute is someone working, not a runaway, so it must
    only ever be subject to the identical-text cap.
    """
    assert "terminal-queue-watcher" not in server._INJECT_UNATTENDED_SOURCES
    for i in range(server._INJECT_UNATTENDED_HOURLY_LIMIT * 2):
        result = _poke(
            text=f"distinct message {i}",
            source="terminal-queue-watcher",
            now=1000 + i,
        )
        assert result is None, f"human queued message {i} was refused"


def test_window_rolls_off_so_a_block_self_heals(ledger):
    """No permanent lockout: the cap is a rolling window, not a latch."""
    limit = server._INJECT_REPEAT_LIMIT
    for i in range(limit):
        _poke(source="api", now=1000 + i)
    assert _poke(source="api", now=1000 + limit) is not None
    later = 1000 + limit + server._INJECT_BUDGET_WINDOW_S + 1
    assert _poke(source="api", now=later) is None


def test_blocked_attempts_keep_the_window_full(ledger):
    """A runaway that keeps hammering stays blocked until it actually stops.

    Blocked attempts are recorded like any other, so retrying does not let the
    window drain out from under the injector.
    """
    limit = server._INJECT_REPEAT_LIMIT
    for i in range(limit):
        _poke(source="api", now=1000 + i)
    for i in range(20):
        assert _poke(source="api", now=1000 + limit + i) is not None
    events = json.loads(ledger.read_text())["sessions"]["s1"]["events"]
    assert len(events) == limit + 20


def test_counter_is_shared_state_on_disk_not_process_memory(ledger):
    """The incident's injector was a different process.

    Pre-seeding the ledger the way a sibling process would must be enough to
    trip this process's next attempt.
    """
    key = server._inject_budget_text_key("continue")
    ledger.write_text(json.dumps({
        "sessions": {
            "s1": {"events": [[1000, key, 1]] * server._INJECT_REPEAT_LIMIT}
        }
    }))
    blocked = _poke(now=1001)
    assert blocked is not None
    assert blocked["reason"] == "repeat"


def test_sessions_are_metered_independently(ledger):
    """A fleet ping to 20 sessions is not a runaway on any one of them."""
    for i in range(server._INJECT_REPEAT_LIMIT + 5):
        assert _poke(sid=f"session-{i}", source="fleet-ping", now=1000 + i) is None


# ── Fail open ────────────────────────────────────────────────────────────────

def test_corrupt_ledger_fails_open(ledger):
    """A broken counter must never wedge every message in the fleet."""
    ledger.write_text("{ not json at all")
    assert _poke(now=1000) is None


def test_unwritable_ledger_fails_open(tmp_path, monkeypatch):
    monkeypatch.setattr(
        server, "INJECT_BUDGET_FILE", tmp_path / "no" / "such" / "dir" / "b.json"
    )
    monkeypatch.setattr(server, "Path", server.Path)
    real_mkdir = server.Path.mkdir

    def boom(self, *a, **kw):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(server.Path, "mkdir", boom)
    try:
        assert server._inject_budget_check("s1", "continue", "api") is None
    finally:
        monkeypatch.setattr(server.Path, "mkdir", real_mkdir)


def test_missing_session_id_is_not_metered(ledger):
    assert server._inject_budget_check("", "continue", "api") is None


# ── The held bucket ──────────────────────────────────────────────────────────

def test_trip_lands_in_the_held_bucket_for_the_human(ledger):
    limit = server._INJECT_REPEAT_LIMIT
    for i in range(limit + 1):
        _poke(source="api", now=1000 + i)
    held = server._inject_blocked_recent_entries()
    assert len(held) == 1
    assert held[0]["session_id"] == "s1"
    assert held[0]["reason"] == "repeat"
    assert "continue" in held[0]["preview"]


def test_held_bucket_is_capped(ledger):
    for i in range(server._INJECT_HELD_MAX + 30):
        _poke(source="api", now=1000 + i)
    held = json.loads(ledger.read_text())["held"]
    assert len(held) == server._INJECT_HELD_MAX


def test_health_payload_carries_trips(ledger):
    """The dashboard poll already reads this; a runaway must show up in it."""
    assert "inject_blocked" in server.build_ccc_health()


# ── The hot-loop trap (the regression that matters) ──────────────────────────

def test_watcher_drops_blocked_results_before_the_requeue_branch():
    """`ok:false` from the terminal-queue watcher is requeued at the FRONT of
    the queue and retried every tick. If a circuit-breaker trip reaches that
    branch, the rate limit becomes a permanent 5s-tick hot loop on the same
    blocked text. The blocked check MUST come first.
    """
    src = inspect.getsource(server._start_resume_queue_watcher)
    blocked_at = src.find('result.get("blocked")')
    requeue_at = src.find("_requeue_terminal_input_front(sid, text)")
    assert blocked_at != -1, "terminal-queue watcher lost its blocked check"
    assert requeue_at != -1
    assert blocked_at < requeue_at, (
        "a circuit-breaker trip now falls through to the requeue branch — "
        "that converts a rate limit into a hot loop (see CCC-863)"
    )


def test_engine_bridge_recover_does_not_requeue_blocked():
    src = inspect.getsource(server._recover_engine_bridge)
    assert 'retry.get("blocked")' in src
