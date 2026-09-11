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
