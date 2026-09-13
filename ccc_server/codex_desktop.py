"""Bounded follower client for the desktop's existing, user-owned IPC router.

This is a separate transport from app-server JSON-RPC. Only observed versioned
follower operations are translated; there is no arbitrary desktop RPC proxy.
"""
from __future__ import annotations
import copy
import json
import os
import socket
import select
import stat
import struct
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

MAX_FRAME = 32 * 1024 * 1024
MAX_STATE = 16 * 1024 * 1024
READ_METHODS = {"thread/read", "thread/turns/list"}
# The follower steer contract has no atomic expected-turn guard. Keep native
# turn/steer unavailable here instead of weakening that API guarantee. CCC's
# Steer button uses the dedicated DesktopClient.steer() below, which follows
# the desktop's own owner-resolves-the-active-turn semantics and reports the
# steered turn id as its receipt.
WRITE_METHODS = {"turn/start", "turn/interrupt", "thread/compact/start"}
METHODS = READ_METHODS | WRITE_METHODS
ANSWERS = {
    "item/commandExecution/requestApproval": ("thread-follower-command-approval-decision", "decision"),
    "item/fileChange/requestApproval": ("thread-follower-file-approval-decision", "decision"),
    "item/permissions/requestApproval": ("thread-follower-permissions-request-approval-response", "response"),
    "item/tool/requestUserInput": ("thread-follower-submit-user-input", "response"),
    "mcpServer/elicitation/request": ("thread-follower-submit-mcp-server-elicitation-response", "response"),
}


def endpoint():
    if os.environ.get("CCC_CODEX_DESKTOP_IPC") == "0" or (os.environ.get("CCC_EPHEMERAL") and os.environ.get("CCC_CODEX_DESKTOP_IPC") != "1"):
        return None
    target = Path.home() / ".codex" / "ipc" / "ipc.sock"
    try:
        parent, entry = target.parent.lstat(), target.lstat()
        uid = os.getuid()
        if (stat.S_ISDIR(parent.st_mode) and stat.S_ISSOCK(entry.st_mode)
                and parent.st_uid == entry.st_uid == uid
                and not (parent.st_mode | entry.st_mode) & 0o022):
            return target
    except (AttributeError, OSError):
        pass
    return None


def apply_patches(state, patches):
    """Apply the desktop's Immer paths atomically; reject gaps/invalid paths."""
    if not isinstance(patches, list) or len(patches) > 10000:
        raise ValueError("Unsupported desktop patch batch")
    result = dict(state)
    for patch in patches:
        path = patch.get("path")
        op = patch.get("op")
        if not isinstance(path, list) or len(path) > 64 or op not in ("add", "remove", "replace"):
            raise ValueError("Unsupported desktop patch")
        if not path:
            if op != "replace" or not isinstance(patch.get("value"), dict):
                raise ValueError("Invalid desktop root patch")
            result = copy.deepcopy(patch["value"])
            continue
        parent = result
        for key in path[:-1]:
            child = parent[key]
            if not isinstance(child, (dict, list)): raise ValueError("Invalid desktop patch path")
            cloned = child.copy()
            parent[key] = cloned
            parent = cloned
        key = path[-1]
        if isinstance(parent, list):
            if type(key) is not int or key < 0 or key > len(parent):
                raise ValueError("Invalid desktop array path")
            if op == "add": parent.insert(key, copy.deepcopy(patch["value"]))
            elif op == "remove": parent.pop(key)
            else: parent[key] = copy.deepcopy(patch["value"])
        elif isinstance(parent, dict) and isinstance(key, str):
            if op == "remove": del parent[key]
            else: parent[key] = copy.deepcopy(patch["value"])
        else:
            raise ValueError("Invalid desktop patch target")
    return result


def normalize_thread(state):
    history = (state.get("turnHistory") or {}).get("history") or {}
    entities = history.get("entitiesByKey") or {}
    turns = state.get("turns") or []
    if entities:
        turns = []
        seen = set()
        for island in history.get("islands") or []:
            for entry in island.get("entries") or []:
                key = entry.get("value")
                if key not in entities: key = entry.get("key")
                if key in entities and key not in seen:
                    seen.add(key); turns.append(entities[key])
    normalized = []
    for index, turn in enumerate(turns):
        tid = turn.get("turnId") or turn.get("id")
        if not tid: continue
        items = copy.deepcopy(turn.get("items") or [])
        user_input = (turn.get("params") or {}).get("input") or []
        if user_input and not any(item.get("type") == "userMessage" for item in items):
            items.insert(0, {"id": str(tid) + "-input", "type": "userMessage", "content": copy.deepcopy(user_input)})
        normalized.append({"id": tid, "status": turn.get("status", "completed"),
                           "items": items, "error": turn.get("error"), "diff": turn.get("diff")})
    return {"id": state.get("id"), "cwd": state.get("cwd"), "name": state.get("title"),
            "turns": normalized, "status": state.get("threadRuntimeStatus"),
            "model": state.get("latestModel"), "updatedAt": state.get("updatedAt"),
            "forkedFromId": state.get("forkedFromId"),
            "transport": "desktop-ipc", "historyComplete": bool(history.get("isComplete", True))
            and all((turn.get("itemsPagination") or {}).get("hasLoadedOldest", True) for turn in turns)}


