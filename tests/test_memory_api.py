"""Tests for ccc_server/memory_api.py: the API-layer glue over ship_graph's
is_shipped/search_sessions that powers GET /api/memory/recall,
GET /api/memory/shipped, and the `ccc recall` / `ccc shipped` CLI verbs."""

import json
import subprocess

import pytest

import ccc_server.memory_api as memory_api
import ccc_server.session_fts as session_fts
import ccc_server.ship_graph as ship_graph


@pytest.fixture
def mock_memory_env(tmp_path, monkeypatch):
    """A real transcript in a real git repo, indexed by both ship_graph
    (session_meta: repo/date) and session_fts (sdoc: title/snippet) — the
    two already-synced tables memory_api.recall() joins across."""
    ship_db = tmp_path / "ship_graph.sqlite"
    fts_db = tmp_path / "session_fts.sqlite"
    wt_db_path = tmp_path / "queues.db"
    projects_dir = tmp_path / "projects"
    codex_dir = tmp_path / "codex"
    repo_dir = tmp_path / "widget-repo"

    projects_dir.mkdir(parents=True)
    codex_dir.mkdir(parents=True)
    repo_dir.mkdir(parents=True)

    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("# initial", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(
        ["git", "commit", "-m", "feat(widgets): add confetti animation"],
        cwd=repo_dir, check=True,
    )
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True,
    ).stdout.strip()

    session_dir = projects_dir / "widget-repo"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session-abc.jsonl"
    lines = [
        {
            "type": "user",
            "cwd": str(repo_dir),
            "timestamp": "2026-09-20T10:00:00Z",
            "message": {"role": "user", "content": "add a confetti animation on save"},
        },
        {
            "type": "assistant",
            "cwd": str(repo_dir),
            "timestamp": "2026-09-20T10:01:00Z",
            "message": {
                "role": "assistant",
                "content": f"Done — [main {commit_sha[:7]}] feat(widgets): add confetti animation",
            },
        },
    ]
    session_file.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")

    monkeypatch.setenv("CCC_SHIP_GRAPH_DB", str(ship_db))
    monkeypatch.setenv("WATCHTOWER_DB", str(wt_db_path))
    monkeypatch.setenv("CCC_PROJECTS_ROOT", str(projects_dir))
    monkeypatch.setenv("CCC_CODEX_SESSIONS_ROOT", str(codex_dir))
    monkeypatch.setenv("CCC_KIMI_SESSIONS_ROOT", str(tmp_path / "kimi-empty"))
    monkeypatch.setenv("CCC_GEMINI_TMP_ROOT", str(tmp_path / "gemini-empty"))
    monkeypatch.setenv("CCC_AGY_BRAIN_ROOTS", "")
    monkeypatch.setenv("CCC_CURSOR_PROJECTS_ROOT", str(tmp_path / "cursor-empty"))
    monkeypatch.setenv("CCC_SHIP_GRAPH_DAYS", "0")  # disable cutoff for tests
    monkeypatch.setenv("CCC_SHIP_GRAPH_REPOS", str(repo_dir))
    monkeypatch.setenv("CCC_SESSION_FTS_DB", str(fts_db))
    monkeypatch.setenv("CCC_DECISIONS_DB", str(tmp_path / "decisions.sqlite"))
    # Keep the host's real ~/.hermes sessions out of search results.
    from ccc_server import hermes as _hermes
    monkeypatch.setattr(_hermes, "search_hermes_messages", lambda *a, **k: [])
    monkeypatch.setenv("CCC_SESSION_FTS_DAYS", "0")  # disable cutoff for tests
    monkeypatch.setenv("CCC_SESSION_FTS_ALLOW_SCRATCH", "1")  # tmp_path looks scratch-y
    # These tests exercise the FTS/graph enrichment path, not the optional
    # embeddings channel; keep them hermetic regardless of whether the dev
    # box happens to have a local Ollama daemon running (see
    # test_session_fts.py's fts_env fixture for the same guard). Without
    # this, _vec_cache -- a process-wide global -- can carry a previous
    # test's session vector into this test's (unrelated) query and produce
    # a false-positive RRF-fused hit with score 0.0.
    monkeypatch.setenv("CCC_SESSION_FTS_EMBED", "0")

    for mod in (ship_graph, session_fts):
        if hasattr(mod._tls, "conn") and mod._tls.conn:
            try:
                mod._tls.conn.close()
            except Exception:
                pass
            mod._tls.conn = None
    ship_graph._last_sync_ts = 0.0
    session_fts._last_sync_ts = 0.0
    ship_graph._bg_sync_running = False
    session_fts._bg_sync_running = False
    session_fts._ollama_state["ts"] = 0.0
    session_fts._ollama_state["ok"] = False
    session_fts._vec_cache["sids"] = []
    session_fts._vec_cache["vecs"] = []
    # ship_graph.search_sessions() re-ranks a separate dynamically-loaded copy
    # of session_fts with its own TTL clock (see _get_base_search_sessions);
    # force a fresh copy so it isn't skipped as still-warm from a prior test.
    ship_graph._base_search_sessions = None

    return {"repo_dir": repo_dir, "commit_sha": commit_sha, "projects_dir": projects_dir,
            "codex_dir": codex_dir, "tmp_path": tmp_path}


