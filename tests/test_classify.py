"""Drive CCC's session-classification path against a hand-crafted JSONL fixture.

The smoke suite (`tests/test_smoke.py`) only proves `import server` doesn't
explode. The real risk surface is the parser that turns
`~/.claude/projects/<slug>/<sid>.jsonl` events into kanban-card metadata
(`find_conversations`, `_extract_tail_meta`, `_parse_session_state`) and
the side-car merger (`_add_sidecar_fields`). This test exercises both
against `tests/fixtures/mock_session.jsonl` so a regression in the
event-shape handling fails CI instead of waiting to be noticed visually
on someone's kanban.

Pattern lifted from BloopAI/vibe-kanban's `qa_mock` executor: instead of
mocking the runtime (`claude` itself), feed CCC a realistic-looking
transcript and assert the parser surfaces it correctly.

stdlib-only — `unittest` / `unittest.mock`, no pytest.
"""
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = Path(PROJECT_ROOT) / "tests" / "fixtures" / "mock_session.jsonl"
MOCK_SESSION_ID = "00000000-mock-4000-8000-000000000001"
CLAUDE_DESKTOP_SESSION_ID = "33333333-3333-4333-8333-333333333333"
CLAUDE_DESKTOP_APP_SESSION_ID = "44444444-4444-4444-8444-444444444444"
CLAUDE_DESKTOP_MISSING_SESSION_ID = "55555555-5555-4555-8555-555555555555"
CODEX_SESSION_ID = "11111111-1111-4111-8111-111111111111"
CODEX_TRAILER_SESSION_ID = "22222222-2222-4222-8222-222222222222"

sys.path.insert(0, PROJECT_ROOT)


def _fresh_server():
    """Re-import server.py so module-level Path constants pick up our env."""
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


def _write_user_jsonl(path, session_id, content, cwd):
    event = {
        "type": "user",
        "message": {"role": "user", "content": content},
        "timestamp": "2026-05-02T00:00:00.000Z",
        "cwd": str(cwd),
        "sessionId": session_id,
        "gitBranch": "main",
    }
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")


def _write_jsonl_events(path, events):
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )


class TestGeneratedHelperSessionFilter(unittest.TestCase):
    def test_all_repos_hides_generated_helper_sessions(self):
        tmp_home = tempfile.mkdtemp(prefix="ccc-all-repos-home-")
        prev_home = os.environ.get("HOME")
        try:
            resolved_home = Path(tmp_home).resolve()
            os.environ["HOME"] = str(resolved_home)
            server = _fresh_server()

            repo = resolved_home / "demo-repo"
            repo.mkdir()
            project_dir = resolved_home / ".claude" / "projects" / "-demo-repo"
            project_dir.mkdir(parents=True)

            real_sid = "00000000-0000-4000-8000-000000000001"
            title_sid = "00000000-0000-4000-8000-000000000002"
            image_sid = "00000000-0000-4000-8000-000000000003"
            _write_user_jsonl(
                project_dir / f"{real_sid}.jsonl",
                real_sid,
                "please update the clients report",
                repo,
            )
            _write_user_jsonl(
                project_dir / f"{title_sid}.jsonl",
                title_sid,
                "Produce a concise 4-8 word title summarizing what the user is trying to do below.",
                repo,
            )
            _write_user_jsonl(
                project_dir / f"{image_sid}.jsonl",
                image_sid,
                "Use the Read tool to open this image: '/Users/test/Downloads/example.png'. "
                "Then output ONLY a single JSON line, no other text",
                repo,
            )

            rows = server.find_all_conversations()
            sids = {r["session_id"] for r in rows}

            self.assertIn(real_sid, sids)
            self.assertNotIn(title_sid, sids)
            self.assertNotIn(image_sid, sids)
        finally:
            if prev_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev_home
            for mod in ("server", "morning", "morning_store"):
                sys.modules.pop(mod, None)
            shutil.rmtree(tmp_home, ignore_errors=True)


class TestTranscriptControlMessageFilter(unittest.TestCase):
    def test_session_titles_skip_local_command_wrappers(self):
        tmp_home = tempfile.mkdtemp(prefix="ccc-control-text-home-")
        prev_home = os.environ.get("HOME")
        try:
            resolved_home = Path(tmp_home).resolve()
            os.environ["HOME"] = str(resolved_home)
            server = _fresh_server()

            repo = resolved_home / "demo-repo"
            repo.mkdir()
            server._append_custom_repo(str(repo))
            project_dir = (
                resolved_home
                / ".claude"
                / "projects"
                / server._encode_project_slug(str(repo))
            )
            project_dir.mkdir(parents=True)

            sid = "00000000-ctrl-4000-8000-000000000001"
            real_prompt = "Investigate why checkout tests fail"
            common = {
                "userType": "external",
                "entrypoint": "cli",
                "cwd": str(repo),
                "sessionId": sid,
                "version": "2.1.138",
                "gitBranch": "main",
            }
            _write_jsonl_events(
                project_dir / f"{sid}.jsonl",
                [
                    {
                        **common,
                        "type": "user",
                        "isMeta": True,
                        "message": {
                            "role": "user",
                            "content": (
                                "<local-command-caveat>Caveat: generated by "
                                "local commands.</local-command-caveat>"
                            ),
                        },
                        "timestamp": "2026-05-02T00:00:00.000Z",
                    },
                    {
                        **common,
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": (
                                "<command-name>/resume</command-name>\n"
                                "<command-message>resume</command-message>"
                            ),
                        },
                        "timestamp": "2026-05-02T00:00:01.000Z",
                    },
                    {
                        **common,
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": (
                                "<local-command-stdout>No conversations found"
                                "</local-command-stdout>"
                            ),
                        },
                        "timestamp": "2026-05-02T00:00:02.000Z",
                    },
                    {
                        **common,
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": (
                                "<bash-input>open ~/example.md</bash-input>"
                            ),
                        },
                        "timestamp": "2026-05-02T00:00:02.500Z",
                    },
                    {
                        **common,
                        "type": "user",
                        "message": {
                            "role": "user",
                            "content": (
                                "<bash-stdout>(Bash completed with no output)"
                                "</bash-stdout><bash-stderr></bash-stderr>"
                            ),
                        },
                        "timestamp": "2026-05-02T00:00:02.600Z",
                    },
                    {
                        **common,
                        "type": "user",
                        "message": {"role": "user", "content": real_prompt},
                        "timestamp": "2026-05-02T00:00:03.000Z",
                    },
                ],
            )

            repo_rows = server.find_conversations(str(repo))
            all_rows = server.find_all_conversations()
            repo_card = next(c for c in repo_rows if c["session_id"] == sid)
            all_card = next(c for c in all_rows if c["session_id"] == sid)

            self.assertEqual(repo_card["first_message"], real_prompt)
            self.assertEqual(all_card["first_message"], real_prompt)
            parsed = server.parse_conversation(sid, repo_path=str(repo))
            user_texts = [
                ev["text"] for ev in parsed["events"] if ev["type"] == "user_text"
            ]
            self.assertEqual(user_texts, ["/resume", real_prompt])
        finally:
            if prev_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev_home
            for mod in ("server", "morning", "morning_store"):
                sys.modules.pop(mod, None)
            shutil.rmtree(tmp_home, ignore_errors=True)

    def test_queued_prompt_attachments_render_as_user_messages(self):
        tmp_home = tempfile.mkdtemp(prefix="ccc-queued-prompt-home-")
        prev_home = os.environ.get("HOME")
        try:
            resolved_home = Path(tmp_home).resolve()
            os.environ["HOME"] = str(resolved_home)
            server = _fresh_server()

            repo = resolved_home / "demo-repo"
            repo.mkdir()
            server._append_custom_repo(str(repo))
            project_dir = (
                resolved_home
                / ".claude"
                / "projects"
                / server._encode_project_slug(str(repo))
            )
            project_dir.mkdir(parents=True)

            sid = "00000000-queue-4000-8000-000000000001"
            common = {
                "userType": "external",
                "entrypoint": "cli",
                "cwd": str(repo),
                "sessionId": sid,
                "version": "2.1.142",
                "gitBranch": "main",
            }
            events = [
                {
                    **common,
                    "type": "user",
                    "message": {"role": "user", "content": "first ask"},
                    "timestamp": "2026-05-15T19:00:00.000Z",
                },
                {
                    **common,
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "Bash",
                                "input": {"command": "sleep 5"},
                            }
                        ],
                    },
                    "timestamp": "2026-05-15T19:00:01.000Z",
                },
                {
                    **common,
                    "type": "attachment",
                    "attachment": {
                        "type": "queued_command",
                        "prompt": [
                            {"type": "text", "text": "clarify while busy"}
                        ],
                        "commandMode": "prompt",
                    },
                    "timestamp": "2026-05-15T19:00:02.000Z",
                },
                {
                    **common,
                    "type": "attachment",
                    "attachment": {
                        "type": "queued_command",
                        "prompt": "<task-notification>done</task-notification>",
                        "commandMode": "task-notification",
                    },
                    "timestamp": "2026-05-15T19:00:03.000Z",
                },
                {
                    "type": "last-prompt",
                    "lastPrompt": "first ask",
                    "leafUuid": "leaf-1",
                    "sessionId": sid,
                },
            ]
            path = project_dir / f"{sid}.jsonl"
            path.write_text(
                "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events),
                encoding="utf-8",
            )

            parsed = server.parse_conversation(sid, repo_path=str(repo))
            user_texts = [
                ev["text"] for ev in parsed["events"] if ev["type"] == "user_text"
            ]
            self.assertEqual(user_texts, ["first ask", "clarify while busy"])

            tail = server._extract_tail_meta(path)
            self.assertEqual(tail["last_prompt"], "clarify while busy")
            self.assertEqual(tail["last_event_type"], "user")
            self.assertGreater(tail["last_meaningful_ts"], 0)
        finally:
            if prev_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = prev_home
            for mod in ("server", "morning", "morning_store"):
                sys.modules.pop(mod, None)
            shutil.rmtree(tmp_home, ignore_errors=True)


