"""CCC-245: overlapping permission asks in one Claude session.

PostToolUse used to delete the session's `_needs_approval` marker whenever ANY
tool returned, so a sub-agent's or parallel tool's completion dropped the
needs-you badge while another tool was still parked on its prompt. These drive
the real hook scripts (PermissionRequest / Notification / PostToolUse / Stop)
against a throwaway HOME and check the marker the dashboard reads.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOKS = os.path.join(ROOT, "hooks")
SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
A = ("Bash", {"command": "rm -rf build", "description": "clean"})
B = ("Edit", {"file_path": "/x/y.py", "old_string": "a", "new_string": "b"})
C = ("Read", {"file_path": "/x/z.py"})


@pytest.fixture
def home(tmp_path):
    return tmp_path


def _live(home):
    return home / ".claude" / "command-center" / "live-state"


def _marker(home):
    return _live(home) / f"{SID}_needs_approval.json"


def _asks(home):
    d = _live(home) / "asks" / SID
    return sorted(os.listdir(d)) if d.is_dir() else []


def _hook(home, script, payload):
    env = dict(os.environ, HOME=str(home), CCC_NOTIFY="0",
               CCC_APPROVAL_HOOK_DIR=str(home / "cloud-approvals"))
    env.pop("CCC_PORT", None)
    r = subprocess.run([sys.executable, os.path.join(HOOKS, script)],
                       input=json.dumps(dict(payload, session_id=SID)),
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def ask(home, call, transcript=""):
    tool, tin = call
    _hook(home, "permission-request.py", {
        "hook_event_name": "PermissionRequest", "tool_name": tool,
        "tool_input": tin, "transcript_path": transcript})
    _hook(home, "notification.py", {
        "hook_event_name": "Notification", "notification_type": "permission_prompt",
        "message": f"Claude needs your permission to use {tool}"})


def done(home, call, tool_use_id="", transcript=""):
    tool, tin = call
    _hook(home, "post-tool-use.py", {
        "hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": tin,
        "tool_use_id": tool_use_id, "tool_response": {},
        "transcript_path": transcript})


def _transcript(path, uses, results=()):
    lines = []
    for uid, (tool, tin) in uses:
        lines.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": uid, "name": tool, "input": tin}]}})
    for uid in results:
        lines.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": uid, "content": "x"}]}})
    path.write_text("".join(json.dumps(l) + "\n" for l in lines))
    return str(path)


def test_single_ask_clears_on_its_own_tool(home):
    ask(home, A)
    assert _marker(home).exists() and len(_asks(home)) == 1
    done(home, A)
    assert not _marker(home).exists() and _asks(home) == []


def test_overlapping_asks_hold_until_the_last_is_answered(home):
    ask(home, A)
    ask(home, B)
    done(home, A)
    assert _marker(home).exists(), "B is still waiting on its prompt"
    assert len(_asks(home)) == 1
    done(home, B)
    assert not _marker(home).exists() and _asks(home) == []


def test_parallel_tool_without_ask_does_not_clear(home):
    ask(home, A)
    done(home, C)
    assert _marker(home).exists()
    done(home, A)
    assert not _marker(home).exists()


def test_tool_use_id_resolves_even_if_input_was_rewritten(home, tmp_path):
    tp = _transcript(tmp_path / "t.jsonl", [("u1", A), ("u2", B)])
    ask(home, A, tp)
    ask(home, B, tp)
    done(home, ("Bash", dict(A[1], command="rm -rf ./build")), "u1", tp)
    assert _marker(home).exists() and len(_asks(home)) == 1
    done(home, B, "u2", tp)
    assert not _marker(home).exists()


def test_denied_ask_is_pruned_from_the_transcript(home, tmp_path):
    t = tmp_path / "t.jsonl"
    tp = _transcript(t, [("u1", A), ("u2", B)])
    ask(home, A, tp)
    ask(home, B, tp)
    # A was denied: Claude writes its tool_result, PostToolUse never fires.
    _transcript(t, [("u1", A), ("u2", B), ("u3", C)], results=["u1"])
    done(home, C, "u3", tp)
    assert _marker(home).exists() and len(_asks(home)) == 1   # B still open
    done(home, B, "u2", tp)
    assert not _marker(home).exists() and _asks(home) == []


def test_ask_recorded_before_transcript_flush_is_pruned_later(home, tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text("")
    ask(home, A, str(t))          # no tool_use in the transcript yet
    _transcript(t, [("u1", A), ("u3", C)], results=["u1"])
    done(home, C, "u3", str(t))
    assert not _marker(home).exists() and _asks(home) == []


def test_legacy_install_without_permission_request_hook(home):
    _hook(home, "notification.py", {"message": "Claude needs your permission to use Bash"})
    done(home, C)
    assert not _marker(home).exists()


def test_stop_clears_open_asks(home):
    ask(home, A)
    ask(home, B)
    _hook(home, "stop.py", {"hook_event_name": "Stop", "cwd": "/tmp"})
    assert not _marker(home).exists() and _asks(home) == []


def test_server_cleanup_prunes_asks_of_dead_sessions(home, monkeypatch):
    import server
    ask(home, A)
    live = _live(home)
    (live / "asks" / "live-sid").mkdir(parents=True)
    monkeypatch.setattr(server, "SIDECAR_STATE_DIR", live)
    server._cleanup_stale_sidecars({"live-sid"})
    assert _asks(home) == []
    assert (live / "asks" / "live-sid").is_dir()
