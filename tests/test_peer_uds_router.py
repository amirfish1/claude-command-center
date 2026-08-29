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


import ccc_peer_uds


def test_uds_gate_reads_backend_env(monkeypatch):
    monkeypatch.delenv("CCC_MESSAGING_BACKEND", raising=False)
    assert server._uds_messaging_enabled() is False
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    assert server._uds_messaging_enabled() is True
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "wt,uds")
    assert server._uds_messaging_enabled() is True
    assert server._wt_messaging_enabled() is False  # unchanged: exact "wt" only


def test_uds_source_eligibility():
    for src in ("ask", "group-chat-coordinate", "group-chat-auto-nudge", "group-chat-manual-nudge", "announced_from", "report_to", "wt"):
        assert server._uds_source_eligible(src) is True
    for src in ("api", "user", "manual", "ccc-spawn", "terminal-queue-watcher", "", None):
        assert server._uds_source_eligible(src) is False


def _enable_uds(monkeypatch, tmp_path, registry_row, sent):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setattr(server, "_load_session_registry", lambda: {"target-sid": registry_row})
    monkeypatch.setattr(server, "SESSIONS_REGISTRY", tmp_path)
    monkeypatch.setattr(ccc_peer_uds, "load_peer_token", lambda d, pid, sp: "tok")

    def fake_send(socket_path, lines, timeout_s=3.0):
        sent.append((socket_path, [json.loads(l) for l in lines]))
        return {"ok": True, "error": ""}

    monkeypatch.setattr(ccc_peer_uds, "send_lines", fake_send)


def _dialable_row(tmp_path):
    sock = tmp_path / "t.sock"
    sock.touch()
    return {"pid": 4242, "messagingSocketPath": str(sock), "peerProtocol": 1, "version": "2.1.251"}


def test_try_uds_delivers_and_returns_receipt(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda sid, mid, body, timeout_s=6.0: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    result = server._try_uds_peer_delivery("target-sid", "please review", source="ask")
    assert result["ok"] is True and result["via"] == "uds" and result["receipt"] == "delivered"
    assert result["receipt_id"]
    assert len(sent) == 1
    socket_path, frames = sent[0]
    assert frames[0] == {"type": "auth", "token": "tok"}
    user = frames[1]
    assert user["msg_id"] == result["receipt_id"]
    assert user["priority"] == "next"
    assert "from" not in user  # no sender session known, no address claimed
    assert user["message"]["content"].startswith("<cross-session-message>")
    assert "please review" in user["message"]["content"]


def test_try_uds_steer_uses_priority_now(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    server._try_uds_peer_delivery("target-sid", "stop", source="ask", mode="steer")
    assert sent[0][1][-1]["priority"] == "now"


def test_try_uds_attests_bypass_only_for_ccc_spawned_sender(monkeypatch, tmp_path):
    sent = []
    sender_sock = tmp_path / "sender.sock"
    sender_sock.touch()
    rows = {
        "target-sid": _dialable_row(tmp_path),
        "sender-sid": {"pid": 77, "messagingSocketPath": str(sender_sock), "peerProtocol": 1, "version": "2.1.251", "name": "dispatcher"},
    }
    _enable_uds(monkeypatch, tmp_path, rows["target-sid"], sent)
    monkeypatch.setattr(server, "_load_session_registry", lambda: rows)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session",
                        lambda sid: {"engine": "claude"} if sid == "sender-sid" else None)
    server._try_uds_peer_delivery("target-sid", "go", source="group-chat-coordinate", peer_sender_sid="sender-sid")
    user = sent[0][1][-1]
    assert user["from"] == "uds:" + str(sender_sock)
    content = user["message"]["content"]
    assert 'from="uds:' + str(sender_sock) + '"' in content
    assert 'from-name="dispatcher"' in content
    assert 'from-mode="bypass"' in content

    sent.clear()
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    server._try_uds_peer_delivery("target-sid", "go", source="group-chat-coordinate", peer_sender_sid="sender-sid")
    assert "from-mode=" not in sent[0][1][-1]["message"]["content"]


def test_try_uds_falls_through_when_held_or_unknown(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    for verdict in ("held", "unknown"):
        monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: verdict)
        assert server._try_uds_peer_delivery("target-sid", "x", source="ask") is None


def test_try_uds_skips_ineligible_source_without_dialing(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    assert server._try_uds_peer_delivery("target-sid", "x", source="user") is None
    assert server._try_uds_peer_delivery("target-sid", "x", source="api") is None
    assert sent == []


def test_try_uds_skips_when_gate_off_or_target_not_dialable(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.delenv("CCC_MESSAGING_BACKEND")
    assert server._try_uds_peer_delivery("target-sid", "x", source="ask") is None
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setattr(server, "_load_session_registry", lambda: {})
    assert server._try_uds_peer_delivery("target-sid", "x", source="ask") is None
    assert sent == []


def test_try_uds_loads_registry_once_and_never_forks(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    calls = {"registry": 0}
    row = _dialable_row(tmp_path)

    def counting_registry():
        calls["registry"] += 1
        return {"target-sid": row}

    monkeypatch.setattr(server, "_load_session_registry", counting_registry)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess in uds path")))
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess in uds path")))
    server._try_uds_peer_delivery("target-sid", "x", source="ask")
    assert calls["registry"] == 1
