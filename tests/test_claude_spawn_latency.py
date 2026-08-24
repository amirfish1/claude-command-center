"""Focused latency contracts for a freshly spawned Claude session."""

import importlib
import time
from unittest import mock


server = importlib.import_module("server")
morning_launch = importlib.import_module("ccc_server.morning_launch")


def test_legacy_worker_retries_claude_spawn_without_prewarm_keyword(monkeypatch):
    calls = []
    replies = [
        {
            "ok": False,
            "code": "engine_dispatch_failed",
            "error": "spawn_session() got an unexpected keyword argument 'prewarm_id'",
        },
        {"ok": True, "pid": 42, "session_id": "cold-session"},
    ]

    def routed(engine, operation, args, **kwargs):
        calls.append((engine, operation, args, kwargs))
        return replies.pop(0)

    monkeypatch.setattr(server, "_control_plane_engine_call", routed)

    result = server.spawn_session(
        "Reply READY", name="retry cold", cwd="/tmp", repo_path="/tmp",
        prewarm_id="warm-1",
    )

    assert result["ok"] is True
    assert result["prewarm_fallback"] is True
    assert calls[0][2]["prewarm_id"] == "warm-1"
    assert "prewarm_id" not in calls[1][2]
    assert calls[0][3]["idempotency_key"] != calls[1][3]["idempotency_key"]


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


def test_prewarmed_model_metadata_is_scheduled_after_first_response(monkeypatch):
    created = []

    class Timer:
        def __init__(self, delay, target, args):
            self.delay = delay
            self.target = target
            self.args = args
            self.daemon = False
            self.started = False
            created.append(self)

        def start(self):
            self.started = True

    monkeypatch.setattr(server.threading, "Timer", Timer)

    server._schedule_session_model_update(
        "session-fast", "claude-sonnet-5", False,
    )

    timer = created[0]
    assert timer.delay >= 10
    assert timer.target is server._set_session_model
    assert timer.args == ("session-fast", "claude-sonnet-5", False, None)
    assert timer.daemon is True
    assert timer.started is True


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


def test_matching_claude_prewarm_is_claimed_once(monkeypatch):
    class Proc:
        def poll(self):
            return None

    entry = {
        "prewarm_id": "warm-1",
        "created_at_epoch": time.time(),
        "cwd": "/tmp/project",
        "model": "claude-sonnet-5",
        "proc": Proc(),
    }
    monkeypatch.setattr(server, "_prune_claude_prewarms", lambda: None)
    monkeypatch.setattr(server, "_CLAUDE_PREWARMS", {})
    server._CLAUDE_PREWARMS["warm-1"] = entry

    claimed = server._take_claude_prewarm(
        "warm-1", "/tmp/project", "claude-sonnet-5",
    )
    claimed_again = server._take_claude_prewarm(
        "warm-1", "/tmp/project", "claude-sonnet-5",
    )

    assert claimed is entry
    assert claimed_again is None


def test_mismatched_claude_prewarm_is_not_consumed(monkeypatch):
    class Proc:
        def poll(self):
            return None

    entry = {
        "prewarm_id": "warm-2",
        "created_at_epoch": time.time(),
        "cwd": "/tmp/project",
        "model": "claude-sonnet-5",
        "proc": Proc(),
    }
    monkeypatch.setattr(server, "_prune_claude_prewarms", lambda: None)
    monkeypatch.setattr(server, "_CLAUDE_PREWARMS", {})
    server._CLAUDE_PREWARMS["warm-2"] = entry

    claimed = server._take_claude_prewarm(
        "warm-2", "/tmp/other", "claude-sonnet-5",
    )

    assert claimed is None
    assert server._CLAUDE_PREWARMS["warm-2"] is entry


def test_matching_claude_prewarm_requires_the_native_session_name(monkeypatch):
    class Proc:
        def poll(self):
            return None

    entry = {
        "prewarm_id": "warm-name",
        "created_at_epoch": time.time(),
        "cwd": "/tmp/project",
        "repo_path": "/tmp/project",
        "model": "claude-sonnet-5",
        "name": "real-session",
        "proc": Proc(),
    }
    monkeypatch.setattr(server, "_prune_claude_prewarms", lambda: None)
    monkeypatch.setattr(server, "_CLAUDE_PREWARMS", {"warm-name": entry})

    claimed = server._take_claude_prewarm_for_request(
        "warm-name",
        cwd="/tmp/project",
        repo_path="/tmp/project",
        model="claude-sonnet-5",
        name="different-session",
    )

    assert claimed is None
    assert server._CLAUDE_PREWARMS["warm-name"] is entry


def test_validated_prewarm_request_claims_without_repo_reresolution(tmp_path, monkeypatch):
    class Proc:
        def poll(self):
            return None

    entry = {
        "prewarm_id": "warm-fast",
        "created_at_epoch": time.time(),
        "cwd": str(tmp_path),
        "repo_path": str(tmp_path),
        "model": "claude-sonnet-5",
        "proc": Proc(),
    }
    monkeypatch.setattr(server, "_prune_claude_prewarms", lambda: None)
    monkeypatch.setattr(server, "_CLAUDE_PREWARMS", {"warm-fast": entry})

    claimed = server._take_claude_prewarm_for_request(
        "warm-fast",
        cwd=str(tmp_path),
        repo_path=str(tmp_path),
        model="claude-sonnet-5",
    )

    assert claimed is entry
    assert server._CLAUDE_PREWARMS == {}


