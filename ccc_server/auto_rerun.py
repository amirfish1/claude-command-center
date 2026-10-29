# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Opt-in "rerun once" for sessions that died.

A dead session is notify-only by default (session_death.py). A session or a
scheduled job can opt in to one automatic rerun per death, and every rerun
goes through the wake guard (wake_guard.py: at most 2 per 24h, no second wake
without progress, escalate to the owner and stop).

Opt-ins live in `auto-rerun.json` in the CCC state dir:

    {
      "sessions": {"<session id>": {"enabled_at": 1790000000}},
      "rules": [
        {"name": "nightly-report",
         "cwd": "/path/to/repo",                  # the dead session's cwd
         "title_prefix": "# Nightly report",      # optional: its first prompt
         "command": ["systemctl", "--user", "start", "--no-block", "report.service"]}
      ]
    }

* A session opt-in resumes that same session with a short note (the same
  injector a manual send uses, so a dead child is resumed via --resume).
* A rule is for scheduled jobs that start a fresh session every run (e.g. a
  `claude -p` behind a timer): it reruns the job's `command` (split-free argv,
  no shell). Its wake-guard key is `rule:<name>`, and because each rerun is a
  new session there is no transcript to measure, so a second death inside
  24h counts as "no progress": the guard refuses, tells the owner, and stops
  until the opt-in is re-saved (`set_rule` / `set_session` clear the ledger).

HTTP: GET /api/auto-rerun lists opt-ins; POST {"session_id", "enabled"}
toggles a session. Rules run commands, so they are host-file only.

Stdlib-only; the inject / command / wake-guard calls are injectable for tests.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time

try:  # POSIX only; best-effort lock elsewhere.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

COMMAND_TIMEOUT_S = 60
SOURCE = "dead-session-rerun"
RESUME_NOTE = ("CCC note: this session's process stopped mid-turn and you opted in to one "
               "automatic rerun. Check the current state of what you were doing (files, "
               "processes, partial writes) before redoing anything, then continue the task.")


def _default_path():
    base = os.environ.get("CCC_STATE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "command-center"
    )
    return os.path.join(base, "auto-rerun.json")


def _wake_path(path):
    return os.path.join(os.path.dirname(path or _default_path()), "session-wakes.json")


def load(path=None):
    try:
        with open(path or _default_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    sessions = data.get("sessions") if isinstance(data.get("sessions"), dict) else {}
    rules = [r for r in data.get("rules") or [] if _rule_ok(r)]
    return {"sessions": sessions, "rules": rules}


def _rule_ok(rule):
    return (isinstance(rule, dict) and isinstance(rule.get("name"), str) and rule["name"].strip()
            and isinstance(rule.get("cwd"), str) and rule["cwd"].strip()
            and isinstance(rule.get("command"), list) and rule["command"]
            and all(isinstance(a, str) for a in rule["command"]))


def _mutate(path, fn):
    path = path or _default_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock = open(path + ".lock", "a+")
    try:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(path)
        result = fn(data)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, path)
        return result
    finally:
        lock.close()


def set_session(sid, enabled, path=None, now=None):
    """Opt a session in or out. Saving either way gives it a fresh wake budget."""
    sid = str(sid or "")
    now = time.time() if now is None else float(now)

    def fn(data):
        if enabled:
            data["sessions"][sid] = {"enabled_at": now}
        else:
            data["sessions"].pop(sid, None)
    _mutate(path, fn)
    _clear_wakes(sid, path)


def set_rule(rule, path=None):
    """Add or replace a rule by name (None command -> remove it)."""
    name = str((rule or {}).get("name") or "").strip()
    if not name:
        raise ValueError("rule needs a name")
    if rule.get("command") is not None and not _rule_ok(rule):
        raise ValueError("rule needs name, cwd and a command argv list")

    def fn(data):
        data["rules"] = [r for r in data["rules"] if r.get("name") != name]
        if rule.get("command") is not None:
            data["rules"].append({k: rule[k] for k in ("name", "cwd", "title_prefix", "command") if k in rule})
    _mutate(path, fn)
    _clear_wakes("rule:" + name, path)


def _clear_wakes(key, path):
    try:
        from ccc_server import wake_guard
        wake_guard.clear(key, path=_wake_path(path))
    except Exception:
        pass


def _norm(p):
    return os.path.realpath(os.path.expanduser(str(p or ""))).rstrip("/")


