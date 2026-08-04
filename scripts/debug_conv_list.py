#!/usr/bin/env python3
"""Dump the sessions CCC's "All" tab would show, for a given window/lane.

Quick terminal reproduction of the client-side filtering in static/app.js
(_archiveWindowAllowsRow, _allTabNaturalLane) against the same
/api/conversations/all data the sidebar renders from -- for spotting why a
session is missing from "All" without opening devtools.

Known gaps vs the real UI (approximations, not exact):
  - Lane classification here is the "natural" lane only: it does not walk
    parent_session_id chains to inherit a parent's lane, and it does not
    apply any per-session manual lane override set in the UI. A session
    nested under a Workers-lane parent will show here under its natural
    lane, not the parent's.
  - Uses live /api/wt/workers + /api/queue/status to flag WatchTower worker
    rows, same signals the UI uses, but skips its title-regex fallback for
    workers CCC has no session-id record of.

Usage:
    scripts/debug_conv_list.py                      # 1d window, Coding lane
    scripts/debug_conv_list.py --window 7d --lane all
    scripts/debug_conv_list.py --grep sonia
    scripts/debug_conv_list.py --fast                # accept a cached snapshot
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _fetch(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def window_cutoff(window):
    days = {"1d": 1, "7d": 7}.get(window)
    if not days:
        return None
    return time.time() - days * 86400


def row_ts(row):
    for key in (
        "modified", "mtime", "last_interacted", "last_activity",
        "last_mtime", "archived_at", "closed_at", "started_at",
    ):
        v = row.get(key)
        if v:
            v = float(v)
            return v / 1000 if v > 100000000000 else v
    return 0


def in_window(row, cutoff):
    if cutoff is None:
        return True
    if row.get("pinned") or row.get("source") == "hermes" or row.get("engine") == "hermes":
        return True
    return row_ts(row) >= cutoff


def natural_lane(row, wt_worker_sids):
    is_hermes = row.get("source") == "hermes" or row.get("engine") == "hermes"
    is_hermes_worker = is_hermes and (
        int(row.get("hermes_tool_calls") or 0) > 0
        or str(row.get("hermes_profile") or "").strip()
    )
    if is_hermes_worker:
        return "workers"
    sid = str(row.get("session_id") or row.get("id") or "").strip()
    if sid and sid in wt_worker_sids:
        return "workers"
    if is_hermes:
        return "messages"
    return "coding"


def fmt_age(seconds):
    if seconds < 0:
        return "?"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--window", default="1d", choices=["1d", "7d", "all"])
    ap.add_argument("--lane", default="coding", choices=["coding", "workers", "messages", "all"])
    ap.add_argument("--host", default="http://127.0.0.1:8090")
    ap.add_argument(
        "--fast", action="store_true",
        help="accept the server's cached snapshot instead of forcing a full "
             "rescan -- faster, but can miss a session the cache hasn't "
             "picked up yet, which is exactly the kind of gap this is for",
    )
    ap.add_argument("--grep", default="", help="case-insensitive substring filter on title/session id")
    args = ap.parse_args()

    stale_ok = "&stale_ok=1" if args.fast else ""
    convs_url = f"{args.host}/api/conversations/all?include_prs=0{stale_ok}"
    try:
        data = _fetch(convs_url)
    except urllib.error.URLError as e:
        print(f"error: could not reach {args.host} ({e}). Is CCC running?", file=sys.stderr)
        sys.exit(1)
    convs = data.get("conversations") or []

    wt_worker_sids = set()
    try:
        qs = _fetch(f"{args.host}/api/queue/status")
        wt_worker_sids.update(str(s) for s in (qs.get("worker_session_ids") or []))
    except Exception:
        pass
    try:
        wt = _fetch(f"{args.host}/api/wt/workers")
        for w in wt.get("workers") or []:
            if w.get("session_id"):
                wt_worker_sids.add(str(w["session_id"]))
    except Exception:
        pass

    cutoff = window_cutoff(args.window)
    seen_sids = {}
    dupes = set()
    rows = []
    for c in convs:
        sid = str(c.get("session_id") or c.get("id") or "")
        if sid:
            if sid in seen_sids:
                dupes.add(sid)
            seen_sids[sid] = seen_sids.get(sid, 0) + 1
        if not in_window(c, cutoff):
            continue
        lane = natural_lane(c, wt_worker_sids)
        if args.lane != "all" and lane != args.lane:
            continue
        if args.grep:
            hay = " ".join(
                str(c.get(k) or "") for k in
                ("display_name", "ai_title", "first_message", "session_id", "id")
            ).lower()
            if args.grep.lower() not in hay:
                continue
        rows.append((row_ts(c), lane, c))

    rows.sort(key=lambda r: r[0], reverse=True)
    now = time.time()
    print(
        f"{len(rows)} session(s) in window={args.window} lane={args.lane} "
        f"(of {len(convs)} total, cache={'stale-ok' if args.fast else 'fresh'})\n"
    )
    print(f"{'sid':<10} {'age':>7}  {'lane':<9}{'engine':<9}{'title':<52} repo")
    for ts, lane, c in rows:
        age = fmt_age(now - ts) if ts else "?"
        sid = str(c.get("session_id") or c.get("id") or "")[:8]
        title = (c.get("display_name") or c.get("ai_title") or "").strip().replace("\n", " ")[:52]
        engine = str(c.get("engine") or c.get("source") or "?")
        folder = c.get("folder_label_chip") or c.get("folder_path") or ""
        parent = c.get("parent_session_id") or ""
        tail = f"  parent={str(parent)[:8]}" if parent else ""
        print(f"{sid:<10} {age:>7}  {lane:<9}{engine:<9}{title:<52} {folder}{tail}")

    if dupes:
        print(f"\n! {len(dupes)} duplicate session_id(s) in the raw payload — a likely source of "
              f"missing/dupe rows client-side:")
        for sid in sorted(dupes):
            print(f"  {sid}  (appears {seen_sids[sid]}x)")


if __name__ == "__main__":
    main()
