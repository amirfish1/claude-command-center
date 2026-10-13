"""Changed snapshots go to the cloud as a delta when the service supports it."""

import copy
import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ccc_server import cloud_relay  # noqa: E402

OK = {"ok": True, "queued": 0, "unchanged_ok": True, "delta_ok": True}


class FakeClient:
    def __init__(self, responses=None):
        self.bodies = []
        self.responses = list(responses or [])

    def post_state(self, body):
        self.bodies.append(copy.deepcopy(body))
        return self.responses.pop(0) if self.responses else dict(OK)


def _rows(n, pad=0):
    return [{"ref": f"s{i}", "title": "t" * pad + str(i), "state": "idle",
             "recency": i} for i in range(n)]


def _snap(sessions=None, at="t", **extra):
    s = {"v": 1, "snapshot_at": at, "device": {"ccc_version": "1"},
         "sessions": sessions if sessions is not None else _rows(40, 30),
         "attention": [], "questions": [], "workers": []}
    s.update(extra)
    return s


def apply_delta(prev, d):
    """Reference reconstruction, mirroring the cloud's algorithm."""
    out = copy.deepcopy(prev)
    out["snapshot_at"] = d["snapshot_at"]
    if "device" in d:
        out["device"] = d["device"]
    if "sessions" in d:
        sd = d["sessions"]
        by = {r["ref"]: r for r in out["sessions"]}
        order = [r["ref"] for r in out["sessions"] if r["ref"] not in sd["remove"]]
        for r in sd["upsert"]:
            if r["ref"] not in by:
                order.append(r["ref"])
            by[r["ref"]] = r
        order = sd.get("order") or order
        out["sessions"] = [by[r] for r in order]
    for k, v in d.items():
        if k not in ("v", "snapshot_at", "delta", "base", "digest", "device",
                     "sessions"):
            out[k] = v
    return out


class BuildDeltaTests(unittest.TestCase):
    def roundtrip(self, prev, new):
        d = cloud_relay.build_delta(prev, new, "b", "d")
        self.assertIsNotNone(d)
        got = apply_delta(prev, d)
        for k in set(new) | set(got):
            if k in ("snapshot_at",):
                continue
            self.assertEqual(got.get(k) or [], new.get(k) or [], k) \
                if k != "device" else self.assertEqual(got[k], new[k])
        return d

    def test_upsert_remove_reorder(self):
        prev = _snap()
        rows = _rows(40, 30)
        rows[5]["state"] = "running"
        new_rows = [rows[5]] + rows[:5] + rows[7:] + [
            {"ref": "new", "title": "n", "state": "idle", "recency": 99}]
        d = self.roundtrip(prev, _snap(new_rows))
        self.assertEqual(d["sessions"]["remove"], ["s6"])
        self.assertIn("order", d["sessions"])
        self.assertEqual(len(d["sessions"]["upsert"]), 2)

    def test_no_order_when_implied(self):
        rows = _rows(10)
        new_rows = rows[:3] + rows[4:] + [{"ref": "z", "recency": 1}]
        new_rows[0] = dict(new_rows[0], state="busy")
        d = self.roundtrip(_snap(rows), _snap(new_rows))
        self.assertNotIn("order", d["sessions"])

    def test_unchanged_sections_omitted(self):
        prev = _snap(questions=[{"ref": "q"}])
        d = cloud_relay.build_delta(prev, _snap(at="t2", questions=[{"ref": "q"}]),
                                    "b", "d")
        for k in ("sessions", "questions", "attention", "workers", "device"):
            self.assertNotIn(k, d)
        self.assertTrue(d["delta"])

    def test_section_changes_sent_whole_and_emptied(self):
        prev = _snap(schedules=[{"label": "a"}], replies=[{"x": 1}])
        new = _snap(questions=[{"ref": "q"}], replies=[{"x": 2}])
        d = self.roundtrip(prev, new)
        self.assertEqual(d["questions"], [{"ref": "q"}])
        self.assertEqual(d["schedules"], [])
        self.assertEqual(d["replies"], [{"x": 2}])
        self.assertNotIn("attention", d)

    def test_refless_rows_not_deltable(self):
        self.assertIsNone(cloud_relay.build_delta(
            _snap([{"title": "a"}]), _snap([{"title": "b"}]), "b", "d"))


