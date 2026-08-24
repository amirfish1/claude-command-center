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
    scripts/debug_conv_list.py                      # top 5, 1d window, Coding lane
    scripts/debug_conv_list.py --window 7d --lane all --top 0   # full listing
    scripts/debug_conv_list.py --grep sonia
    scripts/debug_conv_list.py --fast                # accept a cached snapshot
    scripts/debug_conv_list.py --watch 5             # re-poll every 5s, flag drops

--watch is for the "new session appears, vanishes ~30s, reappears" bug: it
polls /api/conversations/list?window=all&stale_ok=1 -- the exact endpoint,
params, and (lightweight) cache path the sidebar itself polls -- on a fixed
interval and tracks every recently-active session_id across ticks. If a sid
seen in the payload goes missing on a later tick and comes back, that is
proof the drop happened in the SERVER's response, not in client-side
rendering, and gets printed as a loud "!!! VANISHED" / "reappeared" pair with
the gap duration. The server's own [ROW-FLICKER] log lines (service.out.log,
see _trace_row_flicker in server.py) should show the same sid at the same
time -- if they don't, the drop is somewhere else in the pipeline (the two
travel through the same _archive_list_payload call, so they should agree).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# Line-buffer stdout even when piped (e.g. `| tee`) -- otherwise --watch's
# output sits in a block buffer and a consumer sees nothing until the
# process exits, which for a "leave it running and watch" tool is silent
# failure, not slow output.
try:
    sys.stdout.reconfigure(line_buffering=True)
except (AttributeError, ValueError):
    pass


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


def fetch_worker_sids(host):
    wt_worker_sids = set()
    try:
        qs = _fetch(f"{host}/api/queue/status")
        wt_worker_sids.update(str(s) for s in (qs.get("worker_session_ids") or []))
    except Exception:
        pass
    try:
        wt = _fetch(f"{host}/api/wt/workers")
        for w in wt.get("workers") or []:
            if w.get("session_id"):
                wt_worker_sids.add(str(w["session_id"]))
    except Exception:
        pass
    return wt_worker_sids


def fetch_conversations(args, *, watch_mode):
    if watch_mode:
        # Same endpoint + params static/app.js's loadArchiveAll() polls for
        # the sidebar (see the /api/conversations/list handler + the
        # _archive_list_payload -> _trace_row_flicker instrumentation it now
        # runs). Cheap, cache-backed -- fine to hit every few seconds without
        # forcing the O(all-sessions) rescan --fast normally opts out of.
        url = f"{args.host}/api/conversations/list?window=all&stale_ok=1"
        data = _fetch(url)
        return data.get("conversations") or []
    stale_ok = "&stale_ok=1" if args.fast else ""
    convs_url = f"{args.host}/api/conversations/all?include_prs=0{stale_ok}"
    data = _fetch(convs_url)
    return data.get("conversations") or []


def build_rows(convs, wt_worker_sids, args):
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
    return rows, dupes, seen_sids


def print_snapshot(rows, convs, dupes, seen_sids, args):
    now = time.time()
    top = args.top if args.top > 0 else len(rows)
    shown = rows[:top]
    truncated = len(rows) - len(shown)
    print(
        f"{len(rows)} session(s) in window={args.window} lane={args.lane} "
        f"(of {len(convs)} total, cache={'stale-ok' if args.fast or args.watch else 'fresh'})"
        + (f" -- showing top {len(shown)}" if truncated > 0 else "")
    )
    print(f"{'sid':<10} {'age':>7}  {'lane':<9}{'engine':<9}{'title':<52} repo")
    for ts, lane, c in shown:
        age = fmt_age(now - ts) if ts else "?"
        sid = str(c.get("session_id") or c.get("id") or "")[:8]
        title = (c.get("display_name") or c.get("ai_title") or "").strip().replace("\n", " ")[:52]
        engine = str(c.get("engine") or c.get("source") or "?")
        folder = c.get("folder_label_chip") or c.get("folder_path") or ""
        parent = c.get("parent_session_id") or ""
        tail = f"  parent={str(parent)[:8]}" if parent else ""
        print(f"{sid:<10} {age:>7}  {lane:<9}{engine:<9}{title:<52} {folder}{tail}")
    if truncated > 0:
        print(f"... {truncated} more (--top 0 for all)")

    if dupes:
        print(f"\n! {len(dupes)} duplicate session_id(s) in the raw payload — a likely source of "
              f"missing/dupe rows client-side:")
        for sid in sorted(dupes):
            print(f"  {sid}  (appears {seen_sids[sid]}x)")


