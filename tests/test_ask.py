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


class MergeHitsTest(unittest.TestCase):
    def test_interleaves_and_dedupes_by_session_id(self):
        recent = [{"session_id": "aaa-1", "cwd": "/r/one", "ts_unix": 100, "snippet": "s1"},
                  {"session_id": "bbb-2", "cwd": "/r/two", "ts_unix": 90, "snippet": "s2"}]
        history = [{"session_id": "aaa-1", "cwd": "/r/one", "ts_unix": 100, "snippet": "dup"},
                   {"session_id": "ccc-3", "cwd": "/r/three", "ts_unix": 80, "snippet": "s3"}]
        hits = server.merge_ask_hits(recent, history)
        # i=0 pushes recent aaa-1, skips history dup; i=1 pushes bbb-2 then ccc-3
        self.assertEqual([h["id"] for h in hits], ["aaa-1", "bbb-2", "ccc-3"])
        self.assertEqual(hits[0]["source"], "recent")

    def test_cap_respected(self):
        recent = [{"session_id": f"r-{i}", "snippet": ""} for i in range(20)]
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


class CitationsTest(unittest.TestCase):
    def test_known_ids_extracted_in_order_once(self):
        ans = "See [[session:aaa-1]] and [[session:bbb-2]], mostly [[session:aaa-1]]."
        self.assertEqual(server.parse_ask_citations(ans, ["aaa-1", "bbb-2"]),
                         ["aaa-1", "bbb-2"])

    def test_unknown_ids_ignored(self):
        self.assertEqual(server.parse_ask_citations("[[session:evil-99]]", ["aaa-1"]), [])


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


if __name__ == "__main__":
    unittest.main()
