"""GH #71 — stale Claude headless detection / retirement.

Unit-level coverage of the staleness machinery without spawning real
`claude` processes: we stage a fake transcript (.jsonl) and a fake headless
stdout log on disk, build a spawn-entry dict shaped like the real ones, and
drive the helper functions directly.

The hard contracts under test:
  * A lone headless that only ITSELF advances the transcript is never flagged
    stale (no-regression: the no-concurrency inject path must be unchanged).
  * A transcript advanced by an EXTERNAL writer (no new headless result) is
    flagged stale.
  * A busy headless (active tool child) is never retired.
  * The use-time inject path retires + respawns on stale, and is untouched
    when there is no concurrency.
"""
import json
import multiprocessing
import os
import sys
import threading
from unittest import mock

import pytest


@pytest.fixture()
def server_mod():
    sys.modules.pop("server", None)
    import server
    return server


def _write_jsonl(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")


def _event(uuid):
    return {"type": "assistant", "uuid": uuid, "sessionId": "SID", "entrypoint": "sdk-cli"}


def _result_lines(n):
    return "".join(
        json.dumps({"type": "result", "subtype": "success", "session_id": "SID", "num_turns": i + 1}) + "\n"
        for i in range(n)
    )


def _stage(server_mod, tmp_path, transcript_events, hl_result_count):
    """Stage a transcript + headless log; return (sid, entry)."""
    sid = "11111111-2222-3333-4444-555555555555"
    projects = tmp_path / "projects"
    enc = "-fake-cwd"
    transcript = projects / enc / (sid + ".jsonl")
    _write_jsonl(transcript, transcript_events)
    log = tmp_path / "hl.log"
    log.write_text(_result_lines(hl_result_count))
    server_mod.PROJECTS_ROOT = projects
    entry = {
        "pid": 999999,
        "engine": "claude",
        "resumed_sid": sid,
        "log": str(log),
        "fifo": None,
        "stdin_fd": None,
    }
    return sid, entry, transcript, log


def _hold_spawn_registry_lock(registry_path, entered, release):
    import server

    server.SPAWNED_PIDS_FILE = server.Path(registry_path)
    with server._spawn_registry_exclusive_lock():
        entered.set()
        release.wait(5)


def test_no_watermark_is_not_stale(server_mod, tmp_path):
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    # No watermark recorded yet → never stale (first use baselines it).
    assert server_mod._headless_spawn_is_stale(entry, sid) is False


def test_lone_headless_own_response_not_stale(server_mod, tmp_path):
    """No-regression: the headless's OWN turn advancing the transcript must
    not be mistaken for an external writer."""
    sid, entry, transcript, log = _stage(server_mod, tmp_path, [_event("a")], 0)
    # CCC injects → record watermark (size/uuid of [a], result_count=0).
    server_mod._update_spawn_transcript_watermark(entry, sid)
    # The headless responds: transcript grows AND its stdout log gains a result.
    _write_jsonl(transcript, [_event("a"), _event("b")])
    log.write_text(_result_lines(1))
    # Tail moved but result_count rose → attributed to the headless → NOT stale.
    assert server_mod._headless_spawn_is_stale(entry, sid) is False
    # And the watermark re-baselined to the new tail.
    assert entry["_transcript_watermark"][2] == 1


def test_external_writer_is_stale(server_mod, tmp_path):
    """A transcript advance with NO new headless result == external writer."""
    sid, entry, transcript, log = _stage(server_mod, tmp_path, [_event("a")], 1)
    log.write_text(_result_lines(1))
    server_mod._update_spawn_transcript_watermark(entry, sid)  # baseline at result_count=1
    # External terminal appends a turn; headless produced NO new result.
    _write_jsonl(transcript, [_event("a"), _event("ext1"), _event("ext2")])
    assert server_mod._headless_spawn_is_stale(entry, sid) is True


def test_uuidless_trailer_write_not_stale(server_mod, tmp_path):
    """Regression (premature-death hunt): a uuid-less trailer write —
    last-prompt / mode / custom-title / queue-operation / agent-name / pr-link
    — grows the transcript file but is NOT a conversation turn. It must NOT be
    flagged stale. Counting raw byte-size growth from these benign metadata
    events caused false-positive staleness that retired warm headless
    processes (28% of one real transcript was uuid-less), forcing a cold
    resume on the next send."""
    sid, entry, transcript, log = _stage(server_mod, tmp_path, [_event("a")], 1)
    log.write_text(_result_lines(1))
    server_mod._update_spawn_transcript_watermark(entry, sid)
    # Benign metadata is appended: file grows, but the last real (uuid'd)
    # event is unchanged and the headless produced no new result.
    _write_jsonl(transcript, [
        _event("a"),
        {"type": "last-prompt", "text": "benign metadata, not a conversation turn"},
        {"type": "mode", "mode": "default"},
    ])
    assert server_mod._headless_spawn_is_stale(entry, sid) is False


def test_no_change_not_stale(server_mod, tmp_path):
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    server_mod._update_spawn_transcript_watermark(entry, sid)
    assert server_mod._headless_spawn_is_stale(entry, sid) is False


def test_headless_turn_in_progress_uses_result_target(server_mod, tmp_path):
    _sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    entry["input_result_target"] = 5

    assert server_mod._headless_turn_in_progress(entry) is True

    log.write_text(_result_lines(5))
    assert server_mod._headless_turn_in_progress(entry) is False


def test_headless_turn_in_progress_ignores_malformed_targets(server_mod, tmp_path):
    _sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 1
    )
    log.write_text(_result_lines(1))

    for value in (-1, "bad", None):
        entry["input_result_target"] = value
        assert server_mod._headless_turn_in_progress(entry) is False


