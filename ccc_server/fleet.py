"""Extracted from server.py (originally lines 53811-54833).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone, time as datetime_time
import federation
import json
import os
import subprocess
import threading
import time
import urllib.request

from ccc_server import core as _core
from ccc_server import github_quota as _github_quota

# ---------------------------------------------------------------------------
# CCC federation — cross-machine node identity, pairing, and peer protocol
#
# One CCC installation (one HOME) = one federation node. Peers are other CCC
# installations, typically on other machines, reached over SSH (executing a
# tiny HTTP client on the peer so the request hits the peer CCC on ITS OWN
# loopback — the command API is never exposed to a network) or over direct
# loopback for a second isolated CCC on this machine. Pairing establishes a
# shared secret; every peer-facing endpoint except hello/pair validates it.
# The trust model is unchanged: anything that can reach a node's loopback
# (which SSH-as-the-user grants) already has user-level control there.
# ---------------------------------------------------------------------------


def _federation_self_hello():
    """This node's identity card — safe to serve unauthenticated (contains no
    secrets; loopback callers already have user-level access)."""
    ident = federation.node_identity()
    return {
        "ok": True,
        "proto": federation.FEDERATION_PROTO_VERSION,
        "node_id": ident["node_id"],
        "display_name": ident.get("display_name") or "",
        "version": _core.__version__,
        "port": _core.PORT,
        "caps": federation.capability_manifest(_core.__version__, list(_core._ORCHESTRATION_SPAWN_ENGINES)),
        "time": time.time(),
    }


def _federation_require_peer(handler, action=None):
    """Validate the pairing headers on a peer-facing request. Sends the 403
    itself and returns None when unpaired; returns the peer entry when OK.

    `action` names the endpoint for scope enforcement -- pass it for every
    endpoint except /v1/route (which enforces scope per routed action inside
    _federation_execute_route) and endpoints safe for any paired peer
    regardless of scope (hello is unauthenticated; health and unpair need no
    scope check: health leaks nothing and unpair only removes the caller's
    own pairing). Without it, a `memory:read`-scoped peer -- meant to reach
    only the four read-only memory_* actions -- could still call any other
    peer-facing endpoint (handoff, group-chat import, sessions/repo/fleet
    inventory) since those never routed through the scope gate at all."""
    peer_id = (handler.headers.get("X-CCC-Peer") or "").strip()
    token = (handler.headers.get("X-CCC-Peer-Token") or "").strip()
    peer = federation.validate_peer_auth(peer_id, token)
    if peer is None:
        handler.send_json({"ok": False, "error": "unpaired_peer"}, 403)
        return None
    if action is not None and not _federation_peer_scope_allows(peer, action):
        handler.send_json({"ok": False, "error": "scope_forbidden",
                            "detail": f"peer scope does not permit {action!r}"}, 403)
        return None
    return peer


def _federation_touch_peer(peer_node_id, **extra):
    try:
        federation.update_peer(
            peer_node_id,
            last_seen=datetime.now().astimezone().isoformat(timespec="seconds"),
            **extra,
        )
    except OSError:
        pass


def _federation_lease_owners():
    """{session_id: owner_node} for every recorded handoff lease — one
    directory listing, no per-row file probes."""
    out = {}
    try:
        for path in (federation.leases_dir()).glob("*.json"):
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("owner_node"):
                out[path.stem] = data["owner_node"]
    except OSError:
        pass
    return out


def _federation_row_activity(row):
    """Last-activity epoch for an archive row: mtime, else start timestamp."""
    try:
        m = float(row.get("mtime") or 0)
    except (TypeError, ValueError):
        m = 0.0
    return m or _federation_row_epoch(row)


def _federation_sessions_inventory(limit=200):
    """Compact cross-repo session inventory for a peer aggregator. Served from
    the same response cache as /api/sessions?all=1 — no extra O(all) work."""
    me = federation.node_id()
    rows, _from_cache = _core._archive_all_rows_cached({
        "include_prs": False,
        "resolve_pr_states": False,
        "resolve_effective": False,
        "resolve_worktree_dirty": False,
    })
    leases = _federation_lease_owners()
    # Reverse repo map (path -> stable identity) so every row carries the
    # cross-machine repository identity, not just this node's local path.
    path_to_identity = {}
    for ident_key, mapped_path in federation.load_repo_map().items():
        path_to_identity[mapped_path.rstrip("/")] = ident_key

    def _identity_for_cwd(cwd):
        cwd = (cwd or "").rstrip("/")
        while cwd and cwd != "/":
            hit = path_to_identity.get(cwd)
            if hit:
                return hit
            cwd = os.path.dirname(cwd)
        return None

    # The archive cache is not guaranteed newest-first (delta refreshes can
    # append changed rows at the end), and the peer ships only `limit` rows,
    # so order by last activity here or recent sessions get cut (CCC-1216).
    rows = sorted(rows, key=_federation_row_activity, reverse=True)
    out = []
    for r in rows[: max(1, min(int(limit or 200), 1000))]:
        sid = r.get("session_id") or r.get("id")
        if not sid:
            continue
        lease_owner = leases.get(sid)
        out.append({
            "owned_here": lease_owner in (None, me),
            "moved_to_node": lease_owner if lease_owner and lease_owner != me else None,
            "repo_identity": _identity_for_cwd(r.get("session_cwd") or r.get("cwd")),
            "session_id": sid,
            "ref": federation.format_session_ref(me, sid),
            "node_id": me,
            "engine": r.get("engine") or r.get("source") or "claude",
            "source": r.get("source"),
            "display_name": r.get("display_name") or "",
            "first_message": (r.get("first_message") or "")[:160],
            "cwd": r.get("session_cwd") or r.get("cwd") or "",
            "branch": r.get("effective_branch") or r.get("branch") or "",
            "is_live": bool(r.get("is_live")),
            # Last activity, not start time: the aggregator sorts on this
            # and the sidebar shows its age.
            "timestamp": r.get("mtime") or r.get("timestamp"),
            "model": r.get("model"),
            "parent_session_id": r.get("parent_session_id"),
            "claude_auth_failed": bool(r.get("claude_auth_failed")),
        })
    return {
        "ok": True,
        "node_id": me,
        "observed_at": time.time(),
        "sessions": out,
        "count": len(out),
    }


# Per-peer session inventories: short TTL so the dashboard poll doesn't
# hammer transports; failures serve the last good payload LABELED stale.
_FEDERATED_SESSIONS_CACHE = {}
_FEDERATED_SESSIONS_CACHE_LOCK = threading.Lock()
_FEDERATED_SESSIONS_TTL = 10.0


def _federation_fetch_peer_sessions(peer, limit):
    node = peer["node_id"]
    now = time.time()
    with _FEDERATED_SESSIONS_CACHE_LOCK:
        cached = _FEDERATED_SESSIONS_CACHE.get(node)
        if cached and now - cached["ts"] < _FEDERATED_SESSIONS_TTL:
            return {**cached["payload"], "stale": False}, None
    try:
        payload = federation.PeerClient(peer).request(
            "GET", f"/api/federation/v1/sessions?limit={int(limit)}", timeout=20)
    except federation.PeerError as e:
        with _FEDERATED_SESSIONS_CACHE_LOCK:
            cached = _FEDERATED_SESSIONS_CACHE.get(node)
        if cached:
            return {**cached["payload"], "stale": True}, {"error": e.kind, "detail": str(e)}
        return None, {"error": e.kind, "detail": str(e)}
    with _FEDERATED_SESSIONS_CACHE_LOCK:
        _FEDERATED_SESSIONS_CACHE[node] = {"ts": now, "payload": payload}
    _federation_touch_peer(node)
    return {**payload, "stale": False}, None


# Per-peer browser-reachable web URL (for embedding the peer's own CCC). One
# routed phone_access_status call per peer per TTL, never per row; failures
# are remembered briefly so a dead peer isn't re-dialed on every poll.
_PEER_WEB_URL_CACHE = {}
_PEER_WEB_URL_LOCK = threading.Lock()
_PEER_WEB_URL_TTL = 300.0
_PEER_WEB_URL_ERR_TTL = 30.0


def _federation_peer_web_url(node_id, allow_fetch=True):
    """{"web_url", "web_url_state"} for one peer, cached. State: ok | none
    (no address published) | pin (address exists but the peer has a PIN, and
    its SameSite=Strict cookie can't ride a cross-site frame) | unknown
    (status call failed or not yet fetched)."""
    now = time.time()
    with _PEER_WEB_URL_LOCK:
        hit = _PEER_WEB_URL_CACHE.get(node_id)
        if hit and now < hit["expires"]:
            return dict(hit["entry"])
    if not allow_fetch:
        if hit:
            return dict(hit["entry"])
        return {"web_url": None, "web_url_state": "unknown"}
    from ccc_server import phone_access as _phone_access
    res = _core._federation_proxy_session_action(
        node_id, "phone_access_status", {"qr": False}, timeout=8.0)
    if isinstance(res, dict) and res.get("ok"):
        url = _phone_access.web_url_from_status(res)
        if url and res.get("pin_set"):
            entry = {"web_url": None, "web_url_state": "pin"}
        elif url:
            entry = {"web_url": url, "web_url_state": "ok"}
        else:
            entry = {"web_url": None, "web_url_state": "none"}
        ttl = _PEER_WEB_URL_TTL
    else:
        entry = {"web_url": None, "web_url_state": "unknown"}
        ttl = _PEER_WEB_URL_ERR_TTL
        if hit and hit["entry"].get("web_url"):
            entry = dict(hit["entry"])  # keep the last good address
    with _PEER_WEB_URL_LOCK:
        _PEER_WEB_URL_CACHE[node_id] = {"entry": entry, "expires": time.time() + ttl}
    return dict(entry)


def _federation_row_epoch(row):
    """Sort key for federated rows: engines report `timestamp` as epoch
    seconds or an ISO string, so normalize before comparing."""
    ts = row.get("timestamp")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str) and ts:
        try:
            return float(ts)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return 0.0


def _federation_federated_sessions(limit=200, peers_only=False):
    """One session list across every node: local + each paired peer, each
    row carrying its owning node, global ref, and staleness. With
    `peers_only` the local inventory is skipped (the sidebar already has the
    local rows) and only peer rows are returned."""
    me = _federation_self_hello()
    if peers_only:
        local = {"observed_at": time.time(), "sessions": []}
    else:
        local = _federation_sessions_inventory(limit=limit)
    sessions = []
    for row in local["sessions"]:
        row["node_name"] = me["display_name"]
        row["stale"] = False
        sessions.append(row)
    nodes = [{
        "node_id": me["node_id"], "name": me["display_name"], "self": True,
        "ok": True, "observed_at": local["observed_at"], "stale": False,
    }]
    peers = federation.load_peers()
    if peers:
        def _one(peer):
            result = _federation_fetch_peer_sessions(peer, limit)
            # Only dial the address lookup for a peer that answered; a dead
            # one reuses whatever is cached instead of a second timeout.
            web = _federation_peer_web_url(
                peer["node_id"], allow_fetch=result[0] is not None)
            return peer, result, web
        with ThreadPoolExecutor(max_workers=min(4, len(peers))) as pool:
            results = list(pool.map(_one, peers))
        for peer, (payload, err), web in results:
            entry = {
                "node_id": peer["node_id"],
                "name": peer.get("name"),
                "self": False,
                **web,
            }
            if payload:
                stale = bool(payload.get("stale"))
                entry.update({
                    "ok": err is None,
                    "stale": stale,
                    "observed_at": payload.get("observed_at"),
                })
                if err:
                    entry.update(err)
                for row in payload.get("sessions", []):
                    row["node_name"] = peer.get("name")
                    row["stale"] = stale
                    sessions.append(row)
            else:
                entry.update({"ok": False, "stale": True,
                              "observed_at": None, **(err or {})})
            nodes.append(entry)
    sessions.sort(key=_federation_row_epoch, reverse=True)
    return {"ok": True, "sessions": sessions, "nodes": nodes,
            "count": len(sessions)}


def _federation_default_branch_view(repo_path):
    """Origin default-branch name + this clone's fetched view of its SHA."""
    rc, out, _err = _core._git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], repo_path)
    default_ref = out.strip() if rc == 0 and out.strip() else ""
    branch = default_ref.rsplit("/", 1)[-1] if default_ref else ""
    if not branch:
        for cand in ("main", "master"):
            rc, _out, _err = _core._git(["rev-parse", "--verify", "--quiet", f"origin/{cand}"], repo_path)
            if rc == 0:
                branch = cand
                break
    if not branch:
        return {"branch": None, "sha": None}
    rc, out, _err = _core._git(["rev-parse", f"origin/{branch}"], repo_path)
    return {"branch": branch, "sha": out.strip() if rc == 0 else None}


