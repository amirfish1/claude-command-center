"""Tests for ccc_server/where_answer.py: "where are we?" -- `ccc where
<query|sid>` / GET /api/memory/where/<query>. Composes session_brief.brief()
across a lineage chain plus any runbook docs those sessions referenced into
one headless, read-only Claude call, then renders the model's structured
JSON answer into safe HTML.
"""

import json
import unittest
from unittest import mock

import pytest

import ccc_server.lineage as lineage
import ccc_server.report_routes as report_routes
import ccc_server.session_fts as session_fts
import ccc_server.ship_graph as ship_graph
import ccc_server.where_answer as where_answer
import server  # binds ccc_server.where_answer's _core proxy to real names


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _envelope(result_obj_or_str):
    result = result_obj_or_str if isinstance(result_obj_or_str, str) else json.dumps(result_obj_or_str)
    return json.dumps({"result": result, "num_turns": 3, "total_cost_usd": 0.01, "is_error": False})


# -- pure helpers -------------------------------------------------------------

class ReferencedDocPathsTest(unittest.TestCase):
    def test_finds_backtick_wrapped_relative_path_under_repo_root(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "docs", "runbooks"))
            doc_path = os.path.join(d, "docs", "runbooks", "relaunch.md")
            with open(doc_path, "w") as f:
                f.write("# runbook")
            b = {
                "last_assistant_reply": "Follow `docs/runbooks/relaunch.md` for the two manual steps.",
                "last_user_asks": [],
                "files_touched": [],
                "cwd": d,
            }
            found = where_answer._referenced_doc_paths(b, d)
            self.assertEqual(found, [os.path.normpath(doc_path)])

    def test_nonexistent_path_is_dropped(self):
        b = {"last_assistant_reply": "see docs/ghost.md", "last_user_asks": [], "files_touched": []}
        self.assertEqual(where_answer._referenced_doc_paths(b, "/no/such/repo"), [])


class ParseModelAnswerTest(unittest.TestCase):
    def test_parses_plain_json(self):
        raw = json.dumps({
            "status": "Two manual steps remain.",
            "action_items": [{"text": "Create the conversion action", "deep_link": None}],
            "continue_prompt": "Pick up where the runbook left off.",
        })
        parsed = where_answer._parse_model_answer(raw)
        self.assertEqual(parsed["status"], "Two manual steps remain.")
        self.assertEqual(len(parsed["action_items"]), 1)
        self.assertEqual(parsed["continue_prompt"], "Pick up where the runbook left off.")

    def test_strips_markdown_code_fence(self):
        raw = "```json\n" + json.dumps({"status": "ok", "action_items": [], "continue_prompt": ""}) + "\n```"
        parsed = where_answer._parse_model_answer(raw)
        self.assertEqual(parsed["status"], "ok")

    def test_malformed_json_falls_back_to_status_text(self):
        parsed = where_answer._parse_model_answer("not json at all")
        self.assertEqual(parsed["status"], "not json at all")
        self.assertEqual(parsed["action_items"], [])

    def test_action_item_without_text_is_dropped(self):
        raw = json.dumps({"status": "x", "action_items": [{"deep_link": "https://x"}], "continue_prompt": ""})
        parsed = where_answer._parse_model_answer(raw)
        self.assertEqual(parsed["action_items"], [])


class SafeHrefTest(unittest.TestCase):
    def test_allows_https(self):
        self.assertEqual(where_answer._safe_href("https://ads.google.com/x"), "https://ads.google.com/x")

    def test_allows_absolute_path_as_file_link(self):
        self.assertEqual(where_answer._safe_href("/Users/x/runbook.md"), "file:///Users/x/runbook.md")

    def test_rejects_javascript_scheme(self):
        self.assertEqual(where_answer._safe_href("javascript:alert(1)"), "")

    def test_empty_is_empty(self):
        self.assertEqual(where_answer._safe_href(None), "")
        self.assertEqual(where_answer._safe_href(""), "")


