"""kap-server transport for Kimi — Stage 1 spike (additive, flag-gated).

CCC drives Kimi over ACP today (``ccc_server/acp.py``). ACP exposes no steer
method, and the kap-server daemon that *does* cannot steer a turn an ACP
subprocess owns: the session store is shared but the live turn belongs to
whichever process holds the engine core in memory. Reaching steer therefore
means running the session on kap-server, not calling kap-server alongside ACP.

This module is the narrow proof of that path: adopt a running ``kimi web``
daemon, create one session, submit a prompt, stream the turn over WebSocket,
and emit CCC's own normalized events so the existing frontend renders it with
no changes.

Deliberately NOT here — daemon supervision/restart, adoption of ACP-created
sessions, approvals, steer itself. Those are the rest of Stage 1 and Stage 2.

The contract is machine-readable and served live by the daemon; pinned copies
live in docs/kimi-kap/ so a Kimi upgrade shows up as a reviewable diff:
  REST  /openapi.json   OpenAPI 3.0.3
  WS    /asyncapi.json  AsyncAPI 3.1.0, 32 messages on /api/v1/ws
"""

from __future__ import annotations

from pathlib import Path
import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
import uuid

# --- daemon discovery ------------------------------------------------------
#
# `kimi web` writes one JSON file per live server under
# <home>/server/instances/<server_id>.json carrying pid, host, port and a
# periodic heartbeat_at (epoch ms). That registry is the adoption mechanism:
# CCC never guesses a port.

_KAP_HOME_ENV = "KIMI_CODE_HOME"
_KAP_HOME_DEFAULT = "~/.kimi-code"
_KAP_HEARTBEAT_STALE_S = 120.0
_KAP_MAIN_AGENT = "main"
_KAP_WS_PATH = "/api/v1/ws"
_KAP_WS_BEARER_PREFIX = "kimi-code.bearer."


class KapUnavailable(RuntimeError):
    """No live kap-server daemon to talk to."""


class KapError(RuntimeError):
    """The daemon answered, but with a non-zero envelope code."""


def kap_home():
    raw = os.environ.get(_KAP_HOME_ENV) or _KAP_HOME_DEFAULT
    return Path(os.path.expanduser(raw))


def kap_token():
    """Bearer token for both REST and WS. Written by the daemon at startup."""
    try:
        return (kap_home() / "server.token").read_text().strip()
    except OSError:
        return ""


def _pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def kap_discover():
    """Newest live daemon record, or None. Stale heartbeats and dead pids are
    skipped so a crashed server's leftover file never wins."""
    inst_dir = kap_home() / "server" / "instances"
    try:
        entries = sorted(inst_dir.glob("*.json"))
    except OSError:
        return None
    now = time.time()
    best = None
    for path in entries:
        try:
            rec = json.loads(path.read_text() or "{}")
        except (OSError, ValueError):
            continue
        beat = float(rec.get("heartbeat_at") or 0) / 1000.0
        if beat and (now - beat) > _KAP_HEARTBEAT_STALE_S:
            continue
        if not _pid_alive(rec.get("pid")):
            continue
        if best is None or beat > best[0]:
            best = (beat, rec)
    return best[1] if best else None


def kap_endpoint():
    """(host, port) of the live daemon; raises KapUnavailable if there is none."""
    rec = kap_discover()
    if not rec or not rec.get("port"):
        raise KapUnavailable("no live kimi kap-server instance registered")
    return str(rec.get("host") or "127.0.0.1"), int(rec["port"])


def kap_available():
    try:
        kap_endpoint()
        return bool(kap_token())
    except KapUnavailable:
        return False


# --- REST ------------------------------------------------------------------
#
# Every route answers the same envelope: {code, msg, data, request_id}, with
# code 0 for success. Errors carry a dotted code (session.not_found, ...).

def kap_request(method, path, body=None, timeout=30.0):
    host, port = kap_endpoint()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        "http://%s:%d%s" % (host, port, path), data=data, method=method)
    req.add_header("Authorization", "Bearer " + kap_token())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8")[:400]
        except Exception:
            pass
        raise KapError("%s %s -> HTTP %s %s" % (method, path, exc.code, detail))
    except (urllib.error.URLError, OSError) as exc:
        raise KapUnavailable("%s %s -> %s" % (method, path, exc))
    code = payload.get("code")
    if code not in (0, None):
        raise KapError("%s %s -> code %s: %s" % (
            method, path, code, payload.get("msg")))
    return payload.get("data")


