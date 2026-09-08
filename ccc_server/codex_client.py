"""Scoped client service for the Codex workspace.

The browser never owns a native connection. Calls and volatile credentials
travel through the existing engine owner, without copying them into the work
ledger. Mutations use connection-local action receipts and are never retried.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from ccc_server import core as _core
from ccc_server.codex_conversation import CODEX_CONVERSATIONS
from ccc_server.codex_requests import CODEX_REQUESTS

_CLIENT_LOCK = threading.RLock()
_CLIENT_ACTIONS = OrderedDict()
_CLIENT_RECEIPTS = {}
_CLIENT_RECEIPT_LIMIT = 65536
_CLIENT_THREADS = OrderedDict()
_CLIENT_HANDLES = OrderedDict()
_CLIENT_TOOLS = {}
_CLIENT_TOOL_SLOTS = threading.BoundedSemaphore(4)
_CLIENT_TRANSPORT = None


def redact_client_data(value):
    secret_names = {"apikey", "accesstoken", "refreshtoken", "password", "secret",
                    "clientsecret", "authorization", "bearertoken", "idtoken"}
    if isinstance(value, dict):
        return {key: ("[redacted]" if str(key).lower().replace("_", "").replace("-", "") in secret_names
                      else redact_client_data(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_client_data(item) for item in value]
    return value


def _client_parameter_fields(descriptor):
    root = (descriptor or {}).get("params_schema") or {}
    pending = [(root, 0)]
    fields = set()
    seen = set()
    while pending:
        schema, depth = pending.pop()
        if not isinstance(schema, dict) or depth > 16:
            continue
        fields.update(schema.get("properties", {}))
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and ref not in seen:
            seen.add(ref)
            target = root
            for part in ref[2:].split("/"):
                target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(target, dict) else {}
            pending.append((target, depth + 1))
        for key in ("allOf", "oneOf", "anyOf"):
            pending.extend((entry, depth + 1) for entry in schema.get(key, []) if isinstance(entry, dict))
    return fields


def validate_operation_context(method, params, context, *, resolve_repo, read_thread, descriptor=None,
                               allowed_skill_paths=()):
    """Bind native task and path parameters to an explicit browser context."""
    if not isinstance(params, dict) or not isinstance(context, dict):
        raise ValueError("Parameters and context must be objects")
    normalized = copy.deepcopy(params)
    tid = normalized.get("threadId")
    if "threadId" in normalized and (not isinstance(tid, str) or not tid.strip()):
        raise ValueError("A task id is required")
    selected = context.get("thread_id") or context.get("session_id")
    workspace_call = method.startswith(("fs/", "command/", "process/", "fuzzyFileSearch"))
    fields = _client_parameter_fields(descriptor)
    path_scoped = bool({"cwd", "cwds"} & (fields | set(normalized)))
    needs_repo = (bool(tid) or workspace_call or method == "thread/start"
                  or (descriptor or {}).get("scope") == "workspace"
                  or bool(normalized.get("extraLogFiles")) or path_scoped)
    local_context_call = needs_repo or method.startswith(("config/", "skills/", "hooks/", "windowsSandbox/"))
    if (context.get("environment_id") and local_context_call
            and not method.startswith(("environment/", "remoteControl/"))):
        raise ValueError("This operation requires a connection to the selected environment")
    repo = None
    if needs_repo:
        raw = context.get("repo_path")
        if not isinstance(raw, str) or not raw:
            raise ValueError("Select a repository first")
        repo = Path(resolve_repo(raw)).resolve()
    if tid:
        if not isinstance(tid, str) or not selected or tid != selected:
            raise ValueError("Operation belongs to a different task")
        thread = read_thread(tid)
        if not isinstance(thread, dict) or not isinstance(thread.get("cwd"), str):
            raise ValueError("Cannot establish this task's repository")
        cwd = Path(thread["cwd"]).expanduser().resolve()
        try:
            cwd.relative_to(repo)
        except ValueError:
            raise ValueError("Task does not belong to the selected repository") from None
    def confined(raw):
        if not isinstance(raw, str) or not raw:
            raise ValueError("A file path is required")
        value = Path(raw).expanduser()
        value = (value if value.is_absolute() else repo / value).resolve()
        try:
            value.relative_to(repo)
        except ValueError:
            raise ValueError("Path is outside the selected repository") from None
        return str(value)
    if method == "thread/start" or method in ("command/exec", "process/spawn"):
        normalized["cwd"] = confined(normalized.get("cwd") or str(repo))
    elif normalized.get("cwd") is not None and repo:
        normalized["cwd"] = ([confined(value) for value in normalized["cwd"]]
                             if method == "thread/list" and isinstance(normalized["cwd"], list)
                             else confined(normalized["cwd"]))
    if repo and ("cwds" in fields or "cwds" in normalized):
        if normalized.get("cwds") is not None and not isinstance(normalized["cwds"], list):
            raise ValueError("Working directories must be a list")
        normalized["cwds"] = [confined(value) for value in (normalized.get("cwds") or [str(repo)])]
    if repo and "cwd" in fields and method in (
        "config/read", "permissionProfile/list", "hooks/list", "plugin/list", "environment/info",
    ) and normalized.get("cwd") is None:
        normalized["cwd"] = str(repo)
    if method.startswith("fs/"):
        for field in ("path", "sourcePath", "destinationPath"):
            if field in normalized:
                normalized[field] = confined(normalized[field])
    if method.startswith("fuzzyFileSearch") and repo:
        for field in ("roots",):
            if field in normalized:
                if not isinstance(normalized[field], list):
                    raise ValueError("Search roots must be a list")
                normalized[field] = [confined(value) for value in normalized[field]]
    if method == "thread/list" and context.get("repo_path"):
        repo = Path(resolve_repo(context["repo_path"])).resolve()
        if isinstance(normalized.get("cwd"), list):
            normalized["cwd"] = [confined(value) for value in normalized["cwd"]] or [str(repo)]
        else:
            normalized["cwd"] = confined(normalized.get("cwd") or str(repo))
    if method == "skills/config/write" and normalized.get("path"):
        skill = str(Path(normalized["path"]).expanduser().resolve())
        if skill not in {str(Path(value).expanduser().resolve()) for value in allowed_skill_paths}:
            raise ValueError("Choose a skill discovered for this workspace")
        normalized["path"] = skill
    if method.startswith("config/") and normalized.get("filePath"):
        target = Path(normalized["filePath"]).expanduser().resolve()
        user_config = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve() / "config.toml"
        if target != user_config:
            confined(str(target))
            if target.name != "config.toml":
                raise ValueError("Select a Codex configuration file")
        normalized["filePath"] = str(target)
    if method in ("skills/extraRoots/set", "skills/list") and repo:
        # Additional roots are workspace configuration, not arbitrary file
        # access. Native-discovered global skills are handled above.
        for field in ("roots", "extraRoots"):
            if field in normalized:
                if not isinstance(normalized[field], list):
                    raise ValueError("Skill roots must be a list")
                normalized[field] = [confined(value) for value in normalized[field]]
    if method == "feedback/upload" and normalized.get("extraLogFiles"):
        if repo is None:
            raise ValueError("Select a repository for diagnostic attachments")
        if not isinstance(normalized["extraLogFiles"], list):
            raise ValueError("Diagnostic attachments must be a list")
        normalized["extraLogFiles"] = [confined(value) for value in normalized["extraLogFiles"]]
    return normalized


def _client_rpc(method, params, timeout=25, expected_generation=None):
    transport = _core._ensure_codex_app_server()
    if transport is None:
        return {"ok": False, "error": "Codex app-server is unavailable", "code": "codex_unavailable"}
    generation = CODEX_CONVERSATIONS.generation
    if expected_generation is not None and expected_generation != generation:
        return {"ok": False, "error": "Codex reconnected before the action was sent", "code": "stale_connection",
                "generation": generation, "resync_required": True}
    options = {"_route": False, "_null_params": params is None}
    options["_expected_generation"] = generation
    reply = _core._codex_app_server_request(method, params, timeout=timeout, **options)
    if expected_generation is None and CODEX_CONVERSATIONS.generation != generation:
        return {"ok": False, "error": "Codex reconnected while reading; reload the current state",
                "code": "stale_connection", "resync_required": True,
                "generation": CODEX_CONVERSATIONS.generation}
    if not isinstance(reply, dict):
        return {"ok": False, "error": "Codex returned no response", "uncertain": True, "generation": generation}
    if "result" in reply:
        return {"ok": True, "result": reply["result"], "generation": generation}
    error = reply.get("error")
    message = str((error.get("message") if isinstance(error, dict) else error) or "Codex request failed")
    return {"ok": False, "error": message, "code": error.get("code") if isinstance(error, dict) else reply.get("code"), "generation": generation,
            "uncertain": bool(reply.get("ambiguous") or "timeout" in message.lower() or "timed out" in message.lower())}


def _client_read_thread(tid, *, fresh=False):
    with _CLIENT_LOCK:
        cached = _CLIENT_THREADS.get(tid)
        if cached and not fresh and time.monotonic() - cached[0] < 30:
            return copy.deepcopy(cached[1])
    result = _client_rpc("thread/read", {"threadId": tid, "includeTurns": False})
    if not result.get("ok"):
        raise ValueError(result.get("error") or "Cannot read this task")
    thread = (result.get("result") or {}).get("thread")
    if not isinstance(thread, dict):
        raise ValueError("Codex returned no task metadata")
    metadata = {k: v for k, v in thread.items() if k != "turns"}
    with _CLIENT_LOCK:
        _CLIENT_THREADS[tid] = (time.monotonic(), metadata)
        _CLIENT_THREADS.move_to_end(tid)
        while len(_CLIENT_THREADS) > 128:
            _CLIENT_THREADS.popitem(last=False)
    return metadata


def _client_scope(method, params, context, descriptor=None):
    skills = []
    if method == "skills/config/write":
        if not isinstance(context, dict) or not context.get("repo_path"):
            raise ValueError("Select a repository first")
        if context.get("environment_id"):
            raise ValueError("This operation requires a connection to the selected environment")
        repo = _core.resolve_repo_path(context["repo_path"])
        response = _client_rpc("skills/list", {"cwds": [repo]})
        if not response.get("ok"):
            raise ValueError("Cannot read the workspace's installed skills")
        for group in (response.get("result") or {}).get("data", []):
            skills.extend(skill["path"] for skill in group.get("skills", []) if isinstance(skill, dict) and skill.get("path"))
    return validate_operation_context(method, params, context,
        resolve_repo=_core.resolve_repo_path, descriptor=descriptor, allowed_skill_paths=skills,
        read_thread=lambda tid: _client_read_thread(tid, fresh=True))


def codex_client_connect(transport):
    global _CLIENT_TRANSPORT
    with _CLIENT_LOCK:
        if _CLIENT_TRANSPORT is transport:
            return
        _CLIENT_TRANSPORT = transport
        generation = uuid.uuid4().hex
        CODEX_CONVERSATIONS.connect(generation)
        CODEX_REQUESTS.connect(generation)
        _CLIENT_ACTIONS.clear()
        _CLIENT_RECEIPTS.clear()
        _CLIENT_THREADS.clear()
        _CLIENT_HANDLES.clear()


def codex_client_disconnect(transport):
    with _CLIENT_LOCK:
        if _CLIENT_TRANSPORT is transport:
            generation = CODEX_CONVERSATIONS.generation
            CODEX_CONVERSATIONS.disconnect(generation)
            CODEX_REQUESTS.disconnect(generation)


def register_codex_client_tool(name, callback):
    """Register a deliberately supplied host tool; unknown names never run."""
    if not isinstance(name, str) or not name or not callable(callback):
        raise ValueError("A tool name and callable are required")
    with _CLIENT_LOCK:
        _CLIENT_TOOLS[name] = callback


def codex_client_observe(payload, transport=None):
    """Observe messages without starting transports or performing schema I/O."""
    with _CLIENT_LOCK:
        return _codex_client_observe_locked(payload, transport)


def _codex_client_observe_locked(payload, transport):
    if not isinstance(payload, dict):
        return False
    method = payload.get("method")
    params = payload.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        return False
    with _CLIENT_LOCK:
        current = _CLIENT_TRANSPORT
    if transport is not None and transport is not current:
        return True  # a late message from a retired reader cannot change state
    if "id" in payload:
        if current is None:
            return False
        try:
            if CODEX_REQUESTS.register(payload["id"], method, params):
                CODEX_CONVERSATIONS.record("ccc/request/pending", {
                    "threadId": params.get("threadId") or params.get("conversationId")})
                return False  # preserve legacy approval status bookkeeping
            if method == "currentTime/read":
                current.send_json({"id": payload["id"], "result": {"currentTimeAt": int(time.time())}})
            elif method == "item/tool/call":
                callback = _CLIENT_TOOLS.get(params.get("tool"))
                if callback is None or not _CLIENT_TOOL_SLOTS.acquire(blocking=False):
                    current.send_json({"id": payload["id"], "result": {"success": False,
                        "contentItems": [{"type": "inputText", "text": "This host tool is unavailable or busy in CCC."}]}})
                else:
                    # Registered handlers run away from the native reader.
                    # The callback is an explicit host extension, not model
                    # supplied code. Its response is tied to this connection.
                    def run():
                        try:
                            try:
                                result = callback(copy.deepcopy(params.get("arguments")))
                                if not isinstance(result, dict) or "success" not in result or "contentItems" not in result:
                                    raise ValueError("Invalid tool result")
                            except Exception:
                                result = {"success": False, "contentItems": [{"type": "inputText", "text": "The host tool failed."}]}
                            with _CLIENT_LOCK:
                                if current is _CLIENT_TRANSPORT:
                                    try:
                                        current.send_json({"id": payload["id"], "result": result})
                                    except OSError:
                                        pass
                        finally:
                            _CLIENT_TOOL_SLOTS.release()
                    threading.Thread(target=run, daemon=True, name="codex-client-tool").start()
            else:
                current.send_json({"id": payload["id"], "error": {"code": -32601,
                    "message": "This host capability is not available in CCC"}})
        except ValueError:
            current.send_json({"id": payload["id"], "error": {"code": -32602,
                "message": "The server request could not be presented safely"}})
        return True
    CODEX_CONVERSATIONS.record(method, redact_client_data(params))
    tid = params.get("threadId")
    if method == "serverRequest/resolved":
        CODEX_REQUESTS.resolve(params.get("requestId"), tid, CODEX_REQUESTS.generation)
    elif method == "turn/completed":
        turn = params.get("turn") or {}
        CODEX_REQUESTS.cancel_turn(tid, turn.get("id") or params.get("turnId"), CODEX_REQUESTS.generation)
    if method in ("thread/metadata/updated", "thread/project/updated", "thread/deleted", "thread/settings/updated"):
        with _CLIENT_LOCK:
            _CLIENT_THREADS.pop(tid, None)
    if method == "process/exited":
        for field in ("processHandle", "processId"):
            _CLIENT_HANDLES.pop(str(params.get(field) or ""), None)
    return False


def _client_history(data):
    context = data.get("context") or {}
    tid = context.get("thread_id")
    _client_scope("thread/read", {"threadId": tid}, context)
    before = CODEX_CONVERSATIONS.cursor
    generation = CODEX_CONVERSATIONS.generation
    metadata = _client_read_thread(tid)
    params = {"threadId": tid, "limit": 20, "itemsView": "full", "sortDirection": "desc"}
    if data.get("cursor"):
        params["cursor"] = data["cursor"]
    page = _client_rpc("thread/turns/list", params)
    if page.get("ok"):
        value = page.get("result") or {}
        turns = value.get("data", value.get("turns", []))
        if not isinstance(turns, list):
            raise ValueError("Codex returned invalid turn history")
        metadata = {**metadata, "turns": list(reversed(turns))}
        next_cursor = value.get("nextCursor")
    elif (not data.get("cursor") and page.get("code") == -32600
          and "not materialized yet" in str(page.get("error", "")).lower()):
        # Native threads exist before their first persisted user message.
        # Their metadata is real; an empty history is the correct UI state.
        metadata = {**metadata, "turns": []}
        next_cursor = None
    elif page.get("code") == -32601 or "experimental" in str(page.get("error", "")).lower():
        full = _client_rpc("thread/read", {"threadId": tid, "includeTurns": True})
        if not full.get("ok"):
            return full
        metadata = (full.get("result") or {}).get("thread") or metadata
        next_cursor = None
    else:
        return page
    if not CODEX_CONVERSATIONS.hydrate(metadata, before, generation=generation):
        return {"ok": False, "resync_required": True, "error": "Connection changed while loading history"}
    return {**CODEX_CONVERSATIONS.snapshot(tid), "next_cursor": next_cursor,
            "requests": CODEX_REQUESTS.snapshot(tid)["requests"]}


def _client_legacy_request_resolved(tid, request_id):
    """Retire only the matching legacy card when the richer UI answers it."""
    with _core._CODEX_APP_SERVER_LOCK:
        state = _core._CODEX_APP_SERVER_THREAD_STATE.get(tid)
        if not isinstance(state, dict):
            return
        pending = state.get("pending_approval_request")
        if not isinstance(pending, dict):
            return
        original = pending.get("request_id_raw", pending.get("request_id"))
        if type(original) is not type(request_id) or original != request_id:
            return
        state.pop("pending_approval_request", None)
        remaining = CODEX_REQUESTS.snapshot(tid)["requests"]
        state["thread_needs_approval"] = bool(remaining)
        if not remaining:
            state["active_flags"] = [flag for flag in state.get("active_flags", [])
                                      if flag not in ("waitingOnApproval", "waitingOnUserInput")]
        active = state.get("active_items") or {}
        for key, item in list(active.items()):
            if isinstance(item, dict) and item.get("request_id_raw", item.get("request_id")) in (request_id, str(request_id)):
                active.pop(key, None)
        item = state.get("active_item") or {}
        if item.get("request_id_raw", item.get("request_id")) in (request_id, str(request_id)):
            state.pop("active_item", None)


def _client_operation(data):
    from ccc_server.codex_capabilities import get_codex_catalog, validate_codex_operation
    method = data.get("method")
    if not isinstance(method, str):
        raise ValueError("Choose an operation")
    catalog = get_codex_catalog()
    selected = next((entry for entry in catalog.get("methods", []) if entry.get("method") == method), None)
    if not selected or not selected.get("available"):
        raise ValueError((selected or {}).get("unavailable_reason") or "Unknown Codex operation")
    raw_params = data.get("params", {})
    context = data.get("context", {})
    if not isinstance(raw_params, dict) or not isinstance(context, dict):
        raise ValueError("Parameters and context must be objects")
    params = _client_scope(method, raw_params, context, descriptor=selected)
    descriptor = validate_codex_operation(method, params, catalog=catalog)
    # Native process and watcher handles are capabilities tied to the repo
    # where this client created them. Do not accept arbitrary global handles.
    context_repo = str(Path(context["repo_path"]).expanduser().resolve()) if context.get("repo_path") else ""
    for handle_field in ("processId", "processHandle", "watchId"):
        handle = params.get(handle_field)
        if handle and method not in ("command/exec", "process/spawn", "fs/watch"):
            with _CLIENT_LOCK:
                if _CLIENT_HANDLES.get(str(handle)) != context_repo:
                    raise ValueError("This terminal or watcher belongs to a different workspace")
    mutating = not descriptor.get("read_only", False)
    action_id = data.get("action_id")
    if mutating and (not isinstance(action_id, str) or not 8 <= len(action_id) <= 128):
        raise ValueError("An action receipt is required")
    generation = None
    fingerprint = hashlib.sha256(json.dumps([method, params, context], sort_keys=True).encode()).hexdigest()
    reserved = []
    if mutating:
        with _CLIENT_LOCK:
            generation = CODEX_CONVERSATIONS.generation
            if not generation or data.get("generation") != generation:
                return {"ok": False, "code": "stale_connection", "generation": generation,
                        "error": "The Codex connection changed. Read the current task state before trying again."}
            old = _CLIENT_ACTIONS.get(action_id)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Action receipt already belongs to another operation")
                return copy.deepcopy(old["result"])
            if action_id in _CLIENT_RECEIPTS:
                raise ValueError("This action was already submitted and will not be sent again")
            if len(_CLIENT_RECEIPTS) >= _CLIENT_RECEIPT_LIMIT:
                raise ValueError("This connection has reached its action receipt limit")
            if len(_CLIENT_ACTIONS) >= 128:
                disposable = next((key for key, value in _CLIENT_ACTIONS.items() if value.get("state") == "complete"), None)
                if disposable is None:
                    raise ValueError("Too many unresolved actions; wait for Codex to finish")
                _CLIENT_ACTIONS.pop(disposable)
            if method in ("command/exec", "process/spawn", "fs/watch"):
                reserved = [str(params[field]) for field in ("processId", "processHandle", "watchId") if params.get(field)]
                if any(handle in _CLIENT_HANDLES for handle in reserved):
                    raise ValueError("This terminal or watcher id is already in use")
                if len(_CLIENT_HANDLES) + len(reserved) > 256:
                    raise ValueError("Too many active terminals or file watchers")
                for handle in reserved:
                    _CLIENT_HANDLES[handle] = context_repo
            _CLIENT_RECEIPTS[action_id] = fingerprint
            _CLIENT_ACTIONS[action_id] = {"fingerprint": fingerprint, "state": "pending",
                "result": {"ok": False, "uncertain": True, "error": "This action is already in progress; it will not be sent twice"}}
    result = _client_rpc(method, None if descriptor.get("params_type") == "null" else params,
                         expected_generation=generation if mutating else None)
    if result.get("ok"):
        output = result.get("result")
        with _CLIENT_LOCK:
            for obj in (params, output if isinstance(output, dict) else {}):
                for field in ("processId", "processHandle", "watchId"):
                    if obj.get(field) and method in ("process/spawn", "fs/watch"):
                        _CLIENT_HANDLES[str(obj[field])] = context_repo
            if params.get("threadId"):
                _CLIENT_THREADS.pop(params["threadId"], None)
            if method == "fs/unwatch" and params.get("watchId"):
                _CLIENT_HANDLES.pop(str(params["watchId"]), None)
    result = redact_client_data(result)
    if mutating:
        with _CLIENT_LOCK:
            if method == "command/exec" and result.get("ok") or not result.get("ok") and not result.get("uncertain"):
                for handle in reserved:
                    _CLIENT_HANDLES.pop(handle, None)
            entry = _CLIENT_ACTIONS.get(action_id)
            if entry and generation == CODEX_CONVERSATIONS.generation:
                entry["result"] = copy.deepcopy(result)
                entry["state"] = "uncertain" if result.get("uncertain") else "complete"
    return result


def codex_client_dispatch(action, data):
    """Run on the engine owner; no raw credentials are written to its ledger."""
    from ccc_server.codex_capabilities import get_codex_catalog
    try:
        if not isinstance(data, dict):
            raise ValueError("Client request must be an object")
        if "context" in data and not isinstance(data["context"], dict):
            raise ValueError("Client context must be an object")
        if action in ("catalog", "schema", "preferences"):
            if action == "preferences":
                if not isinstance(data.get("experimental"), bool):
                    raise ValueError("Preview preference must be a boolean")
                os.environ["CCC_CODEX_EXPERIMENTAL"] = "1" if data["experimental"] else "0"
            catalog = get_codex_catalog()
            if action == "schema":
                descriptor = next((d for d in catalog.get("methods", []) if d["method"] == data.get("method")), None)
                if descriptor is None:
                    raise ValueError("Unknown operation")
                return {"ok": True, "descriptor": descriptor}
            return {**{k: v for k, v in catalog.items() if k != "methods"},
                    "experimental_enabled": os.environ.get("CCC_CODEX_EXPERIMENTAL") == "1",
                    "methods": [{k: v for k, v in d.items() if k != "params_schema"} for d in catalog.get("methods", [])]}
        if action == "operation":
            return _client_operation(data)
        if action == "history":
            return _client_history(data)
        context = data.get("context") or {}
        tid = context.get("thread_id")
        if not tid:
            raise ValueError("Select a Codex task")
        _client_scope("thread/read", {"threadId": tid}, context)
        if action == "state":
            return {**CODEX_CONVERSATIONS.snapshot(tid), "requests": CODEX_REQUESTS.snapshot(tid)["requests"]}
        if action == "events":
            cursor = max(0, int(data.get("cursor") or 0))
            return {**CODEX_CONVERSATIONS.events_since(cursor, data.get("generation"), tid),
                    "requests": CODEX_REQUESTS.snapshot(tid)["requests"]}
        if action == "respond":
            with _CLIENT_LOCK:
                transport = _CLIENT_TRANSPORT
                if transport is None or transport is not _core._CODEX_APP_SERVER_TRANSPORT:
                    raise ValueError("The original connection is no longer available")
                result = CODEX_REQUESTS.respond(data.get("key"), data.get("result"),
                    generation=data.get("generation"), thread_id=tid, send=transport.send_json)
            _client_legacy_request_resolved(tid, result["request_id"])
            CODEX_CONVERSATIONS.record("serverRequest/resolved", {"threadId": tid, "requestId": result["request_id"]})
            return {"ok": True}
        raise ValueError("Unknown client action")
    except (ValueError, TypeError, OSError, RecursionError) as error:
        return {"ok": False, "error": str(error)[:400], "code": "codex_client_error"}


def codex_client_call(action, data):
    # Engine query dispatch deliberately does not persist the payload. This
    # channel carries OAuth answers and audio as well as ordinary operations.
    # Mutation receipts above prevent a retry from submitting an action twice.
    routed = _core._control_plane_engine_call("codex", "client", {
        "action": action, "data": data}, mutate=False, timeout_ms=35000)
    if routed is not None:
        return routed
    return codex_client_dispatch(action, data)