def test_successful_stream_write_persists_command_uuid(
    server_mod, tmp_path, monkeypatch
):
    _sid, entry, _transcript, _log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    registry = tmp_path / "spawned-pids.json"
    registry.write_text(json.dumps([{"pid": entry["pid"], "engine": "claude"}]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    monkeypatch.setattr(server_mod, "_write_via_spawn_fd", lambda *_a, **_k: True)

    assert server_mod._write_stream_json_user_message(entry, "follow up") is True

    assert "input_result_target" not in entry
    assert len(entry["input_command_uuids"]) == 1
    assert isinstance(entry["input_accepted_at"], float)
    saved = json.loads(registry.read_text())
    assert saved == [{
        "pid": entry["pid"],
        "engine": "claude",
        "input_command_uuids": entry["input_command_uuids"],
        "input_accepted_at": entry["input_accepted_at"],
    }]


def test_multiple_stream_writes_track_each_command_uuid(
    server_mod, tmp_path, monkeypatch
):
    _sid, entry, _transcript, _log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    registry = tmp_path / "spawned-pids.json"
    registry.write_text(json.dumps([{"pid": entry["pid"], "engine": "claude"}]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    monkeypatch.setattr(server_mod, "_write_via_spawn_fd", lambda *_a, **_k: True)

    assert server_mod._write_stream_json_user_message(entry, "first") is True
    assert server_mod._write_stream_json_user_message(entry, "second") is True

    # Claude may absorb both messages into one result, but it emits a completed
    # command_lifecycle event for each UUID.
    assert len(entry["input_command_uuids"]) == 2
    assert json.loads(registry.read_text())[0]["input_command_uuids"] == entry[
        "input_command_uuids"
    ]


def test_failed_stream_write_does_not_advance_result_target(
    server_mod, tmp_path, monkeypatch
):
    _sid, entry, _transcript, _log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    entry["input_result_target"] = 5
    registry = tmp_path / "spawned-pids.json"
    registry.write_text(json.dumps([{
        "pid": entry["pid"],
        "engine": "claude",
        "input_result_target": 5,
    }]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    monkeypatch.setattr(server_mod, "_write_via_spawn_fd", lambda *_a, **_k: False)
    monkeypatch.setattr(server_mod, "_write_via_pipe", lambda *_a, **_k: False)

    assert server_mod._write_stream_json_user_message(entry, "lost") is False

    assert entry["input_result_target"] == 5
    assert "input_accepted_at" not in entry
    assert "input_accepted_at" not in json.loads(registry.read_text())[0]


def test_concurrent_stream_writes_serialize_command_uuid_state(
    server_mod, tmp_path, monkeypatch
):
    _sid, entry, _transcript, _log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    registry = tmp_path / "spawned-pids.json"
    registry.write_text(json.dumps([{"pid": entry["pid"], "engine": "claude"}]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    delivered = []

    def accept_line(_entry, line, timeout=0.25):
        delivered.append(json.loads(line))
        return True

    monkeypatch.setattr(server_mod, "_write_via_spawn_fd", accept_line)
    outcomes = []
    threads = [
        threading.Thread(
            target=lambda text=text: outcomes.append(
                server_mod._write_stream_json_user_message(entry, text)
            )
        )
        for text in ("first", "second")
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert outcomes == [True, True]
    assert sorted(
        item["message"]["content"][0]["text"] for item in delivered
    ) == ["first", "second"]
    assert len(entry["input_command_uuids"]) == 2
    assert {item["uuid"] for item in delivered} == set(entry["input_command_uuids"])
    assert json.loads(registry.read_text())[0]["input_command_uuids"] == entry[
        "input_command_uuids"
    ]


def test_stream_write_tracks_claude_command_uuid_across_result_race(
    server_mod, tmp_path, monkeypatch
):
    """A preceding result may land while the FIFO write is in progress.

    The accepted message must stay owned until Claude's command lifecycle
    acknowledges that exact UUID; a result count sampled on either side of the
    physical write cannot establish that causal relationship.
    """
    _sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    entry["input_result_target"] = 5
    registry = tmp_path / "spawned-pids.json"
    registry.write_text(json.dumps([{
        "pid": entry["pid"],
        "engine": "claude",
        "input_result_target": 5,
    }]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    command_uuid = "aaaaaaaa-1111-4111-8111-111111111111"
    monkeypatch.setattr(server_mod.uuid, "uuid4", lambda: command_uuid)

    def accept_after_old_result(_entry, line, timeout=0.25):
        payload = json.loads(line)
        assert payload["uuid"] == command_uuid
        log.write_text(_result_lines(5))
        return True

    monkeypatch.setattr(server_mod, "_write_via_spawn_fd", accept_after_old_result)

    assert server_mod._write_stream_json_user_message(entry, "follow up") is True
    assert server_mod._headless_turn_in_progress(entry) is True
    assert entry["input_command_uuids"] == [command_uuid]

    with log.open("a") as fh:
        fh.write(json.dumps({
            "type": "command_lifecycle",
            "command_uuid": command_uuid,
            "state": "completed",
        }) + "\n")

    assert server_mod._headless_turn_in_progress(entry) is False
    saved = json.loads(registry.read_text())[0]
    assert saved["input_command_uuids"] == [command_uuid]


def test_coalesced_stream_writes_wait_for_every_command_uuid(
    server_mod, tmp_path, monkeypatch
):
    _sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    registry = tmp_path / "spawned-pids.json"
    registry.write_text(json.dumps([{"pid": entry["pid"], "engine": "claude"}]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    command_uuids = iter([
        "aaaaaaaa-1111-4111-8111-111111111111",
        "bbbbbbbb-2222-4222-8222-222222222222",
    ])
    monkeypatch.setattr(server_mod.uuid, "uuid4", lambda: next(command_uuids))
    monkeypatch.setattr(server_mod, "_write_via_spawn_fd", lambda *_a, **_k: True)

    assert server_mod._write_stream_json_user_message(entry, "first") is True
    assert server_mod._write_stream_json_user_message(entry, "second") is True

    first, second = entry["input_command_uuids"]
    log.write_text(
        _result_lines(5)
        + json.dumps({
            "type": "command_lifecycle", "command_uuid": second,
            "state": "completed",
        }) + "\n"
    )
    assert server_mod._headless_turn_in_progress(entry) is True

    with log.open("a") as fh:
        fh.write(json.dumps({
            "type": "command_lifecycle", "command_uuid": first,
            "state": "completed",
        }) + "\n")
    assert server_mod._headless_turn_in_progress(entry) is False


def test_ask_waits_for_its_own_command_result_when_prior_turn_finishes(
    server_mod, tmp_path, monkeypatch
):
    """A synchronous ask must not return an older active turn's result.

    A fleet verifier can complete just after its parent sends a corrective
    follow-up.  Both inputs share one persistent headless process, so the
    follow-up must wait for its own ``user_message_uuid`` rather than accept
    the next terminal result in the shared stream.
    """
    sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 0
    )
    earlier_command = "aaaaaaaa-1111-4111-8111-111111111111"
    asked_command = "bbbbbbbb-2222-4222-8222-222222222222"
    entry["input_command_uuids"] = [earlier_command]
    entry["proc"] = mock.Mock()
    entry["proc"].poll.return_value = None
    original = list(server_mod._spawned_sessions)
    server_mod._spawned_sessions[:] = [entry]

    def write_ask(target, _text):
        target["input_command_uuids"] = [earlier_command, asked_command]
        log.write_text(
            json.dumps({
                "type": "result",
                "result": "stale verifier report",
                "user_message_uuid": earlier_command,
            }) + "\n" + json.dumps({
                "type": "result",
                "result": "fresh corrective report",
                "user_message_uuid": asked_command,
            }) + "\n"
        )
        return True

    try:
        with mock.patch.object(server_mod, "_detect_session_engine", return_value="claude"), \
             mock.patch.object(server_mod, "find_session_cwd", return_value=str(tmp_path)), \
             mock.patch.object(
                 server_mod, "session_live_status", return_value={"live": False, "tty": None}
             ), \
             mock.patch.object(server_mod, "_poll_spawn_entry", return_value=None), \
             mock.patch.object(server_mod, "_write_stream_json_user_message", side_effect=write_ask):
            result = server_mod.ask_session_and_wait(sid, "verify current head", timeout_ms=100)
    finally:
        server_mod._spawned_sessions.clear()
        server_mod._spawned_sessions.extend(original)

    assert result["ok"] is True
    assert result["text"] == "fresh corrective report"


def test_ask_routes_claude_to_persistent_worker(server_mod):
    """The process that owns a warm Claude must also wait for its reply."""
    sid = "11111111-2222-3333-4444-555555555555"
    expected = {
        "ok": True,
        "text": "worker-owned reply",
        "source": "resume-headless",
    }
    with mock.patch.object(server_mod, "_detect_session_engine", return_value="claude"), \
         mock.patch.object(server_mod, "_control_plane_routes_engines", return_value=True), \
         mock.patch.object(
             server_mod,
             "_control_plane_engine_call",
             return_value=expected,
         ) as ask, \
         mock.patch.object(server_mod, "find_session_cwd", return_value="/repo"), \
         mock.patch.object(
             server_mod,
             "session_live_status",
             return_value={"live": False, "tty": None},
         ), \
         mock.patch.object(server_mod, "_try_wt_ask_for_headless_delivery", return_value=None), \
         mock.patch.object(
             server_mod,
             "resume_session_headless",
             return_value={"ok": False, "error": "should not resume locally"},
         ):
        result = server_mod.ask_session_and_wait(sid, "status?", timeout_ms=12_000)

    assert result == expected
    ask.assert_called_once_with(
        "claude",
        "ask",
        {"session_id": sid, "text": "status?", "timeout_ms": 12_000, "cwd": None},
        timeout_ms=12_000,
    )


def test_worker_executes_claude_ask_in_its_owned_process(server_mod):
    """Worker-owned spawns keep their FIFO and in-memory state available."""
    from worker_engines import EngineHost

    expected = {"ok": True, "text": "worker-owned reply"}
    server_mod.ask_session_and_wait = mock.Mock(return_value=expected)
    host = EngineHost(mock.Mock())
    host._module = server_mod

    result = host._call("claude", "ask", {
        "session_id": "worker-session",
        "text": "status?",
        "timeout_ms": 12_000,
        "cwd": "/repo",
    })

    assert result == expected
    server_mod.ask_session_and_wait.assert_called_once_with(
        "worker-session", "status?", timeout_ms=12_000, cwd="/repo"
    )


def test_post_result_stream_activity_keeps_next_turn_open(server_mod, tmp_path):
    _sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    entry["input_result_target"] = 5
    log.write_text(
        _result_lines(5)
        + json.dumps({"type": "system", "subtype": "init"}) + "\n"
        + json.dumps({"type": "assistant", "message": {"content": []}}) + "\n"
    )

    assert server_mod._headless_turn_in_progress(entry) is True

    log.write_text(_result_lines(6))
    assert server_mod._headless_turn_in_progress(entry) is False


def test_benign_post_result_trailer_does_not_reopen_turn(server_mod, tmp_path):
    _sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    entry["input_result_target"] = 5
    log.write_text(
        _result_lines(5)
        + json.dumps({"type": "command_lifecycle", "subtype": "completed"})
        + "\n"
        + json.dumps({"type": "rate_limit_event"}) + "\n"
    )

    assert server_mod._headless_turn_in_progress(entry) is False


def test_retire_idle_helper_skips_busy(server_mod, tmp_path):
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    with mock.patch.object(server_mod, "_detect_session_engine", return_value="claude"), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value={"pid": 1}), \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire:
        res = server_mod._retire_idle_headless_for_session(sid)
    assert res["retired"] is False
    assert res.get("reason") == "busy"
    retire.assert_not_called()


def test_retire_idle_helper_skips_owned_pure_text_turn(server_mod, tmp_path):
    sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    log.write_text(_result_lines(4))
    entry["input_result_target"] = 5

    with mock.patch.object(server_mod, "_detect_session_engine", return_value="claude"), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_ensure_headless_staleness_watcher_started"), \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire:
        result = server_mod._retire_idle_headless_for_session(
            sid, reason="terminal-takeover", defer_if_busy=True
        )

    assert result == {"retired": False, "reason": "busy", "deferred": True}
    assert entry["retire_when_idle"] is True
    retire.assert_not_called()


def test_retire_idle_helper_retires_idle(server_mod, tmp_path):
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    with mock.patch.object(server_mod, "_detect_session_engine", return_value="claude"), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire:
        res = server_mod._retire_idle_headless_for_session(sid)
    assert res["retired"] is True
    assert res["pid"] == 999999
    retire.assert_called_once()


def test_retire_idle_helper_skips_non_claude(server_mod, tmp_path):
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    with mock.patch.object(server_mod, "_detect_session_engine", return_value="codex"), \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire:
        res = server_mod._retire_idle_headless_for_session(sid)
    assert res["retired"] is False
    retire.assert_not_called()


def _status_takeover_retire(status):
    """Mirror of the server status endpoint's CCC-173 on-observe retire gate.

    Kept in lockstep with server.py: when a poll observes a Claude session that
    has BOTH a live headless and a live terminal, retire the idle headless and
    clear the headless fields so the proc pill stops showing "headless" the
    moment a terminal takes the session over.
    """
    import server as _srv
    if status.get("headless_present") and status.get("terminal_present"):
        retired = _srv._retire_idle_headless_for_session(
            status["sid"], reason="status-terminal-takeover")
        if retired.get("retired"):
            status["headless_present"] = False
            status["headless_pid"] = None
            status["headless_stale"] = False
            status["retired_headless_pid"] = retired.get("pid")
    return status


def test_status_retires_headless_when_terminal_takes_over(server_mod, tmp_path):
    """CCC-173: headless + live terminal on the same Claude session →
    the headless is retired on observe and the pill fields clear."""
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    status = {"sid": sid, "headless_present": True, "headless_pid": 999999,
              "headless_stale": False, "terminal_present": True}
    with mock.patch.object(server_mod, "_detect_session_engine", return_value="claude"), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire:
        out = _status_takeover_retire(status)
    retire.assert_called_once()
    assert out["headless_present"] is False
    assert out["headless_pid"] is None
    assert out["retired_headless_pid"] == 999999


def test_status_keeps_headless_with_no_terminal(server_mod, tmp_path):
    """No terminal owner → never retire; a lone headless must keep running."""
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    status = {"sid": sid, "headless_present": True, "headless_pid": 999999,
              "headless_stale": False, "terminal_present": False}
    with mock.patch.object(server_mod, "_detect_session_engine", return_value="claude"), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire:
        out = _status_takeover_retire(status)
    retire.assert_not_called()
    assert out["headless_present"] is True


def test_status_keeps_busy_headless_under_terminal(server_mod, tmp_path):
    """Terminal present but headless is mid-turn → never retire (hard rule)."""
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    status = {"sid": sid, "headless_present": True, "headless_pid": 999999,
              "headless_stale": False, "terminal_present": True}
    with mock.patch.object(server_mod, "_detect_session_engine", return_value="claude"), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value={"pid": 1}), \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire:
        out = _status_takeover_retire(status)
    retire.assert_not_called()
    assert out["headless_present"] is True


def test_live_terminal_snapshot_batches_registry_process_probe(server_mod, tmp_path):
    """The staleness watcher should not fork ps once per session-registry row."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    sid = "11111111-2222-3333-4444-555555555555"
    other_sid = "22222222-3333-4444-5555-666666666666"
    for idx, (session_id, pid) in enumerate([
        (sid, 10101),
        (sid, 10102),
        (other_sid, 10103),
    ]):
        (sessions / f"{pid}.json").write_text(json.dumps({
            "sessionId": session_id,
            "pid": pid,
            "status": "running",
        }))
    server_mod.SESSIONS_REGISTRY = sessions

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)

        class R:
            returncode = 0
            stderr = ""

        r = R()
        assert args[:2] == ["ps", "-o"]
        pids = str(args[-1]).split(",")
        r.stdout = "\n".join(
            f"{pid} ttys001 /usr/local/bin/claude /usr/local/bin/claude -p"
            for pid in pids
        )
        return r

    with mock.patch.object(server_mod.subprocess, "run", side_effect=fake_run):
        snapshot = server_mod._live_claude_terminal_pids_by_session()

    assert snapshot[sid] == {10101, 10102}
    assert snapshot[other_sid] == {10103}
    assert len(calls) == 1


def test_inject_no_concurrency_writes_fifo_unchanged(server_mod, tmp_path):
    """No concurrency: a lone idle headless inject must behave exactly as
    before — a single FIFO write, no retire, no respawn."""
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    # Give it a baseline watermark so the stale-check runs (and returns False).
    server_mod._update_spawn_transcript_watermark(entry, sid)
    status = {"live": False, "tty": None, "status": None}
    with mock.patch.object(server_mod, "find_session_cwd", return_value="/fake/cwd"), \
         mock.patch.object(server_mod, "session_live_status", return_value=status), \
         mock.patch.object(server_mod, "_is_codex_session", return_value=False), \
         mock.patch.object(server_mod, "_is_cursor_session", return_value=False), \
         mock.patch.object(server_mod, "_is_gemini_session", return_value=False), \
         mock.patch.object(server_mod, "_is_antigravity_session", return_value=False), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_terminal_input_queue_has_pending", return_value=False), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_pending_ask_user_question_for_session", return_value=False), \
         mock.patch.object(server_mod, "_write_stream_json_user_message", return_value=True) as wr, \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire, \
         mock.patch.object(server_mod, "resume_session_headless") as respawn:
        res = server_mod._inject_text_into_session(sid, "hello")
    assert res["ok"] is True
    assert res["via"] == "spawn-fifo"
    wr.assert_called_once()
    retire.assert_not_called()
    respawn.assert_not_called()


def test_inject_stale_retires_and_respawns(server_mod, tmp_path):
    """Use-time staleness: an external writer advanced the transcript → the
    headless is retired and a fresh resume handles the text."""
    sid, entry, transcript, log = _stage(server_mod, tmp_path, [_event("a")], 1)
    log.write_text(_result_lines(1))
    server_mod._update_spawn_transcript_watermark(entry, sid)
    # External writer appends, no new headless result → stale.
    _write_jsonl(transcript, [_event("a"), _event("ext1")])
    status = {"live": False, "tty": None, "status": None}
    with mock.patch.object(server_mod, "find_session_cwd", return_value="/fake/cwd"), \
         mock.patch.object(server_mod, "session_live_status", return_value=status), \
         mock.patch.object(server_mod, "_is_codex_session", return_value=False), \
         mock.patch.object(server_mod, "_is_cursor_session", return_value=False), \
         mock.patch.object(server_mod, "_is_gemini_session", return_value=False), \
         mock.patch.object(server_mod, "_is_antigravity_session", return_value=False), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_terminal_input_queue_has_pending", return_value=False), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_pending_ask_user_question_for_session", return_value=False), \
         mock.patch.object(server_mod, "_write_stream_json_user_message", return_value=True) as wr, \
         mock.patch.object(server_mod, "_resume_ledger_append"), \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire, \
         mock.patch.object(server_mod, "resume_session_headless",
                           return_value={"ok": True, "resumed": True}) as respawn:
        res = server_mod._inject_text_into_session(sid, "hello")
    # Stale path: retired + respawned, no FIFO write to the stale headless.
    retire.assert_called_once()
    respawn.assert_called_once()
    wr.assert_not_called()
    assert res.get("resumed") is True


def test_inject_busy_headless_never_retired_even_if_tail_moved(server_mod, tmp_path):
    """Safety: a busy headless (active tool child) is never retired by the
    use-time check, even if the transcript looks advanced."""
    sid, entry, transcript, log = _stage(server_mod, tmp_path, [_event("a")], 1)
    log.write_text(_result_lines(1))
    server_mod._update_spawn_transcript_watermark(entry, sid)
    _write_jsonl(transcript, [_event("a"), _event("ext1")])
    status = {"live": False, "tty": None, "status": None}
    # active_child truthy at the moment of the guard → busy → queue, not retire.
    with mock.patch.object(server_mod, "find_session_cwd", return_value="/fake/cwd"), \
         mock.patch.object(server_mod, "session_live_status", return_value=status), \
         mock.patch.object(server_mod, "_is_codex_session", return_value=False), \
         mock.patch.object(server_mod, "_is_cursor_session", return_value=False), \
         mock.patch.object(server_mod, "_is_gemini_session", return_value=False), \
         mock.patch.object(server_mod, "_is_antigravity_session", return_value=False), \
         mock.patch.object(server_mod, "_is_kilo_session", return_value=False), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_terminal_input_queue_has_pending", return_value=False), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child",
                           return_value={"pid": 4242}), \
         mock.patch.object(server_mod, "_pending_ask_user_question_for_session", return_value=False), \
         mock.patch.object(server_mod, "_queue_terminal_input",
                           return_value={"ok": True, "queued": True}) as q, \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire, \
         mock.patch.object(server_mod, "resume_session_headless") as respawn:
        res = server_mod._inject_text_into_session(sid, "hello")
    retire.assert_not_called()
    respawn.assert_not_called()
    # Current behavior for active tool child: we do not proactively queue for a merely-busy
    # turn (the stream-json path accepts mid-turn input). We either succeed the write or
    # return the "pipe is busy" error. The key safety is "never retired".
    assert res.get("ok") is False or "busy" in str(res.get("error", "")).lower() or res.get("queued") is True
    # q may or may not be called depending on exact write outcome; the old "always queue on busy"
    # contract was intentionally relaxed.


def test_inject_owned_mid_turn_transcript_growth_never_retires(
    server_mod, tmp_path
):
    """Production regression: a live owned turn may grow the transcript
    before its terminal result appears and while no tool child is running."""
    sid, entry, transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    log.write_text(_result_lines(4))
    server_mod._update_spawn_transcript_watermark(entry, sid)
    entry["input_result_target"] = 5
    _write_jsonl(transcript, [_event("a"), _event("owned-mid-turn")])
    status = {"live": False, "tty": None, "status": None}

    with mock.patch.object(server_mod, "find_session_cwd", return_value="/fake/cwd"), \
         mock.patch.object(server_mod, "session_live_status", return_value=status), \
         mock.patch.object(server_mod, "_is_codex_session", return_value=False), \
         mock.patch.object(server_mod, "_is_cursor_session", return_value=False), \
         mock.patch.object(server_mod, "_is_gemini_session", return_value=False), \
         mock.patch.object(server_mod, "_is_antigravity_session", return_value=False), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_session_has_live_terminal", return_value=False), \
         mock.patch.object(server_mod, "_terminal_input_queue_has_pending", return_value=False), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_pending_ask_user_question_for_session", return_value=False), \
         mock.patch.object(server_mod, "_write_stream_json_user_message", return_value=True) as write, \
         mock.patch.object(
             server_mod,
             "_headless_spawn_is_stale",
             wraps=server_mod._headless_spawn_is_stale,
         ) as stale, \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire, \
         mock.patch.object(server_mod, "resume_session_headless") as resume:
        result = server_mod._inject_text_into_session(sid, "follow up")

    assert result == {"ok": True, "pid": entry["pid"], "via": "spawn-fifo"}
    write.assert_called_once_with(entry, "follow up")
    stale.assert_not_called()
    retire.assert_not_called()
    resume.assert_not_called()


def test_force_queue_holds_busy_headless_followup(server_mod, tmp_path):
    sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    log.write_text(_result_lines(4))
    entry["input_result_target"] = 5
    status = {"live": False, "tty": None, "status": None}

    with mock.patch.object(server_mod, "find_session_cwd", return_value="/fake/cwd"), \
         mock.patch.object(server_mod, "session_live_status", return_value=status), \
         mock.patch.object(server_mod, "_is_codex_session", return_value=False), \
         mock.patch.object(server_mod, "_is_cursor_session", return_value=False), \
         mock.patch.object(server_mod, "_is_gemini_session", return_value=False), \
         mock.patch.object(server_mod, "_is_antigravity_session", return_value=False), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_session_has_live_terminal", return_value=False), \
         mock.patch.object(server_mod, "_terminal_input_queue_has_pending", return_value=False), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_pending_ask_user_question_for_session", return_value=False), \
         mock.patch.object(
             server_mod,
             "_queue_terminal_input",
             return_value={"ok": True, "queued": True, "via": "terminal-queued"},
         ) as queue, \
         mock.patch.object(server_mod, "_write_stream_json_user_message") as write:
        result = server_mod._inject_text_into_session(
            sid, "wait for next turn", force_queue=True
        )

    assert result["queued"] is True
    queue.assert_called_once()
    write.assert_not_called()


def test_terminal_queue_waits_for_worker_owned_headless(server_mod, monkeypatch):
    sid = "11111111-2222-3333-4444-555555555555"
    monkeypatch.setattr(
        server_mod, "_find_live_spawn_entry_for_session", lambda _sid: None,
    )
    monkeypatch.setattr(
        server_mod, "_control_plane_engine_call",
        lambda engine, operation, args, **kwargs: {
            "ok": True, "owned": True, "busy": True, "pid": 42,
        },
    )

    assert server_mod._terminal_queue_waits_for_headless_turn(
        sid, {"live": True, "tty": None, "pid": 42},
    ) is True
    assert server_mod._worker_owned_claude_input_state(sid)["owned"] is True


def test_ordinary_send_steers_busy_headless_followup(server_mod, tmp_path):
    sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    log.write_text(_result_lines(4))
    entry["input_result_target"] = 5
    status = {"live": False, "tty": None, "status": None}

    with mock.patch.object(server_mod, "find_session_cwd", return_value="/fake/cwd"), \
         mock.patch.object(server_mod, "session_live_status", return_value=status), \
         mock.patch.object(server_mod, "_is_codex_session", return_value=False), \
         mock.patch.object(server_mod, "_is_cursor_session", return_value=False), \
         mock.patch.object(server_mod, "_is_gemini_session", return_value=False), \
         mock.patch.object(server_mod, "_is_antigravity_session", return_value=False), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_session_has_live_terminal", return_value=False), \
         mock.patch.object(server_mod, "_terminal_input_queue_has_pending", return_value=False), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_pending_ask_user_question_for_session", return_value=False), \
         mock.patch.object(server_mod, "_headless_spawn_is_stale", return_value=False), \
         mock.patch.object(server_mod, "_queue_terminal_input") as queue, \
         mock.patch.object(server_mod, "_write_stream_json_user_message", return_value=True) as write:
        result = server_mod._inject_text_into_session(sid, "context for this turn")

    assert result["ok"] is True
    assert result["via"] == "spawn-fifo"
    write.assert_called_once_with(entry, "context for this turn")
    queue.assert_not_called()


def test_control_plane_inject_forwards_explicit_queue_flag(server_mod):
    sid = "11111111-2222-3333-4444-555555555555"
    status = {"live": False, "tty": None, "status": None}

    with mock.patch.object(server_mod, "_is_codex_session", return_value=False), \
         mock.patch.object(server_mod, "_is_cursor_session", return_value=False), \
         mock.patch.object(server_mod, "_is_kimi_session", return_value=False), \
         mock.patch.object(server_mod, "_is_hermes_session", return_value=False), \
         mock.patch.object(server_mod, "_is_opencode_session", return_value=False), \
         mock.patch.object(server_mod, "_is_devin_cli_session", return_value=False), \
         mock.patch.object(server_mod, "_is_gemini_session", return_value=False), \
         mock.patch.object(server_mod, "_is_antigravity_session", return_value=False), \
         mock.patch.object(server_mod, "find_session_cwd", return_value="/fake/cwd"), \
         mock.patch.object(server_mod, "session_live_status", return_value=status), \
         mock.patch.object(server_mod, "_control_plane_routes_engines", return_value=True), \
         mock.patch.object(
             server_mod,
             "_control_plane_engine_call",
             return_value={"ok": True, "queued": True},
         ) as routed:
        result = server_mod._inject_text_into_session(
            sid, "wait until next turn", force_queue=True
        )

    assert result == {"ok": True, "queued": True}
    args = routed.call_args.args
    assert args[:2] == ("claude", "inject")
    assert args[2]["force_queue"] is True


def test_federated_inject_forwards_explicit_queue_flag(server_mod):
    sid = "11111111-2222-3333-4444-555555555555"
    with mock.patch.object(
        server_mod,
        "_federation_resolve_target",
        return_value=(sid, "peer-node"),
    ), mock.patch.object(
        server_mod,
        "_federation_proxy_session_action",
        return_value={"ok": True, "queued": True},
    ) as proxy:
        result = server_mod._inject_text_into_session(
            "peer-node:" + sid,
            "wait until next turn",
            force_queue=True,
        )

    assert result == {"ok": True, "queued": True}
    proxy.assert_called_once_with("peer-node", "inject", {
        "session_id": sid,
        "text": "wait until next turn",
        "mode": "send",
        "force_queue": True,
    })


def test_terminal_queue_waits_for_owned_headless_turn(server_mod, tmp_path):
    sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    entry["input_result_target"] = 5
    status = {"live": True, "tty": None, "pid": entry["pid"]}

    with mock.patch.object(
        server_mod, "_find_live_spawn_entry_for_session", return_value=entry
    ):
        assert server_mod._terminal_queue_waits_for_headless_turn(sid, status) is True
        assert server_mod._terminal_queue_waits_for_headless_turn(
            sid, {"live": False, "tty": None}
        ) is True
        log.write_text(_result_lines(5))
        assert server_mod._terminal_queue_waits_for_headless_turn(sid, status) is False


def test_failed_write_during_owned_turn_queues_without_retiring(
    server_mod, tmp_path
):
    sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    log.write_text(_result_lines(4))
    entry["input_result_target"] = 5
    status = {"live": False, "tty": None, "status": None}

    with mock.patch.object(server_mod, "find_session_cwd", return_value="/fake/cwd"), \
         mock.patch.object(server_mod, "session_live_status", return_value=status), \
         mock.patch.object(server_mod, "_is_codex_session", return_value=False), \
         mock.patch.object(server_mod, "_is_cursor_session", return_value=False), \
         mock.patch.object(server_mod, "_is_gemini_session", return_value=False), \
         mock.patch.object(server_mod, "_is_antigravity_session", return_value=False), \
         mock.patch.object(server_mod, "_find_live_spawn_entry_for_session", return_value=entry), \
         mock.patch.object(server_mod, "_session_has_live_terminal", return_value=False), \
         mock.patch.object(server_mod, "_terminal_input_queue_has_pending", return_value=False), \
         mock.patch.object(server_mod, "_spawn_entry_active_tool_child", return_value=None), \
         mock.patch.object(server_mod, "_pending_ask_user_question_for_session", return_value=False), \
         mock.patch.object(server_mod, "_write_stream_json_user_message", return_value=False), \
         mock.patch.object(
             server_mod,
             "_queue_terminal_input",
             return_value={"ok": True, "queued": True, "via": "terminal-queued"},
         ) as queue, \
         mock.patch.object(server_mod, "_retire_unresponsive_spawn_entry") as retire, \
         mock.patch.object(server_mod, "resume_session_headless") as resume:
        result = server_mod._inject_text_into_session(sid, "do not lose this")

    assert result["queued"] is True
    queue.assert_called_once()
    retire.assert_not_called()
    resume.assert_not_called()


def test_resume_reuse_failed_write_during_owned_turn_does_not_retire(
    server_mod, tmp_path
):
    sid, entry, _transcript, log = _stage(
        server_mod, tmp_path, [_event("a")], 4
    )
    log.write_text(_result_lines(4))
    entry["input_result_target"] = 5
    original = list(server_mod._spawned_sessions)
    server_mod._spawned_sessions[:] = [entry]
    try:
        with mock.patch.object(
            server_mod, "_claude_subagent_parent_session_id", return_value=None
        ), mock.patch.object(
            server_mod, "_control_plane_engine_call", return_value=None
        ), mock.patch.object(
            server_mod, "_poll_spawn_entry", return_value=None
        ), mock.patch.object(
            server_mod, "_write_stream_json_user_message", return_value=False
        ), mock.patch.object(
            server_mod, "_spawn_entry_active_tool_child", return_value=None
        ), mock.patch.object(
            server_mod, "_retire_unresponsive_spawn_entry"
        ) as retire:
            result = server_mod.resume_session_headless(sid, "keep this safe")

        assert result["ok"] is False
        assert "busy" in result["error"]
        retire.assert_not_called()
    finally:
        server_mod._spawned_sessions.clear()
        server_mod._spawned_sessions.extend(original)


def test_record_spawn_registry_persists_result_target_state(
    server_mod, tmp_path, monkeypatch
):
    registry = tmp_path / "spawned-pids.json"
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)

    server_mod._record_spawn_to_registry(
        pid=4242,
        name="followup-safe",
        log_path=tmp_path / "spawn.log",
        cwd=tmp_path,
        spawned_at="20260812T120000",
        command_summary="test prompt",
        engine="claude",
        input_result_target=1,
        input_accepted_at=1234.5,
    )

    row = json.loads(registry.read_text())[0]
    assert row["input_result_target"] == 1
    assert row["input_accepted_at"] == 1234.5


def test_reattach_preserves_result_target_state(
    server_mod, tmp_path, monkeypatch
):
    registry = tmp_path / "spawned-pids.json"
    log = tmp_path / "spawn.log"
    log.write_text(_result_lines(4))
    registry.write_text(json.dumps([{
        "pid": os.getpid(),
        "session_id": "11111111-2222-3333-4444-555555555555",
        "name": "followup-safe",
        "log": str(log),
        "fifo": None,
        "cwd": str(tmp_path),
        "spawned_at": "20260812T120000",
        "command_summary": "test prompt",
        "engine": "claude",
        "input_result_target": 5,
        "input_accepted_at": 1234.5,
    }]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    original = list(server_mod._spawned_sessions)
    server_mod._spawned_sessions.clear()
    try:
        with mock.patch.object(server_mod, "_pid_is_engine_process", return_value=True), \
             mock.patch.object(server_mod, "_open_fifo_writer", return_value=None):
            server_mod._reattach_spawned_orphans()

        assert len(server_mod._spawned_sessions) == 1
        entry = server_mod._spawned_sessions[0]
        assert entry["input_result_target"] == 5
        assert entry["input_accepted_at"] == 1234.5
        assert server_mod._headless_turn_in_progress(entry) is True
        saved = json.loads(registry.read_text())[0]
        assert saved["input_result_target"] == 5
        assert saved["input_accepted_at"] == 1234.5
    finally:
        server_mod._spawned_sessions.clear()
        server_mod._spawned_sessions.extend(original)


def test_reattach_preserves_command_uuid_state(
    server_mod, tmp_path, monkeypatch
):
    registry = tmp_path / "spawned-pids.json"
    log = tmp_path / "spawn.log"
    command_uuid = "aaaaaaaa-1111-4111-8111-111111111111"
    log.write_text(_result_lines(1))
    registry.write_text(json.dumps([{
        "pid": os.getpid(),
        "session_id": "11111111-2222-3333-4444-555555555555",
        "name": "followup-safe",
        "log": str(log),
        "fifo": None,
        "cwd": str(tmp_path),
        "spawned_at": "20260812T120000",
        "command_summary": "test prompt",
        "engine": "claude",
        "input_command_uuids": [command_uuid],
        "input_accepted_at": 1234.5,
    }]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    original = list(server_mod._spawned_sessions)
    server_mod._spawned_sessions.clear()
    try:
        with mock.patch.object(server_mod, "_pid_is_engine_process", return_value=True), \
             mock.patch.object(server_mod, "_open_fifo_writer", return_value=None):
            server_mod._reattach_spawned_orphans()

        entry = server_mod._spawned_sessions[0]
        assert entry["input_command_uuids"] == [command_uuid]
        assert server_mod._headless_turn_in_progress(entry) is True
        with log.open("a") as fh:
            fh.write(json.dumps({
                "type": "command_lifecycle", "command_uuid": command_uuid,
                "state": "completed",
            }) + "\n")
        assert server_mod._headless_turn_in_progress(entry) is False
        assert json.loads(registry.read_text())[0]["input_command_uuids"] == [
            command_uuid
        ]
    finally:
        server_mod._spawned_sessions.clear()
        server_mod._spawned_sessions.extend(original)


def test_reattach_drops_malformed_result_target_state(
    server_mod, tmp_path, monkeypatch
):
    registry = tmp_path / "spawned-pids.json"
    log = tmp_path / "spawn.log"
    log.write_text(_result_lines(1))
    registry.write_text(json.dumps([{
        "pid": os.getpid(),
        "session_id": "11111111-2222-3333-4444-555555555555",
        "name": "legacy",
        "log": str(log),
        "fifo": None,
        "cwd": str(tmp_path),
        "spawned_at": "20260812T120000",
        "command_summary": "test prompt",
        "engine": "claude",
        "input_result_target": -4,
        "input_accepted_at": "not-a-time",
    }]))
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    original = list(server_mod._spawned_sessions)
    server_mod._spawned_sessions.clear()
    try:
        with mock.patch.object(server_mod, "_pid_is_engine_process", return_value=True), \
             mock.patch.object(server_mod, "_open_fifo_writer", return_value=None):
            server_mod._reattach_spawned_orphans()

        entry = server_mod._spawned_sessions[0]
        assert "input_result_target" not in entry
        assert "input_accepted_at" not in entry
        saved = json.loads(registry.read_text())[0]
        assert "input_result_target" not in saved
        assert "input_accepted_at" not in saved
    finally:
        server_mod._spawned_sessions.clear()
        server_mod._spawned_sessions.extend(original)


def test_spawn_registry_lock_serializes_separate_processes(
    server_mod, tmp_path, monkeypatch
):
    registry = tmp_path / "spawned-pids.json"
    registry.write_text("[]")
    monkeypatch.setattr(server_mod, "SPAWNED_PIDS_FILE", registry)
    context = multiprocessing.get_context("fork")
    first_entered = context.Event()
    first_release = context.Event()
    second_entered = context.Event()
    second_release = context.Event()
    first = context.Process(
        target=_hold_spawn_registry_lock,
        args=(str(registry), first_entered, first_release),
    )
    second = context.Process(
        target=_hold_spawn_registry_lock,
        args=(str(registry), second_entered, second_release),
    )
    first.start()
    assert first_entered.wait(2)
    second.start()
    try:
        assert not second_entered.wait(0.3)
        first_release.set()
        assert second_entered.wait(2)
    finally:
        first_release.set()
        second_release.set()
        first.join(3)
        second.join(3)
        if first.is_alive():
            first.terminate()
        if second.is_alive():
            second.terminate()