def kap_meta():
    return kap_request("GET", "/api/v1/meta", timeout=10.0)


def kap_workspace_for(cwd):
    """Workspace id whose root is `cwd`, creating it if the daemon has none.
    Kimi keys its session tree by workspace, so this has to resolve first."""
    root = str(Path(cwd).resolve())
    data = kap_request("GET", "/api/v1/workspaces") or {}
    items = data.get("items") if isinstance(data, dict) else data
    for ws in (items or []):
        if str(ws.get("root") or "").rstrip("/") == root.rstrip("/"):
            return ws.get("id")
    created = kap_request("POST", "/api/v1/workspaces",
                          {"root": root, "name": Path(root).name})
    return (created or {}).get("id")


_KAP_DEFAULT_MODEL = "kimi-code/k3"


def kap_create_session(cwd, title="", model=None, thinking=None,
                       permission_mode=None):
    """Create a session and give it a model.

    The model is not optional in practice: a session without one accepts a
    prompt, stores it, and then never runs a turn -- status reports no model,
    busy stays false, and nothing on the wire explains it. POST /sessions
    advertises an `agent_config` but does not apply it; the effective route is
    POST /sessions/{id}/profile, so creation is two calls."""
    body = {"workspace_id": kap_workspace_for(cwd)}
    if title:
        body["title"] = title
    data = kap_request("POST", "/api/v1/sessions", body) or {}
    sid = data.get("id") or (data.get("session") or {}).get("id")
    if not sid:
        raise KapError("session create returned no id: %s" % (data,))
    agent_config = {"model": model or _KAP_DEFAULT_MODEL}
    if thinking:
        agent_config["thinking"] = thinking
    if permission_mode:
        agent_config["permission_mode"] = permission_mode
    kap_request("POST", "/api/v1/sessions/%s/profile" % sid,
                {"agent_config": agent_config})
    return sid


def kap_submit_prompt(sid, text, **opts):
    """Enqueue a prompt. Returns its prompt_id -- the handle `prompts:steer`
    takes, which is why the queue is addressable at all."""
    body = {"content": [{"type": "text", "text": str(text)}]}
    body.update({k: v for k, v in opts.items() if v is not None})
    data = kap_request("POST", "/api/v1/sessions/%s/prompts" % sid, body) or {}
    return data.get("prompt_id") or data.get("id")


def kap_prompt_queue(sid):
    """{active, queued} -- the durable server-side queue CCC reconstructs
    client-side today."""
    return kap_request("GET", "/api/v1/sessions/%s/prompts" % sid) or {}


def kap_session_status(sid):
    return kap_request("GET", "/api/v1/sessions/%s/status" % sid) or {}


