"""Live-activity exposes native and ACP Devin turns under the sidebar ID."""

import json
import os
import time

import pytest

import server
from ccc_server import antigravity_stream, devin


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


@pytest.mark.parametrize("phase, expected", [
    ("tool_running", "working"),
    ("between_tools", "working"),
    ("approval", "waiting"),
    ("stopped", "idle"),
    ("stale_activity", "idle"),
    ("exited", None),
    ("acp_idle", "idle"),
    ("acp_running", "working"),
    ("acp_approval", "waiting"),
])
def test_devin_activity_joins_sidebar_row(tmp_path, monkeypatch, phase, expected):
    raw_id = "sample-session"
    sid = f"devincli-{raw_id}"
    markers = tmp_path / "markers"
    locks = tmp_path / "locks"
    markers.mkdir()
    locks.mkdir()
    now = time.time()
    lock = locks / f"{raw_id}.lock"
    lock.write_text(str(os.getpid() if phase != "exited" else 999999999))
    timestamp = now - server._WORKING_GAP_WINDOW - 1 if phase == "stale_activity" else now
    status = "waiting" if phase == "stopped" else "active"
    (markers / f"{raw_id}.json").write_text(json.dumps({
        "session_id": raw_id, "status": status, "timestamp": timestamp, "tool": "edit",
    }))
    if phase in {"tool_running", "approval", "stopped"}:
        (markers / f"{raw_id}_in_flight.json").write_text(json.dumps({
            "started_at": now - 1, "tool": "exec",
        }))
    if phase == "approval":
        (markers / f"{raw_id}_needs_approval.json").write_text(json.dumps({
            "notification_type": "permission_prompt", "message": "Approval requested",
        }))

    monkeypatch.setattr(server, "SIDECAR_STATE_DIR", markers)
    monkeypatch.setattr(devin, "_devin_cli_locks_dirs", lambda: [locks])
    monkeypatch.setattr(devin, "_DEVIN_CLI_LIVE_CACHE", {})
    monkeypatch.setattr(server, "_session_live_cache", {})
    monkeypatch.setattr(server, "_load_session_registry", dict)
    monkeypatch.setattr(server, "_live_engine_session_ids", set)
    monkeypatch.setattr(server, "_hermes_active_session_ids", set)
    monkeypatch.setattr(server, "_acp_attached_session_ids", set)
    monkeypatch.setattr(antigravity_stream, "acp_session_ids", set)
    monkeypatch.setattr(server, "_codex_pool_candidate_sids", set)
    monkeypatch.setattr(server, "_spawn_registry_entry_for_session", lambda _: None)
    monkeypatch.setattr(server, "_acp_load_state", lambda _: None)
    monkeypatch.setattr(server, "_ACP_TURN_TRACK", {})
    acp_status = None
    snap = None
    if phase.startswith("acp_"):
        acp_status = "idle" if phase == "acp_idle" else "running"
        snap = {"status": "idle" if acp_status == "idle" else "active",
                "loaded": True, "updated_at": now,
                "pending_permissions": phase == "acp_approval"}
    monkeypatch.setattr(server, "_acp_session_snapshot", lambda *_: snap)

    activity = server._build_live_sessions_activity_uncached()
    assert raw_id not in activity
    if expected is None:
        assert sid not in activity
    else:
        row = activity[sid]
        assert row["is_live"] and row["state"] == expected
        assert row["sidecar_in_flight"] is (phase in {"tool_running", "approval"})
        assert row["needs_approval"] is (phase in {"approval", "acp_approval"})
        assert row["acp_status"] == acp_status
        if phase == "stopped" or snap:
            assert row["sidecar_ts"] == now
