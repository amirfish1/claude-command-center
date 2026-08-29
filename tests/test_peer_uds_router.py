"""Router-side tests for the UDS peer transport (slice 2)."""

import json

import server


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_peer_receipt_delivered_when_msg_id_lands(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [
        {"type": "user", "isMeta": True, "origin": {"kind": "peer", "msg_id": "m-1", "body": "hi"}},
    ])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-1", "hi", timeout_s=0.2) == "delivered"


def test_peer_receipt_delivered_for_absorbed_attachment(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [
        {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "peer", "msg_id": "m-2"}}},
    ])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-2", "x", timeout_s=0.2) == "delivered"


def test_peer_receipt_held_when_preview_matches(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [
        {"type": "system", "subtype": "informational",
         "content": "Held peer message — from uds:/tmp/cc-socks/1.sock; preview: «please run tests now» — not delivered"},
    ])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-3", "please run tests now", timeout_s=0.2) == "held"


def test_peer_receipt_unknown_on_timeout(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [{"type": "user", "message": {"content": "unrelated"}}])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-4", "x", timeout_s=0.2) == "unknown"


def test_peer_receipt_held_matches_truncated_and_escaped_preview(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    body = 'Please re-run "tests/test_ccc_peer_uds.py" with ünïcode and then report back to me'
    content = "Held peer message — from uds:/tmp/cc-socks/1.sock; preview: «" + body[:60] + "» — not delivered"
    _write_jsonl(p, [
        {"type": "system", "subtype": "informational", "content": content},
    ])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-5", body, timeout_s=0.2) == "held"
