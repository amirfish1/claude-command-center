# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Jobs feed: the outcome-oriented view of scheduled jobs behind the Jobs tab.

Sources (read-only; CCC surfaces scheduling, it does not own execution):
1. Hermes VM systemd timers whose ``.timer`` file lives in /etc/systemd/system
   (vendor timers live in /lib or /usr/lib and are ignored). Everything is
   collected with one local subprocess on Linux or one ssh round-trip elsewhere.
2. Laptop launchd agents that are actually scheduled (StartInterval or
   StartCalendarInterval). Always-on daemons (RunAtLoad/KeepAlive only) are
   dropped.

Serving model: ``get_jobs_feed()`` returns the cached payload immediately and
kicks a background refresh when the cache is older than the TTL, so a request
thread never waits on ssh (except the very first call, bounded).
"""

from __future__ import annotations

import os
import platform
import plistlib
import re
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ccc_server import scheduled_jobs as _sj

FEED_TTL_S = 45.0
SSH_CONNECT_TIMEOUT_S = 5
SSH_TOTAL_TIMEOUT_S = 25
FIRST_LOAD_WAIT_S = 8.0

_MARK = "@@CCCJOB:"

# Runs on the Hermes VM. One ssh, all units. Kept POSIX-ish (bash on Ubuntu).
REMOTE_SCRIPT = r"""
export LC_ALL=C SYSTEMD_COLORS=0 TZ=UTC
for t in /etc/systemd/system/*.timer; do
  [ -e "$t" ] || continue
  tn=${t##*/}
  echo "@@CCCJOB:TIMER $tn"
  systemctl show "$tn" -p Unit,UnitFileState,ActiveState,NextElapseUSecRealtime,LastTriggerUSec,TimersCalendar,TimersMonotonic 2>/dev/null
  svc=$(systemctl show "$tn" -p Unit --value 2>/dev/null)
  [ -n "$svc" ] || continue
  echo "@@CCCJOB:SVC"
  systemctl show "$svc" --timestamp=unix -p Description,ActiveState,Result,ExecMainStatus,ExecMainStartTimestamp,ExecMainExitTimestamp,InvocationID,WorkingDirectory,ExecStart 2>/dev/null
  echo "@@CCCJOB:HIST"
  journalctl -u "$svc" --since -7d _PID=1 -o short-unix --no-pager -q 2>/dev/null \
    | grep -E "Deactivated successfully|Failed with result" | cut -c1-400 | tail -n 60
  inv=$(systemctl show "$svc" -p InvocationID --value 2>/dev/null)
  echo "@@CCCJOB:OUT"
  if [ -n "$inv" ]; then
    journalctl "_SYSTEMD_INVOCATION_ID=$inv" -o cat --no-pager -q 2>/dev/null | tail -n 150 | cut -c1-400
  fi
  echo "@@CCCJOB:ENDUNIT"