def test_prewarm_request_with_different_folder_is_not_consumed(tmp_path, monkeypatch):
    other = tmp_path / "other"
    other.mkdir()

    class Proc:
        def poll(self):
            return None

    entry = {
        "prewarm_id": "warm-path",
        "created_at_epoch": time.time(),
        "cwd": str(tmp_path),
        "repo_path": str(tmp_path),
        "model": "claude-sonnet-5",
        "proc": Proc(),
    }
    monkeypatch.setattr(server, "_prune_claude_prewarms", lambda: None)
    monkeypatch.setattr(server, "_CLAUDE_PREWARMS", {"warm-path": entry})

    claimed = server._take_claude_prewarm_for_request(
        "warm-path",
        cwd=str(other),
        repo_path=str(other),
        model="claude-sonnet-5",
    )

    assert claimed is None
    assert server._CLAUDE_PREWARMS["warm-path"] is entry


def test_failed_prewarm_log_adoption_rebuilds_a_cold_command(tmp_path, monkeypatch):
    class Proc:
        pid = 43211

        def poll(self):
            return None

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    warm_log = log_dir / ".ccc-prewarm-warm-3.log"
    warm_log.write_text("", encoding="utf-8")
    warm_entry = {
        "prewarm_id": "warm-3",
        "created_at_epoch": time.time(),
        "cwd": str(tmp_path),
        "repo_path": str(tmp_path),
        "model": "claude-sonnet-5",
        "proc": Proc(),
        "log": str(warm_log),
        "session_id": "warm-session-id",
        "partial_messages": True,
        "command": [
            "/fake/claude", "--name", "ccc-prewarm",
            "--session-id", "warm-session-id",
        ],
    }
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(
        server,
        "_spawn_repo_context",
        lambda **k: {"cwd": str(tmp_path), "repo_path": str(tmp_path)},
    )
    monkeypatch.setattr(server, "repo_log_dir", lambda _repo: log_dir)
    claim = mock.Mock(return_value=warm_entry)
    monkeypatch.setattr(server, "_take_claude_prewarm_for_request", claim)
    monkeypatch.setattr(server, "_discard_claude_prewarm", mock.Mock())
    real_replace = server.os.replace

    def fail_only_for_prewarm(source, target):
        if str(source) == str(warm_log):
            raise OSError("rename failed")
        return real_replace(source, target)

    monkeypatch.setattr(server.os, "replace", fail_only_for_prewarm)
    monkeypatch.setattr(server, "_set_session_model", lambda *a, **k: None)
    monkeypatch.setattr(
        server,
        "_resolve_claude_bin",
        lambda: {"available": True, "bin": "/fake/claude"},
    )
    monkeypatch.setattr(
        server,
        "_claude_spawn_capabilities",
        lambda _bin: {"session_id": True, "partial_messages": True},
    )
    monkeypatch.setattr(server, "_make_stdin_fifo", lambda _path: ("/tmp/test.stdin", 91))
    monkeypatch.setattr(server, "_open_fifo_writer", lambda _path: 92)
    monkeypatch.setattr(server, "_close_fd_quiet", lambda _fd: None)
    monkeypatch.setattr(server, "_write_stream_json_user_message", lambda *a, **k: True)
    monkeypatch.setattr(server.subprocess, "Popen", lambda cmd, **kw: Proc())
    monkeypatch.setattr(server, "_record_spawn_to_registry", lambda **kw: None)
    monkeypatch.setattr(server, "_schedule_claude_desktop_visibility_retry", lambda *a, **k: None)
    monkeypatch.setattr(server, "_spawn_timeline_save", lambda: None)
    monkeypatch.setattr(server, "_spawn_timeline_sync", lambda: None)
    original_spawns = list(server._spawned_sessions)
    original_timeline = dict(server._SPAWN_TIMELINE)
    server._spawned_sessions.clear()
    server._SPAWN_TIMELINE.clear()
    try:
        result = server.spawn_session(
            "Reply READY",
            name="real-session",
            cwd=str(tmp_path),
            model="claude-sonnet-5",
            prewarm_id="warm-3",
        )
        command = server._spawned_sessions[-1]["command"]
    finally:
        for row in server._spawned_sessions:
            fh = row.get("log_fh")
            if fh:
                fh.close()
        server._spawned_sessions[:] = original_spawns
        server._SPAWN_TIMELINE.clear()
        server._SPAWN_TIMELINE.update(original_timeline)

    assert result["ok"] is True
    assert result["prewarmed"] is False
    assert command[command.index("--name") + 1] == "real-session"
    assert command[command.index("--session-id") + 1] == result["session_id"]
    assert "ccc-prewarm" not in command
    assert "warm-session-id" not in command
    assert claim.call_args.kwargs["name"] == "real-session"


