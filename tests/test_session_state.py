"""Unit tests for the coarse session-state classifier (`_session_state_label`).

This is the single source of truth for working / idle / waiting / ended that
`/api/session/<id>.state`, the `/api/sessions` `?state=` filter, the per-row
`state` field, and the frontend kanban all bind to. The #1 reliability bug it
fixes: a session whose last tool finished but whose Stop hook never fired keeps
`sidecar_status=="active"` on disk; combined with the 30-min liveness window it
used to read "working" for up to half an hour while nothing was running. The
fix bounds the sticky `active` flag by `_WORKING_GAP_WINDOW` (120 s) freshness.

stdlib-only (`unittest`) — matches `tests/test_session_classifier.py`.
"""
import importlib
import os
import sys
import time
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)


def _fresh_server():
    for mod in ("server", "morning", "morning_store"):
        sys.modules.pop(mod, None)
    return importlib.import_module("server")


def _row(**overrides):
    """A live session row with no in-flight work — the 'idle' baseline.

    Defaults: live, last tool long finished (`sidecar_status` waiting, ts old),
    nothing pending, no human-input markers. Each test overrides what it needs.
    """
    row = {
        "session_id": "00000000-0000-4000-8000-0000000000aa",
        "is_live": True,
        "pending_tool": None,
        "sidecar_in_flight": False,
        "subagent_in_flight_count": 0,
        "sidecar_status": "waiting",
        "sidecar_ts": 0,
        "last_event_type": "assistant",
        "last_assistant_text": "ok, done.",
        "question_waiting": False,
        "needs_approval": False,
    }
    row.update(overrides)
    return row


class TestSessionStateLabel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def label(self, **over):
        return self.server._session_state_label(_row(**over))

    # --- working: real, currently-executing signals (regardless of freshness) -
    def test_sidecar_in_flight_is_working(self):
        self.assertEqual(self.label(sidecar_in_flight=True), "working")

    def test_pending_tool_is_working(self):
        self.assertEqual(self.label(pending_tool="Bash"), "working")

    def test_subagent_in_flight_is_working(self):
        """Genuine subagents in flight: fresh, non-waiting sidecar."""
        self.assertEqual(
            self.label(subagent_in_flight_count=2, sidecar_status="active",
                       sidecar_ts=time.time() - 5),
            "working",
        )

    # --- THE REOPENED BUG: sticky subagent count must not pin working --------
    def test_stale_subagent_count_with_clean_stop_is_idle(self):
        """Mirrors live session 33a6df22: subagents finished, parent Stopped
        cleanly (sidecar_status=='waiting'), but subagent_in_flight_count never
        cleared. A clean Stop contradicts in-flight subagents -> idle."""
        self.assertEqual(
            self.label(subagent_in_flight_count=5, sidecar_in_flight=False,
                       sidecar_status="waiting", sidecar_ts=time.time() - 879),
            "idle",
        )

    def test_stale_subagent_count_past_window_is_idle(self):
        """Subagent count > 0 but the sidecar is stale past the window
        (count wedged after the turn ended) -> idle, not working."""
        self.assertEqual(
            self.label(subagent_in_flight_count=3, sidecar_status="active",
                       sidecar_ts=time.time() - 27 * 60),
            "idle",
        )

    def test_fresh_active_sidecar_is_working(self):
        """Between-tools think gap: status=='active' and touched just now."""
        self.assertEqual(
            self.label(sidecar_status="active", sidecar_ts=time.time() - 5),
            "working",
        )

    # --- THE BUG: stale active must read idle, not working -------------------
    def test_stale_active_sidecar_is_idle(self):
        """27-min-old 'active' (Stop hook never fired) is NOT working."""
        self.assertEqual(
            self.label(sidecar_status="active", sidecar_ts=time.time() - 27 * 60),
            "idle",
        )

    def test_active_sidecar_just_past_window_is_idle(self):
        self.assertEqual(
            self.label(sidecar_status="active", sidecar_ts=time.time() - 121),
            "idle",
        )

    # --- idle: live but nothing in flight -----------------------------------
    def test_live_waiting_status_is_idle(self):
        self.assertEqual(self.label(sidecar_status="waiting"), "idle")

    # --- waiting: blocked on a human ----------------------------------------
    def test_question_waiting_is_waiting(self):
        self.assertEqual(self.label(question_waiting=True), "waiting")

    def test_needs_approval_is_waiting(self):
        self.assertEqual(self.label(needs_approval=True), "waiting")

    def test_waiting_wins_over_stale_active(self):
        self.assertEqual(
            self.label(needs_approval=True, sidecar_status="active",
                       sidecar_ts=time.time() - 27 * 60),
            "waiting",
        )

    # --- ended: not live ----------------------------------------------------
    def test_not_live_is_ended(self):
        self.assertEqual(self.label(is_live=False), "ended")

    def test_not_live_with_stale_active_is_ended(self):
        """A dead session's leftover 'active' marker must not read working."""
        self.assertEqual(
            self.label(is_live=False, sidecar_status="active",
                       sidecar_ts=time.time() - 5),
            "ended",
        )

    def test_not_live_with_question_is_ended_not_waiting(self):
        """Mirrors the 183 dead sessions whose last assistant message ended in
        a question: you cannot reply to unblock a dead process (you'd resume
        it — a different action), so a not-live session is ended regardless of
        its last message. waiting is only meaningful for LIVE sessions."""
        self.assertEqual(self.label(is_live=False, question_waiting=True), "ended")
        self.assertEqual(self.label(is_live=False, needs_approval=True), "ended")

    def test_not_live_with_soft_block_text_is_ended(self):
        self.assertEqual(
            self.label(is_live=False,
                       last_assistant_text="Want me to proceed with the fix?"),
            "ended",
        )

    # --- prose soft-block recency bound (CCC follow-up) ---------------------
    _PROSE_Q = ("Which option do you want — A or B? Let me know how you'd "
                "like me to proceed.")

    def test_fresh_prose_soft_block_is_waiting(self):
        """A live session whose RECENT last message reads like a question is a
        heuristic soft block → waiting."""
        self.assertEqual(
            self.label(last_assistant_text=self._PROSE_Q, modified=time.time() - 600),
            "waiting",
        )

    def test_stale_prose_soft_block_demotes_to_idle(self):
        """The prose heuristic has no 'human answered' signal, so a live-but-idle
        session parked at an OLD question must leave NEEDS YOU once the turn is
        older than _SOFT_BLOCK_PROSE_MAX_AGE — the 'never taking sessions out'
        fix. (Formal flags are exempt; see below.)"""
        self.assertEqual(
            self.label(last_assistant_text=self._PROSE_Q, modified=time.time() - 3 * 3600),
            "idle",
        )

    def test_stale_formal_question_stays_waiting(self):
        """Formal AskUserQuestion / permission prompts are guaranteed hits — the
        prose recency bound never demotes them."""
        old = time.time() - 3 * 3600
        self.assertEqual(self.label(question_waiting=True, modified=old), "waiting")
        self.assertEqual(self.label(needs_approval=True, modified=old), "waiting")

    def test_prose_max_age_constant_is_2h(self):
        self.assertEqual(self.server._SOFT_BLOCK_PROSE_MAX_AGE, 2 * 3600)

    def test_window_constant_is_120s(self):
        self.assertEqual(self.server._WORKING_GAP_WINDOW, 120)


