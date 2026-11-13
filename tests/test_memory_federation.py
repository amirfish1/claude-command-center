# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""MEMORY-8 (multi-machine S3): memory_recall/memory_shipped/memory_brief/
memory_file_history federation route actions.

Never spins up a real server: `_federation_execute_route` runs entirely
in-process, and the one call it makes to reach a local API endpoint
(`fleet._federation_self_api`) is monkeypatched to a fake that records what
it was asked to fetch. That's enough to prove the routing/translation/
capping/scope logic in ccc_server/fleet.py without a live HTTP loopback.
"""

import json

import pytest

import server  # noqa: F401 -- adopts ccc_server modules' names before fleet needs them
import federation
from ccc_server import fleet


@pytest.fixture(autouse=True)
def isolated_federation_home(tmp_path, monkeypatch):
    """federation.py reads $HOME/.claude/command-center -- every test here
    gets a scratch home so pairing/repo-map state never touches the real
    one and never leaks between tests."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def _fake_self_api(monkeypatch, result):
    seen = {}

    def fake(method, api_path, body=None, query=None, timeout=60.0):
        seen.update(method=method, path=api_path, body=body, query=query, timeout=timeout)
        return result

    monkeypatch.setattr(fleet, "_federation_self_api", fake)
    return seen


def _envelope(action, args=None, hops=2, req_id=""):
    return {"action": action, "args": args or {}, "hops": hops, "req_id": req_id}


# -- route table ---------------------------------------------------------


def test_memory_actions_are_registered_read_only():
    spec = fleet._FEDERATION_ROUTE_ACTIONS
    for action, path in (
        ("memory_recall", "/api/memory/recall"),
        ("memory_shipped", "/api/memory/shipped"),
        ("memory_file_history", "/api/memory/file-history"),
    ):
        method, api_path, mutating = spec[action]
        assert method == "GET"
        assert api_path == path
        assert mutating is False


def test_capability_manifest_advertises_memory():
    caps = federation.capability_manifest("9.9.9")
    assert caps["memory"] == 1
    assert "memory" in caps["features"]


# -- arg translation -------------------------------------------------------


def test_memory_recall_forces_local_scope_and_caps_limit(monkeypatch):
    seen = _fake_self_api(monkeypatch, {"query": "x", "results": []})
    payload, status = fleet._federation_execute_route(
        _envelope("memory_recall", {"q": "confetti", "limit": 999}))
    assert status == 200
    assert seen["method"] == "GET"
    assert seen["path"] == "/api/memory/recall"
    assert seen["query"]["scope"] == "local"
    assert seen["query"]["limit"] == fleet._MEMORY_MAX_ROWS


def test_memory_recall_cannot_override_scope_to_all(monkeypatch):
    """A peer asking scope=all (which would mean 'fan out further') is
    silently overridden to local -- this is the one-hop-only rule, and it
    must hold even if a compromised or buggy peer sends scope=all."""
    seen = _fake_self_api(monkeypatch, {"query": "x", "results": []})
    fleet._federation_execute_route(
        _envelope("memory_recall", {"q": "x", "scope": "all"}))
    assert seen["query"]["scope"] == "local"


def test_memory_recall_truncates_overlong_query(monkeypatch):
    seen = _fake_self_api(monkeypatch, {"query": "x", "results": []})
    long_q = "x" * 900
    fleet._federation_execute_route(_envelope("memory_recall", {"q": long_q}))
    assert len(seen["query"]["q"]) == fleet._MEMORY_ARG_STR_CAP


def test_memory_shipped_forces_local_scope(monkeypatch):
    seen = _fake_self_api(monkeypatch, {"shipped": False, "evidence": [], "tickets": []})
    fleet._federation_execute_route(_envelope("memory_shipped", {"topic": "widgets"}))
    assert seen["path"] == "/api/memory/shipped"
    assert seen["query"]["topic"] == "widgets"
    assert seen["query"]["scope"] == "local"


def test_memory_brief_builds_path_segment_and_drops_scope_limit(monkeypatch):
    seen = _fake_self_api(monkeypatch, {"found": True})
    fleet._federation_execute_route(_envelope("memory_brief", {"q": "abc12345", "limit": 5}))
    assert seen["path"] == "/api/memory/brief/abc12345"
    assert "scope" not in (seen["query"] or {})
    assert "limit" not in (seen["query"] or {})


def test_memory_brief_url_quotes_the_query(monkeypatch):
    seen = _fake_self_api(monkeypatch, {"found": False})
    fleet._federation_execute_route(_envelope("memory_brief", {"q": "a session with spaces/slash"}))
    assert seen["path"] == "/api/memory/brief/a%20session%20with%20spaces%2Fslash"


