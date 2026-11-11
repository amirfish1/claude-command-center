"""On-call over real HTTP: the preview flag gate, the local-only rule, and one
full setup through the same handler the dashboard and `ccc oncall` use."""

import json
import socket
import socketserver
import threading
import urllib.error
import urllib.request

import pytest

import server
from ccc_server.oncall import engine as engine_mod


def _call(port, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers=dict({"Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


@pytest.fixture
def port(tmp_path, monkeypatch):
    from ccc_server import log_parse
    monkeypatch.setattr(log_parse, "FEATURE_FLAGS_FILE", tmp_path / "feature-flags.json")
    monkeypatch.setenv("CCC_ONCALL_STATE_DIR", str(tmp_path / "oncall"))
    monkeypatch.delenv("CCC_FF_ONCALL", raising=False)
    monkeypatch.delenv("CCC_PROXY_SECRET", raising=False)
    monkeypatch.delenv("CCC_PROXY_SECRET_FILE", raising=False)
    monkeypatch.setattr(engine_mod, "_ENGINE", None)

    class Handler(server.CommandCenterHandler):
        def log_message(self, *_args):
            pass

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        number = s.getsockname()[1]
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", number), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield number
    httpd.shutdown()
    httpd.server_close()
    engine_mod._ENGINE = None


def test_everything_is_404_until_the_preview_flag_is_on(port, tmp_path):
    for method, path in (("GET", "/api/oncall/status"), ("POST", "/api/oncall/config"),
                         ("POST", "/api/oncall/report"), ("POST", "/api/oncall/tick")):
        code, res = _call(port, method, path, {} if method == "POST" else None)
        assert code == 404, (method, path, res)
        assert "preview" in res["error"]
    # Off means off: nothing was created on disk either.
    assert not (tmp_path / "oncall").exists()


def test_setup_report_and_status_over_http(port, tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_FF_ONCALL", "1")
    code, res = _call(port, "GET", "/api/oncall/status")
    assert code == 200 and res["flag"] is True and res["enabled"] is False

    sent = tmp_path / "sent.txt"
    code, res = _call(port, "POST", "/api/oncall/channel",
                      {"channel": {"type": "command", "send": f"sh -c 'cat >> {sent}'", "owner": "me@example.com"}})
    assert code == 200, res
    code, res = _call(port, "POST", "/api/oncall/test", {})
    assert code == 200 and res["ok"] and sent.read_text().startswith("This is a test from CCC On-call.")

    # A bad request is a 400 with a sentence, not a stack trace.
    code, res = _call(port, "POST", "/api/oncall/config", {"enabled": True})
    assert code == 400 and res["error"] == "Name a queue first."
    code, res = _call(port, "POST", "/api/oncall/channel", {"channel": {"type": "pager"}})
    assert code == 400 and res["error"] == "Pick a channel type."
    code, res = _call(port, "GET", "/api/oncall/nothing")
    assert code == 404


def test_settings_are_local_only(port, monkeypatch):
    monkeypatch.setenv("CCC_FF_ONCALL", "1")
    # A request that came through a reverse proxy or a tunnel is not this person at this machine.
    code, res = _call(port, "GET", "/api/oncall/status", headers={"X-Forwarded-For": "203.0.113.9"})
    assert code in (401, 403), res
    code, res = _call(port, "POST", "/api/oncall/config", {"queue": "SHOP"},
                      headers={"X-Forwarded-For": "203.0.113.9"})
    assert code in (401, 403), res
    assert _call(port, "GET", "/api/oncall/status")[1]["queue"] == ""


def test_the_flag_is_registered_and_off_by_default():
    from ccc_server import log_parse
    assert log_parse._PREVIEW_FLAGS["oncall"]["default"] is False
    assert server._feature_flag("oncall") in (True, False)