def _federation_repo_inventory_payload(repo_path=None, repo_identity_key=None,
                                       fetch=False, include_prs=True):
    """Git/worktree inventory for ONE repo on THIS node. Path is validated
    here — the node that owns the filesystem does the checking."""
    if not repo_path and repo_identity_key:
        repo_path = federation.resolve_repo_path(repo_identity_key)
        if not repo_path:
            return {"ok": False, "error": "stale_mapping",
                    "detail": f"no local mapping for {repo_identity_key}"}, 404
    try:
        repo_path = _core.resolve_repo_path(repo_path)
    except _core.RepoContextError as e:
        return {"ok": False, "error": e.code, "detail": str(e)}, e.status
    ident = federation.repo_identity(repo_path) or {}
    if fetch:
        # The one explicitly-requested mutation-adjacent step a scan may do:
        # refresh remote refs. Never touches the working tree.
        _core._git(["fetch", "--quiet", "--prune", "origin"], repo_path, timeout=60)
    wt_payload = _core.list_repo_worktrees(
        repo_path, include_prs=include_prs, fresh=True)
    worktrees = []
    for wt in wt_payload.get("worktrees", []):
        entry = dict(wt)
        wt_path = entry.get("path") or ""
        rc, out, _err = _core._git(["status", "--porcelain"], wt_path)
        status_lines = [ln for ln in out.splitlines() if ln.strip()] if rc == 0 else []
        entry["dirty_files"] = len(status_lines)
        entry["staged"] = sum(1 for ln in status_lines if ln[:1].strip())
        entry["untracked"] = sum(1 for ln in status_lines if ln.startswith("??"))
        # Computed fresh (not via the 60s _unpushed_cache): an inventory scan
        # is explicitly a "look now" operation and stale counts here would
        # make the whole fleet view lie.
        rc, out, _err = _core._git(["rev-list", "--count", "@{u}..HEAD"], wt_path)
        if rc == 0 and out.strip().isdigit():
            unpushed = int(out.strip())
        else:
            # No upstream configured — count commits unreachable from EVERY
            # origin ref, the honest definition of "unpublished".
            rc, out, _err = _core._git(["rev-list", "--count", "HEAD",
                                  "--not", "--remotes=origin"], wt_path)
            unpushed = int(out.strip()) if rc == 0 and out.strip().isdigit() else None
        entry["unpublished_commits"] = unpushed
        rc, out, _err = _core._git(["rev-parse", "HEAD"], wt_path)
        entry["head_sha"] = out.strip() if rc == 0 else None
        worktrees.append(entry)
    default_view = _federation_default_branch_view(repo_path)
    for entry in worktrees:
        # Reachability PROOF for cleanup decisions: is this worktree's head
        # already contained in the fetched view of origin's default branch?
        head = entry.get("head_sha")
        merged = None
        if head and default_view.get("branch"):
            rc, _o, _e = _core._git(["merge-base", "--is-ancestor", head,
                               f"origin/{default_view['branch']}"], repo_path)
            merged = rc == 0
        entry["merged_into_default"] = merged
        entry["is_primary_clone"] = (
            os.path.realpath(entry.get("path") or "") == os.path.realpath(repo_path))
    return {
        "ok": True,
        "node_id": federation.node_id(),
        "observed_at": time.time(),
        "repo_path": repo_path,
        "repo_identity": ident.get("identity"),
        "repo_identity_kind": ident.get("kind"),
        "default_branch": default_view,
        "worktrees": worktrees,
        "orphan_prs": wt_payload.get("orphan_prs", []),
        "open_prs_count": wt_payload.get("open_prs_count", 0),
        "prs_skipped": bool(wt_payload.get("prs_skipped")),
    }, 200


