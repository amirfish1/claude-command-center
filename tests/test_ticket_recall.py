"""WatchTower tickets in `ccc recall` (ticket_fts)."""
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import memory_fanout, ticket_fts

CLOSER = "11111111-1111-4111-8111-111111111111"
FILER = "22222222-2222-4222-8222-222222222222"


def _item(number, ref, note, *, summary="", comment="", updated=1.0):
    history = [
        {"event": "filed", "by": {"kind": "system"}},
        {"event": "claim", "by": {"kind": "worker", "machine": "her", "session_id": CLOSER}},
    ]
    if comment:
        history.append({"event": "comment", "text": comment,
                        "by": {"kind": "worker", "session_id": FILER}})
    item = {"number": number, "ref": ref, "project": ref.split("-")[0], "status": "open",
            "title": "", "note": note, "text": note, "created_at": "2026-11-20T00:00:00Z",
            "history": history, "claimed_session_id": CLOSER, "claimed_machine": "her"}
    if summary:
        item.update(status="closed", closed_at="2026-11-21T00:00:00Z",
                    resolution={"summary": summary, "session_id": CLOSER, "machine": "her"})
        history.append({"event": "close", "by": {"kind": "worker", "session_id": CLOSER}})
    return item, updated


class TicketRecallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.wt = self.tmp / "queues.db"
        src = sqlite3.connect(self.wt)
        src.execute("CREATE TABLE items (number INTEGER PRIMARY KEY, item_json TEXT, updated_ts REAL)")
        src.commit()
        src.close()
        env = {"WATCHTOWER_DB": str(self.wt), "CCC_TICKET_FTS_DB": str(self.tmp / "t.sqlite")}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        ticket_fts._last_sync[0] = 0.0

    def tearDown(self):
        self._env.stop()

    def _put(self, item, updated):
        src = sqlite3.connect(self.wt)
        src.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?)",
                    (item["number"], json.dumps(item), updated))
        src.commit()
        src.close()

    def _sync(self):
        ticket_fts._last_sync[0] = 0.0
        return ticket_fts.sync()

    def test_finds_ticket_text_and_names_the_closing_session(self):
        self._put(*_item(1, "CCC-1", "/home/x/pasted-images/a.png  Customer cannot cancel booking",
                         summary="Cancel button now refunds the class pass", comment="Repro on iOS"))
        self._put(*_item(2, "OPS-2", "Unrelated disk alert"))
        self._sync()
        hits = ticket_fts.search("refunds class pass")
        self.assertEqual([h["ref"] for h in hits], ["CCC-1"])
        self.assertEqual(hits[0]["sessions"][0], {"session_id": CLOSER, "machine": "her", "role": "closed"})
        self.assertIn(FILER, [s["session_id"] for s in hits[0]["sessions"]])
        self.assertEqual(hits[0]["title"], "Customer cannot cancel booking")  # image path dropped
        self.assertEqual([h["ref"] for h in ticket_fts.search("iOS repro")], ["CCC-1"])

    def test_unchanged_store_parses_nothing_and_edits_parse_only_that_ticket(self):
        for n in range(1, 30):
            self._put(*_item(n, f"OPS-{n}", f"ticket {n}"))
        self._sync()
        with mock.patch.object(ticket_fts, "ticket_doc", wraps=ticket_fts.ticket_doc) as doc:
            self._sync()
            self.assertEqual(doc.call_count, 0)
            self._put(*_item(7, "OPS-7", "ticket seven about kayaks", updated=2.0))
            self._sync()
            self.assertEqual(doc.call_count, 1)
        self.assertEqual([h["ref"] for h in ticket_fts.search("kayaks")], ["OPS-7"])

    def test_deleted_ticket_leaves_the_index(self):
        self._put(*_item(1, "CCC-1", "zebra migration"))
        self._sync()
        src = sqlite3.connect(self.wt)
        src.execute("DELETE FROM items WHERE number=1")
        src.commit()
        src.close()
        self._sync()
        self.assertEqual(ticket_fts.search("zebra"), [])

    def test_search_never_builds_a_cold_index_in_the_request(self):
        self._put(*_item(1, "CCC-1", "walrus"))
        with mock.patch.object(ticket_fts.threading, "Thread") as thread:
            self.assertEqual(ticket_fts.search("walrus"), [])
            thread.assert_called_once()
        self._sync()
        self.assertEqual([h["ref"] for h in ticket_fts.search("walrus")], ["CCC-1"])

    def test_merge_tags_each_machine_and_keeps_same_ref_from_both(self):
        local = {"tickets": [{"ref": "OPS-5", "title": "here"}]}
        entries = [{"status": memory_fanout.OK, "name": "mac", "node_id": "m",
                    "result": {"tickets": [{"ref": "OPS-5", "title": "there"}]}},
                   {"status": "timeout", "name": "down", "node_id": "d", "result": None}]
        merged = memory_fanout.merge_tickets(local, {"name": "hermes"}, entries)
        self.assertEqual([(t["ref"], t["node_name"], t["local"]) for t in merged],
                         [("OPS-5", "hermes", True), ("OPS-5", "mac", False)])


if __name__ == "__main__":
    unittest.main()
