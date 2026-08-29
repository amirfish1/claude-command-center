"""Tests for CCC's own Claude-peer socket server (slice 3: CCC as a peer).

The socket path must stay short: AF_UNIX's sun_path is capped at 104 bytes
on macOS, and pytest's `tmp_path` fixture alone (a nested per-test dir under
/private/var/folders/.../pytest-of-<user>/pytest-<n>/<test>0) is already
~105 chars before appending anything -- binding a real socket there fails
with "AF_UNIX path too long". Registry row/key files have no such limit (they
are plain files), so those live under `tmp_path`; only the socket directory
uses a short-path `tempfile.mkdtemp()` fixture instead.
"""

import json
import os
import queue
import shutil
import socket
import tempfile
import threading
import time

import pytest

import ccc_peer_uds
import server


@pytest.fixture
def sock_dir():
    d = tempfile.mkdtemp(prefix="ccc-peer-")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _reset_peer_state():
    server._CCC_PEER_STATE.update(
        sock=None, token="", socket_path="", row_path=None, key_path=None,
    )
    with server._CCC_ASK_CORRELATION_LOCK:
        server._CCC_ASK_CORRELATION.clear()


@pytest.fixture(autouse=True)
def _clean_peer_state():
    _reset_peer_state()
    yield
    try:
        server._ccc_peer_server_stop()
    except Exception:
        pass
    _reset_peer_state()


def test_ccc_peer_server_start_publishes_row_and_key(monkeypatch, tmp_path, sock_dir):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(server, "SESSIONS_REGISTRY", sessions_dir)
    monkeypatch.setattr(server, "_ccc_peer_socket_dir", lambda: __import__("pathlib").Path(sock_dir))

    result = server._ccc_peer_server_start()
    assert result["ok"] is True

    row_path = sessions_dir / f"{os.getpid()}.json"
    assert row_path.exists()
    row = json.loads(row_path.read_text())
    assert row["name"] == "ccc"
    assert row["peerProtocol"] == 1
    assert row["pid"] == os.getpid()
    assert row["messagingSocketPath"] == result["socket_path"]

    key_files = [f for f in os.listdir(sessions_dir) if f.endswith(".key")]
    assert len(key_files) == 1
    key_path = sessions_dir / key_files[0]
    assert oct(key_path.stat().st_mode)[-3:] == "600"
    payload = json.loads(key_path.read_text())
    assert payload.get("peerToken")

    server._ccc_peer_server_stop()
    assert not row_path.exists()
    assert not key_path.exists()


def test_ccc_peer_server_start_noop_when_gate_off(monkeypatch):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "legacy")
    result = server._ccc_peer_server_start()
    assert result == {"ok": False, "reason": "gate_off"}


def test_ccc_peer_server_start_twice_is_idempotent(monkeypatch, tmp_path, sock_dir):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(server, "SESSIONS_REGISTRY", sessions_dir)
    monkeypatch.setattr(server, "_ccc_peer_socket_dir", lambda: __import__("pathlib").Path(sock_dir))
    first = server._ccc_peer_server_start()
    second = server._ccc_peer_server_start()
    assert first["ok"] is True
    assert second == {"ok": True, "reason": "already_running"}


def test_ccc_peer_server_start_refuses_on_registry_shape_drift(monkeypatch, tmp_path, sock_dir):
    """A locally observed 'real' Claude row missing a required key (Claude
    Code's own registry format drifted) must make CCC refuse to publish,
    not crash and not publish a row nothing can actually use."""
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    drifted_row = {
        "pid": 999, "sessionId": "x", "cwd": "/x",
        # messagingSocketPath deliberately missing
        "peerProtocol": 1, "version": "2.1.251",
    }
    (sessions_dir / "999.json").write_text(json.dumps(drifted_row))
    monkeypatch.setattr(server, "SESSIONS_REGISTRY", sessions_dir)
    monkeypatch.setattr(server, "_ccc_peer_socket_dir", lambda: __import__("pathlib").Path(sock_dir))

    result = server._ccc_peer_server_start()
    assert result["ok"] is False
    assert "shape_drift" in result["reason"]
    assert not (sessions_dir / f"{os.getpid()}.json").exists()


