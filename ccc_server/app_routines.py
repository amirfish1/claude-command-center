# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""App routines: named, scheduled jobs that belong to a user app.

An app folder (~/.claude/command-center/apps/<id>/) declares routines in its
app.json:

    "routines": [
      {"id": "booking-reminder", "name": "Booking Reminder", "icon": "🛎",
       "does": "Warns before a booking stops being refundable",
       "schedule": "daily 08:00", "kind": "script",
       "run": ["python3", "booking_reminder.py"]},
      {"id": "price-watch", "name": "Price Watch", "icon": "🔎",
       "does": "Checks whether prices dropped",
       "schedule": "every 12h", "kind": "agent",
       "prompt": "Look up the current prices and compare with prices.json."}
    ]

Two kinds, one card each in the app:

* script: `run` (an argv list, never a shell string) executes in the app
  folder. The last stdout line may be a JSON object
  {"summary": "...", "alert": true, "notify": {"title": "...", "body": "..."}};
  otherwise the last line is the summary. A non-zero exit is a failure.
* agent: a fresh session is spawned in the app folder with `prompt`; the
  agent reports its result back through POST /api/apps/routines/report.

Everything the server learns (on/off, last run, outcome, the app's builder
agent) is kept in <app>/ccc-state.json so app.json stays the author's file.
The loop reads a handful of small files per tick: no session scans and no
subprocess unless a routine is actually due.

Stdlib-only; no side effects at import.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from ccc_server.paths import COMMAND_CENTER_STATE_DIR

try:  # zoneinfo data can be missing on minimal installs
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

APPS_DIR = COMMAND_CENTER_STATE_DIR / "apps"
STATE_NAME = "ccc-state.json"
TICK_SECONDS = 30
SCRIPT_TIMEOUT_SECONDS = 300
AGENT_STALE_SECONDS = 2 * 3600
HISTORY_LIMIT = 10
_SUMMARY_LIMIT = 400

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_EVERY_RE = re.compile(r"^every\s+(\d+)\s*(m|min|h|d)$")
_DAILY_RE = re.compile(r"^daily\s+(\d{1,2}):(\d{2})$")

_lock = threading.Lock()
_running = set()  # (app_id, routine_id) currently executing a script


def valid_id(value):
    return isinstance(value, str) and bool(_ID_RE.match(value))


def app_dir(app_id, apps_dir=None):
    """The app's folder, or None when the id is malformed or unknown."""
    if not valid_id(app_id):
        return None
    d = Path(apps_dir or APPS_DIR) / app_id
    return d if (d / "app.json").is_file() else None


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return default
    return data if isinstance(data, type(default)) else default


def _write_json_atomic(path, obj):
    tmp = Path(str(path) + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def parse_schedule(text):
    """'daily HH:MM' or 'every N(m|h|d)' -> a dict, or None when invalid."""
    s = " ".join(str(text or "").lower().split())
    m = _DAILY_RE.match(s)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2))
        if hh < 24 and mm < 60:
            return {"type": "daily", "hour": hh, "minute": mm}
        return None
    m = _EVERY_RE.match(s)
    if m:
        n = int(m.group(1))
        seconds = n * {"m": 60, "min": 60, "h": 3600, "d": 86400}[m.group(2)]
        if seconds >= 300:  # nothing runs more often than every 5 minutes
            return {"type": "every", "seconds": seconds}
    return None


def schedule_text(sched):
    if not sched:
        return "No schedule: runs only when you press Run now"
    if sched["type"] == "daily":
        h, m = sched["hour"], sched["minute"]
        return "Every day at %d:%02d %s" % (h % 12 or 12, m, "AM" if h < 12 else "PM")
    s = sched["seconds"]
    for unit, name in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if s % unit == 0:
            n = s // unit
            return f"Every {name}" if n == 1 else f"Every {n} {name}s"
    return f"Every {s} seconds"


def _tz(name):
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(str(name))
        except Exception:
            pass
    return None  # local time


def next_run(sched, last_run, now, tz_name=None):
    """Epoch seconds of the next run; <= now means due. None = never."""
    if not sched:
        return None
    if sched["type"] == "every":
        return now if not last_run else last_run + sched["seconds"]
    tz = _tz(tz_name)
    here = datetime.fromtimestamp(now, tz) if tz else datetime.fromtimestamp(now)
    slot = here.replace(hour=sched["hour"], minute=sched["minute"], second=0, microsecond=0)
    if slot > here:
        previous = slot - timedelta(days=1)
    else:
        previous, slot = slot, slot + timedelta(days=1)
    # A slot missed while the server was down runs once when it comes back.
    if not last_run or last_run < previous.timestamp():
        return previous.timestamp()
    return slot.timestamp()