done
echo "@@CCCJOB:END"
"""

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WD_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)([,.\-]|$)")


# ── small helpers ───────────────────────────────────────────────────────────

def _iso(epoch):
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def parse_systemd_ts(val):
    """'@1790697601' or 'Wed 2026-09-30 02:00:00 UTC' -> epoch seconds, or None."""
    if not val:
        return None
    val = val.strip()
    if val in ("", "0", "n/a", "(null)"):
        return None
    if val.startswith("@"):
        try:
            return float(val[1:])
        except ValueError:
            return None
    m = re.search(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})", val)
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1) + " " + m.group(2), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc).timestamp()  # remote runs with TZ=UTC


def human_seconds(sec):
    sec = int(sec)
    if sec % 86400 == 0 and sec >= 86400:
        return f"{sec // 86400}d"
    if sec % 3600 == 0 and sec >= 3600:
        return f"{sec // 3600}h"
    if sec % 60 == 0 and sec >= 60:
        return f"{sec // 60}m"
    return f"{sec}s"


_SYSTEMD_NOISE_RE = re.compile(
    r"^(Finished|Started|Starting|Stopped|Stopping|Deactivated|Consumed)\b|"
    r": (Deactivated successfully|Consumed .* CPU time|Failed with result)|"
    r"^\S+\.(service|timer): ")


def _authored(lines):
    out = [_ANSI_RE.sub("", ln).rstrip() for ln in lines]
    return [ln for ln in out if ln.strip() and not _SYSTEMD_NOISE_RE.search(ln.strip())]


# A job's own log() helper often stamps each line, so `CCC_OUTCOME:` may sit
# behind an ISO timestamp (bym-ship: "2026-09-30T14:27:37Z CCC_OUTCOME: ...").
_OUTCOME_RE = re.compile(
    r"^(?:\[?\d{4}-\d\d-\d\d[T ][\d:.]+(?:Z|[+-]\d\d:?\d\d)?\]?\s+)?CCC_OUTCOME:\s*(.*)$")


def pick_outcome(lines):
    """(text, kind): last CCC_OUTCOME: line -> ('..','summary'); else the last
    job-authored line -> ('..','output'); else ('', '')."""
    cleaned = _authored(lines)
    for ln in reversed(cleaned):
        m = _OUTCOME_RE.match(ln.strip())
        if m:
            return m.group(1).strip()[:300], "summary"
    return (cleaned[-1].strip()[:300], "output") if cleaned else ("", "")


_NOT_WT_PREFIX = {
    "UTF", "SHA", "ISO", "MD", "AES", "RSA", "HTTP", "HTTPS", "TLS", "SSL", "TCP", "UDP", "GPT",
    "CVE", "UTC", "GMT", "IPV", "RFC", "PDF", "JSON", "USD", "EUR", "HTML", "CSS", "UUID", "TS",
    "ES", "X", "PR", "HTTP", "ECMA", "WGS", "EXIT", "PID", "SIG", "ERR", "ENOENT", "TZ", "GB", "MB",
}
_WT_RE = re.compile(r"(?<![\w./#:=-])([A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*)-(\d{1,5})(?![\w.-]|/)")
_GH_RE = re.compile(r"https://github\.com/([\w.-]+)/([\w.-]+)/(pull|issues)/(\d+)")
_PR_RE = re.compile(r"\bPR #(\d+)")


def extract_tickets(lines, limit=12):
    """Ticket/PR/issue refs a run printed: GitHub URLs, bare 'PR #N', WatchTower refs."""
    text = "\n".join(_ANSI_RE.sub("", ln) for ln in lines)
    found, seen = [], set()

    def add(ref, kind, url=None, repo=None):
        key = (kind, ref, repo)
        if key in seen or len(found) >= limit:
            return
        seen.add(key)
        d = {"ref": ref, "kind": kind}
        if url:
            d["url"] = url
        if repo:
            d["repo"] = repo
        found.append(d)

    urls = {}
    for m in _GH_RE.finditer(text):
        owner, repo, typ, num = m.groups()
        kind = "pr" if typ == "pull" else "issue"
        urls[(kind, num)] = True
        add(f"PR #{num}" if kind == "pr" else f"#{num}", kind, m.group(0).rstrip(".,)"), f"{owner}/{repo}")
    for m in _PR_RE.finditer(text):
        if ("pr", m.group(1)) not in urls:
            add(f"PR #{m.group(1)}", "pr")
    stripped = _GH_RE.sub(" ", text)
    for m in _WT_RE.finditer(stripped):
        if m.group(1).split("-")[0] in _NOT_WT_PREFIX or len(m.group(1)) < 2:
            continue
        add(m.group(0), "watchtower")
    return found


def nodash(s):
    """No em-dashes in user copy: ' \u2014 ' -> ': '."""
    return (s or "").replace(" \u2014 ", ": ").replace("\u2014", ":")


_PROJ_RE = re.compile(r"/(?:Apps|dev|opt|srv|repos|src|projects|code)/([^/]+)")
_CONTAINER_DIRS = {"tools", "_test-demos", "scratch", "clients"}


def project_from_paths(paths):
    """(project, repo_path) from candidate absolute paths; ('Other', None) if unknown."""
    for path in paths:
        if not path or not path.startswith("/"):
            continue
        m = _PROJ_RE.search(path)
        if m:
            name, end = m.group(1), m.end()
            if name in _CONTAINER_DIRS:
                nxt = re.match(r"/([^/]+)", path[end:])
                if nxt and "." not in nxt.group(1)[-4:]:
                    name, end = nxt.group(1), end + nxt.end()
            return name, path[:end]
    return "Other", None


def _fmt_hhmm(hour, minute, tzname, ref=None):
    """Convert a wall-clock time in tzname (None = UTC) to laptop-local HH:MM."""
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tzname) if tzname and tzname != "UTC" else timezone.utc
        base = ref or datetime.now(timezone.utc)
        dt = datetime(base.year, base.month, base.day, hour, minute, tzinfo=tz)
        loc = dt.astimezone()
        return f"{loc.hour:02d}:{loc.minute:02d}"
    except Exception:
        return f"{hour:02d}:{minute:02d}"


def _parse_calendar_spec(spec):
    """Split an OnCalendar spec into (weekday|None, date|None, time|None, tz|None)."""
    wd = date = tm = tz = None
    for tok in spec.split():
        if tm is None and ":" in tok:
            tm = tok
        elif date is None and re.match(r"^[\d*]+-[\d*]+(-[\d*/.,]+)?$", tok):
            date = tok
        elif wd is None and _WD_RE.match(tok):
            wd = tok
        elif tz is None:
            tz = tok
    return wd, date, tm, tz


def format_systemd_schedule(timers_calendar, timers_monotonic=None):
    """Readable schedule from `TimersCalendar=`/`TimersMonotonic=` show lines."""
    specs = []
    for line in timers_calendar or []:
        m = re.search(r"OnCalendar=(.*?)\s*;", line)
        if m:
            specs.append(m.group(1).strip())
    if specs:
        groups = {}  # weekday label -> [times]
        raw = []
        for spec in specs:
            wd, date, tm, tz = _parse_calendar_spec(spec)
            if date not in (None, "*-*-*"):
                raw.append(spec)
                continue
            tmm = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", tm or "")
            if tmm:
                label = None
                if wd:
                    label = wd.replace("..", "-")
                groups.setdefault(label, []).append(
                    _fmt_hhmm(int(tmm.group(1)), int(tmm.group(2)), tz))
                continue
            step = re.match(r"^\*:(\d+)/(\d+)(?::\d{2})?$", tm or "")
            if step:
                return f"Every {step.group(2)}m"
            hourly = re.match(r"^\*:(\d{2})(?::\d{2})?$", tm or "")
            if hourly:
                return f"Hourly :{hourly.group(1)}"
            raw.append(spec)
        parts = []
        for label, times in groups.items():
            times = sorted(set(times))
            if label is None:
                parts.append("Daily " + ", ".join(times))
            elif "-" in label or "," in label:
                parts.append(f"{label} " + ", ".join(times))
            else:
                parts.append(f"Weekly {label} " + ", ".join(times))
        parts.extend(raw)
        return "; ".join(parts)
    for line in timers_monotonic or []:
        m = re.search(r"On(\w+?)USec=(.*?)\s*;", line)
        if m:
            kind, val = m.group(1), m.group(2).strip()
            if kind in ("UnitActive", "UnitInactive"):
                return f"Every {val}"
            if kind == "Boot":
                return f"{val} after boot"
            return f"{val} after {kind.lower()}"
    return "On demand"



def _local_minutes(hour, minute, tzname):
    hhmm = _fmt_hhmm(hour, minute, tzname)
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def systemd_timeline(timers_calendar, timers_monotonic=None):
    """Structured schedule for the 24h strip: {kind, minutes[], weekdays[], interval_s}."""
    minutes, weekdays, interval = [], [], None
    for line in timers_calendar or []:
        m = re.search(r"OnCalendar=(.*?)\s*;", line)
        if not m:
            continue
        wd, date, tm, tz = _parse_calendar_spec(m.group(1).strip())
        if wd and wd not in weekdays:
            weekdays.append(wd.replace("..", "-"))
        tmm = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", tm or "")
        step = re.match(r"^\*:(?:\d+)/(\d+)(?::\d{2})?$", tm or "")
        if tmm:
            minutes.append(_local_minutes(int(tmm.group(1)), int(tmm.group(2)), tz))
        elif step:
            interval = int(step.group(1)) * 60
        elif re.match(r"^\*:\d{2}", tm or ""):
            interval = 3600
    if not minutes and interval is None:
        for line in timers_monotonic or []:
            m = re.search(r"OnUnitActiveUSec=(.*?)\s*;", line)
            if m:
                interval = _parse_span_s(m.group(1))
                break
    if interval:
        return {"kind": "interval", "interval_s": interval, "minutes": [], "weekdays": []}
    if minutes:
        return {"kind": "times", "minutes": sorted(set(minutes)), "weekdays": weekdays, "interval_s": None}
    return {"kind": "none", "minutes": [], "weekdays": [], "interval_s": None}


def _parse_span_s(val):
    """'1h 30min' / '5min' / '30s' -> seconds (None if unparseable)."""
    total, hit = 0, False
    for n, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(d|h|min|m|s)\b", val or ""):
        hit = True
        total += float(n) * {"d": 86400, "h": 3600, "min": 60, "m": 60, "s": 1}[unit]
    return int(total) if hit and total else None


# ── Hermes parsing ──────────────────────────────────────────────────────────

def _parse_show(block_lines):
    """systemctl show output -> dict; TimersCalendar/TimersMonotonic -> lists."""
    d = {}
    for ln in block_lines:
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        if k in ("TimersCalendar", "TimersMonotonic"):
            d.setdefault(k, []).append(ln)
        else:
            d[k] = v.strip()
    return d


def parse_hermes_output(text, now=None):
    """Parse the REMOTE_SCRIPT stdout into job dicts (pure; no I/O)."""
    now = now if now is not None else time.time()
    units = []
    cur = None
    section = None
    for ln in text.splitlines():
        if ln.startswith(_MARK):
            tag = ln[len(_MARK):]
            if tag.startswith("TIMER "):
                cur = {"timer": tag[6:].strip(), "TIMER": [], "SVC": [], "HIST": [], "OUT": []}
                units.append(cur)
                section = "TIMER"
            elif tag in ("SVC", "HIST", "OUT"):
                section = tag
            elif tag == "ENDUNIT":
                section = None
            continue
        if cur is not None and section:
            cur[section].append(ln)

    jobs = []
    for u in units:
        t = _parse_show(u["TIMER"])
        s = _parse_show(u["SVC"])
        svc = t.get("Unit") or u["timer"].replace(".timer", ".service")
        name = svc[:-8] if svc.endswith(".service") else svc
        timer_enabled = t.get("UnitFileState") == "enabled"

        history = []
        for ln in u["HIST"]:
            parts = ln.split(None, 1)
            if len(parts) < 2:
                continue
            try:
                at = float(parts[0])
            except ValueError:
                continue
            history.append({"at": _iso(at), "ok": "Deactivated successfully" in parts[1]})
        history = history[-30:]

        start = parse_systemd_ts(s.get("ExecMainStartTimestamp")) or parse_systemd_ts(t.get("LastTriggerUSec"))
        exit_ts = parse_systemd_ts(s.get("ExecMainExitTimestamp"))
        duration = round(exit_ts - start, 1) if (start and exit_ts and exit_ts >= start) else None
        nxt = parse_systemd_ts(t.get("NextElapseUSecRealtime"))
        exit_code = int(s["ExecMainStatus"]) if s.get("ExecMainStatus", "").isdigit() else None
        result = s.get("Result", "")
        svc_active = s.get("ActiveState", "")

        if not timer_enabled:
            status = "disabled"
        elif svc_active in ("active", "activating", "reloading"):
            status = "running"
        elif svc_active == "failed" or (result and result != "success"):
            status = "failed"
        elif history and not history[-1]["ok"]:
            status = "failed"
        elif nxt is None:
            status = "stale"
        else:
            status = "ok"

        outcome, outcome_kind = pick_outcome(u["OUT"])
        wd = (s.get("WorkingDirectory") or "").lstrip("!-")
        exec_paths = re.findall(r"(?:path=|argv\[\]=|\s)(/[^\s;]+)", s.get("ExecStart") or "")
        exec_paths = [x for x in exec_paths if not x.startswith(("/bin", "/usr", "/sbin"))]
        project, repo_path = project_from_paths([wd] + exec_paths)
        if project == "Other" and wd and wd not in ("/", "~") and wd.startswith("/") and wd.count("/") > 1:
            project, repo_path = wd.rstrip("/").rsplit("/", 1)[-1], wd

        jobs.append({
            "id": f"hermes:{svc}",
            "name": name,
            "host": "hermes",
            "manager": "systemd",
            "description": nodash(s.get("Description", "")),
            "project": project,
            "repo_path": repo_path,
            "schedule": format_systemd_schedule(t.get("TimersCalendar"), t.get("TimersMonotonic")),
            "timeline": systemd_timeline(t.get("TimersCalendar"), t.get("TimersMonotonic")),
            "enabled": timer_enabled,
            "status": status,
            "last_run_at": _iso(start),
            "last_duration_s": duration,
            "exit_code": exit_code,
            "next_run_at": _iso(nxt) if timer_enabled else None,
            "history": history,
            "outcome": outcome,
            "outcome_kind": outcome_kind,
            "tickets": extract_tickets(u["OUT"]),
        })
    return jobs


def collect_hermes(timeout_s=SSH_CONNECT_TIMEOUT_S):
    """One local Linux subprocess or SSH round-trip. Returns (jobs, error)."""
    try:
        res = subprocess.run(
            _sj._systemd_command(["bash", "-s"], timeout_s),
            input=REMOTE_SCRIPT, capture_output=True, text=True,
            timeout=SSH_TOTAL_TIMEOUT_S, check=False,
        )
    except Exception as e:
        return None, str(e)
    if res.returncode != 0 or (_MARK + "END") not in res.stdout:
        return None, (res.stderr.strip() or f"ssh exited with code {res.returncode}")[:300]
    return parse_hermes_output(res.stdout), None


# ── Laptop ──────────────────────────────────────────────────────────────────

_WEEKDAY_NAMES = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}



_INTERPRETERS = {"bash", "sh", "zsh", "env", "python", "python3", "node", "caffeinate", "nice", "open", "osascript", "ruby", "perl", "uv", "npx"}
_SCRIPT_DESC_CACHE = {}  # path -> (mtime, text)


def _expand(path):
    return os.path.expanduser(path.replace("$HOME", "~")) if path else path


def launchd_program_paths(data):
    """ProgramArguments minus the launchd-jobs wrapper and its label argument."""
    args = data.get("ProgramArguments") or ([data["Program"]] if data.get("Program") else [])
    label = data.get("Label")
    out = []
    for a in args:
        if not isinstance(a, str):
            continue
        if a == label or a.startswith("com.") and "/" not in a:
            continue
        a = _expand(a)
        if os.path.basename(os.path.dirname(a)) == "launchd-jobs":
            continue
        out.append(a)
    return out


def script_path(data):
    """First ProgramArguments entry that is a script/binary, not an interpreter or flag."""
    for a in launchd_program_paths(data):
        base = os.path.basename(a)
        if a.startswith("-") or base in _INTERPRETERS or base.rstrip("0123456789.") in _INTERPRETERS:
            continue
        if "/" in a or re.search(r"\.(sh|py|js|mjs|rb|pl)$", a):
            return a
    return None


def _script_first_comment(path):
    """One-line purpose from a script's leading comment/docstring (mtime-cached)."""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    hit = _SCRIPT_DESC_CACHE.get(path)
    if hit and hit[0] == st.st_mtime:
        return hit[1]
    text = ""
    try:
        with open(path, "rb") as fb:
            raw = fb.read(2048)
        if b"\0" in raw:
            return ""
        head = raw.decode("utf-8", "replace").splitlines()[:25]
        for i, ln in enumerate(head):
            t = ln.strip()
            if not t or t.startswith("#!") or t.startswith("# -*-") or t.startswith("# Copyright") or t.startswith("# SPDX"):
                continue
            if t.startswith("#") and not t.startswith("#!/"):
                t = t.lstrip("#").strip()
            elif t.startswith(('"""', "\'\'\'")):
                t = t.strip("\"'").strip()
            elif t.startswith("//"):
                t = t.lstrip("/").strip()
            else:
                break
            if t and not t.lower().startswith(("usage", "set ", "shellcheck")):
                text = re.sub(r"^[\w./-]+\.(sh|py|js|mjs)\s*[:\u2014-]+\s*", "", t)
                break
    except Exception:
        text = ""
    text = nodash(text)[:110]
    _SCRIPT_DESC_CACHE[path] = (st.st_mtime, text)
    return text