def _federation_self_api(method, api_path, body=None, query=None, timeout=60.0):
    """Call one of THIS server's own endpoints over loopback. Used by the
    route executor so peer-routed actions run through exactly the same
    validation as local callers."""
    url = f"http://127.0.0.1:{_core.PORT}{api_path}"
    if query:
        pairs = {k: v for k, v in query.items() if v is not None}
        if pairs:
            url += "?" + urllib.parse.urlencode(pairs)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            parsed = json.loads(e.read().decode("utf-8", "replace"))
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            parsed.setdefault("ok", False)
            parsed.setdefault("status", e.code)
            return parsed
        return {"ok": False, "status": e.code, "error": str(parsed)[:200]}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"self-api call failed: {e}"}


# action -> (method, path, mutating). Mutating actions are deduped by req_id
# so a retried route envelope never double-executes an external effect.
_FEDERATION_ROUTE_ACTIONS = {
    "spawn": ("POST", "/api/sessions/spawn", True),
    "inject": ("POST", "/api/inject-input", True),
    "ask": ("POST", "/api/ask", False),
    "group_chat_read": ("GET", "/api/group-chat/read", False),
    "group_chat_post": ("POST", "/api/group-chat/post", True),
    "group_chat_nudge": ("POST", "/api/group-chat/nudge", True),
    "group_chat_add": ("POST", "/api/group-chat/add", True),
    "group_chat_create": ("POST", "/api/coordinate", True),
    "fleet_step": ("POST", "/api/fleet/step", True),
    "attribute": ("POST", "/api/fleet/attribute", False),
    # One-click Claude Code re-login on the node that lost it. submit is
    # deduped so a retried envelope never pastes the one-time code twice.
    "claude_auth_status": ("POST", "/api/claude-auth/status", False),
    "claude_auth_start": ("POST", "/api/claude-auth/start", False),
    "claude_auth_submit": ("POST", "/api/claude-auth/submit", True),
    "claude_auth_cancel": ("POST", "/api/claude-auth/cancel", False),
    "claude_auth_nudge": ("POST", "/api/claude-auth/nudge", True),
    # Phone access (ccc_server/phone_access.py): the Fleet page shows and
    # sets up each node's tailnet phone URL on that node's own loopback.
    "phone_access_status": ("POST", "/api/phone-access/status", False),
    "phone_access_enable": ("POST", "/api/phone-access/enable", True),
    "phone_access_disable": ("POST", "/api/phone-access/disable", True),
    "phone_access_test": ("POST", "/api/phone-access/test", False),
    # Memory (MEMORY-8, multi-machine S3): read-only, so no req_id dedupe.
    # api_path for memory_brief is rebuilt per call below (its query is a
    # URL path segment, not a query string) -- the entry here is a stand-in.
    "memory_recall": ("GET", "/api/memory/recall", False),
    "memory_shipped": ("GET", "/api/memory/shipped", False),
    "memory_brief": ("GET", "/api/memory/brief", False),
    "memory_file_history": ("GET", "/api/memory/file-history", False),
}

_MEMORY_ROUTE_ACTIONS = frozenset(
    {"memory_recall", "memory_shipped", "memory_brief", "memory_file_history"}
)

# Least-privilege pairing (multi-machine spec section 5): a peer entry can
# carry an optional `scopes` list. An ABSENT key (not just falsy -- None
# specifically) or one containing "*" is unrestricted (back-compatible with
# every peer paired before this). A peer scoped to "memory:read" can reach
# only the read-only memory_* route actions -- everything else (spawn,
# inject, group chat, ...) is refused server-side, regardless of what the
# caller asks for. An explicit but EMPTY scopes list ([]) is not the same as
# absent: it means the peer was deliberately paired with no capabilities at
# all, and must be refused everything.
_SCOPE_ACTIONS = {
    "memory:read": _MEMORY_ROUTE_ACTIONS,
}

# Bounds from the multi-machine spec section 5 ("Bounded responses"): no raw
# transcript, tool output, or file contents ever leave this function to
# begin with (the underlying memory_api/ship_graph/session_brief endpoints
# only ever returned summarized rows) -- this only bounds row count and the
# size of the already-summarized snippet/match text and overall payload.
_MEMORY_MAX_ROWS = 30
_MEMORY_SNIPPET_CAP = 400
_MEMORY_MATCH_CAP = 600
_MEMORY_RESPONSE_CAP_BYTES = 256 * 1024
_MEMORY_ARG_STR_CAP = 500


def _federation_peer_scope_allows(peer, action):
    """True unless `peer` was paired with an explicit, non-wildcard `scopes`
    list that excludes `action`. `peer=None` is a trusted internal caller
    (e.g. a direct test call, not a peer-routed one) and is unrestricted.

    Only an ABSENT `scopes` key is unrestricted -- an explicit `scopes: []`
    means the peer was deliberately paired with zero capabilities and must
    be refused every action (it must not be conflated with "absent" just
    because both are falsy)."""
    if peer is None:
        return True
    scopes = peer.get("scopes")
    if scopes is None:
        return True
    if "*" in scopes:
        return True
    allowed: set[str] = set()
    for scope in scopes:
        allowed |= _SCOPE_ACTIONS.get(scope, set())
    return action in allowed


