# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 32736-33990).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import collections
import copy
import json
import os
import subprocess
import threading
import time
import ux_fixes_queue  # kept as fallback when watchtower is not installed

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# Queue / ticket-appearance events for replay (W22 / B4)
#
# CCC replays group chats on a message-ordered timeline; this derives queue
# events (a ticket appearing, being claimed, being resolved) from the SAME
# durable ticket store the health strip reads — no parallel event store. The
# item's created_at/claimed_at/closed_at timestamps ARE the event log; we fold
# them in time order so each event carries the queue's depth right after it, and
# the replay UI interleaves them without rescanning tickets per frame.
#
# Queue replay design notes are kept with the implementation and tests.
# ---------------------------------------------------------------------------
_QUEUE_REPLAY_MAX_EVENTS = 300           # newest-wins cap on returned events
_QUEUE_REPLAY_MAX_LOOKBACK_S = 14 * 24 * 3600  # ignore events older than this
_queue_replay_cache = {}  # (mtime_ns, size) -> {"events": [...], "truncated": bool}
_queue_replay_cache_lock = threading.Lock()


def _queue_store_path_for_cache():
    """Resolve the durable queue-store file so we can cache by (mtime, size).

    The active engine (`_q`) owns disk layout: WatchTower exposes
    `_resolve_store_path()`; the stdlib fallback uses `ux_fixes_queue.QUEUE_FILE`
    (the same file). Returns a Path or None."""
    resolve = getattr(_core._q, "_resolve_store_path", None)
    if callable(resolve):
        try:
            return Path(resolve())
        except Exception:
            pass
    try:
        return Path(ux_fixes_queue.QUEUE_FILE)
    except Exception:
        return None