def launchd_description(data):
    if data.get("Comment"):
        return nodash(str(data["Comment"]))[:110]
    sp = script_path(data)
    if not sp:
        return "Runs a shell command"
    base = os.path.basename(sp)
    if re.search(r"\.(sh|py|js|mjs|rb|pl)$", base):
        got = _script_first_comment(sp)
        if got:
            return got
    args = launchd_program_paths(data)
    rest = [a for a in args[args.index(sp) + 1:] if not a.startswith("-")][:1] if sp in args else []
    return "Runs " + " ".join([base] + rest)


def launchd_timeline(data):
    interval = data.get("StartInterval")
    if interval:
        try:
            return {"kind": "interval", "interval_s": int(interval), "minutes": [], "weekdays": []}
        except Exception:
            pass
    cal = data.get("StartCalendarInterval")
    items = [cal] if isinstance(cal, dict) else [c for c in (cal or []) if isinstance(c, dict)]
    minutes, weekdays, hourly = [], [], False
    for it in items:
        h, m = it.get("Hour"), it.get("Minute", 0)
        if isinstance(h, int) and isinstance(m, int):
            minutes.append(h * 60 + m)
        else:
            hourly = True
        wd = it.get("Weekday")
        if wd is not None and _WEEKDAY_NAMES.get(wd) and _WEEKDAY_NAMES[wd] not in weekdays:
            weekdays.append(_WEEKDAY_NAMES[wd])
    if hourly and not minutes:
        return {"kind": "interval", "interval_s": 3600, "minutes": [], "weekdays": []}
    if minutes:
        return {"kind": "times", "minutes": sorted(set(minutes)), "weekdays": weekdays, "interval_s": None}
    return {"kind": "none", "minutes": [], "weekdays": [], "interval_s": None}


