# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Tests for ccc_server.codex_cloud_threads — Codex cloud (dot/aeon) threads.

The fake backend is an in-process RFC 6455 server on loopback speaking just
enough of the Codex app-server JSON-RPC family (initialize, thread/list,
thread/read, thread/turns/list) to exercise the real client end to end:
upgrade, subprotocols, masking, fragmentation, ping/pong, pagination, and
error mapping. All fixture ids/tokens are obvious fakes.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import unittest
from unittest import mock

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CCC_TEST_ISOLATION", "1")

import ccc_server
for _name in ("paths", "log_parse", "repo_paths", "session_graph"):
    try:
        ccc_server.register(__import__(f"ccc_server.{_name}", fromlist=[_name]))
    except Exception:
        pass

from ccc_server import codex_cloud_threads as cloud

FAKE_TOKEN = "ccc-cloud-test-token-0000"
FAKE_ACCOUNT = "acct-test-0000"
TID_ROOT = "11111111-2222-3333-4444-555555555555"
TID_CHILD = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TID_OTHER = "99999999-8888-7777-6666-555555555555"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _srv_send_frame(conn, opcode, payload, fin=True, masked=False):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    head = bytearray([(0x80 if fin else 0) | opcode])
    n = len(payload)
    if n < 126:
        head.append((0x80 if masked else 0) | n)
    elif n < 65536:
        head.append((0x80 if masked else 0) | 126)
        head += struct.pack(">H", n)
    else:
        head.append((0x80 if masked else 0) | 127)
        head += struct.pack(">Q", n)
    if masked:
        mask = b"\x01\x02\x03\x04"
        head += mask
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    conn.sendall(bytes(head) + payload)


def _srv_read_exact(conn, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return bytes(buf)


def _srv_read_message(conn, on_control=None):
    """Read one complete (possibly fragmented) client message. Client frames
    are masked per RFC 6455."""
    data = bytearray()
    opcode = 0
    while True:
        head = _srv_read_exact(conn, 2)
        fin = bool(head[0] & 0x80)
        op = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        n = head[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", _srv_read_exact(conn, 2))[0]
        elif n == 127:
            n = struct.unpack(">Q", _srv_read_exact(conn, 8))[0]
        mask = _srv_read_exact(conn, 4) if masked else b""
        payload = _srv_read_exact(conn, n) if n else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if op in (0x8, 0x9, 0xA):
            if on_control:
                on_control(op, payload)
            if op == 0x9:
                _srv_send_frame(conn, 0xA, payload[:125])
            continue
        if op in (0x1, 0x2):
            opcode = op
            data = bytearray(payload)
            if fin:
                return opcode, bytes(data)
        elif op == 0x0:
            data += payload
            if fin:
                return opcode, bytes(data)


class FakeCloudServer(threading.Thread):
    """In-process fake codex-cloud-backend. `rpc(method, params)` returns a
    result or raises RpcError; hook callbacks record handshake facts."""

    def __init__(self, rpc=None, *, reject_status=None, fragment_at=0,
                 ping_during_reply=False):
        super().__init__(daemon=True, name="fake-cloud")
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.port = self.listener.getsockname()[1]
        self.rpc = rpc or (lambda m, p: {})
        self.reject_status = reject_status
        self.fragment_at = fragment_at
        self.ping_during_reply = ping_during_reply
        self.connections = 0
        self.handshake = {}
        self.requests = []
        self.got_pong = threading.Event()
        self.stop_flag = threading.Event()

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.port}/"

    def _on_control(self, op, payload):
        if op == 0xA:
            self.got_pong.set()

    def _handshake(self, conn):
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return False
            data += chunk
        head = bytes(data).split(b"\r\n\r\n", 1)[0].decode("latin-1")
        lines = head.split("\r\n")
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        self.handshake = headers
        if self.reject_status:
            conn.sendall(
                f"HTTP/1.1 {self.reject_status} Rejected\r\n"
                f"Content-Length: 0\r\n\r\n".encode())
            return False
        key = headers.get("sec-websocket-key", "")
        accept = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        conn.sendall(
            ("HTTP/1.1 101 Switching Protocols\r\n"
             "Upgrade: websocket\r\n"
             "Connection: Upgrade\r\n"
             f"Sec-WebSocket-Accept: {accept}\r\n"
             "Sec-WebSocket-Protocol: codex-app-server\r\n"
             "\r\n").encode())
        return True

    def _serve(self, conn):
        with conn:
            if not self._handshake(conn):
                return
            while not self.stop_flag.is_set():
                try:
                    _op, payload = _srv_read_message(conn, self._on_control)
                except (ConnectionError, OSError):
                    return
                try:
                    msg = json.loads(payload)
                except ValueError:
                    continue
                self.requests.append(msg)
                if "id" not in msg:
                    continue  # notification (e.g. initialized)
                try:
                    result = self.rpc(msg.get("method"), msg.get("params") or {})
                    reply = {"id": msg["id"], "result": result or {}}
                except RpcError as exc:
                    reply = {"id": msg["id"],
                             "error": {"code": exc.code, "message": str(exc)}}
                body = json.dumps(reply).encode("utf-8")
                if self.ping_during_reply:
                    _srv_send_frame(conn, 0x9, b"ping")
                if self.fragment_at and len(body) > self.fragment_at:
                    first = body[: self.fragment_at]
                    rest = body[self.fragment_at:]
                    _srv_send_frame(conn, 0x1, first, fin=False)
                    while rest:
                        chunk, rest = rest[: self.fragment_at], rest[self.fragment_at:]
                        _srv_send_frame(conn, 0x0, chunk, fin=not rest)
                else:
                    _srv_send_frame(conn, 0x1, body)

    def run(self):
        while not self.stop_flag.is_set():
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def stop(self):
        self.stop_flag.set()
        try:
            self.listener.close()
        except OSError:
            pass
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=0.3).close()
        except OSError:
            pass


