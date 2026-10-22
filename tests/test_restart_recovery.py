"""Restart recovery: sessions a machine restart cut off mid-turn.

Fake transcripts on disk with controlled mtimes, and fixed boot/shutdown
times, so nothing depends on this machine's real boot history.
"""

import json
import os
import time

import pytest

from ccc_server import restart_recovery as rr

BOOT = 1_800_000_000.0
DOWN = BOOT - 3600  # machine went down an hour before it came back


@pytest.fixture(autouse=True)
def _fresh_memo():
    rr._MEMO = None
    yield
    rr._MEMO = None


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, "cwd": "/w/app", **extra}


def _tool_use(name="Bash", **inp):
    return {"type": "assistant", "cwd": "/w/app", "message": {
        "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "t1", "name": name, "input": inp}]}}


def _tool_result():
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def _reply(text="done"):
    return {"type": "assistant", "message": {
        "role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


def _write(root, sid, records, mtime, project="-w-app"):
    d = root / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / (sid + ".jsonl")
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    os.utime(p, (mtime, mtime))
    return p


def _state(tmp_path, shutdowns=(DOWN,), boot=BOOT, now=None):
    return rr.state(projects_root=tmp_path / "projects", state_file=tmp_path / "state.json",
                    boot=boot, now=now or boot + 60, shutdowns=list(shutdowns))


def test_open_turn_kinds():
    assert rr.open_turn([_user("go"), _reply()]) is None
    step = rr.open_turn([_user("go"), _tool_use(command="pytest", description="Run tests")])
    assert step["kind"] == "tool_running" and step["tool"] == "Bash" and step["detail"] == "Run tests"
    assert rr.open_turn([_user("go"), _tool_use(), _tool_result()])["kind"] == "awaiting_model"
    assert rr.open_turn([_reply(), _user("next?")])["kind"] == "prompt_unanswered"


def test_bookkeeping_records_do_not_open_a_turn():
    # Esc interrupts, slash commands and meta records after a finished reply.
    recs = [_user("go"), _reply(), _user("[Request interrupted by user]"),
            _user("<command-name>/exit</command-name>"), _user("x", isMeta=True),
            {"type": "cost-state"}, {"type": "last-prompt", "lastPrompt": "go"}]
    assert rr.open_turn(recs) is None
    assert rr.open_turn([_user("go"), _reply(), _user("side", isSidechain=True)]) is None


def test_only_turns_open_just_before_the_shutdown_count(tmp_path):
    root = tmp_path / "projects"
    _write(root, "aaaaaaaa-cut", [_user("go"), _tool_use(command="make")], DOWN - 120)
    _write(root, "bbbbbbbb-done", [_user("go"), _reply()], DOWN - 60)
    # Died on its own 40 minutes before the shutdown (API error, timeout).
    _write(root, "cccccccc-early", [_user("go"), _tool_use(), _tool_result()], DOWN - 2400)
    # A subagent may legitimately run for hours.
    _write(root, "dddddddd-agent", [_user("go"), _tool_use("Agent", prompt="research")], DOWN - 3 * 3600)
    # Written after this boot: a live or already-resumed session.
    _write(root, "eeeeeeee-live", [_user("go"), _tool_use()], BOOT + 10)
    got = _state(tmp_path)
    ids = [s["session_id"] for s in got["sessions"]]
    assert ids == ["aaaaaaaa-cut", "dddddddd-agent"]
    first = got["sessions"][0]
    assert first["shutdown_at"] == DOWN and first["approximate"] is False
    assert first["title"] == "go" and first["cwd"] == "/w/app"
    assert "path" not in first and "size" not in first


def test_each_session_is_matched_to_the_shutdown_that_stopped_it(tmp_path):
    earlier = DOWN - 5 * 3600
    root = tmp_path / "projects"
    _write(root, "aaaaaaaa-old", [_user("go"), _tool_use()], earlier - 60)
    _write(root, "bbbbbbbb-new", [_user("go"), _tool_use()], DOWN - 60)
    got = {s["session_id"]: s["shutdown_at"] for s in _state(tmp_path, shutdowns=(earlier, DOWN))["sessions"]}
    assert got == {"aaaaaaaa-old": earlier, "bbbbbbbb-new": DOWN}


def test_unknown_shutdown_falls_back_to_boot_and_says_so(tmp_path):
    _write(tmp_path / "projects", "aaaaaaaa-cut", [_user("go"), _tool_use(), _tool_result()], BOOT - 7200)
    got = _state(tmp_path, shutdowns=())["sessions"]
    assert [s["session_id"] for s in got] == ["aaaaaaaa-cut"] and got[0]["approximate"] is True


def test_resumed_and_dismissed_sessions_drop_off(tmp_path):
    root = tmp_path / "projects"
    a = _write(root, "aaaaaaaa-one", [_user("go"), _tool_use()], DOWN - 60)
    _write(root, "bbbbbbbb-two", [_user("go"), _tool_use()], DOWN - 30)
    assert len(_state(tmp_path)["sessions"]) == 2
    # Resumed: the transcript moved on.
    with open(a, "a") as fh:
        fh.write(json.dumps(_tool_result()) + "\n")
    assert [s["session_id"] for s in _state(tmp_path)["sessions"]] == ["bbbbbbbb-two"]
    assert rr.dismiss(["bbbbbbbb-two"], state_file=tmp_path / "state.json") == 1
    assert _state(tmp_path)["sessions"] == []


def test_scan_runs_once_per_boot_and_carries_undismissed_entries(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    _write(root, "aaaaaaaa-cut", [_user("go"), _tool_use()], DOWN - 60)
    _state(tmp_path)
    calls = []
    real_scan = rr.scan
    monkeypatch.setattr(rr, "scan", lambda *a, **k: calls.append(1) or real_scan(*a, **k))
    rr._MEMO = None
    _state(tmp_path)  # same boot: served from the persisted file
    assert calls == []
    # Rebooted again before anyone looked; the old entry is outside the new
    # scan window but still untouched, so it is carried over.
    later = BOOT + 4 * 86400
    got = _state(tmp_path, shutdowns=(later - 600,), boot=later, now=later + 60)
    assert calls == [1]
    assert [s["session_id"] for s in got["sessions"]] == ["aaaaaaaa-cut"]


def test_heartbeat_before_boot_records_the_shutdown(tmp_path):
    hb, log = tmp_path / "hb", tmp_path / "shutdowns.json"
    hb.touch()
    os.utime(hb, (DOWN, DOWN))
    assert rr.record_heartbeat_shutdown(BOOT, hb, log) == DOWN
    assert rr.record_heartbeat_shutdown(BOOT, hb, log) == DOWN
    assert json.loads(log.read_text()) == [DOWN]
    os.utime(hb, (BOOT + 5, BOOT + 5))  # touched in this boot: not a shutdown
    assert rr.record_heartbeat_shutdown(BOOT, hb, log) is None
    assert rr.known_shutdowns(BOOT, BOOT + 60, log, journal=[DOWN + 30, DOWN - 86400]) == [DOWN - 86400, DOWN]


def test_parse_journal_boot_list():
    out = (" -1 e0fe836b0a3641fab418298da2d04af2 Thu 2026-10-22 12:22:45 UTC Thu 2026-10-22 12:41:46 UTC\n"
           "  0 aa8ee495cfa342b7920d652d059756ea Thu 2026-10-22 12:44:55 UTC Thu 2026-10-22 13:40:22 UTC\n")
    got = rr.parse_boot_list(out)
    assert [time.strftime("%H:%M:%S", time.gmtime(t)) for t in got] == ["12:41:46", "13:40:22"]


def test_resume_prompt_names_the_unfinished_step():
    text = rr.resume_prompt({"kind": "tool_running", "tool": "Bash", "detail": "Run tests"}, DOWN)
    assert "Bash" in text and "Run tests" in text and "before redoing" in text
    assert "never" not in rr.resume_prompt({"kind": "awaiting_model"}, None)