def is_scheduled_plist(data):
    return isinstance(data, dict) and bool(data.get("StartInterval") or data.get("StartCalendarInterval"))


def format_launchd_schedule(data):
    interval = data.get("StartInterval")
    if interval:
        try:
            return "Every " + human_seconds(int(interval))
        except Exception:
            pass
    cal = data.get("StartCalendarInterval")
    items = [cal] if isinstance(cal, dict) else [c for c in (cal or []) if isinstance(c, dict)]
    groups = {}
    for it in items:
        h, m = it.get("Hour"), it.get("Minute", 0)
        wd = it.get("Weekday")
        label = _WEEKDAY_NAMES.get(wd) if wd is not None else (f"day {it['Day']}" if it.get("Day") else None)
        if isinstance(h, int) and isinstance(m, int):
            groups.setdefault(label, []).append(f"{h:02d}:{m:02d}")
        elif isinstance(m, int):
            groups.setdefault(label, []).append(f":{m:02d}")
    parts = []
    for label, times in groups.items():
        times = sorted(set(times))
        if all(t.startswith(":") for t in times):
            parts.append("Hourly " + ", ".join(times))
        elif label is None:
            parts.append("Daily " + ", ".join(times))
        elif label.startswith("day"):
            parts.append(f"Monthly {label} " + ", ".join(times))
        else:
            parts.append(f"Weekly {label} " + ", ".join(times))
    return "; ".join(parts) or "Scheduled"


