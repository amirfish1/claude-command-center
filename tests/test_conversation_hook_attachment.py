"""Regression coverage for hook output attachment entries in conversation view (CCC-GH-109)."""

import json
import server


def test_hook_success_attachment_surfaces_stdout_and_event():
    event = {
        "parentUuid": "29cc272a-d36d-4650-bfe0-9a98c5330698",
        "isSidechain": False,
        "type": "attachment",
        "timestamp": "2026-08-22T20:41:47.500Z",
        "attachment": {
            "type": "hook_success",
            "hookName": "SessionStart:resume",
            "toolUseID": "ba37710a-5e72-404f-8d75-7d2d45a094b1",
            "hookEvent": "SessionStart",
            "content": "[Token Optimizer] A recent checkpoint is available",
            "stdout": "[Token Optimizer] A recent checkpoint is available (prior work on chuck-realtor-web · branch main) at /path/to/checkpoint.md. Load it only if it matches what you are working on now.\n",
            "stderr": "",
            "exitCode": 0,
            "durationMs": 360,
        },
    }

    parsed = server._parse_conversation_event(event, 60)

    assert parsed is not None
    assert parsed["type"] == "attachment"
    assert parsed["subtype"] == "hook"
    assert parsed["line"] == 60
    assert parsed["ts"] == "2026-08-22T20:41:47.500Z"
    assert parsed["hook_event"] == "SessionStart"
    assert parsed["hook_name"] == "SessionStart:resume"
    assert "[Token Optimizer] A recent checkpoint is available" in parsed["text"]
    assert parsed["duration_ms"] == 360
    assert parsed["exit_code"] == 0


def test_async_hook_response_surfaces_stderr():
    event = {
        "type": "attachment",
        "timestamp": "2026-08-22T19:19:20.038Z",
        "attachment": {
            "type": "async_hook_response",
            "processId": "async_hook_39705",
            "hookName": "SessionStart:startup",
            "hookEvent": "SessionStart",
            "stdout": "",
            "stderr": "[Token Optimizer] hook budget exceeded; skipping ensure-health tick to keep session responsive\n",
            "exitCode": 0,
        },
    }

    parsed = server._parse_conversation_event(event, 23)

    assert parsed is not None
    assert parsed["type"] == "attachment"
    assert parsed["hook_event"] == "SessionStart"
    assert "hook budget exceeded" in parsed["text"]
    assert "hook budget exceeded" in parsed["stderr"]


def test_hook_additional_context_content_list():
    event = {
        "type": "attachment",
        "timestamp": "2026-08-22T19:19:02.858Z",
        "attachment": {
            "type": "hook_additional_context",
            "content": ["<EXTREMELY_IMPORTANT>\nYou have superpowers.\n</EXTREMELY_IMPORTANT>"],
            "hookName": "SessionStart",
            "toolUseID": "SessionStart",
            "hookEvent": "SessionStart",
        },
    }

    parsed = server._parse_conversation_event(event, 8)

    assert parsed is not None
    assert parsed["type"] == "attachment"
    assert "<EXTREMELY_IMPORTANT>" in parsed["text"]


def test_non_hook_attachments_ignored():
    for non_hook in ["deferred_tools_delta", "agent_listing_delta", "total_tokens_reminder", "skill_listing"]:
        event = {
            "type": "attachment",
            "timestamp": "2026-08-22T19:19:05.035Z",
            "attachment": {
                "type": non_hook,
            },
        }
        assert server._parse_conversation_event(event, 10) is None


def test_empty_hook_attachment_ignored():
    event = {
        "type": "attachment",
        "timestamp": "2026-08-22T19:19:05.035Z",
        "attachment": {
            "type": "hook_success",
            "hookName": "PostToolUse:Test",
            "hookEvent": "PostToolUse",
            "stdout": "",
            "stderr": "",
            "content": "",
        },
    }
    assert server._parse_conversation_event(event, 11) is None