# --- WebSocket (RFC 6455 client, stdlib only) ------------------------------
#
# CCC ships zero runtime dependencies, so the client is hand-rolled rather
# than pulling in `websockets`. Only what the event stream needs: a client
# handshake, text/binary frame reassembly, close/ping control frames, and
# masked client sends. Auth rides the subprotocol -- kap-server reads
# `kimi-code.bearer.<token>` from Sec-WebSocket-Protocol, not a header
# (packages/kap-server/src/transport/ws/bearerProtocol.ts).

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class KapWebSocket:
    def __init__(self, host, port, token, timeout=60.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        self._closed = False
        self._send_lock = threading.Lock()
        self._handshake(host, port, token)

    def _handshake(self, host, port, token):
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s:%d\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Protocol: %s%s\r\n"
            "\r\n"
        ) % (_KAP_WS_PATH, host, port, key, _KAP_WS_BEARER_PREFIX, token)
        self.sock.sendall(req.encode("ascii"))
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise KapUnavailable("ws handshake: server closed")
            head += chunk
        header, _, rest = head.partition(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status:
            raise KapUnavailable("ws handshake failed: %s" % status)
        expect = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()).decode("ascii")
        if expect.lower() not in header.decode("latin-1").lower():
            raise KapUnavailable("ws handshake: bad Sec-WebSocket-Accept")
        self._buf = rest

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("ws closed by peer")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self):
        """Next application message as str, or None once the peer closes."""
        payload = b""
        while True:
            b0, b1 = struct.unpack("!BB", self._recv_exact(2))
            fin, opcode = b0 & 0x80, b0 & 0x0F
            length = b1 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            if b1 & 0x80:  # server frames must not be masked, but be lenient
                mask = self._recv_exact(4)
                raw = bytes(c ^ mask[i % 4]
                            for i, c in enumerate(self._recv_exact(length)))
            else:
                raw = self._recv_exact(length)
            if opcode == 0x8:
                self.close()
                return None
            if opcode == 0x9:
                self._send_frame(0xA, raw)
                continue
            if opcode == 0xA:
                continue
            payload += raw
            if fin:
                return payload.decode("utf-8", "replace")

    def _send_frame(self, opcode, data):
        mask = os.urandom(4)
        masked = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < (1 << 16):
            head = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, n)
        with self._send_lock:
            self.sock.sendall(head + mask + masked)

    def send_json(self, obj):
        self._send_frame(0x1, json.dumps(obj).encode("utf-8"))

    def recv_json(self, timeout=None):
        """Next frame as a dict, answering heartbeats on the way.

        kap-server's heartbeat is an *application* frame -- {"type":"ping"}
        inside the payload -- not the RFC 6455 ping opcode, and it closes the
        socket after two missed replies (wsConnectionV1.onHeartbeat). Any
        inbound frame resets its timer, so a client that only answers the
        protocol-level ping gets silently disconnected 20s in with no error.
        """
        if timeout is not None:
            # The pump needs to wake periodically to re-check the queue even
            # when the socket is quiet; a bare recv would block through the
            # whole idle window. An expired read is not an error -- {} is a
            # no-op frame the mapper ignores.
            self.sock.settimeout(timeout)
        while True:
            try:
                msg = self.recv()
            except socket.timeout:
                return {}
            if msg is None:
                return None
            try:
                frame = json.loads(msg)
            except ValueError:
                continue
            reply = kap_heartbeat_reply(frame)
            if reply is not None:
                self.send_json(reply)
                continue
            return frame

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def kap_heartbeat_reply(frame):
    """Pong for an application-level ping frame, else None."""
    if not isinstance(frame, dict) or frame.get("type") != "ping":
        return None
    nonce = (frame.get("payload") or {}).get("nonce") or ""
    return {"type": "pong", "payload": {"nonce": nonce}}


def kap_open_stream(sid, transcript="delta", since=None):
    """Connected, subscribed socket for `sid`. `transcript` is the per-agent
    granularity kap-server offers: off | turn | block | delta."""
    host, port = kap_endpoint()
    ws = KapWebSocket(host, port, kap_token())
    ws.send_json({
        "type": "client_hello",
        "id": uuid.uuid4().hex,
        "payload": {"client_id": "ccc-%s" % uuid.uuid4().hex[:12]},
    })
    payload = {"session_id": sid, "transcript": {_KAP_MAIN_AGENT: transcript}}
    if since is not None:
        payload["transcript_since"] = {_KAP_MAIN_AGENT: int(since)}
    ws.send_json({
        "type": "subscribe_v2",
        "id": uuid.uuid4().hex,
        "payload": payload,
    })
    return ws


# --- transcript mapping ----------------------------------------------------
#
# What the daemon actually streams is NOT the 58-type agent event union: with
# subscribe_v2 you get `transcript.ops`, a small document protocol that
# Kimi's own UI renders from. Seven ops, observed end-to-end on 0.39.1:
#
#   prompt.upsert  prompt + status (running -> completed): the queue state
#   turn.upsert    turn t0, state running -> completed
#   step.upsert    a step inside a turn (t0.1)
#   frame.upsert   a content frame, kind thinking|text, id t0.1.f1
#   append         text into a frame at a byte offset -- the streaming delta
#   marker.upsert  undo markers
#   meta.merge     activity, agent phase, usage, contextTokens
#
# This is a better surface than the raw events: frames are addressable, the
# appends are offset-based so a resync can be reconciled rather than replayed
# blind, and meta.merge carries live usage/context that ACP never exposed.