class DesktopClient:
    kind = "desktop-ipc"

    def __init__(self):
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.sock = None
        self.client_id = None
        self.epoch = None
        self.pending = {}
        self.states = OrderedDict()
        self.owners = {}
        self.seen_at = {}
        self.state_sizes = {}

    def connect(self):
        with self.connect_lock:
            if self.sock is not None: return
            target = endpoint()
            if target is None: raise ValueError("Desktop connection is unavailable")
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout(5)
            try:
                sock.connect(str(target))
            except ConnectionRefusedError:
                # The socket file survived a Desktop quit that didn't unlink
                # it; nothing is listening. Remove it so endpoint() stops
                # reporting a desktop that isn't there, and re-raise as the
                # same "no desktop" signal callers already fall back on.
                sock.close()
                try:
                    target.unlink()
                except OSError:
                    pass
                raise ValueError("Desktop connection is unavailable") from None
            sock.settimeout(None)
            with self.lock:
                self.sock = sock; self.client_id = "initializing-client"
                self.epoch = uuid.uuid4().hex
                self.states.clear(); self.owners.clear(); self.seen_at.clear(); self.state_sizes.clear()
            threading.Thread(target=self._reader, args=(sock,), daemon=True, name="codex-desktop-reader").start()
            try:
                result = self.request("initialize", {"clientType": "ccc"}, version=0)
                self.client_id = result["result"]["clientId"]
            except Exception:
                self._disconnect(sock)
                raise

    def _send(self, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        if len(raw) > 4 * 1024 * 1024: raise ValueError("Desktop request is too large")
        with self.lock:
            if self.sock is None: raise ValueError("Desktop disconnected")
            payload = memoryview(struct.pack("<I", len(raw)) + raw)
            deadline = time.monotonic() + 5
            while payload:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([], [self.sock], [], remaining)[1]:
                    raise TimeoutError("Desktop send timed out; it will not be retried")
                try: sent = self.sock.send(payload, socket.MSG_DONTWAIT)
                except BlockingIOError: continue
                if not sent: raise ConnectionError("Desktop disconnected while sending")
                payload = payload[sent:]

    def request(self, method, params, *, version=1, target=None, timeout=8):
        rid = uuid.uuid4().hex
        pending = {"event": threading.Event(), "response": None}
        with self.lock:
            if len(self.pending) >= 16: raise ValueError("Too many desktop requests")
            self.pending[rid] = pending
        try:
            self._send({"type": "request", "requestId": rid, "sourceClientId": self.client_id,
                        "version": version, "method": method, "params": params,
                        "targetClientId": target, "timeoutMs": int(timeout * 1000)})
            if not pending["event"].wait(timeout + 1): raise TimeoutError("Desktop request timed out; it will not be retried")
            response = pending["response"] or {}
            if response.get("resultType") != "success": raise ValueError("Desktop: " + str(response.get("error", "disconnected")))
            if target and response.get("handledByClientId") != target:
                raise ValueError("Desktop task owner changed")
            return response
        finally:
            with self.lock: self.pending.pop(rid, None)

    def _following(self, tid, owner, following):
        self._send({"type": "broadcast", "method": "thread-stream-following-changed", "version": 1,
                    "sourceClientId": self.client_id, "targetClientIds": [owner],
                    "params": {"hostId": "local", "conversationId": tid, "following": following}})

    def snapshot(self, tid):
        self.connect()
        now = time.monotonic()
        with self.lock:
            cached_owner = self.owners.get(tid)
            current = self.states.get(tid)
            if current and cached_owner and now - self.seen_at.get(tid, 0) < 5:
                return copy.deepcopy(current)
        response = self.request("thread-owner-discovery", {"hostId": "local", "conversationId": tid})
        owner = response["handledByClientId"]
        with self.lock:
            if cached_owner and cached_owner != owner:
                self.epoch = uuid.uuid4().hex
                self.states.pop(tid, None)
            self.owners[tid] = owner
            self.seen_at[tid] = now
            if tid in self.states: return copy.deepcopy(self.states[tid])
            if len(self.owners) > 4:
                victim = next(key for key in self.owners if key != tid)
                previous = self.owners.pop(victim)
                self.states.pop(victim, None); self.seen_at.pop(victim, None); self.state_sizes.pop(victim, None)
                self._following(victim, previous, False)
            self._following(tid, owner, True)
            ready = self.condition.wait_for(lambda: tid in self.states or self.sock is None, timeout=8)
            if not ready or tid not in self.states: raise ValueError("Desktop did not provide this task's snapshot")
            return copy.deepcopy(self.states[tid])

    def _reader(self, sock):
        def exact(count):
            output = bytearray()
            while len(output) < count:
                data = sock.recv(count - len(output))
                if not data: raise EOFError()
                output.extend(data)
            return output
        try:
            while True:
                size = struct.unpack("<I", exact(4))[0]
                if not 0 < size <= MAX_FRAME: raise ValueError("Desktop frame exceeds limit")
                self._message(json.loads(exact(size)))
        except (OSError, ValueError, EOFError, KeyError, TypeError, RecursionError):
            pass
        finally:
            self._disconnect(sock)

    def _disconnect(self, sock):
        with self.lock:
            if self.sock is not sock: return
            self.sock = None
            try: sock.close()
            except OSError: pass
            self.states.clear(); self.owners.clear()
            for pending in self.pending.values(): pending["event"].set()
            self.condition.notify_all()

    def _message(self, message):
        with self.lock:
            kind = message.get("type")
            if kind == "response":
                pending = self.pending.get(message.get("requestId"))
                if pending:
                    pending["response"] = message; pending["event"].set()
            elif kind == "client-discovery-request":
                self._send({"type": "client-discovery-response", "requestId": message["requestId"], "response": {"canHandle": False}})
            elif kind == "broadcast" and message.get("method") == "thread-stream-state-changed":
                if message.get("version") != 11: return
                params = message.get("params") or {}; tid = params.get("conversationId")
                if params.get("hostId") != "local" or self.owners.get(tid) != message.get("sourceClientId"): return
                change = params.get("change") or {}; revision = change.get("revision")
                if type(revision) is not int: return
                previous = self.states.get(tid)
                if previous and revision <= previous[0]: return
                try:
                    if change.get("type") == "snapshot":
                        state = change["conversationState"]
                        size = len(json.dumps(state))
                    elif change.get("type") == "patches" and previous and change.get("baseRevision") == previous[0]:
                        state = apply_patches(previous[1], change.get("patches"))
                        # Conservative upper bound; a new snapshot resets it.
                        # Avoid serializing the entire history on every token.
                        size = self.state_sizes.get(tid, 0) + len(json.dumps(change.get("patches")))
                    else: raise ValueError("Desktop stream gap")
                    if not isinstance(state, dict) or state.get("id") != tid or size > MAX_STATE:
                        raise ValueError("Invalid desktop state")
                    self.states[tid] = (revision, state)
                    self.state_sizes[tid] = size
                except (ValueError, TypeError, KeyError, IndexError):
                    self.states.pop(tid, None)
                    self.seen_at.pop(tid, None)
                self.condition.notify_all()

    def rpc(self, method, params):
        if method not in METHODS: raise ValueError("This action is not exposed by the desktop connection")
        tid = params.get("threadId")
        if not isinstance(tid, str) or not tid: raise ValueError("Select a desktop task")
        revision, state = self.snapshot(tid)
        thread = normalize_thread(state)
        owner = self.owners[tid]
        if method == "thread/read":
            if not params.get("includeTurns"): thread = {**thread, "turns": []}
            return {"thread": thread}
        if method == "thread/turns/list":
            turns = thread["turns"]
            cursor = params.get("cursor")
            if cursor == "desktop-full":
                response = self.request("thread-follower-load-complete-history", {"conversationId": tid}, target=owner, timeout=25)
                desired = response.get("result", {}).get("revision")
                with self.condition:
                    if not self.condition.wait_for(lambda: self.states.get(tid, (-1,))[0] >= desired, timeout=8):
                        raise ValueError("Desktop history has not arrived yet")
                _, state = self.snapshot(tid); thread = normalize_thread(state); turns = thread["turns"]
            elif cursor:
                prefix = "desktop-before:"
                if not isinstance(cursor, str) or not cursor.startswith(prefix): raise ValueError("Invalid desktop history cursor")
                boundary = cursor[len(prefix):]
                matches = [i for i, turn in enumerate(turns) if turn["id"] == boundary]
                if not matches: raise ValueError("Desktop history changed; reopen the task")
                turns = turns[:matches[0]]
            limit = min(20, max(1, int(params.get("limit") or 20)))
            page = turns[-limit:]
            next_cursor = "desktop-before:" + str(page[0]["id"]) if len(turns) > limit else (None if thread["historyComplete"] or cursor == "desktop-full" else "desktop-full")
            return {"data": list(reversed(page)), "nextCursor": next_cursor}
        active = [turn for turn in thread["turns"] if turn["status"] == "inProgress"]
        if method in ("turn/steer", "turn/interrupt"):
            expected = params.get("expectedTurnId") if method == "turn/steer" else params.get("turnId")
            if not active or active[-1]["id"] != expected: raise ValueError("The active desktop turn changed")
        if method == "turn/start":
            if active: raise ValueError("Codex Desktop is still working on this task; wait for it to finish")
            native = {k: v for k, v in params.items() if k != "threadId"}
            native["threadId"] = tid
            request = {"conversationId": tid, "turnStart": {"request": native, "context": {"inheritThreadSettings": True}}}
            method_name, version = "thread-follower-start-turn", 2
        elif method == "turn/interrupt":
            request = {"conversationId": tid, "expectedTurnId": params["turnId"], "mode": "user-stop"}
            method_name, version = "thread-follower-interrupt-turn", 4
        else:
            request = {"conversationId": tid}; method_name, version = "thread-follower-compact-thread", 1
        response = self.request(method_name, request, version=version, target=owner, timeout=25)["result"]
        if method == "turn/start":
            result = response.get("result") or {}
            return result if isinstance(result, dict) and "turn" in result else {"turn": result}
        return response

    def answer(self, tid, wire):
        _, state = self.snapshot(tid)
        requests = [r for r in state.get("requests", []) if type(r.get("id")) is type(wire.get("id")) and r.get("id") == wire.get("id")]
        if len(requests) != 1 or requests[0].get("method") not in ANSWERS:
            raise ValueError("The desktop request is no longer pending")
        method, field = ANSWERS[requests[0]["method"]]
        result = wire["result"]
        value = result.get("decision") if field == "decision" else result
        self.request(method, {"conversationId": tid, "requestId": wire["id"], field: value}, target=self.owners[tid], timeout=25)

    def steer(self, tid, text, *, cwd=None, image_paths=(), client_message_id=None):
        """Steer the desktop's active turn through the owner (CCC Steer button).

        This is the follower steer contract (thread-follower-steer-turn v1),
        NOT native turn/steer: the owning desktop window resolves the current
        in-progress turn itself and answers with the steered turn id. The
        restoreMessage mirrors what the desktop composer sends, so a failed
        steer lands the text back in the desktop's own queued follow-ups
        instead of vanishing. Raises ValueError when there is no active turn
        or the owner rejects the steer; TimeoutError/ConnectionError leave
        the outcome unknown — callers must surface that, never retry blindly.
        """
        _, state = self.snapshot(tid)
        thread = normalize_thread(state)
        active = [turn for turn in thread.get("turns", []) if turn.get("status") == "inProgress"]
        if not active:
            raise ValueError("no active desktop turn to steer")
        owner = self.owners[tid]
        root = cwd or thread.get("cwd") or "/"
        inputs = [{"type": "text", "text": text, "text_elements": []}]
        inputs.extend({"type": "localImage", "path": str(path)} for path in image_paths)
        params = {
            "conversationId": tid,
            "clientUserMessageId": client_message_id or uuid.uuid4().hex,
            "input": inputs,
            "restoreMessage": {
                "id": uuid.uuid4().hex,
                "text": text,
                "context": {
                    "prompt": text,
                    "turnTrigger": None,
                    "addedFiles": [],
                    "fileAttachments": [],
                    "ideContext": None,
                    "imageAttachments": [],
                    "workspaceRoots": [root],
                },
                "cwd": root,
                "createdAt": int(time.time() * 1000),
            },
            "serviceTier": None,
            "attachments": [],
            "additionalContext": None,
            "toolOutput": None,
        }
        response = self.request("thread-follower-steer-turn", params, version=1, target=owner, timeout=25)
        result = (response.get("result") or {}).get("result") or {}
        turn_id = result.get("turnId") if isinstance(result, dict) else None
        return {"turn_id": turn_id or active[-1].get("id"), "owner": owner}


DESKTOP = DesktopClient()
