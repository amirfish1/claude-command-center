"""Sidebar overlays retain missing sessions without waiting on engine startup."""

import threading

import server


def test_busy_registry_keeps_the_same_overlay_without_waiting(monkeypatch, tmp_path):
    class ImmediateLock:
        def __init__(self):
            self.lock = threading.Lock()

        def acquire(self, blocking=True, timeout=-1):
            assert blocking is False, "sidebar discovery must not wait for startup"
            return self.lock.acquire(blocking=False)

        def release(self):
            self.lock.release()

    lock = ImmediateLock()
    monkeypatch.setattr(server, "_ACP_LOCK", lock)
    monkeypatch.setattr(server, "_ACP_HARNESSES", {
        "kimi": {"label": "Kimi"}, "glm": {"label": "GLM"},
    })
    monkeypatch.setattr(server, "_acp_harness_enabled", lambda harness: True)
    state = {"attached": True, "status": "active", "cwd": str(tmp_path),
             "updated_at": 1_700_000_000, "title": "New session"}
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {
        "kimi": {"existing": state, "new": state},
        "glm": {"closed": dict(state, status="closed"),
                "detached": dict(state, attached=False)},
    })
    monkeypatch.setattr(server, "_load_spawn_markers", lambda: {})
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: (set(), set()))
    monkeypatch.setattr(server, "_load_pinned_conversations", lambda: [])
    monkeypatch.setattr(server, "_load_verified_conversations", lambda: [])
    monkeypatch.setattr(server, "_acp_transcript_path", lambda *args: tmp_path / "missing.jsonl")
    monkeypatch.setattr(server, "_acp_transcript_first_prompt", lambda *args: "")
    rows = [{"session_id": "existing"}]
    expected = server._archive_overlay_acp_sessions(rows)
    assert [row["session_id"] for row in expected] == ["new"]
    lock.lock.acquire()
    try:
        assert server._archive_overlay_acp_sessions(rows) == expected
    finally:
        lock.release()
