"""WatchTower `in_review` tickets (WT-5 acceptance gates) in CCC (CCC-1220).

A gated ticket lands in `in_review` on `wt close` and waits there for its
pending stage. It must count as not-closed and not-open everywhere, show its
stage in the queue views, and a review-stage ticket must surface as a
Decision Inbox card whose Accept / Reject run `wt accept` / `wt reject`.
No subprocess: the wt runner is injected.
"""

import importlib
import pathlib
import unittest
from unittest import mock

import server

qe = importlib.import_module("ccc_server.queue_events")
di = importlib.import_module("ccc_server.decision_inbox")
wr = importlib.import_module("ccc_server.wt_review")

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _item(ref, status, **extra):
    base = {"ref": ref, "project": "CCC", "status": status, "title": f"title {ref}",
            "created_at": "2026-09-29T10:00:00Z", "updated_at": "2026-09-29T11:00:00Z"}
    base.update(extra)
    return base


FIXTURE = [
    _item("CCC-1", "open"),
    _item("CCC-2", "in_progress"),
    _item("CCC-3", "in_review", gate_pending="review", accept="tests pass",
          resolution={"summary": "Fixed the thing.", "commit": "abc123"}),
    _item("CCC-4", "in_review", gate_pending="verify"),
    _item("CCC-5", "in_review", gate_pending="review:orchestrator"),
    _item("CCC-6", "closed"),
]


class CountsTest(unittest.TestCase):
    def test_in_review_is_its_own_not_closed_bucket(self):
        counts = qe._current_queue_counts(FIXTURE)
        self.assertEqual(counts["open"], 1)
        self.assertEqual(counts["in_progress"], 1)
        self.assertEqual(counts["in_review"], 3)
        self.assertEqual(counts["closed"], 1)

    def test_queue_health_rows_count_in_review(self):
        with mock.patch.object(server, "_wt_read_config", return_value={"CCC": {}}):
            rows = qe.compute_queues_health(
                health=[{"project": "CCC", "depth": 1}], wt_workers=[], items=FIXTURE)
        row = [r for r in rows if r["queue"] == "CCC"][0]
        self.assertEqual(row["in_review"], 3)
        self.assertEqual(row["in_progress"], 1)
        self.assertEqual(row["closed"], 1)


class ReviewCardsTest(unittest.TestCase):
    def test_stage_labels_match_watchtower(self):
        self.assertEqual(wr.stage_label("verify"), "independent verifier")
        self.assertEqual(wr.stage_label("review"), "submitter")
        self.assertEqual(wr.stage_label("review:orchestrator"), "orchestrator")

    def test_only_person_review_stages_become_cards(self):
        cards = wr.review_cards(FIXTURE)
        refs = sorted(c["source"]["ref"] for c in cards)
        self.assertEqual(refs, ["CCC-3", "CCC-5"])  # verify-stage waits on a verifier
        card = [c for c in cards if c["source"]["ref"] == "CCC-3"][0]
        self.assertEqual(card["id"], "wtr:CCC-3")
        self.assertEqual(card["status"], "open")
        self.assertIn("Fixed the thing.", card["context"])
        self.assertIn("Accept when: tests pass", card["context"])
        self.assertEqual([o["action"]["kind"] for o in card["options"]], ["wt_accept", "wt_reject"])

    def test_payload_leads_with_live_review_cards(self):
        payload = di.decision_inbox_api_payload(
            cards={}, cfg=dict(di.DEFAULT_CONFIG), review_cards=wr.review_cards(FIXTURE))
        self.assertEqual(payload["open_count"], 2)
        self.assertTrue(all(c["kind"] == "wt_review" for c in payload["cards"]))


class AcceptRejectTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def _runner(self, args):
        self.calls.append(args)
        return {"ok": True, "effect": args[0]}

    def test_accept_runs_wt_accept(self):
        res = di.decision_inbox_decide("wtr:CCC-3", 0, review_runner=self._runner)
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls, [["accept", "CCC-3", "--by", "dashboard", "--json"]])

    def test_reject_needs_a_reason(self):
        res = di.decision_inbox_decide("wtr:CCC-3", 1, review_runner=self._runner)
        self.assertFalse(res["ok"])
        self.assertEqual(self.calls, [])

    def test_reject_runs_wt_reject_with_reason(self):
        res = di.decision_inbox_decide("wtr:CCC-3", 1, reason="tests fail", review_runner=self._runner)
        self.assertTrue(res["ok"])
        self.assertEqual(self.calls, [["reject", "CCC-3", "--by", "dashboard", "--json",
                                       "--reason", "tests fail"]])

    def test_unknown_option_is_refused(self):
        res = di.decision_inbox_decide("wtr:CCC-3", 7, review_runner=self._runner)
        self.assertFalse(res["ok"])
        self.assertEqual(self.calls, [])


class VerbOutputTest(unittest.TestCase):
    def test_refusal_with_exit_zero_is_a_failure(self):
        res = wr.parse_verb_output(
            "accept", 0, "", "error: CCC-6 is closed, not in_review -- nothing to accept")
        self.assertFalse(res["ok"])
        self.assertIn("not in_review", res["error"])

    def test_json_item_is_success(self):
        res = wr.parse_verb_output("reject", 0, '{"ref": "CCC-3", "status": "in_progress"}\nresumed', "")
        self.assertTrue(res["ok"])
        self.assertEqual(res["item"]["status"], "in_progress")

    def test_nonzero_exit_is_a_failure(self):
        res = wr.parse_verb_output("accept", 1, "", "error: boom")
        self.assertEqual(res, {"ok": False, "error": "error: boom"})


class FrontendWiringTest(unittest.TestCase):
    """The views that enumerate statuses must know in_review."""

    def test_q2_board(self):
        src = (ROOT / "static" / "q2.js").read_text()
        self.assertIn("in_review: 'awaits review'", src)
        self.assertIn("st === 'blocked' || st === 'in_review'", src)
        self.assertIn("'/api/ux-fixes/accept'", src)
        self.assertIn("'/api/ux-fixes/reject'", src)

    def test_queue_panel(self):
        src = (ROOT / "static" / "app.js").read_text()
        self.assertIn("function _uxqReviewLabel(it)", src)
        self.assertIn("status === 'blocked' || status === 'in_review'", src)
        self.assertIn("'/api/ux-fixes/accept'", src)

    def test_decision_inbox_page(self):
        src = (ROOT / "static" / "decision-inbox.html").read_text()
        self.assertIn("wt_review: 1", src)
        self.assertIn('act.kind === "wt_reject"', src)


if __name__ == "__main__":
    unittest.main()
