# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Codex cloud ("dot" / aeon) threads: discovery, transcript read, caches.

Codex Desktop dots delegate work to cloud threads (`aeon` roots, `aeon_child`
workers, `dreaming`, plain `user` threads). Those threads live in OpenAI's
cloud: they never write a rollout JSONL under ~/.codex/sessions, and they are
absent from Codex's local state DBs, so CCC's file scanner cannot see them.

This module speaks to the (undocumented, internal) Codex cloud backend over a
JSON-RPC WebSocket using the user's own Codex login (~/.codex/auth.json), the
same protocol family as `codex app-server`. It is strictly read-only: the only
methods ever sent are initialize, thread/list, thread/read and
thread/turns/list. No turn/start, no injections, no mutating calls.

Security rules honored here:
  * The access token is never logged, never included in exception messages,
    never returned from an /api route, and never written to disk.
  * The outbound host is pinned to codex-cloud-backend.chatgpt.com unless a
    test overrides it via CCC_CODEX_CLOUD_WS_URL (loopback only).
  * Thread ids are validated against a strict UUID-ish pattern before use.

Performance rules honored here:
  * The session-list path never opens a WebSocket. thread/list runs on a
    single background refresh flight, TTL+ disk cached; every list render
    serves the cached catalog (or the local sidebar cache) immediately.
  * Turn bodies are fetched only when a thread is opened, cached on disk by
    (thread id, updatedAt), with base64 blobs replaced by placeholders.

Everything degrades softly: missing/expired login, unreachable backend, or a
schema change falls back to metadata from the desktop's own local
`cloud-aeon-sidebar-cache-v1` atom with a typed reason, and retries back off
exponentially instead of spinning.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import socket
import ssl
import struct
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ccc_server import core as _core
from ccc_server import test_isolation_active

CLOUD_HOST = "codex-cloud-backend.chatgpt.com"
CLOUD_URL = f"wss://{CLOUD_HOST}/"
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_CONNECT_TIMEOUT = 15.0
_RPC_TIMEOUT = 60.0
_MAX_HEAD = 65536
_MAX_MESSAGE = 96 * 1024 * 1024  # single JSON-RPC reply bound
_LIST_PAGE_LIMIT = 200
_LIST_MAX_PAGES = 10
_TURNS_PAGE_LIMIT = 50
_TURNS_MAX_PAGES = 100
_TURNS_MAX_ITEMS = 5000

_CATALOG_TTL = 120.0
_BACKOFF_BASE = 30.0
_BACKOFF_MAX = 900.0

_BLOB_MIN = 4096           # strings at/above this size get blob-checked
_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=\r\n]+$")
_TEXT_TRUNC = 256 * 1024   # hard cap on any single kept string
_TOOL_RESULT_MAX = 65536   # serialized bytes kept for a tool result payload
_THREAD_ID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_DELEGATION_RE = re.compile(
    r"^\s*<codex_delegation>\s*<source_thread_id>\s*"
    r"(?P<src>[0-9a-fA-F-]{36})\s*</source_thread_id>\s*"
    r"<input>(?P<input>.*?)</input>\s*</codex_delegation>\s*$",
    re.DOTALL,
)

_CLOUD_SOURCES = {"aeon", "aeon_child", "dreaming", "user", "cloud"}


class CloudError(Exception):
    """Typed failure. `reason` is a short machine-readable code safe to
    surface through the API; the message text never contains credentials."""

    reason = "cloud_error"

    def __init__(self, message, *, reason=None):
        super().__init__(message)
        if reason:
            self.reason = reason


class CloudAuthMissing(CloudError):
    reason = "auth_missing"


class CloudAuthRejected(CloudError):
    reason = "auth_rejected"


class CloudUnreachable(CloudError):
    reason = "unreachable"


class CloudHandshakeError(CloudError):
    reason = "handshake_failed"


class CloudSchemaError(CloudError):
    reason = "schema_changed"


class CloudRemoteError(CloudError):
    reason = "remote_error"


class CloudTimeout(CloudError):
    reason = "timeout"


class CloudClosed(CloudError):
    reason = "closed"


# ── Auth ────────────────────────────────────────────────────────────────────

def _codex_home():
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def _valid_http_token(value):
    """True when `value` can safely occupy an HTTP header/subprotocol slot.

    Mirrors the discipline Intendant applies in codex_cloud.rs: a token that
    could break the header (CTLs, non-ASCII) is rejected before the wire ever
    sees it.
    """
    if not isinstance(value, str) or not value or len(value) > 8192:
        return False
    for ch in value:
        o = ord(ch)
        if o < 0x21 or o == 0x7F or o > 0x7E:
            return False
    return True


def _load_codex_auth(auth_path=None):
    """Return (access_token, account_id) from the user's Codex login.

    The token is returned to the caller and only ever sent inside the TLS
    handshake; it is never stored anywhere by this module.
    """
    path = Path(auth_path) if auth_path else _codex_home() / "auth.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raise CloudAuthMissing(
            "no Codex login found; open the Codex app or run `codex login`")
    try:
        data = json.loads(raw)
    except ValueError:
        raise CloudAuthMissing("Codex auth.json is not valid JSON")
    tokens = data.get("tokens") if isinstance(data, dict) else None
    if not isinstance(tokens, dict):
        raise CloudAuthMissing(
            "Codex auth.json has no tokens; open the Codex app or run `codex login`")
    token = tokens.get("access_token")
    account = tokens.get("account_id") or ""
    if not _valid_http_token(token):
        raise CloudAuthRejected("Codex access token is missing or malformed")
    if account and not _valid_http_token(account):
        raise CloudAuthRejected("Codex account id is malformed")
    return token, account


def auth_available():
    try:
        _load_codex_auth()
        return True
    except CloudError:
        return False


# ── Minimal RFC 6455 client ─────────────────────────────────────────────────

def _ws_url():
    override = (os.environ.get("CCC_CODEX_CLOUD_WS_URL") or "").strip()
    return override or CLOUD_URL


