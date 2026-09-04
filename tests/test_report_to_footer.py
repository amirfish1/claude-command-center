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
    # SendMessage is the primary path; the one-line curl *fallback* mention
    # is fine, but the full curl command block must stay curl-footer-only.
    assert "curl -s --max-time" not in out


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
    which peers reject with "No agent named ... is reachable". The footer
    must state the recipient name prominently and forbid session-id
    addressing, and keep a curl fallback."""
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setitem(server._CCC_PEER_STATE, "socket_path", "/tmp/cc-socks/1.sock")
    out = server._wrap_prompt_with_return_address("do the thing", "dispatcher-sid", engine="claude")
    assert 'agent="ccc"' in out
    assert "NEVER address SendMessage" in out
    assert "No agent named" in out
    assert "/api/inject-input" in out
