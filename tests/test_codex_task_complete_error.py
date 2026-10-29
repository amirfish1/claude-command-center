import unittest

import server


def _task_complete(error):
    return server._parse_codex_event({
        "timestamp": "2026-10-29T15:03:41.927Z",
        "type": "event_msg",
        "payload": {
            "type": "task_complete",
            "last_agent_message": None,
            "error": error,
            "duration_ms": 349,
        },
    }, 7)


class CodexTaskCompleteErrorTests(unittest.TestCase):
    def test_usage_limit_error_is_surfaced(self):
        parsed = _task_complete({
            "message": "You've hit your usage limit.",
            "codex_error_info": "usage_limit_exceeded",
        })
        self.assertEqual(parsed["type"], "result")
        self.assertTrue(parsed["is_error"])
        self.assertIn("usage limit", parsed["error"])
        self.assertFalse(parsed.get("no_agent_output"))

    def test_no_error_keeps_silent_label(self):
        parsed = _task_complete(None)
        self.assertTrue(parsed["no_agent_output"])
        self.assertNotIn("error", parsed)


if __name__ == "__main__":
    unittest.main()
