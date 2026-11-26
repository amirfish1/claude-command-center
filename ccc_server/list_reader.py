"""Serve GET /api/conversations/list from the conv_reader child (CCC-679).

Late in a dashboard's uptime, sidebar list reads waited on the dashboard's
GIL: server wall time ~1 s against ~300 ms of CPU, while conversation opens
served by the conv_reader child (CCC-143) stayed at 50-450 ms in the same
windows. This module lets that child answer list reads on its direct port
with the dashboard's own handler code, so the read only competes with the
child's light load.

The child never builds the archive. It serves the snapshot the dashboard
publishes to the archive store (``archive_store``):

* Freshness: before each list read (at most once a second) it reads the
  store's ``variants.cached_at``. A newer stamp reloads the hot rows in a
  background thread and publishes them through the dashboard's own
  ``_archive_serve_cache_store``. That diffs against the previous snapshot,
  so delta tokens and the prebuilt body cache keep working across reloads.
* Mutations: the dashboard forwards every serve-cache clear and restamp
  (archive, trash, lanes, idea tickets, new transcripts) over the private
  socket before its mutation request answers. As a safety net, the child
  also restamps when a lifecycle or lane sidecar changes on disk.
* ACP sessions live only in the dashboard's memory. The dashboard pushes a
  scalar copy of its ACP session state whenever it changes. Until the first
  push lands (about a second after the child's socket appears), the child
  uses the dashboard's saved ACP registry files, which list the sessions
  that were attached when last saved.
* Start-up: the first store load finishes before the child binds the shared
  direct port, so a new child never takes connections it can only decline.
* Side effects: background refreshes, list-body warmers, disk-body saves,
  history-layer publication and every archive-store write are disabled in
  the child. Two processes never write the same files.

A declined read answers 503; the browser then reads from the dashboard
exactly as before. ``CCC_LIST_READER=0`` turns the child path off.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

SYNC_INTERVAL_S = 1.0

_lock = threading.Lock()
_state = {
    "server": None,
    "loaded": False,       # first store load published
    "acp": False,          # first ACP push received
    "stamps": None,        # {variant key: cached_at} last published
    "checked": 0.0,
    "reloading": False,
    "sidecars": None,
    "reloads": 0,
    "reload_ms": None,
    "warms": 0,
    "warm_ms": None,
    "warming": False,      # a body warm is running
    "warm_again": False,   # a mutation landed during it: warm once more
    "busy": [],            # recent [kind, start, end|None] reload/warm spans
    "windows": {},         # {window: monotonic time of its last list read}
    "mutations": 0,
    "declined": 0,
    "served": 0,
    "errors": 0,
    "last_error": None,
}


def enabled():
    return (os.environ.get("CCC_LIST_READER", "1").strip().lower()
            not in ("0", "false", "no", "off"))


def ready():
    with _lock:
        return bool(_state["loaded"] and _state["acp"])


def status():
    with _lock:
        return {key: _state[key] for key in (
            "loaded", "acp", "reloads", "reload_ms", "warms", "warm_ms", "mutations", "declined",
            "served", "errors", "last_error")} | {"enabled": enabled(),
                                                 "windows": sorted(_state["windows"])}


# ------------------------------------------------------------------ child


def setup(server_mod):
    """Make the imported dashboard module safe to serve list reads from.

    Called once in the child after ``import server``. Each patch replaces a
    module global, which the list code looks up at call time."""
    from ccc_server import archive_store

    with _lock:
        _state["server"] = server_mod
    # No detached rebuilds: rows come from the dashboard's publications.
    server_mod._archive_refresh_slot_available = lambda key, now: False
    # Ticket titles come from the dashboard's published WatchTower snapshot,
    # as in the other detached archive processes. Otherwise every reload whose
    # 30 s ticket memo had expired ran a fresh `wt list` (13 s) inside the
    # reload, which held each publication back by ~20 s (CCC-691).
    server_mod._WT_DISPLAY_SNAPSHOT_ONLY = True
    # No list-body warmers, disk-body saves or history-layer publication.
    server_mod._archive_list_start_warm = lambda slot, warm: False
    server_mod._archive_list_disk_save = lambda *a, **k: False
    server_mod._archive_history_start_publication = lambda *a, **k: None
    server_mod._save_archive_response_cache = lambda *a, **k: False
    # While a reload or warm is rebuilding bodies, or only the live-session
    # rows changed, a page load gets the previous body instead of waiting on
    # a rebuild (CCC-691).
    server_mod._ARCHIVE_LIST_STALE_OK = stale_ok
    # Belt and braces: the child must never write the archive store.
    original_connect = archive_store._connect

    def read_only_connect(cache_file, *, write=False, shared_reader=False):
        if write:
            raise sqlite3.OperationalError("conv reader opens the archive store read-only")
        return original_connect(cache_file, shared_reader=shared_reader)

    archive_store._connect = read_only_connect
    original_diag = server_mod.record_diag

    def record_diag(kind, ms, detail=None):
        if kind == "list_server" and isinstance(detail, dict):
            detail = dict(detail, reader=True, **busy_overlap(ms))
        return original_diag(kind, ms, detail)

    server_mod.record_diag = record_diag
    try:
        _provisional_acp(server_mod)
    except Exception as exc:
        _note_error(exc)
    # Synchronous: serve() binds the direct port only after this returns.
    # Unhydrated, like the dashboard's own cold serve; hydration (seconds on
    # cold file caches) follows in the background.
    try:
        _reload(hydrate=False)
    except Exception as exc:
        _note_error(exc)
        return
    with _lock:
        _state["reloading"] = True
    threading.Thread(target=_reload_in_background, name="list-reader-hydrate", daemon=True).start()


def _provisional_acp(srv):
    """The dashboard's saved ACP registry, until its first live push."""
    sessions = {}
    for harness in list(getattr(srv, "_ACP_HARNESSES", {}) or {}):
        try:
            with open(srv._acp_state_file(harness), encoding="utf-8") as fh:
                saved = (json.load(fh) or {}).get("sessions") or {}
        except (OSError, ValueError, AttributeError):
            continue
        attached = {str(sid): dict(st, attached=True) for sid, st in saved.items() if isinstance(st, dict)}
        if attached:
            sessions[harness] = attached
    with _lock:
        if _state["acp"]:
            return
        srv._ACP_SESSION_STATE.clear()
        srv._ACP_SESSION_STATE.update(sessions)
        _state["acp"] = "provisional"


