"""thread/resume on a multi-agent v2 sub-agent loads its parent first.

Codex rejects resuming an unloaded sub-agent thread with "resume the parent
first". Before this, every send/steer to such a thread bounced back to the
durable queue forever (the queued-steer tray "walked back" the message on
each click). `_codex_app_server_request` now repairs that rejection itself.
"""
import importlib
import json
from unittest import mock

server = importlib.import_module("server")
from ccc_server import codex  # noqa: E402

PARENT = "01a15061-9614-7641-9ac3-307c330e9069"
CHILD = "01a1506a-22be-7302-9104-292b6b13e750"
GRANDCHILD = "01a1506b-0000-7000-8000-000000000001"

SUBAGENT_ERR = {"error": {
    "code": -32600,
    "message": "cannot resume an unloaded multi-agent v2 sub-agent through its "
               "parent; resume the parent first, or use thread/read to inspect it",
}}
OK = {"result": {"thread": {"id": "x", "status": {"type": "idle"}}}}


class FakeDaemon:
    """Resumes a thread only once its parent is loaded."""

    def __init__(self, parents):
        self.parents = parents
        self.loaded = set()
        self.calls = []

    def __call__(self, method, params=None, timeout=20, **_kw):
        tid = (params or {}).get("threadId")
        self.calls.append((method, tid))
        parent = self.parents.get(tid)
        if parent and parent not in self.loaded:
            return SUBAGENT_ERR
        self.loaded.add(tid)
        return OK


def _run(parents, thread_id):
    daemon = FakeDaemon(parents)
    with mock.patch.object(codex, "_codex_app_server_request_once", side_effect=daemon), \
         mock.patch.object(codex, "_codex_thread_parent_id", side_effect=lambda t: parents.get(t, "")), \
         mock.patch.object(server, "_resume_ledger_append", create=True):
        resp = codex._codex_app_server_request("thread/resume", {"threadId": thread_id})
    return resp, daemon


def test_subagent_resume_loads_parent_then_retries():
    resp, daemon = _run({CHILD: PARENT}, CHILD)
    assert resp == OK
    assert daemon.calls == [
        ("thread/resume", CHILD),
        ("thread/resume", PARENT),
        ("thread/resume", CHILD),
    ]


def test_nested_subagent_loads_whole_chain_top_down():
    resp, daemon = _run({GRANDCHILD: CHILD, CHILD: PARENT}, GRANDCHILD)
    assert resp == OK
    assert daemon.calls == [
        ("thread/resume", GRANDCHILD),
        ("thread/resume", CHILD),
        ("thread/resume", PARENT),
        ("thread/resume", CHILD),
        ("thread/resume", GRANDCHILD),
    ]


def test_unknown_parent_returns_original_rejection():
    daemon = FakeDaemon({CHILD: PARENT})
    with mock.patch.object(codex, "_codex_app_server_request_once", side_effect=daemon), \
         mock.patch.object(codex, "_codex_thread_parent_id", return_value=""):
        resp = codex._codex_app_server_request("thread/resume", {"threadId": CHILD})
    assert resp == SUBAGENT_ERR
    assert daemon.calls == [("thread/resume", CHILD)]


def test_other_methods_and_errors_untouched():
    calls = []

    def once(method, params=None, timeout=20, **_kw):
        calls.append(method)
        return SUBAGENT_ERR if method == "turn/start" else {"error": {"message": "thread not found"}}

    with mock.patch.object(codex, "_codex_app_server_request_once", side_effect=once):
        codex._codex_app_server_request("turn/start", {"threadId": CHILD})
        codex._codex_app_server_request("thread/resume", {"threadId": CHILD})
    assert calls == ["turn/start", "thread/resume"]