class KapTranscriptMapper:
    """Reduces kap-server transcript ops into CCC conversation events.

    Frames accumulate into one assistant message per turn, matching what the
    ACP path emits, so the frontend cannot tell the two transports apart.
    """

    def __init__(self, agent_id=_KAP_MAIN_AGENT, message_id_prefix="kap-kimi"):
        self.agent_id = agent_id
        self.prefix = message_id_prefix
        self.frames = {}
        self.order = []
        self.turn_id = None
        self.turn_state = None
        self.emitted_turns = set()
        self.prompts = {}
        self.usage = {}
        self.context_tokens = None
        self.activity = None
        self.busy = None
        self.end_reason = None
        self.error = None
        self.last_seq = None
        self.epoch = None

    # -- helpers ------------------------------------------------------------

    def _reset_turn(self):
        self.frames = {}
        self.order = []
        self.end_reason = None
        self.error = None

    def blocks(self):
        """Current turn's frames as CCC blocks, in arrival order."""
        out = []
        for fid in self.order:
            fr = self.frames.get(fid) or {}
            kind = fr.get("kind") or "text"
            if kind in ("thinking", "text"):
                out.append({"kind": kind, "text": fr.get("text") or ""})
            else:
                # Tool frames are not exercised by the smoke turn; pass the
                # kind through rather than inventing a shape for them.
                block = {"kind": kind, "text": fr.get("text") or ""}
                if fr.get("extra"):
                    block.update(fr["extra"])
                out.append(block)
        return out

    def _flush(self, subtype):
        out = []
        blocks = self.blocks()
        if blocks:
            out.append({
                "type": "assistant",
                "message_id": "%s-%s" % (self.prefix, self.turn_id or "0"),
                "blocks": blocks,
            })
        result = {"type": "result", "subtype": subtype}
        if self.error:
            # Without this the turn just stops. A quota 403 ("you've reached
            # your 5-hour usage limit") arrives only as a step endMessage and
            # a notice marker, so dropping it leaves the user staring at a
            # dead conversation with nothing to act on.
            result["error"] = self.error
        out.append(result)
        self._reset_turn()
        return out

    # -- ops ----------------------------------------------------------------

    def _apply_op(self, op):
        kind = op.get("op")
        if kind == "prompt.upsert":
            prompt = op.get("prompt") or {}
            pid = prompt.get("promptId")
            if pid:
                self.prompts[pid] = prompt.get("status")
            return []
        if kind == "turn.upsert":
            turn = op.get("turn") or {}
            tid = turn.get("turnId")
            state = turn.get("state")
            if state == "running":
                if tid != self.turn_id:
                    self._reset_turn()
                self.turn_id = tid
                self.turn_state = state
                return []
            if state in ("completed", "failed", "cancelled", "aborted"):
                self.turn_state = state
                if turn.get("error"):
                    self.error = str(turn["error"])
                # kap-server repeats the terminal turn.upsert; emit once.
                if tid in self.emitted_turns:
                    return []
                self.emitted_turns.add(tid)
                return self._flush(self.end_reason or state)
            return []
        if kind == "frame.upsert":
            fr = op.get("frame") or {}
            fid = fr.get("frameId")
            if not fid:
                return []
            entry = self.frames.setdefault(fid, {"kind": None, "text": ""})
            if fid not in self.order:
                self.order.append(fid)
            entry["kind"] = fr.get("kind") or entry["kind"]
            # A frame.upsert carrying text is a reconciliation of the appends
            # (it arrives again at turn end with the settled value), so it
            # replaces rather than concatenates.
            if fr.get("text"):
                entry["text"] = fr["text"]
            extra = {k: v for k, v in fr.items()
                     if k not in ("frameId", "kind", "text")}
            if extra:
                entry.setdefault("extra", {}).update(extra)
            return []
        if kind == "append":
            target = op.get("target") or {}
            fid = target.get("frameId")
            if not fid:
                return []
            entry = self.frames.setdefault(fid, {"kind": None, "text": ""})
            if fid not in self.order:
                self.order.append(fid)
            offset = op.get("offset")
            text = str(op.get("text") or "")
            if isinstance(offset, int) and offset != len(entry["text"]):
                # Offsets let a gap be repaired instead of replayed blind.
                entry["text"] = entry["text"][:offset] + text
            else:
                entry["text"] += text
            return []
        if kind == "step.upsert":
            # A step that ends in error carries the only human-readable
            # explanation on the wire (`endMessage`); the turn.upsert that
            # follows has the same text but arrives after the step, and a
            # cancelled turn has no turn-level error at all.
            step = op.get("step") or {}
            if step.get("endReason") == "error" and step.get("endMessage"):
                self.error = str(step["endMessage"])
            return []
        if kind == "marker.upsert":
            item = op.get("item") or {}
            payload = item.get("payload") or {}
            if item.get("marker") == "notice" and payload.get("level") == "error":
                self.error = str(payload.get("message") or self.error or "")
            return []
        if kind == "meta.merge":
            meta = op.get("meta") or {}
            if "activity" in meta:
                self.activity = meta["activity"]
            agent = meta.get("agent") or {}
            if "usage" in agent:
                self.usage = agent["usage"]
            if "contextTokens" in agent:
                self.context_tokens = agent["contextTokens"]
            phase = agent.get("phase") or {}
            if phase.get("kind") == "ended" and phase.get("reason"):
                self.end_reason = phase["reason"]
            return []
        return []

    # -- frames -------------------------------------------------------------

    def feed(self, frame):
        """Consume one WS frame; return a list of CCC events to emit."""
        ftype = frame.get("type") or ""
        if ftype in ("server_hello", "ack", "ping", "pong"):
            return []
        if ftype == "resync_required":
            # A seq gap. The caller reopens with transcript_since=last_seq;
            # surfacing it keeps the hole visible rather than dropping turns.
            return [{"type": "result", "subtype": "resync_required"}]
        if frame.get("seq") is not None:
            self.last_seq = frame["seq"]
        if frame.get("epoch"):
            self.epoch = frame["epoch"]

        payload = frame.get("payload") or {}
        if ftype == "transcript.reset":
            self._reset_turn()
            return []
        if ftype == "event.session.work_changed":
            self.busy = payload.get("busy")
            return []
        if ftype != "transcript.ops":
            return []
        # Subagents share this stream; only the main agent's turn is ours.
        agent = payload.get("agent_id", payload.get("agentId"))
        if agent is not None and agent != self.agent_id:
            return []
        out = []
        for op in payload.get("ops") or []:
            out.extend(self._apply_op(op))
        return out


