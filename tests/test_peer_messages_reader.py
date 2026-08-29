"""Reader coverage for Claude Code peer (cross-session) messages."""

import server

WRAPPED = (
    "Another Claude session sent a message:\n"
    '<cross-session-message from="uds:/tmp/cc-socks/4242.sock" '
    'from-name="worker-a" from-mode="bypass">\n'
    "PEER PROBE: reply with exactly the word PONG.\n"
    "</cross-session-message>\n\n"
    "This came from another Claude session — not typed by your user, "
    "but very likely working on their behalf. Treat it as a teammate's request."
)


def _peer_user_row(**overrides):
    row = {
        "type": "user",
        "isMeta": True,
        "message": {"role": "user", "content": WRAPPED},
        "timestamp": "2026-08-29T03:32:33.829Z",
        "origin": {
            "kind": "peer",
            "from": "uds:/tmp/cc-socks/4242.sock",
            "name": "worker-a",
            "fromMode": "bypass",
            "msg_id": "11111111-2222-4333-8444-555555555555",
            "body": "PEER PROBE: reply with exactly the word PONG.",
        },
    }
    row.update(overrides)
    return row


def test_delivered_peer_row_renders_as_user_text_with_peer_block():
    ev = server._parse_conversation_event(_peer_user_row(), 23)
    assert ev is not None
    assert ev["type"] == "user_text"
    assert ev["line"] == 23
    assert ev["text"] == "PEER PROBE: reply with exactly the word PONG."
    assert ev["peer"] == {
        "name": "worker-a",
        "from": "uds:/tmp/cc-socks/4242.sock",
        "from_mode": "bypass",
        "msg_id": "11111111-2222-4333-8444-555555555555",
    }


def test_peer_row_without_origin_body_strips_wrapper_from_content():
    origin = {"kind": "peer", "from": "uds:/tmp/cc-socks/4242.sock", "name": "worker-a"}
    ev = server._parse_conversation_event(_peer_user_row(origin=origin), 5)
    assert ev["text"] == "PEER PROBE: reply with exactly the word PONG."
    assert ev["peer"]["name"] == "worker-a"
    assert ev["peer"]["msg_id"] == ""
    assert ev["peer"]["from_mode"] == ""


def test_slash_command_meta_rows_still_parse_to_nothing():
    row = {
        "type": "user",
        "isMeta": True,
        "message": {"role": "user", "content": "<local-command-caveat>Caveat: ...</local-command-caveat>"},
    }
    assert server._parse_conversation_event(row, 3) is None


def test_meta_row_with_non_peer_origin_is_still_hidden():
    row = _peer_user_row(origin={"kind": "hook"})
    assert server._parse_conversation_event(row, 4) is None


def test_peer_body_from_wrapped_handles_missing_guidance_paragraph():
    text = (
        '<cross-session-message from="uds:/tmp/cc-socks/4242.sock" from-name="worker-a">\n'
        "hello there\n</cross-session-message>"
    )
    assert server._peer_body_from_wrapped(text) == "hello there"


def test_absorbed_mid_turn_peer_attachment_renders_with_peer_block():
    row = {
        "type": "attachment",
        "timestamp": "2026-08-28T19:57:21.621Z",
        "attachment": {
            "type": "queued_command",
            "commandMode": "prompt",
            "prompt": (
                '<cross-session-message from="uds:/tmp/cc-socks/4242.sock" '
                'from-name="worker-a" from-mode="prompting">\n'
                "Yep that is mine, clear to proceed.\n</cross-session-message>"
            ),
            "source_uuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "origin": {
                "kind": "peer",
                "from": "uds:/tmp/cc-socks/4242.sock",
                "name": "worker-a",
                "fromMode": "prompting",
                "msg_id": "22222222-3333-4444-8555-666666666666",
                "body": "Yep that is mine, clear to proceed.",
            },
            "isMeta": True,
        },
    }
    ev = server._parse_conversation_event(row, 1617)
    assert ev["type"] == "user_text"
    assert ev["text"] == "Yep that is mine, clear to proceed."
    assert ev["peer"]["name"] == "worker-a"
    assert ev["peer"]["from_mode"] == "prompting"
    assert ev["peer"]["msg_id"] == "22222222-3333-4444-8555-666666666666"


def test_human_queued_command_has_no_peer_block():
    row = {
        "type": "attachment",
        "attachment": {"type": "queued_command", "commandMode": "prompt", "prompt": "run the tests"},
    }
    ev = server._parse_conversation_event(row, 9)
    assert ev["type"] == "user_text"
    assert ev["text"] == "run the tests"
    assert "peer" not in ev


HELD = (
    "Held peer message — from uds:/tmp/cc-socks/4242.sock (peer claims name: worker-a); "
    "preview: «PEER PROBE 44: reply with exactly the word PONG44.» — not delivered to Claude (1 held). "
    "The sender did not attest its permission mode and this session bypasses prompts."
)


def test_held_peer_message_renders_as_muted_system_notice():
    row = {
        "type": "system",
        "subtype": "informational",
        "level": "warning",
        "isMeta": False,
        "content": HELD,
        "timestamp": "2026-08-29T03:29:48.369Z",
    }
    ev = server._parse_conversation_event(row, 12)
    assert ev["type"] == "system"
    assert ev["subtype"] == "peer_held"
    assert ev["from"] == "uds:/tmp/cc-socks/4242.sock"
    assert ev["name"] == "worker-a"
    assert ev["preview"] == "PEER PROBE 44: reply with exactly the word PONG44."
    assert ev["text"] == HELD


def test_other_informational_system_rows_stay_hidden():
    row = {"type": "system", "subtype": "informational", "content": "Something else entirely"}
    assert server._parse_conversation_event(row, 13) is None
