"""Cloud relay <-> Tailscale/network-trust coexistence (Acceptance 17 + 18).

Boots the REAL local server.py (isolated HOME, ephemeral loopback port) and
proves the cloud relay is a parallel, opt-in channel that never widens local
network trust:

(a) cloud disabled (default config): NO outbound relay attempt occurs (a
    counting listener standing in for the relay sees zero connections over an
    observation window) while /api/sessions keeps working locally.
(b) enabling cloud via POST /api/cloud-relay/config from a localhost origin
    leaves network.json byte-for-byte unchanged, leaves ALLOWED_ORIGINS
    behavior unchanged (a foreign-Origin POST still 403s), and leaves
    GET /api/network-config output identical before/after.
(c) CCC_CLOUD_DISABLED=1 with an enabled+paired config produces no relay
    traffic at all.
(d) POST /api/cloud-relay/config from a non-localhost Origin is rejected 403.

Run as its own pytest invocation (same note as test_cloud_relay_e2e.py):

    python3 -m pytest tests/test_tailscale_coexistence.py -p no:cacheprovider
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud_relay_harness import LocalCCCFixture, wait_until

RELAY_QUIET_WINDOW_S = 5.0   # "no relay traffic" observation window


class CountingListener:
    """A bare TCP listener standing in for the relay endpoint. Any accepted
    connection counts as an outbound relay attempt (the client would be
    dialing this host:port). Connections are closed immediately."""

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(16)
        self.port = self._srv.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.connections = 0
        self._stop = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self):
        while not self._stop:
            try:
                cli, _ = self._srv.accept()
            except OSError:
                break
            self.connections += 1
            try:
                cli.close()
            except OSError:
                pass

    def stop(self):
        self._stop = True
        try:
            self._srv.close()
        except OSError:
            pass


def _seed_cloud_state(local: LocalCCCFixture, relay_url: str, enabled: bool):
    """Pre-write paired cloud state under the fixture's isolated HOME
    (~/.claude/command-center/cloud/) so the server's relay manager has a
    relay_url to dial if (and only if) policy allows."""
    cloud_dir = local.home / ".claude" / "command-center" / "cloud"
    cloud_dir.mkdir(parents=True, exist_ok=True)
    (cloud_dir / "config.json").write_text(json.dumps({
        "enabled": enabled,
        "relay_url": relay_url,
        "share_titles": True,
        "device_id": "dev-coexist-test",
        "account_email_masked": "a***@***.com",
        "account_ref": "acct-coexist-test",
        "disabled_reason": "",
    }, indent=2) + "\n")
    creds = cloud_dir / "credentials"
    creds.write_text(json.dumps({
        "device_id": "dev-coexist-test",
        "device_secret": "sk-ant-test-XXXX-not-a-real-secret",
    }))
    creds.chmod(0o600)


def _network_json_bytes(local: LocalCCCFixture):
    p = local.home / ".claude" / "command-center" / "network.json"
    return p.read_bytes() if p.exists() else None


def _seed_network_json(local: LocalCCCFixture):
    """Write a concrete network.json so 'unchanged byte-for-byte' compares
    real bytes, not mutual absence."""
    p = local.home / ".claude" / "command-center"
    p.mkdir(parents=True, exist_ok=True)
    (p / "network.json").write_text(json.dumps({
        "bind_host": None,
        "allowed_origins": [],
        "trust_tailnet": False,
    }, indent=2) + "\n")


def _assert_foreign_origin_post_403(local: LocalCCCFixture, path, body):
    status, obj = local.request(
        "POST", path, body,
        headers={"Origin": "http://evil.example.net:4444"})
    assert status == 403, (path, status, obj)


# ---------------------------------------------------------------------------
# (a) + (b) + (d): one real server boot, default (disabled) cloud config
# ---------------------------------------------------------------------------

def test_acceptance_17_18_disabled_then_localhost_enable():
    # Acceptance 17: cloud disabled -> zero outbound relay traffic, local API
    # fully functional. Acceptance 18: enabling from a localhost origin never
    # touches network trust (network.json, ALLOWED_ORIGINS, network-config).
    listener = CountingListener()
    local = LocalCCCFixture(cloud_disabled=False)  # default config, no env kill
    try:
        _seed_network_json(local)
        # Paired but DISABLED config pointing at our counting listener: the
        # strongest form of (a) — a relay_url exists, policy alone gates it.
        _seed_cloud_state(local, listener.base, enabled=False)
        local.start()

        # --- (a) local sessions work; zero relay attempts in the window ---
        sid = "dddddddd-0000-0000-0000-000000000004"
        local.add_session(sid, "Local-only work item")
        wait_until(lambda: any(
            (r.get("session_id") or r.get("id")) == sid
            for r in local.list_sessions_all()), timeout=25,
            msg="local session listed with cloud disabled")

        status, obj = local.request("GET", "/api/cloud-relay/status")
        assert status == 200
        assert obj["enabled"] is False
        assert obj["loop"]["state"] == "off"

        deadline = time.monotonic() + RELAY_QUIET_WINDOW_S
        while time.monotonic() < deadline:
            assert listener.connections == 0, \
                "relay dialed while cloud disabled"
            time.sleep(0.25)

        # --- (d) foreign-Origin POST to the cloud-relay config is 403 ---
        _assert_foreign_origin_post_403(
            local, "/api/cloud-relay/config", {"enabled": True})
        # Still disabled, still quiet.
        assert listener.connections == 0

        # --- (b) enable from a localhost origin; network trust unchanged ---
        net_before = _network_json_bytes(local)
        assert net_before is not None
        s, nc_before = local.request("GET", "/api/network-config")
        assert s == 200

        s, res = local.request("POST", "/api/cloud-relay/config",
                               {"enabled": True})
        assert s == 200 and res.get("ok") is True, (s, res)
        assert res.get("enabled") is True

        # The loop is live now and dials the (stub) relay — proof the enable
        # took effect through this endpoint and nothing else was needed.
        wait_until(lambda: listener.connections > 0, timeout=10,
                   msg="relay dialed after explicit localhost enable")

        # network.json is byte-for-byte identical.
        assert _network_json_bytes(local) == net_before

        # GET /api/network-config output identical before/after.
        s, nc_after = local.request("GET", "/api/network-config")
        assert s == 200
        assert nc_after == nc_before

        # ALLOWED_ORIGINS behavior unchanged: foreign-Origin POSTs still 403
        # on both a normal endpoint and the cloud-relay endpoints.
        _assert_foreign_origin_post_403(
            local, "/api/inject-input", {"session_id": "x", "text": "y"})
        _assert_foreign_origin_post_403(
            local, "/api/cloud-relay/config", {"enabled": False})

        # And a localhost-origin GET of the status shows enabled (sanity).
        s, obj = local.request("GET", "/api/cloud-relay/status")
        assert s == 200 and obj["enabled"] is True
    finally:
        local.cleanup()
        listener.stop()


# ---------------------------------------------------------------------------
# (c) CCC_CLOUD_DISABLED=1 beats an enabled + paired config
# ---------------------------------------------------------------------------

def test_acceptance_17_env_kill_switch_blocks_enabled_config():
    # Acceptance 17: the hard env switch wins over persisted config — an
    # enabled, paired device produces NO relay traffic under
    # CCC_CLOUD_DISABLED=1.
    listener = CountingListener()
    local = LocalCCCFixture(cloud_disabled=True)  # exports CCC_CLOUD_DISABLED=1
    try:
        _seed_cloud_state(local, listener.base, enabled=True)
        local.start()

        status, obj = local.request("GET", "/api/cloud-relay/status")
        assert status == 200
        assert obj["env_disabled"] is True
        assert obj["loop"]["state"] == "off"

        # Local API still answers while the relay stays silent.
        s, _ = local.request("GET", "/api/sessions?all=1")
        assert s == 200

        deadline = time.monotonic() + RELAY_QUIET_WINDOW_S
        while time.monotonic() < deadline:
            assert listener.connections == 0, \
                "relay dialed despite CCC_CLOUD_DISABLED=1"
            time.sleep(0.25)
    finally:
        local.cleanup()
        listener.stop()
