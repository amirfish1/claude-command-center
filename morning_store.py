"""Read goal.md files from ~/.claude/log-viewer/morning/goals/.

Uses a tiny YAML-subset parser (stdlib-only) since CCC avoids dependencies.
The subset supports exactly what the goal.md schema needs:

- Top-level `key: value` scalars (strings, numbers, nulls, booleans)
- Quoted string values ("foo", 'foo')
- One-deep list of dicts keyed by `  - key: value` with further 4-space indented siblings
- A list of scalar strings under a key (`tactical_keywords:\n  - foo\n  - bar`)

It is *not* a general YAML parser. Anything fancier than the above will fail.
Errors are surfaced by raising ValueError with a line hint so the Morning UI
can show a parse-error banner for a broken goal file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# YAML-subset parser
# ---------------------------------------------------------------------------

_NULL_LITERALS = {"null", "~", ""}
_TRUE = {"true", "True", "yes"}
_FALSE = {"false", "False", "no"}


def _coerce_scalar(raw):
    s = raw.strip()
    if not s:
        return None
    # Quoted string → strip quotes
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        return s[1:-1]
    if s in _NULL_LITERALS:
        return None
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    # Integer
    try:
        if re.fullmatch(r"-?\d+", s):
            return int(s)
    except ValueError:
        pass
    return s


def _indent_of(line):
    i = 0
    while i < len(line) and line[i] == " ":
        i += 1
    return i


def _parse_yaml_subset(text):
    """Parse the tiny YAML subset described in the module docstring.

    Returns a dict (the root is always a mapping in our schema).
    """
    lines = text.splitlines()
    out = {}
    i = 0
    n = len(lines)

    def _strip_comment(s):
        # Drop trailing `# comment` if not inside a quoted string.
        in_single = False
        in_double = False
        for idx, c in enumerate(s):
            if c == "'" and not in_double:
                in_single = not in_single
            elif c == '"' and not in_single:
                in_double = not in_double
            elif c == "#" and not in_single and not in_double:
                return s[:idx].rstrip()
        return s

    while i < n:
        raw = lines[i]
        # Skip blank / comment-only
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        raw = _strip_comment(raw)
        indent = _indent_of(raw)
        if indent != 0:
            raise ValueError(f"unexpected indent at line {i + 1}: {raw!r}")
        if ":" not in raw:
            raise ValueError(f"missing ':' at line {i + 1}: {raw!r}")

        key, _, rest = raw.partition(":")
        key = key.strip()
        rest = rest.strip()

        if rest:
            out[key] = _coerce_scalar(rest)
            i += 1
            continue

        # Value is on subsequent indented lines. Peek to determine list vs. scalar.
        j = i + 1
        # Skip blanks
        while j < n and not lines[j].strip():
            j += 1
        if j >= n:
            out[key] = None
            i = j
            continue

        peek = _strip_comment(lines[j])
        peek_indent = _indent_of(peek)
        peek_stripped = peek.strip()

        if peek_indent > 0 and peek_stripped.startswith("- "):
            # It's a list.
            items = []
            while j < n:
                line = _strip_comment(lines[j])
                if not line.strip():
                    j += 1
                    continue
                lind = _indent_of(line)
                if lind == 0:
                    break  # back at root
                lstripped = line.strip()
                if lstripped.startswith("- "):
                    # Either "- scalar" or "- key: value"
                    after = lstripped[2:]
                    if ":" in after and not (after.startswith('"') or after.startswith("'")):
                        # dict item — collect this line's k:v, then any indented siblings
                        item = {}
                        fk, _, fv = after.partition(":")
                        fk = fk.strip()
                        fv = fv.strip()
                        dict_indent = lind  # indent of the "- " line
                        # Siblings align with the first key after "- ", which sits two
                        # characters past dict_indent (the "- " prefix itself).
                        sibling_indent = dict_indent + 2
                        if fv:
                            item[fk] = _coerce_scalar(fv)
                            j += 1
                            # continue pulling siblings
                            while j < n:
                                sline = _strip_comment(lines[j])
                                if not sline.strip():
                                    j += 1
                                    continue
                                sind = _indent_of(sline)
                                sstrip = sline.strip()
                                if sind == sibling_indent and not sstrip.startswith("- "):
                                    # more key:value for this dict item
                                    sk, _, sv = sstrip.partition(":")
                                    sk = sk.strip()
                                    sv = sv.strip()
                                    if sv:
                                        item[sk] = _coerce_scalar(sv)
                                        j += 1
                                    else:
                                        # nested list under this key, e.g. tactical_keywords:
                                        k2 = j + 1
                                        nested = []
                                        while k2 < n:
                                            nline = _strip_comment(lines[k2])
                                            if not nline.strip():
                                                k2 += 1
                                                continue
                                            nind = _indent_of(nline)
                                            nstrip = nline.strip()
                                            if nind > sibling_indent and nstrip.startswith("- "):
                                                nested.append(_coerce_scalar(nstrip[2:]))
                                                k2 += 1
                                            else:
                                                break
                                        item[sk] = nested
                                        j = k2
                                else:
                                    break
                            items.append(item)
                            continue
                        else:
                            # `- key:` with no inline value — nested list follows
                            item[fk] = None
                            j += 1
                            items.append(item)
                            continue
                    else:
                        items.append(_coerce_scalar(after))
                        j += 1
                else:
                    break
            out[key] = items
            i = j
        else:
            # Scalar on next line — not supported in our subset; treat as None
            out[key] = None
            i = j

    return out


# ---------------------------------------------------------------------------
# goal.md loader
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def parse_goal_md(text):
    """Return (frontmatter_dict, body_markdown_str). Raises ValueError on malformed."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError("goal.md missing `---` frontmatter fences")
    fm_text, body = m.group(1), m.group(2).strip()
    fm = _parse_yaml_subset(fm_text)
    return fm, body