def test_recall_enriches_hits_with_title_repo_date_snippet(mock_memory_env):
    res = memory_api.recall("confetti animation", limit=10)
    assert res["query"] == "confetti animation"
    results = res["results"]
    assert results, "expected at least one hit"
    row = results[0]
    assert row["session_id"] == "session-abc"
    assert row["repo"] == "widget-repo"
    assert row["date"] == "2026-09-20"
    assert row["snippet"]
    assert isinstance(row["title"], str)
    # Every result carries the ticket's four enrichment fields, plus the
    # last-activity time the multi-machine merge (S4) dedupes on. Lease
    # fields appear only for sessions that were handed off.
    assert set(row) == {"session_id", "title", "repo", "date", "snippet", "last_activity_ts"}


def test_recall_empty_query_returns_no_results(mock_memory_env):
    assert memory_api.recall("", limit=10) == {"query": "", "results": [], "indexing": False}


def test_recall_unmatched_query_returns_empty_results(mock_memory_env):
    res = memory_api.recall("quantum flux capacitor teleportation", limit=10)
    assert res["results"] == []


def test_recall_collapses_continuation_chain_to_newest(mock_memory_env):
    """MEMO-FIX-lineage: a session that continued from session-abc must not
    show up as a second, separate row -- it collapses into one hit with
    chain_collapsed pointing at the count folded in."""
    _write_session(mock_memory_env["projects_dir"] / "widget-repo" / "session-abc-2.jsonl", [
        {"type": "user", "cwd": str(mock_memory_env["repo_dir"]), "timestamp": "2026-09-21T10:00:00Z",
         "message": {"role": "user", "content": (
             "You are continuing a task from an earlier session.\n\n"
             "Origin session id: session-abc\n"
             "Task: keep adding confetti animation polish."
         )}},
        {"type": "assistant", "cwd": str(mock_memory_env["repo_dir"]), "timestamp": "2026-09-21T10:01:00Z",
         "message": {"role": "assistant", "content": "Polished the confetti animation timing."}},
    ])
    res = memory_api.recall("confetti animation", limit=10)
    sids = [r["session_id"] for r in res["results"]]
    assert sids.count("session-abc") + sids.count("session-abc-2") == 1
    survivor = next(r for r in res["results"]
                     if r["session_id"] in ("session-abc", "session-abc-2"))
    assert survivor["session_id"] == "session-abc-2"
    assert survivor["chain_collapsed"] == 1
    assert survivor["chain_collapsed_sids"] == ["session-abc"]


def test_shipped_contract_passthrough_with_topic_echo(mock_memory_env):
    res = memory_api.shipped("confetti animation")
    assert res["topic"] == "confetti animation"
    assert res["shipped"] is True
    assert "confidence" in res and "evidence" in res and "tickets" in res


