"""Regression coverage for provider interruption bookkeeping rows."""

import server


def test_request_interruption_marker_is_not_rendered_as_user_text():
    event = {
        "type": "user",
        "message": {"role": "user", "content": "[Request interrupted by user]"},
    }

    assert server._parse_conversation_event(event, 7) is None


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
