"""WatchTower takeover adapter; no supervision timers or agent delivery here.

The bridge preserves CCC's legacy row/API shapes while WatchTower owns the
durable lifecycle. Persisting ownership before adoption fences old CCC jobs;
an ambiguous CLI failure must never fall back to another supervisor.
"""

import hashlib
import json
import os
import threading
import time

_available = None
_started = False
_start_lock = threading.Lock()


def available():
    return _available is True


def _marker():
    from . import idea_tickets as tickets
    return tickets._state_path().parent / "watchtower-takeover.json"


def _save_marker(**fields):
    from . import idea_tickets as tickets
    with tickets._file_lock():
        target = _marker()
        try:
            data = json.loads(target.read_text())
        except (OSError, ValueError):
            data = {}
        data.update(fields)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(data))
        temporary.replace(target)


def _call(args):
    from . import idea_tickets as tickets
    rc, output = tickets._wt(["takeover", *args, "--json"])
    try:
        result = json.loads(output)
    except (ValueError, TypeError):
        return {"ok": False, "error": "WatchTower takeover is unavailable or returned invalid JSON"}
    if not isinstance(result, dict):
        return {"ok": False, "error": "Invalid WatchTower takeover response"}
    if rc != 0:
        return {**result, "ok": False, "error": result.get("error") or result.get("reason")
                or "WatchTower takeover failed"}
    return {**result, "ok": result.get("ok", True)}


def _mirror(ref, result, *, auto=False):
    from . import idea_tickets as tickets
    states = {"pending_delivery": "running", "running": "running",
              "waiting": "waiting", "needs_input": "blocked",
              "paused_unreachable": "waiting", "cancelled": "taken_back",
              "completed": "closed"}
    state = result.get("state")
    if state not in states:
        return
    session = result.get("session") or {}
    sid = session.get("session_id") if isinstance(session, dict) else session
    supervision = result.get("supervision") or {}
    fields = {"owner": "watchtower", "state": states[state],
              "supervision_state": state, "auto": bool(auto),
              "supervision_id": result.get("supervision_id"),
              "generation": result.get("generation"),
              "delivery_state": result.get("delivery_state"),
              "question": result.get("question") or supervision.get("question") or "",
              "waiting_on": result.get("waiting_on") or result.get("wait_identity") or supervision.get("wait_identity") or
              (result.get("reason") if state == "paused_unreachable" else "") or "",
              "sync_error": "", "adoption_pending": False,
              "nudges": supervision.get("reminders", 0)}
    if sid:
        fields["session"] = sid
    tickets._set_handoff(ref, **fields)
    if state == "completed":
        tickets._mutate(lambda d: d["tickets"].get(ref, {}).__setitem__("closed", True))


def adopt(sid, ref, *, auto=False, legacy=None):
    from . import idea_tickets as tickets
    # Caller holds the effect lock, which also guards all old CCC effects.
    current = (tickets.load_state()["tickets"].get(ref) or {}).get("handoff") or {}
    tickets._set_handoff(ref, owner="watchtower", session=sid,
                         state=current.get("state") or "running", auto=bool(auto),
                         adoption_pending=True)
    args = [sid, "--ticket", ref]
    if legacy is not None:
        args += ["--legacy-json", json.dumps(legacy, separators=(",", ":"))]
    result = _call(args)
    if result.get("ok"):
        _mirror(ref, result, auto=auto)
    else:
        error = result.get("error") or "Adoption failed"
        tickets._set_handoff(ref, sync_error=error, state="waiting",
                             supervision_state="paused_unreachable",
                             waiting_on="WatchTower adoption: " + error)
    tickets._restamp_rows()
    return {**result, "ref": ref}


def cancel(sid, ref):
    from . import idea_tickets as tickets
    result = _call(["cancel", sid])
    if result.get("ok"):
        _mirror(ref, result)
        tickets._set_handoff(ref, state="taken_back")
        tickets._restamp_rows()
    return {**result, "ref": ref}


def answer(sid, ref, text):
    from . import idea_tickets as tickets
    rc, _output = tickets._wt(["answer", ref, text])
    if rc:
        return {"ok": False, "ref": ref, "error": "WatchTower could not accept the answer"}
    # WT owns delivery, including retry. Never inject a duplicate from CCC.
    tickets._set_handoff(ref, state="running", question="")
    tickets._restamp_rows()
    return {"ok": True, "ref": ref}


