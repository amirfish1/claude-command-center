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
import concurrent.futures
import json
import os
import sys
import threading
import time
import uuid
import warnings

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


# ── Auto-unarchive sweep candidacy gate (CCC-435 follow-up) ──────────────────
# _auto_unarchive_live_sessions used to gate the (heavy, engine-classifying)
# _archive_session_is_live probe by `sid in _discover_live_session_ids()`.
# CCC-435 swapped that for an ungated per-sid probe so pool-model Codex.app
# threads (invisible to the resume-arg scan) could still be caught — but that
# turned the sweep into O(all archived) heavy probes every 30s. The fix
# restores a candidacy gate that unions the cheap discovery set with a
# cached, bulk Codex-pool candidate set, so the probe count stays bounded by
# candidates, not by archive size.

def test_auto_unarchive_sweep_gates_by_candidacy(monkeypatch):
    """N old archived sids with zero liveness candidates → zero probes."""
    monkeypatch.setattr(server, "_archive_auto_sweep_last", 0.0)
    monkeypatch.setattr(server, "_archive_grace", {})
    monkeypatch.setattr(server, "_discover_live_session_ids", lambda: set())
    monkeypatch.setattr(server, "_codex_pool_candidate_sids", lambda now=None: frozenset())
    calls = _count_calls(monkeypatch, "_archive_session_is_live", passthrough_return=False)

    archived = [str(uuid.uuid4()) for _ in range(300)]
    kept = server._auto_unarchive_live_sessions(list(archived))

    assert kept == archived, "no candidates were live — nothing should be dropped"
    assert len(calls) == 0, (
        f"_archive_session_is_live called {len(calls)}x for 300 archived sids with "
        "zero liveness candidates — the auto-unarchive sweep's candidacy gate regressed"
    )


def test_auto_unarchive_sweep_reaches_pool_codex_candidate(monkeypatch, tmp_path):
    """A pool-model Codex thread with a fresh rollout is still auto-unarchived.

    CCC-435: Codex.app's `codex app-server` puts no session id on any command
    line, so the resume-arg scan (_discover_live_session_ids) never sees it.
    The candidacy gate must still reach it via _codex_pool_candidate_sids —
    a single cached bulk query, gated behind _codex_pool_alive() — not by
    ungating the probe for every archived sid.
    """
    monkeypatch.setattr(server, "_archive_auto_sweep_last", 0.0)
    monkeypatch.setattr(server, "_archive_grace", {})
    monkeypatch.setattr(server, "_session_live_cache", {})
    monkeypatch.setattr(server, "_codex_pool_candidates_cache", {"ts": 0.0, "sids": frozenset()})

    sid = str(uuid.uuid4())
    other_sid = str(uuid.uuid4())  # not a codex thread — must stay archived
    now = time.time()

    # Mock the codex index: pool is alive, and the bulk fetch returns one
    # thread row for `sid`, updated seconds ago (candidacy signal).
    monkeypatch.setattr(server, "_codex_pool_alive", lambda now=None: True)
    monkeypatch.setattr(
        server, "_codex_fetch_threads",
        lambda where="", params=(), limit=None: (
            [{"id": sid, "updated_at_ms": int(now * 1000) - 5000}] if not where else []
        ),
    )
    monkeypatch.setattr(server, "_discover_live_session_ids", lambda: set())

    # Mock the actual-liveness resolution path so it doesn't touch real
    # filesystem/sqlite state: fresh rollout for `sid`, nothing for the other.
    rollout = tmp_path / f"{sid}.jsonl"
    rollout.write_text("{}\n")

    def _fake_state_fields(target_sid, now=None):
        if target_sid == sid:
            return {"codex_state": "working", "codex_fresh": True}
        return {"codex_state": None, "codex_fresh": False}

    monkeypatch.setattr(server, "_codex_state_fields", _fake_state_fields)
    monkeypatch.setattr(server, "_is_codex_session", lambda s: s == sid)

    def _fake_reader(target_sid, repo_path=None):
        if target_sid == sid:
            return rollout, None
        return None, None

    monkeypatch.setattr(server, "_resolve_conversation_reader", _fake_reader)

    calls = _count_calls(monkeypatch, "_archive_session_is_live")
    kept = server._auto_unarchive_live_sessions([sid, other_sid])

    assert sid not in kept, "fresh pool-codex candidate was not auto-unarchived"
    assert other_sid in kept, "non-candidate sid must stay archived"
    # Probed the true candidate (sid); may or may not probe other_sid depending
    # on _discover_live_session_ids/_codex_pool_candidate_sids overlap, but must
    # not blow past a tiny bound (never O(all archived)).
    assert len(calls) <= 2, (
        f"_archive_session_is_live called {len(calls)}x for 2 archived sids — "
        "candidacy gate did not bound the probe count"
    )


