"""WatchTower ticket stage pipeline for CCC (CCC-1236).

WatchTower now runs a ticket through up to six stages: plan (WT-11),
plan review, build, checks (``cmd:`` gates), verify (WT-6) or a human review
(WT-5), then closed. CCC used to collapse everything after a worker's close
into one ``in_review`` status. This module turns what WatchTower already
recorded on the ticket (its effective gates, ``item["plan"]``,
``gate_pending`` / ``gate_results`` / ``verifier`` and the history events)
into one display model the queue views render as a stage strip:

    {"stages": [{"key", "label", "state", "role", "engine", "model",
                 "since", "detail"}...],
     "current": "<key>", "since": iso, "loops": [...], "waiting": [...],
     "floor_routed": "<model>"}

``state`` is one of done / current / failed / skipped / pending. Nothing here
re-decides a WatchTower rule: gate order, the verifier and the role models all
come from WatchTower (``effective_gates``, ``roles.effective_role_model``).

PERFORMANCE (CLAUDE.md "Performance gates"): pure dict work per row, no
subprocess, no file read per row. Queue gates and role tables are read once
per queue and cached for ``_ROLE_TTL_S``.
"""

from __future__ import annotations

import threading
import time


STAGE_LABELS = {
    "plan": "Plan",
    "plan_review": "Plan review",
    "build": "Build",
    "checks": "Checks",
    "verify": "Verify",
    "review": "Review",
    "closed": "Closed",
}
# Which WatchTower role (WT-14) runs each stage.
STAGE_ROLES = {
    "plan": "planner",
    "plan_review": "plan_reviewer",
    "build": "builder",
    "verify": "verifier",
}

_ROLE_TTL_S = 60.0
_cache_lock = threading.Lock()
_role_cache = {}   # queue -> (ts, {role: {engine, model, source}})
_gates_cache = {}  # queue -> (ts, [gates])


def _now():
    return time.time()


def _queue_gates(queue):
    with _cache_lock:
        ent = _gates_cache.get(queue)
        if ent and _now() - ent[0] < _ROLE_TTL_S:
            return ent[1]
    gates = []
    try:
        from watchtower import config as _wt_config
        gates = list(_wt_config.gates(queue) or [])
    except Exception:
        gates = []
    with _cache_lock:
        _gates_cache[queue] = (_now(), gates)
    return gates


def role_table(queue):
    """WatchTower's four role models for ``queue`` (cached; {} on error)."""
    with _cache_lock:
        ent = _role_cache.get(queue)
        if ent and _now() - ent[0] < _ROLE_TTL_S:
            return ent[1]
    table = {}
    try:
        from watchtower import roles as _wt_roles
        table = _wt_roles.role_table(queue) or {}
    except Exception:
        table = {}
    with _cache_lock:
        _role_cache[queue] = (_now(), table)
    return table


def ticket_gates(item):
    """The ticket's own ``gates`` override, else the queue's default."""
    if item.get("effective_gates") is not None:
        return list(item.get("effective_gates") or [])
    if item.get("gates") is not None:
        return list(item.get("gates") or [])
    return _queue_gates(str(item.get("project") or ""))


def _history(item):
    h = item.get("history")
    return [e for e in h if isinstance(e, dict)] if isinstance(h, list) else []


def _last(events, pred):
    for ev in reversed(events):
        if pred(ev):
            return ev
    return None


def _reason(ev):
    return str(ev.get("reason") or "")


def _is_check_fail(ev):
    return ev.get("event") == "reopen" and _reason(ev).startswith("gate cmd:")


def _is_verify_fail(ev):
    return ev.get("event") == "reopen" and _reason(ev).startswith("independent verification failed")


def _is_review_reject(ev):
    return ev.get("event") == "reopen" and _reason(ev).startswith("rejected by ")


def _plural(n):
    return f"{n}×"


def _role_info(item, stage, roles):
    """(engine, model) running ``stage``: the recorded spawn first (what
    actually ran), else the role WatchTower would resolve now."""
    plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
    rec = {}
    if stage == "plan":
        rec = plan.get("planner") or {}
    elif stage == "plan_review":
        rec = plan.get("reviewer") or {}
    elif stage == "verify":
        rec = item.get("verifier") or {}
    if isinstance(rec, dict) and (rec.get("engine") or rec.get("model")):
        return str(rec.get("engine") or ""), str(rec.get("model") or "")
    role = STAGE_ROLES.get(stage)
    if stage == "build" and item.get("model_floor"):
        return "", str(item.get("model_floor"))
    r = roles.get(role) if role else None
    if isinstance(r, dict):
        return str(r.get("engine") or ""), str(r.get("model") or "")
    return "", ""