# --- render bridge (flag-gated) --------------------------------------------
#
# The mapper already emits CCC's own conversation shape, so rendering is just
# a matter of putting those events where the frontend already looks: the ACP
# layer's per-session deque and transcript file. Reusing that path means zero
# frontend changes -- a kap-driven turn draws with the same code as an
# ACP-driven one.
#
# Off unless CCC_KIMI_KAP=1. The flag is not decoration: registering a session
# in the ACP registry makes CCC's Kimi layer believe an ACP subprocess owns
# it, and the two transports cannot both drive one session. Until per-session
# routing exists (rest of Stage 1), this stays opt-in.

_KAP_FLAG_ENV = "CCC_KIMI_KAP"


_KAP_FLAG_FILE = "kimi-kap.on"


def kap_flag_path():
    """Marker file that turns kap routing on for an already-running server.

    The env var alone is not enough in practice: CCC servers run for days, so
    an env-only flag means the only way to try kap is to restart the process
    and drop every live session. The marker is checked per call, so touching
    or removing it takes effect on the next poll.
    """
    # Not resolved off ccc_server.core: that proxy only populates inside the
    # server process, and the whole point of the marker is that it can be
    # created from a shell. Same literal server.py uses.
    try:
        from ccc_server import core as _core
        base = Path(_core.COMMAND_CENTER_STATE_DIR)
    except Exception:
        base = Path.home() / ".claude" / "command-center"
    return base / _KAP_FLAG_FILE


def kap_enabled():
    if os.environ.get(_KAP_FLAG_ENV, "").strip() in ("1", "true", "yes"):
        return True
    try:
        return kap_flag_path().exists()
    except OSError:
        return False