# ── UX-fixes queue health (candidacy gate) ───────────────────────────────────
# compute_ux_fixes_health must read the queue ONCE and probe liveness only for
# the single candidate fixer of a project that has open tickets — never an
# O(all sessions/transcripts) scan and never a per-project/row subprocess fork.

def test_ux_fixes_health_no_all_sessions_or_subprocess(monkeypatch):
    """Health must not scan all sessions or fork per project/row.

    Two projects, each with open tickets and a distinct fixer claimer, plus a
    pile of unrelated closed tickets. The candidacy gate means liveness is
    resolved at most once per project-with-open-tickets, and the full archive
    build is never touched.
    """
    uxq = importlib.import_module("ux_fixes_queue")

    def _item(number, project, status, claimed_by=None, seq=None):
        return {
            "number": number, "project": project, "seq": seq or number,
            "ref": f"{project}-{seq or number}", "status": status,
            "claimed_by": claimed_by, "closed_by": claimed_by,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
            "claimed_at": "2026-06-01T00:00:00Z",
            "closed_at": "2026-06-01T00:00:00Z" if status == "closed" else None,
        }

    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    items = (
        [_item(i, "CCC", "open") for i in range(1, 15)]
        + [_item(100, "CCC", "in_progress", claimed_by=sid_a)]
        + [_item(i, "BYM", "closed", claimed_by=sid_b) for i in range(200, 260)]
        + [_item(300, "BYM", "open")]
    )
    monkeypatch.setattr(server._q, "list_items", lambda *a, **k: list(items))

    live_calls = _count_calls(monkeypatch, "_archive_session_is_live", passthrough_return=False)
    # The full archive build is the O(all sessions) path; it must never run.
    build_calls = _count_calls(monkeypatch, "find_all_conversations")
    archive_calls = _count_calls(monkeypatch, "_build_archive_conversations")

    health = server.compute_ux_fixes_health()

    projects = {p["project"] for p in health}
    assert {"CCC", "BYM"} <= projects, f"expected CCC+BYM in health, got {projects}"
    # One liveness probe per project-with-open-tickets (2), not per ticket (~75).
    assert len(live_calls) <= 4, (
        f"_archive_session_is_live called {len(live_calls)}x — health is probing "
        "liveness per ticket instead of per candidate fixer (candidacy gate lost)"
    )
    assert build_calls == [] and archive_calls == [], (
        "compute_ux_fixes_health triggered the O(all sessions) archive build — "
        "it must work off the queue file + cheap liveness primitive only"
    )


# ── Label-claimed fixer is reachable (the nudge-reach bug) ────────────────────
# A fixer that claims with a human LABEL (e.g. "codex-ccc-drain-20260625")
# instead of a UUID used to resolve to fixer_session_id=None, so a detected-
# stuck queue had no session to nudge. The fix makes the watcher reach such a
# fixer via (a) the additive claimed_session_id field and (b) a spawn-registry
# name → session_id fallback — both candidacy-gated and registry-read-once.

