"""Default-off outcome workspace adapter; WatchTower remains the authority.

The dashboard exposes owner editing operations, never trusted evidence or
acceptance writes. Agent/runtime operations use WatchTower's own CLI contract.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse

from ccc_server import core as _core

_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,95}\Z")
_MUTATIONS = {"create", "update", "link", "event"}
_PATCH = {"title", "goal", "criteria", "commitment", "rank", "deadline", "state",
          "next_action", "responsibility", "description", "displaced_by", "kind"}
_CREATE = _PATCH | {"repo_path", "queue", "source_key", "authority"}
_SETTINGS = None
_SETTINGS_AT = 0.0


def _call(action, payload):
    executable = _core._wt_cli_path()
    if not executable:
        return {"ok": False, "code": "outcomes_unavailable",
                "error": "WatchTower is unavailable."}, 503
    try:
        result = subprocess.run(
            [executable, "outcome", "api", action, "--payload-json",
             json.dumps(payload, ensure_ascii=True, allow_nan=False)],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        if not isinstance(data, dict):
            raise ValueError("Invalid outcome response")
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"ok": False, "code": "outcomes_unavailable",
                "error": "The outcome service is unavailable. Existing sessions are unchanged."}, 503
    if result.returncode or not data.get("ok"):
        code = str(data.get("code") or "")
        error = str(data.get("error") or "Outcome operation failed")
        status = 409 if code in ("conflict", "revision_conflict") or "revision" in error.lower() else 400
        if "not found" in error.lower():
            status = 404
        return data, status
    return data, 200


def settings(payload=None):
    """One persisted setting in WatchTower controls both runtime and UI."""
    global _SETTINGS, _SETTINGS_AT
    if payload is not None:
        if not isinstance(payload, dict) or set(payload) != {"enabled"} or type(payload["enabled"]) is not bool:
            return {"ok": False, "error": "enabled must be a boolean"}, 400
    elif _SETTINGS is not None and time.monotonic() - _SETTINGS_AT < 2:
        return dict(_SETTINGS), 200
    data, status = _call("settings", payload or {})
    if status != 200:
        if payload is None:
            return {"ok": True, "enabled": False, "available": False,
                    "error": data.get("error", "Outcome service unavailable")}, 200
        return data, status
    # The contract accepts a nested settings record as an additive extension.
    record = data.get("settings") if isinstance(data.get("settings"), dict) else data
    if type(record.get("enabled")) is not bool:
        return {"ok": True, "enabled": False, "available": False,
                "error": "This WatchTower version has no outcome workspace setting."}, 200
    data = {**data, "enabled": record["enabled"], "available": True,
            "outcomes_workspace_enabled": record["enabled"]}
    _SETTINGS, _SETTINGS_AT = dict(data), time.monotonic()
    return data, 200


def _validate_id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("Invalid outcome identifier")


def _validate_patch(patch):
    if not isinstance(patch, dict) or not patch or set(patch) - _PATCH:
        raise ValueError("Unsupported outcome fields")
    if "state" in patch and patch["state"] not in ("proposed", "active", "parked", "stopped"):
        raise ValueError("Delivery is determined by evidence, not an editing operation")
    if "commitment" in patch and type(patch["commitment"]) is not bool:
        raise ValueError("commitment must be a boolean")
    if "rank" in patch and patch["rank"] is not None and (type(patch["rank"]) is not int or not 0 <= patch["rank"] <= 10000):
        raise ValueError("rank must be a nonnegative integer or null")
    for key, limit in (("title", 300), ("goal", 8000)):
        if key in patch and (not isinstance(patch[key], str) or not patch[key].strip() or len(patch[key]) > limit):
            raise ValueError(f"{key} must be nonempty text, up to {limit} characters")
    if "criteria" in patch:
        criteria = patch["criteria"]
        if not isinstance(criteria, list) or not 1 <= len(criteria) <= 32:
            raise ValueError("Provide between one and 32 acceptance criteria")
        for item in criteria:
            if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
                raise ValueError("Each acceptance criterion needs text")
            if set(item) - {"id", "text", "required", "check_kind", "accepts", "max_age_s"}:
                raise ValueError("Acceptance evidence cannot be set through the editor")


def operate(action, payload=None, *, resolve_repo=None):
    if action == "settings":
        return settings(payload)
    status_data, _ = settings()
    if not status_data.get("available"):
        return {"ok": False, "code": "outcomes_unavailable", "error": status_data.get("error")}, 503
    if not status_data.get("enabled"):
        return {"ok": False, "code": "outcomes_disabled", "error": "Outcome workspace is off."}, 503
    if action not in {"list", "get"} | _MUTATIONS:
        return {"ok": False, "error": "Unsupported outcome operation"}, 404
    if not isinstance(payload, dict):
        return {"ok": False, "error": "Expected a JSON object"}, 400
    payload = dict(payload)
    try:
        if action in ("get", "update", "link", "event"):
            _validate_id(payload.get("id"))
        if action == "create":
            if set(payload) - _CREATE:
                raise ValueError("Unsupported outcome fields")
            _validate_patch({k: v for k, v in payload.items() if k in _PATCH})
            if not payload.get("title") or not payload.get("goal") or not payload.get("criteria"):
                raise ValueError("Title, goal and acceptance criteria are required")
            if not payload.get("repo_path"):
                raise ValueError("repo_path is required")
        if action == "update":
            if set(payload) - {"id", "expected_revision", "patch"}:
                raise ValueError("Unsupported update fields")
            if type(payload.get("expected_revision")) is not int:
                raise ValueError("expected_revision is required")
            _validate_patch(payload.get("patch"))
        if action == "link":
            if set(payload) - {"id", "ref", "relation", "source", "expected_revision"}:
                raise ValueError("Unsupported link fields")
            if payload.get("relation", "related") not in ("serves", "related"):
                raise ValueError("Invalid link relation")
            if not isinstance(payload.get("ref"), str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*-\d+", payload["ref"]):
                raise ValueError("A ticket reference is required")
            payload = {"id": payload["id"], "kind": "ticket",
                       "target": payload["ref"],
                       "relation": payload.get("relation", "related"),
                       "provenance": {"source": "owner"}}
        if action == "event":
            # Owner can request a fresh review, not forge an executor/verifier receipt.
            if set(payload) - {"id", "event_key", "type", "reason"} or payload.get("type") != "owner_review":
                raise ValueError("Only an owner review request is accepted here")
            payload = {"id": payload["id"], "type": "manual",
                       "key": payload.get("event_key") or "",
                       "payload": {"summary": str(payload.get("reason") or "Owner requested review")}}
        if payload.get("repo_path"):
            if not resolve_repo:
                raise ValueError("Repository context is unavailable")
            payload["repo_path"] = resolve_repo(payload["repo_path"])
    except (ValueError, TypeError, OSError) as exc:
        return {"ok": False, "error": str(exc)}, 400
    return _call(action, payload)


def handle(handler, method, *, resolve_repo):
    parsed = urllib.parse.urlparse(handler.path)
    suffix = parsed.path.rstrip("/")[len("/api/outcomes"):].strip("/")
    if method == "GET":
        if suffix == "settings":
            data, status = settings()
        elif not suffix:
            query = urllib.parse.parse_qs(parsed.query)
            payload = {"repo_path": query["repo_path"][-1]} if query.get("repo_path") else {}
            data, status = operate("list", payload, resolve_repo=resolve_repo)
        else:
            data, status = operate("get", {"id": suffix}, resolve_repo=resolve_repo)
    else:
        try:
            size = int(handler.headers.get("Content-Length", "0"))
            if not 0 < size <= 128 * 1024:
                handler.send_json({"ok": False, "error": "Outcome body is missing or too large"}, 413)
                return
            def reject_constant(_value):
                raise ValueError("Nonfinite JSON number")
            payload = json.loads(handler.rfile.read(size), parse_constant=reject_constant)
        except (ValueError, OSError):
            handler.send_json({"ok": False, "error": "Invalid JSON body"}, 400)
            return
        if suffix != "settings" and suffix not in _MUTATIONS:
            handler.send_json({"ok": False, "error": "Unsupported outcome operation"}, 404)
            return
        data, status = operate(suffix, payload, resolve_repo=resolve_repo)
    handler.send_json(data, status)


def cached_enabled():
    """Cheap presentation hint after the settings API warmed the authority read.

    Never launch a subprocess from the session census or a Stop-hook callback.
    Runtime admission still checks WatchTower's persisted setting independently.
    """
    return bool(_SETTINGS and _SETTINGS.get("enabled") and _SETTINGS.get("available"))


def prepare_spawn(payload, parent_session_id=None):
    """Attach explicit provenance; lineage alone never picks a business goal."""
    explicit = str(payload.get("outcome_id") or "").strip()
    if not explicit and not cached_enabled():
        return None
    state, _ = settings()
    if not state.get("enabled"):
        if explicit:
            raise ValueError("Enable the outcome workspace before linking a spawn")
        return None
    outcome = None
    if explicit:
        _validate_id(explicit)
        data, status = _call("get", {"id": explicit})
        if status != 200:
            raise ValueError(data.get("error") or "Outcome unavailable")
        outcome = data.get("outcome")
    elif parent_session_id:
        data, status = _call("list", {})
        if status == 200:
            candidates = []
            for item in data.get("outcomes") or []:
                associations = item.get("associations") or []
                sessions = item.get("session_ids") or []
                if parent_session_id in sessions or any(
                    isinstance(a, dict) and a.get("session_id") == parent_session_id
                    for a in associations
                ):
                    if item.get("state") not in ("delivered", "stopped", "parked"):
                        candidates.append(item)
            if len(candidates) == 1:
                outcome = candidates[0]
    if not isinstance(outcome, dict):
        if explicit:
            raise ValueError("Outcome unavailable")
        return None
    if outcome.get("state") in ("delivered", "stopped", "parked"):
        raise ValueError("This outcome is not active; reopen it before starting linked work")
    return outcome


def spawn_prefix(outcome):
    if not outcome:
        return ""
    criteria = "; ".join(str(c.get("text") or "") for c in outcome.get("criteria") or [] if isinstance(c, dict))
    return ("Outcome context (association, not additional authority):\n"
            f"ID: {outcome['id']}\nGoal: {outcome.get('goal') or outcome.get('title')}\n"
            f"Finish line: {criteria}\n"
            "Keep the user outcome distinct from this attempt. Return evidence and the next owned action. "
            "Use only already authorized scope; sends, money and consequential changes retain their existing gates.\n\n")


def associate_spawn(outcome, result, parent_session_id=None):
    if not outcome or not result.get("ok"):
        return
    sid = result.get("session_id")
    result["outcome_id"] = outcome["id"]
    if not sid:
        result["outcome_association"] = "pending_identity"
        return
    data, status = _call("associate", {"id": outcome["id"], "session_id": sid,
                                      "source": "explicit_spawn", "parent_session_id": parent_session_id or ""})
    result["outcome_association"] = "recorded" if status == 200 else "unconfirmed"
    if status != 200:
        # The process already exists: never turn this into a spawn retry.
        result["outcome_association_error"] = data.get("error") or "Association could not be recorded"