class TestExtractTailMetaPrLink(unittest.TestCase):
    def test_pr_link_event_sets_tail_pr_fields(self):
        server = _fresh_server()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text(
                '{"type":"pr-link",'
                '"sessionId":"afcc907b-3ab5-44ac-9222-b42c1f1fe60e",'
                '"prNumber":242,'
                '"prUrl":"https://github.com/amirfish1/my-finance-app/pull/242",'
                '"prRepository":"amirfish1/my-finance-app"}\n',
                encoding="utf-8",
            )

            meta = server._extract_tail_meta(path)

        self.assertEqual(meta["tail_pr_number"], 242)
        self.assertEqual(
            meta["tail_pr_url"],
            "https://github.com/amirfish1/my-finance-app/pull/242",
        )


class TestExtractCodexTailMetaSummary(unittest.TestCase):
    def test_agent_summary_sets_pr_branch_and_worktree_fields(self):
        server = _fresh_server()
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "demo-repo-wt-feature"
            path = Path(tmp) / "rollout.jsonl"
            message = (
                f"Opened the PR: https://github.com/octo-org/demo-repo/pull/{25}\n"
                "Branch: feat/codex-parity\n"
                f"Worktree: {worktree}\n"
            )
            path.write_text(
                json.dumps({
                    "type": "event_msg",
                    "payload": {
                        "type": "agent_message",
                        "message": message,
                        "timestamp": "2026-05-02T12:00:00Z",
                    },
                }) + "\n",
                encoding="utf-8",
            )

            meta = server._extract_codex_tail_meta(path)

        self.assertEqual(meta["tail_pr_number"], 25)
        self.assertEqual(
            meta["tail_pr_url"],
            f"https://github.com/octo-org/demo-repo/pull/{25}",
        )
        self.assertEqual(meta["tail_branch"], "feat/codex-parity")
        self.assertEqual(meta["tail_worktree_path"], str(worktree))

    def test_git_worktree_add_sets_branch_and_worktree_fields(self):
        server = _fresh_server()
        with tempfile.TemporaryDirectory() as tmp:
            worktree = Path(tmp) / "demo-repo-wt-row-state"
            path = Path(tmp) / "rollout.jsonl"
            path.write_text(
                json.dumps({
                    "timestamp": "2026-05-02T12:00:00Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "arguments": json.dumps({
                            "cmd": (
                                "git worktree add -b "
                                f"feat/row-state {worktree} main"
                            ),
                        }),
                        "call_id": "call_worktree",
                    },
                }) + "\n",
                encoding="utf-8",
            )

            meta = server._extract_codex_tail_meta(path)

        self.assertEqual(meta["tail_branch"], "feat/row-state")
        self.assertEqual(meta["tail_worktree_path"], str(worktree))
        self.assertEqual(meta["pending_tool"], "exec_command")


class TestFindConversationsOnMockFixture(unittest.TestCase):
    """find_conversations() should locate the fixture session and parse its
    signals (has_edit, pending_tool, last_event_type, session_state)."""

    @classmethod
    def setUpClass(cls):
        # Stage the fixture inside a temp ~/.claude/projects/<slug>/ tree.
        cls.tmp_home = tempfile.mkdtemp(prefix="ccc-mock-home-")
        # Resolve up front: on macOS /var/folders is a symlink to
        # /private/var/folders, and server.py runs Path(...).resolve() on
        # incoming repo paths. If we computed the slug from the unresolved
        # path we'd point at the wrong projects dir.
        cls.fake_repo = (Path(cls.tmp_home) / "fake-repo").resolve()
        cls.fake_repo.mkdir(parents=True)
        (cls.fake_repo / ".git").mkdir()
        # HOME also has to resolve for the same reason — server.py reads
        # Path.home() at import time to derive the projects root.
        resolved_home = Path(cls.tmp_home).resolve()

        # Override HOME so PROJECTS_ROOT (Path.home()/".claude"/"projects")
        # resolves into our tmp tree, AND so all the *FILE side-car paths
        # (SESSION_NAMES_FILE etc.) point at empty defaults instead of the
        # real user's command-center state — no test pollution.
        cls._prev_env = {
            "HOME": os.environ.get("HOME"),
        }
        os.environ["HOME"] = str(resolved_home)

        cls.server = _fresh_server()
        cls.resolved_home = resolved_home
        cls.projects_dir = cls.server._canonical_conversation_path(
            str(cls.fake_repo),
            MOCK_SESSION_ID,
        ).parent
        cls.projects_dir.mkdir(parents=True)

        # Copy fixture under <session_id>.jsonl so find_session_cwd / scanners
        # match by filename. Use the server-derived dir so the test follows
        # Claude Code's current project-slug encoder.
        target = cls.projects_dir / f"{MOCK_SESSION_ID}.jsonl"
        shutil.copy(FIXTURE, target)
        cls.target_path = target

    @classmethod
    def tearDownClass(cls):
        # Restore env so subsequent suites (test_smoke etc.) see the real
        # user paths again.
        for k, v in cls._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Force a re-import on next access so cached state from this run
        # doesn't leak into other tests.
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(cls.tmp_home, ignore_errors=True)

    def test_fixture_is_discovered(self):
        """find_conversations() must surface the mock fixture as a card."""
        convs = self.server.find_conversations(str(self.fake_repo))
        sids = [c["session_id"] for c in convs]
        self.assertIn(MOCK_SESSION_ID, sids,
                      f"mock session not found among {sids!r}")

    def test_fixture_metadata_extracted(self):
        """Custom-title, first-message, and branch must round-trip."""
        convs = self.server.find_conversations(str(self.fake_repo))
        card = next(c for c in convs if c["session_id"] == MOCK_SESSION_ID)
        self.assertEqual(card["display_name"], "mock-session-classifier-coverage")
        self.assertEqual(card["branch"], "main")
        self.assertIn("README.md", card["first_message"])

    def test_fixture_has_edit_signal(self):
        """The Edit tool_use must light up has_edit — the kanban uses this
        to push the card past Planning into Working."""
        convs = self.server.find_conversations(str(self.fake_repo))
        card = next(c for c in convs if c["session_id"] == MOCK_SESSION_ID)
        self.assertTrue(card["has_edit"],
                        "has_edit should be True after an Edit tool_use")
        # No commit/push in the fixture — those signals must stay False.
        self.assertFalse(card["has_commit"])
        self.assertFalse(card["has_push"])

    def test_fixture_session_state_parsed(self):
        """The trailing <session-state> block in the last assistant turn
        must round-trip through _parse_session_state."""
        convs = self.server.find_conversations(str(self.fake_repo))
        card = next(c for c in convs if c["session_id"] == MOCK_SESSION_ID)
        st = card["session_state"]
        self.assertIsNotNone(st, "session_state must parse out of fixture")
        self.assertIn("stdlib", (st.get("did") or "").lower())
        self.assertIn("pip", (st.get("insight") or "").lower())
        self.assertIsNotNone(st.get("next_step_user"))

    def test_fixture_classifies_as_working_or_verified(self):
        """The card has has_edit + a parsed session-state DID line + a final
        `result` event, which on the kanban routes to Working (live) or
        Verified (after the user marks it done). The Python signals that
        drive that decision must all be present."""
        convs = self.server.find_conversations(str(self.fake_repo))
        card = next(c for c in convs if c["session_id"] == MOCK_SESSION_ID)
        # has_edit alone is enough for the JS classifier to leave Planning;
        # combined with a session-state outcome, the card is "Verified-ready".
        self.assertTrue(card["has_edit"])
        self.assertIsNotNone(card["session_state"])
        # The trailing `{"type":"result"}` event closes the turn, and the
        # tail parser picks that up as last_event_type. Pending-tool state
        # must clear once the result lands so the card stops showing a
        # spinner. (This caught a real bug class: pending_tool only clears
        # on `result`/`user`, not on `assistant`, so a final assistant turn
        # without a closing result would leave the card "stuck".)
        self.assertEqual(card["last_event_type"], "result")
        self.assertIsNone(card["pending_tool"])
        self.assertIsNone(card["pending_file"])