def test_ux_fixes_health_resolves_label_claimed_fixer(monkeypatch):
    """A label-claimed fixer must yield a reachable (non-null) session id.

    Three projects, each with open tickets and a fixer that claimed with a
    NON-UUID label. The id is recoverable three ways:
      - LBLA: additive `claimed_session_id` field (preferred)
      - LBLB: spawn-registry `name` match
      - LBLC: a UUID embedded in an engine-prefixed label
    All three must resolve to a real UUID in fixer_session_id (the old code
    returned None and the queue looked unreachable / always stuck).
    """
    uxq = importlib.import_module("ux_fixes_queue")

    sid_a = str(uuid.uuid4())
    sid_b = str(uuid.uuid4())
    sid_c = str(uuid.uuid4())

    # Recent timestamp so the claim is inside the watcher's lookback window
    # (a claim older than _UXQ_NUDGE_LOOKBACK_S is intentionally ignored).
    from datetime import datetime, timezone, timedelta
    recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _item(number, project, status, *, claimed_by=None,
              claimed_session_id=None, seq=None):
        return {
            "number": number, "project": project, "seq": seq or number,
            "ref": f"{project}-{seq or number}", "status": status,
            "claimed_by": claimed_by, "closed_by": claimed_by,
            "claimed_session_id": claimed_session_id,
            "created_at": recent,
            "updated_at": recent,
            "claimed_at": recent,
            "closed_at": recent if status == "closed" else None,
        }

    items = (
        # LBLA: label claim, but the real session id is in claimed_session_id.
        [_item(i, "LBLA", "open") for i in range(1, 4)]
        + [_item(50, "LBLA", "in_progress",
                 claimed_by="codex-ccc-drain-20260625", claimed_session_id=sid_a)]
        # LBLB: pure label claim, resolvable only via the spawn registry name.
        + [_item(i, "LBLB", "open") for i in range(60, 63)]
        + [_item(80, "LBLB", "in_progress", claimed_by="codex-fix-lblb")]
        # LBLC: engine-prefixed label carrying an embedded UUID.
        + [_item(i, "LBLC", "open") for i in range(90, 93)]
        + [_item(120, "LBLC", "in_progress", claimed_by=f"codex:{sid_c}")]
    )
    monkeypatch.setattr(server._q, "list_items", lambda *a, **k: list(items))
    # Spawn registry: only LBLB's label maps to a session id (the (b) path).
    monkeypatch.setattr(
        server, "_load_spawn_registry",
        lambda: [{"name": "codex-fix-lblb", "session_id": sid_b, "engine": "codex"}],
    )

    # Perf guards: registry read is one call (not per row), liveness stays
    # candidacy-gated (one probe per project-with-open-tickets), no all-sessions.
    reg_calls = _count_calls(monkeypatch, "_load_spawn_registry")
    live_calls = _count_calls(monkeypatch, "_archive_session_is_live", passthrough_return=True)
    build_calls = _count_calls(monkeypatch, "find_all_conversations")
    archive_calls = _count_calls(monkeypatch, "_build_archive_conversations")

    health = {p["project"]: p for p in server.compute_ux_fixes_health()}

    assert health["LBLA"]["fixer_session_id"] == sid_a, (
        "claimed_session_id (additive field) was not honored — label-claimed "
        "fixer still resolves to None"
    )
    assert health["LBLB"]["fixer_session_id"] == sid_b, (
        "spawn-registry name fallback did not resolve a pure label claim"
    )
    assert health["LBLC"]["fixer_session_id"] == sid_c, (
        "embedded UUID in an engine-prefixed label was not recovered"
    )
    # The registry is read at most once per pass (candidacy-gated), never per row.
    assert len(reg_calls) <= 1, (
        f"_load_spawn_registry called {len(reg_calls)}x — must be read once per "
        "health pass, not per ticket"
    )
    # One liveness probe per project-with-open-tickets (3), not per ticket.
    assert len(live_calls) <= 6, (
        f"_archive_session_is_live called {len(live_calls)}x — liveness probing "
        "regressed past the candidacy gate"
    )
    assert build_calls == [] and archive_calls == [], (
        "label resolution must not trigger the O(all sessions) archive build"
    )


