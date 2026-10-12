"""First-customer onboarding: relay URL safety, sign-up guidance, and honest
service-side disconnects (revoked / account deleted / credentials rejected).
Pure local state, no network."""

import pytest

from ccc_server import cloud_relay


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CCC_CLOUD_SERVICE_URL", raising=False)
    monkeypatch.delenv("CCC_CLOUD_DISABLED", raising=False)


# --- relay URL ---------------------------------------------------------------

@pytest.mark.parametrize("url,ok", [
    ("https://cloud.example.test", True),
    ("https://cloud.example.test:8443/", True),
    ("http://127.0.0.1:8790", True),
    ("http://localhost:8790", True),
    ("http://[::1]:8790", True),
    ("http://cloud.example.test", False),
    ("http://100.64.0.7:8790", False),        # tailnet IP is not loopback
    ("ftp://cloud.example.test", False),
    ("cloud.example.test", False),
    ("", False),
])
def test_relay_url_acceptable(url, ok):
    assert cloud_relay.relay_url_acceptable(url) is ok


def test_start_pairing_refuses_plaintext_remote_relay():
    mgr = cloud_relay.CloudRelayManager.__new__(cloud_relay.CloudRelayManager)
    res = mgr.start_pairing("ABCD1234", "http://cloud.example.test")
    assert res == {"ok": False, "error": "relay_url_insecure"}


@pytest.mark.parametrize("chosen,reported,expect", [
    ("https://cloud.example.test", "https://cloud.example.test/",
     "https://cloud.example.test"),
    ("https://cloud.example.test", "https://cloud.example.test:443",
     "https://cloud.example.test:443"),
    # A different origin in the service answer never redirects the secret.
    ("https://cloud.example.test", "https://evil.example.test",
     "https://cloud.example.test"),
    ("https://cloud.example.test", "http://cloud.example.test",
     "https://cloud.example.test"),
    ("https://cloud.example.test/", None, "https://cloud.example.test"),
    ("https://cloud.example.test", 42, "https://cloud.example.test"),
])
def test_pin_relay_url(chosen, reported, expect):
    assert cloud_relay._pin_relay_url(chosen, reported) == expect


def test_start_pairing_pins_chosen_origin(monkeypatch):
    mgr = cloud_relay.CloudRelayManager.__new__(cloud_relay.CloudRelayManager)
    mgr.ccc_version = "0"
    import threading
    mgr._lock = threading.RLock()

    class FakeClient:
        def __init__(self, url):
            pass

        def pair_complete(self, **kw):
            return {"device_id": "d1", "device_secret": "s1",
                    "relay_base_url": "https://elsewhere.example.test",
                    "account_email_masked": "a***@***.test"}

    monkeypatch.setattr(cloud_relay, "RelayClient", FakeClient)
    res = mgr.start_pairing("ABCD1234", "https://cloud.example.test")
    assert res["ok"] is True
    assert mgr._pending["relay_url"] == "https://cloud.example.test"


# --- sign-up guidance ---------------------------------------------------------

def test_signup_url_from_configured_default(monkeypatch):
    assert cloud_relay.signup_url() == ""
    monkeypatch.setenv("CCC_CLOUD_SERVICE_URL", "https://cloud.example.test/")
    assert cloud_relay.signup_url() == "https://cloud.example.test/signup"
    # A configured relay wins over the env default.
    assert cloud_relay.signup_url("https://other.example.test") == \
        "https://other.example.test/signup"
    # Never advertise a plaintext remote sign-up page.
    assert cloud_relay.signup_url("http://cloud.example.test") == ""


def test_status_exposes_signup_url(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_CLOUD_SERVICE_URL", "https://cloud.example.test")
    mgr = cloud_relay.CloudRelayManager(8090, ccc_version="0",
                                        state_dir=str(tmp_path))
    st = mgr.status()
    assert st["paired"] is False
    assert st["signup_url"] == "https://cloud.example.test/signup"


# --- service-side disconnect ---------------------------------------------------

def _paired_manager(tmp_path):
    mgr = cloud_relay.CloudRelayManager(8090, ccc_version="0",
                                        state_dir=str(tmp_path))
    cloud_relay.save_credentials(mgr.paths, {"device_id": "dev-1",
                                             "device_secret": "sec-1"})
    mgr.config.update({"enabled": True, "device_id": "dev-1",
                       "relay_url": "https://cloud.example.test",
                       "account_email_masked": "a***@***.test"})
    cloud_relay.save_config(mgr.paths, mgr.config)
    return mgr


@pytest.mark.parametrize("reason", ["device_revoked", "account_deleted",
                                    "credentials_rejected"])
def test_service_disconnect_wipes_dead_credential(tmp_path, reason):
    mgr = _paired_manager(tmp_path)
    mgr.mark_disabled(reason)
    assert cloud_relay.load_credentials(mgr.paths) is None
    st = mgr.status()
    assert st["paired"] is False and st["enabled"] is False
    assert st["disabled_reason"] == reason
    # Re-pair prefill survives; re-enabling without pairing is refused.
    assert st["relay_url"] == "https://cloud.example.test"
    assert mgr.set_config(enabled=True) == {"ok": False,
                                            "error": "not_paired"}
    # Idempotent: the second loop thread hitting the same refusal.
    mgr.mark_disabled(reason)
    assert cloud_relay.load_credentials(mgr.paths) is None


class _Client:
    def __init__(self, errors):
        self.errors = list(errors)


class _Mgr:
    def __init__(self):
        self.disabled = []

    def mark_disabled(self, reason):
        self.disabled.append(reason)


def test_credential_rejections_become_fatal_after_threshold():
    mgr = _Mgr()
    loop = cloud_relay.RelayLoop(mgr)
    err = cloud_relay.RelayError("bad_device_credentials", 401, "")
    n = cloud_relay.CREDENTIAL_REJECTS_FATAL
    for _ in range(n - 1):
        assert loop._handle_fatal(err) is False
    assert mgr.disabled == []
    assert loop._handle_fatal(err) is True
    assert mgr.disabled == ["credentials_rejected"]
    assert loop.state == "disabled"


def test_success_resets_credential_rejections():
    mgr = _Mgr()
    loop = cloud_relay.RelayLoop(mgr)
    err = cloud_relay.RelayError("bad_device_credentials", 401, "")
    for _ in range(cloud_relay.CREDENTIAL_REJECTS_FATAL - 1):
        loop._handle_fatal(err)
    loop._cred_rejects = 0          # what a successful push/poll does
    assert loop._handle_fatal(err) is False
    assert mgr.disabled == []


def test_network_errors_never_fatal():
    mgr = _Mgr()
    loop = cloud_relay.RelayLoop(mgr)
    err = cloud_relay.RelayError("network_error", 0, "")
    for _ in range(20):
        assert loop._handle_fatal(err) is False
    assert mgr.disabled == []