def _note_error(exc):
    with _lock:
        _state["errors"] += 1
        _state["last_error"] = repr(exc)[:300]
    print(f"  [list-reader] {exc!r}", flush=True)


def _store_stamps(srv):
    """{variant key: cached_at} straight from the store, or None."""
    from ccc_server import archive_store

    path = archive_store.database_path(srv._ARCHIVE_RESPONSE_CACHE_FILE)
    if not path.exists():
        return None
    con = archive_store._connect(srv._ARCHIVE_RESPONSE_CACHE_FILE)
    try:
        return {str(key): float(stamp) for key, stamp in
                con.execute("SELECT key, cached_at FROM variants")}
    finally:
        con.close()


def _sidecar_stamp(srv):
    stamps = []
    for name in ("ARCHIVED_CONVERSATIONS_FILE", "TRASHED_CONVERSATIONS_FILE",
                 "SESSION_LANE_OVERRIDES_FILE"):
        path = getattr(srv, name, None)
        try:
            st = os.stat(path) if path is not None else None
            stamps.append((st.st_mtime_ns, st.st_size) if st else None)
        except OSError:
            stamps.append(None)
    return tuple(stamps)


def _reload(hydrate=True):
    """Load the store's hot rows and publish them like a dashboard refresh.

    The store keeps hot rows as persisted, before the per-serve overlays
    (spawn origin, live registry state, question/sidecar defaults). The
    dashboard re-layers those in ``_rehydrate_archive_cached_rows`` on every
    refresh, so the child must too or its bodies drift from the dashboard's.
    ``hydrate=False`` is the startup fast path, mirroring the dashboard's own
    cold serve; a hydrated reload follows immediately in the background.
    """
    srv = _state["server"]
    from ccc_server import archive_snapshot, archive_store
    from ccc_server.archive_store import HotRows

    started = time.perf_counter()
    sidecars = _sidecar_stamp(srv)
    data = archive_store.load(srv._ARCHIVE_RESPONSE_CACHE_FILE, hot=True, retain_hot_tokens=True)
    if not data:
        raise RuntimeError("archive store has no snapshot")
    data = archive_snapshot.upgrade_driver_metadata(
        data, srv._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION, srv._ARCHIVE_RESPONSE_CACHE_BUILD_ID)
    if (data.get("schema_version") != srv._ARCHIVE_RESPONSE_CACHE_SCHEMA_VERSION
            or data.get("build_id") != srv._ARCHIVE_RESPONSE_CACHE_BUILD_ID):
        # A newer dashboard build owns the store; this child is about to be
        # replaced. Keep declining rather than serve another schema.
        raise RuntimeError("archive store schema differs from this build")
    keep = {}
    for key, entry in (data.get("entries") or {}).items():
        if isinstance(key, str) and isinstance(entry, dict) and isinstance(entry.get("conversations"), list):
            keep[key] = {"cached_at": float(entry.get("cached_at") or 0),
                         "conversations": entry["conversations"],
                         "signature": entry.get("signature") or None}
    if not keep:
        raise RuntimeError("archive store has no entries")
    with srv._ARCHIVE_RESPONSE_CACHE_LOCK:
        srv._ARCHIVE_RESPONSE_CACHE.update(keep)
    srv._ARCHIVE_RESPONSE_CACHE_LOADED = True
    base_key = srv._archive_response_cache_key()
    for key, entry in keep.items():
        with srv._archive_serve_lock:
            known = key in srv._archive_serve_cache
            generation = srv._archive_serve_generation
        if not known and key != base_key:
            continue  # its first read takes the ordinary cold path
        source = entry["conversations"]
        if isinstance(source, HotRows):
            rows = srv._overlay_conversation_lifecycle_flags(list(source), copy_on_change=True)
            tokens = source.tokens
        else:
            rows = srv._overlay_conversation_lifecycle_flags([dict(r) for r in source if isinstance(r, dict)])
            tokens = None
        if hydrate:
            rows = srv._rehydrate_archive_cached_rows(rows, preselected=True)
            tokens = None  # proofs only fit the exact persisted row objects
        partial = str(entry.get("signature") or "").startswith("partial:")
        srv._archive_serve_cache_store(key, rows, generation, ts=entry["cached_at"],
                                       partial=partial, persisted_tokens=tokens)
    with _lock:
        _state["stamps"] = {key: entry["cached_at"] for key, entry in keep.items()}
        _state["sidecars"] = sidecars
        _state["loaded"] = True
        _state["reloads"] += 1
        _state["reload_ms"] = round((time.perf_counter() - started) * 1000.0, 1)