def kap_emit_to_ccc(sid, events, cwd=""):
    """Push mapped events into CCC's conversation store for `sid`.

    Must run **inside the CCC server process**: `ccc_server.acp` resolves
    `COMMAND_CENTER_STATE_DIR` off the lazy `core` proxy at import time, which
    only server.py populates. The import is deliberately kept inside this
    function so the rest of the module (discovery, REST, WS, mapping) stays
    usable standalone -- which is how the transport was proven against a live
    daemon without booting CCC.

    Returns the number of events written, or 0 when the flag is off.
    """
    if not events or not kap_enabled():
        return 0
    from ccc_server import acp as _acp
    from ccc_server import core as _core
    written = 0
    with _core._ACP_LOCK:
        _core._acp_session("kimi", sid, create=True, cwd=cwd)
        for event in events:
            _acp._acp_emit_event_unlocked("kimi", sid, event)
            written += 1
    return written


def kap_run_turn(sid, text, cwd="", on_events=None, timeout=300.0):
    """Submit `text` to `sid` and pump its turn to completion.

    Returns (prompt_id, events). Events are also handed to `on_events` as they
    are produced, and written into CCC's store when the flag is on.
    """
    mapper = KapTranscriptMapper()
    ws = kap_open_stream(sid)
    collected = []
    try:
        prompt_id = kap_submit_prompt(sid, text)
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = ws.recv_json()
            if frame is None:
                break
            events = mapper.feed(frame)
            if not events:
                continue
            collected.extend(events)
            if on_events:
                on_events(events)
            kap_emit_to_ccc(sid, events, cwd=cwd)
            if any(e.get("type") == "result" for e in events):
                break
    finally:
        ws.close()
    return prompt_id, collected


def kap_steer(sid, prompt_ids):
    """Promote already-queued prompts into the turn that is running now.

    This is the whole reason kap exists as a CCC transport. Over ACP a prompt
    sent mid-turn is rejected by the agent ("another turn is already in
    progress"), so CCC's steer degraded to cancel-then-resend: the turn died
    and restarted, losing whatever it had done. Here the prompt is already on
    the server's queue with an id, and steering is a promotion of that id into
    the live turn -- the turn keeps running and sees the new text.
    """
    ids = [str(p) for p in (prompt_ids or []) if p]
    if not ids:
        return {}
    return kap_request(
        "POST", "/api/v1/sessions/%s/prompts:steer" % sid, {"prompt_ids": ids}
    ) or {}


# --- Routing: which sessions CCC drives over kap ---------------------------
#
# Adoption, not migration. The daemon and the ACP subprocess index the same
# `~/.kimi-code/session_index.jsonl` and read the same transcript dir, so the
# daemon can load a session the ACP transport created (Stage 0 probe). What
# cannot be shared is the LIVE turn: whichever process holds the engine core
# in memory owns `_active`/`_queued`. So a session is kap-routed only while the
# daemon can actually serve it, and the answer is cached briefly rather than
# asked on every keystroke -- session-status polls this per second.

_KAP_OWNS_TTL_S = 20.0
_KAP_OWNS_CACHE = {}
_KAP_OWNS_LOCK = threading.Lock()


def kap_owns(sid, ttl=_KAP_OWNS_TTL_S):
    """True when the daemon can serve `sid` right now. Cached for `ttl`."""
    if not sid:
        return False
    now = time.time()
    with _KAP_OWNS_LOCK:
        hit = _KAP_OWNS_CACHE.get(sid)
        if hit and now - hit[0] < ttl:
            return hit[1]
    try:
        ok = bool(kap_session_status(sid))
    except (KapUnavailable, KapError, OSError):
        ok = False
    with _KAP_OWNS_LOCK:
        _KAP_OWNS_CACHE[sid] = (now, ok)
    return ok


def kap_forget(sid):
    """Drop the cached ownership answer (after a daemon restart, say)."""
    with _KAP_OWNS_LOCK:
        _KAP_OWNS_CACHE.pop(sid, None)


def kap_routes(sid):
    """Should this session's turns go over kap? Flag on, daemon up, session
    loadable. Any 'no' falls straight back to the ACP path, so the flag being
    off or the daemon being down is a downgrade, never a breakage."""
    return bool(sid) and kap_enabled() and kap_available() and kap_owns(sid)


