"""Capability discovery and input validation for the Codex app-server protocol.

The module is deliberately stdlib-only.  Importing it performs no filesystem or
subprocess work; protocol discovery happens only when the catalog is requested.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from ccc_server import core as _core


_SAFE_READ_METHODS = frozenset(
    {
        "account/rateLimits/read",
        "account/read",
        "account/usage/read",
        "account/workspaceMessages/read",
        "app/installed",
        "app/list",
        "app/read",
        "collaborationMode/list",
        "config/read",
        "configRequirements/read",
        "environment/info",
        "environment/status",
        "experimentalFeature/list",
        "externalAgentConfig/detect",
        "externalAgentConfig/import/readHistories",
        "fs/getMetadata",
        "fs/readDirectory",
        "fs/readFile",
        "fuzzyFileSearch",
        "hooks/list",
        "mcpServer/resource/read",
        "mcpServerStatus/list",
        "model/list",
        "modelProvider/capabilities/read",
        "permissionProfile/list",
        "plugin/installed",
        "plugin/list",
        "plugin/read",
        "plugin/search",
        "plugin/share/list",
        "plugin/skill/read",
        "project/list",
        "project/read",
        "remoteControl/client/list",
        "remoteControl/pairing/status",
        "remoteControl/status/read",
        "server/diagnostics",
        "skills/list",
        "thread/backgroundTerminals/list",
        "thread/goal/get",
        "thread/items/list",
        "thread/list",
        "thread/loaded/list",
        "thread/queue/list",
        "thread/read",
        "thread/realtime/listVoices",
        "thread/search",
        "thread/searchOccurrences",
        "thread/timeline/list",
        "thread/turns/list",
        "threadSection/list",
        "windowsSandbox/readiness",
    }
)

_PREVIEW_METHODS = frozenset(
    {
        "thread/inject_items",
        "plugin/install",
        "plugin/list",
        "plugin/read",
        "plugin/reconcile",
        "plugin/uninstall",
    }
)
_PREVIEW_PREFIXES = ("plugin/share/",)
_INTERNAL_METHODS = frozenset(
    {
        "externalAgentConfig/import/recordHistory",
        "initialize",
        "thread/decrement_elicitation",
        "thread/increment_elicitation",
    }
)

_CACHE_VERSION = 3
_CACHE_DIR = Path.home() / ".claude" / "command-center" / "cache" / "codex-capabilities"
_CATALOG_MEMORY: dict[str, dict] = {}
_CATALOG_LOCK = threading.Lock()
_HOST_PLATFORM = sys.platform

_SPECIAL_TITLES = {
    "fs/readFile": "Read File",
    "fs/writeFile": "Write File",
    "fs/readDirectory": "Browse Directory",
    "fs/createDirectory": "Create Directory",
    "fs/getMetadata": "File Details",
    "fs/copy": "Copy File",
    "fs/remove": "Remove File",
    "fs/watch": "Watch Files",
    "fs/unwatch": "Stop Watching Files",
    "fuzzyFileSearch": "Find Files",
    "permissionProfile/list": "Permission Profiles",
    "windowsSandbox/setupStart": "Set Up Windows Sandbox",
    "windowsSandbox/readiness": "Windows Sandbox Readiness",
    "thread/list": "List Conversations",
    "thread/name/set": "Set Conversation Name",
    "thread/read": "Read Conversation",
    "thread/start": "Start Conversation",
    "thread/loaded/list": "List Loaded Conversations",
    "threadSection/list": "List Conversation Sections",
    "account/rateLimitResetCredit/consume": "Use a Usage Reset",
    "account/sendAddCreditsNudgeEmail": "Request More Credits",
}

_ACTION_NOTES = {
    "thread/delete": "Permanently delete this conversation and its spawned descendant conversations.",
    "thread/archive": "Archive this conversation and its spawned descendant conversations.",
    "thread/rollback": "Remove recent turns from the conversation context. This does not restore files on disk.",
    "account/rateLimitResetCredit/consume": "Use one available usage reset for this account.",
    "account/sendAddCreditsNudgeEmail": "Send an email request for more credits to the workspace owner.",
    "feedback/upload": "Send this feedback and selected diagnostics to OpenAI.",
}

_ACTION_WORDS = frozenset(
    {
        "add",
        "archive",
        "cancel",
        "checkout",
        "clean",
        "clear",
        "compact",
        "consume",
        "create",
        "delete",
        "detect",
        "disable",
        "discover",
        "enable",
        "exec",
        "fork",
        "get",
        "import",
        "install",
        "interrupt",
        "kill",
        "list",
        "login",
        "logout",
        "move",
        "read",
        "reconcile",
        "reload",
        "remove",
        "reset",
        "resize",
        "resume",
        "revert",
        "revoke",
        "rollback",
        "save",
        "search",
        "set",
        "setup",
        "spawn",
        "start",
        "steer",
        "stop",
        "terminate",
        "unarchive",
        "uninstall",
        "unsubscribe",
        "update",
        "upgrade",
        "upload",
        "watch",
        "write",
    }
)


def codex_method_is_mutating(method: str) -> bool:
    """Return false only for methods explicitly known to be side-effect free."""

    return not isinstance(method, str) or method not in _SAFE_READ_METHODS


def _camel_words(value: str) -> list[str]:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = value.replace("_", " ").replace("-", " ")
    return [word for word in value.split() if word]


def _title_words(value: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in _camel_words(value))


def _title_for_method(method: str) -> str:
    special = _SPECIAL_TITLES.get(method)
    if special:
        return special
    parts = method.split("/")
    tail_words = _camel_words(parts[-1])
    action_at = next(
        (index for index, word in enumerate(tail_words) if word.lower() in _ACTION_WORDS),
        None,
    )
    if action_at is not None:
        action = tail_words[action_at:]
        subject = parts[:-1] + ([" ".join(tail_words[:action_at])] if action_at else [])
        words = action + [word for part in subject for word in _camel_words(part)]
    elif parts[-1].lower() in _ACTION_WORDS:
        words = _camel_words(parts[-1]) + [
            word for part in parts[:-1] for word in _camel_words(part)
        ]
    else:
        words = [word for part in parts for word in _camel_words(part)]
    return " ".join(word[:1].upper() + word[1:] for word in words)


def _group_for_method(method: str) -> str:
    if method.startswith("thread/queue/"):
        return "Work queue"
    if method.startswith("thread/goal/"):
        return "Goals"
    if method.startswith("thread/realtime/"):
        return "Voice"
    if method.startswith("thread/backgroundTerminals/") or method.startswith(
        ("command/", "process/")
    ) or method == "thread/shellCommand":
        return "Terminals"
    if method.startswith(("thread/", "turn/", "threadSection/")):
        return "Conversations"
    if method.startswith("project/"):
        return "Projects"
    if method.startswith("account/"):
        return "Account"
    if method.startswith(("model/", "modelProvider/", "collaborationMode/")):
        return "Models"
    if method.startswith(("config/", "configRequirements/", "experimentalFeature/", "permissionProfile/")):
        return "Settings"
    if method.startswith("skills/") or method.startswith("plugin/skill/"):
        return "Skills"
    if method.startswith(("plugin/", "marketplace/")):
        return "Plugins"
    if method.startswith(("app/", "mcpServer/", "mcpServerStatus/")):
        return "Connections"
    if method.startswith(("fs/", "fuzzyFileSearch")):
        return "Files"
    if method.startswith("review/"):
        return "Reviews"
    if method.startswith(("environment/", "windowsSandbox/")):
        return "Environments"
    if method.startswith("remoteControl/"):
        return "Remote control"
    return "Diagnostics"


def _scope_for_method(method: str) -> str:
    if method.startswith("account/"):
        return "account"
    if method.startswith(("thread/", "turn/", "threadSection/", "review/")):
        return "thread"
    if method.startswith(
        (
            "app/",
            "config/",
            "configRequirements/",
            "experimentalFeature/",
            "externalAgentConfig/",
            "fs/",
            "fuzzyFileSearch",
            "marketplace/",
            "mcpServer/",
            "mcpServerStatus/",
            "plugin/",
            "project/",
            "skills/",
        )
    ):
        return "workspace"
    return "server"


def _preview_method(method: str, experimental_only: bool) -> bool:
    return (
        experimental_only
        or method in _PREVIEW_METHODS
        or method.startswith(_PREVIEW_PREFIXES)
    )


def _params_marked_experimental(variant, root):
    pending = [(variant.get("properties", {}).get("params", {}), 0)]
    seen = set()
    while pending:
        schema, depth = pending.pop()
        if not isinstance(schema, dict) or depth > 12:
            continue
        if re.match(r"\s*EXPERIMENTAL\b", str(schema.get("description", "")), re.I):
            return True
        ref = schema.get("$ref")
        if ref and ref not in seen:
            seen.add(ref)
            try:
                pending.append((_local_ref_target(ref, root, "$"), depth + 1))
            except ValueError:
                pass
        for key in ("oneOf", "anyOf", "allOf"):
            pending.extend((entry, depth + 1) for entry in schema.get(key, []) if isinstance(entry, dict))
    return False


def _unavailable_reason(method: str, direction: str, preview: bool) -> str | None:
    if method.startswith("mock/"):
        return "Test-only Codex protocol method"
    if method in _INTERNAL_METHODS:
        return "Managed internally by the CCC Codex transport"
    if method in {"attestation/generate", "account/chatgptAuthTokens/refresh"}:
        return "Host identity and attestation requests are not exposed by CCC"
    if direction == "client_request" and method.startswith("windowsSandbox/") and _HOST_PLATFORM != "win32":
        return "Available on Windows hosts"
    if preview:
        return "Enable Preview features to use this action"
    return None


def _root_definitions(schema: dict) -> dict:
    definitions = schema.get("definitions", schema.get("$defs", {}))
    return definitions if isinstance(definitions, dict) else {}


def _reachable_definitions(schema: dict, direction_schema: dict) -> tuple[str, dict]:
    container = "$defs" if "$defs" in direction_schema and "definitions" not in direction_schema else "definitions"
    available = _root_definitions(direction_schema)
    found = {}
    pending = [schema]
    visited_nodes = set()
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node)
            continue
        if not isinstance(node, dict) or id(node) in visited_nodes:
            continue
        visited_nodes.add(id(node))
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(("#/definitions/", "#/$defs/")):
            raw_name = ref.split("/", 2)[-1].split("/", 1)[0]
            name = raw_name.replace("~1", "/").replace("~0", "~")
            if name in available and name not in found:
                found[name] = copy.deepcopy(available[name])
                pending.append(found[name])
        for key, child in node.items():
            if key not in {"definitions", "$defs"}:
                pending.append(child)
    return container, found


def _resolve_local_ref(schema: dict, root: dict) -> dict | None:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None
    node = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, dict) else None


def _schema_types(
    schema: object,
    root: dict,
    seen_refs: frozenset[str] = frozenset(),
) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref not in seen_refs:
        target = _resolve_local_ref(schema, root)
        if target is not None:
            return _schema_types(target, root, seen_refs | {ref})
    declared = schema.get("type")
    if isinstance(declared, str):
        return {declared}
    if isinstance(declared, list):
        return {item for item in declared if isinstance(item, str)}
    for keyword in ("oneOf", "anyOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            return set().union(*(_schema_types(branch, root, seen_refs) for branch in branches))
    branches = schema.get("allOf")
    if isinstance(branches, list):
        types = set().union(*(_schema_types(branch, root, seen_refs) for branch in branches))
        if types:
            return types
    if any(key in schema for key in ("properties", "required", "additionalProperties")):
        return {"object"}
    return set()


def _object_form_schema(schema: dict, root: dict, types: set[str]) -> dict:
    if types == {"null"}:
        return {"type": "object", "additionalProperties": False}
    if "object" not in types or "null" not in types:
        return copy.deepcopy(schema)
    for keyword in ("oneOf", "anyOf"):
        branches = schema.get(keyword)
        if not isinstance(branches, list):
            continue
        object_branches = [
            branch for branch in branches if "object" in _schema_types(branch, root)
        ]
        if len(object_branches) == 1:
            branch = object_branches[0]
            target = _resolve_local_ref(branch, root) if isinstance(branch, dict) else None
            return copy.deepcopy(target if target is not None else branch)
        if object_branches:
            result = copy.deepcopy(schema)
            result[keyword] = copy.deepcopy(object_branches)
            return result
    return copy.deepcopy(schema)


def _params_schema(variant: dict, direction_schema: dict) -> tuple[dict, str]:
    properties = variant.get("properties")
    params = properties.get("params") if isinstance(properties, dict) else None
    if not isinstance(params, dict):
        params = {"type": "object"}
    target = _resolve_local_ref(params, direction_schema)
    wire_root = target if target is not None else params
    types = _schema_types(wire_root, direction_schema)
    params_type = "null" if types == {"null"} else "object" if "object" in types else (
        next(iter(types)) if len(types) == 1 else "union"
    )
    result = _object_form_schema(wire_root, direction_schema, types)
    container, definitions = _reachable_definitions(result, direction_schema)
    if definitions:
        result[container] = definitions
    if "$schema" in direction_schema:
        result["$schema"] = direction_schema["$schema"]
    if not result.get("title"):
        ref = params.get("$ref", "")
        ref_name = ref.rsplit("/", 1)[-1] if ref else "Parameters"
        result["title"] = _title_words(ref_name)
    return result, params_type


def _variants_by_method(schema: object) -> dict[str, dict]:
    if not isinstance(schema, dict):
        return {}
    variants = {}
    for variant in schema.get("oneOf", []):
        if not isinstance(variant, dict):
            continue
        properties = variant.get("properties")
        method_schema = properties.get("method") if isinstance(properties, dict) else None
        enum = method_schema.get("enum") if isinstance(method_schema, dict) else None
        if not isinstance(enum, list):
            continue
        for method in enum:
            if isinstance(method, str) and method:
                variants[method] = variant
    return variants


def _descriptors_for_direction(
    stable_schema: object,
    full_schema: object,
    direction: str,
) -> list[dict]:
    stable_variants = _variants_by_method(stable_schema)
    full_variants = _variants_by_method(full_schema)
    stable_root = stable_schema if isinstance(stable_schema, dict) else {}
    full_root = full_schema if isinstance(full_schema, dict) else {}
    descriptors = []
    for method in sorted(set(stable_variants) | set(full_variants)):
        experimental_only = method not in stable_variants
        root = full_root if experimental_only else stable_root
        variant = full_variants[method] if experimental_only else stable_variants[method]
        preview = _preview_method(method, experimental_only) or _params_marked_experimental(variant, root)
        reason = _unavailable_reason(method, direction, preview)
        params_schema, params_type = _params_schema(variant, root)
        descriptor = {
            "method": method,
            "title": _title_for_method(method),
            "group": _group_for_method(method),
            "scope": _scope_for_method(method),
            "read_only": not codex_method_is_mutating(method),
            "experimental": preview,
            "available": reason is None,
            "unavailable_reason": reason,
            "params_schema": params_schema,
            "params_type": params_type,
            "direction": direction,
            "internal": method in _INTERNAL_METHODS or method.startswith("mock/"),
        }
        if not experimental_only and method in full_variants:
            full_params_schema, full_params_type = _params_schema(
                full_variants[method], full_root
            )
            if full_params_schema != params_schema or full_params_type != params_type:
                descriptor["_experimental_params_schema"] = full_params_schema
                descriptor["_experimental_params_type"] = full_params_type
        description = _ACTION_NOTES.get(method) or variant.get("description")
        if isinstance(description, str) and description.strip():
            descriptor["description"] = description.strip()
        descriptors.append(descriptor)
    return descriptors


def catalog_from_schemas(stable: dict, full: dict, version: str = "") -> dict:
    """Build a deterministic capability catalog from decoded schema bundles."""

    stable = stable if isinstance(stable, dict) else {}
    full = full if isinstance(full, dict) else {}
    canonical = json.dumps(
        {"stable": stable, "full": full, "version": str(version or "")},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    client_requests = _descriptors_for_direction(
        stable.get("ClientRequest"), full.get("ClientRequest"), "client_request"
    )
    server_requests = _descriptors_for_direction(
        stable.get("ServerRequest"), full.get("ServerRequest"), "server_request"
    )
    notifications = _descriptors_for_direction(
        stable.get("ClientNotification"),
        full.get("ClientNotification"),
        "client_notification",
    ) + _descriptors_for_direction(
        stable.get("ServerNotification"),
        full.get("ServerNotification"),
        "server_notification",
    )
    return {
        "ok": True,
        "version": str(version or ""),
        "fingerprint": hashlib.sha256(canonical).hexdigest(),
        "methods": client_requests,
        "server_requests": server_requests,
        "notifications": sorted(
            notifications, key=lambda row: (row["direction"], row["method"])
        ),
    }


_MAX_VALIDATION_DEPTH = 64
_MAX_COLLECTION_ITEMS = 10_000
_MAX_STRING_LENGTH = 1_000_000


def _check_json_structure(value):
    pending = [(value, 0)]
    nodes = 0
    text_bytes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > _MAX_VALIDATION_DEPTH or nodes > 100_000:
            raise ValueError("Input is nested too deeply or contains too many values")
        if isinstance(item, (list, dict)):
            if len(item) > _MAX_COLLECTION_ITEMS:
                raise ValueError("Input has too many properties" if isinstance(item, dict) else "Input has too many items")
            if isinstance(item, dict):
                if not all(isinstance(key, str) for key in item):
                    raise ValueError("Input object keys must be text")
                try:
                    text_bytes += sum(len(key.encode("utf-8")) for key in item)
                except UnicodeEncodeError:
                    raise ValueError("Input contains an invalid Unicode sequence") from None
                pending.extend((child, depth + 1) for child in item.values())
            else:
                pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > _MAX_STRING_LENGTH:
                raise ValueError("Input text exceeds the supported size")
            try:
                text_bytes += len(item.encode("utf-8"))
            except UnicodeEncodeError:
                raise ValueError("Input contains an invalid Unicode sequence") from None
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Input numbers must be finite")
        elif item is not None and not isinstance(item, (bool, int, float)):
            raise ValueError("Input must contain JSON values only")
        if text_bytes > 4 * 1024 * 1024:
            raise ValueError("Input text exceeds the supported total size")


def _canonical_json(value):
    if isinstance(value, dict):
        return ["object", [[key, _canonical_json(child)] for key, child in sorted(value.items())]]
    if isinstance(value, list):
        return ["array", [_canonical_json(child) for child in value]]
    if isinstance(value, bool):
        return ["boolean", value]
    if isinstance(value, (int, float)):
        return ["number", int(value) if isinstance(value, float) and value.is_integer() else value]
    return ["null" if value is None else "string", value]
_SCHEMA_KEYWORDS = frozenset(
    {
        "$defs",
        "$id",
        "$ref",
        "$schema",
        "additionalProperties",
        "allOf",
        "anyOf",
        "const",
        "default",
        "definitions",
        "deprecated",
        "description",
        "enum",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "oneOf",
        "pattern",
        "properties",
        "readOnly",
        "required",
        "title",
        "type",
        "uniqueItems",
        "writeOnly",
    }
)


class _UnsupportedSchema(ValueError):
    pass


def _schema_error(path: str, message: str) -> ValueError:
    return ValueError(f"{path} {message}")


def _json_kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "non-JSON"


def _json_equal(left: object, right: object) -> bool:
    left_kind = _json_kind(left)
    right_kind = _json_kind(right)
    if left_kind != right_kind:
        if {left_kind, right_kind} <= {"integer", "number"}:
            return left == right
        return False
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right)
        )
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    return left == right


def _check_schema_keywords(schema: dict, path: str) -> None:
    unknown = sorted(set(schema) - _SCHEMA_KEYWORDS)
    if unknown:
        keyword = str(unknown[0])[:80]
        raise _UnsupportedSchema(
            f"{path} uses unsupported schema keyword {keyword!r}"
        )


def _local_ref_target(ref: object, root: dict, path: str) -> dict | bool:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise _UnsupportedSchema(f"{path} uses a non-local schema reference")
    node: object = root
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise _UnsupportedSchema(f"{path} uses an unresolved schema reference")
        node = node[part]
    if not isinstance(node, (dict, bool)):
        raise _UnsupportedSchema(f"{path} references a malformed schema")
    return node


def _matches_type(value: object, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    raise _UnsupportedSchema(f"schema declares unsupported type {expected!r}")


def _size_constraint(schema: dict, keyword: str, path: str) -> int | None:
    boundary = schema.get(keyword)
    if boundary is None:
        return None
    if isinstance(boundary, bool) or not isinstance(boundary, int) or boundary < 0:
        raise _UnsupportedSchema(f"{path} has a malformed {keyword}")
    return boundary


def _validate_schema(
    value: object,
    schema: dict | bool,
    root: dict,
    path: str,
    depth: int,
    active_refs: set[tuple[int, str]],
) -> None:
    if depth > _MAX_VALIDATION_DEPTH:
        raise _schema_error(path, "is nested too deeply")
    if schema is True:
        return
    if schema is False:
        raise _schema_error(path, "is not permitted")
    if not isinstance(schema, dict):
        raise _UnsupportedSchema(f"{path} has a malformed schema")
    _check_schema_keywords(schema, path)

    ref = schema.get("$ref")
    if ref is not None:
        ref_key = (id(value), str(ref))
        if ref_key in active_refs:
            raise _UnsupportedSchema(f"{path} contains a schema reference cycle")
        target = _local_ref_target(ref, root, path)
        active_refs.add(ref_key)
        try:
            _validate_schema(value, target, root, path, depth + 1, active_refs)
        finally:
            active_refs.remove(ref_key)

    for keyword in ("allOf",):
        branches = schema.get(keyword)
        if branches is not None:
            if not isinstance(branches, list) or not branches:
                raise _UnsupportedSchema(f"{path} has a malformed {keyword}")
            for branch in branches:
                _validate_schema(value, branch, root, path, depth + 1, active_refs)

    for keyword, exact in (("anyOf", False), ("oneOf", True)):
        branches = schema.get(keyword)
        if branches is None:
            continue
        if not isinstance(branches, list) or not branches:
            raise _UnsupportedSchema(f"{path} has a malformed {keyword}")
        matches = 0
        errors = []
        for branch in branches:
            try:
                _validate_schema(value, branch, root, path, depth + 1, active_refs)
            except _UnsupportedSchema:
                raise
            except ValueError as exc:
                errors.append(str(exc))
            else:
                matches += 1
        if matches == 0:
            detail = (
                "; "
                + max(
                    errors,
                    key=lambda text: (text.count(".") + text.count("["), -len(text)),
                )
                if errors
                else ""
            )
            raise _schema_error(path, f"does not match an allowed shape{detail}")
        if exact and matches != 1:
            raise _schema_error(path, "matches more than one exclusive shape")

    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not choices or not all(isinstance(choice, str) for choice in choices):
            raise _UnsupportedSchema(f"{path} has a malformed type constraint")
        if not any(_matches_type(value, choice) for choice in choices):
            label = " or ".join(choices)
            raise _schema_error(path, f"must be {label}")

    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list):
            raise _UnsupportedSchema(f"{path} has a malformed enum")
        if not any(_json_equal(value, candidate) for candidate in enum):
            raise _schema_error(path, "is not an allowed value")
    if "const" in schema and not _json_equal(value, schema["const"]):
        raise _schema_error(path, "does not match the required constant")

    if isinstance(value, float) and not math.isfinite(value):
        raise _schema_error(path, "must be a finite number")

    if isinstance(value, dict):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise _schema_error(path, "has too many properties")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise _UnsupportedSchema(f"{path} has malformed object properties")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(
            isinstance(name, str) for name in required
        ):
            raise _UnsupportedSchema(f"{path} has malformed required fields")
        missing = [name for name in required if name not in value]
        if missing:
            safe_names = ", ".join(repr(name[:80]) for name in missing[:5])
            raise _schema_error(path, f"is missing required field(s): {safe_names}")
        minimum = _size_constraint(schema, "minProperties", path)
        maximum = _size_constraint(schema, "maxProperties", path)
        if minimum is not None and len(value) < minimum:
            raise _schema_error(path, "has fewer properties than the minimum")
        if maximum is not None and len(value) > maximum:
            raise _schema_error(path, "has more properties than the maximum")
        additional = schema.get("additionalProperties", True)
        extras = [name for name in value if name not in properties]
        if extras and additional is False:
            raise _schema_error(path, "has unrecognized properties")
        if extras and isinstance(additional, (dict, bool)) and additional is not True:
            for name in extras:
                _validate_schema(
                    value[name], additional, root, f"{path}.*", depth + 1, active_refs
                )
        elif extras and additional not in (True, False):
            raise _UnsupportedSchema(f"{path} has malformed additionalProperties")
        for name, child_schema in properties.items():
            if name in value:
                _validate_schema(
                    value[name],
                    child_schema,
                    root,
                    f"{path}.{name}",
                    depth + 1,
                    active_refs,
                )

    if isinstance(value, list):
        if len(value) > _MAX_COLLECTION_ITEMS:
            raise _schema_error(path, "has too many items")
        minimum = _size_constraint(schema, "minItems", path)
        maximum = _size_constraint(schema, "maxItems", path)
        if minimum is not None and len(value) < minimum:
            raise _schema_error(path, "has fewer items than the minimum")
        if maximum is not None and len(value) > maximum:
            raise _schema_error(path, "has more items than the maximum")
        unique = schema.get("uniqueItems", False)
        if not isinstance(unique, bool):
            raise _UnsupportedSchema(f"{path} has a malformed uniqueItems")
        if unique:
            buckets = {}
            for item in value:
                digest = hashlib.sha256(json.dumps(_canonical_json(item), ensure_ascii=False).encode("utf-8")).digest()
                earlier = buckets.setdefault(digest, [])
                if any(_json_equal(item, candidate) for candidate in earlier):
                    raise _schema_error(path, "must contain unique items")
                earlier.append(item)
        items = schema.get("items")
        if items is not None:
            if not isinstance(items, (dict, bool)):
                raise _UnsupportedSchema(f"{path} has unsupported tuple item schemas")
            for index, item in enumerate(value):
                _validate_schema(
                    item, items, root, f"{path}[{index}]", depth + 1, active_refs
                )

    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH:
            raise _schema_error(path, "is too long")
        minimum = _size_constraint(schema, "minLength", path)
        maximum = _size_constraint(schema, "maxLength", path)
        if minimum is not None and len(value) < minimum:
            raise _schema_error(path, "is shorter than the minimum length")
        if maximum is not None and len(value) > maximum:
            raise _schema_error(path, "is longer than the maximum length")
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise _UnsupportedSchema(f"{path} has a malformed pattern")
            try:
                matched = re.search(pattern, value) is not None
            except re.error as exc:
                raise _UnsupportedSchema(f"{path} has an invalid pattern") from exc
            if not matched:
                raise _schema_error(path, "does not match the required pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        format_name = str(schema.get("format", ""))
        if format_name in ("uint", "int"):
            format_name += "64"
        wire = re.fullmatch(r"(u?int)(8|16|32|64|128)", format_name)
        if wire:
            bits = int(wire.group(2))
            lower = 0 if wire.group(1) == "uint" else -(2 ** (bits - 1))
            upper = 2 ** bits - 1 if wire.group(1) == "uint" else 2 ** (bits - 1) - 1
            if not isinstance(value, int) or not lower <= value <= upper:
                raise _schema_error(path, "is outside the supported integer range")
        for keyword, compare, phrase in (
            ("minimum", lambda a, b: a < b, "is below the minimum"),
            ("maximum", lambda a, b: a > b, "is above the maximum"),
            ("exclusiveMinimum", lambda a, b: a <= b, "must exceed the minimum"),
            ("exclusiveMaximum", lambda a, b: a >= b, "must be below the maximum"),
        ):
            boundary = schema.get(keyword)
            if boundary is not None:
                if isinstance(boundary, bool) or not isinstance(boundary, (int, float)):
                    raise _UnsupportedSchema(f"{path} has a malformed {keyword}")
                if compare(value, boundary):
                    raise _schema_error(path, phrase)


def validate_schema(value: object, schema: dict | bool) -> None:
    """Validate a decoded JSON value against CCC's supported schema subset.

    Errors identify the failing shape or field without including supplied values.
    Unsupported constraints fail closed instead of being silently ignored.
    """

    if not isinstance(schema, (dict, bool)):
        raise ValueError("Schema must be an object or boolean")
    root = schema if isinstance(schema, dict) else {}
    try:
        _check_json_structure(value)
        _validate_schema(value, schema, root, "$", 0, set())
    except _UnsupportedSchema as exc:
        raise ValueError(str(exc)) from None


def _safe_descriptor_label(descriptor: dict) -> str:
    title = descriptor.get("title")
    if not isinstance(title, str):
        return "Codex operation"
    return re.sub(r"[^A-Za-z0-9 ._-]", "", title)[:100] or "Codex operation"


def validate_codex_operation(
    method: str,
    params: dict,
    catalog: dict | None = None,
) -> dict:
    """Return a permitted method descriptor after validating its parameters."""

    if catalog is None:
        catalog = get_codex_catalog()
    methods = catalog.get("methods", []) if isinstance(catalog, dict) else []
    descriptor = next(
        (
            row
            for row in methods
            if isinstance(row, dict) and row.get("method") == method
        ),
        None,
    )
    if descriptor is None:
        raise ValueError("Unknown Codex method")
    if not descriptor.get("available"):
        reason = descriptor.get("unavailable_reason")
        safe_reason = (
            re.sub(r"[^A-Za-z0-9 ._=-]", "", reason)[:240]
            if isinstance(reason, str)
            else "Codex method is unavailable"
        )
        raise ValueError(safe_reason)
    if not isinstance(params, dict):
        raise ValueError("Codex operation parameters must be an object")
    schema = descriptor.get("params_schema")
    try:
        validate_schema(params, schema)
    except ValueError as exc:
        label = _safe_descriptor_label(descriptor)
        raise ValueError(f"Invalid parameters for {label}: {exc}") from None
    return descriptor


def _read_json_file(target_path: Path, max_bytes: int = 16 * 1024 * 1024) -> dict:
    size = target_path.stat().st_size
    if size < 2 or size > max_bytes:
        raise ValueError("Generated Codex schema has an unexpected size")
    with target_path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Generated Codex schema is not an object")
    return value


def _run_schema_command(argv: list[str], timeout: float) -> None:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex schema generation timed out") from exc
    except OSError as exc:
        raise RuntimeError("Codex schema generation could not start") from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Codex schema generation failed with exit code {result.returncode}"
        )


def _generate_schema_maps(executable_path: str) -> tuple[dict, dict, str]:
    try:
        version_result = subprocess.run(
            [executable_path, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5.0,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex version check timed out") from exc
    except OSError as exc:
        raise RuntimeError("Codex version check could not start") from exc
    if version_result.returncode != 0:
        raise RuntimeError(
            f"Codex version check failed with exit code {version_result.returncode}"
        )
    raw_version = version_result.stdout[:240].decode("utf-8", errors="replace").strip()
    version = re.sub(r"[^A-Za-z0-9 ._+-]", "", raw_version)[:160]

    names = ("ClientRequest", "ClientNotification", "ServerRequest", "ServerNotification")
    with tempfile.TemporaryDirectory(prefix="ccc-codex-schema-") as scratch:
        scratch_path = Path(scratch)
        stable_dir = scratch_path / "stable"
        full_dir = scratch_path / "full"
        stable_dir.mkdir(mode=0o700)
        full_dir.mkdir(mode=0o700)
        _run_schema_command(
            [
                executable_path,
                "app-server",
                "generate-json-schema",
                "--out",
                str(stable_dir),
            ],
            20.0,
        )
        _run_schema_command(
            [
                executable_path,
                "app-server",
                "generate-json-schema",
                "--experimental",
                "--out",
                str(full_dir),
            ],
            20.0,
        )
        stable = {name: _read_json_file(stable_dir / f"{name}.json") for name in names}
        full = {name: _read_json_file(full_dir / f"{name}.json") for name in names}
    return stable, full, version


_SCHEMA_LOADER = _generate_schema_maps


def _executable_signature(executable_path: str) -> dict:
    resolved_path = os.path.realpath(executable_path)
    executable_stat = os.stat(resolved_path)
    return {
        "path": resolved_path,
        "device": executable_stat.st_dev,
        "inode": executable_stat.st_ino,
        "size": executable_stat.st_size,
        "mtime_ns": executable_stat.st_mtime_ns,
    }


def _cache_digest(signature: dict) -> str:
    value = json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _versioned_cache_key(signature: dict, version: str) -> str:
    value = json.dumps(
        {"signature": signature, "version": version},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _read_cached_catalog(cache_file: Path, signature: dict) -> dict | None:
    try:
        envelope = _read_json_file(cache_file)
        catalog = envelope.get("catalog")
        if (
            envelope.get("cache_version") != _CACHE_VERSION
            or envelope.get("signature") != signature
            or not isinstance(catalog, dict)
            or catalog.get("ok") is not True
            or not isinstance(catalog.get("version"), str)
            or envelope.get("cache_key")
            != _versioned_cache_key(signature, catalog["version"])
        ):
            return None
        if not all(
            isinstance(catalog.get(key), list)
            for key in ("methods", "server_requests", "notifications")
        ):
            return None
        return catalog
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _write_cached_catalog(cache_file: Path, signature: dict, catalog: dict) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(cache_file.parent, 0o700)
    envelope = {
        "cache_version": _CACHE_VERSION,
        "signature": signature,
        "cache_key": _versioned_cache_key(signature, catalog.get("version", "")),
        "catalog": catalog,
    }
    descriptor, temp_name = tempfile.mkstemp(
        prefix=".codex-capabilities-", suffix=".tmp", dir=str(cache_file.parent)
    )
    try:
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            json.dump(envelope, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, cache_file)
        os.chmod(cache_file, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _load_or_build_catalog(
    resolved: dict,
    loader=None,
    cache_dir: Path | None = None,
) -> dict:
    executable_path = resolved.get("bin") if isinstance(resolved, dict) else None
    if not isinstance(executable_path, str) or not executable_path:
        raise RuntimeError("Codex executable is unavailable")
    signature = _executable_signature(executable_path)
    target_dir = Path(cache_dir) if cache_dir is not None else _CACHE_DIR
    digest = _cache_digest(signature)
    memory_key = f"{target_dir}:{digest}"
    cache_file = target_dir / f"catalog-{digest}.json"
    selected_loader = loader or _SCHEMA_LOADER

    with _CATALOG_LOCK:
        memory_hit = _CATALOG_MEMORY.get(memory_key)
        if memory_hit is not None:
            return memory_hit
        disk_hit = _read_cached_catalog(cache_file, signature)
        if disk_hit is not None:
            _CATALOG_MEMORY[memory_key] = disk_hit
            return disk_hit
        stable, full, version = selected_loader(executable_path)
        catalog = catalog_from_schemas(stable, full, version)
        try:
            _write_cached_catalog(cache_file, signature, catalog)
        except OSError:
            pass
        _CATALOG_MEMORY[memory_key] = catalog
        return catalog


def _catalog_with_runtime_availability(catalog: dict, experimental: bool) -> dict:
    result = dict(catalog)
    for key in ("methods", "server_requests", "notifications"):
        records = []
        for descriptor in catalog.get(key, []):
            record = dict(descriptor)
            if experimental and "_experimental_params_schema" in record:
                record["params_schema"] = record["_experimental_params_schema"]
                record["params_type"] = record.get(
                    "_experimental_params_type", record.get("params_type", "union")
                )
            method = record.get("method", "")
            reason = _unavailable_reason(method, record.get("direction", ""), bool(record.get("experimental")) and not experimental)
            record["available"] = reason is None
            record["unavailable_reason"] = reason
            record["title"] = _title_for_method(method)
            record["group"] = _group_for_method(method)
            record["internal"] = method in _INTERNAL_METHODS or method.startswith("mock/")
            if method in _ACTION_NOTES:
                record["description"] = _ACTION_NOTES[method]
            record.pop("_experimental_params_schema", None)
            record.pop("_experimental_params_type", None)
            records.append(record)
        result[key] = records
    return result


def _bounded_error(value: object) -> str:
    # Resolver errors can contain a literal environment override or a path.
    # Neither belongs in a public response, even when truncated.
    return "Unable to load Codex capabilities. Check the Codex executable and cache permissions."


def get_codex_catalog() -> dict:
    """Discover, cache, and return the current Codex app-server capabilities."""

    try:
        resolved = _core._resolve_codex_bin()
    except Exception as exc:
        return {
            "ok": False,
            "version": "",
            "fingerprint": "",
            "methods": [],
            "server_requests": [],
            "notifications": [],
            "error": _bounded_error(exc),
        }
    if not isinstance(resolved, dict) or not resolved.get("available") or not resolved.get("bin"):
        reason = resolved.get("reason") if isinstance(resolved, dict) else None
        return {
            "ok": False,
            "version": "",
            "fingerprint": "",
            "methods": [],
            "server_requests": [],
            "notifications": [],
            "error": _bounded_error(reason or "Codex executable is unavailable"),
        }
    try:
        base_catalog = _load_or_build_catalog(resolved)
    except Exception as exc:
        return {
            "ok": False,
            "version": "",
            "fingerprint": "",
            "methods": [],
            "server_requests": [],
            "notifications": [],
            "error": _bounded_error(exc),
        }
    experimental = os.environ.get("CCC_CODEX_EXPERIMENTAL") == "1"
    return {**_catalog_with_runtime_availability(base_catalog, experimental), "server_platform": _HOST_PLATFORM}