def load_routines(folder):
    """Validated routines from app.json, plus the manifest itself."""
    meta = _read_json(Path(folder) / "app.json", {})
    out, seen = [], set()
    raw = meta.get("routines")
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or not valid_id(r.get("id")) or r["id"] in seen:
            continue
        kind = r.get("kind") or "script"
        run = r.get("run")
        if kind == "script":
            if not (isinstance(run, list) and run and all(isinstance(a, str) for a in run)):
                continue
        elif kind == "agent":
            if not str(r.get("prompt") or "").strip():
                continue
        else:
            continue
        seen.add(r["id"])
        out.append({
            "id": r["id"],
            "name": str(r.get("name") or r["id"])[:80],
            "icon": str(r.get("icon") or "⏰")[:8],
            "does": str(r.get("does") or "")[:300],
            "schedule": str(r.get("schedule") or ""),
            "kind": kind,
            "run": run if kind == "script" else None,
            "prompt": str(r.get("prompt") or "") if kind == "agent" else None,
            # A setup routine installs what the app needs. It runs once on its
            # own the first time CCC sees the app, then only from "Run now".
            "setup": r.get("setup") is True and kind == "script",
        })
    return out, meta


def load_suggestions(meta):
    """Starter prompts from app.json: things the user can ask the agent."""
    out = []
    raw = meta.get("suggestions") if isinstance(meta, dict) else None
    for s in raw if isinstance(raw, list) else []:
        if isinstance(s, str):
            s = {"label": s, "prompt": s}
        if not isinstance(s, dict):
            continue
        label = str(s.get("label") or "").strip()[:40]
        prompt = str(s.get("prompt") or label)[:600]
        if label and prompt.strip():
            out.append({"label": label, "prompt": prompt})
    return out[:8]


def load_state(folder):
    state = _read_json(Path(folder) / STATE_NAME, {})
    if not isinstance(state.get("routines"), dict):
        state["routines"] = {}
    return state


def _update_routine_state(folder, rid, **fields):
    with _lock:
        state = load_state(folder)
        entry = state["routines"].setdefault(rid, {})
        history = fields.pop("_history", None)
        entry.update(fields)
        if history:
            entry["history"] = ([history] + list(entry.get("history") or []))[:HISTORY_LIMIT]
        _write_json_atomic(Path(folder) / STATE_NAME, state)
        return entry


def describe(app_id, apps_dir=None, now=None):
    """What the app page shows: the builder agent and one row per routine."""
    folder = app_dir(app_id, apps_dir)
    if folder is None:
        return None
    now = now or time.time()
    routines, meta = load_routines(folder)
    state = load_state(folder)
    rows = []
    for r in routines:
        st = state["routines"].get(r["id"], {})
        sched = parse_schedule(r["schedule"])
        enabled = st.get("enabled", True) is not False
        rows.append({
            "id": r["id"], "name": r["name"], "icon": r["icon"], "does": r["does"],
            "kind": r["kind"], "schedule": r["schedule"], "setup": r["setup"],
            "schedule_text": "Runs once, when the app is installed" if r["setup"] and not sched
            else schedule_text(sched),
            "enabled": enabled,
            "running": st.get("status") == "running",
            "last_run": st.get("last_run"),
            "status": st.get("status") or "never",
            "summary": st.get("summary") or "",
            "alert": bool(st.get("alert")),
            "session_id": st.get("session_id") or "",
            "next_run": next_run(sched, st.get("last_run"), now, meta.get("tz")) if enabled else None,
            "history": st.get("history") or [],
        })
    agent = state.get("agent") if isinstance(state.get("agent"), dict) else None
    return {"ok": True, "app": app_id, "label": str(meta.get("label") or app_id),
            "agent": agent, "routines": rows, "suggestions": load_suggestions(meta)}


def overview(apps_dir=None):
    """One small summary per app that has routines or an agent (Apps home)."""
    base = Path(apps_dir or APPS_DIR)
    out = {}
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return out
    for d in entries:
        info = describe(d.name, base) if d.is_dir() else None
        if not info or not (info["routines"] or info["agent"]):
            continue
        out[d.name] = {
            "routines": len(info["routines"]),
            "on": sum(1 for r in info["routines"] if r["enabled"]),
            "alerts": sum(1 for r in info["routines"] if r["alert"]),
            "failed": sum(1 for r in info["routines"] if r["status"] == "failed"),
            "agent": (info["agent"] or {}).get("session_id") or "",
        }
    return out


