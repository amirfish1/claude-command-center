"""Regression coverage for provider interruption bookkeeping rows.

The parser deliberately surfaces the interrupt line (45227703) as a user_text
event carrying its original text; the frontend matches that exact text and
renders a "Session interrupted" marker instead of a user bubble.
"""

import pathlib

import server

APP_JS = pathlib.Path(__file__).resolve().parents[1] / "static" / "app.js"


def _assert_surfaced_with_original_text(event, line, text):
    parsed = server._parse_conversation_event(event, line)
    assert parsed is not None
    assert parsed["type"] == "user_text"
    assert parsed["text"] == text
    # The renderer turns this text into the interrupted marker, not a bubble.
    src = APP_JS.read_text(encoding="utf-8")
    assert "/^\\[Request interrupted by user/.test(_rawText)" in src
    assert "session-interrupted-event" in src


def test_request_interruption_marker_is_not_rendered_as_user_text():
    event = {
        "type": "user",
        "message": {"role": "user", "content": "[Request interrupted by user]"},
    }

    _assert_surfaced_with_original_text(event, 7, "[Request interrupted by user]")


def test_tool_use_interruption_marker_is_not_rendered_as_user_text():
    event = {
        "type": "user",
        "message": {
            "role": "user",
            "content": "[Request interrupted by user for tool use]",
        },
    }

    _assert_surfaced_with_original_text(
        event, 8, "[Request interrupted by user for tool use]"
    )


def test_user_text_that_mentions_interruption_is_still_rendered():
    event = {
        "type": "user",
        "message": {
            "role": "user",
            "content": "I did not request an interruption; please continue.",
        },
    }

    parsed = server._parse_conversation_event(event, 8)

    assert parsed["type"] == "user_text"
    assert parsed["text"] == "I did not request an interruption; please continue."


if __name__ == "__main__":
    test_request_interruption_marker_is_not_rendered_as_user_text()
    test_user_text_that_mentions_interruption_is_still_rendered()
