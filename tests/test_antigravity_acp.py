"""`agy acp` conversation processes: busy/idle, steer, model, fallbacks.

The fake agent mirrors what agy 1.5.14 does on the real wire: a second
session/prompt sent mid-turn answers the first with end_turn and runs the
new one (that is the steer).
"""
import importlib
import json
import sys
import time
from unittest import mock

import pytest

SID = "22222222-2222-2222-2222-222222222222"

FAKE_ACP = r"""
import json, sys, threading, time
lock = threading.Lock()
current = {"id": None, "timer": None}
def out(obj):
    with lock:
        sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
def finish(rid, text):
    with lock:
        if current["id"] != rid:
            return
        current["id"] = None
    out({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
        "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "DONE:" + text}}}})
    out({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})
for line in sys.stdin:
    msg = json.loads(line)
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None:
        out({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "PERM:" + json.dumps(msg.get("result") or msg.get("error"))}}}})
        continue
    if method == "initialize":
        out({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1}})
    elif method == "session/resume":
        if params.get("sessionId") == "missing":
            out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "not found"}})
        else:
            out({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": params["sessionId"], "models": {
                "currentModelId": "fake-medium",
                "availableModels": [{"modelId": "fake-medium", "name": "Fake Model (Medium)"},
                                    {"modelId": "fake-high", "name": "Fake Model (High)"}]}}})
    elif method == "session/set_model":
        out({"jsonrpc": "2.0", "method": "session/update", "params": {"update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "MODEL:" + params["modelId"]}}}})
        out({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif method == "session/prompt":
        text = params["prompt"][0]["text"]
        if text == "die":
            sys.exit(3)
        with lock:
            prev, current["id"] = current["id"], rid
        if prev is not None:
            out({"jsonrpc": "2.0", "id": prev, "result": {"stopReason": "end_turn"}})
        if text == "perm":
            out({"jsonrpc": "2.0", "id": "perm-1", "method": "session/request_permission", "params": {
                "options": [{"optionId": "yes", "kind": "allow_once"}, {"optionId": "no", "kind": "reject_once"}]}})
        if text == "report tool":
            out({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": params["sessionId"], "update": {
                    "sessionUpdate": "tool_call", "toolCallId": "1", "status": "in_progress",
                    "name": "edit_file", "title": "Sensitive tool input",
                }}})
        delay = 1.0 if text.startswith("slow") or text == "report tool" else 0.3
        threading.Timer(delay, finish, (rid, text)).start()
"""


@pytest.fixture
def stream(tmp_path):
    importlib.import_module("server")
    from ccc_server import antigravity_stream
    antigravity_stream._PROCS.clear()
    antigravity_stream._ACP_DISABLED_UNTIL = 0.0
    yield antigravity_stream
    for sp in list(antigravity_stream._PROCS.values()):
        sp.close()
    antigravity_stream._PROCS.clear()
    antigravity_stream._ACP_DISABLED_UNTIL = 0.0


def _start(stream, tmp_path, sid=SID, model="", skip=True):
    return stream.spawn_acp(sid, [sys.executable, "-c", FAKE_ACP], str(tmp_path),
                            str(tmp_path / "acp.log"), ("", ()), model_label=model,
                            skip_permissions=skip, handshake_timeout=5)