def stage_pipeline(item, roles=None, gates=None):
    """Display model for one ticket's stages, or None for an ungated ticket
    with no plan state (the old status dot already says everything)."""
    if not isinstance(item, dict):
        return None
    gates = ticket_gates(item) if gates is None else list(gates or [])
    plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
    has_plan = bool(plan) or any(g == "plan" or str(g).startswith("plan:") for g in gates)
    has_checks = any(str(g).startswith("cmd:") for g in gates)
    has_verify = "verify" in gates
    review_gate = next((g for g in gates if g == "review" or str(g).startswith("review:")), "")
    if not (has_plan or has_checks or has_verify or review_gate):
        return None
    hist = _history(item)
    status = str(item.get("status") or "")
    if status == "closed" and not plan and not item.get("gate_results") and not any(
            e.get("event") in ("in_review", "verify") for e in hist):
        # Closed before these gates existed (queue gates are read live).
        return None
    roles = role_table(str(item.get("project") or "")) if roles is None else (roles or {})

    keys = []
    if has_plan:
        keys += ["plan", "plan_review"]
    keys.append("build")
    if has_checks:
        keys.append("checks")
    if has_verify:
        keys.append("verify")
    if review_gate:
        keys.append("review")
    keys.append("closed")

    plan_status = str(plan.get("status") or "")
    pending = str(item.get("gate_pending") or "")
    if status == "in_review" and not pending:
        pending = "review"

    # Where the ticket is now, and the event that put it there.
    current, since_ev = "build", None
    if status == "closed":
        current = "closed"
    elif status == "in_review":
        current = "verify" if pending == "verify" else "review"
        since_ev = _last(hist, lambda e: e.get("event") in ("in_review", "verify"))
    elif has_plan and plan_status in ("", "planning") and status != "closed":
        current = "plan"
        since_ev = _last(hist, lambda e: e.get("event") in ("plan_start", "plan_review"))
    elif has_plan and plan_status in ("reviewing", "blocked"):
        current = "plan_review"
        since_ev = _last(hist, lambda e: e.get("event") == "plan")
    else:
        since_ev = _last(hist, lambda e: e.get("event") in ("claim", "reopen", "plan_review", "plan_failed"))
    if current not in keys:
        current = "build"
    # Open and unclaimed with nothing started: the ticket is waiting for a
    # worker, not in a stage yet. Its next stage stays pending (not pulsing)
    # and it is left out of per-stage counts.
    queued = bool(status == "open" and not item.get("claimed_by") and (
        current == "build" or (current == "plan" and not plan_status)))
    since = (item.get("closed_at") if current == "closed" else (since_ev or {}).get("at")) \
        or item.get("claimed_at") or item.get("created_at") or ""

    # Loops, read from WatchTower's own events.
    plan_rejects = sum(1 for e in hist if e.get("event") == "plan_review" and e.get("passed") is False)
    check_fails = sum(1 for e in hist if _is_check_fail(e))
    verify_fails = sum(1 for e in hist if _is_verify_fail(e))
    review_rejects = sum(1 for e in hist if _is_review_reject(e))
    loops = []
    if plan_rejects:
        loops.append({"stage": "plan_review", "count": plan_rejects,
                      "text": f"plan rejected {_plural(plan_rejects)}"
                              + (", revised" if plan_status != "blocked" else ", still disputed")})
    if check_fails:
        loops.append({"stage": "checks", "count": check_fails,
                      "text": f"checks failed → reopened {_plural(check_fails)}"})
    if verify_fails:
        loops.append({"stage": "verify", "count": verify_fails,
                      "text": f"verify failed → reopened {_plural(verify_fails)}"})
    if review_rejects:
        loops.append({"stage": "review", "count": review_rejects,
                      "text": f"review rejected → reopened {_plural(review_rejects)}"})

    # The most recent failure since the last successful pass marks its stage
    # red while the ticket is back in build.
    last_fail = None
    if current == "build" and status != "closed":
        last_reopen = _last(hist, lambda e: e.get("event") == "reopen")
        last_close = _last(hist, lambda e: e.get("event") == "close")
        if last_reopen and (not last_close or str(last_reopen.get("at") or "") >= str(last_close.get("at") or "")):
            if _is_check_fail(last_reopen):
                last_fail = "checks"
            elif _is_verify_fail(last_reopen):
                last_fail = "verify"
            elif _is_review_reject(last_reopen):
                last_fail = "review"

    order = {k: i for i, k in enumerate(keys)}
    cur_i = order[current]
    stages = []
    for k in keys:
        i = order[k]
        state = "done" if i < cur_i else ("current" if i == cur_i and not queued else "pending")
        detail = ""
        if k == "closed" and current == "closed":
            state = "done"
        if k in ("plan", "plan_review") and plan_status == "failed":
            state = "skipped"
            detail = str(plan.get("reason") or "plan stage could not run")
        if k == "plan_review" and plan_status == "blocked":
            state = "failed"
            detail = "planner and reviewer still disagree"
        if k == last_fail:
            state = "failed"
        if k == "review" and review_gate.startswith("review:"):
            detail = review_gate[7:].strip()
        engine, model = ("", "")
        if k in STAGE_ROLES:
            engine, model = _role_info(item, k, roles)
        stages.append({
            "key": k, "label": STAGE_LABELS[k], "state": state,
            "role": STAGE_ROLES.get(k, ""), "engine": engine, "model": model,
            "detail": detail,
        })

    # Why it isn't moving.
    waiting = []
    for w in item.get("waiting_on") or []:
        if isinstance(w, dict) and w.get("ref"):
            waiting.append({"kind": "blocked_by", "ref": str(w["ref"]),
                            "text": f"waiting on {w['ref']}"})
        elif isinstance(w, str) and w:
            waiting.append({"kind": "blocked_by", "ref": w, "text": f"waiting on {w}"})
    if item.get("needs_input"):
        waiting.append({"kind": "needs_input", "text": "needs a human answer"})
    if plan_status == "failed" and plan.get("reason"):
        waiting.append({"kind": "plan_failed", "text": "plan skipped: " + str(plan.get("reason"))[:200]})
    for s in stages:
        if s["state"] == "current" and s["role"]:
            r = roles.get(s["role"]) if isinstance(roles, dict) else None
            if not s["model"] and not (isinstance(r, dict) and r.get("engine")):
                waiting.append({"kind": "no_role_model",
                                "text": f"no {s['role'].replace('_', ' ')} model resolved"})

    floor = str(item.get("model_floor") or "")
    builder = roles.get("builder") if isinstance(roles, dict) else None
    floor_routed = ""
    if floor and not (isinstance(builder, dict) and builder.get("model") == floor):
        floor_routed = floor

    return {
        "stages": stages,
        "current": current,
        "queued": queued,
        "since": since,
        "loops": loops,
        "waiting": waiting,
        "floor_routed": floor_routed,
    }


