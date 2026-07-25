import json
import threading
import unittest
import urllib.request
from unittest import mock

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

    def test_extracts_fence_before_ccc_session_state_metadata(self):
        text = self.fenced(self.valid()) + "\n\n<session-state>\nDID: made slides\nINSIGHT: stable keys\nNEXT_STEP_USER: none\n</session-state>"
        prose, artifact, error = server._extract_presentation_artifact(text)
        self.assertEqual(prose, "Human prose.")
        self.assertEqual(artifact["slides"][0]["id"], "cause")
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

    def test_mode3_prompt_adds_server_owned_contract_and_hides_it_from_display(self):
        augmented = server._mode3_prompt("Explain the failure")
        self.assertTrue(augmented.startswith("Explain the failure\n\n<ccc-mode3-instruction"))
        self.assertIn("```ccc-slides", augmented)
        self.assertIn('"layout":"comparison"', augmented)
        self.assertEqual(server._strip_mode3_instruction(augmented), "Explain the failure")

    def test_bootstrap_requests_latest_answer_only(self):
        prompt = server._mode3_prompt("", bootstrap=True)
        self.assertIn("latest completed substantive answer", prompt)
        self.assertIn("Return only the ccc-slides fence", prompt)
        self.assertEqual(server._strip_mode3_instruction(prompt), "")

    def test_provider_user_events_hide_mode3_instruction(self):
        augmented = server._mode3_prompt("Explain the failure")
        claude = server._parse_conversation_event(
            {
                "type": "user",
                "message": {"content": [{"type": "text", "text": augmented}]},
            },
            12,
        )
        codex = server._parse_codex_event(
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": augmented},
            },
            13,
        )
        self.assertEqual(claude["text"], "Explain the failure")
        self.assertEqual(codex["text"], "Explain the failure")

    def test_inject_endpoint_augments_only_mode3_substantive_sends(self):
        sid = "00000000-0000-4000-8000-000000000333"
        httpd = server.http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), server.CommandCenterHandler,
        )
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{httpd.server_address[1]}/api/inject-input"

        def post(payload):
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            with mock.patch.object(
                server,
                "_inject_text_into_session",
                return_value={"ok": True, "via": "mock"},
            ) as inject:
                self.assertTrue(post({
                    "session_id": sid,
                    "text": "Explain this",
                    "mode": "send",
                    "presentation_mode3": True,
                })["ok"])
                self.assertTrue(post({
                    "session_id": sid,
                    "text": "/status",
                    "mode": "send",
                    "presentation_mode3": True,
                })["ok"])
                self.assertTrue(post({
                    "session_id": sid,
                    "text": "Steer now",
                    "mode": "steer",
                    "presentation_mode3": True,
                })["ok"])
                self.assertTrue(post({
                    "session_id": sid,
                    "text": "Picker choice",
                    "mode": "answer",
                    "presentation_mode3": True,
                })["ok"])
                self.assertTrue(post({
                    "session_id": sid,
                    "text": "Plain send",
                    "mode": "send",
                })["ok"])
                self.assertTrue(post({
                    "session_id": sid,
                    "text": "",
                    "mode": "send",
                    "presentation_bootstrap": True,
                })["ok"])

            calls = inject.call_args_list
            self.assertEqual(len(calls), 6)
            augmented = calls[0].args[1]
            self.assertEqual(server._strip_mode3_instruction(augmented), "Explain this")
            self.assertIn("<ccc-mode3-instruction", augmented)
            self.assertEqual(calls[1].args[1], "/status")
            self.assertEqual(calls[2].args[1], "Steer now")
            self.assertEqual(calls[3].args[1], "Picker choice")
            self.assertEqual(calls[4].args[1], "Plain send")
            bootstrap = calls[5].args[1]
            self.assertEqual(server._strip_mode3_instruction(bootstrap), "")
            self.assertIn("latest completed substantive answer", bootstrap)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
