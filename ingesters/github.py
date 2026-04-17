"""Pull open GitHub issues via the `gh` CLI.

We call `gh` directly rather than reusing CCC's internal helpers because
the morning view has a slightly different shape requirement (we want
`text`, `age_days`, `labels`, and a source string) and CCC's helpers are
already tuned for its kanban. Keeping this isolated makes Phase 2 easier
to reason about.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def open_issues(repo_path, repo_label=None, limit=50):
    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        return []
    label = repo_label or repo_path.name
    try:
        r = subprocess.run(
            [
                "gh", "issue", "list",
                "--state", "open",
                "--json", "number,title,labels,createdAt,updatedAt",
                "-L", str(limit),
            ],
            capture_output=True, text=True, timeout=15, cwd=str(repo_path),
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if r.returncode != 0:
        return []
    try:
        raw = json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []

    items = []
    now = datetime.now(timezone.utc)
    for issue in raw:
        try:
            updated = datetime.fromisoformat(
                issue["updatedAt"].replace("Z", "+00:00")
            )
            age_days = max(0, (now - updated).days)
        except (KeyError, ValueError):
            age_days = 0
        labels = [lbl.get("name") for lbl in (issue.get("labels") or [])]
        items.append({
            "priority": "P1",
            "text": f"#{issue['number']} — {issue.get('title', '')}",
            "source": f"{label}/GH",
            "labels": labels,
            "age_days": age_days,
            "goal_slug": None,
        })
    return items