# Sidebar windows whose full body is prebuilt after each reload (CCC-686).
# A page load (no delta token) or a token minted by the other process (the
# dashboard fallback, a previous child) needs a full 1d body. After every
# publication the first such read rebuilt it on the request path: 0.5-3 s,
# the whole tail of the list_read gate while deltas stayed under 800 ms.
WARM_WINDOWS = ("1d",)
# Any other window a browser read from this child within this long is
# prebuilt too (CCC-690): a 7d sidebar paid the same 0.6-1.5 s rebuild on
# every page load after a publication (archive_load p95 2067 ms vs 1000 ms),
# while unused windows (8h, all) should cost nothing.
RECENT_WINDOW_S = 24 * 3600
_OPTIONAL_WINDOWS = ("8h", "7d", "all")
# The browser's 7d read asks for row layouts, a separate body (CCC-691): the
# plain 7d warm never served a real page load.
LAYOUTS = "7d+layouts"


def note_window(window, now=None, *, layouts=False):
    """Remember that a list read asked for ``window`` (raw query value)."""
    window = str(window or "").strip().lower()
    if window not in _OPTIONAL_WINDOWS:
        return
    if layouts and window == "7d":
        window = LAYOUTS
    with _lock:
        _state["windows"][window] = time.monotonic() if now is None else now


