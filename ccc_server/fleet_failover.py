# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import math

from ccc_server import core as _core

FLEET_ACTION_MAX_SESSIONS = 128
_FLEET_ACTION_WORKERS = 4
_FLEET_ENGINE_LABELS = {"claude": "Claude", "codex": "Codex", "devin": "Devin", "kimi": "Kimi"}


def free_failover_fleet():
    status = _core.free_failover_status()
    groups = {}
    for sid, item in (status.get("sessions") or {}).items():
        if not isinstance(item, dict):
            continue
        state = item.get("state")
        if state == "limited":
            if item.get("offer_dismissed"):
                continue
            state = "armed" if item.get("auto_resume_armed") and not item.get("auto_resume_done") else state
        elif state not in ("free", "switch_back_pending"):
            continue
        engine = item.get("engine") or "unknown"
        group = groups.setdefault(engine, {
            "key": engine, "engine": engine,
            "engine_label": _FLEET_ENGINE_LABELS.get(engine, "This engine"),
            "limited_count": 0, "armed_count": 0, "free_count": 0,
            "switch_back_count": 0, "resume_at": None,
            "resume_at_estimated": False, "sessions": [],
        })
        member = {key: item.get(key) for key in (
            "display_name", "engine", "detected_at", "resume_at", "resume_at_estimated",
            "auto_resume_fire_at", "supports_continue_free", "supports_auto_resume",
            "free_model", "free_model_uid", "free_since", "switch_back_offered",
            "pending_reason", "limit_window",
        )}
        member.update(session_id=sid, state=state)
        group["sessions"].append(member)
        count_key = {"limited": "limited_count", "armed": "armed_count"}.get(state, "free_count")
        group[count_key] += 1
        if state == "free" and member["switch_back_offered"]:
            group["switch_back_count"] += 1
        reset = item.get("resume_at")
        if state in ("limited", "armed") and isinstance(reset, (int, float)) and math.isfinite(reset):
            if group["resume_at"] is None or reset < group["resume_at"]:
                group.update(resume_at=reset, resume_at_estimated=bool(item.get("resume_at_estimated")))
    for group in groups.values():
        group["sessions"].sort(key=lambda m: (
            {"limited": 0, "armed": 1, "switch_back_pending": 2, "free": 3}[m["state"]],
            str(m.get("display_name") or m["session_id"]),
        ))
        limited = [m for m in group["sessions"] if m["state"] == "limited"]
        group["can_continue_free"] = bool(limited) and all(m["supports_continue_free"] for m in limited) and (
            group["engine"] == "devin" or bool(status.get("free_ready")))
        group["can_auto_resume"] = bool(limited) and all(m["supports_auto_resume"] for m in limited)
    return {key: status.get(key) for key in (
        "ok", "now", "free_ready", "free_model", "free_via", "auto_resume_max_per_minute",
    )} | {"groups": sorted(groups.values(), key=lambda g: (-(g["limited_count"] + g["armed_count"]), g["key"]))}


def _fleet_apply_one(action, sid, offer, always):
    lock_dir = _core.COMMAND_CENTER_STATE_DIR / "fleet-action-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / (hashlib.sha256(sid.encode()).hexdigest() + ".lock")).open("a+") as lock_fh:
        try:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"ok": False, "code": "busy", "error": "A change is already running for this session."}
        try:
            entry = (_core._load_usage_limit_resumes() or {}).get(sid) or {}
            rec = (_core._load_free_failovers() or {}).get(sid) or {}
            unresolved = bool(entry and not entry.get("fired") and not entry.get("dismissed"))
            armed = bool(rec.get("auto_resume") and rec.get("auto_resume_detected_at") == entry.get("detected_at"))
            free = rec.get("state") in ("free", "switch_back_pending")
            eligible = {
                "continue": unresolved and not free,
                "arm": unresolved and not free,
                "disarm": unresolved and armed,
                "switch_back": free,
                "dismiss": free if offer == "switch_back" else unresolved,
            }[action]
            if not eligible:
                return {"ok": False, "code": "no_limit_stop", "error": "This limit stop has already changed. Check the session list."}
            if action == "continue":
                return _core.free_failover_continue(sid, always=always)
            if action in ("arm", "disarm"):
                return _core.free_failover_arm(sid, armed=action == "arm")
            if action == "switch_back":
                return _core.free_failover_switch_back(sid)
            if armed and offer != "switch_back":
                result = _core.free_failover_arm(sid, armed=False)
                if not result.get("ok"):
                    return result
            return _core.free_failover_dismiss(sid, offer=offer)
        finally:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def free_failover_fleet_action(action, session_ids, offer="failover", always=False):
    if action not in ("continue", "arm", "disarm", "dismiss", "switch_back"):
        return {"ok": False, "error": "Choose a supported session action."}
    if not isinstance(session_ids, list) or not session_ids:
        return {"ok": False, "error": "Choose at least one session."}
    if len(session_ids) > FLEET_ACTION_MAX_SESSIONS:
        return {"ok": False, "error": "Choose no more than 128 sessions at a time."}
    if any(not isinstance(sid, str) or not sid.strip() for sid in session_ids):
        return {"ok": False, "error": "Each session must have a session ID."}
    if offer not in ("failover", "switch_back"):
        return {"ok": False, "error": "Choose a supported offer."}
    sids = list(dict.fromkeys(_core._normalize_sid(sid) for sid in session_ids))

    def run(sid):
        try:
            result = _fleet_apply_one(action, sid, offer, bool(always))
            if not isinstance(result, dict):
                result = {"ok": False, "error": "Could not change this session. Try again."}
        except Exception:
            result = {"ok": False, "code": "action_failed", "error": "Could not change this session. Try again."}
        return dict(result, session_id=sid)

    if action in ("continue", "switch_back") and len(sids) > 1:
        with ThreadPoolExecutor(max_workers=_FLEET_ACTION_WORKERS) as pool:
            results = dict(zip(sids, pool.map(run, sids)))
    else:
        results = {sid: run(sid) for sid in sids}
    succeeded = sum(bool(r.get("ok")) for r in results.values())
    return {"ok": succeeded == len(sids), "action": action,
            "succeeded": succeeded, "failed": len(sids) - succeeded, "results": results}
