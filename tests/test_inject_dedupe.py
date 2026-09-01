"""Duplicate suppression on the inject path.

The incident (2026-09-01): wt's delegate adapter waits 5s for
/api/inject-input, a Codex steer of a 2.5k-char gate comment takes longer, so
wt recorded the send as failed, parked it in its outbox and retried with
backoff -- while CCC delivered every attempt. One comment landed in a single
Codex session five times over nine minutes, burning a full 220k-token turn
each time. Separately, an un-keyed `ccc inject --steer` relayed a watchtower
line the terminal queue had drained 9s earlier into a Kimi session.

Two properties make the window actually hold, and both are pinned here:

  * A suppression answers ``ok: True``. A rejection would send wt's adapter
    looking for another transport and leave the message in its outbox to
    retry -- the text did land, so success is both true and the only reply
    that ends the retry chain.
  * The terminal-queue drain is exempt. It *completes* an attempt that is
    already in the window, so suppressing it would strand the queued message.
"""
import importlib

import pytest

server = importlib.import_module("server")


@pytest.fixture
def window(monkeypatch):
    """Isolated window -- the store is process-wide under pytest."""
    monkeypatch.delenv("CCC_INJECT_DEDUPE_WINDOW_S", raising=False)
    monkeypatch.setattr(server, "_INJECT_DEDUPE_WINDOW_S", 300)
    monkeypatch.setattr(server, "_inject_dedupe_recent", {})
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    return 300


@pytest.fixture
def router(monkeypatch):
    """Stub router that records every call it was actually asked to make."""
    calls = []

    def _router(session_id, text, **kwargs):
        calls.append({"session_id": session_id, "text": text, **kwargs})
        return {"ok": True, "via": "fifo"}

    monkeypatch.setattr(server, "_inject_text_into_session_router", _router)
    return calls


# ── The window holds ─────────────────────────────────────────────────────────

def test_identical_unkeyed_inject_is_suppressed(window, router):
    """The retry shape: same session, same text, no idempotency key."""
    first = server._inject_text_into_session("s1", "gate PASS", source="wt")
    second = server._inject_text_into_session("s1", "gate PASS", source="wt")

    assert len(router) == 1, "the duplicate reached the router"
    assert first.get("deduped") is None
    assert second["deduped"] is True
    assert second["code"] == "duplicate_suppressed"
    # ok:True is what stops wt re-parking this in its outbox.
    assert second["ok"] is True
    assert second["landed"] == "already_delivered"


def test_queued_delivery_is_remembered_too(window, router, monkeypatch):
    """CCC owns a queued message; the drain will deliver it exactly once."""
    monkeypatch.setattr(
        server, "_inject_text_into_session_router",
        lambda *a, **k: {"ok": True, "queued": True, "via": "terminal-queued"},
    )
    server._inject_text_into_session("s1", "gate PASS", source="wt")

    monkeypatch.setattr(
        server, "_inject_text_into_session_router",
        lambda *a, **k: router.append({}) or {"ok": True, "via": "fifo"},
    )
    result = server._inject_text_into_session("s1", "gate PASS", source="wt")

    assert result["deduped"] is True
    assert router == []


def test_suppression_is_per_session_and_per_text(window, router):
    server._inject_text_into_session("s1", "gate PASS", source="wt")
    server._inject_text_into_session("s2", "gate PASS", source="wt")
    server._inject_text_into_session("s1", "different text", source="wt")

    assert len(router) == 3


def test_normalisation_matches_the_circuit_breaker(window):
    """Same keying as _inject_budget_text_key: case- and whitespace-folded."""
    server._inject_dedupe_record("s1", "Gate  PASS", now=1000)
    assert server._inject_duplicate_check(
        "s1", "gate pass", now=1001,
    ) is not None


# ── The exemptions ───────────────────────────────────────────────────────────

def test_terminal_queue_drain_is_never_suppressed(window, router):
    """Suppressing the drain would strand the queued message forever."""
    server._inject_text_into_session("s1", "gate PASS", source="wt")
    result = server._inject_text_into_session(
        "s1", "gate PASS", source="terminal-queue-watcher",
        _from_terminal_queue=True,
    )

    assert len(router) == 2
    assert result.get("deduped") is None


def test_idempotency_key_bypasses_suppression(window, router):
    """A keyed caller owns its own replay semantics -- e.g. the Codex steer
    path deliberately re-sends the same text under a fresh key."""
    server._inject_text_into_session("s1", "gate PASS", source="api")
    server._inject_text_into_session(
        "s1", "gate PASS", source="api", idempotency_key="inject:abc",
    )

    assert len(router) == 2


def test_allow_duplicate_sends_again_without_reaching_the_router_signature(
    window, router,
):
    server._inject_text_into_session("s1", "gate PASS", source="api")
    result = server._inject_text_into_session(
        "s1", "gate PASS", source="api", allow_duplicate=True,
    )

    assert len(router) == 2
    assert result.get("deduped") is None
    # The flag is consumed by the wrapper; the router has no such parameter.
    assert "allow_duplicate" not in router[1]


def test_failed_delivery_is_not_remembered(window, monkeypatch):
    """A real retry after a real failure must still land."""
    calls = []

    def _failing(session_id, text, **kwargs):
        calls.append(text)
        return {"ok": False, "error": "no live channel"}

    monkeypatch.setattr(server, "_inject_text_into_session_router", _failing)
    server._inject_text_into_session("s1", "gate PASS", source="wt")
    server._inject_text_into_session("s1", "gate PASS", source="wt")

    assert len(calls) == 2


# ── The window itself ────────────────────────────────────────────────────────

def test_every_suppression_refreshes_the_window(window):
    """wt's outbox backs off (+70s, +89s, +155s, +265s observed), so a window
    measured only from the first delivery would let the late retries through.
    """
    server._inject_dedupe_record("s1", "gate PASS", now=1000)
    assert server._inject_duplicate_check("s1", "gate PASS", now=1250) is not None
    assert server._inject_duplicate_check("s1", "gate PASS", now=1500) is not None
    assert server._inject_duplicate_check("s1", "gate PASS", now=1700) is not None


def test_message_sends_again_once_the_window_rolls_off(window):
    server._inject_dedupe_record("s1", "gate PASS", now=1000)
    assert server._inject_duplicate_check("s1", "gate PASS", now=1400) is None


def test_zero_window_disables_suppression(window, router, monkeypatch):
    monkeypatch.setenv("CCC_INJECT_DEDUPE_WINDOW_S", "0")
    server._inject_text_into_session("s1", "gate PASS", source="wt")
    server._inject_text_into_session("s1", "gate PASS", source="wt")

    assert len(router) == 2


def test_env_overrides_the_default_window(window, monkeypatch):
    monkeypatch.setenv("CCC_INJECT_DEDUPE_WINDOW_S", "30")
    server._inject_dedupe_record("s1", "gate PASS", now=1000)
    assert server._inject_duplicate_check("s1", "gate PASS", now=1020) is not None
    assert server._inject_duplicate_check("s1", "gate PASS", now=1200) is None


def test_expired_entries_are_pruned(window):
    server._inject_dedupe_record("s1", "gate PASS", now=1000)
    server._inject_dedupe_record("s2", "other", now=1400)

    assert "s1" not in server._inject_dedupe_recent
    assert "s2" in server._inject_dedupe_recent