def test_internal_claude_prewarm_is_hidden_only_until_first_prompt():
    registry = {"warm-session": {"prewarm": True}}

    assert server._is_unclaimed_claude_prewarm(
        None, {}, "warm-session", registry,
    ) is True
    assert server._is_unclaimed_claude_prewarm(
        "Build the feature", {}, "warm-session", registry,
    ) is False
    assert server._is_unclaimed_claude_prewarm(
        None, {}, "normal-session", registry,
    ) is False


def test_orphaned_registered_prewarm_is_reaped_on_owner_restart(monkeypatch):
    entries = [
        {
            "pid": 1234,
            "session_id": "warm-session",
            "engine": "claude",
            "prewarm": True,
            "log": "/tmp/warm.log",
            "fifo": "/tmp/warm.fifo",
        },
        {"pid": 5678, "session_id": "real-session", "engine": "claude"},
    ]
    saved = []
    killed = []
    unlinked = []

    def mutate_registry(mutator):
        rows = list(entries)
        if mutator(rows):
            saved.append(rows)

    monkeypatch.setattr(server, "_mutate_spawn_registry", mutate_registry)
    monkeypatch.setattr(server.os, "killpg", lambda pid, sig: killed.append(pid))
    monkeypatch.setattr(server, "_unlink_quiet", lambda path: unlinked.append(path))

    got = server._reap_orphaned_claude_prewarms()

    assert got == 1
    assert killed == [1234]
    assert unlinked == ["/tmp/warm.fifo", "/tmp/warm.log"]
    assert saved == [[entries[1]]]


def test_prewarm_pool_is_bounded_and_supersedes_same_tab(monkeypatch):
    old_same_tab = {
        "prewarm_id": "old-tab",
        "client_id": "tab-a",
        "created_at_epoch": 1,
    }
    other_tab = {
        "prewarm_id": "other-tab",
        "client_id": "tab-b",
        "created_at_epoch": 2,
    }
    replacement = {
        "prewarm_id": "new-tab",
        "client_id": "tab-a",
        "created_at_epoch": 3,
    }
    discarded = []
    monkeypatch.setattr(
        server,
        "_CLAUDE_PREWARMS",
        {"old-tab": old_same_tab, "other-tab": other_tab},
    )
    monkeypatch.setattr(server, "_CLAUDE_PREWARM_MAX", 2)
    monkeypatch.setattr(server, "_discard_claude_prewarm", discarded.append)

    server._store_claude_prewarm(replacement)

    assert server._CLAUDE_PREWARMS == {
        "other-tab": other_tab,
        "new-tab": replacement,
    }
    assert discarded == [old_same_tab]


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


def test_partial_claude_deltas_keep_interleaved_subagent_routing():
    normalizer = morning_launch._SpawnEventNormalizer()
    parent = "tool-parent"
    child = "tool-child"
    events = [
        {
            "type": "stream_event",
            "parent_tool_use_id": parent,
            "event": {"type": "message_start", "message": {"id": "msg-parent"}},
        },
        {
            "type": "stream_event",
            "parent_tool_use_id": child,
            "event": {"type": "message_start", "message": {"id": "msg-child"}},
        },
        {
            "type": "stream_event",
            "parent_tool_use_id": parent,
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "parent"},
            },
        },
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "parent_tool_use_id": child,
                "delta": {"type": "text_delta", "text": "child"},
            },
        },
    ]

    got = [normalizer.normalize(event) for event in events]

    assert got[2]["message_id"] == "msg-parent"
    assert got[2]["parent_tool_use_id"] == parent
    assert got[3]["message_id"] == "msg-child"
    assert got[3]["parent_tool_use_id"] == child


def test_partial_claude_attach_mid_message_suppresses_final_duplicate():
    normalizer = morning_launch._SpawnEventNormalizer()
    partial = normalizer.normalize({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "READY"},
        },
    })
    final = normalizer.normalize({
        "type": "assistant",
        "message": {
            "id": "msg-arrived-late",
            "content": [{"type": "text", "text": "READY"}],
        },
    })

    assert partial["message_id"] == "claude-stream"
    assert final is None


def test_preassigned_session_id_resolves_spawn_log_without_scanning(tmp_path, monkeypatch):
    log_path = tmp_path / "spawn.jsonl"
    log_path.write_text("", encoding="utf-8")
    entry = {
        "session_id": "claude-preassigned",
        "engine": "claude",
        "log": str(log_path),
        "started": "2026-07-31T00:00:00Z",
    }
    monkeypatch.setattr(server, "_spawned_sessions", [entry])
    monkeypatch.setattr(server, "_poll_spawn_entry", lambda _entry: None)
    monkeypatch.setattr(server, "_load_spawn_registry", lambda: [])
    monkeypatch.setattr(
        morning_launch,
        "_log_session_ids",
        mock.Mock(side_effect=AssertionError("fast path scanned the log")),
    )

    got = morning_launch._resolve_spawn_log_for_session("claude-preassigned")

    assert got == (str(log_path), True)
