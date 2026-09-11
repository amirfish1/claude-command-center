import unittest
from unittest import mock

import server


class CodexSubagentLaneNameTests(unittest.TestCase):
    """Codex multi-agent threads are already surfaced as Lane Map children via
    the codex-native thread_spawn_edges sqlite table; this covers the label
    enrichment so the lane reads "PR113 Independent Review" instead of a bare
    session id.
    """

    def test_edge_name_uses_agent_path_leaf(self):
        with mock.patch.object(
            server, "_codex_thread_row",
            return_value={"agent_path": "/root/pr113_independent_review"},
        ):
            name = server._codex_spawn_edge_name("01a0533c-5f5d-7fb0-b5f2-125f50a10dd3")

        self.assertTrue(name)
        self.assertNotEqual(name.lower(), "root")

    def test_edge_name_blank_when_thread_row_missing(self):
        with mock.patch.object(server, "_codex_thread_row", return_value=None):
            name = server._codex_spawn_edge_name("unknown-thread")

        self.assertEqual(name, "")

    def test_edge_name_swallows_lookup_errors(self):
        with mock.patch.object(server, "_codex_thread_row", side_effect=RuntimeError("boom")):
            name = server._codex_spawn_edge_name("01a0533c-5f5d-7fb0-b5f2-125f50a10dd3")

        self.assertEqual(name, "")


if __name__ == "__main__":
    unittest.main()
