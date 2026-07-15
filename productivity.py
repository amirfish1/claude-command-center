"""Local productivity evidence, aggregation, and persistence for CCC.

The module is intentionally stdlib-only and does not import ``server``.  The
server supplies normalized turns and WatchTower items; this module owns the
portable calculations and Git collection.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import subprocess
import sys
import threading
import time
from collections import defaultdict
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


_CONVENTIONAL_RE = re.compile(r"^([A-Za-z]+)(?:\([^)]*\))?!?:\s+(.+)$")
_TICKET_REF_RE = re.compile(r"\b([A-Z][A-Z0-9_-]*-\d+)\b", re.IGNORECASE)
_HOME_PATH_RE = re.compile(
    r"(?:(?:/Users|/home)/[^/\s]+|[A-Za-z]:\\Users\\[^\\\s]+)"
    r"(?:[/\\][^\s,;:)}\]>]+)*"
)
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_RECORD_SEP = "\x1e"
_FIELD_SEP = "\x1f"
_IDLE_TIME_RE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')
_PRESENCE_HEALTH_LOCK = threading.Lock()
_PRESENCE_HEALTH = None


def classify_commit(subject: str) -> str:
    """Map a Conventional Commit subject to an outcome kind."""
    match = _CONVENTIONAL_RE.match(str(subject or "").strip())
    prefix = match.group(1).lower() if match else ""
    if prefix == "feat":
        return "feature"
    if prefix == "fix":
        return "fix"
    return "other"


def sanitize_evidence_title(value: str) -> str:
    """Remove personal filesystem/email details from browser evidence text."""
    text = str(value or "")
    text = _HOME_PATH_RE.sub("[local path]", text)
    text = _EMAIL_RE.sub("[email]", text)
    return " ".join(text.split()).strip()


def _parse_timestamp(value) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_git_log(text: str, identities: set[str], project: dict) -> list[dict]:
    """Parse one ``git log --numstat`` stream into authored commit evidence."""
    allowed = {str(email).strip().lower() for email in identities if str(email).strip()}
    rows = []
    seen = set()
    for record in str(text or "").split(_RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        header, _, numstat = record.partition("\n")
        fields = header.split(_FIELD_SEP, 4)
        if len(fields) != 5:
            continue
        sha, committed_at, _author_name, author_email, subject = fields
        sha = sha.strip()
        if not sha or sha in seen:
            continue
        if not allowed or author_email.strip().lower() not in allowed:
            continue
        added = deleted = 0
        for line in numstat.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            added += int(parts[0])
            deleted += int(parts[1])
        seen.add(sha)
        rows.append(
            {
                "sha": sha,
                "project_id": str(project.get("id") or ""),
                "project_name": str(project.get("name") or "Unknown project"),
                "committed_at": committed_at.strip(),
                "subject": subject.strip(),
                "kind": classify_commit(subject),
                "lines_added": added,
                "lines_deleted": deleted,
                "lines_changed": added + deleted,
            }
        )
    return rows


def _run_git(path: str | Path, args: list[str], timeout: int = 15) -> str:
    proc = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode:
        raise RuntimeError((proc.stderr or "git command failed").strip())
    return (proc.stdout or "").strip()


def _remote_identity(remote: str, root: str) -> str:
    raw = str(remote or "").strip()
    if raw:
        # Normalize both scp-like git@host:owner/repo and URL forms without
        # retaining credentials in the browser-facing identity.
        if ":" in raw and "://" not in raw:
            host, path = raw.split(":", 1)
            host = host.rsplit("@", 1)[-1]
        else:
            parsed = urlparse(raw)
            host = (parsed.hostname or "").lower()
            path = parsed.path
        path = path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if host and path:
            return f"{host}/{path}".lower()
    return f"local/{Path(root).resolve()}"


def describe_git_repo(path: str | Path) -> dict:
    """Return a private collection path and safe public project identity."""
    root = _run_git(path, ["rev-parse", "--show-toplevel"])
    try:
        remote = _run_git(root, ["config", "--get", "remote.origin.url"])
    except RuntimeError:
        remote = ""
    identity = _remote_identity(remote, root)
    name = Path(identity.split("/", 1)[-1]).name or Path(root).name or "Project"
    return {
        "path": root,
        "id": "project-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
        "name": name,
        "identity": identity,
    }


def discover_git_identities(repositories: list[dict]) -> set[str]:
    """Collect exact configured author emails across known repositories."""
    emails = set()
    for repo in repositories:
        try:
            email = _run_git(repo["path"], ["config", "user.email"])
        except (KeyError, RuntimeError, OSError, subprocess.SubprocessError):
            continue
        if email.strip():
            emails.add(email.strip().lower())
    return emails


def collect_git_commits(repo: dict, cutoff: datetime, identities: set[str]) -> list[dict]:
    """Collect authored commits that are currently reachable from remotes."""
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo["path"]),
            "log",
            "--remotes",
            "--since",
            cutoff.isoformat(),
            "--date=iso-strict",
            "--pretty=format:%x1e%H%x1f%cI%x1f%an%x1f%ae%x1f%s",
            "--numstat",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode:
        raise RuntimeError((proc.stderr or "git log failed").strip())
    return parse_git_log(proc.stdout, identities, repo)


def union_seconds(intervals: list[tuple[datetime, datetime]]) -> float:
    """Return wall-clock seconds covered by one or more intervals."""
    normalized = sorted(
        (start, end)
        for start, end in intervals
        if isinstance(start, datetime) and isinstance(end, datetime) and end > start
    )
    if not normalized:
        return 0.0
    total = 0.0
    current_start, current_end = normalized[0]
    for start, end in normalized[1:]:
        if start <= current_end:
            if end > current_end:
                current_end = end
            continue
        total += (current_end - current_start).total_seconds()
        current_start, current_end = start, end
    total += (current_end - current_start).total_seconds()
    return total


def estimate_work_intervals(
    prompt_times: list[datetime], gap_minutes: int = 30, tail_minutes: int = 5
) -> list[tuple[datetime, datetime]]:
    """Estimate observed work sessions from human-trigger timestamps."""
    times = sorted(set(item for item in prompt_times if isinstance(item, datetime)))
    if not times:
        return []
    sessions = []
    start = last = times[0]
    for current in times[1:]:
        if current - last > timedelta(minutes=gap_minutes):
            sessions.append((start, last + timedelta(minutes=tail_minutes)))
            start = current
        last = current
    sessions.append((start, last + timedelta(minutes=tail_minutes)))
    return sessions


def _split_interval_by_day(
    start: datetime, end: datetime, tzinfo
) -> list[tuple[str, datetime, datetime]]:
    start = start.astimezone(tzinfo)
    end = end.astimezone(tzinfo)
    rows = []
    cursor = start
    while cursor < end:
        next_midnight = datetime.combine(
            cursor.date() + timedelta(days=1), datetime_time.min, tzinfo=tzinfo
        )
        boundary = min(end, next_midnight)
        rows.append((cursor.date().isoformat(), cursor, boundary))
        cursor = boundary
    return rows


def _empty_metrics() -> dict:
    return {
        "features": 0,
        "fixes": 0,
        "deliveries": 0,
        "commits": 0,
        "lines_added": 0,
        "lines_deleted": 0,
        "lines_changed": 0,
        "turns": 0,
        "tokens": 0,
        "agent_gross_seconds": 0.0,
        "agent_net_seconds": 0.0,
        "agent_parallel_seconds": 0.0,
        "human_prompts": 0,
        "observed_work_seconds": 0.0,
        "computer_active_minutes": 0,
        "presence_samples": 0,
        "focus_hours": 0,
        "watchtower_opened": 0,
        "watchtower_closed": 0,
        "work_items": [],
    }


def _project_bucket(buckets: dict, project_id: str, project_name: str) -> dict:
    project_id = str(project_id or "unknown")
    if project_id not in buckets:
        buckets[project_id] = {
            "id": project_id,
            "name": str(project_name or "Unknown project"),
            **_empty_metrics(),
        }
    return buckets[project_id]


def _metric_add(bucket: dict, name: str, value) -> None:
    bucket[name] = bucket.get(name, 0) + value


def _ticket_refs(subject: str) -> set[str]:
    return {match.upper() for match in _TICKET_REF_RE.findall(str(subject or ""))}


def _delivery_evidence(commits: list[dict], tickets: list[dict], tzinfo) -> list[dict]:
    deliveries = []
    by_ref = {}
    for ticket in tickets:
        if ticket.get("status") != "closed" or ticket.get("kind") not in ("feature", "fix"):
            continue
        closed = _parse_timestamp(ticket.get("closed_at"))
        if closed is None:
            continue
        ref = str(ticket.get("ref") or "").strip().upper()
        delivery = {
            "id": f"watchtower:{ref}" if ref else f"watchtower:{len(deliveries)}",
            "date": closed.astimezone(tzinfo).date().isoformat(),
            "timestamp": closed.isoformat(),
            "project_id": str(ticket.get("project_id") or "unknown"),
            "project_name": str(ticket.get("project_name") or "Unknown project"),
            "kind": ticket["kind"],
            "title": sanitize_evidence_title(
                ticket.get("title") or ref or "Closed WatchTower ticket"
            ),
            "ref": ref,
            "sha": "",
            "sources": ["watchtower"],
        }
        deliveries.append(delivery)
        if ref:
            by_ref[ref] = delivery

    for commit in commits:
        kind = commit.get("kind")
        if kind not in ("feature", "fix"):
            continue
        committed = _parse_timestamp(commit.get("committed_at"))
        if committed is None:
            continue
        linked = None
        for ref in _ticket_refs(commit.get("subject") or ""):
            candidate = by_ref.get(ref)
            if candidate and candidate["kind"] == kind:
                linked = candidate
                break
        if linked:
            linked["sha"] = str(commit.get("sha") or "")[:12]
            linked["sources"] = ["git", "watchtower"]
            continue
        deliveries.append(
            {
                "id": f"git:{commit.get('sha')}",
                "date": committed.astimezone(tzinfo).date().isoformat(),
                "timestamp": committed.isoformat(),
                "project_id": str(commit.get("project_id") or "unknown"),
                "project_name": str(commit.get("project_name") or "Unknown project"),
                "kind": kind,
                "title": sanitize_evidence_title(
                    commit.get("subject") or "Untitled commit"
                ),
                "ref": "",
                "sha": str(commit.get("sha") or "")[:12],
                "sources": ["git"],
            }
        )
    deliveries.sort(key=lambda item: (item.get("timestamp") or "", item["title"]), reverse=True)
    return deliveries


def _least_squares_slope(values: list[float]) -> float:
    count = len(values)
    if count < 2:
        return 0.0
    mean_x = (count - 1) / 2
    mean_y = sum(values) / count
    denom = sum((index - mean_x) ** 2 for index in range(count))
    if not denom:
        return 0.0
    return sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values)) / denom


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 4:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    diff_left = [item - mean_left for item in left]
    diff_right = [item - mean_right for item in right]
    denom = math.sqrt(sum(item * item for item in diff_left) * sum(item * item for item in diff_right))
    if not denom:
        return None
    return sum(a * b for a, b in zip(diff_left, diff_right)) / denom


def _round_metrics(bucket: dict) -> dict:
    out = dict(bucket)
    for key in (
        "agent_gross_seconds",
        "agent_net_seconds",
        "agent_parallel_seconds",
        "observed_work_seconds",
    ):
        out[key] = round(float(out.get(key) or 0), 2)
    out["work_items"] = list(out.get("work_items") or [])
    return out


def _public_metrics(bucket: dict) -> dict:
    """Return numeric metrics; delivery evidence lives once at top level."""
    out = _round_metrics(bucket)
    out.pop("work_items", None)
    return out


def aggregate_productivity(
    *,
    commits: list[dict],
    turns: list[dict],
    tickets: list[dict],
    presence: list[dict],
    start_date: date,
    end_date: date,
    tzinfo=None,
) -> dict:
    """Aggregate normalized evidence into browser-safe daily/project trends."""
    tzinfo = tzinfo or datetime.now().astimezone().tzinfo or timezone.utc
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    daily = {}
    cursor = start_date
    while cursor <= end_date:
        daily[cursor.isoformat()] = {"date": cursor.isoformat(), **_empty_metrics()}
        cursor += timedelta(days=1)
    projects = {}
    all_agent_intervals = []
    daily_agent_intervals = defaultdict(list)
    project_agent_intervals = defaultdict(list)
    project_daily_agent_intervals = defaultdict(list)
    prompt_times = []
    project_prompt_times = defaultdict(list)

    filtered_commits = []
    seen_shas = set()
    for commit in commits:
        sha = str(commit.get("sha") or "")
        committed = _parse_timestamp(commit.get("committed_at"))
        if not sha or sha in seen_shas or committed is None:
            continue
        day_key = committed.astimezone(tzinfo).date().isoformat()
        if day_key not in daily:
            continue
        seen_shas.add(sha)
        filtered_commits.append(commit)
        project = _project_bucket(projects, commit.get("project_id"), commit.get("project_name"))
        for bucket in (daily[day_key], project):
            _metric_add(bucket, "commits", 1)
            for key in ("lines_added", "lines_deleted", "lines_changed"):
                _metric_add(bucket, key, int(commit.get(key) or 0))

    filtered_tickets = []
    for ticket in tickets:
        project = _project_bucket(projects, ticket.get("project_id"), ticket.get("project_name"))
        created = _parse_timestamp(ticket.get("created_at"))
        closed = _parse_timestamp(ticket.get("closed_at"))
        if created:
            day_key = created.astimezone(tzinfo).date().isoformat()
            if day_key in daily:
                _metric_add(daily[day_key], "watchtower_opened", 1)
                _metric_add(project, "watchtower_opened", 1)
        if ticket.get("status") == "closed" and closed:
            day_key = closed.astimezone(tzinfo).date().isoformat()
            if day_key in daily:
                _metric_add(daily[day_key], "watchtower_closed", 1)
                _metric_add(project, "watchtower_closed", 1)
                filtered_tickets.append(ticket)

    for turn in turns:
        start = _parse_timestamp(turn.get("t_start"))
        end = _parse_timestamp(turn.get("t_end"))
        if start is None or end is None or end <= start:
            continue
        project_id = str(turn.get("project_id") or "unknown")
        project = _project_bucket(projects, project_id, turn.get("project_name"))
        pieces = _split_interval_by_day(start, end, tzinfo)
        valid_pieces = [piece for piece in pieces if piece[0] in daily]
        if not valid_pieces:
            continue
        tokens = int(turn.get("tokens") or 0)
        total_seconds = (end - start).total_seconds()
        for day_key, piece_start, piece_end in valid_pieces:
            seconds = (piece_end - piece_start).total_seconds()
            share = seconds / total_seconds if total_seconds else 0
            _metric_add(daily[day_key], "agent_gross_seconds", seconds)
            _metric_add(project, "agent_gross_seconds", seconds)
            daily_agent_intervals[day_key].append((piece_start, piece_end))
            project_agent_intervals[project_id].append((piece_start, piece_end))
            project_daily_agent_intervals[(project_id, day_key)].append((piece_start, piece_end))
            _metric_add(daily[day_key], "tokens", round(tokens * share))
            _metric_add(project, "tokens", round(tokens * share))
        start_day = start.astimezone(tzinfo).date().isoformat()
        if start_day in daily:
            _metric_add(daily[start_day], "turns", 1)
            _metric_add(project, "turns", 1)
            if turn.get("human_trigger", True):
                local_start = start.astimezone(tzinfo)
                prompt_times.append(local_start)
                project_prompt_times[project_id].append(local_start)
                _metric_add(daily[start_day], "human_prompts", 1)
                _metric_add(project, "human_prompts", 1)
        all_agent_intervals.extend(
            (piece_start, piece_end)
            for _day_key, piece_start, piece_end in valid_pieces
        )

    for day_key, intervals in daily_agent_intervals.items():
        net = union_seconds(intervals)
        daily[day_key]["agent_net_seconds"] = net
        daily[day_key]["agent_parallel_seconds"] = max(
            0, daily[day_key]["agent_gross_seconds"] - net
        )
    for project_id, intervals in project_agent_intervals.items():
        net = union_seconds(intervals)
        projects[project_id]["agent_net_seconds"] = net
        projects[project_id]["agent_parallel_seconds"] = max(
            0, projects[project_id]["agent_gross_seconds"] - net
        )

    work_intervals = estimate_work_intervals(prompt_times)
    for start, end in work_intervals:
        for day_key, piece_start, piece_end in _split_interval_by_day(start, end, tzinfo):
            if day_key in daily:
                _metric_add(daily[day_key], "observed_work_seconds", (piece_end - piece_start).total_seconds())
    for project_id, times in project_prompt_times.items():
        projects[project_id]["observed_work_seconds"] = union_seconds(
            estimate_work_intervals(times)
        )

    active_minutes = defaultdict(set)
    sample_minutes = defaultdict(set)
    active_hour_minutes = defaultdict(set)
    for sample in presence:
        sampled = _parse_timestamp(sample.get("sampled_at"))
        if sampled is None:
            continue
        local = sampled.astimezone(tzinfo)
        day_key = local.date().isoformat()
        if day_key not in daily:
            continue
        minute_key = int(local.timestamp() // 60)
        sample_minutes[day_key].add(minute_key)
        if sample.get("active"):
            active_minutes[day_key].add(minute_key)
            active_hour_minutes[(day_key, local.hour)].add(local.minute)
    for day_key in daily:
        daily[day_key]["presence_samples"] = len(sample_minutes[day_key])
        daily[day_key]["computer_active_minutes"] = len(active_minutes[day_key])
        daily[day_key]["focus_hours"] = sum(
            1
            for (sample_day, _hour), minutes in active_hour_minutes.items()
            if sample_day == day_key and len(minutes) >= 45
        )

    deliveries = _delivery_evidence(filtered_commits, filtered_tickets, tzinfo)
    for delivery in deliveries:
        day_key = delivery["date"]
        project_id = delivery["project_id"]
        if day_key not in daily:
            continue
        project = _project_bucket(projects, project_id, delivery["project_name"])
        metric = "features" if delivery["kind"] == "feature" else "fixes"
        compact = {
            "kind": delivery["kind"],
            "title": delivery["title"],
            "project_id": project_id,
            "project_name": delivery["project_name"],
            "ref": delivery["ref"],
            "sha": delivery["sha"],
            "sources": delivery["sources"],
        }
        for bucket in (daily[day_key], project):
            _metric_add(bucket, metric, 1)
            _metric_add(bucket, "deliveries", 1)
            bucket["work_items"].append(compact)

    weekly = []
    # Use contiguous seven-day slices from the requested range.  Aligning to
    # calendar Mondays creates partial endpoint buckets (and N+1 buckets for
    # an N-week range), which biases the oldest/newest trend comparison.
    week_start = start_date
    while week_start <= end_date:
        bucket = {"week_start": week_start.isoformat(), **_empty_metrics()}
        for offset in range(7):
            row = daily.get((week_start + timedelta(days=offset)).isoformat())
            if not row:
                continue
            for key, value in row.items():
                if key in ("date", "work_items"):
                    continue
                if isinstance(value, (int, float)):
                    _metric_add(bucket, key, value)
            bucket["work_items"].extend(row["work_items"])
        weekly.append(_public_metrics(bucket))
        week_start += timedelta(days=7)

    summary = _empty_metrics()
    for row in daily.values():
        for key, value in row.items():
            if key in ("date", "agent_net_seconds", "agent_parallel_seconds", "work_items"):
                continue
            if isinstance(value, (int, float)):
                _metric_add(summary, key, value)
        summary["work_items"].extend(row["work_items"])
    summary["agent_net_seconds"] = union_seconds(all_agent_intervals)
    summary["agent_parallel_seconds"] = max(
        0, summary["agent_gross_seconds"] - summary["agent_net_seconds"]
    )
    summary["active_projects"] = sum(
        1
        for row in projects.values()
        if row["commits"] or row["turns"] or row["watchtower_opened"] or row["watchtower_closed"]
    )
    summary["delivery_per_million_tokens"] = round(
        summary["deliveries"] * 1_000_000 / summary["tokens"], 3
    ) if summary["tokens"] else None
    summary["delivery_per_work_hour"] = round(
        summary["deliveries"] * 3600 / summary["observed_work_seconds"], 3
    ) if summary["observed_work_seconds"] else None
    summary["agent_leverage"] = round(
        summary["agent_gross_seconds"] / summary["observed_work_seconds"], 3
    ) if summary["observed_work_seconds"] else None

    delivery_values = [row["deliveries"] for row in weekly]
    split = len(delivery_values) // 2
    old = delivery_values[:split]
    new = delivery_values[-split:] if split else []
    old_avg = sum(old) / len(old) if old else 0
    new_avg = sum(new) / len(new) if new else 0
    change_pct = None
    if old_avg:
        change_pct = round((new_avg - old_avg) * 100 / old_avg, 1)
    elif new_avg:
        change_pct = 100.0
    direction = "flat"
    if change_pct is not None and change_pct > 10:
        direction = "up"
    elif change_pct is not None and change_pct < -10:
        direction = "down"
    paired = [
        (float(row["agent_gross_seconds"]), float(row["deliveries"]))
        for row in weekly
        if row["agent_gross_seconds"] or row["deliveries"]
    ]
    correlation = _pearson(
        [item[0] for item in paired], [item[1] for item in paired]
    ) if paired else None
    trends = {
        "delivery_direction": direction,
        "delivery_change_pct": change_pct,
        "delivery_slope_per_week": round(_least_squares_slope(delivery_values), 3),
        "oldest_half_average": round(old_avg, 3),
        "newest_half_average": round(new_avg, 3),
        "agent_delivery_association": round(correlation, 3) if correlation is not None else None,
    }

    project_rows = []
    for project_id, row in projects.items():
        if not any(
            row.get(key)
            for key in ("commits", "turns", "watchtower_opened", "watchtower_closed", "deliveries")
        ):
            continue
        project_rows.append(_public_metrics(row))
    project_rows.sort(
        key=lambda row: (row["deliveries"], row["commits"], row["tokens"], row["name"]),
        reverse=True,
    )

    return {
        "ok": True,
        "range": {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "days": (end_date - start_date).days + 1,
        },
        "summary": _public_metrics(summary),
        "daily": [_public_metrics(daily[key]) for key in sorted(daily)],
        "weekly": weekly,
        "projects": project_rows,
        "deliveries": deliveries,
        "trends": trends,
    }


class ProductivityStore:
    """Small versioned SQLite store for the cached dashboard and idle samples."""

    SCHEMA_VERSION = 1
    CACHE_KEY = "full-16-weeks"

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()
        self._ensure_schema()

    def _connect(self):
        return sqlite3.connect(str(self.path), timeout=5)

    def _read_schema_version(self) -> int | None:
        if not self.path.is_file():
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()
            return int(row[0]) if row else None
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return None

    def _reset_files(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(self.path) + suffix).unlink()
            except FileNotFoundError:
                pass

    def _ensure_schema(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self._read_schema_version() != self.SCHEMA_VERSION:
                self._reset_files()
            with self._connect() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS meta ("
                    " key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS cache ("
                    " key TEXT PRIMARY KEY, generated_at REAL NOT NULL,"
                    " payload_json TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS presence ("
                    " minute_epoch INTEGER PRIMARY KEY, active INTEGER NOT NULL,"
                    " idle_seconds REAL, sampled_at REAL NOT NULL)"
                )
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    ("schema_version", str(self.SCHEMA_VERSION)),
                )

    def save_payload(self, payload: dict, generated_at: float | None = None) -> None:
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")
        generated_at = float(time.time() if generated_at is None else generated_at)
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO cache(key, generated_at, payload_json)"
                " VALUES (?, ?, ?)",
                (self.CACHE_KEY, generated_at, encoded),
            )

    def load_payload(self) -> dict | None:
        try:
            with self._lock, self._connect() as conn:
                row = conn.execute(
                    "SELECT generated_at, payload_json FROM cache WHERE key = ?",
                    (self.CACHE_KEY,),
                ).fetchone()
            if not row:
                return None
            payload = json.loads(row[1])
            if not isinstance(payload, dict):
                return None
            return {"generated_at": float(row[0]), "payload": payload}
        except (OSError, sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            return None

    def record_presence(
        self,
        sampled_at: datetime,
        *,
        active: bool,
        idle_seconds: float | None,
    ) -> None:
        sampled_at = _parse_timestamp(sampled_at)
        if sampled_at is None:
            raise ValueError("sampled_at must be a timestamp")
        epoch = sampled_at.timestamp()
        minute_epoch = int(epoch // 60) * 60
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO presence("
                " minute_epoch, active, idle_seconds, sampled_at) VALUES (?, ?, ?, ?)",
                (
                    minute_epoch,
                    1 if active else 0,
                    None if idle_seconds is None else float(idle_seconds),
                    epoch,
                ),
            )

    def load_presence(self, start_date: date, end_date: date, *, tzinfo=None) -> list[dict]:
        tzinfo = tzinfo or datetime.now().astimezone().tzinfo or timezone.utc
        start = datetime.combine(start_date, datetime_time.min, tzinfo=tzinfo)
        end = datetime.combine(end_date + timedelta(days=1), datetime_time.min, tzinfo=tzinfo)
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT minute_epoch, active, idle_seconds FROM presence"
                    " WHERE minute_epoch >= ? AND minute_epoch < ? ORDER BY minute_epoch",
                    (int(start.timestamp()), int(end.timestamp())),
                ).fetchall()
        except (OSError, sqlite3.Error):
            return []
        return [
            {
                "sampled_at": datetime.fromtimestamp(row[0], timezone.utc).isoformat(),
                "active": bool(row[1]),
                "idle_seconds": row[2],
            }
            for row in rows
        ]

    def prune_presence(self, *, now: datetime | None = None, weeks: int = 18) -> int:
        now = _parse_timestamp(now or datetime.now(timezone.utc))
        cutoff = int((now - timedelta(weeks=weeks)).timestamp())
        try:
            with self._lock, self._connect() as conn:
                cursor = conn.execute(
                    "DELETE FROM presence WHERE minute_epoch < ?", (cutoff,)
                )
                return max(0, int(cursor.rowcount or 0))
        except (OSError, sqlite3.Error):
            return 0


def read_macos_idle_seconds(output: str) -> float | None:
    """Parse IOHIDSystem's nanosecond idle counter from ``ioreg`` output."""
    match = _IDLE_TIME_RE.search(str(output or ""))
    if not match:
        return None
    try:
        return int(match.group(1)) / 1_000_000_000
    except (TypeError, ValueError, OverflowError):
        return None