def _wait(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _log(tmp_path):
    return (tmp_path / "acp.log").read_text()


def test_prompt_marks_busy_until_answered_and_process_is_reused(stream, tmp_path):
    sp = _start(stream, tmp_path)
    first = stream.send(SID, "one")
    assert first["ok"] and first["via"] == "antigravity-acp"
    assert stream.state(SID)["busy"] is True
    assert stream.send(SID, "mid-turn")["code"] == "busy"   # a plain send never interrupts
    assert _wait(lambda: not stream.state(SID)["busy"])
    assert stream.state(SID)["last_result"]["stopReason"] == "end_turn"
    assert stream.send(SID, "two")["ok"]
    assert stream.state(SID)["pid"] == sp.proc.pid
    assert _wait(lambda: "DONE:two" in _log(tmp_path))


def test_live_activity_reflects_acp_tool_and_turn_end_before_transcript_updates(stream, tmp_path):
    import server

    sp = _start(stream, tmp_path)
    persisted_tail = {"last_event_type": "result", "turn_complete": True, "last_meaningful_ts": 1}
    with mock.patch.object(server, "_archive_session_is_live", return_value=True), \
         mock.patch.object(server, "_detect_session_engine", return_value="antigravity"), \
         mock.patch.object(server, "_antigravity_transcript_path", return_value=tmp_path / "old"), \
         mock.patch.object(server, "_extract_antigravity_tail_meta", return_value=persisted_tail):
        assert SID in server._live_state_only_in_dashboard()
        assert stream.send(SID, "report tool")["ok"]
        assert _wait(lambda: stream.acp_activity(SID).get("tool") == "edit_file")
        entry = server._live_activity_entry_for_session(SID)
        assert server._session_state_label(entry) == "working"
        assert entry["sidecar_tool"] == entry["pending_tool"] == "edit_file"
        assert entry["sidecar_ts"] >= entry["working_since"] > persisted_tail["last_meaningful_ts"]
        assert entry["sidecar_file"] is None

        # A completion update records progress even while the prompt stays open.
        sp._note_activity({"sessionId": SID, "update": {
            "sessionUpdate": "tool_call_update", "toolCallId": "1", "status": "completed",
        }})
        entry = server._live_activity_entry_for_session(SID)
        assert entry["sidecar_in_flight"]
        assert entry["pending_tool"] is None

        assert _wait(lambda: not stream.acp_activity(SID)["busy"])
        entry = server._live_activity_entry_for_session(SID)
        assert server._session_state_label(entry) == "idle"
        assert not entry["sidecar_in_flight"]
        assert entry["working_since"] is None
        assert entry["pending_tool"] is None


def test_steer_lands_in_the_running_turn(stream, tmp_path):
    _start(stream, tmp_path)
    assert stream.send(SID, "slow work")["ok"]
    steered = stream.steer(SID, "change of plan")
    assert steered["ok"] and steered["via"] == "antigravity-acp-steer"
    # agy answers the first prompt at once; the steer keeps the session busy.
    time.sleep(0.15)
    assert stream.state(SID)["busy"] is True
    assert _wait(lambda: not stream.state(SID)["busy"])
    log = _log(tmp_path)
    assert "DONE:change of plan" in log
    assert "DONE:slow work" not in log


def test_steer_is_refused_for_a_stream_json_process(stream, tmp_path):
    fake_stream = "import sys\nfor _ in sys.stdin: pass\n"
    stream.spawn(SID, [sys.executable, "-c", fake_stream], str(tmp_path),
                 str(tmp_path / "s.log"), ("", ()))
    assert stream.steer(SID, "x")["code"] == "steer_unsupported"


def test_model_label_is_applied_over_acp(stream, tmp_path):
    _start(stream, tmp_path, model="Fake Model (High)")
    assert _wait(lambda: "MODEL:fake-high" in _log(tmp_path))


def test_unknown_model_or_conversation_falls_back_for_that_session_only(stream, tmp_path):
    with pytest.raises(stream._AcpSessionError):
        _start(stream, tmp_path, model="Nope 9")
    assert stream.state(SID) is None
    assert stream.acp_enabled()   # other conversations keep steering
    with pytest.raises(stream._AcpSessionError):
        _start(stream, tmp_path, sid="missing")
    assert stream.acp_enabled()


def test_failed_start_pauses_acp_for_everyone(stream, tmp_path):
    with pytest.raises(stream._AcpError):
        stream.spawn_acp(SID, [sys.executable, "-c", "import sys; sys.exit(2)"], str(tmp_path),
                         str(tmp_path / "acp.log"), ("", ()), handshake_timeout=5)
    assert not stream.acp_enabled()


def test_permission_requests_follow_skip_permissions(stream, tmp_path):
    _start(stream, tmp_path)
    assert stream.send(SID, "perm")["ok"]
    assert _wait(lambda: '"optionId": "yes"' in _log(tmp_path))


def test_acp_kill_switch(stream, monkeypatch):
    monkeypatch.setenv("CCC_ANTIGRAVITY_ACP", "0")
    assert not stream.acp_enabled()


def test_process_lost_mid_turn_requeues(stream, tmp_path):
    from ccc_server import core
    _start(stream, tmp_path)
    with mock.patch.object(core, "_apply_pending_input_operations", return_value={"ok": True}) as apply_ops, \
         mock.patch.object(core, "_log_activity"):
        assert stream.send(SID, "die")["ok"]
        assert _wait(lambda: stream.state(SID) is None)
        assert _wait(lambda: apply_ops.called)
    assert apply_ops.call_args[0][1][0]["value"] == "die"


def test_resume_steers_a_busy_acp_process_and_keeps_stream_honest():
    server = importlib.import_module("server")
    eng = importlib.import_module("ccc_server.engines")
    busy = {"pid": 7, "busy": True, "turns": 1, "key": ("", (), "primary"),
            "idle_for_s": 0.0, "last_result": None}
    patches = [
        mock.patch.object(server, "_antigravity_cli_conversation_path", return_value=None),
        mock.patch.object(server, "_antigravity_app_conversation_path", return_value=None),
        mock.patch.object(server, "_resolve_antigravity_bin", return_value={"available": True}),
        mock.patch.object(server, "_get_session_override", return_value=None),
        mock.patch.object(eng._antigravity_stream, "state", return_value=busy),
    ]
    for p in patches:
        p.start()
    try:
        ok = {"ok": True, "via": "antigravity-acp-steer", "pid": 7}
        with mock.patch.object(eng._antigravity_stream, "steer", return_value=ok) as steer:
            assert eng.resume_session_antigravity(SID, "go left", mode="steer") == ok
            steer.assert_called_once_with(SID, "go left")
        refused = {"ok": False, "code": "steer_unsupported"}
        with mock.patch.object(eng._antigravity_stream, "steer", return_value=refused):
            result = eng.resume_session_antigravity(SID, "go left", mode="steer")
            assert result["queued_preserved"] is True
    finally:
        for p in patches:
            p.stop()


def test_live_resume_pid_finds_an_acp_process_by_its_log_name():
    eng = importlib.import_module("ccc_server.engines")
    ps = (f"  41 /usr/bin/agy acp --log-file /x/acp-antigravity-{SID}-20261121T120000.log\n"
          f"  42 /usr/bin/agy acp --log-file /x/acp-antigravity-33333333-3333-3333-3333-333333333333-1.log\n")
    done = mock.Mock(returncode=0, stdout=ps)
    with mock.patch.object(eng.subprocess, "run", return_value=done):
        assert eng._antigravity_live_resume_pid(SID) == 41
        assert eng._antigravity_live_resume_pid("44444444-4444-4444-4444-444444444444") is None


def test_session_matcher_recognizes_acp_processes_by_log_name():
    sg = importlib.import_module("ccc_server.session_graph")
    other = "33333333-3333-3333-3333-333333333333"
    cmd = f"/usr/bin/agy acp --log-file /x/acp-antigravity-{SID}-20261121T120000.log --dangerously-skip-permissions"
    assert sg._antigravity_acp_session(cmd) == SID
    assert sg._command_targets_engine_session(cmd, SID, "antigravity")
    assert not sg._command_targets_engine_session(cmd, other, "antigravity")
    # Another session's ACP process in the same repo is not this session's.
    assert sg._command_targets_other_session(cmd, other, "antigravity")
    assert not sg._command_targets_other_session(cmd, SID, "antigravity")
    stream_cmd = f"/usr/bin/agy --conversation {SID} --print= --input-format stream-json"
    assert sg._antigravity_acp_session(stream_cmd) is None
    assert sg._command_targets_engine_session(stream_cmd, SID, "antigravity")