def warm_windows(now=None):
    now = time.monotonic() if now is None else now
    with _lock:
        recent = [w for w in _OPTIONAL_WINDOWS + (LAYOUTS,)
                  if now - _state["windows"].get(w, float("-inf")) < RECENT_WINDOW_S]
    return WARM_WINDOWS + tuple(recent)


# Reload/warm spans kept for busy_overlap(); a few minutes of history.
_BUSY_KEEP = 32


def _busy_begin(kind):
    span = [kind, time.monotonic(), None]
    with _lock:
        busy = _state["busy"]
        busy.append(span)
        del busy[:-_BUSY_KEEP]
    return span


def _busy_end(span):
    with _lock:
        span[2] = time.monotonic()


def busy_overlap(ms, now=None):
    """How long a request that just took ``ms`` overlapped this child's own
    reload or body warm, for the list_server diagnostic (CCC-691). Lets slow
    reads be told apart from contention with background work."""
    now = time.monotonic() if now is None else now
    start = now - max(float(ms or 0), 0.0) / 1000.0
    out = {}
    with _lock:
        spans = [tuple(span) for span in _state["busy"]]
    for kind, begin, end in spans:
        overlap = min(now if end is None else end, now) - max(begin, start)
        if overlap > 0:
            key = f"busy_{kind}_ms"
            out[key] = round(out.get(key, 0.0) + overlap * 1000.0, 1)
    return out


def _warm_bodies():
    srv = _state["server"]
    span = _busy_begin("warm")
    try:
        _warm_bodies_inner(srv)
    finally:
        _busy_end(span)


def _warm_bodies_inner(srv):
    started = time.perf_counter()
    for name in warm_windows():
        try:
            if name == LAYOUTS:
                srv._archive_list_warm({}, "7d", True, scoped_layouts=True)
            else:
                srv._archive_list_warm({}, name, True)
        except Exception as exc:
            _note_error(exc)
    with _lock:
        _state["warms"] += 1
        _state["warm_ms"] = round((time.perf_counter() - started) * 1000.0, 1)


def _claim_warm():
    """True when the caller should run the warm; otherwise the running warm
    is asked to go round once more, so bursts of mutations coalesce."""
    with _lock:
        if _state["warming"]:
            _state["warm_again"] = True
            return False
        _state["warming"] = True
        _state["warm_again"] = False
        return True


def _warm_until_settled():
    try:
        while True:
            _warm_bodies()
            with _lock:
                if not _state["warm_again"]:
                    return
                _state["warm_again"] = False
    finally:
        with _lock:
            _state["warming"] = False
            _state["warm_again"] = False


def _warm_after_mutation():
    """Prebuild the bodies a restamp just discarded (CCC-691).

    A restamp (archive, trash, lane, new transcript) drops every prebuilt
    body, and the child never refreshes on its own, so the next page load
    rebuilt the full 1d/7d body on the request path: reader full-read misses
    took p50 0.8-1.1 s against 0.1 s for hits, and restamps outnumbered
    publications. A reload in flight warms when it finishes."""
    with _lock:
        if _state["reloading"]:
            return
    if _claim_warm():
        threading.Thread(target=_warm_until_settled, name="list-reader-warm", daemon=True).start()


