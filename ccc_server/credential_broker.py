# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Bounded, one-shot credential requests. No transcript, event or log writes.

Only explicitly remembered values touch disk: owner-only plaintext files in
an owner-only directory. This is the documented portable fallback, not an
additional encryption scheme. Local processes sharing the OS user are trusted.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import urllib.parse

MAX_VALUE_BYTES = 16 * 1024
MAX_BODY_BYTES = 128 * 1024  # includes JSON's escaped representation
MAX_REQUESTS = 128
KINDS = ("password", "token", "ssh-passphrase", "otp")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


class BrokerError(ValueError):
    """Fixed, public-safe error text; never include supplied data."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def valid_name(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise BrokerError("Use a service name of 1-64 letters, digits, dots, underscores or hyphens")
    return name


def valid_value(value):
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > MAX_VALUE_BYTES:
        raise BrokerError("Credential must contain 1-16384 UTF-8 bytes")
    if "\x00" in value:
        raise BrokerError("Credential cannot contain a NUL character")
    return value  # passwords may have significant leading/trailing whitespace


class PrivateStore:
    """Fail closed on unsafe directory/files; atomic writes, private from birth."""

    def __init__(self, root):
        self.root = Path(root)

    def _directory(self):
        self.root.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.root.mkdir(mode=0o700)
        except FileExistsError:
            pass
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(fd)
            raise BrokerError("Credential directory must be owned by you with mode 0700", 503)
        return fd

    @staticmethod
    def _filename(name, kind):
        return hashlib.sha256((name + "\0" + kind).encode()).hexdigest() + ".json"

    def read(self, name, kind):
        if not self.root.exists() and not self.root.is_symlink():
            return None
        directory = self._directory()
        try:
            try:
                fd = os.open(self._filename(name, kind), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                    raise BrokerError("Saved credential file is not private", 503)
                payload = json.loads(stream.read(MAX_BODY_BYTES + 1))
                if payload.get("name") != name or payload.get("kind") != kind:
                    raise BrokerError("Saved credential is invalid", 503)
                return valid_value(payload.get("value"))
        finally:
            os.close(directory)

    def write(self, name, kind, value):
        directory = self._directory()
        temporary = "." + secrets.token_hex(16) + ".tmp"
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"name": name, "kind": kind, "value": value}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._filename(name, kind),
                       src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)

    def forget(self, name):
        if not self.root.exists() and not self.root.is_symlink():
            return
        directory = self._directory()
        try:
            for kind in KINDS:
                try:
                    os.unlink(self._filename(name, kind), dir_fd=directory)
                except FileNotFoundError:
                    pass
        finally:
            os.close(directory)


class Broker:
    def __init__(self, root, clock=time.monotonic):
        self.store = PrivateStore(root)
        self.clock = clock
        self.lock = threading.RLock()
        self.requests = {}
        self._expiry_timer = None
        self._timer_generation = 0

    def _purge(self):
        now = self.clock()
        for request_id, row in list(self.requests.items()):
            if row["deadline"] <= now:
                del self.requests[request_id]

    def _sweep(self, generation):
        with self.lock:
            # A cancelled callback may already be waiting for this lock.
            # It must not clear or duplicate a newer timer.
            if generation != self._timer_generation:
                return
            self._expiry_timer = None
            self._purge()
            self._schedule_sweep()

    def _schedule_sweep(self):
        # One daemon timer per broker, never one thread per request. Clears
        # abandoned values even when no browser/agent is polling anymore.
        if self.requests and self._expiry_timer is None:
            delay = max(0.01, min(r["deadline"] for r in self.requests.values()) - self.clock())
            self._timer_generation += 1
            self._expiry_timer = threading.Timer(delay, self._sweep, (self._timer_generation,))
            self._expiry_timer.daemon = True
            self._expiry_timer.start()

    def shutdown(self):
        with self.lock:
            self.requests.clear()
            self._timer_generation += 1
            if self._expiry_timer:
                self._expiry_timer.cancel()
                self._expiry_timer = None

    def request(self, data):
        name = valid_name(data.get("name"))
        kind = data.get("kind", "password")
        if kind not in KINDS:
            raise BrokerError("Unknown credential kind")
        reason = data.get("reason")
        session_id = data.get("session_id", "")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise BrokerError("A reason of 1-500 characters is required")
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{0,128}", session_id):
            raise BrokerError("Invalid session identifier")
        timeout = data.get("timeout", 300)
        if type(timeout) is not int or not 1 <= timeout <= 900:
            raise BrokerError("Timeout must be an integer from 1 to 900 seconds")
        with self.lock:
            self._purge()
            if len(self.requests) >= MAX_REQUESTS:
                raise BrokerError("Too many pending credential requests", 429)
            value = self.store.read(name, kind) if kind != "otp" else None
            request_id = secrets.token_urlsafe(24)
            token = secrets.token_urlsafe(32)
            self.requests[request_id] = {
                "id": request_id, "name": name, "kind": kind, "reason": reason,
                "session_id": session_id, "expires_at": time.time() + timeout,
                "deadline": self.clock() + timeout,
                "token_hash": hashlib.sha256(token.encode()).digest(),
                "status": "ready" if value is not None else "pending", "value": value,
            }
            # An existing timer may target a later request. Reschedule for
            # the earliest expiry without ever starting a polling worker.
            if self._expiry_timer:
                self._expiry_timer.cancel()
                self._expiry_timer = None
            self._schedule_sweep()
            return {"id": request_id, "token": token, "status": self.requests[request_id]["status"]}

    def pending(self):
        with self.lock:
            self._purge()
            keys = ("id", "name", "kind", "reason", "session_id", "expires_at")
            return [{k: r[k] for k in keys} for r in self.requests.values() if r["status"] == "pending"]

    def _row(self, request_id):
        self._purge()
        row = self.requests.get(request_id) if isinstance(request_id, str) else None
        if row is None:
            raise BrokerError("Credential request expired or already consumed", 410)
        return row

    def resolve(self, data):
        with self.lock:
            row = self._row(data.get("id"))
            if row["status"] != "pending":
                raise BrokerError("Credential request already answered", 409)
            if data.get("deny") is True:
                row["status"] = "denied"
                return
            if type(data.get("remember", False)) is not bool:
                raise BrokerError("Remember must be true or false")
            value = valid_value(data.get("value"))
            if data.get("remember"):
                if row["kind"] == "otp":
                    raise BrokerError("One-time codes cannot be remembered")
                self.store.write(row["name"], row["kind"], value)
            row["value"] = value
            row["status"] = "ready"

    def take(self, data, cancel=False):
        with self.lock:
            row = self._row(data.get("id"))
            token = data.get("token")
            if (not isinstance(token, str) or len(token) > 128 or not hmac.compare_digest(
                    row["token_hash"], hashlib.sha256(token.encode()).digest())):
                raise BrokerError("Invalid retrieval token", 403)
            if cancel:
                del self.requests[row["id"]]
                return {"status": "cancelled"}
            result = {"status": row["status"]}
            if row["status"] == "ready":
                result["value"] = row["value"]
            if row["status"] != "pending":
                del self.requests[row["id"]]
            return result

    def forget(self, name):
        name = valid_name(name)
        with self.lock:
            self.store.forget(name)
            # Revoke any outstanding ready deliveries of the forgotten value.
            for request_id, row in list(self.requests.items()):
                if row["name"] == name:
                    del self.requests[request_id]


_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def broker():
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            from ccc_server.paths import COMMAND_CENTER_STATE_DIR
            _INSTANCE = Broker(COMMAND_CENTER_STATE_DIR / "secrets")
        return _INSTANCE


def _reply(handler, data, status=200):
    # Do not route secret-bearing responses through generic event/log hooks.
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Pragma", "no-cache")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


def handle(handler, method):
    """Called only AFTER CCC's same-origin POST and phone PIN gates."""
    try:
        parsed = urllib.parse.urlsplit(handler.path)
        route = parsed.path.rstrip("/")
        if parsed.query:
            raise BrokerError("Credential endpoints do not accept URL parameters")
        from ccc_server.phone_access import is_remote_request
        remote = is_remote_request(handler.client_address[0], handler.headers)
        if method == "GET" and route == "/api/secrets/pending":
            _reply(handler, {"requests": broker().pending()})
            return
        if method != "POST" or route not in {
            "/api/secrets/request", "/api/secrets/resolve", "/api/secrets/take",
            "/api/secrets/cancel", "/api/secrets/forget",
        }:
            raise BrokerError("Unknown credential endpoint", 404)
        if route != "/api/secrets/resolve" and (remote or handler.headers.get("Origin")):
            # Agent capabilities are only issued to local programs. A trusted
            # phone/browser can answer a prompt, never retrieve its value.
            raise BrokerError("Credential agent operations require a direct local program", 403)
        if remote and not handler.headers.get("Origin", "").startswith("https://"):
            raise BrokerError("Remote credential submission requires HTTPS", 403)
        if handler.headers.get_content_type() != "application/json":
            raise BrokerError("Expected application/json", 415)
        length = int(handler.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_BODY_BYTES or handler.headers.get("Transfer-Encoding"):
            raise BrokerError("Credential request body missing or too large", 413)
        data = json.loads(handler.rfile.read(length))
        if not isinstance(data, dict):
            raise BrokerError("Expected a JSON object")
        instance = broker()
        if route.endswith("/request"):
            result = instance.request(data)
        elif route.endswith("/resolve"):
            instance.resolve(data)
            result = {"ok": True}
        elif route.endswith("/forget"):
            instance.forget(data.get("name"))
            result = {"ok": True}
        else:
            result = instance.take(data, cancel=route.endswith("/cancel"))
        _reply(handler, result)
    except BrokerError as exc:
        _reply(handler, {"error": str(exc)}, exc.status)
    except (OSError, ValueError, TypeError, AttributeError):
        # No exception repr, request body, credential or token in responses/logs.
        _reply(handler, {"error": "Credential operation failed; check local storage permissions and request format"}, 400)
