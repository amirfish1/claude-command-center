"""Delivery ownership and legacy cutover at CCC's WatchTower boundary."""

import importlib
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import idea_tickets as tickets, takeover_bridge as bridge


class TakeoverBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.injects = []
        core = types.SimpleNamespace(
            COMMAND_CENTER_STATE_DIR=Path(self.tmp.name),
            _feature_flag=lambda _name: True,
            _restamp_archive_serve_cache_after_mutation=lambda: None,
            _inject_text_into_session=lambda *a, **kw: self.injects.append(a),
            _log_activity=lambda *a: None,
        )
        self.patches = [mock.patch.object(tickets, "_core", core),
                        mock.patch.object(tickets, "_wt", self.wt),
                        mock.patch.object(bridge, "_available", True)]
        for patch in self.patches:
            patch.start()
        tickets._state_cache.update(key=None, data=None)
        tickets._save_state({
            "sessions": {"session-a": {"ref": "DEMO-1"}},
            "tickets": {"DEMO-1": {"sessions": ["session-a"], "queue": "DEMO",
                                     "closed": False}}, "pending": {}})
        self.response = {"schema_version": 1, "ok": True, "supervision_id": "sup-a",
                         "generation": 1, "ticket": "DEMO-1",
                         "session": {"session_id": "session-a"},
                         "state": "pending_delivery", "delivery_state": "pending"}

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        tickets._state_cache.update(key=None, data=None)
        self.tmp.cleanup()

    def wt(self, args, **_kwargs):
        self.calls.append(args)
        if args[0] == "answer":
            return 0, "accepted"
        return 0, json.dumps(self.response)

    def handoff(self):
        return tickets.load_state()["tickets"]["DEMO-1"]["handoff"]

    def test_handoff_records_owner_before_call_and_never_injects(self):
        original = self.wt

        def check(args, **kw):
            self.assertEqual(self.handoff()["owner"], "watchtower")
            return original(args, **kw)

        with mock.patch.object(tickets, "_wt", check):
            result = tickets.handoff("session-a")
        self.assertTrue(result["ok"])
        self.assertEqual(self.calls, [["takeover", "session-a", "--ticket", "DEMO-1", "--json"]])
        self.assertEqual(self.handoff()["delivery_state"], "pending")
        self.assertEqual(self.injects, [])

    def test_failed_adoption_stays_fenced_and_retries_idempotently(self):
        with mock.patch.object(tickets, "_wt", return_value=(-1, "connection lost")):
            self.assertFalse(tickets.handoff("session-a")["ok"])
        self.assertEqual(self.handoff()["owner"], "watchtower")
        self.assertTrue(self.handoff()["adoption_pending"])
        bridge.sync_once()
        self.assertEqual(self.handoff()["supervision_id"], "sup-a")
        self.assertFalse(self.handoff()["adoption_pending"])
        self.assertEqual(self.injects, [])

    def test_answer_has_one_delivery_owner(self):
        tickets.handoff("session-a")
        self.calls.clear()
        self.assertTrue(tickets.answer("session-a", "Proceed")["ok"])
        self.assertEqual(self.calls, [["answer", "DEMO-1", "Proceed"]])
        self.assertEqual(self.injects, [])

    def test_unsupported_engine_rejected_before_ticket_creation(self):
        before = tickets.load_state()
        result = tickets.handoff("devincli-example")
        self.assertEqual(result["code"], "unsupported_engine")
        self.assertIn("Claude and Codex", result["error"])
        self.assertEqual(tickets.load_state(), before)
        self.assertEqual(self.calls, [])

    def test_failed_unsupported_adoption_stops_retrying(self):
        tickets._set_handoff("DEMO-1", owner="watchtower", session="devincli-example",
                             state="waiting", adoption_pending=True)
        bridge.sync_once()
        self.assertEqual(self.handoff()["state"], "unsupported")
        self.assertFalse(self.handoff()["adoption_pending"])
        bridge.sync_once()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.injects, [])

    def test_legacy_import_preserves_budget_and_is_fenced_before_call(self):
        tickets._set_handoff("DEMO-1", state="blocked", session="session-a",
                             at=100, nudges=2, nudged_at=200, question="Approve?", auto=True)
        bridge.sync_once()
        args = self.calls[0]
        self.assertIn("--legacy-json", args)
        legacy = json.loads(args[args.index("--legacy-json") + 1])
        self.assertTrue(legacy["owner_fenced"])
        self.assertEqual((legacy["enrolled_at"], legacy["reminders"], legacy["state"]),
                         (100, 2, "needs_input"))
        self.assertEqual(legacy["next_deadline"], 200 + tickets.HANDOFF_STALL_S)
        self.assertTrue(legacy["migration_id"])
        self.assertEqual(legacy["reason"], "Approve?")
        self.assertEqual(self.handoff()["owner"], "watchtower")
        self.assertEqual(self.injects, [])

    def test_automatic_enrollment_is_mirrored_without_existing_handoff(self):
        bridge.sync_once({"session-a": self.response})
        self.assertEqual(self.handoff()["supervision_id"], "sup-a")
        self.assertTrue(self.handoff()["auto"])
        self.assertEqual(self.calls, [])

    def test_legacy_takeback_imports_cancellation_suppression(self):
        tickets._set_handoff("DEMO-1", state="taken_back", session="session-a", at=100)
        self.response.update(state="cancelled")
        bridge.sync_once()
        args = self.calls[0]
        legacy = json.loads(args[args.index("--legacy-json") + 1])
        self.assertEqual(legacy["state"], "cancelled")
        self.assertEqual(self.handoff()["state"], "taken_back")

    def test_takeback_uses_watchtower_cancel(self):
        tickets.handoff("session-a")
        self.response.update(state="cancelled")
        self.assertTrue(tickets.takeback("session-a")["ok"])
        self.assertEqual(self.calls[-1], ["takeover", "cancel", "session-a", "--json"])
        self.assertEqual(self.handoff()["state"], "taken_back")

    def test_watchtower_mode_list_projection_has_no_effects(self):
        self.assertEqual(tickets.autopilot_rows([], now=1000), 0)
        self.assertEqual(self.calls, [])

    def test_publisher_reuses_native_identity_and_only_linked_work(self):
        tickets._mutate(lambda d: d["tickets"]["DEMO-1"].update(title="Fix save"))
        native = {"schema_version": 1, "session_id": "session-a", "engine": "codex",
                  "repo_path": "/tmp/demo"}
        self.response = native
        bridge.publish_candidates()
        self.assertEqual(self.calls[0], ["takeover", "observe", "session-a", "--json"])
        publish = self.calls[1]
        payload = json.loads(publish[publish.index("--candidates-json") + 1])
        candidate = payload["scopes"][0]["candidates"][0]
        self.assertEqual((candidate["session_id"], candidate["ticket"]), ("session-a", "DEMO-1"))
        self.assertTrue(payload["delegation_fenced"])
        self.calls.clear()
        bridge.publish_candidates()
        self.assertEqual(self.calls, [])  # unchanged publication has no CLI work

    def test_disabling_feature_withdraws_automatic_candidates(self):
        bridge.publish_candidates(enabled=False)
        args = self.calls[-1]
        payload = json.loads(args[args.index("--candidates-json") + 1])
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["scopes"], [])


if __name__ == "__main__":
    unittest.main()
