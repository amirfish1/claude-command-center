"""Unit tests for ccc_server/ask.py — the Ask tab's pure core.

No subprocesses, no _core access: every function under test either takes
its inputs explicitly (titles/live_ids injection) or is a pure transform.
"""
import os
import unittest
from unittest import mock

import server  # binds ccc_server.ask names onto the server module


class ExtractTermsTest(unittest.TestCase):
    def test_drops_stopwords_keeps_topic_words(self):
        self.assertEqual(server.extract_ask_terms("Where did I work on the BYM ads?"),
                         ["bym", "ads"])

    def test_all_stopwords_falls_back_to_raw_words(self):
        # A pure-stopword question must not return [] (that would skip retrieval
        # silently); fall back to the raw words.
        terms = server.extract_ask_terms("what is the status of that")
        self.assertTrue(terms)

    def test_empty_question(self):
        self.assertEqual(server.extract_ask_terms(""), [])


class FileQuestionHintTest(unittest.TestCase):
    def test_extension_word_triggers(self):
        self.assertTrue(server.looks_like_file_question(
            "where did I store the shot 2 BYM .mov"))

    def test_generic_file_word_triggers(self):
        self.assertTrue(server.looks_like_file_question("where's that screenshot?"))

    def test_ordinary_session_question_does_not_trigger(self):
        self.assertFalse(server.looks_like_file_question("what did I work on yesterday"))


class SearchFilesystemHitsTest(unittest.TestCase):
    def test_ands_filename_terms_and_scopes_to_home(self):
        calls = []

        def runner(argv, **kw):
            calls.append(argv)
            return _FakeProc(stdout="/Users/x/Desktop/Shot 3 - BYM.mov\n")

        paths = server.search_filesystem_hits(["shot", "bym", "mov"], runner=runner)
        self.assertEqual(paths, ["/Users/x/Desktop/Shot 3 - BYM.mov"])
        argv = calls[0]
        self.assertEqual(argv[0], "mdfind")
        self.assertIn("-onlyin", argv)
        self.assertIn(
            "kMDItemFSName == '*shot*'c && kMDItemFSName == '*bym*'c && kMDItemFSName == '*mov*'c",
            argv)

    def test_no_terms_skips_subprocess(self):
        called = []
        self.assertEqual(server.search_filesystem_hits([], runner=lambda *a, **k: called.append(1)), [])
        self.assertFalse(called)

    def test_nonzero_returncode_is_empty(self):
        runner = lambda argv, **kw: _FakeProc(returncode=1, stderr="mdfind disabled")
        self.assertEqual(server.search_filesystem_hits(["x"], runner=runner), [])

    def test_timeout_is_empty(self):
        import subprocess as _sp

        def runner(argv, **kw):
            raise _sp.TimeoutExpired(cmd="mdfind", timeout=4)

        self.assertEqual(server.search_filesystem_hits(["x"], runner=runner), [])

    def test_cap_respected(self):
        runner = lambda argv, **kw: _FakeProc(stdout="\n".join(f"/f{i}" for i in range(20)))
        self.assertEqual(len(server.search_filesystem_hits(["x"], limit=3, runner=runner)), 3)


class TriggerQuestionHintTest(unittest.TestCase):
    def test_trigger_word_triggers(self):
        self.assertTrue(server.looks_like_trigger_question(
            "what triggers my BYM pre-flight check, a GitHub push?"))

    def test_cron_word_triggers(self):
        self.assertTrue(server.looks_like_trigger_question("is this a cron job?"))

    def test_ordinary_session_question_does_not_trigger(self):
        self.assertFalse(server.looks_like_trigger_question("what did I work on yesterday"))


class SearchLocalAutomationHitsTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        # search_local_automation_hits reads the module-level constant as a
        # global at call time out of ccc_server.ask's OWN __dict__ — adoption
        # onto `server` copies the value once, it doesn't alias the name, so
        # patching server._LAUNCH_AGENTS_DIR would not reach the function.
        from ccc_server import ask as ask_module
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.launch_agents = Path(self._tmp.name) / "LaunchAgents"
        self.launch_agents.mkdir()
        self.repo = Path(self._tmp.name) / "repo"
        (self.repo / ".github" / "workflows").mkdir(parents=True)
        self._patch = mock.patch.object(ask_module, "_LAUNCH_AGENTS_DIR", self.launch_agents)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_finds_plist_by_hyphen_insensitive_subject_match_and_extracts_trigger_keys(self):
        (self.launch_agents / "com.amirfish.bym-perf-preflight.plist").write_text(
            "<plist><dict>\n"
            "<key>Label</key><string>com.amirfish.bym-perf-preflight</string>\n"
            "<key>StartInterval</key><integer>3600</integer>\n"
            "</dict></plist>\n")
        (self.launch_agents / "com.amirfish.unrelated.plist").write_text(
            "<plist><dict><key>Label</key><string>com.amirfish.unrelated</string></dict></plist>\n")
        terms = server.extract_ask_terms(
            "what triggers my BYM pre-flight check, a GitHub push?")
        hits = server.search_local_automation_hits(terms, repo_roots=[])
        self.assertEqual(len(hits), 1)
        self.assertIn("bym-perf-preflight.plist", hits[0]["path"])
        self.assertIn("StartInterval", hits[0]["snippet"])
        self.assertIn("3600", hits[0]["snippet"])

    def test_no_subject_match_is_empty(self):
        (self.launch_agents / "com.amirfish.unrelated.plist").write_text(
            "<plist><dict><key>Label</key><string>com.amirfish.unrelated</string></dict></plist>\n")
        terms = server.extract_ask_terms("what triggers my BYM pre-flight check?")
        self.assertEqual(server.search_local_automation_hits(terms, repo_roots=[]), [])

    def test_finds_github_workflow_trigger_block(self):
        wf = self.repo / ".github" / "workflows" / "bym-deploy.yml"
        wf.write_text(
            "name: bym-deploy\n"
            "on:\n"
            "  push:\n"
            "    branches: [main]\n"
            "jobs:\n"
            "  deploy:\n"
            "    runs-on: ubuntu-latest\n")
        terms = server.extract_ask_terms("what triggers the bym deploy workflow?")
        hits = server.search_local_automation_hits(terms, repo_roots=[str(self.repo)])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["kind"], "GitHub workflow")
        self.assertIn("push", hits[0]["snippet"])
        self.assertNotIn("runs-on", hits[0]["snippet"])

    def test_no_terms_returns_empty(self):
        self.assertEqual(server.search_local_automation_hits([], repo_roots=[]), [])


class MergeHitsTest(unittest.TestCase):
    def test_interleaves_dedupes_then_sorts_newest_first(self):
        # bbb-2 is newest (ts_unix 200) despite arriving second in the
        # interleave order, so the sort — not the interleave position — must
        # decide the final order.
        recent = [{"session_id": "aaa-1", "cwd": "/r/one", "ts_unix": 100, "snippet": "s1"},
                  {"session_id": "bbb-2", "cwd": "/r/two", "ts_unix": 200, "snippet": "s2"}]
        history = [{"session_id": "aaa-1", "cwd": "/r/one", "ts_unix": 100, "snippet": "dup"},
                   {"session_id": "ccc-3", "cwd": "/r/three", "ts_unix": 80, "snippet": "s3"}]
        hits = server.merge_ask_hits(recent, history)
        self.assertEqual([h["id"] for h in hits], ["bbb-2", "aaa-1", "ccc-3"])
        self.assertEqual(hits[0]["source"], "recent")

    def test_older_recent_hit_ranks_below_newer_history_hit(self):
        recent = [{"session_id": "old-recent", "cwd": "/r/one", "ts_unix": 10, "snippet": "s1"}]
        history = [{"session_id": "new-history", "cwd": "/r/two", "ts_unix": 9999, "snippet": "s2"}]
        hits = server.merge_ask_hits(recent, history)
        self.assertEqual([h["id"] for h in hits], ["new-history", "old-recent"])

    def test_missing_ts_unix_sorts_as_oldest(self):
        recent = [{"session_id": "no-ts", "cwd": "/r/one", "snippet": "s1"},
                  {"session_id": "has-ts", "cwd": "/r/two", "ts_unix": 5, "snippet": "s2"}]
        hits = server.merge_ask_hits(recent, [])
        self.assertEqual([h["id"] for h in hits], ["has-ts", "no-ts"])

    def test_cap_respected(self):
        recent = [{"session_id": f"r-{i}", "snippet": "", "ts_unix": i} for i in range(20)]
        self.assertEqual(len(server.merge_ask_hits(recent, [], cap=5)), 5)

    def test_snippet_marks_stripped_and_truncated(self):
        recent = [{"session_id": "x-1", "snippet": "<mark>bym</mark> " + "a" * 500}]
        hit = server.merge_ask_hits(recent, [])[0]
        self.assertNotIn("<mark>", hit["snippet"])
        self.assertLessEqual(len(hit["snippet"]), 220)


