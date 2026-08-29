"""Unit tests for the stdlib UDS peer-messaging helpers."""

import hashlib
import json
import os
import socket
import tempfile
import threading
from pathlib import Path

import ccc_peer_uds as uds


def test_wrap_builds_cross_session_wrapper_with_only_known_attrs():
    out = uds.wrap("hello", from_addr="uds:/tmp/cc-socks/4242.sock", from_name="worker-a", from_mode="bypass")
    assert out == (
        '<cross-session-message from="uds:/tmp/cc-socks/4242.sock" '
        'from-name="worker-a" from-mode="bypass">\nhello\n</cross-session-message>'
    )
    assert uds.wrap("hi") == "<cross-session-message>\nhi\n</cross-session-message>"


def test_wrap_escapes_attribute_quotes_and_body_close_tag():
    out = uds.wrap("a </cross-session-message> b", from_name='x"y')
    assert 'from-name="x&quot;y"' in out
    assert out.count("</cross-session-message>") == 1


def test_key_path_uses_sha256_of_socket_path():
    d = Path("/nonexistent/sessions")
    sock = "/tmp/cc-socks/4242.sock"
    digest = hashlib.sha256(sock.encode()).hexdigest()
    assert uds.key_path_for(d, 4242, sock) == d / f"4242.{digest}.key"


def test_load_peer_token_reads_json_key_file(tmp_path):
    sock = "/tmp/cc-socks/4242.sock"
    uds.key_path_for(tmp_path, 4242, sock).write_text(json.dumps({"peerToken": "tok-123"}))
    assert uds.load_peer_token(tmp_path, 4242, sock) == "tok-123"
    assert uds.load_peer_token(tmp_path, 9999, sock) == ""


def test_version_tuple_and_resolve_target_refusals(tmp_path):
    assert uds.version_tuple("2.1.251") == (2, 1, 251)
    assert uds.version_tuple("junk") == ()
    sock = tmp_path / "s.sock"
    base = {"pid": 4242, "messagingSocketPath": str(sock), "peerProtocol": 1, "version": "2.1.251"}
    r = uds.resolve_target(base)
    assert r["ok"] is False and r["reason"] == "socket_missing"
    sock.touch()
    r = uds.resolve_target(base)
    assert r["ok"] is True and r["socket_path"] == str(sock) and r["pid"] == 4242
    assert uds.resolve_target(dict(base, peerProtocol=2))["reason"] == "peer_protocol"
    assert uds.resolve_target(dict(base, version="2.1.200"))["reason"] == "version_too_old"
    assert uds.resolve_target(dict(base, messagingSocketPath=""))["reason"] == "no_socket_path"
    assert uds.resolve_target({})["reason"] == "no_socket_path"


def test_build_frame_lines_auth_then_user():
    lines = uds.build_frame_lines(
        "wrapped", token="tok", from_addr="uds:/tmp/cc-socks/1.sock",
        msg_id="11111111-2222-4333-8444-555555555555", priority="next",
    )
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"type": "auth", "token": "tok"}
    user = json.loads(lines[1])
    assert user == {
        "type": "user",
        "message": {"role": "user", "content": "wrapped"},
        "from": "uds:/tmp/cc-socks/1.sock",
        "msg_id": "11111111-2222-4333-8444-555555555555",
        "priority": "next",
    }
    assert all(l.endswith(b"\n") for l in lines)


def test_build_frame_lines_omits_auth_and_from_when_blank():
    lines = uds.build_frame_lines("w", msg_id="m1")
    assert len(lines) == 1
    assert "from" not in json.loads(lines[0])


def test_build_frame_lines_rejects_oversize_line():
    try:
        uds.build_frame_lines("x" * (1024 * 1024), msg_id="m1")
    except ValueError as exc:
        assert "1 MiB" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def _fake_server(path, received):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        buf = b""
        while True:
            chunk = conn.recv(65536)
            if not chunk:
                break
            buf += chunk
        received.extend(buf.split(b"\n"))
        conn.close()
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_send_lines_writes_all_lines_and_closes():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.sock")
        received = []
        t = _fake_server(path, received)
        lines = uds.build_frame_lines("hi", token="tok", msg_id="m1")
        result = uds.send_lines(path, lines, timeout_s=2.0)
        t.join(timeout=3)
        assert result == {"ok": True, "error": ""}
        assert json.loads(received[0])["type"] == "auth"
        assert json.loads(received[1])["msg_id"] == "m1"


def test_send_lines_reports_connect_refused(tmp_path):
    result = uds.send_lines(str(tmp_path / "missing.sock"), [b"{}\n"], timeout_s=0.5)
    assert result["ok"] is False and result["error"]


def test_send_lines_never_raises_on_none_socket():
    result = uds.send_lines(None, [b"{}\n"], timeout_s=0.5)
    assert result["ok"] is False and result["error"]


def test_send_lines_never_raises_on_invalid_socket_type():
    result = uds.send_lines(12345, [b"{}\n"], timeout_s=0.5)
    assert result["ok"] is False and result["error"]


def test_send_lines_never_raises_on_negative_timeout():
    result = uds.send_lines("/tmp/cc-socks/nope.sock", [b"{}\n"], timeout_s=-1)
    assert result["ok"] is False and result["error"]