# --- The live turn pump ----------------------------------------------------

_KAP_PUMPS = {}
_KAP_PUMP_LOCK = threading.Lock()


def kap_pump_active(sid):
    with _KAP_PUMP_LOCK:
        pump = _KAP_PUMPS.get(sid)
    return bool(pump and pump.get("thread") and pump["thread"].is_alive())


def _kap_pump(sid, cwd, idle_grace=3.0, max_run=1800.0):
    """Stream `sid` until its queue drains, writing mapped events into CCC.

    One pump per session, not one per prompt: a steer lands in the SAME turn
    the pump is already reading, so spawning a second reader would duplicate
    every frame. The pump outlives an individual turn and exits only once the
    server reports nothing active and nothing queued -- otherwise it would tear
    down between two queued prompts and lose the second turn's stream.
    """
    mapper = KapTranscriptMapper()
    ws = None
    try:
        ws = kap_open_stream(sid)
        deadline = time.time() + max_run
        idle_since = None
        while time.time() < deadline:
            frame = ws.recv_json(timeout=30.0)
            if frame is None:
                break
            events = mapper.feed(frame)
            if events:
                kap_emit_to_ccc(sid, events, cwd=cwd)
            if mapper.busy:
                idle_since = None
                continue
            # Not busy. Give the server a grace window to pick the next queued
            # prompt up before concluding the queue is really empty.
            if idle_since is None:
                idle_since = time.time()
            elif time.time() - idle_since >= idle_grace:
                try:
                    queue = kap_prompt_queue(sid)
                except (KapUnavailable, KapError, OSError):
                    break
                if not (queue.get("active") or queue.get("queued")):
                    break
                idle_since = None
    except Exception as exc:  # noqa: BLE001 - a pump must never kill the server
        try:
            kap_emit_to_ccc(sid, [{
                "type": "result", "subtype": "error",
                "error": "kap stream ended: %s" % (exc,),
            }], cwd=cwd)
        except Exception:
            pass
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        with _KAP_PUMP_LOCK:
            _KAP_PUMPS.pop(sid, None)


def kap_ensure_pump(sid, cwd=""):
    """Start the session's pump if it isn't already reading."""
    with _KAP_PUMP_LOCK:
        pump = _KAP_PUMPS.get(sid)
        if pump and pump["thread"].is_alive():
            return False
        thread = threading.Thread(
            target=_kap_pump, args=(sid, cwd),
            name="kap-pump-%s" % sid[:8], daemon=True,
        )
        _KAP_PUMPS[sid] = {"thread": thread, "started_at": time.time()}
    thread.start()
    return True


def kap_prompt(sid, text, visible_text=None, cwd="", mode="send"):
    """Send (or steer) one prompt over kap. CCC-shaped result dict.

    Queue-first is what makes send and steer the same code path: every prompt
    is enqueued and gets an id, so a mid-turn send is not an error to recover
    from -- it is a queued prompt, and steering is one extra call that promotes
    it. There is no busy rejection to handle here at all.
    """
    visible = visible_text if visible_text is not None else text
    try:
        prompt_id = kap_submit_prompt(sid, text)
    except (KapUnavailable, KapError, OSError) as exc:
        return {"ok": False, "error": "kap submit failed: %s" % (exc,),
                "code": "kap_unavailable"}
    if not prompt_id:
        return {"ok": False, "error": "kap returned no prompt_id",
                "code": "kap_error"}
    # Echo the user's text into the transcript before the turn streams, so the
    # message appears immediately -- same ordering the ACP path gives.
    kap_emit_to_ccc(sid, [{"type": "user_text", "text": visible}], cwd=cwd)
    steered = False
    if mode == "steer":
        try:
            kap_steer(sid, [prompt_id])
            steered = True
        except (KapUnavailable, KapError, OSError) as exc:
            # The prompt is still queued and will run in order; that is a
            # weaker outcome than steering but not a lost message.
            return {"ok": True, "prompt_id": prompt_id, "transport": "kap",
                    "steered": False,
                    "note": "queued (steer call failed: %s)" % (exc,)}
    kap_ensure_pump(sid, cwd=cwd)
    return {"ok": True, "prompt_id": prompt_id, "transport": "kap",
            "steered": steered}
