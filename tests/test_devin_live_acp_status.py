"""CCC-1203: live-activity exposes a Devin session's ACP turn state."""

import server


def _setup(monkeypatch, *, lock_live, loaded, status):
    # The worker owns the `devin acp` conn, so the turn state comes from its
    # snapshot, never from this process's (stale) registry copy.
    monkeypatch.setattr(server, "_devin_cli_session_live", lambda raw: lock_live)
    monkeypatch.setattr(server, "_acp_load_state", lambda harness: None)
    snap = {"status": status, "loaded": loaded} if status else None
    monkeypatch.setattr(server, "_acp_session_snapshot", lambda harness, sid: snap)
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {"devin": {}})


def test_dormant_idle_session_reports_idle(monkeypatch):
    _setup(monkeypatch, lock_live=False, loaded=False, status="idle")
    assert server._devin_live_acp_status("raw-1") == "idle"


def test_unknown_to_registry_reports_idle(monkeypatch):
    _setup(monkeypatch, lock_live=False, loaded=False, status=None)
    assert server._devin_live_acp_status("raw-1") == "idle"


def test_active_acp_turn_reports_running(monkeypatch):
    _setup(monkeypatch, lock_live=True, loaded=True, status="active")
    assert server._devin_live_acp_status("raw-1") == "running"


def test_headless_lock_holder_is_unknown(monkeypatch):
    # A `devin -p` run (or another ACP host) holds the lock: the registry
    # can't see its turn, so don't claim idle.
    _setup(monkeypatch, lock_live=True, loaded=False, status="idle")
    assert server._devin_live_acp_status("raw-1") is None


def test_acp_status_is_a_live_activity_field():
    assert "acp_status" in server._LIVE_ACTIVITY_FIELD_KEYS
