# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Cross-machine memory: fan recall / shipped / brief / file-history out to
paired peers and merge the answers (multi-machine S4).

Each node indexes only itself. A `scope=all` request answers locally first,
then asks every paired peer the matching read-only `memory_*` route action
(see fleet._FEDERATION_ROUTE_ACTIONS) in parallel, under a per-peer
deadline. A peer always answers local-scope only, so fan-out is one hop and
can never recurse.

What lives here and nowhere else:

  - The per-peer circuit breaker. After a timeout / offline peer, skip it
    for 60 s, doubling to 10 min. A sleeping laptop is the normal case for
    the VM querying the Mac; the breaker keeps that side at local speed.
  - The last-answer cache: (peer, action, args) -> answer, 10 min. Used only
    when the live call fails, and always labelled `stale` with its age.
  - Recall merge: per-node ranked lists combined with RRF (k=60, equal node
    weight; bm25 scores are not comparable across indexes), ties broken by
    recency, then deduped by native session id. A deduped row is owned by
    the handoff lease's owner node, else the copy with the later activity,
    and lists the other copies in `also_on`. When a lease shows both copies
    were active after the last handoff, the session forked: both rows stay,
    labelled `fork_of`.
  - Shipped merge with the honesty rule: with a peer unreachable, the
    verdict is "NOT FOUND on reachable nodes (... unreachable)", never
    NOT SHIPPED.

Nothing here runs on the local-only request path: `scope=local` (the API
default) never imports a peer client or touches peers.json.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime

import federation

PEER_DEADLINE_S = 4.0  # ssh peer recall is ~1-1.2s idle, >1.5s beside a busy local recall (OPS-1431)
_TRANSPORT_TIMEOUT_S = 8.0
RRF_K = 60
_BREAKER_BASE_S = 60.0
_BREAKER_MAX_S = 600.0
_CACHE_TTL_S = 600.0
_CACHE_MAX = 200

_STATE_LOCK = threading.Lock()
_BREAKERS: dict[str, dict] = {}
_CACHE: dict[tuple, dict] = {}
_INFLIGHT: set[tuple] = set()

# Statuses reported per node in `nodes[]` (spec section 4.2).
OK = "ok"
TIMEOUT = "timeout"
PEER_OFFLINE = "peer_offline"
UNSUPPORTED = "unsupported_capability"
SKIPPED = "skipped_backoff"
UNPAIRED = "unpaired_peer"
ERROR = "error"

_BREAKER_TRIPPING = {TIMEOUT, PEER_OFFLINE}


def reset_state() -> None:
    """Forget breaker and cache state (tests, and a peer re-pair)."""
    with _STATE_LOCK:
        _BREAKERS.clear()
        _CACHE.clear()
        _INFLIGHT.clear()


def _cache_key(node_id: str, action: str, args: dict) -> tuple:
    norm = json.dumps(args, sort_keys=True, default=str)
    return (node_id, action, hashlib.sha256(norm.encode()).hexdigest())


def _breaker_open(node_id: str, now: float) -> dict | None:
    with _STATE_LOCK:
        b = _BREAKERS.get(node_id)
        if b and b.get("until", 0) > now:
            return dict(b)
    return None


def _record_outcome(node_id: str, status: str, now: float) -> None:
    with _STATE_LOCK:
        b = _BREAKERS.setdefault(node_id, {"backoff": 0.0, "until": 0.0, "last_ok_at": None})
        if status == OK:
            b.update(backoff=0.0, until=0.0, last_ok_at=now, last_error=None)
        elif status in _BREAKER_TRIPPING:
            backoff = min(_BREAKER_MAX_S, (b.get("backoff") or 0.0) * 2 or _BREAKER_BASE_S)
            b.update(backoff=backoff, until=now + backoff, last_error=status,
                     last_error_at=now)


def last_ok_at(node_id: str) -> float | None:
    with _STATE_LOCK:
        b = _BREAKERS.get(node_id)
        return b.get("last_ok_at") if b else None


def _cache_put(key: tuple, result: dict, now: float) -> None:
    with _STATE_LOCK:
        _CACHE[key] = {"ts": now, "result": result}
        if len(_CACHE) > _CACHE_MAX:
            for k, _v in sorted(_CACHE.items(), key=lambda kv: kv[1]["ts"])[: len(_CACHE) - _CACHE_MAX]:
                _CACHE.pop(k, None)


def _cache_get(key: tuple, now: float) -> dict | None:
    with _STATE_LOCK:
        hit = _CACHE.get(key)
    if hit and now - hit["ts"] <= _CACHE_TTL_S:
        return hit
    return None


