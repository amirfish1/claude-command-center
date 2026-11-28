"""One conversation's list row for panes that never show the list (CCC-728)."""
import pytest

import server
from ccc_server import archive_store


def _boom(name):
    def fail(*_args, **_kwargs):
        raise AssertionError(name + " ran")
    return fail


@pytest.fixture
def sources(monkeypatch):
    """Stub the three row sources; the full list source must never run."""
    state = {"hot": [], "store": None, "overlays": [], "calls": []}

    def hot(key, cache_options, **kwargs):
        state["calls"].append(("hot", kwargs.get("copy_rows"), kwargs.get("defer_cold")))
        return state["hot"], True, 1

    def store(path, key, overlay_rows, **kwargs):
        state["calls"].append(("store", set(kwargs["session_ids"])))
        if state["store"] is None:
            return None
        return {"conversations": [dict(r) for r in state["store"]]}

    def overlays(rows, existing_ids, phase, **kwargs):
        state["calls"].append(("overlays", set(existing_ids)))
        return list(rows) + state["overlays"], state["overlays"]

    monkeypatch.setattr(server, "_archive_serve_hot_rows_versioned", hot)
    monkeypatch.setattr(archive_store, "load_with_overrides", store)
    monkeypatch.setattr(server, "_archive_list_source_overlays", overlays)
    monkeypatch.setattr(server, "_overlay_conversation_lifecycle_flags", lambda rows, **k: rows)
    monkeypatch.setattr(server, "_archive_list_source_rows_cached", _boom("full list source"))
    monkeypatch.setattr(server, "_archive_list_source_rows_read", _boom("full list source"))
    # The full list's ticket side effects belong to the sidebar read only.
    monkeypatch.setattr(server, "_archive_list_ticket_updates", _boom("ticket effects"))
    return state


def test_recent_session_comes_from_the_serve_snapshot_projected_like_the_list(sources):
    row = {"session_id": "abc", "source": "interactive", "display_name": "Ski app",
           "goal": "ship it", "goal_status": "active", "model": "claude-sonnet-5-5",
           "last_assistant_text": "Done."}
    sources["hot"] = [{"session_id": "other"}, row]
    out = server.compute_conversation_row(" abc ")
    expected = server._archive_list_project_rows(
        [dict(row)], window="all", _retain_templates=False, _ticket_effects=False)[0]
    assert out == expected
    assert out["display_name"] == "Ski app"
    assert out["model"] == "claude-sonnet-5-5"
    assert out["last_assistant_preview"] == "Done."
    # Read-only snapshot reference, no cold build, and nothing else read.
    assert sources["calls"] == [("hot", False, True)]
    assert row == {"session_id": "abc", "source": "interactive", "display_name": "Ski app",
                   "goal": "ship it", "goal_status": "active", "model": "claude-sonnet-5-5",
                   "last_assistant_text": "Done."}, "the shared snapshot row is not mutated"


def test_older_session_is_read_from_the_archive_store_by_id(sources):
    sources["hot"] = [{"session_id": "recent"}]
    sources["store"] = [{"session_id": "old", "source": "interactive", "display_name": "Old"}]
    out = server.compute_conversation_row("old")
    assert out["session_id"] == "old" and out["display_name"] == "Old"
    assert [c[0] for c in sources["calls"]] == ["hot", "store"]
    assert sources["calls"][1] == ("store", {"old"})


def test_session_newer_than_the_snapshot_comes_from_the_engine_overlays(sources):
    # A /clear or Codex-continuation successor before the next snapshot.
    sources["hot"] = [{"session_id": "recent"}]
    sources["store"] = []
    sources["overlays"] = [{"session_id": "elsewhere"},
                           {"session_id": "new-sid", "source": "codex", "engine": "codex"}]
    out = server.compute_conversation_row("new-sid")
    assert out["session_id"] == "new-sid"
    assert [c[0] for c in sources["calls"]] == ["hot", "store", "overlays"]
    assert "recent" in sources["calls"][2][1], "overlays skip sessions the snapshot has"


def test_unknown_or_empty_ids_return_none(sources):
    assert server.compute_conversation_row("missing") is None
    calls = len(sources["calls"])
    assert server.compute_conversation_row("") is None
    assert server.compute_conversation_row(None) is None
    assert len(sources["calls"]) == calls, "empty ids read nothing"


def test_route_is_matched_before_the_transcript_route():
    source = open(server.__file__, encoding="utf-8").read()
    row_route = source.index('r"^/api/conversations/[^/]+/row$"')
    transcript_route = source.index('r"^/api/conversations/(?!all$|list$|order$)[^/]+$"')
    assert row_route < transcript_route