def _federation_resolve_mapped_repo(ident_key):
    """Local clone path for a stable cross-machine repo identity on THIS
    node -- the saved map first, else a scan of known repos (paths never
    travel between nodes, so each side maps the same identity to its own
    clone). Shared by the spawn and memory_file_history route actions."""
    mapped = federation.resolve_repo_path(ident_key)
    if mapped:
        return mapped
    for candidate in _core._known_repo_paths():
        try:
            cand = federation.repo_identity(candidate)
        except Exception:
            cand = None
        if cand and cand["identity"] == ident_key:
            federation.map_repo(ident_key, candidate)
            return candidate
    return None


def _federation_cap_memory_row(row):
    if not isinstance(row, dict):
        return row
    row = dict(row)
    snippet = row.get("snippet")
    if isinstance(snippet, str) and len(snippet) > _MEMORY_SNIPPET_CAP:
        row["snippet"] = snippet[:_MEMORY_SNIPPET_CAP]
    match = row.get("match")
    if isinstance(match, str) and len(match) > _MEMORY_MATCH_CAP:
        row["match"] = match[:_MEMORY_MATCH_CAP]
    elif isinstance(match, dict):
        # memory_api.recall() populates this from session_fts.section_matches(),
        # whose text field is "snippet" (there is no "text" key anywhere in
        # that shape) -- checking the wrong key silently let an oversize
        # section snippet (up to the FTS snippet() call's own bound, not this
        # module's 600-char cap) through uncapped.
        text = match.get("snippet")
        if isinstance(text, str) and len(text) > _MEMORY_MATCH_CAP:
            row["match"] = {**match, "snippet": text[:_MEMORY_MATCH_CAP]}
    return row


def _federation_cap_memory_response(result):
    """Bound a memory_* route result to the caps above. Oversize responses
    are truncated (row count first, then dropped trailing rows if still
    over the byte cap) and flagged truncated: true rather than sent whole.

    Caps EVERY top-level list value generically, not a hardcoded name list
    ("results"/"evidence"/"history") -- memory_brief alone returns several
    more (files_touched, commits, tickets, last_user_asks, ...), and a
    hardcoded set would silently exempt whichever of those the caller didn't
    think to name (the spec's 256 KB response cap is meant to hold for the
    serialized response as a whole, not per enumerated field)."""
    if not isinstance(result, dict):
        return result
    result = dict(result)
    truncated = False
    list_keys = [k for k, v in result.items() if isinstance(v, list)]
    for key in list_keys:
        rows = result[key]
        if len(rows) > _MEMORY_MAX_ROWS:
            rows = rows[:_MEMORY_MAX_ROWS]
            truncated = True
        result[key] = [_federation_cap_memory_row(r) for r in rows]
    while list_keys:
        try:
            size = len(json.dumps(result).encode("utf-8"))
        except (TypeError, ValueError):
            break
        if size <= _MEMORY_RESPONSE_CAP_BYTES:
            break
        shrunk = False
        for key in list_keys:
            if result[key]:
                result[key] = result[key][:-1]
                shrunk = True
                truncated = True
        if not shrunk:
            break
    if truncated:
        result["truncated"] = True
    return result


def _federation_execute_route(envelope, peer=None):
    """Execute a routed action envelope on this node. Returns (payload, status).

    `peer` is the caller's own pairing entry (as returned by
    `_federation_require_peer`), used only to enforce an optional
    least-privilege `scopes` list on that peer. Pass None for a trusted
    internal caller that isn't a peer-routed request at all."""
    if not isinstance(envelope, dict):
        return {"ok": False, "error": "bad_request", "detail": "expected envelope object"}, 400
    action = envelope.get("action") or ""
    args = envelope.get("args") or {}
    if not isinstance(args, dict):
        return {"ok": False, "error": "bad_request", "detail": "args must be an object"}, 400
    spec = _FEDERATION_ROUTE_ACTIONS.get(action)
    if spec is None:
        return {"ok": False, "error": "unsupported_capability",
                "detail": f"unknown route action {action!r}"}, 400
    if not _federation_peer_scope_allows(peer, action):
        return {"ok": False, "error": "scope_forbidden",
                "detail": f"peer scope does not permit {action!r}"}, 403
    try:
        hops = int(envelope.get("hops", 0))
    except (TypeError, ValueError):
        hops = 0
    if hops <= 0:
        return {"ok": False, "error": "routing_loop",
                "detail": "hop limit exhausted"}, 400
    method, api_path, mutating = spec
    req_id = str(envelope.get("req_id") or "")
    if mutating and req_id:
        prior = federation.check_and_record_request(req_id)
        if prior is not None:
            if "result" in prior:
                return {"ok": True, "duplicate": True, "result": prior["result"]}, 200
            return {"ok": False, "error": "duplicate_request_in_progress",
                    "detail": "same req_id seen but no recorded result"}, 409
    if action == "spawn":
        # Placement was decided by the routing node; the owning node always
        # executes locally (never re-forwards to its own CCC_SSH_HOST).
        args = {**args, "remote": False}
        # Cross-node spawns carry the stable repo identity — THIS node maps
        # it to its own clone path (paths never travel between machines).
        ident_key = str(args.pop("repo_identity", "") or "").strip()
        if ident_key and not args.get("repo_path") and not args.get("cwd"):
            mapped = _federation_resolve_mapped_repo(ident_key)
            if not mapped:
                return {"ok": False, "error": "stale_mapping",
                        "detail": f"no local clone mapped for {ident_key} on "
                                  "this node"}, 404
            args["repo_path"] = mapped
    timeout = 60.0
    if action.startswith("phone_access_"):
        # Same as claude_auth: the pairing secret authorised this call.
        args = {**args, "via_route": True}
    if action.startswith("claude_auth_"):
        # The pairing secret authorised this call; don't also demand the
        # peer's own Settings preview toggle (see claude_auth_handle).
        args = {**args, "via_route": True}
        timeout = 240.0 if action == "claude_auth_submit" else 90.0
    if action == "ask":
        try:
            timeout = min(630.0, max(30.0, float(args.get("timeout_ms") or 30000) / 1000.0 + 30.0))
        except (TypeError, ValueError):
            timeout = 60.0
    if action in _MEMORY_ROUTE_ACTIONS:
        # Never let a peer's request fan this node out further — a memory
        # route action always answers from this node's own local index
        # ("memory fan-out is one hop, never transitive", federation.md).
        args = {**args, "scope": "local"}
        for key in ("q", "topic", "path"):
            if isinstance(args.get(key), str) and len(args[key]) > _MEMORY_ARG_STR_CAP:
                args[key] = args[key][:_MEMORY_ARG_STR_CAP]
        # memory_shipped's `shas` (spec section 4.1: optional, <=20) isn't
        # consumed by shipped() yet (that's S2/S4 territory) -- capped here
        # regardless, so the route-arg contract holds the moment it lands
        # and an oversize list can't already ride through unbounded today.
        if isinstance(args.get("shas"), list) and len(args["shas"]) > 20:
            args["shas"] = args["shas"][:20]
        try:
            args["limit"] = max(1, min(int(args.get("limit") or _MEMORY_MAX_ROWS), _MEMORY_MAX_ROWS))
        except (TypeError, ValueError):
            args["limit"] = _MEMORY_MAX_ROWS
        if action == "memory_file_history":
            # repo_identity + rel_path, never a raw path — paths never
            # travel between machines. THIS node maps identity to its own
            # clone, same mechanism as the spawn action above.
            ident_key = str(args.pop("repo_identity", "") or "").strip()
            rel_path = str(args.pop("rel_path", "") or "").strip()
            if not ident_key or not rel_path:
                return {"ok": False, "error": "bad_request",
                        "detail": "memory_file_history requires repo_identity and rel_path"}, 400
            mapped = _federation_resolve_mapped_repo(ident_key)
            if not mapped:
                return {"ok": False, "error": "stale_mapping",
                        "detail": f"no local clone mapped for {ident_key} on "
                                  "this node"}, 404
            # rel_path comes from the peer and must be repo-relative: an
            # absolute rel_path makes os.path.join silently discard `mapped`
            # (os.path.join('/a', '/etc/passwd') == '/etc/passwd'), and '..'
            # segments can walk back out after normpath. Reject either
            # rather than let a peer probe paths outside the repo it claimed.
            if os.path.isabs(rel_path):
                return {"ok": False, "error": "bad_request",
                        "detail": "rel_path must be repo-relative"}, 400
            candidate = os.path.normpath(os.path.join(mapped, rel_path))
            if candidate != mapped and not candidate.startswith(mapped + os.sep):
                return {"ok": False, "error": "bad_request",
                        "detail": "rel_path escapes the mapped repo"}, 400
            args["path"] = candidate
            args.pop("scope", None)  # file_history has no scope concept
        elif action == "memory_brief":
            # The local endpoint takes its query as a URL path segment
            # (/api/memory/brief/<q>), not a query string.
            q = str(args.pop("q", "") or "")[:_MEMORY_ARG_STR_CAP]
            args.pop("scope", None)  # brief resolves one local session only
            args.pop("limit", None)
            api_path = f"/api/memory/brief/{urllib.parse.quote(q, safe='')}"
    if method == "GET":
        result = _federation_self_api(method, api_path, query=args, timeout=timeout)
    else:
        result = _federation_self_api(method, api_path, body=args, timeout=timeout)
    if action in _MEMORY_ROUTE_ACTIONS:
        result = _federation_cap_memory_response(result)
    if mutating and req_id:
        federation.record_request_result(req_id, result)
    return {"ok": True, "result": result}, 200