def _classify(err: Exception) -> str:
    kind = getattr(err, "kind", "") or ""
    text = str(err)
    if kind == "http_error" and ("unsupported_capability" in text or "unknown route action" in text):
        # An older peer that predates the memory_* route actions answers
        # 400 unsupported_capability; report it as such, never drop silently.
        return UNSUPPORTED
    if kind in (TIMEOUT, PEER_OFFLINE, UNSUPPORTED, UNPAIRED):
        return kind
    return ERROR


def _call_peer(peer: dict, action: str, args: dict, timeout: float) -> dict:
    """One routed memory action against one peer. Never raises. Records the
    outcome itself, so a call that finishes after the caller's deadline
    still closes the breaker and fills the cache for the next query."""
    node = peer.get("node_id") or ""
    key = _cache_key(node, action, args)
    t0 = time.time()
    try:
        return _call_peer_inner(peer, node, key, action, args, timeout, t0)
    finally:
        with _STATE_LOCK:
            _INFLIGHT.discard(key)


def _call_peer_inner(peer, node, key, action, args, timeout, t0) -> dict:
    try:
        envelope = federation.make_route_envelope(action, args, hops=1)
        routed = federation.PeerClient(peer).request(
            "POST", "/api/federation/v1/route", envelope, timeout=timeout, pipe=True)
        inner = routed.get("result") if isinstance(routed, dict) else None
        if not isinstance(inner, dict):
            raise federation.PeerError("bad_response", "peer returned no action result")
        if inner.get("ok") is False and inner.get("error"):
            raise federation.PeerError(str(inner.get("error")), str(inner.get("detail") or ""))
        status, detail, result = OK, "", inner
    except federation.PeerError as e:
        status, detail, result = _classify(e), str(e)[:200], None
    except Exception as e:  # transport bugs must not break the local answer
        status, detail, result = ERROR, f"{type(e).__name__}: {e}"[:200], None
    now = time.time()
    _record_outcome(node, status, now)
    if result is not None:
        _cache_put(key, result, now)
    return {"status": status, "detail": detail, "result": result,
            "latency_ms": int((now - t0) * 1000)}


def fan_out(action: str, args: dict, deadline_s: float = PEER_DEADLINE_S,
            peers: list[dict] | None = None) -> list[dict]:
    """Ask every paired peer `action` in parallel. Returns one entry per peer:
    {node_id, name, status, latency_ms, result|None, stale, cache_age_s,
    detail, last_ok_at}. A peer that misses the deadline is reported as
    `timeout` (its call keeps running and still fills the cache for next
    time); a failed peer falls back to its cached answer, labelled stale."""
    if peers is None:
        peers = federation.load_peers()
    now = time.time()
    out: list[dict] = []
    live: list[tuple[dict, dict]] = []
    for peer in peers:
        node = peer.get("node_id") or ""
        entry = {"node_id": node, "name": peer.get("name") or node[:8],
                 "status": OK, "latency_ms": 0, "result": None,
                 "stale": False, "cache_age_s": None, "detail": ""}
        out.append(entry)
        transport = (peer.get("transport") or {}).get("type")
        if transport == "unconfigured":
            entry["status"] = UNSUPPORTED
            entry["detail"] = "no transport configured back to this peer"
            continue
        b = _breaker_open(node, now)
        if b:
            entry["status"] = SKIPPED
            entry["detail"] = f"backing off after {b.get('last_error') or 'failure'}"
            continue
        key = _cache_key(node, action, args)
        with _STATE_LOCK:
            busy = key in _INFLIGHT
            if not busy:
                _INFLIGHT.add(key)
        if busy:
            # The same question to this peer (e.g. a cold ssh connect that
            # overran its deadline) is still running; don't stack another.
            entry["status"] = TIMEOUT
            entry["detail"] = "previous request to this peer still running"
            continue
        live.append((peer, entry))

    if live:
        pool = ThreadPoolExecutor(max_workers=min(4, len(live)))
        # The transport timeout is longer than the deadline on purpose: a
        # cold ssh connect takes ~2-3 s, and letting it finish in the
        # background warms the connection for the next query.
        futures = {pool.submit(_call_peer, peer, action, args, max(deadline_s, _TRANSPORT_TIMEOUT_S)): entry
                   for peer, entry in live}
        done, _pending = wait(futures, timeout=deadline_s)
        for fut, entry in futures.items():
            if fut in done:
                entry.update(fut.result())
            else:
                entry["status"] = TIMEOUT
                entry["latency_ms"] = int(deadline_s * 1000)
                entry["detail"] = f"no answer within {deadline_s:g}s"
                # No breaker trip here: the call is still running and
                # records its own outcome (transport timeout or offline
                # trips the breaker; a late success closes it).
        pool.shutdown(wait=False, cancel_futures=False)

    now = time.time()
    for entry in out:
        entry["last_ok_at"] = last_ok_at(entry["node_id"])
        if entry["status"] != OK:
            hit = _cache_get(_cache_key(entry["node_id"], action, args), now)
            if hit:
                entry["result"] = hit["result"]
                entry["stale"] = True
                entry["cache_age_s"] = int(now - hit["ts"])
    return out