def goals_dir_default():
    return Path.home() / ".claude" / "log-viewer" / "morning" / "goals"


def load_goal(goal_dir):
    """Load a single goal directory into the shape used by morning.py.

    Returns dict with keys: slug, name, life_area, accent, intent_markdown,
    strategies, created, status.
    """
    goal_dir = Path(goal_dir)
    md_path = goal_dir / "goal.md"
    text = md_path.read_text()
    fm, body = parse_goal_md(text)
    slug = goal_dir.name
    strategies = fm.get("strategies") or []
    # Normalize: each strategy may or may not have claude_session_id. If the
    # session isn't set, we mark the UI session_state "never"; otherwise we'd
    # need to check CCC's live-state registry — that's Phase 3.
    for s in strategies:
        sid = s.get("claude_session_id")
        if s.get("status") == "dropped":
            s["session_state"] = "dropped"
        elif sid:
            s["session_state"] = "dormant"  # Phase 3 will upgrade to alive if it's running
        else:
            s["session_state"] = "never"
        s.setdefault("session_summary", "")
    return {
        "slug": slug,
        "name": fm.get("name") or slug,
        "life_area": fm.get("life_area") or "",
        "accent": fm.get("accent") or "#5ac8fa",
        "created": fm.get("created"),
        "status": fm.get("status") or "active",
        "intent_markdown": body,
        "strategies": strategies,
    }


def load_all_goals(goals_dir=None):
    """Return a list of loaded goal dicts for every subdirectory containing goal.md."""
    goals_dir = Path(goals_dir) if goals_dir else goals_dir_default()
    if not goals_dir.is_dir():
        return []
    goals = []
    for child in sorted(goals_dir.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "goal.md").is_file():
            continue
        try:
            goals.append(load_goal(child))
        except (OSError, ValueError) as e:
            # Surface as a pseudo-goal so the UI can show the error inline
            # rather than silently dropping the goal.
            goals.append({
                "slug": child.name,
                "name": child.name,
                "life_area": "(parse error)",
                "accent": "#c0392b",
                "intent_markdown": f"**goal.md failed to parse:**\n\n{e}",
                "strategies": [],
                "status": "error",
            })
    return goals


