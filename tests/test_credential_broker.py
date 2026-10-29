"""Credential boundary checks; no real credentials, external CLIs or services."""
import concurrent.futures
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from ccc_server import credential_broker as cb

FAKE = "  obvious-test-password-XXXX\n "


@pytest.fixture
def vault_dir(tmp_path, monkeypatch):
    from ccc_server import paths, vault
    monkeypatch.setattr(paths, "COMMAND_CENTER_STATE_DIR", tmp_path)
    monkeypatch.setattr(vault, "_vault_keychain_available", lambda: False)
    return tmp_path / "vault"


@pytest.fixture
def broker(vault_dir):
    instance = cb.Broker()
    yield instance
    instance.shutdown()


def request(instance, **changes):
    return instance.request(dict(name="example-service", reason="Test login", session_id="test-session", **changes))


def capability(created):
    return {k: created[k] for k in ("id", "token")}


def test_ephemeral_one_shot_and_metadata_allowlist(broker, vault_dir):
    created = request(broker)
    assert created["status"] == "pending"
    metadata = broker.pending()
    assert set(metadata[0]) == {"id", "name", "kind", "reason", "session_id", "store_only", "expires_at"}
    assert created["token"] not in json.dumps(metadata)
    assert broker.take(capability(created)) == {"status": "pending"}
    broker.resolve({"id": created["id"], "value": FAKE})
    assert broker.pending() == []
    assert not vault_dir.exists()
    assert broker.take(capability(created)) == {"status": "ready", "value": FAKE}
    with pytest.raises(cb.BrokerError) as error:
        broker.take(capability(created))
    assert error.value.status == 410
    assert not broker.requests


def test_wrong_token_does_not_consume_and_no_token_is_published(broker):
    created = request(broker)
    broker.resolve({"id": created["id"], "value": FAKE})
    assert created["token"] not in repr(broker.requests)
    for invalid in (None, "wrong-test-token", ["bad"], "x" * 129):
        with pytest.raises(cb.BrokerError) as error:
            broker.take({"id": created["id"], "token": invalid})
        assert error.value.status == 403
    assert broker.take(capability(created))["value"] == FAKE


def test_simultaneous_delivery_has_one_winner(broker):
    created = request(broker)
    broker.resolve({"id": created["id"], "value": FAKE})

    def take(_):
        try:
            return broker.take(capability(created))["value"]
        except cb.BrokerError as exc:
            return exc.status

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(take, range(8)))
    assert results.count(FAKE) == 1
    assert results.count(410) == 7


def test_deny_cancel_and_duplicate_answer(broker):
    created = request(broker)
    broker.resolve({"id": created["id"], "deny": True})
    with pytest.raises(cb.BrokerError) as error:
        broker.resolve({"id": created["id"], "value": FAKE})
    assert error.value.status == 409
    assert broker.take(capability(created)) == {"status": "denied"}
    created = request(broker)
    assert broker.take(capability(created), cancel=True) == {"status": "cancelled"}
    assert not broker.pending()


def test_expiry_purges_values_without_a_reader(vault_dir):
    instance = cb.Broker()
    try:
        created = request(instance, timeout=1)
        instance.resolve({"id": created["id"], "value": FAKE})
        deadline = time.monotonic() + 3
        while instance.requests and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not instance.requests
        assert not vault_dir.exists()
    finally:
        instance.shutdown()


def test_earlier_deadline_reschedules_and_stale_timer_cannot_duplicate(broker):
    request(broker, timeout=900)
    generation = broker._timer_generation
    created = request(broker, timeout=1)
    active = broker._expiry_timer
    broker._sweep(generation)
    assert broker._expiry_timer is active
    assert active.interval <= 1
    broker.take(capability(created), cancel=True)


def test_remember_goes_to_vault_survives_restart_and_forget_revokes(broker, vault_dir):
    from ccc_server import vault
    created = request(broker)
    broker.resolve({"id": created["id"], "value": FAKE, "remember": True})
    entry = vault.vault_get_entry("example-service")
    assert entry["source"] == {"broker_name": "example-service", "broker_kind": "password"}
    assert FAKE.strip() not in (vault_dir / "index.json").read_text()
    assert FAKE.strip() not in (vault_dir / "secrets.enc.json").read_text()
    assert not (vault_dir.parent / "secrets").exists()
    broker.shutdown()
    restarted = cb.Broker()
    try:
        reused = request(restarted)
        assert reused["status"] == "ready"
        assert restarted.take(capability(reused))["value"] == FAKE.strip()
        outstanding = request(restarted)
        restarted.forget("example-service")
        assert vault.vault_get_entry("example-service") is None
        with pytest.raises(cb.BrokerError):
            restarted.take(capability(outstanding))
        assert request(restarted)["status"] == "pending"
    finally:
        restarted.shutdown()