def test_parent_id_read_from_rollout_session_meta(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    meta = {"type": "session_meta", "payload": {
        "id": CHILD,
        "source": {"subagent": {"thread_spawn": {"parent_thread_id": PARENT, "depth": 1}}},
    }}
    rollout.write_text(json.dumps(meta) + "\n")
    with mock.patch.object(server, "_resolve_codex_rollout_path", return_value=rollout), \
         mock.patch.object(codex, "_codex_spawn_parent_by_child", return_value={}):
        assert codex._codex_thread_parent_id(CHILD) == PARENT

    meta["payload"] = {"id": CHILD, "parent_thread_id": PARENT, "source": "vscode"}
    rollout.write_text(json.dumps(meta) + "\n")
    with mock.patch.object(server, "_resolve_codex_rollout_path", return_value=rollout):
        assert codex._codex_thread_parent_id(CHILD) == PARENT


def test_parent_id_falls_back_to_spawn_edges():
    with mock.patch.object(server, "_resolve_codex_rollout_path", return_value=None), \
         mock.patch.object(codex, "_codex_spawn_parent_by_child", return_value={CHILD: PARENT}):
        assert codex._codex_thread_parent_id(CHILD) == PARENT


# --- relay: Codex refuses direct input to v2 sub-agents; go via the parent ---

from ccc_server import queue_events  # noqa: E402

FORBIDDEN = {
    "ok": False, "via": "codex-app-server", "stage": "turn/start", "fallback": "queue",
    "error": "direct app-server input is not allowed for multi-agent v2 sub-agents",
}
META = {"parent": PARENT, "agent_path": "/root/turn_count", "nickname": "Hooke"}


def _relay(steer, result, parent_replies):
    calls = []

    def fake_resume(sid, text, **kw):
        calls.append((sid, text, kw))
        return parent_replies.pop(0)

    with mock.patch.object(server, "resume_session_codex", side_effect=fake_resume), \
         mock.patch.object(server, "_codex_subagent_meta", return_value=dict(META)), \
         mock.patch.object(server, "_resume_ledger_append", create=True):
        out = queue_events._codex_relay_subagent_input(CHILD, "hello there", steer=steer, result=result)
    return out, calls


def test_relay_ignores_other_errors():
    out, calls = _relay(False, {"ok": False, "error": "thread not found"}, [])
    assert out is None and calls == []


def test_relay_steers_active_parent():
    out, calls = _relay(True, FORBIDDEN, [{"ok": True, "via": "codex-steer", "turn_id": "t1"}])
    assert out["ok"] and out["via"] == "codex-steer"
    assert out["relayed_via_parent"] == PARENT
    assert out["relay_agent_path"] == "/root/turn_count"
    (sid, text, kw), = calls
    assert sid == PARENT and kw == {"steer": True, "_native_delivery": True}
    assert "/root/turn_count" in text and "followup_task" in text
    assert text.endswith("hello there")


def test_relay_starts_turn_on_idle_parent_and_commits_steer():
    out, calls = _relay(True, FORBIDDEN, [
        {"ok": False, "via": "codex-steer", "code": "codex_no_active_turn"},
        {"ok": True, "via": "codex-app-turn", "turn_id": "t2"},
    ])
    # The queued-steer transaction only commits "codex-steer" results.
    assert out["ok"] and out["via"] == "codex-steer" and out["relay_via"] == "codex-app-turn"
    assert calls[1][0] == PARENT
    # Never append to the parent's durable queue.
    assert calls[1][2] == {"steer": False, "_native_delivery": True, "_from_queue": True}


def test_relay_busy_parent_reports_queued_without_committing():
    out, _calls = _relay(False, FORBIDDEN, [
        {"ok": False, "via": "codex-steer", "code": "codex_steer_failed"},
        {"ok": True, "queued": True, "via": "codex-resume-queued", "queued_reason": "busy"},
    ])
    assert out["queued"] and "relay_via" not in out
    assert out["via"] == "codex-resume-queued"


def test_relay_without_parent_returns_none():
    with mock.patch.object(server, "_codex_subagent_meta", return_value={"parent": "", "agent_path": "", "nickname": ""}), \
         mock.patch.object(server, "_codex_thread_parent_id", return_value=""), \
         mock.patch.object(server, "resume_session_codex") as resume:
        assert queue_events._codex_relay_subagent_input(CHILD, "x", steer=False, result=FORBIDDEN) is None
    resume.assert_not_called()


def test_forbidden_error_detection():
    assert codex._codex_error_is_subagent_input_forbidden(FORBIDDEN)
    assert codex._codex_error_is_subagent_input_forbidden(SUBAGENT_ERR)
    assert codex._codex_error_is_subagent_input_forbidden(
        {"error": "cannot resume multi-agent v2 child x: parent execution policy has changed; retry through the parent"})
    assert not codex._codex_error_is_subagent_input_forbidden({"error": "thread not found"})
