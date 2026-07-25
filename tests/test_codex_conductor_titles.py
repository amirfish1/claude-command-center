"""Regression coverage for Conductor's injected Codex system wrapper."""

import importlib
import sys
import unittest


class TestCodexConductorTitles(unittest.TestCase):
    def test_display_name_omits_leading_conductor_system_instruction(self):
        sys.modules.pop("server", None)
        server = importlib.import_module("server")
        prompt = (
            "<system_instruction>\n"
            "You are working inside Conductor, a Mac app that lets the user run "
            "many coding agents in parallel.\n"
            "</system_instruction>\n\n"
            "is this a worktree?"
        )

        self.assertEqual(
            server._codex_display_name({}, first_message=prompt),
            "is this a worktree?",
        )


if __name__ == "__main__":
    unittest.main()