class EnrichHitsTest(unittest.TestCase):
    def test_titles_status_repo_from_injected_maps(self):
        hits = [{"id": "aaa-1", "cwd": "/Users/x/Apps/myrepo", "ts_unix": 1, "snippet": "", "source": "recent"}]
        out = server.enrich_ask_hits(hits, titles={"aaa-1": "BYM ads campaign"}, live_ids={"aaa-1"})
        self.assertEqual(out[0]["title"], "BYM ads campaign")
        self.assertEqual(out[0]["repo"], "myrepo")
        self.assertEqual(out[0]["status"], "live")

    def test_missing_title_and_idle_status(self):
        hits = [{"id": "bbb-2", "cwd": "", "ts_unix": 1, "snippet": "", "source": "history"}]
        out = server.enrich_ask_hits(hits, titles={}, live_ids=set())
        self.assertEqual(out[0]["title"], "")
        self.assertEqual(out[0]["status"], "idle")


class PromptTest(unittest.TestCase):
    def test_prompt_contains_citation_rule_hits_and_question(self):
        hits = [{"id": "aaa-1", "cwd": "/r/x", "ts_unix": None, "snippet": "worked on ads",
                 "source": "recent", "title": "BYM ads", "repo": "x", "status": "idle"}]
        p = server.build_ask_prompt("where are the ads?", [], hits)
        self.assertIn("[[session:aaa-1]]", p)
        self.assertIn("where are the ads?", p)
        self.assertIn("[[session:ID]]", p)  # the citation instruction

    def test_history_folded_and_capped(self):
        history = [{"q": f"q{i}", "a": f"a{i}"} for i in range(10)]
        p = server.build_ask_prompt("next?", history, [])
        self.assertNotIn("q0", p)   # only the last 4 turns survive
        self.assertIn("q9", p)

    def test_no_hits_says_none_found(self):
        self.assertIn("(none found)", server.build_ask_prompt("q", [], []))

    def test_prompt_instructs_recency_preference(self):
        p = server.build_ask_prompt("q", [], [])
        self.assertIn("Prefer the most recent sessions", p)

    def test_fs_hits_none_omits_file_section(self):
        p = server.build_ask_prompt("q", [], [], fs_hits=None)
        self.assertNotIn("File matches", p)

    def test_fs_hits_listed_when_present(self):
        p = server.build_ask_prompt("q", [], [], fs_hits=["/Users/x/Desktop/Shot 3 - BYM.mov"])
        self.assertIn("File matches", p)
        self.assertIn("/Users/x/Desktop/Shot 3 - BYM.mov", p)

    def test_automation_hits_none_omits_section(self):
        p = server.build_ask_prompt("q", [], [], automation_hits=None)
        self.assertNotIn("Automation configs", p)

    def test_automation_hits_listed_when_present(self):
        p = server.build_ask_prompt("q", [], [], automation_hits=[
            {"path": "/x/com.amirfish.bym-perf-preflight.plist", "kind": "launchd job",
             "snippet": "StartInterval 3600"}])
        self.assertIn("Automation configs", p)
        self.assertIn("com.amirfish.bym-perf-preflight.plist", p)
        self.assertIn("StartInterval 3600", p)

    def test_automation_hits_empty_says_none_found(self):
        p = server.build_ask_prompt("q", [], [], automation_hits=[])
        self.assertIn("Automation configs", p)
        self.assertIn("(none found)", p)

    def test_fs_hits_empty_list_says_none_found(self):
        p = server.build_ask_prompt("q", [], [], fs_hits=[])
        self.assertIn("File matches", p)
        self.assertIn("(none found)", p)


