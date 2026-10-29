"""WatchTower error alerts for the Queue panel (stdlib-only).

Problem this solves: a queue can silently stop draining because a worker
failed to launch (engine usage limit, auth expired, API down, missing binary),
because WatchTower's own daemon died, or because a backend call keeps failing
(``gh`` auth lost, GitHub rate limit). WatchTower records all of these -- but
in places nobody looks at: ``~/.watchtower/launch-failures.json`` and
``ERROR`` lines buried in a multi-MB ``activity.log``. This module folds them
into one small, ack-able list the dashboard shows above the queue.

Four sources, all cheap (no subprocess, no O(all sessions) work):

* ``launch-failures.json`` -- WatchTower's durable per-(queue, engine) record
  of the last launch failure plus its cooldown. WatchTower clears a record on
  the next successful launch, so a record that is still present is still an
  open problem.
* ``activity.log`` ``ERROR`` lines -- backend failures (GitHub list, etc.).
  Only the tail of the file is read (``_LOG_TAIL_BYTES``) and the parse is
  cached by ``(mtime, size)``. Identical messages per queue collapse into one
  alert with a count and a last-seen time.
* ``outbox.json`` dead ``notify`` messages -- a needs-input/status notice that
  cannot be delivered dies after 20 retries as a DEADMSG line nobody reads.
  The outbox keeps the dead record (with its ``[watchtower] REF verb`` text),
  so the strip can surface it until acked. Later notices and the existing
  ticket snapshot clear superseded requests without fetching ticket state.
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
# Activity-log ERRORs are historical observations, unlike launch-failure
# records which persist until WatchTower reports a recovery. Keep one visible
# only while it is actively recurring; otherwise a recovered transient stays
# out of the queue panel and remains available in activity.log.
_ACTIVITY_ERROR_FRESH_S = 15 * 60
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
# outbox.json dead-notify parse cache: same (mtime, size) keying as the log.
_outbox_cache = {"key": None, "alerts": []}
_ticket_snapshot_cache = {"key": None, "items": {}}
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
    cutoff = now - _ACTIVITY_ERROR_FRESH_S
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
        # The same dependency failure (for example lost `gh` auth) can hit
        # several GitHub-backed queues at once. One alert with its affected
        # queue list is actionable; a stack of copies is not.
        key = _short_hash(msg.lower())
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "title": _error_headline(detail),
                "detail": msg,
                "first_ts": ts,
                "ts": ts,
                "count": 0,
                "queues": set(),
            }
        g["count"] += 1
        g["queues"].add(queue)
        if ts > g["ts"]:
            g["ts"] = ts
        if ts < g["first_ts"]:
            g["first_ts"] = ts
    out = []
    for h, g in groups.items():
        queues = sorted(g["queues"])
        queue = queues[0] if len(queues) == 1 else "WATCHTOWER"
        out.append({
            "id": "log:%s:%s" % (queue, h),
            "kind": "activity_error",
            "severity": "warning",
            "queue": queue,
            "queues": queues,
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
                    if a["ts"] >= now - _ACTIVITY_ERROR_FRESH_S]
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


# ── source 3: outbox.json dead notify messages ─────────────────────────────

# Ticket-event notices are sent as "[watchtower] REF verb — detail" with
# notify=True (watchtower/queue.py _notify_ticket_event). A dead one proves
# delivery failed, not that its recipient is gone or its question still open.
_DEAD_NOTIFY_RE = re.compile(r"^\[watchtower\]\s+(\S+)\s+(.*?)(?:\s*—\s*|$)")
# Verbs that hand something to a human get "Needs your input" wording; a dead
# claimed/closed echo is informational, not a question.
_NEEDS_INPUT_VERBS = ("needs input", "awaits product decision")


def _iso_to_epoch(value):
    try:
        return datetime.strptime(
            str(value or ""), "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


def _dead_notify_ts(msg):
    """Approximate time the message died: last scheduled attempt, TTL expiry,
    or creation -- whichever exists."""
    for field in ("next_attempt_at", "expires_at", "created_at"):
        ts = _iso_to_epoch(msg.get(field))
        if ts:
            return ts
    return 0.0


def _ticket_snapshot_path():
    try:
        return Path(_core._WT_ITEMS_SNAPSHOT_FILE)
    except Exception:
        return Path.home() / ".claude" / "command-center" / "wt_items_snapshot.json"


def _alert_ticket_snapshot(path):
    """Read the existing display snapshot, never fetch tickets on an alert poll.

    A missing ticket is unknown (snapshots can be partial). Cache the small
    ref/state index by file identity; do not retain full ticket histories.
    """
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return {}
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        if _ticket_snapshot_cache["key"] == key:
            return _ticket_snapshot_cache["items"]
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    items = {
        str(row["ref"]): {k: row.get(k) for k in
                          ("status", "needs_input", "updated_at")}
        for row in rows if isinstance(row, dict) and row.get("ref")
    }
    with _lock:
        _ticket_snapshot_cache.update(key=key, items=items)
    return items


def _current_dead_notices(alerts, items, now):
    out = []
    for a in alerts:
        ref = a["id"].removeprefix("dead-notify:")
        item = items.get(ref) or {}
        # Compare to the EVENT time, not the last retry: a resolved question
        # can continue failing delivery for hours. Never let an older snapshot
        # hide a newer request after a ticket was reopened.
        event_ts = a["_event_ts"]
        if event_ts and _iso_to_epoch(item.get("updated_at")) >= event_ts:
            if a["_needs_input"] and (
                item.get("status") == "closed"
                or (item.get("status") in ("open", "in_progress", "in_review")
                    and item.get("needs_input") is False)
            ):
                continue
        out.append({k: v for k, v in a.items() if not k.startswith("_")})
        out[-1]["age_seconds"] = max(0, int(now - a["ts"])) if a["ts"] else None
    return out


def dead_notify_alerts(wt_home=None, now=None, items_path=None):
    """Failed notices that have not been superseded, grouped per ticket ref.

    A later notice supersedes an older event even while pending or delivered.
    An input request also clears when a newer ticket snapshot shows it no
    longer needs input. Unknown state stays visible; age alone never clears it.
    """
    now = time.time() if now is None else now
    path = Path(wt_home or _wt_home()) / "outbox.json"
    try:
        st = path.stat()
    except OSError:
        return []
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _lock:
        cached = _outbox_cache["alerts"] if _outbox_cache["key"] == key else None
    if cached is not None:
        items = _alert_ticket_snapshot(items_path or _ticket_snapshot_path()) if cached else {}
        return _current_dead_notices(cached, items, now)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return []
    notices = []
    latest = {}
    for m in messages:
        if not isinstance(m, dict) or not m.get("notify"):
            continue
        if m.get("status") not in ("dead", "pending", "delivered"):
            continue
        parsed = _DEAD_NOTIFY_RE.match(str(m.get("text") or "").strip())
        ref = parsed.group(1) if parsed else ""
        verb = (parsed.group(2) or "").strip() if parsed else ""
        event_ts = _iso_to_epoch(m.get("created_at"))
        notices.append((m, ref, verb, event_ts))
        if ref:
            latest[ref] = max(latest.get(ref, 0), event_ts)
    groups = {}
    for m, ref, verb, event_ts in notices:
        if m.get("status") != "dead":
            continue
        if ref and event_ts and event_ts < latest[ref]:
            continue
        key_ref = ref or str(m.get("id") or "?")
        g = groups.get(key_ref)
        ts = _dead_notify_ts(m)
        if g is None:
            g = groups[key_ref] = {"ref": ref, "verb": verb, "ts": ts,
                                   "event_ts": event_ts, "count": 0}
        g["count"] += 1
        if ts > g["ts"]:
            g["ts"] = ts
            g["event_ts"] = event_ts
            if ref:
                g["ref"], g["verb"] = ref, verb
    out = []
    for key_ref, g in groups.items():
        ref = g["ref"]
        queue = ref.rsplit("-", 1)[0] if re.match(r"^.+-\d+$", ref) else ""
        needs_input = g["verb"] in _NEEDS_INPUT_VERBS
        title = ("Needs your input: " if needs_input else "Notice never delivered: ") + (ref or key_ref)
        out.append({
            "id": "dead-notify:%s" % key_ref,
            "kind": "dead_notify",
            "severity": "error" if needs_input else "warning",
            "queue": queue.upper() or "WATCHTOWER",
            "engine": "",
            "model": "",
            "worker_id": "",
            "title": title,
            "detail": "ticket notification could not be delivered",
            "ts": g["ts"],
            "ts_iso": _iso(g["ts"]),
            "first_ts_iso": _iso(g["ts"]),
            "count": g["count"],
            "cooldown_until": None,
            "cooldown_until_iso": None,
            "cooldown_active": False,
            "log": "",
            "session_id": "",
            "_event_ts": g["event_ts"],
            "_needs_input": needs_input,
        })
    with _lock:
        _outbox_cache["key"] = key
        _outbox_cache["alerts"] = out
    items = _alert_ticket_snapshot(items_path or _ticket_snapshot_path()) if out else {}
    return _current_dead_notices(out, items, now)


# ── source 4: WatchTower daemon state ───────────────────────────────────────

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


# ── investigate ──────────────────────────────────────────────────────────────

INVESTIGATE_QUEUE = "WATCHTOWER"


def investigate_wt_alert(alert_id, *, queue=INVESTIGATE_QUEUE, timeout=30):
    """File a WatchTower ticket asking a worker to investigate one alert.

    Idempotent per alert id (``wt add --dedupe-key``): a second click returns
    the same ticket instead of filing a duplicate. Returns ``{ok, ref, output}``.
    """
    import subprocess
    alert_id = str(alert_id or "").strip()
    alert = next((a for a in collect_wt_alerts(include_acked=True).get("alerts", [])
                  if a.get("id") == alert_id), None)
    if not alert:
        return {"ok": False, "error": "alert not found (it may have cleared)"}
    wt = _core._wt_cli_path()
    if not wt:
        return {"ok": False, "error": "wt CLI not found"}
    queues = ", ".join(str(q) for q in (alert.get("queues") or [alert.get("queue")]) if q)
    title = "Investigate WatchTower alert: " + str(alert.get("title") or alert_id)[:80]
    text = "\n".join(filter(None, [
        "Investigate and fix the root cause of this WatchTower error alert "
        "(filed from the Queue panel Investigate button).",
        "Queue(s): " + queues if queues else "",
        "Title: " + str(alert.get("title") or ""),
        "Detail: " + str(alert.get("detail") or ""),
        "Occurrences: " + str(alert.get("count") or 1),
        "Last seen: " + str(alert.get("ts_iso") or ""),
        "Log: " + str(alert.get("log") or ""),
        "Alert id: " + alert_id,
    ]))
    try:
        r = subprocess.run(
            [wt, "add", "-q", queue, "--title", title, "--text", text, "--type", "bug",
             "--dedupe-key", "wt-alert:" + alert_id],
            capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)}
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    if r.returncode != 0:
        return {"ok": False, "error": out[-300:] or "wt add failed"}
    m = re.search(r"\b[A-Z][A-Z0-9_-]*-\d+\b", out)
    return {"ok": True, "ref": m.group(0) if m else "", "queue": queue, "output": out[-300:]}


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
                      now=None, include_acked=False, items_path=None):
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
    alerts.extend(dead_notify_alerts(wt_home=wt_home, now=now, items_path=items_path))
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
