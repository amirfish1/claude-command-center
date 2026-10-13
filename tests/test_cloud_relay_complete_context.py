"""Cloud relay: complete context for an external agent (full pending tool
input, full prose question, non-empty turns, plain folder labels). No server."""
import json
import os
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server import cloud_relay as cr
from ccc_server import relay_context as rc

SID = "11111111-2222-3333-4444-555555555555"


def _write(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(path)


def _tool_use(name, tin, tid="t1"):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tid, "name": name, "input": tin}]}}


def _text(role, text):
    return {"type": role, "message": {"role": role, "content": text}}


class API:
    def __init__(self, rows, items):
        self.rows, self.items = rows, items
        self.calls = []

    def get(self, path, query=None):
        self.calls.append((path, dict(query or {})))
        if path == "/api/sessions":
            return 200, {"sessions": self.rows}
        if path == "/api/attention":
            return 200, {"items": self.items}
        return 200, {"ok": True}

    def post(self, path, body=None):
        return 200, {"ok": True}

    def call(self, method, path, body=None, query=None, timeout=None):
        return self.get(path, query)


def _row(path, **over):
    r = {"id": SID, "session_id": SID, "display_name": "Ship it",
         "folder_label": "claude-command-center", "state": "waiting",
         "is_live": True, "mtime": time.time(), "jsonl_path": path}
    r.update(over)
    return r


def _item(kind):
    return {"kind": kind, "priority": 1, "session_id": SID, "mtime": time.time()}


# --- full pending tool input ------------------------------------------------

def test_full_bash_command_reaches_attention_action(tmp_path):
    cmd = "python3 - <<'PY'\n" + "print('step')\n" * 60 + "PY"
    p = _write(tmp_path / "s.jsonl", [_text("user", "go"),
                                      _tool_use("Bash", {"command": cmd})])
    api = API([_row(p, pending_tool="Bash", pending_file=cmd[:60],
                    needs_approval=True)], [_item("question_blocked")])
    it = cr.build_snapshot(api, {"share_titles": True})["attention"][0]
    assert it["kind"] == "approval"
    assert len(cmd) > 300 and "print('step')\nprint('step')" in it["action"]
    assert it["action"].endswith("PY") and len(it["action"]) <= cr.CAP_ACTION


def test_action_redacts_secrets_and_is_capped(tmp_path):
    cmd = "curl -H 'x: sk-ant-test-XXXX1234567890abcdef' a@b.com " + "y" * 5000
    p = _write(tmp_path / "s.jsonl", [_tool_use("Bash", {"command": cmd})])
    api = API([_row(p, pending_tool="Bash", needs_approval=True)],
              [_item("question_blocked")])
    act = cr.build_snapshot(api, {"share_titles": True})["attention"][0]["action"]
    assert "sk-ant-test" not in act and "a@b.com" not in act
    assert len(act) <= cr.CAP_ACTION


def test_edit_and_write_actions_name_the_file():
    e = rc.format_action("Edit", {"file_path": "src/app.py",
                                  "old_string": "a\nb", "new_string": "c"})
    assert e.startswith("Edit src/app.py") and "-2/+1 lines" in e
    w = rc.format_action("Write", {"file_path": "notes.md", "content": "x\ny\n"})
    assert w.startswith("Write notes.md (2 lines, 4 chars)")


