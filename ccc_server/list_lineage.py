"""Sidebar delta tokens that survive a conversation-reader restart (CCC-702).

A ``/api/conversations/list?since=<token>`` token is ``nonce.ver.ctx.ovl``.
The nonce is random per process, because versions restart at 0. Every
dashboard restart respawns the conv_reader child, so every open page's next
sidebar read carried a token from the previous child, was rejected
("nonce"), and was answered with the whole body: 1.3 MB and 1.3-2.5 s for
7d, which also pushed archive_load past its 2 s budget.

The reader child now saves its token lineage to ``<nonce>.json`` (under
``<archive cache stem>.delta-lineage/``) a few seconds after each change and
when it hands off to the next child:

* per serve-cache key: ``ver``, ``track_since``, ``row_rev`` and a digest of
  every row at ``ver`` (the exact source row, as sorted-key JSON);
* the remembered ctx-history entries (parents, resumes, idea tickets, title
  overlays) that per-session context comparison needs, as references to
  content-addressed ``blobs/<sha1>`` files written once (they rarely change,
  and inline they made every periodic save rewrite ~2.5 MB);
* a fingerprint of the code that turns a source row into a sidebar row.

A later process that receives a token from that nonce, with
``track_since <= V <= ver``, knows what the client holds: rows that changed
after ``V`` (``row_rev``) are stale, and every other row equals its saved
digest. It resends every row whose current digest differs, plus rows whose
context inputs differ, exactly like a delta inside one process. Nothing is
trusted from version numbers alone, so a reader that lags the dashboard
(CCC-691's objection to cross-process tokens) only resends more rows.

Evicted ctx entries leave links (ctx -> next newer ctx of the same scope plus
the sids whose context differs, CCC-709); the newest ``MAX_LINKS`` are saved
so an old token can still reach a saved entry.

Tokens minted after the last save, a changed projection fingerprint, or a
ctx with no saved entry or link still get a full body (reasons "range",
"nonce", "ctx").
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import types
from pathlib import Path

SCHEMA = 2
SAVE_INTERVAL_S = 2.0
MAX_AGE_S = 2 * 86400
MAX_CTX_BYTES = 16 << 20
MAX_LINKS = 1024
BLOB_TOUCH_S = 3600.0
_CTX_FIELDS = ("parents", "resumes", "ideas", "titles")
_BLOB_RE = re.compile(r"[0-9a-f]{40}")
_NONCE_RE = re.compile(r"[0-9a-f]{8}")

_lock = threading.Lock()
_loaded = {}          # nonce -> (stat signature, lineage or None)
_saved = {"sig": None, "path": None, "saves": 0, "save_ms": None, "errors": 0,
          "last_error": None, "adopted": 0}
_fingerprint = {}     # id(server module) -> fingerprint
_thread = {"started": False}
_ctx_refs = {}        # ctx name -> ({field: sha1}, {sha1: text}) (names are content hashes)
_blob_touched = {}    # sha1 -> monotonic time this process last wrote/touched it
_blob_cache = {}      # sha1 -> text, for loading


def directory(srv):
    cache = Path(srv._ARCHIVE_RESPONSE_CACHE_FILE)
    return cache.parent / (cache.stem + ".delta-lineage")


def status():
    with _lock:
        return {k: _saved[k] for k in ("saves", "save_ms", "errors", "last_error", "adopted")}


# ---------------------------------------------------------------- fingerprint

# The functions whose code decides what a client holds for a row. Every
# function, class and ccc_server module attribute they reach by name is
# hashed too, so an unrelated server.py edit keeps old tokens valid while a
# projection change invalidates them.
_ROOTS = ("_archive_list_delta_payload", "_archive_list_replay_payload",
          "_archive_list_project_rows", "_archive_list_project_row")


def _code_names(code):
    out = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            out |= _code_names(const)
    return out


def _code_digest(code):
    digest = hashlib.sha256(code.co_code)
    digest.update(repr((code.co_names, code.co_varnames, code.co_freevars)).encode())
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            digest.update(_code_digest(const).encode())
        else:
            digest.update(_stable_repr(const).encode())
    return digest.hexdigest()


_ADDRESS_RE = re.compile(r" at 0x[0-9a-fA-F]+")


def _stable_repr(value):
    """repr() without object addresses or set order, which differ in every
    process (string hashing is randomized per process)."""
    if isinstance(value, (frozenset, set)):
        return "{" + ",".join(sorted(_stable_repr(v) for v in value)) + "}"
    if isinstance(value, tuple):
        return "(" + ",".join(_stable_repr(v) for v in value) + ")"
    if isinstance(value, dict):
        return "{" + ",".join(sorted(f"{_stable_repr(k)}:{_stable_repr(v)}"
                                     for k, v in value.items())) + "}"
    return _ADDRESS_RE.sub("", repr(value))


def _our_module(name):
    name = name or ""
    return name in ("server", "__main__") or name.startswith("ccc_server")


def _ours(obj):
    return _our_module(getattr(obj, "__module__", ""))


def fingerprint(srv):
    """Hash of the row projection code reachable from _ROOTS in ``srv``."""
    cached = _fingerprint.get(id(srv))
    if cached is not None:
        return cached
    digest = hashlib.sha256()
    digest.update(str(getattr(srv, "_ARCHIVE_RESPONSE_CACHE_BUILD_ID", "")).encode())
    digest.update(repr(tuple(getattr(srv, "_ARCHIVE_LIST_FIELDS", ()))).encode())
    seen = set()
    stack = [getattr(srv, name) for name in _ROOTS if callable(getattr(srv, name, None))]
    hashed = []
    module_lists = {}
    while stack:
        obj = stack.pop()
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        if isinstance(obj, type):
            members = [v for v in vars(obj).values()
                       if isinstance(v, (types.FunctionType, staticmethod, classmethod, property))]
            for member in members:
                if isinstance(member, (staticmethod, classmethod)):
                    member = member.__func__
                elif isinstance(member, property):
                    member = member.fget
                if isinstance(member, types.FunctionType):
                    stack.append(member)
            continue
        if not isinstance(obj, types.FunctionType):
            continue
        # Bytecode, names and constants: comments and line moves don't
        # count. The module name differs between a dashboard run as a script
        # (__main__) and the imported copy; hash the code, not its home.
        defaults = list(obj.__defaults__ or ()) + list((obj.__kwdefaults__ or {}).values())
        hashed.append(("code", obj.__qualname__, _code_digest(obj.__code__),
                       _stable_repr(obj.__defaults__), _stable_repr(obj.__kwdefaults__)))
        stack.extend(v for v in defaults if isinstance(v, types.FunctionType) and _ours(v))
        scope = obj.__globals__
        modules = module_lists.get(id(scope))
        if modules is None:
            modules = module_lists[id(scope)] = [
                v for v in list(scope.values())
                if isinstance(v, types.ModuleType) and _our_module(v.__name__)]
        for name in _code_names(obj.__code__):
            value = scope.get(name)
            candidates = [value] if value is not None else []
            # module.attr references: co_names holds the attribute name.
            candidates += [getattr(m, name) for m in modules if hasattr(m, name)]
            for value in candidates:
                if isinstance(value, (types.FunctionType, type)):
                    if _ours(value):
                        stack.append(value)
                elif isinstance(value, (str, int, float, bool, tuple, frozenset)):
                    if name.lstrip("_")[:1].isupper():  # module constants
                        hashed.append(("const", name, _stable_repr(value)[:4096]))
                elif not isinstance(value, types.ModuleType) and _ours(type(value)):
                    stack.append(type(value))  # e.g. a template cache instance
    for item in sorted(hashed):
        digest.update(repr(item).encode())
    value = digest.hexdigest()[:16]
    _fingerprint[id(srv)] = value
    return value


# ------------------------------------------------------------------- saving


def _signature(srv):
    with srv._archive_serve_lock:
        versions = tuple(sorted((str(k), e.get("ver"), e.get("track_since"))
                                for k, e in srv._archive_serve_cache.items()))
    with srv._archive_delta_ctx_lock:
        ctxs = tuple(srv._ARCHIVE_DELTA_CTX_HIST)
        links = (len(srv._ARCHIVE_DELTA_CTX_LINKS), next(reversed(srv._ARCHIVE_DELTA_CTX_LINKS), None))
    return versions, ctxs, links


def export(srv):
    """This process's lineage as a JSON-ready dict, or None if untracked."""
    with srv._archive_serve_lock:
        snap = [(key, entry.get("ver"), entry.get("track_since"), entry.get("row_rev"),
                 list(entry.get("rows") or []))
                for key, entry in srv._archive_serve_cache.items()
                if entry.get("track_since") and entry.get("row_rev") is not None]
    keys = {}
    live = set()
    for key, ver, track_since, row_rev, rows in snap:
        digests = {}
        for row in rows:
            sid = srv._archive_row_sid(row)
            if not sid or sid in digests:
                digests = None  # the client can't merge a repeated id either
                break
            digests[sid] = srv._archive_delta_row_digest(row)
            live.add(id(row))
        if digests is not None and isinstance(key, str):
            keys[key] = {"ver": ver, "track_since": track_since,
                         "row_rev": dict(row_rev), "digests": digests}
    srv._archive_delta_digest_prune(live)
    if not keys:
        return None
    with srv._archive_delta_ctx_lock:
        hist = list(srv._ARCHIVE_DELTA_CTX_HIST.items())
        links = list(srv._ARCHIVE_DELTA_CTX_LINKS.items())[-MAX_LINKS:]
    ctx = {}
    blobs = {}
    budget = MAX_CTX_BYTES
    for name, entry in reversed(hist):  # newest first
        scope = entry["scope"]
        if scope[0] not in keys:
            continue
        refs, texts = _ctx_blobs(name, entry)
        size = sum(len(texts[h]) for h in set(refs.values()) if h not in blobs)
        if size > budget:
            break
        budget -= size
        blobs.update(texts)
        ctx[name] = {"scope": list(scope), "blobs": refs}
    live = set(ctx)
    for name in [n for n in _ctx_refs if n not in live]:
        _ctx_refs.pop(name, None)
    ctx_links = {name: [list(scope), nxt, sorted(sids)]
                 for name, (scope, nxt, sids) in links if scope[0] in keys}
    return {"schema": SCHEMA, "nonce": srv._ARCHIVE_DELTA_NONCE,
            "fingerprint": fingerprint(srv), "pid": os.getpid(),
            "written_at": time.time(), "keys": keys, "ctx": ctx, "ctx_links": ctx_links,
            "_blobs": blobs}


