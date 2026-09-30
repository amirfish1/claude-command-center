"""Unit coverage for hooks/post-compact-codex.py's re-orientation block
(MEMO-FIX-16). Fast, standalone — imports the hook module directly, no
server, no subprocess. Only test_prints_via_stdin and
test_never_fails_the_turn_on_bad_input exercise the real process so the
"never fail the turn" and timing behavior get checked end to end.

Fixture shapes mirror the real Codex rollout record layout used elsewhere in
this repo's tests (see tests/test_codex_compact_lifecycle.py) and in
ccc_server/codex_parse.py's `_parse_codex_event`: no session content is
copied in.
"""

import importlib.util
import json
import pathlib
import subprocess
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
HOOK_PATH = REPO_ROOT / "hooks" / "post-compact-codex.py"

spec = importlib.util.spec_from_file_location("ccc_post_compact_codex_hook", str(HOOK_PATH))
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


def _user_message(text):
    return {"type": "event_msg", "payload": {"type": "user_message", "message": text}}


def _item_completed_user_message(text):
    return {"type": "event_msg", "payload": {"type": "item_completed", "item": {
        "type": "UserMessage",
        "content": [{"type": "text", "text": text}],
    }}}


def _agent_message(text):
    """Not a user ask — must never be picked up as one."""
    return {"type": "event_msg", "payload": {"type": "agent_message", "message": text}}


def _tool_output_json(obj, *, custom=False):
    ptype = "custom_tool_call_output" if custom else "function_call_output"
    return {"type": "response_item", "payload": {"type": ptype, "output": json.dumps(obj)}}


def _tool_output_list_blocks(obj):
    """The structured-output shape (list of {"type": "text"|"input_image", ...})."""
    return {"type": "response_item", "payload": {
        "type": "function_call_output",
        "output": [{"type": "text", "text": json.dumps(obj)}],
    }}


def _jsonl(records):
    return "\n".join(json.dumps(r) for r in records) + "\n"


def test_ticket_ref_from_classic_user_message():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(
        _jsonl([_user_message("You are the worker for WatchTower ticket MEMO-FIX-16.")]),
        state,
    )
    assert state["ticket_ref"] == "MEMO-FIX-16"
    assert state["asks"] == ["You are the worker for WatchTower ticket MEMO-FIX-16."]


def test_ticket_ref_from_item_completed_user_message():
    """Newer (multi-agent-capable) rollouts wrap user text in an item_completed
    UserMessage instead of the classic flat user_message event."""
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(_jsonl([_item_completed_user_message("claim WT-48 please")]), state)
    assert state["ticket_ref"] == "WT-48"
    assert state["asks"] == ["claim WT-48 please"]


def test_agent_message_is_not_treated_as_an_ask():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(_jsonl([_agent_message("sure, on it")]), state)
    assert state["asks"] == []


def test_tool_output_json_supplies_title_and_ref():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(
        _jsonl([_tool_output_json({"ref": "OPS-1246", "title": "Fix the thing"})]),
        state,
    )
    assert state["ticket_ref"] == "OPS-1246"
    assert state["ticket_title"] == "Fix the thing"


def test_custom_tool_output_json_also_supplies_ref():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(
        _jsonl([_tool_output_json({"ref": "OPS-2", "title": "Custom tool ref"}, custom=True)]),
        state,
    )
    assert state["ticket_ref"] == "OPS-2"
    assert state["ticket_title"] == "Custom tool ref"


def test_tool_output_structured_list_blocks_parse_too():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(
        _jsonl([_tool_output_list_blocks({"ref": "MEMO-FIX-16", "title": "Codex hook parity"})]),
        state,
    )
    assert state["ticket_ref"] == "MEMO-FIX-16"
    assert state["ticket_title"] == "Codex hook parity"


def test_tool_output_without_title_is_ignored():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(_jsonl([_tool_output_json({"ref": "OPS-1246"})]), state)
    assert state["ticket_ref"] == ""


def test_tool_output_wins_over_earlier_text_ref():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(
        _jsonl([
            _user_message("ticket MEMO-FIX-16 please"),
            _tool_output_json({"ref": "MEMO-FIX-16", "title": "Codex hook parity"}),
        ]),
        state,
    )
    assert state["ticket_ref"] == "MEMO-FIX-16"
    assert state["ticket_title"] == "Codex hook parity"


