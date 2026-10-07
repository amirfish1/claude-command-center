# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Fleet-level limit view: one banner for a whole limit event, not N cards.

When a usage limit hits, every session parked on that account's wall shows
up together. The watcher in ccc_server/usage_limit.py (plus the Devin pass
in ccc_server/free_failover.py) already detects stops across engines,
weekly and 5h windows alike, and records them in the shared resume store —
headless lanes and queue workers land there exactly like foreground
sessions, because detection tails transcripts, not dashboards.

This module only re-shapes the per-session data free_failover_status()
already computes (two cached JSON stores + a TTL'd readiness probe, zero
transcript work) into per-engine groups, and fans one approved click out
to the existing per-session actions — the per-minute auto-resume stagger
in free_failover._free_failover_auto_pass still applies, so "resume all"
can never re-hit the limit as a burst.

Names still living in server.py are reached via ``_core`` at call time.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time

from ccc_server import core as _core


_ENGINE_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
    "devin": "Devin",
    "kimi": "Kimi",
}

# A fleet POST is one user click; the cap keeps a hand-edited payload from
# turning into a queue of resume attempts.
FLEET_ACTION_MAX_SESSIONS = 128

# continue / switch_back do real process work (retire + resume); bounding
# the fan-out keeps a 20-session click from queueing behind one slow lane.
_FLEET_ACTION_WORKERS = 4


def _fleet_group_key(item):
    """Which account limit a stopped session belongs to.

    Limits are per engine account — every Claude session shares one wall,
    every Codex account its own — so the engine IS the group."""
    return str(item.get("engine") or "unknown").lower() or "unknown"


def free_failover_fleet():
    """Group the live limit/failover state into one banner per limit wall.

    Membership mirrors what the old per-session cards surfaced, so when
    this endpoint is up the fleet banner owns the whole lifecycle and the
    per-session module only exists as a fallback for older servers:

      limited              stopped, offer visible     (checkbox row)
      armed                approved resume-at-reset   (badge row)
      free                 running on a $0 model      (badge row +
                                                       switch-back buttons)
      switch_back_pending  free process winding down   (badge row)

    Cheap by construction: free_failover_status() reads only the two
    cached JSON stores and the TTL'd free-ready probe — no transcript
    scan, no archive build, no subprocess, no per-session file I/O."""
    status = _core.free_failover_status()
    now = status.get("now") or time.time()
    free_ready = bool(status.get("free_ready"))

    groups = {}
    for sid, item in (status.get("sessions") or {}).items():
        if not isinstance(item, dict):
            continue
        state = item.get("state")
        if state == "limited":
            if item.get("offer_dismissed"):
                continue
            member_state = "armed" if (
                item.get("auto_resume_armed")
                and not item.get("auto_resume_done")
            ) else "limited"
        elif state in ("free", "switch_back_pending"):
            member_state = state
        else:
            continue  # resolved / unknown — nothing for a banner to offer

        key = _fleet_group_key(item)
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "key": key,
                "engine": item.get("engine") or key,
                "engine_label": _ENGINE_LABELS.get(key, "This engine"),
                "limited_count": 0,
                "armed_count": 0,
                "free_count": 0,
                "switch_back_count": 0,
                "resume_at": None,
                "resume_at_estimated": False,
                "can_continue_free": False,
                "can_auto_resume": False,
                "sessions": [],
            }

        resume_at = item.get("resume_at")
        member = {
            "session_id": sid,
            "display_name": item.get("display_name"),
            "engine": item.get("engine"),
            "state": member_state,
            "detected_at": item.get("detected_at"),
            "resume_at": resume_at,
            "resume_at_estimated": bool(item.get("resume_at_estimated")),
            "auto_resume_fire_at": item.get("auto_resume_fire_at"),
            "supports_continue_free": bool(item.get("supports_continue_free")),
            "supports_auto_resume": bool(item.get("supports_auto_resume")),
            "free_model": item.get("free_model"),
            "free_model_uid": item.get("free_model_uid"),
            "free_since": item.get("free_since"),
            "switch_back_offered": bool(item.get("switch_back_offered")),
            "pending_reason": item.get("pending_reason"),
        }
        group["sessions"].append(member)
        if member_state == "limited":
            group["limited_count"] += 1
            if isinstance(resume_at, (int, float)) and (
                group["resume_at"] is None or resume_at < group["resume_at"]
            ):
                group["resume_at"] = resume_at
                group["resume_at_estimated"] = bool(
                    item.get("resume_at_estimated"))
        elif member_state == "armed":
            group["armed_count"] += 1
        elif member_state == "free":
            group["free_count"] += 1
            if member["switch_back_offered"]:
                group["switch_back_count"] += 1
        elif member_state == "switch_back_pending":
            group["free_count"] += 1

    for group in groups.values():
        sessions = group["sessions"]
        sessions.sort(
            key=lambda m: (
                {"limited": 0, "armed": 1,
                 "switch_back_pending": 2, "free": 3}.get(m["state"], 4),
                str(m.get("display_name") or m.get("session_id") or ""),
            )
        )
        limited = [m for m in sessions if m["state"] == "limited"]
        engine = group["key"]
        # Same gate the per-session card uses: claude needs a ready free
        # router; devin picks from its own free catalog tier.
        group["can_continue_free"] = bool(limited) and all(
            m["supports_continue_free"] for m in limited
        ) and (engine == "devin" or free_ready)
        group["can_auto_resume"] = bool(limited) and all(
            m["supports_auto_resume"] for m in limited
        )

    ordered = sorted(
        groups.values(),
        key=lambda g: (
            -(g["limited_count"] + g["armed_count"]),
            g["resume_at"] if isinstance(g["resume_at"], (int, float)) else 9e18,
        ),
    )
    return {
        "ok": True,
        "now": now,
        "free_ready": free_ready,
        "free_model": status.get("free_model"),
        "free_via": status.get("free_via"),
        "auto_resume_max_per_minute": status.get(
            "auto_resume_max_per_minute"),
        "groups": ordered,
    }


