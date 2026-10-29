"""Per-queue role models for the Queue manager (CCC-1235, WatchTower WT-14).

WatchTower owns the rule: ``watchtower.roles.effective_role_model`` decides
which engine/model runs each role, and ``watchtower.config.set_role`` is the
same validated setter ``wt config -q Q --<role>-engine/--<role>-model`` uses.
This module only shapes that for the dashboard: the effective value + its
source per role, and picker choices from the exact catalog ``set_role``
validates against, so the UI cannot offer a model WatchTower would refuse.

In-process, no subprocess: the server already imports ``watchtower.config``.
WatchTower is imported lazily so an older install (no ``roles`` module) just
reports ``available: False`` instead of breaking server import.
"""
from __future__ import annotations

ROLE_LABELS = {
    "planner": "Planner",
    "plan_reviewer": "Plan reviewer",
    "builder": "Builder",
    "verifier": "Verifier",
}

ROLE_HINTS = {
    "planner": "Writes the plan. Default: the builder's engine at its strongest model.",
    "plan_reviewer": "Reviews the plan. A different model family is recommended.",
    "builder": "Implements the ticket. Set with Engine and Model above.",
    "verifier": "Checks the finished work. A different family than the builder is recommended.",
}


def _wt():
    try:
        from watchtower import config, models, roles
    except Exception:  # ImportError, or a broken checkout mid-merge
        return None
    if not hasattr(config, "set_role") or not hasattr(roles, "role_table"):
        return None
    return config, models, roles


def _match_queue(config, queue: str) -> str:
    want = str(queue or "").strip().upper()
    for key in config._load():
        if str(key).strip().upper() == want:
            return key
    return ""


def _engine_choices(config, models) -> list:
    """Engines a role can run on, each with its approved model ids.

    Only engines with a readable catalog are offered: with no catalog
    ``is_approved_model`` accepts anything, which is exactly the "UI offers
    a model wt refuses later" class of bug this panel must not reintroduce.
    """
    try:
        from watchtower import workers
        available = workers.engine_available
    except Exception:
        available = None
    out = []
    for eng in models.ENGINES:
        if available is not None and not available(eng):
            continue
        canonical = [m for m in (models.catalog(eng) or ()) if not config.is_blocked_model(m)]
        if canonical:
            out.append({"engine": eng, "models": canonical})
    return out


def role_state(queue: str) -> dict:
    wt = _wt()
    if wt is None:
        return {"ok": True, "available": False,
                "error": "This WatchTower install has no per-role models; update WatchTower."}
    config, models, roles = wt
    key = _match_queue(config, queue)
    if not key:
        return {"ok": False, "error": f"unknown queue {queue!r}"}
    table = roles.role_table(key)
    rows = []
    for role in roles.ROLES:
        eff = table.get(role) or {}
        o_eng, o_model = ("", "") if role == "builder" else config.role_override(key, role)
        rows.append({
            "role": role,
            "label": ROLE_LABELS.get(role, role),
            "hint": ROLE_HINTS.get(role, ""),
            "editable": role in config.ROLE_KEYS,
            "engine": eff.get("engine", ""),
            "model": eff.get("model", ""),
            "source": eff.get("source", ""),
            "override_engine": o_eng,
            "override_model": o_model,
        })
    return {"ok": True, "available": True, "queue": key, "roles": rows,
            "builder_engine": (table.get("builder") or {}).get("engine", ""),
            "engines": _engine_choices(config, models)}


def set_roles(queue: str, updates) -> dict:
    """Apply ``{role: {"engine": str, "model": str}}``; "" clears a field.

    Each role is written through ``config.set_role`` on its own, so one bad
    choice is reported against its row without undoing the others.
    """
    wt = _wt()
    if wt is None:
        return {"ok": False, "error": "This WatchTower install has no per-role models; update WatchTower."}
    config, _models, _roles = wt
    if not isinstance(updates, dict):
        raise ValueError("roles must be an object of {role: {engine, model}}")
    key = _match_queue(config, queue)
    if not key:
        return {"ok": False, "error": f"unknown queue {queue!r}"}
    errors = {}
    for role, value in updates.items():
        if role not in config.ROLE_KEYS:
            errors[str(role)] = f"unknown role; expected one of {', '.join(config.ROLE_KEYS)}"
            continue
        value = value if isinstance(value, dict) else {}
        eng = str(value.get("engine") or "").strip().lower()
        mdl = str(value.get("model") or "").strip()
        if any(c in eng + mdl for c in "\r\n"):
            errors[role] = "engine and model must be one line"
            continue
        try:
            config.set_role(key, role, eng, mdl)
        except ValueError as e:
            errors[role] = str(e)
    state = role_state(key)
    state["ok"] = not errors
    state["errors"] = errors
    if errors:
        state["error"] = "; ".join(f"{ROLE_LABELS.get(r, r)}: {m}" for r, m in errors.items())
    return state
