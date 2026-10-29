"""Cloud relay approval hook: full tool input on the wire (hard cap, explicit
truncation flag), the hash pinned over it, and the PermissionRequest hook that
applies a cloud decision only to the exact tool call. No server."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server import approval_hook as ah
from ccc_server import cloud_relay as cr
from ccc_server import relay_context as rc

HOOK = os.path.join(PROJECT_ROOT, "hooks", "permission-request.py")
SID = "11111111-2222-3333-4444-555555555555"


def _load_hook():
    spec = importlib.util.spec_from_file_location("perm_hook", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _transcript(tmp_path, tool, tin):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": tool, "input": tin}]}}) + "\n")
    return str(p)


@pytest.fixture
def appdir(tmp_path, monkeypatch):
    d = tmp_path / "cloud-approvals"
    monkeypatch.setenv("CCC_APPROVAL_HOOK_DIR", str(d))
    # The hook also records each ask under ~/.claude (CCC-245): keep it out
    # of the real HOME.
    monkeypatch.setenv("HOME", str(tmp_path))
    return d


# --- full tool input on the wire ---------------------------------------------

def test_multiedit_keeps_every_edit_and_hash_covers_all(tmp_path):
    edits = [{"old_string": f"a{i}", "new_string": f"b{i}"} for i in range(9)]
    p = _transcript(tmp_path, "MultiEdit", {"file_path": "src/x.py", "edits": edits})
    inp = rc.pending_input(p)
    assert not inp["truncated"]
    doc = json.loads(inp["text"])
    assert len(doc["input"]["edits"]) == 9 and doc["input"]["edits"][8]["new_string"] == "b8"
    assert inp["sha256"] == hashlib.sha256(inp["text"].encode()).hexdigest()


def test_over_cap_input_is_flagged_not_dropped(tmp_path):
    body = "x" * (rc.INPUT_CAP + 500)
    p = _transcript(tmp_path, "Write", {"file_path": "a.txt", "content": body})
    inp = rc.pending_input(p)
    assert inp["truncated"] is True and len(inp["text"]) == rc.INPUT_CAP
    full = rc.canonical_input("Write", {"file_path": "a.txt", "content": body})
    assert inp["sha256"] == hashlib.sha256(full.encode()).hexdigest()


def _api(path, tool):
    class API:
        def get(self, p, query=None):
            if p == "/api/sessions":
                return 200, {"sessions": [{
                    "id": SID, "session_id": SID, "display_name": "t",
                    "folder_label": "repo", "state": "waiting", "is_live": True,
                    "needs_approval": True, "pending_tool": tool,
                    "mtime": time.time(), "jsonl_path": path}]}
            if p == "/api/attention":
                return 200, {"items": [{"kind": "question_blocked", "priority": 1,
                                        "session_id": SID, "mtime": time.time()}]}
            return 200, {"ok": True}

        def post(self, p, body=None):
            return 200, {"ok": True}

        def call(self, method, p, body=None, query=None, timeout=None):
            return self.get(p, query)
    return API()


def test_attention_item_carries_full_input_with_flags(tmp_path):
    content = "line\n" * 400
    p = _transcript(tmp_path, "Write", {"file_path": "docs/a.md", "content": content})
    it = cr.build_snapshot(_api(p, "Write"), {"share_titles": True})["attention"][0]
    assert it["kind"] == "approval"
    assert it["action_input_truncated"] is False
    assert json.loads(it["action_input"])["input"]["content"] == content
    assert it["action_input_sha256"] == hashlib.sha256(
        it["action_input"].encode()).hexdigest()
    assert len(it["action"]) <= cr.CAP_ACTION        # display text stays short


def test_attention_item_truncation_flag_and_full_hash(tmp_path):
    p = _transcript(tmp_path, "Write", {"file_path": "a", "content": "z" * 30000})
    it = cr.build_snapshot(_api(p, "Write"), {"share_titles": True})["attention"][0]
    assert it["action_input_truncated"] is True
    assert len(it["action_input"]) <= cr.CAP_ACTION_INPUT
    assert hashlib.sha256(it["action_input"].encode()).hexdigest() \
        != it["action_input_sha256"]


def test_masked_secret_breaks_hash_so_cloud_cannot_allow(tmp_path):
    p = _transcript(tmp_path, "Write", {
        "file_path": "a", "content": "key=sk-ant-test-XXXX1234567890abcdef"})
    it = cr.build_snapshot(_api(p, "Write"), {"share_titles": True})["attention"][0]
    assert "sk-ant-test" not in it["action_input"]
    assert hashlib.sha256(it["action_input"].encode()).hexdigest() \
        != it["action_input_sha256"]


def test_no_input_without_share_titles(tmp_path):
    p = _transcript(tmp_path, "Write", {"file_path": "a", "content": "hi"})
    it = cr.build_snapshot(_api(p, "Write"), {"share_titles": False})["attention"][0]
    assert "action_input" not in it


def test_detail_pending_has_full_input(tmp_path):
    p = _transcript(tmp_path, "Edit", {"file_path": "a.py", "old_string": "o" * 600,
                                       "new_string": "n" * 600})
    ex = cr.CommandExecutor(_api(p, "Edit"), cr.IdempotencyStore(str(tmp_path / "i.db")))
    ctx = ex._detail_context(SID, _api(p, "Edit").get("/api/sessions")[1]["sessions"][0])
    pend = ctx["pending"]
    assert json.loads(pend["action_input"])["input"]["new_string"] == "n" * 600
    assert pend["action_input_truncated"] is False


def test_hook_and_relay_canonical_forms_agree():
    hook = _load_hook()
    for tool, tin in (("Bash", {"command": "ls", "description": "d"}),
                      ("Write", {"file_path": "é.txt", "content": "ü\n\"q\""}),
                      ("MultiEdit", {"edits": [{"b": 1, "a": 2}], "file_path": "x"})):
        assert hook.canonical_input(tool, tin) == rc.canonical_input(tool, tin)
        assert hook.input_sha256(tool, tin) == rc.input_sha256(
            rc.canonical_input(tool, tin))


# --- the PermissionRequest hook ----------------------------------------------

def _run_hook(appdir, tool, tin, wait="0.6", sid=SID):
    env = dict(os.environ, CCC_APPROVAL_HOOK_DIR=str(appdir),
               CCC_APPROVAL_HOOK_WAIT=wait)
    t0 = time.time()
    r = subprocess.run([sys.executable, HOOK], input=json.dumps({
        "session_id": sid, "tool_name": tool, "tool_input": tin,
        "hook_event_name": "PermissionRequest"}), env=env,
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0
    return r.stdout.strip(), time.time() - t0


def _decision_path(sha, sid=SID):
    return os.path.join(ah.base_dir(), "decisions", f"{sid}.{sha[:16]}.json")


def _post(sha, decision="allow", **over):
    ah.post_decision(SID, sha, decision)
    if over:
        path = _decision_path(sha)
        doc = json.load(open(path))
        doc.update(over)
        json.dump(doc, open(path, "w"))


TIN = {"command": "pytest -q", "description": "tests"}
SHA = rc.input_sha256(rc.canonical_input("Bash", TIN))


def _out_decision(out):
    return json.loads(out)["hookSpecificOutput"]["decision"]["behavior"]


def test_hook_exits_fast_without_enabled_marker(appdir):
    _post(SHA)
    out, dt = _run_hook(appdir, "Bash", TIN, wait="30")
    assert out == "" and dt < 5
    assert os.path.exists(_decision_path(SHA))        # not consumed


def test_hook_allows_matching_decision_once(appdir):
    ah.touch_enabled()
    _post(SHA, "allow")
    out, _ = _run_hook(appdir, "Bash", TIN)
    assert _out_decision(out) == "allow"
    assert not os.path.exists(_decision_path(SHA))
    out2, _ = _run_hook(appdir, "Bash", TIN)           # single use
    assert out2 == ""


def test_hook_deny_decision(appdir):
    ah.touch_enabled()
    _post(SHA, "deny")
    out, _ = _run_hook(appdir, "Bash", TIN)
    body = json.loads(out)["hookSpecificOutput"]["decision"]
    assert body["behavior"] == "deny" and body["message"]


def test_hook_ignores_decision_for_other_tool_input(appdir):
    ah.touch_enabled()
    other = rc.input_sha256(rc.canonical_input("Bash", {"command": "make deploy"}))
    _post(other)
    out, _ = _run_hook(appdir, "Bash", TIN)
    assert out == ""
    assert os.path.exists(_decision_path(other))      # not ours, left alone


def test_hook_rejects_tampered_hash_inside_matching_filename(appdir):
    ah.touch_enabled()
    _post(SHA, "allow", sha256="0" * 64)
    out, _ = _run_hook(appdir, "Bash", TIN)
    assert out == ""
    assert os.path.exists(_decision_path(SHA))


def test_hook_ignores_and_removes_expired_decision(appdir):
    ah.touch_enabled()
    _post(SHA, "allow", expires=time.time() - 1)
    out, _ = _run_hook(appdir, "Bash", TIN)
    assert out == ""
    assert not os.path.exists(_decision_path(SHA))


def test_decision_ttl_is_120s():
    assert ah.DECISION_TTL_S == 120


def test_hook_falls_through_when_nobody_answers(appdir):
    ah.touch_enabled()
    out, dt = _run_hook(appdir, "Bash", TIN, wait="0.5")
    assert out == "" and 0.4 <= dt < 5
    assert not os.path.exists(os.path.join(
        ah.base_dir(), "waiting", f"{SID}.{SHA[:16]}.json"))


def test_hook_skips_interactive_question_tools(appdir):
    ah.touch_enabled()
    out, dt = _run_hook(appdir, "AskUserQuestion", {"questions": []}, wait="30")
    assert out == "" and dt < 5


def test_hook_picks_up_decision_posted_while_parked(appdir):
    ah.touch_enabled()
    env = dict(os.environ, CCC_APPROVAL_HOOK_DIR=str(appdir),
               CCC_APPROVAL_HOOK_WAIT="20")
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    proc.stdin.write(json.dumps({"session_id": SID, "tool_name": "Bash",
                                 "tool_input": TIN}))
    proc.stdin.close()
    for _ in range(100):
        if ah.waiting(SID, SHA):
            break
        time.sleep(0.05)
    assert ah.waiting(SID, SHA)
    assert ah.deliver(SID, SHA, "allow") == "applied"
    assert _out_decision(proc.stdout.read()) == "allow"
    proc.wait(timeout=10)


def _line(block, role="assistant"):
    return json.dumps({"type": role, "message": {"role": role,
                                                 "content": [block]}}) + "\n"


def _park(appdir, transcript, wait="20"):
    env = dict(os.environ, CCC_APPROVAL_HOOK_DIR=str(appdir),
               CCC_APPROVAL_HOOK_WAIT=wait)
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    proc.stdin.write(json.dumps({"session_id": SID, "tool_name": "Bash",
                                 "tool_input": TIN,
                                 "transcript_path": transcript}))
    proc.stdin.close()
    for _ in range(100):
        if ah.waiting(SID, SHA):
            break
        time.sleep(0.05)
    assert ah.waiting(SID, SHA)
    return proc


def test_hook_stops_parking_once_prompt_answered_by_hand(appdir, tmp_path):
    ah.touch_enabled()
    tr = tmp_path / "s.jsonl"
    tr.write_text(_line({"type": "tool_use", "id": "t9", "name": "Bash",
                         "input": TIN}))
    proc = _park(appdir, str(tr))
    t0 = time.time()
    with open(tr, "a") as f:
        f.write(_line({"type": "tool_result", "tool_use_id": "t9",
                       "content": "ok"}, role="user"))
    assert proc.stdout.read() == ""            # no decision emitted
    proc.wait(timeout=10)
    assert time.time() - t0 < 5
    assert not ah.waiting(SID, SHA)


def test_earlier_identical_resolved_call_does_not_end_parking(appdir, tmp_path):
    ah.touch_enabled()
    tr = tmp_path / "s.jsonl"
    tr.write_text(
        _line({"type": "tool_use", "id": "t1", "name": "Bash", "input": TIN})
        + _line({"type": "tool_result", "tool_use_id": "t1"}, role="user")
        + _line({"type": "tool_use", "id": "t2", "name": "Bash", "input": TIN}))
    proc = _park(appdir, str(tr))
    time.sleep(1.5)                            # past one resolved-check tick
    assert proc.poll() is None
    assert ah.deliver(SID, SHA, "allow") == "applied"
    assert _out_decision(proc.stdout.read()) == "allow"
    proc.wait(timeout=10)


# --- relay executor: hook first, keystroke only as fallback -------------------

class PermAPI:
    def __init__(self, rows):
        self.rows, self.posts = rows, []

    def get(self, path, query=None):
        return (200, {"sessions": self.rows}) if path == "/api/sessions" else (200, {})

    def post(self, path, body=None):
        self.posts.append((path, body))
        return 200, {"ok": True}

    def call(self, method, path, body=None, query=None, timeout=None):
        return self.get(path, query)


def _execute(tmp_path, api, payload):
    ex = cr.CommandExecutor(api, cr.IdempotencyStore(str(tmp_path / "i.db")))
    now = time.time()
    return ex.execute({"v": 1, "request_id": "r1", "seq": 1,
                       "capability": "approval.answer",
                       "created": cr._iso_from_epoch(now),
                       "expires": cr._iso_from_epoch(now + 300),
                       "payload": payload})


def _row(path):
    return {"id": SID, "session_id": SID, "state": "waiting", "is_live": True,
            "needs_approval": True, "pending_tool": "Bash", "jsonl_path": path,
            "mtime": time.time()}


def _payload(sha=SHA, **over):
    d = {"session_ref": SID, "decision": "allow", "action_sha256": sha,
         "hash_kind": "input"}
    d.update(over)
    return d


def _fake_waiting(sha):
    path = os.path.join(ah.base_dir(), "waiting", f"{SID}.{sha[:16]}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump({"sha256": sha}, open(path, "w"))


def test_no_parked_hook_uses_keystroke_fallback(tmp_path, appdir):
    api = PermAPI([_row(_transcript(tmp_path, "Bash", TIN))])
    res = _execute(tmp_path, api, _payload())
    assert res["status"] == "ok" and res["detail_code"] == "delivered"
    assert api.posts == [("/api/claude/permission",
                          {"session_id": SID, "decision": "accept"})]


def test_parked_hook_gets_decision_and_no_keystroke(tmp_path, appdir, monkeypatch):
    ah.touch_enabled()
    api = PermAPI([_row(_transcript(tmp_path, "Bash", TIN))])
    env = dict(os.environ, CCC_APPROVAL_HOOK_DIR=str(appdir),
               CCC_APPROVAL_HOOK_WAIT="20")
    proc = subprocess.Popen([sys.executable, HOOK], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    proc.stdin.write(json.dumps({"session_id": SID, "tool_name": "Bash",
                                 "tool_input": TIN}))
    proc.stdin.close()
    for _ in range(100):
        if ah.waiting(SID, SHA):
            break
        time.sleep(0.05)
    res = _execute(tmp_path, api, _payload())
    assert res["status"] == "ok" and res["detail_code"] == "hook"
    assert api.posts == []
    assert _out_decision(proc.stdout.read()) == "allow"
    proc.wait(timeout=10)


def test_unclaimed_decision_is_withdrawn_then_keystroke(tmp_path, appdir, monkeypatch):
    monkeypatch.setattr(ah, "ACK_WAIT_S", 0.3)
    api = PermAPI([_row(_transcript(tmp_path, "Bash", TIN))])
    _fake_waiting(SHA)                      # looks parked, but nobody claims
    res = _execute(tmp_path, api, _payload())
    assert res["detail_code"] == "delivered" and len(api.posts) == 1
    assert not os.path.exists(_decision_path(SHA))   # no stale decision left


def test_changed_input_is_rejected_before_any_delivery(tmp_path, appdir):
    api = PermAPI([_row(_transcript(tmp_path, "Bash", {"command": "make deploy"}))])
    _fake_waiting(SHA)
    res = _execute(tmp_path, api, _payload())
    assert res["status"] == "rejected" and res["error_code"] == "action_changed"
    assert api.posts == [] and not os.path.exists(_decision_path(SHA))


def test_legacy_text_hash_still_uses_keystroke_only(tmp_path, appdir):
    api = PermAPI([_row(_transcript(tmp_path, "Bash", {"command": "pytest -q"}))])
    sha = hashlib.sha256(b"Bash: pytest -q").hexdigest()
    res = _execute(tmp_path, api, {"session_ref": SID, "decision": "allow",
                                   "action_sha256": sha})
    assert res["status"] == "ok" and len(api.posts) == 1


def test_bad_hash_kind_is_invalid(tmp_path, appdir):
    api = PermAPI([_row(_transcript(tmp_path, "Bash", TIN))])
    res = _execute(tmp_path, api, _payload(hash_kind="weird"))
    assert res["status"] == "rejected" and res["error_code"] == "invalid_payload"