def add_user_tactical(goal_slug, text, source_note="morning braindump", meta=None):
    """Append a user-authored tactical item to
    ~/.claude/log-viewer/morning/user-tactical.jsonl. Morning.py's tactical
    aggregator reads this file alongside repo-scanned items.

    `meta` (optional dict): extra fields to persist with the item — e.g.
    braindump classification, LLM notes, matched_existing. These survive
    into the Today strip so the UI can show context without a second card.

    Returns {"ok": True, "id": ...}.
    """
    import json
    import hashlib
    import time as _time

    path = Path.home() / ".claude" / "log-viewer" / "morning" / "user-tactical.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    cid = hashlib.sha1(f"user:{goal_slug}:{text}:{_time.time()}".encode()).hexdigest()[:12]
    entry = {
        "id": cid,
        "goal_slug": goal_slug,
        "text": text,
        "source": source_note,
        "created_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }
    if meta and isinstance(meta, dict):
        for k in ("classification", "notes", "matched_existing"):
            v = meta.get(k)
            if v:
                entry[k] = v
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "id": cid}


def _user_tactical_order_path():
    return Path.home() / ".claude" / "log-viewer" / "morning" / "user-tactical-order.json"


def load_user_tactical_order():
    """Return the persisted order [cid, cid, ...] or [] if none saved.

    Missing ids (items added since last save) get appended to the end in
    creation-timestamp order by the caller. Extra ids not matching any known
    item are ignored.
    """
    import json
    path = _user_tactical_order_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def save_user_tactical_order(ids):
    """Persist a list of user-tactical ids in display order."""
    import json
    path = _user_tactical_order_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(ids, list):
        ids = []
    try:
        path.write_text(json.dumps(ids, indent=2))
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def load_user_tactical(include_dismissed=False):
    """Return the list of user-added tactical items.

    By default skips dismissed items. Pass include_dismissed=True to get
    everything for a "Completed" view. Active items are ordered by the
    saved drag-order (see save_user_tactical_order); dismissed items follow
    in most-recent-first order.
    """
    import json
    path = Path.home() / ".claude" / "log-viewer" / "morning" / "user-tactical.jsonl"
    if not path.is_file():
        return []
    items = {}
    dismissed_ids = set()
    dismissed_at = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = ev.get("id")
                if not cid:
                    continue
                if ev.get("dismissed_at"):
                    dismissed_ids.add(cid)
                    dismissed_at[cid] = ev.get("dismissed_at")
                    continue
                items.setdefault(cid, ev)
    except OSError:
        return []

    active_map = {k: v for k, v in items.items() if k not in dismissed_ids}
    order = load_user_tactical_order()
    ordered_active = []
    seen = set()
    for cid in order:
        if cid in active_map and cid not in seen:
            ordered_active.append(active_map[cid])
            seen.add(cid)
    for cid, v in active_map.items():
        if cid not in seen:
            ordered_active.append(v)

    if not include_dismissed:
        return ordered_active

    dismissed_items = []
    for cid in dismissed_ids:
        if cid in items:
            entry = dict(items[cid])
            entry["dismissed_at"] = dismissed_at.get(cid)
            dismissed_items.append(entry)
    dismissed_items.sort(key=lambda e: e.get("dismissed_at") or "", reverse=True)
    return ordered_active + dismissed_items