def _reload_in_background():
    span = _busy_begin("reload")
    try:
        _reload()
    except Exception as exc:
        _note_error(exc)
        return
    else:
        # Claimed before "reloading" clears, so rebuilding() has no gap
        # between the reload publishing rows and the warm building bodies.
        warm = _claim_warm()
    finally:
        _busy_end(span)
        with _lock:
            _state["reloading"] = False
    if warm:
        _warm_until_settled()


def rebuilding():
    """True while a reload or warm is rebuilding the prebuilt bodies."""
    return bool(_state["reloading"] or _state["warming"])


def stale_ok(reason, slot):
    """May a page load get the previous body for `slot` (key, window, cached)?

    "rebuilding": only while a reload or warm is under way. "overlay": the
    body differs only in its live-session rows; allowed for bodies the warm
    prebuilds, and that warm is started here so the next read hits."""
    if reason == "rebuilding":
        return rebuilding()
    key, window = slot[0], slot[1]
    name = LAYOUTS if window == "7d" and str(key).endswith(":layouts") else window
    if reason == "overlay" and name in warm_windows():
        if _claim_warm():
            threading.Thread(target=_warm_until_settled, name="list-reader-warm", daemon=True).start()
        return True
    return False


def sync(now=None):
    """Pick up a new dashboard publication or sidecar change. Cheap: one
    SQLite query and three stats, at most once a second."""
    srv = _state["server"]
    if srv is None:
        return
    now = time.monotonic() if now is None else now
    with _lock:
        if not _state["loaded"] or _state["reloading"] or now - _state["checked"] < SYNC_INTERVAL_S:
            return
        _state["checked"] = now
        known_stamps, known_sidecars = _state["stamps"], _state["sidecars"]
    try:
        stamps = _store_stamps(srv)
    except (OSError, sqlite3.Error) as exc:
        _note_error(exc)
        return
    if stamps and any(stamp > (known_stamps or {}).get(key, 0.0) for key, stamp in stamps.items()):
        with _lock:
            if _state["reloading"]:
                return
            _state["reloading"] = True
        threading.Thread(target=_reload_in_background, name="list-reader-reload", daemon=True).start()
        return
    sidecars = _sidecar_stamp(srv)
    if sidecars != known_sidecars:
        # A mutation forward was missed (child busy or restarting): restamp
        # every row from the sidecars, as the dashboard did.
        srv._restamp_archive_serve_cache_after_mutation()
        with _lock:
            _state["sidecars"] = sidecars
        _warm_after_mutation()


def handle_op(req):
    """Socket ops from the dashboard (see conv_reader.call)."""
    srv = _state["server"]
    if srv is None:
        return {"ok": False}
    op = req.get("op")
    if op == "list_mutation":
        if req.get("kind") == "clear":
            srv._clear_archive_serve_cache()
        else:
            def as_set(value):
                return {str(v) for v in value} if isinstance(value, list) else None
            srv._restamp_archive_serve_cache_after_mutation(
                archived_set=as_set(req.get("archived")),
                trashed_set=as_set(req.get("trashed")),
                mutated_sids=as_set(req.get("sids")),
            )
        with _lock:
            _state["mutations"] += 1
            # The mutation's own sidecar write must not trigger a second,
            # full restamp on the next sync.
            _state["sidecars"] = _sidecar_stamp(srv)
        _warm_after_mutation()
        return {"ok": True}
    if op == "list_acp":
        state = req.get("state")
        if not isinstance(state, dict):
            return {"ok": False}
        sessions = {str(h): {str(sid): dict(st) for sid, st in (by_sid or {}).items() if isinstance(st, dict)}
                    for h, by_sid in state.items() if isinstance(by_sid, dict)}
        with _lock:
            srv._ACP_SESSION_STATE.clear()
            srv._ACP_SESSION_STATE.update(sessions)
            _state["acp"] = True
        return {"ok": True}
    if op == "list_status":
        return {"ok": True, "status": status()}
    return {"ok": False}


def note_served(declined=False):
    with _lock:
        _state["declined" if declined else "served"] += 1


# -------------------------------------------------------------- dashboard