def _expected_period_s(data):
    interval = data.get("StartInterval")
    if interval:
        try:
            return int(interval)
        except Exception:
            return 86400
    cal = data.get("StartCalendarInterval")
    items = [cal] if isinstance(cal, dict) else [c for c in (cal or []) if isinstance(c, dict)]
    if items and all(i.get("Day") for i in items):
        return 31 * 86400
    if items and all(i.get("Weekday") is not None for i in items):
        return 7 * 86400
    if items and all(i.get("Hour") is None for i in items):
        return 3600
    return 86400


def laptop_status(*, pid, exit_code, loaded, last_run_epoch, period_s, now):
    """running / failed / disabled / stale / ok / unknown for a scheduled agent."""
    if not loaded:
        return "disabled"
    if pid is not None:
        return "running"
    if exit_code not in (None, 0):
        return "failed"
    if last_run_epoch is None:
        return "unknown"
    limit = 3 * period_s if period_s < 86400 else 2 * period_s
    return "stale" if (now - last_run_epoch) > limit else "ok"


def collect_laptop(now=None):
    """Scheduled launchd agents only. One `launchctl list`, no per-row subprocess."""
    now = now if now is not None else time.time()
    lc = {}
    try:
        proc = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=4, check=False)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    lc[parts[2]] = {
                        "pid": int(parts[0]) if parts[0].isdigit() else None,
                        "status": int(parts[1]) if parts[1].lstrip("-").isdigit() else None,
                    }
    except Exception:
        pass
    d = Path.home() / "Library" / "LaunchAgents"
    jobs = []
    if not d.is_dir():
        return jobs
    for p in sorted(d.glob("*.plist")):
        if p.name.startswith(("com.apple.", "com.google.")):
            continue
        try:
            with open(p, "rb") as f:
                data = plistlib.load(f)
        except Exception:
            continue
        if not is_scheduled_plist(data):
            continue
        label = data.get("Label") or p.stem
        info = lc.get(label)
        logs = [x for x in (data.get("StandardOutPath"), data.get("StandardErrorPath")) if x]
        last_run = None
        outcome, outcome_kind, tickets = "", "", []
        for lp in logs:
            try:
                path = Path(lp).expanduser()
                mt = path.stat().st_mtime
            except Exception:
                continue
            if last_run is None or mt > last_run:
                last_run = mt
                tail = _sj._read_file_tail(path, max_lines=20)
                if tail and tail.strip():
                    outcome, outcome_kind = pick_outcome(tail.splitlines())
                    tickets = extract_tickets(tail.splitlines())
        exit_code = info.get("status") if info else None
        status = laptop_status(
            pid=info.get("pid") if info else None, exit_code=exit_code, loaded=info is not None,
            last_run_epoch=last_run, period_s=_expected_period_s(data), now=now)
        wd = _expand(data.get("WorkingDirectory") or "")
        project, repo_path = project_from_paths(launchd_program_paths(data) + [wd])
        if project == "Other" and wd.startswith("/") and wd.count("/") > 2 and wd != str(Path.home()):
            project, repo_path = wd.rstrip("/").rsplit("/", 1)[-1], wd
        jobs.append({
            "id": f"laptop:{label}",
            "name": label,
            "host": "laptop",
            "manager": "launchd",
            "description": launchd_description(data),
            "project": project,
            "repo_path": repo_path,
            "schedule": format_launchd_schedule(data),
            "timeline": launchd_timeline(data),
            "enabled": info is not None,
            "status": status,
            "last_run_at": _iso(last_run),
            "last_duration_s": None,
            "exit_code": exit_code,
            "next_run_at": None,
            "history": [],
            "outcome": outcome,
            "outcome_kind": outcome_kind,
            "tickets": tickets,
        })
    return jobs