def test_memory_file_history_maps_repo_identity_to_local_clone(monkeypatch, tmp_path):
    repo_dir = tmp_path / "widget-repo"
    repo_dir.mkdir()
    federation.map_repo("github.com/acme/widget-repo", str(repo_dir))
    seen = _fake_self_api(monkeypatch, {"path": "x", "repo": "widget-repo", "history": []})

    fleet._federation_execute_route(_envelope("memory_file_history", {
        "repo_identity": "github.com/acme/widget-repo",
        "rel_path": "src/app.py",
    }))
    assert seen["path"] == "/api/memory/file-history"
    assert seen["query"]["path"].endswith("widget-repo/src/app.py")
    assert "repo_identity" not in seen["query"]
    assert "rel_path" not in seen["query"]
    assert "scope" not in seen["query"]  # file_history has no scope concept


def test_memory_file_history_unmapped_identity_is_stale_mapping(monkeypatch):
    monkeypatch.setattr(fleet._core, "_known_repo_paths", lambda: [])
    payload, status = fleet._federation_execute_route(_envelope("memory_file_history", {
        "repo_identity": "github.com/nobody/nowhere",
        "rel_path": "a.py",
    }))
    assert status == 404
    assert payload["error"] == "stale_mapping"


def test_memory_file_history_requires_both_args():
    payload, status = fleet._federation_execute_route(
        _envelope("memory_file_history", {"repo_identity": "github.com/a/b"}))
    assert status == 400
    assert payload["error"] == "bad_request"


def test_memory_file_history_rejects_path_escape(monkeypatch, tmp_path):
    """rel_path is peer-supplied. os.path.join('/repo', '/etc/passwd') ==
    '/etc/passwd' (the join silently discards the base for an absolute
    second argument), and '..' segments can walk back out after normpath --
    both must be refused rather than resolved against the real clone."""
    repo_dir = tmp_path / "widget-repo"
    repo_dir.mkdir()
    federation.map_repo("github.com/acme/widget-repo", str(repo_dir))
    seen = _fake_self_api(monkeypatch, {"path": "x", "repo": "widget-repo", "history": []})

    for escaping_rel_path in ("/etc/passwd", "../../../../etc/passwd", "../outside"):
        payload, status = fleet._federation_execute_route(_envelope("memory_file_history", {
            "repo_identity": "github.com/acme/widget-repo",
            "rel_path": escaping_rel_path,
        }))
        assert status == 400, escaping_rel_path
        assert payload["error"] == "bad_request", escaping_rel_path
    assert seen == {}  # never reached the local endpoint


# -- response capping -------------------------------------------------------


def test_response_row_count_is_capped_and_flagged(monkeypatch):
    rows = [{"session_id": str(i), "snippet": "s"} for i in range(50)]
    _fake_self_api(monkeypatch, {"query": "x", "results": rows})
    payload, status = fleet._federation_execute_route(_envelope("memory_recall", {"q": "x"}))
    result = payload["result"]
    assert len(result["results"]) == fleet._MEMORY_MAX_ROWS
    assert result["truncated"] is True


def test_snippet_and_match_text_are_capped(monkeypatch):
    rows = [{
        "session_id": "1",
        "snippet": "s" * 900,
        "match": "m" * 900,
    }]
    _fake_self_api(monkeypatch, {"query": "x", "results": rows})
    payload, _ = fleet._federation_execute_route(_envelope("memory_recall", {"q": "x"}))
    row = payload["result"]["results"][0]
    assert len(row["snippet"]) == fleet._MEMORY_SNIPPET_CAP
    assert len(row["match"]) == fleet._MEMORY_MATCH_CAP


def test_response_dict_match_text_is_capped(monkeypatch):
    # Real shape, from session_fts.section_matches() via memory_api.recall():
    # {"section", "turn", "turn_end", "snippet"} -- "snippet", never "text".
    rows = [{"session_id": "1", "match": {"snippet": "m" * 900, "turn": 3}}]
    _fake_self_api(monkeypatch, {"query": "x", "results": rows})
    payload, _ = fleet._federation_execute_route(_envelope("memory_recall", {"q": "x"}))
    row = payload["result"]["results"][0]
    assert len(row["match"]["snippet"]) == fleet._MEMORY_MATCH_CAP
    assert row["match"]["turn"] == 3


def test_response_total_size_is_capped(monkeypatch):
    """Even under the 30-row cap, a response can still be oversized -- an
    uncapped field (e.g. 'title', which this module doesn't truncate) can
    still blow the byte budget, and the byte cap must drop trailing rows
    until the serialized payload fits, same as a truncated snippet/match
    would."""
    rows = [{"session_id": str(i), "title": "t" * 10_000} for i in range(30)]
    _fake_self_api(monkeypatch, {"query": "x", "results": rows})
    payload, _ = fleet._federation_execute_route(_envelope("memory_recall", {"q": "x"}))
    result = payload["result"]
    assert len(json.dumps(result).encode("utf-8")) <= fleet._MEMORY_RESPONSE_CAP_BYTES
    assert result["truncated"] is True
    assert len(result["results"]) < 30