def _catalog():
    return [
        {"id": TID_ROOT, "name": "Fake root thread", "threadSource": "aeon",
         "cwd": "/tmp/fake-repo", "model": "fake-model",
         "createdAt": 1700000000, "updatedAt": 1700000100,
         "status": {"type": "notLoaded"}},
        {"id": TID_CHILD, "name": "Fake child thread", "threadSource": "aeon_child",
         "cwd": "/tmp/fake-repo", "model": "fake-model",
         "createdAt": 1700000200, "updatedAt": 1700000300,
         "status": {"type": "notLoaded"}},
    ]


def _turns(tid, count):
    return [{
        "id": f"turn-{i}", "items": [
            {"type": "userMessage", "id": f"u{i}",
             "content": [{"type": "text", "text": f"question {i}"}]},
            {"type": "agentMessage", "id": f"a{i}", "text": f"answer {i}"},
        ],
        "itemsView": "full", "status": "completed",
        "startedAt": 1700000000 + i, "completedAt": 1700000000 + i + 1,
        "durationMs": 1000, "error": None,
    } for i in range(count)]


class _ResetState(unittest.TestCase):
    def setUp(self):
        self._saved_env = dict(os.environ)
        cloud._CATALOG.update({
            "ts": 0.0, "threads": None, "degraded": "cloud catalog still loading",
            "failures": 0, "next_retry": 0.0, "refreshing": False,
            "live_ok": False,
        })
        cloud._SIDEBAR_CACHE.update({"key": None, "threads": []})
        cloud._THREAD_MEMO.clear()
        cloud._THREAD_REFRESHING.clear()
        self._server = None

    def tearDown(self):
        if self._server:
            self._server.stop()
        os.environ.clear()
        os.environ.update(self._saved_env)
        cloud._CATALOG.update({
            "ts": 0.0, "threads": None, "degraded": "cloud catalog still loading",
            "failures": 0, "next_retry": 0.0, "refreshing": False,
            "live_ok": False,
        })
        cloud._THREAD_MEMO.clear()
        cloud._THREAD_REFRESHING.clear()

    def start_server(self, rpc=None, **kw):
        # The loopback backend must never depend on a developer's real login.
        auth = mock.patch.object(
            cloud, "_load_codex_auth", return_value=(FAKE_TOKEN, FAKE_ACCOUNT))
        auth.start()
        self.addCleanup(auth.stop)
        self._server = FakeCloudServer(rpc, **kw)
        self._server.start()
        os.environ["CCC_CODEX_CLOUD_WS_URL"] = self._server.url
        return self._server

    def client(self):
        return cloud.CodexCloudClient(
            ws_url=self._server.url, token=FAKE_TOKEN, account_id=FAKE_ACCOUNT)


