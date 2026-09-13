import unittest

import server


class CodexImageGenerationTests(unittest.TestCase):
    """Slice 3 of the codex-single-renderer-merge plan: a rollout parser
    branch for `response_item/image_generation_call`, the record Codex's
    image-generation tool actually persists (verified against real rollout
    JSONL - see ccc_server/codex_parse.py's inline note). The dual-emitted
    `event_msg/image_generation_end` twin is deliberately NOT handled, same
    as the existing item_completed dual-emission convention, to avoid a
    doubled card.
    """

    def test_image_generation_call_renders_as_image_generation_block(self):
        parsed = server._parse_codex_event({
            "type": "response_item",
            "payload": {
                "type": "image_generation_call",
                "id": "ig_1",
                "revised_prompt": "a red bicycle on a beach",
                "status": "completed",
                "result": "not-real-but-nonempty-base64==",
                "metadata": {"turn_id": "turn-1"},
            },
        }, 10)

        self.assertEqual(parsed["type"], "assistant")
        self.assertEqual(len(parsed["blocks"]), 1)
        block = parsed["blocks"][0]
        self.assertEqual(block["kind"], "image_generation")
        self.assertEqual(block["id"], "ig_1")
        self.assertEqual(block["prompt"], "a red bicycle on a beach")
        self.assertEqual(block["status"], "completed")
        self.assertEqual(block["image_idx"], 0)
        self.assertEqual(parsed["images"], [
            {"kind": "base64", "media_type": "image/png", "line": 10, "idx": 0},
        ])

    def test_image_generation_call_without_result_has_no_image_ref(self):
        parsed = server._parse_codex_event({
            "type": "response_item",
            "payload": {
                "type": "image_generation_call",
                "id": "ig_2",
                "revised_prompt": "a blue bicycle",
                "status": "generating",
            },
        }, 11)

        self.assertEqual(parsed["type"], "assistant")
        block = parsed["blocks"][0]
        self.assertEqual(block["status"], "generating")
        self.assertNotIn("image_idx", block)
        self.assertNotIn("images", parsed)

    def test_long_prompt_is_truncated(self):
        parsed = server._parse_codex_event({
            "type": "response_item",
            "payload": {
                "type": "image_generation_call",
                "id": "ig_3",
                "revised_prompt": "x" * 700,
            },
        }, 12)

        prompt = parsed["blocks"][0]["prompt"]
        self.assertTrue(prompt.endswith("..."))
        self.assertLessEqual(len(prompt), 604)

    def test_turn_meta_applies_to_image_generation_events(self):
        turn_meta = {"turn_id": "turn-42"}
        parsed = server._parse_codex_event({
            "type": "response_item",
            "payload": {
                "type": "image_generation_call",
                "id": "ig_4",
                "revised_prompt": "a cat",
            },
        }, 13, codex_turn_meta=turn_meta)

        self.assertEqual(parsed.get("turn_id"), "turn-42")


if __name__ == "__main__":
    unittest.main()
