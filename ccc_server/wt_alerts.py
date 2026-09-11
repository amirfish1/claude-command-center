"""WatchTower error alerts for the Queue panel (stdlib-only).

Problem this solves: a queue can silently stop draining because a worker
failed to launch (engine usage limit, auth expired, API down, missing binary),
because WatchTower's own daemon died, or because a backend call keeps failing
(``gh`` auth lost, GitHub rate limit). WatchTower records all of these -- but
in places nobody looks at: ``~/.watchtower/launch-failures.json`` and
``ERROR`` lines buried in a multi-MB ``activity.log``. This module folds them
into one small, ack-able list the dashboard shows above the queue.

Three sources, all cheap (no subprocess, no O(all sessions) work):

* ``launch-failures.json`` -- WatchTower's durable per-(queue, engine) record
  of the last launch failure plus its cooldown. WatchTower clears a record on
  the next successful launch, so a record that is still present is still an
  open problem.
* ``activity.log`` ``ERROR`` lines -- backend failures (GitHub list, etc.).
  Only the tail of the file is read (``_LOG_TAIL_BYTES``) and the parse is
  cached by ``(mtime, size)``. Identical messages per queue collapse into one
  alert with a count and a last-seen time.
* WatchTower service state -- ``_watchtower_service_status`` (already cached
  by the server). A stopped or degraded daemon means NO queue drains.

Acks are persisted server-side (``wt-alert-acks.json`` in the CCC state dir)
so they hold across browsers, popouts, and the phone. An ack is a timestamp
keyed by the alert's stable id: the alert stays hidden while its latest
occurrence is older than the ack, and re-surfaces the moment a NEWER
occurrence lands. That is what makes the strip safe to dismiss -- dismissing
never hides a fresh failure.

Every name here that touches the server's globals goes through ``_core``
(resolved at call time), and every entry point takes explicit paths so tests
can point it at a temp WatchTower home without importing server.py.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ccc_server import core as _core

# How much of activity.log to read for ERROR lines. ~1 MB is roughly a week
# of a busy fleet's log; the time window below is the real bound.
_LOG_TAIL_BYTES = 1_000_000
# Only ERROR lines newer than this are alerts; older ones are history.
_LOG_WINDOW_S = 48 * 3600
# Cap on alerts returned (the UI shows a strip, not a log).
_MAX_ALERTS = 20
# Acks older than this are pruned from the ack file -- any alert they would
# have hidden is long outside the log window / launch-failure streak window.
_ACK_RETENTION_S = 30 * 24 * 3600

_ACK_FILE_NAME = "wt-alert-acks.json"

_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC\s+(\S*?)\s*ERROR\s+(.*)$"
)

_lock = threading.Lock()
# activity.log ERROR parse cache: {"key": (mtime, size), "alerts": [...]}
_log_cache = {"key": None, "alerts": []}
# First time the WatchTower daemon was seen not-online (epoch). None = online.
_service_down_since = None


def _wt_home():
    try:
        return Path(_core._WT_HOME)
    except Exception:
        return Path.home() / ".watchtower"


def wt_alert_acks_path():
    try:
        base = Path(_core.COMMAND_CENTER_STATE_DIR)
    except Exception:
        base = Path.home() / ".claude" / "command-center"
    return base / _ACK_FILE_NAME


def _iso(ts):
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _short_hash(text):
    return hashlib.sha1(str(text).encode("utf-8", "replace")).hexdigest()[:10]


# ── source 1: launch-failures.json ──────────────────────────────────────────

def launch_failure_alerts(wt_home=None, now=None):
    """One alert per (queue, engine) record in WatchTower's launch-failures file."""
    now = time.time() if now is None else now
    path = Path(wt_home or _wt_home()) / "launch-failures.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out = []
    for key, rec in data.items():
        if not isinstance(rec, dict):
            continue
        queue = str(rec.get("queue") or str(key).split(":")[0] or "?").upper()
        engine = str(rec.get("engine") or "").strip()
        failed_at = rec.get("failed_at")
        try:
            failed_at = float(failed_at) if failed_at is not None else 0.0
        except (TypeError, ValueError):
            failed_at = 0.0
        if not failed_at:
            # Older records only carry the ISO stamp.
            try:
                failed_at = datetime.strptime(
                    str(rec.get("recorded_at") or ""), "%Y-%m-%dT%H:%M:%SZ"
                ).replace(tzinfo=timezone.utc).timestamp()
            except ValueError:
                failed_at = 0.0
        try:
            cooldown_until = float(rec.get("cooldown_until") or 0)
        except (TypeError, ValueError):
            cooldown_until = 0.0
        reason = str(rec.get("reason") or "worker launch failed").strip()
        model = str(rec.get("model") or "").strip()
        worker_id = str(rec.get("worker_id") or "").strip()
        consecutive = rec.get("consecutive")
        try:
            consecutive = int(consecutive or 0)
        except (TypeError, ValueError):
            consecutive = 0
        bits = []
        if worker_id:
            bits.append(worker_id)
        eng = engine + (":" + model if model else "")
        if eng:
            bits.append("[" + eng + "]")
        if rec.get("exit_code") is not None:
            bits.append("exit %s" % rec.get("exit_code"))
        if consecutive > 1:
            bits.append("x%d" % consecutive)
        out.append({
            "id": "launch:%s:%s" % (queue, engine or "?"),
            "kind": "launch_failure",
            "severity": "error",
            "queue": queue,
            "engine": engine,
            "model": model,
            "worker_id": worker_id,
            "title": reason,
            "detail": " ".join(bits),
            "ts": failed_at,
            "ts_iso": _iso(failed_at),
            "age_seconds": max(0, int(now - failed_at)) if failed_at else None,
            "count": max(1, consecutive),
            "cooldown_until": cooldown_until or None,
            "cooldown_until_iso": _iso(cooldown_until),
            "cooldown_active": bool(cooldown_until and cooldown_until > now),
            "log": str(rec.get("log") or ""),
            "session_id": str(rec.get("session_id") or ""),
        })
    return out