def _with_local(local_fn, action: str, args: dict, peers=None):
    """Run the local answer and the peer fan-out at the same time, so the
    wall time is max(local, peer deadline), not their sum. Returns
    (local_result, local_ms, peer_entries)."""
    box: dict = {}

    def run_local():
        t0 = time.time()
        try:
            box["result"] = local_fn()
        except Exception as e:  # surfaced after the join, like a local call
            box["error"] = e
        box["ms"] = int((time.time() - t0) * 1000)

    t = threading.Thread(target=run_local, name="memory-fanout-local", daemon=True)
    t.start()
    entries = fan_out(action, args, peers=peers)
    t.join()
    if "error" in box:
        raise box["error"]
    return box["result"], box["ms"], entries


def _self_node() -> dict:
    ident = federation.node_identity()
    return {"node_id": ident.get("node_id") or "", "name": ident.get("display_name") or "this machine"}


def _node_summary(entry: dict, result: dict | None) -> dict:
    out = {
        "node_id": entry["node_id"],
        "name": entry["name"],
        "status": entry["status"],
        "latency_ms": entry.get("latency_ms", 0),
        "indexing": bool((result or {}).get("indexing")),
    }
    if entry.get("self"):
        out["self"] = True
    if entry.get("stale"):
        out["stale"] = True
        out["cache_age_s"] = entry.get("cache_age_s")
    if entry.get("detail"):
        out["detail"] = entry["detail"]
    if entry.get("last_ok_at"):
        out["last_ok_at"] = entry["last_ok_at"]
    return out


def _envelope(local_node: dict, local_ms: int, local_result: dict, peer_entries: list[dict]) -> dict:
    nodes = [_node_summary({**local_node, "status": OK, "latency_ms": local_ms, "self": True},
                           local_result)]
    nodes += [_node_summary(e, e.get("result")) for e in peer_entries]
    unreachable = [e["name"] for e in peer_entries if e["status"] != OK]
    return {"nodes": nodes, "peers_unreachable": unreachable, "partial": bool(unreachable)}


# -- recall ------------------------------------------------------------------


def _norm_sid(sid: str) -> str:
    return (sid or "").removeprefix("session_")


def _tag_rows(rows: list[dict], node: dict, local: bool, stale: bool = False) -> list[dict]:
    tagged = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("session_id"):
            continue
        r = dict(r)
        r["node_id"] = node["node_id"]
        r["node_name"] = node["name"]
        r["ref"] = federation.format_session_ref(node["node_id"], r["session_id"])
        r["local"] = local
        if stale:
            r["stale"] = True
        tagged.append(r)
    return tagged


