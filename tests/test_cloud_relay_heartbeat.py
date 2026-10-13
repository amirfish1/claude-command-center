"""Unchanged snapshots go to the cloud as a small heartbeat, not a full push."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ccc_server import cloud_relay  # noqa: E402


class FakeClient:
    def __init__(self, responses=None):
        self.bodies = []
        self.responses = list(responses or [])

    def post_state(self, body):
        self.bodies.append(body)
        if self.responses:
            return self.responses.pop(0)
        return {"ok": True, "queued": 0, "unchanged_ok": True}


def _snap(sessions=None, at="2026-10-13T00:00:00Z"):
    return {"v": 1, "snapshot_at": at, "device": {"ccc_version": "1"},
            "sessions": sessions or [], "attention": [], "questions": [],
            "workers": []}


class HeartbeatTests(unittest.TestCase):
    def _loop(self, client):
        m = SimpleNamespace(local_api=None, config={}, ccc_version="1",
                            paths=None, client=client)
        return cloud_relay.RelayLoop(m)

    def _push(self, loop, snap):
        with mock.patch.object(cloud_relay, "build_snapshot", return_value=snap):
            return loop._push_once()

    def test_digest_ignores_timestamp(self):
        self.assertEqual(cloud_relay.snapshot_digest(_snap(at="a")),
                         cloud_relay.snapshot_digest(_snap(at="b")))
        self.assertNotEqual(cloud_relay.snapshot_digest(_snap()),
                            cloud_relay.snapshot_digest(_snap([{"ref": "x"}])))

    def test_unchanged_sends_heartbeat(self):
        client = FakeClient()
        loop = self._loop(client)
        self._push(loop, _snap(at="t1"))
        self._push(loop, _snap(at="t2"))
        self.assertIn("sessions", client.bodies[0])
        self.assertEqual(client.bodies[1],
                         {"v": 1, "snapshot_at": "t2", "unchanged": True})

    def test_change_sends_full(self):
        client = FakeClient()
        loop = self._loop(client)
        self._push(loop, _snap())
        self._push(loop, _snap([{"ref": "x"}]))
        self.assertIn("sessions", client.bodies[1])

    def test_no_heartbeat_without_service_support(self):
        client = FakeClient([{"ok": True}, {"ok": True}])
        loop = self._loop(client)
        self._push(loop, _snap())
        self._push(loop, _snap())
        self.assertIn("sessions", client.bodies[1])

    def test_need_full_resends_snapshot(self):
        client = FakeClient([{"ok": True, "unchanged_ok": True},
                             {"ok": True, "need_full": True},
                             {"ok": True, "unchanged_ok": True}])
        loop = self._loop(client)
        self._push(loop, _snap())
        self._push(loop, _snap())
        self.assertEqual(len(client.bodies), 3)
        self.assertTrue(client.bodies[1].get("unchanged"))
        self.assertIn("sessions", client.bodies[2])

    def test_full_refresh_after_interval(self):
        client = FakeClient()
        loop = self._loop(client)
        self._push(loop, _snap())
        loop._last_full_ts -= cloud_relay.STATE_FULL_REFRESH_S + 1
        self._push(loop, _snap())
        self.assertIn("sessions", client.bodies[1])


if __name__ == "__main__":
    unittest.main()