class RenderHtmlTest(unittest.TestCase):
    def test_escapes_status_and_action_text(self):
        parsed = {"status": "<script>alert(1)</script>", "action_items": [], "continue_prompt": ""}
        out = where_answer.render_html(parsed, {"briefs": [], "session_id": "abc"})
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_no_action_items_shows_nothing_outstanding(self):
        parsed = {"status": "all done", "action_items": [], "continue_prompt": ""}
        out = where_answer.render_html(parsed, {"briefs": [], "session_id": "abc"})
        self.assertIn("Nothing outstanding", out)
        self.assertNotIn("where-checklist", out)

    def test_action_item_with_safe_link_renders_anchor(self):
        parsed = {
            "status": "s", "continue_prompt": "",
            "action_items": [{"text": "Create the conversion action", "deep_link": "https://ads.google.com"}],
        }
        out = where_answer.render_html(parsed, {"briefs": [], "session_id": "abc"})
        self.assertIn('<a href="https://ads.google.com"', out)
        self.assertIn("Create the conversion action", out)

    def test_unsafe_link_renders_as_plain_text_not_anchor(self):
        parsed = {
            "status": "s", "continue_prompt": "",
            "action_items": [{"text": "do the thing", "deep_link": "javascript:alert(1)"}],
        }
        out = where_answer.render_html(parsed, {"briefs": [], "session_id": "abc"})
        self.assertNotIn("<a href", out)
        self.assertIn("do the thing", out)

    def test_continue_prompt_renders_button_with_escaped_json_payload(self):
        parsed = {"status": "s", "action_items": [], "continue_prompt": 'finish it; say "go"'}
        gathered = {"briefs": [{"cwd": "/repo", "engine": "claude"}], "session_id": "abcdef12", "parent": "orch-1"}
        out = where_answer.render_html(parsed, gathered)
        self.assertIn("where-continue-btn", out)
        self.assertIn("data-spawn-payload=", out)
        self.assertNotIn('"go"', out)  # raw quote must be escaped, not left bare in the attribute

    def test_no_continue_prompt_omits_button(self):
        parsed = {"status": "s", "action_items": [], "continue_prompt": ""}
        out = where_answer.render_html(parsed, {"briefs": [], "session_id": "abc"})
        self.assertNotIn("where-continue-btn", out)


# -- gather_chain / caching / end-to-end --------------------------------------

@pytest.fixture
def mock_where_env(tmp_path, monkeypatch):
    ship_db = tmp_path / "ship_graph.sqlite"
    fts_db = tmp_path / "session_fts.sqlite"
    projects_dir = tmp_path / "projects"
    repo_dir = tmp_path / "widget-repo"
    projects_dir.mkdir(parents=True)
    repo_dir.mkdir(parents=True)

    docs_dir = repo_dir / "docs" / "runbooks"
    docs_dir.mkdir(parents=True)
    runbook_path = docs_dir / "relaunch.md"
    runbook_path.write_text("# Relaunch runbook\n\nCreate the `Demo Booked` conversion action.\n")

    session_dir = projects_dir / "widget-repo"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "where-session-1.jsonl"
    lines = [
        {
            "type": "user", "cwd": str(repo_dir), "timestamp": "2026-09-20T10:00:00Z",
            "message": {"role": "user", "content": "relaunch the ads campaign"},
        },
        {
            "type": "assistant", "cwd": str(repo_dir), "timestamp": "2026-09-20T10:08:00Z",
            "message": {"role": "assistant", "content": (
                "Everything is committed. One thing needs you: follow "
                "`docs/runbooks/relaunch.md` to create the conversion action."
            )},
        },
    ]
    session_file.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")

    monkeypatch.setenv("CCC_SHIP_GRAPH_DB", str(ship_db))
    monkeypatch.setenv("CCC_PROJECTS_ROOT", str(projects_dir))
    monkeypatch.setenv("CCC_CODEX_SESSIONS_ROOT", str(tmp_path / "codex-empty"))
    monkeypatch.setenv("CCC_KIMI_SESSIONS_ROOT", str(tmp_path / "kimi-empty"))
    monkeypatch.setenv("CCC_GEMINI_TMP_ROOT", str(tmp_path / "gemini-empty"))
    monkeypatch.setenv("CCC_CURSOR_PROJECTS_ROOT", str(tmp_path / "cursor-empty"))
    monkeypatch.setenv("CCC_SHIP_GRAPH_DAYS", "0")
    monkeypatch.setenv("CCC_SHIP_GRAPH_REPOS", str(repo_dir))
    monkeypatch.setenv("CCC_SESSION_FTS_DB", str(fts_db))
    monkeypatch.setenv("CCC_SESSION_FTS_DAYS", "0")
    monkeypatch.setenv("CCC_SESSION_FTS_ALLOW_SCRATCH", "1")
    monkeypatch.setenv("CCC_SESSION_FTS_EMBED", "0")
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path / "state"))
    # Isolate the Hermes messages_fts channel so a real ~/.hermes can't
    # answer an unrelated query.
    monkeypatch.setattr(server, "HERMES_STATE_DB", tmp_path / "hermes" / "state.db")
    monkeypatch.setattr(server, "HERMES_PROFILES_DIR", tmp_path / "hermes" / "profiles")

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
    ship_graph._base_search_sessions = None

    with open(lineage._session_graph_path(), "w", encoding="utf-8") as f:
        json.dump({"edges": []}, f)
    with open(report_routes._default_path(), "w", encoding="utf-8") as f:
        json.dump({}, f)

    return {"sid": "where-session-1", "repo_dir": repo_dir, "runbook_path": runbook_path}


