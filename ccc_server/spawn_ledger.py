"""Read-only spawn-ledger scorecard support.

The ledger is owned outside CCC and is append-only JSONL. This module only
normalizes rows and computes grade aggregates for the dashboard API.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_SPAWN_LEDGER_PATH = Path("/Users/amirfish/MyOfficeMgr/projects/spawn-ledger/ledger.jsonl")

LEDGER_FIELDS = (
    "ts", "session_id", "engine", "model", "effort", "task_type", "lane",
    "repo", "prompt_summary", "grade", "grade_notes",
)


def spawn_ledger_path(path=None):
    if path is not None:
        return Path(path).expanduser()
    return Path(os.environ.get("SPAWN_LEDGER_PATH") or DEFAULT_SPAWN_LEDGER_PATH).expanduser()


def _text(value):
    return str(value or "").strip()


def _grade(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num < 1 or num > 5:
        return None
    return int(num) if num.is_integer() else num


def _ts_sort_key(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = _text(value)
    if not text:
        return 0.0
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


def _normalize_row(raw):
    row = {field: _text(raw.get(field)) for field in LEDGER_FIELDS if field != "grade"}
    row["grade"] = _grade(raw.get("grade"))
    return row


def read_spawn_ledger(path=None):
    """Return (rows, ignored_lines, error) without mutating the ledger."""
    p = spawn_ledger_path(path)
    rows = []
    ignored = 0
    try:
        with p.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    ignored += 1
                    continue
                if not isinstance(raw, dict):
                    ignored += 1
                    continue
                row = _normalize_row(raw)
                row["_line_no"] = line_no
                rows.append(row)
    except FileNotFoundError:
        return [], 0, "ledger not found"
    except OSError as e:
        return [], 0, str(e)

    rows.sort(key=lambda r: (_ts_sort_key(r.get("ts")), r.get("_line_no", 0)), reverse=True)
    for row in rows:
        row.pop("_line_no", None)
    return rows, ignored, ""


def _avg(total, count):
    return round(total / count, 2) if count else None


def spawn_ledger_scorecard(rows):
    groups = {}
    task_types = set()
    for row in rows:
        grade = row.get("grade")
        if grade is None:
            continue
        engine = row.get("engine") or "unknown"
        model = row.get("model") or "unknown"
        task_type = row.get("task_type") or "unknown"
        task_types.add(task_type)
        group = groups.setdefault((engine, model), {"tasks": {}, "total": 0.0, "n": 0})
        cell = group["tasks"].setdefault(task_type, {"total": 0.0, "n": 0})
        cell["total"] += float(grade)
        cell["n"] += 1
        group["total"] += float(grade)
        group["n"] += 1

    out_rows = []
    for (engine, model), group in sorted(groups.items()):
        tasks = {
            task_type: {"avg": _avg(cell["total"], cell["n"]), "n": cell["n"]}
            for task_type, cell in sorted(group["tasks"].items())
        }
        out_rows.append({
            "engine": engine,
            "model": model,
            "tasks": tasks,
            "overall": {"avg": _avg(group["total"], group["n"]), "n": group["n"]},
        })
    return {"task_types": sorted(task_types), "rows": out_rows}


def spawn_ledger_payload(path=None):
    p = spawn_ledger_path(path)
    rows, ignored, error = read_spawn_ledger(p)
    graded_count = sum(1 for row in rows if row.get("grade") is not None)
    payload = {
        "ok": True,
        "path": str(p),
        "rows": rows,
        "row_count": len(rows),
        "graded_count": graded_count,
        "ignored_lines": ignored,
        "scorecard": spawn_ledger_scorecard(rows),
    }
    if error:
        payload["error"] = error
    return payload
