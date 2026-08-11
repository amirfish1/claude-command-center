"""Uncertain work must be able to LEAVE `uncertain`.

`reconcile_uncertain` only ever reclaimed items with live execution evidence.
Everything else stayed `uncertain` forever, and every worker restart added a
fresh batch, so the count only ever went up: 48 items spanning 16 days pinned
the System status chip to "attention" permanently. A number that can only grow
is not a signal.

These tests pin the retirement pass: old orphans go terminal, young ones are
left alone (a restart is still adopting live transports), and reclaimable work
is never retired out from under a reconcile.
"""

import pathlib
import tempfile
import time
import unittest
from unittest import mock

from control_plane import WorkLedger
from worker_engines import RETIRE_UNCERTAIN_AFTER_S, EngineHost


class FakeRuntime:
    def __init__(self, ledger):
        self.ledger = ledger
        self.epoch = "test-epoch"

    def _engines(self):  # pragma: no cover - parity with WorkerRuntime
        return None


class RetireStaleUncertainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = WorkLedger(pathlib.Path(self.tmp.name) / "control-plane.sqlite3")
        self.host = EngineHost(FakeRuntime(self.ledger))

    def _uncertain(self, key, *, age_s):
        item, _created = self.ledger.submit(
            engine="claude",
            idempotency_key=key,
            session_id="session-" + key,
            kind="inject",
            payload={"operation": "inject", "args": {"session_id": "s", "text": "hi"}},
        )
        self.ledger.transition(item["id"], "dispatching", owner_epoch="old-epoch")
        self.ledger.transition(
            item["id"], "uncertain",
            error="worker restarted before completion was confirmed",
        )
        with self.ledger._session() as conn:
            conn.execute(
                "UPDATE work_items SET updated_at=? WHERE id=?",
                (time.time() - age_s, item["id"]),
            )
        return item["id"]

    def test_old_orphans_are_retired_so_the_count_can_reach_zero(self):
        work_id = self._uncertain("stale", age_s=RETIRE_UNCERTAIN_AFTER_S + 60)

        retired = self.host.retire_stale_uncertain()

        self.assertEqual([row["id"] for row in retired], [work_id])
        self.assertEqual(self.ledger.get(work_id)["state"], "cancelled")
        self.assertEqual(self.ledger.summary().get("uncertain"), 0)

    def test_retirement_records_why_instead_of_claiming_failure(self):
        """The outcome is unknown, not failed: an inject may well have landed."""
        work_id = self._uncertain("stale", age_s=RETIRE_UNCERTAIN_AFTER_S + 60)

        self.host.retire_stale_uncertain()

        item = self.ledger.get(work_id)
        self.assertEqual(item["state"], "cancelled")
        self.assertIn("retired by reconciler", item["last_error"])

    def test_recent_orphans_survive_a_restart_still_adopting_transports(self):
        work_id = self._uncertain("fresh", age_s=30)

        self.assertEqual(self.host.retire_stale_uncertain(), [])
        self.assertEqual(self.ledger.get(work_id)["state"], "uncertain")

    def test_reconcile_runs_first_so_live_work_is_never_retired(self):
        """Reclaimed work has already left `uncertain` when retirement reads."""
        work_id = self._uncertain("live", age_s=RETIRE_UNCERTAIN_AFTER_S + 60)
        legacy = mock.Mock()
        legacy._pid_alive.return_value = True
        with mock.patch.object(self.host, "_legacy", return_value=legacy), \
             mock.patch.object(self.host, "_track_async"):
            with self.ledger._session() as conn:
                conn.execute(
                    "UPDATE work_items SET result_json=? WHERE id=?",
                    ('{"pid": 4242}', work_id),
                )
            self.host.reconcile_uncertain()

        self.assertEqual(self.ledger.get(work_id)["state"], "running")
        self.assertEqual(self.host.retire_stale_uncertain(), [])
        self.assertEqual(self.ledger.get(work_id)["state"], "running")


if __name__ == "__main__":
    unittest.main()