class TestHandshakeAndCatalog(_ResetState):
    def test_handshake_subprotocols_and_catalog(self):
        def rpc(method, params):
            if method == "initialize":
                return {"server": "fake", "version": "1"}
            if method == "thread/list":
                return {"data": _catalog(), "nextCursor": None}
            raise RpcError(-32601, "no such method")

        srv = self.start_server(rpc)
        with self.client() as client:
            threads = client.list_threads()
        self.assertEqual(len(threads), 2)
        self.assertEqual(threads[0]["id"], TID_ROOT)
        protos = srv.handshake.get("sec-websocket-protocol", "")
        self.assertIn("codex-app-server", protos)
        self.assertIn("codex-client.ccc", protos)
        self.assertIn(f"openai-bearer.{FAKE_TOKEN}", protos)
        self.assertEqual(srv.handshake.get("chatgpt-account-id"), FAKE_ACCOUNT)
        # initialize then the bare `initialized` notification both arrived.
        methods = [m.get("method") for m in srv.requests]
        self.assertIn("initialize", methods)
        self.assertIn("initialized", methods)
        self.assertIn("thread/list", methods)

    def test_thread_list_pagination(self):
        calls = []

        def rpc(method, params):
            if method == "initialize":
                return {}
            if method == "thread/list":
                calls.append(params.get("cursor"))
                data = _catalog()[:1] if len(calls) == 1 else _catalog()[1:]
                cursor = "page-2" if len(calls) == 1 else None
                return {"data": data, "nextCursor": cursor}
            raise RpcError(-32601, "no")

        srv = self.start_server(rpc)
        with self.client() as client:
            threads = client.list_threads(limit=1)
        self.assertEqual([t["id"] for t in threads], [TID_ROOT, TID_CHILD])
        self.assertEqual(calls, [None, "page-2"])


class TestTurnsPagination(_ResetState):
    def test_iter_turns_follows_next_cursor(self):
        cursors = []

        def rpc(method, params):
            if method == "initialize":
                return {}
            if method == "thread/turns/list":
                self.assertEqual(params["threadId"], TID_CHILD)
                self.assertEqual(params["itemsView"], "full")
                self.assertEqual(params["sortDirection"], "asc")
                cursors.append(params.get("cursor"))
                idx = len(cursors) - 1
                turns = _turns(TID_CHILD, 5)[idx * 2: idx * 2 + 2]
                return {"data": turns,
                        "nextCursor": f"c{idx + 1}" if idx < 2 else None}
            raise RpcError(-32601, "no")

        self.start_server(rpc)
        with self.client() as client:
            turns = list(client.iter_turns(TID_CHILD))
        self.assertEqual(len(turns), 5)
        self.assertEqual(cursors, [None, "c1", "c2"])
        self.assertEqual([t["id"] for t in turns],
                         [f"turn-{i}" for i in range(5)])