# ── Ordering + payload ──────────────────────────────────────────────────────

def sort_jobs(jobs):
    """Most recent run first; disabled at the bottom. (The UI groups by project.)"""
    return sorted(jobs, key=lambda j: (
        j["status"] == "disabled",
        # newest first: invert the ISO string ordering via negative epoch
        -(datetime.fromisoformat(j["last_run_at"]).timestamp() if j.get("last_run_at") else 0),
        j["name"]))


def summarize(jobs):
    def n(s):
        return sum(1 for j in jobs if j["status"] == s)
    return {
        "total": len(jobs), "failed": n("failed"), "stale": n("stale"), "running": n("running"),
        "ok": n("ok"), "disabled": n("disabled"),
        "attention": n("failed") + n("stale"),
    }


_LOCK = threading.Lock()
_STATE = {
    "hermes": {"jobs": [], "at": None, "ok_at": None, "error": None},
    "laptop": {"jobs": [], "at": None},
    "started": False,
}
_INFLIGHT = threading.Event()
_FIRST = threading.Event()


def refresh_now():
    """Collect both hosts (one ssh) and update state. Keeps last-good Hermes data on failure."""
    laptop = collect_laptop()
    hermes, err = collect_hermes()
    now = time.time()
    with _LOCK:
        _STATE["laptop"] = {"jobs": laptop, "at": now}
        h = _STATE["hermes"]
        h["at"] = now
        if hermes is not None:
            h.update(jobs=hermes, ok_at=now, error=None)
        else:
            h["error"] = err
    _FIRST.set()


