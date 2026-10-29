"""CCC-227: Codex skill loads (shell reads of SKILL.md) surface as cards."""
import unittest

import server  # noqa: F401  (wires ccc_server.core)
from ccc_server.codex_parse import _codex_skill_reads, _parse_codex_event


class CodexSkillReadsTest(unittest.TestCase):
    def test_detects_shell_reads(self):
        self.assertEqual(
            _codex_skill_reads("/bin/bash -lc 'cat /h/.codex/skills/watchtower/SKILL.md'"),
            [("watchtower", "/h/.codex/skills/watchtower/SKILL.md")],
        )
        self.assertEqual(
            _codex_skill_reads("sed -n 1,200p ~/.agents/skills/ccc-orchestration/SKILL.md"),
            [("ccc-orchestration", "~/.agents/skills/ccc-orchestration/SKILL.md")],
        )

    def test_ignores_non_reads(self):
        self.assertEqual(_codex_skill_reads("apply_patch skills/foo/SKILL.md"), [])
        self.assertEqual(_codex_skill_reads("ls skills/foo"), [])

    def test_custom_exec_call_gets_skill_block_before_tool(self):
        ev = {
            "type": "response_item",
            "timestamp": "2026-10-27T03:35:18Z",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec",
                "call_id": "call_1",
                "input": 'text(await tools.exec_command({cmd:"cat /h/.codex/skills/watchtower/SKILL.md"}));\n'
                         'text(await tools.exec_command({cmd:"cat /h/.codex/skills/run/SKILL.md"}));',
            },
        }
        res = _parse_codex_event(ev, 7)
        kinds = [(b["kind"], b.get("name")) for b in res["blocks"]]
        self.assertEqual(kinds[:2], [("skill_loaded", "watchtower"), ("skill_loaded", "run")])
        self.assertEqual(kinds[-1][0], "tool_use")


if __name__ == "__main__":
    unittest.main()
