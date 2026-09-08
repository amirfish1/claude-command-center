"""Scoped client service for the Codex workspace.

The browser never owns a native connection. Calls and volatile credentials
travel through the existing engine owner, without copying them into the work
ledger. Mutations use connection-local action receipts and are never retried.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import threading
import tempfile
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
_CLIENT_QUEUE_SWITCHES = set()
_CLIENT_RECEIPT_LIMIT = 65536
_CLIENT_COMMAND_LIMIT_MS = 30 * 60 * 1000
_CLIENT_THREADS = OrderedDict()
_CLIENT_HANDLES = OrderedDict()
_CLIENT_TOOLS = {}
_CLIENT_TOOL_SLOTS = threading.BoundedSemaphore(4)
_CLIENT_TRANSPORT = None
_CLIENT_PREFERENCES_LOADED = False
_CLIENT_LIFECYCLE_COND = threading.Condition()
_CLIENT_LIFECYCLE_PENDING = OrderedDict()
_CLIENT_LIFECYCLE_LIMIT = 2048
_CLIENT_LIFECYCLE_WORKER = None
_CLIENT_LIFECYCLE_LAST_ERROR = None


def _client_lifecycle_worker():
    global _CLIENT_LIFECYCLE_LAST_ERROR
    while True:
        with _CLIENT_LIFECYCLE_COND:
            while not _CLIENT_LIFECYCLE_PENDING:
                _CLIENT_LIFECYCLE_COND.wait()
            sid, method = _CLIENT_LIFECYCLE_PENDING.popitem(last=False)
        try:
            _core._codex_sync_native_lifecycle(method, {sid})
            _CLIENT_LIFECYCLE_LAST_ERROR = None
        except Exception as error:
            _client_requeue_lifecycle_failure(sid, method, error)
            try:
                _core._log_activity(
                    "codex-client", "LIFECYCLE_SYNC_PENDING",
                    f"method={method} thread={sid} error={type(error).__name__}",
                )
            except Exception:
                print(
                    f"[codex-client] lifecycle sync pending for {method} {sid}: "
                    f"{type(error).__name__}", flush=True,
                )
            time.sleep(1)


def _client_requeue_lifecycle_failure(sid, method, error):
    """Retain failed work unless a newer exact state is already pending."""
    global _CLIENT_LIFECYCLE_LAST_ERROR
    with _CLIENT_LIFECYCLE_COND:
        if sid not in _CLIENT_LIFECYCLE_PENDING:
            _CLIENT_LIFECYCLE_PENDING[sid] = method
        _CLIENT_LIFECYCLE_LAST_ERROR = {
            "method": method, "thread_id": sid,
            "error": str(error)[:240], "ts": time.time(),
        }


def _client_enqueue_lifecycle(method, thread_ids):
    """Coalesce exact native ids; the reader never performs filesystem work."""
    global _CLIENT_LIFECYCLE_WORKER, _CLIENT_LIFECYCLE_LAST_ERROR
    if os.environ.get("CCC_EPHEMERAL"):
        return True
    ids = {sid.strip() for sid in thread_ids if isinstance(sid, str) and sid.strip()}
    if method not in ("thread/archived", "thread/unarchived", "thread/deleted") or not ids:
        return False
    accepted = True
    with _CLIENT_LIFECYCLE_COND:
        for sid in sorted(ids):
            current = _CLIENT_LIFECYCLE_PENDING.get(sid)
            if current == "thread/deleted":
                continue
            if sid not in _CLIENT_LIFECYCLE_PENDING and len(_CLIENT_LIFECYCLE_PENDING) >= _CLIENT_LIFECYCLE_LIMIT:
                accepted = False
                _CLIENT_LIFECYCLE_LAST_ERROR = {
                    "method": method, "thread_id": sid,
                    "error": "lifecycle sync queue is full", "ts": time.time(),
                }
                try:
                    _core._log_activity(
                        "codex-client", "LIFECYCLE_SYNC_OVERFLOW",
                        f"method={method} thread={sid}",
                    )
                except Exception:
                    print(
                        f"[codex-client] lifecycle sync queue full for {method} {sid}",
                        flush=True,
                    )
                continue
            _CLIENT_LIFECYCLE_PENDING[sid] = method
            _CLIENT_LIFECYCLE_PENDING.move_to_end(sid)
        if _CLIENT_LIFECYCLE_WORKER is None or not _CLIENT_LIFECYCLE_WORKER.is_alive():
            _CLIENT_LIFECYCLE_WORKER = threading.Thread(
                target=_client_lifecycle_worker,
                daemon=True,
                name="codex-client-lifecycle",
            )
            _CLIENT_LIFECYCLE_WORKER.start()
        _CLIENT_LIFECYCLE_COND.notify()
    return accepted


def _client_retire_deleted_thread(tid):
    if not isinstance(tid, str) or not tid:
        return
    CODEX_REQUESTS.cancel_thread(tid, CODEX_REQUESTS.generation)
    _core._codex_retire_deleted_thread_state({tid}, persist=False)
    with _CLIENT_LOCK:
        _CLIENT_THREADS.pop(tid, None)


def _client_preferences_path():
    return _core.COMMAND_CENTER_STATE_DIR / "codex-client-preferences.json"


def _client_load_preferences():
    global _CLIENT_PREFERENCES_LOADED
    with _CLIENT_LOCK:
        if _CLIENT_PREFERENCES_LOADED:
            return
        if "CCC_CODEX_EXPERIMENTAL" not in os.environ and not os.environ.get("CCC_EPHEMERAL"):
            try:
                with _client_preferences_path().open(encoding="utf-8") as handle:
                    raw = handle.read(4097)
                value = json.loads(raw) if len(raw) <= 4096 else {}
                if isinstance(value, dict) and isinstance(value.get("experimental"), bool):
                    os.environ["CCC_CODEX_EXPERIMENTAL"] = "1" if value["experimental"] else "0"
            except (OSError, ValueError):
                pass
        _CLIENT_PREFERENCES_LOADED = True


def _client_save_preferences(experimental):
    if not os.environ.get("CCC_EPHEMERAL"):
        destination = _client_preferences_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix=".codex-client-", dir=str(destination.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"experimental": experimental}, handle)
            os.replace(filename, destination)
        finally:
            try:
                os.unlink(filename)
            except FileNotFoundError:
                pass
    os.environ["CCC_CODEX_EXPERIMENTAL"] = "1" if experimental else "0"


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


def _client_rpc_timeout(method, params):
    if method != "command/exec" or not isinstance(params, dict):
        return 25
    duration = params.get("timeoutMs")
    if duration is None:
        duration = _CLIENT_COMMAND_LIMIT_MS
    if (isinstance(duration, bool) or not isinstance(duration, (int, float))
            or isinstance(duration, float) and not math.isfinite(duration)):
        return 25
    return max(25, int(min(_CLIENT_COMMAND_LIMIT_MS, max(0, duration)) / 1000) + 5)


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
    tid = params.get("threadId") or params.get("conversationId")
    tombstone = CODEX_CONVERSATIONS.snapshot(tid).get("thread") if tid else None
    if tombstone and tombstone.get("deleted") and method != "thread/deleted":
        if "id" in payload and current is not None:
            current.send_json({"id": payload["id"], "error": {
                "code": -32600, "message": "This task has been deleted"}})
        return True
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
    if method in ("thread/archived", "thread/unarchived", "thread/deleted") and tid:
        if method == "thread/deleted":
            _client_retire_deleted_thread(tid)
        _client_enqueue_lifecycle(method, {tid})
        if method == "thread/deleted":
            # The legacy handler creates state for every notification before
            # specializing it. A delete has already retired that state above.
            return True
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


def _client_sync_lifecycle(method, params, result, context_repo):
    """Mirror explicit native user actions into CCC's existing sidebar data."""
    if os.environ.get("CCC_EPHEMERAL"):
        return
    output = result.get("result")
    thread = output.get("thread") if isinstance(output, dict) else None
    if method in ("thread/start", "thread/fork") and isinstance(thread, dict) and thread.get("id"):
        _core._codex_thread_registry_upsert(thread["id"], source="ccc-client",
            visibility="user-visible", transport_owner="ccc-managed-app-server",
            transport=_core._codex_app_server_transport_kind(),
            cwd=thread.get("cwd") or context_repo, repo_path=context_repo,
            title=thread.get("name") or thread.get("preview") or "Codex conversation",
            parent_session_id=thread.get("forkedFromId") or (
                params.get("threadId") if method == "thread/fork" else ""))
    elif method == "thread/name/set":
        sid = params.get("threadId")
        name = params.get("name") or ""
        _core._save_session_name_override(sid, name or None)
        _core._codex_thread_registry_upsert(sid, source="ccc-client", name=name, title=name)
    elif method in ("thread/archive", "thread/unarchive", "thread/delete"):
        notification = {
            "thread/archive": "thread/archived",
            "thread/unarchive": "thread/unarchived",
            "thread/delete": "thread/deleted",
        }[method]
        _core._codex_sync_native_lifecycle(notification, {params.get("threadId")})


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
    if method == "command/exec":
        if params.get("disableTimeout") is True:
            raise ValueError("Use a host process for commands without a time limit")
        if params.get("timeoutMs") is None:
            params["timeoutMs"] = _CLIENT_COMMAND_LIMIT_MS
        elif isinstance(params["timeoutMs"], (int, float)) and params["timeoutMs"] > _CLIENT_COMMAND_LIMIT_MS:
            raise ValueError("Commands support up to 30 minutes; use a host process for longer work")
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
            if method.startswith("thread/queue/") and params.get("threadId") in _CLIENT_QUEUE_SWITCHES:
                raise ValueError("Wait for the message queue ownership change")
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
                "method": method, "thread_id": params.get("threadId"),
                "result": {"ok": False, "uncertain": True, "error": "This action is already in progress; it will not be sent twice"}}
    if mutating and method.startswith("thread/queue/"):
        from ccc_server.codex_queue_owner import begin_native_queue_action
        try:
            begin_native_queue_action(params["threadId"], action_id)
        except (ValueError, OSError) as error:
            with _CLIENT_LOCK:
                result = {"ok": False, "error": str(error), "code": "queue_owner_conflict"}
                _CLIENT_ACTIONS[action_id]["result"] = result
                _CLIENT_ACTIONS[action_id]["state"] = "complete"
            return result
    result = _client_rpc(method, None if descriptor.get("params_type") == "null" else params,
                         timeout=_client_rpc_timeout(method, params),
                         expected_generation=generation if mutating else None)
    if mutating and method.startswith("thread/queue/") and not result.get("uncertain"):
        from ccc_server.codex_queue_owner import finish_native_queue_action
        try:
            finish_native_queue_action(params["threadId"], action_id)
        except (ValueError, OSError):
            result["queue_owner_sync_pending"] = True
    if result.get("ok"):
        output = result.get("result")
        try:
            _client_sync_lifecycle(method, params, result, context_repo)
        except Exception:
            # The native mutation already succeeded. A mirror failure must
            # never turn it into a failed action that a caller might replay.
            result["sidebar_sync_pending"] = True
            notification = {
                "thread/archive": "thread/archived",
                "thread/unarchive": "thread/unarchived",
                "thread/delete": "thread/deleted",
            }.get(method)
            if notification and params.get("threadId"):
                _client_enqueue_lifecycle(notification, {params["threadId"]})
        if method in ("thread/revert", "thread/rollback"):
            CODEX_CONVERSATIONS.invalidate_history(params.get("threadId"))
        elif method == "thread/delete":
            CODEX_CONVERSATIONS.record("thread/deleted", {
                "threadId": params.get("threadId"),
                "thread": {"id": params.get("threadId"), "cwd": context_repo},
            })
            _client_retire_deleted_thread(params.get("threadId"))
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
            _client_load_preferences()
            if action == "preferences":
                if not isinstance(data.get("experimental"), bool):
                    raise ValueError("Preview preference must be a boolean")
                _client_save_preferences(data["experimental"])
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
            from ccc_server.codex_queue_owner import native_queue_owned
            result = _client_history(data)
            result["queue_owner"] = "native" if native_queue_owned((data.get("context") or {}).get("thread_id")) else "ccc"
            return result
        context = data.get("context") or {}
        tid = context.get("thread_id")
        if not tid:
            raise ValueError("Select a Codex task")
        tombstone = CODEX_CONVERSATIONS.snapshot(tid).get("thread")
        if action in ("state", "events") and tombstone and tombstone.get("deleted"):
            validate_operation_context("thread/read", {"threadId": tid}, context,
                resolve_repo=_core.resolve_repo_path, read_thread=lambda _: tombstone)
        else:
            _client_scope("thread/read", {"threadId": tid}, context)
        if action == "queue-owner":
            from ccc_server.codex_queue_owner import claim_native_queue, release_native_queue, native_queue_owned
            with _CLIENT_LOCK:
                if tid in _CLIENT_QUEUE_SWITCHES or any(
                    entry.get("thread_id") == tid and entry.get("method", "").startswith("thread/queue/")
                    and entry.get("state") != "complete" for entry in _CLIENT_ACTIONS.values()
                ):
                    raise ValueError("Wait for the pending Codex queue action before switching queues")
                _CLIENT_QUEUE_SWITCHES.add(tid)
            try:
                if data.get("owner") == "native":
                    claim_native_queue(tid)
                elif data.get("owner") == "ccc":
                    release_native_queue(tid, lambda: _client_rpc("thread/queue/list", {"threadId": tid}))
                else:
                    raise ValueError("Choose the CCC or Codex queue")
                return {"ok": True, "queue_owner": "native" if native_queue_owned(tid) else "ccc"}
            finally:
                with _CLIENT_LOCK:
                    _CLIENT_QUEUE_SWITCHES.discard(tid)
        if action == "state":
            from ccc_server.codex_queue_owner import native_queue_owned
            return {**CODEX_CONVERSATIONS.snapshot(tid), "requests": CODEX_REQUESTS.snapshot(tid)["requests"],
                    "queue_owner": "native" if native_queue_owned(tid) else "ccc"}
        if action == "events":
            from ccc_server.codex_queue_owner import native_queue_owned
            cursor = max(0, int(data.get("cursor") or 0))
            return {**CODEX_CONVERSATIONS.events_since(cursor, data.get("generation"), tid),
                    "queue_owner": "native" if native_queue_owned(tid) else "ccc", "requests": CODEX_REQUESTS.snapshot(tid)["requests"]}
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
    deadline = 70000 if action == "history" else 45000
    if action == "operation" and isinstance(data, dict):
        deadline = max(deadline, (_client_rpc_timeout(data.get("method"), data.get("params")) + 10) * 1000)
    routed = _core._control_plane_engine_call("codex", "client", {
        "action": action, "data": data}, mutate=False, timeout_ms=deadline)
    result = routed if routed is not None else codex_client_dispatch(action, data)
    if action == "operation" and result.get("ok") and isinstance(data, dict):
        method = data.get("method")
        if method in ("thread/start", "thread/fork", "thread/name/set", "thread/archive", "thread/unarchive", "thread/delete", "thread/metadata/update"):
            _core._invalidate_dashboard("archive", reason="codex-client")
            _core._invalidate_dashboard("sessions", reason="codex-client")
            if method == "thread/name/set":
                params = data.get("params") or {}
                name = params.get("name") or ""
                _core._publish_dashboard_patch("conversation.patch", "conversation", params.get("threadId"),
                    {"name": name, "title": name, "display_name": name, "name_overridden": bool(name)})
    return result