def _ctx_blobs(name, entry):
    """Content references for one ctx entry, computed once per entry."""
    hit = _ctx_refs.get(name)
    if hit is not None:
        return hit
    refs, texts = {}, {}
    for field in _CTX_FIELDS:
        value = entry[field]
        text = (json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
                if field == "parents" else str(value))
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        refs[field] = digest
        texts[digest] = text
    _ctx_refs[name] = (refs, texts)
    return refs, texts


def _write_blobs(target, blobs):
    """Write each blob once; refresh the mtime of reused ones hourly."""
    folder = target / "blobs"
    folder.mkdir(parents=True, exist_ok=True)
    now = time.monotonic()
    for digest, text in blobs.items():
        last = _blob_touched.get(digest)
        if last is not None and now - last < BLOB_TOUCH_S:
            continue
        path = folder / digest
        try:
            os.utime(path)
        except FileNotFoundError:
            tmp = folder / f".{digest}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
            os.replace(tmp, path)
        _blob_touched[digest] = now


def save(srv, *, force=False):
    """Write this process's lineage if it changed since the last save."""
    started = time.perf_counter()
    try:
        sig = _signature(srv)
        with _lock:
            if not force and sig == _saved["sig"]:
                return False
        data = export(srv)
        if data is None:
            return False
        target = directory(srv)
        target.mkdir(parents=True, exist_ok=True)
        _write_blobs(target, data.pop("_blobs"))  # before the file that names them
        path = target / f"{data['nonce']}.json"
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, separators=(",", ":"), default=str)
        os.replace(tmp, path)
        with _lock:
            _saved.update(sig=sig, path=str(path), saves=_saved["saves"] + 1,
                          save_ms=round((time.perf_counter() - started) * 1000.0, 1))
        return True
    except Exception as exc:  # never let a save break serving
        with _lock:
            _saved["errors"] += 1
            _saved["last_error"] = repr(exc)[:300]
        return False


