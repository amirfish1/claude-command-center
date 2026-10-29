"""Conversation-reader process wire protocol and fallback (CCC-143)."""

import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ccc_server import conv_reader, memory_api, session_fts, ship_graph


class _FakeServer:
    """Stands in for server.py inside the child."""

    def __init__(self):
        self.calls = []

    def _conversation_open_response(self, conv_id, query, accept, in_reader=False):
        self.calls.append((conv_id, query, accept, in_reader))
        if conv_id == "declined":
            return None
        if conv_id == "boom":
            raise RuntimeError("parse failed")
        return {"body": b'{"id": "%s"}' % conv_id.encode(),
                "encoding": "gzip" if "gzip" in accept else None,
                "cache": "hit"}


class ConvReaderProtocolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "r.sock"
        self.fake = _FakeServer()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(self.path))
        self.listener.listen(8)
        self._stop = False
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()
        self._saved = (dict(conv_reader._state), dict(conv_reader._stats))
        conv_reader._state.update(sock=str(self.path), proc=None, ready=True,
                                  errors=0)

    def tearDown(self):
        self._stop = True
        self.listener.close()
        conv_reader._state.clear()
        conv_reader._state.update(self._saved[0])
        conv_reader._stats.clear()
        conv_reader._stats.update(self._saved[1])
        self.tmp.cleanup()

    def _accept(self):
        while not self._stop:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            conv_reader._handle(conn, self.fake)

    def test_forward_returns_child_body(self):
        out = conv_reader.forward("abc", "tail=120", "gzip, br")
        self.assertEqual(out["body"], b'{"id": "abc"}')
        self.assertEqual(out["encoding"], "gzip")
        self.assertEqual(out["cache"], "reader-hit")
        self.assertEqual(self.fake.calls, [("abc", "tail=120", "gzip, br", True)])

    def test_decline_falls_back_without_counting_error(self):
        self.assertIsNone(conv_reader.forward("declined", "", ""))
        self.assertEqual(conv_reader._state["errors"], 0)

    def test_child_exception_is_a_decline(self):
        self.assertIsNone(conv_reader.forward("boom", "", ""))
        self.assertEqual(conv_reader._state["errors"], 0)

    def test_not_ready_skips_the_socket(self):
        conv_reader._state["ready"] = False
        self.assertIsNone(conv_reader.forward("abc", "", ""))
        self.assertEqual(self.fake.calls, [])

    def test_dead_socket_counts_errors(self):
        conv_reader._state["sock"] = str(Path(self.tmp.name) / "missing.sock")
        for _ in range(conv_reader.MAX_CONSECUTIVE_ERRORS):
            self.assertIsNone(conv_reader.forward("abc", "", ""))
        self.assertEqual(conv_reader._state["errors"],
                         conv_reader.MAX_CONSECUTIVE_ERRORS)


    # MEMORY-20: memory_recall runs in the child; the dashboard keeps the
    # index sync and the indexing flag, and falls back in-process.
    def _patch_dashboard_side(self, local):
        kicks = []
        patches = [
            mock.patch.object(memory_api, "recall_in_process", local),
            mock.patch.object(ship_graph, "_get_connection", lambda: "sg"),
            mock.patch.object(session_fts, "_get_connection", lambda: "sf"),
            mock.patch.object(ship_graph, "_sync_all", lambda c: kicks.append(c)),
            mock.patch.object(session_fts, "_sync_index", lambda c: kicks.append(c)),
            mock.patch.object(session_fts, "is_indexing", lambda: True),
            mock.patch.object(ship_graph, "is_indexing", lambda: False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return kicks

    def test_memory_recall_runs_in_child(self):
        local = mock.Mock(return_value={"query": "q", "results": [{"session_id": "s1"}],
                                        "indexing": False})
        kicks = self._patch_dashboard_side(local)
        out = memory_api.recall("pipe lease", limit=7)
        self.assertEqual(out["results"], [{"session_id": "s1"}])
        self.assertTrue(out["indexing"])  # the dashboard's flag, not the child's
        local.assert_called_once_with("pipe lease", limit=7)
        self.assertEqual(kicks, ["sg", "sf"])

    def test_memory_recall_falls_back_when_child_fails(self):
        local = mock.Mock(side_effect=[RuntimeError("child"), {"results": [], "indexing": False}])
        self._patch_dashboard_side(local)
        out = memory_api.recall("pipe", limit=3)
        self.assertEqual(out, {"results": [], "indexing": False})
        self.assertEqual(local.call_count, 2)

    def test_memory_recall_in_process_when_reader_down(self):
        conv_reader._state["ready"] = False
        local = mock.Mock(return_value={"results": []})
        kicks = self._patch_dashboard_side(local)
        memory_api.recall("pipe", limit=3)
        local.assert_called_once_with("pipe", 3)
        self.assertEqual(kicks, [])


class ReaderChildSyncTest(unittest.TestCase):
    """MEMORY-20: the reader child never walks the corpus itself."""

    def setUp(self):
        p = mock.patch.dict(os.environ, {"CCC_CONV_READER_CHILD": "1"})
        p.start()
        self.addCleanup(p.stop)

    def test_session_fts_sync_is_skipped_in_child(self):
        refresh = mock.Mock()
        with mock.patch.object(session_fts, "_last_sync_ts", 0.0), \
                mock.patch.object(session_fts, "_refresh_vec_cache_async", refresh), \
                mock.patch.object(session_fts, "_start_background_sync") as bg:
            session_fts._sync_index(None)
            self.assertGreater(session_fts._last_sync_ts, 0.0)
        refresh.assert_called_once_with()
        bg.assert_not_called()

    def test_ship_graph_sync_is_skipped_in_child(self):
        with mock.patch.object(ship_graph, "_last_sync_ts", 0.0), \
                mock.patch.object(ship_graph, "_start_background_sync") as bg:
            ship_graph._sync_all(None)
            self.assertGreater(ship_graph._last_sync_ts, 0.0)
        bg.assert_not_called()

class ConvReaderSwitchTest(unittest.TestCase):
    def test_kill_switch(self):
        saved = os.environ.get("CCC_CONV_READER")
        try:
            os.environ["CCC_CONV_READER"] = "0"
            self.assertFalse(conv_reader.enabled())
            self.assertIsNone(conv_reader.start())
        finally:
            if saved is None:
                os.environ.pop("CCC_CONV_READER", None)
            else:
                os.environ["CCC_CONV_READER"] = saved

    def test_child_never_spawns_a_reader(self):
        saved = os.environ.get("CCC_CONV_READER_CHILD")
        try:
            os.environ["CCC_CONV_READER_CHILD"] = "1"
            self.assertIsNone(conv_reader.start())
        finally:
            if saved is None:
                os.environ.pop("CCC_CONV_READER_CHILD", None)
            else:
                os.environ["CCC_CONV_READER_CHILD"] = saved


if __name__ == "__main__":
    unittest.main()
