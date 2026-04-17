"""Morning view module for Claude Command Center.

Phase 2 composition layer: reads real goal.md files from
~/.claude/log-viewer/morning/goals/ and scans watched repos for tactical
items (TODO.md, PARKING_LOT.md, GitHub issues). Falls back to sample
data if no goals have been seeded yet so the page still renders on a
fresh install.

Phase 3+ will derive session deliverables, recent-session summaries,
and ribbon text from Claude Code transcripts. For now the ribbon is a
simple "N active · M done" summary of the goal's strategies.
"""

import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import morning_store
from ingesters import github, repo_files


_CONFIG_PATH = Path.home() / ".claude" / "log-viewer" / "morning" / "config.json"


# ---------------------------------------------------------------------------
# Fallback sample data (used only when no goal.md files exist)
# ---------------------------------------------------------------------------

_SAMPLE_GOALS = [
    {"slug": "bym-growth", "name": "BYM growth", "life_area": "The Initiatives",
     "accent": "#27ae60",
     "ribbon": {"date": "Apr 17", "text": "5 commits · 3 issues closed · demo mode shipped", "source": "auto"}},
    {"slug": "nvidia-course", "name": "Nvidia course", "life_area": "The Initiatives",
     "accent": "#f39c12",
     "ribbon": {"date": "Apr 17", "text": "3 commits · spec draft landed · Eran aligned", "source": "auto"}},
    {"slug": "ai-forms", "name": "AI forms", "life_area": "The Initiatives",
     "accent": "#3498db",
     "ribbon": {"date": "Apr 17", "text": "no activity 4 days · \"$5 MCP\" still parked", "source": "auto"}},
    {"slug": "taxes", "name": "Taxes", "life_area": "HOME/FAMILY",
     "accent": "#9b59b6",
     "ribbon": {"date": "Apr 17", "text": "URGENT — deadline Apr 15 passed", "source": "manual"}},
]

_SAMPLE_STRATEGIC = [
    {"priority": "P0", "goal_slug": "nvidia-course",
     "text": "Come up with structure: raw material + workshop", "source": "Notion", "age_days": 3},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "Push/promote BYM (advance growth)", "source": "Notion", "age_days": 3},
    {"priority": "P0", "goal_slug": "taxes",
     "text": "Taxes", "source": "Notion", "age_days": 3},
    {"priority": "P1", "goal_slug": "ai-forms",
     "text": "Push AI forms (decide: launch / marketing / sales)", "source": "Notion", "age_days": 3},
]

_SAMPLE_TACTICAL = [
    {"priority": "P0", "goal_slug": "bym-growth",
     "text": "Re-run migration for Joyce after fixes", "source": "TODO.md", "age_days": 2},
    {"priority": "P0", "goal_slug": "bym-growth",
     "text": "#114 — same-day swap instructors fails", "source": "GH", "age_days": 0},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "ICS email invitations instead of GCal invites", "source": "TODO.md", "age_days": 5},
    {"priority": "P1", "goal_slug": "bym-growth",
     "text": "Verify new Calendly token holds all 7 scopes", "source": "TODO.md", "age_days": 4},
    {"priority": "P2", "goal_slug": "bym-growth",
     "text": "Test passkey auth end-to-end on production", "source": "PARKING", "age_days": 13},
]

