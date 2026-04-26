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

stdlib-only — `unittest`, no pytest, no mock libs.
"""
import importlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = Path(REPO_ROOT) / "tests" / "fixtures" / "mock_session.jsonl"
MOCK_SESSION_ID = "00000000-mock-4000-8000-000000000001"

sys.path.insert(0, REPO_ROOT)


def _fresh_server():
    """Re-import server.py so module-level Path constants pick up our env."""
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


class TestFindConversationsOnMockFixture(unittest.TestCase):
    """find_conversations() should locate the fixture session and parse its
    signals (has_edit, pending_tool, last_event_type, session_state)."""

    @classmethod
    def setUpClass(cls):
        # Stage the fixture inside a temp ~/.claude/projects/<slug>/ tree.
        # CCC computes CONVERSATIONS_DIR from REPO_ROOT (CCC_WATCH_REPO env)
        # at import time: slug = "-" + REPO_ROOT.lstrip("/").replace("/","-").
        cls.tmp_home = tempfile.mkdtemp(prefix="ccc-mock-home-")
        # Resolve up front: on macOS /var/folders is a symlink to
        # /private/var/folders, and server.py runs Path(...).resolve() on
        # CCC_WATCH_REPO. If we computed the slug from the unresolved path
        # we'd point at the wrong projects dir.
        cls.fake_repo = (Path(cls.tmp_home) / "fake-repo").resolve()
        cls.fake_repo.mkdir(parents=True)
        # HOME also has to resolve for the same reason — server.py reads
        # Path.home() at import time to derive the projects root.
        resolved_home = Path(cls.tmp_home).resolve()

        slug = "-" + str(cls.fake_repo).lstrip("/").replace("/", "-")
        cls.projects_dir = resolved_home / ".claude" / "projects" / slug
        cls.projects_dir.mkdir(parents=True)
        cls.resolved_home = resolved_home

        # Copy fixture under <session_id>.jsonl so find_session_cwd / scanners
        # match by filename.
        target = cls.projects_dir / f"{MOCK_SESSION_ID}.jsonl"
        shutil.copy(FIXTURE, target)
        cls.target_path = target

        # Tell server.py: "this is the repo I'm watching". That sets
        # REPO_ROOT, which derives CONVERSATIONS_DIR. Also override HOME so
        # PROJECTS_ROOT (Path.home()/".claude"/"projects") resolves into
        # our tmp tree, AND so all the *FILE side-car paths
        # (SESSION_NAMES_FILE etc.) point at empty defaults instead of the
        # real user's command-center state — no test pollution.
        cls._prev_env = {
            "CCC_WATCH_REPO": os.environ.get("CCC_WATCH_REPO"),
            "HOME": os.environ.get("HOME"),
        }
        os.environ["CCC_WATCH_REPO"] = str(cls.fake_repo)
        os.environ["HOME"] = str(cls.resolved_home)

        cls.server = _fresh_server()
        # Sanity: the server should now be looking at our staged dir.
        assert cls.server.CONVERSATIONS_DIR == cls.projects_dir, (
            f"server CONVERSATIONS_DIR={cls.server.CONVERSATIONS_DIR!r} "
            f"!= staged {cls.projects_dir!r}"
        )

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
        convs = self.server.find_conversations()
        sids = [c["session_id"] for c in convs]
        self.assertIn(MOCK_SESSION_ID, sids,
                      f"mock session not found among {sids!r}")

    def test_fixture_metadata_extracted(self):
        """Custom-title, first-message, and branch must round-trip."""
        convs = self.server.find_conversations()
        card = next(c for c in convs if c["session_id"] == MOCK_SESSION_ID)
        self.assertEqual(card["display_name"], "mock-session-classifier-coverage")
        self.assertEqual(card["branch"], "main")
        self.assertIn("README.md", card["first_message"])

    def test_fixture_has_edit_signal(self):
        """The Edit tool_use must light up has_edit — the kanban uses this
        to push the card past Planning into Working."""
        convs = self.server.find_conversations()
        card = next(c for c in convs if c["session_id"] == MOCK_SESSION_ID)
        self.assertTrue(card["has_edit"],
                        "has_edit should be True after an Edit tool_use")
        # No commit/push in the fixture — those signals must stay False.
        self.assertFalse(card["has_commit"])
        self.assertFalse(card["has_push"])

    def test_fixture_session_state_parsed(self):
        """The trailing <session-state> block in the last assistant turn
        must round-trip through _parse_session_state."""
        convs = self.server.find_conversations()
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
        convs = self.server.find_conversations()
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


if __name__ == "__main__":
    unittest.main()