# How long a session_id is tracked for vanish/reappear detection after it was
# last seen "fresh" (i.e. genuinely recently active) -- keeps the tracker from
# flagging an old session that legitimately ages out of relevance, and bounds
# memory for a --watch run left going for hours.
FLICKER_FRESH_S = 15 * 60
FLICKER_FORGET_S = 30 * 60


def watch_loop(args):
    print(f"watching {args.host} every {args.watch}s (Ctrl-C to stop)\n")
    last_seen = {}      # sid -> epoch last confirmed present in the full payload
    missing_since = {}  # sid -> epoch first NOT present after being seen fresh
    wt_worker_sids = fetch_worker_sids(args.host)
    tick = 0
    while True:
        tick += 1
        now = time.time()
        try:
            convs = fetch_conversations(args, watch_mode=True)
        except urllib.error.URLError as e:
            print(f"[{time.strftime('%H:%M:%S')}] error: could not reach {args.host} ({e})", file=sys.stderr)
            time.sleep(args.watch)
            continue
        if tick % 12 == 1:  # worker sids drift slowly; refresh every ~minute at 5s cadence
            wt_worker_sids = fetch_worker_sids(args.host)
        rows, dupes, seen_sids = build_rows(convs, wt_worker_sids, args)

        print(f"[{time.strftime('%H:%M:%S')}] ---")
        print_snapshot(rows, convs, dupes, seen_sids, args)

        now_ids = {}
        for c in convs:
            sid = str(c.get("session_id") or c.get("id") or "")
            if sid:
                now_ids[sid] = c
        for sid in now_ids:
            gap_missing_at = missing_since.pop(sid, None)
            if gap_missing_at is not None:
                print(f"  !!! reappeared sid={sid[:8]} gap={now - gap_missing_at:.0f}s")
            if (now - row_ts(now_ids[sid])) < FLICKER_FRESH_S:
                last_seen[sid] = now
        for sid, seen_at in list(last_seen.items()):
            if sid in now_ids or sid in missing_since:
                continue
            if now - seen_at > FLICKER_FORGET_S:
                last_seen.pop(sid, None)
                continue
            missing_since[sid] = now
            print(f"  !!! VANISHED sid={sid[:8]} last_seen_ago={now - seen_at:.0f}s "
                  f"(was in the payload last poll, is not now -- check service.out.log for "
                  f"[ROW-FLICKER] {sid[:8]} at this timestamp)")

        print()
        time.sleep(args.watch)


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
    ap.add_argument("--top", type=int, default=5, help="rows to print, most-recent first (0 = all)")
    ap.add_argument(
        "--watch", type=float, default=0,
        help="re-poll every N seconds and flag any session that vanishes from "
             "the payload and later reappears (0 = single run)",
    )
    args = ap.parse_args()

    if args.watch > 0:
        try:
            watch_loop(args)
        except KeyboardInterrupt:
            print("\nstopped")
        return

    try:
        convs = fetch_conversations(args, watch_mode=False)
    except urllib.error.URLError as e:
        print(f"error: could not reach {args.host} ({e}). Is CCC running?", file=sys.stderr)
        sys.exit(1)

    wt_worker_sids = fetch_worker_sids(args.host)
    rows, dupes, seen_sids = build_rows(convs, wt_worker_sids, args)
    print_snapshot(rows, convs, dupes, seen_sids, args)


if __name__ == "__main__":
    main()
