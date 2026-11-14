"""The dashboard must see wake stages recorded by another process."""

import collections
import json
import time
from unittest import mock

import pytest


@pytest.fixture
def wake(tmp_path, monkeypatch):
    import server  # noqa: F401
    from ccc_server import core, queue_events as q

    ledger = tmp_path / "resume.jsonl"
    backup = tmp_path / "resume.1.jsonl"
    monkeypatch.setattr(core, "_RESUME_LEDGER_FILE", ledger)
    monkeypatch.setattr(core, "_RESUME_LEDGER_BACKUP", backup)
    monkeypatch.setattr(core, "_CODEX_WAKE_EVENTS", collections.OrderedDict())
    monkeypatch.setattr(core, "_codex_thread_state_resolved", lambda sid: {})
    monkeypatch.setattr(core, "_codex_app_server_transport_kind", lambda: "stdio")
    monkeypatch.setattr(q, "_CODEX_WAKE_LEDGER_CACHE", collections.OrderedDict())
    monkeypatch.setattr(q, "_codex_wake_rollout_snapshot", lambda sid: {})
    return core, q, ledger, backup


def write(ledger, rows):
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))


def rows_at(epoch):
    return [
        {"sid": "thread-a", "event": "codex_wake_attempt", "epoch": epoch, "model": "test-model"},
        {"sid": "thread-a", "event": "codex_wake_stage", "epoch": epoch + .1, "stage": "turn-start-fast"},
        {"sid": "thread-a", "event": "codex_wake_ok", "epoch": epoch + .2, "turn_id": "turn-a"},
    ]


def test_worker_only_ack_is_running_not_waiting_for_delivery(wake):
    core, q, ledger, _ = wake
    rows = rows_at(time.time() - 20)
    write(ledger, rows)
    assert not core._CODEX_WAKE_EVENTS
    status = q.build_codex_wake_status("thread-a")
    assert status["active"]
    assert status["model"] == "test-model"
    assert status["stages"] == [
        {"name": "connect", "ts": rows[0]["epoch"], "done": True},
        {"name": "turn-start", "ts": rows[1]["epoch"], "done": True},
        {"name": "running", "ts": rows[2]["epoch"], "done": False},
    ]
    assert status["outcome"] is None


def test_previous_turn_does_not_ack_new_attempt(wake):
    _, q, ledger, _ = wake
    now = time.time()
    rows = rows_at(now - 120)
    rows.append({"sid": "thread-a", "event": "codex_wake_attempt", "epoch": now - 90})
    write(ledger, rows)
    status = q.build_codex_wake_status("thread-a")
    assert status["outcome"] == "error"
    assert all(s["name"] != "running" for s in status["stages"])
    assert not status["stages"][0]["done"]


def test_rotation_and_completion_survive_empty_dashboard_memory(wake, monkeypatch):
    _, q, ledger, backup = wake
    rows = rows_at(time.time() - 20)
    write(backup, rows)
    write(ledger, [{"sid": "unrelated", "event": "exit", "epoch": time.time()}])
    monkeypatch.setattr(q, "_codex_wake_rollout_snapshot", lambda sid: {
        "task_complete_epoch": time.time() - 1, "input_tokens": 34237,
        "output_tokens": 707, "no_agent_output": False,
    })
    assert q.build_codex_wake_status("thread-a")["outcome"] == "reply"


def test_unchanged_poll_reads_no_ledger_and_append_invalidates(wake):
    _, q, ledger, _ = wake
    rows = rows_at(time.time() - 20)
    write(ledger, rows[:1])
    q._codex_wake_events("thread-a")
    with mock.patch.object(type(ledger), "open", side_effect=AssertionError("reread")):
        assert len(q._codex_wake_events("thread-a")) == 1
    write(ledger, rows)
    assert len(q._codex_wake_events("thread-a")) == 3


def test_local_unflushed_events_and_equal_millisecond_order(wake):
    core, q, ledger, _ = wake
    rows = rows_at(time.time() - 20)
    rows[1]["epoch"] = rows[0]["epoch"]
    write(ledger, rows[:2])
    core._CODEX_WAKE_EVENTS["thread-a"] = collections.deque(rows)
    events = q._codex_wake_events("thread-a")
    assert events == rows


def test_tail_is_bounded_and_malformed_rows_are_ignored(wake, monkeypatch):
    _, q, ledger, _ = wake
    monkeypatch.setattr(q, "_CODEX_WAKE_LEDGER_BYTES", 2048)
    ledger.write_text("x" * 10000 + "\nnot-json\n[]\n" + json.dumps({
        "sid": "thread-a", "event": "codex_wake_attempt", "epoch": "bad",
    }) + "\n" + "".join(json.dumps(r) + "\n" for r in rows_at(time.time())))
    real_open = type(ledger).open
    reads = []

    def measured_open(candidate, *args, **kwargs):
        fh = real_open(candidate, *args, **kwargs)
        wrapped = mock.MagicMock(wraps=fh)
        wrapped.__enter__.return_value = wrapped
        wrapped.read.side_effect = lambda size: (reads.append(size), fh.read(size))[1]
        wrapped.__exit__.side_effect = lambda *args: fh.close()
        return wrapped

    with mock.patch.object(type(ledger), "open", measured_open):
        assert len(q._codex_wake_events("thread-a")) == 3
    assert reads == [2048]
