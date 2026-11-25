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
                                  warms=0, warm_ms=None, errors=0, windows={},
                                  warming=False, warm_again=False, busy=[])

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
        with mock.patch.object(list_reader, "_warm_after_mutation") as warm:
            list_reader.handle_op({"op": "list_mutation", "kind": "restamp",
                                   "archived": ["a"], "trashed": None, "sids": ["a", "b"]})
            list_reader.handle_op({"op": "list_mutation", "kind": "clear"})
        self.assertEqual(self.srv.calls, [("restamp", {"a"}, None, {"a", "b"}), ("clear",)])
        self.assertEqual(list_reader.status()["mutations"], 2)
        self.assertEqual(warm.call_count, 2)

    def test_mutation_rewarms_discarded_bodies(self):
        # CCC-691: a restamp drops the prebuilt bodies; without a rewarm the
        # next page load rebuilt the full body on the request path.
        list_reader._state["windows"] = {}
        list_reader.handle_op({"op": "list_mutation", "kind": "restamp", "sids": ["a"]})
        for thread in threading.enumerate():
            if thread.name == "list-reader-warm":
                thread.join(5)
        self.assertEqual(self.srv.calls, [("restamp", None, None, {"a"}),
                                          ("warm", {}, "1d", True, {})])
        self.assertFalse(list_reader._state["warming"])

    def test_mutation_during_reload_leaves_warm_to_reload(self):
        list_reader._state["reloading"] = True
        with mock.patch.object(list_reader.threading, "Thread") as thread:
            list_reader._warm_after_mutation()
        thread.assert_not_called()

    def test_mutations_during_warm_coalesce_into_one_more_pass(self):
        list_reader._state["windows"] = {}
        self.assertTrue(list_reader._claim_warm())
        with mock.patch.object(list_reader.threading, "Thread") as thread:
            list_reader._warm_after_mutation()
            list_reader._warm_after_mutation()
        thread.assert_not_called()
        list_reader._warm_until_settled()
        self.assertEqual([c[0] for c in self.srv.calls], ["warm", "warm"])
        self.assertFalse(list_reader._state["warming"])
        self.assertFalse(list_reader._state["warm_again"])

    def test_busy_overlap_attributes_reload_and_warm(self):
        list_reader._state["busy"] = [["reload", 100.0, 101.0], ["warm", 101.0, None],
                                      ["warm", 50.0, 51.0]]
        # A 1.5 s request from 100.0 to 101.5: all of the reload, half the warm.
        self.assertEqual(list_reader.busy_overlap(1500, now=101.5),
                         {"busy_reload_ms": 1000.0, "busy_warm_ms": 500.0})
        # Later, only the still-running warm counts; finished spans do not.
        self.assertEqual(list_reader.busy_overlap(100, now=200.0), {"busy_warm_ms": 100.0})
        list_reader._state["busy"][1][2] = 102.0
        self.assertEqual(list_reader.busy_overlap(100, now=200.0), {})

    def test_reload_records_busy_span(self):
        list_reader._state["reloading"] = True
        with mock.patch.object(list_reader, "_reload"):
            list_reader._reload_in_background()
        kinds = [span[0] for span in list_reader._state["busy"]]
        self.assertEqual(kinds, ["reload", "warm"])
        self.assertTrue(all(span[2] is not None for span in list_reader._state["busy"]))

    def test_rebuilding_has_no_gap_between_reload_and_warm(self):
        # CCC-691: stale bodies are served only while rebuilding() is true;
        # the warm must be claimed before "reloading" clears.
        seen = []
        list_reader._state["reloading"] = True
        with mock.patch.object(list_reader, "_reload"), \
                mock.patch.object(list_reader, "_warm_bodies",
                                  side_effect=lambda: seen.append(list_reader.rebuilding())):
            list_reader._reload_in_background()
        self.assertEqual(seen, [True])
        self.assertFalse(list_reader.rebuilding())

    def test_stale_ok_overlay_only_for_prebuilt_bodies_and_starts_a_warm(self):
        list_reader._state["windows"] = {}
        with mock.patch.object(list_reader.threading, "Thread") as thread:
            self.assertTrue(list_reader.stale_ok("overlay", ("k", "1d", True)))
            self.assertFalse(list_reader.stale_ok("overlay", ("k", "7d", True)))
            list_reader.note_window("7d", layouts=True)
            self.assertFalse(list_reader.stale_ok("overlay", ("k", "7d", True)))
            # Second call while the first warm is claimed coalesces.
            self.assertTrue(list_reader.stale_ok("overlay", ("k:layouts", "7d", True)))
        self.assertEqual(thread.call_count, 1)
        self.assertTrue(list_reader._state["warm_again"])
        # The claimed warm counts as rebuilding.
        self.assertTrue(list_reader.stale_ok("rebuilding", ("k", "1d", True)))

    def test_layouts_7d_read_warms_the_layouts_body(self):
        # CCC-691: the browser's 7d read asks for row layouts, a separate body
        # from the plain 7d one CCC-690 warmed.
        list_reader._state["windows"] = {}
        list_reader.note_window("7d", now=1000.0, layouts=True)
        self.assertEqual(list_reader.warm_windows(now=1001.0), ("1d", list_reader.LAYOUTS))
        list_reader.note_window("7d", layouts=True)
        list_reader._warm_bodies()
        self.assertEqual(self.srv.calls, [("warm", {}, "1d", True, {}),
                                          ("warm", {}, "7d", True, {"scoped_layouts": True})])

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
            with mock.patch.object(list_reader, "_warm_after_mutation") as warm:
                list_reader.sync(now=100.0)
                list_reader.sync(now=100.5)  # throttled
        self.assertEqual(self.srv.calls, [("restamp", None, None, None)])
        warm.assert_called_once_with()

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
