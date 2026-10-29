"""A loaded skill's rules (isMeta user row) render as a Skill card, not nothing."""

import server

SKILL_BODY = (
    "Base directory for this skill: /home/u/.claude/skills/wt-triage-queue\n\n"
    "# Triage a stuck WatchTower queue\n\n"
    "## 1. Confirm it's actually stuck\n"
)


def _meta_row(content):
    return {
        "type": "user",
        "isMeta": True,
        "timestamp": "2026-10-21T22:00:00.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": content}]},
    }


def test_skill_body_meta_row_surfaces_as_skill_user_text():
    ev = server._parse_conversation_event(_meta_row(SKILL_BODY), 32)
    assert ev is not None
    assert ev["type"] == "user_text"
    assert ev["line"] == 32
    assert ev["skill"] == "wt-triage-queue"
    assert "## 1. Confirm it's actually stuck" in ev["text"]
    assert ev["images"] == []


def test_skill_body_as_plain_string_content_also_surfaces():
    row = _meta_row(SKILL_BODY)
    row["message"]["content"] = SKILL_BODY
    ev = server._parse_conversation_event(row, 7)
    assert ev and ev["skill"] == "wt-triage-queue"


def test_other_meta_rows_stay_hidden():
    assert server._parse_conversation_event(
        _meta_row("<local-command-caveat>Caveat: ...</local-command-caveat>"), 3) is None
    assert server._parse_conversation_event(_meta_row("plain meta note"), 4) is None


def test_skill_body_never_becomes_the_session_title():
    assert server._extract_user_prompt_text(_meta_row(SKILL_BODY)) == ""