class CitationsTest(unittest.TestCase):
    def test_known_ids_extracted_in_order_once(self):
        ans = "See [[session:aaa-1]] and [[session:bbb-2]], mostly [[session:aaa-1]]."
        self.assertEqual(server.parse_ask_citations(ans, ["aaa-1", "bbb-2"]),
                         ["aaa-1", "bbb-2"])

    def test_unknown_ids_ignored(self):
        self.assertEqual(server.parse_ask_citations("[[session:evil-99]]", ["aaa-1"]), [])


class ActionsTest(unittest.TestCase):
    def test_spawn_continue_marker_parsed_for_known_ids_only(self):
        ans = "I can continue it: [[action:spawn-continue:aaa-1]] [[action:spawn-continue:zzz-9]]"
        self.assertEqual(server.parse_ask_actions(ans, ["aaa-1"]), ["aaa-1"])

    def test_prompt_mentions_action_marker(self):
        p = server.build_ask_prompt("continue it", [], [])
        self.assertIn("[[action:spawn-continue:ID]]", p)


class EngineSelectTest(unittest.TestCase):
    def _fake_resolvers(self, agy=None, claude=None):
        # Patch on the server module: _core resolves attributes there at call time.
        return (mock.patch.object(server, "_resolve_antigravity_bin",
                                  lambda: agy or {"available": False}),
                mock.patch.object(server, "_resolve_claude_bin",
                                  lambda: claude or {"available": False}))

    def test_prefers_agy_when_available(self):
        p1, p2 = self._fake_resolvers(agy={"available": True, "bin": "/x/agy"},
                                      claude={"available": True, "bin": "/x/claude"})
        with p1, p2, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCC_ASK_ENGINE", None); os.environ.pop("CCC_ASK_MODEL", None)
            eng = server.select_ask_engine()
        self.assertEqual(eng["engine"], "antigravity")
        self.assertEqual(eng["model"], "gemini-3.7-flash-low")

    def test_falls_back_to_claude(self):
        p1, p2 = self._fake_resolvers(claude={"available": True, "bin": "/x/claude"})
        with p1, p2:
            os.environ.pop("CCC_ASK_ENGINE", None); os.environ.pop("CCC_ASK_MODEL", None)
            eng = server.select_ask_engine()
        self.assertEqual(eng["engine"], "claude")
        self.assertEqual(eng["model"], "haiku")

    def test_env_overrides(self):
        p1, p2 = self._fake_resolvers(claude={"available": True, "bin": "/x/claude"})
        with p1, p2, mock.patch.dict(os.environ, {"CCC_ASK_ENGINE": "claude",
                                                  "CCC_ASK_MODEL": "claude-sonnet-5"}):
            eng = server.select_ask_engine()
        self.assertEqual(eng["model"], "claude-sonnet-5")

    def test_none_available_is_typed_error(self):
        p1, p2 = self._fake_resolvers()
        with p1, p2:
            os.environ.pop("CCC_ASK_ENGINE", None)
            eng = server.select_ask_engine()
        self.assertFalse(eng["available"])
        self.assertEqual(eng["code"], "ask_engine_unavailable")


