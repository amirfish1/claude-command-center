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
def _fresh_memo(tmp_path, monkeypatch):
    rr._MEMO = None
    # Never let a test see this machine's real Codex / ACP transcripts.
    monkeypatch.setattr(rr, "CODEX_ROOT", tmp_path / "codex")
    monkeypatch.setattr(rr, "ACP_ROOT", tmp_path / "acp")
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


# --- RESIL-3: Codex rollouts and ACP engines (Kimi, Devin) ---

CODEX_SID = "01a16bbc-f43e-7ac0-a141-6e15345f2fd2"


def _ev(ptype, **extra):
    return {"type": "event_msg", "payload": {"type": ptype, **extra}}


def _ri(ptype, **extra):
    return {"type": "response_item", "payload": {"type": ptype, **extra}}


def _codex_write(tmp_path, records, mtime, sid=CODEX_SID):
    day = tmp_path / "codex" / time.strftime("%Y/%m/%d", time.localtime(mtime))
    day.mkdir(parents=True, exist_ok=True)
    p = day / ("rollout-2027-01-15T08-00-00-%s.jsonl" % sid)
    meta = {"type": "session_meta", "payload": {"id": sid, "cwd": "/w/codex-app"}}
    p.write_text("".join(json.dumps(r) + "\n" for r in [meta] + records))
    os.utime(p, (mtime, mtime))
    return p


def _acp_write(tmp_path, engine, stem, records, mtime):
    d = tmp_path / "acp" / engine
    d.mkdir(parents=True, exist_ok=True)
    p = d / (stem + ".jsonl")
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    os.utime(p, (mtime, mtime))
    return p


def test_codex_open_turn_kinds():
    start, done = _ev("task_started"), _ev("task_complete")
    user = _ri("message", role="user", content=[])
    assert rr.codex_open_turn([start, user, _ri("message", role="assistant"), done]) is None
    assert rr.codex_open_turn([start, _ev("turn_aborted")]) is None
    assert rr.codex_open_turn([user]) is None  # no task boundary in the tail: never guess
    step = rr.codex_open_turn([start, user, _ri("function_call", name="shell",
                                                 arguments=json.dumps({"command": ["pytest", "-q"]}))])
    assert step["kind"] == "tool_running" and step["tool"] == "shell" and step["detail"] == "pytest -q"
    assert rr.codex_open_turn([start, _ri("custom_tool_call", name="exec", input="x"),
                               _ri("custom_tool_call_output")])["kind"] == "awaiting_model"
    assert rr.codex_open_turn([done, start, user])["kind"] == "prompt_unanswered"


def test_acp_open_turn_kinds():
    assert rr.acp_open_turn([{"type": "user_text", "text": "go"}, {"type": "result", "subtype": "end_turn"}]) is None
    assert rr.acp_open_turn([{"type": "user_text", "text": "go"}])["kind"] == "prompt_unanswered"
    step = rr.acp_open_turn([{"type": "user_text", "text": "go"}, {"type": "assistant", "blocks": [
        {"kind": "thinking", "text": "hm"}, {"kind": "tool_use", "name": "Bash", "detail": "make test"}]}])
    assert step["kind"] == "tool_running" and step["tool"] == "Bash" and step["detail"] == "make test"
    assert rr.acp_open_turn([{"type": "user_text"}, {"type": "assistant", "blocks": [
        {"kind": "text", "text": "ok"}]}])["kind"] == "awaiting_model"


def test_codex_kimi_and_devin_sessions_cut_off_by_the_restart(tmp_path):
    _write(tmp_path / "projects", "aaaaaaaa-claude", [_user("go"), _tool_use(command="make")], DOWN - 100)
    _codex_write(tmp_path, [_ev("task_started"), _ev("user_message", message="fix the build"),
                            _ri("function_call", name="shell", arguments="{}")], DOWN - 200)
    _acp_write(tmp_path, "kimi", "session_11111111-2222", [{"type": "user_text", "text": "kimi task"}], DOWN - 300)
    _acp_write(tmp_path, "devin", "cookie-college", [{"type": "user_text", "text": "devin task"}], DOWN - 400)
    # Finished, or went quiet long before the shutdown: not the restart's fault.
    _acp_write(tmp_path, "kimi", "session_33333333-done",
               [{"type": "user_text", "text": "x"}, {"type": "result", "subtype": "end_turn"}], DOWN - 60)
    _acp_write(tmp_path, "devin", "long-ago", [{"type": "user_text", "text": "x"}], DOWN - 5 * 3600)
    got = {s["session_id"]: s for s in _state(tmp_path)["sessions"]}
    assert set(got) == {"aaaaaaaa-claude", CODEX_SID, "session_11111111-2222", "devincli-cookie-college"}
    assert [got[k]["engine"] for k in ("aaaaaaaa-claude", CODEX_SID, "session_11111111-2222",
                                      "devincli-cookie-college")] == ["claude", "codex", "kimi", "devin"]
    assert got[CODEX_SID]["cwd"] == "/w/codex-app" and got[CODEX_SID]["title"] == "fix the build"
    assert got[CODEX_SID]["step"]["kind"] == "tool_running"
    assert got["devincli-cookie-college"]["title"] == "devin task"


def test_resumed_codex_session_drops_off(tmp_path):
    p = _codex_write(tmp_path, [_ev("task_started")], DOWN - 60)
    assert [s["session_id"] for s in _state(tmp_path)["sessions"]] == [CODEX_SID]
    with open(p, "a") as fh:
        fh.write(json.dumps(_ev("task_complete")) + "\n")
    rr._MEMO = None
    assert _state(tmp_path)["sessions"] == []


def test_codex_scan_lists_only_day_dirs_in_the_window(tmp_path, monkeypatch):
    listed = []
    real = rr._jsonl_entries
    monkeypatch.setattr(rr, "_jsonl_entries", lambda d: listed.append(str(d)) or real(d))
    _state(tmp_path)
    codex_dirs = [d for d in listed if "/codex/" in d]
    assert codex_dirs and len(codex_dirs) <= 8
