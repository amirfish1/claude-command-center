"""Decision extraction -> read API -> CLI/MCP, using only temporary data."""
import http.server
from datetime import datetime, timezone
import importlib.machinery
import importlib.util
import json
import sqlite3
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

# Import first: server assembles/reloads its ccc_server modules at import time.
import server
from ccc_server import decision_extraction as dex, mazkir, memory_api


@pytest.fixture
def decision_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_DECISIONS_DB", str(tmp_path / "decisions.sqlite"))
    dex._reset_connection_for_tests()
    # A fallback may search existing indexes, never the host's own sessions.
    monkeypatch.setattr(memory_api, "_decision_hits", lambda *a: [])
    yield tmp_path
    dex._reset_connection_for_tests()


def seed(root, sid="session-choice", repo="widget-repo", quote=None, day="20"):
    p = root / f"{sid}.jsonl"
    rows = [
        {"type": "user", "cwd": f"/repos/{repo}",
         "message": {"role": "user", "content": "Investigate storage options."}},
        {"type": "assistant", "message": {"role": "assistant",
         "content": "We decided to use an unrelated assistant suggestion."}},
        {"type": "user", "timestamp": f"2026-09-{day}T10:00:00Z",
         "message": {"role": "user", "content": quote or
                     "We decided to use SQLite for the queue engine."}},
    ]
    p.write_text("\n".join(map(json.dumps, rows)) + "\n")
    st = p.stat()
    dex.run_once(files=[("claude", str(p), st.st_mtime, st.st_size)])
    return p


def test_quote_search_ranks_coverage_then_recency_and_filters_repo(decision_env):
    seed(decision_env, "session-old", day="19")
    seed(decision_env, "session-new", day="21")
    seed(decision_env, "session-partial", day="22", quote="We decided to pause the queue rewrite.")
    seed(decision_env, "session-other", repo="other-repo", day="23")
    hits = dex.search_decisions("What did we decide about the queue engine?", repo="widget-repo", limit=2)
    assert [r["session_id"] for r in hits] == ["session-new", "session-old"]
    assert hits[0]["source_line"] == 3
    assert dex.search_decisions("nothing-matches-here") == []
    assert dex.search_decisions("What did we decide?") == []


def test_quote_search_applies_topic_before_limit(decision_env):
    seed(decision_env, "session-old", day="01")
    conn = dex._get_connection()
    conn.executemany(
        "INSERT INTO decisions (id, session_id, ts, quote) VALUES (?, ?, ?, ?)",
        [(f"filler-{i}", f"session-{i}", 2_000_000_000 + i,
          "We decided to change the button color.") for i in range(600)],
    )
    conn.commit()
    assert dex.search_decisions("queue engine", limit=1)[0]["session_id"] == "session-old"


def test_query_never_creates_missing_store(decision_env, monkeypatch):
    missing = decision_env / "not-created" / "decisions.sqlite"
    monkeypatch.setenv("CCC_DECISIONS_DB", str(missing))
    assert dex.search_decisions("queue engine") == []
    assert dex.lookup_decision_sessions(["session-choice"]) == {}
    assert not missing.parent.exists()


def test_old_store_is_read_without_migration_then_writer_adds_line(decision_env):
    db = decision_env / "decisions.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE decisions (id TEXT PRIMARY KEY, session_id TEXT, repo TEXT, "
                     "engine TEXT, ts REAL, date TEXT, quote TEXT, pattern TEXT, confidence REAL, "
                     "source_path TEXT, project_dir TEXT, extracted_at REAL)")
        conn.execute("INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     ("old", "session-old", "widget-repo", "claude", 1, "2026-09-01",
                      "We decided to use SQLite for the queue engine.", "strong", 0.9,
                      "/transcripts/session-old.jsonl", "widget-repo", 1))
    assert dex.search_decisions("queue engine")[0]["source_line"] is None
    with sqlite3.connect(db) as conn:
        assert "source_line" not in {r[1] for r in conn.execute("PRAGMA table_info(decisions)")}
    dex._get_connection()
    assert "source_line" in {r[1] for r in dex._get_connection().execute("PRAGMA table_info(decisions)")}


def test_append_and_edit_replace_quotes_without_duplicates(decision_env):
    p = seed(decision_env)
    with p.open("a") as f:
        f.write(json.dumps({"type": "user", "message": {"role": "user",
                      "content": "We decided to use a dark theme."}}) + "\n")
    st = p.stat()
    dex.run_once(files=[("claude", str(p), st.st_mtime, st.st_size)])
    assert len(dex.list_decisions()) == 2
    seed(decision_env, quote="We decided to use Postgres for the queue engine.")
    rows = dex.search_decisions("queue engine")
    assert len(rows) == 1 and "Postgres" in rows[0]["quote"]
    assert dex.search_decisions("dark theme") == []


def test_failed_read_retains_quotes_and_remains_retryable(decision_env):
    p = seed(decision_env)
    p.unlink()
    rec = dex.run_once(files=[("claude", str(p), 999, 999)])
    assert rec["errors"]
    assert len(dex.search_decisions("queue engine")) == 1
    assert dex._get_connection().execute("SELECT mtime FROM scan_state").fetchone()[0] != 999