def test_shipped_unknown_topic_not_shipped(mock_memory_env):
    res = memory_api.shipped("quantum flux capacitor teleportation")
    assert res["shipped"] is False
    assert res["topic"] == "quantum flux capacitor teleportation"


def test_shipped_empty_topic_matches_is_shipped_contract(mock_memory_env):
    res = memory_api.shipped("")
    assert res == {"shipped": False, "confidence": 0.0, "evidence": [], "tickets": [], "topic": ""}


def test_file_history_returns_commit_and_session_newest_first(mock_memory_env):
    res = memory_api.file_history(str(mock_memory_env["repo_dir"] / "app.py"))
    assert res["path"] == str(mock_memory_env["repo_dir"] / "app.py")
    assert res["repo"] == "widget-repo"
    kinds = {e["kind"] for e in res["history"]}
    assert "commit" in kinds
    commit_entries = [e for e in res["history"] if e["kind"] == "commit"]
    assert commit_entries[0]["why"] == "feat(widgets): add confetti animation"
    assert commit_entries[0]["hash"]
    # Every entry carries a one-line "why" and is newest-first by date.
    dates = [e["date"] for e in res["history"] if e.get("date")]
    assert dates == sorted(dates, reverse=True)


def test_file_history_repo_relative_path_resolves_against_known_repos(mock_memory_env):
    res = memory_api.file_history("app.py", repo="widget-repo")
    assert res["repo"] == "widget-repo"
    assert any(e["kind"] == "commit" for e in res["history"])


def test_file_history_untracked_path_returns_empty_history(mock_memory_env):
    res = memory_api.file_history(str(mock_memory_env["repo_dir"] / "nope.py"))
    assert res["history"] == []


def test_file_history_empty_path_returns_empty_history(mock_memory_env):
    assert memory_api.file_history("") == {"path": "", "repo": "", "history": []}


def _write_session(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_file_history_finds_sessions_for_non_repo_paths(mock_memory_env):
    """MEMO-FIX-21: any absolute path, in a repo or not, maps to the sessions
    that wrote or read it -- including Codex cwd-relative patch paths."""
    scratch = mock_memory_env["tmp_path"] / "dev" / "scratch" / "study"
    report = scratch / "report.html"
    notes = scratch / "notes.md"
    _write_session(mock_memory_env["projects_dir"] / "scratch" / "writer-1.jsonl", [
        {"type": "user", "cwd": str(scratch), "timestamp": "2026-09-26T10:00:00Z",
         "message": {"role": "user", "content": "write the cost report"}},
        {"type": "assistant", "cwd": str(scratch), "timestamp": "2026-09-26T10:01:00Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Write", "input": {"file_path": str(report), "content": "x"}},
             {"type": "tool_use", "name": "Read", "input": {"file_path": str(notes)}},
         ]}},
    ])
    _write_session(mock_memory_env["projects_dir"] / "scratch" / "reader-2.jsonl", [
        {"type": "user", "cwd": str(scratch), "timestamp": "2026-09-27T10:00:00Z",
         "message": {"role": "user", "content": "review the report"}},
        {"type": "assistant", "cwd": str(scratch), "timestamp": "2026-09-27T10:01:00Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Read", "input": {"file_path": str(report)}},
         ]}},
    ])
    codex_sid = "019f0000-0000-7000-8000-000000000021"
    _write_session(mock_memory_env["codex_dir"] / "2026" / "09" / "27" / f"rollout-x-{codex_sid}.jsonl", [
        {"type": "session_meta", "timestamp": "2026-09-27T11:00:00Z",
         "payload": {"id": codex_sid, "cwd": str(scratch)}},
        {"type": "response_item", "timestamp": "2026-09-27T11:01:00Z",
         "payload": {"type": "custom_tool_call", "name": "apply_patch",
                     "input": "*** Begin Patch\n*** Update File: notes.md\n@@\n-a\n+b\n*** End Patch"}},
    ])
    ship_graph._last_sync_ts = 0.0

    res = memory_api.file_history(str(report))
    assert res["repo"] == ""
    ops = {e["session_id"]: e["op"] for e in res["history"] if e["kind"] == "session"}
    assert ops == {"writer-1": "wrote", "reader-2": "read"}

    res = memory_api.file_history(str(notes))
    ops = {e["session_id"]: e["op"] for e in res["history"] if e["kind"] == "session"}
    assert ops == {"writer-1": "read", codex_sid: "wrote"}


