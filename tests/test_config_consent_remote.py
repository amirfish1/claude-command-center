"""Config approvals use the same trust boundary as other dashboard actions."""

import http.server
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import server


TAILNET_ORIGIN = "http://100.100.1.2:8090"
PROXY_ORIGIN = "https://node.example-tailnet.ts.net"


@pytest.fixture
def consent_http(tmp_path, monkeypatch):
    cc = server.config_consent
    home = tmp_path / "home"
    home.mkdir()
    ctx = cc.Ctx(
        home=home,
        state_dir=home / ".claude" / "command-center",
        ccc_root=Path(__file__).resolve().parents[1],
        hook_scripts_dir=home / ".claude" / "command-center" / "hooks",
    )
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir()
    original = '{"model": "test-model", "permissions": {"allow": []}}\n'
    settings.write_text(original)
    monkeypatch.setattr(cc, "default_ctx", lambda: ctx)
    monkeypatch.setattr(server, "ALLOWED_ORIGINS", [TAILNET_ORIGIN, PROXY_ORIGIN])
    monkeypatch.setattr(server, "RUNTIME_NETWORK_INFO", {})
    monkeypatch.setattr(server, "NETWORK_CONFIG_FILE", tmp_path / "network.json")
    monkeypatch.setenv("CCC_PHONE_ACCESS_FILE", str(tmp_path / "phone-access.json"))
    monkeypatch.delenv("CCC_PROXY_SECRET", raising=False)
    monkeypatch.delenv("CCC_PROXY_SECRET_FILE", raising=False)
    monkeypatch.delenv("CCC_SKIP_SKILL_INSTALL", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{httpd.server_port}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            response = urllib.request.urlopen(req, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            request.last_headers = response.headers
            return response.status, json.load(response)

    yield request, ctx, settings, original
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize("headers", [
    {"Origin": TAILNET_ORIGIN, "Host": "100.100.1.2:8090"},
    {"Origin": PROXY_ORIGIN, "Host": "node.example-tailnet.ts.net",
     "X-Forwarded-For": "100.64.0.9", "X-Forwarded-Proto": "https"},
    {"Origin": "http://localhost:8090"},
])
def test_approve_all_and_revoke_preserve_other_settings(consent_http, headers):
    request, ctx, settings, original = consent_http
    status, view = request("/api/config-consent", headers=headers)
    assert status == 200
    decisions = {i["id"]: "approve" for i in view["items"] if i["needs_review"]}
    assert "claude-hooks" in decisions
    status, result = request("/api/config-consent/decide", {"decisions": decisions}, headers)
    assert status == 200, result
    assert result["ok"]
    assert set(result["results"]) == set(decisions)
    assert json.loads(settings.read_text())["model"] == "test-model"
    assert "hooks" in json.loads(settings.read_text())
    assert list((ctx.state_dir / "config-backups").rglob("settings.json"))
    status, result = request("/api/config-consent/revoke", {}, headers)
    assert status == 200, result
    assert result["ok"]
    assert settings.read_text() == original


@pytest.mark.parametrize("path,body", [
    ("/api/config-consent/decide", {"decisions": {"claude-hooks": "approve"}}),
    ("/api/config-consent/revoke", {}),
    ("/api/config-consent/notice-ack", {}),
])
def test_remote_consent_keeps_origin_pin_and_proxy_secret_gates(consent_http, monkeypatch, path, body):
    request, ctx, settings, original = consent_http
    headers = {"Origin": TAILNET_ORIGIN, "Host": "100.100.1.2:8090"}
    status, _ = request(path, body, {**headers, "Origin": "https://untrusted.example"})
    assert status == 403
    assert settings.read_text() == original

    assert server.phone_access.set_pin("482913")["ok"]
    status, result = request(path, body, headers)
    assert status == 401
    assert result["pin_required"]
    assert settings.read_text() == original
    status, result = request("/api/phone-access/unlock", {"pin": "482913"}, headers)
    assert status == 200, result
    headers["Cookie"] = request.last_headers["Set-Cookie"].split(";", 1)[0]
    status, _ = request(path, body, {**headers, "Origin": "https://untrusted.example"})
    assert status == 403

    monkeypatch.setenv("CCC_PROXY_SECRET", "test-proxy-secret")
    status, _ = request(path, body, headers)
    assert status == 401
    assert settings.read_text() == original
    headers["X-CCC-Proxy-Secret"] = "test-proxy-secret"
    status, result = request(path, body, headers)
    assert status == 200, result
    assert result["ok"]


def test_remote_config_approval_does_not_grant_network_admin(consent_http):
    request, _, _, _ = consent_http
    headers = {"Origin": TAILNET_ORIGIN, "Host": "100.100.1.2:8090"}
    status, _ = request("/api/network-config", {}, headers)
    assert status == 403
    status, _ = request("/api/phone-access/disable", {}, headers)
    assert status == 403