class TestProtocolEdgeCases(_ResetState):
    def test_include_turns_true_is_schema_rejected(self):
        def rpc(method, params):
            if method == "initialize":
                return {}
            if method == "thread/read" and params.get("includeTurns"):
                raise RpcError(-32602, "includeTurns rejected; use thread/turns/list")
            return {}

        self.start_server(rpc)
        with self.client() as client:
            with self.assertRaises(cloud.CloudSchemaError):
                client.request("thread/read",
                               {"threadId": TID_CHILD, "includeTurns": True})

    def test_401_upgrade_maps_to_auth_rejected(self):
        self.start_server(reject_status=401)
        with self.assertRaises(cloud.CloudAuthRejected) as ctx:
            self.client().connect()
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
        self.assertNotIn(FAKE_ACCOUNT, str(ctx.exception))

    def test_unreachable_backend(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        client = cloud.CodexCloudClient(
            ws_url=f"ws://127.0.0.1:{port}/",
            token=FAKE_TOKEN, account_id=FAKE_ACCOUNT)
        with self.assertRaises(cloud.CloudUnreachable):
            client.connect()

    def test_fragmented_large_reply_and_ping(self):
        payload_text = "x" * (2 * 1024 * 1024)

        def rpc(method, params):
            if method == "initialize":
                return {}
            if method == "thread/read":
                return {"thread": {"id": TID_CHILD, "name": payload_text}}
            raise RpcError(-32601, "no")

        srv = self.start_server(rpc, fragment_at=64 * 1024, ping_during_reply=True)
        with self.client() as client:
            meta = client.read_thread_meta(TID_CHILD)
        self.assertEqual(meta["name"], payload_text)
        self.assertTrue(srv.got_pong.is_set(),
                        "client must answer a ping interleaved mid-message")

    def test_token_never_echoed_in_errors(self):
        def rpc(method, params):
            if method == "initialize":
                return {}
            if method == "thread/read":
                # A hostile/buggy backend that echoes the bearer token back.
                raise RpcError(-32000, f"upstream rejected {FAKE_TOKEN} badly")
            return {}

        self.start_server(rpc)
        with self.client() as client:
            with self.assertRaises(cloud.CloudRemoteError) as ctx:
                client.read_thread_meta(TID_CHILD)
        self.assertNotIn(FAKE_TOKEN, str(ctx.exception))
        self.assertIn("[redacted]", str(ctx.exception))

    def test_mutating_methods_never_reach_the_wire(self):
        client = cloud.CodexCloudClient(
            ws_url="ws://127.0.0.1:1/", token=FAKE_TOKEN,
            account_id=FAKE_ACCOUNT)
        with self.assertRaises(cloud.CloudError):
            client.request("turn/start", {"threadId": TID_CHILD})
        with self.assertRaises(cloud.CloudError):
            client.request("thread/name/set", {})
        self.assertIsNone(self._server)  # no server was even needed

    def test_host_pinning_and_scheme_rules(self):
        with self.assertRaises(cloud.CloudHandshakeError):
            cloud.ws_connect("wss://example.com/")
        with self.assertRaises(cloud.CloudHandshakeError):
            cloud.ws_connect("ws://example.com/")
        with self.assertRaises(cloud.CloudHandshakeError):
            cloud.ws_connect("https://codex-cloud-backend.chatgpt.com/")

    def test_thread_id_validation(self):
        self.assertTrue(cloud.valid_thread_id(TID_CHILD))
        self.assertFalse(cloud.valid_thread_id("not-a-uuid"))
        self.assertFalse(cloud.valid_thread_id("../../../etc/passwd"))
        self.assertFalse(cloud.valid_thread_id(""))
        with self.assertRaises(cloud.CloudError) as ctx:
            cloud.fetch_cloud_thread("../evil")
        self.assertEqual(ctx.exception.reason, "invalid_thread_id")


class TestAuth(_ResetState):
    def test_missing_auth(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            os.environ["CODEX_HOME"] = home
            with self.assertRaises(cloud.CloudAuthMissing) as ctx:
                cloud._load_codex_auth()
            self.assertNotIn("access_token", str(ctx.exception))
            self.assertFalse(cloud.auth_available())

    def test_malformed_token_rejected(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as home:
            auth = Path(home) / "auth.json"
            auth.write_text(json.dumps({"tokens": {
                "access_token": "bad\r\ntoken",
                "account_id": FAKE_ACCOUNT}}))
            os.environ["CODEX_HOME"] = home
            with self.assertRaises(cloud.CloudAuthRejected):
                cloud._load_codex_auth()


class TestBlobStripping(_ResetState):
    def test_data_uri_blob_replaced(self):
        blob = "data:image/png;base64," + ("A" * 9000)
        out = cloud._strip_blobs({"img": blob, "keep": "short"})
        self.assertEqual(out["keep"], "short")
        self.assertEqual(out["img"]["type"], "cloud_blob")
        self.assertEqual(out["img"]["bytes"], len(blob))
        self.assertEqual(out["img"]["media"], "image/png")

    def test_raw_base64_blob_replaced(self):
        blob = "QUJD" * 3000  # 12000 chars, pure b64 alphabet, no spaces
        out = cloud._strip_blobs({"payload": blob})
        self.assertEqual(out["payload"]["type"], "cloud_blob")
        self.assertEqual(out["payload"]["bytes"], len(blob))

    def test_long_prose_not_blob(self):
        prose = ("a perfectly ordinary sentence. " * 400)
        out = cloud._strip_blobs({"text": prose})
        self.assertEqual(out["text"], prose)

    def test_oversized_text_truncated(self):
        huge = "z" * (cloud._TEXT_TRUNC + 5000)
        out = cloud._strip_blobs({"out": huge})
        self.assertLess(len(out["out"]), len(huge))
        self.assertIn("truncated", out["out"])


class TestDelegationAndEvents(_ResetState):
    def test_parse_delegation(self):
        wrapped = (
            "<codex_delegation>\n"
            f"  <source_thread_id>{TID_ROOT}</source_thread_id>\n"
            "  <input>do the delegated thing</input>\n"
            "</codex_delegation>")
        parent, inner = cloud.parse_delegation(wrapped)
        self.assertEqual(parent, TID_ROOT)
        self.assertEqual(inner, "do the delegated thing")
        parent, inner = cloud.parse_delegation("plain message")
        self.assertEqual(parent, "")
        self.assertEqual(inner, "plain message")

    def test_turns_to_events_shape(self):
        turns = _turns(TID_CHILD, 2)
        turns[0]["items"].insert(1, {
            "type": "reasoning", "id": "r0",
            "summary": [{"type": "summary_text", "text": "thinking about it"}],
            "content": []})
        turns[0]["items"].append({
            "type": "commandExecution", "id": "cmd0",
            "command": "echo fake", "aggregatedOutput": "fake\n",
            "status": "completed", "exitCode": 0})
        events, parent = cloud.cloud_turns_to_events(turns)
        kinds = [e["type"] for e in events]
        self.assertIn("user_text", kinds)
        self.assertIn("assistant", kinds)
        self.assertIn("tool_result", kinds)
        self.assertIn("result", kinds)
        self.assertEqual(parent, "")
        reasoning = [e for e in events
                     if any(b.get("kind") == "thinking"
                            for b in e.get("blocks", []))]
        self.assertTrue(reasoning)
        cmd_result = [e for e in events if e["type"] == "tool_result"]
        self.assertTrue(any(e["text"] == "fake\n" for e in cmd_result))

    def test_turns_to_events_delegation(self):
        turns = [{
            "id": "t0", "status": "completed", "durationMs": 10,
            "startedAt": 1700000000,
            "items": [{
                "type": "userMessage", "id": "u0",
                "content": [{"type": "text", "text":
                    "<codex_delegation>"
                    f"<source_thread_id>{TID_ROOT}</source_thread_id>"
                    "<input>inner ask</input></codex_delegation>"}]}],
        }]
        events, parent = cloud.cloud_turns_to_events(turns)
        self.assertEqual(parent, TID_ROOT)
        user_events = [e for e in events if e["type"] == "user_text"]
        self.assertEqual(len(user_events), 1)
        self.assertEqual(user_events[0]["text"], "inner ask")
        self.assertEqual(user_events[0]["delegated_from_thread"], TID_ROOT)


class TestFetchAndCache(_ResetState):
    def _rpc(self):
        turns = _turns(TID_CHILD, 3)

        def rpc(method, params):
            if method == "initialize":
                return {}
            if method == "thread/read":
                return {"thread": {"id": TID_CHILD, "name": "Fake child",
                                   "updatedAt": 1700000300}}
            if method == "thread/turns/list":
                return {"data": turns, "nextCursor": None}
            raise RpcError(-32601, "no")
        return rpc

    def test_fetch_then_cache_hit(self):
        import tempfile
        with tempfile.TemporaryDirectory() as state:
            os.environ["CCC_CODEX_CLOUD_STATE_DIR"] = state
            srv = self.start_server(self._rpc())
            first = cloud.fetch_cloud_thread(TID_CHILD)
            self.assertFalse(first["from_cache"])
            self.assertEqual(len(first["turns"]), 3)
            second = cloud.fetch_cloud_thread(TID_CHILD)
            self.assertTrue(second["from_cache"])
            self.assertEqual(srv.connections, 1,
                             "cached open must not reconnect the backend")
            # Cache file has no secrets and stripped blobs only.
            cached = json.loads(
                (cloud._thread_cache_path(TID_CHILD)).read_text())
            self.assertNotIn(FAKE_TOKEN, json.dumps(cached))

    def test_stale_cache_serves_now_and_refreshes_in_background(self):
        """CCC-1254: a stale cache must not refetch on the open's thread."""
        import tempfile
        with tempfile.TemporaryDirectory() as state:
            os.environ["CCC_CODEX_CLOUD_STATE_DIR"] = state
            srv = self.start_server(self._rpc())
            cloud._thread_cache_write(TID_CHILD, 1700000000, {}, [])
            cloud._CATALOG["threads"] = [{"id": TID_CHILD,
                                          "updatedAt": 1700000300}]
            cloud._CATALOG["ts"] = time.time()
            gate = threading.Event()
            real = cloud._fetch_thread_live

            def slow_live(*a, **kw):
                gate.wait(5)
                return real(*a, **kw)
            with mock.patch.object(cloud, "_fetch_thread_live", slow_live):
                fetched = cloud.fetch_cloud_thread(TID_CHILD)
                self.assertTrue(fetched["from_cache"])
                self.assertTrue(fetched.get("refreshing"))
                self.assertEqual(fetched["turns"], [])
                # Second stale open while in flight: no second refetch.
                self.assertFalse(cloud._schedule_thread_refresh(TID_CHILD, 0))
                gate.set()
                deadline = time.time() + 5
                while cloud._THREAD_REFRESHING and time.time() < deadline:
                    time.sleep(0.02)
            self.assertEqual(srv.connections, 1)
            fresh = cloud.fetch_cloud_thread(TID_CHILD)
            self.assertTrue(fresh["from_cache"])
            self.assertFalse(fresh.get("refreshing"))
            self.assertEqual(len(fresh["turns"]), 3)

    def test_fetch_failure_serves_stale_cache(self):
        import tempfile
        with tempfile.TemporaryDirectory() as state:
            os.environ["CCC_CODEX_CLOUD_STATE_DIR"] = state
            self.start_server(self._rpc())
            cloud.fetch_cloud_thread(TID_CHILD)
            self._server.stop()
            # Backend down: stale cache still serves with a degraded reason.
            fetched = cloud.fetch_cloud_thread(TID_CHILD, force=True)
            self.assertTrue(fetched["from_cache"])
            self.assertIn("stale", fetched["degraded"])

    def test_parse_cloud_conversation_failure_is_typed(self):
        import tempfile
        with tempfile.TemporaryDirectory() as state:
            os.environ["CCC_CODEX_CLOUD_STATE_DIR"] = state
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
            sock.close()
            os.environ["CCC_CODEX_CLOUD_WS_URL"] = f"ws://127.0.0.1:{port}/"
            result = cloud.parse_cloud_conversation(TID_CHILD)
            self.assertTrue(result["codex_cloud"])
            self.assertEqual(result["turn_count"], 0)
            self.assertIn("unavailable",
                          result["events"][0]["text"])
            self.assertIn("cloud_degraded", result)


class TestListPathPerformance(_ResetState):
    def test_list_path_makes_zero_network_calls(self):
        """The session-list path must never open a socket (perf gate)."""
        cloud._CATALOG["threads"] = _catalog()
        cloud._CATALOG["ts"] = time.time()
        cloud._CATALOG["live_ok"] = True
        os.environ["CCC_CODEX_CLOUD_STATE_DIR"] = "/nonexistent-ccc-cloud-test"
        with mock.patch.object(cloud, "ws_connect",
                               side_effect=AssertionError("network on list path")):
            rows = cloud.find_codex_cloud_conversations(repo_only=False)
            self.assertEqual(len(rows), 2)
            child = [r for r in rows if r["session_id"] == TID_CHILD][0]
            self.assertEqual(child["engine"], "codex")
            self.assertTrue(child["codex_cloud"])
            self.assertEqual(child["thread_source"], "aeon_child")
            self.assertEqual(child["parent_session_id"], "")
            # Repeat calls stay cache-served and fast.
            t0 = time.monotonic()
            for _ in range(50):
                cloud.find_codex_cloud_conversations(repo_only=False)
            self.assertLess(time.monotonic() - t0, 2.0)
            self.assertTrue(cloud.is_cloud_thread_id(TID_CHILD))
            self.assertFalse(cloud.is_cloud_thread_id(TID_OTHER))

    def test_parent_from_dot_root(self):
        entry = {
            "id": TID_CHILD, "threadSource": "aeon_child",
            "parentThreadId": "", "dotRootId": TID_ROOT,
        }
        self.assertEqual(cloud.cloud_parent_id(entry), TID_ROOT)
        root = {"id": TID_ROOT, "threadSource": "aeon",
                "parentThreadId": "", "dotRootId": TID_ROOT}
        self.assertEqual(cloud.cloud_parent_id(root), "")
        explicit = {"id": TID_CHILD, "threadSource": "aeon_child",
                    "parentThreadId": TID_OTHER, "dotRootId": TID_ROOT}
        self.assertEqual(cloud.cloud_parent_id(explicit), TID_OTHER)


if __name__ == "__main__":
    unittest.main()
