"""Live agy session delivery through its language server (ccc_server.antigravity_ls)."""

import json
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ccc_server import antigravity_ls as ls

CID = "c5e79f4f-9ecb-4869-8280-28026d577cfc"
TOKEN = "99ca1111-2222-3333-4444-555566667777"
USER_CONFIG = {"plannerConfig": {"planModel": "MODEL_PLACEHOLDER_M300"}}


class _FakeLS(BaseHTTPRequestHandler):
    calls = []
    status = "CASCADE_RUN_STATUS_RUNNING"

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        method = self.path.rsplit("/", 1)[-1]
        if self.headers.get("x-codeium-csrf-token") != TOKEN:
            self._reply(401, {"code": "unauthenticated", "message": "invalid CSRF token"})
            return
        type(self).calls.append((method, body))
        if method == "GetCascadeTrajectory":
            self._reply(200, {"trajectory": {"steps": [
                {"userInput": {"items": [{"text": "first"}], "userConfig": {"old": True}}},
                {"plannerResponse": {}},
                {"userInput": {"items": [{"text": "second"}], "userConfig": USER_CONFIG}},
            ]}})
        elif method == "GetAllCascadeTrajectories":
            self._reply(200, {"trajectorySummaries": {CID: {"status": type(self).status}}})
        else:
            self._reply(200, {})

    def _reply(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class AntigravityLSTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.reg = self.tmp.name
        _FakeLS.calls = []
        _FakeLS.status = "CASCADE_RUN_STATUS_RUNNING"
        ls._config_cache.clear()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeLS)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addr = f"127.0.0.1:{self.server.server_address[1]}"
        self.server_up = True

    def tearDown(self):
        self._stop_server()
        self.tmp.cleanup()

    def _stop_server(self):
        if self.server_up:
            self.server.shutdown()
            self.server.server_close()
            self.server_up = False

    def _env(self, **over):
        env = {"ANTIGRAVITY_CONVERSATION_ID": CID, "ANTIGRAVITY_LS_ADDRESS": self.addr,
               "ANTIGRAVITY_CSRF_TOKEN": TOKEN}
        env.update(over)
        return env

    def test_register_writes_private_entry(self):
        self.assertEqual(ls.register_from_env(self._env(), registry_dir=self.reg), CID)
        path = os.path.join(self.reg, f"{CID}.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        entry = ls.lookup(CID, registry_dir=self.reg)
        self.assertEqual((entry["address"], entry["token"]), (self.addr, TOKEN))

    def test_register_ignores_non_agy_env(self):
        self.assertIsNone(ls.register_from_env({}, registry_dir=self.reg))
        self.assertIsNone(ls.register_from_env(
            self._env(ANTIGRAVITY_LS_ADDRESS="evil.example.com:80"), registry_dir=self.reg))
        self.assertIsNone(ls.register_from_env(
            self._env(ANTIGRAVITY_CONVERSATION_ID="../x"), registry_dir=self.reg))
        self.assertEqual(os.listdir(self.reg), [])

    def test_send_uses_latest_user_config_and_when_idle(self):
        ls.register_from_env(self._env(), registry_dir=self.reg)
        result = ls.send(CID, "hello", registry_dir=self.reg)
        self.assertEqual(result["via"], "antigravity-ls")
        method, body = _FakeLS.calls[-1]
        self.assertEqual(method, "SendUserCascadeMessage")
        self.assertEqual(body["cascadeId"], CID)
        self.assertEqual(body["cascadeConfig"], USER_CONFIG)
        self.assertEqual(body["items"], [{"text": "hello"}])
        self.assertEqual(body["deliveryStrategy"], ls.DELIVERY_SEND)

    def test_steer_delivers_next_invocation_and_config_is_cached(self):
        ls.register_from_env(self._env(), registry_dir=self.reg)
        ls.send(CID, "one", registry_dir=self.reg)
        ls.send(CID, "two", mode="steer", registry_dir=self.reg)
        methods = [m for m, _ in _FakeLS.calls]
        self.assertEqual(methods.count("GetCascadeTrajectory"), 1)
        self.assertEqual(_FakeLS.calls[-1][1]["deliveryStrategy"], ls.DELIVERY_STEER)

    def test_headless_run_takes_message_only_mid_turn(self):
        ls.register_from_env(self._env(), registry_dir=self.reg)
        self.assertEqual(
            ls.send(CID, "mid", registry_dir=self.reg, require_running=True)["via"],
            "antigravity-ls")
        _FakeLS.status = "CASCADE_RUN_STATUS_IDLE"
        sends = [m for m, _ in _FakeLS.calls].count("SendUserCascadeMessage")
        self.assertIsNone(ls.send(CID, "late", registry_dir=self.reg, require_running=True))
        self.assertEqual([m for m, _ in _FakeLS.calls].count("SendUserCascadeMessage"), sends)
        self.assertIsNotNone(ls.lookup(CID, registry_dir=self.reg))

    def test_unregistered_session_falls_back(self):
        self.assertIsNone(ls.send(CID, "hello", registry_dir=self.reg))

    def test_rejected_token_forgets_entry(self):
        ls.register_from_env(self._env(ANTIGRAVITY_CSRF_TOKEN="stale"), registry_dir=self.reg)
        self.assertIsNone(ls.send(CID, "hello", registry_dir=self.reg))
        self.assertIsNone(ls.lookup(CID, registry_dir=self.reg))

    def test_dead_endpoint_forgets_entry(self):
        ls.register_from_env(self._env(), registry_dir=self.reg)
        self._stop_server()
        self.assertIsNone(ls.send(CID, "hello", registry_dir=self.reg))
        self.assertIsNone(ls.lookup(CID, registry_dir=self.reg))


if __name__ == "__main__":
    unittest.main()