def _iso_to_epoch(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S%z",):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def merge_recall(node_lists: list[list[dict]], limit: int) -> list[dict]:
    """RRF across per-node ranked lists, then dedupe by native session id.

    Each input list is one node's rows, already in that node's rank order
    and already tagged (`node_id`, `ref`, `local`). Rows may carry
    `last_activity_ts`, `lease_owner` and `lease_handoff_at` (added by
    memory_api.recall); all are optional so an older peer still merges."""
    scored: list[tuple[float, float, dict]] = []
    for rows in node_lists:
        for rank, row in enumerate(rows, 1):
            score = 1.0 / (RRF_K + rank)
            scored.append((score, float(row.get("last_activity_ts") or 0.0), row))

    groups: dict[str, list[tuple[float, float, dict]]] = {}
    order: list[str] = []
    for item in scored:
        key = _norm_sid(item[2]["session_id"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged: list[tuple[float, float, dict]] = []
    for key in order:
        copies = groups[key]
        if len({c[2]["node_id"] for c in copies}) == 1:
            best = max(copies, key=lambda c: (c[0], c[1]))
            merged.append(best)
            continue
        best_score = max(c[0] for c in copies)
        lease_owner = next((c[2].get("lease_owner") for c in copies if c[2].get("lease_owner")), None)
        handoff_at = max((_iso_to_epoch(c[2].get("lease_handoff_at")) for c in copies), default=0.0)
        if lease_owner and any(c[2]["node_id"] == lease_owner for c in copies):
            owner = next(c for c in copies if c[2]["node_id"] == lease_owner)
        else:
            owner = max(copies, key=lambda c: (c[1], c[2].get("local", False)))
        others = [c for c in copies if c is not owner]
        forked = bool(handoff_at) and all(c[1] > handoff_at for c in copies)
        if forked:
            owner_ref = owner[2]["ref"]
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(handoff_at))
            merged.append((best_score, owner[1], {**owner[2], "forked": True}))
            for c in others:
                merged.append((c[0], c[1], {**c[2], "forked": True,
                                            "fork_of": owner_ref, "fork_at": when}))
            continue
        row = dict(owner[2])
        row["also_on"] = [{"node_id": c[2]["node_id"], "node_name": c[2].get("node_name"),
                           "ts": c[1] or None} for c in others]
        merged.append((best_score, owner[1], row))

    merged.sort(key=lambda m: (m[0], m[1]), reverse=True)
    return [m[2] for m in merged[:limit]]


def recall_all(query: str, limit: int, local_recall) -> dict:
    """scope=all recall. `local_recall(query, limit)` is memory_api's local
    recall, passed in so this module never imports memory_api (no cycle)."""
    node = _self_node()
    local, local_ms, entries = _with_local(
        lambda: local_recall(query, limit), "memory_recall", {"q": query, "limit": min(limit, 30)})
    lists = [_tag_rows(local.get("results") or [], node, local=True)]
    for e in entries:
        res = e.get("result") or {}
        lists.append(_tag_rows(res.get("results") or [],
                               {"node_id": e["node_id"], "name": e["name"]},
                               local=False, stale=e.get("stale", False)))
    out = dict(local)
    out["results"] = merge_recall(lists, limit)
    out["indexing"] = bool(local.get("indexing")) or any(
        (e.get("result") or {}).get("indexing") for e in entries if e["status"] == OK)
    out.update(_envelope(node, local_ms, local, entries))
    out["scope"] = "all"
    return out


# -- shipped -----------------------------------------------------------------


def _verdict_rank(result: dict) -> int:
    v = str((result or {}).get("verdict") or "")
    if v == "SHIPPED":
        return 3
    if v.startswith("PUSHED"):
        return 2
    if v.startswith("COMMITTED ON"):
        return 1
    if not v and (result or {}).get("shipped"):
        return 3  # an older peer that only reports the boolean
    return 0


def _since(ts: float | None) -> str:
    return f" since {time.strftime('%H:%M', time.localtime(ts))}" if ts else ""


def shipped_all(topic: str, local_shipped) -> dict:
    node = _self_node()
    local, local_ms, entries = _with_local(
        lambda: local_shipped(topic), "memory_shipped", {"topic": topic})

    for ev in local.get("evidence") or []:
        ev.setdefault("node_name", node["name"])
    candidates = [(node, local, False)]
    for e in entries:
        res = e.get("result")
        if isinstance(res, dict) and e["status"] == OK:
            candidates.append(({"node_id": e["node_id"], "name": e["name"]}, res, False))
        elif isinstance(res, dict) and e.get("stale"):
            # A cached answer can only make the verdict MORE positive; it
            # never stands in for an unreachable peer saying "not found".
            candidates.append(({"node_id": e["node_id"], "name": e["name"]}, res, True))

    best_node, best, best_stale = max(candidates, key=lambda c: (_verdict_rank(c[1]), c[0] is node))
    out = dict(local)
    out.update(_envelope(node, local_ms, local, entries))
    out["scope"] = "all"
    unreachable = [e for e in entries if e["status"] != OK]

    if _verdict_rank(best) > 0:
        if best is not local:
            evidence = []
            for ev in best.get("evidence") or []:
                evidence.append({**ev, "node_name": best_node["name"]})
            out["evidence"] = evidence + [ev for ev in (local.get("evidence") or [])
                                          if ev.get("commit") not in {x.get("commit") for x in evidence}]
            out["verdict"] = best.get("verdict") or "SHIPPED"
            out["shipped"] = bool(best.get("shipped", True))
            out["confidence"] = best.get("confidence", local.get("confidence"))
            for k in ("origin_freshness", "tickets"):
                if best.get(k):
                    out[k] = best[k]
            if best_stale:
                out["verdict_stale"] = True
        out["verdict_node"] = best_node["name"]
        return out

    checked = [node["name"]] + [e["name"] for e in entries if e["status"] == OK]
    age_note = ""
    fresh = local.get("origin_freshness") or {}
    if isinstance(fresh.get("age_s"), (int, float)):
        age_note = f"; {fresh.get('origin_ref') or 'origin'} fetched {max(0, int(fresh['age_s'] // 60))} min ago"
    unreach_note = "".join(f"; {e['name']} unreachable{_since(e.get('last_ok_at'))}"
                           for e in unreachable)
    out["verdict"] = f"NOT FOUND on reachable nodes ({', '.join(checked)}{age_note}{unreach_note})"
    out["shipped"] = False
    return out


# -- brief -------------------------------------------------------------------


def brief_all(query: str, local_brief) -> dict:
    """Local first. Only when this node can't resolve the session (or the
    query is a global ref naming a peer) ask peers; the first peer that
    resolves it wins, tagged with its node. `node_status` (not `nodes`,
    which brief already uses for its lineage-ref map) reports each peer."""
    node = _self_node()
    owner, native = federation.parse_session_ref(query or "")
    peers = federation.load_peers()
    if owner and owner != node["node_id"]:
        peers = [p for p in peers if p.get("node_id") == owner]
        query = native
        local = {"query": query, "session_id": None, "alternates": [], "found": False}
        local_ms = 0
    else:
        t0 = time.time()
        local = local_brief(native if owner else query)
        local_ms = int((time.time() - t0) * 1000)
        if local.get("found"):
            local = dict(local)
            local.update(node_id=node["node_id"], node_name=node["name"], local=True)
            return local
    entries = fan_out("memory_brief", {"q": query}, peers=peers)
    env = _envelope(node, local_ms, local, entries)
    for e in entries:
        res = e.get("result")
        if isinstance(res, dict) and res.get("found"):
            out = dict(res)
            out.update(node_id=e["node_id"], node_name=e["name"], local=False,
                       ref=federation.format_session_ref(e["node_id"], res.get("session_id") or ""))
            if e.get("stale"):
                out["stale"] = True
            out["node_status"] = env["nodes"]
            out["peers_unreachable"] = env["peers_unreachable"]
            out["partial"] = env["partial"]
            return out
    out = dict(local)
    out["node_status"] = env["nodes"]
    out["peers_unreachable"] = env["peers_unreachable"]
    out["partial"] = env["partial"]
    return out


# -- file history ------------------------------------------------------------


def file_history_all(path: str, repo: str, limit: int, local_history, resolve_repo) -> dict:
    """`resolve_repo(path, repo)` -> (repo_name, repo_root, rel_path). Peers
    get the stable repo identity plus the repo-relative path, never a local
    absolute path; each maps the identity to its own clone."""
    node = _self_node()
    t0 = time.time()
    local = local_history(path, repo=repo, limit=limit)
    local_ms = int((time.time() - t0) * 1000)
    _name, root, rel = resolve_repo(path, repo)
    ident = federation.repo_identity(root) if root else None
    entries: list[dict] = []
    if ident and rel:
        entries = fan_out("memory_file_history",
                          {"repo_identity": ident["identity"], "rel_path": rel, "limit": min(limit, 30)})
    merged: list[dict] = []
    seen_commits: dict[str, dict] = {}
    for e in local.get("history") or []:
        e = {**e, "node_name": node["name"], "local": True}
        if e.get("kind") == "commit":
            seen_commits[e.get("hash") or ""] = e
        merged.append(e)
    for pe in entries:
        for e in (pe.get("result") or {}).get("history") or []:
            if not isinstance(e, dict):
                continue
            if e.get("kind") == "commit":
                h = e.get("hash") or ""
                if h in seen_commits:  # same commit, both clones have it
                    seen_commits[h].setdefault("also_on", []).append(pe["name"])
                    continue
                e = {**e, "node_name": pe["name"], "local": False}
                seen_commits[h] = e
            else:
                e = {**e, "node_name": pe["name"], "local": False,
                     "ref": federation.format_session_ref(pe["node_id"], e.get("session_id") or "")}
            if pe.get("stale"):
                e["stale"] = True
            merged.append(e)
    merged.sort(key=lambda e: e.get("ts") or 0, reverse=True)
    out = dict(local)
    out["history"] = merged[:limit]
    out.update(_envelope(node, local_ms, local, entries))
    out["scope"] = "all"
    if not ident or not rel:
        out["fanout_skipped"] = "path is not inside a known repo with a stable identity"
    return out