def test_ux_fixes_claim_stores_real_session_id(monkeypatch, tmp_path):
    """claim_next stores a real session UUID in claimed_session_id, additively.

    A worker may claim with a label; passing session_uuid (or embedding a UUID
    in the label) records the reachable id without touching claimed_by — so old
    label-only behavior is preserved and the new field is purely additive.
    """
    uxq = importlib.import_module("ux_fixes_queue")
    qf = tmp_path / "ux-fixes-queue.json"
    monkeypatch.setattr(uxq, "QUEUE_FILE", qf)
    monkeypatch.setattr(uxq, "_LOCK_FILE", qf.with_suffix(".lock"))

    real_sid = str(uuid.uuid4())
    uxq.enqueue(project="ZZ", note="first")
    uxq.enqueue(project="ZZ", note="second")

    # (a) explicit session_uuid alongside a human label.
    claimed = uxq.claim_next("codex-ccc-drain", project="ZZ", session_uuid=real_sid)
    assert claimed["claimed_by"] == "codex-ccc-drain", "label attribution lost"
    assert claimed["claimed_session_id"] == real_sid, "real session id not stored"

    # (b) label-only claim leaves the additive field empty (non-breaking).
    claimed2 = uxq.claim_next("just-a-label", project="ZZ")
    assert claimed2["claimed_by"] == "just-a-label"
    assert claimed2.get("claimed_session_id") in (None, ""), (
        "a label-only claim must not fabricate a claimed_session_id"
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
    server._archive_serve_refreshing.clear()
    # Short-TTL memos (corpus signature + per-session liveness) are module
    # globals; reset them so a leaked sig/liveness entry from a prior test can't
    # make a signature-gated assertion order-dependent.
    _reset_short_ttl_memos()
    yield
    server._ARCHIVE_RESPONSE_CACHE.clear()
    server._ARCHIVE_RESPONSE_CACHE_LOADED = False
    server._archive_serve_cache.clear()
    server._archive_serve_refreshing.clear()
    _reset_short_ttl_memos()


def _reset_short_ttl_memos():
    """Clear the corpus-signature and per-session-liveness memos (added for the
    under-polling perf work) between tests. Tolerant of their absence so the
    suite still runs if the memos are removed."""
    sig = getattr(server, "_archive_sig_cache", None)
    if isinstance(sig, dict):
        sig["ts"] = 0.0
        sig["sig"] = None
    live = getattr(server, "_session_live_cache", None)
    if isinstance(live, dict):
        live.clear()


_ALL_OPTS = dict(include_prs=False, resolve_pr_states=False,
                 resolve_effective=False, resolve_worktree_dirty=False)


_ALL_KEY = server._archive_response_cache_key(**_ALL_OPTS)


def test_archive_build_cache_skips_rebuild_when_unchanged(big_projects, isolated_archive_cache, monkeypatch):
    """The signature-gated build cache must NOT re-scan all sessions when the
    transcript corpus is unchanged.

    This is the regression guard for the wedge: a second pass over an unchanged
    corpus must NOT call find_all_conversations (the full per-session scan). We
    drive _archive_compute_rows (the synchronous build layer) directly — the SWR
    serve layer is exercised separately — so the assertion is deterministic and
    no background refresh threads leak into other tests.
    """
    n, _ = big_projects
    rows1, from_cache1 = server._archive_compute_rows(_ALL_KEY, _ALL_OPTS)
    assert from_cache1 is False, "cold pass should have built"
    assert len(rows1) >= n, "cold build should include the synthetic corpus"

    builds = _count_calls(monkeypatch, "find_all_conversations")
    rows2, from_cache2 = server._archive_compute_rows(_ALL_KEY, _ALL_OPTS)
    assert from_cache2 is True, "unchanged corpus should rehydrate, not rebuild"
    assert builds == [], (
        f"find_all_conversations called {len(builds)}x on an unchanged corpus — "
        "the ?all=1 build cache regressed (every poll re-scans all sessions)"
    )
    assert len(rows2) == len(rows1), "rehydrate must return the same rows as the build"


def test_archive_build_cache_invalidates_on_change(big_projects, isolated_archive_cache, monkeypatch, tmp_path):
    """Touching a transcript must bust the signature and force exactly one rebuild
    (the build cache must never serve a stale payload after a real change)."""
    n, sids = big_projects
    cold_rows, _ = server._archive_compute_rows(_ALL_KEY, _ALL_OPTS)  # warm the cache

    # Sanity: an unchanged pass rehydrates without rebuilding (proves the bust
    # below is the signature reacting to the edit, not a cold cache).
    _, warm_from_cache = server._archive_compute_rows(_ALL_KEY, _ALL_OPTS)
    assert warm_from_cache is True

    # Mutate one transcript's mtime → corpus signature changes.
    p = tmp_path / ".claude" / "projects" / "-tmp-perf-repo" / f"{sids[0]}.jsonl"
    newer = time.time() - 29 * 86400  # still old enough to stay out of liveness windows
    os.utime(p, (newer, newer))

    # The corpus signature is memoized for _ARCHIVE_SIG_TTL (default 2s) so
    # concurrent per-key refreshes share one full-corpus walk. That means a real
    # edit is picked up on the NEXT signature computation once the tiny TTL
    # lapses — not necessarily within the same 2s window. Expire the memo here to
    # simulate that lapse deterministically; the invariant under test is
    # "a real change forces exactly ONE rebuild (never an infinite stale
    # rehydrate)", which holds regardless of the memo window.
    sig_cache = getattr(server, "_archive_sig_cache", None)
    if isinstance(sig_cache, dict):
        sig_cache["ts"] = 0.0
        sig_cache["sig"] = None

    builds = _count_calls(monkeypatch, "find_all_conversations")
    rows, from_cache = server._archive_compute_rows(_ALL_KEY, _ALL_OPTS)
    assert from_cache is False, "a changed corpus must rebuild, not rehydrate the stale payload"
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


def test_ux_fixes_health_payload_coalesces_concurrent_polls(monkeypatch):
    """Queue health is a dashboard poll target; concurrent clients must share
    one expensive queue/log scan instead of each request rebuilding it."""
    assert hasattr(server, "build_ux_fixes_health_payload")

    server._ux_fixes_health_snapshot["ts"] = 0.0
    server._ux_fixes_health_snapshot["data"] = None
    monkeypatch.setattr(server, "_UX_FIXES_HEALTH_TTL", 60.0)

    calls = []
    lock = threading.Lock()

    def slow_health():
        with lock:
            calls.append(time.time())
        time.sleep(0.05)
        return [{"project": "CCC", "depth": 1, "stuck": False}]

    monkeypatch.setattr(server, "compute_ux_fixes_health", slow_health)
    monkeypatch.setattr(server, "_wt_read_workers", lambda: [])
    monkeypatch.setattr(server, "compute_queues_health", lambda health, workers: [{"queue": "CCC"}])
    monkeypatch.setattr(server, "_wt_read_worker_session_ids", lambda: [])
    monkeypatch.setattr(server, "_wt_past_workers", lambda hours=24: [])

    barrier = threading.Barrier(4)

    def call_payload():
        barrier.wait()
        return server.build_ux_fixes_health_payload()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: call_payload(), range(4)))

    assert len(calls) == 1, "concurrent /api/ux-fixes/health polls rebuilt independently"
    assert all(r["count"] == 1 for r in results)
    assert all(r["queues"] == [{"queue": "CCC"}] for r in results)