def test_session_reparse_replaces_its_edges_and_files(mock_memory_env):
    _write_session(mock_memory_env["projects_dir"] / "widget-repo" / "ticketed-1.jsonl", [
        {"type": "user", "cwd": str(mock_memory_env["repo_dir"]), "timestamp": "2026-09-22T10:00:00Z",
         "message": {"role": "user", "content": "work on WIDGET-42"}},
        {"type": "assistant", "timestamp": "2026-09-22T10:01:00Z", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/abs/notes.md"}},
        ]}},
    ])
    conn = ship_graph._get_connection()
    ship_graph._sync_all(conn, force=True)

    def counts():
        return (
            conn.execute("SELECT COUNT(*) FROM edges WHERE src = 'ticketed-1'").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM session_files WHERE sid = 'ticketed-1'").fetchone()[0],
        )

    before = counts()
    assert before == (1, 1)
    conn.execute("UPDATE transcripts SET mtime = -1")
    conn.commit()
    ship_graph._sync_all(conn, force=True)
    assert counts() == before


def test_session_files_migration_is_background_only(mock_memory_env, monkeypatch):
    conn = ship_graph._get_connection()
    ship_graph._sync_all(conn, force=True)
    conn.execute("DELETE FROM meta WHERE key = 'session_files_v'")
    conn.commit()
    started = []
    monkeypatch.setattr(ship_graph, "_start_background_sync", lambda: started.append(1))
    monkeypatch.setattr(ship_graph, "_sync_transcripts", lambda *a: pytest.fail("parsed inline"))
    ship_graph._last_sync_ts = 0.0
    ship_graph._sync_all(conn, force=False)
    assert started


def test_recall_points_at_best_matching_section(mock_memory_env):
    filler = "routine refactor progress with nothing notable " * 30
    lines = []
    for i in range(1, 121):
        lines.append({"type": "user", "cwd": str(mock_memory_env["repo_dir"]),
                      "timestamp": "2026-09-21T10:00:00Z",
                      "message": {"role": "user", "content": f"step {i}"}})
        extra = " the wombat ledger came up" if i == 70 else ""
        lines.append({"type": "assistant", "timestamp": "2026-09-21T10:00:01Z",
                      "message": {"role": "assistant", "content": filler + extra}})
    _write_session(mock_memory_env["projects_dir"] / "widget-repo" / "long-1.jsonl", lines)
    res = memory_api.recall("wombat ledger")
    hit = next(r for r in res["results"] if r["session_id"] == "long-1")
    assert hit["match"]["turn"] <= 70 <= hit["match"]["turn_end"]
    assert "[wombat]" in hit["match"]["snippet"].lower()


def test_decisions_filters_to_decision_shaped_snippets(mock_memory_env):
    res = memory_api.decisions("confetti animation")
    assert res["topic"] == "confetti animation"
    # The seeded transcript's snippet ("add a confetti animation on save")
    # carries no decision language, so nothing should pass the filter.
    assert res["results"] == []


def test_decisions_empty_topic_returns_empty_results(mock_memory_env):
    assert memory_api.decisions("") == {"topic": "", "results": []}


def test_decisions_matches_decision_language_in_snippet(mock_memory_env, monkeypatch):
    monkeypatch.setattr(
        memory_api, "_sdoc_rows",
        lambda sids: {sid: {"title": "Pick a queue engine",
                             "snippet": "we decided to go with sqlite instead of postgres"}
                      for sid in sids})
    monkeypatch.setattr(memory_api._sg, "search_sessions",
                        lambda q, limit=20: [{"session_id": "session-abc"}])
    res = memory_api.decisions("queue engine")
    assert res["topic"] == "queue engine"
    assert len(res["results"]) == 1
    assert res["results"][0]["session_id"] == "session-abc"
    assert "instead of" in res["results"][0]["snippet"]