def test_ccc_peer_server_start_own_row_invisible_to_load_session_registry(monkeypatch, tmp_path, sock_dir):
    """Safety property: CCC's own row must never surface as a live Claude
    session anywhere _load_session_registry() feeds (sidebar, ask target
    resolution, etc). _process_comm_is_claude filters by pid->comm, and
    CCC's own pid is never a `claude` process, so this holds without any
    extra filtering -- this test is the regression guard for that."""
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(server, "SESSIONS_REGISTRY", sessions_dir)
    monkeypatch.setattr(server, "_ccc_peer_socket_dir", lambda: __import__("pathlib").Path(sock_dir))
    monkeypatch.setattr(server, "_scan_engine_processes", lambda: [])  # nothing looks like claude

    server._ccc_peer_server_start()
    registry = server._load_session_registry()
    assert all(row.get("name") != "ccc" for row in registry.values())


def _start_real_peer(monkeypatch, tmp_path, sock_dir):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    monkeypatch.setattr(server, "SESSIONS_REGISTRY", sessions_dir)
    monkeypatch.setattr(server, "_ccc_peer_socket_dir", lambda: __import__("pathlib").Path(sock_dir))
    result = server._ccc_peer_server_start()
    assert result["ok"] is True
    return result["socket_path"], server._CCC_PEER_STATE["token"]


def _send_frame(socket_path, token, content, from_field=None, msg_id="m-1"):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(2.0)
    s.connect(socket_path)
    s.sendall(json.dumps({"type": "auth", "token": token}).encode() + b"\n")
    user = {"type": "user", "message": {"role": "user", "content": content}, "msg_id": msg_id}
    if from_field is not None:
        user["from"] = from_field
    s.sendall(json.dumps(user).encode() + b"\n")
    s.shutdown(socket.SHUT_WR)
    return s


def test_inbound_report_envelope_routes_to_inject_text_into_session(monkeypatch, tmp_path, sock_dir):
    socket_path, token = _start_real_peer(monkeypatch, tmp_path, sock_dir)
    calls = []
    monkeypatch.setattr(
        server, "_inject_text_into_session",
        lambda sid, text, **kw: calls.append((sid, text, kw)) or {"ok": True},
    )
    envelope = json.dumps({
        "session_id": "dispatcher-sid", "mode": "steer",
        "announced_from": "worker-a", "text": "STATUS: SUCCEEDED",
    })
    s = _send_frame(socket_path, token, envelope)
    for _ in range(50):
        if calls:
            break
        time.sleep(0.05)
    s.close()
    assert len(calls) == 1
    sid, text, kw = calls[0]
    assert sid == "dispatcher-sid"
    assert text == "STATUS: SUCCEEDED"
    assert kw["mode"] == "steer"
    assert kw["source"] == "announced_from"
    assert kw["announced_from"] == "worker-a"


def test_inbound_ask_reply_resolves_pending_ask_and_does_not_inject(monkeypatch, tmp_path, sock_dir):
    socket_path, token = _start_real_peer(monkeypatch, tmp_path, sock_dir)
    calls = []
    monkeypatch.setattr(server, "_inject_text_into_session", lambda *a, **k: calls.append((a, k)))
    q = server._ccc_ask_correlation_register("msg-123", sender_sid="asker-sid", target_sid=None)
    s = _send_frame(socket_path, token, "orig_msg_id: msg-123\nPONG")
    reply = q.get(timeout=3.0)
    s.close()
    assert reply == "PONG"
    assert calls == []


def test_inbound_unroutable_frame_is_logged_not_injected(monkeypatch, tmp_path, sock_dir):
    socket_path, token = _start_real_peer(monkeypatch, tmp_path, sock_dir)
    calls = []
    logged = []
    monkeypatch.setattr(server, "_inject_text_into_session", lambda *a, **k: calls.append((a, k)))
    real_log = server._log_activity

    def spy_log(*a, **k):
        logged.append(a)
        return real_log(*a, **k)

    monkeypatch.setattr(server, "_log_activity", spy_log)
    s = _send_frame(socket_path, token, "just a friendly hello, no structure")
    for _ in range(50):
        if any("CCC-PEER-UNROUTED" in str(a) for a in logged):
            break
        time.sleep(0.05)
    s.close()
    assert calls == []
    assert any("CCC-PEER-UNROUTED" in str(a) for a in logged)


