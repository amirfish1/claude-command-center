"""List reader inside the conv-reader child (CCC-679)."""

import os
import threading
import unittest
from unittest import mock

from ccc_server import conv_reader, list_reader


class _FakeSrv:
    def __init__(self):
        self.calls = []
        self._ACP_SESSION_STATE = {"claude": {"old": {"attached": True}}}
        self.ARCHIVED_CONVERSATIONS_FILE = "/nonexistent/a"
        self.TRASHED_CONVERSATIONS_FILE = "/nonexistent/t"
        self.SESSION_LANE_OVERRIDES_FILE = "/nonexistent/l"

    def _clear_archive_serve_cache(self):
        self.calls.append(("clear",))

    def _restamp_archive_serve_cache_after_mutation(self, archived_set=None, trashed_set=None,
                                                    mutated_sids=None):
        self.calls.append(("restamp", archived_set, trashed_set, mutated_sids))


class ListReaderOpsTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(list_reader._state)
        self.srv = _FakeSrv()
        list_reader._state.update(server=self.srv, loaded=True, acp=False, mutations=0,
                                  sidecars=None, stamps={}, reloading=False, checked=0.0)

    def tearDown(self):
        list_reader._state.clear()
        list_reader._state.update(self.saved)

    def test_not_ready_until_acp_push(self):
        self.assertFalse(list_reader.ready())
        reply = list_reader.handle_op({"op": "list_acp", "state": {"claude": {"s1": {"attached": True}}}})
        self.assertEqual(reply, {"ok": True})
        self.assertTrue(list_reader.ready())
        self.assertEqual(self.srv._ACP_SESSION_STATE, {"claude": {"s1": {"attached": True}}})

    def test_mutation_forwards_sets(self):
        list_reader.handle_op({"op": "list_mutation", "kind": "restamp",
                               "archived": ["a"], "trashed": None, "sids": ["a", "b"]})
        list_reader.handle_op({"op": "list_mutation", "kind": "clear"})
        self.assertEqual(self.srv.calls, [("restamp", {"a"}, None, {"a", "b"}), ("clear",)])
        self.assertEqual(list_reader.status()["mutations"], 2)

    def test_unknown_op_and_bad_acp(self):
        self.assertEqual(list_reader.handle_op({"op": "list_nope"}), {"ok": False})
        self.assertEqual(list_reader.handle_op({"op": "list_acp", "state": []}), {"ok": False})

    def test_sync_reloads_on_newer_publication(self):
        list_reader._state["stamps"] = {"k": 10.0}
        started = threading.Event()
        with mock.patch.object(list_reader, "_store_stamps", return_value={"k": 11.0}), \
                mock.patch.object(list_reader, "_reload", side_effect=lambda *a, **k: started.set()):
            list_reader.sync(now=100.0)
            self.assertTrue(started.wait(5))
        self.assertEqual(self.srv.calls, [])

    def test_sync_restamps_on_missed_sidecar_change(self):
        list_reader._state["stamps"] = {"k": 10.0}
        list_reader._state["sidecars"] = ("stale",)
        with mock.patch.object(list_reader, "_store_stamps", return_value={"k": 10.0}):
            list_reader.sync(now=100.0)
            list_reader.sync(now=100.5)  # throttled
        self.assertEqual(self.srv.calls, [("restamp", None, None, None)])

    def test_acp_snapshot_keeps_attached_scalars(self):
        snap = list_reader.acp_snapshot({"claude": {
            "a": {"attached": True, "title": "x", "proc": object()},
            "b": {"attached": False}}})
        self.assertEqual(snap, {"claude": {"a": {"attached": True, "title": "x"}}})
        self.assertEqual(list_reader.acp_signature(snap), list_reader.acp_signature(dict(snap)))


class DashboardForwardTest(unittest.TestCase):
    def test_mutation_forward_skipped_when_child_not_ready(self):
        saved = dict(conv_reader._state)
        try:
            conv_reader._state.update(ready=False)
            with mock.patch.object(conv_reader, "call") as call:
                conv_reader.list_mutation("clear")
            call.assert_not_called()
        finally:
            conv_reader._state.clear()
            conv_reader._state.update(saved)

    def test_kill_switch(self):
        with mock.patch.dict(os.environ, {"CCC_LIST_READER": "0"}):
            self.assertFalse(list_reader.enabled())
        with mock.patch.dict(os.environ, {"CCC_LIST_READER": ""}):
            self.assertTrue(list_reader.enabled())


if __name__ == "__main__":
    unittest.main()