def sync_once(statuses=None):
    """Import fenced legacy records, then refresh presentation from WT truth."""
    from . import idea_tickets as tickets
    # Automatic enrollment originates in WT, so it may have no local handoff
    # record yet. Only mirror sessions already associated with that ticket.
    for sid, result in (statuses or {}).items():
        ref = result.get("ticket")
        linked = tickets.load_state()["sessions"].get(sid) or {}
        if result.get("ok") and ref and linked.get("ref") == ref:
            with tickets.handoff_effect_lock(ref):
                local = tickets.load_state()["tickets"].get(ref) or {}
                if not local.get("handoff"):
                    _mirror(ref, result, auto=True)
    data = tickets.load_state()
    for ref, ticket in list(data["tickets"].items()):
        h = ticket.get("handoff") or {}
        sid = h.get("session")
        if not sid or ticket.get("closed"):
            continue
        legacy_states = {*tickets._ACTIVE_HANDOFF, "taken_back"}
        if h.get("owner") != "watchtower" and h.get("state") not in legacy_states:
            continue
        with tickets.handoff_effect_lock(ref):
            tickets._state_cache["key"] = None
            current = (tickets.load_state()["tickets"].get(ref) or {}).get("handoff") or {}
            if current.get("owner") != "watchtower":
                if current.get("state") not in legacy_states:
                    continue
                migration_id = hashlib.sha256(
                    json.dumps([ref, sid, current.get("at")], separators=(",", ":")).encode()
                ).hexdigest()
                since = max(float(current.get("at") or 0), float(current.get("nudged_at") or 0))
                legacy = {"owner_fenced": True, "migration_id": migration_id,
                          "state": {"blocked": "needs_input", "taken_back": "cancelled"}.get(current["state"], current["state"]),
                          "reason": current.get("question") or "",
                          "reminders": int(current.get("nudges") or 0),
                          "enrolled_at": current.get("at") or time.time(),
                          "next_deadline": since + tickets.HANDOFF_STALL_S,
                          "wait_identity": current.get("waiting_on") or ""}
                # Keep the exact import payload across retries, including a
                # crash after fencing but before WT returns its result.
                tickets._set_handoff(ref, owner="watchtower", legacy_import=legacy)
                adopt(sid, ref, auto=current.get("auto", False), legacy=legacy)
                continue
            if current.get("legacy_import") and not current.get("supervision_id"):
                adopt(sid, ref, auto=current.get("auto", False), legacy=current["legacy_import"])
                continue
            if current.get("adoption_pending"):
                adopt(sid, ref, auto=current.get("auto", False))
                continue
            result = (statuses.get(sid) or {}) if statuses is not None else _call(["status", sid])
            if result.get("ok") and result.get("ticket") == ref:
                _mirror(ref, result, auto=current.get("auto", False))
    tickets._restamp_rows()


def publish_candidates(*, enabled=True):
    """Publish only explicitly linked work; discovery remains native to WT."""
    from . import idea_tickets as tickets
    data = tickets.load_state()
    scopes = {}
    looked_up = 0
    for ref, ticket in (list(data["tickets"].items()) if enabled else []):
        if ticket.get("closed") or not ticket.get("queue"):
            continue
        members = ticket.get("sessions") or []
        leads = [sid for sid in members if (data["sessions"].get(sid) or {}).get("kind") != "child"
                 and (data["sessions"].get(sid) or {}).get("ref") == ref
                 and not (data["sessions"].get(sid) or {}).get("explicitly_archived")]
        if not leads:
            continue
        sid = leads[-1]
        entry = data["sessions"].get(sid) or {}
        meta = entry.get("takeover_observation") or {}
        if not meta.get("repo_path") and meta.get("retry_at", 0) <= time.time() and looked_up < 5:
            looked_up += 1
            native = _call(["observe", sid])
            if native.get("repo_path") and native.get("engine") in ("claude", "codex"):
                meta = {"repo_path": native["repo_path"], "engine": native["engine"]}
                tickets._mutate(lambda d: d["sessions"].get(sid, {}).__setitem__(
                    "takeover_observation", meta))
            else:
                tickets._mutate(lambda d: d["sessions"].get(sid, {}).__setitem__(
                    "takeover_observation", {"retry_at": time.time() + 900}))
        repo = meta.get("repo_path")
        brief = ticket.get("task_brief") or ticket.get("title")
        if not repo or not brief:
            continue
        key = (repo, ticket["queue"])
        scope = scopes.setdefault(key, {"repo_path": repo, "queue": ticket["queue"], "candidates": []})
        scope["candidates"].append({"session_id": sid, "engine": meta["engine"],
                                    "ticket": ref, "task_brief": brief,
                                    "authorized": True,
                                    "related_session_ids": [s for s in members if s != sid]})
    payload = {"schema_version": 1, "enabled": bool(enabled), "delegation_fenced": True,
               "scopes": [scopes[k] for k in sorted(scopes)]}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    try:
        saved = json.loads(_marker().read_text())
    except (OSError, ValueError):
        saved = {}
    if saved.get("candidates_hash") == digest:
        return
    result = _call(["candidates", "--source", "ccc", "--candidates-json", raw])
    if result.get("ok"):
        _save_marker(owner="watchtower", candidates_hash=digest)
    else:
        raise RuntimeError(result.get("error") or "Candidate publication failed")


def start():
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True, name="ccc-takeover-bridge").start()


def _loop():
    global _available
    from . import idea_tickets as tickets
    # A transient unavailable CLI must not resurrect the legacy supervisor.
    if _marker().exists():
        _available = True
    while True:
        try:
            if tickets._enabled():
                result = _call(["status"])
                if result.get("ok") and result.get("schema_version") == 1:
                    _save_marker(owner="watchtower")
                    _available = True
                    rows = result.get("supervisions") or []
                    statuses = {r["session"]["session_id"]: {**r, "ok": True}
                                for r in rows if isinstance(r.get("session"), dict)}
                    sync_once(statuses)
                    publish_candidates()
            elif _marker().exists():
                # Turning off CCC's opt-in must also stop new automatic
                # enrollment; existing explicit supervision remains WT-owned.
                publish_candidates(enabled=False)
        except Exception as exc:
            tickets._core._log_activity("idea", "IDEA_TICKET_ERR", f"takeover bridge {exc}")
        time.sleep(60)