def _refresh_thread():
    try:
        refresh_now()
    finally:
        _INFLIGHT.clear()
        _FIRST.set()


def build_payload(now=None):
    now = now if now is not None else time.time()
    with _LOCK:
        h = dict(_STATE["hermes"])
        lp = dict(_STATE["laptop"])
    h_online = h["at"] is not None and h["error"] is None
    hosts = {
        "hermes": {
            "status": "online" if h_online else ("offline" if h["at"] is not None else "loading"),
            "error": h["error"],
            "collected_at": _iso(h["ok_at"]),
            "age_s": round(now - h["ok_at"]) if h["ok_at"] else None,
            "stale_data": bool(h["ok_at"]) and not h_online,
            "jobs_count": len(h["jobs"]),
        },
        "laptop": {
            "status": "online", "error": None,
            "collected_at": _iso(lp["at"]),
            "age_s": round(now - lp["at"]) if lp["at"] else None,
            "stale_data": False, "jobs_count": len(lp["jobs"]),
        },
    }
    jobs = sort_jobs(list(h["jobs"]) + list(lp["jobs"]))
    return {
        "ok": True,
        "collected_at": _iso(now),
        "hosts": hosts,
        # Which of the two hosts is THIS machine: on Linux the systemd timers
        # are local and there is no launchd, so the UI hides the Laptop view.
        "local_host": "hermes" if platform.system() == "Linux" else "laptop",
        "jobs": jobs,
        "summary": {
            "total": len(jobs),
            "hermes": summarize(h["jobs"]),
            "laptop": summarize(lp["jobs"]),
            "all": summarize(jobs),
        },
    }


def get_jobs_feed(wait_s=FIRST_LOAD_WAIT_S, force=False):
    """Cached payload; refreshes in the background when older than FEED_TTL_S."""
    now = time.time()
    start = False
    with _LOCK:
        at = _STATE["hermes"]["at"]
        if (force or at is None or (now - at) > FEED_TTL_S) and not _INFLIGHT.is_set():
            _INFLIGHT.set()
            start = True
    if force or at is None or (now - at) > FEED_TTL_S:
        if start:
            threading.Thread(target=_refresh_thread, name="jobs-feed-refresh", daemon=True).start()
        if at is None:
            _FIRST.wait(timeout=wait_s)
    return build_payload()
