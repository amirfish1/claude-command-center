"""Unit tests for ccc_server.notify — the L14 notification pipeline.

The module is exercised without importing server.py: publishing, identity and
cost lookups are all injected or monkeypatched, and state files are pointed at
pytest tmp dirs by the fixture below.
"""

import json
import time
from pathlib import Path

import pytest

from ccc_server import notify, popups


@pytest.fixture(autouse=True)
def _isolated_notify_state(tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "STATE_FILE", tmp_path / "notify-state.json")
    monkeypatch.setattr(notify, "LOG_FILE", tmp_path / "notify-log.json")
    # These tests cover delivery mechanics, so every notify pop-up is approved.
    monkeypatch.setattr(popups, "APPROVED", frozenset(popups.ALL))
    notify.reset_for_tests()
    notify._TOTALS_MEMO["ts"] = 0.0
    notify._TOTALS_MEMO["value"] = None
    yield
    notify.reset_for_tests()


def _captured():
    items = []
    return items, items.append


# ── validate / post ─────────────────────────────────────────────────────


def test_validate_happy_path():
    item, err = notify.validate({
        "title": "Build finished",
        "body": "Done in 4m.",
        "kind": "task",
        "url": "/?session=abc",
        "session_id": "abc",
    })
    assert err is None
    assert item["title"] == "Build finished"
    assert item["kind"] == "task"
    assert item["id"].startswith("ntf_")
    assert item["ts"] > 0


def test_validate_requires_title():
    item, err = notify.validate({"body": "hi"})
    assert item is None and "title" in err


def test_validate_rejects_unknown_kind_and_bad_url():
    item, err = notify.validate({"title": "x", "kind": "loud"})
    assert item is None and "kind" in err
    item, err = notify.validate({"title": "x", "url": "javascript:alert(1)"})
    assert item is None and "url" in err


def test_post_publishes_and_logs():
    sent, publish = _captured()
    resp, status = notify.post({"title": "Hello", "kind": "info"}, publish=publish)
    assert status == 200 and resp["ok"]
    assert len(sent) == 1 and sent[0]["title"] == "Hello"
    log = json.loads(notify.LOG_FILE.read_text())
    assert len(log) == 1 and log[0]["title"] == "Hello"


def test_post_dedupes_same_text_within_window():
    sent, publish = _captured()
    notify.post({"title": "Same", "kind": "info"}, publish=publish, now=1000)
    resp, status = notify.post({"title": "Same", "kind": "info"}, publish=publish, now=1030)
    assert resp["deduped"] is True
    assert len(sent) == 1  # nothing re-published
    resp, status = notify.post({"title": "Same", "kind": "info"}, publish=publish, now=2000)
    assert resp.get("deduped") is not True
    assert len(sent) == 2


def test_post_rate_limit():
    sent, publish = _captured()
    notify._RATE_MAX = 3
    try:
        for i in range(3):
            resp, status = notify.post({"title": f"n{i}"}, publish=publish, now=1000 + i)
            assert status == 200
        resp, status = notify.post({"title": "over"}, publish=publish, now=1010)
        assert status == 429
    finally:
        notify._RATE_MAX = 30


# ── pending / history ───────────────────────────────────────────────────


def test_pending_cursor():
    sent, publish = _captured()
    notify.post({"title": "first"}, publish=publish)
    second = notify.post({"title": "second", "body": "b"}, publish=publish)[0]
    data = notify.pending(since_id=sent[0]["id"])
    assert [i["title"] for i in data["items"]] == ["second"]
    assert data["latest"] == second["id"]


def test_pending_unknown_cursor_falls_back_to_recent():
    sent, publish = _captured()
    notify.post({"title": "fresh"}, publish=publish)
    data = notify.pending(since_id="ntf_gone")
    assert [i["title"] for i in data["items"]] == ["fresh"]


def test_history_newest_first():
    sent, publish = _captured()
    for i in range(5):
        notify.post({"title": f"t{i}"}, publish=publish)
    items = notify.history(3)
    assert [i["title"] for i in items] == ["t4", "t3", "t2"]


# ── observe_session_states ──────────────────────────────────────────────


