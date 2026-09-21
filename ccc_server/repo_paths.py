# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Leaf module for git and repo-path resolution helpers.

Stdlib-only. Anything a test patches on `server` (or that still lives there)
is read through `_core`, which resolves `server` first and the registry of
adopted ccc_server modules when `server` is absent.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path

from ccc_server import core as _core


# Visual-only override of which repo a session appears under in the all-repos
# archive view. {session_id: repo_path}. Does not touch the JSONL transcript —
# the session's recorded cwd is unchanged, only the row's grouping is moved.
# Used when a session was launched in repo A but the work logically belongs
# under repo B and the user wants the row to appear there for scanning.
_REPO_PINS_FILE = Path.home() / ".claude" / "command-center" / "repo-pins.json"


def _load_repo_pins():
    try:
        with open(_REPO_PINS_FILE) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


_git_root_cache: dict = {}


def _find_git_root(folder_path: str) -> "str | None":
    """Walk up from folder_path to find the nearest ancestor that is a git
    repo root (contains a .git file or dir).  Returns the root path string,
    or None if no git repo is found up to the filesystem root.  Cached."""
    if not folder_path:
        return None
    if folder_path in _git_root_cache:
        return _git_root_cache[folder_path]
    p = Path(folder_path)
    candidate = p if p.is_dir() else p.parent
    while True:
        try:
            if (candidate / ".git").exists():
                result = str(candidate)
                _git_root_cache[folder_path] = result
                return result
        except OSError:
            pass
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    _git_root_cache[folder_path] = None
    return None


