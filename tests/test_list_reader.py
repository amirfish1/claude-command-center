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

    def _archive_list_warm(self, cache_options, window, stale_ok, **kwargs):
        self.calls.append(("warm", cache_options, window, stale_ok, kwargs))


class ListReaderOpsTest(unittest.TestCase):
    def setUp(self):
        self.saved = dict(list_reader._state)
        self.srv = _FakeSrv()
        list_reader._state.update(server=self.srv, loaded=True, acp=False, mutations=0,
                                  sidecars=None, stamps={}, reloading=False, checked=0.0,
                                  warms=0, warm_ms=None, errors=0, windows={})

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
        self.assertEqual([c for c in self.srv.calls if c[0] != "warm"], [])  # no restamp

    def test_reload_warms_page_load_body(self):
        # CCC-686: a full 1d read after each publication rebuilt the body on
        # the request path; the background reload now prebuilds it.
        list_reader._state["reloading"] = True
        with mock.patch.object(list_reader, "_reload"):
            list_reader._reload_in_background()
        self.assertEqual(self.srv.calls, [("warm", {}, "1d", True, {})])
        self.assertFalse(list_reader._state["reloading"])
        self.assertEqual(list_reader.status()["warms"], 1)

    def test_reload_warms_recently_read_windows(self):
        # CCC-690: a 7d sidebar rebuilt its full body on every page load
        # after a publication; windows read in the last day are prebuilt too.
        list_reader._state["windows"] = {}
        list_reader.note_window("7d", now=1000.0)
        list_reader.note_window("bogus", now=1000.0)
        list_reader.note_window("1d", now=1000.0)  # always warmed anyway
        self.assertEqual(list_reader.warm_windows(now=1001.0), ("1d", "7d"))
        self.assertEqual(list_reader.warm_windows(
            now=1000.0 + list_reader.RECENT_WINDOW_S + 1), ("1d",))
        list_reader._state["reloading"] = True
        with mock.patch.object(list_reader, "_reload"), \
                mock.patch.object(list_reader, "warm_windows", return_value=("1d", "7d")):
            list_reader._reload_in_background()
        self.assertEqual(self.srv.calls, [("warm", {}, "1d", True, {}), ("warm", {}, "7d", True, {})])

    def test_failed_reload_does_not_warm(self):
        list_reader._state["reloading"] = True
        with mock.patch.object(list_reader, "_reload", side_effect=RuntimeError("no snapshot")), \
                mock.patch("builtins.print"):
            list_reader._reload_in_background()
        self.assertEqual(self.srv.calls, [])
        self.assertFalse(list_reader._state["reloading"])

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
