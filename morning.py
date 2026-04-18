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


def _load_context_library(goal_slug):
    """Return context artifacts attached to this goal.

    Reads `goals/<slug>/context/*.md` and enriches with metadata from
    `goals/<slug>/attachments.jsonl` (provenance: source type, source_id,
    fetched_at). Files without a matching jsonl entry still surface — the
    attachments log is a convenience, not a requirement.
    """
    goal_dir = morning_store.goals_dir_default() / goal_slug
    ctx_dir = goal_dir / "context"
    if not ctx_dir.is_dir():
        return []

    # Build provenance map from attachments.jsonl, if present.
    provenance = {}
    attach_log = goal_dir / "attachments.jsonl"
    if attach_log.is_file():
        try:
            with open(attach_log, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rel = ev.get("path")
                    if rel:
                        provenance[rel] = ev
        except OSError:
            pass

    out = []
    for md in sorted(ctx_dir.glob("*.md")):
        rel = f"context/{md.name}"
        p = provenance.get(rel) or {}
        out.append({
            "type": (p.get("source") or "DOC").upper().replace("_", " "),
            "label": p.get("title") or md.stem,
            "source": p.get("source_id") or rel,
            "path": str(md),
            "fetched_at": p.get("fetched_at"),
        })
    return out


def _load_inbox():
    """Read recent inbox jsonl files and return candidates that haven't
    been promoted or dismissed.

    The jsonl files are append-only. A candidate is the first record with
    a given `id`. Subsequent records with the same `id` and a
    `promoted_to` / `dismissed_at` field mark the candidate as handled —
    we filter those out of the returned set. Capped at the last 7 days.
    """
    inbox_dir = Path.home() / ".claude" / "log-viewer" / "morning" / "inbox"
    if not inbox_dir.is_dir():
        return []
    candidates_by_id = {}
    handled_ids = set()
    for jsonl in sorted(inbox_dir.glob("*.jsonl"))[-7:]:  # last 7 days, chronological
        try:
            f = open(jsonl, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = ev.get("id")
                if not cid:
                    continue
                if ev.get("promoted_to") or ev.get("dismissed_at"):
                    handled_ids.add(cid)
                    continue
                # First record seen wins as the candidate.
                candidates_by_id.setdefault(cid, ev)
        finally:
            f.close()
    return [c for cid, c in candidates_by_id.items() if cid not in handled_ids]


def _upgrade_session_states(goals):
    """Upgrade each strategy's session_state from the on-disk default
    ("dormant" whenever a claude_session_id exists) to "alive" when CCC's
    session registry shows a running process, and annotate session_summary
    with concrete pid / tty / mtime information.

    Imports `server` lazily to avoid a circular import at module load time.
    """
    try:
        import server as _server  # lazy — server.py also imports morning
    except Exception:
        return
    for g in goals:
        for s in g.get("strategies", []):
            sid = s.get("claude_session_id")
            if not sid:
                continue
            try:
                cwd = _server.find_session_cwd(sid)
                status = _server.session_live_status(sid, cwd)
            except Exception:
                continue
            if status.get("live"):
                s["session_state"] = "alive"
                pid = status.get("pid")
                tty = status.get("tty")
                bits = [f"session {sid[:8]}", "alive"]
                if pid:
                    bits.append(f"pid {pid}")
                if tty:
                    bits.append(f"tty {tty}")
                s["session_summary"] = " · ".join(bits)
            else:
                s["session_state"] = "dormant"
                bits = [f"session {sid[:8]}", "dormant"]
                if status.get("recently_written"):
                    bits.append("recent activity")
                s["session_summary"] = " · ".join(bits)


def _deliverables_for_goal(goal):
    """Scan each strategy's session transcript and extract tool-use events
    that correspond to durable artifacts: Write/Edit target paths and
    git-commit Bash commands. Returns a deduplicated list capped at 20.

    Transcripts live under ~/.claude/projects/*/<session_id>.jsonl. Each
    assistant message may contain one or more `tool_use` content blocks.
    """
    sids = {
        s.get("claude_session_id"): s.get("id")
        for s in goal.get("strategies", [])
        if s.get("claude_session_id")
    }
    if not sids:
        return []
    projects_root = Path.home() / ".claude" / "projects"
    if not projects_root.is_dir():
        return []

    deliverables = []
    seen = set()

    def _add(kind, label, sess_label):
        key = (kind, label)
        if not label or key in seen:
            return
        seen.add(key)
        deliverables.append({
            "type": kind,
            "label": label,
            "source": f"{kind.lower()} · {sess_label}",
        })

    _commit_msg_re = re.compile(r"""-m\s+(?:"([^"]+)"|'([^']+)')""")
    for sid, strat_id in sids.items():
        jsonl = None
        for pd in projects_root.iterdir():
            cand = pd / f"{sid}.jsonl"
            if cand.is_file():
                jsonl = cand
                break
        if not jsonl:
            continue
        try:
            f = open(jsonl, "r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "assistant":
                    continue
                for block in (ev.get("message") or {}).get("content", []) or []:
                    if block.get("type") != "tool_use":
                        continue
                    name = block.get("name") or ""
                    inp = block.get("input") or {}
                    if name == "Write":
                        _add("FILE", inp.get("file_path", ""), strat_id)
                    elif name in ("Edit", "MultiEdit"):
                        _add("EDIT", inp.get("file_path", ""), strat_id)
                    elif name == "Bash":
                        cmd = inp.get("command", "") or ""
                        if "git commit" in cmd:
                            m = _commit_msg_re.search(cmd)
                            msg = ""
                            if m:
                                msg = (m.group(1) or m.group(2) or "").split("\n")[0][:80]
                            _add("COMMIT", msg or "(no message)", strat_id)
        finally:
            f.close()

    return deliverables[:20]


def _recent_sessions_for_goal(goal):
    """Return a list of recent Claude sessions whose session_id appears in
    any strategy of the given goal. Pulled from CCC's find_all_sessions()
    so we inherit its ordering and metadata.
    """
    sids = {
        s.get("claude_session_id")
        for s in goal.get("strategies", [])
        if s.get("claude_session_id")
    }
    if not sids:
        return []
    try:
        import server as _server
    except Exception:
        return []
    try:
        all_sessions = _server.find_all_sessions() or []
    except Exception:
        return []
    out = []
    for sess in all_sessions:
        sid = sess.get("session_id")
        if sid not in sids:
            continue
        summary = (
            sess.get("display_name")
            or (sess.get("first_message") or "")[:80]
            or f"session {sid[:8]}"
        )
        when = sess.get("modified_human") or ""
        if sess.get("is_live"):
            when = (when + " · alive").strip(" ·")
        out.append({
            "summary": summary,
            "when": when,
            "session_id": sid,
        })
    return out


def _group_suggested(items, min_group_size=3):
    """Collapse same-source clusters of items into group rows so the
    Suggested list doesn't drown in 10 BYM GitHub issues.

    Groups by (goal_slug, source) pairs. Any cluster with >= min_group_size
    items becomes one row of kind='group' with an `items` payload the
    frontend can expand. Smaller clusters stay as individual rows.
    """
    buckets = {}
    order = []
    for it in items:
        key = (it.get("goal_slug"), it.get("source"))
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(it)

    out = []
    for key in order:
        group = buckets[key]
        goal_slug, source = key
        if len(group) >= min_group_size:
            out.append({
                "kind": "group",
                "goal_slug": goal_slug,
                "source": source,
                "count": len(group),
                "text": f"{source} — {len(group)} items",
                "collapsed": True,
                "items": group,
            })
        else:
            for it in group:
                it["kind"] = "single"
                out.append(it)
    return out


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

    _upgrade_session_states(goals)

    goal_cards = [{
        "slug": g["slug"],
        "name": g["name"],
        "life_area": g["life_area"],
        "accent": g["accent"],
        "ribbon": _ribbon_for(g),
    } for g in goals]

    strategic = _strategic_from_goals(goals)

    # "Today" = only the things Amir explicitly committed to. Everything
    # we scan from TODO.md / PARKING_LOT / GitHub starts in "Suggested"
    # so Today doesn't drown in auto-scanned backlog.
    today = []
    completed = []
    for ut in morning_store.load_user_tactical(include_dismissed=True):
        row = {
            "priority": "P1",
            "goal_slug": ut.get("goal_slug"),
            "text": ut.get("text", ""),
            "source": ut.get("source") or "braindump",
            "age_days": 0,
            "user_tactical_id": ut.get("id"),
            "classification": ut.get("classification"),
            "notes": ut.get("notes"),
            "matched_existing": ut.get("matched_existing"),
        }
        if ut.get("dismissed_at"):
            row["dismissed_at"] = ut.get("dismissed_at")
            completed.append(row)
        else:
            today.append(row)

    suggested = _scan_all_repos()
    _tag_tactical(suggested, goals)
    suggested = _group_suggested(suggested)

    # Preserve old key for backwards-compat with any existing client code;
    # the new UI reads `today` + `suggested` directly.
    tactical = today + [
        item
        for group in suggested
        for item in (group.get("items") if group.get("collapsed") else [group])
    ]

    return {
        "goals": goal_cards,
        "strategic": strategic,
        "today": today,
        "completed": completed,
        "suggested": suggested,
        "tactical": tactical,  # deprecated — retained for any older consumers
        "inbox": _load_inbox(),
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

    _upgrade_session_states([goal])

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
        "deliverables": _deliverables_for_goal(goal),
        "context_library": _load_context_library(slug),
        "recent_sessions": _recent_sessions_for_goal(goal),
    }