def test_observe_emits_finished_after_work_stretch(monkeypatch):
    sent, publish = _captured()
    monkeypatch.setattr(notify, "_session_identity", lambda sid: {"name": "Fix the tests"})
    monkeypatch.setattr(notify, "_session_is_free_runtime", lambda sid, ident=None: False)
    monkeypatch.setattr(notify, "_session_cost", lambda sid: None)
    monkeypatch.setattr(notify.time, "monotonic", lambda: 1000.0)

    notify.observe_session_states(None, {"s1": {"state": "working"}})
    monkeypatch.setattr(notify.time, "monotonic", lambda: 1000.0 + 30)
    emitted = notify.observe_session_states(
        {"s1": {"state": "working"}},
        {"s1": {"state": "idle"}},
        publish=publish,
        now=5000,
    )
    assert len(emitted) == 1
    item = emitted[0]
    assert item["kind"] == "task"
    assert item["title"] == "Fix the tests finished"
    assert "Done in 30s" in item["body"]
    assert item["url"] == "/?session=s1"


def test_observe_suppresses_short_stretches():
    sent, publish = _captured()
    notify.observe_session_states(None, {"s1": {"state": "working"}})
    emitted = notify.observe_session_states(
        {"s1": {"state": "working"}},
        {"s1": {"state": "idle"}},
        publish=publish,
    )
    # Under _MIN_WORK_S the working stretch earns no ping.
    assert emitted == []


def test_observe_emits_needs_input():
    sent, publish = _captured()
    notify.observe_session_states(None, {"s1": {"state": "working"}})
    emitted = notify.observe_session_states(
        {"s1": {"state": "working"}},
        {"s1": {"state": "waiting", "question_waiting": True}},
        publish=publish,
    )
    assert len(emitted) == 1
    assert emitted[0]["kind"] == "needs_input"
    assert "needs you" in emitted[0]["title"]


def test_observe_emits_wrapped_up_when_working_session_ends():
    sent, publish = _captured()
    notify.observe_session_states(None, {"s1": {"state": "working"}})
    emitted = notify.observe_session_states(
        {"s1": {"state": "working"}},
        {},  # session gone
        publish=publish,
    )
    # Short stretch (monotonic barely advanced) -> suppressed by _MIN_WORK_S.
    assert emitted == []


def test_observe_cooldown_limits_repeat_pings(monkeypatch):
    sent, publish = _captured()
    monkeypatch.setattr(notify, "_session_identity", lambda sid: {"name": "n"})
    monkeypatch.setattr(notify, "_session_is_free_runtime", lambda sid, ident=None: False)
    monkeypatch.setattr(notify, "_session_cost", lambda sid: None)
    base = [1000.0]
    monkeypatch.setattr(notify.time, "monotonic", lambda: base[0])

    def tick(prev, cur, t):
        base[0] = t
        return notify.observe_session_states(prev, cur, publish=publish, now=t)

    tick(None, {"s1": {"state": "working"}}, 0)
    assert tick({"s1": {"state": "working"}}, {"s1": {"state": "idle"}}, 60)
    tick(None, {"s1": {"state": "working"}}, 70)
    # Second finish 80s later is inside the 120s task cooldown -> suppressed.
    assert tick({"s1": {"state": "working"}}, {"s1": {"state": "idle"}}, 150) == []
    tick(None, {"s1": {"state": "working"}}, 160)
    assert tick({"s1": {"state": "working"}}, {"s1": {"state": "idle"}}, 300)


def test_observe_free_runtime_cost_line(monkeypatch):
    sent, publish = _captured()
    monkeypatch.setattr(notify, "_session_identity", lambda sid: {"name": "task"})
    monkeypatch.setattr(notify, "_session_is_free_runtime", lambda sid, ident=None: True)
    monkeypatch.setattr(notify, "_session_cost", lambda sid: {
        "cost_usd": None, "input_tokens": 100000, "cache_read_tokens": 0,
        "cache_creation_tokens": 0, "output_tokens": 10000, "total_tokens": 110000,
    })
    start = [1000.0]
    monkeypatch.setattr(notify.time, "monotonic", lambda: start[0])
    notify.observe_session_states(None, {"s1": {"state": "working"}})
    start[0] = 1000.0 + 240
    emitted = notify.observe_session_states(
        {"s1": {"state": "working"}}, {"s1": {"state": "idle"}}, publish=publish)
    assert len(emitted) == 1
    body = emitted[0]["body"]
    assert "Cost $0" in body
    # 100k in @ $3 + 10k out @ $15 = $0.45 at Sonnet rates.
    assert "saved about $0.45" in body