class TestEndedBlocked(unittest.TestCase):
    """`ended_blocked` distinguishes a process that died WHILE blocked on a
    human (real open question/approval, now orphaned) from a clean finish —
    both read state=="ended", but only the former had a pending ask to the
    user. No 5th state value; recency is the consumer's job (mtime is on row).
    """

    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def blocked(self, **over):
        return self.server._session_ended_blocked(_row(**over))

    def test_not_live_question_is_ended_blocked(self):
        self.assertTrue(self.blocked(is_live=False, question_waiting=True))

    def test_not_live_needs_approval_is_ended_blocked(self):
        self.assertTrue(self.blocked(is_live=False, needs_approval=True))

    def test_not_live_soft_block_text_is_ended_blocked(self):
        self.assertTrue(self.blocked(
            is_live=False, last_assistant_text="Want me to proceed with the fix?"))

    def test_not_live_clean_stop_is_not_ended_blocked(self):
        self.assertFalse(self.blocked(
            is_live=False, last_assistant_text="All done, shipped it."))

    def test_live_session_is_never_ended_blocked(self):
        # A live blocked session is "waiting", not ended — ended_blocked False.
        self.assertFalse(self.blocked(is_live=True, question_waiting=True))

    def test_ended_blocked_pairs_with_state_ended(self):
        row = _row(is_live=False, question_waiting=True)
        self.assertEqual(self.server._session_state_label(row), "ended")
        self.assertTrue(self.server._session_ended_blocked(row))

    def test_list_path_stamps_ended_blocked(self):
        blocked = _row(is_live=False, question_waiting=True)
        clean = _row(is_live=False, last_assistant_text="All done.")
        out = self.server._apply_session_query_params([blocked, clean], {})
        self.assertEqual(out[0]["state"], "ended")
        self.assertTrue(out[0]["ended_blocked"])
        self.assertEqual(out[1]["state"], "ended")
        self.assertFalse(out[1]["ended_blocked"])