def test_live_activity_cache_covers_dashboard_poll_interval(monkeypatch):
    """The live sidebar polls every 5s; that steady-state poll must not force
    a real live-activity rebuild every time when a recent snapshot exists."""
    server._live_activity_snapshot["ts"] = 0.0
    server._live_activity_snapshot["data"] = None

    now = [1000.0]
    calls = []

    def fake_time():
        return now[0]

    def build_uncached():
        calls.append(now[0])
        return {"sid-1": {"state": "working"}}

    monkeypatch.setattr(server.time, "time", fake_time)
    monkeypatch.setattr(server, "_record_live_build_ms", lambda ms: None)
    monkeypatch.setattr(server, "_build_live_sessions_activity_uncached", build_uncached)

    first = server.build_live_sessions_activity()
    now[0] += 5.0
    second = server.build_live_sessions_activity()

    assert first == second == {"sid-1": {"state": "working"}}
    assert calls == [1000.0], "5s dashboard polls should reuse the live snapshot"


def test_throughput_startup_prewarm_defaults_off(monkeypatch):
    """Throughput prewarm parses a large history cache; it must not run during
    normal daemon startup unless the user explicitly opts in."""
    monkeypatch.delenv("CCC_PREWARM_THROUGHPUT_ON_STARTUP", raising=False)
    assert server._should_prewarm_throughput_on_startup() is False

    monkeypatch.setenv("CCC_PREWARM_THROUGHPUT_ON_STARTUP", "1")
    assert server._should_prewarm_throughput_on_startup() is True


