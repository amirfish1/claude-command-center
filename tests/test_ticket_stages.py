"""WatchTower stage pipeline on queue tickets (CCC-1236).

A gated ticket shows only the stages it actually has (plan, plan review,
build, checks, verify, review, closed), which one it is in, who runs it, how
long it has been there, and loop counts read from WatchTower's own events.
No subprocess, no WatchTower config: gates and role tables are passed in.
"""

import importlib
import pathlib
import unittest
from unittest import mock

import server

ts = importlib.import_module("ccc_server.ticket_stages")
qe = importlib.import_module("ccc_server.queue_events")

ROOT = pathlib.Path(__file__).resolve().parent.parent

ROLES = {
    "planner": {"engine": "claude", "model": "claude-opus-5-5", "source": "default"},
    "plan_reviewer": {"engine": "codex", "model": "gpt-6.1-sol", "source": "default"},
    "builder": {"engine": "claude", "model": "claude-sonnet-5-5", "source": "queue"},
    "verifier": {"engine": "codex", "model": "gpt-6.1-sol", "source": "default"},
}
GATES = ["plan:claude-opus-5-5", "cmd:pytest -q", "verify"]


def _item(status, **extra):
    base = {"ref": "CCC-9", "project": "CCC", "status": status,
            "created_at": "2026-09-30T10:00:00Z", "history": [
                {"event": "filed", "at": "2026-09-30T10:00:00Z"}]}
    base.update(extra)
    return base


def _states(sp):
    return {s["key"]: s["state"] for s in sp["stages"]}


class PipelineTest(unittest.TestCase):
    def test_ungated_ticket_has_no_pipeline(self):
        self.assertIsNone(ts.stage_pipeline(_item("open"), roles=ROLES, gates=[]))

    def test_only_the_tickets_own_stages_show(self):
        sp = ts.stage_pipeline(_item("open"), roles=ROLES, gates=["verify"])
        self.assertEqual([s["key"] for s in sp["stages"]], ["build", "verify", "closed"])
        sp = ts.stage_pipeline(_item("open"), roles=ROLES, gates=GATES)
        self.assertEqual([s["key"] for s in sp["stages"]],
                         ["plan", "plan_review", "build", "checks", "verify", "closed"])

    def test_planning_is_current_with_the_planner_model(self):
        it = _item("in_progress", plan={"status": "planning", "round": 1,
                                        "planner": {"engine": "claude", "model": "claude-opus-5-5",
                                                    "worker_id": "w1"}},
                   history=[{"event": "plan_start", "at": "2026-09-30T10:05:00Z"}])
        sp = ts.stage_pipeline(it, roles=ROLES, gates=GATES)
        self.assertEqual(sp["current"], "plan")
        self.assertEqual(sp["since"], "2026-09-30T10:05:00Z")
        plan = sp["stages"][0]
        self.assertEqual((plan["state"], plan["model"]), ("current", "claude-opus-5-5"))
        self.assertEqual(_states(sp)["build"], "pending")

    def test_plan_rejection_is_a_loop(self):
        it = _item("in_progress", plan={"status": "reviewing", "round": 2},
                   history=[{"event": "plan_start", "at": "a"},
                            {"event": "plan", "at": "b", "round": 1},
                            {"event": "plan_review", "at": "c", "passed": False, "round": 1},
                            {"event": "plan", "at": "2026-09-30T10:20:00Z", "round": 2}])
        sp = ts.stage_pipeline(it, roles=ROLES, gates=GATES)
        self.assertEqual(sp["current"], "plan_review")
        self.assertEqual(sp["since"], "2026-09-30T10:20:00Z")
        self.assertEqual(_states(sp)["plan"], "done")
        self.assertEqual(sp["stages"][1]["model"], "gpt-6.1-sol")
        self.assertEqual(sp["loops"][0]["text"], "plan rejected 1×, revised")

    def test_verify_pending_after_checks(self):
        it = _item("in_review", gate_pending="verify", plan={"status": "accepted"},
                   verifier={"engine": "codex", "model": "gpt-6.1-sol", "worker_id": "v1"},
                   history=[{"event": "close", "at": "x"},
                            {"event": "in_review", "at": "2026-09-30T11:00:00Z", "reviewer": "verify"}])
        sp = ts.stage_pipeline(it, roles=ROLES, gates=GATES)
        self.assertEqual(sp["current"], "verify")
        st = _states(sp)
        self.assertEqual((st["build"], st["checks"], st["verify"], st["closed"]),
                         ("done", "done", "current", "pending"))

    def test_verify_failure_reopens_as_a_red_loop(self):
        it = _item("in_progress", plan={"status": "accepted"}, history=[
            {"event": "close", "at": "2026-09-30T11:00:00Z"},
            {"event": "in_review", "at": "2026-09-30T11:00:00Z"},
            {"event": "reopen", "at": "2026-09-30T11:10:00Z",
             "reason": "independent verification failed: button still clipped"},
            {"event": "claim", "at": "2026-09-30T11:10:00Z"}])
        sp = ts.stage_pipeline(it, roles=ROLES, gates=GATES)
        self.assertEqual(sp["current"], "build")
        self.assertEqual(_states(sp)["verify"], "failed")
        self.assertIn("verify failed → reopened 1×", [l["text"] for l in sp["loops"]])

    def test_check_failure_counts(self):
        it = _item("in_progress", history=[
            {"event": "reopen", "at": "t1", "reason": "gate cmd:pytest -q failed (exit 1): boom"},
            {"event": "reopen", "at": "t2", "reason": "gate cmd:pytest -q failed (exit 1): boom"}])
        sp = ts.stage_pipeline(it, roles=ROLES, gates=["cmd:pytest -q"])
        self.assertEqual(_states(sp)["checks"], "failed")
        self.assertEqual(sp["loops"][0]["count"], 2)

    def test_closed_is_all_done(self):
        it = _item("closed", closed_at="2026-09-30T12:00:00Z", gate_results=[{"gate": "verify"}])
        sp = ts.stage_pipeline(it, roles=ROLES, gates=["verify"])
        self.assertEqual(set(_states(sp).values()), {"done"})

    def test_closed_before_gates_existed_has_no_pipeline(self):
        self.assertIsNone(ts.stage_pipeline(_item("closed"), roles=ROLES, gates=["verify"]))

    def test_floor_routing_and_waiting_on(self):
        it = _item("open", model_floor="claude-opus-5-5", waiting_on=["WT-11"], needs_input=True)
        sp = ts.stage_pipeline(it, roles=ROLES, gates=["verify"])
        self.assertEqual(sp["floor_routed"], "claude-opus-5-5")
        self.assertEqual(sp["stages"][0]["model"], "claude-opus-5-5")
        kinds = [w["kind"] for w in sp["waiting"]]
        self.assertIn("blocked_by", kinds)
        self.assertIn("needs_input", kinds)

    def test_plan_failed_is_skipped_not_red(self):
        it = _item("in_progress", plan={"status": "failed", "reason": "planner model blocked"})
        sp = ts.stage_pipeline(it, roles=ROLES, gates=GATES)
        st = _states(sp)
        self.assertEqual((st["plan"], st["plan_review"], st["build"]), ("skipped", "skipped", "current"))