def test_inbound_connection_wrong_token_is_rejected(monkeypatch, tmp_path, sock_dir):
    socket_path, token = _start_real_peer(monkeypatch, tmp_path, sock_dir)
    calls = []
    monkeypatch.setattr(server, "_inject_text_into_session", lambda *a, **k: calls.append((a, k)))
    s = _send_frame(socket_path, "wrong-token", json.dumps({"session_id": "x", "text": "y"}))
    time.sleep(0.3)
    s.close()
    assert calls == []


def test_ask_delivery_registers_correlation_and_headers_the_wrapper(monkeypatch, tmp_path):
    sock = tmp_path / "target.sock"
    sock.touch()
    row = {"pid": 4242, "messagingSocketPath": str(sock), "peerProtocol": 1, "version": "2.1.251"}
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setattr(server, "_load_session_registry", lambda: {"target-sid": row})
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(tmp_path / "s.jsonl"))
    monkeypatch.setattr(ccc_peer_uds, "load_peer_token", lambda d, pid, sp: "tok")
    sent = []
    monkeypatch.setattr(
        ccc_peer_uds, "send_lines",
        lambda sp, lines, timeout_s=3.0: (sent.append([json.loads(l) for l in lines]) or {"ok": True, "error": ""}),
    )
    result = server._try_uds_peer_delivery("target-sid", "please review", source="ask")
    assert result["ok"] is True
    content = sent[0][-1]["message"]["content"]
    msg_id = result["receipt_id"]
    assert f"orig_msg_id: {msg_id}" in content
    with server._CCC_ASK_CORRELATION_LOCK:
        assert msg_id in server._CCC_ASK_CORRELATION
        assert server._CCC_ASK_CORRELATION[msg_id]["target_sid"] == "target-sid"


def test_from_addr_falls_back_to_ccc_socket_for_non_claude_sender(monkeypatch, tmp_path):
    sock = tmp_path / "target.sock"
    sock.touch()
    row = {"pid": 4242, "messagingSocketPath": str(sock), "peerProtocol": 1, "version": "2.1.251"}
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    # peer_sender_sid resolves to a row with no messagingSocketPath (a non-Claude engine)
    monkeypatch.setattr(server, "_load_session_registry", lambda: {"target-sid": row, "sender-sid": {}})
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(tmp_path / "s.jsonl"))
    monkeypatch.setattr(ccc_peer_uds, "load_peer_token", lambda d, pid, sp: "tok")
    server._CCC_PEER_STATE["socket_path"] = "/tmp/cc-socks/999.sock"
    sent = []
    monkeypatch.setattr(
        ccc_peer_uds, "send_lines",
        lambda sp, lines, timeout_s=3.0: (sent.append([json.loads(l) for l in lines]) or {"ok": True, "error": ""}),
    )
    server._try_uds_peer_delivery("target-sid", "go", source="group-chat-coordinate", peer_sender_sid="sender-sid")
    user = sent[0][-1]
    assert user["from"] == "uds:/tmp/cc-socks/999.sock"
    assert 'from="uds:/tmp/cc-socks/999.sock"' in user["message"]["content"]
    assert 'from-name="ccc"' in user["message"]["content"]


def test_from_addr_stays_empty_when_ccc_peer_server_not_running(monkeypatch, tmp_path):
    sock = tmp_path / "target.sock"
    sock.touch()
    row = {"pid": 4242, "messagingSocketPath": str(sock), "peerProtocol": 1, "version": "2.1.251"}
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setattr(server, "_load_session_registry", lambda: {"target-sid": row, "sender-sid": {}})
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(tmp_path / "s.jsonl"))
    monkeypatch.setattr(ccc_peer_uds, "load_peer_token", lambda d, pid, sp: "tok")
    assert server._CCC_PEER_STATE["socket_path"] == ""
    sent = []
    monkeypatch.setattr(
        ccc_peer_uds, "send_lines",
        lambda sp, lines, timeout_s=3.0: (sent.append([json.loads(l) for l in lines]) or {"ok": True, "error": ""}),
    )
    server._try_uds_peer_delivery("target-sid", "go", source="group-chat-coordinate", peer_sender_sid="sender-sid")
    user = sent[0][-1]
    assert "from" not in user
