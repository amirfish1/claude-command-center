"""Tests for the report_to return-address footer. The curl POST to
/api/inject-input is the only path ever instructed as primary; SendMessage
to CCC's own "ccc" peer identity is at most a best-effort mention for Claude
children, demoted from primary 2026-10-03 (OPS-1250: CCC's own registry row
publishes "kind": "background", which ListAgents never lists, so SendMessage
to "ccc" reliably fails with "No agent named ... is reachable" regardless of
gate/socket state -- reproduced repeatedly, not a flaky edge case)."""

import server
import pytest


@pytest.mark.parametrize("engine", ["claude", "codex", "cursor", "antigravity"])
def test_coordinators_yield_to_reports_instead_of_sleep_polling(engine):
    out = server._wrap_prompt_with_return_address("coordinate release", "parent", engine=engine)
    assert "`report_to`" in out
    assert "end your current turn while waiting" in out
    assert "Do not repeatedly call sleep or poll child status" in out
    # Ending a waiting turn must not generate an early success callback.
    assert "send your own completion report only after the required child results" in out
    assert "gh run watch <run-id> --exit-status" in out
    assert "Give the watcher a timeout and retain its process handle" in out


def test_footer_curl_is_always_present_for_claude_when_gate_on(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid", engine="claude")
    # OPS-1250 regression: curl must be the primary instruction even when the
    # uds gate is on and CCC's peer socket is listening -- the old code
    # dropped the curl block entirely in this state, which is exactly the
    # state where the SendMessage-to-"ccc" target silently never delivers.
    assert "curl -s --max-time" in out
    assert '"session_id": "dispatcher-sid"' in out


def test_footer_mentions_sendmessage_only_as_best_effort(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid", engine="claude")
    assert "SendMessage" in out
    assert 'agent="ccc"' in out
    assert "best-effort" in out
    assert "do NOT retry" in out
    assert "curl -s --max-time" in out


def test_footer_stays_curl_when_gate_off(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "legacy")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid", engine="claude")
    assert "curl" in out
    assert "SendMessage" not in out


def test_footer_stays_curl_when_ccc_peer_server_not_running(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "")
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid", engine="claude")
    assert "curl" in out
    assert "SendMessage" not in out


def test_footer_stays_curl_for_non_claude_engine_even_with_gate_on(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid", engine="codex")
    assert "curl" in out
    assert "SendMessage" not in out


def test_footer_no_op_without_report_to(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    assert server._wrap_prompt_with_return_address("do the thing", "", engine="claude") == "do the thing"


def test_footer_defaults_to_claude_engine_when_unspecified(monkeypatch):
    """Every existing call site before slice 3 assumed the child was Claude
    (the only engine that had SendMessage available at all); the new
    `engine` kwarg must default to "claude" so an un-migrated call site
    keeps today's behaviour instead of silently losing the footer."""
    monkeypatch.delenv("CCC_MESSAGING_BACKEND", raising=False)
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid")
    assert "curl" in out


def test_footer_sendmessage_warns_against_session_id_recipient(monkeypatch):
    """OPS-927: a lane that loses the footer's exact wording to context
    compaction tended to SendMessage the dispatcher's session UUID directly,
    which peers reject with "No agent named ... is reachable". The best-effort
    SendMessage mention names "No agent named ... is reachable" explicitly so
    a lane that hits it moves straight to curl instead of retrying it."""
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid", engine="claude")
    assert 'agent="ccc"' in out
    assert "No agent named" in out
    assert "/api/inject-input" in out