class TestClaudeDesktopVisibility(unittest.TestCase):
    def setUp(self):
        self.tmp_home = tempfile.mkdtemp(prefix="ccc-claude-desktop-home-")
        self._prev_home = os.environ.get("HOME")
        self.resolved_home = Path(self.tmp_home).resolve()
        os.environ["HOME"] = str(self.resolved_home)
        self.server = _fresh_server()
        self.fake_repo = (self.resolved_home / "fake-repo").resolve()
        self.fake_repo.mkdir(parents=True)
        (self.fake_repo / ".git").mkdir()
        self.projects_dir = self.server._canonical_conversation_path(
            str(self.fake_repo),
            CLAUDE_DESKTOP_SESSION_ID,
        ).parent
        self.projects_dir.mkdir(parents=True)
        self.desktop_workspace = (
            self.resolved_home
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude-code-sessions"
            / "org-1"
            / "workspace-1"
        )
        self.desktop_workspace.mkdir(parents=True)

    def tearDown(self):
        if self._prev_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._prev_home
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(self.tmp_home, ignore_errors=True)

    def _write_claude_session(self, sid=CLAUDE_DESKTOP_SESSION_ID):
        path = self.projects_dir / f"{sid}.jsonl"
        _write_jsonl_events(path, [
            {
                "type": "user",
                "sessionId": sid,
                "timestamp": "2026-05-02T00:00:00.000Z",
                "cwd": str(self.fake_repo),
                "gitBranch": "main",
                "message": {
                    "role": "user",
                    "content": "Make the Desktop sidebar show CCC sessions",
                },
            },
            {
                "type": "assistant",
                "sessionId": sid,
                "timestamp": "2026-05-02T00:00:05.000Z",
                "cwd": str(self.fake_repo),
                "message": {
                    "role": "assistant",
                    "model": "claude-opus-4-test",
                    "content": [{"type": "text", "text": "Done"}],
                },
            },
        ])
        return path

    def test_claude_desktop_visibility_writes_metadata_for_cli_session(self):
        self._write_claude_session()

        ok = self.server._ensure_claude_desktop_session_visible(
            CLAUDE_DESKTOP_SESSION_ID,
            spawn_entry={"cwd": str(self.fake_repo), "name": "desktop-sidebar"},
        )

        meta_path = self.desktop_workspace / f"local_{CLAUDE_DESKTOP_SESSION_ID}.json"
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertTrue(ok)
        self.assertEqual(data["sessionId"], f"local_{CLAUDE_DESKTOP_SESSION_ID}")
        self.assertEqual(data["cliSessionId"], CLAUDE_DESKTOP_SESSION_ID)
        self.assertEqual(data["cwd"], str(self.fake_repo))
        self.assertEqual(data["title"], "Make the Desktop sidebar show CCC sessions")
        self.assertEqual(data["model"], "claude-opus-4-test")
        self.assertEqual(data["createdAt"], 1777680000000)
        self.assertEqual(data["lastActivityAt"], 1777680005000)
        self.assertEqual(data["completedTurns"], 1)
        self.assertEqual(int(meta_path.stat().st_mtime), 1777680005)

    def test_claude_desktop_visibility_preserves_existing_user_title(self):
        self._write_claude_session()
        existing = self.desktop_workspace / f"local_{CLAUDE_DESKTOP_APP_SESSION_ID}.json"
        existing.write_text(json.dumps({
            "sessionId": f"local_{CLAUDE_DESKTOP_APP_SESSION_ID}",
            "cliSessionId": CLAUDE_DESKTOP_SESSION_ID,
            "cwd": "/tmp/old",
            "originCwd": "/tmp/old",
            "title": "Keep my Desktop title",
            "titleSource": "user",
            "createdAt": 1,
            "lastActivityAt": 2,
            "isArchived": False,
        }), encoding="utf-8")

        ok = self.server._ensure_claude_desktop_session_visible(
            CLAUDE_DESKTOP_SESSION_ID,
            spawn_entry={"cwd": str(self.fake_repo)},
        )

        data = json.loads(existing.read_text(encoding="utf-8"))
        self.assertTrue(ok)
        self.assertEqual(data["sessionId"], f"local_{CLAUDE_DESKTOP_APP_SESSION_ID}")
        self.assertEqual(data["cliSessionId"], CLAUDE_DESKTOP_SESSION_ID)
        self.assertEqual(data["title"], "Keep my Desktop title")
        self.assertEqual(data["titleSource"], "user")
        self.assertEqual(data["cwd"], str(self.fake_repo))
        self.assertEqual(data["lastActivityAt"], 1777680005000)

    def test_claude_desktop_visibility_requires_cli_transcript(self):
        ok = self.server._ensure_claude_desktop_session_visible(
            CLAUDE_DESKTOP_MISSING_SESSION_ID,
            spawn_entry={
                "cwd": str(self.fake_repo),
                "prompt": "This session never wrote a Claude Code transcript",
            },
        )

        meta_path = self.desktop_workspace / f"local_{CLAUDE_DESKTOP_MISSING_SESSION_ID}.json"
        self.assertFalse(ok)
        self.assertFalse(meta_path.exists())

    def test_claude_desktop_prune_removes_synthetic_unresumable_rows(self):
        bad = self.desktop_workspace / f"local_{CLAUDE_DESKTOP_MISSING_SESSION_ID}.json"
        bad.write_text(json.dumps({
            "sessionId": CLAUDE_DESKTOP_MISSING_SESSION_ID,
            "cliSessionId": CLAUDE_DESKTOP_MISSING_SESSION_ID,
            "cwd": "",
            "originCwd": "",
            "createdAt": 1777680000000,
            "lastActivityAt": 1777680000000,
            "completedTurns": 0,
            "permissionMode": "default",
            "chromePermissionMode": "skip_all_permission_checks",
            "alwaysAllowedReasons": [],
            "enabledMcpTools": {},
            "remoteMcpServersConfig": [],
            "isArchived": False,
        }), encoding="utf-8")
        real_shape = self.desktop_workspace / f"local_{CLAUDE_DESKTOP_APP_SESSION_ID}.json"
        real_shape.write_text(json.dumps({
            "sessionId": CLAUDE_DESKTOP_APP_SESSION_ID,
            "cliSessionId": CLAUDE_DESKTOP_MISSING_SESSION_ID,
            "cwd": str(self.fake_repo),
            "originCwd": str(self.fake_repo),
            "createdAt": 1777680000000,
            "lastActivityAt": 1777680000000,
            "completedTurns": 0,
            "permissionMode": "default",
            "enabledMcpTools": {"fake-tool": True},
            "remoteMcpServersConfig": [],
            "alwaysAllowedReasons": [],
            "isArchived": False,
        }), encoding="utf-8")

        result = self.server.prune_unresumable_claude_desktop_metadata()

        self.assertEqual(result["pruned"], 1)
        self.assertFalse(bad.exists())
        self.assertTrue(real_shape.exists())

    def test_claude_desktop_prune_removes_transcript_unavailable_placeholders(self):
        placeholder = self.desktop_workspace / f"{CLAUDE_DESKTOP_MISSING_SESSION_ID}.json"
        placeholder.write_text(json.dumps({
            "sessionId": CLAUDE_DESKTOP_MISSING_SESSION_ID,
            "cwd": "",
            "originCwd": "",
            "createdAt": 1777680000000,
            "lastActivityAt": 1777680000000,
            "completedTurns": 0,
            "permissionMode": "default",
            "chromePermissionMode": "skip_all_permission_checks",
            "alwaysAllowedReasons": [],
            "enabledMcpTools": {},
            "remoteMcpServersConfig": [],
            "isArchived": False,
            "transcriptUnavailable": True,
            "sessionPermissionUpdates": [],
        }), encoding="utf-8")

        result = self.server.prune_unresumable_claude_desktop_metadata()

        self.assertEqual(result["pruned"], 1)
        self.assertFalse(placeholder.exists())

    def test_claude_spawn_id_resolution_marks_desktop_visible(self):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "session_id": CLAUDE_DESKTOP_SESSION_ID,
            }) + "\n")
            fh.flush()
            entry = {
                "engine": "claude",
                "pid": 8181,
                "log": fh.name,
                "cwd": str(self.fake_repo),
            }
            with mock.patch.object(
                self.server,
                "_ensure_claude_desktop_session_visible",
                return_value=True,
            ) as ensure_visible, mock.patch.object(
                self.server,
                "_update_spawn_session_id_in_registry",
            ) as update_registry:
                sid = self.server._spawn_session_id_from_entry(entry)

        self.assertEqual(sid, CLAUDE_DESKTOP_SESSION_ID)
        self.assertEqual(entry["session_id"], CLAUDE_DESKTOP_SESSION_ID)
        ensure_visible.assert_called_once_with(
            CLAUDE_DESKTOP_SESSION_ID,
            spawn_entry=entry,
        )
        update_registry.assert_called_once_with(8181, CLAUDE_DESKTOP_SESSION_ID)

    def test_claude_desktop_backfill_scans_recent_ccc_logs(self):
        self._write_claude_session()
        log_dir = self.fake_repo / ".claude" / "logs"
        log_dir.mkdir(parents=True)
        log_path = log_dir / "spawn-desktop-sidebar-20260502T000000.log"
        log_path.write_text(json.dumps({
            "session_id": CLAUDE_DESKTOP_SESSION_ID,
        }) + "\n", encoding="utf-8")
        now = 1777680100
        os.utime(log_path, (now, now))

        result = self.server.backfill_claude_desktop_visibility(
            days=7,
            repo_paths=[str(self.fake_repo)],
            now=now,
        )

        meta_path = self.desktop_workspace / f"local_{CLAUDE_DESKTOP_SESSION_ID}.json"
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertTrue(meta_path.is_file())


