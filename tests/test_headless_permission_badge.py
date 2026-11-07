"""A headless `claude -p` session blocked on a permission prompt must show the
needs-approval badge and be answerable from the dashboard.

Claude Code fires the Notification hook (the usual badge source) only when a
dialog is shown, which never happens in print mode. So the PermissionRequest
hook raises the badge itself while it parks, and the dashboard's Approve/Deny
reaches the parked hook through a decision file instead of a macOS keystroke.
Incident: 2026-11-03, HARBOR session 06de7070 sat 15 min with no badge.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server import approval_hook as ah  # noqa: E402
from ccc_server import relay_context as rc  # noqa: E402

HOOK = os.path.join(PROJECT_ROOT, "hooks", "permission-request.py")
SID = "66666666-7777-8888-9999-000000000000"
TIN = {"command": "python3 - <<'EOF'\nprint(1)\nEOF", "description": "edit"}
SHA = rc.input_sha256(rc.canonical_input("Bash", TIN))


def _load_hook():
    spec = importlib.util.spec_from_file_location("perm_hook_headless", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CCC_APPROVAL_HOOK_DIR", str(tmp_path / "approvals"))
    monkeypatch.setenv("CCC_LIVE_STATE_DIR", str(tmp_path / "live"))
    monkeypatch.setenv("CCC_APPROVAL_HOOK_HEADLESS", "0")
    return tmp_path


def _marker(tmp_path, sid=SID):
    return tmp_path / "live" / f"{sid}_needs_approval.json"


def _start_hook(wait="20"):
    proc = subprocess.Popen(
        [sys.executable, HOOK], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        text=True, env=dict(os.environ, CCC_APPROVAL_HOOK_WAIT=wait))
    proc.stdin.write(json.dumps({"session_id": SID, "tool_name": "Bash",
                                 "tool_input": TIN,
                                 "hook_event_name": "PermissionRequest"}))
    proc.stdin.close()
    for _ in range(200):
        if ah.waiting(SID, SHA):
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("hook never parked")


def test_parked_hook_raises_badge_and_drops_it_on_decision(env):
    ah.touch_enabled()
    proc = _start_hook()
    doc = json.loads(_marker(env).read_text())
    assert doc["type"] == "permission_prompt"
    assert "Bash" in doc["message"]
    assert ah.deliver(SID, SHA, "allow") == "applied"
    out = json.loads(proc.stdout.read())
    proc.wait(timeout=10)
    assert out["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert not _marker(env).exists()


def test_badge_dropped_when_nobody_answers(env):
    ah.touch_enabled()
    proc = _start_hook(wait="0.8")
    assert _marker(env).exists()
    assert proc.stdout.read().strip() == ""
    proc.wait(timeout=10)
    assert not _marker(env).exists()


def test_existing_notification_marker_is_left_alone(env):
    ah.touch_enabled()
    _marker(env).parent.mkdir(parents=True, exist_ok=True)
    _marker(env).write_text(json.dumps({"message": "from Notification hook"}))
    proc = _start_hook(wait="0.6")
    proc.stdout.read()
    proc.wait(timeout=10)
    assert json.loads(_marker(env).read_text())["message"] == "from Notification hook"


def test_relay_off_and_interactive_parent_does_not_park(env):
    t0 = time.time()
    r = subprocess.run([sys.executable, HOOK], input=json.dumps({
        "session_id": SID, "tool_name": "Bash", "tool_input": TIN}),
        env=dict(os.environ, CCC_APPROVAL_HOOK_WAIT="5"),
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and r.stdout.strip() == ""
    assert time.time() - t0 < 3
    assert not _marker(env).exists()


def test_headless_parent_without_relay_parks_with_badge(env):
    os.environ["CCC_APPROVAL_HOOK_HEADLESS"] = "1"   # restored by monkeypatch
    proc = _start_hook(wait="20")
    assert _marker(env).exists()
    assert ah.deliver(SID, SHA, "allow") == "applied"
    proc.stdout.read()
    proc.wait(timeout=10)


def test_headless_parent_detection(tmp_path, monkeypatch):
    monkeypatch.delenv("CCC_APPROVAL_HOOK_HEADLESS", raising=False)
    hook = _load_hook()
    if not os.path.isdir("/proc"):
        pytest.skip("needs /proc")
    fake = str(tmp_path / "claude")
    code = "import time; time.sleep(30)"
    for argv, want in (([fake, "-c", code, "-p", "--verbose"], True),
                       ([fake, "-c", code, "--resume", "x"], False)):
        # argv[0] is what /proc reports, so this looks like a claude process.
        proc = subprocess.Popen(argv, executable=sys.executable)
        try:
            # Start the walk at the fake claude itself.
            assert hook.headless_parent(pid=proc.pid) is want
        finally:
            proc.kill()
            proc.wait()


def _sg():
    import server  # noqa: F401  (wires ccc_server._core before session_graph)
    from ccc_server import session_graph as sg
    return sg


def test_dashboard_answer_reaches_parked_hook(env, monkeypatch):
    sg = _sg()
    transcript = env / "t.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "u1", "name": "Bash", "input": TIN}]}}) + "\n")
    monkeypatch.setattr(sg, "_find_session_jsonl", lambda sid: transcript)
    monkeypatch.setattr(sg, "isClaudeSource_backend", lambda sid: True)
    monkeypatch.setattr(sg.sys, "platform", "linux")
    ah.touch_enabled()
    proc = _start_hook()
    assert sg.claude_approval_hook_sha(SID) == SHA
    res = sg.respond_claude_permission(SID, "decline")
    assert res == {"ok": True, "decision": "decline", "via": "hook"}
    out = json.loads(proc.stdout.read())
    proc.wait(timeout=10)
    assert out["hookSpecificOutput"]["decision"]["behavior"] == "deny"


def test_dashboard_answer_without_parked_hook_keeps_macos_guard(env, monkeypatch):
    sg = _sg()
    monkeypatch.setattr(sg, "_find_session_jsonl", lambda sid: None)
    monkeypatch.setattr(sg, "isClaudeSource_backend", lambda sid: True)
    monkeypatch.setattr(sg.sys, "platform", "linux")
    res = sg.respond_claude_permission(SID, "accept")
    assert res["ok"] is False and res["code"] == "macos_only"
