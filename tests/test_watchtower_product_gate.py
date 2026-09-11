"""Product gate support in CCC (2026-09-01 design): gated badge, Ack/Nack actions."""

import importlib
import json
import unittest
from types import ModuleType
from unittest import mock

import server
from ccc_server.queue_events import compute_queues_health


class TestWatchtowerProductGate(unittest.TestCase):
    def test_gate_ack_notifies_claimed_worker(self):
        item = {
            "ref": "CCC-800",
            "status": "in_progress",
            "needs_input": False,
            "block_kind": "rationale",
            "claimed_session_id": "22222222-3333-4444-5555-666666666666",
            "product_ack": {"by": "CCC", "comment": "approved"},
        }
        gate_ack = mock.Mock(return_value=item)
        messages = mock.Mock()
        messages.deliver_message.return_value = {"ok": True, "transport": "fifo"}
        package = ModuleType("watchtower")
        package.messages = messages

        with mock.patch.object(server._q, "gate_ack", gate_ack, create=True), \
             mock.patch.dict("sys.modules", {
                 "watchtower": package,
                 "watchtower.messages": messages,
             }):
            returned, delivery = server._gate_ack_queue_item_and_notify_worker(
                "CCC-800", "approved"
            )

        self.assertIs(returned, item)
        self.assertEqual(delivery, {"ok": True, "transport": "fifo"})
        gate_ack.assert_called_once_with("CCC-800", comment="approved", by="CCC")
        messages.deliver_message.assert_called_once()
        self.assertIn("APPROVED", messages.deliver_message.call_args[0][1])

    def test_gate_nack_iceboxes(self):
        item = {
            "ref": "CCC-800",
            "status": "open",
            "readiness": "needs-rationale",
            "needs_input": False,
            "product_nack": {"by": "CCC", "comment": "not this quarter"},
        }
        gate_nack = mock.Mock(return_value=item)

        with mock.patch.object(server._q, "gate_nack", gate_nack, create=True):
            returned = server._gate_nack_queue_item("CCC-800", "not this quarter", close=False)

        self.assertIs(returned, item)
        gate_nack.assert_called_once_with("CCC-800", reason="not this quarter", by="CCC", close=False)

    def test_queues_health_tallies_gated_tickets(self):
        items = [
            {
                "project": "FEAT-GATE",
                "ref": "FEAT-GATE-1",
                "status": "in_progress",
                "needs_input": True,
                "block_kind": "rationale",
            },
            {
                "project": "FEAT-GATE",
                "ref": "FEAT-GATE-2",
                "status": "in_progress",
                "needs_input": True,
                "block_kind": "input",
            },
            {
                "project": "FEAT-GATE",
                "ref": "FEAT-GATE-3",
                "status": "open",
                "needs_input": False,
            },
        ]
        health = [{"project": "FEAT-GATE", "depth": 3, "stuck": False}]
        config = {"FEAT-GATE": {"auto_drain": True, "claim_types": []}}

        with mock.patch.object(server, "_wt_read_config", return_value=config):
            rows = compute_queues_health(health=health, wt_workers=[], items=items)

        feat_row = next(r for r in rows if r["queue"] == "FEAT-GATE")
        self.assertEqual(feat_row.get("gated"), 1)


    def test_queue_config_payload_round_trips_product_gate(self):
        on = server._queue_config_from_payload({"queue": "GATEQ", "product_gate": True})
        self.assertIs(on["config"]["product_gate"], True)
        off = server._queue_config_from_payload({"queue": "GATEQ"})
        self.assertIs(off["config"]["product_gate"], False)
        self.assertIs(server._QUEUE_CONFIG_DEFAULTS["product_gate"], False)


if __name__ == "__main__":
    unittest.main()