# ── source 2: activity.log ERROR lines ──────────────────────────────────────

def _normalize_error_message(detail):
    """Collapse a noisy ERROR detail to the part that identifies the failure.

    ``GitHub list failed: gh issue list --repo x --state open ... failed: To
    get started with GitHub CLI, please run:  gh auth login`` -> the text
    after the last ``failed:`` (the actual error), so repeats of the same
    failure across different commands group together.
    """
    text = " ".join(str(detail or "").split())
    if " failed: " in text:
        text = text.rsplit(" failed: ", 1)[1].strip() or text
    return text[:160]


def _error_headline(detail):
    """The leading 'what failed' clause of an ERROR line (before any command)."""
    text = " ".join(str(detail or "").split())
    head = text.split(":", 1)[0].strip() if ":" in text else text
    return (head or "WatchTower error")[:80]


def _parse_error_lines(text, now):
    cutoff = now - _LOG_WINDOW_S
    groups = {}
    for line in text.splitlines():
        m = _LOG_LINE_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(
                m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
        if ts < cutoff:
            continue
        queue = (m.group(3) or "").strip().upper() or "WATCHTOWER"
        detail = m.group(4).strip()
        msg = _normalize_error_message(detail)
        key = (queue, _short_hash(msg.lower()))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "queue": queue,
                "title": _error_headline(detail),
                "detail": msg,
                "first_ts": ts,
                "ts": ts,
                "count": 0,
            }
        g["count"] += 1
        if ts > g["ts"]:
            g["ts"] = ts
        if ts < g["first_ts"]:
            g["first_ts"] = ts
    out = []
    for (queue, h), g in groups.items():
        out.append({
            "id": "log:%s:%s" % (queue, h),
            "kind": "activity_error",
            "severity": "warning",
            "queue": queue,
            "engine": "",
            "model": "",
            "worker_id": "",
            "title": g["title"],
            "detail": g["detail"],
            "ts": g["ts"],
            "ts_iso": _iso(g["ts"]),
            "age_seconds": max(0, int(now - g["ts"])),
            "first_ts_iso": _iso(g["first_ts"]),
            "count": g["count"],
            "cooldown_until": None,
            "cooldown_until_iso": None,
            "cooldown_active": False,
            "log": "",
            "session_id": "",
        })
    return out


def activity_error_alerts(wt_home=None, now=None):
    """ERROR lines from the activity.log tail, grouped per (queue, message).

    Cached by (mtime, size) of the log; the file is appended constantly, so
    this effectively re-parses ~1 MB once per health poll -- milliseconds.
    """
    now = time.time() if now is None else now
    path = Path(wt_home or _wt_home()) / "activity.log"
    try:
        st = path.stat()
    except OSError:
        return []
    key = (st.st_mtime, st.st_size)
    with _lock:
        if _log_cache["key"] == key:
            cached = _log_cache["alerts"]
            # Age fields depend on `now`; refresh them on the cached copies.
            return [dict(a, age_seconds=max(0, int(now - a["ts"]))) for a in cached
                    if a["ts"] >= now - _LOG_WINDOW_S]
    try:
        with open(path, "rb") as f:
            if st.st_size > _LOG_TAIL_BYTES:
                f.seek(st.st_size - _LOG_TAIL_BYTES)
                f.readline()  # drop the partial first line
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    alerts = _parse_error_lines(text, now)
    with _lock:
        _log_cache["key"] = key
        _log_cache["alerts"] = alerts
    return list(alerts)


