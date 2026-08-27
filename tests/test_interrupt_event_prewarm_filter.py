"""Prewarm reservations get SIGTERM'd on expiry while "mid-turn" (idle, but
Claude still writes "[Request interrupted by user]" to their own throwaway
transcript). That's routine prewarm-pool churn, not a stuck user session --
_emit_interrupt_event must not surface it as a "Request interrupt ... session
is now stuck" toast. See server.py's _emit_interrupt_event for the guard.
"""
import server


def test_emit_interrupt_event_skips_prewarm_placeholder_name(monkeypatch):
    monkeypatch.setattr(server, "_INTERRUPT_EVENTS_ENABLED", True)
    monkeypatch.setattr(server, "_SEEN_INTERRUPTS", set())
    calls = []
    monkeypatch.setattr(server, "_record_kill_event", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_resume_ledger_append", lambda *a, **k: None)

    server._emit_interrupt_event(
        "sid-1", "uuid-1", source="sse-stream", agent_name="prewarm-ccc-ui",
    )

    assert calls == []


def test_emit_interrupt_event_skips_ccc_prewarm_agent_name(monkeypatch):
    monkeypatch.setattr(server, "_INTERRUPT_EVENTS_ENABLED", True)
    monkeypatch.setattr(server, "_SEEN_INTERRUPTS", set())
    calls = []
    monkeypatch.setattr(server, "_record_kill_event", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_resume_ledger_append", lambda *a, **k: None)

    server._emit_interrupt_event(
        "sid-2", "uuid-2", source="transcript-scan", agent_name="ccc-prewarm",
    )

    assert calls == []


def test_emit_interrupt_event_still_fires_for_real_session(monkeypatch):
    monkeypatch.setattr(server, "_INTERRUPT_EVENTS_ENABLED", True)
    monkeypatch.setattr(server, "_SEEN_INTERRUPTS", set())
    calls = []
    monkeypatch.setattr(server, "_record_kill_event", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)
    monkeypatch.setattr(server, "_resume_ledger_append", lambda *a, **k: None)

    server._emit_interrupt_event(
        "sid-3", "uuid-3", source="transcript-scan", agent_name="fix-login-bug",
    )

    assert len(calls) == 1
