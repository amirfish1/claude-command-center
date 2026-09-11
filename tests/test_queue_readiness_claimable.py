"""A queue of unshaped tickets is unstaffed on purpose, not stuck.

WatchTower's claim filter (`watchtower.queue._claim_candidates`) skips tickets
whose readiness is `needs-shaping` / `needs-spec`, and its reconciler counts
claimable depth through that exact filter — so it deliberately spawns no worker
for a queue whose only open tickets are unshaped.

CCC copied only the claim_types half of that filter. The result was a queue
reading `stuck` (auto-drain on, "claimable" work, zero workers) forever, over
tickets no worker is permitted to claim: an alarm that no restart, no spawn and
no amount of draining could ever clear. This pins the readiness half.
"""

import unittest
from unittest import mock

import server
from ccc_server.queue_events import compute_queues_health


def _ticket(ref, **kw):
    item = {
        "project": "FEAT-NEXT",
        "ref": ref,
        "status": "open",
        "type": "feature",
        "readiness": "ready",
        "created_at": "2026-06-28T17:52:51Z",
        "updated_at": "2026-08-09T22:58:34Z",
    }
    item.update(kw)
    return item


def _rows(items, *, depth):
    """One auto-drain queue, zero live workers — the shape that reads `stuck`."""
    health = [{"project": "FEAT-NEXT", "depth": depth, "stuck": True}]
    config = {"FEAT-NEXT": {"auto_drain": True, "claim_types": []}}
    with mock.patch.object(server, "_wt_read_config", return_value=config):
        return {
            row["queue"]: row
            for row in compute_queues_health(health=health, wt_workers=[], items=items)
        }


class ReadinessGatedClaimableTests(unittest.TestCase):
    def test_unshaped_tickets_do_not_make_an_unstaffed_queue_stuck(self):
        rows = _rows(
            [_ticket("FEAT-NEXT-13", readiness="needs-shaping"),
             _ticket("FEAT-NEXT-100", readiness="needs-spec")],
            depth=2,
        )
        row = rows["FEAT-NEXT"]

        self.assertEqual(row["claimable"], 0)
        self.assertFalse(row["stuck"])
        self.assertEqual(row["state"], "backlog")
        self.assertEqual(row["depth"], 2, "the work is still visible, just not an alarm")

    def test_a_shaped_ticket_with_no_worker_is_still_stuck(self):
        """The alarm must survive: this is the case it exists for."""
        rows = _rows([_ticket("FEAT-NEXT-14", readiness="ready")], depth=1)
        row = rows["FEAT-NEXT"]

        self.assertEqual(row["claimable"], 1)
        self.assertTrue(row["stuck"])
        self.assertEqual(row["state"], "stuck")

    def test_a_manual_run_request_overrides_the_readiness_gate(self):
        """Pressing ▶ on an unshaped ticket is WatchTower's explicit override."""
        rows = _rows(
            [_ticket("FEAT-NEXT-15", readiness="needs-shaping", run_requested=True)],
            depth=1,
        )

        self.assertEqual(rows["FEAT-NEXT"]["claimable"], 1)
        self.assertTrue(rows["FEAT-NEXT"]["stuck"])

    def test_ccc_agrees_with_watchtowers_own_unclaimable_readiness_set(self):
        from ccc_server.queue_events import _UNCLAIMABLE_READINESS

        try:
            from watchtower.queue import UNCLAIMABLE_READINESS
        except Exception:  # WatchTower lives in another repo; optional here
            self.skipTest("watchtower is not importable in this environment")
        self.assertEqual(set(_UNCLAIMABLE_READINESS), set(UNCLAIMABLE_READINESS))


if __name__ == "__main__":
    unittest.main()