def presence_summary(rows: list[dict], *, tzinfo=None) -> dict:
    tzinfo = tzinfo or datetime.now().astimezone().tzinfo or timezone.utc
    sampled = set()
    active = set()
    hour_minutes = defaultdict(set)
    ordered_times = []
    for row in rows:
        timestamp = _parse_timestamp(row.get("sampled_at"))
        if timestamp is None:
            continue
        local = timestamp.astimezone(tzinfo)
        minute_epoch = int(local.timestamp() // 60)
        sampled.add(minute_epoch)
        ordered_times.append(local)
        if row.get("active"):
            active.add(minute_epoch)
            hour_minutes[(local.date().isoformat(), local.hour)].add(local.minute)
    first = min(ordered_times) if ordered_times else None
    last = max(ordered_times) if ordered_times else None
    span_minutes = int((last - first).total_seconds() // 60) + 1 if first and last else 0
    return {
        "sample_minutes": len(sampled),
        "active_minutes": len(active),
        "focus_hours": sum(1 for minutes in hour_minutes.values() if len(minutes) >= 45),
        "coverage_pct": round(len(sampled) * 100 / span_minutes, 1) if span_minutes else 0.0,
        "first_sampled_at": first.isoformat() if first else None,
        "last_sampled_at": last.isoformat() if last else None,
    }


def _set_presence_health(*, available: bool, reason: str | None, sampled_at=None) -> None:
    global _PRESENCE_HEALTH
    checked_at = _parse_timestamp(sampled_at)
    value = {
        "sampler_available": bool(available),
        "reason": reason,
        "last_checked_at": (
            checked_at.isoformat()
            if checked_at is not None
            else datetime.now(timezone.utc).isoformat()
        ),
    }
    with _PRESENCE_HEALTH_LOCK:
        _PRESENCE_HEALTH = value


def presence_health() -> dict:
    """Return privacy-safe capability health for the local idle sampler."""
    with _PRESENCE_HEALTH_LOCK:
        if isinstance(_PRESENCE_HEALTH, dict):
            return dict(_PRESENCE_HEALTH)
    if sys.platform != "darwin":
        return {
            "sampler_available": False,
            "reason": "unsupported_platform",
            "last_checked_at": None,
        }
    return {
        "sampler_available": True,
        "reason": "not_yet_sampled",
        "last_checked_at": None,
    }


def sample_presence(store: ProductivityStore, now: datetime | None = None) -> dict:
    """Take one local OS-idle sample and persist its minute bucket."""
    now = now or datetime.now(timezone.utc)
    if sys.platform != "darwin":
        _set_presence_health(
            available=False, reason="unsupported_platform", sampled_at=now
        )
        return {"available": False, "reason": "unsupported_platform"}
    try:
        proc = subprocess.run(
            ["ioreg", "-c", "IOHIDSystem"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        _set_presence_health(
            available=False, reason="idle_time_unavailable", sampled_at=now
        )
        return {"available": False, "reason": "idle_time_unavailable"}
    idle = read_macos_idle_seconds(proc.stdout) if proc.returncode == 0 else None
    if idle is None:
        _set_presence_health(
            available=False, reason="idle_time_unavailable", sampled_at=now
        )
        return {"available": False, "reason": "idle_time_unavailable"}
    is_active = idle < 300
    store.record_presence(now, active=is_active, idle_seconds=idle)
    _set_presence_health(available=True, reason=None, sampled_at=now)
    return {"available": True, "active": is_active, "idle_seconds": round(idle, 3)}


def run_presence_sampler(
    store: ProductivityStore,
    stop_event: threading.Event | None = None,
    interval: float = 60,
) -> None:
    """Sample presence until the daemon process or optional event stops."""
    stop_event = stop_event or threading.Event()
    while not stop_event.is_set():
        try:
            sample_presence(store)
            store.prune_presence()
        except Exception:
            # Presence is optional evidence and must never take down CCC.
            _set_presence_health(available=False, reason="sampling_failed")
        stop_event.wait(max(1.0, float(interval)))