class RepoContextError(ValueError):
    """Structured error for repo-explicit API validation."""

    def __init__(self, code, message, *, status=400, path=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.path = path

    def as_payload(self):
        out = {"ok": False, "error": self.code, "code": self.code, "message": str(self)}
        if self.path:
            out["path"] = self.path
        return out


def repo_log_dir(repo_path):
    return Path(repo_path).expanduser().resolve() / ".claude" / "logs"


# _known_repo_paths walks every known/recent/custom repo AND rediscovers repos
# from ~/.claude/projects (169 dirs, ~100k is_dir syscalls). One /api/sessions
# load called it 20 times -- mostly via resolve_repo_path, which every request
# path funnels through -- for 1.6s of pure repeat work. The answer only moves
# when a repo is added or removed, so a few seconds of staleness is invisible
# while the repeat cost is not.
# The walk is ~40 ms in an idle process but hundreds of syscalls, each a GIL
# round-trip; under a busy dashboard one rebuild stretched to 3 to 6 s in
# stack samples, with two request threads rebuilding at once because every
# expired caller walked independently. So: one rebuild at a time, callers
# that find an expired list keep serving it while the walker runs, and the
# memo lives 30 s (adding/removing a repo invalidates explicitly).
_KNOWN_REPO_PATHS_CACHE = {"at": 0.0, "paths": None}


_KNOWN_REPO_PATHS_TTL_S = 30.0


_KNOWN_REPO_PATHS_LOCK = threading.Lock()


_KNOWN_REPO_PATHS_REBUILD_LOCK = threading.Lock()


def _invalidate_known_repo_paths():
    """Drop the memo — call after adding/removing a repo."""
    with _KNOWN_REPO_PATHS_LOCK:
        _core._KNOWN_REPO_PATHS_CACHE["at"] = 0.0
        _core._KNOWN_REPO_PATHS_CACHE["paths"] = None


def _known_repo_paths_memo_hit():
    with _KNOWN_REPO_PATHS_LOCK:
        hit = _core._KNOWN_REPO_PATHS_CACHE["paths"]
        fresh = hit is not None and time.time() - _core._KNOWN_REPO_PATHS_CACHE["at"] < _core._KNOWN_REPO_PATHS_TTL_S
        return hit, fresh


def _known_repo_paths():
    hit, fresh = _known_repo_paths_memo_hit()
    if fresh:
        return list(hit)
    if hit is not None:
        # Expired but present: only one thread rebuilds, the rest serve the
        # last list instead of queueing behind the walk.
        if not _KNOWN_REPO_PATHS_REBUILD_LOCK.acquire(blocking=False):
            return list(hit)
    else:
        # Nothing cached yet (startup or explicit invalidation): wait for the
        # one rebuild rather than each caller walking the disk.
        _KNOWN_REPO_PATHS_REBUILD_LOCK.acquire()
    try:
        hit, fresh = _known_repo_paths_memo_hit()
        if fresh:
            return list(hit)
        out = _core._known_repo_paths_uncached()
        with _KNOWN_REPO_PATHS_LOCK:
            _core._KNOWN_REPO_PATHS_CACHE["at"] = time.time()
            _core._KNOWN_REPO_PATHS_CACHE["paths"] = out
        return list(out)
    finally:
        _KNOWN_REPO_PATHS_REBUILD_LOCK.release()


def _git_toplevel_for_existing_dir(path):
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not p.is_dir():
        return None
    try:
        r = subprocess.run(
            ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            return str(Path(r.stdout.strip()).resolve())
    except (subprocess.SubprocessError, OSError):
        pass
    return None


def _has_project_marker(path):
    """Return whether ``path`` is an explicit project root.

    A project-local ``.claude/`` directory is a valid marker, but the global
    ``~/.claude/`` configuration directory is not. Treating that global
    directory as a marker makes every plain folder under the user's home look
    like a child of one giant project, so typed scratch folders never get
    registered as their own CCC workspaces.
    """
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return False
    try:
        if (p / ".git").exists():
            return True
        if not (p / ".claude").is_dir():
            return False
        return p != Path.home().resolve()
    except (OSError, ValueError, RuntimeError):
        return False


def _nearest_marked_repo_dir(path):
    """Return the closest existing ancestor that CCC can treat as a repo root.

    Non-git project folders are supported when they carry a `.claude/`
    marker. This matters for plain asset/project directories where sessions
    are launched from a subfolder rather than the marker-bearing root.
    """
    try:
        p = Path(path).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    if not p.is_dir():
        return None
    for candidate in (p, *p.parents):
        try:
            if _has_project_marker(candidate):
                return str(candidate)
        except OSError:
            continue
    return None


def _plus_space_path_candidates(raw, *, cap=10):
    """Yield variants of `raw` where each space is optionally swapped for `+`.

    Defends against the URL query-string quirk: `+` decodes to a space in a
    query, so a literal `+` in a repo path arrives at the server as a space.
    When the as-given path doesn't resolve, we try `+` in each space slot to
    see if exactly one variant maps to a real/known repo.

    Cap is a safety bound: with N space positions there are 2^N variants. A
    path with more than `cap` spaces falls back to a single "all spaces → +"
    attempt instead of enumerating.
    """
    seen = set()
    if raw not in seen:
        seen.add(raw)
        yield raw
    if " " not in raw:
        return
    space_positions = [i for i, ch in enumerate(raw) if ch == " "]
    n = len(space_positions)
    if n > cap:
        alt = raw.replace(" ", "+")
        if alt not in seen:
            seen.add(alt)
            yield alt
        return
    chars0 = list(raw)
    for mask in range(1, 1 << n):
        chars = list(chars0)
        for i, pos in enumerate(space_positions):
            if mask & (1 << i):
                chars[pos] = "+"
        alt = "".join(chars)
        if alt not in seen:
            seen.add(alt)
            yield alt


def _resolve_repo_path_check(raw, *, known):
    """Return (canon_path, error_code) for a single candidate string.

    error_code is None on success. On failure it is one of "not_dir" or
    "not_allowed" so the outer logic can decide which fallback to attempt.
    """
    try:
        p = Path(raw).expanduser().resolve()
    except (OSError, ValueError, RuntimeError):
        return None, "not_dir"
    if not p.is_dir():
        return None, "not_dir"
    s = str(p)
    looks_like_repo = _has_project_marker(p)
    if s not in known and not looks_like_repo:
        return s, "not_allowed"
    return s, None


def resolve_repo_path(value):
    """Validate and canonicalize one concrete repo path.

    The sentinel "ALL" is intentionally rejected here. Aggregate scope belongs
    to aggregate endpoints, not to the repo_path field.

    Pure validator: never mutates server-side state. Bumping recent-repos is
    the API boundary's job (see require_repo_context); doing it here would
    pollute the recent list with cache-invalidation calls and other
    behind-the-scenes path normalizations.
    """
    raw = str(value or "").strip()
    if not raw:
        raise RepoContextError("repo_required", "repo_path is required")
    if raw.upper() == "ALL":
        raise RepoContextError("invalid_repo_path", "repo_path must be one real path, not ALL", path=raw)

    # Allow rule: must be in the known-repos list OR look like a real repo
    # (`.git` or `.claude` directory present). The looks-like-repo escape
    # hatch is intentional ergonomics — it lets first-time spawns into a
    # discovered folder succeed without a separate /api/repo/add round-trip.
    # The trade-off: any reachable directory with `.git` is acceptable, so
    # the gate isn't a strict allow-list. Path traversal is still bounded
    # by `Path.resolve()` above, and shell-out endpoints layer their own
    # sandboxing on top (see /api/open's REPO_ROOT-style clamps).
    known = set(_core._known_repo_paths())

    canon, err = _resolve_repo_path_check(raw, known=known)
    if err is None:
        return canon

    # Fallback for JSON-body over-encoding. A caller may URL-encode the
    # repo_path VALUE inside a JSON body (wrong: JSON values are not percent-
    # encoded), so /Users/.../BYM+Finie arrives as /Users/.../BYM%2BFinie. If
    # the as-given raw failed but its percent-decoded form resolves to a known
    # repo, accept that. Only fires when raw carries a literal '%' escape, so a
    # legitimate path is never altered.
    if "%" in raw:
        for variant in (urllib.parse.unquote(raw), urllib.parse.unquote_plus(raw)):
            if variant == raw:
                continue
            cand, cand_err = _resolve_repo_path_check(variant, known=known)
            if cand_err is None and cand:
                return cand

    # Fallback for query-string `+` decoding. A repo at /Users/.../BYM+Finie
    # arrives over the wire as /Users/.../BYM Finie because `+` decodes to a
    # space in a URL query. Try restoring `+` in space positions and see if
    # exactly one variant resolves to a real or known repo. Skip the raw
    # since we already tried it.
    if " " in raw:
        matches = []
        for variant in _plus_space_path_candidates(raw):
            if variant == raw:
                continue
            cand, cand_err = _resolve_repo_path_check(variant, known=known)
            if cand_err is None and cand:
                matches.append(cand)
        # Dedup while preserving order.
        matches = list(dict.fromkeys(matches))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RepoContextError(
                "ambiguous_repo_path",
                "repo_path matches multiple known repos after `+`/space normalization; encode `+` as %2B",
                path=raw,
            )

    # No variant resolved. Re-raise the original error using the as-given raw.
    if err == "not_dir":
        try:
            p = Path(raw).expanduser().resolve()
            shown = str(p)
        except (OSError, ValueError, RuntimeError) as e:
            raise RepoContextError("invalid_repo_path", f"could not resolve repo_path: {e}", path=raw)
        raise RepoContextError("invalid_repo_path", f"repo_path is not a directory: {shown}", path=shown)
    # err == "not_allowed"
    if canon is None:
        canon = str(Path(raw).expanduser().resolve())
    raise RepoContextError(
        "repo_not_allowed",
        "repo_path is not known; add it through /api/repo/add first",
        path=canon,
        status=403,
    )


def _git(args, cwd, timeout=10):
    """Run `git <args>` in cwd. Returns (rc, stdout, stderr) — stderr trimmed."""
    try:
        r = subprocess.run(
            ["git"] + list(args),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, r.stdout, (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "git not found on PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"git {' '.join(args)} timed out"
