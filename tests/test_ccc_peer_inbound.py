"""Tests for CCC's inbound peer-frame parsing (slice 3)."""

import json
import socket
import threading

import ccc_peer_inbound as inbound


def _pair():
    a, b = socket.socketpair()
    return a, b


def test_read_frames_yields_parsed_dicts_and_skips_bad_json():
    server_sock, client_sock = _pair()
    client_sock.sendall(b'{"type":"auth","token":"t"}\n')
    client_sock.sendall(b'not json\n')
    client_sock.sendall(b'{"type":"user","msg_id":"m1"}\n')
    client_sock.shutdown(socket.SHUT_WR)
    frames = list(inbound.read_frames(server_sock, first_line_deadline_s=2.0))
    assert frames == [{"type": "auth", "token": "t"}, {"type": "user", "msg_id": "m1"}]


def test_read_frames_closes_on_oversize_line():
    server_sock, client_sock = _pair()
    client_sock.sendall(b'{"type":"user","msg_id":"' + b"x" * 200 + b'"}\n')
    client_sock.shutdown(socket.SHUT_WR)
    frames = list(inbound.read_frames(server_sock, max_line_bytes=64, first_line_deadline_s=2.0))
    assert frames == []  # oversize line drops the connection, not the process


def test_read_frames_deadline_on_silent_connection():
    server_sock, client_sock = _pair()
    frames = list(inbound.read_frames(server_sock, first_line_deadline_s=0.2))
    assert frames == []
    client_sock.close()


def test_unwrap_message_strips_wrapper():
    wrapped = '<cross-session-message from="uds:x">hello there</cross-session-message>'
    assert inbound.unwrap_message(wrapped) == "hello there"


def test_unwrap_message_passthrough_when_no_wrapper():
    assert inbound.unwrap_message("plain text") == "plain text"


def test_parse_report_envelope_valid_shape():
    body = json.dumps({"session_id": "abc", "mode": "steer", "text": "STATUS: SUCCEEDED"})
    env = inbound.parse_report_envelope(body)
    assert env == {"session_id": "abc", "mode": "steer", "text": "STATUS: SUCCEEDED"}


def test_parse_report_envelope_rejects_non_report_json():
    assert inbound.parse_report_envelope('{"foo": "bar"}') is None
    assert inbound.parse_report_envelope("not json at all") is None


def test_extract_orig_msg_id_present():
    orig, rest = inbound.extract_orig_msg_id("orig_msg_id: 11111111-2222-4333-8444-555555555555\nPONG")
    assert orig == "11111111-2222-4333-8444-555555555555"
    assert rest == "PONG"


def test_extract_orig_msg_id_absent():
    orig, rest = inbound.extract_orig_msg_id("just a reply, no header")
    assert orig is None
    assert rest == "just a reply, no header"
