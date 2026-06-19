"""Performance-regression guards.

Every slow-CCC incident has been the same bug class: a user-facing path doing
O(all conversations/sessions) work — a subprocess fork, a full-file parse, or a
list rebuild — per item, uncached. Correctness tests miss these because tiny
fixtures stay fast while production (1000+ transcripts, cold caches) is seconds.

These tests assert *how much work* happens, not just that the output is right:
  - call-count invariants  (machine-independent; catch a removed gate/cache)
  - a lenient latency budget on a synthetic scale fixture

If one fails, a hot path lost its gating or caching — don't relax the bound,
restore the gate. See CLAUDE.md "Performance gates".
"""
import importlib
import json
import os
import sys
import time
import uuid

import pytest

server = importlib.import_module("server")


def _write_transcript(path, sid, *, old_ts):
    """Minimal valid transcript: one user + one assistant turn."""
    lines = [
        {"type": "user", "sessionId": sid, "timestamp": "2026-01-01T00:00:00.000Z",
         "cwd": str(path.parent), "gitBranch": "main",
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "sessionId": sid, "timestamp": "2026-01-01T00:00:01.000Z",
         "message": {"role": "assistant", "id": f"msg_{sid[:8]}",
                     "model": "claude-opus-4-8",
                     "usage": {"input_tokens": 10, "output_tokens": 5},
                     "content": [{"type": "text", "text": "hi"}]}},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    os.utime(path, (old_ts, old_ts))  # old mtime → outside any liveness window


@pytest.fixture
def big_projects(tmp_path, monkeypatch):
    """Synthetic ~/.claude/projects with N old transcripts.

    Returns (n, sids). Redirects both HOME (find_all_conversations recomputes
    Path.home() per call) and server.PROJECTS_ROOT (compute_global_stats reads
    the module global) so both hot paths see the synthetic tree.
    """
    n = 200
    root = tmp_path / ".claude" / "projects"
    slug = "-tmp-perf-repo"
    (root / slug).mkdir(parents=True)
    old_ts = time.time() - 30 * 86400
    sids = []
    for _ in range(n):
        sid = str(uuid.uuid4())
        sids.append(sid)
        _write_transcript(root / slug / f"{sid}.jsonl", sid, old_ts=old_ts)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(server, "PROJECTS_ROOT", root)
    return n, sids


def _count_calls(monkeypatch, attr, passthrough_return=None):
    calls = []
    orig = getattr(server, attr)

    def spy(*a, **k):
        calls.append((a, k))
        if passthrough_return is not None:
            return passthrough_return
        return orig(*a, **k)

    monkeypatch.setattr(server, attr, spy)
    return calls


# ── Liveness-gate invariants ────────────────────────────────────────────────
# Old, untouched sessions must NOT get the per-row liveness probe (which scans
# every Gemini/Codex file). These guard the cold-build and warm-serve gates.

def test_archive_rehydrate_skips_liveness_for_old_rows(monkeypatch):
    """Warm cached-serve path: ungated probing here was the ~10s/load bug."""
    old_mtime = time.time() - 30 * 86400
    rows = [{"session_id": str(uuid.uuid4()), "mtime": old_mtime} for _ in range(300)]
    calls = _count_calls(monkeypatch, "_archive_session_is_live", passthrough_return=False)
    server._rehydrate_archive_cached_rows(rows)
    assert len(calls) <= 10, (
        f"_archive_session_is_live called {len(calls)}x for 300 old rows — "
        "the rehydrate liveness gate regressed"
    )


def test_archive_build_skips_liveness_for_old_sessions(big_projects, monkeypatch):
    """Cold-build path: same gate, in find_all_conversations."""
    n, _ = big_projects
    calls = _count_calls(monkeypatch, "_archive_session_is_live", passthrough_return=False)
    server._build_archive_conversations()
    assert len(calls) <= 20, (
        f"_archive_session_is_live called {len(calls)}x for {n} old sessions — "
        "the archive-build liveness gate regressed"
    )


# ── Stats persistence ─────────────────────────────────────────────────────────
# The per-transcript stats cache must survive a reload, or the first Stats open
# after a restart re-parses every transcript (~40s, GIL-blocking).

def test_stats_cache_roundtrip_avoids_reparse(big_projects, monkeypatch, tmp_path):
    cache_file = tmp_path / "stats_file_cache.json"
    monkeypatch.setattr(server, "_STATS_FILE_CACHE_FILE", cache_file)
    server._STATS_FILE_CACHE.clear()

    server.compute_global_stats()      # cold build populates the cache
    server._save_stats_file_cache()    # persist
    assert cache_file.is_file(), "stats cache was not persisted"

    server._STATS_FILE_CACHE.clear()   # simulate a restart
    server._load_stats_file_cache()    # reload from disk

    reparses = _count_calls(monkeypatch, "_stats_aggregate_file", passthrough_return={"session_id": None, "by_date": {}})
    server.compute_global_stats()
    assert reparses == [], (
        f"{len(reparses)} transcripts re-parsed after reload — "
        "stats cache persistence regressed (every restart will cold-scan)"
    )


def test_conv_meta_cache_roundtrip_avoids_reparse(big_projects, monkeypatch, tmp_path):
    """The disk tail-meta cache must actually hit after a restart.

    Regression guard for the JSON list-vs-tuple bug: cache_key is built as a
    tuple (st_mtime_ns, st_size) but JSON has no tuple type, so it reloads as
    a list — and [a, b] == (a, b) is always False. The freshness check then
    never matched, so every server restart re-parsed all ~1k+ transcripts
    (tens of seconds at 100% CPU, GIL-starving every other request thread and
    wedging the dashboard). _load_conv_meta_cache must coerce the key back to
    a tuple so warm calls hit.
    """
    cache_file = tmp_path / "conv_meta_cache.json"
    monkeypatch.setattr(server, "_CONV_META_CACHE_FILE", cache_file)
    server._conv_meta_cache.clear()

    flags = dict(resolve_pr_states=False, resolve_effective=False,
                 resolve_worktree_dirty=False)
    server.find_all_conversations(**flags)   # cold build populates the cache
    server._save_conv_meta_cache()           # persist
    assert cache_file.is_file(), "conv-meta cache was not persisted"

    server._conv_meta_cache.clear()          # simulate a restart
    server._load_conv_meta_cache()           # reload from disk
    assert server._conv_meta_cache, "conv-meta cache reloaded empty"
    keyed = [e for e in server._conv_meta_cache.values() if e.get("cache_key") is not None]
    assert keyed, "no cache_key'd entries reloaded — cannot verify the round-trip"
    for entry in keyed:
        # The bug: JSON deserializes the (mtime_ns, size) tuple as a list, and
        # the freshness check ([a, b] == (a, b)) is then always False. A tuple
        # here proves _load_conv_meta_cache coerced it back so warm calls hit.
        assert not isinstance(entry["cache_key"], list), (
            "cache_key reloaded as a list — _extract_tail_meta will never hit "
            "the disk cache and every restart cold-scans all transcripts"
        )
        assert isinstance(entry["cache_key"], tuple)

    # Behavioural: a warm rebuild must not re-parse anything. A cache miss in
    # _extract_tail_meta rewrites the entry and flips the dirty flag, so a
    # clean (False) flag after the second build proves every row hit the cache.
    server._conv_meta_cache_dirty = False
    server.find_all_conversations(**flags)
    assert server._conv_meta_cache_dirty is False, (
        "warm find_all_conversations re-parsed transcripts after a cache "
        "reload — the disk tail-meta cache regressed (every restart cold-scans)"
    )


# ── Archive ?all=1 response cache ─────────────────────────────────────────────
# /api/sessions?all=1 and /api/conversations?all=1 are polled by the dashboard
# AND the COO board. They used to call _build_archive_conversations (O(all
# sessions)) on EVERY request with no response cache and no single-flight — a
# 14s request once wedged the live server. _archive_all_rows_cached now has two
# cache layers: a durable signature-gated *build* cache (unchanged transcript
# corpus → no O(all) rebuild; a real change → exactly one rebuild reusing the
# per-(mtime,size) parse cache) and a short time-based *serve* cache that
# coalesces concurrent polls. These guard the build cache; serve coalescing is
# exercised separately below.

@pytest.fixture
def isolated_archive_cache(monkeypatch, tmp_path):
    """Point the archive response cache at a temp file, empty, no disk load."""
    cache_file = tmp_path / "archive_resp_cache.json"
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_FILE", cache_file)
    server._ARCHIVE_RESPONSE_CACHE.clear()
    server._ARCHIVE_RESPONSE_CACHE_LOADED = True  # skip loading the real cache
    server._ARCHIVE_BUILD_LOCKS.clear()
    server._archive_serve_cache.clear()
    yield
    server._ARCHIVE_RESPONSE_CACHE.clear()
    server._ARCHIVE_RESPONSE_CACHE_LOADED = False
    server._archive_serve_cache.clear()


_ALL_OPTS = dict(include_prs=False, resolve_pr_states=False,
                 resolve_effective=False, resolve_worktree_dirty=False)


def test_archive_all_warm_serve_skips_rebuild(big_projects, isolated_archive_cache, monkeypatch):
    """Second ?all=1 on an unchanged corpus must serve from the build cache, no
    O(all) rebuild.

    This is the regression guard for the wedge: the warm path must NOT call
    find_all_conversations (the full per-session scan) again. The serve cache is
    cleared first so this exercises the durable signature-gated build cache, not
    the ephemeral time window.
    """
    n, _ = big_projects
    rows1, from_cache1 = server._archive_all_rows_cached(_ALL_OPTS)
    assert from_cache1 is False, "cold call should have built"
    assert len(rows1) >= n, "cold build should include the synthetic corpus"

    server._archive_serve_cache.clear()  # bypass the time-based serve window
    builds = _count_calls(monkeypatch, "find_all_conversations")
    rows2, from_cache2 = server._archive_all_rows_cached(_ALL_OPTS)
    assert from_cache2 is True, "warm call should have served from the build cache"
    assert builds == [], (
        f"find_all_conversations called {len(builds)}x on an unchanged corpus — "
        "the ?all=1 build cache regressed (every poll re-scans all sessions)"
    )
    assert len(rows2) == len(rows1), "warm serve must return the same rows as the cold build"


def test_archive_all_cache_invalidates_on_change(big_projects, isolated_archive_cache, monkeypatch, tmp_path):
    """Touching a transcript must bust the signature and force exactly one rebuild
    (the build cache must never serve a stale payload after a real change)."""
    n, sids = big_projects
    cold_rows, _ = server._archive_all_rows_cached(_ALL_OPTS)  # warm the cache

    # Sanity: with the serve window cleared, an unchanged call serves from the
    # build cache without rebuilding (proves the bust below is the signature
    # reacting to the edit, not a cold cache).
    server._archive_serve_cache.clear()
    _, warm_from_cache = server._archive_all_rows_cached(_ALL_OPTS)
    assert warm_from_cache is True

    # Mutate one transcript's mtime → corpus signature changes.
    p = tmp_path / ".claude" / "projects" / "-tmp-perf-repo" / f"{sids[0]}.jsonl"
    newer = time.time() - 29 * 86400  # still old enough to stay out of liveness windows
    os.utime(p, (newer, newer))

    server._archive_serve_cache.clear()  # past the serve window → hit the build cache
    builds = _count_calls(monkeypatch, "find_all_conversations")
    rows, from_cache = server._archive_all_rows_cached(_ALL_OPTS)
    assert from_cache is False, "a changed corpus must NOT serve the stale cached payload"
    assert len(builds) == 1, (
        f"expected exactly one rebuild after a real change, got {len(builds)}"
    )
    assert len(rows) == len(cold_rows)


def test_archive_all_serve_cache_coalesces_concurrent_polls(big_projects, isolated_archive_cache, monkeypatch):
    """Within the serve TTL, repeated polls must NOT rebuild OR rehydrate — even
    if the corpus changed — so a burst of dashboard+COO polls collapses to one
    computation. This is the per-request CPU-drain fix; staleness is ≤ the TTL.
    """
    monkeypatch.setattr(server, "_ARCHIVE_SERVE_TTL", 60.0)  # wide window for the test
    server._archive_all_rows_cached(_ALL_OPTS)  # populate the serve cache

    builds = _count_calls(monkeypatch, "find_all_conversations")
    rehydrates = _count_calls(monkeypatch, "_rehydrate_archive_cached_rows")
    for _ in range(5):
        _, from_cache = server._archive_all_rows_cached(_ALL_OPTS)
        assert from_cache is True
    assert builds == [], "serve-cache window must not rebuild"
    assert rehydrates == [], (
        "serve-cache window must not even rehydrate — concurrent polls within "
        "the TTL must share one snapshot (the per-request liveness-probe drain)"
    )


# ── Latency budget (lenient smoke on the scale fixture) ───────────────────────

def test_stats_build_under_budget(big_projects):
    t = time.perf_counter()
    server.compute_global_stats()
    dt = time.perf_counter() - t
    assert dt < 6.0, f"compute_global_stats took {dt:.1f}s on {big_projects[0]} files"


def test_archive_build_under_budget(big_projects):
    t = time.perf_counter()
    server._build_archive_conversations()
    dt = time.perf_counter() - t
    assert dt < 6.0, f"_build_archive_conversations took {dt:.1f}s on {big_projects[0]} files"
