"""Behavioral guardrails for group-chat background activity."""

import server
from pathlib import Path


def test_group_chat_becomes_inactive_fifteen_minutes_after_last_real_activity():
    meta = {"created_at": 1_000, "last_message_at": 1_000}

    assert server.group_chat_activity_state(meta, now=1_899) == "active"
    assert server.group_chat_activity_state(meta, now=1_900) == "inactive"


def test_group_chat_message_or_participant_change_wakes_inactive_chat():
    inactive = {"created_at": 1_000, "last_message_at": 1_000}
    sent_message = {**inactive, "last_message_at": 2_000}
    added_participant = {**inactive, "participant_changed_at": 2_000}

    assert server.group_chat_activity_state(inactive, now=2_000) == "inactive"
    assert server.group_chat_activity_state(sent_message, now=2_000) == "active"
    assert server.group_chat_activity_state(added_participant, now=2_000) == "active"


def test_explicit_group_chat_state_wins_over_recent_activity():
    recent = {"created_at": 2_000, "last_message_at": 2_000}

    assert server.group_chat_activity_state({**recent, "paused": True}, now=2_001) == "paused"
    assert server.group_chat_activity_state({**recent, "closed_at": 2_001}, now=2_001) == "closed"
    assert server.group_chat_activity_state({**recent, "archived": True}, now=2_001) == "archived"


def test_active_chat_summary_skips_inactive_participant_probes(tmp_path, monkeypatch):
    chats = tmp_path / "group-chats"
    chats.mkdir()
    active_md = chats / "active.md"
    inactive_md = chats / "inactive.md"
    active_md.write_text("active", encoding="utf-8")
    inactive_md.write_text("inactive", encoding="utf-8")
    (chats / "active.json").write_text(
        '{"topic":"Active", "created_at": 1900, "last_message_at": 1900, "session_ids":["a"]}',
        encoding="utf-8",
    )
    (chats / "inactive.json").write_text(
        '{"topic":"Inactive", "created_at": 1, "last_message_at": 1, "session_ids":["b"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(server.os.path, "expanduser", lambda value: str(chats))
    monkeypatch.setattr(
        server, "_group_chat_participant_meta",
        lambda sid: (_ for _ in ()).throw(AssertionError(f"probed {sid}")),
    )

    assert server._list_active_group_chat_summaries(now=2_000) == [{
        "topic": "Active",
        "state": "active",
        "session_ids": ["a"],
    }]


def test_active_chat_route_uses_lightweight_active_summary():
    source = Path(server.__file__).read_text(encoding="utf-8")
    route = source[source.index('elif path == "/api/group-chats/active":'):
                   source.index('elif path == "/api/group-chats/archived":')]

    assert "_list_active_group_chat_summaries()" in route


def test_browser_starts_group_chat_timer_only_after_an_active_chat_exists():
    source = Path("static/app.js").read_text(encoding="utf-8")
    start = source.index("function pollGcActive()")
    end = source.index("if ($gcActiveBtn)", start)
    poller = source[start:end]

    assert "let _gcActivePollTimer = null" in source
    assert "let _gcActivePollPromise = null" in source
    assert "if (_gcActivePollPromise) return _gcActivePollPromise;" in poller
    assert "if (activeCount && !_gcActivePollTimer)" in poller
    assert "else if (!activeCount && _gcActivePollTimer)" in poller
