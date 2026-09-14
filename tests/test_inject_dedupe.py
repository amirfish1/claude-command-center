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
import threading
from unittest import mock

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


def test_new_idempotency_key_sends_again(window, router):
    """A fresh key is a distinct composer action, even for identical text."""
    server._inject_text_into_session("s1", "gate PASS", source="api")
    server._inject_text_into_session(
        "s1", "gate PASS", source="api", idempotency_key="inject:abc",
    )

    assert len(router) == 2


def test_matching_idempotency_key_delivers_only_once_during_a_race(
    window, monkeypatch,
):
    """Dashboard and worker replays of one composer send share its key."""
    router_entered = threading.Event()
    release_router = threading.Event()
    calls = []

    def _router(session_id, text, **kwargs):
        calls.append((session_id, text))
        router_entered.set()
        release_router.wait(timeout=1)
        return {"ok": True, "via": "spawn-fifo"}

    replay_waiting = threading.Event()
    original_acquire = server._inject_dedupe_acquire

    def _acquire(*args, **kwargs):
        acquired = original_acquire(*args, **kwargs)
        if acquired[1] is not None:
            replay_waiting.set()
        return acquired

    monkeypatch.setattr(server, "_inject_dedupe_acquire", _acquire)
    monkeypatch.setattr(server, "_inject_text_into_session_router", _router)
    first_result = []
    first = threading.Thread(
        target=lambda: first_result.append(server._inject_text_into_session(
            "s1", "composer send", source="composer",
            idempotency_key="inject:composer-send",
        )),
    )
    first.start()
    assert router_entered.wait(timeout=1)

    second_result = []
    second = threading.Thread(
        target=lambda: second_result.append(server._inject_text_into_session(
            "s1", "composer send", source="composer",
            idempotency_key="inject:composer-send",
        )),
    )
    second.start()
    assert replay_waiting.wait(timeout=1)
    release_router.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert len(calls) == 1
    assert first_result[0]["ok"] is True
    assert second_result[0]["deduped"] is True


def test_keyed_replay_times_out_while_owner_is_stuck(window, monkeypatch):
    """A stuck owner must not retain every same-key request thread forever."""
    router_entered = threading.Event()
    release_router = threading.Event()

    def _router(session_id, text, **kwargs):
        router_entered.set()
        release_router.wait(timeout=1)
        return {"ok": True, "via": "spawn-fifo"}

    monkeypatch.setattr(server, "_INJECT_DEDUPE_WAIT_S", 0.05, raising=False)
    monkeypatch.setattr(server, "_inject_text_into_session_router", _router)
    first = threading.Thread(target=lambda: server._inject_text_into_session(
        "s1", "composer send", source="composer",
        idempotency_key="inject:composer-send",
    ))
    first.start()
    assert router_entered.wait(timeout=1)

    result = []
    second = threading.Thread(target=lambda: result.append(
        server._inject_text_into_session(
            "s1", "composer send", source="composer",
            idempotency_key="inject:composer-send",
        )
    ))
    second.start()
    second.join(timeout=0.5)
    release_router.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert result[0]["ok"] is False
    assert result[0]["code"] == "delivery_in_progress"


def test_waiting_keyed_replay_retries_after_owner_failure(window, monkeypatch):
    """A failed owner releases its reservation instead of losing the replay."""
    first_router_entered = threading.Event()
    release_first = threading.Event()
    second_router_entered = threading.Event()
    calls = []

    def _router(session_id, text, **kwargs):
        calls.append((session_id, text))
        if len(calls) == 1:
            first_router_entered.set()
            release_first.wait(timeout=1)
            return {"ok": False, "error": "fifo unavailable"}
        second_router_entered.set()
        return {"ok": True, "via": "spawn-fifo"}

    monkeypatch.setattr(server, "_inject_text_into_session_router", _router)
    first_result = []
    second_result = []
    first = threading.Thread(
        target=lambda: first_result.append(server._inject_text_into_session(
            "s1", "composer send", source="composer",
            idempotency_key="inject:composer-send",
        )),
    )
    first.start()
    assert first_router_entered.wait(timeout=1)
    second = threading.Thread(
        target=lambda: second_result.append(server._inject_text_into_session(
            "s1", "composer send", source="composer",
            idempotency_key="inject:composer-send",
        )),
    )
    second.start()

    assert not second_router_entered.wait(timeout=0.1)
    release_first.set()
    first.join(timeout=1)
    second.join(timeout=1)

    assert len(calls) == 2
    assert first_result[0]["ok"] is False
    assert second_result[0]["ok"] is True


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


