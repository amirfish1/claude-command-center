"""Focused latency contracts for a freshly spawned Claude session."""

import importlib
import time
from unittest import mock


server = importlib.import_module("server")
morning_launch = importlib.import_module("ccc_server.morning_launch")


def test_capability_probe_finds_session_id_and_partial_messages(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    completed = mock.Mock(
        returncode=0,
        stdout="--session-id\n--include-partial-messages\n",
        stderr="",
    )

    with mock.patch.object(server.subprocess, "run", return_value=completed) as run:
        got = server._probe_claude_spawn_capabilities(str(fake))

    assert got == {
        "ready": True,
        "bin": str(fake),
        "session_id": True,
        "partial_messages": True,
    }
    run.assert_called_once_with(
        [str(fake), "--help"],
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_capability_probe_fails_closed_when_help_fails(tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    with mock.patch.object(
        server.subprocess,
        "run",
        side_effect=server.subprocess.TimeoutExpired([str(fake), "--help"], 5),
    ):
        got = server._probe_claude_spawn_capabilities(str(fake))

    assert got == {
        "ready": True,
        "bin": str(fake),
        "session_id": False,
        "partial_messages": False,
    }


def test_timeline_can_share_the_browser_request_origin(monkeypatch):
    monkeypatch.setattr(server, "_spawn_timeline_sync", lambda: None)
    server._SPAWN_TIMELINE.clear()
    origin_ms = time.time() * 1000 - 125

    server._spawn_timeline_start(
        "claude-timeline",
        t0_epoch_ms=origin_ms,
        engine="claude",
    )
    server._spawn_timeline_mark("claude-timeline", "process_started")

    entry = server._spawn_timeline_get("claude-timeline")
    assert entry["engine"] == "claude"
    assert 100 <= entry["marks"]["process_started"] <= 500


def test_warm_spawn_preassigns_id_and_never_waits_for_log(tmp_path, monkeypatch):
    class Proc:
        pid = 43210
        stdin = None

        def poll(self):
            return None

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    registry = mock.Mock()
    visibility = mock.Mock()
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(
        server,
        "_spawn_repo_context",
        lambda **k: {"cwd": str(tmp_path), "repo_path": str(tmp_path)},
    )
    monkeypatch.setattr(server, "repo_log_dir", lambda _repo: log_dir)
    monkeypatch.setattr(
        server,
        "_resolve_claude_bin",
        lambda: {"available": True, "bin": "/fake/claude"},
    )
    monkeypatch.setattr(
        server,
        "_claude_spawn_capabilities",
        lambda _bin: {
            "ready": True,
            "bin": "/fake/claude",
            "session_id": True,
            "partial_messages": True,
        },
    )
    monkeypatch.setattr(server, "_make_stdin_fifo", lambda _path: ("/tmp/test.stdin", 91))
    monkeypatch.setattr(server, "_open_fifo_writer", lambda _path: 92)
    monkeypatch.setattr(server, "_close_fd_quiet", lambda _fd: None)
    monkeypatch.setattr(server, "_write_stream_json_user_message", lambda *a, **k: True)
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **kw: Proc())
    monkeypatch.setattr(server, "_record_spawn_to_registry", registry)
    monkeypatch.setattr(server, "_schedule_claude_desktop_visibility_retry", visibility)
    monkeypatch.setattr(
        server,
        "_wait_for_spawn_session_id",
        mock.Mock(side_effect=AssertionError("fast path waited for log identity")),
    )
    monkeypatch.setattr(server, "_spawn_timeline_save", lambda: None)
    monkeypatch.setattr(server, "_spawn_timeline_sync", lambda: None)
    original_spawns = list(server._spawned_sessions)
    original_timeline = dict(server._SPAWN_TIMELINE)
    server._spawned_sessions.clear()
    server._SPAWN_TIMELINE.clear()
    try:
        result = server.spawn_session(
            "Reply READY",
            cwd=str(tmp_path),
            timeline_t0_epoch_ms=time.time() * 1000,
        )
        entry = server._spawned_sessions[-1]
        marks = dict(server._SPAWN_TIMELINE[result["session_id"]]["marks"])
    finally:
        for row in server._spawned_sessions:
            fh = row.get("log_fh")
            if fh:
                fh.close()
        server._spawned_sessions[:] = original_spawns
        server._SPAWN_TIMELINE.clear()
        server._SPAWN_TIMELINE.update(original_timeline)

    assert result["ok"] is True
    assert result["session_id_pending"] is False
    assert result["session_id"] == entry["session_id"]
    command = entry["command"]
    assert command[command.index("--session-id") + 1] == result["session_id"]
    assert "--include-partial-messages" in command
    registry.assert_called_once()
    assert registry.call_args.kwargs["session_id"] == result["session_id"]
    visibility.assert_called_once_with(result["session_id"], spawn_entry=entry)
    assert {"process_started", "initial_prompt_written", "spawn_response_sent"} <= set(marks)


def test_uncapable_spawn_retains_log_identity_fallback(tmp_path, monkeypatch):
    """Older Claude CLIs keep the current discovery behavior."""
    monkeypatch.setattr(
        server,
        "_claude_spawn_capabilities",
        lambda _bin: {
            "ready": True,
            "bin": "/fake/claude",
            "session_id": False,
            "partial_messages": False,
        },
        raising=False,
    )
    assert server._claude_spawn_capabilities("/fake/claude")["session_id"] is False


def test_partial_claude_text_deltas_are_forwarded_without_final_duplicate():
    normalizer = morning_launch._SpawnEventNormalizer()
    events = [
        {
            "type": "stream_event",
            "event": {
                "type": "message_start",
                "message": {"id": "msg-fast", "role": "assistant"},
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "RE"},
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "ADY"},
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "msg-fast",
                "content": [{"type": "text", "text": "READY"}],
            },
        },
    ]

    got = [normalizer.normalize(event) for event in events]

    assert got[0] is None
    assert got[1] == {
        "type": "assistant_block",
        "message_id": "msg-fast",
        "blocks": [{"type": "text", "text": "RE"}],
        "partial": True,
    }
    assert got[2]["blocks"] == [{"type": "text", "text": "ADY"}]
    assert got[3] is None


def test_completed_claude_events_still_work_without_partial_streaming():
    normalizer = morning_launch._SpawnEventNormalizer()

    got = normalizer.normalize({
        "type": "assistant",
        "message": {
            "id": "msg-legacy",
            "content": [{"type": "text", "text": "READY"}],
        },
    })

    assert got == {
        "type": "assistant_block",
        "message_id": "msg-legacy",
        "blocks": [{"type": "text", "text": "READY"}],
    }


def test_completed_claude_event_preserves_tools_after_partial_text():
    normalizer = morning_launch._SpawnEventNormalizer()
    normalizer.normalize({
        "type": "stream_event",
        "event": {"type": "message_start", "message": {"id": "msg-tools"}},
    })
    normalizer.normalize({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Checking"},
        },
    })

    got = normalizer.normalize({
        "type": "assistant",
        "message": {
            "id": "msg-tools",
            "content": [
                {"type": "text", "text": "Checking"},
                {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "README.md"}},
            ],
        },
    })

    assert got["message_id"] == "msg-tools"
    assert got["blocks"] == [{
        "type": "tool_use",
        "name": "Read",
        "id": "tool-1",
        "summary": "README.md",
    }]