def _federation_proxy_session_action(owner_node, action, args, timeout=60.0):
    """Proxy an inject/ask/spawn to the CCC that owns the session. Returns the
    remote action's own result dict (typed errors on transport failure)."""
    peer = federation.get_peer(owner_node)
    if not peer:
        return {"ok": False, "error": "unpaired_peer",
                "detail": f"session owner {owner_node[:13]}… is not a paired peer "
                          "of this node"}
    envelope = federation.make_route_envelope(action, args)
    try:
        routed = federation.PeerClient(peer).request(
            "POST", "/api/federation/v1/route", envelope, timeout=timeout)
    except federation.PeerError as e:
        return {"ok": False, "error": e.kind, "detail": str(e),
                "owner_node": owner_node}
    inner = routed.get("result") if isinstance(routed, dict) else None
    if not isinstance(inner, dict):
        return {"ok": False, "error": "bad_response",
                "detail": "peer returned no action result", "owner_node": owner_node}
    inner.setdefault("routed_to", owner_node)
    return inner


def _federation_resolve_target(session_ref):
    """Split a possibly node-qualified session reference.
    Returns (native_session_id, owner_node_or_None-if-local)."""
    node, native = federation.parse_session_ref(session_ref or "")
    if node and node != federation.node_id():
        return native, node
    return native, None


def _federation_find_peer(node_ref):
    """Look a peer up by node_id or display name."""
    peer = federation.get_peer(node_ref)
    if peer:
        return peer
    for p in federation.load_peers():
        if (p.get("name") or "").strip().lower() == node_ref.strip().lower():
            return p
    return None


def _federation_globalize_ref(value):
    """Qualify a bare local session id with this node's identity."""
    if not value:
        return value
    node, _native = federation.parse_session_ref(value)
    return value if node else federation.format_session_ref(federation.node_id(), value)


def _federation_spawn_on_node(node_ref, payload):
    """Route a spawn request to a paired peer. Returns (payload, status).

    - repo_path/cwd are THIS node's paths; they are translated to the stable
      repo identity and re-resolved by the target node's own mapping.
    - report_to / parent_session_id become global refs so the child's
      completion report and hierarchy survive the machine boundary.
    """
    peer = _federation_find_peer(node_ref)
    if not peer:
        return {"ok": False, "error": "unpaired_peer",
                "detail": f"no paired peer {node_ref!r}"}, 404
    args = {k: v for k, v in payload.items() if k not in ("node",)}
    repo_identity_key = str(args.pop("repo", "") or args.pop("repo_identity", "") or "").strip()
    local_hint = args.pop("repo_path", None) or args.pop("cwd", None)
    if not repo_identity_key and local_hint:
        top = _core._git_toplevel_for_existing_dir(str(local_hint))
        ident = federation.repo_identity(top) if top else None
        if not ident:
            return {"ok": False, "error": "stale_mapping",
                    "detail": f"cannot derive a repository identity from "
                              f"{local_hint!r} to place the spawn on the peer"}, 400
        repo_identity_key = ident["identity"]
    if not repo_identity_key:
        return {"ok": False, "error": "bad_request",
                "detail": "cross-node spawn needs repo (identity) or a local "
                          "repo_path/cwd to translate"}, 400
    args["repo_identity"] = repo_identity_key
    for key in ("report_to", "return_to", "reply_to", "parent_session_id",
                "parentSessionId", "parent_sid"):
        if args.get(key):
            args[key] = _federation_globalize_ref(str(args[key]))
    result = _core._federation_proxy_session_action(
        peer["node_id"], "spawn", args, timeout=120.0)
    status = 200 if result.get("ok") else 502
    if isinstance(result, dict):
        result.setdefault("node_id", peer["node_id"])
        result.setdefault("node_name", peer.get("name"))
        sid = result.get("session_id")
        if sid:
            result.setdefault("ref", federation.format_session_ref(peer["node_id"], sid))
        if result.get("spawn_id"):
            result.setdefault("spawn_ref", federation.format_session_ref(
                peer["node_id"], str(result["spawn_id"])))
    return result, status