def test_throughput_aggregate_cache_covers_footer_poll_interval(monkeypatch):
    """The dashboard throughput pill polls /api/throughput/history every 90s;
    the aggregate cache must span that cadence or idle dashboards rebuild 56d
    history on every refresh."""
    server._THROUGHPUT_AGG_CACHE.clear()
    now = [1000.0]
    calls = []

    monkeypatch.setattr(server.time, "time", lambda: now[0])

    def fake_find_all_conversations(*args, **kwargs):
        calls.append(now[0])
        return []

    monkeypatch.setattr(server, "find_all_conversations", fake_find_all_conversations)

    first, first_status = server._throughput_payload("all_56_days")
    now[0] += 90.0
    second, second_status = server._throughput_payload("all_56_days")

    assert first_status == second_status == 200
    assert first == second
    assert calls == [1000.0], "90s footer poll should reuse aggregate throughput history"


def test_throughput_history_cache_only_does_not_compute(monkeypatch):
    """The main dashboard footer may ask for today's throughput badge, but it
    must not compute the expensive 56-day history when no cache exists."""
    server._THROUGHPUT_AGG_CACHE.clear()

    def fail_compute(*args, **kwargs):
        raise AssertionError("cache-only history must not compute throughput")

    monkeypatch.setattr(server, "_throughput_payload", fail_compute)

    payload, status = server._throughput_history_payload(cache_only=True)

    assert status == 200
    assert payload == {"ok": True, "daily": [], "cached": False}


def test_throughput_initial_payload_never_computes(monkeypatch, tmp_path):
    """The throughput page's first paint must use only cheap snapshot data."""
    server._THROUGHPUT_AGG_CACHE.clear()
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)

    def fail_find(*args, **kwargs):
        raise AssertionError("initial throughput payload must not discover conversations")

    monkeypatch.setattr(server, "find_all_conversations", fail_find)

    payload, status = server._throughput_initial_payload("all_7_days")

    assert status == 200
    assert payload["ok"] is True
    assert payload["session_id"] == "all_7_days"
    assert payload["scope"]["aggregate"] is True
    assert payload["summary"]["total_turns"] == 0
    assert payload["summary"]["hourly"] == []
    assert payload["turns"] == []
    assert payload["snapshot"]["state"] == "empty"
    assert payload["snapshot"]["cached"] is False


