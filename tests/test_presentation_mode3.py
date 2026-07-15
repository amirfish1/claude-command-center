import json
import unittest

import server


class PresentationMode3Tests(unittest.TestCase):
    def valid(self):
        return {
            "version": 1,
            "deck_title": "Refresh behavior",
            "theme": "cyan",
            "slides": [
                {
                    "id": "cause",
                    "layout": "statement",
                    "title": "The key collided",
                    "subtitle": "Resize exposed it.",
                    "statement": "Slide keys were only unique inside one answer.",
                },
                {
                    "id": "fix",
                    "layout": "bullets",
                    "title": "The fix",
                    "items": [
                        "Scope keys by answer",
                        "Follow only the old tail",
                    ],
                },
            ],
        }

    def fenced(self, value):
        return "Human prose.\n\n```ccc-slides\n" + json.dumps(value) + "\n```"

    def test_extracts_terminal_artifact_and_preserves_prose(self):
        prose, artifact, error = server._extract_presentation_artifact(
            self.fenced(self.valid())
        )
        self.assertEqual(prose, "Human prose.")
        self.assertEqual(artifact["slides"][1]["id"], "fix")
        self.assertEqual(error, "")

    def test_discards_unknown_keys_in_valid_artifact(self):
        value = self.valid()
        value["ignored"] = "top-level"
        value["slides"][0]["ignored"] = "slide-level"
        _prose, artifact, error = server._extract_presentation_artifact(
            self.fenced(value)
        )
        self.assertEqual(error, "")
        self.assertNotIn("ignored", artifact)
        self.assertNotIn("ignored", artifact["slides"][0])

    def test_rejects_unknown_layout_duplicate_ids_and_nine_slides(self):
        unknown = self.valid()
        unknown["slides"][0]["layout"] = "html"
        duplicate = self.valid()
        duplicate["slides"][1]["id"] = "cause"
        too_many = self.valid()
        too_many["slides"] = [
            {
                "id": "s" + str(i),
                "layout": "statement",
                "title": str(i),
                "statement": str(i),
            }
            for i in range(9)
        ]
        for value in (unknown, duplicate, too_many):
            with self.subTest(value=value):
                prose, artifact, error = server._extract_presentation_artifact(
                    self.fenced(value)
                )
                self.assertEqual(prose, "Human prose.")
                self.assertIsNone(artifact)
                self.assertTrue(error)

    def test_rejects_malformed_oversized_or_active_content(self):
        malformed = "Human prose.\n\n```ccc-slides\n{not-json}\n```"
        prose, artifact, error = server._extract_presentation_artifact(malformed)
        self.assertEqual(prose, "Human prose.")
        self.assertIsNone(artifact)
        self.assertEqual(error, "invalid_json")

        oversized = self.valid()
        oversized["slides"][0]["statement"] = "x" * 321
        active = self.valid()
        active["slides"][0]["statement"] = "<script>alert(1)</script>"
        for value in (oversized, active):
            with self.subTest(value=value):
                _prose, artifact, error = server._extract_presentation_artifact(
                    self.fenced(value)
                )
                self.assertIsNone(artifact)
                self.assertTrue(error)

    def test_nonterminal_fence_is_not_extracted(self):
        text = self.fenced(self.valid()) + "\nTrailing text"
        prose, artifact, error = server._extract_presentation_artifact(text)
        self.assertEqual(prose, text.strip())
        self.assertIsNone(artifact)
        self.assertEqual(error, "")

    def test_claude_parser_attaches_artifact_and_keeps_prose(self):
        event = {
            "type": "assistant",
            "timestamp": "2026-07-15T00:00:00Z",
            "message": {
                "id": "msg-mode3",
                "content": [{"type": "text", "text": self.fenced(self.valid())}],
            },
        }
        parsed = server._parse_conversation_event(event, 7)
        self.assertEqual(parsed["blocks"], [{"kind": "text", "text": "Human prose."}])
        self.assertEqual(parsed["presentation_artifact"]["deck_title"], "Refresh behavior")
        self.assertNotIn("presentation_artifact_error", parsed)

    def test_artifact_only_claude_bootstrap_survives_parsing(self):
        text = "```ccc-slides\n" + json.dumps(self.valid()) + "\n```"
        event = {
            "type": "assistant",
            "message": {"id": "msg-bootstrap", "content": [{"type": "text", "text": text}]},
        }
        parsed = server._parse_conversation_event(event, 8)
        self.assertEqual(parsed["blocks"], [])
        self.assertEqual(parsed["presentation_artifact"]["slides"][0]["id"], "cause")

    def test_codex_parser_attaches_artifact_and_keeps_prose(self):
        event = {
            "type": "event_msg",
            "timestamp": "2026-07-15T00:00:00Z",
            "payload": {"type": "agent_message", "message": self.fenced(self.valid())},
        }
        parsed = server._parse_codex_event(event, 9)
        self.assertEqual(parsed["blocks"], [{"kind": "text", "text": "Human prose."}])
        self.assertEqual(parsed["presentation_artifact"]["slides"][1]["id"], "fix")

    def test_invalid_artifact_reports_compact_error_on_provider_events(self):
        text = "Visible.\n\n```ccc-slides\n{}\n```"
        claude = server._parse_conversation_event(
            {
                "type": "assistant",
                "message": {"id": "bad", "content": [{"type": "text", "text": text}]},
            },
            10,
        )
        codex = server._parse_codex_event(
            {
                "type": "event_msg",
                "payload": {"type": "agent_message", "message": text},
            },
            11,
        )
        for parsed in (claude, codex):
            with self.subTest(parsed=parsed):
                self.assertEqual(parsed["blocks"], [{"kind": "text", "text": "Visible."}])
                self.assertTrue(parsed["presentation_artifact_error"])


if __name__ == "__main__":
    unittest.main()
