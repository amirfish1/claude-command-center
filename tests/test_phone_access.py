"""Phone access (ccc_server/phone_access.py) and the QR encoder behind it.

Pure pieces only: parsing Tailscale's JSON, choosing a serve port without
touching other entries, classifying failures, the PIN gate, and the QR
matrix. The live server path (routes, hot-reloaded origins, PIN gate over
HTTP, the federation route) is covered by test_phone_access_two_node.py.
"""

import hashlib
import json

import pytest

from ccc_server import phone_access as pa
from ccc_server import qrcode


# -- QR ----------------------------------------------------------------------

def _bits(matrix):
    return "".join("1" if c else "0" for row in matrix for c in row)


# Golden values produced by libqrencode (`qrencode -8 -l M -m 0 -t ASCII`).
# Its penalty scoring differs slightly from the spec's, so it may pick a
# different (equally valid) mask: compare against every mask for the first,
# and the auto-selected mask for the second, where both agree.
QR_GOLDEN = {
    "https://vm-node-01.tail1234.ts.net:8443/":
        (29, "8908d1617a263e2999980e23c92fe53fa45a8b2bfd1f14740aeb8deea4c1d79d"),
    "https://dev-laptop-machine0.tail56789a.ts.net:10000/":
        (33, "56f2c33f5ebeb5c8a0cc90f0d592d3042da6a9dcc6f1ec0ad29adba0d86678ce"),
}


def test_qr_matches_libqrencode_for_some_mask():
    text = "https://vm-node-01.tail1234.ts.net:8443/"
    size, digest = QR_GOLDEN[text]
    digests = {hashlib.sha256(_bits(qrcode.encode(text, mask=m)).encode()).hexdigest()
               for m in range(8)}
    assert len(qrcode.encode(text)) == size
    assert digest in digests


def test_qr_auto_mask_matches_libqrencode():
    text = "https://dev-laptop-machine0.tail56789a.ts.net:10000/"
    size, digest = QR_GOLDEN[text]
    m = qrcode.encode(text)
    assert len(m) == size
    assert hashlib.sha256(_bits(m).encode()).hexdigest() == digest


def test_qr_version_grows_and_svg_is_self_contained():
    assert len(qrcode.encode("x")) == 21
    assert len(qrcode.encode("x" * 300)) > 57  # version info region (v7+)
    svg = qrcode.to_svg("https://a.ts.net/")
    assert svg.startswith("<svg") and "<script" not in svg
    with pytest.raises(ValueError):
        qrcode.encode("x" * 5000)


# -- Tailscale JSON ------------------------------------------------------------

STATUS_RUNNING = {
    "Version": "1.102.1",
    "BackendState": "Running",
    "AuthURL": "",
    "Self": {"DNSName": "Laptop.example-tailnet.ts.net.", "UserID": 7,
             "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"]},
    "User": {"7": {"LoginName": "someone@example.com"}},
    "CurrentTailnet": {"Name": "someone@example.com", "MagicDNSEnabled": True},
    "CertDomains": ["laptop.example-tailnet.ts.net"],
}


def test_parse_status_running():
    st = pa.parse_tailscale_status(STATUS_RUNNING)
    assert st["running"] and st["logged_in"]
    assert st["hostname"] == "laptop.example-tailnet.ts.net"
    assert st["login_name"] == "someone@example.com"
    assert st["https_certs"] is True
    assert st["ips"][0] == "100.101.102.103"


def test_parse_status_needs_login():
    st = pa.parse_tailscale_status({"BackendState": "NeedsLogin",
                                    "AuthURL": "https://login.tailscale.com/a/abc"})
    assert not st["running"] and not st["logged_in"] and st["needs_login"]
    assert st["auth_url"].endswith("/abc")
    assert pa.parse_tailscale_status(None)["hostname"] == ""


SERVE_BUSY = {
    "TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
    "Web": {
        "laptop.example-tailnet.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18765"}}},
        "laptop.example-tailnet.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://localhost:9000"}}},
    },
}


def test_plan_skips_ports_used_by_others_and_reports_them():
    serve = pa.parse_serve_status(SERVE_BUSY)
    plan = pa.plan_serve(serve, 8090)
    assert plan["action"] == "create"
    assert plan["https_port"] == 10000
    assert plan["target"] == "http://127.0.0.1:8090"
    assert {c["https_port"] for c in plan["conflicts"]} == {443, 8443}


def test_plan_reuses_an_existing_entry_for_ccc():
    serve = pa.parse_serve_status(SERVE_BUSY)
    # localhost:9000 is normalised to 127.0.0.1:9000, so it is CCC on 9000.
    plan = pa.plan_serve(serve, 9000)
    assert plan == {"action": "reuse", "https_port": 8443,
                    "target": "http://127.0.0.1:9000", "conflicts": []}


def test_plan_none_when_every_candidate_is_taken():
    tcp = {str(p): {"HTTPS": True} for p in pa._CANDIDATE_HTTPS_PORTS}
    plan = pa.plan_serve(pa.parse_serve_status({"TCP": tcp}), 8090)
    assert plan["action"] == "none" and plan["https_port"] is None


