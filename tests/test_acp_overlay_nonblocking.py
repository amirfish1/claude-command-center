"""Sidebar overlays retain missing sessions without waiting on engine startup."""

import threading
import json

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


def test_overlay_reads_only_its_marker_and_observes_replacement(monkeypatch, tmp_path):
    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()
    marker = marker_dir / "new.json"
    marker.write_text(json.dumps({"spawned_via": "ui"}))
    (tmp_path / "outside.json").write_text(json.dumps({"spawned_via": "outside"}))
    monkeypatch.setattr(server, "SPAWN_MARKERS_DIR", marker_dir)
    state = {"attached": True, "status": "active", "cwd": str(tmp_path),
             "updated_at": 1_700_000_000}
    monkeypatch.setattr(server, "_ACP_HARNESSES", {"kimi": {"label": "Kimi"}})
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {
        "kimi": {"new": state, "../outside": state},
    })
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: (set(), set()))
    monkeypatch.setattr(server, "_load_pinned_conversations", lambda: [])
    monkeypatch.setattr(server, "_load_verified_conversations", lambda: [])
    monkeypatch.setattr(server, "_acp_transcript_path", lambda *args: tmp_path / "missing.jsonl")
    monkeypatch.setattr(server, "_acp_transcript_first_prompt", lambda *args: "")

    def fail_global_scan():
        raise AssertionError("overlay must read only the missing session marker")

    monkeypatch.setattr(server, "_load_spawn_markers", fail_global_scan)
    decode = server._decode_spawn_marker_file
    reads = []

    def read_marker(target):
        reads.append(target)
        return decode(target)

    monkeypatch.setattr(server, "_decode_spawn_marker_file", read_marker)

    def attributions():
        return {row["session_id"]: row["spawned_via"]
                for row in server._archive_overlay_acp_sessions([])}

    assert attributions() == {"new": "ui", "../outside": "unknown"}
    assert reads == [marker]
    replacement = marker_dir / "replacement.json"
    replacement.write_text(json.dumps({"spawned_via": "cli"}))
    replacement.replace(marker)
    assert attributions()["new"] == "cli"
    marker.write_text("{malformed")
    assert attributions()["new"] == "unknown"
    marker.unlink()
    assert attributions()["new"] == "unknown"