def free_failover_fleet_action(action, session_ids, offer="failover",
                               always=False):
    """Apply one approved click to every selected session.

    Pure fan-out over the per-session primitives in free_failover.py —
    their validation (engine support, state checks, the flock'd stores)
    stays the source of truth, and each session's outcome is reported
    individually so a partial failure is visible, never silent."""
    action = str(action or "").strip().lower()
    if action not in ("continue", "arm", "disarm", "dismiss", "switch_back"):
        return {"ok": False, "error": "unknown action"}

    if not isinstance(session_ids, (list, tuple)):
        session_ids = [session_ids] if session_ids else []
    seen = set()
    sids = []
    for raw in session_ids[:FLEET_ACTION_MAX_SESSIONS]:
        sid = str(raw or "").strip()
        if sid and sid not in seen:
            seen.add(sid)
            sids.append(sid)
    if not sids:
        return {"ok": False, "error": "missing session_ids"}

    def run(sid):
        try:
            if action == "continue":
                return _core.free_failover_continue(sid, always=always)
            if action == "arm":
                return _core.free_failover_arm(sid, armed=True)
            if action == "disarm":
                return _core.free_failover_arm(sid, armed=False)
            if action == "switch_back":
                return _core.free_failover_switch_back(sid)
            return _core.free_failover_dismiss(sid, offer=offer)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    heavy = action in ("continue", "switch_back")
    results = {}
    if heavy and len(sids) > 1:
        with ThreadPoolExecutor(max_workers=_FLEET_ACTION_WORKERS) as pool:
            for sid, res in zip(sids, pool.map(run, sids)):
                results[sid] = res
    else:
        for sid in sids:
            results[sid] = run(sid)

    succeeded = sum(1 for r in results.values()
                    if isinstance(r, dict) and r.get("ok"))
    failed = len(sids) - succeeded
    try:
        _core._log_activity(
            "free-failover", f"FLEET_{action.upper()}",
            f"requested={len(sids)} ok={succeeded} failed={failed}",
        )
    except Exception:
        pass
    return {
        "ok": failed == 0,
        "action": action,
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }
