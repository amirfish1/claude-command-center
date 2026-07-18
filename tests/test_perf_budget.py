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
import urllib.request
import uuid
import warnings
from pathlib import Path

import pytest

server = importlib.import_module("server")


def test_productivity_refresh_reads_shared_sources_once(monkeypatch):
    """One refresh may scan globally, but never once per project/turn."""
    calls = {"conversations": 0, "tickets": 0}

    def conversations(**kwargs):
        calls["conversations"] += 1
        return []

    def tickets():
        calls["tickets"] += 1
        return []

    class Store:
        def load_presence(self, start_date, end_date, tzinfo=None):
            return []

    monkeypatch.setattr(server, "find_all_conversations", conversations)
    monkeypatch.setattr(server._q, "list_items", tickets)
    monkeypatch.setattr(server, "_productivity_known_repos", lambda rows: ([], []))
    monkeypatch.setattr(server, "_PRODUCTIVITY_STORE", Store())

    snapshot = server._productivity_build_snapshot(
        now=server.datetime(2026, 7, 14, tzinfo=server.timezone.utc)
    )

    assert calls == {"conversations": 1, "tickets": 1}
    assert set(snapshot["datasets"]) == {"6", "8", "12", "16"}


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
    # find_all_conversations now merges every supported engine into the archive.
    # This fixture is a synthetic Claude corpus; keep import-time engine globals
    # pointed at tmp_path so local Codex/Gemini/Cursor/Hermes state cannot dirty
    # the shared meta cache and make the perf gate depend on the developer's box.
    codex_home = tmp_path / ".codex"
    monkeypatch.setattr(server, "CODEX_STATE_DB", codex_home / "state_5.sqlite")
    monkeypatch.setattr(server, "CODEX_SESSIONS_ROOT", codex_home / "sessions")
    monkeypatch.setattr(server, "CODEX_GOALS_DB_CANDIDATES", (
        codex_home / "goals_1.sqlite",
        codex_home / "sqlite" / "goals_1.sqlite",
    ))
    monkeypatch.setattr(server, "CODEX_PARENT_LINKS_FILE", tmp_path / ".claude" / "command-center" / "codex-parent-links.json")
    gemini_home = tmp_path / ".gemini"
    monkeypatch.setattr(server, "GEMINI_HOME", gemini_home)
    monkeypatch.setattr(server, "ANTIGRAVITY_HOME", gemini_home / "antigravity")
    monkeypatch.setattr(server, "ANTIGRAVITY_BRAIN", gemini_home / "antigravity" / "brain")
    monkeypatch.setattr(server, "ANTIGRAVITY_CONVERSATIONS", gemini_home / "antigravity" / "conversations")
    monkeypatch.setattr(server, "ANTIGRAVITY_CLI_HOME", gemini_home / "antigravity-cli")
    monkeypatch.setattr(server, "ANTIGRAVITY_CLI_SETTINGS", gemini_home / "antigravity-cli" / "settings.json")
    monkeypatch.setattr(server, "ANTIGRAVITY_CLI_BRAIN", gemini_home / "antigravity-cli" / "brain")
    monkeypatch.setattr(server, "ANTIGRAVITY_CLI_CONVERSATIONS", gemini_home / "antigravity-cli" / "conversations")
    monkeypatch.setattr(server, "ANTIGRAVITY_MAIN_LOG", tmp_path / "Library" / "Logs" / "Antigravity" / "main.log")
    monkeypatch.setattr(server, "ANTIGRAVITY_SUMMARIES_PROTO", gemini_home / "antigravity" / "agyhub_summaries_proto.pb")
    cursor_home = tmp_path / ".cursor"
    monkeypatch.setattr(server, "CURSOR_HOME", cursor_home)
    monkeypatch.setattr(server, "CURSOR_PROJECTS_ROOT", cursor_home / "projects")
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setattr(server, "HERMES_HOME", hermes_home)
    monkeypatch.setattr(server, "HERMES_STATE_DB", hermes_home / "state.db")
    monkeypatch.setattr(server, "HERMES_GATEWAY_SESSIONS", hermes_home / "sessions" / "sessions.json")
    monkeypatch.setattr(server, "HERMES_WHATSAPP_DIR", hermes_home / "whatsapp")
    monkeypatch.setattr(server, "HERMES_WHATSAPP_BRIDGE_LOG", hermes_home / "whatsapp" / "bridge.log")
    monkeypatch.setattr(server, "HERMES_CHUCK_PENDING_DIR", hermes_home / "whatsapp" / "chuck_realtor_pending")
    monkeypatch.setattr(server, "HERMES_PROFILES_DIR", hermes_home / "profiles")
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

def test_reattached_zombie_checks_share_one_process_state_scan(monkeypatch):
    """N reattached children require one bulk `ps`, never one fork per PID."""
    calls = []

    class Result:
        returncode = 0
        stderr = ""
        stdout = "11111 S\n22222 Z\n33333 S\n"

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    server._reset_ttl_memo_caches()
    monkeypatch.setattr(server.subprocess, "run", fake_run)

    assert server._pid_is_zombie(11111) is False
    assert server._pid_is_zombie(22222) is True
    assert server._pid_is_zombie(33333) is False
    assert calls == [["ps", "-A", "-o", "pid=,stat="]]

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


def test_token_quality_cache_directory_is_scanned_once(monkeypatch, tmp_path):
    """Quality lookup indexes cache files once, never once per session row."""
    quality_dir = tmp_path / ".codex" / "token-optimizer"
    quality_dir.mkdir(parents=True)
    sids = [str(uuid.uuid4()) for _ in range(100)]
    for sid in sids:
        (quality_dir / f"quality-cache-{sid}.json").write_text(
            json.dumps({"score": 95, "grade": "A", "timestamp": "now"})
        )

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_CACHE", {})
    scans = []
    original_glob = Path.glob
    original_iterdir = Path.iterdir

    def counting_glob(path, pattern):
        scans.append(path)
        return original_glob(path, pattern)

    def counting_iterdir(path):
        scans.append(path)
        return original_iterdir(path)

    monkeypatch.setattr(Path, "glob", counting_glob)
    monkeypatch.setattr(Path, "iterdir", counting_iterdir)

    values = [server._token_optimizer_quality_for_session(sid) for sid in sids]

    assert all(value.get("quality_score") == 95 for value in values)
    assert len(scans) <= 2, (
        f"quality lookup scanned cache directories {len(scans)}x for "
        f"{len(sids)} rows instead of sharing one directory index"
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

    # Behavioural: a warm rebuild must not re-parse Claude transcripts. Other
    # engine tail caches share _conv_meta_cache_dirty, so spy on the Claude
    # extraction path directly instead of using the global dirty flag as a
    # proxy.
    orig_extract = server._extract_tail_meta
    tail_misses = []

    def spy_extract(path):
        try:
            st = path.stat()
            cache_key = (st.st_mtime_ns, st.st_size)
        except OSError:
            cache_key = None
        cached = server._conv_meta_cache.get(str(path))
        if not (cached and cached.get("cache_key") == cache_key):
            tail_misses.append(str(path))
        return orig_extract(path)

    monkeypatch.setattr(server, "_extract_tail_meta", spy_extract)
    server.find_all_conversations(**flags)
    assert not tail_misses, (
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


def test_conversations_all_stale_ok_uses_coalesced_serve_cache(monkeypatch):
    """Dashboard archive polls must share the serve cache instead of each
    rehydrating the full archive response independently."""
    calls = []
    expected = [{"session_id": "sid-cached", "mtime": 1}]

    def serve_rows(key, options):
        calls.append((key, options))
        return expected, True

    monkeypatch.setattr(server, "_archive_serve_rows", serve_rows)
    monkeypatch.setattr(
        server,
        "_build_archive_conversations",
        lambda **kwargs: pytest.fail("stale_ok bypassed the coalesced serve cache"),
    )

    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = (
            f"http://127.0.0.1:{httpd.server_address[1]}"
            "/api/conversations/all?stale_ok=1"
        )
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert payload["conversations"] == expected
    assert payload["count"] == 1
    assert len(calls) == 1
    assert calls[0] == (_ALL_KEY, _ALL_OPTS)


def test_archive_cache_clear_rejects_inflight_stale_write(isolated_archive_cache):
    """A refresh started before Archive must not restore its old row afterward."""
    generation = server._archive_serve_generation

    server._clear_archive_serve_cache()

    assert server._archive_serve_generation == generation + 1
    assert server._archive_serve_cache_store(
        _ALL_KEY,
        [{"session_id": "sid-a", "archived": False}],
        generation,
    ) is False
    assert _ALL_KEY not in server._archive_serve_cache
    assert server._archive_serve_cache_store(
        _ALL_KEY,
        [{"session_id": "sid-a", "archived": True}],
        server._archive_serve_generation,
    ) is True
    assert server._archive_serve_cache[_ALL_KEY]["rows"][0]["archived"] is True


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
    assert len(builds) == 1, (
        f"expected exactly one scan after a real change, got {len(builds)}"
    )
    # W84: a changed corpus must be RE-READ, never served stale — but the
    # re-read should be the incremental delta path (one scan scoped to the
    # touched transcript), not a full O(all-rows) rebuild. A full rebuild
    # (from_cache=False, unscoped scan) is the acceptable fallback when the
    # delta is unknowable (restart, engine-source change, bulk delta).
    only = builds[0][1].get("only_jsonl_paths")
    if from_cache:
        assert only is not None and str(p) in {str(x) for x in only}, (
            "incremental refresh did not re-parse the changed transcript"
        )
        assert len(only) == 1, (
            f"1-file change re-parsed {len(only)} files — delta is not scoped"
        )
    else:
        assert only is None, "full rebuild must scan the whole corpus"
    assert any(r.get("session_id") == sids[0] for r in rows)
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

    def fake_archive_conversations(*args, **kwargs):
        calls.append(now[0])
        return []

    monkeypatch.setattr(server, "find_all_conversations", fake_archive_conversations)

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


def test_throughput_history_cache_only_reads_engine_scoped_cache(monkeypatch):
    server._THROUGHPUT_AGG_CACHE.clear()
    key = server._throughput_aggregate_cache_key("all_56_days", "claude")
    server._THROUGHPUT_AGG_CACHE[key] = {
        "ts": time.time(),
        "status": 200,
        "payload": {"summary": {"daily": [{"date": "2026-07-12"}]}},
    }

    def fail_compute(*_args, **_kwargs):
        raise AssertionError("cache-only history must not compute")

    monkeypatch.setattr(server, "_throughput_payload", fail_compute)
    payload, status = server._throughput_history_payload(cache_only=True)

    assert status == 200
    assert payload == {
        "ok": True,
        "daily": [{"date": "2026-07-12"}],
        "cached": True,
    }


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


def test_throughput_bootstrap_round_trips_complete_context(monkeypatch, tmp_path):
    """The fast snapshot must contain every input needed by the final graph."""
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        server,
        "_weekly_usage_block",
        lambda: {"available": True, "pct_per_token": 0.01},
    )
    monkeypatch.setattr(
        server,
        "usage_reset_events_payload",
        lambda **_kwargs: {"events": [{"id": "reset-1"}]},
    )
    payload = {
        "ok": True,
        "session_id": "all_7_days",
        "scope": {"aggregate": True, "engine": "claude"},
        "summary": {},
        "turns": [],
    }

    model = server._throughput_build_bootstrap(
        "all_7_days",
        "claude",
        payload,
        generated_at=123.0,
        refresh={"sessions_read": 9},
    )
    assert server._throughput_write_bootstrap("all_7_days", "claude", model)

    loaded = server._throughput_read_bootstrap("all_7_days", "claude")

    assert loaded["schema"] == server._THROUGHPUT_BOOTSTRAP_SCHEMA
    assert loaded["throughput"] == payload
    assert loaded["weekly"]["pct_per_token"] == 0.01
    assert loaded["reset_events"] == [{"id": "reset-1"}]
    assert loaded["generated_at"] == 123.0
    assert loaded["refresh"]["sessions_read"] == 9


def test_throughput_bootstrap_preserves_zero_generation_time(monkeypatch):
    monkeypatch.setattr(server, "_weekly_usage_block", lambda: {})
    monkeypatch.setattr(server, "usage_reset_events_payload", lambda **_: {"events": []})
    payload = {
        "ok": True,
        "session_id": "all_7_days",
        "scope": {"aggregate": True, "engine": "claude"},
        "summary": {},
        "turns": [],
    }

    model = server._throughput_build_bootstrap(
        "all_7_days", "claude", payload, generated_at=0
    )

    assert model["generated_at"] == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema", 999),
        ("session_id", "all_today"),
        ("engine", "codex"),
        ("weekly", None),
        ("reset_events", {}),
    ],
)
def test_throughput_bootstrap_rejects_incompatible_models(
    monkeypatch, tmp_path, field, value
):
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)
    model = {
        "schema": 1,
        "session_id": "all_7_days",
        "engine": "claude",
        "generated_at": 123.0,
        "throughput": {
            "ok": True,
            "session_id": "all_7_days",
            "scope": {"aggregate": True, "engine": "claude"},
            "summary": {},
            "turns": [],
        },
        "weekly": {"available": True},
        "reset_events": [],
        "refresh": {},
    }
    model[field] = value
    path = tmp_path / "bootstrap-all_7_days.json"
    path.write_text(json.dumps(model), encoding="utf-8")

    assert server._throughput_read_bootstrap("all_7_days", "claude") is None


def test_throughput_initial_payload_includes_only_cached_complete_bootstrap(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)

    def fail_compute(*_args, **_kwargs):
        raise AssertionError("cache-only bootstrap must not compute context")

    monkeypatch.setattr(server, "_weekly_usage_block", fail_compute)
    monkeypatch.setattr(server, "usage_reset_events_payload", fail_compute)
    monkeypatch.setattr(server, "find_all_conversations", fail_compute)

    payload, status = server._throughput_initial_payload("all_7_days")

    assert status == 200
    assert payload["ok"] is True
    assert payload["bootstrap"] is None


def test_throughput_refresh_is_single_flight(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def fake_payload(*_args, progress=None, **_kwargs):
        calls.append(1)
        entered.set()
        release.wait(2)
        return {
            "ok": True,
            "session_id": "all_7_days",
            "scope": {"aggregate": True, "engine": "claude"},
            "summary": {},
            "turns": [],
        }, 200

    monkeypatch.setattr(server, "_throughput_payload", fake_payload)
    monkeypatch.setattr(server, "_throughput_build_bootstrap", lambda *a, **k: {})
    monkeypatch.setattr(server, "_throughput_write_bootstrap", lambda *a, **k: True)
    with server._THROUGHPUT_REFRESH_LOCK:
        server._THROUGHPUT_REFRESH_JOBS.clear()

    first = server._throughput_refresh_start("all_7_days", "claude")
    assert entered.wait(1)
    second = server._throughput_refresh_start("all_7_days", "claude")

    assert first["job_id"] == second["job_id"]
    assert len(calls) == 1
    release.set()


def test_throughput_refresh_reports_live_session_progress(monkeypatch):
    finished = threading.Event()

    def fake_payload(*_args, progress=None, **_kwargs):
        progress("sessions_discovered", 12)
        progress("cache_hit")
        progress("parsed")
        progress("session_read")
        finished.set()
        return {
            "ok": True,
            "session_id": "all_7_days",
            "scope": {"aggregate": True, "engine": "claude"},
            "summary": {},
            "turns": [],
        }, 200

    monkeypatch.setattr(server, "_throughput_payload", fake_payload)
    monkeypatch.setattr(server, "_throughput_build_bootstrap", lambda *a, **k: {})
    monkeypatch.setattr(server, "_throughput_write_bootstrap", lambda *a, **k: True)
    with server._THROUGHPUT_REFRESH_LOCK:
        server._THROUGHPUT_REFRESH_JOBS.clear()

    started = server._throughput_refresh_start("all_7_days", "claude")
    assert finished.wait(1)
    for _ in range(100):
        status = server._throughput_refresh_status("all_7_days", "claude")
        if status["state"] == "complete":
            break
        time.sleep(0.01)

    assert status["job_id"] == started["job_id"]
    assert status["sessions_discovered"] == 12
    assert status["sessions_read"] == 1
    assert status["cache_hits"] == 1
    assert status["parsed"] == 1
    assert status["last_refreshed_at"] is not None
    assert status["expected_ms"] > 0
    assert status["elapsed_ms"] >= 0


def test_throughput_refresh_failure_preserves_previous_completion(monkeypatch):
    def fail_payload(*_args, **_kwargs):
        return {"error": "boom"}, 500

    monkeypatch.setattr(server, "_throughput_payload", fail_payload)
    with server._THROUGHPUT_REFRESH_LOCK:
        server._THROUGHPUT_REFRESH_JOBS.clear()
        server._THROUGHPUT_REFRESH_LAST_SUCCESS["all_7_days:claude"] = {
            "completed_at": 456.0,
            "duration_ms": 1200,
        }

    server._throughput_refresh_start("all_7_days", "claude")
    for _ in range(100):
        status = server._throughput_refresh_status("all_7_days", "claude")
        if status["state"] == "failed":
            break
        time.sleep(0.01)

    assert status["state"] == "failed"
    assert status["error"] == "boom"
    assert status["last_refreshed_at"] == 456.0
    assert status["expected_ms"] == 1200


def test_throughput_refresh_uses_persisted_duration_after_restart(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def fake_payload(*_args, **_kwargs):
        entered.set()
        release.wait(2)
        return {"error": "stopped"}, 500

    monkeypatch.setattr(server, "_throughput_payload", fake_payload)
    monkeypatch.setattr(
        server,
        "_throughput_read_bootstrap",
        lambda *_args, **_kwargs: {
            "generated_at": 789.0,
            "refresh": {"elapsed_ms": 4200},
        },
    )
    with server._THROUGHPUT_REFRESH_LOCK:
        server._THROUGHPUT_REFRESH_JOBS.clear()
        server._THROUGHPUT_REFRESH_LAST_SUCCESS.clear()

    status = server._throughput_refresh_start("all_7_days", "claude")
    assert entered.wait(1)

    assert status["expected_ms"] == 4200
    assert status["last_refreshed_at"] == 789.0
    release.set()


def test_throughput_refresh_persists_completed_metadata(monkeypatch):
    captured = {}

    def fake_payload(*_args, **_kwargs):
        return {
            "ok": True,
            "session_id": "all_7_days",
            "scope": {"aggregate": True, "engine": "claude"},
            "summary": {},
            "turns": [],
        }, 200

    def fake_build(*_args, **kwargs):
        captured["refresh"] = kwargs["refresh"]
        return {"valid": True}

    monkeypatch.setattr(server, "_throughput_payload", fake_payload)
    monkeypatch.setattr(server, "_throughput_build_bootstrap", fake_build)
    monkeypatch.setattr(server, "_throughput_write_bootstrap", lambda *_: True)
    with server._THROUGHPUT_REFRESH_LOCK:
        server._THROUGHPUT_REFRESH_JOBS.clear()
        server._THROUGHPUT_REFRESH_LAST_SUCCESS.clear()

    server._throughput_refresh_start("all_7_days", "claude")
    for _ in range(100):
        status = server._throughput_refresh_status("all_7_days", "claude")
        if status["state"] == "complete":
            break
        time.sleep(0.01)

    persisted = captured["refresh"]
    assert persisted["state"] == "complete"
    assert persisted["completed_at"] == persisted["last_refreshed_at"]
    assert persisted["elapsed_ms"] == persisted["expected_ms"]
    assert persisted["elapsed_ms"] > 0


@pytest.mark.parametrize("session_id", ["", "single-session", "all_today"])
def test_throughput_refresh_rejects_unsupported_scopes(session_id):
    assert server._throughput_refresh_scope_supported(session_id) is False


def test_throughput_refresh_accepts_default_aggregate_scope():
    assert server._throughput_refresh_scope_supported("all_7_days") is True


def test_throughput_refresh_projects_remaining_time_from_live_progress():
    job = {
        "state": "refreshing",
        "started_at": 100.0,
        "completed_at": None,
        "expected_ms": 35000,
        "sessions_discovered": 100,
        "sessions_read": 50,
    }

    status = server._throughput_refresh_public(job, now=101.0)

    assert 3000 <= status["expected_ms"] <= 4000


def test_throughput_recent_conversations_filters_before_opening(monkeypatch, tmp_path):
    projects = tmp_path / "projects"
    folder = projects / "-tmp-repo"
    folder.mkdir(parents=True)
    old = folder / "old-session.jsonl"
    recent = folder / "recent-session.jsonl"
    old.write_text("not-json\n", encoding="utf-8")
    recent.write_text("not-json\n", encoding="utf-8")
    now = 1_800_000_000.0
    os.utime(old, (now - 15 * 86400, now - 15 * 86400))
    os.utime(recent, (now - 2 * 86400, now - 2 * 86400))
    events = []

    rows = server._throughput_recent_conversations(
        "claude",
        now - 14 * 86400,
        projects_root=projects,
        progress=lambda event, value=None: events.append((event, value)),
    )

    assert [row["session_id"] for row in rows] == ["recent-session"]
    assert rows[0]["jsonl_path"] == str(recent)
    assert ("phase", "discovering") in events
    assert ("folders_scanned", 1) in events
    assert ("sessions_discovered", 1) in events


def test_throughput_recent_conversations_isolates_codex_and_applies_cutoff(
    monkeypatch, tmp_path
):
    old = tmp_path / "old.jsonl"
    recent = tmp_path / "recent.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    recent.write_text("{}\n", encoding="utf-8")
    now = 1_800_000_000.0
    os.utime(old, (now - 20 * 86400, now - 20 * 86400))
    os.utime(recent, (now - 2 * 86400, now - 2 * 86400))
    monkeypatch.setattr(
        server,
        "_codex_fetch_threads",
        lambda limit=None: [
            {"id": "old", "path": str(old), "title": "Old"},
            {"id": "recent", "path": str(recent), "title": "Recent"},
        ],
    )
    resolved = []

    def rollout_path(row):
        resolved.append(row["id"])
        return Path(row["path"])

    monkeypatch.setattr(server, "_codex_rollout_path_from_row", rollout_path)
    monkeypatch.setattr(
        server,
        "_codex_ts_seconds",
        lambda row, *_args: now - (20 if row["id"] == "old" else 2) * 86400,
    )

    rows = server._throughput_recent_conversations("codex", now - 14 * 86400)

    assert [row["session_id"] for row in rows] == ["recent"]
    assert rows[0]["engine"] == "codex"
    assert resolved == ["recent"]


def test_throughput_long_scopes_keep_archive_semantics(monkeypatch):
    server._THROUGHPUT_AGG_CACHE.clear()
    monkeypatch.setattr(
        server,
        "_throughput_recent_conversations",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("bounded indexer is only for the live seven-day view")
        ),
    )
    monkeypatch.setattr(server, "find_all_conversations", lambda **_kwargs: [])

    payload, status = server._throughput_payload("all_56_days", force_refresh=True)

    assert status == 200
    assert payload["scope"]["range"] == "Last 56 days"


def test_throughput_aggregate_uses_bounded_indexer_not_archive(monkeypatch, tmp_path):
    server._THROUGHPUT_AGG_CACHE.clear()
    monkeypatch.setattr(server, "_THROUGHPUT_DISK_CACHE_DIR", tmp_path)
    seen = {}

    def recent(engine, cutoff, **_kwargs):
        seen.update(engine=engine, cutoff=cutoff)
        return []

    def fail_archive(*_args, **_kwargs):
        raise AssertionError("throughput must not scan the global archive")

    monkeypatch.setattr(server, "_throughput_recent_conversations", recent)
    monkeypatch.setattr(server, "find_all_conversations", fail_archive)
    now = time.time()

    payload, status = server._throughput_payload(
        "all_7_days", engine_filter="claude", force_refresh=True
    )

    assert status == 200
    assert payload["ok"] is True
    assert seen["engine"] == "claude"
    assert 13.9 * 86400 <= now - seen["cutoff"] <= 14.1 * 86400


def test_weekly_usage_uses_recent_throughput_indexer(monkeypatch):
    calls = []
    monkeypatch.setattr(server, "_live_weekly_usage", lambda **_: None)
    monkeypatch.setattr(server, "_weekly_pct_calibration", lambda: None)
    monkeypatch.setattr(
        server,
        "_throughput_recent_conversations",
        lambda engine, cutoff, **kwargs: calls.append((engine, cutoff)) or [],
    )
    monkeypatch.setattr(
        server,
        "find_all_conversations",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("weekly context must not scan the global archive")
        ),
    )

    result = server._weekly_usage_block()

    assert result["available"] is False
    assert calls and calls[0][0] == "claude"
    assert time.time() - 8 * 86400 < calls[0][1] < time.time() - 6 * 86400


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


def test_import_doc_does_no_all_sessions_work(monkeypatch, tmp_path):
    """The plan-to-fleet import shell-out (W51) touches one file and one `wt`
    subprocess — never a scan of every conversation/session. Guard the call
    counts so a future edit can't quietly add an O(all sessions) lookup."""
    conv_calls = _count_calls(monkeypatch, "find_all_conversations", passthrough_return=[])

    # _q is a module, not a plain function, so spy on its list_items directly.
    list_calls = []
    orig_list = server._q.list_items
    def list_spy(*a, **k):
        list_calls.append((a, k))
        return orig_list(*a, **k)
    monkeypatch.setattr(server._q, "list_items", list_spy)

    class _FakeProc:
        returncode = 0
        stdout = (
            "WOULD FILE: [feature] Add a thing (L1-L4)\n"
            "EXISTS: [bug] Old thing (L9)\n"
            "IMPORT dry-run: candidates=2 new=1 existing=1; pass --apply to file\n"
        )
        stderr = ""

    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: _FakeProc())

    doc = tmp_path / "plan.md"
    doc.write_text("# Plan\n- do a thing\n")
    result = server._run_wt_import(doc, "PRODUCT", apply=False)

    assert result["ok"] is True
    assert result["counts"] == {"candidates": 2, "new": 1, "existing": 1}
    assert conv_calls == [], "import-doc must not scan all conversations"
    assert list_calls == [], "import-doc dry-run must not list queue items"


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


# ── Archive incremental refresh (W84) ───────────────────────────────────────
# "Every session on disk" is the product premise: a transcript that appears in
# ~/.claude/projects must reach the ?all=1 snapshot (dashboard list + search)
# on the next refresh, and that refresh must re-parse ONLY the touched files.
# Before this gate, ANY corpus change re-ran the full O(all-rows) build
# (~12s warm, 60-95s under live GIL contention), so list/search freshness
# equaled the rebuild duration.

def test_archive_incremental_refresh_reparses_only_changed_transcripts(
    big_projects, monkeypatch, tmp_path
):
    n, _ = big_projects
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE", {})
    monkeypatch.setattr(server, "_ARCHIVE_RESPONSE_CACHE_LOADED", True)
    monkeypatch.setattr(server, "_ARCHIVE_STATMAP_BY_SIG", {})
    monkeypatch.setattr(server, "_archive_sig_cache", {"ts": 0.0, "sig": None})
    monkeypatch.setattr(server, "_ARCHIVE_SIG_TTL", 0.0)  # re-walk per call in test
    monkeypatch.setattr(server, "_save_archive_response_cache", lambda: None)
    monkeypatch.setattr(server, "_save_conv_meta_cache", lambda: None)

    builds = []
    real_build = server._build_archive_conversations

    def counting_build(**kw):
        builds.append(kw)
        return real_build(**kw)

    monkeypatch.setattr(server, "_build_archive_conversations", counting_build)

    opts = {
        "include_prs": False,
        "resolve_pr_states": False,
        "resolve_effective": False,
        "resolve_worktree_dirty": False,
    }
    key = server._archive_response_cache_key(**opts)

    _, from_cache = server._archive_compute_rows(key, opts)
    assert len(builds) == 1 and from_cache is False  # cold: one full build

    _, from_cache = server._archive_compute_rows(key, opts)
    assert len(builds) == 1 and from_cache is True  # unchanged: rehydrate only

    # A new transcript lands. It must appear in the rows WITHOUT a second
    # full build, and per-row work must be O(delta), not O(all rows).
    root = tmp_path / ".claude" / "projects" / "-tmp-perf-repo"
    new_sid = str(uuid.uuid4())
    _write_transcript(root / f"{new_sid}.jsonl", new_sid, old_ts=time.time())
    quality_calls = _count_calls(
        monkeypatch, "_token_optimizer_quality_for_session", passthrough_return={}
    )

    rows, from_cache = server._archive_compute_rows(key, opts)

    assert new_sid in {r.get("session_id") for r in rows}, (
        "new transcript missing from the refreshed archive snapshot"
    )
    assert len(builds) == 1, (
        "a single new transcript triggered a full archive rebuild — "
        "the W84 incremental delta path regressed"
    )
    assert len(quality_calls) <= 3, (
        f"per-row quality lookup ran {len(quality_calls)}x for a 1-file delta "
        f"over {n} rows — delta build is doing O(all-rows) work"
    )

    # Deleting the transcript must drop the row on the next refresh —
    # still without a full rebuild.
    (root / f"{new_sid}.jsonl").unlink()
    rows, _ = server._archive_compute_rows(key, opts)
    assert new_sid not in {r.get("session_id") for r in rows}
    assert len(builds) == 1