def test_gather_chain_finds_referenced_runbook(mock_where_env):
    gathered = where_answer.gather_chain(mock_where_env["sid"])
    self_ = mock_where_env
    assert gathered["found"] is True
    assert gathered["chain_sids"] == [self_["sid"]]
    doc_paths = [d["path"] for d in gathered["docs"]]
    assert str(self_["runbook_path"]) in doc_paths


def test_gather_chain_unknown_query_not_found(mock_where_env):
    gathered = where_answer.gather_chain("totally-unrelated-query-xyz")
    assert gathered["found"] is False


def test_answer_where_end_to_end_with_fake_runner_and_caches(mock_where_env):
    calls = []

    def runner(argv, **kw):
        calls.append(argv)
        return _FakeProc(stdout=_envelope({
            "status": "One manual step remains.",
            "action_items": [{"text": "Create the Demo Booked conversion action", "deep_link": None}],
            "continue_prompt": "Confirm the conversion action was created.",
        }))

    with mock.patch.object(where_answer._core, "_resolve_claude_bin",
                            lambda: {"available": True, "bin": "/x/claude"}):
        out1 = where_answer.answer_where(mock_where_env["sid"], runner=runner)
        assert out1["found"] is True
        assert out1["cached"] is False
        assert "Create the Demo Booked conversion action" in out1["html"]
        assert out1["status"] == "One manual step remains."
        assert out1["action_items"][0]["text"] == "Create the Demo Booked conversion action"
        assert out1["continue_prompt"] == "Confirm the conversion action was created."
        assert len(calls) == 1

        out2 = where_answer.answer_where(mock_where_env["sid"], runner=runner)
        assert out2["cached"] is True
        assert len(calls) == 1  # second call must hit the on-disk cache, not re-invoke claude


def test_call_sonnet_unavailable_claude_raises(mock_where_env):
    with mock.patch.object(where_answer._core, "_resolve_claude_bin",
                            lambda: {"available": False, "reason": "not found"}):
        with pytest.raises(RuntimeError):
            where_answer._call_sonnet("PROMPT", cwd=".", runner=lambda *a, **k: _FakeProc())


# -- argv shape (needs `import server` so _core resolves through it) ---------

class CallSonnetArgvTest(unittest.TestCase):
    def test_argv_is_read_only_and_uses_sonnet(self):
        calls = []

        def runner(argv, **kw):
            calls.append((argv, kw))
            return _FakeProc(stdout=_envelope({"status": "ok", "action_items": [], "continue_prompt": ""}))

        with mock.patch.object(server, "_resolve_claude_bin",
                                lambda: {"available": True, "bin": "/x/claude"}):
            result = where_answer._call_sonnet("PROMPT", cwd="/tmp", runner=runner)

        self.assertEqual(json.loads(result)["status"], "ok")
        argv, kw = calls[0]
        self.assertEqual(argv[0], "/x/claude")
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read,Grep,Glob")
        disallowed_arg = next(a for a in argv if a.startswith("--disallowedTools="))
        for tool in ("Bash", "Write", "Edit", "WebFetch", "WebSearch"):
            self.assertIn(tool, disallowed_arg)
        self.assertEqual(argv[-1], "PROMPT")
        self.assertEqual(kw.get("cwd"), "/tmp")

    def test_nonzero_returncode_raises_with_stderr(self):
        runner = lambda argv, **kw: _FakeProc(returncode=1, stderr="permission denied")
        with mock.patch.object(server, "_resolve_claude_bin",
                                lambda: {"available": True, "bin": "/x/claude"}):
            with self.assertRaises(RuntimeError) as ctx:
                where_answer._call_sonnet("PROMPT", cwd="/tmp", runner=runner)
        self.assertIn("permission denied", str(ctx.exception))
