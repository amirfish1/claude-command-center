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
import sys
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


def test_no_change_not_stale(server_mod, tmp_path):
    sid, entry, _t, _l = _stage(server_mod, tmp_path, [_event("a")], 0)
    server_mod._update_spawn_transcript_watermark(entry, sid)
    assert server_mod._headless_spawn_is_stale(entry, sid) is False


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
