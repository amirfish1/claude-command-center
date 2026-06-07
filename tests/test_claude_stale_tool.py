import importlib
import os
import sys
import unittest


class TestClaudeStaleToolFields(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        cls.server = importlib.import_module("server")

    def test_no_inflight_is_not_stale(self):
        fields = self.server._claude_stale_tool_fields(
            started_at=100.0, in_flight=False, now=100.0 + 10_000, threshold_s=900,
        )
        self.assertFalse(fields["stale_tool_call"])
        self.assertEqual(fields["stale_tool_age_s"], 0)
        self.assertEqual(fields["stale_tool_threshold_s"], 900)

    def test_inflight_below_threshold_is_not_stale(self):
        fields = self.server._claude_stale_tool_fields(
            started_at=1000.0, in_flight=True, now=1000.0 + 60, threshold_s=900,
        )
        self.assertFalse(fields["stale_tool_call"])
        self.assertEqual(fields["stale_tool_age_s"], 60)

    def test_inflight_past_threshold_is_stale(self):
        # 3h-old hung tool child, the real-world case.
        fields = self.server._claude_stale_tool_fields(
            started_at=1000.0, in_flight=True, now=1000.0 + 3 * 3600, threshold_s=900,
        )
        self.assertTrue(fields["stale_tool_call"])
        self.assertEqual(fields["stale_tool_age_s"], 3 * 3600)
        self.assertEqual(fields["stale_tool_threshold_s"], 900)

    def test_missing_started_at_is_not_stale(self):
        fields = self.server._claude_stale_tool_fields(
            started_at=None, in_flight=True, now=10_000.0, threshold_s=900,
        )
        self.assertFalse(fields["stale_tool_call"])
        self.assertEqual(fields["stale_tool_age_s"], 0)

    def test_zero_threshold_disables_detection(self):
        fields = self.server._claude_stale_tool_fields(
            started_at=1000.0, in_flight=True, now=1000.0 + 10_000, threshold_s=0,
        )
        self.assertFalse(fields["stale_tool_call"])

    def test_threshold_env_default(self):
        old = os.environ.pop("CCC_STALE_TOOL_SEC", None)
        try:
            self.assertEqual(self.server._stale_tool_threshold_s(), 900.0)
        finally:
            if old is not None:
                os.environ["CCC_STALE_TOOL_SEC"] = old

    def test_threshold_env_override(self):
        old = os.environ.get("CCC_STALE_TOOL_SEC")
        os.environ["CCC_STALE_TOOL_SEC"] = "120"
        try:
            self.assertEqual(self.server._stale_tool_threshold_s(), 120.0)
        finally:
            if old is None:
                os.environ.pop("CCC_STALE_TOOL_SEC", None)
            else:
                os.environ["CCC_STALE_TOOL_SEC"] = old


if __name__ == "__main__":
    unittest.main()