class TestCodexConversationAdapter(unittest.TestCase):
    """Codex stores durable threads in ~/.codex/state_*.sqlite plus rollout
    JSONL files. CCC should surface those rows like regular session cards."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_home = tempfile.mkdtemp(prefix="ccc-codex-home-")
        cls.fake_repo = (Path(cls.tmp_home) / "fake-repo").resolve()
        cls.fake_repo.mkdir(parents=True)
        (cls.fake_repo / ".git").mkdir()
        cls.fake_worktree = (Path(cls.tmp_home) / "fake-repo-wt-codex").resolve()
        cls.fake_worktree.mkdir(parents=True)
        resolved_home = Path(cls.tmp_home).resolve()
        cls._prev_env = {
            "HOME": os.environ.get("HOME"),
        }
        os.environ["HOME"] = str(resolved_home)

        codex_dir = resolved_home / ".codex"
        rollout_dir = codex_dir / "sessions" / "2026" / "05" / "02"
        rollout_dir.mkdir(parents=True)
        cls.rollout = rollout_dir / f"rollout-2026-05-02T00-00-00-{CODEX_SESSION_ID}.jsonl"
        lines = [
            {
                "timestamp": "2026-05-02T00:00:00.000Z",
                "type": "session_meta",
                "payload": {
                    "id": CODEX_SESSION_ID,
                    "timestamp": "2026-05-02T00:00:00.000Z",
                    "cwd": str(cls.fake_repo),
                    "source": "exec",
                    "model": "gpt-5.5",
                },
            },
            {
                "timestamp": "2026-05-02T00:00:01.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": "please edit README.md",
                    "images": [],
                },
            },
            {
                "timestamp": "2026-05-02T00:00:02.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "apply_patch",
                    "arguments": "{}",
                    "call_id": "call_patch",
                },
            },
            {
                "timestamp": "2026-05-02T00:00:03.000Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_patch",
                    "output": "Success",
                },
            },
            {
                "timestamp": "2026-05-02T00:00:04.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": (
                        "Done.\n\n<session-state>\n"
                        "DID: Edited the readme.\n"
                        "INSIGHT: Codex rollout parsed.\n"
                        "NEXT_STEP_USER: Review it.\n"
                        "</session-state>\n\n"
                        f"Opened the PR: https://github.com/octo-org/demo-repo/pull/{25}\n"
                        "Branch: feat/codex-parity\n"
                        f"Worktree: {cls.fake_worktree}"
                    ),
                    "phase": "final_answer",
                },
            },
            {
                "timestamp": "2026-05-02T00:00:04.500Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "last_token_usage": {
                            "input_tokens": 1200,
                            "cached_input_tokens": 800,
                            "output_tokens": 45,
                            "reasoning_output_tokens": 12,
                            "total_tokens": 1245,
                        },
                        "total_token_usage": {
                            "input_tokens": 10000,
                            "cached_input_tokens": 7000,
                            "output_tokens": 450,
                            "reasoning_output_tokens": 120,
                            "total_tokens": 10450,
                        },
                        "model_context_window": 258400,
                    },
                },
            },
            {
                "timestamp": "2026-05-02T00:00:05.000Z",
                "type": "event_msg",
                "payload": {"type": "task_complete", "duration_ms": 1234},
            },
        ]
        cls.rollout.write_text(
            "\n".join(json.dumps(line) for line in lines) + "\n",
            encoding="utf-8",
        )
        cls.trailer_prompt = (
            "please inspect app_node toast\n\n"
            "Before your final reply, end with a block formatted EXACTLY like this "
            "(the Claude Command Center dashboard parses it):\n"
            "<session-state>\n"
            "DID: <one sentence>\n"
            "INSIGHT: <one sentence>\n"
            "NEXT_STEP_USER: <one sentence>\n"
            "</session-state>"
        )
        cls.trailer_rollout = (
            rollout_dir / f"rollout-2026-05-02T00-01-00-{CODEX_TRAILER_SESSION_ID}.jsonl"
        )
        trailer_lines = [
            {
                "timestamp": "2026-05-02T00:01:00.000Z",
                "type": "session_meta",
                "payload": {
                    "id": CODEX_TRAILER_SESSION_ID,
                    "timestamp": "2026-05-02T00:01:00.000Z",
                    "cwd": str(cls.fake_repo),
                    "source": "exec",
                    "model": "gpt-5.5",
                },
            },
            {
                "timestamp": "2026-05-02T00:01:01.000Z",
                "type": "event_msg",
                "payload": {
                    "type": "user_message",
                    "message": cls.trailer_prompt,
                    "images": [],
                },
            },
        ]
        cls.trailer_rollout.write_text(
            "\n".join(json.dumps(line) for line in trailer_lines) + "\n",
            encoding="utf-8",
        )

        db_path = codex_dir / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        try:
            con.execute(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    rollout_path TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    model_provider TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    title TEXT NOT NULL,
                    sandbox_policy TEXT NOT NULL,
                    approval_mode TEXT NOT NULL,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    has_user_event INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    cli_version TEXT NOT NULL DEFAULT '',
                    first_user_message TEXT NOT NULL DEFAULT '',
                    model TEXT,
                    reasoning_effort TEXT,
                    git_branch TEXT,
                    thread_source TEXT
                )
                """
            )
            con.execute(
                """
                INSERT INTO threads (
                    id, rollout_path, created_at, updated_at, source,
                    model_provider, cwd, title, sandbox_policy, approval_mode,
                    tokens_used, has_user_event, archived, cli_version,
                    first_user_message, model, reasoning_effort, git_branch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    CODEX_SESSION_ID,
                    str(cls.rollout),
                    1777680000,
                    1777680005,
                    "exec",
                    "openai",
                    str(cls.fake_repo),
                    "Codex readme edit",
                    "danger-full-access",
                    "never",
                    42,
                    1,
                    0,
                    "0.test",
                    "please edit README.md",
                    "gpt-5.5",
                    "medium",
                    "main",
                ),
            )
            con.execute(
                """
                INSERT INTO threads (
                    id, rollout_path, created_at, updated_at, source,
                    model_provider, cwd, title, sandbox_policy, approval_mode,
                    tokens_used, has_user_event, archived, cli_version,
                    first_user_message, model, reasoning_effort, git_branch
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    CODEX_TRAILER_SESSION_ID,
                    str(cls.trailer_rollout),
                    1777680060,
                    1777680061,
                    "exec",
                    "openai",
                    str(cls.fake_repo),
                    "",
                    "danger-full-access",
                    "never",
                    0,
                    1,
                    0,
                    "0.test",
                    cls.trailer_prompt,
                    "gpt-5.5",
                    "medium",
                    "main",
                ),
            )
            con.commit()
        finally:
            con.close()

        cls.server = _fresh_server()

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(cls.tmp_home, ignore_errors=True)

    def setUp(self):
        self.server._reset_ttl_memo_caches()

    def _set_codex_thread_source(self, session_id, thread_source):
        db_path = Path(self.tmp_home) / ".codex" / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        try:
            con.execute(
                "UPDATE threads SET thread_source = ? WHERE id = ?",
                (thread_source, session_id),
            )
            con.commit()
        finally:
            con.close()

    def _codex_thread_source(self, session_id):
        db_path = Path(self.tmp_home) / ".codex" / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT thread_source FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def _set_codex_source(self, session_id, source):
        db_path = Path(self.tmp_home) / ".codex" / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        try:
            con.execute(
                "UPDATE threads SET source = ? WHERE id = ?",
                (source, session_id),
            )
            con.commit()
        finally:
            con.close()

    def _codex_source(self, session_id):
        db_path = Path(self.tmp_home) / ".codex" / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        try:
            row = con.execute(
                "SELECT source FROM threads WHERE id = ?",
                (session_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def _set_codex_updated_at(self, session_id, updated_at):
        db_path = Path(self.tmp_home) / ".codex" / "state_5.sqlite"
        con = sqlite3.connect(db_path)
        try:
            con.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                (updated_at, session_id),
            )
            con.commit()
        finally:
            con.close()

    def _codex_rollout_meta(self):
        with self.rollout.open(encoding="utf-8") as fh:
            for line in fh:
                obj = json.loads(line)
                if obj.get("type") == "session_meta":
                    return obj.get("payload") or {}
        return {}

    def test_codex_thread_is_discovered_as_session_card(self):
        cards = self.server.find_codex_conversations(str(self.fake_repo))
        card = next(c for c in cards if c["session_id"] == CODEX_SESSION_ID)
        self.assertEqual(card["source"], "codex")
        self.assertEqual(card["engine"], "codex")
        self.assertEqual(card["display_name"], "Codex readme edit")
        self.assertTrue(card["has_edit"])
        self.assertEqual(card["last_event_type"], "result")
        self.assertEqual(card["session_state"]["did"], "Edited the readme.")
        self.assertEqual(card["tail_pr_number"], 25)
        self.assertEqual(
            card["tail_pr_url"],
            f"https://github.com/octo-org/demo-repo/pull/{25}",
        )
        self.assertEqual(card["effective_branch"], "feat/codex-parity")
        self.assertEqual(card["effective_kind"], "worktree")
        self.assertEqual(card["folder_path"], str(self.fake_repo))
        self.assertEqual(card["session_cwd"], str(self.fake_worktree))
        self.assertTrue(card["session_cwd_is_worktree"])

    def test_codex_visibility_stamp_marks_exec_thread_as_user(self):
        self._set_codex_thread_source(CODEX_SESSION_ID, None)
        self._set_codex_source(CODEX_SESSION_ID, "exec")
        old_mtime = 1777680005.0
        os.utime(self.rollout, (old_mtime, old_mtime))

        result = self.server._mark_codex_thread_user_visible(CODEX_SESSION_ID)

        self.assertTrue(result)
        self.assertEqual(self._codex_thread_source(CODEX_SESSION_ID), "user")
        self.assertEqual(self._codex_source(CODEX_SESSION_ID), "vscode")
        meta = self._codex_rollout_meta()
        self.assertEqual(meta.get("thread_source"), "user")
        self.assertEqual(meta.get("source"), "vscode")
        self.assertEqual(self.rollout.stat().st_mtime, old_mtime)

    def test_codex_visibility_stamp_repairs_user_exec_thread(self):
        self._set_codex_thread_source(CODEX_SESSION_ID, "user")
        self._set_codex_source(CODEX_SESSION_ID, "exec")

        result = self.server._mark_codex_thread_user_visible(CODEX_SESSION_ID)

        self.assertTrue(result)
        self.assertEqual(self._codex_thread_source(CODEX_SESSION_ID), "user")
        self.assertEqual(self._codex_source(CODEX_SESSION_ID), "vscode")

    def test_codex_spawn_id_resolution_marks_thread_user_visible(self):
        self._set_codex_thread_source(CODEX_SESSION_ID, None)
        self._set_codex_source(CODEX_SESSION_ID, "exec")
        with tempfile.NamedTemporaryFile("w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "thread.started",
                "thread_id": CODEX_SESSION_ID,
            }) + "\n")
            fh.flush()
            entry = {
                "engine": "codex",
                "pid": 9090,
                "log": fh.name,
            }
            with mock.patch.object(
                self.server,
                "_update_spawn_session_id_in_registry",
            ) as update_registry:
                sid = self.server._spawn_session_id_from_entry(entry)

        self.assertEqual(sid, CODEX_SESSION_ID)
        self.assertEqual(entry["session_id"], CODEX_SESSION_ID)
        self.assertEqual(self._codex_thread_source(CODEX_SESSION_ID), "user")
        self.assertEqual(self._codex_source(CODEX_SESSION_ID), "vscode")
        update_registry.assert_called_once_with(9090, CODEX_SESSION_ID)

    def test_codex_sidebar_backfill_marks_recent_ccc_logs_only(self):
        self._set_codex_thread_source(CODEX_SESSION_ID, None)
        self._set_codex_thread_source(CODEX_TRAILER_SESSION_ID, None)
        self._set_codex_source(CODEX_SESSION_ID, "exec")
        self._set_codex_source(CODEX_TRAILER_SESSION_ID, "exec")
        self._set_codex_updated_at(CODEX_SESSION_ID, 1777680005)
        log_dir = self.fake_repo / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "spawn-codex-readme-20260502T000000.log"
        log_path.write_text(
            json.dumps({
                "type": "thread.started",
                "thread_id": CODEX_SESSION_ID,
            }) + "\n",
            encoding="utf-8",
        )
        now = time.time()
        os.utime(log_path, (now, now))

        with mock.patch.object(
            self.server,
            "_append_codex_sidebar_project_roots",
            return_value=0,
        ):
            result = self.server.backfill_codex_sidebar_visibility(
                days=7,
                repo_paths=[str(self.fake_repo)],
                now=now,
            )

        self.assertEqual(result["found"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self._codex_thread_source(CODEX_SESSION_ID), "user")
        self.assertEqual(self._codex_source(CODEX_SESSION_ID), "vscode")
        self.assertIsNone(self._codex_thread_source(CODEX_TRAILER_SESSION_ID))
        self.assertEqual(self._codex_source(CODEX_TRAILER_SESSION_ID), "exec")
        self.assertEqual(self.rollout.stat().st_mtime, 1777680005)

    def test_codex_sidebar_backfill_scans_recent_codex_cwds(self):
        self._set_codex_thread_source(CODEX_SESSION_ID, None)
        self._set_codex_source(CODEX_SESSION_ID, "exec")
        log_dir = self.fake_repo / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "spawn-codex-cwd-derived-20260502T000000.log"
        log_path.write_text(
            json.dumps({
                "type": "thread.started",
                "thread_id": CODEX_SESSION_ID,
            }) + "\n",
            encoding="utf-8",
        )
        now = 1777680100
        os.utime(log_path, (now, now))

        with mock.patch.object(self.server, "_known_repo_paths", return_value=[]), \
             mock.patch.object(
                 self.server,
                 "_append_codex_sidebar_project_roots",
                 return_value=0,
             ):
            result = self.server.backfill_codex_sidebar_visibility(days=7, now=now)

        self.assertEqual(result["found"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self._codex_thread_source(CODEX_SESSION_ID), "user")
        self.assertEqual(self._codex_source(CODEX_SESSION_ID), "vscode")

    def test_codex_sidebar_backfill_adds_project_roots(self):
        self._set_codex_thread_source(CODEX_SESSION_ID, None)
        self._set_codex_source(CODEX_SESSION_ID, "exec")
        log_dir = self.fake_repo / ".claude" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "spawn-codex-project-root-20260502T000000.log"
        log_path.write_text(
            json.dumps({
                "type": "thread.started",
                "thread_id": CODEX_SESSION_ID,
            }) + "\n",
            encoding="utf-8",
        )
        now = time.time()
        os.utime(log_path, (now, now))

        with mock.patch.object(
            self.server,
            "_append_codex_sidebar_project_roots",
            return_value=1,
        ) as add_projects:
            result = self.server.backfill_codex_sidebar_visibility(
                days=7,
                repo_paths=[str(self.fake_repo)],
                now=now,
            )

        self.assertEqual(result["projects_added"], 1)
        add_projects.assert_called_once_with([str(self.fake_repo)])

    def test_codex_sidebar_project_roots_write_offline_state(self):
        state_path = Path(self.tmp_home) / ".codex" / ".codex-global-state.json"
        state_path.write_text(
            json.dumps({
                "electron-saved-workspace-roots": [str(self.fake_worktree)],
                "project-order": [str(self.fake_worktree)],
                "active-workspace-roots": [str(self.fake_worktree)],
            }),
            encoding="utf-8",
        )

        with mock.patch.object(
            self.server,
            "_codex_desktop_app_is_running",
            return_value=False,
        ):
            added = self.server._append_codex_sidebar_project_roots([str(self.fake_repo)])

        data = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(added, 1)
        self.assertEqual(data["electron-saved-workspace-roots"][0], str(self.fake_repo))
        self.assertEqual(data["project-order"][0], str(self.fake_repo))

    def test_codex_sidebar_project_roots_use_deeplink_when_running(self):
        state_path = Path(self.tmp_home) / ".codex" / ".codex-global-state.json"
        state_path.write_text(
            json.dumps({
                "electron-saved-workspace-roots": [],
                "project-order": [],
                "active-workspace-roots": [str(self.fake_worktree)],
            }),
            encoding="utf-8",
        )

        with mock.patch.object(
            self.server,
            "_codex_desktop_app_is_running",
            return_value=True,
        ), mock.patch.object(
            self.server,
            "_open_codex_workspace_root_deeplink",
            return_value=True,
        ) as open_link, mock.patch.object(
            self.server,
            "_wait_for_codex_workspace_roots",
            return_value={str(self.fake_repo)},
        ):
            added = self.server._append_codex_sidebar_project_roots([str(self.fake_repo)])

        self.assertEqual(added, 1)
        open_link.assert_any_call(str(self.fake_repo))
        open_link.assert_any_call(str(self.fake_worktree))

    def test_codex_session_state_instruction_is_not_title_text(self):
        cards = self.server.find_codex_conversations(str(self.fake_repo))
        card = next(c for c in cards if c["session_id"] == CODEX_TRAILER_SESSION_ID)
        self.assertEqual(card["display_name"], "please inspect app_node toast")
        self.assertEqual(card["first_message"], "please inspect app_node toast")
        self.assertNotIn("Before your final reply", card["display_name"])
        self.assertNotIn("<session-state>", card["first_message"])

        parsed = self.server.parse_conversation(CODEX_TRAILER_SESSION_ID)
        user_event = next(ev for ev in parsed["events"] if ev["type"] == "user_text")
        self.assertEqual(user_event["text"], "please inspect app_node toast")

    def test_codex_rollout_parses_into_conversation_events(self):
        parsed = self.server.parse_conversation(CODEX_SESSION_ID)
        event_types = [ev["type"] for ev in parsed["events"]]
        self.assertIn("user_text", event_types)
        self.assertIn("assistant", event_types)
        self.assertIn("tool_result", event_types)
        self.assertIn("result", event_types)
        assistant_texts = [
            block["text"]
            for ev in parsed["events"] if ev["type"] == "assistant"
            for block in ev.get("blocks", []) if block.get("kind") == "text"
        ]
        self.assertTrue(any("Edited the readme" in text for text in assistant_texts))
        result = next(ev for ev in parsed["events"] if ev["type"] == "result")
        self.assertNotIn("cost_usd", result)
        self.assertEqual(result["token_usage"]["input_tokens"], 1200)
        self.assertEqual(result["token_usage"]["cached_input_tokens"], 800)
        self.assertEqual(result["token_usage"]["output_tokens"], 45)
        self.assertEqual(result["token_usage"]["reasoning_output_tokens"], 12)

    def test_codex_usage_uses_last_turn_window_not_cumulative_totals(self):
        usage = self.server.extract_session_usage(CODEX_SESSION_ID)
        self.assertEqual(usage["latest_input_tokens"], 1200)
        self.assertEqual(usage["peak_input_tokens"], 1200)
        self.assertEqual(usage["total_input_tokens"], 3000)
        self.assertEqual(usage["total_cache_read_tokens"], 7000)
        self.assertEqual(usage["total_output_tokens"], 450)
        self.assertEqual(usage["context_limit"], 258400)

    def test_codex_injection_routes_to_codex_resume(self):
        with mock.patch.object(
            self.server,
            "resume_session_codex",
            return_value={"ok": True, "via": "codex-resume"},
        ) as patched:
            result = self.server._inject_text_into_session(CODEX_SESSION_ID, "follow up")
        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "codex-resume")
        patched.assert_called_once_with(CODEX_SESSION_ID, "follow up")

    def test_codex_live_status_prefers_spawn_registry_session_id(self):
        with mock.patch.object(
            self.server,
            "_load_spawn_registry",
            return_value=[{
                "pid": 12345,
                "session_id": CODEX_SESSION_ID,
                "engine": "codex",
                "cwd": str(self.fake_repo),
            }],
        ), mock.patch.object(
            self.server, "_pid_is_engine_process", return_value=True
        ), mock.patch.object(
            self.server, "_process_tty", return_value=None
        ), mock.patch.object(
            self.server, "_proc_cwd", return_value=str(self.fake_repo)
        ), mock.patch.object(
            self.server, "_proc_ancestor_terminal", return_value=(None, None)
        ):
            status = self.server.session_live_status(CODEX_SESSION_ID, str(self.fake_repo))

        self.assertTrue(status["live"])
        self.assertEqual(status["pid"], 12345)
        self.assertEqual(status["match_count"], 1)
        self.assertEqual(status["cwd"], str(self.fake_repo))

    def test_live_codex_process_scan_matches_truncated_comm_via_args(self):
        ps_result = mock.Mock(
            stdout=(
                "123 ?? /opt/homebrew/bi /opt/homebrew/bin/codex exec "
                "resume --json 11111111-1111-4111-8111-111111111111\n"
            )
        )
        with mock.patch.object(
            self.server.subprocess, "run", return_value=ps_result
        ), mock.patch.object(
            self.server, "_proc_cwd", return_value=str(self.fake_repo)
        ), mock.patch.object(
            self.server, "_proc_ancestor_terminal", return_value=(None, None)
        ):
            rows = self.server.find_live_codex_processes()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pid"], 123)
        self.assertIn("codex exec resume", rows[0]["command"])

    def test_codex_exact_session_match_ignores_prompt_text_after_delimiter(self):
        sid = CODEX_SESSION_ID
        self.assertTrue(self.server._command_targets_engine_session(
            f"/opt/homebrew/bin/codex exec resume --json {sid} follow up",
            sid,
            "codex",
        ))
        self.assertFalse(self.server._command_targets_engine_session(
            f"/opt/homebrew/bin/codex exec --json -- prompt mentions {sid}",
            sid,
            "codex",
        ))

    def test_dead_spawn_registry_entry_does_not_cwd_match_other_codex_run(self):
        with mock.patch.object(
            self.server,
            "_load_spawn_registry",
            return_value=[{
                "pid": 12345,
                "session_id": CODEX_SESSION_ID,
                "engine": "codex",
                "cwd": str(self.fake_repo),
            }],
        ), mock.patch.object(
            self.server, "_pid_is_engine_process", return_value=False
        ), mock.patch.object(
            self.server,
            "find_live_codex_processes",
            return_value=[{
                "pid": 23456,
                "tty": None,
                "cwd": str(self.fake_repo),
                "terminal_app": None,
                "command": "/opt/homebrew/bin/codex exec unrelated prompt",
            }],
        ):
            status = self.server.session_live_status(CODEX_SESSION_ID, str(self.fake_repo))

        self.assertFalse(status["live"])
        self.assertEqual(status["match_count"], 0)


class TestCodexActivityFields(unittest.TestCase):
    """Codex rows synthesize Claude-like live activity chips from rollouts."""

    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def test_pending_tool_maps_to_active_tool_chip(self):
        fields = self.server._codex_activity_fields_from_tail({
            "pending_tool": "apply_patch",
            "pending_file": "README.md",
            "last_meaningful_ts": 1700000000,
            "last_event_type": "assistant",
        }, live=True)
        self.assertEqual(fields["sidecar_status"], "active")
        self.assertEqual(fields["sidecar_tool"], "apply_patch")
        self.assertEqual(fields["sidecar_file"], "README.md")
        self.assertEqual(fields["sidecar_ts"], 1700000000)
        self.assertTrue(fields["sidecar_in_flight"])

    def test_live_mid_turn_without_tool_maps_to_thinking_chip(self):
        fields = self.server._codex_activity_fields_from_tail({
            "pending_tool": None,
            "last_meaningful_ts": 1700000001,
            "last_event_type": "user",
        }, live=True)
        self.assertEqual(fields["sidecar_status"], "active")
        self.assertEqual(fields["sidecar_tool"], "Thinking")
        self.assertIsNone(fields["sidecar_file"])
        self.assertEqual(fields["sidecar_ts"], 1700000001)
        self.assertTrue(fields["sidecar_in_flight"])

    def test_completed_or_dormant_codex_has_no_activity_chip(self):
        completed = self.server._codex_activity_fields_from_tail({
            "last_meaningful_ts": 1700000002,
            "last_event_type": "result",
        }, live=True)
        dormant = self.server._codex_activity_fields_from_tail({
            "pending_tool": "apply_patch",
            "last_event_type": "assistant",
        }, live=False)
        self.assertIsNone(completed["sidecar_tool"])
        self.assertFalse(completed["sidecar_in_flight"])
        self.assertIsNone(dormant["sidecar_tool"])
        self.assertFalse(dormant["sidecar_in_flight"])

    def test_stale_codex_tool_is_not_treated_as_running(self):
        tail = {
            "pending_tool": "write_stdin",
            "pending_file": "session 7095",
            "pending_tool_ts": 1700000000,
            "last_meaningful_ts": 1700000000,
            "last_event_type": "assistant",
        }

        stale = self.server._codex_stale_tool_fields(
            tail,
            now=1700000901,
            threshold_s=900,
        )
        fields = self.server._codex_activity_fields_from_tail(tail, live=True)

        self.assertTrue(stale["stale_tool_call"])
        self.assertEqual(stale["stale_tool_age_s"], 901)
        self.assertIsNone(fields["sidecar_tool"])
        self.assertFalse(fields["sidecar_in_flight"])

    def test_codex_tail_meta_records_pending_tool_timestamp(self):
        event = {
            "type": "response_item",
            "timestamp": "2026-06-01T17:01:37.718Z",
            "payload": {
                "type": "function_call",
                "name": "write_stdin",
                "call_id": "call_stale",
                "arguments": json.dumps({
                    "session_id": 7095,
                    "chars": "",
                    "yield_time_ms": 1000,
                }),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout.jsonl"
            _write_jsonl_events(path, [event])
            meta = self.server._extract_codex_tail_meta(path)

        self.assertEqual(meta["pending_tool"], "write_stdin")
        self.assertGreater(meta["pending_tool_ts"], 0)
        self.assertEqual(meta["pending_tool_ts"], meta["last_meaningful_ts"])


class TestShellCommandPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def test_strips_shell_option_wrapper(self):
        cmd = (
            "true && unsetopt NO_EXTENDED_GLOB 2>/dev/null || true && "
            "setopt NO_EXTENDED_GLOB 2>/dev/null || true && "
            "python3 -m pytest tests/test_smoke.py"
        )

        self.assertEqual(
            self.server._shell_command_preview(cmd),
            "python3 -m pytest tests/test_smoke.py",
        )

    def test_keeps_real_command_chain(self):
        cmd = "cd /tmp/project && npm run build"

        self.assertEqual(
            self.server._shell_command_preview(cmd),
            "cd /tmp/project && npm run build",
        )

    def test_redacts_secrets(self):
        cmd = "curl -H 'Authorization: Bearer sk-ant-test-XXXXXXXXXXXXXXXX' https://example.test"

        self.assertNotIn("sk-ant-test-XXXXXXXXXXXXXXXX", self.server._shell_command_preview(cmd))
        self.assertIn("[redacted]", self.server._shell_command_preview(cmd))

    def test_inline_python_script_gets_readable_activity_label(self):
        cmd = (
            "/tmp/venv/bin/python3 << 'EOF'\n"
            "import json, shutil\n"
            "src = '/tmp/demo/draft_info.json'\n"
            "shutil.copy2(src, src + '.bak')\n"
            "# inspect the current state after the edit\n"
            "print('Current segments:')\n"
            "EOF"
        )

        label = self.server._shell_command_activity_label(cmd)

        self.assertIn("Python script", label)
        self.assertIn("backs up a file", label)
        self.assertIn("inspect the current state", label)
        self.assertNotIn("import json", label)

    def test_inline_shell_comment_gets_readable_activity_label(self):
        cmd = (
            'CCC_URL="http://127.0.0.1:8090" '
            "# Check if there's a rename endpoint by looking at the API\n"
            'curl -s "$CCC_URL/api/conversations?repo_path=/tmp/repo" | '
            "python3 -c \"print('x')\""
        )

        label = self.server._shell_command_activity_label(cmd)

        self.assertEqual(
            label,
            "Shell command: Check if there's a rename endpoint by looking at the API",
        )
        self.assertNotIn("curl -s", label)
        self.assertNotIn("python3 -c", label)

    def test_inline_shell_comment_ignores_hash_inside_quotes(self):
        cmd = "python3 -c \"print('# not a shell comment')\""

        label = self.server._shell_command_activity_label(cmd)

        self.assertNotIn("Shell command:", label)
        self.assertIn("python3 -c", label)

    def test_collapsed_inline_shell_comment_trims_following_command(self):
        cmd = (
            'CCC_URL="http://127.0.0.1:8090" '
            "# Check if there's a rename endpoint by looking at the API "
            'curl -s "$CCC_URL/api/conversations?repo_path=/tmp/repo" | '
            "python3 -c \"print('x')\""
        )

        label = self.server._shell_command_activity_label(cmd)

        self.assertEqual(
            label,
            "Shell command: Check if there's a rename endpoint by looking at the API",
        )

    def test_bash_tool_parse_keeps_readable_label_and_raw_command(self):
        cmd = (
            "python3 << 'EOF'\n"
            "token = 'sk-ant-test-XXXXXXXXXXXXXXXX'\n"
            "# inspect transcript segments\n"
            "print(token)\n"
            "EOF"
        )
        ev = {
            "type": "assistant",
            "timestamp": "2026-05-23T12:00:00.000Z",
            "message": {
                "id": "msg-test",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu-test",
                        "name": "Bash",
                        "input": {"command": cmd},
                    }
                ],
            },
        }

        parsed = self.server._parse_conversation_event(ev, 1)
        block = parsed["blocks"][0]

        self.assertEqual(block["kind"], "tool_use")
        self.assertIn("Python script", block["detail"])
        self.assertIn("inspect transcript segments", block["detail"])
        self.assertIn("\n", block["command"])
        self.assertIn("[redacted]", block["command"])
        self.assertNotIn("sk-ant-test-XXXXXXXXXXXXXXXX", block["command"])


class TestAntigravityParsing(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def test_embedded_system_task_message_is_not_assistant_text(self):
        ev = {
            "step_index": 242,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-05-24T23:54:20Z",
            "content": (
                "[Message] timestamp=2026-05-24T23:54:33Z sender=system "
                "priority=MESSAGE_PRIORITY_LOW content=[Task "
                "707a2446-4bcb-4b07-a697-6b8e7411b401/task-241] command: "
                '"npx turbo build --filter=bookyourmat" finished, exit code: 0. Output:\n'
                "Actual tasks: 2 / 2\n"
                "_\n"
                "</div>\n"
            ),
        }

        parsed = self.server._parse_antigravity_event(ev, 304)

        self.assertIsNone(parsed)

    def test_regular_antigravity_planner_text_still_renders(self):
        ev = {
            "step_index": 243,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "status": "DONE",
            "created_at": "2026-05-24T23:55:05Z",
            "content": "The build completed successfully.",
        }

        parsed = self.server._parse_antigravity_event(ev, 305)

        self.assertEqual(parsed["type"], "assistant")
        self.assertEqual(parsed["blocks"][0]["text"], "The build completed successfully.")


class TestAddSidecarFields(unittest.TestCase):
    """_add_sidecar_fields() merges PreToolUse/PostToolUse hook output into
    a session card. The kanban relies on these fields (sidecar_status,
    sidecar_tool, sidecar_has_writes) to decide the column for live
    sessions, so a regression here silently mis-classifies cards."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_home = tempfile.mkdtemp(prefix="ccc-sidecar-home-")
        cls._prev_env = {"HOME": os.environ.get("HOME")}
        # Resolve so we don't trip over /var → /private/var on macOS.
        os.environ["HOME"] = str(Path(cls.tmp_home).resolve())
        cls.server = _fresh_server()
        cls.sidecar_dir = cls.server.SIDECAR_STATE_DIR
        cls.sidecar_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(cls.tmp_home, ignore_errors=True)

    def _write_sidecar(self, sid, body):
        import json
        (self.sidecar_dir / f"{sid}.json").write_text(json.dumps(body))

    def test_dead_session_gets_blank_sidecar_block(self):
        """is_live=False ⇒ no sidecar reads, all fields default to None/0/False."""
        entry = {"session_id": "dead-sid", "is_live": False}
        self.server._add_sidecar_fields(entry)
        self.assertIsNone(entry["sidecar_status"])
        self.assertFalse(entry["sidecar_has_writes"])
        self.assertIsNone(entry["sidecar_tool"])
        self.assertIsNone(entry["sidecar_file"])
        self.assertEqual(entry["sidecar_ts"], 0)

    def test_live_session_merges_sidecar_state(self):
        """is_live=True ⇒ tool/file/status/has_writes come from the side-car
        JSON the hooks wrote to ~/.claude/command-center/live-state/."""
        sid = "live-completed-sid"
        self._write_sidecar(sid, {
            "status": "waiting",
            "has_writes": True,
            "tool": "Edit",
            "file": "/tmp/mock-session/README.md",
            "timestamp": 1700000000,
        })
        entry = {"session_id": sid, "is_live": True}
        self.server._add_sidecar_fields(entry)
        self.assertEqual(entry["sidecar_status"], "waiting")
        self.assertTrue(entry["sidecar_has_writes"])
        self.assertEqual(entry["sidecar_tool"], "Edit")
        self.assertEqual(entry["sidecar_file"], "/tmp/mock-session/README.md")
        self.assertEqual(entry["sidecar_ts"], 1700000000)

    def test_live_session_with_no_sidecar_file_returns_blanks(self):
        """A live session that hasn't yet emitted a sidecar (e.g. just
        spawned, no tool calls yet) must still produce a fully-formed
        entry — missing keys would crash the JSON serializer downstream."""
        entry = {"session_id": "no-sidecar-yet-sid", "is_live": True}
        self.server._add_sidecar_fields(entry)
        self.assertIsNone(entry["sidecar_status"])
        self.assertIsNone(entry["sidecar_tool"])
        self.assertIsNone(entry["sidecar_file"])
        self.assertFalse(entry["sidecar_has_writes"])
        self.assertEqual(entry["sidecar_ts"], 0)


class TestLiveSessionsActivity(unittest.TestCase):
    """build_live_sessions_activity() backs /api/sessions/live-activity."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_home = tempfile.mkdtemp(prefix="ccc-live-act-home-")
        cls._prev_env = {"HOME": os.environ.get("HOME")}
        os.environ["HOME"] = str(Path(cls.tmp_home).resolve())
        cls.server = _fresh_server()
        cls.sidecar_dir = cls.server.SIDECAR_STATE_DIR
        cls.sidecar_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        shutil.rmtree(cls.tmp_home, ignore_errors=True)

    def test_live_activity_includes_in_flight_bash(self):
        sid = "live-bash-sid-000000000001"
        inflight = {
            "tool": "Bash",
            "file": "git status",
            "started_at": 1700000001,
        }
        (self.sidecar_dir / f"{sid}_in_flight.json").write_text(json.dumps(inflight))
        with mock.patch.object(self.server, "_archive_session_is_live", return_value=True):
            payload = self.server.build_live_sessions_activity()
        self.assertIn(sid, payload)
        row = payload[sid]
        self.assertTrue(row.get("is_live"))
        self.assertEqual(row.get("sidecar_tool"), "Bash")
        self.assertTrue(row.get("sidecar_in_flight"))


if __name__ == "__main__":
    unittest.main()