class ArgvTest(unittest.TestCase):
    def test_agy_argv_is_lightweight(self):
        argv = server.ask_engine_argv(
            {"engine": "antigravity", "bin": "/x/agy", "model": "gemini-3.7-flash-low"}, "PROMPT")
        self.assertEqual(argv, ["/x/agy", "--print", "PROMPT",
                                "--model", "gemini-3.7-flash-low",
                                "--effort", "low", "--disable-slash-commands"])

    def test_claude_argv_skips_mcp(self):
        argv = server.ask_engine_argv(
            {"engine": "claude", "bin": "/x/claude", "model": "haiku"}, "PROMPT")
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[-1], "PROMPT")


class _FakeProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


class HandleAskTest(unittest.TestCase):
    """handle_assistant_ask with every external seam injected: search
    functions patched on the server module, subprocess runner injected."""

    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"CCC_ASK_ENGINE": "claude"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(lambda: os.environ.pop("CCC_ASK_ENGINE", None))
        self.recent_calls = []
        self.history_calls = []

        def _recent(q, days=2, limit=20, cwd_like=None):
            self.recent_calls.append({"days": days, "limit": limit})
            return {"results": [{"session_id": "aaa-1", "cwd": "/r/x",
                                  "ts_unix": 100, "snippet": "bym ads work"}]}

        def _hist(q, limit=20, cwd_like=None, since=None, semantic=False):
            self.history_calls.append({"since": since})
            return {"results": []}

        patches = [
            mock.patch.object(server, "_resolve_claude_bin",
                              lambda: {"available": True, "bin": "/x/claude"}),
            mock.patch.object(server, "search_recent_sessions", _recent),
            mock.patch.object(server, "search_conversation_history", _hist),
            mock.patch.object(server, "_auto_titled_session_ids", lambda: {"aaa-1": "BYM ads"}),
            mock.patch.object(server, "_discover_live_session_ids", lambda: set()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_happy_path_cites_and_sources(self):
        runner = lambda argv, **kw: _FakeProc(stdout="You did it in [[session:aaa-1]].")
        body, status = server.handle_assistant_ask({"question": "where bym ads?"}, runner=runner)
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertIn("[[session:aaa-1]]", body["answer"])
        self.assertEqual(body["sources"][0]["id"], "aaa-1")
        self.assertEqual(body["sources"][0]["title"], "BYM ads")
        self.assertEqual(body["sources"][0]["snippet"], "bym ads work")
        self.assertEqual(body["engine"], "claude")
        self.assertEqual(body["hit_count"], 1)

    def test_range_maps_to_days_and_since(self):
        runner = lambda argv, **kw: _FakeProc(stdout="ok")
        server.handle_assistant_ask({"question": "bym ads", "range": "24h"}, runner=runner)
        self.assertEqual(self.recent_calls[-1]["days"], 1)
        self.assertIsNotNone(self.history_calls[-1]["since"])

    def test_range_any_means_no_since_and_max_days(self):
        runner = lambda argv, **kw: _FakeProc(stdout="ok")
        server.handle_assistant_ask({"question": "bym ads", "range": "any"}, runner=runner)
        self.assertEqual(self.recent_calls[-1]["days"], 30)
        self.assertIsNone(self.history_calls[-1]["since"])

    def test_file_question_wires_filesystem_hits_into_prompt(self):
        prompts = []

        def runner(argv, **kw):
            prompts.append(argv[-1])
            return _FakeProc(stdout="It's at /Users/x/Desktop/Shot 3 - BYM.mov")

        with mock.patch.object(server, "search_filesystem_hits",
                                lambda terms, **kw: ["/Users/x/Desktop/Shot 3 - BYM.mov"]):
            body, status = server.handle_assistant_ask(
                {"question": "where did I store the shot 2 BYM .mov"}, runner=runner)
        self.assertEqual(status, 200)
        self.assertIn("File matches", prompts[-1])
        self.assertIn("/Users/x/Desktop/Shot 3 - BYM.mov", prompts[-1])

    def test_ordinary_question_skips_filesystem_lookup(self):
        called = []
        prompts = []

        def runner(argv, **kw):
            prompts.append(argv[-1])
            return _FakeProc(stdout="ok")

        with mock.patch.object(server, "search_filesystem_hits",
                                lambda terms, **kw: called.append(terms) or []):
            server.handle_assistant_ask({"question": "bym ads"}, runner=runner)
        self.assertFalse(called)
        self.assertNotIn("File matches", prompts[-1])

    def test_trigger_question_wires_automation_hits_into_prompt(self):
        prompts = []

        def runner(argv, **kw):
            prompts.append(argv[-1])
            return _FakeProc(stdout="It runs hourly via launchd, not a GitHub push.")

        with mock.patch.object(server, "search_local_automation_hits",
                                lambda terms, repo_roots=None, **kw: [
                                    {"path": "/x/com.amirfish.bym-perf-preflight.plist",
                                     "kind": "launchd job", "snippet": "StartInterval 3600"}]):
            body, status = server.handle_assistant_ask(
                {"question": "what triggers my BYM pre-flight check?"}, runner=runner)
        self.assertEqual(status, 200)
        self.assertIn("Automation configs", prompts[-1])
        self.assertIn("StartInterval 3600", prompts[-1])

    def test_ordinary_question_skips_automation_lookup(self):
        called = []
        runner = lambda argv, **kw: _FakeProc(stdout="ok")
        with mock.patch.object(server, "search_local_automation_hits",
                                lambda terms, **kw: called.append(terms) or []):
            server.handle_assistant_ask({"question": "bym ads"}, runner=runner)
        self.assertFalse(called)

    def test_default_range_matches_prior_behavior(self):
        runner = lambda argv, **kw: _FakeProc(stdout="ok")
        server.handle_assistant_ask({"question": "bym ads"}, runner=runner)
        self.assertEqual(self.recent_calls[-1]["days"], 14)
        self.assertIsNone(self.history_calls[-1]["since"])

    def test_empty_question_400(self):
        body, status = server.handle_assistant_ask({"question": "  "})
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "ask_bad_request")

    def test_engine_failure_502(self):
        runner = lambda argv, **kw: _FakeProc(stderr="boom", returncode=1)
        body, status = server.handle_assistant_ask({"question": "q"}, runner=runner)
        self.assertEqual(status, 502)
        self.assertEqual(body["code"], "ask_engine_error")

    def test_timeout_504(self):
        import subprocess as sp
        def runner(argv, **kw):
            raise sp.TimeoutExpired(argv, kw.get("timeout", 0))
        body, status = server.handle_assistant_ask({"question": "q"}, runner=runner)
        self.assertEqual(status, 504)
        self.assertEqual(body["code"], "ask_timeout")

    def test_no_engine_503(self):
        with mock.patch.object(server, "_resolve_claude_bin", lambda: {"available": False}):
            body, status = server.handle_assistant_ask({"question": "q"})
        self.assertEqual(status, 503)
        self.assertEqual(body["code"], "ask_engine_unavailable")

    def test_search_layer_crash_degrades_to_no_hits(self):
        calls = {"recent": 0, "hist": 0}

        def _boom_recent(*a, **kw):
            calls["recent"] += 1
            raise RuntimeError("index locked")

        def _boom_hist(*a, **kw):
            calls["hist"] += 1
            raise RuntimeError("index locked")

        with mock.patch.object(server, "search_recent_sessions", _boom_recent), \
             mock.patch.object(server, "search_conversation_history", _boom_hist):
            runner = lambda argv, **kw: _FakeProc(stdout="No matching sessions found.")
            body, status = server.handle_assistant_ask(
                {"question": "database sessions"}, runner=runner)
        self.assertEqual(status, 200)
        self.assertEqual(body["sources"], [])
        # The point of this test is that the crash path was actually reached
        # (a non-empty query must survive extract_ask_terms, or these mocks
        # never fire and the test would pass for the wrong reason).
        self.assertEqual(calls, {"recent": 1, "hist": 1})


if __name__ == "__main__":
    unittest.main()
