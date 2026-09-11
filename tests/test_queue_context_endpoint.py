"""Coverage for the linked-conversation queue-context provider plumbing.

The /api/queue/context endpoint resolves a ticket's linked conversation (e.g.
the Becky thread a digest ticket describes) by matching user-configured
extract regexes against the ticket body and running the configured command.
"""

import importlib
import json
import pathlib
import sys
import tempfile
import unittest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

server = importlib.import_module("server")


def _reset_caches():
    server._queue_context_cfg_cache["mtime"] = None
    server._queue_context_cfg_cache["cfg"] = {}
    server._queue_context_result_cache.clear()


class TestQueueContextLookup(unittest.TestCase):
    def setUp(self):
        self._orig_file = server._QUEUE_CONTEXT_FILE
        self._tmp = tempfile.TemporaryDirectory()
        self.cfg_path = pathlib.Path(self._tmp.name) / "queue-context.json"
        server._QUEUE_CONTEXT_FILE = self.cfg_path
        _reset_caches()

    def tearDown(self):
        server._QUEUE_CONTEXT_FILE = self._orig_file
        _reset_caches()
        self._tmp.cleanup()

    def _write_cfg(self, cfg):
        self.cfg_path.write_text(json.dumps(cfg))

    def _echo_provider(self, payload, extract=r"(owner:[0-9a-f-]{36})"):
        return {
            "extract": extract,
            "command": [
                sys.executable, "-c",
                "import json; print(json.dumps(%r))" % (payload,),
            ],
            "cache_s": 300,
        }

    def test_unconfigured_queue_reports_configured_false(self):
        item = {"project": "BECKY", "text": "anything"}
        out = server._queue_context_lookup(item)
        self.assertTrue(out["ok"])
        self.assertFalse(out["configured"])
        self.assertFalse(out["found"])

    def test_no_key_in_body_reports_found_false(self):
        self._write_cfg({"BECKY": [self._echo_provider({"ok": True})]})
        item = {"project": "BECKY", "text": "no conversation key here"}
        out = server._queue_context_lookup(item)
        self.assertTrue(out["ok"])
        self.assertTrue(out["configured"])
        self.assertFalse(out["found"])

    def test_key_in_github_body_resolves_transcript(self):
        transcript = {"ok": True, "kind": "owner",
                      "turns": [{"role": "owner", "text": "hi", "at": "2026-08-30T00:00:00Z"}]}
        self._write_cfg({"BECKY": [self._echo_provider(transcript)]})
        key = "owner:52af855b-1bfd-4006-ab46-e5e7ed720ab8"
        item = {"project": "BECKY", "text": "truncated…",
                "_github_body": "## Backend pointers\n- conversationKey: " + key}
        out = server._queue_context_lookup(item)
        self.assertTrue(out["ok"])
        self.assertTrue(out["found"])
        self.assertEqual(out["key"], key)
        self.assertEqual(out["transcript"]["turns"][0]["text"], "hi")

    def test_provider_failure_reports_found_with_error(self):
        self._write_cfg({"BECKY": [{
            "extract": r"(owner:[0-9a-f-]{36})",
            "command": [sys.executable, "-c", "print('not json')"],
        }]})
        item = {"project": "BECKY",
                "text": "conversationKey: owner:52af855b-1bfd-4006-ab46-e5e7ed720ab8"}
        out = server._queue_context_lookup(item)
        self.assertFalse(out["ok"])
        self.assertTrue(out["found"])
        self.assertIn("invalid JSON", out["error"])

    def test_result_is_cached_per_key(self):
        transcript = {"ok": True, "turns": []}
        marker = pathlib.Path(self._tmp.name) / "runs"
        self._write_cfg({"BECKY": [{
            "extract": r"(owner:[0-9a-f-]{36})",
            "command": [
                sys.executable, "-c",
                "import json, pathlib; p = pathlib.Path(%r); "
                "p.write_text(p.read_text() + 'x' if p.exists() else 'x'); "
                "print(json.dumps(%r))" % (str(marker), transcript),
            ],
            "cache_s": 300,
        }]})
        item = {"project": "BECKY",
                "text": "owner:52af855b-1bfd-4006-ab46-e5e7ed720ab8"}
        first = server._queue_context_lookup(item)
        second = server._queue_context_lookup(item)
        self.assertTrue(first["ok"])
        self.assertEqual(first, second)
        self.assertEqual(marker.read_text(), "x")

    def test_wildcard_queue_providers_apply(self):
        transcript = {"ok": True, "turns": [{"role": "system", "text": "t", "at": ""}]}
        self._write_cfg({"*": [self._echo_provider(transcript)]})
        item = {"project": "OTHERQ",
                "text": "owner:52af855b-1bfd-4006-ab46-e5e7ed720ab8"}
        out = server._queue_context_lookup(item)
        self.assertTrue(out["ok"])
        self.assertTrue(out["found"])


class TestTicketProseWiring(unittest.TestCase):
    """Static wiring: both ticket views load the shared renderer."""

    def test_both_pages_include_ticket_prose_assets(self):
        for page in ("index.html", "q2.html"):
            html = (PROJECT_ROOT / "static" / page).read_text(encoding="utf-8")
            self.assertIn("/static/ticket-prose.js", html, page)
            self.assertIn("/static/ticket-prose.css", html, page)

    def test_both_views_prefer_full_github_body(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
        self.assertIn("item._github_body || item.text", app_js)
        self.assertIn("item._github_body || item.text", q2_js)

    def test_both_views_render_via_shared_prose_renderer(self):
        app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
        self.assertIn("CCCTicketProse.render(", app_js)
        self.assertIn("CCCTicketProse.render(", q2_js)
        self.assertIn("CCCTicketProse.renderTranscript(", app_js)
        self.assertIn("CCCTicketProse.renderTranscript(", q2_js)

    def test_machine_comment_never_becomes_the_title(self):
        tp = (PROJECT_ROOT / "static" / "ticket-prose.js").read_text(encoding="utf-8")
        q2_js = (PROJECT_ROOT / "static" / "q2.js").read_text(encoding="utf-8")
        self.assertIn("cleanForTitle", tp)
        self.assertIn("digest-finding-id", tp)
        # q2's titleOf strips HTML comments before deriving the row title.
        self.assertIn("replace(/<!--[\\s\\S]*?-->/g", q2_js)


if __name__ == "__main__":
    unittest.main()