# ── scheduled items ─────────────────────────────────────────────────────


def _at(hour, minute=0):
    # 2026-10-06 is a Tuesday; build a naive local epoch for the given hour.
    import datetime as _dt
    return _dt.datetime(2026, 10, 6, hour, minute).timestamp()


def test_digest_fires_once_after_6pm(monkeypatch):
    sent, publish = _captured()
    monkeypatch.setattr(notify, "_usage_db_totals",
                        lambda now=None, fresh=False: {"all_time_usd": 40.0, "today_usd": 12.5,
                                                     "sessions_today": 3})
    monkeypatch.setattr(notify, "_savings_snapshot", lambda base_url, rng: None)
    emitted = notify.emit_due(now=_at(18, 5), publish=publish, os_notify=False)
    kinds = [i["kind"] for i in emitted]
    assert "digest" in kinds
    digest = [i for i in emitted if i["kind"] == "digest"][0]
    assert "$12.50" in digest["body"] or "$12" in digest["body"]
    # Second call same day: no digest again.
    emitted2 = notify.emit_due(now=_at(21, 0), publish=publish, os_notify=False)
    assert all(i["kind"] != "digest" for i in emitted2)


def test_no_digest_before_6pm(monkeypatch):
    sent, publish = _captured()
    monkeypatch.setattr(notify, "_usage_db_totals",
                        lambda now=None, fresh=False: {"all_time_usd": 0, "today_usd": 0,
                                                     "sessions_today": 0})
    emitted = notify.emit_due(now=_at(14, 0), publish=publish, os_notify=False)
    assert all(i["kind"] != "digest" for i in emitted)


def test_milestone_fires_once(monkeypatch):
    sent, publish = _captured()
    monkeypatch.setattr(notify, "_usage_db_totals",
                        lambda now=None, fresh=False: {"all_time_usd": 150.0, "today_usd": 0,
                                                     "sessions_today": 0})
    emitted = notify.emit_due(now=_at(10, 0), publish=publish, os_notify=False)
    milestones = [i for i in emitted if i["kind"] == "milestone"]
    titles = [i["title"] for i in milestones]
    assert any("$10" in t for t in titles) and any("$100" in t for t in titles)
    assert not any("$1,000" in t for t in titles)
    # Never again.
    emitted2 = notify.emit_due(now=_at(11, 0), publish=publish, os_notify=False)
    assert all(i["kind"] != "milestone" for i in emitted2)


def test_milestone_uses_savings_snapshot(monkeypatch):
    sent, publish = _captured()
    monkeypatch.setattr(notify, "_usage_db_totals", lambda now=None, fresh=False: None)
    monkeypatch.setattr(notify, "_savings_snapshot",
                        lambda base_url, rng: {"api_value_usd": 20.0, "free_saved_usd": 30.0,
                                               "free_tokens": 0, "free_runs": 4})
    emitted = notify.emit_due(now=_at(10, 0), publish=publish, os_notify=False,
                              base_url="http://127.0.0.1:8090")
    milestones = [i for i in emitted if i["kind"] == "milestone"]
    bodies = " ".join(i["title"] + " " + i["body"] for i in milestones)
    assert "$10" in bodies and "$5" in bodies and "$25" in bodies


# ── formatting ──────────────────────────────────────────────────────────


def test_fmt_duration():
    assert notify._fmt_duration(0) == ""
    assert notify._fmt_duration(45) == "45s"
    assert notify._fmt_duration(240) == "4m"
    assert notify._fmt_duration(3700) == "1h 1m"


def test_fmt_usd():
    assert notify._fmt_usd(0) == ""
    assert notify._fmt_usd(0.004) == "<$0.01"
    assert notify._fmt_usd(1.8) == "$1.80"
    assert notify._fmt_usd(12.5) == "$12.50"
    assert notify._fmt_usd(1200) == "$1,200"


def test_unapproved_kind_is_held(monkeypatch, tmp_path):
    monkeypatch.setattr(popups, "APPROVED", frozenset())
    sent = []
    resp, status = notify.post({"title": "Done", "kind": "task"}, publish=sent.append)
    assert status == 200 and resp["held"] is True
    assert sent == []
    assert notify.history() == []
