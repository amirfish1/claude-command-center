"""Unit tests for the session-attention classifier (`_classify_attention`).

The classifier decides which "Needs Your Attention" bucket (kind +
priority) a session lands in based on sidecar evidence already merged
onto the conversation row. The README's Roadmap section explicitly
flags this as the highest-leverage place to add tests — silent
miscategorisation here is the worst-case regression for users.

This file is a kickoff (issue #55): it covers the main bucket / column
split with pure-function fixtures, not an exhaustive cross-product. A
follow-up issue can extend coverage to the full matrix.

stdlib-only (`unittest` + `unittest.mock`) — matches `tests/test_smoke.py`
and `tests/test_classify.py`.
"""
import importlib
import os
import sys
import unittest
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def _fresh_server():
    """Re-import server.py so any module-level mutation by a prior
    test (e.g. SIDECAR_STATE_DIR redirection) doesn't leak in."""
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


def _session_row(**overrides):
    """Build a realistic session-row dict the classifier consumes.

    Defaults represent a dormant, unremarkable session: not live, no
    edits, no commit, no PR, not backlog. Each test overrides only the
    fields it cares about — keeps the fixture surface intentional.
    """
    row = {
        "session_id": "00000000-0000-4000-8000-000000000001",
        "id": "00000000-0000-4000-8000-000000000001",
        "display_name": "demo session",
        "first_message": "do the thing",
        "source": "session",
        "is_live": False,
        "archived": False,
        "verified": False,
        "backlog_type": None,
        "session_state": {},
        "has_edit": False,
        "has_commit": False,
        "has_push": False,
        "linked_issue": None,
        "tail_issue_number": None,
        "issue_number": None,
        "gh_state": None,
        "pending_tool": None,
        "pending_file": None,
        "sidecar_status": None,
        "last_event_type": None,
        "session_cwd": None,
        "issue_labels": [],
        "gh_in_progress": False,
    }
    row.update(overrides)
    return row


class TestClassifyAttentionSuppressionRules(unittest.TestCase):
    """Cases where the classifier must return None — i.e. the card should
    NOT appear in the Needs-Your-Attention column at all."""

    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def test_archived_session_is_suppressed(self):
        row = _session_row(archived=True, has_edit=True, has_commit=False)
        self.assertIsNone(self.server._classify_attention(row))

    def test_verified_session_is_suppressed(self):
        row = _session_row(verified=True, has_push=True,
                           linked_issue=42, gh_state="OPEN")
        self.assertIsNone(self.server._classify_attention(row))

    def test_todo_backlog_type_is_suppressed(self):
        """TODO.md entries explicitly opt out: user said don't flood NYA."""
        row = _session_row(source="backlog", backlog_type="todo")
        self.assertIsNone(self.server._classify_attention(row))

    def test_parking_backlog_type_is_suppressed(self):
        row = _session_row(source="backlog", backlog_type="parking")
        self.assertIsNone(self.server._classify_attention(row))

    def test_dormant_next_step_waiting_is_suppressed(self):
        """Session self-reports waiting on an external party → don't nag."""
        row = _session_row(
            is_live=False,
            session_state={"next_step_user": "Wait for Bob to approve"},
            has_edit=True,
        )
        self.assertIsNone(self.server._classify_attention(row))

    def test_dormant_next_step_done_is_suppressed(self):
        """Session self-reports work already shipped → don't nag."""
        row = _session_row(
            is_live=False,
            session_state={"next_step_user": "Already shipped — nothing to commit"},
            has_edit=True,
            has_commit=True,
        )
        self.assertIsNone(self.server._classify_attention(row))