def dismiss_user_tactical(item_id):
    """Append a dismissed marker for a user-tactical item."""
    import json
    import time as _time
    path = Path.home() / ".claude" / "log-viewer" / "morning" / "user-tactical.jsonl"
    if not path.is_file():
        return {"ok": False, "error": "no user tactical file"}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "id": item_id,
                "dismissed_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
            }) + "\n")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def attach_context(goal_slug, source, source_id, title, body_markdown):
    """Persist a context artifact for a goal.

    Writes a markdown file under `goals/<slug>/context/` and appends a
    provenance record to `goals/<slug>/attachments.jsonl`. Idempotent on
    (source, source_id): re-attaching the same artifact overwrites the
    body and appends a new attach event (not dedupes — the log is a
    timeline).

    Returns {"ok": True, "path": str} or {"ok": False, "error": str}.
    """
    import json
    import time as _time

    goals_dir = goals_dir_default()
    goal_dir = goals_dir / goal_slug
    if not (goal_dir / "goal.md").is_file():
        return {"ok": False, "error": f"unknown goal: {goal_slug}"}
    ctx_dir = goal_dir / "context"
    ctx_dir.mkdir(parents=True, exist_ok=True)

    # Build a filename. Prefix by source type + sanitized source_id.
    safe_title = re.sub(r"[^A-Za-z0-9_-]+", "-", (title or source_id or "untitled"))[:60].strip("-") or "untitled"
    safe_source = re.sub(r"[^A-Za-z0-9_-]+", "_", (source or "doc"))[:20]
    filename = f"{safe_source}--{safe_title}.md"
    target = ctx_dir / filename
    try:
        target.write_text(body_markdown or "")
    except OSError as e:
        return {"ok": False, "error": f"write failed: {e}"}

    rel_path = f"context/{filename}"
    entry = {
        "source": source,
        "source_id": source_id,
        "title": title,
        "path": rel_path,
        "fetched_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }
    attach_log = goal_dir / "attachments.jsonl"
    try:
        with open(attach_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # non-fatal; markdown is still on disk
    return {"ok": True, "path": str(target)}


def mark_inbox_item(candidate_id, **update):
    """Append a shadow record to an inbox jsonl file marking a candidate
    promoted or dismissed. The _load_inbox reader filters items that have
    `promoted_to` or `dismissed_at` set in any record matching their id.

    We don't rewrite existing jsonl files — we append a minimal
    "update" record keyed by the candidate_id. That matches the append-only
    convention of the morning-view jsonl artifacts (progress, attachments,
    inbox).
    """
    import json
    import time as _time

    inbox_dir = Path.home() / ".claude" / "log-viewer" / "morning" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    today = _time.strftime("%Y-%m-%d")
    jsonl = inbox_dir / f"{today}.jsonl"
    entry = {"id": candidate_id, "updated_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())}
    entry.update(update)
    try:
        with open(jsonl, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


def save_strategy_session_id(goal_slug, strategy_id, session_id):
    """Text-edit goal.md to set a specific strategy's claude_session_id.

    Returns True on success, False if the goal or strategy couldn't be found.
    """
    path = goals_dir_default() / goal_slug / "goal.md"
    if not path.is_file():
        return False
    text = path.read_text()
    lines = text.splitlines()
    out = []
    in_block = False
    replaced = False
    target_header_re = re.compile(r"^  - id: " + re.escape(strategy_id) + r"\s*$")
    sibling_re = re.compile(r"^(    claude_session_id:\s*).+$")
    for line in lines:
        if target_header_re.match(line):
            in_block = True
            out.append(line)
            continue
        if in_block:
            # End of the strategy block: next list item or outdented content
            if re.match(r"^  - id: ", line) or re.match(r"^[^ ]", line) or line.startswith("---"):
                in_block = False
            elif not replaced and sibling_re.match(line):
                out.append(f"    claude_session_id: \"{session_id}\"")
                replaced = True
                continue
        out.append(line)
    if not replaced:
        return False
    new_text = "\n".join(out)
    if text.endswith("\n") and not new_text.endswith("\n"):
        new_text += "\n"
    path.write_text(new_text)
    return True


__all__ = [
    "parse_goal_md",
    "load_goal",
    "load_all_goals",
    "goals_dir_default",
    "save_strategy_session_id",
    "attach_context",
    "mark_inbox_item",
    "add_user_tactical",
    "load_user_tactical",
    "load_user_tactical_order",
    "save_user_tactical_order",
    "dismiss_user_tactical",
]