def test_throughput_full_aggregate_persists_initial_snapshot(monkeypatch, tmp_path):
    """A completed aggregate refresh should seed the next cold initial paint."""
    server._THROUGHPUT_AGG_CACHE.clear()
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)
    monkeypatch.setattr(server, "find_all_conversations", lambda *a, **k: [])

    full_payload, full_status = server._throughput_payload("all_7_days")
    server._THROUGHPUT_AGG_CACHE.clear()
    initial_payload, initial_status = server._throughput_initial_payload("all_7_days")

    assert full_status == initial_status == 200
    assert initial_payload["ok"] is True
    assert initial_payload["session_id"] == "all_7_days"
    assert initial_payload["snapshot"]["state"] == "cached"
    assert initial_payload["snapshot"]["cached"] is True
    assert initial_payload["summary"] == full_payload["summary"]


def test_wt_past_workers_uses_warning_free_utc_timestamp(monkeypatch, tmp_path):
    """The queue-health poll scans past WT worker logs; timestamp formatting
    must not emit Python 3.14 deprecation warnings on every row."""
    logs = tmp_path / "logs"
    logs.mkdir()
    worker_log = logs / "CCC-abcdef12.log"
    worker_log.write_text(json.dumps({"session_id": "sid-1"}) + "\n")
    os.utime(worker_log, (time.time(), time.time()))

    monkeypatch.setattr(server, "_WT_HOME", tmp_path)
    monkeypatch.setattr(server, "_wt_read_workers", lambda: [])

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        rows = server._wt_past_workers(hours=1)

    assert rows and rows[0]["ended_at_iso"].endswith("Z")


def test_pending_resume_retry_backoff_prevents_hot_loop():
    """A stuck durable resume item must not fork a retry every watcher tick."""
    sid = "019f1eb0-e057-7481-a9a2-2ea59a858a24"
    server._pending_resume_retry_after.clear()

    assert server._pending_resume_retry_due(sid, now=100.0)
    server._mark_pending_resume_retry(sid, now=100.0, delay=60.0)

    assert not server._pending_resume_retry_due(sid, now=159.0)
    assert server._pending_resume_retry_due(sid, now=160.1)


def test_archive_rows_carry_state_stamp_cache_safe(big_projects, isolated_archive_cache, monkeypatch):
    """state + ended_blocked must be stamped INTO the cached snapshot, so a warm
    serve (no rebuild, no rehydrate) still carries them.

    /api/conversations?all=1 has no post-serve projection pass, so a stamp applied
    only at response time would be absent on warm hits. Guard that the stamp lives
    in the serve cache: a Tier-1 warm serve carries state+ended_blocked on every
    row without re-running the build.
    """
    monkeypatch.setattr(server, "_ARCHIVE_SERVE_TTL", 60.0)
    cold_rows, _ = server._archive_all_rows_cached(_ALL_OPTS)  # cold build populates the snapshot
    for r in cold_rows:
        assert r.get("state") is not None, "cold build row missing state"
        assert "ended_blocked" in r, "cold build row missing ended_blocked"

    # Warm Tier-1 serve: no rebuild, no rehydrate — must still carry the stamp.
    builds = _count_calls(monkeypatch, "find_all_conversations")
    rehydrates = _count_calls(monkeypatch, "_rehydrate_archive_cached_rows")
    warm_rows, from_cache = server._archive_all_rows_cached(_ALL_OPTS)
    assert from_cache is True and builds == [] and rehydrates == [], "warm serve should not recompute"
    assert warm_rows, "warm serve returned no rows"
    for r in warm_rows:
        assert r.get("state") is not None, "warm-served row missing state (stamp not cache-safe)"
        assert "ended_blocked" in r, "warm-served row missing ended_blocked (stamp not cache-safe)"


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