def test_phone_origin_omits_443():
    assert pa.phone_origin("Node.ts.net.", 443) == "https://node.ts.net"
    assert pa.phone_origin("node.ts.net", 8443) == "https://node.ts.net:8443"


def test_classify_serve_errors():
    e = pa.classify_serve_error(
        "Serve is not enabled on your tailnet.\nTo enable, visit:\n\n"
        "         https://login.tailscale.com/f/serve?node=abc\n")
    assert e["error"] == "serve_not_enabled"
    assert e["action_url"] == "https://login.tailscale.com/f/serve?node=abc"
    assert pa.classify_serve_error("Access denied: serve config denied")["error"] == "needs_operator"
    assert pa.classify_serve_error("boom")["error"] == "serve_failed"


def test_classify_test_failures():
    assert pa.classify_test_failure(None, None, "[Errno 8] nodename nor servname provided")["error"] == "dns"
    assert pa.classify_test_failure(None, None, "timed out")["error"] == "timeout"
    assert pa.classify_test_failure(None, None, "[Errno 61] Connection refused")["error"] == "tailscale_down"
    assert pa.classify_test_failure(
        403, {"error": "cross-origin POST rejected", "origin": "https://x.ts.net"})["error"] == "cross_origin"
    assert pa.classify_test_failure(502, "")["error"] == "serve_backend_down"
    assert pa.classify_test_failure(200, {"nonce": "other"})["error"] == "serve_conflict"


# -- PIN gate ------------------------------------------------------------------

@pytest.fixture
def state(tmp_path, monkeypatch):
    path = tmp_path / "phone-access.json"
    monkeypatch.setenv("CCC_PHONE_ACCESS_FILE", str(path))
    pa._pin_fail_times.clear()
    yield path
    pa._pin_fail_times.clear()


LOCAL = {"Host": "127.0.0.1:8090"}
VIA_SERVE = {"Host": "laptop.example-tailnet.ts.net:10000", "X-Forwarded-For": "100.64.0.9"}


def test_remote_detection():
    assert not pa.is_remote_request("127.0.0.1", LOCAL)
    assert not pa.is_remote_request("::1", LOCAL)
    assert pa.is_remote_request("127.0.0.1", VIA_SERVE)
    assert pa.is_remote_request("127.0.0.1", {"Cf-Connecting-Ip": "1.2.3.4"})
    assert pa.is_remote_request("192.168.1.20", LOCAL)
    assert pa.is_remote_request("127.0.0.1", {"Host": "x.ts.net"})


def test_pin_gate_only_for_remote_and_only_when_set(state):
    assert not pa.pin_gate_blocks("/", "127.0.0.1", VIA_SERVE)  # no PIN yet
    assert pa.set_pin("123")["ok"] is False
    assert pa.set_pin("482913")["ok"]
    assert oct(state.stat().st_mode & 0o777) == "0o600"
    assert "482913" not in state.read_text()
    assert pa.pin_gate_blocks("/", "127.0.0.1", VIA_SERVE)
    assert not pa.pin_gate_blocks("/", "127.0.0.1", LOCAL)
    for exempt in pa.PIN_EXEMPT_PATHS:
        assert not pa.pin_gate_blocks(exempt, "127.0.0.1", VIA_SERVE)

    assert pa.unlock("000000")["error"] == "wrong_pin"
    ok = pa.unlock("482913")
    assert ok["ok"] and ok["token"]
    assert ok["token"] not in state.read_text()  # stored hashed
    cookie = {**VIA_SERVE, "Cookie": f"a=b; {pa.PHONE_PIN_COOKIE}={ok['token']}"}
    assert not pa.pin_gate_blocks("/api/sessions", "127.0.0.1", cookie)

    # A new PIN signs every phone out; clearing opens the gate again.
    pa.set_pin("777777")
    assert pa.pin_gate_blocks("/api/sessions", "127.0.0.1", cookie)
    pa.clear_pin()
    assert not pa.pin_gate_blocks("/api/sessions", "127.0.0.1", VIA_SERVE)


def test_unlock_is_rate_limited(state):
    pa.set_pin("482913")
    for _ in range(pa._PIN_MAX_FAILS_PER_MIN):
        assert pa.unlock("bad-guess")["error"] == "wrong_pin"
    # Even the right PIN is refused while the window is hot.
    assert pa.unlock("482913")["error"] == "rate_limited"


def test_state_origin_feeds_live_allowlist(state, monkeypatch):
    monkeypatch.setattr(pa, "_network_file_live", lambda: ([], False))
    assert pa.live_extra_origins() == []
    state.write_text(json.dumps({"serve": {"origin": "https://laptop.example-tailnet.ts.net:10000"}}))
    assert pa.live_extra_origins() == ["https://laptop.example-tailnet.ts.net:10000"]


def test_rebinding_style_host_on_loopback_is_remote():
    assert pa.is_remote_request("127.0.0.1", {"Host": "evil.example.com"})
    assert not pa.is_remote_request("::1", {"Host": "[::1]:8090"})
    assert not pa.is_remote_request("127.0.0.1", {"Host": "localhost:8090"})