def prune(srv, now=None):
    """Delete other processes' lineage files and blobs older than MAX_AGE_S."""
    now = time.time() if now is None else now
    own = f"{srv._ARCHIVE_DELTA_NONCE}.json"
    entries = []
    for folder in (directory(srv), directory(srv) / "blobs"):
        try:
            entries.extend(os.scandir(folder))
        except OSError:
            pass
    for entry in entries:
        if entry.name == own or entry.name == "blobs":
            continue
        try:
            if now - entry.stat().st_mtime > MAX_AGE_S:
                os.unlink(entry.path)
        except OSError:
            pass


def start(srv, interval_s=SAVE_INTERVAL_S):
    """Save in the background whenever the lineage changes (the list reader
    child, and since CCC-691 the dashboard too, so the child can adopt
    tokens the dashboard minted)."""
    with _lock:
        if _thread["started"]:
            return
        _thread["started"] = True
    srv._ARCHIVE_DELTA_LINEAGE_LOADER = lambda nonce: load(srv, nonce)
    prune(srv)

    def run():
        while True:
            time.sleep(interval_s)
            save(srv)

    threading.Thread(target=run, name="list-delta-lineage", daemon=True).start()


# ------------------------------------------------------------------ loading


def _parse(srv, raw):
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    if raw.get("fingerprint") != fingerprint(srv):
        return None
    keys = {}
    for key, entry in (raw.get("keys") or {}).items():
        try:
            keys[key] = {"ver": int(entry["ver"]), "track_since": int(entry["track_since"]),
                         "row_rev": {str(s): int(v) for s, v in entry["row_rev"].items()},
                         "digests": {str(s): str(d) for s, d in entry["digests"].items()}}
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    ctx = {}
    for name, entry in (raw.get("ctx") or {}).items():
        try:
            scope = tuple(entry["scope"])
            texts = {f: _read_blob(srv, entry["blobs"][f]) for f in _CTX_FIELDS}
            if any(t is None for t in texts.values()):
                continue  # pruned or never written: that ctx can't vouch
            ctx[str(name)] = {"scope": scope, "parents": dict(json.loads(texts["parents"])),
                              "resumes": texts["resumes"], "ideas": texts["ideas"],
                              "titles": texts["titles"]}
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    links = {}
    raw_links = raw.get("ctx_links")
    for name, link in (raw_links.items() if isinstance(raw_links, dict) else ()):
        try:
            scope, nxt, sids = link
            if not isinstance(scope, list) or not isinstance(sids, list):
                continue
            links[str(name)] = (tuple(scope), str(nxt), frozenset(str(s) for s in sids))
        except (TypeError, ValueError):
            continue
    return {"nonce": raw.get("nonce"), "keys": keys, "ctx": ctx, "links": links}


