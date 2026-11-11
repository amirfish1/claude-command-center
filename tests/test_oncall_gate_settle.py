"""Give an eligible minor block time to settle before alerting the owner."""

from datetime import datetime, timezone

import pytest

from ccc_server.oncall import alerts
from ccc_server.oncall.store import Store


NOW = datetime(2026, 11, 11, 12, tzinfo=timezone.utc).timestamp()


class Queue:
    def __init__(self, item):
        self.item = item
        self.calls = []

    def blocked(self, queue):
        self.calls.append(queue)
        return [self.item] if self.item.get("needs_input") else []


@pytest.fixture
def rig(tmp_path):
    store = Store(tmp_path)
    cfg = {"queue": "TEST", "minor_decisions": {"enabled": True}}
    item = {"ref": "TEST-1", "needs_input": True, "status": "awaiting_answer",
            "block_question": "Keep the fallback for one release?",
            "blocked_at": "2026-11-11T12:00:00Z",
            "block_proposal": {"class": "code-change-revertable", "weight": "minor"}}
    queue = Queue(item)
    yield cfg, store, queue
    store.close()


def collect(rig, now=NOW):
    cfg, store, queue = rig
    return alerts.collect_decisions(cfg, store, queue, now=now)


def test_transient_then_autoapproved_never_creates_alert(rig):
    _, store, queue = rig
    assert collect(rig) == 0
    assert collect(rig, NOW + 4.999) == 0
    assert not store.alerts()
    queue.item.update(needs_input=False, status="in_progress",
                      answered_at="2026-11-11T12:00:01Z")
    assert collect(rig, NOW + 5) == 0
    assert not store.alerts()
    assert queue.calls == ["TEST"] * 3


def test_still_blocked_at_grace_creates_exactly_one_alert(rig):
    _, store, _ = rig
    assert collect(rig, NOW + 4.999) == 0
    assert collect(rig, NOW + 5) == 1
    assert collect(rig, NOW + 6) == 0
    assert len(store.alerts(state="open")) == 1


@pytest.mark.parametrize("reason", ["needs you: required preference is unknown", ""])
def test_explicit_decline_alerts_immediately(rig, reason):
    _, store, queue = rig
    queue.item["block_question"] += f"\n[auto-approval declined: {reason}]"
    assert collect(rig) == 1
    assert len(store.alerts(state="open")) == 1


@pytest.mark.parametrize("change", ["disabled", "major", "owner", "missing", "malformed"])
def test_other_owner_questions_alert_immediately(rig, change):
    cfg, store, queue = rig
    if change == "disabled":
        cfg["minor_decisions"]["enabled"] = False
    elif change == "major":
        queue.item["block_proposal"]["weight"] = "major"
    elif change == "owner":
        queue.item["block_proposal"]["class"] = "owner-decision"
    elif change == "missing":
        del queue.item["block_proposal"]
    else:
        queue.item["block_proposal"] = "minor"
    assert collect(rig) == 1
    assert len(store.alerts(state="open")) == 1


@pytest.mark.parametrize("stamp", ["", "not-a-time", "2026-02-30T12:00:00Z",
                                   "2026-11-11T12:00:01Z", "2099-01-01T00:00:00Z"])
def test_bad_and_future_times_are_not_hidden(rig, stamp):
    _, store, queue = rig
    queue.item["blocked_at"] = stamp
    assert collect(rig) == 1
    assert len(store.alerts(state="open")) == 1


@pytest.mark.parametrize("stamp", ["2026-11-11T12:00:00.500000Z",
                                   "2026-11-11T14:00:00.500000+02:00",
                                   "2026-11-11T12:00:00.500000"])
def test_iso_variants_defer_only_within_grace(rig, stamp):
    _, store, queue = rig
    queue.item["blocked_at"] = stamp
    assert collect(rig, NOW + 1) == 0
    assert not store.alerts()
    assert collect(rig, NOW + 5.5) == 1