# ── source 3: WatchTower daemon state ───────────────────────────────────────

def service_alerts(service_status=None, now=None):
    """A critical alert while the WatchTower daemon is not online.

    ``ts`` is when the outage was FIRST noticed by this process, so an ack
    sticks for the duration of one outage and a later, separate outage
    re-alerts.
    """
    global _service_down_since
    now = time.time() if now is None else now
    if service_status is None:
        try:
            service_status = _core._watchtower_service_status(probe_api=True)
        except Exception:
            service_status = None
    if not isinstance(service_status, dict):
        return []
    state = str(service_status.get("state") or "")
    if state == "online":
        _service_down_since = None
        return []
    if not service_status.get("installed", True):
        # Nothing to alert on: this machine doesn't run WatchTower at all.
        _service_down_since = None
        return []
    if _service_down_since is None:
        _service_down_since = now
    since = _service_down_since
    if state == "degraded":
        title = "WatchTower daemon is running but its API is not answering"
        detail = "queues will not drain until the daemon is healthy"
    else:
        title = "WatchTower daemon is not running"
        detail = "no queue is being drained; start the service"
    return [{
        "id": "service:watchtower",
        "kind": "service_down",
        "severity": "critical",
        "queue": "WATCHTOWER",
        "engine": "",
        "model": "",
        "worker_id": "",
        "title": title,
        "detail": detail,
        "ts": since,
        "ts_iso": _iso(since),
        "age_seconds": max(0, int(now - since)),
        "count": 1,
        "cooldown_until": None,
        "cooldown_until_iso": None,
        "cooldown_active": False,
        "log": "",
        "session_id": "",
        "service_state": state or "stopped",
    }]


# ── acks ─────────────────────────────────────────────────────────────────────

def load_wt_alert_acks(path=None):
    path = Path(path or wt_alert_acks_path())
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _save_wt_alert_acks(acks, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(acks, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def ack_wt_alerts(ids, path=None, now=None):
    """Record an ack (now) for each id. Returns the ack map after the write."""
    now = time.time() if now is None else now
    path = Path(path or wt_alert_acks_path())
    ids = [str(i).strip() for i in (ids or []) if str(i or "").strip()]
    with _lock:
        acks = load_wt_alert_acks(path)
        for i in ids:
            acks[i] = now
        # Prune stale acks so the file never grows without bound.
        acks = {k: v for k, v in acks.items() if now - v < _ACK_RETENTION_S}
        _save_wt_alert_acks(acks, path)
    return acks


def _is_acked(alert, acks):
    acked_at = acks.get(alert.get("id"))
    if acked_at is None:
        return False
    ts = alert.get("ts") or 0
    # Hidden only while nothing newer than the ack has happened.
    return float(ts) <= float(acked_at)


# ── public entry point ───────────────────────────────────────────────────────

_SEVERITY_RANK = {"critical": 0, "error": 1, "warning": 2}


def collect_wt_alerts(*, wt_home=None, acks_path=None, service_status=None,
                      now=None, include_acked=False):
    """All current WatchTower alerts, un-acked first, severity then newest.

    Returns ``{"alerts": [...], "total": n, "acked": m}`` where ``alerts``
    is capped at ``_MAX_ALERTS`` and excludes acked entries unless
    ``include_acked`` is set (then each carries ``acked: bool``).
    """
    now = time.time() if now is None else now
    alerts = []
    alerts.extend(service_alerts(service_status=service_status, now=now))
    alerts.extend(launch_failure_alerts(wt_home=wt_home, now=now))
    alerts.extend(activity_error_alerts(wt_home=wt_home, now=now))
    acks = load_wt_alert_acks(acks_path)
    visible = []
    acked_n = 0
    for a in alerts:
        acked = _is_acked(a, acks)
        if acked:
            acked_n += 1
            if not include_acked:
                continue
        a = dict(a)
        a["acked"] = acked
        visible.append(a)
    visible.sort(key=lambda a: (
        1 if a.get("acked") else 0,
        _SEVERITY_RANK.get(a.get("severity"), 9),
        -float(a.get("ts") or 0),
    ))
    return {
        "alerts": visible[:_MAX_ALERTS],
        "total": len(alerts) - acked_n,
        "acked": acked_n,
    }