def _federation_record_cross_node_spawn_edge(payload, proxied):
    """Multi-machine S6: after `_federation_spawn_on_node` places a spawn on
    a peer, record the child as a global ref in THIS node's own session
    graph too. Without this, only the peer's side of the edge exists (its
    `_record_spawn_to_registry` already accepts the globalized parent ref
    `_federation_spawn_on_node` sends) -- the dispatching node itself has no
    record that its session has a child living elsewhere, so `ccc brief`/
    family-tree queries against the LOCAL parent never find it.

    Best-effort: a failed/unresolved spawn (no `ref`) or a spawn with no
    parent linkage records nothing, same as a same-node spawn would.
    """
    if not (isinstance(proxied, dict) and proxied.get("ok") and proxied.get("ref")):
        return
    report_to, _ = _core._normalize_return_address(payload)
    parent, _ = _core._normalize_spawn_parent_session_id(payload, report_to=report_to)
    if not parent:
        return
    _core._session_graph_add_edge(
        parent, proxied["ref"],
        source="ccc-spawn-cross-node",
        engine=str(payload.get("engine") or "").strip(),
        resumable=False,
    )


def _federation_handle_pair_request(data):
    """Inbound pairing: a peer (which already proved loopback/SSH access)
    introduces itself with a shared secret. Store it; return our identity.

    TODO(security, needs a design decision -- spec section 5 "least
    privilege for new pairings"): this endpoint takes no `_federation_require_peer`
    check by construction (pairing is how a node BECOMES a peer), and the
    entry it stores never sets "scopes" -- so `_federation_peer_scope_allows`
    treats every self-paired node as unrestricted ("*") from here, with no
    way for THIS side of a pairing to grant only "memory:read" at pair time.
    A less-trusted third machine can only be scoped down after the fact, by
    editing peers.json out of band. Separately, `federation.upsert_peer`
    merges by `node_id` ({**existing, **entry}), so a caller who can reach
    this loopback-authenticated endpoint and already knows (or reuses) an
    existing peer's node_id overwrites that peer's secret with one of its
    own choosing -- existing "scopes" survive the merge (entry never sets
    that key), but the caller now controls that identity's secret. Neither
    is a regression from before scopes existed (same-user loopback/SSH
    access already implied full control), but a real least-privilege story
    for a less-trusted peer needs this endpoint to accept/require an
    intended `scopes` at pair time and to not silently rotate an existing
    peer's secret. Left open per MEMORY-15 (S3 security review)."""
    peer_node = str(data.get("node_id") or "").strip()
    secret = str(data.get("secret") or "").strip()
    if not peer_node or not secret:
        return {"ok": False, "error": "bad_request",
                "detail": "node_id and secret are required"}, 400
    if peer_node == federation.node_id():
        return {"ok": False, "error": "bad_request", "detail": "cannot pair with self"}, 400
    transport = data.get("transport") or {"type": "unconfigured"}
    try:
        federation.upsert_peer({
            "node_id": peer_node,
            "name": str(data.get("display_name") or "peer")[:80],
            "transport": transport,
            "secret": secret,
            "version": data.get("version"),
            "caps": data.get("caps"),
            "paired_by": "inbound",
            "last_seen": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
    except ValueError as e:
        return {"ok": False, "error": "bad_request", "detail": str(e)}, 400
    print(f"  [federation] Paired with node {peer_node[:8]}… ({data.get('display_name')})")
    return {**_federation_self_hello(), "paired": True}, 200


def _federation_pair_initiate(data):
    """Outbound pairing: probe the peer's hello, generate a secret, register
    ourselves with the peer, then store the peer locally."""
    transport = data.get("transport") or {}
    ttype = transport.get("type")
    if ttype not in ("ssh", "loopback"):
        return {"ok": False, "error": "bad_request",
                "detail": "transport.type must be ssh or loopback"}, 400
    if ttype == "loopback":
        try:
            port = int(transport.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        if not port:
            return {"ok": False, "error": "bad_request",
                    "detail": "loopback transport requires a port"}, 400
        if port == _core.PORT:
            return {"ok": False, "error": "bad_request",
                    "detail": "that port is this server"}, 400
    if ttype == "ssh" and not (transport.get("host") or "").strip():
        return {"ok": False, "error": "bad_request",
                "detail": "ssh transport requires a host"}, 400
    probe = federation.PeerClient({"node_id": "pending", "transport": transport, "secret": ""})
    try:
        hello = probe.request("GET", "/api/federation/v1/hello", timeout=20)
    except federation.PeerError as e:
        return {"ok": False, "error": e.kind, "detail": str(e)}, 502
    peer_node = hello.get("node_id")
    if not peer_node:
        return {"ok": False, "error": "bad_response",
                "detail": "peer hello carried no node_id"}, 502
    if peer_node == federation.node_id():
        return {"ok": False, "error": "bad_request",
                "detail": "that address is this node"}, 400
    secret = federation.generate_pairing_secret()
    reverse = data.get("reverse_transport")
    if not reverse and ttype == "loopback":
        reverse = {"type": "loopback", "port": _core.PORT}
    if not reverse:
        reverse = {"type": "unconfigured"}
    me = _federation_self_hello()
    try:
        probe.request("POST", "/api/federation/v1/pair", {
            "node_id": me["node_id"],
            "display_name": me["display_name"],
            "secret": secret,
            "version": me["version"],
            "caps": me["caps"],
            "transport": reverse,
        }, timeout=20)
    except federation.PeerError as e:
        return {"ok": False, "error": e.kind, "detail": f"pair handshake failed: {e}"}, 502
    stored_transport = dict(transport)
    if not stored_transport.get("port") and hello.get("port"):
        stored_transport["port"] = hello["port"]
    entry = federation.upsert_peer({
        "node_id": peer_node,
        "name": str(data.get("name") or hello.get("display_name") or "peer")[:80],
        "transport": stored_transport,
        "secret": secret,
        "version": hello.get("version"),
        "caps": hello.get("caps"),
        "paired_by": "outbound",
        "last_seen": datetime.now().astimezone().isoformat(timespec="seconds"),
    })
    return {"ok": True, "peer": _federation_public_peer(entry)}, 200


def _federation_public_peer(peer):
    """Peer entry safe to hand to the browser — never leaks the secret."""
    out = {k: v for k, v in peer.items() if k != "secret"}
    out["has_secret"] = bool(peer.get("secret"))
    return out


def _federation_peers_public():
    return {
        "ok": True,
        "self": _federation_self_hello(),
        "peers": [_federation_public_peer(p) for p in federation.load_peers()],
        "repo_map": federation.load_repo_map(),
    }


def _federation_test_peer(peer_node_id):
    peer = federation.get_peer(peer_node_id)
    if not peer:
        return {"ok": False, "error": "unpaired_peer",
                "detail": f"no paired peer {peer_node_id}"}, 404
    client = federation.PeerClient(peer)
    started = time.time()
    try:
        health = client.request("GET", "/api/federation/v1/health", timeout=20)
    except federation.PeerError as e:
        return {"ok": False, "error": e.kind, "detail": str(e),
                "latency_ms": int((time.time() - started) * 1000)}, 502
    _federation_touch_peer(peer_node_id, version=health.get("version"))
    return {
        "ok": True,
        "health": health,
        "latency_ms": int((time.time() - started) * 1000),
    }, 200


# ---------------------------------------------------------------------------
# Fleet — multi-node repository inventory
#
# One repo × node matrix with INDEPENDENT state dimensions: worktree state,
# unpublished commits, origin default-branch view, PRs, production
# deployment, and associated sessions. Every dimension carries its own
# observed_at and its own error — a clean tree, a pushed commit, a merged PR
# and a green deploy are different facts and never collapse into one flag.
# ---------------------------------------------------------------------------

FLEET_CONFIG_FILE = _core.COMMAND_CENTER_STATE_DIR / "fleet.json"

_REPO_IDENTITY_MEMO = {}
_REPO_IDENTITY_MEMO_TTL = 300.0


def _fleet_repo_identity_cached(repo_path):
    now = time.time()
    hit = _REPO_IDENTITY_MEMO.get(repo_path)
    if hit and now - hit[0] < _REPO_IDENTITY_MEMO_TTL:
        return hit[1]
    ident = None
    try:
        ident = federation.repo_identity(repo_path)
    except Exception:
        ident = None
    _REPO_IDENTITY_MEMO[repo_path] = (now, ident)
    return ident


def _fleet_config():
    try:
        data = json.loads(FLEET_CONFIG_FILE.read_text())
        if isinstance(data, dict):
            return {"pinned": list(data.get("pinned") or []),
                    "hidden": list(data.get("hidden") or []),
                    # automap=False restricts the fleet to explicitly mapped
                    # repos (isolation for tests / minimal setups).
                    "automap": bool(data.get("automap", True))}
    except (OSError, ValueError):
        pass
    return {"pinned": [], "hidden": [], "automap": True}


_FLEET_CONFIG_LOCK = threading.Lock()


def _fleet_set_pinned(identity, pinned):
    """Add/remove one repo identity from the pinned ("favourites") list.

    Read-modify-write under a lock: the Fleet page can fire several toggles
    in quick succession and this file is a single shared JSON blob, so an
    unsynchronised write would silently drop a concurrent pin. Unknown keys
    in the file are preserved.
    """
    identity = str(identity or "").strip()
    if not identity:
        return None
    with _FLEET_CONFIG_LOCK:
        try:
            raw = json.loads(FLEET_CONFIG_FILE.read_text())
            if not isinstance(raw, dict):
                raw = {}
        except (OSError, ValueError):
            raw = {}
        current = [str(x) for x in (raw.get("pinned") or []) if str(x)]
        if pinned:
            if identity not in current:
                current.append(identity)
        else:
            current = [x for x in current if x != identity]
        raw["pinned"] = current
        try:
            FLEET_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            FLEET_CONFIG_FILE.write_text(json.dumps(raw, indent=2))
        except OSError as e:
            return {"ok": False, "error": "write_failed", "detail": str(e)}
        return {"ok": True, "identity": identity,
                "pinned": bool(pinned), "all_pinned": current}


_FLEET_PRS_CACHE = {}  # legacy alias; the live cache is ccc_server/github_quota.py


def _fleet_prs(repo_path, repo_kind):
    """Open-PR dimension with an explicit error channel (unlike the UI's
    best-effort cache, a fleet scan must distinguish 'no PRs' from 'gh
    failed').

    Shares github_quota.open_prs with the worktrees modal (lane W6-1): the
    two used to keep independent 30s caches of the same 2.9-point GraphQL
    call, so a fleet scan with the modal open paid twice.
    """
    if repo_kind != "remote":
        return {"skipped": "no remote host", "observed_at": time.time()}
    prs, error = _github_quota.open_prs(repo_path, checks=True, timeout=12)
    now = time.time()
    if error:
        return {"error": error, "observed_at": now}
    out = []
    for pr in prs:
        checks = pr.get("statusCheckRollup") or []
        failing = [c.get("name") for c in checks
                   if isinstance(c, dict) and (c.get("conclusion") or "").upper()
                   in ("FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED")]
        pending = [c.get("name") for c in checks
                   if isinstance(c, dict) and not c.get("conclusion")
                   and (c.get("status") or "").upper() in ("IN_PROGRESS", "QUEUED", "PENDING")]
        out.append({
            "number": pr.get("number"),
            "title": pr.get("title"),
            "branch": pr.get("headRefName"),
            "head_sha": pr.get("headRefOid"),
            "draft": bool(pr.get("isDraft")),
            "url": pr.get("url"),
            "updated_at": pr.get("updatedAt"),
            "mergeable": pr.get("mergeable"),
            "merge_state": pr.get("mergeStateStatus"),
            "review_decision": pr.get("reviewDecision"),
            "checks_failing": failing,
            "checks_pending": pending,
            "checks_total": len(checks),
        })
    return {"open": out, "observed_at": now}


_FLEET_DEPLOY_CACHE = {}
_FLEET_DEPLOY_TTL = 60.0


def _fleet_deployment(repo_path):
    """Production-deployment dimension — separate from Git facts. Only
    consulted when the repo has a Vercel project configured."""
    project = _core._resolve_vercel_project(repo_path)
    now = time.time()
    if not project:
        return {"skipped": "no deployment provider configured",
                "observed_at": now}
    hit = _FLEET_DEPLOY_CACHE.get(repo_path)
    if hit and now - hit[0] < _FLEET_DEPLOY_TTL:
        return hit[1]
    try:
        status = _core.vercel_deploy_status(repo_path)
    except Exception as e:
        status = {"error": str(e)}
    payload = {"provider": "vercel", "observed_at": now, **(status or {})}
    _FLEET_DEPLOY_CACHE[repo_path] = (now, payload)
    return payload


def _fleet_sessions_for_repo(repo_path):
    """Sessions associated with this repo's clone or sibling worktrees —
    from the cached archive rows, no extra scanning."""
    try:
        rows, _fc = _core._archive_all_rows_cached({
            "include_prs": False, "resolve_pr_states": False,
            "resolve_effective": False, "resolve_worktree_dirty": False,
        })
    except Exception:
        return []
    me = federation.node_id()
    prefix = repo_path.rstrip("/")
    out = []
    for r in rows:
        cwd = (r.get("session_cwd") or "").rstrip("/")
        if not cwd:
            continue
        if cwd == prefix or cwd.startswith(prefix + "/") or cwd.startswith(prefix + "-wt-"):
            out.append({
                "session_id": r.get("session_id"),
                "ref": federation.format_session_ref(me, r.get("session_id") or ""),
                "display_name": r.get("display_name") or "",
                "cwd": cwd,
                "branch": r.get("effective_branch") or r.get("branch"),
                "is_live": bool(r.get("is_live")),
                "timestamp": r.get("timestamp"),
            })
        if len(out) >= 20:
            break
    return out


def _fleet_local_repo_entry(repo_path, fetch=False, include_deploy=True,
                            include_prs=True, include_sessions=True):
    """Every dimension for ONE repo on THIS node."""
    payload, status = _federation_repo_inventory_payload(
        repo_path=repo_path, fetch=fetch, include_prs=include_prs)
    if status != 200:
        return {"ok": False, "node_id": federation.node_id(),
                "repo_path": repo_path, "observed_at": time.time(),
                "error": payload.get("error"), "detail": payload.get("detail")}
    payload["prs"] = (_fleet_prs(payload["repo_path"],
                                 payload.get("repo_identity_kind"))
                      if include_prs else {"skipped": "excluded",
                                           "observed_at": time.time()})
    payload["deployment"] = (_fleet_deployment(payload["repo_path"])
                             if include_deploy else {"skipped": "excluded",
                                                     "observed_at": time.time()})
    if not include_sessions:
        # The session dimension reads the all-rows archive cache, which costs
        # a full transcript parse when cold (~45s). That is the single worst
        # thing to put in front of first paint, so the fast pass drops it.
        payload["sessions"] = []
        payload["sessions_skipped"] = True
        return payload
    payload["sessions"] = _fleet_sessions_for_repo(payload["repo_path"])
    # Fold current hook markers into the persisted provenance index while
    # we're already looking at these sessions (fleet scans are off the
    # dashboard hot path).
    _core._provenance_harvest_sidecars([s.get("session_id")
                                  for s in payload["sessions"]
                                  if s.get("session_id")])
    return payload


_FLEET_SCAN_WORKERS = 12


def _fleet_local_inventory(fetch=False, include_deploy=True, include_prs=True,
                           include_sessions=True):
    """{identity: entry} for every repo mapped (or discoverable) on this
    node. Known repos are auto-mapped by identity as a side effect so a
    freshly-paired fleet needs no hand mapping.

    Repos are scanned on a bounded thread pool. Every dimension is
    IO-bound (git subprocesses, `gh`, the deploy provider) and a real
    fleet is tens of repos, so scanning serially made the whole view wait
    on the sum of every repo's network latency.
    """
    repo_map = dict(federation.load_repo_map())
    if _fleet_config().get("automap", True):
        for rp in _core._known_repo_paths():
            ident = _fleet_repo_identity_cached(rp)
            if ident and ident["identity"] not in repo_map:
                try:
                    federation.map_repo(ident["identity"], rp)
                    repo_map[ident["identity"]] = rp
                except (OSError, ValueError):
                    pass
    out = {}
    scannable = []
    for identity_key, path in sorted(repo_map.items()):
        if not os.path.isdir(path):
            out[identity_key] = {"ok": False, "error": "stale_mapping",
                                 "detail": f"mapped path missing: {path}",
                                 "observed_at": time.time()}
            continue
        scannable.append((identity_key, path))
    if not scannable:
        return out

    def _scan(item):
        identity_key, path = item
        try:
            return identity_key, _fleet_local_repo_entry(
                path, fetch=fetch, include_deploy=include_deploy,
                include_prs=include_prs, include_sessions=include_sessions)
        except Exception as e:
            return identity_key, {"ok": False, "error": "scan_failed",
                                  "detail": str(e)[:300],
                                  "repo_path": path,
                                  "observed_at": time.time()}

    workers = min(_FLEET_SCAN_WORKERS, len(scannable))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for identity_key, entry in pool.map(_scan, scannable):
            out[identity_key] = entry
    return out


_FLEET_PEER_CACHE = {}
_FLEET_PEER_CACHE_LOCK = threading.Lock()
_FLEET_PEER_TTL = 15.0


def _fleet_fetch_peer_inventory(peer, fetch=False, include_prs=True,
                                include_deploy=True, include_sessions=True):
    node = peer["node_id"]
    # The fast pass and the enriching pass return different shapes, so they
    # cannot share a cache slot — a fast-pass hit would otherwise satisfy the
    # enrich request with PR-less data forever.
    cache_key = (node, bool(include_prs), bool(include_deploy),
                 bool(include_sessions))
    now = time.time()
    with _FLEET_PEER_CACHE_LOCK:
        hit = _FLEET_PEER_CACHE.get(cache_key)
        if hit and now - hit["ts"] < _FLEET_PEER_TTL and not fetch:
            return {**hit["payload"], "stale": False}, None
    try:
        payload = federation.PeerClient(peer).request(
            "GET", "/api/federation/v1/fleet-inventory"
            f"?fetch={1 if fetch else 0}"
            f"&prs={1 if include_prs else 0}"
            f"&deploy={1 if include_deploy else 0}"
            f"&sessions={1 if include_sessions else 0}",
            timeout=120 if fetch else 45)
    except federation.PeerError as e:
        with _FLEET_PEER_CACHE_LOCK:
            hit = _FLEET_PEER_CACHE.get(cache_key)
        if hit:
            return {**hit["payload"], "stale": True}, {"error": e.kind, "detail": str(e)}
        return None, {"error": e.kind, "detail": str(e)}
    with _FLEET_PEER_CACHE_LOCK:
        _FLEET_PEER_CACHE[cache_key] = {"ts": now, "payload": payload}
    _federation_touch_peer(node)
    return {**payload, "stale": False}, None


def _fleet_inventory_payload(fetch=False, include_deploy=True, include_prs=True,
                             include_sessions=True):
    """The Fleet view's data: repo × node matrix with per-source freshness."""
    me = _federation_self_hello()
    local = _fleet_local_inventory(fetch=fetch, include_deploy=include_deploy,
                                   include_prs=include_prs,
                                   include_sessions=include_sessions)
    nodes = [{"node_id": me["node_id"], "name": me["display_name"],
              "self": True, "ok": True, "stale": False,
              "observed_at": time.time()}]
    repos = {}
    for identity_key, entry in local.items():
        repos.setdefault(identity_key, {"identity": identity_key, "nodes": {}})
        repos[identity_key]["nodes"][me["node_id"]] = entry
        if entry.get("repo_identity_kind"):
            repos[identity_key]["kind"] = entry["repo_identity_kind"]
    peers = federation.load_peers()
    if peers:
        def _one(peer):
            return peer, _fleet_fetch_peer_inventory(
                peer, fetch=fetch, include_prs=include_prs,
                include_deploy=include_deploy,
                include_sessions=include_sessions)
        with ThreadPoolExecutor(max_workers=min(4, len(peers))) as pool:
            results = list(pool.map(_one, peers))
        for peer, (payload, err) in results:
            node_entry = {"node_id": peer["node_id"], "name": peer.get("name"),
                          "self": False}
            if payload:
                stale = bool(payload.get("stale"))
                node_entry.update({"ok": err is None, "stale": stale,
                                   "observed_at": payload.get("observed_at")})
                if err:
                    node_entry.update(err)
                for identity_key, entry in (payload.get("repos") or {}).items():
                    if isinstance(entry, dict):
                        entry["stale"] = stale
                    repos.setdefault(identity_key,
                                     {"identity": identity_key, "nodes": {}})
                    repos[identity_key]["nodes"][peer["node_id"]] = entry
            else:
                node_entry.update({"ok": False, "stale": True,
                                   "observed_at": None, **(err or {})})
            nodes.append(node_entry)
    config = _fleet_config()
    repo_list = []
    for identity_key in sorted(repos):
        if identity_key in config["hidden"]:
            continue
        item = repos[identity_key]
        item["pinned"] = identity_key in config["pinned"]
        repo_list.append(item)
    repo_list.sort(key=lambda r: (not r["pinned"], r["identity"]))
    return {
        "ok": True,
        "observed_at": time.time(),
        "nodes": nodes,
        "repos": repo_list,
    }