def test_kinds_are_separate_and_otp_is_never_persisted(broker):
    created = request(broker)
    broker.resolve({"id": created["id"], "value": FAKE, "remember": True})
    token = request(broker, kind="token")
    assert token["status"] == "pending"
    otp = request(broker, kind="otp")
    with pytest.raises(cb.BrokerError, match="cannot be remembered"):
        broker.resolve({"id": otp["id"], "value": "000000", "remember": True})
    broker.resolve({"id": otp["id"], "value": "000000"})
    assert broker.take(capability(otp))["value"] == "000000"
    assert request(broker, kind="otp")["status"] == "pending"


@pytest.mark.parametrize("patch", [
    {"name": "../escape"}, {"name": []}, {"reason": ""}, {"reason": "x" * 501},
    {"session_id": "bad/session"}, {"timeout": True}, {"timeout": 901}, {"timeout": 0},
    {"kind": "unknown"}, {"kind": []},
])
def test_invalid_request_fields_leave_no_state(broker, patch):
    payload = {"name": "example-service", "reason": "Test", **patch}
    with pytest.raises(cb.BrokerError):
        broker.request(payload)
    assert not broker.requests


@pytest.mark.parametrize("value", [None, "", 42, "x" * (cb.MAX_VALUE_BYTES + 1), "\0"])
def test_invalid_values_are_not_saved(broker, vault_dir, value):
    created = request(broker)
    with pytest.raises(cb.BrokerError):
        broker.resolve({"id": created["id"], "value": value, "remember": True})
    assert not vault_dir.exists()
    assert broker.take(capability(created)) == {"status": "pending"}


def test_vault_entry_not_written_by_broker_is_rejected(broker):
    from ccc_server import vault
    vault.vault_create_entry("example-service", "other-test-value-XXXX", kind="other")
    with pytest.raises(cb.BrokerError, match="invalid"):
        request(broker)