def acp_snapshot(acp_state):
    """Scalar copy of the dashboard's ACP session state for the child."""
    out = {}
    for harness, sessions in list((acp_state or {}).items()):
        copied = {}
        for sid, st in list((sessions or {}).items()):
            if isinstance(st, dict) and st.get("attached"):
                copied[str(sid)] = {k: v for k, v in list(st.items())
                                    if isinstance(v, (str, int, float, bool)) or v is None}
        if copied:
            out[str(harness)] = copied
    return out


def acp_signature(snapshot):
    return json.dumps(snapshot, sort_keys=True, default=str)


# CCC-701: a fallback list read carrying the reader's delta token. The
# browser falls back to the dashboard when its connection to the reader port
# stalls (CCC-681/694), not because the reader is down. The dashboard could
# not answer the reader's token (its nonce is per process), so it sent the
# whole 370-680 KB list, and its own token then made the next direct read a
# full body again: 18 of 60 slow reads in the CCC-679 watch window were such
# full bodies. Relaying the read to the reader over loopback keeps it a
# delta in the reader's token lineage, built off the dashboard's GIL.
RELAY_CONNECT_S = 0.25
# Headers must arrive within this long, or the dashboard serves the read as
# before. A cold reader build takes up to several seconds.
RELAY_HEADERS_S = 3.0
RELAY_MAX_BYTES = 64 << 20
_RELAY_REQUEST_HEADERS = ("Accept-Encoding", "If-None-Match", "X-CCC-Archive-Boot",
                          "X-CCC-Proxy-Secret", "Authorization")
_RELAY_SKIP_RESPONSE = frozenset((
    "connection", "keep-alive", "transfer-encoding", "content-length", "date", "server",
    "access-control-allow-origin", "access-control-allow-credentials",
    "access-control-expose-headers", "timing-allow-origin",
))
_relay_stats = {"relayed": 0, "fallbacks": 0, "last_error": None}


def foreign_token(since, own_nonce):
    """True when `since` is a list delta token minted by another process."""
    nonce = str(since or "").split(".", 1)[0]
    return bool(nonce) and nonce != own_nonce and len(str(since)) <= 256


def relay_status():
    with _lock:
        return dict(_relay_stats)


def relay(handler, port, *, connect_s=RELAY_CONNECT_S, headers_s=RELAY_HEADERS_S):
    """Answer `handler`'s list GET with the reader's response on loopback
    `port`. True when the response was sent; False leaves the read to the
    dashboard (reader declined, failed, or too slow to start answering)."""
    import http.client
    import socket

    conn = http.client.HTTPConnection("127.0.0.1", int(port), timeout=connect_s)
    try:
        conn.connect()
        # Keep the socket: a Connection: close reply detaches it from conn.
        sock = conn.sock
        sock.settimeout(headers_s)
        headers = {name: value for name in _RELAY_REQUEST_HEADERS
                   if (value := handler.headers.get(name))}
        conn.request("GET", handler.path, headers=headers)
        response = conn.getresponse()
        if response.status not in (200, 304):
            raise ConnectionError(f"reader answered {response.status}")
        sock.settimeout(max(headers_s, 30.0))
        body = response.read(RELAY_MAX_BYTES + 1)
        if len(body) > RELAY_MAX_BYTES:
            raise ConnectionError("reader body too large to relay")
        kept = [(name, value) for name, value in response.getheaders()
                if name.lower() not in _RELAY_SKIP_RESPONSE]
    except (OSError, http.client.HTTPException, socket.timeout, ValueError) as exc:
        with _lock:
            _relay_stats["fallbacks"] += 1
            _relay_stats["last_error"] = repr(exc)[:200]
        return False
    finally:
        conn.close()
    handler.send_response(response.status)
    for name, value in kept:
        handler.send_header(name, value)
    handler.send_header("X-CCC-List-Relay", "reader")
    if response.status != 304:
        handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    if response.status != 304:
        try:
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    with _lock:
        _relay_stats["relayed"] += 1
    return True
