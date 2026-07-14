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


def test_trash_active_session_archives_and_trashes(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", tmp_path / "archived.json")
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    monkeypatch.setattr(server, "SIDECAR_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "_archive_grace", {})
    monkeypatch.setattr(server, "_save_archive_grace", lambda: None)
    monkeypatch.setattr(server, "_kill_session_by_id", lambda sid: {"ok": True})
    monkeypatch.setattr(server, "_log_archive_event", lambda *args: None)

    result = server._set_conversation_trashed("sid-a", True)

    assert result == {"archived": True, "trashed": True, "killed": {"ok": True}}
    assert server._load_archived_conversations(sweep=False) == ["sid-a"]
    assert server._load_trashed_conversations() == ["sid-a"]


def test_untrash_returns_to_archived(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ARCHIVED_CONVERSATIONS_FILE", tmp_path / "archived.json")
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    server._save_archived_conversations(["sid-a"])
    server._save_trashed_conversations(["sid-a"])

    result = server._set_conversation_trashed("sid-a", False)

    assert result == {"archived": True, "trashed": False, "killed": None}
    assert server._load_archived_conversations(sweep=False) == ["sid-a"]
    assert server._load_trashed_conversations() == []


def test_unarchive_clears_trashed_state(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRASHED_CONVERSATIONS_FILE", tmp_path / "trashed.json")
    server._save_trashed_conversations(["sid-a"])

    server._clear_trashed_on_unarchive("sid-a")

    assert server._load_trashed_conversations() == []


def test_group_chat_trash_archives_first(tmp_path, monkeypatch):
    chat_path = tmp_path / "chat.md"
    chat_path.write_text("# Chat\n")
    state = {"archived": False, "trashed": False}
    monkeypatch.setattr(server, "_resolve_group_chat_ref", lambda *args: str(chat_path))
    monkeypatch.setattr(server, "_load_group_chat_sidecar", lambda path: dict(state))

    def set_archived(path, archived, raw_uuid=""):
        state["archived"] = archived
        return {"ok": True}

    def update_sidecar(path, **fields):
        state.update(fields)
        return True

    monkeypatch.setattr(server, "_group_chat_set_archived", set_archived)
    monkeypatch.setattr(server, "_update_group_chat_sidecar", update_sidecar)

    assert server._group_chat_set_trashed(str(chat_path), True) == {"ok": True}
    assert state == {"archived": True, "trashed": True}


def test_group_chat_unarchive_clears_trash(tmp_path, monkeypatch):
    chat_path = tmp_path / "chat.md"
    chat_path.write_text("# Chat\n")
    saved = {}
    monkeypatch.setattr(server, "_resolve_group_chat_ref", lambda *args: str(chat_path))
    monkeypatch.setattr(server, "_update_group_chat_sidecar", lambda path, **fields: saved.update(fields) or True)
    monkeypatch.setattr(server, "_group_chat_log_system", lambda *args: None)

    assert server._group_chat_set_archived(str(chat_path), False) == {"ok": True}
    assert saved == {"archived": False, "archived_at": None, "trashed": False}


def test_post_router_exposes_trash_endpoints():
    source = inspect.getsource(server.CommandCenterHandler.do_POST)

    assert '/api/conversations/[^/]+/trash' in source
    assert '"/api/group-chats/trash"' in source
    assert '"/api/group-chats/untrash"' in source
    assert '"trashed": bool(meta.get("trashed"))' in inspect.getsource(
        server._list_group_chats
    )
