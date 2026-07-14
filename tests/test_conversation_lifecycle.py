import inspect

import pytest

import server


def test_trashed_conversations_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "trashed-conversations.json"
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", path)

    assert server._load_trashed_conversations() == []
    assert server._save_trashed_conversations(["sid-a", "sid-b"]) == ["sid-a", "sid-b"]
    assert server._load_trashed_conversations() == ["sid-a", "sid-b"]


def test_trashed_loader_ignores_non_string_entries(tmp_path, monkeypatch):
    path = tmp_path / "trashed-conversations.json"
    path.write_text('["sid-a", 7, null, "sid-b"]')
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", path)

    assert server._load_trashed_conversations() == ["sid-a", "sid-b"]


def test_archive_cache_rehydrate_refreshes_trashed_state(monkeypatch):
    monkeypatch.setattr(server, "_load_session_name_overrides", lambda: {})
    monkeypatch.setattr(server, "_load_archived_conversations", lambda **kwargs: ["sid-a"])
    monkeypatch.setattr(server, "_load_trashed_conversations", lambda **kwargs: ["sid-a"])
    monkeypatch.setattr(server, "_load_verified_conversations", lambda: [])
    monkeypatch.setattr(server, "_load_pinned_conversations", lambda: [])
    monkeypatch.setattr(server, "_spawn_registry_entries_by_session", lambda: {})
    monkeypatch.setattr(server, "_discover_live_session_ids", lambda: set())

    rows = server._rehydrate_archive_cached_rows([
        {"session_id": "sid-a", "mtime": 1, "trashed": False},
        {"session_id": "sid-b", "mtime": 1, "trashed": True},
    ])

    assert rows[0]["trashed"] is True
    assert rows[1]["trashed"] is False


def test_live_registry_row_stamps_trashed_state():
    signature = inspect.signature(server._live_registry_conversation_row)

    assert "trashed_set" in signature.parameters
    assert '"trashed": sid in (trashed_set or set())' in inspect.getsource(
        server._live_registry_conversation_row
    )


@pytest.mark.parametrize(
    "builder_name",
    [
        "find_all_conversations",
        "find_conversations",
        "find_codex_conversations",
        "find_gemini_conversations",
        "find_cursor_conversations",
        "find_hermes_conversations",
        "find_kilo_conversations",
        "find_antigravity_conversations",
        "find_pkood_agents",
    ],
)
def test_every_conversation_builder_stamps_trashed_state(builder_name):
    source = inspect.getsource(getattr(server, builder_name))

    assert "_load_trashed_conversations" in source
    assert '"trashed":' in source
