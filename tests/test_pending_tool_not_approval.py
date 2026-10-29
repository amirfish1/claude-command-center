"""A running tool is not a permission prompt (false "paused on Bash")."""
import importlib
import os
import sys
import uuid

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server.approval_evidence import pending_tool_is_approval

server = importlib.import_module("server")


def _row(**over):
    r = {
        "session_id": str(uuid.uuid4()), "source": "interactive",
        "engine": "claude", "is_live": True, "last_event_type": "assistant",
        "pending_tool": "Bash", "pending_file": "make test-all",
        "subagent_in_flight_count": 0, "sidecar_in_flight": False,
        "sidecar_status": None, "question_waiting": False,
        "needs_approval": False, "last_assistant_text": "",
        "session_state": {},
    }
    r.update(over)
    return r


def test_long_running_claude_tool_is_not_an_approval():
    item = server._classify_attention(_row())
    assert item is None or item["kind"] != "pending_tool"
    assert "approval" not in str((item or {}).get("where", "")).lower()


def test_real_permission_prompt_still_reported():
    item = server._classify_attention(_row(needs_approval=True))
    assert item and item["priority"] == 1
    assert "approval" in item["where"].lower()


def test_session_state_is_working_while_tool_runs():
    assert server._session_state_label(_row()) == "working"


def test_engines_without_a_hook_keep_the_heuristic():
    assert pending_tool_is_approval({"engine": "codex"}) is True
    assert pending_tool_is_approval({"engine": "claude"}) is False
    assert pending_tool_is_approval({"engine": "claude", "needs_approval": True})
    assert pending_tool_is_approval({"source": "interactive"}) is False
