"""The unified multi-node list (/api/sessions?federated=1) must sort rows whose
`timestamp` mixes epoch floats and ISO strings; the raw sort raised TypeError
and the server dropped the connection with an empty reply."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: F401  (initializes ccc_server core state)
from ccc_server import fleet


class TestFederatedSessionsSort(unittest.TestCase):
    def test_row_epoch_normalizes_mixed_formats(self):
        epoch = fleet._federation_row_epoch
        self.assertEqual(epoch({"timestamp": 1790000000.5}), 1790000000.5)
        self.assertEqual(epoch({"timestamp": "1790000000"}), 1790000000.0)
        self.assertEqual(epoch({"timestamp": "2026-09-28T00:00:00Z"}),
                         epoch({"timestamp": "2026-09-28T00:00:00+00:00"}))
        self.assertEqual(epoch({"timestamp": "not a date"}), 0.0)
        self.assertEqual(epoch({"timestamp": None}), 0.0)
        self.assertEqual(epoch({}), 0.0)

    def test_federated_list_sorts_mixed_timestamps(self):
        local = {"observed_at": 1.0, "sessions": [
            {"session_id": "old-iso", "timestamp": "2020-01-01T00:00:00Z"},
            {"session_id": "new-float", "timestamp": 1790000000.0},
            {"session_id": "none", "timestamp": None},
        ]}
        peer = {"node_id": "peer-1", "name": "peer"}
        remote = {"observed_at": 2.0, "sessions": [
            {"session_id": "mid-iso", "timestamp": "2024-01-01T00:00:00+00:00"},
        ]}
        with mock.patch.object(fleet, "_federation_self_hello",
                               return_value={"node_id": "me", "display_name": "me"}), \
             mock.patch.object(fleet, "_federation_sessions_inventory", return_value=local), \
             mock.patch.object(fleet.federation, "load_peers", return_value=[peer]), \
             mock.patch.object(fleet, "_federation_peer_web_url",
                               return_value={"web_url": None, "web_url_state": "none"}), \
             mock.patch.object(fleet, "_federation_fetch_peer_sessions",
                               return_value=({**remote, "stale": False}, None)):
            out = fleet._federation_federated_sessions(limit=10)
        self.assertEqual([r["session_id"] for r in out["sessions"]],
                         ["new-float", "mid-iso", "old-iso", "none"])

    def test_peer_inventory_keeps_most_recent_rows_when_capped(self):
        # CCC-1216: a peer's archive cache had its recent rows appended at the
        # END, so rows[:limit] shipped only days-old sessions. Rows carry the
        # start time in `timestamp` and last activity in `mtime`.
        rows = [{"session_id": f"old-{i}", "mtime": 1000.0 + i,
                 "timestamp": "2020-01-01T00:00:00Z"} for i in range(5)]
        rows += [
            {"session_id": "recent", "mtime": 9000.0,
             "timestamp": "2020-01-01T00:00:00Z"},
            {"session_id": "newest", "mtime": 9500.0},
        ]
        with mock.patch.object(fleet._core, "_archive_inventory_rows_cached",
                               return_value=(rows, True)), \
             mock.patch.object(fleet, "_federation_lease_owners", return_value={}), \
             mock.patch.object(fleet.federation, "load_repo_map", return_value={}):
            out = fleet._federation_sessions_inventory(limit=3)
        self.assertEqual([r["session_id"] for r in out["sessions"]],
                         ["newest", "recent", "old-4"])
        # The row reports last activity, not the start time.
        self.assertEqual(out["sessions"][1]["timestamp"], 9000.0)


if __name__ == "__main__":
    unittest.main()