def with_stages(items):
    """Attach ``stage_pipeline`` as ``stages`` to every gated/planned ticket.
    Returns new dicts for those rows; others pass through unchanged."""
    if not items:
        return items
    out = []
    changed = False
    for it in items:
        if isinstance(it, dict):
            try:
                sp = stage_pipeline(it)
            except Exception:
                sp = None
            if sp is not None:
                it = dict(it, stages=sp)
                changed = True
        out.append(it)
    return out if changed else items


def stage_counts(items):
    """``{queue: {stage: n}}`` over not-closed tickets that carry ``stages``."""
    counts = {}
    for it in items or []:
        if not isinstance(it, dict) or it.get("status") == "closed":
            continue
        sp = it.get("stages")
        if not isinstance(sp, dict):
            continue
        q = str(it.get("project") or "").strip().upper()
        cur = sp.get("current")
        if q and cur and cur != "closed" and not sp.get("queued"):
            counts.setdefault(q, {})
            counts[q][cur] = counts[q].get(cur, 0) + 1
    return counts


def stage_stuck_seconds():
    """A ticket in one stage longer than this is 'stuck at stage': the same
    no-progress threshold WatchTower's health uses for a stuck queue."""
    try:
        from watchtower import health as _wt_health
        return int(getattr(_wt_health, "STUCK_MINUTES", 10)) * 60
    except Exception:
        return 600


def worker_sessions(worker_ids):
    """``{worker_id: session_id}`` for planner/reviewer/verifier links. One
    workers.json read per call; detail payloads only, never list rows."""
    ids = {str(w) for w in worker_ids if w}
    if not ids:
        return {}
    try:
        from watchtower import workers as _wt_workers
        recs = _wt_workers._load().get("workers", [])
    except Exception:
        return {}
    out = {}
    for w in recs:
        if isinstance(w, dict) and w.get("worker_id") in ids and w.get("session_id"):
            out[w["worker_id"]] = str(w["session_id"])
    return out


def detail_stage_sessions(item):
    """Session ids for the stage roles on one ticket (detail view)."""
    plan = item.get("plan") if isinstance(item.get("plan"), dict) else {}
    roles = {
        "plan": (plan.get("planner") or {}).get("worker_id") if isinstance(plan.get("planner"), dict) else "",
        "plan_review": (plan.get("reviewer") or {}).get("worker_id") if isinstance(plan.get("reviewer"), dict) else "",
        "verify": (item.get("verifier") or {}).get("worker_id") if isinstance(item.get("verifier"), dict) else "",
    }
    sids = worker_sessions(roles.values())
    out = {k: sids.get(v, "") for k, v in roles.items() if v}
    if item.get("claimed_session_id"):
        out["build"] = str(item["claimed_session_id"])
    return {k: v for k, v in out.items() if v}