def test_asks_skip_injected_notifications():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(_jsonl([
        _user_message("fix the flaky test"),
        _user_message("[watchtower] WT-9 claimed"),
        _user_message("[SYSTEM NOTIFICATION - NOT USER INPUT] task done"),
    ]), state)
    assert state["asks"] == ["fix the flaky test"]
    assert state["ticket_ref"] == ""


def test_asks_keeps_last_three():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(_jsonl([_user_message(f"ask {i}") for i in range(5)]), state)
    assert state["asks"][-3:] == ["ask 2", "ask 3", "ask 4"]


def test_continuation_origin_marker_is_captured_and_excluded_from_asks():
    state = {"asks": [], "ticket_ref": "", "ticket_title": "", "continued_from": ""}
    hook._scan_into(_jsonl([
        _user_message(
            "You are continuing a task from an earlier Codex session, "
            "which ran long.\n\nOrigin session id: 93580c29-29f9-4ce3-8077-db00ea0a920f\n"
            "Task: Continue the work from where it left off."
        ),
        _user_message("keep going on the relaunch"),
    ]), state)
    assert state["continued_from"] == "93580c29-29f9-4ce3-8077-db00ea0a920f"
    assert state["asks"] == ["keep going on the relaunch"]


def test_build_block_includes_continued_from_line():
    from _reorient_shared import build_block
    block = build_block(["do the thing"], "", "", "abc12345")
    assert "Continued from: abc12345" in block


def test_malformed_tool_output_json_is_skipped_not_raised():
    state = {"asks": [], "ticket_ref": "", "ticket_title": ""}
    hook._scan_into(
        _jsonl([{"type": "response_item", "payload": {
            "type": "function_call_output", "output": "not json",
        }}]),
        state,
    )
    assert state["ticket_ref"] == ""


def test_prints_via_stdin(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(_jsonl([
        _user_message("You are the worker for WatchTower ticket MEMO-FIX-16."),
        _tool_output_json({"ref": "MEMO-FIX-16", "title": "Codex hook parity"}),
    ]))
    # Real PostCompact hook input schema (post-compact.command.input.schema.json):
    # session_id, transcript_path (nullable), trigger, cwd, model, turn_id.
    payload = json.dumps({
        "hook_event_name": "PostCompact",
        "session_id": "abc123",
        "transcript_path": str(rollout),
        "trigger": "auto",
        "cwd": str(tmp_path),
        "model": "gpt-5.6-sol",
        "turn_id": "turn-1",
    })

    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload, capture_output=True, text=True, timeout=5,
    )
    elapsed = time.time() - t0

    assert proc.returncode == 0
    assert elapsed < 3.0  # "exits fast"; 1s flaked on loaded runners (interpreter startup)
    assert "MEMO-FIX-16" in proc.stdout
    assert "Codex hook parity" in proc.stdout
    assert "ccc recall" in proc.stdout
    assert len(proc.stdout) <= hook.MAX_BLOCK_CHARS + 1  # trailing newline from print()


def test_prints_continued_from_via_stdin(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(_jsonl([
        _user_message(
            "You are continuing a task from an earlier session, which ran long.\n\n"
            "Origin session id: 93580c29-29f9-4ce3-8077-db00ea0a920f\n"
            "Task: Continue the work from where it left off."
        ),
        _user_message("finish the relaunch runbook"),
    ]))
    payload = json.dumps({
        "hook_event_name": "PostCompact",
        "session_id": "abc123",
        "transcript_path": str(rollout),
        "trigger": "auto",
        "cwd": str(tmp_path),
        "model": "gpt-5.6-sol",
        "turn_id": "turn-1",
    })

    proc = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload, capture_output=True, text=True, timeout=5,
    )

    assert proc.returncode == 0
    assert "Continued from: 93580c29-29f9-4ce3-8077-db00ea0a920f" in proc.stdout


def test_never_fails_the_turn_on_bad_input():
    for bad_input in (
        "", "not json", "{}", '{"session_id": "x"}',
        json.dumps({"session_id": "x", "transcript_path": None, "trigger": "auto"}),
        json.dumps({"session_id": "x", "transcript_path": "/no/such/file.jsonl", "trigger": "manual"}),
    ):
        proc = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=bad_input, capture_output=True, text=True, timeout=5,
        )
        assert proc.returncode == 0, bad_input
        assert proc.stdout == ""
