"""HTTP wiring for the CCC Cloud relay (server.py -> ccc_server/cloud_relay.py).

The pairing/enable/unpair endpoints and the status read are localhost-only:
a tailnet peer, a reverse proxy on loopback (tailscale serve), or a foreign
Origin must never pair this machine or read its pairing state.
"""
import email
import io
import json
import sys

import pytest


def _server():
    sys.argv = ["server.py"]
    import server
    return server


class FakeManager:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append("status")
        return {"paired": False, "enabled": False}

    def get_config(self):
        return {"enabled": False}

    def start_pairing(self, code, url):
        self.calls.append(("pair", code, url))
        return {"ok": True, "masked_email": "a***@***.com"}

    def set_config(self, enabled=None, share_titles=None):
        self.calls.append(("config", enabled, share_titles))
        return {"ok": True}

    def confirm_pairing(self):
        return {"ok": True}

    def abandon_pairing(self):
        return {"ok": True}

    def unpair(self):
        self.calls.append("unpair")
        return {"ok": True}


@pytest.fixture
def fake(monkeypatch):
    server = _server()
    mgr = FakeManager()
    monkeypatch.setattr(server, "cloud_relay_manager", lambda port=None: mgr)
    return server, mgr


def _call(server, method, path, payload=None, ip="127.0.0.1", headers=None):
    raw = json.dumps(payload or {}).encode() if method == "POST" else b""
    handler = server.CommandCenterHandler.__new__(server.CommandCenterHandler)
    handler.path = path
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.client_address = (ip, 0)
    handler.close_connection = True
    hdrs = {"Content-Length": str(len(raw)), "Host": f"127.0.0.1:{server.PORT}"}
    hdrs.update(headers or {})
    handler.headers = email.message_from_string(
        "".join(f"{k}: {v}\r\n" for k, v in hdrs.items()) + "\r\n")
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    getattr(handler, f"do_{method}")()
    head, _, body = handler.wfile.getvalue().partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    return status, json.loads(body or b"{}")


def test_local_pair_reaches_manager(fake):
    server, mgr = fake
    status, out = _call(server, "POST", "/api/cloud-relay/pair",
                        {"pair_code": "ABCD1234", "relay_url": "https://r.example"},
                        headers={"Origin": f"http://127.0.0.1:{server.PORT}"})
    assert status == 200 and out["ok"] is True
    assert mgr.calls == [("pair", "ABCD1234", "https://r.example")]


def test_tailnet_peer_cannot_pair(fake):
    server, mgr = fake
    status, out = _call(server, "POST", "/api/cloud-relay/pair",
                        {"pair_code": "X"}, ip="100.64.0.7")
    assert status == 403 and "localhost-only" in out["error"]
    assert mgr.calls == []


def test_reverse_proxy_on_loopback_cannot_unpair(fake):
    server, mgr = fake
    status, _ = _call(server, "POST", "/api/cloud-relay/unpair",
                      headers={"X-Forwarded-For": "100.64.0.7"})
    assert status == 403
    status, _ = _call(server, "POST", "/api/cloud-relay/unpair",
                      headers={"Host": "mac.tailnet-1234.ts.net"})
    assert status == 403
    assert mgr.calls == []


def test_loopback_origin_on_other_port_rejected(fake):
    server, mgr = fake
    status, _ = _call(server, "POST", "/api/cloud-relay/config", {"enabled": True},
                      headers={"Origin": f"http://127.0.0.1:{server.PORT + 1}"})
    assert status == 403
    assert mgr.calls == []


def test_status_read_is_localhost_only(fake):
    server, mgr = fake
    status, _ = _call(server, "GET", "/api/cloud-relay/status", ip="100.64.0.7")
    assert status == 403
    status, out = _call(server, "GET", "/api/cloud-relay/status")
    assert status == 200 and out["paired"] is False
    assert mgr.calls == ["status"]