def _parse_ws_url(url):
    from urllib.parse import urlparse
    parts = urlparse(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("ws", "wss"):
        raise CloudHandshakeError(f"unsupported WebSocket scheme {scheme!r}")
    host = parts.hostname or ""
    if scheme == "wss":
        if host != CLOUD_HOST:
            raise CloudHandshakeError(
                "cloud WebSocket host is pinned to codex-cloud-backend.chatgpt.com")
    elif not test_isolation_active():
        # Plain ws:// exists only so tests can point at an in-process fake.
        raise CloudHandshakeError("cloud WebSocket requires wss://")
    elif host not in ("127.0.0.1", "localhost", "::1"):
        raise CloudHandshakeError("test WebSocket override must be loopback")
    port = parts.port or (443 if scheme == "wss" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return scheme, host, port, path


def _recv_until(sock, marker, limit, deadline):
    buf = bytearray()
    while marker not in buf:
        if time.monotonic() > deadline or len(buf) > limit:
            raise CloudHandshakeError("WebSocket handshake response too large or too slow")
        chunk = sock.recv(8192)
        if not chunk:
            raise CloudClosed("connection closed during handshake")
        buf.extend(chunk)
    head, _, rest = bytes(buf).partition(marker)
    return head, rest


def ws_connect(url=None, *, subprotocols=(), headers=None, token="", account_id="",
               timeout=_CONNECT_TIMEOUT):
    """Open a WebSocket and return (sock, buffered_bytes, negotiated_subproto)."""
    url = url or _ws_url()
    scheme, host, port, path = _parse_ws_url(url)
    deadline = time.monotonic() + timeout
    try:
        raw = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise CloudUnreachable(f"cannot reach {host}: {exc.__class__.__name__}") from None
    try:
        if scheme == "wss":
            ctx = ssl.create_default_context()
            sock = ctx.wrap_socket(raw, server_hostname=host)
        else:
            sock = raw
        sock.settimeout(max(1.0, deadline - time.monotonic()))
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        protos = list(subprotocols)
        if token:
            protos.append(f"openai-bearer.{token}")
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if protos:
            lines.append("Sec-WebSocket-Protocol: " + ", ".join(protos))
        for name, value in (headers or {}).items():
            lines.append(f"{name}: {value}")
        if account_id:
            lines.append(f"chatgpt-account-id: {account_id}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        head, rest = _recv_until(sock, b"\r\n\r\n", _MAX_HEAD, deadline)
        status_line, _, header_blob = head.partition(b"\r\n")
        parts = status_line.split(b" ", 2)
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        if status == 401 or status == 403:
            sock.close()
            raise CloudAuthRejected(
                "cloud backend rejected the Codex login (HTTP %d); "
                "open the Codex app or run `codex login` to refresh it" % status)
        if status != 101:
            sock.close()
            raise CloudHandshakeError(f"WebSocket upgrade failed (HTTP {status})")
        hdrs = {}
        for line in header_blob.split(b"\r\n"):
            if b":" in line:
                name, _, value = line.partition(b":")
                hdrs[name.strip().lower().decode("ascii", "replace")] = value.strip().decode("ascii", "replace")
        expect = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        if hdrs.get("sec-websocket-accept") != expect:
            sock.close()
            raise CloudHandshakeError("WebSocket upgrade returned a bad accept key")
        sock.settimeout(None)
        return sock, rest, hdrs.get("sec-websocket-protocol") or ""
    except CloudError:
        try:
            raw.close()
        except OSError:
            pass
        raise
    except (OSError, ssl.SSLError) as exc:
        try:
            raw.close()
        except OSError:
            pass
        if isinstance(exc, socket.timeout) or "timed out" in str(exc):
            raise CloudTimeout("timed out connecting to the cloud backend") from None
        raise CloudUnreachable(f"cannot reach {host}: {exc.__class__.__name__}") from None


def _ws_send_frame(sock, opcode, payload):
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    mask = secrets.token_bytes(4)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _recv_exact(sock, buffered, count, deadline):
    while len(buffered) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CloudTimeout("timed out reading from the cloud backend")
        try:
            sock.settimeout(remaining)
        except OSError:
            pass
        try:
            chunk = sock.recv(min(1 << 20, count - len(buffered) + 4096))
        except socket.timeout:
            raise CloudTimeout("timed out reading from the cloud backend") from None
        except OSError:
            raise CloudClosed("connection lost while reading") from None
        if not chunk:
            raise CloudClosed("connection closed by the backend")
        buffered.extend(chunk)
    out = bytes(buffered[:count])
    del buffered[:count]
    return out


def _ws_recv_message(sock, buffered, deadline):
    """Return (opcode, payload) for one complete message.

    Handles fragmented messages, ping/pong, and interleaved control frames.
    Raises CloudClosed on close frames and CloudTimeout past `deadline`.
    """
    message = bytearray()
    opcode = 0
    while True:
        head = _recv_exact(sock, buffered, 2, deadline)
        fin = bool(head[0] & 0x80)
        op = head[0] & 0x0F
        masked = bool(head[1] & 0x80)
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", _recv_exact(sock, buffered, 2, deadline))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_exact(sock, buffered, 8, deadline))[0]
            if length >= (1 << 63):
                raise CloudError("invalid WebSocket frame length")
        if length > _MAX_MESSAGE:
            raise CloudError("cloud backend sent a frame beyond the size bound")
        mask = _recv_exact(sock, buffered, 4, deadline) if masked else b""
        if len(message) + length > _MAX_MESSAGE:
            raise CloudError("cloud backend sent a message beyond the size bound")
        payload = _recv_exact(sock, buffered, length, deadline) if length else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if op == 0x9:  # ping
            _ws_send_frame(sock, 0xA, payload[:125])
            continue
        if op == 0xA:  # pong
            continue
        if op == 0x8:  # close
            try:
                _ws_send_frame(sock, 0x8, payload[:125])
            except OSError:
                pass
            raise CloudClosed("cloud backend closed the connection")
        if op in (0x1, 0x2):
            opcode = op
            message = bytearray(payload)
            if fin:
                return opcode, bytes(message)
            continue
        if op == 0x0:  # continuation
            if not opcode:
                raise CloudError("unexpected WebSocket continuation frame")
            message += payload
            if fin:
                return opcode, bytes(message)
            continue
        raise CloudError(f"unsupported WebSocket opcode {op}")


def _redact(text, *secrets_):
    out = str(text or "")
    for secret in secrets_:
        if secret and isinstance(secret, str):
            out = out.replace(secret, "[redacted]")
    return out


class CodexCloudClient:
    """JSON-RPC-over-WebSocket client for the Codex cloud backend.

    Read-only by construction: only the methods in _READ_METHODS may be sent.
    """

    _READ_METHODS = {"initialize", "thread/list", "thread/read", "thread/turns/list"}

    def __init__(self, *, ws_url=None, token=None, account_id=None,
                 timeout=_RPC_TIMEOUT):
        self._ws_url = ws_url
        self._token = token
        self._account_id = account_id
        self._timeout = timeout
        self._sock = None
        self._buffered = bytearray()
        self._next_id = 0

    def _scrub(self, text):
        return _redact(text, self._token, self._account_id)

    def connect(self):
        if self._sock is not None:
            return
        if self._token is None:
            self._token, self._account_id = _load_codex_auth()
        self._sock, rest, _proto = ws_connect(
            self._ws_url,
            subprotocols=("codex-app-server", "codex-client.ccc"),
            token=self._token,
            account_id=self._account_id,
        )
        self._buffered = bytearray(rest)
        try:
            version = "0"
            try:
                import server as _srv
                version = str(getattr(_srv, "__version__", "0") or "0")
            except Exception:
                pass
            self.request("initialize", {"clientInfo": {"name": "ccc", "version": version}})
            self._send({"method": "initialized"})
        except Exception:
            self.close()
            raise

    def _send(self, value):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        try:
            _ws_send_frame(self._sock, 0x1, raw)
        except OSError:
            raise CloudClosed("connection lost while sending") from None

    def request(self, method, params=None, *, timeout=None):
        if method not in self._READ_METHODS:
            raise CloudError(f"method {method} is not allowed (read-only client)")
        if self._sock is None:
            self.connect()
        self._next_id += 1
        rid = self._next_id
        self._send({"id": rid, "method": method, "params": params or {}})
        deadline = time.monotonic() + (timeout or self._timeout)
        while True:
            _op, payload = _ws_recv_message(self._sock, self._buffered, deadline)
            try:
                msg = json.loads(payload)
            except ValueError:
                continue
            if not isinstance(msg, dict) or msg.get("id") != rid:
                continue  # notification or response for another request
            if "error" in msg and msg["error"] is not None:
                err = msg["error"] if isinstance(msg["error"], dict) else {}
                code = err.get("code")
                message = self._scrub(err.get("message") or "remote error")[:400]
                if code in (-32600, -32602):
                    raise CloudSchemaError(message)
                if code in (-32001, -32603) and "unauthorized" in message.lower():
                    raise CloudAuthRejected(message)
                raise CloudRemoteError(message)
            return msg.get("result")

    def list_threads(self, *, limit=_LIST_PAGE_LIMIT):
        """Full thread catalog, following nextCursor (bounded pages)."""
        threads = []
        cursor = None
        for _ in range(_LIST_MAX_PAGES):
            params = {"limit": limit}
            if cursor:
                params["cursor"] = cursor
            result = self.request("thread/list", params) or {}
            data = result.get("data") or result.get("threads") or []
            if isinstance(data, list):
                threads.extend(t for t in data if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return threads

    def read_thread_meta(self, thread_id):
        result = self.request(
            "thread/read", {"threadId": thread_id, "includeTurns": False}) or {}
        thread = result.get("thread") or {}
        return thread if isinstance(thread, dict) else {}

    def iter_turns(self, thread_id, *, items_view="full", limit=_TURNS_PAGE_LIMIT):
        """Yield turn dicts oldest-first via thread/turns/list pagination."""
        cursor = None
        yielded = 0
        for _ in range(_TURNS_MAX_PAGES):
            result = self.request("thread/turns/list", {
                "threadId": thread_id,
                "itemsView": items_view,
                "limit": limit,
                "sortDirection": "asc",
                "cursor": cursor,
            }) or {}
            data = result.get("data") or result.get("turns") or []
            for turn in data:
                if not isinstance(turn, dict):
                    continue
                yield turn
                yielded += 1
                if yielded >= _TURNS_MAX_ITEMS:
                    return
            cursor = result.get("nextCursor")
            if not cursor:
                return

    def close(self):
        sock, self._sock = self._sock, None
        if sock is None:
            return
        try:
            _ws_send_frame(sock, 0x8, b"")
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ── Data helpers ────────────────────────────────────────────────────────────

def valid_thread_id(value):
    return bool(_THREAD_ID_RE.fullmatch(str(value or "").strip()))


def parse_delegation(text):
    """Split a <codex_delegation> userMessage wrapper.

    Returns (source_thread_id, inner_text). Returns ("", original text) for
    ordinary messages.
    """
    match = _DELEGATION_RE.match(text or "")
    if not match:
        return "", text
    return match.group("src"), match.group("input").strip()


def _strip_blobs(value):
    """Replace embedded base64/data-URI payloads with byte-size placeholders.

    Cloud turn frames embed screenshots and attachments as base64; holding or
    serving them verbatim is what made a 109-turn thread weigh ~89 MB. Kept
    text is also hard-capped so a pathological output can't balloon the cache.
    """
    if isinstance(value, str):
        if len(value) > _TEXT_TRUNC:
            return value[:_TEXT_TRUNC] + "\n… [%d bytes truncated]" % (
                len(value.encode("utf-8", "replace")) - _TEXT_TRUNC)
        if len(value) >= _BLOB_MIN:
            if value.startswith("data:") and "base64," in value[:128]:
                semi = value.find(";")
                media = value[5:semi] if 5 < semi < 128 else ""
                return {"type": "cloud_blob", "media": media,
                        "bytes": len(value)}
            head = value[:128]
            if " " not in head and _BLOB_RE.match(value):
                return {"type": "cloud_blob", "bytes": len(value)}
        return value
    if isinstance(value, dict):
        return {k: _strip_blobs(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_blobs(v) for v in value]
    return value


def _shrink_turn_items(turn):
    """Cap oversized tool-result payloads inside a cached turn.

    mcpToolCall results dominate real thread weight (a 109-turn thread held
    ~38 MB of spreadsheet dumps under `result`). The renderer only shows a
    short clip, so anything past _TOOL_RESULT_MAX becomes a preview +
    byte-size placeholder instead of being cached wholesale.
    """
    items = turn.get("items") if isinstance(turn, dict) else None
    if not isinstance(items, list):
        return turn
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("result", "structuredContent"):
            payload = item.get(key)
            if payload is None or isinstance(payload, (str, int, float, bool)):
                continue
            try:
                dumped = json.dumps(payload, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            if len(dumped) > _TOOL_RESULT_MAX:
                item[key] = {
                    "type": "cloud_blob",
                    "bytes": len(dumped.encode("utf-8", "replace")),
                    "preview": dumped[:4000],
                }
    return turn


def _epoch_seconds(value):
    """Normalize cloud timestamps (epoch s, epoch ms, or ISO) to epoch s."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        if v > 1e12:
            return v / 1000.0
        return v if v > 0 else 0.0
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _iso_ts(epoch):
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


# ── Local fallback: the desktop's sidebar cache atom ───────────────────────

_SIDEBAR_CACHE = {"key": None, "threads": []}


def _sidebar_threads():
    """Metadata from the desktop's own cloud-aeon-sidebar-cache-v1 atom.

    This is the degraded-mode catalog: names, cwds, models, timestamps, dot
    profiles and the dot's active root thread id. No turn history exists
    locally. Cached by (mtime, size) so session-list polls stay free.
    """
    path = _codex_home() / ".codex-global-state.json"
    try:
        st = path.stat()
        key = (st.st_mtime, st.st_size)
    except OSError:
        return []
    if _SIDEBAR_CACHE["key"] == key:
        return _SIDEBAR_CACHE["threads"]
    threads = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        atoms = data.get("electron-persisted-atom-state") or {}
        cache = atoms.get("cloud-aeon-sidebar-cache-v1")
        if isinstance(cache, str):
            cache = json.loads(cache)
        if isinstance(cache, dict):
            profiles = cache.get("profilesByThreadId") or {}
            for th in cache.get("threads") or []:
                if not isinstance(th, dict):
                    continue
                tid = str(th.get("id") or "")
                if not valid_thread_id(tid):
                    continue
                profile = profiles.get(tid) or {}
                source = th.get("threadSource") or ""
                entry = {
                    "id": tid,
                    "name": th.get("name") or "",
                    "preview": th.get("preview") or "",
                    "cwd": th.get("cwd") or "",
                    "model": th.get("model") or "",
                    "createdAt": _epoch_seconds(th.get("createdAt")),
                    "updatedAt": _epoch_seconds(th.get("updatedAt")),
                    "threadSource": source,
                    "parentThreadId": th.get("parentThreadId") or "",
                    "status": th.get("status"),
                    "source": th.get("source"),
                    "environments": th.get("environments"),
                    "dotName": profile.get("display_name") or "",
                    "dotRootId": profile.get("active_root_thread_id") or "",
                }
                threads.append(entry)
    except (OSError, ValueError, TypeError, AttributeError):
        threads = []
    _SIDEBAR_CACHE["key"] = key
    _SIDEBAR_CACHE["threads"] = threads
    return threads


# ── Catalog cache (memory TTL + disk + background single-flight refresh) ────

def _cloud_state_dir():
    override = (os.environ.get("CCC_CODEX_CLOUD_STATE_DIR") or "").strip()
    if override:
        return Path(override)
    try:
        base = _core.COMMAND_CENTER_STATE_DIR
    except Exception:
        base = Path.home() / ".claude" / "command-center"
    return Path(base) / "codex-cloud"


def _catalog_disk_path():
    return _cloud_state_dir() / "catalog.json"


def cloud_catalog_signature_path():
    """File whose mtime marks a real catalog change — for the archive's
    corpus signature (writes are skipped when content is identical)."""
    return _catalog_disk_path()


_CATALOG = {
    "ts": 0.0,
    "threads": None,
    "degraded": "cloud catalog still loading",
    "failures": 0,
    "next_retry": 0.0,
    "refreshing": False,
    "live_ok": False,
}
_CATALOG_LOCK = threading.Lock()


def _catalog_disk_read():
    try:
        data = json.loads(_catalog_disk_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    threads = data.get("threads") if isinstance(data, dict) else None
    if not isinstance(threads, list):
        return None
    return data


def _catalog_disk_write(threads):
    try:
        target = _catalog_disk_path()
        try:
            prev = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and prev.get("threads") == threads:
                # The catalog file's mtime is the archive signature's cloud
                # change signal; rewriting identical content would bust the
                # archive cache every refresh TTL for nothing.
                return
        except (OSError, ValueError):
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"fetched_at": time.time(), "threads": threads},
            ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


def _catalog_refresh():
    try:
        with CodexCloudClient() as client:
            threads = client.list_threads()
        threads = [_strip_blobs(t) for t in threads]
        # thread/list carries no dot/parent linkage; the desktop's local
        # sidebar atom does (profile -> active_root_thread_id). Merge it so
        # rows can nest under their dot root without a per-thread thread/read.
        sidebar_by_id = {t.get("id"): t for t in _sidebar_threads()
                         if isinstance(t, dict) and t.get("id")}
        for th in threads:
            if not isinstance(th, dict):
                continue
            sb = sidebar_by_id.get(th.get("id"))
            if not sb:
                continue
            if not th.get("parentThreadId") and sb.get("parentThreadId"):
                th["parentThreadId"] = sb["parentThreadId"]
            if sb.get("dotName"):
                th["dotName"] = sb["dotName"]
            if sb.get("dotRootId"):
                th["dotRootId"] = sb["dotRootId"]
        _catalog_disk_write(threads)
        with _CATALOG_LOCK:
            _CATALOG.update({
                "ts": time.time(),
                "threads": threads,
                "degraded": None,
                "failures": 0,
                "next_retry": 0.0,
                "refreshing": False,
                "live_ok": True,
            })
    except CloudError as exc:
        with _CATALOG_LOCK:
            _CATALOG["failures"] += 1
            delay = min(_BACKOFF_MAX, _BACKOFF_BASE * (2 ** (_CATALOG["failures"] - 1)))
            _CATALOG["next_retry"] = time.time() + delay
            _CATALOG["degraded"] = _catalog_reason(exc)
            _CATALOG["refreshing"] = False
    except Exception as exc:  # never take down the refresh thread
        with _CATALOG_LOCK:
            _CATALOG["failures"] += 1
            _CATALOG["next_retry"] = time.time() + _BACKOFF_BASE
            _CATALOG["degraded"] = f"unexpected error ({exc.__class__.__name__})"
            _CATALOG["refreshing"] = False


def _catalog_reason(exc):
    if isinstance(exc, CloudAuthMissing):
        return "no Codex login; open the Codex app or run `codex login`"
    if isinstance(exc, CloudAuthRejected):
        return "Codex login rejected or expired; open the Codex app or run `codex login`"
    if isinstance(exc, CloudSchemaError):
        return f"cloud schema changed: {exc}"
    if isinstance(exc, (CloudUnreachable, CloudTimeout)):
        return "cloud backend unreachable"
    if isinstance(exc, CloudHandshakeError):
        return f"cloud handshake failed: {exc}"
    return str(exc)[:160]


def _schedule_catalog_refresh(force=False):
    """Kick a background catalog fetch if one is warranted. Never blocks."""
    if test_isolation_active() and not os.environ.get("CCC_CODEX_CLOUD_WS_URL"):
        return False
    with _CATALOG_LOCK:
        now = time.time()
        if _CATALOG["refreshing"]:
            return False
        if not force and now < _CATALOG["next_retry"]:
            return False
        if not auth_available():
            _CATALOG["degraded"] = (
                "no Codex login; open the Codex app or run `codex login`")
            _CATALOG["next_retry"] = now + _BACKOFF_BASE
            return False
        _CATALOG["refreshing"] = True
    threading.Thread(
        target=_catalog_refresh, daemon=True, name="codex-cloud-catalog").start()
    return True


def _normalize_live_thread(th):
    """Uniform shape for a live-catalog thread dict (already blob-stripped)."""
    tid = str(th.get("id") or "")
    return {
        "id": tid,
        "name": th.get("name") or "",
        "preview": th.get("preview") or "",
        "cwd": th.get("cwd") or "",
        "model": th.get("model") or "",
        "createdAt": _epoch_seconds(th.get("createdAt")),
        "updatedAt": _epoch_seconds(th.get("updatedAt")),
        "threadSource": th.get("threadSource") or "",
        "parentThreadId": th.get("parentThreadId") or "",
        "status": th.get("status"),
        "source": th.get("source"),
        "environments": th.get("environments"),
        "dotName": th.get("dotName") or "",
        "dotRootId": th.get("dotRootId") or "",
    }


def cloud_catalog(*, allow_refresh=True):
    """(threads, degraded_reason, source) for the session-list path.

    Never opens a socket on the caller's thread: a stale/empty catalog just
    schedules the background refresh and serves what we already have.
    """
    now = time.time()
    with _CATALOG_LOCK:
        mem = _CATALOG["threads"]
        fresh = mem is not None and (now - _CATALOG["ts"]) < _CATALOG_TTL
        degraded = _CATALOG["degraded"]
        live_ok = _CATALOG["live_ok"]
    if fresh:
        return mem, None, "live"
    if allow_refresh:
        _schedule_catalog_refresh()
    if mem is not None:
        return mem, degraded, "live-stale" if live_ok else "disk"
    disk = _catalog_disk_read()
    if disk is not None:
        threads = [_normalize_live_thread(t) for t in disk.get("threads") or []
                   if isinstance(t, dict) and valid_thread_id(t.get("id"))]
        with _CATALOG_LOCK:
            if _CATALOG["threads"] is None:
                _CATALOG["threads"] = threads
                _CATALOG["ts"] = disk.get("fetched_at") or 0.0
        if threads:
            return threads, degraded or "serving cached catalog", "disk"
    sidebar = _sidebar_threads()
    if sidebar:
        return sidebar, degraded or "cloud catalog unavailable; showing cached metadata", "sidebar"
    # Negative caching: remember "no catalog data" for one TTL so repeated
    # is_cloud_thread_id lookups don't retry the disk+sidebar reads per call.
    with _CATALOG_LOCK:
        if _CATALOG["threads"] is None:
            _CATALOG["threads"] = []
            _CATALOG["ts"] = now
    return [], degraded or "cloud threads unavailable", "none"


def cloud_catalog_updated_at(thread_id):
    """Catalog-freshness timestamp for cache keys; 0 when unknown."""
    threads, _degraded, _src = cloud_catalog(allow_refresh=False)
    for t in threads:
        if t.get("id") == thread_id:
            return _epoch_seconds(t.get("updatedAt"))
    return 0.0


def is_cloud_thread_id(session_id):
    """True when `session_id` is a known cloud thread. Local data only."""
    sid = str(session_id or "").strip()
    if not valid_thread_id(sid):
        return False
    threads, _degraded, _src = cloud_catalog(allow_refresh=False)
    return any(t.get("id") == sid for t in threads)


def cloud_thread_row_for(session_id):
    sid = str(session_id or "").strip()
    threads, degraded, _src = cloud_catalog(allow_refresh=False)
    for t in threads:
        if t.get("id") == sid:
            return t, degraded
    return None, degraded


def cloud_parent_id(entry):
    """Parent session id for list nesting: catalog parentThreadId first, then
    the owning dot's active root thread (aeon_child/dreaming nest under it)."""
    if not isinstance(entry, dict):
        return ""
    parent = str(entry.get("parentThreadId") or "").strip()
    if parent and valid_thread_id(parent):
        return parent
    source = str(entry.get("threadSource") or "")
    root = str(entry.get("dotRootId") or "").strip()
    if source in ("aeon_child", "dreaming") and valid_thread_id(root) and root != entry.get("id"):
        return root
    return ""


# ── Per-thread transcript cache + normalized event mapping ─────────────────

def _thread_cache_path(thread_id):
    return _cloud_state_dir() / "threads" / f"{thread_id}.json"


# (mtime, size)-gated memo so ?after= polling against an open cloud thread
# stats the cache file instead of re-decoding a multi-MB JSON per poll.
_THREAD_MEMO = {}


def _thread_cache_read(thread_id):
    path = _thread_cache_path(thread_id)
    try:
        st = path.stat()
    except OSError:
        _THREAD_MEMO.pop(thread_id, None)
        return None
    key = (st.st_mtime, st.st_size)
    memo = _THREAD_MEMO.get(thread_id)
    if memo and memo.get("key") == key:
        return memo.get("data")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        _THREAD_MEMO.pop(thread_id, None)
        return None
    if not isinstance(data, dict) or data.get("thread_id") != thread_id:
        return None
    if not isinstance(data.get("turns"), list):
        return None
    _THREAD_MEMO[thread_id] = {"key": key, "data": data}
    return data


def _thread_cache_write(thread_id, updated_at, meta, turns):
    try:
        target = _thread_cache_path(thread_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "thread_id": thread_id,
            "updated_at": updated_at,
            "fetched_at": time.time(),
            "meta": meta,
            "turns": turns,
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass


# Thread ids with a background turn refetch in flight (single-flight per id).
_THREAD_REFRESHING = set()
_THREAD_REFRESH_LOCK = threading.Lock()


def _cached_result(cached, *, degraded=None, refreshing=False):
    out = {
        "meta": cached.get("meta") or {},
        "turns": cached.get("turns") or [],
        "updated_at": _epoch_seconds(cached.get("updated_at")),
        "from_cache": True,
        "degraded": degraded,
    }
    if refreshing:
        out["refreshing"] = True
    return out


def _fetch_thread_live(thread_id, catalog_updated):
    """Open the socket, pull every turn, strip blobs, write the cache."""
    with CodexCloudClient() as client:
        meta = client.read_thread_meta(thread_id)
        turns = list(client.iter_turns(thread_id))
    updated_at = _epoch_seconds(meta.get("updatedAt")) or catalog_updated or time.time()
    meta = _strip_blobs(meta)
    turns = [_shrink_turn_items(_strip_blobs(t)) for t in turns]
    _thread_cache_write(thread_id, updated_at, meta, turns)
    return {
        "meta": meta, "turns": turns, "updated_at": updated_at,
        "from_cache": False, "degraded": None,
    }


def _background_thread_refresh(thread_id, catalog_updated):
    try:
        _fetch_thread_live(thread_id, catalog_updated)
    except Exception:  # stale cache keeps serving; next stale open retries
        pass
    finally:
        with _THREAD_REFRESH_LOCK:
            _THREAD_REFRESHING.discard(thread_id)


def _schedule_thread_refresh(thread_id, catalog_updated):
    """Kick a background turn refetch for `thread_id`. Never blocks."""
    with _THREAD_REFRESH_LOCK:
        if thread_id in _THREAD_REFRESHING:
            return False
        _THREAD_REFRESHING.add(thread_id)
    threading.Thread(
        target=_background_thread_refresh, args=(thread_id, catalog_updated),
        daemon=True, name=f"codex-cloud-thread-{thread_id[:8]}").start()
    return True


def fetch_cloud_thread(thread_id, *, force=False):
    """{meta, turns, updated_at, from_cache, degraded} for one open.

    The ONLY path that opens a WebSocket for turn bodies. A cold miss (no
    cache) fetches inline; a stale cache (catalog updatedAt moved past it)
    is served immediately while a single-flight background refetch rewrites
    the cache. Inline refetch of an active multi-MB thread blocked conv
    opens for 8-13s (CCC-1254); the cache file's (mtime, size) change makes
    the next ?after= poll pick up the new turns. Blobs are stripped before
    caching.
    """
    if not valid_thread_id(thread_id):
        raise CloudError("invalid thread id", reason="invalid_thread_id")
    catalog_updated = cloud_catalog_updated_at(thread_id)
    cached = _thread_cache_read(thread_id)
    if cached is not None and not force:
        cached_updated = _epoch_seconds(cached.get("updated_at"))
        if not catalog_updated or cached_updated >= catalog_updated:
            return _cached_result(cached)
    test_offline = (test_isolation_active()
                    and not os.environ.get("CCC_CODEX_CLOUD_WS_URL"))
    if test_offline:
        if cached is not None:
            return _cached_result(cached, degraded="cloud fetch disabled in tests")
        raise CloudUnreachable("cloud fetch disabled in tests")
    if cached is not None and not force:
        _schedule_thread_refresh(thread_id, catalog_updated)
        return _cached_result(cached, refreshing=True)
    try:
        return _fetch_thread_live(thread_id, catalog_updated)
    except CloudError as exc:
        if cached is not None:
            return _cached_result(
                cached, degraded=f"stale transcript ({_catalog_reason(exc)})")
        raise


def _cloud_user_text(item):
    parts = []
    content = item.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
    elif isinstance(content, str):
        parts.append(content)
    return "\n\n".join(p for p in parts if p).strip()


def _clip(text, limit=800):
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "\n..."


def cloud_turns_to_events(turns, *, line_start=1):
    """Map cloud turn items to the normalized events the existing Codex
    transcript renderer already draws (same shapes _parse_codex_event emits).
    Returns (events, delegation_parent_id)."""
    events = []
    delegation_parent = ""
    line = line_start

    def emit(ev):
        nonlocal line
        ev["line"] = line
        line += 1
        events.append(ev)

    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        turn_id = str(turn.get("id") or "")
        ts = _iso_ts(_epoch_seconds(turn.get("startedAt")))
        for item in turn.get("items") or []:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            iid = str(item.get("id") or "")
            if itype == "userMessage":
                text = _cloud_user_text(item)
                parent, inner = parse_delegation(text)
                if parent:
                    delegation_parent = delegation_parent or parent
                    text = inner
                try:
                    text = _core._strip_ccc_session_state_instruction(text)
                except Exception:
                    pass  # bare-module context (tests): strip is cosmetic
                if not text:
                    continue
                ev = {"ts": ts, "type": "user_text", "text": text, "images": []}
                if parent:
                    ev["delegated_from_thread"] = parent
                if turn_id:
                    ev["turn_id"] = turn_id
                emit(ev)
            elif itype == "agentMessage":
                text = str(item.get("text") or "").strip()
                if not text:
                    continue
                emit({"ts": ts, "type": "assistant",
                      "message_id": f"codex-cloud-{iid or line}",
                      "turn_id": turn_id,
                      "blocks": [{"kind": "text", "text": text}]})
            elif itype == "reasoning":
                parts = []
                for section in (item.get("summary"), item.get("content")):
                    for entry in section or []:
                        if isinstance(entry, dict) and isinstance(entry.get("text"), str):
                            parts.append(entry["text"])
                        elif isinstance(entry, str):
                            parts.append(entry)
                text = "\n\n".join(p.strip() for p in parts if p.strip()).strip()
                if not text:
                    continue
                emit({"ts": ts, "type": "assistant",
                      "message_id": f"codex-cloud-reasoning-{iid or line}",
                      "turn_id": turn_id,
                      "blocks": [{"kind": "thinking", "text": text}]})
            elif itype == "commandExecution":
                command = str(item.get("command") or "")
                emit({"ts": ts, "type": "assistant",
                      "message_id": f"codex-cloud-cmd-{iid or line}",
                      "turn_id": turn_id,
                      "blocks": [{"kind": "tool_use", "name": "Bash",
                                  "detail": _clip(command, 200), "command": command,
                                  "id": iid}]})
                output = str(item.get("aggregatedOutput") or "")
                exit_code = item.get("exitCode")
                if output or (isinstance(exit_code, int) and exit_code != 0):
                    emit({"ts": ts, "type": "tool_result",
                          "tool_use_id": iid, "turn_id": turn_id,
                          "text": _clip(output),
                          "is_error": bool(isinstance(exit_code, int) and exit_code != 0)})
            elif itype == "mcpToolCall":
                server = str(item.get("server") or "")
                tool = str(item.get("tool") or "tool")
                label = f"{server}:{tool}" if server else tool
                try:
                    detail = _clip(json.dumps(item.get("arguments") or {},
                                              ensure_ascii=False), 200)
                except (TypeError, ValueError):
                    detail = ""
                emit({"ts": ts, "type": "assistant",
                      "message_id": f"codex-cloud-mcp-{iid or line}",
                      "turn_id": turn_id,
                      "blocks": [{"kind": "tool_use", "name": label,
                                  "detail": detail, "id": iid}]})
                result = item.get("result")
                error = item.get("error")
                if result is not None or error:
                    if isinstance(result, dict) and result.get("type") == "cloud_blob":
                        out = "%s\n… [%d bytes omitted]" % (
                            str(result.get("preview") or ""),
                            int(result.get("bytes") or 0))
                    else:
                        try:
                            out = result if isinstance(result, str) else json.dumps(
                                result, ensure_ascii=False)
                        except (TypeError, ValueError):
                            out = str(result)
                    emit({"ts": ts, "type": "tool_result",
                          "tool_use_id": iid, "turn_id": turn_id,
                          "text": _clip(out), "is_error": bool(error)})
            elif itype == "functionCallOutput":
                out = item.get("output")
                if not isinstance(out, str):
                    try:
                        out = json.dumps(out, ensure_ascii=False)
                    except (TypeError, ValueError):
                        out = str(out)
                emit({"ts": ts, "type": "tool_result",
                      "tool_use_id": str(item.get("name") or iid),
                      "turn_id": turn_id, "text": _clip(out), "is_error": False})
            elif itype == "fileChange":
                changes = [c for c in (item.get("changes") or [])
                           if isinstance(c, dict)]
                files = []
                for change in changes:
                    entry = {
                        "path": str(change.get("path") or ""),
                        "kind": change.get("kind"),
                        "diff": _clip(str(change.get("diff") or ""), 40000),
                    }
                    files.append(entry)
                if files:
                    emit({"ts": ts, "type": "assistant",
                          "message_id": f"codex-cloud-file-{iid or line}",
                          "turn_id": turn_id,
                          "blocks": [{"kind": "diff", "files": files,
                                      "id": iid}]})
            elif itype == "contextCompaction":
                emit({"ts": ts, "type": "system", "subtype": "compact_boundary",
                      "engine": "codex", "turn_id": turn_id,
                      "compact": {"trigger": "auto", "pre_tokens": 0,
                                  "post_tokens": 0, "duration_ms": 0}})
            elif itype == "webSearch":
                query = str(item.get("query") or "")
                emit({"ts": ts, "type": "assistant",
                      "message_id": f"codex-cloud-web-{iid or line}",
                      "turn_id": turn_id,
                      "blocks": [{"kind": "tool_use", "name": "webSearch",
                                  "detail": _clip(query, 200), "id": iid}]})
            elif itype == "imageView":
                emit({"ts": ts, "type": "assistant",
                      "message_id": f"codex-cloud-img-{iid or line}",
                      "turn_id": turn_id,
                      "blocks": [{"kind": "tool_use", "name": "imageView",
                                  "detail": str(item.get("path") or ""), "id": iid}]})
            elif itype == "imageGeneration":
                emit({"ts": ts, "type": "assistant",
                      "message_id": f"codex-cloud-imggen-{iid or line}",
                      "turn_id": turn_id,
                      "blocks": [{"kind": "image_generation", "id": iid,
                                  "prompt": _clip(item.get("revisedPrompt") or "", 600),
                                  "status": str(item.get("status") or "completed")}]})
            elif itype in ("collabAgentToolCall", "subAgentActivity"):
                label = str(item.get("tool") or item.get("kind") or "agent")
                emit({"ts": ts, "type": "system", "subtype": "codex_subagent",
                      "kind": f"cloud_{itype}", "turn_id": turn_id,
                      "agent_thread_id": str(item.get("agentThreadId") or ""),
                      "text": f"Agent {label}"})
            elif itype == "plan":
                text = str(item.get("text") or "").strip()
                if text:
                    emit({"ts": ts, "type": "assistant",
                          "message_id": f"codex-cloud-plan-{iid or line}",
                          "turn_id": turn_id,
                          "blocks": [{"kind": "text", "text": text}]})
            elif itype == "dynamicToolCall":
                emit({"ts": ts, "type": "assistant",
                      "message_id": f"codex-cloud-dyn-{iid or line}",
                      "turn_id": turn_id,
                      "blocks": [{"kind": "tool_use",
                                  "name": str(item.get("tool") or "tool"),
                                  "detail": "", "id": iid}]})
            # hookPrompt, sleep, enteredReviewMode, exitedReviewMode and
            # unknown future types are intentionally skipped.
        status = str(turn.get("status") or "")
        if status and status != "inProgress":
            result = {"ts": _iso_ts(_epoch_seconds(turn.get("completedAt"))) or ts,
                      "type": "result",
                      "duration_ms": turn.get("durationMs") or "?",
                      "turn_id": turn_id}
            error = turn.get("error")
            if error or status not in ("completed", ""):
                result["is_error"] = True
                try:
                    result["text"] = _clip(json.dumps(error, ensure_ascii=False)
                                           if not isinstance(error, str) else error)
                except (TypeError, ValueError):
                    result["text"] = str(error)
            emit(result)
    return events, delegation_parent


# (mtime, size)-gated memo for the mapped event list: re-deriving 1.9k events
# from a cached thread costs ~0.4s, which every ?after= poll would otherwise
# pay. The cache file stat is the invalidation key — a refetch rewrites it.
_EVENTS_MEMO = {}


def _cloud_events_memo(thread_id, turns):
    try:
        st = _thread_cache_path(thread_id).stat()
        key = (st.st_mtime, st.st_size)
    except OSError:
        key = None
    memo = _EVENTS_MEMO.get(thread_id)
    if key and memo and memo.get("key") == key:
        return memo.get("events"), memo.get("parent")
    events, parent = cloud_turns_to_events(turns)
    if key:
        _EVENTS_MEMO[thread_id] = {"key": key, "events": events, "parent": parent}
    return events, parent


def parse_cloud_conversation(thread_id, *, after_line=0):
    """parse_conversation-compatible result for a cloud thread.

    Adds `cloud` metadata fields (additive only). On failure returns a result
    with a single system event carrying the degraded reason so the pane shows
    an explanation instead of an empty view.
    """
    try:
        fetched = fetch_cloud_thread(thread_id)
    except CloudError as exc:
        reason = _catalog_reason(exc)
        row, _d = cloud_thread_row_for(thread_id)
        meta = row or {}
        return {
            "events": [{
                "line": 1, "ts": "",
                "type": "system", "subtype": "cloud_unavailable",
                "text": f"cloud thread, transcript unavailable: {reason}",
            }],
            "last_line": 1,
            "engine": "codex",
            "codex_cloud": True,
            "cloud_degraded": reason,
            "turn_count": 0,
            "cloud_thread": {
                "id": thread_id,
                "name": meta.get("name") or "",
                "model": meta.get("model") or "",
                "cwd": meta.get("cwd") or "",
                "thread_source": meta.get("threadSource") or "",
            },
        }
    events, delegation_parent = _cloud_events_memo(thread_id, fetched["turns"])
    meta = fetched.get("meta") or {}
    parent = (str(meta.get("parentThreadId") or "")
              or delegation_parent
              or cloud_parent_id(meta))
    last_line = events[-1]["line"] if events else 0
    if after_line:
        events = [e for e in events if e["line"] > after_line]
    result = {
        "events": events,
        "last_line": last_line,
        "engine": "codex",
        "codex_cloud": True,
        "turn_count": len(fetched["turns"]),
        "cloud_from_cache": bool(fetched.get("from_cache")),
        "cloud_thread": {
            "id": thread_id,
            "name": meta.get("name") or "",
            "model": meta.get("model") or "",
            "cwd": meta.get("cwd") or "",
            "thread_source": meta.get("threadSource") or "",
            "status": meta.get("status"),
            "parent_thread_id": parent or "",
        },
    }
    if fetched.get("degraded"):
        result["cloud_degraded"] = fetched["degraded"]
    return result


# ── Session-list rows ───────────────────────────────────────────────────────

def _cloud_cwds(entry):
    """All candidate cwds for a catalog entry (thread cwd + environments)."""
    seen = []
    for value in [entry.get("cwd"),
                  *((e or {}).get("cwd") for e in (entry.get("environments") or [])
                    if isinstance(e, dict)),
                  *(r for e in (entry.get("environments") or []) if isinstance(e, dict)
                    for r in (e.get("runtimeWorkspaceRoots") or []))]:
        if isinstance(value, str) and value and value not in seen:
            seen.append(value)
    return seen


def find_codex_cloud_conversations(
    repo_path=None,
    include_old=True,
    repo_only=True,
    progress=None,
    limit=None,
    resolve_pr_states=True,
    resolve_worktree_dirty=True,
):
    """Session-list rows for Codex cloud (dot/aeon) threads.

    Row shape matches find_codex_conversations output so the existing
    renderers, sorters, and hierarchy code work unchanged. Cloud-only fields
    (`codex_cloud`, `cloud_degraded`, `thread_source`, `dot_name`) are
    additive. This function never touches the network — the catalog is
    served from the TTL/disk cache or the desktop sidebar cache.
    """
    threads, degraded, _source = cloud_catalog()
    if not threads:
        return []
    try:
        name_overrides = _core._load_session_name_overrides()
    except Exception:
        name_overrides = {}
    try:
        archived_set, trashed_set = _core._load_conversation_lifecycle_sets()
    except Exception:
        archived_set, trashed_set = set(), set()
    try:
        repo_pins = _core._load_repo_pins()
    except Exception:
        repo_pins = {}
    try:
        last_interactions = _core._load_last_interactions()
    except Exception:
        last_interactions = {}

    if repo_only:
        repo_path = _core.resolve_repo_path(repo_path)
        repo_path_obj = Path(repo_path)
    git_top_cache = {}
    cutoff = _core._session_scan_cutoff_ts(include_old)
    max_rows = _core._session_scan_file_limit(include_old)

    out = []
    for entry in threads:
        sid = str(entry.get("id") or "")
        if not valid_thread_id(sid):
            continue
        pinned = repo_pins.get(sid)
        cwds = _cloud_cwds(entry)
        local_cwd = next(
            (c for c in cwds if Path(c).is_absolute() and Path(c).is_dir()), "")
        if repo_only:
            if pinned:
                if pinned != repo_path:
                    continue
            else:
                if not any(
                    _core._codex_cwd_matches_repo(c, repo_path_obj, git_top_cache)
                    for c in cwds
                ):
                    continue
        modified = (_epoch_seconds(entry.get("updatedAt"))
                    or _epoch_seconds(entry.get("createdAt")))
        freshness = max(modified, last_interactions.get(sid) or 0)
        if not include_old and cutoff > 0 and freshness < cutoff:
            continue
        if not include_old and max_rows > 0 and len(out) >= max_rows:
            continue
        title = str(entry.get("name") or "").strip()
        preview = _core._strip_ccc_session_state_instruction(
            str(entry.get("preview") or "")).strip()
        first_message = preview[:200]
        display_name = (_core._truncate_session_name(name_overrides.get(sid))
                        or _core._truncate_session_name(title)
                        or _core._truncate_session_name(preview)
                        or f"Cloud thread {sid[:8]}")
        cwd = local_cwd or (cwds[0] if cwds else "")
        folder_path = pinned or cwd
        if folder_path:
            try:
                _git_root = _core._find_git_root(folder_path)
                folder_label = _core._resolve_dir_case(_git_root or folder_path)
            except Exception:
                folder_label = folder_path
        else:
            folder_label = "Codex Cloud"
        source = str(entry.get("threadSource") or "")
        status = entry.get("status")
        status_type = status.get("type") if isinstance(status, dict) else ""
        is_live = status_type in ("active", "inProgress", "running", "loaded")
        out.append({
            "id": sid,
            "session_id": sid,
            "source": "codex",
            "engine": "codex",
            "codex_cloud": True,
            "cloud": True,
            "thread_source": source,
            "dot_name": str(entry.get("dotName") or ""),
            "dot_root_id": str(entry.get("dotRootId") or ""),
            "cloud_degraded": degraded,
            "timestamp": "",
            "branch": "",
            "git_branch": "",
            "first_message": first_message,
            "display_name": display_name,
            "status_rail_title": title or display_name,
            "ai_title": title or None,
            "name_overridden": bool(name_overrides.get(sid)),
            "last_prompt": preview[:200],
            "size": 0,
            "modified": modified,
            "modified_human": time.strftime(
                "%Y-%m-%d %H:%M", time.localtime(modified)) if modified else "",
            "mtime": modified,
            "jsonl_path": "",
            "folder_label": folder_label,
            "folder_path": folder_path,
            "session_cwd": local_cwd,
            "session_cwd_exists": bool(local_cwd),
            "session_cwd_is_worktree": bool(
                local_cwd and (Path(local_cwd) / ".git").is_file()),
            "worktree_dirty": False,
            "archived": sid in archived_set,
            "trashed": sid in trashed_set,
            "pinned_repo": bool(pinned),
            "last_interacted": last_interactions.get(sid),
            "is_live": is_live,
            "spawn_pid": None,
            "parent_session_id": cloud_parent_id(entry),
            "model": str(entry.get("model") or ""),
            "cloud_status": status_type,
        })
    out.sort(key=lambda r: r.get("last_interacted") or r.get("modified") or 0,
             reverse=True)
    if progress:
        progress("codex-cloud", state="done", count=len(out),
                 detail=f"{len(out)} Codex cloud thread(s) ready.")
    if limit and len(out) > int(limit):
        out = out[: int(limit)]
    return out
