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

    def test_claude_first_message_omits_conductor_system_instruction(self):
        # Conductor wraps Claude Code prompts too, and the Claude title path
        # never went through _codex_display_name — every such session showed
        # "<system_instruction>" as its sidebar name.
        sys.modules.pop("server", None)
        server = importlib.import_module("server")
        ev = {
            "type": "user",
            "message": {
                "role": "user",
                "content": (
                    "<system_instruction>\n"
                    "You are working inside Conductor, a Mac app that lets the "
                    "user run many coding agents in parallel.\n"
                    "The target branch for this workspace is origin/main.\n"
                    "</system_instruction>\n\n"
                    "I need to write back to twilio"
                ),
            },
        }

        self.assertEqual(
            server._extract_user_prompt_text(ev),
            "I need to write back to twilio",
        )

    def test_wrapper_only_prompt_yields_no_title(self):
        # Nothing after the closing tag means there is no user ask yet; an
        # empty string lets the head scan fall through to the next message
        # instead of naming the session after the wrapper.
        sys.modules.pop("server", None)
        server = importlib.import_module("server")

        self.assertEqual(
            server._strip_host_system_instruction(
                "<system_instruction>\nboilerplate\n</system_instruction>\n"
            ),
            "",
        )

    def test_mid_message_system_instruction_is_left_alone(self):
        # Only a leading wrapper is host boilerplate. A user quoting the tag
        # mid-prompt keeps their text intact.
        sys.modules.pop("server", None)
        server = importlib.import_module("server")
        text = "why does <system_instruction>foo</system_instruction> show up?"

        self.assertEqual(server._strip_host_system_instruction(text), text)


if __name__ == "__main__":
    unittest.main()
