"""On-call HTTP surface: setup as a stranger does it, with nothing preconfigured.

The Telegram and uptime checks talk to a small local HTTP server, so the real
channel and source code runs end to end with no account and no network.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ccc_server.oncall import api
from ccc_server.oncall import engine as engine_mod
from ccc_server.oncall import store as st
from ccc_server.oncall import wt as wtmod

TOKEN = "110201543:TEST-not-a-real-token"


class FakeWT:
    def __init__(self):
        self.added, self.configured, self.policies = [], [], []
        self.installed = True

    def available(self):
        return self.installed

    def add(self, queue, *, title, text, dedupe_key="", url="", kind="bug"):
        self.added.append((queue, title, text))
        return f"{queue}-{len(self.added)}"

    def configure_queue(self, queue, repo_path="", workers=1):
        self.configured.append((queue, repo_path))

    def policy_set(self, queue, enabled):
        self.policies.append((queue, enabled))

    def decisions(self, queue):
        return [{"ref": f"{queue}-3", "state": "applied", "reason": "git revert abc1234"}]

    def blocked(self, queue):
        return []

    def ls(self, queue, status="active", limit=200):
        return []

    def find(self, ref):
        return None


class Vault:
    """The three Vault calls On-call makes, held in memory."""

    class VaultError(ValueError):
        pass

    def __init__(self):
        self.entries = {}

    def vault_get_entry(self, name):
        return {"name": name} if name in self.entries else None

    def vault_create_entry(self, name, value, **_kw):
        self.entries[name] = value

    def vault_update_entry(self, name, value=None, **_kw):
        self.entries[name] = value

    def vault_get_value(self, name):
        return self.entries.get(name, "")


class _Telegram(BaseHTTPRequestHandler):
    updates = []
    sent = []

    def log_message(self, *_a):
        pass

    def _reply(self, payload, status=200):
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self._reply({"status": "ok"})
        else:
            self._reply({"status": "down"}, 503)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if not self.path.startswith(f"/bot{TOKEN}/"):
            self._reply({"ok": False, "description": "Unauthorized"}, 401)
            return
        method = self.path.rsplit("/", 1)[-1]
        if method == "getMe":
            self._reply({"ok": True, "result": {"username": "example_oncall_bot"}})
        elif method == "getUpdates":
            self._reply({"ok": True, "result": type(self).updates})
        elif method == "sendMessage":
            type(self).sent.append(body)
            self._reply({"ok": True, "result": {"message_id": len(type(self).sent)}})
        else:
            self._reply({"ok": False, "description": "no such method"}, 404)


@pytest.fixture
def web():
    _Telegram.updates, _Telegram.sent = [], []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Telegram)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    vault = Vault()
    import ccc_server
    monkeypatch.setattr(ccc_server, "vault", vault, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "ccc_server.vault", vault)
    wt = FakeWT()
    eng = engine_mod.Engine(tmp_path / "oncall", wt=wt, flag_on=lambda: True)

    class Rig:
        pass

    r = Rig()
    r.eng, r.wt, r.vault, r.root = eng, wt, vault, tmp_path / "oncall"
    r.get = lambda name: api.handle("GET", f"/api/oncall/{name}", eng=eng)
    r.post = lambda name, body=None: api.handle("POST", f"/api/oncall/{name}", body or {}, eng=eng)
    return r


def _add_command_channel(rig, tmp_path, owner="me@example.com"):
    out = tmp_path / "sent.txt"
    code, res = rig.post("channel", {"channel": {
        "type": "command", "send": f"sh -c 'cat >> {out}'", "owner": owner}})
    assert code == 200, res
    return out


def test_a_fresh_install_is_off_and_describes_every_form(rig):
    code, res = rig.get("status")
    assert code == 200 and res["ok"]
    assert res["enabled"] is False and res["channels"] == [] and res["waiting"] == []
    assert set(res["channel_types"]) == set(st.CHANNEL_TYPES)
    assert set(res["source_types"]) == set(st.SOURCE_TYPES)
    for fields in list(res["channel_types"].values()) + list(res["source_types"].values()):
        for field in fields:
            assert {"name", "label", "secret", "optional", "placeholder"} <= set(field)
            # A secret is always a Vault reference, never a plain config value.
            assert field["name"].endswith("_vault") == bool(field["secret"])


def test_turning_it_on_needs_a_queue_and_a_channel(rig, tmp_path):
    code, res = rig.post("config", {"enabled": True})
    assert code == 400 and res["error"] == "Name a queue first."
    # A refused change leaves nothing behind.
    assert rig.get("status")[1]["enabled"] is False and not rig.eng.active()
    code, res = rig.post("config", {"queue": "shop", "repo": str(tmp_path)})
    assert code == 200 and res["config"]["queue"] == "SHOP"
    assert rig.wt.configured == [("SHOP", str(tmp_path))]
    code, res = rig.post("config", {"enabled": True})
    assert code == 400 and res["error"] == "Add a message channel first."
    _add_command_channel(rig, tmp_path)
    code, res = rig.post("config", {"enabled": True})
    assert code == 200 and res["config"]["enabled"] is True
    assert rig.eng.active()


def test_without_watchtower_setup_still_saves_and_says_so(rig):
    rig.wt.installed = False
    code, res = rig.post("config", {"queue": "SHOP"})
    assert code == 200
    assert res["notes"] == ["WatchTower is not installed, so no agent will pick tickets up yet."]


def test_telegram_setup_finds_the_chat_and_keeps_the_token_in_the_vault(rig, web):
    body = {"channel": {"type": "telegram", "api_base": web}, "secrets": {"token_vault": TOKEN}}
    code, res = rig.post("channel", body)
    assert code == 409
    assert res["error"] == "Open your bot in Telegram, send it any message, then add it again."
    assert rig.get("status")[1]["channels"] == []

    _Telegram.updates = [{"update_id": 7, "message": {
        "message_id": 1, "text": "hi", "from": {"id": 4242, "first_name": "Dana"},
        "chat": {"id": 4242, "type": "private", "first_name": "Dana"}}}]
    code, res = rig.post("channel", body)
    assert code == 200, res
    assert res["channel"]["owner"] == "4242"
    assert res["channel"]["token_vault"] == "oncall-channel-telegram-token"
    assert rig.vault.entries == {"oncall-channel-telegram-token": TOKEN}
    # The token is in the Vault only: not in the config file, not in any reply.
    assert TOKEN not in (rig.root / st.CONFIG_NAME).read_text()
    assert TOKEN not in json.dumps(res) and TOKEN not in json.dumps(rig.get("status")[1])

    code, res = rig.post("test")
    assert code == 200 and res["ok"], res
    assert str(_Telegram.sent[-1]["chat_id"]) == "4242"
    assert "This is a test from CCC On-call." in _Telegram.sent[-1]["text"]


def test_a_wrong_bot_token_is_refused_without_echoing_it(rig, web):
    code, res = rig.post("channel", {"channel": {"type": "telegram", "api_base": web},
                                     "secrets": {"token_vault": "999:WRONG-test-token"}})
    assert code == 422
    assert "Unauthorized" in res["error"] and "WRONG" not in res["error"]
    assert rig.get("status")[1]["channels"] == []


def test_a_secret_typed_as_a_plain_field_is_never_saved(rig, web):
    code, res = rig.post("channel", {"channel": {"type": "telegram", "api_base": web, "token": TOKEN}})
    assert code in (400, 409, 422)
    path = rig.root / st.CONFIG_NAME
    assert not path.exists() or TOKEN not in path.read_text()


def test_channel_toggle_and_remove(rig, tmp_path):
    _add_command_channel(rig, tmp_path)
    assert rig.post("channel", {"action": "toggle", "channel": {"id": "command", "enabled": False}})[0] == 200
    assert rig.get("status")[1]["channels"][0]["enabled"] is False
    assert rig.post("channel", {"action": "remove", "channel": {"id": "command"}})[0] == 200
    assert rig.get("status")[1]["channels"] == []


def test_test_message_runs_the_real_command_channel(rig, tmp_path):
    out = _add_command_channel(rig, tmp_path)
    code, res = rig.post("test")
    assert code == 200 and res["results"] == [{"channel": "command", "ok": True, "error": ""}]
    assert out.read_text().startswith("This is a test from CCC On-call.")


def test_test_with_no_channel_says_what_to_do(rig):
    code, res = rig.post("test")
    assert code == 400 and res["error"] == "Add a message channel first."


def test_uptime_source_is_checked_before_it_is_saved(rig, web):
    code, res = rig.post("source", {"source": {"type": "http", "url": web + "/missing"}})
    assert code == 422 and "503" in res["error"]
    code, res = rig.post("source", {"source": {"type": "http", "url": web + "/health", "queue": "shop"}})
    assert code == 200, res
    assert res["source"]["id"] == "http" and res["source"]["queue"] == "SHOP"
    assert rig.get("status")[1]["sources"][0]["type"] == "http"
    assert rig.post("source", {"action": "remove", "source": {"id": "http"}})[0] == 200
    assert rig.get("status")[1]["sources"] == []


def test_known_customers_and_owner_reports(rig, tmp_path):
    code, res = rig.post("customer", {"action": "add", "address": "Dana@Example.com", "name": "Dana"})
    assert code == 200 and res["known"] == [{"address": "dana@example.com", "name": "Dana"}]
    assert rig.post("customer", {"action": "remove", "address": "dana@example.com"})[1]["known"] == []

    code, res = rig.post("report", {"text": "Checkout button does nothing on Safari"})
    assert code == 400 and "ccc oncall setup" in res["error"]
    rig.post("config", {"queue": "SHOP"})
    code, res = rig.post("report", {"text": "Checkout button does nothing on Safari"})
    assert code == 200 and res["ref"] == "SHOP-1"
    assert "Checkout button does nothing on Safari" in rig.wt.added[0][2]

    # The owner filed it, so there is no customer to ask.
    code, res = rig.post("customer", {"action": "ask", "ref": "SHOP-1", "text": "Which browser?"})
    assert code == 400 and "no reporter" in res["error"]


def test_minor_decision_switch_sets_the_queue_policy(rig):
    rig.post("config", {"queue": "SHOP"})
    rig.wt.policies.clear()
    code, res = rig.post("config", {"minor_decisions": {"enabled": True}})
    assert code == 200 and rig.wt.policies == [("SHOP", True)]
    assert rig.get("status")[1]["minor_decisions"] is True
    code, res = rig.get("decisions")
    assert res["decisions"] == [{"ref": "SHOP-3", "state": "applied",
                                 "reason": "git revert abc1234", "queue": "SHOP"}]


def test_decisions_say_what_was_decided_and_how_to_undo_it(rig):
    rig.post("config", {"queue": "SHOP"})
    rig.wt.find = lambda ref: {"history": [
        {"event": "block", "question": "Name it bindCheckout or attachCheckoutHandler?"},
        {"event": "auto_decision", "recommend": "bindCheckout", "undo": "git revert abc1234"}]}
    assert rig.get("decisions")[1]["decisions"] == [{
        "ref": "SHOP-3", "state": "applied", "reason": "git revert abc1234", "queue": "SHOP",
        "question": "Name it bindCheckout or attachCheckoutHandler?",
        "recommend": "bindCheckout", "undo": "git revert abc1234"}]


def test_inbound_is_for_command_channels_only(rig, tmp_path):
    code, res = rig.post("inbound", {"channel": "nope", "text": "hi"})
    assert code == 400
    _add_command_channel(rig, tmp_path)
    rig.post("config", {"queue": "SHOP"})
    rig.post("config", {"enabled": True})
    code, res = rig.post("inbound", {"channel": "command", "id": "m1",
                                     "sender": "me@example.com", "text": "status"})
    assert code == 200, res
    assert "On-call is on." in (tmp_path / "sent.txt").read_text()


def test_unknown_requests_and_bad_input_never_raise(rig):
    assert rig.get("nothing")[0] == 404
    assert rig.post("nothing")[0] == 404
    assert rig.post("channel", {"channel": {"type": "pager"}})[0] == 400
    assert rig.post("source", {"source": {"type": "pager"}})[0] == 400
    assert api.handle("POST", "/api/oncall/config", "not a dict", eng=rig.eng)[0] == 200


def test_session_questions_become_alerts_and_clear(rig, tmp_path):
    out = _add_command_channel(rig, tmp_path)
    rig.post("config", {"queue": "SHOP"})
    rig.post("config", {"enabled": True})
    describe = lambda sid: ("checkout fix", "Keep the old redirect, or remove it now?")
    waiting = {"s1": {"state": "idle", "question_waiting": True}}
    # The first look is a baseline: nothing already waiting is announced.
    assert api.observe_sessions(None, waiting, describe, eng=rig.eng) == 0
    assert api.observe_sessions({"s1": {"question_waiting": False}}, waiting, describe, eng=rig.eng) == 1
    rig.eng.tick(force=True)
    text = out.read_text()
    assert "An agent is waiting on you (#1)" in text and "Keep the old redirect" in text
    api.observe_sessions(waiting, {"s1": {"question_waiting": False}}, describe, eng=rig.eng)
    assert rig.eng.store.alerts(state="open") == []
    assert api.session_stopped("s2", "checkout fix", "It died mid-task.", "99", eng=rig.eng)["kind"] == "stopped"


def test_nothing_is_raised_while_it_is_off(rig):
    waiting = {"s1": {"question_waiting": True}}
    assert api.observe_sessions({}, waiting, lambda sid: ("t", "q"), eng=rig.eng) == 0
    assert api.session_stopped("s2", "t", "b", "1", eng=rig.eng) is None
    assert not (rig.root / "oncall.db").exists() or rig.eng.store.alerts(state="open") == []