def _post_json(port, path, body, timeout=60):
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}{path}", data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("ok", False)
        data.setdefault("error", f"HTTP {e.code}")
    except (OSError, ValueError) as e:
        data = {"ok": False, "error": str(e)}
    return data if isinstance(data, dict) else {"ok": False, "error": "bad response"}


def _notify(port, app_id, routine, note, alert):
    title = str((note or {}).get("title") or routine["name"])
    try:
        from ccc_server import app_channels
        app_channels.deliver(title, str((note or {}).get("body") or ""), f"/view/{app_id}")
    except Exception:
        pass  # a channel must never break a routine
    if not port:
        return
    _post_json(port, "/api/notify", {
        "title": title, "body": str((note or {}).get("body") or ""),
        "kind": "needs_input" if alert else "info", "url": f"/view/{app_id}",
    }, timeout=10)


def parse_script_output(stdout):
    """(summary, alert, notify) from a script's stdout."""
    lines = [ln.strip() for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return "Done", False, None
    last = lines[-1]
    if last.startswith("{"):
        try:
            obj = json.loads(last)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            note = obj.get("notify") if isinstance(obj.get("notify"), dict) else None
            return str(obj.get("summary") or "Done")[:_SUMMARY_LIMIT], bool(obj.get("alert")), note
    return last[:_SUMMARY_LIMIT], False, None


def _finish(folder, app_id, routine, status, summary, alert, note, port, started, session_id=None):
    fields = {"status": status, "summary": summary, "alert": bool(alert), "last_run": started,
              "_history": {"at": started, "status": status, "summary": summary, "alert": bool(alert)}}
    if session_id is not None:
        fields["session_id"] = session_id
    _update_routine_state(folder, routine["id"], **fields)
    if note or alert or status == "failed":
        if status == "failed" and not note:
            note = {"title": f"{routine['name']} failed", "body": summary}
        elif not note:
            note = {"title": routine["name"], "body": summary}
        _notify(port, app_id, routine, note, alert or status == "failed")


def _run_script(folder, app_id, routine, port, started):
    try:
        proc = subprocess.run(
            routine["run"], cwd=str(folder), capture_output=True, text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS, stdin=subprocess.DEVNULL,
            env={**os.environ, "CCC_APP_ID": app_id, "CCC_APP_DIR": str(folder),
                 "CCC_ROUTINE_ID": routine["id"]})
    except subprocess.TimeoutExpired:
        _finish(folder, app_id, routine, "failed",
                f"Stopped after {SCRIPT_TIMEOUT_SECONDS // 60} minutes", False, None, port, started)
        return
    except OSError as e:
        _finish(folder, app_id, routine, "failed", f"Could not start: {e}"[:_SUMMARY_LIMIT],
                False, None, port, started)
        return
    if proc.returncode != 0:
        tail = [ln for ln in (proc.stderr or proc.stdout or "").splitlines() if ln.strip()]
        _finish(folder, app_id, routine, "failed",
                (tail[-1] if tail else f"Exit code {proc.returncode}")[:_SUMMARY_LIMIT],
                False, None, port, started)
        return
    summary, alert, note = parse_script_output(proc.stdout)
    _finish(folder, app_id, routine, "ok", summary, alert, note, port, started)


def agent_prompt(app_id, label, routine, port):
    return (
        f'You are "{routine["name"]}", a scheduled routine of the CCC app "{label}", '
        f"which lives in this folder. Do this run now, then stop:\n\n{routine['prompt']}\n\n"
        "Save anything the app's page should show into the app's .json data files. "
        "When you are done, report the result in one plain sentence by running:\n"
        f"curl -s -X POST http://127.0.0.1:{int(port)}/api/apps/routines/report "
        "-H 'Content-Type: application/json' -d "
        f"'{{\"app\":\"{app_id}\",\"id\":\"{routine['id']}\",\"summary\":\"<one sentence>\","
        "\"alert\":false}'\n"
        'Set "alert" to true only when the owner should look at this today.'
    )


def _run_agent(folder, app_id, label, routine, port, started):
    res = _post_json(port, "/api/sessions/spawn", {
        "prompt": agent_prompt(app_id, label, routine, port),
        "cwd": str(folder), "name": f"{app_id}-{routine['id']}"[:60],
    })
    sid = res.get("session_id") or ""
    if not sid:
        _finish(folder, app_id, routine, "failed",
                ("Could not start the agent: " + str(res.get("error") or "unknown"))[:_SUMMARY_LIMIT],
                False, None, port, started)
        return
    _update_routine_state(folder, routine["id"], status="running", summary="Agent is working",
                          alert=False, last_run=started, session_id=sid)


def run_now(app_id, rid, port, apps_dir=None, wait=False):
    """Start one routine. Returns (result, http_status)."""
    folder = app_dir(app_id, apps_dir)
    if folder is None:
        return {"ok": False, "error": "unknown app"}, 404
    routines, meta = load_routines(folder)
    routine = next((r for r in routines if r["id"] == rid), None)
    if routine is None:
        return {"ok": False, "error": "unknown routine"}, 404
    key = (app_id, rid)
    with _lock:
        if key in _running:
            return {"ok": True, "already_running": True}, 200
        _running.add(key)
    started = time.time()
    _update_routine_state(folder, rid, status="running", summary="Running", last_run=started)

    def work():
        try:
            if routine["kind"] == "script":
                _run_script(folder, app_id, routine, port, started)
            else:
                _run_agent(folder, app_id, str(meta.get("label") or app_id), routine, port, started)
        except Exception as e:  # a routine must never take the loop down
            _finish(folder, app_id, routine, "failed", f"Error: {e}"[:_SUMMARY_LIMIT],
                    False, None, port, started)
        finally:
            with _lock:
                _running.discard(key)

    if wait:
        work()
    else:
        threading.Thread(target=work, daemon=True, name=f"ccc-routine-{app_id}-{rid}").start()
    return {"ok": True, "started": True}, 200


def set_enabled(app_id, rid, enabled, apps_dir=None):
    folder = app_dir(app_id, apps_dir)
    if folder is None:
        return {"ok": False, "error": "unknown app"}, 404
    if not any(r["id"] == rid for r in load_routines(folder)[0]):
        return {"ok": False, "error": "unknown routine"}, 404
    _update_routine_state(folder, rid, enabled=bool(enabled))
    return {"ok": True, "enabled": bool(enabled)}, 200


def _rewrite_manifest(folder, rid, change):
    """Atomically rewrite only routine `rid` in app.json. `change(entry)` edits
    the entry in place, or returns "delete" to drop it. Returns False when the
    routine is not there."""
    path = Path(folder) / "app.json"
    with _lock:
        meta = _read_json(path, {})
        raw = meta.get("routines")
        if not isinstance(raw, list):
            return False
        for i, entry in enumerate(raw):
            if isinstance(entry, dict) and entry.get("id") == rid:
                if change(entry) == "delete":
                    del raw[i]
                _write_json_atomic(path, meta)
                return True
    return False


def update_routine(payload, apps_dir=None):
    """Edit a routine's name, icon or schedule. Returns (result, http_status)."""
    folder = app_dir(payload.get("app"), apps_dir)
    if folder is None:
        return {"ok": False, "error": "unknown app"}, 404
    rid = payload.get("id")
    if not any(r["id"] == rid for r in load_routines(folder)[0]):
        return {"ok": False, "error": "unknown routine"}, 404
    edits = {}
    if "name" in payload:
        name = " ".join(str(payload["name"] or "").split())[:80]
        if not name:
            return {"ok": False, "error": "The name cannot be empty"}, 400
        edits["name"] = name
    if "icon" in payload:
        icon = str(payload["icon"] or "").strip()[:8]
        if not icon:
            return {"ok": False, "error": "Pick an icon"}, 400
        edits["icon"] = icon
    if "schedule" in payload:
        sched = " ".join(str(payload["schedule"] or "").lower().split())
        if sched and parse_schedule(sched) is None:
            return {"ok": False, "error": "That schedule is not valid (at least every 5 minutes)"}, 400
        edits["schedule"] = sched
    if not edits:
        return {"ok": False, "error": "nothing to change"}, 400
    if not _rewrite_manifest(folder, rid, lambda e: e.update(edits)):
        return {"ok": False, "error": "unknown routine"}, 404
    return {"ok": True, **edits}, 200


def delete_routine(app_id, rid, apps_dir=None):
    """Remove a routine from app.json and forget its saved state."""
    folder = app_dir(app_id, apps_dir)
    if folder is None:
        return {"ok": False, "error": "unknown app"}, 404
    if not _rewrite_manifest(folder, rid, lambda e: "delete"):
        return {"ok": False, "error": "unknown routine"}, 404
    with _lock:
        state = load_state(folder)
        if state["routines"].pop(rid, None) is not None:
            _write_json_atomic(folder / STATE_NAME, state)
    return {"ok": True}, 200


def report(payload, port, apps_dir=None):
    """An agent routine reporting its result."""
    app_id, rid = payload.get("app"), payload.get("id")
    folder = app_dir(app_id, apps_dir)
    if folder is None:
        return {"ok": False, "error": "unknown app"}, 404
    routine = next((r for r in load_routines(folder)[0] if r["id"] == rid), None)
    if routine is None:
        return {"ok": False, "error": "unknown routine"}, 404
    summary = " ".join(str(payload.get("summary") or "Done").split())[:_SUMMARY_LIMIT]
    alert = payload.get("alert") is True
    note = payload.get("notify") if isinstance(payload.get("notify"), dict) else None
    started = load_state(folder)["routines"].get(rid, {}).get("last_run") or time.time()
    _finish(folder, app_id, routine, "ok", summary, alert, note, port, started)
    return {"ok": True}, 200


def ensure_agent(app_id, port, fresh=False, apps_dir=None, docs_path=""):
    """The app's one builder agent: return it, or start it on first use."""
    folder = app_dir(app_id, apps_dir)
    if folder is None:
        return {"ok": False, "error": "unknown app"}, 404
    state = load_state(folder)
    agent = state.get("agent") if isinstance(state.get("agent"), dict) else None
    if agent and agent.get("session_id") and not fresh:
        return {"ok": True, "session_id": agent["session_id"], "existing": True}, 200
    label = str(load_routines(folder)[1].get("label") or app_id)
    docs = f" How apps and routines work is documented in {docs_path}." if docs_path else ""
    if (folder / "CLAUDE.md").is_file() or (folder / "AGENTS.md").is_file():
        docs += " This app came with its own instructions (CLAUDE.md or AGENTS.md in this folder); follow them."
    res = _post_json(port, "/api/sessions/spawn", {
        "cwd": str(folder), "name": f"{label} agent"[:60],
        "prompt": (
            f'You are the builder agent for the CCC app "{label}", which lives in this folder. '
            "The owner talks to you from a pane next to the app's page and asks you to add or "
            f"change what the app shows and the routines it runs.{docs} Read app.json, index.html "
            "and the .json data files. Then reply in three lines or fewer saying what the app has "
            "today, and wait for instructions. Do not change anything yet."),
    })
    sid = res.get("session_id") or ""
    if not sid:
        return {"ok": False, "error": str(res.get("error") or "could not start the agent")}, 502
    with _lock:
        state = load_state(folder)
        state["agent"] = {"session_id": sid, "started_at": time.time()}
        _write_json_atomic(folder / STATE_NAME, state)
    return {"ok": True, "session_id": sid, "existing": False}, 200


def tick(port, apps_dir=None, now=None):
    """Start every routine that is due. Returns the (app, routine) pairs started."""
    base = Path(apps_dir or APPS_DIR)
    now = now or time.time()
    started = []
    try:
        entries = sorted(base.iterdir())
    except OSError:
        return started
    for d in entries:
        if not d.is_dir() or d.name.startswith(".") or not (d / "app.json").is_file():
            continue
        routines, meta = load_routines(d)
        if not routines:
            continue
        state = load_state(d)
        for r in routines:
            st = state["routines"].get(r["id"], {})
            if st.get("enabled", True) is False:
                continue
            if st.get("status") == "running" and r["kind"] == "script" and (d.name, r["id"]) not in _running:
                # The server restarted mid-run; the next slot runs it again.
                _update_routine_state(d, r["id"], status="failed", summary="Interrupted by a restart")
                continue
            if st.get("status") == "running":
                # An agent that never reported back must not block the routine forever.
                if now - (st.get("last_run") or 0) > AGENT_STALE_SECONDS and (d.name, r["id"]) not in _running:
                    _update_routine_state(d, r["id"], status="failed",
                                          summary="The agent did not report a result")
                continue
            due = next_run(parse_schedule(r["schedule"]), st.get("last_run"), now, meta.get("tz"))
            if r["setup"] and not st.get("last_run"):
                due = now  # first sight of the app: install what it needs
            if due is not None and due <= now:
                run_now(d.name, r["id"], port, base)
                started.append((d.name, r["id"]))
    return started


def loop(port, stop=None):
    """Server background loop; started once from main()."""
    while not (stop and stop.is_set()):
        try:
            tick(port)
        except Exception:
            pass
        time.sleep(TICK_SECONDS)