def _read_blob(srv, digest):
    digest = str(digest)
    if not _BLOB_RE.fullmatch(digest):
        return None
    with _lock:
        text = _blob_cache.get(digest)
    if text is not None:
        return text
    try:
        text = (directory(srv) / "blobs" / digest).read_text(encoding="utf-8")
    except OSError:
        return None
    if hashlib.sha1(text.encode("utf-8")).hexdigest() != digest:
        return None
    with _lock:
        _blob_cache[digest] = text
        while len(_blob_cache) > 32:
            _blob_cache.pop(next(iter(_blob_cache)))
    return text


def load(srv, nonce):
    """The saved lineage of process ``nonce``, re-read when its file changes."""
    nonce = str(nonce or "")
    if not _NONCE_RE.fullmatch(nonce) or nonce == srv._ARCHIVE_DELTA_NONCE:
        return None
    path = directory(srv) / f"{nonce}.json"
    try:
        st = os.stat(path)
    except OSError:
        return None
    sig = (st.st_mtime_ns, st.st_size)
    with _lock:
        hit = _loaded.get(nonce)
    if hit is not None and hit[0] == sig:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as fh:
            lineage = _parse(srv, json.load(fh))
    except (OSError, ValueError):
        lineage = None
    if lineage is not None and lineage.get("nonce") != nonce:
        lineage = None
    with _lock:
        _loaded[nonce] = (sig, lineage)
        while len(_loaded) > 8:
            _loaded.pop(next(iter(_loaded)))
        if lineage is not None:
            _saved["adopted"] += 1
    return lineage
