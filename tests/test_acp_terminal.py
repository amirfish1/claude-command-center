"""ACP terminal capability handlers (_acp_handle_terminal_request).

Covers the terminal/* agent→client requests that power kimi's shell tools
(Bash/Glob/Grep) when CCC is the ACP client. Responses are captured by
monkeypatching _acp_respond; commands are real stdlib subprocesses (/bin/echo,
sleep, sys.executable) — no external systems involved.
"""

from __future__ import annotations

import sys
import time

import pytest

import server


@pytest.fixture()
def captured(monkeypatch):
    """Capture _acp_respond payloads keyed by req_id."""
    responses = {}

    def fake_respond(harness, req_id, result=None, error=None):
        responses[req_id] = {"result": result, "error": error}

    monkeypatch.setattr(server, "_acp_respond", fake_respond)
    return responses


@pytest.fixture(autouse=True)
def clean_terminals():
    yield
    with server._ACP_TERMINALS_LOCK:
        leftovers = list(server._ACP_TERMINALS.items())
        server._ACP_TERMINALS.clear()
    for _, entry in leftovers:
        if entry["proc"].poll() is None:
            entry["proc"].kill()
        entry["exit_event"].set()


def _wait_response(responses, req_id, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if req_id in responses:
            return responses[req_id]
        time.sleep(0.05)
    raise AssertionError(f"no response for req_id {req_id}")


def test_create_output_wait_release_roundtrip(captured):
    server._acp_handle_terminal_request("kimi", 1, "terminal/create", {
        "sessionId": "s1", "command": "/bin/echo", "args": ["hello"],
    })
    tid = captured[1]["result"]["terminalId"]

    server._acp_handle_terminal_request("kimi", 2, "terminal/wait_for_exit", {
        "sessionId": "s1", "terminalId": tid,
    })
    resp = _wait_response(captured, 2)
    assert resp["error"] is None
    assert resp["result"]["exitCode"] == 0
    assert resp["result"]["signal"] is None

    server._acp_handle_terminal_request("kimi", 3, "terminal/output", {
        "sessionId": "s1", "terminalId": tid,
    })
    out = captured[3]["result"]
    assert "hello" in out["output"]
    assert out["truncated"] is False
    assert out["exitStatus"]["exitCode"] == 0

    server._acp_handle_terminal_request("kimi", 4, "terminal/release", {
        "sessionId": "s1", "terminalId": tid,
    })
    assert captured[4]["result"] == {}
    with server._ACP_TERMINALS_LOCK:
        assert tid not in server._ACP_TERMINALS


def test_kill_keeps_terminal_queryable(captured):
    server._acp_handle_terminal_request("kimi", 1, "terminal/create", {
        "sessionId": "s1", "command": "sleep", "args": ["30"],
    })
    tid = captured[1]["result"]["terminalId"]

    server._acp_handle_terminal_request("kimi", 2, "terminal/kill", {
        "sessionId": "s1", "terminalId": tid,
    })
    assert captured[2]["error"] is None

    server._acp_handle_terminal_request("kimi", 3, "terminal/wait_for_exit", {
        "sessionId": "s1", "terminalId": tid,
    })
    resp = _wait_response(captured, 3)
    assert resp["result"]["exitCode"] is None
    assert resp["result"]["signal"] == "SIGKILL"

    server._acp_handle_terminal_request("kimi", 4, "terminal/release", {
        "sessionId": "s1", "terminalId": tid,
    })


def test_output_byte_limit_truncates(captured):
    server._acp_handle_terminal_request("kimi", 1, "terminal/create", {
        "sessionId": "s1",
        "command": sys.executable,
        "args": ["-c", "print('x' * 100000)"],
        "outputByteLimit": 8192,
    })
    tid = captured[1]["result"]["terminalId"]

    server._acp_handle_terminal_request("kimi", 2, "terminal/wait_for_exit", {
        "sessionId": "s1", "terminalId": tid,
    })
    _wait_response(captured, 2)

    server._acp_handle_terminal_request("kimi", 3, "terminal/output", {
        "sessionId": "s1", "terminalId": tid,
    })
    out = captured[3]["result"]
    assert out["truncated"] is True
    assert len(out["output"]) <= 8192
    assert set(out["output"].strip()) == {"x"}

    server._acp_handle_terminal_request("kimi", 4, "terminal/release", {
        "sessionId": "s1", "terminalId": tid,
    })


def test_unknown_terminal_id_errors(captured):
    server._acp_handle_terminal_request("kimi", 1, "terminal/output", {
        "sessionId": "s1", "terminalId": "nope",
    })
    assert captured[1]["error"]["code"] == -32602


def test_create_requires_command(captured):
    server._acp_handle_terminal_request("kimi", 1, "terminal/create", {
        "sessionId": "s1", "command": "",
    })
    assert captured[1]["error"]["code"] == -32602


def test_terminal_argv_keeps_command_plus_args():
    assert server._acp_terminal_argv("/bin/echo", ["hello"]) == ["/bin/echo", "hello"]
    assert server._acp_terminal_argv("/bin/echo") == ["/bin/echo"]
    assert server._acp_terminal_argv("sleep", []) == ["sleep"]


def test_terminal_argv_splits_grok_bash_lc_command_line():
    assert server._acp_terminal_argv("/bin/bash -lc 'echo ok'") == [
        "/bin/bash", "-lc", "echo ok",
    ]
    assert server._acp_terminal_argv("/bin/bash -lc 'echo ok'", []) == [
        "/bin/bash", "-lc", "echo ok",
    ]


def test_terminal_argv_overlong_command_does_not_use_command_as_path():
    payload = "/bin/bash -lc " + repr("x" * 8000)
    argv = server._acp_terminal_argv(payload)
    assert argv[0] == "/bin/bash"
    assert argv[1] == "-lc"
    assert len(argv[0]) < 64


def test_create_accepts_grok_bash_lc_command_line(captured):
    server._acp_handle_terminal_request("grok", 1, "terminal/create", {
        "sessionId": "s1", "command": "/bin/bash -lc 'echo grok-shell'",
    })
    assert captured[1]["error"] is None
    tid = captured[1]["result"]["terminalId"]

    server._acp_handle_terminal_request("grok", 2, "terminal/wait_for_exit", {
        "sessionId": "s1", "terminalId": tid,
    })
    resp = _wait_response(captured, 2)
    assert resp["error"] is None
    assert resp["result"]["exitCode"] == 0

    server._acp_handle_terminal_request("grok", 3, "terminal/output", {
        "sessionId": "s1", "terminalId": tid,
    })
    assert "grok-shell" in captured[3]["result"]["output"]

    server._acp_handle_terminal_request("grok", 4, "terminal/release", {
        "sessionId": "s1", "terminalId": tid,
    })
