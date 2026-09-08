"""Connection-scoped, exactly-once answers to Codex server requests.

Only pending UI interactions live here. Host callbacks (clock, registered
tools, authentication, attestation) are dispatched by the transport adapter.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
import subprocess
import sys
from collections import OrderedDict
from pathlib import Path


INTERACTIVE_METHODS = frozenset({
    "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
    "item/permissions/requestApproval", "item/tool/requestUserInput",
    "mcpServer/elicitation/request", "applyPatchApproval", "execCommandApproval",
})


def _validate_mcp_form(value, schema):
    """Untrusted provider schemas cannot monopolize the native reader/GIL.

    This subprocess runs only on a deliberate form submission, never during
    row rendering, polling, or native streaming. Input travels over stdin.
    """
    try:
        result = subprocess.run([sys.executable, "-m", "ccc_server.codex_form_check"],
            input=json.dumps({"value": value, "schema": schema}), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2,
            cwd=str(Path(__file__).resolve().parent.parent))
    except subprocess.TimeoutExpired:
        raise ValueError("Form validation exceeded the supported time limit") from None
    if result.returncode != 0:
        raise ValueError("Form input does not match the requested fields or rules")


def _subset(granted, requested):
    if isinstance(granted, dict):
        return isinstance(requested, dict) and all(
            key in requested and _subset(value, requested[key])
            for key, value in granted.items())
    if isinstance(granted, list):
        return isinstance(requested, list) and all(value in requested for value in granted)
    if granted is False and requested is True:
        return True
    return type(granted) is type(requested) and granted == requested


def validate_request_answer(method, params, result):
    if not isinstance(result, dict):
        raise ValueError("Answer must be an object")
    if len(json.dumps(result)) > 256 * 1024:
        raise ValueError("Answer is too large")
    if method == "item/tool/requestUserInput":
        if set(result) != {"answers"} or not isinstance(result["answers"], dict):
            raise ValueError("Questions require an answers object")
        questions = {q.get("id"): q for q in params.get("questions", []) if isinstance(q, dict)}
        for qid, answer in result["answers"].items():
            if qid not in questions:
                raise ValueError("Answer refers to an unknown question")
            if not isinstance(answer, dict) or set(answer) != {"answers"}:
                raise ValueError("Each question requires a list of answers")
            values = answer["answers"]
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ValueError("Question answers must be text")
        # An empty answers object is the protocol's cancellation response.
        if result["answers"] and set(result["answers"]) != set(questions):
            raise ValueError("Answer each question before submitting")
    elif method == "mcpServer/elicitation/request":
        if set(result) - {"action", "content", "_meta"}:
            raise ValueError("Unexpected form response field")
        if result.get("action") not in ("accept", "decline", "cancel"):
            raise ValueError("Choose accept, decline, or cancel")
        if result["action"] == "accept" and params.get("mode") == "form":
            schema = params.get("requestedSchema")
            if not isinstance(schema, dict):
                raise ValueError("The requested form has no supported schema")
            _validate_mcp_form(result.get("content"), schema)
    elif method == "item/permissions/requestApproval":
        if set(result) - {"permissions", "scope", "strictAutoReview"}:
            raise ValueError("Unexpected permission response field")
        if not isinstance(result.get("permissions"), dict):
            raise ValueError("Choose the permissions to grant")
        if result.get("scope", "turn") not in ("turn", "session"):
            raise ValueError("Permission scope must be turn or session")
        if "strictAutoReview" in result and not isinstance(result["strictAutoReview"], (bool, type(None))):
            raise ValueError("Review preference must be a boolean")
        if not _subset(result["permissions"], params.get("permissions", {})):
            raise ValueError("Cannot grant permissions that were not requested")
    else:
        if set(result) != {"decision"}:
            raise ValueError("Approval requires one decision")
        choices = params.get("availableDecisions")
        if not isinstance(choices, list) or not choices:
            choices = (["approved", "approved_for_session", "denied", "abort"]
                       if method in ("applyPatchApproval", "execCommandApproval")
                       else ["accept", "acceptForSession", "decline", "cancel"])
        if result["decision"] not in choices:
            raise ValueError("Choose one of the offered approval decisions")


class CodexRequestRegistry:
    def __init__(self, *, capacity=128):
        self._lock = threading.RLock()
        self._generation = ""
        self._pending = {}
        self._terminal = OrderedDict()
        self._capacity = max(1, capacity)

    @property
    def generation(self):
        with self._lock:
            return self._generation

    def connect(self, generation):
        with self._lock:
            if self._generation != str(generation):
                self._pending.clear()
                self._terminal.clear()
                self._generation = str(generation)

    def disconnect(self, generation):
        with self._lock:
            if self._generation == generation:
                for pending in self._pending.values():
                    pending["state"] = "disconnected"

    def register(self, request_id, method, params):
        if method not in INTERACTIVE_METHODS:
            return None
        if type(request_id) not in (int, str) or not isinstance(params, dict):
            raise ValueError("Invalid server request")
        if isinstance(request_id, str) and len(request_id) > 256:
            raise ValueError("Invalid server request id")
        if len(json.dumps(params)) > 256 * 1024:
            raise ValueError("Server request exceeds the supported size")
        tid = params.get("threadId") or params.get("conversationId")
        if not isinstance(tid, str) or not tid:
            raise ValueError("Server request has no task context")
        with self._lock:
            if (type(request_id).__name__, request_id) in self._terminal:
                raise ValueError("This request has already been resolved")
            for key, pending in self._pending.items():
                if type(pending["request_id"]) is type(request_id) and pending["request_id"] == request_id:
                    if pending["method"] != method or pending["params"] != params:
                        raise ValueError("Conflicting server request id")
                    return key
            if len(self._terminal) >= max(256, self._capacity * 4):
                # Fail closed rather than forgetting an answered ID and
                # allowing it to be answered twice in this connection.
                raise ValueError("This connection has reached its answered-request limit")
            if len(self._pending) >= self._capacity:
                raise ValueError("Too many unanswered requests")
            key = uuid.uuid4().hex
            self._pending[key] = {"key": key, "request_id": request_id,
                                  "generation": self._generation, "method": method,
                                  "thread_id": tid, "turn_id": params.get("turnId", ""),
                                  "params": copy.deepcopy(params), "state": "pending", "ts": time.time()}
            return key

    def snapshot(self, thread_id=None):
        with self._lock:
            records = [{k: v for k, v in p.items() if k != "request_id"}
                       for p in self._pending.values()
                       if thread_id is None or p["thread_id"] == thread_id]
            return copy.deepcopy({"ok": True, "generation": self._generation, "requests": records})

    def respond(self, key, result, *, generation, thread_id, send):
        with self._lock:
            pending = self._pending.get(key)
            if generation != self._generation or not pending or pending["generation"] != generation:
                raise ValueError("This request is no longer available; the connection changed")
            if pending["thread_id"] != thread_id:
                raise ValueError("This request belongs to a different task")
            if pending["state"] != "pending":
                raise ValueError("This response cannot be retried; refresh task state")
            validate_request_answer(pending["method"], pending["params"], result)
            response = {"id": pending["request_id"], "result": copy.deepcopy(result)}
            pending["state"] = "sending"
            try:
                send(response)
            except (OSError, RuntimeError):
                pending["state"] = "uncertain"
                raise ValueError("Connection interrupted while answering; delivery is uncertain") from None
            self._pending.pop(key, None)
            self._remember_terminal(pending["request_id"])
            return {"ok": True, "method": pending["method"], "request_id": pending["request_id"],
                    "thread_id": pending["thread_id"]}

    def resolve(self, request_id, thread_id, generation):
        with self._lock:
            if generation != self._generation:
                return
            for key, pending in list(self._pending.items()):
                if (pending["thread_id"] == thread_id and
                        type(pending["request_id"]) is type(request_id) and pending["request_id"] == request_id):
                    self._pending.pop(key, None)
                    self._remember_terminal(request_id)

    def _remember_terminal(self, request_id):
        self._terminal[(type(request_id).__name__, request_id)] = True
        # At most capacity already-pending requests can finish after the
        # registration limit above. Thus this map is bounded without eviction.

    def cancel_turn(self, thread_id, turn_id, generation):
        with self._lock:
            if generation != self._generation:
                return
            for key, pending in list(self._pending.items()):
                if pending["generation"] == generation and pending["thread_id"] == thread_id and pending["turn_id"] == turn_id:
                    self._pending.pop(key, None)
                    self._remember_terminal(pending["request_id"])

    def cancel_thread(self, thread_id, generation):
        """Retire every unanswered request for an irreversibly deleted task."""
        with self._lock:
            if generation != self._generation:
                return
            for key, pending in list(self._pending.items()):
                if pending["generation"] == generation and pending["thread_id"] == thread_id:
                    self._pending.pop(key, None)
                    self._remember_terminal(pending["request_id"])


CODEX_REQUESTS = CodexRequestRegistry()
