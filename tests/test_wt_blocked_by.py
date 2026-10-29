"""WatchTower ticket dependencies (WT-4 `blocked_by`) in CCC (CCC-1221).

WatchTower skips a ticket until every blocker is closed as completed. CCC
must agree: a blocked ticket is not claimable work (no fake staffing alarm),
the queue panel names the blocker instead of READY, and the header counts
blocked tickets apart from ready ones. Fixtures: open / closed / declined
blocker. CCC-1226: the rule itself is WatchTower's (``waiting_on``, WT-9);
CCC only attaches it to ticket lists and reads it, with no copy of its own.
"""

import json
import pathlib
import re
import shutil
import subprocess
import unittest
from unittest import mock

import server
from ccc_server.queue_events import compute_queues_health, with_waiting_on

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _t(ref, status="open", **kw):
    item = {"ref": ref, "project": "CCC", "status": status, "type": "bug",
            "readiness": "ready", "created_at": "2026-09-29T10:00:00Z",
            "updated_at": "2026-09-29T11:00:00Z"}
    item.update(kw)
    return item


ITEMS = [
    _t("CCC-1"),                                                   # open blocker
    _t("CCC-2", "closed"),                                         # completed
    _t("CCC-3", "closed", product_nack=True),                      # declined
    _t("CCC-4", "closed", resolution={"unresolved": ["todo"]}),    # unresolved
    _t("CCC-5", "in_review"),                                      # gate pending
    _t("CCC-10", blocked_by=["CCC-1"]),
    _t("CCC-11", blocked_by=["CCC-2"]),
    _t("CCC-12", blocked_by=["CCC-3"]),
    _t("CCC-13", blocked_by=["CCC-4"]),
    _t("CCC-14", blocked_by=["CCC-2", "CCC-1"]),
    _t("CCC-15", blocked_by=["CCC-5"]),
    _t("CCC-16", blocked_by=["CCC-404"]),                          # missing: ignored
]

EXPECTED = {
    "CCC-10": ["CCC-1"],
    "CCC-11": [],
    "CCC-12": ["CCC-3"],
    "CCC-13": ["CCC-4"],
    "CCC-14": ["CCC-1"],
    "CCC-15": ["CCC-5"],
    "CCC-16": [],
}


class WaitingOnTest(unittest.TestCase):
    def test_attaches_watchtower_waiting_on(self):
        got = {it["ref"]: it["waiting_on"] for it in with_waiting_on(ITEMS)
               if it.get("blocked_by")}
        self.assertEqual(got, EXPECTED)
        # Tickets without blocked_by are passed through untouched.
        self.assertNotIn("waiting_on", with_waiting_on(ITEMS)[0])

    def test_cross_queue_blocker_resolved_from_extra(self):
        it = _t("OTHER-1", blocked_by=["CCC-1"], project="OTHER")
        self.assertEqual(with_waiting_on([it])[0]["waiting_on"], [])
        self.assertEqual(with_waiting_on([it], extra=ITEMS)[0]["waiting_on"], ["CCC-1"])

    def test_escalated_stuck_blocker_answered_by_a_human_counts_as_satisfied(self):
        it = _t("CCC-20", blocked_by=["CCC-3"], blocker_escalated=["CCC-3"])
        self.assertEqual(with_waiting_on([it], extra=ITEMS)[0]["waiting_on"], [])

    def test_older_watchtower_without_waiting_on_adds_nothing(self):
        class OldWT:
            pass
        with mock.patch.object(server, "_q", OldWT()):
            out = with_waiting_on(ITEMS)
        self.assertIs(out, ITEMS)
        self.assertFalse(any("waiting_on" in it for it in out))

    def test_ccc_keeps_no_copy_of_the_rule(self):
        from ccc_server import wt_review
        self.assertFalse(hasattr(wt_review, "blocker_verdict"))
        src = (ROOT / "static" / "app.js").read_text()
        self.assertNotIn("_uxqBlockerVerdict", src)
        self.assertNotIn("b.product_nack", src)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_js_reads_waiting_on(self):
        src = (ROOT / "static" / "app.js").read_text()
        m = re.search(r"  function _uxqWaitingOn\(it\) \{.*?\n  \}\n", src, re.S)
        self.assertIsNotNone(m)
        items = with_waiting_on(ITEMS) + [_t("CCC-30", blocked_by=["CCC-1"])]  # no field: old WT
        script = (m.group(0)
                  + "const items = " + json.dumps(items) + ";\n"
                  + "const out = {};\n"
                  + "items.filter(i => i.blocked_by).forEach(i => { out[i.ref] = _uxqWaitingOn(i); });\n"
                  + "process.stdout.write(JSON.stringify(out));\n")
        res = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        want = {ref: (v[0] if v else "") for ref, v in EXPECTED.items()}
        want["CCC-30"] = ""
        self.assertEqual(json.loads(res.stdout), want)


class QueueHealthTest(unittest.TestCase):
    def test_blocked_tickets_are_not_claimable_and_counted(self):
        health = [{"project": "CCC", "depth": 8, "stuck": True}]
        with mock.patch.object(server, "_wt_read_config",
                               return_value={"CCC": {"auto_drain": True, "claim_types": []}}):
            row = [r for r in compute_queues_health(health=health, wt_workers=[], items=ITEMS)
                   if r["queue"] == "CCC"][0]
        # Claimable: CCC-1, CCC-11, CCC-16. Blocked: CCC-10, 12, 13, 14, 15.
        self.assertEqual(row["claimable"], 3)
        self.assertEqual(row["blocked"], 5)


class FrontendWiringTest(unittest.TestCase):
    def test_queue_panel_chip_and_count(self):
        src = (ROOT / "static" / "app.js").read_text()
        self.assertIn("'waiting on ' + escapeHtml(waitingOn)", src)
        self.assertIn("data-blocker-ref", src)
        self.assertIn("it.readiness && it.status !== 'closed'", src)
        self.assertIn("' blocked</span>'", src)


if __name__ == "__main__":
    unittest.main()