def _queue_replay_events_uncached():
    """Derive + fold queue events from one _q.list_items() read.

    Depth semantics ("queue filling/draining over time"): a queue's depth is its
    set of ACTIVE tickets — created but not yet resolved. So `created` bumps
    depth +1, `resolved` drops it -1, and `claimed` annotates state without
    moving depth (the ticket is still in the queue, now being worked). Folding
    runs over the FULL history so `depth_after` stays absolute even after the
    lookback trim below drops old events from what we render."""
    try:
        items = _core._q.list_items()
    except Exception:
        items = []
    raw = []  # (ts, kind, base)
    for it in items:
        if not isinstance(it, dict):
            continue
        base = {
            "ref": str(it.get("ref") or "").strip(),
            "queue": (str(it.get("queue") or "").strip()
                      or str(it.get("project") or "").strip() or "?"),
            "project": str(it.get("project") or ""),
            "note": str(it.get("note") or "")[:200],
            "claimed_by": str(it.get("claimed_by") or ""),
        }
        created = _core._uxq_parse_ts(it.get("created_at"))
        claimed = _core._uxq_parse_ts(it.get("claimed_at"))
        closed = _core._uxq_parse_ts(it.get("closed_at"))
        if created:
            raw.append((created, "created", base))
        if claimed:
            raw.append((claimed, "claimed", base))
        if closed:
            raw.append((closed, "resolved", base))
    # Stable time order; created-before-resolved on identical stamps.
    _kind_rank = {"created": 0, "claimed": 1, "resolved": 2}
    raw.sort(key=lambda r: (r[0], _kind_rank.get(r[1], 1)))
    depth_by_queue = {}
    events = []
    for ts, kind, base in raw:
        q = base["queue"]
        depth = depth_by_queue.get(q, 0)
        if kind == "created":
            depth += 1
        elif kind == "resolved":
            depth = max(0, depth - 1)
        depth_by_queue[q] = depth
        ev = dict(base)
        ev["kind"] = kind
        ev["ts"] = ts
        ev["iso"] = datetime.fromtimestamp(
            ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        ev["depth_after"] = depth
        events.append(ev)
    cutoff = time.time() - _QUEUE_REPLAY_MAX_LOOKBACK_S
    events = [e for e in events if e["ts"] >= cutoff]
    truncated = False
    if len(events) > _QUEUE_REPLAY_MAX_EVENTS:
        events = events[-_QUEUE_REPLAY_MAX_EVENTS:]
        truncated = True
    return {"events": events, "truncated": truncated}


def _queue_replay_events():
    """Queue events for replay, memoised by the store file's (mtime, size).

    An unchanged store returns the cached, pre-folded list with zero JSON parse
    — the replay timeline is built once per group-chat read (already coalesced),
    never per frame (CLAUDE.md § Performance gates)."""
    store = _queue_store_path_for_cache()
    key = None
    if store is not None:
        try:
            st = store.stat()
            key = (st.st_mtime_ns, st.st_size)
        except OSError:
            key = None
    if key is not None:
        cached = _queue_replay_cache.get(key)
        if cached is not None:
            return cached
    result = _core._queue_replay_events_uncached()
    if key is not None:
        with _queue_replay_cache_lock:
            _queue_replay_cache.clear()  # only the current store snapshot matters
            _queue_replay_cache[key] = result
    return result



# Minutes a fixer can make no closing progress before its project's queue is
# judged STUCK (used by compute_ux_fixes_health). Mirrors the nudge watcher's
# "no progress" intuition; kept generous so a slow-but-working fix isn't flagged.
_UXQ_STUCK_NO_PROGRESS_S = 10 * 60


# Mirrors watchtower.queue.UNCLAIMABLE_READINESS. A ticket in one of these
# states is not work a drainer can pick up, so it must not count toward the
# depth that decides whether a queue is stuck.
_UNCLAIMABLE_READINESS = ("needs-shaping", "needs-spec")


def compute_ux_fixes_health(items=None):
    """Per-project queue-health snapshot used by /api/ux-fixes/health.

    Returns a list of dicts, one per project that has open tickets:

        {project, depth, oldest_open_age_seconds, fixer_session_id,
         fixer_live, fixer_idle_or_stale, stuck}

    `stuck` = depth > 0 AND (no resolvable live fixer OR the fixer has closed
    nothing for _UXQ_STUCK_NO_PROGRESS_S). A stuck project with no resolvable
    fixer is reported (a human / the UI handles spawning) but never auto-spawned.

    PERFORMANCE (CLAUDE.md "Performance gates"): this does ZERO O(all
    sessions/transcripts) work. The queue is one JSON file read once via the
    backend-agnostic list_items() API. Liveness is resolved ONLY for the single
    candidate fixer of a project that already has open tickets, through the
    cheap sidecar/live-id primitive `_archive_session_is_live` (a stat() plus
    the in-memory live-id set) — no per-row transcript parse and no subprocess
    fork per project/row.
    """
    try:
        items = _core._q.list_items() if items is None else items
    except Exception:
        items = []
    now = time.time()
    # Pre-scan: which projects have open tickets. The fixer-resolution and
    # liveness probe are candidacy-gated to these, so the spawn registry (read
    # below to resolve label-claimed fixers) is touched at most once per pass —
    # and only when there is open work to reach. No per-row file/subprocess.
    open_projects = {
        str(it.get("project") or "?") for it in items if it.get("status") == "open"
    }
    registry_by_name = _core._uxq_spawn_names_to_sids() if open_projects else {}
    # First pass: group by project. depth + oldest open age, and the most
    # recently active fixer (claimer) plus its highest closed seq (progress).
    by_project = {}  # project → bucket
    for it in items:
        proj = str(it.get("project") or "?")
        b = by_project.setdefault(proj, {
            "depth": 0,
            "oldest_open_ts": None,
            "fixer_session_id": None,
            "fixer_last_ts": 0.0,
            "fixer_progress_ts": 0.0,
            "last_activity_ts": 0.0,
        })
        status = it.get("status")
        if status == "open":
            b["depth"] += 1
            created = _core._uxq_parse_ts(it.get("created_at"))
            if created and (b["oldest_open_ts"] is None or created < b["oldest_open_ts"]):
                b["oldest_open_ts"] = created
        # Most recent touch across ANY ticket in the project (open or closed) —
        # drives the health-strip's reverse-chronological order (CCC-458), same
        # "newest activity first" convention as Current Sessions (CCC-421).
        touch_ts = max(
            _core._uxq_parse_ts(it.get("updated_at")),
            _core._uxq_parse_ts(it.get("closed_at")),
            _core._uxq_parse_ts(it.get("created_at")),
        )
        if touch_ts and touch_ts > b["last_activity_ts"]:
            b["last_activity_ts"] = touch_ts
        # Only resolve a fixer for projects that have open tickets (candidacy
        # gate). Prefer the additive real-session field, then a UUID label, then
        # a registry name match — see _uxq_resolve_fixer_sid.
        if proj not in open_projects:
            continue
        sid = _core._uxq_resolve_fixer_sid(it, registry_by_name=registry_by_name)
        if not sid:
            continue
        ts = _core._uxq_parse_ts(it.get("updated_at") or it.get("claimed_at"))
        if not ts or (now - ts) > _core._UXQ_NUDGE_LOOKBACK_S:
            continue
        # The fixer is the claimer whose activity is most recent.
        if ts > b["fixer_last_ts"]:
            b["fixer_last_ts"] = ts
            b["fixer_session_id"] = sid
        if status == "closed":
            closed_ts = _core._uxq_parse_ts(it.get("closed_at") or it.get("updated_at"))
            if closed_ts > b["fixer_progress_ts"]:
                b["fixer_progress_ts"] = closed_ts
    # Second pass: only projects WITH open tickets are candidates for the (cheap)
    # liveness probe — that is the candidacy gate. One probe per such project.
    out = []
    for proj, b in by_project.items():
        if b["depth"] <= 0:
            continue
        fixer = b["fixer_session_id"]
        fixer_live = bool(fixer) and _core._archive_session_is_live(fixer)
        # "Made no closing progress for N minutes" — also treat a fixer we never
        # saw close anything (progress_ts == 0) as stale once it has a claim.
        last_progress = b["fixer_progress_ts"] or b["fixer_last_ts"]
        idle_or_stale = (not last_progress) or ((now - last_progress) > _UXQ_STUCK_NO_PROGRESS_S)
        stuck = (not fixer_live) or idle_or_stale
        oldest_ts = b["oldest_open_ts"]
        last_activity_ts = b["last_activity_ts"]
        out.append({
            "project": proj,
            "depth": b["depth"],
            "oldest_open_age_seconds": int(now - oldest_ts) if oldest_ts else None,
            "last_activity_seconds": int(now - last_activity_ts) if last_activity_ts else None,
            "fixer_session_id": fixer,
            "fixer_live": fixer_live,
            "fixer_idle_or_stale": idle_or_stale,
            "stuck": stuck,
        })
    # Reverse-chronological (CCC-458): most-recently-touched queue first. A
    # queue with no resolvable activity timestamp sorts last, not first.
    out.sort(key=lambda r: (
        r["last_activity_seconds"] if r["last_activity_seconds"] is not None else float("inf"),
        r["project"],
    ))
    return out


def compute_queues_health(health=None, wt_workers=None, items=None):
    """Per-QUEUE snapshot for the dashboard's "Evergreen" sidebar section.

    WatchTower workers are ephemeral (spawned/reaped on a short TTL, so they
    flicker), but queues are durable — the right thing to watch over time. This
    folds three durable/semi-durable sources into one row per queue:

      - the configured-queue list (``watchtower.config.all_queues()``) so a
        queue with zero open work and zero live workers still shows;
      - per-project open depth + stuck flag from ``compute_ux_fixes_health()``;
      - live worker counts from the annotated ``wt_workers`` rows.

    Each row: ``{queue, depth, closed, total, oldest_open_age_seconds, workers,
    auto_drain, stuck, state, fixer_session_id}`` where ``state`` is one of
    ``stuck`` / ``draining`` / ``backlog``. ``depth`` is the open count;
    ``closed`` / ``total`` give a done/total progress count for the queue.

    PERFORMANCE: no O(all sessions/transcripts) work. It consumes the already
    computed ``health`` and ``wt_workers`` and reads the small queue-config file
    once (``all_queues()``). No subprocess, no per-row transcript parse.
    """
    if health is None:
        health = _core.compute_ux_fixes_health()
    if wt_workers is None:
        wt_workers = []

    def _norm(s):
        return str(s or "").strip().upper()

    # Live worker counts per queue (one pass over the annotated worker rows).
    worker_counts = {}
    for w in (wt_workers or []):
        q = _norm(w.get("queue"))
        if not q:
            continue
        worker_counts[q] = worker_counts.get(q, 0) + 1

    # Durable configured-queue list keyed by normalized name → auto_drain + repo_path.
    # Read the config file directly (no watchtower import dependency).
    cfg_drain = {}
    cfg_repo = {}
    cfg_claim = {}
    cfg_workers = {}
    cfg_names = set()
    try:
        for name, conf in (_core._wt_read_config() or {}).items():
            qn = _norm(name)
            cfg_names.add(qn)
            cfg_drain[qn] = bool((conf or {}).get("auto_drain", False))
            rp = str((conf or {}).get("repo_path") or "").strip()
            if rp:
                cfg_repo[qn] = rp
            ct = (conf or {}).get("claim_types", [])
            cfg_claim[qn] = [t for t in ct if t in ("bug", "feature")] if isinstance(ct, list) else []
            try:
                cfg_workers[qn] = max(1, int((conf or {}).get("desired_workers", 1) or 1))
            except (TypeError, ValueError):
                cfg_workers[qn] = 1
    except Exception:
        cfg_drain = {}
        cfg_repo = {}
        cfg_claim = {}
        cfg_workers = {}
        cfg_names = set()

    health_by_q = {_norm(r.get("project")): r for r in (health or [])}

    # Closed + total counts per queue from ONE list_items() pass (one JSON file
    # read, no per-row subprocess/transcript parse — see PERFORMANCE note above).
    # `open` stays = depth (from health); these add a done/total progress count.
    closed_by_q = {}
    total_by_q = {}
    claimable_by_q = {}  # queue → open items a worker is ALLOWED to claim
    last_activity_q = {}  # queue → most-recent item-touch epoch (any status)
    try:
        for it in ((_core._q.list_items() if items is None else items) or []):
            qn = _norm(it.get("project"))
            if not qn or qn == "?":
                continue
            total_by_q[qn] = total_by_q.get(qn, 0) + 1
            if it.get("status") == "closed":
                closed_by_q[qn] = closed_by_q.get(qn, 0) + 1
            # Claimable depth honors the queue's claim_types filter. A bug-only
            # queue (claim_types=['bug']) whose open tickets are all features has
            # zero claimable work — the drainer is idle BY DESIGN, not stuck.
            # GitHub-backed WatchTower queues expose every open repo issue for
            # inventory, but only issues with the queue label carry
            # claimable=True; un-runnable issues must not trigger drain state.
            if it.get("status") == "open":
                if it.get("claimable") is False:
                    continue
                # Readiness gating, the other half of WatchTower's claim
                # filter. `claim_next` skips needs-shaping/needs-spec tickets
                # unless it is explicitly shaping, and the WT reconciler counts
                # claimable depth through that same filter — so a queue whose
                # only open tickets are unshaped is NOT under-staffed, it is
                # correctly unstaffed, and WT will never spawn for it. CCC used
                # to copy only the claim_types half, which made those queues
                # read "stuck" forever: an alarm about work no worker is
                # allowed to touch, which no restart or spawn could ever clear.
                # A ticket a human pressed ▶ on is claimable regardless — that
                # is the manual override WT honours too.
                readiness = str(it.get("readiness") or "").strip().lower()
                if readiness in _UNCLAIMABLE_READINESS and not it.get("run_requested"):
                    continue
                types = cfg_claim.get(qn, [])
                # Untyped == bug (matches WatchTower's claim filter): a ticket
                # filed without a type must not silently vanish from a bugs-only
                # queue, so it counts as claimable there.
                ty = str(it.get("type") or it.get("item_type") or "").strip().lower()
                if ty not in ("bug", "feature"):
                    ty = "bug"
                if not types or ty in types:
                    claimable_by_q[qn] = claimable_by_q.get(qn, 0) + 1
            # Most recent touch = newest of updated/closed/created — drives the
            # "active in last N days" filter so a drained-but-recent queue still
            # shows while a long-dead queue drops off.
            ts = max(
                _core._uxq_parse_ts(it.get("updated_at")),
                _core._uxq_parse_ts(it.get("closed_at")),
                _core._uxq_parse_ts(it.get("created_at")),
            )
            if ts and ts > last_activity_q.get(qn, 0):
                last_activity_q[qn] = ts
    except Exception:
        closed_by_q = {}
        total_by_q = {}
        last_activity_q = {}

    names = set()
    names.update(cfg_drain.keys())
    names.update(health_by_q.keys())
    names.update(worker_counts.keys())
    names.update(total_by_q.keys())
    names.discard("")
    names.discard("?")

    # Drop explicitly-deleted queues (CCC-425 reopen) unless they've had NEW
    # activity since the deletion — a re-filed ticket legitimately resurrects
    # the queue, but stale closed-ticket history from before the delete must
    # not keep it pinned in the health strip forever.
    try:
        deletions = _core._wt_read_queue_deletions()
    except Exception:
        deletions = {}
    for qn, deleted_at in deletions.items():
        qn = _norm(qn)
        if qn in cfg_names:
            continue  # re-added to config since deletion — always show
        if last_activity_q.get(qn, 0) <= float(deleted_at or 0):
            names.discard(qn)

    out = []
    for q in names:
        hr = health_by_q.get(q) or {}
        depth = int(hr.get("depth") or 0)
        claimable = int(claimable_by_q.get(q, 0))
        workers = int(worker_counts.get(q, 0))
        auto = bool(cfg_drain.get(q, False))
        # A queue with a live WatchTower worker is NOT stuck — the worker is
        # draining it. The legacy fixer-based `stuck` (compute_ux_fixes_health)
        # only sees stuck because WT workers claim with a non-UUID worker_id and
        # never write their cloud session_id onto the ticket, so it can't resolve
        # a "live fixer". A hung worker is a worker-health concern surfaced on the
        # worker row, not a queue-level stuck. Trust WT ground-truth: workers > 0.
        #
        # `claimable` (not raw `depth`) gates stuck: a bug-only queue whose open
        # tickets are all features has open depth but ZERO claimable work, so the
        # drainer sitting idle is correct, not stuck. Those parked items show as
        # `backlog` (calm), never `stuck` (alarm).
        # Only an auto-drain queue can be "stuck": one with auto_drain OFF is a
        # deliberate parking lot (backlog), not a fire. `claimable` (not raw
        # `depth`) gates it so a bug-only queue full of features reads backlog.
        stuck = bool(hr.get("stuck")) and auto and claimable > 0 and workers == 0
        if stuck:
            state = "stuck"
        elif depth > 0 and claimable == 0:
            state = "backlog"   # open items exist but all filtered out by claim_types
        elif auto:
            state = "draining"
        else:
            state = "backlog"
        out.append({
            "queue": q,
            "depth": depth,
            "claimable": claimable,
            "closed": int(closed_by_q.get(q, 0)),
            "total": int(total_by_q.get(q, 0)),
            "oldest_open_age_seconds": hr.get("oldest_open_age_seconds"),
            "workers": workers,
            "auto_drain": auto,
            "stuck": stuck,
            "state": state,
            "fixer_session_id": hr.get("fixer_session_id"),
            "repo_path": cfg_repo.get(q, ""),
            "claim_types": cfg_claim.get(q, []),
            "desired_workers": cfg_workers.get(q, 1),
            "configured": q in cfg_names,
            "last_activity_seconds": (
                int(time.time() - last_activity_q[q]) if q in last_activity_q else None
            ),
        })
    # Stuck first, then deepest, then most workers, then name — most-urgent top.
    out.sort(key=lambda r: (0 if r["stuck"] else 1, -r["depth"], -r["workers"], r["queue"]))
    return out


_UX_FIXES_LIST_TTL = 10.0
_ux_fixes_list_cache = {}
_ux_fixes_list_cache_lock = threading.Lock()
_ux_fixes_list_refreshing = set()


def _ux_fixes_list_refresh(status_filter, lane_filter):
    key = (status_filter or "", lane_filter or "")
    try:
        items = _core._q.list_items(status=status_filter, lane=lane_filter) or []
        with _ux_fixes_list_cache_lock:
            _ux_fixes_list_cache[key] = {"ts": time.time(), "items": items}
    except Exception:
        pass
    finally:
        with _ux_fixes_list_cache_lock:
            _ux_fixes_list_refreshing.discard(key)


def _ux_fixes_list_items_cached(status_filter=None, lane_filter=None):
    """TTL memo for /api/ux-fixes/list, stale-while-revalidate: past the TTL
    the last copy is served immediately and one background thread rebuilds —
    GitHub-backed queue merges (`gh issue list`) never block a request."""
    key = (status_filter or "", lane_filter or "")
    now = time.time()
    with _ux_fixes_list_cache_lock:
        ent = _ux_fixes_list_cache.get(key)
    if ent and now - ent["ts"] < _UX_FIXES_LIST_TTL:
        return ent["items"]
    if ent is not None:
        with _ux_fixes_list_cache_lock:
            if key not in _ux_fixes_list_refreshing:
                _ux_fixes_list_refreshing.add(key)
                threading.Thread(
                    target=_ux_fixes_list_refresh,
                    args=(status_filter, lane_filter),
                    daemon=True, name="uxq-list-refresh",
                ).start()
        return ent["items"]
    items = _core._q.list_items(status=status_filter, lane=lane_filter) or []
    with _ux_fixes_list_cache_lock:
        if len(_ux_fixes_list_cache) > 32:
            _ux_fixes_list_cache.clear()
        _ux_fixes_list_cache[key] = {"ts": time.time(), "items": items}
    return items


# ---------------------------------------------------------------------------
# GitHub-backed queue freshness
# ---------------------------------------------------------------------------
# The queue-events SSE detects change by stat'ing the local ticket store, which
# a GitHub-backed queue never touches — its tickets live in GitHub Issues. The
# original cover for that was a blind 60s beat, but a beat only tells the client
# to refetch, and the endpoint it refetches is stale-while-revalidate: the first
# poll after a remote change serves the OLD list and merely schedules a rebuild,
# so the new issue does not surface until the NEXT beat. Two beats plus the
# backend's own 20s list cache put a freshly-filed issue up to ~2 minutes away,
# which is why a manual browser reload looked like the only thing that worked.
#
# So: poll deliberately instead of beating blindly. While at least one board has
# the SSE open, refresh the GitHub list on a fixed cadence, write the result
# into the very memo /api/queue/list serves, and bump a version counter when the
# ticket set actually changes. The SSE folds that counter into its change
# detection, so the push now means "there is something new AND it is already
# warm" — the client's refetch returns fresh data on the first try.
#
# Cost is bounded by subscribers, not by tabs: one `gh issue list` per interval
# while a board is open, zero when none is.
#
# 5s, not 20s: the requirement is "a new issue is on the board within 5s".
# That is affordable because WatchTower's list now revalidates with an ETag —
# an unchanged poll is a conditional GET that returns 304 in ~0.5s and costs
# nothing against the rate limit, so polling 4x more often consumes *less*
# quota than the old unconditional `gh issue list` every 20s.
_GH_POLL_INTERVAL_S = 5.0
_gh_watch_lock = threading.Lock()
_gh_watch = {"subscribers": 0, "thread": None, "version": 0, "sig": None}
_gh_watch_wake = threading.Event()


def _github_queue_configured():
    """True when any queue in queue-config.json is GitHub-backed."""
    try:
        cfg = _core._wt_read_config()
    except Exception:
        return False
    return any(
        isinstance(v, dict) and str(v.get("backend") or "") == "github"
        for v in cfg.values()
    )


def _gh_queue_signature(items):
    """Change fingerprint over GitHub-sourced tickets only.

    `github_repo` is stamped on every item the GitHub backend builds, so it
    identifies remote rows without trusting the user-settable `source` field.
    Sorted so list ordering can never masquerade as a change.
    """
    rows = sorted(
        (
            str(it.get("ref") or ""),
            str(it.get("status") or ""),
            str(it.get("updated_at") or ""),
        )
        for it in items
        if isinstance(it, dict) and it.get("github_repo")
    )
    return tuple(rows)


def _gh_queue_poll_once():
    """One forced remote refresh. Warms the list memo, returns True on change."""
    try:
        items = _core._q.list_items(fresh=True) or []
    except TypeError:
        # The stdlib fallback engine has no `fresh` kwarg — and no remote to
        # refresh either, so this degrades to a plain read.
        try:
            items = _core._q.list_items() or []
        except Exception:
            return False
    except Exception:
        return False

    with _ux_fixes_list_cache_lock:
        _ux_fixes_list_cache[("", "")] = {"ts": time.time(), "items": items}

    sig = _gh_queue_signature(items)
    with _gh_watch_lock:
        first = _gh_watch["sig"] is None
        changed = (not first) and sig != _gh_watch["sig"]
        _gh_watch["sig"] = sig
        if changed:
            _gh_watch["version"] += 1
    return changed


def _gh_queue_watch_loop():
    while True:
        with _gh_watch_lock:
            if _gh_watch["subscribers"] <= 0:
                _gh_watch["thread"] = None
                return
        # Re-read config every pass so a queue switched to the GitHub backend
        # starts being watched without needing a reconnect.
        if _github_queue_configured():
            try:
                _gh_queue_poll_once()
            except Exception:
                pass
        _gh_watch_wake.wait(_GH_POLL_INTERVAL_S)
        _gh_watch_wake.clear()


def gh_queue_watch_enter():
    """Register an SSE subscriber; start the watcher if it is not running."""
    with _gh_watch_lock:
        _gh_watch["subscribers"] += 1
        if _gh_watch["thread"] is None:
            t = threading.Thread(
                target=_gh_queue_watch_loop, daemon=True, name="gh-queue-watch",
            )
            _gh_watch["thread"] = t
            t.start()


def gh_queue_watch_exit():
    with _gh_watch_lock:
        _gh_watch["subscribers"] = max(0, _gh_watch["subscribers"] - 1)
        idle = _gh_watch["subscribers"] <= 0
    if idle:
        # Let the loop notice and retire instead of sleeping out the interval.
        _gh_watch_wake.set()


def gh_queue_version():
    """Monotonic counter; changes when the remote ticket set changed."""
    with _gh_watch_lock:
        return _gh_watch["version"]


_UX_FIXES_HEALTH_TTL = 15.0
_ux_fixes_health_snapshot = {"ts": 0.0, "data": None}
_ux_fixes_health_snapshot_lock = threading.Lock()


def _build_ux_fixes_health_payload_uncached():
    # One queue read for both builders — list_items() also merges GitHub-backed
    # queues via `gh issue list` subprocesses on cold cache, so calling it twice
    # per build doubled the dominant cost.
    try:
        items = _core._q.list_items() or []
    except Exception:
        items = []
    health = _core.compute_ux_fixes_health(items=items)
    # Live WT workers, read straight from workers.json (no watchtower import
    # dependency). Carries each worker's cloud session UUID so the dashboard can
    # link a worker row to its conversation.
    try:
        wt_workers = _core._wt_read_workers()
    except Exception:
        wt_workers = []
    # Per-queue rollup (durable) for the dashboard's Evergreen section.
    try:
        queues = _core.compute_queues_health(health, wt_workers, items=items)
    except Exception:
        queues = []
    # Persistent ledger of every cloud session_id that was ever a WT worker
    # (survives pruning).
    try:
        worker_session_ids = _core._wt_read_worker_session_ids()
    except Exception:
        worker_session_ids = []
    # Past workers from the last 24h (log files, excluding live).
    try:
        past_workers = _core._wt_past_workers(hours=24)
    except Exception:
        past_workers = []
    return {
        "ok": True,
        "projects": health,
        "count": len(health),
        "wt_workers": wt_workers,
        "queues": queues,
        "worker_session_ids": worker_session_ids,
        "past_workers": past_workers,
    }


_ux_fixes_health_refreshing = False


def _ux_fixes_health_refresh():
    global _ux_fixes_health_refreshing
    try:
        data = _build_ux_fixes_health_payload_uncached()
        with _ux_fixes_health_snapshot_lock:
            _core._ux_fixes_health_snapshot["data"] = data
            _core._ux_fixes_health_snapshot["ts"] = time.time()
    except Exception:
        pass
    finally:
        with _ux_fixes_health_snapshot_lock:
            _ux_fixes_health_refreshing = False


def build_ux_fixes_health_payload(force=False):
    """Coalesced payload for /api/ux-fixes/health.

    The dashboard, detail rail, and foreground refocus paths can all ask for
    queue health at once. The uncached build touches WatchTower queue state and
    recent worker logs, so concurrent polls must share one fresh snapshot
    instead of each request rebuilding it in its own HTTP thread.

    Stale-while-revalidate: past the TTL, callers get the last snapshot
    immediately and ONE background thread rebuilds — no request thread ever
    waits on a WatchTower/gh issue-list merge again.
    """
    global _ux_fixes_health_refreshing
    snap = _core._ux_fixes_health_snapshot
    now = time.time()
    if not force and snap["data"] is not None:
        if now - snap["ts"] < _core._UX_FIXES_HEALTH_TTL:
            return copy.deepcopy(snap["data"])
        with _ux_fixes_health_snapshot_lock:
            if not _ux_fixes_health_refreshing:
                _ux_fixes_health_refreshing = True
                threading.Thread(
                    target=_ux_fixes_health_refresh,
                    daemon=True, name="uxq-health-refresh",
                ).start()
        return copy.deepcopy(snap["data"])
    with _ux_fixes_health_snapshot_lock:
        now = time.time()
        if not force and snap["data"] is not None and now - snap["ts"] < _core._UX_FIXES_HEALTH_TTL:
            return copy.deepcopy(snap["data"])
        data = _build_ux_fixes_health_payload_uncached()
        snap["data"] = data
        snap["ts"] = time.time()
        return copy.deepcopy(data)


def _queue_codex_resume(session_id, text, pid=None, reason=None, *, only_if_pending=False):
    with _core._pending_resume_lock:
        if only_if_pending and not _core._pending_resume_queue.get(session_id):
            return None
        queue = _core._pending_resume_queue.setdefault(session_id, [])
        # Deduplicate identical text so a slow-to-confirm delivery (e.g. a
        # verifier report landing on a finished Codex thread) cannot pile up
        # duplicate queued copies that get re-injected repeatedly.
        if text not in queue:
            queue.append(text)
            _core._pending_resume_queue[session_id] = queue
    _core._save_pending_inputs()
    _core._schedule_codex_queue_pump(session_id)
    payload = {
        "ok": True,
        "queued": True,
        "via": "codex-resume-queued",
    }
    if reason:
        # Carry the real reason (e.g. "Codex thread is active") so the client can
        # show "Queued: <reason>" inline instead of a bare "Queued".
        payload["queued_reason"] = reason
        payload["error"] = reason
    approval_hint = _core._codex_pending_approval_hint(session_id)
    if approval_hint:
        payload["pending_approval"] = approval_hint
        if not reason or approval_hint not in reason:
            blocked = (
                f"blocked on a Codex approval: {approval_hint}. Answer it "
                "(approve button or POST /api/codex/approval) to release "
                "the queue"
            )
            payload["queued_reason"] = f"{reason} — {blocked}" if reason else blocked
            payload["error"] = payload["queued_reason"]
    if pid is not None:
        payload["pid"] = pid
    return payload


# ── Live Codex wake/turn status (feeds GET /api/codex-wake-status) ──────────
# The conversation pane polls this while "Codex resuming…" is up so the user
# sees the wake broken down moment to moment (stages, live tokens, context %)
# and, critically, learns when a turn produced nothing because the session hit
# its context limit. Everything here is single-session and cheap: the stage
# timeline comes from the in-memory `_CODEX_WAKE_EVENTS` window (NO file scan)
# and the token/context readout comes from a ~16KB TAIL of one rollout file,
# cached by (path, mtime, size) so repeated polls of an unchanged file re-read
# nothing (perf gate).
_CODEX_WAKE_TAIL_CACHE = collections.OrderedDict()
_CODEX_WAKE_TAIL_LOCK = threading.Lock()
_CODEX_WAKE_TAIL_BYTES = 16 * 1024
_CODEX_WAKE_TAIL_MAX = 16


def _codex_wake_rollout_snapshot(session_id):
    """Latest model/effort + token/context numbers for a Codex session, read
    from the TAIL of its rollout jsonl. Cached by (path, mtime, size). Missing
    or unreadable rollout -> an all-None snapshot. Never raises."""
    empty = {
        "model": None, "effort": None,
        "context_used": None, "context_window": None, "context_pct": None,
        "input_tokens": None, "output_tokens": None,
        "task_complete_epoch": 0.0, "no_agent_output": None,
    }
    try:
        path = _core._resolve_codex_rollout_path(session_id)
    except Exception:
        path = None
    if not path:
        return dict(empty)
    try:
        st = path.stat()
    except OSError:
        return dict(empty)
    key = (str(path), st.st_mtime_ns, st.st_size)
    with _CODEX_WAKE_TAIL_LOCK:
        hit = _CODEX_WAKE_TAIL_CACHE.get(str(path))
        if hit and hit[0] == key:
            _CODEX_WAKE_TAIL_CACHE.move_to_end(str(path))
            return dict(hit[1])
    snap = dict(empty)
    try:
        with open(path, "rb") as f:
            if st.st_size > _CODEX_WAKE_TAIL_BYTES:
                f.seek(-_CODEX_WAKE_TAIL_BYTES, os.SEEK_END)
                f.readline()  # drop the partial first line after the seek
            chunk = f.read()
        for raw in chunk.split(b"\n"):
            if not raw.strip():
                continue
            try:
                ev = json.loads(raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(ev, dict):
                continue
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            ev_type = ev.get("type")
            ptype = payload.get("type")
            if ev_type == "turn_context":
                model = payload.get("model")
                if model:
                    snap["model"] = model
                settings = ((payload.get("collaboration_mode") or {}).get("settings") or {})
                eff = (
                    payload.get("effort")
                    or payload.get("reasoning_effort")
                    or (settings.get("reasoning_effort") if isinstance(settings, dict) else None)
                )
                if eff:
                    snap["effort"] = eff
                continue
            if ptype == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                window = _core._codex_int(info.get("model_context_window"))
                last = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
                inp = _core._codex_int(last.get("input_tokens"))
                out = _core._codex_int(last.get("output_tokens"))
                # Live context occupancy is the size of the LAST prompt sent
                # (last_token_usage.input_tokens), NOT the cumulative
                # total_token_usage.total_tokens (which sums every turn and
                # overflows the window). As the conversation fills, input_tokens
                # climbs toward model_context_window; at the cap it reads 100%.
                if window:
                    snap["context_window"] = window
                if inp:
                    snap["context_used"] = inp
                snap["input_tokens"] = inp
                snap["output_tokens"] = out
                continue
            if ptype == "task_complete":
                snap["task_complete_epoch"] = _core._codex_event_epoch(ev) or snap["task_complete_epoch"]
                text = (payload.get("last_agent_message") or payload.get("message") or "").strip()
                snap["no_agent_output"] = not bool(text)
                continue
    except OSError:
        return dict(empty)
    cu, cw = snap.get("context_used"), snap.get("context_window")
    if cu is not None and cw:
        snap["context_pct"] = round(100.0 * cu / cw, 1)
    with _CODEX_WAKE_TAIL_LOCK:
        _CODEX_WAKE_TAIL_CACHE[str(path)] = (key, dict(snap))
        _CODEX_WAKE_TAIL_CACHE.move_to_end(str(path))
        while len(_CODEX_WAKE_TAIL_CACHE) > _CODEX_WAKE_TAIL_MAX:
            _CODEX_WAKE_TAIL_CACHE.popitem(last=False)
    return snap


def build_codex_wake_status(session_id):
    """Assemble the live wake/turn breakdown for one Codex session. Reads the
    in-memory per-sid wake-event window plus a single rollout tail — no
    all-sessions scan, no subprocess. Never raises (returns a safe shell)."""
    try:
        with _core._RESUME_LEDGER_LOCK:
            dq = _core._CODEX_WAKE_EVENTS.get(session_id)
            events = list(dq) if dq else []
    except Exception:
        events = []
    now = time.time()

    def _last(evname, **match):
        for e in reversed(events):
            if e.get("event") != evname:
                continue
            if all(e.get(k) == v for k, v in match.items()):
                return e
        return None

    attempt = _last("codex_wake_attempt")
    connect = _last("codex_wake_stage", stage="connect")
    turnstart = _last("codex_wake_stage", stage="turn-start")
    ok = _last("codex_wake_ok")
    warn = _last("codex_wake_warn")
    exec_ev = _last("codex_wake_exec")
    fail = _last("codex_wake_fail")
    queued = _last("codex_wake_queued")

    attempt_epoch = float((attempt or {}).get("epoch") or 0.0)

    snap = _codex_wake_rollout_snapshot(session_id)
    app_state = _core._codex_app_server_thread_state(session_id)
    app_transport = _core._codex_app_server_transport_kind()
    ctx_used = snap.get("context_used")
    ctx_win = snap.get("context_window")
    ctx_pct = snap.get("context_pct")

    running_reached = bool(ok or exec_ev)
    tc_epoch = float(snap.get("task_complete_epoch") or 0.0)
    turn_ended = bool(running_reached and tc_epoch and tc_epoch >= (attempt_epoch - 1.0))

    # A hard failure that will not yield a turn: exec-stage failure, or a
    # queue fallback (message parked for later). A fail whose fallback is
    # "exec" is a retry, not terminal, so it is ignored here.
    hard_error = None
    if fail and float(fail.get("epoch") or 0.0) >= attempt_epoch:
        if fail.get("stage") == "exec" or fail.get("fallback") == "queue":
            hard_error = fail.get("error")
    if not hard_error and queued and float(queued.get("epoch") or 0.0) >= attempt_epoch and not turn_ended:
        hard_error = queued.get("reason") or queued.get("error")

    outcome = None
    outcome_detail = None
    if turn_ended:
        inp = snap.get("input_tokens") or 0
        out = snap.get("output_tokens") or 0
        empty = bool(snap.get("no_agent_output")) or (inp + out == 0)
        if not empty:
            outcome = "reply"
        elif ctx_pct is not None and ctx_pct >= 99:
            outcome = "empty_context_full"
            if ctx_used is not None and ctx_win:
                outcome_detail = f"context {ctx_used}/{ctx_win} ({ctx_pct}%), compact to continue"
            else:
                outcome_detail = "context full, compact to continue"
        else:
            outcome = "empty_other"
            outcome_detail = "turn ended with no output"
    elif hard_error:
        outcome = "error"
        outcome_detail = hard_error
    elif warn and float(warn.get("epoch") or 0.0) >= attempt_epoch:
        outcome = "warning"
        outcome_detail = warn.get("warning")

    active = bool(attempt) and outcome is None

    # Stage timeline. Emit a stage only once it has STARTED; the current stage
    # is the emitted one with done=false (frontend renders a spinner there).
    resume_returned = bool(turnstart or ok or exec_ev or queued or hard_error)
    threadresume_done = bool(turnstart or ok or exec_ev or hard_error)
    turnstart_started = bool(turnstart or ok or exec_ev)
    turnstart_done = bool(ok or exec_ev)
    running_done = outcome is not None

    stages = []
    if attempt or connect:
        stages.append({
            "name": "connect",
            "ts": float((connect or attempt or {}).get("epoch") or 0.0) or None,
            "done": bool(resume_returned),
        })
        stages.append({
            "name": "thread-resume",
            "ts": float((turnstart or connect or attempt or {}).get("epoch") or 0.0) or None,
            "done": bool(threadresume_done),
        })
    if turnstart_started:
        stages.append({
            "name": "turn-start",
            "ts": float((turnstart or ok or exec_ev or {}).get("epoch") or 0.0) or None,
            "done": bool(turnstart_done),
        })
    if running_reached:
        stages.append({
            "name": "running",
            "ts": float((ok or exec_ev or {}).get("epoch") or 0.0) or None,
            "done": bool(running_done),
        })

    # turn_context may sit beyond the tail window on a long session; fall back
    # to the model/effort recorded on the wake attempt row so the readout is
    # never blank during an active wake.
    model = snap.get("model") or (attempt or {}).get("model")
    effort = snap.get("effort") or (attempt or {}).get("effort")

    return {
        "sid": session_id,
        "active": active,
        "stages": stages,
        "model": model,
        "effort": effort,
        "context_used": ctx_used,
        "context_window": ctx_win,
        "context_pct": ctx_pct,
        "input_tokens": snap.get("input_tokens"),
        "output_tokens": snap.get("output_tokens"),
        "app_server_state": app_state,
        "app_server_transport": app_transport,
        "managed_app_server": app_transport == "managed",
        "warning": warn.get("warning") if warn and float(warn.get("epoch") or 0.0) >= attempt_epoch else None,
        "elapsed_s": round(now - attempt_epoch, 1) if attempt_epoch else None,
        "outcome": outcome,
        "outcome_detail": outcome_detail,
    }


def resume_session_codex(
    session_id, text, *, steer=False, _from_queue=False, idempotency_key=None,
):
    """Resume a dormant Codex thread with a new prompt via `codex exec resume`."""
    routed = _core._control_plane_engine_call(
        "codex", "resume", {
            "session_id": session_id,
            "text": text,
            "steer": bool(steer),
            "from_queue": bool(_from_queue),
        },
        idempotency_key=idempotency_key,
    )
    if routed is not None:
        return routed
    text = _core._strip_ccc_session_state_instruction(text)
    if not text:
        return {"ok": False, "error": "missing text"}
    if not steer and not _from_queue:
        queued_behind_earlier = _core._queue_codex_resume(
            session_id,
            text,
            reason="Waiting behind an earlier queued Codex message",
            only_if_pending=True,
        )
        if queued_behind_earlier is not None:
            return queued_behind_earlier
    image_paths = _core._extract_pasted_image_paths(text)
    resolved = _core._resolve_codex_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"], "code": resolved.get("code")}
    active_resume_entry = None
    for s in _core._spawned_sessions:
        if s.get("engine") == "codex" and s.get("resumed_sid") == session_id:
            try:
                if _core._poll_spawn_entry(s) is None:
                    active_resume_entry = s
                    break
            except Exception:
                pass
    row = _core._codex_thread_row(session_id) or {}
    spawned_ctx = _core._spawn_registry_entry_for_session(session_id, "codex") or {}
    cwd = (
        row.get("cwd")
        or spawned_ctx.get("cwd")
        or (active_resume_entry or {}).get("cwd")
        or _core.find_session_cwd(session_id)
    )
    cwd_error = None
    if not cwd or not Path(cwd).is_dir():
        try:
            cwd = _core.repo_from_session(session_id)["cwd"]
        except _core.RepoContextError as e:
            cwd_error = e
            cwd = None
    # Per-session override (set via the click-to-switch picker) wins over
    # the env-var default and the previous run's recorded model.
    override = _core._get_session_override(session_id)
    override_model = (override or {}).get("model") if override else None
    reasoning_effort = (override or {}).get("reasoning_effort") or ""
    model = override_model or os.environ.get("CCC_CODEX_MODEL") or row.get("model") or _core._spawn_fallback_model_for_engine("codex")
    if override_model:
        model, model_error = _core._validate_codex_model(model, require_available=True)
        if model_error:
            return {
                "ok": False,
                "error": model_error,
                "code": "codex_model_unavailable",
                "known_codex_models": list(_core._ENGINE_KNOWN_MODELS["codex"]),
            }
    _core._resume_ledger_append(
        "codex_wake_attempt", sid=session_id,
        cwd=cwd, model=model, effort=reasoning_effort, steer=bool(steer),
    )
    if steer:
        return _core._codex_steer_via_app_server(
            session_id,
            text,
            cwd=cwd,
            model=model,
            image_paths=image_paths,
        )
    app_result = _core._codex_resume_or_steer_via_app_server(
        session_id,
        text,
        cwd=cwd,
        model=model,
        image_paths=image_paths,
        allow_start=active_resume_entry is None,
        reasoning_effort=reasoning_effort,
    )
    if app_result.get("ok"):
        if (
            app_result.get("accepted")
            and app_result.get("confirmed") is not True
            and not _from_queue
        ):
            reason = (
                app_result.get("warning")
                or "Codex accepted the turn but the input is not visible yet"
            )
            queued = _core._queue_codex_resume(session_id, text, reason=reason)
            # Close the notification-vs-enqueue race: if the authoritative
            # userMessage arrived just before the durable copy was added, its
            # state marker is still available and can reconcile the copy now.
            delivered = _core._codex_app_server_thread_state(session_id)
            if (
                str(delivered.get("last_delivered_user_text") or "").strip()
                == str(text or "").strip()
                and (
                    not app_result.get("turn_id")
                    or str(delivered.get("last_delivered_user_turn_id") or "")
                    == str(app_result.get("turn_id"))
                )
            ):
                _core._consume_matching_pending_input(session_id, text)
                confirmed = dict(app_result)
                confirmed["confirmed"] = True
                confirmed["confirmation_source"] = "app-server-notification"
                return confirmed
            accepted_via = app_result.get("via")
            queued.update({
                key: value
                for key, value in app_result.items()
                if key not in ("via", "queued", "queued_reason", "error")
            })
            if accepted_via:
                queued["accepted_via"] = accepted_via
            queued["queued"] = True
            queued["queued_reason"] = reason
            return queued
        return app_result
    if app_result.get("fallback") == "queue":
        if _from_queue:
            reason = app_result.get("error") or "Codex thread is still active"
            return {
                "ok": True,
                "queued": True,
                "via": "codex-resume-queued",
                "queued_reason": reason,
                "error": reason,
            }
        queued = _core._queue_codex_resume(
            session_id,
            text,
            pid=(active_resume_entry or {}).get("pid"),
            reason=app_result.get("error"),
        )
        if app_result.get("writer"):
            queued["writer"] = app_result["writer"]
        if app_result.get("desktop_attached"):
            queued["desktop_attached"] = True
        return queued
    if active_resume_entry is not None:
        if _from_queue:
            return {
                "ok": True,
                "queued": True,
                "via": "codex-resume-queued",
                "pid": active_resume_entry.get("pid"),
            }
        return _core._queue_codex_resume(session_id, text, pid=active_resume_entry.get("pid"))
    if cwd_error is not None:
        return cwd_error.as_payload()
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"resume-codex-{session_id[:8]}-{timestamp}.log"
    repo_for_logs = _core._git_toplevel_for_existing_dir(cwd) or cwd
    log_dir = _core.repo_log_dir(repo_for_logs)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / log_filename
    cmd = [
        resolved["bin"], *_core._codex_context_window_args(), "exec", "resume",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model", model,
    ]
    if reasoning_effort:
        cmd.extend(["-c", f"model_reasoning_effort={reasoning_effort}"])
    for image_path in image_paths:
        cmd.extend(["--image", image_path])
    cmd.extend([session_id, text])
    _core._resume_ledger_append(
        "codex_wake_exec", sid=session_id,
        stage="exec", log_path=str(log_path),
    )
    log_fh = open(log_path, "w")
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            start_new_session=True,
        )
    except (FileNotFoundError, OSError) as e:
        log_fh.close()
        _core._resume_ledger_append(
            "codex_wake_fail", sid=session_id,
            stage="exec", error=str(e),
        )
        return {"ok": False, "error": str(e), "via": "codex-resume", "stage": "exec"}
    entry = {
        "pid": proc.pid,
        "name": f"resume-codex-{session_id[:8]}",
        "log": str(log_path),
        "prompt": text[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "resumed_sid": session_id,
        "fifo": None,
        "stdin_fd": None,
        "engine": "codex",
        "cwd": cwd,
        "repo_path": repo_for_logs,
        "model": model,
    }
    _core._spawned_sessions.append(entry)
    _core._record_spawn_to_registry(
        pid=proc.pid,
        name=entry["name"],
        log_path=log_path,
        cwd=cwd,
        spawned_at=timestamp,
        command_summary=text[:200],
        fifo=None,
        engine="codex",
        session_id=session_id,
        repo_path=repo_for_logs,
        model=model,
    )
    return {"ok": True, "pid": proc.pid, "log": str(log_path), "resumed": True, "via": "codex-resume"}


def _extract_codex_usage(session_id):
    empty = {
        "latest_input_tokens": 0,
        "peak_input_tokens": 0,
        "total_output_tokens": 0,
        "total_input_tokens": 0,
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": 0,
        "model": "",
        "reasoning_effort": "",
        "context_limit": 0,
        "cost_usd": 0.0,
        "cost_breakdown_usd": {"input": 0.0, "cache_creation": 0.0,
                               "cache_read": 0.0, "output": 0.0},
    }
    path = _core._resolve_codex_rollout_path(session_id)
    row = _core._codex_thread_row(session_id) or {}
    if not path:
        return empty
    latest = {}
    totals = {}
    peak = 0
    context_limit = 0
    model = row.get("model") or ""
    reasoning_effort = row.get("reasoning_effort") or ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                if ev.get("type") == "turn_context":
                    model = payload.get("model") or model
                    settings = ((payload.get("collaboration_mode") or {}).get("settings") or {})
                    effort = (
                        payload.get("effort")
                        or payload.get("reasoning_effort")
                        or (settings.get("reasoning_effort") if isinstance(settings, dict) else None)
                    )
                    reasoning_effort = effort or reasoning_effort
                if payload.get("type") != "token_count":
                    continue
                info = payload.get("info") or {}
                context_limit = _core._codex_int(info.get("model_context_window")) or context_limit
                usage = info.get("last_token_usage") or info.get("total_token_usage") or {}
                total_usage = info.get("total_token_usage") or usage
                if not isinstance(usage, dict):
                    continue
                if isinstance(total_usage, dict):
                    totals = total_usage
                latest = usage
                # Codex/OpenAI usage reports cached input as a subset of
                # input_tokens. Adding it again turns cumulative session usage
                # into impossible context-window numbers.
                window = _core._codex_int(usage.get("input_tokens"))
                peak = max(peak, window)
    except OSError:
        return empty
    if not latest:
        return {**empty, "override": _core._get_session_override(session_id)}
    latest_input = _core._codex_int(latest.get("input_tokens"))
    total_input = _core._codex_int(totals.get("input_tokens"))
    cache_read = _core._codex_int(totals.get("cached_input_tokens"))
    total_output = _core._codex_int(totals.get("output_tokens"))
    normalized_totals = {
        "total_input_tokens": max(total_input - cache_read, 0),
        "total_cache_creation_tokens": 0,
        "total_cache_read_tokens": cache_read,
        "total_output_tokens": total_output,
    }
    cost = _core._session_usage_cost("codex", model, normalized_totals)
    return {
        **empty,
        "latest_input_tokens": latest_input,
        "peak_input_tokens": peak,
        **normalized_totals,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "context_limit": context_limit,
        "override": _core._get_session_override(session_id),
        **cost,
    }


def _extract_codex_timeline(session_id):
    path = _core._resolve_codex_rollout_path(session_id)
    if not path:
        return {"events": [], "total_turns": 0}
    events = []
    pending_by_id = {}
    turn = 0
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                ts = _core._codex_event_timestamp(ev)
                if ev.get("type") == "event_msg" and payload.get("type") == "agent_message":
                    turn += 1
                    continue
                if ev.get("type") != "response_item":
                    continue
                if payload.get("type") == "function_call":
                    name = payload.get("name") or ""
                    args = _core._codex_args(payload.get("arguments"))
                    cmd = _core._codex_tool_command(name, args)
                    if not cmd:
                        continue
                    kind = None
                    subject = ""
                    if _core._TIMELINE_PR_CREATE_RE.search(cmd):
                        kind = "pr"
                        m = _core._TIMELINE_PR_TITLE_RE.search(cmd)
                        if m:
                            subject = m.group(1)
                    elif _core._TIMELINE_PUSH_RE.search(cmd):
                        kind = "push"
                    elif _core._TIMELINE_COMMIT_RE.search(cmd):
                        kind = "commit"
                        m = _core._TIMELINE_COMMIT_MSG_RE.search(cmd)
                        if m:
                            subject = m.group(1)
                    if not kind:
                        continue
                    events.append({
                        "kind": kind,
                        "turn": max(turn, 1),
                        "ts": ts,
                        "subject": subject,
                        "success": None,
                    })
                    call_id = payload.get("call_id") or ""
                    if call_id:
                        pending_by_id[call_id] = len(events) - 1
                elif payload.get("type") == "function_call_output":
                    call_id = payload.get("call_id") or ""
                    if call_id not in pending_by_id:
                        continue
                    idx = pending_by_id.pop(call_id)
                    entry = events[idx]
                    entry["success"] = True
                    text = payload.get("output") or ""
                    if entry["kind"] == "commit":
                        m = _core._TIMELINE_COMMIT_RESULT_RE.search(text)
                        if m:
                            entry["sha"] = m.group(1)
                            if not entry.get("subject"):
                                entry["subject"] = m.group(2).strip()[:200]
                    elif entry["kind"] == "pr":
                        m = _core._TIMELINE_PR_NUMBER_FROM_URL_RE.search(text)
                        if m:
                            entry["pr_number"] = int(m.group(1))
    except OSError:
        return {"events": [], "total_turns": 0}
    return {"events": events, "total_turns": turn}