class PushTests(unittest.TestCase):
    def _loop(self, client):
        m = SimpleNamespace(local_api=None, config={}, ccc_version="1",
                            paths=None, client=client)
        return cloud_relay.RelayLoop(m)

    def _push(self, loop, snap):
        with mock.patch.object(cloud_relay, "build_snapshot", return_value=snap):
            return loop._push_once()

    def _changed(self):
        rows = _rows(40, 30)
        rows[3] = dict(rows[3], state="running", recency=500)
        return _snap(rows, at="t2")

    def test_delta_after_delta_ok(self):
        client = FakeClient()
        loop = self._loop(client)
        self._push(loop, _snap())
        self.assertNotIn("delta", client.bodies[0])
        self.assertEqual(client.bodies[0]["digest"],
                         cloud_relay.snapshot_digest(_snap()))
        self._push(loop, self._changed())
        d = client.bodies[1]
        self.assertTrue(d["delta"])
        self.assertEqual(d["base"], cloud_relay.snapshot_digest(_snap()))
        self.assertEqual(d["digest"], cloud_relay.snapshot_digest(self._changed()))
        self.assertLess(len(str(d)), len(str(client.bodies[0])) / 3)
        # chained: next delta bases on the delta's digest
        third = self._changed()
        third["sessions"][0] = dict(third["sessions"][0], state="busy")
        self._push(loop, third)
        self.assertEqual(client.bodies[2]["base"], d["digest"])

    def test_old_cloud_gets_full(self):
        client = FakeClient([{"ok": True, "unchanged_ok": True}] * 3)
        loop = self._loop(client)
        self._push(loop, _snap())
        self._push(loop, self._changed())
        self.assertNotIn("delta", client.bodies[1])
        self.assertIn("sessions", client.bodies[1])

    def test_need_full_resends_full(self):
        client = FakeClient([dict(OK), {"ok": True, "need_full": True}, dict(OK)])
        loop = self._loop(client)
        self._push(loop, _snap())
        self._push(loop, self._changed())
        self.assertEqual(len(client.bodies), 3)
        self.assertTrue(client.bodies[1]["delta"])
        self.assertNotIn("delta", client.bodies[2])
        self.assertEqual(len(client.bodies[2]["sessions"]), 40)
        # and the resent full is the new delta base
        third = self._changed()
        third["sessions"][1] = dict(third["sessions"][1], state="busy")
        self._push(loop, third)
        self.assertEqual(client.bodies[3]["base"],
                         cloud_relay.snapshot_digest(self._changed()))

    def test_full_refresh_interval(self):
        client = FakeClient()
        loop = self._loop(client)
        self._push(loop, _snap())
        loop._last_full_ts -= cloud_relay.STATE_FULL_REFRESH_S + 1
        self._push(loop, self._changed())
        self.assertNotIn("delta", client.bodies[1])

    def test_error_resets_to_full(self):
        client = FakeClient()
        loop = self._loop(client)
        self._push(loop, _snap())
        loop._last_digest = None  # what _push_loop does on RelayError
        loop._last_snapshot = None
        self._push(loop, self._changed())
        self.assertNotIn("delta", client.bodies[1])

    def test_unchanged_still_heartbeats(self):
        client = FakeClient()
        loop = self._loop(client)
        self._push(loop, _snap())
        self._push(loop, _snap(at="t9"))
        self.assertTrue(client.bodies[1].get("unchanged"))


if __name__ == "__main__":
    unittest.main()
