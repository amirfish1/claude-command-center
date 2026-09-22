"""Queued /compact must not loop forever on "Not enough messages to compact."

Incident (2026-08-26, twice): a /compact queued behind a running turn was
delivered, the session compacted (199k -> 25k), and a SECOND queued /compact
then hit the freshly compacted session. Claude Code answered with
`compact_result: "failed", compact_error: "Not enough messages to compact."`.
The terminal-queue watcher treated that `ok: False` like a transient delivery
failure -- requeue at the front, 60s backoff -- so the session received
"/compact" every minute until the dashboard was restarted.

Two invariants pinned here:

  * A compact/clear outcome that the SESSION ITSELF refused is terminal.
    Retrying cannot change it; the entry is consumed, not requeued.
  * The terminal-input queue never holds two copies of the same slash
    command for one session -- the second /compact could not have stacked.
"""
import importlib
import inspect

import pytest

server = importlib.import_module("server")


def test_not_enough_messages_is_terminal():
    result = {
        "ok": False,
        "via": "live-spawn-stdin",
        "code": "compact_failed",
        "compact_result": "failed",
        "compact_error": "Not enough messages to compact.",
        "compact": True,
    }
    assert server._terminal_queue_result_is_terminal(result) is True


@pytest.mark.parametrize("code", [
    "compact_unsupported_engine",
    "clear_unsupported_engine",
    "compact_needs_manual",
])
def test_other_session_refusals_are_terminal(code):
    assert server._terminal_queue_result_is_terminal({"ok": False, "code": code})


@pytest.mark.parametrize("result", [
    {"ok": False},
    {"ok": False, "code": "compact_stdin_write_failed"},
    {"ok": False, "code": "compact_session_busy"},
    {"ok": False, "code": "compact_spawn_exited"},
    None,
    "nope",
])
def test_delivery_failures_are_not_terminal(result):
    assert server._terminal_queue_result_is_terminal(result) is False


def test_dead_target_drop_is_loud_and_clears_the_entire_terminal_queue(monkeypatch, tmp_path):
    """A fresh sidecar is not a delivery channel for an exited one-shot run."""
    sid = "dead-cron-session"
    text = "the user message"
    completed = []
    activity = []
    monkeypatch.setattr(
        server,
        "_apply_pending_input_operations",
        lambda *args, **kwargs: {"ok": True, "value": [[text]]},
    )
    monkeypatch.setattr(
        server, "_complete_pending_input_handoff", lambda value: completed.append(value),
    )
    # Isolate the CCC-28 receipt file this now touches from the developer's
    # real ~/.claude/command-center state.
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        server, "_clear_foreign_writer_hold", lambda value: None,
    )
    monkeypatch.setattr(
        server, "_terminal_queue_clear_hold", lambda value: None,
    )
    monkeypatch.setattr(
        server, "_log_activity", lambda *args: activity.append(args),
    )

    dropped = server._drop_dead_terminal_queue(sid, code="dead_target")

    assert dropped == [text]
    assert completed == [text]
    assert activity == [
        (
            "inject",
            "Q_DROP",
            "session=dead-cron-session code=dead_target "
            "text='the user message' — no live delivery target; dropped as undeliverable",
        )
    ]


def test_watcher_drops_a_fresh_but_dead_target_before_retrying_delivery():
    """A fresh sidecar must not let a one-shot Claude process enter retries."""
    source = inspect.getsource(server._start_resume_queue_watcher)
    status_at = source.find("status = _core.session_live_status")
    dead_drop_at = source.find(
        '_core._drop_dead_terminal_queue(sid, code="dead_target")'
    )
    requeue_at = source.find("_core._requeue_terminal_input_front(sid, text)")

    assert status_at != -1
    assert dead_drop_at > status_at
    assert requeue_at > dead_drop_at


def test_retry_loop_branches_are_no_longer_silent():
    """CCC-28: a composer inject was accepted into the terminal queue
    (`queued=True`, logged once) and then vanished forever -- no Q_HELD, no
    Q_DROP, no INJECT_STALLED, nothing -- because a retried delivery that
    re-parks itself (`result["queued"]`) or fails outright (`not
    result["ok"]`) hit neither of those log sites. Pin that both retry
    outcomes now call the same throttled hold-logger as every named hold
    reason above them, so a message stuck in this exact loop leaves a
    repeating trail instead of silence."""
    source = inspect.getsource(server._start_resume_queue_watcher)
    queued_at = source.find('if result.get("queued"):')
    queued_log_at = source.find(
        '_log_terminal_queue_hold(sid, "requeued_self_queued")'
    )
    not_ok_at = source.find('elif not result.get("ok"):')
    not_ok_log_at = source.find(
        '_log_terminal_queue_hold(sid, "requeued_after_failed_delivery")'
    )

    assert queued_at != -1 and queued_log_at != -1
    assert not_ok_at != -1 and not_ok_log_at != -1
    assert queued_at < queued_log_at < not_ok_at
    assert not_ok_at < not_ok_log_at


@pytest.fixture
def isolated_queue(monkeypatch):
    monkeypatch.setattr(server, "_pending_terminal_input_queue", {})
    monkeypatch.setattr(server, "_save_pending_inputs", lambda: None)
    monkeypatch.setattr(server, "_foreign_writer_hold_for_sid", lambda sid: None)
    return server._pending_terminal_input_queue


def test_queue_dedupes_repeated_compact(isolated_queue):
    sid = "sid-compact"
    first = server._queue_terminal_input_unlocked(sid, "/compact")
    second = server._queue_terminal_input_unlocked(sid, "/compact ")
    assert isolated_queue[sid] == ["/compact"]
    assert first["queued"] is True and first["queued_count"] == 1
    assert second["queued"] is True and second["queued_count"] == 1
    assert second.get("deduped") is True


def test_queue_dedupes_repeated_clear(isolated_queue):
    sid = "sid-clear"
    server._queue_terminal_input_unlocked(sid, "/clear")
    server._queue_terminal_input_unlocked(sid, "/clear")
    assert isolated_queue[sid] == ["/clear"]


def test_queue_keeps_plain_text_duplicates(isolated_queue):
    sid = "sid-text"
    server._queue_terminal_input_unlocked(sid, "continue please")
    server._queue_terminal_input_unlocked(sid, "continue please")
    assert isolated_queue[sid] == ["continue please", "continue please"]