def test_completed_tool_is_not_pending(tmp_path):
    p = _write(tmp_path / "s.jsonl", [
        _tool_use("Bash", {"command": "ls"}, "t9"),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t9", "content": "ok"}]}}])
    assert rc.last_pending_tool(p) is None


def test_detail_pending_approval_has_full_action(tmp_path):
    cmd = "agent-reach doctor 2>&1 | head -40; " + "rg -n foo . ; " * 30
    p = _write(tmp_path / "s.jsonl", [_text("user", "hi"),
                                      _tool_use("Bash", {"command": cmd})])
    ex = _executor(tmp_path, [_row(p, pending_tool="Bash", pending_file=cmd[:60],
                                   needs_approval=True)])
    d = ex.execute(_cmd(SID))["detail"]
    assert d["pending"]["type"] == "approval"
    assert d["pending"]["action"].count("rg -n foo") == 30


# --- prose questions --------------------------------------------------------

def test_prose_question_text_in_attention_and_detail(tmp_path):
    q = "Q8: should the build presence use the cloud relay or local only?\n" + "detail " * 200
    p = _write(tmp_path / "s.jsonl", [_text("user", "go"), _text("assistant", q)])
    row = _row(p)
    it = cr.build_snapshot(API([row], [_item("soft_block")]),
                           {"share_titles": True})["attention"][0]
    assert it["kind"] == "question"
    assert it["question_text"].startswith("Q8: should the build presence")
    assert len(it["question_text"]) > cr.CAP_SUMMARY
    d = _executor(tmp_path, [row]).execute(_cmd(SID))["detail"]
    assert d["pending"]["type"] == "question" and d["pending"]["options"] == []
    assert d["pending"]["text"].startswith("Q8: should the build presence")


def test_question_text_hidden_without_share_titles(tmp_path):
    p = _write(tmp_path / "s.jsonl", [_text("assistant", "Secret plan?")])
    it = cr.build_snapshot(API([_row(p)], [_item("soft_block")]),
                           {"share_titles": False})["attention"][0]
    assert "question_text" not in it


def test_last_assistant_text_cached_by_mtime_size(tmp_path, monkeypatch):
    p = _write(tmp_path / "s.jsonl", [_text("assistant", "Want me to proceed?")])
    calls = []
    real = rc.read_tail_lines
    monkeypatch.setattr(rc, "read_tail_lines",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    assert rc.last_assistant_text(p) == "Want me to proceed?"
    n = len(calls)
    assert rc.last_assistant_text(p) == "Want me to proceed?"
    assert len(calls) == n


def test_no_transcript_read_without_candidates(tmp_path, monkeypatch):
    p = _write(tmp_path / "s.jsonl", [_text("assistant", "hi")])
    monkeypatch.setattr(rc, "read_tail_lines",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("read")))
    api = API([_row(p, state="working")], [])
    cr.build_snapshot(api, {"share_titles": True})
    assert [c[0] for c in api.calls] == ["/api/sessions", "/api/attention",
                                         "/api/ux-fixes/health"]


# --- empty turns ------------------------------------------------------------

def test_tool_heavy_tail_still_returns_turns(tmp_path):
    big = "z" * 90_000
    events = [_text("user", "start"), _text("assistant", "working on it")]
    for i in range(3):
        events.append(_tool_use("Bash", {"command": "x"}, f"t{i}"))
        events.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": big}]}})
    p = _write(tmp_path / "s.jsonl", events)
    turns = cr.tail_read_turns(p)
    assert [t["text"] for t in turns][:2] == ["start", "working on it"]


def test_codex_response_items_become_turns(tmp_path):
    ev = {"type": "response_item", "payload": {
        "type": "message", "role": "assistant",
        "content": [{"type": "output_text", "text": "Approved."}]}}
    p = _write(tmp_path / "c.jsonl", [{"type": "session_meta", "payload": {}}, ev])
    assert cr.tail_read_turns(p) == [{"role": "assistant", "text": "Approved."}]


def test_detail_turn_count_and_empty_subagent_falls_back(tmp_path):
    parent = _write(tmp_path / "p.jsonl", [_text("user", "hi"), _text("assistant", "yo")])
    agent = _write(tmp_path / "a.jsonl", [_tool_use("Bash", {"command": "ls"})])
    ref = f"{SID}:agent-abc"
    rows = [_row(parent),
            _row(agent, id=ref, session_id="agent-abc", parent_session_id=SID,
                 state="idle")]
    d = _executor(tmp_path, rows).execute(_cmd(ref))["detail"]
    assert d["turn_count"] == len(d["turns"]) == 2
    assert d["fallback"] == "parent"
    d2 = _executor(tmp_path, rows).execute(_cmd(SID, "d-2"))["detail"]
    assert d2["turn_count"] == 2


# --- labels -----------------------------------------------------------------

def test_plain_folder_labels_are_not_redacted():
    for name in ("claude-command-center", "ccc-cloud-wt-instinct", "my_app_v2",
                 "Kokoro-Local-Voice-2"):
        assert cr.label_cap(name, cr.CAP_LABEL) == name


def test_real_secrets_in_labels_still_redacted():
    for bad in ("sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
                "ghp_abcdEFGH1234ijklMNOP5678", "/Users/bob/proj/x",
                "bob@example.com", "deadbeefdeadbeefdeadbeef"):
        assert "[redacted]" in cr.label_cap(bad, cr.CAP_LABEL), bad


def test_snapshot_keeps_repo_label(tmp_path):
    p = _write(tmp_path / "s.jsonl", [_text("assistant", "hi")])
    snap = cr.build_snapshot(API([_row(p, state="idle")], []),
                             {"share_titles": True})
    assert snap["sessions"][0]["repo_label"] == "claude-command-center"


# --- helpers ----------------------------------------------------------------

def _executor(tmp_path, rows):
    store = cr.IdempotencyStore(str(tmp_path / "i.db"))
    return cr.CommandExecutor(API(rows, []), store, share_titles_fn=lambda: True)


def _cmd(ref, rid="d-1"):
    now = time.time()
    return {"v": 1, "request_id": rid, "seq": 1, "capability": "session.detail",
            "created": cr._iso_from_epoch(now),
            "expires": cr._iso_from_epoch(now + 300),
            "payload": {"session_ref": ref}}
