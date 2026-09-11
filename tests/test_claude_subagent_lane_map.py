import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import server
from ccc_server.session_graph import (
    _SessionGraph,
    _agent_transcript_active,
    _session_graph_enrich_claude_task_subagents,
)


class ClaudeSubagentLaneMapTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.graph_file = self.tmp_path / "session-graph.json"
        self.graph = _SessionGraph(self.graph_file)
        self.projects_root = self.tmp_path / "projects"
        self.projects_root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_enrich_claude_task_subagents_reads_meta_json(self):
        parent_sid = "test-parent-session-12345"
        project_dir = self.projects_root / "test-project" / parent_sid / "subagents"
        project_dir.mkdir(parents=True, exist_ok=True)

        agent_id = "agent-a1b2c3d4e5f6"
        jsonl_path = project_dir / f"{agent_id}.jsonl"
        jsonl_path.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}) + "\n")

        meta_path = project_dir / f"{agent_id}.meta.json"
        meta_path.write_text(json.dumps({
            "agentType": "general-purpose",
            "description": "Build post-preview modal and home reminder",
            "model": "opus"
        }))

        with mock.patch.object(server, "_session_graph", self.graph), \
             mock.patch.object(server, "PROJECTS_ROOT", self.projects_root):
            _session_graph_enrich_claude_task_subagents(parent_sid)

        meta = self.graph.edge_meta(agent_id)
        self.assertEqual(meta.get("name"), "Build post-preview modal and home reminder")
        self.assertEqual(meta.get("model"), "opus")
        self.assertEqual(meta.get("engine"), "claude")
        self.assertFalse(meta.get("resumable"))

    def test_agent_transcript_active_skips_trailing_attachments(self):
        transcript_file = self.tmp_path / "agent-active.jsonl"
        records = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done with task"}]}},
            {"type": "attachment", "rendered": [{"content": "system context"}]},
        ]
        with open(transcript_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        st = transcript_file.stat()
        # Should be inactive because the last semantic turn is assistant with text-only
        active = _agent_transcript_active(transcript_file, st.st_mtime, st.st_size)
        self.assertFalse(active)

        # Append a user tool-result + attachment
        records2 = [
            {"type": "user", "message": {"content": "tool result"}},
            {"type": "attachment", "rendered": []},
        ]
        with open(transcript_file, "a", encoding="utf-8") as f:
            for r in records2:
                f.write(json.dumps(r) + "\n")

        st = transcript_file.stat()
        # Should be active because the last semantic turn is user waiting for assistant answer
        active = _agent_transcript_active(transcript_file, st.st_mtime, st.st_size)
        self.assertTrue(active)


if __name__ == "__main__":
    unittest.main()