def test_memory_brief_oversize_lists_are_capped_generically(monkeypatch):
    """memory_brief's own list fields (files_touched, commits, tickets, ...)
    aren't named "results"/"evidence"/"history" -- the row/byte caps must
    apply to every top-level list generically, or these ride through
    uncapped past the spec's per-response bound (MEMORY-15)."""
    fake_result = {
        "found": True,
        "session_id": "abc123",
        "files_touched": [f"file{i}.py" for i in range(60)],
        "commits": [{"sha": str(i), "subject": "x"} for i in range(60)],
        "tickets": [{"ref": f"T-{i}"} for i in range(60)],
    }
    _fake_self_api(monkeypatch, fake_result)
    payload, status = fleet._federation_execute_route(_envelope("memory_brief", {"q": "abc12345"}))
    assert status == 200
    result = payload["result"]
    assert len(result["files_touched"]) == fleet._MEMORY_MAX_ROWS
    assert len(result["commits"]) == fleet._MEMORY_MAX_ROWS
    assert len(result["tickets"]) == fleet._MEMORY_MAX_ROWS
    assert result["truncated"] is True


def test_non_memory_action_response_is_not_capped(monkeypatch):
    """The row/byte caps are specific to memory_* actions -- an unrelated
    action's payload (e.g. group_chat_read) must pass through untouched."""
    big_rows = [{"x": i} for i in range(1000)]
    _fake_self_api(monkeypatch, {"messages": big_rows})
    payload, _ = fleet._federation_execute_route(_envelope("group_chat_read", {}))
    assert len(payload["result"]["messages"]) == 1000


# -- scope enforcement -------------------------------------------------------


def test_unrestricted_peer_can_call_any_registered_action(monkeypatch):
    _fake_self_api(monkeypatch, {"ok": True})
    peer = {"node_id": "peer-1"}  # no "scopes" key at all: back-compat "*"
    _, status = fleet._federation_execute_route(_envelope("memory_recall", {"q": "x"}), peer=peer)
    assert status == 200
    _, status2 = fleet._federation_execute_route(_envelope("group_chat_read", {}), peer=peer)
    assert status2 == 200


def test_memory_scoped_peer_can_call_memory_actions(monkeypatch):
    _fake_self_api(monkeypatch, {"ok": True})
    peer = {"node_id": "peer-2", "scopes": ["memory:read"]}
    for action, args in (
        ("memory_recall", {"q": "x"}),
        ("memory_shipped", {"topic": "x"}),
        ("memory_brief", {"q": "abc"}),
    ):
        _, status = fleet._federation_execute_route(_envelope(action, args), peer=peer)
        assert status == 200, action


def test_memory_scoped_peer_is_refused_spawn_and_inject(monkeypatch):
    _fake_self_api(monkeypatch, {"ok": True})
    peer = {"node_id": "peer-3", "scopes": ["memory:read"]}
    for action in ("spawn", "inject", "group_chat_read", "ask"):
        payload, status = fleet._federation_execute_route(_envelope(action, {}), peer=peer)
        assert status == 403, action
        assert payload["error"] == "scope_forbidden"


def test_unknown_action_is_unsupported_regardless_of_peer_scope():
    peer = {"node_id": "peer-4", "scopes": ["memory:read"]}
    payload, status = fleet._federation_execute_route(_envelope("not_a_real_action", {}), peer=peer)
    assert status == 400
    assert payload["error"] == "unsupported_capability"


def test_wildcard_scope_is_unrestricted(monkeypatch):
    """A peer explicitly paired with scopes=["*"] is unrestricted, same as
    a peer with no scopes key at all -- spawn's own arg handling may still
    reject the call for unrelated reasons, so this only proves the scope
    gate itself isn't what blocks it."""
    _fake_self_api(monkeypatch, {"ok": True})
    peer = {"node_id": "peer-5", "scopes": ["*"]}
    payload, _ = fleet._federation_execute_route(_envelope("spawn", {"cwd": "/tmp"}), peer=peer)
    assert payload.get("error") != "scope_forbidden"


def test_peer_scope_allows_helper_matrix():
    allows = fleet._federation_peer_scope_allows
    assert allows(None, "spawn") is True
    assert allows({}, "spawn") is True  # no "scopes" key at all: absent -> "*"
    # An explicit but EMPTY scopes list is not the same as absent -- it means
    # the peer was deliberately paired with zero capabilities.
    assert allows({"scopes": []}, "spawn") is False
    assert allows({"scopes": []}, "memory_recall") is False
    assert allows({"scopes": ["*"]}, "spawn") is True
    assert allows({"scopes": ["memory:read"]}, "memory_recall") is True
    assert allows({"scopes": ["memory:read"]}, "spawn") is False
    assert allows({"scopes": ["memory:read", "*"]}, "spawn") is True
