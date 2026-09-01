"""Cross-process regression coverage for Kimi remote-busy retries."""

from __future__ import annotations

import json

import server


def test_worker_remote_busy_handoff_preserves_dashboard_queue_exactly_once(
    tmp_path,
    monkeypatch,
):
    pending_file = tmp_path / "pending-inputs.json"
    handoff_dir = tmp_path / "pending-input-handoffs"
    sid = "session-kimi-worker-busy"
    other_sid = "session-unrelated-dashboard-queue"

    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    monkeypatch.setattr(server, "PENDING_INPUT_HANDOFF_DIR", handoff_dir)
    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(
        server,
        "_pending_terminal_input_queue",
        {other_sid: ["leave this queued"]},
    )
    monkeypatch.setattr(server, "_pending_terminal_handoff_ids", {})
    assert server._save_pending_inputs({other_sid}) is True

    # The persistent engine worker has process-private queue dictionaries. A
    # Kimi remote-busy race must hand the retry back without saving that empty
    # worker snapshot over the dashboard's durable queue.
    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(server, "_pending_terminal_input_queue", {})
    monkeypatch.setenv("CCC_WORKER_PROCESS", "1")
    monkeypatch.setattr(server, "_ACP_SESSION_STATE", {"kimi": {}})
    with server._ACP_LOCK:
        state = server._acp_session("kimi", sid, create=True)
        state["status"] = "active"
        state["active_turn"] = {
            "req_id": 17,
            "msg_id": "m17",
            "text": "",
            "thought": "",
            "tools": {},
            "prompt": "/goal keep the queue empty",
            "from_queue": False,
        }

    server._acp_finalize_turn(
        "kimi",
        sid,
        {
            "error": {
                "message": (
                    "Invalid request: Cannot launch a new turn while "
                    "another turn (ID 17) is active"
                ),
            },
        },
        {"req_id": 17, "is_active": True},
    )

    durable_before_ingest = json.loads(pending_file.read_text())
    durable_rows = durable_before_ingest["terminal_queue"][other_sid]
    assert durable_rows == ["leave this queued"]
    assert durable_before_ingest["pending_entry_ids"]["terminal_queue"][other_sid][0]
    handoff_files = list(handoff_dir.glob("*.json"))
    assert len(handoff_files) == 1
    # Simulate the dashboard watcher loading its own durable snapshot and
    # ingesting the worker-owned handoff.
    monkeypatch.delenv("CCC_WORKER_PROCESS")
    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(server, "_pending_terminal_input_queue", {})
    monkeypatch.setattr(server, "_pending_terminal_handoff_ids", {})
    server._load_pending_inputs()
    assert server._ingest_pending_input_handoffs() == 1
    assert server._pending_terminal_input_queue == {
        other_sid: ["leave this queued"],
        sid: ["/goal keep the queue empty"],
    }

    # Handoff-backed rows stay out of the dashboard snapshot because the
    # unique inbox file remains authoritative until proven delivery.
    assert server._save_pending_inputs({other_sid, sid}) is True
    durable_after_ingest = json.loads(pending_file.read_text())
    durable_rows = durable_after_ingest["terminal_queue"][other_sid]
    assert durable_rows == ["leave this queued"]
    assert durable_after_ingest["pending_entry_ids"]["terminal_queue"][other_sid][0]
    assert handoff_files[0].exists()

    # Model a watcher restart after ingestion but before delivery. The new
    # process loads the dashboard queue, then discovers the authoritative
    # handoff exactly once.
    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(server, "_pending_terminal_input_queue", {})
    monkeypatch.setattr(server, "_pending_terminal_handoff_ids", {})
    server._load_pending_inputs()
    assert server._ingest_pending_input_handoffs() in (0, 1)
    assert server._ingest_pending_input_handoffs() == 0
    assert server._pending_terminal_input_queue[sid] == [
        "/goal keep the queue empty",
    ]
    queued_retry = server._pending_terminal_input_queue[sid][0]
    assert server._complete_pending_input_handoff(queued_retry) is True
    assert not handoff_files[0].exists()


def test_worker_handoff_restores_popped_retry_to_fifo_front(
    tmp_path,
    monkeypatch,
):
    pending_file = tmp_path / "pending-inputs.json"
    handoff_dir = tmp_path / "pending-input-handoffs"
    sid = "session-kimi-worker-front"

    monkeypatch.setattr(server, "PENDING_INPUTS_FILE", pending_file)
    monkeypatch.setattr(server, "PENDING_INPUT_HANDOFF_DIR", handoff_dir)
    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(
        server,
        "_pending_terminal_input_queue",
        {sid: ["later prompt"]},
    )
    monkeypatch.setattr(server, "_pending_terminal_handoff_ids", {})
    assert server._save_pending_inputs({sid}) is True

    monkeypatch.setattr(server, "_pending_resume_queue", {})
    monkeypatch.setattr(server, "_pending_terminal_input_queue", {})
    assert server._write_pending_input_handoff(
        sid,
        "popped retry",
        front=True,
    )

    server._load_pending_inputs()
    assert server._ingest_pending_input_handoffs() in (0, 1)
    assert server._pending_terminal_input_queue[sid] == [
        "popped retry",
        "later prompt",
    ]
