"""Cloud relay: approval.answer executes through the dashboard's permission
path only when the pending prompt still matches the action the relay judged.
No server."""
import hashlib
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server import cloud_relay as cr

SID = "11111111-2222-3333-4444-555555555555"


def _transcript(tmp_path, cmd):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash",
             "input": {"command": cmd}}]}}) + "\n")
    return str(p)


class API:
    def __init__(self, rows, perm=(200, {"ok": True})):
        self.rows, self.perm, self.posts = rows, perm, []

    def get(self, path, query=None):
        if path == "/api/sessions":
            return 200, {"sessions": self.rows}
        return 200, {"ok": True}

    def post(self, path, body=None):
        self.posts.append((path, body))
        return self.perm

    def call(self, method, path, body=None, query=None, timeout=None):
        return self.get(path, query)


def _row(path, **over):
    r = {"id": SID, "session_id": SID, "state": "waiting", "is_live": True,
         "needs_approval": True, "pending_tool": "Bash", "jsonl_path": path,
         "mtime": time.time()}
    r.update(over)
    return r


def _run(tmp_path, api, payload, rid="aa-1"):
    store = cr.IdempotencyStore(str(tmp_path / "i.db"))
    ex = cr.CommandExecutor(api, store)
    now = time.time()
    env = {"v": 1, "request_id": rid, "seq": 1, "capability": "approval.answer",
           "created": cr._iso_from_epoch(now),
           "expires": cr._iso_from_epoch(now + 300), "payload": payload}
    return ex.execute(env)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_capability_is_advertised_and_allowed():
    assert "approval.answer" in cr.ALLOWED_CAPABILITIES
    assert "approval_answer" in cr.DEVICE_CAPABILITIES


def test_matching_action_is_answered_via_dashboard_path(tmp_path):
    p = _transcript(tmp_path, "pytest -q")
    api = API([_row(p)])
    res = _run(tmp_path, api, {"session_ref": SID, "decision": "allow",
                               "action_sha256": _sha("Bash: pytest -q")})
    assert res["status"] == "ok", res
    assert api.posts == [("/api/claude/permission",
                          {"session_id": SID, "decision": "accept"})]


def test_deny_maps_to_decline(tmp_path):
    p = _transcript(tmp_path, "pytest -q")
    api = API([_row(p)])
    res = _run(tmp_path, api, {"session_ref": SID, "decision": "deny",
                               "action_sha256": _sha("Bash: pytest -q")})
    assert res["status"] == "ok"
    assert api.posts[0][1]["decision"] == "decline"


def test_hash_mismatch_is_action_changed_and_no_keystroke(tmp_path):
    p = _transcript(tmp_path, "make deploy")      # prompt changed meanwhile
    api = API([_row(p)])
    res = _run(tmp_path, api, {"session_ref": SID, "decision": "allow",
                               "action_sha256": _sha("Bash: pytest -q")})
    assert res["status"] == "rejected" and res["error_code"] == "action_changed"
    assert api.posts == []


def test_no_pending_prompt_is_rejected(tmp_path):
    p = _transcript(tmp_path, "pytest -q")
    api = API([_row(p, needs_approval=False)])
    res = _run(tmp_path, api, {"session_ref": SID, "decision": "allow",
                               "action_sha256": _sha("Bash: pytest -q")})
    assert res["status"] == "rejected"
    assert res["error_code"] == "no_pending_approval"
    assert api.posts == []


def test_unsupported_platform_is_rejected_unsupported(tmp_path):
    p = _transcript(tmp_path, "pytest -q")
    api = API([_row(p)], perm=(501, {"ok": False, "code": "macos_only",
                                     "error": "macOS only"}))
    res = _run(tmp_path, api, {"session_ref": SID, "decision": "allow",
                               "action_sha256": _sha("Bash: pytest -q")})
    assert res["status"] == "rejected" and res["error_code"] == "unsupported"
    assert res["detail_code"] == "macos_only"


def test_not_live_tty_is_rejected(tmp_path):
    p = _transcript(tmp_path, "pytest -q")
    api = API([_row(p)], perm=(409, {"ok": False, "code": "not_live_tty"}))
    res = _run(tmp_path, api, {"session_ref": SID, "decision": "allow",
                               "action_sha256": _sha("Bash: pytest -q")})
    assert res["status"] == "rejected" and res["error_code"] == "not_live_tty"


def test_subagent_and_non_claude_are_unsupported(tmp_path):
    p = _transcript(tmp_path, "pytest -q")
    sha = _sha("Bash: pytest -q")
    api = API([_row(p)])
    res = _run(tmp_path, api, {"session_ref": SID + ":agent-abc123",
                               "decision": "allow", "action_sha256": sha})
    assert res["error_code"] == "unsupported"
    api2 = API([_row(p, engine="codex")])
    res = _run(tmp_path, api2, {"session_ref": SID, "decision": "allow",
                                "action_sha256": sha}, rid="aa-2")
    assert res["error_code"] == "unsupported"
    assert api.posts == [] and api2.posts == []


def test_bad_payload_is_rejected_before_any_lookup(tmp_path):
    api = API([])
    for i, pl in enumerate((
            {"session_ref": SID, "decision": "allow"},                 # no hash
            {"session_ref": SID, "decision": "maybe", "action_sha256": "0" * 64},
            {"session_ref": SID, "decision": "allow", "action_sha256": "zz"})):
        res = _run(tmp_path, api, pl, rid=f"bad-{i}")
        assert res["status"] == "rejected"
        assert res["error_code"] == "invalid_payload"
    assert api.posts == []


def test_redaction_matches_what_the_snapshot_sent(tmp_path):
    secret = "sk-ant-test-" + "A" * 30
    p = _transcript(tmp_path, f"echo {secret}")
    shown = cr.redact_cap("Bash: echo " + secret, cr.CAP_ACTION)
    api = API([_row(p)])
    res = _run(tmp_path, api, {"session_ref": SID, "decision": "allow",
                               "action_sha256": _sha(shown)})
    assert res["status"] == "ok"