class TestStaleActiveReadsIdleEndToEnd(unittest.TestCase):
    """The reproduced #1 bug: a live session whose last tool finished 27 min
    ago (sidecar still says 'active' because the Stop hook never fired) must
    read 'idle' — both in the /api/sessions list and at /api/session/<id>."""

    @classmethod
    def setUpClass(cls):
        cls.server = _fresh_server()

    def test_list_path_stamps_idle_and_filters_correctly(self):
        """_apply_session_query_params stamps state and ?state= filters on it."""
        row = _row(
            sidecar_status="active",
            sidecar_ts=time.time() - 27 * 60,  # Stop never fired
            sidecar_in_flight=False,
        )
        out = self.server._apply_session_query_params([row], {})
        self.assertEqual(out[0]["state"], "idle")
        # ?state=working must NOT return the stale-active session...
        self.assertEqual(
            self.server._apply_session_query_params(
                [dict(row)], {"state": ["working"]}), [])
        # ...and ?state=idle must.
        kept = self.server._apply_session_query_params(
            [dict(row)], {"state": ["idle"]})
        self.assertEqual(len(kept), 1)

    def test_detail_endpoint_reads_idle(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest import mock

        sid = "11111111-2222-4333-8444-555555555555"
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            # A fresh-mtime sidecar (so is_live is True via the 30-min window)
            # but whose CONTENT says the last tool finished 27 min ago and was
            # never superseded by a Stop -> status sticks at "active".
            sc = tdp / f"{sid}.json"
            sc.write_text(json.dumps({
                "session_id": sid,
                "status": "active",
                "tool": "Bash",
                "file": "echo hi",
                "has_writes": False,
                "timestamp": time.time() - 27 * 60,
            }))
            jsonl = tdp / f"{sid}.jsonl"
            jsonl.write_text(
                json.dumps({
                    "type": "assistant",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "done."}]},
                }) + "\n"
            )
            with mock.patch.object(self.server, "SIDECAR_STATE_DIR", tdp), \
                 mock.patch.object(self.server, "_find_session_jsonl",
                                   return_value=str(jsonl)):
                body, code = self.server.compute_session_detail(sid)
        self.assertEqual(code, 200)
        self.assertTrue(body["is_live"])          # live (fresh sidecar mtime)
        self.assertFalse(body["sidecar_in_flight"])  # nothing running
        self.assertEqual(body["state"], "idle")   # ...so: idle, not "working"


class TestWaitingStickyHysteresis(unittest.TestCase):
    """CCC-174: a live session that flicks off "waiting" for a single poll must
    stay pinned to "waiting" for the demote window, so NEEDS YOU does not bounce.
    `_session_state_label` stays pure; the stickiness is in `_stamp_session_state`.
    """

    def setUp(self):
        self.server = _fresh_server()
        self.server._waiting_sticky_seen.clear()
        self.sid = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"

    def _stamp(self, **over):
        return self.server._stamp_session_state(_row(session_id=self.sid, **over))

    def test_waiting_is_immediate(self):
        # Entering waiting must NOT be debounced — a real ask shows at once.
        self.assertEqual(self._stamp(question_waiting=True), "waiting")

    def test_transient_demotion_is_held(self):
        self.assertEqual(self._stamp(question_waiting=True), "waiting")
        # Next poll: the marker flapped off, but a tool is now in flight -> raw
        # would be "working". Within the window it stays "waiting".
        self.assertEqual(
            self._stamp(question_waiting=False, sidecar_in_flight=True), "waiting")

    def test_demotion_releases_after_window(self):
        self.assertEqual(self._stamp(question_waiting=True), "waiting")
        # Force the recorded timestamp past the window.
        self.server._waiting_sticky_seen[self.sid] = (
            time.monotonic() - self.server._WAITING_STICKY_WINDOW - 1)
        self.assertEqual(
            self._stamp(question_waiting=False, sidecar_in_flight=True), "working")

    def test_ended_drops_pin_immediately(self):
        # A dead process is "ended" even if it was waiting last poll — you resume
        # it, not reply to it; the pin must not survive death.
        self.assertEqual(self._stamp(question_waiting=True), "waiting")
        self.assertEqual(self._stamp(is_live=False, question_waiting=True), "ended")
        self.assertNotIn(self.sid, self.server._waiting_sticky_seen)

    def test_no_session_id_is_passthrough(self):
        row = _row(question_waiting=True)
        row.pop("session_id", None)
        self.assertEqual(self.server._stamp_session_state(row), "waiting")


if __name__ == "__main__":
    unittest.main()
