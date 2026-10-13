"""Attention items carry sub-agent / approval context; session.detail returns
title, parent, state, pending approval and recent turns. No server boot."""
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server import cloud_relay as cr

PARENT = "11111111-2222-3333-4444-555555555555"
AGENT_BARE = "agent-abc123"
AGENT_REF = f"{PARENT}:{AGENT_BARE}"


def _rows(tmp_path, parent_live=True):
    pj = tmp_path / "parent.jsonl"
    pj.write_text(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": "parent turn"}}) + "\n")
    aj = tmp_path / "agent.jsonl"
    aj.write_text(json.dumps({"type": "assistant", "message": {
        "role": "assistant", "content": "agent turn"}}) + "\n")
    return [
        {"id": PARENT, "session_id": PARENT, "display_name": "Ship the relay",
         "folder_label": "demo-repo", "state": "working" if parent_live else "idle",
         "is_live": parent_live, "mtime": time.time(), "jsonl_path": str(pj)},
        {"id": AGENT_REF, "session_id": AGENT_BARE, "display_name": "Commit and rebase",
         "state": "waiting", "is_live": True, "mtime": time.time(),
         "parent_session_id": PARENT, "pending_tool": "Bash", "needs_approval": True,
         "pending_file": "git push origin main", "jsonl_path": str(aj)},
    ]


class API:
    def __init__(self, rows):
        self.rows = rows

    def get(self, path, query=None):
        if path == "/api/sessions":
            return 200, {"sessions": self.rows}
        if path == "/api/attention":
            return 200, {"items": [{
                "kind": "pending_tool", "priority": 1, "session_id": AGENT_BARE,
                "next_step": "Jump to terminal - Claude paused on Bash",
                "mtime": time.time()}]}
        return 200, {"ok": True}

    def post(self, path, body=None):
        return 200, {"ok": True}

    def call(self, method, path, body=None, query=None, timeout=None):
        return self.get(path, query)


def test_subagent_attention_item_has_parent_and_action(tmp_path):
    snap = cr.build_snapshot(API(_rows(tmp_path)), {"share_titles": True})
    it = snap["attention"][0]
    assert it["kind"] == "approval"
    assert it["session_ref"] == AGENT_REF
    assert it["subagent"] is True
    assert it["parent_ref"] == PARENT
    assert it["parent_title"] == "Ship the relay"
    assert it["agent_label"] == "Commit and rebase"
    assert it["title"] == "Commit and rebase (sub-agent of Ship the relay)"
    assert it["action"] == "Bash: git push origin main"
    assert it["repo_label"] == "demo-repo"
    assert it["covered_by_parent"] is True


def test_covered_by_parent_false_when_parent_idle(tmp_path):
    snap = cr.build_snapshot(API(_rows(tmp_path, parent_live=False)),
                             {"share_titles": True})
    assert snap["attention"][0]["covered_by_parent"] is False


def test_share_titles_off_hides_text_fields(tmp_path):
    snap = cr.build_snapshot(API(_rows(tmp_path)), {"share_titles": False})
    it = snap["attention"][0]
    blob = json.dumps(it)
    for hidden in ("Ship the relay", "Commit and rebase", "git push", "demo-repo"):
        assert hidden not in blob
    assert "action" not in it and "agent_label" not in it
    assert "parent_title" not in it and "repo_label" not in it
    assert it["subagent"] is True and it["parent_ref"] == PARENT


def test_action_is_redacted_and_capped(tmp_path):
    rows = _rows(tmp_path)
    rows[1]["pending_file"] = "curl -d bob@example.com " + "x" * 900
    it = cr.build_snapshot(API(rows), {"share_titles": True})["attention"][0]
    assert "bob@example.com" not in it["action"]
    assert len(it["action"]) <= cr.CAP_ACTION


def test_plain_session_item_unchanged(tmp_path):
    class A(API):
        def get(self, path, query=None):
            if path == "/api/attention":
                return 200, {"items": [{"kind": "soft_block", "priority": 2,
                                        "session_id": PARENT, "mtime": time.time()}]}
            return super().get(path, query)
    it = cr.build_snapshot(A(_rows(tmp_path)), {"share_titles": True})["attention"][0]
    for k in ("subagent", "parent_ref", "action", "covered_by_parent"):
        assert k not in it


def _ex(tmp_path, rows, share=True):
    store = cr.IdempotencyStore(str(tmp_path / "i.db"))
    return cr.CommandExecutor(API(rows), store, share_titles_fn=lambda: share)


def _cmd(ref, rid="d-1"):
    now = time.time()
    return {"v": 1, "request_id": rid, "seq": 1, "capability": "session.detail",
            "created": cr._iso_from_epoch(now),
            "expires": cr._iso_from_epoch(now + 300),
            "payload": {"session_ref": ref}}


def test_detail_for_subagent_has_context_and_turns(tmp_path):
    r = _ex(tmp_path, _rows(tmp_path)).execute(_cmd(AGENT_REF))
    d = r["detail"]
    assert r["status"] == "ok"
    assert d["parent_ref"] == PARENT and d["subagent"] is True
    assert d["title"] == "Commit and rebase (sub-agent of Ship the relay)"
    assert d["pending"] == {"type": "approval", "tool": "Bash",
                            "action": "Bash: git push origin main"}
    assert d["turns"][-1]["text"] == "agent turn"
    assert d["state"] == "waiting"


def test_detail_subagent_falls_back_to_parent_turns(tmp_path):
    rows = _rows(tmp_path)
    rows[1]["jsonl_path"] = None
    d = _ex(tmp_path, rows).execute(_cmd(AGENT_REF))["detail"]
    assert d["fallback"] == "parent"
    assert d["turns"][-1]["text"] == "parent turn"
    assert d["agent_label"] == "Commit and rebase"


def test_detail_question_with_options_and_opaque_mode(tmp_path):
    rows = _rows(tmp_path)
    rows[0].update(question_waiting=True, question_text="Ship it?",
                   question_options=["Yes", "No"])
    d = _ex(tmp_path, rows).execute(_cmd(PARENT))["detail"]
    assert d["pending"]["type"] == "question"
    assert [o["label"] for o in d["pending"]["options"]] == ["Yes", "No"]
    o = _ex(tmp_path, rows, share=False).execute(_cmd(PARENT, "d-2"))["detail"]
    assert "Ship it?" not in json.dumps(o)
    assert "Ship the relay" not in json.dumps(o)
