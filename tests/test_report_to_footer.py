"""Tests for the report_to return-address footer (slice 3: SendMessage-to-ccc
for Claude children, curl fallback for everyone else)."""

import server


def test_footer_uses_sendmessage_for_claude_when_gate_on_and_peer_running(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid", engine="claude")
    assert "SendMessage" in out
    assert 'agent="ccc"' in out
    assert '"session_id": "dispatcher-sid"' in out
    assert "curl" not in out


def test_footer_stays_curl_when_gate_off(monkeypatch):
    monkeypatch.delenv("CCC_MESSAGING_BACKEND", raising=False)
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