def test_api_prefers_quotes_and_preserves_existing_result_fields(decision_env, monkeypatch):
    p = seed(decision_env)
    monkeypatch.setattr(memory_api, "_decision_hits", lambda *a: pytest.fail("unnecessary fallback"))
    monkeypatch.setattr(memory_api, "_sdoc_rows", lambda *a: pytest.fail("unnecessary corpus lookup"))
    row = memory_api.decisions("queue engine", repo="widget-repo")["results"][0]
    assert {"session_id", "title", "repo", "date", "snippet"} <= row.keys()
    assert row["provenance"] == "extracted_user_quote"
    assert row["status"] == "candidate"
    assert row["source"] == {"path": str(p), "line": 3, "engine": "claude", "timestamp": datetime(2026, 9, 20, 10, tzinfo=timezone.utc).timestamp()}
    assert 0 < row["confidence"] <= 1


def test_api_labels_fallback_and_keeps_repo_filter(decision_env, monkeypatch):
    monkeypatch.setattr(memory_api, "_decision_hits", lambda *a: [
        {"session_id": "other", "title": "Other", "snippet": "we decided something"},
        {"session_id": "match", "title": "Match", "snippet": "we decided to use SQLite"}])
    monkeypatch.setattr(memory_api._sg, "_get_connection", lambda: None)
    monkeypatch.setattr(memory_api, "_session_meta_rows", lambda *a: {
        "other": {"repo": "other-repo"}, "match": {"repo": "widget-repo"}})
    row, = memory_api.decisions("queue engine", repo="widget-repo", limit=1)["results"]
    assert row["session_id"] == "match"
    assert row["provenance"] == "session_snippet" and row["status"] == "unverified"


def test_ask_resolves_native_decision_citation_without_external_index(decision_env):
    seed(decision_env)
    sources, cited, _ = mazkir.assemble_sources(
        "SQLite [[session:session-choice]] and [[session:session-unknown]]", [],
        db_path=str(decision_env / "missing-index.sqlite"))
    assert cited == ["session-choice"]
    assert sources[0]["repo"] == "widget-repo" and sources[0]["cited"]
    assert "SQLite" in sources[0]["snippet"]


def test_citation_lookup_does_not_let_one_session_starve_others(decision_env):
    seed(decision_env, "session-a")
    seed(decision_env, "session-b")
    conn = dex._get_connection()
    conn.executemany("INSERT INTO decisions (id, session_id, ts, quote) VALUES (?, ?, ?, ?)",
                     [(f"extra-{i}", "session-a", 2_000_000_000 + i, "Another quote") for i in range(20)])
    conn.commit()
    assert set(dex.lookup_decision_sessions(["session-a", "session-b"])) == {"session-a", "session-b"}


def test_mcp_and_cli_over_real_http(decision_env, capsys):
    seed(decision_env)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        state = mazkir.CccState(base)
        result = mazkir.handle_request(state, {"id": 1, "method": "tools/call", "params": {
            "name": "decisions", "arguments": {"topic": "queue & engine", "repo": "widget-repo", "limit": 99}}})
        payload = json.loads(result["result"]["content"][0]["text"])
        assert payload["results"][0]["source"]["line"] == 3
        empty = state.call("decisions", {"topic": "queue engine", "repo": "other-repo"})
        assert empty["results"] == []
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(base + "/api/memory/decisions?topic=")
        assert error.value.code == 400

        loader = importlib.machinery.SourceFileLoader("decision_recall_cli", str(Path(__file__).parents[1] / "ccc"))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        cli = importlib.util.module_from_spec(spec)
        loader.exec_module(cli)
        assert cli.main(["--server", base, "decisions", "queue engine", "--repo", "widget-repo"]) == 0
        out = capsys.readouterr().out
        assert "[user quote (candidate)]" in out and ".jsonl:3" in out and "SQLite" in out
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_mcp_is_available_without_index_and_rejects_missing_topic(decision_env):
    assert "decisions" in {t["name"] for t in mazkir.available_tools()}
    assert "decisions" in mazkir.system_prompt(index_bin=None)
    out = mazkir.handle_request(mazkir.CccState(fetch=lambda p: pytest.fail("invalid HTTP request")),
                               {"id": 1, "method": "tools/call", "params": {"name": "decisions"}})
    assert out["result"]["isError"]


def test_queries_do_no_scans_subprocesses_or_db_writes(decision_env, monkeypatch):
    seed(decision_env)
    dex._reset_connection_for_tests()
    before = (decision_env / "decisions.sqlite").read_bytes()
    for name in ("run_once", "_candidate_files", "extract_decisions_from_file", "_init_db"):
        monkeypatch.setattr(dex, name, lambda *a, **k: pytest.fail("query started extraction"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("query spawned process"))
    for _ in range(3):
        assert dex.search_decisions("queue engine")
        assert dex.lookup_decision_sessions(["session-choice"])
        assert memory_api.decisions("queue engine")["results"]
    assert (decision_env / "decisions.sqlite").read_bytes() == before
