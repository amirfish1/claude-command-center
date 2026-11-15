# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Topic-specific parent retrieval shared by recall and sidebar search."""
import sqlite3

from ccc_server import session_fts as fts


def test_parent_evidence_is_distinct_bounded_and_topic_specific(monkeypatch):
    edges = {f"child-{i}": "parent" for i in range(20)}
    edges.update({"other-child": "other-parent", "parent": "grandparent"})
    monkeypatch.setattr(fts._lineage, "_load_spawn_edges", lambda: (edges, {}))
    monkeypatch.setattr(fts, "_meta_for_sids", lambda conn, sids: dict.fromkeys(sids, {}))
    ranked = fts._rank_topic_parents(None, ["direct", "child-0", "child-1", "child-2"])
    assert ranked[0] == "parent"
    assert "other-parent" not in ranked
    assert "grandparent" not in ranked
    ranked = fts._rank_topic_parents(None, ["other-child", "child-0", "child-1", "child-2"])
    assert ranked.index("parent") < ranked.index("other-parent")
    assert fts._rank_topic_parents(None, ["direct", "child-0"] * 5)[0] == "direct"
    assert fts._rank_topic_parents(None, ["direct", "child-0"])[-1] == "parent"


def test_missing_graph_or_parent_preserves_ranking(monkeypatch):
    monkeypatch.setattr(fts._lineage, "_load_spawn_edges", lambda: ({"child": "missing"}, {}))
    monkeypatch.setattr(fts, "_meta_for_sids", lambda conn, sids: {})
    assert fts._rank_topic_parents(None, ["direct", "child"]) == ["direct", "child"]
    assert fts._rank_topic_parents(None, []) == []


def test_search_retrieves_parent_before_limit_without_its_own_text_match(monkeypatch):
    conn = sqlite3.connect(":memory:")
    fts._init_db(conn)
    for sid, title in [("parent", "Coordination"), ("other", "Coordination")]:
        conn.execute("INSERT INTO sdoc VALUES (?, ?, '', '', '', '', '')", (sid, title))
        conn.execute("INSERT INTO file_cache(path, sid, indexed) VALUES (?, ?, 1)", (sid, sid))
    for i in range(4):
        conn.execute("INSERT INTO sdoc VALUES (?, 'quartz', '', '', '', '', '')", (f"child-{i}",))
    edges = {f"child-{i}": "parent" for i in range(4)}
    edges.update({f"unrelated-{i}": "other" for i in range(100)})
    monkeypatch.setattr(fts._lineage, "_load_spawn_edges", lambda: (edges, {}))
    monkeypatch.setattr(fts, "_get_connection", lambda: conn)
    monkeypatch.setattr(fts, "_sync_index", lambda *a, **kw: None)
    monkeypatch.setattr(fts, "_ollama_available", lambda: False)
    monkeypatch.setattr(fts, "_hermes_channel_sids", lambda *a: [])
    assert fts.search_sessions("quartz", limit=1)[0]["session_id"] == "parent"
    assert "other" not in [h["session_id"] for h in fts.search_sessions("quartz")]
    # Explicit syntax retains exact-match semantics.
    assert "parent" not in [h["session_id"] for h in fts.search_sessions('"quartz"')]
    conn.close()