def match(incident, data):
    """('session', sid, None) / ('rule', 'rule:<name>', rule) / None."""
    sid = str(incident.get("session_id") or "")
    if sid and sid in data["sessions"]:
        return "session", sid, None
    cwd = _norm(incident.get("cwd"))
    title = " ".join(str(incident.get("title") or "").split())
    for rule in data["rules"]:
        if _norm(rule["cwd"]) != cwd or not cwd:
            continue
        prefix = " ".join(str(rule.get("title_prefix") or "").split())
        if prefix and not title.startswith(prefix):
            continue
        return "rule", "rule:" + rule["name"], rule
    return None


def _run_command(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_S)
        out = (r.stdout or r.stderr or "").strip()[:200]
        return {"ok": r.returncode == 0, "error": None if r.returncode == 0 else f"rc={r.returncode} {out}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:200]}


def _inject(sid, text):
    from ccc_server import core as _core
    return _core._inject_text_into_session(sid, text, mode="send", source=SOURCE)


def _log(verb, detail):
    try:
        from ccc_server.activity_log import _log_activity
        _log_activity("auto-rerun", verb, detail)
    except Exception:
        pass


def plan(incident, *, path=None, now=None, request=None):
    """Decide (synchronously, cheap file I/O) whether this death is rerun.

    Returns None when the session did not opt in, else
    {"key", "kind", "allowed", "reason", "rule"}. An allowed rerun is
    already recorded in the wake ledger; run it with `execute()`."""
    data = load(path)
    hit = match(incident, data)
    if hit is None:
        return None
    kind, key, rule = hit
    if request is None:
        from ccc_server import wake_guard
        request = wake_guard.request
    transcript = incident.get("path") if kind == "session" else None
    allowed, reason = request(key, source=SOURCE, incident=f"{incident.get('session_id')}:{incident.get('size')}",
                              transcript_path=transcript, now=now, path=_wake_path(path))
    _log("RERUN_PLANNED" if allowed else "RERUN_GUARDED",
         f"key={key} session={incident.get('session_id')} reason={reason}")
    return {"key": key, "kind": kind, "allowed": allowed, "reason": reason, "rule": rule}


def execute(decision, incident, *, inject=None, run=None):
    """Carry out an allowed rerun. Returns the injector / command result."""
    if not decision or not decision.get("allowed"):
        return None
    if decision["kind"] == "rule":
        result = (run or _run_command)(list(decision["rule"]["command"]))
    else:
        try:
            result = (inject or _inject)(incident["session_id"], RESUME_NOTE)
        except Exception as exc:  # noqa: BLE001
            result = {"ok": False, "error": str(exc)[:200]}
    ok = bool(isinstance(result, dict) and result.get("ok"))
    _log("RERUN" if ok else "RERUN_FAIL",
         f"key={decision['key']} session={incident.get('session_id')} "
         f"error={(result or {}).get('error') if isinstance(result, dict) else result}")
    return result


def execute_async(decision, incident):
    if decision and decision.get("allowed"):
        threading.Thread(target=execute, args=(decision, incident), daemon=True,
                         name="ccc-auto-rerun").start()


def note_for(decision):
    """One sentence for the death toast / phone alert."""
    if not decision:
        return ""
    if decision["allowed"]:
        return " CCC is rerunning it once (opt-in)."
    if decision["reason"] in ("cap", "no_progress", "escalated"):
        return " Its automatic rerun was refused (the last one made no progress or hit the cap)."
    return ""


def handle_api_get(handler):
    data = load()
    handler.send_json({"ok": True, "sessions": data["sessions"],
                       "rules": [{"name": r["name"], "cwd": r["cwd"]} for r in data["rules"]]})


def handle_api_post(handler):
    try:
        length = int(handler.headers.get("Content-Length", "0") or 0)
        payload = json.loads(handler.rfile.read(length) if 0 < length <= 65536 else b"{}")
    except (ValueError, OSError):
        payload = None
    if not isinstance(payload, dict):
        handler.send_json({"ok": False, "error": "Expected a JSON object."}, 400)
        return
    # Session opt-ins only: a rule runs a command, so rules are edited in the
    # host's auto-rerun.json (or set_rule from a local shell), never over HTTP.
    sid = payload.get("session_id")
    if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", sid):
        handler.send_json({"ok": False, "error": 'Expected {"session_id", "enabled"}.'}, 400)
        return
    set_session(sid, payload.get("enabled", True) is not False)
    handler.send_json({"ok": True, **load()})