def test_request_cap_and_pending_poll_have_no_filesystem_work(broker, monkeypatch):
    monkeypatch.setattr(cb, "MAX_REQUESTS", 3)
    for _ in range(3):
        request(broker)
    with pytest.raises(cb.BrokerError) as error:
        request(broker)
    assert error.value.status == 429

    def forbidden(*args, **kwargs):
        raise AssertionError("pending poll touched filesystem")

    monkeypatch.setattr(Path, "iterdir", forbidden)
    monkeypatch.setattr(Path, "stat", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    for _ in range(10):
        assert len(broker.pending()) == 3


@pytest.fixture
def http_broker(broker, monkeypatch):
    # Exercise the real CCC handler (Origin/PIN gate order included), without
    # main(), background scanners, worker startup or live state writes.
    import server
    monkeypatch.setattr(cb, "_INSTANCE", broker)
    monkeypatch.setattr(server, "SLOW_REQ_MS", 0)
    monkeypatch.setattr(server, "ALLOWED_ORIGINS", ["https://phone.test"])
    monkeypatch.setattr(server.phone_access, "pin_gate_blocks", lambda *args: False)
    monkeypatch.setattr(server.phone_access, "live_extra_origins", lambda: [])
    monkeypatch.setattr(server.phone_access, "live_trust_tailnet", lambda: False)
    http = ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{http.server_port}"
    try:
        yield base, server
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def api(base, action, data=None, headers=None, method=None):
    raw = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(base + "/api/secrets/" + action, data=raw,
                                     headers={"Content-Type": "application/json", **(headers or {})},
                                     method=method)
    try:
        response = urllib.request.urlopen(request, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read()), response.headers


def test_http_lifecycle_no_caching_and_no_logs(http_broker, capfd):
    base, server = http_broker
    status, created, _ = api(base, "request", {"name": "example-service", "reason": "Test"})
    assert status == 200
    status, metadata, headers = api(base, "pending")
    assert status == 200 and "token" not in json.dumps(metadata)
    assert headers["Cache-Control"] == "no-store"
    assert api(base, "resolve", {"id": created["id"], "value": FAKE}, {"Origin": base})[0] == 200
    status, result, headers = api(base, "take", capability(created))
    assert status == 200 and result["value"] == FAKE
    assert headers["Cache-Control"] == "no-store"
    assert api(base, "take", capability(created))[0] == 410
    assert api(base, "missing?token=obvious-test-secret-XXXX")[0] == 400
    logs = capfd.readouterr()
    assert "obvious-test" not in logs.out + logs.err
    assert "/api/secrets/" not in logs.out + logs.err


def test_http_browser_remote_origin_and_pin_boundaries(http_broker, monkeypatch):
    base, server = http_broker
    _, created, _ = api(base, "request", {"name": "example-service", "reason": "Test"})
    for action in ("request", "take", "cancel", "forget"):
        assert api(base, action, capability(created), {"Origin": base})[0] == 403
        assert api(base, action, capability(created), {"X-Forwarded-For": "192.0.2.10"})[0] == 403
    payload = {"id": created["id"], "value": FAKE}
    assert api(base, "resolve", payload, {"Origin": "https://untrusted.test"})[0] == 403
    assert api(base, "resolve", payload, {"Origin": base, "X-Forwarded-For": "192.0.2.10"})[0] == 403
    monkeypatch.setattr(server.phone_access, "pin_gate_blocks", lambda *args: True)
    assert api(base, "resolve", payload, {"Origin": "https://phone.test"})[0] == 401
    assert api(base, "pending")[0] == 401
    monkeypatch.setattr(server.phone_access, "pin_gate_blocks", lambda *args: False)
    assert api(base, "resolve", payload, {"Origin": "https://phone.test", "X-Forwarded-For": "192.0.2.10"})[0] == 200
    assert api(base, "take", capability(created))[1]["value"] == FAKE


def test_http_malformed_body_limits_and_metadata_only_get(http_broker):
    base, _ = http_broker
    assert api(base, "request", ["not-object"])[0] == 400
    assert api(base, "request", {"name": "example", "reason": "Test"}, {"Content-Type": "text/plain"})[0] == 415
    assert api(base, "request", {"value": "x" * (cb.MAX_BODY_BYTES + 1)})[0] == 413
    assert api(base, "take")[0] == 404
    assert api(base, "missing", {})[0] == 404


def load_cli():
    filename = Path(__file__).resolve().parents[1] / "ccc"
    loader = importlib.machinery.SourceFileLoader("credential_test_cli", str(filename))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_cli_refuses_unintentional_output_and_terminal(http_broker, monkeypatch, capsys):
    base, _ = http_broker
    cli = load_cli()
    assert cli.main(["--server", base, "secret", "request", "example", "--reason", "Test", "--kind", "otp"]) == 2
    assert "cannot be stored" in capsys.readouterr().err
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert cli.main(["--server", base, "secret", "request", "example", "--reason", "Test", "--return-key"]) == 2
    assert "never print credentials" in capsys.readouterr().err


@pytest.mark.parametrize("base", ["https://remote.test", "http://remote.test", "http://127.0.0.1/user", "http://test@127.0.0.1", "http://127.0.0.1?query=1"])
def test_cli_local_transport_only(base):
    with pytest.raises(ValueError):
        load_cli()._secret_post(base, "request", {})


def test_cli_delivers_exact_value_without_stderr_echo(http_broker, broker, capsys):
    base, _ = http_broker
    created = request(broker)
    broker.resolve({"id": created["id"], "value": FAKE, "remember": True})
    cli = load_cli()
    assert cli.main(["--server", base, "secret", "request", "example-service", "--reason", "Test", "--stdout"]) == 0
    output = capsys.readouterr()
    assert output.out == FAKE.strip()
    assert FAKE not in output.err
    assert cli.main(["--server", base, "secret", "forget", "example-service"]) == 0
    assert request(broker)["status"] == "pending"


def test_store_only_saves_to_vault_and_agent_gets_only_the_entry_name(broker):
    from ccc_server import vault
    created = request(broker, store_only=True)
    assert broker.pending()[0]["store_only"] is True
    broker.resolve({"id": created["id"], "value": FAKE})
    assert vault.vault_get_value("example-service") == FAKE.strip()
    assert broker.requests[created["id"]]["value"] is None
    taken = broker.take(capability(created))
    assert taken == {"status": "stored", "entry": "example-service"}
    assert FAKE.strip() not in json.dumps(taken)


def test_store_only_always_prompts_so_a_saved_value_can_be_replaced(broker):
    from ccc_server import vault
    first = request(broker, store_only=True)
    broker.resolve({"id": first["id"], "value": FAKE})
    broker.take(capability(first))
    again = request(broker, store_only=True)
    assert again["status"] == "pending"
    broker.resolve({"id": again["id"], "value": "replacement-test-value-XXXX"})
    assert vault.vault_get_value("example-service") == "replacement-test-value-XXXX"


@pytest.mark.parametrize("patch", [{"kind": "otp"}, {"store_only": "yes"}])
def test_store_only_rejects_otp_and_non_boolean(broker, patch):
    with pytest.raises(cb.BrokerError):
        request(broker, **{"store_only": True, **patch})
    assert not broker.requests


def test_cli_defaults_to_store_only_and_prints_no_value(http_broker, broker, capsys):
    from ccc_server import vault
    base, _ = http_broker
    cli = load_cli()
    import threading as _t
    def answer():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            pending = broker.pending()
            if pending:
                broker.resolve({"id": pending[0]["id"], "value": FAKE})
                return
            time.sleep(0.02)
    thread = _t.Thread(target=answer)
    thread.start()
    assert cli.main(["--server", base, "secret", "request", "example-service", "--reason", "Test", "--timeout", "10"]) == 0
    thread.join()
    output = capsys.readouterr()
    assert output.out == "saved to Vault: example-service\n"
    assert FAKE.strip() not in output.out + output.err
    assert vault.vault_get_value("example-service") == FAKE.strip()
