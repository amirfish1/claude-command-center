"""Queue / ticket-appearance events for the replay timeline (W22 / B4).

Exercises the derive-and-fold logic (`_queue_replay_events_uncached`) with a
synthetic, public queue (no private data): three tickets across two queues,
each with created/claimed/closed timestamps, and asserts the emitted events are
time-ordered with a correct per-queue open-depth fold.
"""
import importlib
import sys


def _load_server():
    sys.modules.pop("server", None)
    return importlib.import_module("server")


# Synthetic durable ticket items — same shape ux_fixes_queue / watchtower.queue
# persist (ref/queue/status/created_at/claimed_at/closed_at).
_FAKE_ITEMS = [
    {
        "ref": "DEMO-1", "queue": "DEMO", "project": "DEMO", "status": "closed",
        "note": "first ticket", "claimed_by": "demo-aaa",
        "created_at": "2026-07-16T10:00:00Z",
        "claimed_at": "2026-07-16T10:05:00Z",
        "closed_at": "2026-07-16T10:20:00Z",
    },
    {
        "ref": "DEMO-2", "queue": "DEMO", "project": "DEMO", "status": "in_progress",
        "note": "second ticket", "claimed_by": "demo-bbb",
        "created_at": "2026-07-16T10:10:00Z",
        "claimed_at": "2026-07-16T10:12:00Z",
        "closed_at": None,
    },
    {
        "ref": "OTHER-9", "queue": "OTHER", "project": "OTHER", "status": "open",
        "note": "other queue ticket", "claimed_by": "",
        "created_at": "2026-07-16T10:15:00Z",
        "claimed_at": None,
        "closed_at": None,
    },
]


def test_queue_replay_events_are_time_ordered_with_depth_fold(monkeypatch):
    server = _load_server()

    class _FakeQ:
        @staticmethod
        def list_items():
            return [dict(it) for it in _FAKE_ITEMS]

    monkeypatch.setattr(server, "_q", _FakeQ)

    out = server._queue_replay_events_uncached()
    events = out["events"]

    # DEMO: created(+1), created(+1), claimed(0), resolved(-1) -> 5 events with
    # OTHER's single created. Total = 3 created + 2 claimed + 1 resolved = 6.
    assert len(events) == 6
    assert out["truncated"] is False

    # Strictly non-decreasing by timestamp (the replay merge relies on order).
    ts = [e["ts"] for e in events]
    assert ts == sorted(ts)

    # Every event carries the derived fields the UI renders.
    for e in events:
        assert e["kind"] in ("created", "claimed", "resolved")
        assert e["ref"]
        assert e["queue"]
        assert isinstance(e["depth_after"], int)
        assert e["iso"].endswith("Z")

    # Depth fold is per-queue and tracks the ACTIVE (created-not-resolved) set:
    # DEMO created x2 -> depth 2, then DEMO-1 resolved -> depth 1.
    by = {(e["ref"], e["kind"]): e for e in events}
    assert by[("DEMO-1", "created")]["depth_after"] == 1
    assert by[("DEMO-2", "created")]["depth_after"] == 2
    assert by[("DEMO-2", "claimed")]["depth_after"] == 2   # claim doesn't drain
    assert by[("DEMO-1", "resolved")]["depth_after"] == 1
    # OTHER is an independent queue: its first (only) created is depth 1.
    assert by[("OTHER-9", "created")]["depth_after"] == 1
    assert by[("OTHER-9", "created")]["queue"] == "OTHER"


def test_queue_replay_events_tolerate_bad_input(monkeypatch):
    server = _load_server()

    class _BoomQ:
        @staticmethod
        def list_items():
            raise RuntimeError("store unavailable")

    monkeypatch.setattr(server, "_q", _BoomQ)
    out = server._queue_replay_events_uncached()
    assert out == {"events": [], "truncated": False}