def test_failed_inject_logs_rejection_not_success(window, monkeypatch):
    """A rejected delegate request must not inflate successful inject counts."""
    monkeypatch.setattr(
        server, "_inject_text_into_session_router",
        lambda *args, **kwargs: {
            "ok": False,
            "code": "dead_target",
            "error": "delegate rejected the message",
        },
    )

    with mock.patch.object(server, "_log_activity") as log_activity:
        result = server._inject_text_into_session(
            "s1", "answer to a closed ticket", source="wt",
        )

    assert result["ok"] is False
    assert [call.args[1] for call in log_activity.call_args_list] == ["INJECT_REJECT"]
    assert "code=dead_target" in log_activity.call_args.args[2]


def test_successful_inject_keeps_success_activity_log(window, monkeypatch):
    monkeypatch.setattr(
        server, "_inject_text_into_session_router",
        lambda *args, **kwargs: {"ok": True, "via": "codex-steer"},
    )

    with mock.patch.object(server, "_log_activity") as log_activity:
        result = server._inject_text_into_session("s1", "working answer", source="wt")

    assert result["ok"] is True
    assert [call.args[1] for call in log_activity.call_args_list] == ["INJECT"]
    assert "via=codex-steer" in log_activity.call_args.args[2]


def test_worker_and_uds_results_do_not_duplicate_downstream_logs(window, monkeypatch):
    """Those delivery owners already record their accepted result."""
    for via in ("worker", "uds"):
        monkeypatch.setattr(
            server, "_inject_text_into_session_router",
            lambda *args, **kwargs: {"ok": True, "via": via},
        )
        with mock.patch.object(server, "_log_activity") as log_activity:
            result = server._inject_text_into_session("s1", f"through {via}", source="wt")

        assert result["ok"] is True
        log_activity.assert_not_called()


def test_worker_owned_inject_does_not_log_a_dashboard_attempt(monkeypatch):
    """The worker is the sole delivery owner, so it emits the one INJECT row."""
    with mock.patch.object(server, "find_session_cwd", return_value=None), \
         mock.patch.object(server, "session_live_status", return_value={}), \
         mock.patch.object(server, "_is_real_tty", return_value=False), \
         mock.patch.object(server, "_is_codex_session", return_value=False), \
         mock.patch.object(server, "_is_kimi_session", return_value=False), \
         mock.patch.object(server, "_session_acp_harness", return_value=""), \
         mock.patch.object(server, "_is_cursor_session", return_value=False), \
         mock.patch.object(server, "_is_hermes_session", return_value=False), \
         mock.patch.object(server, "_is_opencode_session", return_value=False), \
         mock.patch.object(server, "_is_devin_cli_session", return_value=False), \
         mock.patch.object(server, "_is_gemini_session", return_value=False), \
         mock.patch.object(server, "_is_antigravity_session", return_value=False), \
         mock.patch.object(
             server, "_control_plane_engine_call",
             return_value={"ok": True, "via": "worker"},
         ), \
         mock.patch.object(server, "_log_activity") as log_activity:
        result = server._inject_text_into_session_router(
            "sid", "body", idempotency_key="inject:key",
        )

    assert result["via"] == "worker"
    log_activity.assert_not_called()


def test_worker_owned_transport_result_logs_only_in_the_worker(window, monkeypatch):
    """The dashboard must not re-log the worker's concrete FIFO result."""
    with mock.patch.object(server, "find_session_cwd", return_value=None), \
         mock.patch.object(server, "session_live_status", return_value={}), \
         mock.patch.object(server, "_is_real_tty", return_value=False), \
         mock.patch.object(server, "_is_codex_session", return_value=False), \
         mock.patch.object(server, "_is_kimi_session", return_value=False), \
         mock.patch.object(server, "_session_acp_harness", return_value=""), \
         mock.patch.object(server, "_is_cursor_session", return_value=False), \
         mock.patch.object(server, "_is_hermes_session", return_value=False), \
         mock.patch.object(server, "_is_opencode_session", return_value=False), \
         mock.patch.object(server, "_is_devin_cli_session", return_value=False), \
         mock.patch.object(server, "_is_gemini_session", return_value=False), \
         mock.patch.object(server, "_is_antigravity_session", return_value=False), \
         mock.patch.object(
             server, "_control_plane_engine_call",
             return_value={"ok": True, "via": "spawn-fifo"},
         ), \
         mock.patch.object(server, "_log_activity") as log_activity:
        result = server._inject_text_into_session(
            "sid", "body", idempotency_key="inject:key",
        )

    assert result["via"] == "spawn-fifo"
    log_activity.assert_not_called()


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
