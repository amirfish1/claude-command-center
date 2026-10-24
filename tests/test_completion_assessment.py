"""Evidence-backed completion gates and backwards-compatible footer parsing."""
import json

import pytest
import server  # Initialize the shared core before extracted modules.

from ccc_server.session_graph import (
    _CCC_SESSION_STATE_SYSTEM_PROMPT,
    _claude_session_state_args,
    _completion_assessment,
    _parse_session_state,
)


def completed():
    return {
        "version": 1,
        "resolution": {"score": 0.9, "evidence": "Original requested behavior implemented."},
        "verification": {"score": 0.8, "evidence": "Original reproduction and regression checks passed."},
        "delivery": {"score": 1, "evidence": "Remote ref matches the delivered commit."},
        "decision_dependency": {"score": 0, "evidence": "No consequential decision remains."},
        "caveats": [], "remaining_actions": [],
    }


def footer(assessment):
    return '<session-state>\nDID: Implemented the request.\nINSIGHT: Verified the original failure.\nNEXT_STEP_USER: None.\nCOMPLETION: ' + json.dumps(assessment) + '\n</session-state>'


def test_boundary_scores_pass_without_averaging():
    state = _parse_session_state(footer(completed()))
    assert state["did"] == "Implemented the request."
    assert state["completion"]["ready_to_close"] is True
    assert state["completion"]["reasons"] == []


@pytest.mark.parametrize("axis,score", [
    ("resolution", 0.899), ("verification", 0.799), ("delivery", 0.999),
    ("decision_dependency", 0.001),
])
def test_each_gate_independently_blocks_completion(axis, score):
    assessment = completed()
    assessment[axis]["score"] = score
    assessment["ready_to_close"] = True  # A model verdict cannot override the gates.
    result = _completion_assessment(assessment)
    assert result["valid"] is True
    assert result["ready_to_close"] is False
    assert result["reasons"]


@pytest.mark.parametrize("bad", [True, "1", None, -0.1, 1.1, float('nan'), float('inf'), 10**400])
def test_scores_must_be_finite_real_numbers_in_range(bad):
    assessment = completed()
    assessment["resolution"]["score"] = bad
    assert _completion_assessment(assessment)["valid"] is False


def test_delivery_not_applicable_still_requires_evidence_and_an_explicit_boolean():
    assessment = completed()
    assessment["delivery"] = {"score": 0, "applicable": False, "evidence": "Analysis-only request; no artifact to deliver."}
    assert _completion_assessment(assessment)["ready_to_close"] is True
    assessment["delivery"]["applicable"] = "false"
    assert _completion_assessment(assessment)["valid"] is False
    assessment["delivery"] = {"score": 0, "applicable": False, "evidence": " "}
    assert _completion_assessment(assessment)["valid"] is False


@pytest.mark.parametrize("key", ["resolution", "verification", "delivery", "decision_dependency", "caveats", "remaining_actions"])
def test_missing_assessment_fields_fail_closed(key):
    assessment = completed()
    del assessment[key]
    assert _completion_assessment(assessment)["ready_to_close"] is False


def test_caveats_must_be_disclosed_and_blocking_caveats_prevent_close():
    assessment = completed()
    assessment["caveats"] = [{"text": "Optional enhancement tracked in TEST-12.", "blocking": False}]
    assert _completion_assessment(assessment)["ready_to_close"] is True
    assessment["caveats"][0]["blocking"] = True
    assert _completion_assessment(assessment)["ready_to_close"] is False
    del assessment["caveats"][0]["blocking"]
    assert _completion_assessment(assessment)["valid"] is False


def test_pending_low_downside_work_does_not_become_complete_by_scoring_it_highly():
    assessment = completed()
    assessment["remaining_actions"] = [{"text": "Run the original reproduction.", "confidence": 0.95,
        "downside": 0.05, "within_scope": True, "reversible": True}]
    result = _completion_assessment(assessment)
    assert result["ready_to_close"] is False
    assert "Unfinished actions remain." in result["reasons"]
    assessment["remaining_actions"][0]["downside"] = "low"
    assert _completion_assessment(assessment)["valid"] is False


@pytest.mark.parametrize("requires_approval", [False, True])
def test_diagnosed_malfunction_stays_open_until_the_remedy_is_handled(requires_approval):
    assessment = completed()
    assessment["resolution"] = {"score": 0.5, "evidence": "Found a disabled integration; it still fails."}
    assessment["decision_dependency"] = {"score": 1 if requires_approval else 0,
        "evidence": "Repair needs new authorization." if requires_approval else "Repair already authorized."}
    assessment["remaining_actions"] = [{"text": "Connect the existing integration and verify the original failure.",
        "confidence": 0.95, "downside": 0.1, "within_scope": not requires_approval, "reversible": True}]
    text = footer(assessment)
    if requires_approval:
        text = text.replace("NEXT_STEP_USER: None.",
            'NEXT_STEP_USER: Approve connecting the disabled integration.\n'
            'NEXT_ACTION: {"label":"Connect","reply":"Connect the disabled integration and verify it."}')
    state = _parse_session_state(text)
    assert state["completion"]["ready_to_close"] is False
    assert "Unfinished actions remain." in state["completion"]["reasons"]
    if requires_approval:
        assert state["next_action"]["label"] == "Connect"
        assert "A user decision remains." in state["completion"]["reasons"]
    else:
        assert state["next_step_user"] == "None."
        assert "next_action" not in state


def test_explicit_explanation_only_can_finish_without_fabricated_approval():
    assessment = completed()
    assessment["resolution"]["evidence"] = "Answered the explicit explanation-only request with the verified cause."
    assessment["delivery"] = {"applicable": False, "score": 0,
        "evidence": "User explicitly requested an explanation without changes."}
    state = _parse_session_state(footer(assessment))
    assert state["completion"]["ready_to_close"] is True
    assert state["next_step_user"] == "None."
    assert "next_action" not in state


@pytest.mark.parametrize("raw", ["not JSON", '{"version":1}', 'null', '[]', 'true'])
def test_invalid_json_does_not_erase_the_legacy_summary_or_recommend_close(raw):
    text = '<session-state>\nDID: Partial work.\nCOMPLETION: ' + raw + '\n</session-state>'
    state = _parse_session_state(text)
    assert state["did"] == "Partial work."
    assert state["completion"]["ready_to_close"] is False


def test_last_footer_wins_and_legacy_response_shape_is_preserved():
    legacy = '<session-state>\nDID: More work remains.\nINSIGHT: Found a gap.\nNEXT_STEP_USER: None.\n</session-state>'
    expected = {"did": "More work remains.", "insight": "Found a gap.", "next_step_user": "None."}
    assert _parse_session_state(legacy) == expected
    assert _parse_session_state(footer(completed()) + '\n' + legacy) == expected


def test_prompt_is_delivered_through_existing_system_instruction_channel():
    assert _CCC_SESSION_STATE_SYSTEM_PROMPT in _claude_session_state_args()[1]
    for term in ("COMPLETION:", "remaining_actions", "decision_dependency", "Never infer authorization", "manual archive", "instead of asking"):
        assert term in _CCC_SESSION_STATE_SYSTEM_PROMPT
