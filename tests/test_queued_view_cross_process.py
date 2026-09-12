"""The queued-message view follows the durable queue file.

The worker process consumes queued input straight in the file. The dashboard
must not keep showing a message the worker already delivered.
"""
import json

import server
import ccc_server.pending_inputs as pending_inputs


def _write_queue(path, resume):
    path.write_text(json.dumps({
        "resume_queue": resume,
        "devin_steers": {},
        "terminal_queue": {},
        "auto_resume_opt_in": {},
    }))


def test_queued_view_drops_a_message_another_process_consumed(monkeypatch, tmp_path):
    sid = "queued-view-cross-process"
    pending_file = tmp_path / "pending-inputs.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    monkeypatch.setattr(server, "_detect_session_engine", lambda _sid: "claude")
    monkeypatch.setattr(pending_inputs, "_queued_view_file_identity", {})
    _write_queue(pending_file, {sid: ["already steered"]})
    server._pending_resume_queue.pop(sid, None)

    shown = [ev["text"] for ev in server._get_queued_events_for_session(sid)]
    assert shown == ["already steered"]

    # The worker delivers it and rewrites the file; this process's memory
    # still holds the old copy.
    _write_queue(pending_file, {})
    assert server._pending_resume_queue.get(sid)

    assert server._get_queued_events_for_session(sid) == []
    server._pending_resume_queue.pop(sid, None)


def test_unchanged_file_is_not_reread(monkeypatch, tmp_path):
    sid = "queued-view-unchanged"
    pending_file = tmp_path / "pending-inputs.json"
    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    monkeypatch.setattr(server, "_detect_session_engine", lambda _sid: "claude")
    monkeypatch.setattr(pending_inputs, "_queued_view_file_identity", {})
    _write_queue(pending_file, {sid: ["waiting"]})
    server._pending_resume_queue.pop(sid, None)

    calls = []
    real = server._refresh_pending_inputs_for_session
    monkeypatch.setattr(server, "_refresh_pending_inputs_for_session",
                        lambda s: calls.append(s) or real(s))
    server._get_queued_events_for_session(sid)
    server._get_queued_events_for_session(sid)
    assert calls == [sid]
    server._pending_resume_queue.pop(sid, None)
