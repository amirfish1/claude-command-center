"""Claude cache reads must not depend on unrelated Codex worker state."""
import sys
from unittest.mock import Mock

import pytest


@pytest.fixture
def overlay(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["server.py"])
    import server
    sid = "example-session"
    transcript = tmp_path / (sid + ".jsonl")
    transcript.write_text('{"type":"user"}\n')
    monkeypatch.setattr(server, "_CONV_PATH_CACHE", {sid: str(transcript)})
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_THREAD_STATE", {})
    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(server, "_pending_terminal_input_queue", {})
    monkeypatch.setattr(server, "_detect_session_engine", lambda _: "claude")
    monkeypatch.setattr(server, "_conv_parse_jsonl_mtime", lambda _: (1, 22))
    monkeypatch.setattr(server, "_CONV_BYTES_CACHE", server._CONV_BYTES_CACHE.__class__(
        max_bytes=1024 * 1024, max_entries=16))
    capture = Mock(return_value=())
    resolved = Mock(return_value={})
    monkeypatch.setattr(server, "_codex_capture_fingerprint", capture)
    monkeypatch.setattr(server, "_codex_thread_state_resolved", resolved)
    return server, sid, capture, resolved


def test_claude_cache_survives_unavailable_codex_worker_and_tracks_both_queues(overlay):
    server, sid, _, resolved = overlay
    resolved.side_effect = OSError("unavailable")
    server._conv_response_bytes_put(sid, 0, b"transcript", None)
    assert server._conv_response_bytes_get(sid, 0)["raw"] == b"transcript"
    for queue in (server._pending_resume_queue, server._pending_terminal_input_queue):
        queue[sid] = ["queued input"]
        assert server._conv_response_bytes_get(sid, 0) is None
        queue.clear()
        assert server._conv_response_bytes_get(sid, 0)["raw"] == b"transcript"
    resolved.assert_not_called()


@pytest.mark.parametrize("source", ["unknown", "native", "codex", "local-state", "capture"])
def test_other_sources_retain_resolved_worker_state_and_fail_closed(overlay, monkeypatch, source):
    server, sid, capture, resolved = overlay
    if source == "unknown":
        server._CONV_PATH_CACHE.clear()
    elif source == "native":
        server._CONV_PATH_CACHE[sid] = "/example/rollout.jsonl"
    elif source == "codex":
        monkeypatch.setattr(server, "_detect_session_engine", lambda _: "codex")
    elif source == "local-state":
        server._CODEX_APP_SERVER_THREAD_STATE[sid] = {"active_item": "running"}
    else:
        capture.return_value = (("capture.log", 1, 10),)
    server._conv_response_bytes_put(sid, 0, b"old", None)
    assert server._conv_response_bytes_get(sid, 0)["raw"] == b"old"
    resolved.return_value = {"recent_items": [{"text": "new output"}]}
    assert server._conv_response_bytes_get(sid, 0) is None
    resolved.return_value = None
    assert server._conv_overlay_fingerprint(sid) is None
    resolved.side_effect = OSError("unavailable")
    assert server._conv_overlay_fingerprint(sid) is None


def test_discovered_capture_invalidates_previously_cached_claude_body(overlay):
    server, sid, capture, resolved = overlay
    server._conv_response_bytes_put(sid, 0, b"old", None)
    capture.return_value = (("capture.log", 1, 10),)
    assert server._conv_response_bytes_get(sid, 0) is None
    server._conv_response_bytes_put(sid, 0, b"new", None)
    capture.return_value = (("capture.log", 2, 20),)
    assert server._conv_response_bytes_get(sid, 0) is None
    assert resolved.call_count == 3