def test_concurrent_pin_guesses_cannot_outrun_the_limit(state):
    import threading
    pa.set_pin("482913")
    results = []

    def guess():
        results.append(pa.unlock("bad-guess").get("error"))
    ts = [threading.Thread(target=guess) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("wrong_pin") <= pa._PIN_MAX_FAILS_PER_MIN


def test_enable_refuses_funnel_exposed_entry(state, monkeypatch):
    monkeypatch.setattr(pa, "tailscale_status", lambda max_age_s=3.0: {
        "installed": True, "running": True, "hostname": "n.ts.net", "ips": []})
    monkeypatch.setattr(pa, "serve_status", lambda: {"ok": True, "funnel": ["n.ts.net:443"],
        "occupied_ports": [443], "entries": [{"host": "n.ts.net", "https_port": 443,
        "path": "/", "proxy": "http://127.0.0.1:8090", "target": "http://127.0.0.1:8090"}]})
    r = pa.enable(8090)
    assert r["ok"] is False and r["error"] == "funnel_on"


def test_tailscale_probe_reports_installed_state(monkeypatch, tmp_path):
    """First-run step probe: cheap, cached Tailscale status, no serve/QR work."""
    import stat
    import server  # noqa: F401  (ccc_server resolves state/flags through it)
    from ccc_server import phone_access as pa
    monkeypatch.setattr(pa, "state_file", lambda: tmp_path / "phone-access.json")
    monkeypatch.setenv("CCC_TAILSCALE_BIN", str(tmp_path / "missing"))
    pa._ts_status_cache.update({"ts": 0, "data": None})
    body, code = pa.phone_access_handle("tailscale", {})
    assert code == 200 and body["ok"] and body["tailscale"]["installed"] is False
    fake = tmp_path / "tailscale"
    fake.write_text('#!/bin/sh\necho \'{"BackendState":"NeedsLogin","AuthURL":"https://login.tailscale.com/a/x"}\'\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CCC_TAILSCALE_BIN", str(fake))
    pa._ts_status_cache.update({"ts": 0, "data": None})
    body, _ = pa.phone_access_handle("tailscale", {})
    assert body["tailscale"]["installed"] and body["tailscale"]["needs_login"]
    assert "qr_svg" not in body and "serve" not in body


# -- conversation-reader serve entry (CCC-143) ---------------------------------

def _entry(port, target):
    return {"host": "n.ts.net", "https_port": port, "path": "/",
            "proxy": target, "target": target}


def test_reader_plan_creates_reuses_or_leaves_other_entries_alone():
    t = "http://127.0.0.1:18090"
    assert pa.plan_reader_serve({"entries": []}, 18090)["action"] == "create"
    assert pa.plan_reader_serve({"entries": [_entry(18090, t)]}, 18090)["action"] == "reuse"
    other = {"entries": [_entry(18090, "http://127.0.0.1:9999")], "occupied_ports": [18090]}
    assert pa.plan_reader_serve(other, 18090)["action"] == "taken"
    assert pa.plan_reader_serve({"entries": [], "occupied_ports": [18090]}, 18090)["action"] == "taken"
    assert pa.plan_reader_serve({"entries": []}, None)["action"] == "none"


def test_enable_adds_reader_entry_and_disable_removes_only_ours(state, monkeypatch):
    calls = []
    entries = [_entry(443, "http://127.0.0.1:8090")]
    monkeypatch.setenv("CCC_CONV_READER_PORT", "")
    monkeypatch.setattr(pa, "tailscale_status", lambda max_age_s=3.0: {
        "installed": True, "running": True, "hostname": "n.ts.net", "ips": []})
    monkeypatch.setattr(pa, "serve_status", lambda: {
        "ok": True, "funnel": [], "occupied_ports": [e["https_port"] for e in entries],
        "entries": list(entries)})

    def fake_run(args, timeout=10.0):
        calls.append(args)
        if args[:2] == ["serve", "--bg"]:
            port = int(args[2].split("=")[1])
            entries.append(_entry(port, args[3]))
        return 0, "", ""
    monkeypatch.setattr(pa, "_run_tailscale", fake_run)

    r = pa.enable(8090)
    assert r["ok"] and r["reader"]["ok"] and r["reader"]["created_by"] == "ccc"
    assert ["serve", "--bg", "--https=18090", "http://127.0.0.1:18090"] in calls
    assert pa.load_state()["reader"]["https_port"] == 18090

    pa.disable()
    assert ["serve", "--https=18090", "off"] in calls
    # The dashboard entry existed before CCC: left alone.
    assert ["serve", "--https=443", "off"] not in calls
    assert "reader" not in pa.load_state()


def test_reader_entry_only_added_when_phone_access_is_on(state, monkeypatch):
    monkeypatch.setattr(pa, "serve_status", lambda: (_ for _ in ()).throw(AssertionError))
    assert pa.ensure_reader_serve_if_enabled(8090) is None
