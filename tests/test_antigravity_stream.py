"""Persistent stream-json Antigravity process: `result` decides busy vs idle."""
import importlib
import os
import sys
import time
from unittest import mock

import pytest

FAKE_AGY = r"""
import json, sys, time
print(json.dumps({"event": "init", "conversation_id": "c"}), flush=True)
for line in sys.stdin:
    msg = json.loads(line)["message"]["content"]
    if msg == "die":
        sys.exit(3)
    if msg == "missing-result":
        print(json.dumps({"event": "step_update", "step_update": {
            "state": "DONE", "step_type": "agent_response",
        }}), flush=True)
        continue
    time.sleep(0.4)
    print(json.dumps({"event": "result", "result": {"status": "SUCCESS", "response": msg.upper()}}), flush=True)
"""


@pytest.fixture
def stream(tmp_path):
    importlib.import_module("server")
    from ccc_server import antigravity_stream
    antigravity_stream._PROCS.clear()
    antigravity_stream._DISABLED_UNTIL = 0.0
    yield antigravity_stream
    for sp in list(antigravity_stream._PROCS.values()):
        sp.close()
    antigravity_stream._PROCS.clear()
    antigravity_stream._DISABLED_UNTIL = 0.0


def _start(stream, tmp_path, sid="conv-1", cli_log_path=None):
    return stream.spawn(sid, [sys.executable, "-c", FAKE_AGY], str(tmp_path),
                        str(tmp_path / "agy.log"), ("", ()),
                        cli_log_path=cli_log_path)


def _wait(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_result_event_marks_idle_and_process_is_reused(stream, tmp_path):
    sp = _start(stream, tmp_path)
    assert stream.send("conv-1", "one")["ok"]
    assert stream.state("conv-1")["busy"] is True
    assert stream.send("conv-1", "mid-turn")["code"] == "busy"   # never writes mid-turn
    assert _wait(lambda: not stream.state("conv-1")["busy"])
    assert stream.state("conv-1")["last_result"]["response"] == "ONE"
    pid = stream.state("conv-1")["pid"]
    assert stream.send("conv-1", "two")["ok"]
    assert _wait(lambda: not stream.state("conv-1")["busy"])
    st = stream.state("conv-1")
    assert (st["pid"], st["turns"], st["last_result"]["response"]) == (pid, 2, "TWO")
    assert stream.owns_pid(pid)


def test_idle_process_is_not_busy_however_long_it_lives(stream, tmp_path):
    _start(stream, tmp_path)
    server = importlib.import_module("server")
    with mock.patch.object(server, "_is_antigravity_session", return_value=True):
        assert server._resume_queue_engine_busy("conv-1") is False
        stream.send("conv-1", "one")
        assert server._resume_queue_engine_busy("conv-1") is True


def test_process_lost_mid_turn_requeues_and_backs_off(stream, tmp_path):
    server = importlib.import_module("server")
    with mock.patch.object(server, "_apply_pending_input_operations", return_value={"ok": True}) as apply_ops, \
         mock.patch.object(server, "_log_activity"):
        _start(stream, tmp_path)
        assert stream.send("conv-1", "die")["ok"]
        assert _wait(lambda: stream.state("conv-1") is None)
        assert _wait(lambda: apply_ops.called)
    op = apply_ops.call_args[0][1][0]
    assert (op["field"], op["action"], op["value"]) == ("resume", "append_tail", "die")
    assert stream.enabled() is False        # one-shot path takes over for a while


def test_missing_result_retires_after_quiet_final_without_replay(stream, tmp_path):
    server = importlib.import_module("server")
    cli_log = tmp_path / "diagnostics.log"
    cli_log.touch()
    sp = _start(stream, tmp_path, cli_log_path=cli_log)
    assert stream.send("conv-1", "missing-result")["ok"]
    assert _wait(lambda: sp.final_step_at is not None)
    quiet_time = time.time() - 120
    sp.final_step_at = quiet_time
    os.utime(sp.log_fh.name, (quiet_time, quiet_time))

    with mock.patch.object(server, "_antigravity_turn_finished", return_value=False) as finished, \
         mock.patch.object(server, "_apply_pending_input_operations") as apply_ops, \
         mock.patch.object(server, "_log_activity"):
        assert stream.state("conv-1")["busy"]  # native diagnostics are still active
        finished.assert_not_called()
        os.utime(cli_log, (quiet_time, quiet_time))
        assert stream.state("conv-1")["busy"]  # no persisted final response yet
        assert not sp.proc.stdin.closed

        finished.return_value = True
        assert stream.state("conv-1")["busy"]  # still owns the conversation
        assert sp.proc.stdin.closed
        assert _wait(lambda: stream.state("conv-1") is None)
        apply_ops.assert_not_called()

    _start(stream, tmp_path)
    assert stream.send("conv-1", "next")["ok"]
    assert _wait(lambda: stream.state("conv-1")["last_result"])
    assert stream.state("conv-1")["last_result"]["response"] == "NEXT"


def test_resume_sends_to_idle_stream_and_queues_behind_a_busy_one():
    server = importlib.import_module("server")
    eng = importlib.import_module("ccc_server.engines")
    sid = "11111111-1111-1111-1111-111111111111"
    idle = {"pid": 7, "busy": False, "turns": 1, "key": ("", ()), "idle_for_s": 5.0, "last_result": None}
    common = [
        mock.patch.object(server, "_antigravity_cli_conversation_path", return_value=None),
        mock.patch.object(server, "_antigravity_app_conversation_path", return_value=None),
        mock.patch.object(server, "_resolve_antigravity_bin", return_value={"available": True}),
        mock.patch.object(server, "_get_session_override", return_value=None),
    ]
    for p in common:
        p.start()
    try:
        with mock.patch.object(eng._antigravity_stream, "state", return_value=idle), \
             mock.patch.object(eng._antigravity_stream, "send", return_value={"ok": True, "via": "antigravity-stream"}) as send:
            assert eng.resume_session_antigravity(sid, "hello")["via"] == "antigravity-stream"
            send.assert_called_once_with(sid, "hello")
        busy = dict(idle, busy=True)
        with mock.patch.object(eng._antigravity_stream, "state", return_value=busy), \
             mock.patch.object(eng._antigravity_stream, "send") as send, \
             mock.patch.object(server, "_apply_pending_input_operations", return_value={"ok": True}) as apply_ops:
            result = eng.resume_session_antigravity(sid, "later")
            assert result["queued"] is True
            send.assert_not_called()
            assert apply_ops.call_args[0][1][0]["value"] == "later"
    finally:
        for p in common:
            p.stop()
