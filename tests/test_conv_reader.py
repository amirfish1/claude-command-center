"""Conversation-reader process wire protocol and fallback (CCC-143)."""

import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from ccc_server import conv_reader


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
