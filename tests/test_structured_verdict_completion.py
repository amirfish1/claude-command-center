"""A format-constrained verdict is a delivered response, not a coding task."""
import json

import pytest
import server
from ccc_server.session_graph import _session_state_from_tail_meta


PROMPT = 'Assess this supplied digest. Return ONLY a JSON object with your verdict.'
REPLY = '{"stuck": false, "confidence": 0.35, "reason": "The workflow is progressing."}'


def transcript(tmp_path, extra=(), stop="end_turn"):
    records = [
        {"type": "user", "message": {"content": PROMPT}},
        *extra,
        {"type": "assistant", "message": {"stop_reason": stop,
            "content": [{"type": "text", "text": REPLY}]}},
    ]
    target = tmp_path / "verdict.jsonl"
    target.write_text("\n".join(json.dumps(r, separators=(',', ':')) for r in records))
    return target


def test_completed_json_verdict_survives_a_metadata_cache_hit(tmp_path):
    target = transcript(tmp_path)
    meta = server._extract_tail_meta(target)
    assert _session_state_from_tail_meta(meta)["response_complete"] is True
    assert server._extract_tail_meta(target) is meta
    # Persisted older caches are migrated only for JSON-looking responses.
    meta.pop("turn_has_tools")
    assert _session_state_from_tail_meta(server._extract_tail_meta(target))["response_complete"] is True


@pytest.mark.parametrize("patch", [
    {"last_prompt": "Fix the failing test."},
    {"last_prompt": "Return ONLY JSON describing the patch."},
    {"last_prompt": "Implement the fix, then return a JSON assessment."},
    {"last_event_type": "user"}, {"last_assistant_stop_reason": None},
    {"last_assistant_stop_reason": "max_tokens"}, {"turn_has_tools": True},
    {"last_assistant_text": '{"stuck":'}, {"last_assistant_text": "null"},
    {"last_assistant_text": '{}'}, {"last_assistant_text": '{"error":"failed"}'},
    {"last_assistant_text": '```json\n' + REPLY + '\n```'},
    {"goal": "Repair the workflow", "goal_status": "active"},
    {"last_api_error": "authentication_failed"},
])
def test_no_completion_for_unfinished_or_ordinary_work(tmp_path, patch):
    meta = dict(server._extract_tail_meta(transcript(tmp_path)), **patch)
    assert _session_state_from_tail_meta(meta) is None


def test_tool_results_do_not_hide_work_in_this_turn(tmp_path):
    target = transcript(tmp_path, extra=[
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "id": "tool-test", "input": {"command": "true"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tool-test", "content": "ok"}]}},
    ])
    assert _session_state_from_tail_meta(server._extract_tail_meta(target)) is None


def test_new_request_and_partial_footer_cannot_inherit_completion(tmp_path):
    target = transcript(tmp_path)
    with target.open('a') as handle:
        handle.write('\n' + json.dumps({"type": "user", "message": {"content": "Continue the assessment."}}))
    assert _session_state_from_tail_meta(server._extract_tail_meta(target)) is None
    meta = dict(server._extract_tail_meta(transcript(tmp_path)),
                last_assistant_text=REPLY + '\n<session-state>')
    assert _session_state_from_tail_meta(meta) is None


def test_explicit_footer_still_has_precedence(tmp_path):
    meta = dict(server._extract_tail_meta(transcript(tmp_path)),
                last_assistant_text='<session-state>\nDID: Assessment incomplete.\nNEXT_STEP_USER: Choose a scope.\n</session-state>')
    state = _session_state_from_tail_meta(meta)
    assert state["next_step_user"] == "Choose a scope."
    assert "response_complete" not in state


def test_fresh_feed_enriches_only_the_exact_persisted_verdict(tmp_path, monkeypatch):
    from ccc_server.ultra_fresh import FreshSession
    target = transcript(tmp_path)
    monkeypatch.setattr(server, "_conv_parse_jsonl_mtime", lambda sid: (1, 1))
    monkeypatch.setattr(server, "conversation_transcript_path", lambda sid: target)
    monkeypatch.setattr(server, "_resolve_spawn_log_for_session", lambda sid: (None, False))
    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "claude")
    monkeypatch.setattr(server, "parse_conversation", lambda sid, **kw: {"last_line": 2, "events": [
        {"type": "assistant", "line": 2, "ts": "2026-11-01T10:00:00Z",
         "blocks": [{"kind": "text", "text": REPLY}]}]})
    assert FreshSession("verdict-test").read()[-1]["session_state"]["response_complete"] is True
    with target.open('a') as handle:
        handle.write('\n' + json.dumps({"type": "user", "message": {"content": "Continue the assessment."}}))
    assert FreshSession("verdict-test").read()[-1]["session_state"] is None