class TestClassifyAttentionSessionCases(unittest.TestCase):
    """Live + dormant session buckets (priorities 1–5)."""

    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def test_live_pending_tool_is_priority_1(self):
        row = _session_row(
            is_live=True,
            pending_tool="Bash",
            pending_file="rm -rf /tmp/scratch",
        )
        item = self.server._classify_attention(row)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "pending_tool")
        self.assertEqual(item["priority"], 1)
        self.assertIn("tool approval", item["where"])
        # The default next_step mentions the pending tool name.
        self.assertIn("Bash", item["next_step"])

    def test_stale_codex_tool_is_priority_1_attention(self):
        row = _session_row(
            source="codex",
            stale_tool_call=True,
            stale_tool_age_s=3700,
            pending_tool="write_stdin",
            pending_file="session 7095",
        )
        item = self.server._classify_attention(row)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "stale_tool_call")
        self.assertEqual(item["priority"], 1)
        self.assertIn("Codex", item["where"])
        self.assertIn("Wake Codex", item["next_step"])
        # The ticking age must be split out so the dashboard can patch it in
        # place instead of rebuilding the whole conversation list every minute.
        self.assertEqual(item["stale_tool_age_s"], 3700)
        self.assertIn("{{stale_age}}", item["next_step"])
        self.assertNotIn("for about", item["next_step"])

    def test_live_sidecar_waiting_is_priority_2(self):
        row = _session_row(is_live=True, sidecar_status="waiting")
        item = self.server._classify_attention(row)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "sidecar_waiting")
        self.assertEqual(item["priority"], 2)
        self.assertIn("awaiting your prompt", item["where"])

    def test_pushed_with_open_issue_is_priority_3(self):
        """has_push + linked OPEN issue → PR likely missing `Closes #N`."""
        row = _session_row(
            is_live=False,
            has_push=True,
            linked_issue=99,
            gh_state="OPEN",
        )
        item = self.server._classify_attention(row)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "pushed_open")
        self.assertEqual(item["priority"], 3)
        self.assertIn("#99", item["where"])

    def test_dormant_uncommitted_edits_with_issue_is_priority_4(self):
        row = _session_row(
            is_live=False,
            has_edit=True,
            has_commit=False,
            linked_issue=55,
        )
        item = self.server._classify_attention(row)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "uncommitted_edits")
        self.assertEqual(item["priority"], 4)
        self.assertIn("uncommitted", item["where"])

    def test_dormant_uncommitted_edits_without_issue_ref_is_suppressed(self):
        """Scratch / exploratory sessions with no issue link don't nag —
        keeps the NYA column free of "by the way…" sessions running in
        leftover worktrees."""
        row = _session_row(is_live=False, has_edit=True, has_commit=False)
        self.assertIsNone(self.server._classify_attention(row))

    def test_committed_not_pushed_is_priority_5_when_ahead(self):
        """has_commit + ahead-of-upstream commits → priority 5."""
        row = _session_row(
            is_live=False,
            has_commit=True,
            has_push=False,
            session_cwd="/tmp/some-repo",
        )
        # Patch the upstream-ahead probe — it's the only impure dep.
        with mock.patch.object(self.server, "_count_unpushed_commits",
                               return_value=2):
            item = self.server._classify_attention(row)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "committed_not_pushed")
        self.assertEqual(item["priority"], 5)
        self.assertIn("unpushed", item["where"])

    def test_committed_not_pushed_suppressed_when_zero_commits_ahead(self):
        """has_commit is a tool-call flag, not a repo-state check. When
        the working tree is actually clean (e.g. a `git pull` fast-forwarded
        the commit onto already-pushed history) we must NOT nag."""
        row = _session_row(
            is_live=False,
            has_commit=True,
            has_push=False,
            session_cwd="/tmp/some-repo",
        )
        with mock.patch.object(self.server, "_count_unpushed_commits",
                               return_value=0):
            self.assertIsNone(self.server._classify_attention(row))


class TestClassifyAttentionBacklogCases(unittest.TestCase):
    """Backlog (GitHub) buckets (priorities 6–7)."""

    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def test_needs_attention_label_is_priority_6(self):
        row = _session_row(
            source="backlog",
            backlog_type="github",
            issue_number=12,
            tail_issue_number=12,
            issue_labels=["needs-attention", "bug"],
        )
        item = self.server._classify_attention(row)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "needs_attention_label")
        self.assertEqual(item["priority"], 6)
        self.assertIn("needs-attention", item["where"])
        self.assertIn("#12", item["next_step"])

    def test_open_backlog_with_no_wip_is_priority_7(self):
        row = _session_row(
            source="backlog",
            backlog_type="github",
            issue_number=7,
            tail_issue_number=7,
            issue_labels=[],
            gh_in_progress=False,
        )
        item = self.server._classify_attention(row)
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "open_backlog")
        self.assertEqual(item["priority"], 7)
        self.assertIn("Backlog", item["where"])

    def test_icebox_backlog_is_suppressed(self):
        row = _session_row(
            source="backlog",
            backlog_type="github",
            issue_number=3,
            issue_labels=["icebox"],
        )
        self.assertIsNone(self.server._classify_attention(row))

    def test_in_progress_backlog_is_suppressed(self):
        """`claude-in-progress` label OR gh_in_progress flag → session
        already running, so it's not "open backlog" any more."""
        row = _session_row(
            source="backlog",
            backlog_type="github",
            issue_number=4,
            issue_labels=["claude-in-progress"],
        )
        self.assertIsNone(self.server._classify_attention(row))


if __name__ == "__main__":
    unittest.main()