_SAMPLE_INBOX = [
    {"source": "Apple Notes", "age_days": 2, "suggested_goal": None,
     "text": "Try using a local LLM for the Wispr transcription cleanup instead of sending to cloud"},
    {"source": "Google Doc", "age_days": 1, "suggested_goal": None,
     "text": "Should explore putting the command center behind proper auth so I can share it with Eran"},
    {"source": "Wispr", "age_days": 0, "suggested_goal": None,
     "text": "Idea: morning dashboard that aggregates all my todos so I don't have to remember where things are"},
    {"source": "Apple Notes", "age_days": 4, "suggested_goal": None,
     "text": "Find a decent mattress finally"},
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config():
    try:
        return json.loads(_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {"watched_repos": []}


def _tag_tactical(items, goals):
    """Assign `goal_slug` and `strategy_id` by word-boundary matching each
    strategy's `tactical_keywords` against item text + section heading.
    First match wins. Mutates items in place.

    Word-boundary matching (\\b) avoids false positives like "ad" matching
    inside the surname "Madler" or "strategy" matching "Strategy A" as a
    section header chunk — matches require the keyword to stand as its own
    word (or phrase bounded by non-word chars).
    """
    for item in items:
        blob = (item.get("text", "") + " " + (item.get("section") or "")).lower()
        for g in goals:
            matched = False
            for s in g.get("strategies", []):
                for kw in (s.get("tactical_keywords") or []):
                    if not kw:
                        continue
                    pattern = r"\b" + re.escape(kw.lower()) + r"\b"
                    if re.search(pattern, blob):
                        item["goal_slug"] = g["slug"]
                        item["strategy_id"] = s["id"]
                        matched = True
                        break
                if matched:
                    break
            if matched:
                break


def _ribbon_for(goal):
    strats = goal.get("strategies", [])
    if not strats:
        return {"date": datetime.now().strftime("%b %d"),
                "text": "no strategies yet — edit goal.md", "source": "derived"}
    active = sum(1 for s in strats if s.get("status") == "active")
    done = sum(1 for s in strats if s.get("status") == "done")
    dropped = sum(1 for s in strats if s.get("status") == "dropped")
    parts = [f"{active} active"]
    if done:
        parts.append(f"{done} done")
    if dropped:
        parts.append(f"{dropped} dropped")
    return {"date": datetime.now().strftime("%b %d"),
            "text": " · ".join(parts), "source": "derived"}


def _strategic_from_goals(goals):
    rows = []
    for g in goals:
        for s in g.get("strategies", []):
            if s.get("status") != "active":
                continue
            rows.append({
                "priority": s.get("priority", "P1"),
                "goal_slug": g["slug"],
                "strategy_id": s["id"],
                "text": s.get("text", ""),
                "source": s.get("source", "goal.md"),
                "age_days": 0,
            })
    return rows


def _scan_all_repos():
    cfg = _load_config()
    items = []
    for repo in cfg.get("watched_repos", []):
        path = repo.get("path")
        label = repo.get("label")
        if not path:
            continue
        items.extend(repo_files.scan_todo_md(path, label))
        items.extend(repo_files.scan_parking_lot(path, label))
        items.extend(github.open_issues(path, label, limit=30))
    return items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_morning_state():
    """Return the full state needed to render /morning.

    Prefers real goals from ~/.claude/log-viewer/morning/goals/. Falls back
    to sample data if no goals are seeded yet (so a fresh install still
    renders something meaningful).
    """
    goals = morning_store.load_all_goals()
    if not goals:
        return {
            "goals": copy.deepcopy(_SAMPLE_GOALS),
            "strategic": copy.deepcopy(_SAMPLE_STRATEGIC),
            "tactical": copy.deepcopy(_SAMPLE_TACTICAL),
            "inbox": copy.deepcopy(_SAMPLE_INBOX),
            "last_refreshed": datetime.now(timezone.utc).isoformat(),
        }

    goal_cards = [{
        "slug": g["slug"],
        "name": g["name"],
        "life_area": g["life_area"],
        "accent": g["accent"],
        "ribbon": _ribbon_for(g),
    } for g in goals]

    strategic = _strategic_from_goals(goals)
    tactical = _scan_all_repos()
    _tag_tactical(tactical, goals)

    return {
        "goals": goal_cards,
        "strategic": strategic,
        "tactical": tactical,
        "inbox": [],  # Phase 5: LLM extraction from free-form sources
        "last_refreshed": datetime.now(timezone.utc).isoformat(),
    }


def get_goal_detail(slug):
    """Return detail for one goal, or None if slug is unknown."""
    goals = morning_store.load_all_goals()
    goal = next((g for g in goals if g["slug"] == slug), None)
    if goal is None:
        # Fallback to sample demo data for Phase-1 slugs that don't have real files
        # yet (only relevant when goals dir is entirely empty, but we keep this
        # for demo parity).
        return None

    tactical = _scan_all_repos()
    _tag_tactical(tactical, goals)
    tagged = [t for t in tactical if t.get("goal_slug") == slug]

    return {
        "slug": goal["slug"],
        "name": goal["name"],
        "life_area": goal["life_area"],
        "accent": goal["accent"],
        "intent_markdown": goal["intent_markdown"],
        "strategies": goal["strategies"],
        "tactical_tagged": [{
            "text": t["text"],
            "source": t["source"],
            "strategy_id": t.get("strategy_id"),
        } for t in tagged],
        "deliverables": [],   # Phase 3: transcript-derived
        "context_library": [],  # Phase 4: ingested inputs
        "recent_sessions": [],  # Phase 3: transcript-indexed
    }
