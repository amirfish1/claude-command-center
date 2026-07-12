"""CCC Cloud Relay — end-to-end acceptance scenarios.

Each test boots the REAL hosted relay (ccc-cloud, in-process) and the REAL
device client (cloud_relay.py), wired either to the REAL local CCC server.py
(subprocess, isolated HOME + synthetic transcripts) or to a scriptable
FakeLocalAPI with a call journal. Nothing mocks the wire protocol.

Fixture library: tests/cloud_relay_harness.py. Run this file as its own pytest
invocation (see the harness module docstring re: the `server` name-collision).

    python3 -m pytest tests/test_cloud_relay_e2e.py -p no:cacheprovider

Scenario -> Acceptance item is noted per-test.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cloud_relay_harness import (
    CloudFixture, LocalCCCFixture, FakeLocalAPI, DeviceFixture, TogglableProxy,
    sign_in_browser, wait_until, make_envelope, import_cloud_relay, free_port,
)


# ---------------------------------------------------------------------------
# constant snapshot/restore so per-test window/requeue patches never leak
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_tunables():
    from cloud_relay_harness import _import_cloud
    cloud = _import_cloud()
    cr = import_cloud_relay()
    snap = {
        "H_online": cloud.H.DEVICE_ONLINE_WINDOW_S,
        "relay_stale": cloud.relay.SNAPSHOT_STALE_S,
        "relay_requeue": cloud.relay.INFLIGHT_REQUEUE_S,
        "cr_poll": cr.POLL_WAIT_S,
        "cr_state": cr.STATE_PUSH_INTERVAL_S,
        "cr_state_min": cr.STATE_PUSH_MIN_INTERVAL_S,
        "cr_bmin": cr.BACKOFF_MIN_S,
        "cr_bmax": cr.BACKOFF_MAX_S,
    }
    yield
    cloud.H.DEVICE_ONLINE_WINDOW_S = snap["H_online"]
    cloud.relay.SNAPSHOT_STALE_S = snap["relay_stale"]
    cloud.relay.INFLIGHT_REQUEUE_S = snap["relay_requeue"]
    cr.POLL_WAIT_S = snap["cr_poll"]
    cr.STATE_PUSH_INTERVAL_S = snap["cr_state"]
    cr.STATE_PUSH_MIN_INTERVAL_S = snap["cr_state_min"]
    cr.BACKOFF_MIN_S = snap["cr_bmin"]
    cr.BACKOFF_MAX_S = snap["cr_bmax"]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _device_online(browser, device_id):
    _s, o = browser.request("GET", "/v1/devices")
    return any(d["id"] == device_id and d["online"]
               for d in (o or {}).get("devices", []))


def _command_state(browser, request_id):
    _s, o = browser.request("GET", f"/v1/commands/{request_id}")
    return (o or {}).get("state"), o


def _send_command(browser, device_id, capability="session.send_input",
                  payload=None):
    if payload is None:
        payload = {"session_ref": "sess-1", "text": "hello from cloud"}
    s, o = browser.request(
        "POST", "/v1/commands",
        {"device_id": device_id, "capability": capability, "payload": payload},
        headers=browser.csrf_headers())
    return s, o


# ---------------------------------------------------------------------------
# Acceptance 2: Pairing e2e
# ---------------------------------------------------------------------------

def test_acceptance_2_pairing_e2e():
    # Acceptance 2: fresh account -> pair code -> RelayClient pair+confirm ->
    # loop starts -> device shows online in GET /v1/devices within 5s.
    cloud = CloudFixture()
    try:
        browser = sign_in_browser(cloud, "pair@example.com")
        fake = FakeLocalAPI()
        dev = DeviceFixture(cloud, browser, local_api=fake)
        try:
            assert dev.device_id  # persisted credentials + config after confirm
            wait_until(lambda: _device_online(browser, dev.device_id),
                       timeout=5, msg="device online within 5s")
            # Cloud recorded the one-time first-online event on first sync.
            assert cloud.event_count(dev.device_id, "device_first_online") == 1
            # Local config reflects the paired account, no secret in config.
            assert dev.mgr.config["account_email_masked"]
            assert "device_secret" not in open(dev.mgr.paths.config).read()
        finally:
            dev.cleanup()
    finally:
        cloud.cleanup()


# ---------------------------------------------------------------------------
# Acceptance 4: Session truth (REAL local server.py)
# ---------------------------------------------------------------------------

def test_acceptance_4_session_truth():
    # Acceptance 4: real LocalCCCFixture with 2 synthetic sessions of different
    # mtimes -> device syncs -> GET /v1/sessions shows both, correct recency
    # order, with machine/engine/state/freshness present.
    cloud = CloudFixture()
    local = LocalCCCFixture()
    try:
        local.start()
        now = time.time()
        local.add_session("aaaaaaaa-0000-0000-0000-000000000001",
                          "Older task", when=now - 600)
        local.add_session("bbbbbbbb-0000-0000-0000-000000000002",
                          "Newer task", when=now - 30)
        wait_until(lambda: len(local.list_sessions_all()) >= 2, timeout=20,
                   msg="both synthetic sessions discovered locally")

        browser = sign_in_browser(cloud, "truth@example.com")
        dev = DeviceFixture(cloud, browser, local_port=local.port)
        try:
            wait_until(lambda: _device_online(browser, dev.device_id),
                       timeout=8, msg="device online")

            def both_synced():
                _s, o = browser.request("GET", "/v1/sessions")
                for d in o.get("devices", []):
                    if d["device_id"] != dev.device_id:
                        continue
                    refs = [x.get("ref") for x in d["data"]]
                    return d if (
                        "aaaaaaaa-0000-0000-0000-000000000001" in refs and
                        "bbbbbbbb-0000-0000-0000-000000000002" in refs) else None
                return None

            entry = wait_until(both_synced, timeout=8,
                               msg="both sessions synced to cloud")
            data = entry["data"]
            refs = [x["ref"] for x in data]
            # Recency order: newer session first.
            assert refs.index("bbbbbbbb-0000-0000-0000-000000000002") < \
                refs.index("aaaaaaaa-0000-0000-0000-000000000001")
            # Freshness envelope present and honest.
            assert entry["observed_at"] and entry["stale"] is False
            assert entry["device_offline"] is False
            # Per-session truth fields present.
            s0 = data[0]
            assert s0["engine"] == "claude"
            assert s0.get("state")
            assert s0.get("machine_label")
            assert s0.get("recency")
        finally:
            dev.cleanup()
    finally:
        local.cleanup()
        cloud.cleanup()


# ---------------------------------------------------------------------------
# Acceptance 6: Remote input end-to-end
# ---------------------------------------------------------------------------

def test_acceptance_6_remote_input_e2e():
    # Acceptance 6: browser POST /v1/commands session.send_input -> device
    # executes against the local CCC (FakeLocalAPI here for a clean ok) ->
    # /v1/commands/<id> reaches ok with detail_code; browser saw the
    # queued -> delivered/ok progression; inject hit exactly once.
    cloud = CloudFixture()
    try:
        browser = sign_in_browser(cloud, "input@example.com")
        fake = FakeLocalAPI()
        fake.inject_response = {"ok": True}
        dev = DeviceFixture(cloud, browser, local_api=fake)
        try:
            wait_until(lambda: _device_online(browser, dev.device_id),
                       timeout=6, msg="device online")
            s, o = _send_command(browser, dev.device_id,
                                 payload={"session_ref": "sess-1",
                                          "text": "deploy the login page"})
            assert s == 200 and o["state"] == "queued", (s, o)
            rid = o["request_id"]

            seen = set()

            def progressed():
                st, _o = _command_state(browser, rid)
                seen.add(st)
                return st == "ok"

            wait_until(progressed, timeout=10, msg="command reaches ok")
            # Browser-facing progression: queued -> delivered -> ok (delivered
            # may be transient; ok is terminal).
            assert "ok" in seen
            _st, final = _command_state(browser, rid)
            assert final["detail_code"] == "delivered"
            # The device hit its own loopback inject endpoint exactly once.
            assert fake.inject_calls == 1
            assert fake.count("POST", "/api/inject-input") == 1
        finally:
            dev.cleanup()
    finally:
        cloud.cleanup()


def test_real_inject_input_reaches_local_server():
    # Companion to Acceptance 6: prove the REAL server.py /api/inject-input
    # endpoint is reachable and returns a structured result (the executor's
    # loopback target). A dormant synthetic session cannot truly resume with no
    # claude binary, so we assert the endpoint is hit and answers structurally.
    local = LocalCCCFixture()
    try:
        local.start()
        sid = "cccccccc-0000-0000-0000-000000000003"
        local.add_session(sid, "A dormant session")
        wait_until(lambda: any(
            (r.get("session_id") or r.get("id")) == sid
            for r in local.list_sessions_all()), timeout=20,
            msg="session discovered")
        status, obj = local.request(
            "POST", "/api/inject-input",
            {"session_id": sid, "text": "remote nudge", "mode": "send",
             "origin": "cloud-relay"})
        assert status == 200
        assert isinstance(obj, dict) and "ok" in obj
    finally:
        local.cleanup()


# ---------------------------------------------------------------------------
# Acceptance 7: Question answer
# ---------------------------------------------------------------------------

def test_acceptance_7_question_answer():
    # Acceptance 7: snapshot with a waiting question -> browser answers via
    # /v1/questions/answer -> device receives question.answer -> executes ->
    # waiting_question_answered event exists in the cloud events table.
    cloud = CloudFixture()
    try:
        browser = sign_in_browser(cloud, "question@example.com")
        fake = FakeLocalAPI()  # default session has a waiting question
        fake.answer_response = {"ok": True}
        dev = DeviceFixture(cloud, browser, local_api=fake)
        try:
            wait_until(lambda: _device_online(browser, dev.device_id),
                       timeout=6, msg="device online")

            # The waiting question should surface in the cloud read view.
            def question_synced():
                _s, o = browser.request("GET", "/v1/questions")
                for d in o.get("devices", []):
                    if d["device_id"] == dev.device_id and d["data"]:
                        return True
                return False

            wait_until(question_synced, timeout=6, msg="question synced")

            s, o = browser.request(
                "POST", "/v1/questions/answer",
                {"device_id": dev.device_id, "session_ref": "sess-1",
                 "question_id": "q-1", "answer": {"index": 0}},
                headers=browser.csrf_headers())
            assert s == 200, (s, o)
            rid = o["request_id"]

            wait_until(lambda: _command_state(browser, rid)[0] == "ok",
                       timeout=10, msg="answer command ok")
            assert fake.answer_calls == 1
            wait_until(
                lambda: cloud.event_count(
                    dev.device_id, "waiting_question_answered") == 1,
                timeout=5, msg="waiting_question_answered event")
        finally:
            dev.cleanup()
    finally:
        cloud.cleanup()


# ---------------------------------------------------------------------------
# Acceptance 8: Offline honesty
# ---------------------------------------------------------------------------

def test_acceptance_8_offline_honesty():
    # Acceptance 8: stop the device loop -> within the (shrunk) window
    # /v1/overview shows stale/offline; POST /v1/commands returns
    # device_offline:true and the command stays queued, never fake-delivered.
    cloud = CloudFixture()
    cloud.set_online_window(1)      # shrink 60s -> 1s
    cloud.set_snapshot_stale(1)     # shrink 90s -> 1s
    try:
        browser = sign_in_browser(cloud, "offline@example.com")
        fake = FakeLocalAPI()
        dev = DeviceFixture(cloud, browser, local_api=fake)
        wait_until(lambda: _device_online(browser, dev.device_id), timeout=6,
                   msg="device online first")
        dev.stop()  # kill the loop; last_seen ages past the 1s window

        # Wait for the device's last long-poll to unregister server-side.
        # Otherwise a command enqueued now gets claimed into that zombie poll
        # response (state 'inflight') even though the loop is dead — that is
        # the dual-delivery window the in-flight requeue handles, not the
        # offline path under test here.
        wait_until(
            lambda: dev.device_id not in cloud.cloud.relay._poll_registry,
            timeout=10, msg="zombie poll drained")

        def offline_in_overview():
            _s, o = browser.request("GET", "/v1/overview")
            for d in o.get("devices", []):
                if d["device_id"] == dev.device_id:
                    return d["stale"] and not d["online"]
            return False

        wait_until(offline_in_overview, timeout=6, msg="device shows offline")

        s, o = _send_command(browser, dev.device_id,
                             payload={"session_ref": "sess-1",
                                      "text": "while offline"})
        assert s == 200 and o.get("device_offline") is True, o
        rid = o["request_id"]
        # The command is honestly queued, NOT fake-delivered.
        assert cloud.command_state(rid) == "queued"
        assert fake.inject_calls == 0
        try:
            dev.cleanup()
        except Exception:
            pass
    finally:
        cloud.cleanup()


# ---------------------------------------------------------------------------
# Acceptance 9: Reconnect without duplicates
# ---------------------------------------------------------------------------

def test_acceptance_9_reconnect_without_duplicates():
    # Acceptance 9: sever proxy mid-loop, queue a command, restore -> device
    # reconnects with backoff, receives the command EXACTLY once (journal
    # count == 1), result ok.
    cloud = CloudFixture()
    # A delivery that raced a dead socket must re-queue quickly so the
    # reconnected device can pick it up within the test window.
    cloud.cloud.relay.INFLIGHT_REQUEUE_S = 1
    proxy = TogglableProxy("127.0.0.1", cloud.port)
    try:
        browser = sign_in_browser(cloud, "reconnect@example.com")
        fake = FakeLocalAPI()
        dev = DeviceFixture(cloud, browser, local_api=fake, relay_url=proxy.base)
        try:
            wait_until(lambda: _device_online(browser, dev.device_id),
                       timeout=8, msg="device online via proxy")

            proxy.sever()                       # simulate network loss
            time.sleep(0.5)
            s, o = _send_command(browser, dev.device_id,
                                 payload={"session_ref": "sess-1",
                                          "text": "queued during outage"})
            assert s == 200, (s, o)
            rid = o["request_id"]
            time.sleep(1.0)
            proxy.restore()                     # network back

            wait_until(lambda: cloud.command_state(rid) == "ok", timeout=20,
                       msg="command ok after reconnect")
            # Delivered and executed exactly once despite the dead-socket race.
            assert fake.inject_calls == 1
        finally:
            dev.cleanup()
    finally:
        proxy.stop()
        cloud.cleanup()


# ---------------------------------------------------------------------------
# Acceptance 10: Replay defense
# ---------------------------------------------------------------------------

def test_acceptance_10_replay_defense():
    # Acceptance 10: re-deliver the same envelope through the LIVE device's
    # executor -> it runs once, the second delivery returns the recorded result
    # (no re-execute); an expired envelope is rejected with status 'expired'.
    cloud = CloudFixture()
    try:
        browser = sign_in_browser(cloud, "replay@example.com")
        cr = import_cloud_relay()
        fake = FakeLocalAPI()
        dev = DeviceFixture(cloud, browser, local_api=fake)
        try:
            wait_until(lambda: _device_online(browser, dev.device_id),
                       timeout=6, msg="device online")

            env = make_envelope(cr, request_id="replay-rid-1", seq=1000,
                                text="run once")
            r1 = dev.executor.execute(env)
            r2 = dev.executor.execute(env)      # hostile/buggy re-delivery
            assert r1["status"] == "ok"
            assert r2 == r1                      # recorded result replayed
            assert fake.inject_calls == 1        # body ran exactly once

            # An expired envelope is fail-closed.
            expired = make_envelope(cr, request_id="replay-rid-expired",
                                    seq=1001, expires_delta=-500)
            re = dev.executor.execute(expired)
            assert re["status"] == "expired"
            assert re["error_code"] == "expired"
            assert fake.inject_calls == 1        # still exactly one execution
        finally:
            dev.cleanup()
    finally:
        cloud.cleanup()


# ---------------------------------------------------------------------------
# Acceptance 12: Revocation
# ---------------------------------------------------------------------------

def test_acceptance_12_revocation():
    # Acceptance 12: revoke device from the browser -> the device's next
    # poll/state gets 403 device_revoked -> loop stops, config marks
    # disabled_reason, no further requests (last_seen quiet for 2 poll
    # intervals); re-pair works fresh.
    cloud = CloudFixture()
    try:
        browser = sign_in_browser(cloud, "revoke@example.com")
        fake = FakeLocalAPI()
        dev = DeviceFixture(cloud, browser, local_api=fake)
        old_device_id = dev.device_id
        wait_until(lambda: _device_online(browser, old_device_id), timeout=6,
                   msg="device online")

        s, o = browser.request(
            "POST", f"/v1/devices/{old_device_id}/revoke",
            headers=browser.csrf_headers())
        assert s == 200 and o.get("ok") is True, (s, o)

        # The loop observes 403 device_revoked and stops, fail-closed.
        wait_until(lambda: dev.loop_state == "disabled", timeout=8,
                   msg="loop disabled after revoke")
        assert dev.mgr.config["disabled_reason"] == "device_revoked"
        assert dev.mgr.config["enabled"] is False

        # No further requests: last_seen stays quiet across 2 poll intervals.
        seen_a = cloud.device_last_seen(old_device_id)
        cr = import_cloud_relay()
        time.sleep(2 * cr.POLL_WAIT_S + 1)
        assert cloud.device_last_seen(old_device_id) == seen_a

        # Re-pair works fresh: a new device, new credentials, online again.
        new_device_id = dev.pair_and_start()
        assert new_device_id != old_device_id
        wait_until(lambda: _device_online(browser, new_device_id), timeout=6,
                   msg="re-paired device online")
        dev.cleanup()
    finally:
        cloud.cleanup()


# ---------------------------------------------------------------------------
# Acceptance 19: Restart resilience
# ---------------------------------------------------------------------------

def test_acceptance_19_restart_resilience():
    # Acceptance 19: restart the cloud on the SAME home+port -> device
    # reconnects, identity preserved (same device_id online), completed command
    # NOT re-executed (idempotency store persisted across the device restart:
    # last_seq + recorded result survive from idempotency.sqlite3).
    import tempfile
    port = free_port()
    home = tempfile.mkdtemp(prefix="ccc-cloud-persist-")
    cr = import_cloud_relay()
    state_dir = tempfile.mkdtemp(prefix="ccc-device-persist-")
    fake = FakeLocalAPI()
    cloud = CloudFixture(home=home, port=port)
    dev = None
    try:
        browser = sign_in_browser(cloud, "restart@example.com")
        dev = DeviceFixture(cloud, browser, local_api=fake, state_dir=state_dir)
        device_id = dev.device_id
        wait_until(lambda: _device_online(browser, device_id), timeout=6,
                   msg="device online")

        s, o = _send_command(browser, device_id,
                             payload={"session_ref": "sess-1", "text": "do it"})
        rid = o["request_id"]
        wait_until(lambda: _command_state(browser, rid)[0] == "ok", timeout=10,
                   msg="command ok")
        assert fake.inject_calls == 1
        old_seq = dev.mgr.store.get_last_seq()
        assert old_seq > 0
        assert dev.mgr.store.get_result(rid) is not None

        # Restart the device client on the SAME state_dir, and the cloud on the
        # SAME home+port.
        dev.stop()
        cloud.stop()
        cloud = CloudFixture(home=home, port=port)
        browser = sign_in_browser(cloud, "restart@example.com")
        dev.cloud = cloud
        dev.browser = browser
        dev.relay_url = cloud.base.rstrip("/")
        dev.restart_client()

        # Idempotency store survived the device restart.
        assert dev.mgr.store.get_last_seq() == old_seq
        assert dev.mgr.store.get_result(rid) is not None

        # Device identity preserved: same device_id comes back online.
        assert dev.mgr.config["device_id"] == device_id
        wait_until(lambda: _device_online(browser, device_id), timeout=8,
                   msg="same device online after restart")

        # A re-delivery of the completed command is NOT re-executed.
        env = make_envelope(cr, request_id=rid, seq=1, text="do it")
        res = dev.mgr.executor.execute(env)
        assert res["status"] == "ok"
        assert fake.inject_calls == 1           # still exactly one execution
    finally:
        if dev is not None:
            dev.cleanup()
        cloud.cleanup()
        import shutil
        shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------------------
# Acceptance 11: Tenant isolation live
# ---------------------------------------------------------------------------

def test_acceptance_11_tenant_isolation():
    # Acceptance 11: two accounts, two devices -> A's browser cannot command
    # B's device (404), and B's device never appears under A's read views.
    cloud = CloudFixture()
    try:
        browser_a = sign_in_browser(cloud, "tenant-a@example.com")
        browser_b = sign_in_browser(cloud, "tenant-b@example.com")
        fake_a = FakeLocalAPI()
        fake_b = FakeLocalAPI()
        dev_a = DeviceFixture(cloud, browser_a, local_api=fake_a)
        dev_b = DeviceFixture(cloud, browser_b, local_api=fake_b)
        try:
            wait_until(lambda: _device_online(browser_a, dev_a.device_id),
                       timeout=6, msg="device A online")
            wait_until(lambda: _device_online(browser_b, dev_b.device_id),
                       timeout=6, msg="device B online")

            # A commanding B's device is rejected as not_found (account-scoped).
            s, o = _send_command(browser_a, dev_b.device_id,
                                 payload={"session_ref": "sess-1", "text": "x"})
            assert s == 404 and o["error"] == "not_found", (s, o)
            assert fake_b.inject_calls == 0

            # A's read views never surface B's device.
            _s, ov = browser_a.request("GET", "/v1/overview")
            a_devices = {d["device_id"] for d in ov.get("devices", [])}
            assert dev_a.device_id in a_devices
            assert dev_b.device_id not in a_devices

            _s, sess = browser_a.request("GET", "/v1/sessions")
            a_sess_devices = {d["device_id"] for d in sess.get("devices", [])}
            assert dev_b.device_id not in a_sess_devices
        finally:
            dev_a.cleanup()
            dev_b.cleanup()
    finally:
        cloud.cleanup()