class QueuedTest(unittest.TestCase):
    def test_unclaimed_unstarted_ticket_is_queued_not_planning(self):
        sp = ts.stage_pipeline(_item("open"), roles=ROLES, gates=GATES)
        self.assertTrue(sp["queued"])
        self.assertEqual(_states(sp)["plan"], "pending")
        self.assertEqual(ts.stage_counts([dict(_item("open"), stages=sp)]), {})

    def test_claimed_build_is_not_queued(self):
        sp = ts.stage_pipeline(_item("in_progress", claimed_by="w"), roles=ROLES, gates=["verify"])
        self.assertFalse(sp["queued"])
        self.assertEqual(_states(sp)["build"], "current")


class QueueCountsTest(unittest.TestCase):
    def test_health_row_has_per_stage_counts(self):
        items = [
            _item("in_progress", ref="CCC-1", gates=GATES, plan={"status": "planning"}),
            _item("in_progress", ref="CCC-2", gates=GATES, plan={"status": "reviewing"}),
            _item("in_progress", ref="CCC-3", gates=GATES, plan={"status": "accepted"}),
            _item("in_review", ref="CCC-4", gates=GATES, plan={"status": "accepted"},
                  gate_pending="verify"),
            _item("open", ref="CCC-5"),
        ]
        with mock.patch.object(ts, "role_table", return_value=ROLES), \
                mock.patch.object(ts, "_queue_gates", return_value=[]), \
                mock.patch.object(server, "_wt_read_config", return_value={"CCC": {}}):
            rows = qe.compute_queues_health(
                health=[{"project": "CCC", "depth": 1}], wt_workers=[], items=items)
        row = [r for r in rows if r["queue"] == "CCC"][0]
        self.assertEqual(row["stage_counts"],
                         {"plan": 1, "plan_review": 1, "build": 1, "verify": 1})
        self.assertGreater(row["stage_stuck_s"], 0)


class UiWiringTest(unittest.TestCase):
    def test_q2_renders_stage_strip_and_counts(self):
        js = (ROOT / "static" / "q2.js").read_text()
        self.assertIn("function stageStripHtml(", js)
        self.assertIn("function stagePanelHtml(", js)
        self.assertIn("queueStageHeaderHtml(state.queue)", js)
        self.assertIn("'plan_review'", js)


if __name__ == "__main__":
    unittest.main()
