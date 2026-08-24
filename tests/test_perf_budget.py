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
import gzip
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import warnings
from pathlib import Path

import pytest

server = importlib.import_module("server")


def test_healthcheck_cold_cache_build_is_singleflight(monkeypatch):
    """Concurrent page loads share one expensive healthcheck rebuild."""
    server._HEALTHCHECK_CACHE = {"ts": 0.0, "data": None}
    calls = 0
    calls_lock = threading.Lock()
    start = threading.Barrier(8)

    def build():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {"checks": [], "overall": "ok"}

    monkeypatch.setattr(server, "_build_healthcheck", build)

    def run():
        start.wait(timeout=2)
        return server._run_healthcheck()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: run(), range(8)))

    assert calls == 1, f"cold healthcheck rebuilt {calls}x for one page-load burst"
    assert results == [{"checks": [], "overall": "ok"}] * 8


def test_healthcheck_session_probe_stops_after_first_file(monkeypatch, tmp_path):
    """The readiness check must not enumerate the whole transcript archive."""
    projects = tmp_path / "projects"
    project = projects / "repo"
    project.mkdir(parents=True)
    for i in range(20):
        (project / f"{i}.jsonl").write_text("{}\n")

    seen = 0
    original_rglob = Path.rglob

    def rglob_once(path, pattern):
        nonlocal seen
        for item in original_rglob(path, pattern):
            seen += 1
            if seen > 1:
                raise AssertionError("healthcheck enumerated more than one session file")
            yield item

    monkeypatch.setattr(Path, "rglob", rglob_once)

    assert server._has_claude_session_file(projects) is True
    assert seen == 1


def test_archive_list_projection_keeps_renderer_and_lifecycle_contract():
    """The slim sidebar response must retain every current row-critical field."""
    now = 2_000_000.0
    transcript = " ".join(
        f"assistant-debug-{index:04d}-{(index * 2654435761) % (2 ** 32):08x}"
        for index in range(600)
    )
    row = {
        "id": "row-1",
        "session_id": "session-1",
        "source": "interactive",
        "engine": "codex",
        "source_platform": "cli",
        "folder_label": "demo",
        "folder_path": "/tmp/demo",
        "slug": "demo",
        "session_cwd": "/tmp/demo",
        "session_cwd_exists": True,
        "session_cwd_is_worktree": True,
        "mtime": now - 10,
        "modified": now - 10,
        "last_interacted": now - 10,
        "size": 123,
        "first_message": "Keep lifecycle metadata",
        "ai_title": "Lifecycle title",
        "display_name": "Lifecycle title",
        "name_overridden": True,
        "spawn_pid": 4242,
        "branch": "main",
        "git_branch": "main",
        "effective_branch": "feature/list",
        "effective_kind": "worktree",
        "worktree_label": "list",
        "archived": True,
        "trashed": True,
        "pinned": True,
        "pin_rank": 2,
        "all_lane_override": "workers",
        "state": "ended",
        "ended_blocked": True,
        "session_state": {"did": "preserved"},
        "goal": "ship this",
        "goal_status": "active",
        "parent_session_id": "parent-1",
        "hermes_lineage_session_ids": ["parent-1"],
        "model": "gpt-5.5",
        "is_live": True,
        "sidecar_status": "working",
        "pending_tool": "Bash",
        "question_waiting": True,
        "question_text": "Approve?",
        "latest_input_tokens": 99,
        "live_context_percent": 25,
        "quality_score": 95,
        "quality_summary": "healthy",
        "codex_state": "working",
        "codex_fresh": True,
        "jsonl_path": "/private/transcript.jsonl",
        "last_assistant_text": transcript,
        "debug": {"transcript": transcript},
    }

    payload = server._archive_list_payload([row], window="1d", now=now)

    projected = payload["conversations"]
    assert projected == [
        {key: row[key] for key in server._ARCHIVE_LIST_FIELDS if key in row}
    ]
    assert payload["window"] == "1d"
    assert payload["count"] == 1
    assert payload["total_count"] == 1
    assert {"archived", "trashed", "pinned", "all_lane_override", "session_state", "goal", "codex_state", "spawn_pid"} <= set(projected[0])
    assert "jsonl_path" not in projected[0]
    assert "last_assistant_text" not in projected[0]
    assert "debug" not in projected[0]
    full = json.dumps({"conversations": [row]}).encode()
    slim = json.dumps(payload).encode()
    assert len(slim) < len(full) * 0.45
    assert len(gzip.compress(slim)) < len(gzip.compress(full)) * 0.75


def test_archive_list_window_filters_old_rows_but_keeps_pinned_and_hermes():
    now = 2_000_000.0
    rows = [
        {"session_id": "recent", "engine": "claude", "mtime": now - 60},
        {"session_id": "week-old", "engine": "claude", "mtime": now - 2 * 86400},
        {"session_id": "old", "engine": "claude", "mtime": now - 8 * 86400},
        {"session_id": "pinned-old", "engine": "claude", "pinned": True, "mtime": now - 8 * 86400},
        {"session_id": "hermes-old", "engine": "hermes", "mtime": now - 8 * 86400},
    ]

    assert [row["session_id"] for row in server._archive_list_project_rows(rows, window="1d", now=now)] == ["recent", "pinned-old", "hermes-old"]
    assert [row["session_id"] for row in server._archive_list_project_rows(rows, window="7d", now=now)] == ["recent", "week-old", "pinned-old", "hermes-old"]
    assert [row["session_id"] for row in server._archive_list_project_rows(rows, window="all", now=now)] == ["recent", "week-old", "old", "pinned-old", "hermes-old"]


def test_archive_list_rows_reuse_warm_archive_cache(monkeypatch):
    calls = []

    def cached(options):
        calls.append(options)
        return ([{"session_id": "warm", "mtime": 2_000_000}], True)

    monkeypatch.setattr(server, "_archive_all_rows_cached", cached)

    rows, from_cache = server._archive_list_rows_cached({"include_prs": False}, window="all")

    assert [row["session_id"] for row in rows] == ["warm"]
    assert from_cache is True
    assert calls == [{"include_prs": False}]


def test_archive_list_source_avoids_copying_full_snapshot(monkeypatch):
    """A short sidebar window must not copy/refresh every historical row."""
    expected = [{"session_id": "recent"}, {"session_id": "old"}]
    calls = []

    def serve(
        key,
        options,
        *,
        copy_rows=True,
        force_refresh=False,
    ):
        calls.append((key, options, copy_rows, force_refresh))
        return expected, True, 7

    monkeypatch.setattr(server, "_archive_serve_rows_versioned", serve)
    options = {"include_prs": False}

    rows, from_cache = server._archive_list_source_rows_cached(options)

    assert rows is expected
    assert from_cache is True
    fresh_rows, fresh_from_cache = server._archive_list_source_rows_cached(
        options,
        force_refresh=True,
    )

    assert fresh_rows is expected
    assert fresh_from_cache is True
    assert calls == [(
        server._archive_response_cache_key(**options),
        options,
        False,
        False,
    ), (
        server._archive_response_cache_key(**options),
        options,
        False,
        True,
    )]


def test_archive_list_http_route_projects_cached_rows(monkeypatch):
    monkeypatch.setattr(
        server,
        "_archive_list_source_rows_cached",
        lambda _options, **_kwargs: ([{
            "session_id": "trashed-row", "engine": "claude", "mtime": 2_000_000,
            "archived": True, "trashed": True, "all_lane_override": "messages",
            "last_assistant_text": "not returned",
        }], True),
    )
    httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.CommandCenterHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/conversations/list?window=all",
            timeout=5,
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert body["window"] == "all"
    assert body["cached"] is True
    assert body["conversations"] == [{
        "session_id": "trashed-row", "engine": "claude", "mtime": 2_000_000,
        "archived": True, "trashed": True, "all_lane_override": "messages",
    }]


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
    # Devin CLI reads its sessions DB via CCC_DEVIN_DB (env override, checked
    # fresh per call) rather than a server-module global, so it isn't covered
    # by the monkeypatch.setattr overrides above. Without this, find_all_
    # conversations hits the developer's REAL ~/.local/share/devin/cli/
    # sessions.db (can be multi-GB with a large WAL) on every call, which is
    # slow and makes this perf gate depend on the developer's box. Point it at
    # a path with no file so _devin_cli_connect() short-circuits to None.
    monkeypatch.setenv("CCC_DEVIN_DB", str(tmp_path / ".local" / "share" / "devin" / "cli" / "sessions.db"))
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


def test_process_comm_native_path_does_not_touch_filesystem(monkeypatch):
    """Classifying every ps row must be string work, not thousands of lstats."""
    native = Path.home() / ".local" / "share" / "claude" / "versions" / "2.1.144"
    resolve_calls = []
    real_resolve = Path.resolve

    def counting_resolve(path, *args, **kwargs):
        resolve_calls.append(path)
        return real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", counting_resolve)

    assert server._process_comm_is_claude(str(native))
    assert resolve_calls == [], "process-name classification performed filesystem resolution"


def test_session_registry_reuses_shared_process_snapshot(monkeypatch, tmp_path):
    """Registry validation and engine discovery share one cached ps snapshot."""
    sid = "00000000-0000-4000-8000-000000000001"
    (tmp_path / "123.json").write_text(
        json.dumps({"pid": 123, "sessionId": sid}),
        encoding="utf-8",
    )
    native = Path.home() / ".local" / "share" / "claude" / "versions" / "2.1.144"
    subprocess_calls = []

    monkeypatch.setattr(server, "SESSIONS_REGISTRY", tmp_path)
    monkeypatch.setattr(
        server,
        "_scan_engine_processes",
        lambda: [("123", "??", str(native), str(native))],
    )

    def fake_run(args, **kwargs):
        subprocess_calls.append(args)
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=f"123 {native}\n",
            stderr="",
        )

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    server._load_session_registry.cache_clear()

    assert sid in server._load_session_registry()
    assert subprocess_calls == [], "registry forked a second ps instead of sharing the snapshot"


def test_live_engine_ids_use_raw_process_snapshot_without_metadata_probes(monkeypatch):
    """The idle liveness poll must not fork cwd/terminal probes per engine PID."""
    sid = "00000000-0000-4000-8000-000000000002"
    monkeypatch.setattr(server, "_spawned_sessions", [])
    monkeypatch.setattr(
        server,
        "_scan_engine_processes",
        lambda: [("123", "??", "/usr/local/bin/codex", f"codex resume {sid}")],
    )
    for name in (
        "find_live_codex_processes",
        "find_live_gemini_processes",
        "find_live_cursor_processes",
    ):
        monkeypatch.setattr(
            server,
            name,
            lambda name=name: pytest.fail(f"idle liveness called metadata scanner {name}"),
        )
    server._engine_live_sids_cache.update(ts=0.0, sids=frozenset())

    assert server._live_engine_session_ids() == frozenset({sid})


def test_codex_pool_probe_uses_raw_process_snapshot_without_metadata(monkeypatch):
    """Checking for app-server must not resolve cwd or terminal ancestry."""
    monkeypatch.setattr(
        server,
        "_scan_engine_processes",
        lambda: [("123", "??", "/usr/local/bin/codex", "codex app-server")],
    )
    monkeypatch.setattr(
        server,
        "find_live_codex_processes",
        lambda: pytest.fail("pool probe called the full Codex process scanner"),
    )
    server._codex_pool_alive_cache.update(ts=0.0, alive=False)

    assert server._codex_pool_alive()


def test_codex_desktop_probe_uses_raw_process_snapshot_without_metadata(monkeypatch):
    """Desktop attachment discovery needs PID/command only, never terminal data."""
    desktop_cmd = "/Applications/Codex.app/Contents/MacOS/codex app-server"
    monkeypatch.setattr(
        server,
        "_raw_engine_process_commands",
        lambda engine: iter([("123", desktop_cmd), ("124", "codex app-server")]),
    )
    monkeypatch.setattr(
        server,
        "find_live_codex_processes",
        lambda: pytest.fail("desktop probe called the full Codex process scanner"),
    )
    monkeypatch.setattr(server, "_CODEX_APP_SERVER_PROC", None)

    assert server._codex_desktop_app_server_procs() == [
        {"pid": 123, "command": desktop_cmd},
    ]


def test_liveness_uses_memoized_engine_classifier(monkeypatch):
    """One candidate gets one cached engine classification, not every probe."""
    sid = "gemini-session"
    probe_calls = []
    monkeypatch.setattr(server, "_detect_session_engine", lambda value: "gemini")
    for name in (
        "_is_kimi_session",
        "_is_codex_session",
        "_is_cursor_session",
        "_is_gemini_session",
        "_is_antigravity_session",
        "_is_kilo_session",
        "_is_opencode_session",
    ):
        monkeypatch.setattr(
            server,
            name,
            lambda value, name=name: probe_calls.append(name) or False,
        )
    monkeypatch.setattr(server, "_live_engine_session_ids", lambda: frozenset({sid}))

    assert server._archive_session_is_live_uncached(sid)
    assert probe_calls == [], "liveness bypassed the memoized engine classifier"

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


def test_repo_sessions_gate_liveness_by_candidates(monkeypatch, tmp_path):
    """Repo-scoped /api/sessions must not probe liveness once per row."""
    now = time.time()
    candidate_sid = str(uuid.uuid4())
    rows = [
        {
            "id": sid,
            "session_id": sid,
            "source": "interactive",
            "modified": now - 3600,
            "mtime": now - 3600,
            "branch": "main",
            "display_name": "session",
            "first_message": "work",
        }
        for sid in [candidate_sid, *(str(uuid.uuid4()) for _ in range(199))]
    ]

    monkeypatch.setattr(server, "resolve_repo_path", lambda path: str(tmp_path))
    monkeypatch.setattr(server, "_load_session_issues", lambda: {})
    monkeypatch.setattr(server, "_load_session_registry", lambda: {})
    monkeypatch.setattr(server, "find_conversations", lambda *a, **k: [dict(r) for r in rows])
    monkeypatch.setattr(server, "_spawned_sessions", [])
    codex_row = {
        "id": "codex-row",
        "session_id": "codex-row",
        "source": "codex",
        "modified": now - 1800,
    }
    monkeypatch.setattr(server, "find_codex_conversations", lambda *a, **k: [codex_row])
    for name in (
        "find_gemini_conversations",
        "find_cursor_conversations",
        "find_antigravity_conversations",
        "find_kilo_conversations",
        "find_hermes_conversations",
    ):
        monkeypatch.setattr(server, name, lambda *a, **k: [])
    monkeypatch.setattr(server, "_find_remote_sessions", lambda *a, **k: [])
    monkeypatch.setattr(server, "find_pkood_agents", lambda: [])
    monkeypatch.setattr(server, "_apply_watchtower_worker_display_names", lambda rows: None)
    monkeypatch.setattr(server, "find_backlog_items", lambda *a, **k: [])
    monkeypatch.setattr(server, "_cleanup_stale_sidecars", lambda *a, **k: None)
    monkeypatch.setattr(server, "_fetch_issue_states", lambda *a, **k: {})
    monkeypatch.setattr(server, "_load_desktop_app_metadata", lambda: {})
    monkeypatch.setattr(server, "_add_sidecar_fields", lambda row: None)
    monkeypatch.setattr(server, "_detect_issue_number_for_session", lambda row: None)
    monkeypatch.setattr(server, "_load_conversation_order", lambda: [])
    monkeypatch.setattr(server, "_load_pinned_conversations", lambda: [])
    monkeypatch.setattr(server, "_apply_pinned_conversation_fields", lambda *a, **k: None)
    monkeypatch.setattr(server, "_sort_pinned_conversations_first", lambda *a, **k: None)
    monkeypatch.setattr(server, "_apply_session_lane_overrides", lambda rows: None)
    verified_rows = []

    def capture_auto_verify(path, conversations=None):
        verified_rows.extend(conversations or [])

    monkeypatch.setattr(server, "auto_verify_closed_issues", capture_auto_verify)
    monkeypatch.setattr(server, "_discover_live_session_ids", lambda: {candidate_sid})
    calls = _count_calls(monkeypatch, "_archive_session_is_live", passthrough_return=True)

    result = server.find_all_sessions(str(tmp_path), include_old=False)

    assert len(result) == len(rows) + 1
    assert [args[0][0] for args in calls] == [candidate_sid], (
        f"repo session list made {len(calls)} precise liveness probes for "
        f"{len(rows)} old rows instead of probing only live candidates"
    )
    assert [row["session_id"] for row in verified_rows] == [
        row["session_id"] for row in rows
    ], "auto-verification must inspect only the original Claude conversation rows"


# ── Workflow-run discovery (candidacy gate) ──────────────────────────────────
# find_conversations attaches Workflow-tool runs from
# <project>/<sid>/subagents/workflows/<run>/. The scan must stay gated: with
# no session subdirs (the common case) it does ZERO filesystem work, and with
# subdirs it touches journals only for sessions that actually own them.

def _mk_workflow_repo(tmp_path, monkeypatch, n_plain=20):
    """Synthetic repo + projects root. Returns (repo, project_dir, parent_sid)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(server, "resolve_repo_path", lambda path: str(repo))
    root = tmp_path / ".claude" / "projects"
    monkeypatch.setattr(server, "PROJECTS_ROOT", root)
    project_dir = root / server._encode_project_slug(repo)
    project_dir.mkdir(parents=True)
    old_ts = time.time() - 30 * 86400
    for _ in range(n_plain):
        sid = str(uuid.uuid4())
        _write_transcript(project_dir / f"{sid}.jsonl", sid, old_ts=old_ts)
    parent_sid = str(uuid.uuid4())
    _write_transcript(project_dir / f"{parent_sid}.jsonl", parent_sid, old_ts=old_ts)
    return repo, project_dir, parent_sid


def _mk_run_dir(project_dir, parent_sid, run_id, agents, now=None):
    """One synthetic workflow run. ``agents``: {agent_id: status} where status
    is 'done' (started+result), 'running' (started, fresh mtime), or
    'interrupted' (started, stale mtime)."""
    now = now if now is not None else time.time()
    run_dir = project_dir / parent_sid / "subagents" / "workflows" / run_id
    run_dir.mkdir(parents=True)
    journal = []
    for agent_id, status in agents.items():
        journal.append({"type": "started", "key": "v2:" + agent_id, "agentId": agent_id})
        if status == "done":
            journal.append({"type": "result", "key": "v2:" + agent_id,
                            "agentId": agent_id, "result": "ok"})
        agent_file = run_dir / f"agent-{agent_id}.jsonl"
        agent_file.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": f"task {agent_id}"},
        }) + "\n")
        mtime = now if status == "running" else now - 3600
        os.utime(agent_file, (mtime, mtime))
    (run_dir / "journal.jsonl").write_text("\n".join(json.dumps(e) for e in journal) + "\n")
    return run_dir


def test_workflow_scan_no_subdirs_does_no_journal_work(monkeypatch, tmp_path):
    """N plain transcripts, zero session subdirs → zero journal probes."""
    repo, _project_dir, _parent = _mk_workflow_repo(tmp_path, monkeypatch)
    calls = _count_calls(monkeypatch, "_parse_workflow_journal", passthrough_return=None)
    server.find_conversations(str(repo))
    assert calls == [], (
        f"_parse_workflow_journal called {len(calls)}x with no session subdirs — "
        "the workflow discovery candidacy gate regressed"
    )


def test_workflow_scan_gated_to_subdir_owning_sessions(monkeypatch, tmp_path):
    """Only the session owning a subagents/workflows dir gets its journal read,
    and the row carries run + per-agent status."""
    repo, project_dir, parent_sid = _mk_workflow_repo(tmp_path, monkeypatch)
    _mk_run_dir(project_dir, parent_sid, "wf_test-001", {
        "aaa111": "done",
        "bbb222": "running",
        "ccc333": "interrupted",
    })
    calls = _count_calls(monkeypatch, "_parse_workflow_journal")
    rows = server.find_conversations(str(repo))
    assert len(calls) == 1, (
        f"_parse_workflow_journal called {len(calls)}x for one run dir — "
        "must be one read per run, never per row"
    )
    row = next(r for r in rows if r["session_id"] == parent_sid)
    assert len(row["workflows"]) == 1
    run = row["workflows"][0]
    assert run["run_id"] == "wf_test-001"
    assert run["status"] == "running", "one running agent makes the run running"
    by_id = {a["id"]: a for a in run["agents"]}
    assert by_id["aaa111"]["status"] == "done"
    assert by_id["bbb222"]["status"] == "running"
    assert by_id["ccc333"]["status"] == "interrupted"
    assert by_id["aaa111"]["conv_id"] == f"{parent_sid}:agent-aaa111"
    assert by_id["aaa111"]["description"] == "task aaa111"
    plain = next(r for r in rows if r["session_id"] != parent_sid)
    assert plain["workflows"] == []


def test_workflow_run_died_midflight_marks_interrupted(monkeypatch, tmp_path):
    """Started lines with no results and stale transcripts = interrupted, not
    running (the workflow runtime writes no tombstone)."""
    repo, project_dir, parent_sid = _mk_workflow_repo(tmp_path, monkeypatch, n_plain=0)
    _mk_run_dir(project_dir, parent_sid, "wf_dead-002", {
        "ddd444": "interrupted",
        "eee555": "interrupted",
    })
    rows = server.find_conversations(str(repo))
    run = next(r for r in rows if r["session_id"] == parent_sid)["workflows"][0]
    assert run["status"] == "interrupted"
    assert {a["status"] for a in run["agents"]} == {"interrupted"}


def test_archive_build_workflow_scan_gated(big_projects, monkeypatch):
    """The archive build must not probe journals when no session owns a
    subagents/workflows dir (the common case on a 200-transcript corpus)."""
    server._ARCHIVE_WORKFLOW_SESSION_DIRS.clear()
    calls = _count_calls(monkeypatch, "_parse_workflow_journal", passthrough_return=None)
    server._build_archive_conversations()
    assert calls == [], (
        f"_parse_workflow_journal called {len(calls)}x in an archive build with "
        "no workflow subdirs — the candidacy gate regressed"
    )
    assert server._ARCHIVE_WORKFLOW_SESSION_DIRS == {}


def test_archive_rehydrate_recomputes_workflow_status(big_projects, monkeypatch, tmp_path):
    """A workflow journal flips without any top-level transcript changing, so
    the cached-serve path (rehydrate) must recompute runs for owner rows."""
    n, sids = big_projects
    parent_sid = sids[0]
    project_dir = tmp_path / ".claude" / "projects" / "-tmp-perf-repo"
    run_dir = project_dir / parent_sid / "subagents" / "workflows" / "wf_rehy-003"
    run_dir.mkdir(parents=True)
    agent_file = run_dir / "agent-abc123.jsonl"
    agent_file.write_text(json.dumps(
        {"type": "user", "message": {"role": "user", "content": "do things"}}) + "\n")
    (run_dir / "journal.jsonl").write_text(
        json.dumps({"type": "started", "key": "v2:x", "agentId": "abc123"}) + "\n")
    fresh = time.time()
    os.utime(agent_file, (fresh, fresh))

    server._ARCHIVE_WORKFLOW_SESSION_DIRS.clear()
    server._ARCHIVE_WORKFLOW_SESSION_DIRS[parent_sid] = project_dir / parent_sid
    server._WORKFLOW_JOURNAL_CACHE.clear()
    row = {"session_id": parent_sid, "engine": "claude", "mtime": time.time() - 30 * 86400}
    journal_calls = _count_calls(monkeypatch, "_parse_workflow_journal")
    out = server._rehydrate_archive_cached_rows([row])
    assert len(journal_calls) == 1, (
        f"rehydrate probed {len(journal_calls)} journals for one owner row — "
        "must be one parse attempt per owner row per serve, never per all rows"
    )
    run = out[0]["workflows"][0]
    assert run["status"] == "running"
    # (mtime,size) cache: an unchanged journal re-parses nothing.
    parsed1 = server._parse_workflow_journal(run_dir / "journal.jsonl")
    parsed2 = server._parse_workflow_journal(run_dir / "journal.jsonl")
    assert parsed1 is parsed2, "journal parse cache did not hit on unchanged file"
    # A non-owner row costs nothing.
    plain = {"session_id": sids[1], "engine": "claude", "mtime": time.time() - 30 * 86400}
    out3 = server._rehydrate_archive_cached_rows([plain])
    assert len(journal_calls) == 3  # 1 rehydrate + 2 direct probes above
    assert "workflows" not in out3[0]


def test_live_engine_scan_skips_claude_spawn_polling(monkeypatch):
    """The non-Claude live-id scan must not poll unrelated Claude workers."""
    claude_spawn = {"engine": "claude", "session_id": "claude-session"}
    codex_spawn = {"engine": "codex", "session_id": "codex-session"}
    monkeypatch.setattr(server, "_spawned_sessions", [claude_spawn, codex_spawn])
    monkeypatch.setattr(server, "find_live_codex_processes", lambda: [])
    monkeypatch.setattr(server, "find_live_gemini_processes", lambda: [])
    monkeypatch.setattr(server, "find_live_cursor_processes", lambda: [])
    # _live_engine_session_ids scans ps via _raw_engine_process_commands, not
    # the find_live_* helpers above; stub it or real local engine processes leak in.
    monkeypatch.setattr(server, "_raw_engine_process_commands", lambda engine: [])
    monkeypatch.setattr(server, "_engine_live_sids_cache", {"ts": 0.0, "sids": frozenset()})
    polled = []

    def poll(entry):
        polled.append(entry)
        return None

    monkeypatch.setattr(server, "_poll_spawn_entry", poll)

    live = server._live_engine_session_ids()

    assert live == frozenset({"codex-session"})
    assert polled == [codex_spawn], (
        "_live_engine_session_ids polled Claude spawns even though they cannot "
        "contribute a non-Claude live session id"
    )


def _write_token_quality_index(root, records):
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "quality-index.json"
    index_path.write_text(json.dumps({"version": 1, "records": records}), encoding="utf-8")
    return index_path


def test_token_quality_index_refresh_and_lookup_never_scans_cache_directories(monkeypatch, tmp_path):
    sid = "11111111-2222-4333-8444-999999999999"
    _write_token_quality_index(tmp_path / ".claude" / "token-optimizer", {
        sid: {
            "score": 95,
            "grade": "A",
            "summary": "compact result",
            "timestamp": "now",
            "source_mtime": 10.0,
            "transcript_mtime": 9.0,
        },
    })
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_RUNTIME_STATE", {})
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_INDEX", {})
    monkeypatch.setattr(
        server.Path,
        "iterdir",
        lambda _path: pytest.fail("quality index refresh scanned a TO directory"),
    )
    monkeypatch.setattr(
        server.Path,
        "glob",
        lambda _path, _pattern: pytest.fail("quality index refresh globbed TO files"),
    )

    assert server._refresh_token_optimizer_quality_index() is True

    monkeypatch.setattr(
        server.Path,
        "home",
        lambda: pytest.fail("request-time quality lookup resolved a TO path"),
    )
    assert server._token_optimizer_quality_for_session(sid) == {
        "quality_score": 95,
        "quality_grade": "A",
        "quality_timestamp": "now",
        "quality_summary": "compact result",
        "quality_source": "token-optimizer-index",
    }


def test_token_quality_index_malformed_update_preserves_last_known_good_map(monkeypatch, tmp_path):
    sid = "11111111-2222-4333-8444-999999999999"
    index_path = _write_token_quality_index(tmp_path / ".codex" / "token-optimizer", {
        sid: {
            "score": 79.2,
            "grade": "B",
            "summary": "first result",
            "timestamp": "first",
            "source_mtime": 10.0,
            "transcript_mtime": 9.0,
        },
    })
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_RUNTIME_STATE", {})
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_INDEX", {})
    assert server._refresh_token_optimizer_quality_index() is True

    time.sleep(0.002)
    index_path.write_text("{not json", encoding="utf-8")

    assert server._refresh_token_optimizer_quality_index() is False
    assert server._token_optimizer_quality_for_session(sid)["quality_summary"] == "first result"


def test_token_quality_index_duplicate_session_uses_source_mtime_then_runtime(monkeypatch, tmp_path):
    sid = "11111111-2222-4333-8444-999999999999"
    record = {
        "score": 80,
        "grade": "A",
        "summary": "claude result",
        "timestamp": "now",
        "source_mtime": 10.0,
        "transcript_mtime": 9.0,
    }
    _write_token_quality_index(tmp_path / ".claude" / "token-optimizer", {sid: record})
    _write_token_quality_index(tmp_path / ".codex" / "token-optimizer", {
        sid: {**record, "score": 70, "grade": "B", "summary": "codex tie winner"},
    })
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_RUNTIME_STATE", {})
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_INDEX", {})

    assert server._refresh_token_optimizer_quality_index() is True
    assert server._token_optimizer_quality_for_session(sid)["quality_summary"] == "codex tie winner"


def test_token_quality_index_refresh_swaps_complete_map_for_concurrent_readers(monkeypatch, tmp_path):
    sid = "11111111-2222-4333-8444-999999999999"
    _write_token_quality_index(tmp_path / ".claude" / "token-optimizer", {
        sid: {
            "score": 90,
            "grade": "A",
            "summary": "new complete map",
            "timestamp": "new",
            "source_mtime": 20.0,
            "transcript_mtime": 19.0,
        },
    })
    old_value = {
        "quality_score": 70,
        "quality_grade": "B",
        "quality_timestamp": "old",
        "quality_summary": "old complete map",
        "quality_source": "token-optimizer-index",
    }
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_RUNTIME_STATE", {})
    monkeypatch.setattr(server, "_TOKEN_OPTIMIZER_QUALITY_INDEX", {sid: old_value})
    start = threading.Event()
    stop = threading.Event()
    seen = []

    def reader():
        start.wait(timeout=1)
        while not stop.is_set():
            seen.append(server._token_optimizer_quality_for_session(sid)["quality_summary"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(reader) for _ in range(8)]
        start.set()
        assert server._refresh_token_optimizer_quality_index() is True
        stop.set()
        for future in futures:
            future.result(timeout=1)

    assert set(seen) <= {"old complete map", "new complete map"}
    assert server._token_optimizer_quality_for_session(sid)["quality_summary"] == "new complete map"


def test_codex_sidebar_backfill_is_opt_in(monkeypatch):
    """A non-critical sidebar helper must not delay CCC startup by default."""
    monkeypatch.delenv("CCC_CODEX_SIDEBAR_BACKFILL", raising=False)
    assert server._codex_sidebar_backfill_enabled() is False

    monkeypatch.setenv("CCC_CODEX_SIDEBAR_BACKFILL", "1")
    assert server._codex_sidebar_backfill_enabled() is True


def test_auto_verify_reuses_supplied_session_rows(monkeypatch, tmp_path):
    """find_all_sessions must not trigger a second conversation scan."""
    monkeypatch.setattr(server, "resolve_repo_path", lambda path: str(tmp_path))
    monkeypatch.setattr(server, "_load_verified_conversations", lambda: [])
    monkeypatch.setattr(server, "_fetch_issue_states", lambda path: {})
    monkeypatch.setattr(
        server,
        "find_conversations",
        lambda *a, **k: pytest.fail("auto-verify rebuilt all conversations"),
    )

    result = server.auto_verify_closed_issues(str(tmp_path), conversations=[])

    assert result == {"ok": True, "newly_verified": [], "count": 0}


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
    wq = importlib.import_module("watchtower.queue")
    import watchtower.config as wt_config
    import watchtower.workers as wt_workers
    # queue._resolve_store_path() reads $WATCHTOWER_STORE fresh per call, but
    # config.CONFIG_FILE / workers.WORKERS_FILE / workers.WORKER_IDS_FILE are
    # bound once at import time -- enqueue()/claim_next() touch all three
    # (queue-config auto-registration, the spawn-registry liveness check), so
    # each must be isolated or this test would read/write the real production
    # files under ~/.watchtower.
    monkeypatch.setenv("WATCHTOWER_STORE", str(tmp_path / "queues.json"))
    monkeypatch.setattr(wt_config, "CONFIG_FILE", tmp_path / "queue-config.json")
    monkeypatch.setattr(wt_workers, "WORKERS_FILE", tmp_path / "workers.json")
    monkeypatch.setattr(wt_workers, "WORKER_IDS_FILE", tmp_path / "worker-ids.json")

    real_sid = str(uuid.uuid4())
    wq.enqueue(project="ZZ", note="first")
    wq.enqueue(project="ZZ", note="second")

    # (a) explicit session_uuid alongside a human label.
    claimed = wq.claim_next("codex-ccc-drain", project="ZZ", session_uuid=real_sid)
    assert claimed["claimed_by"] == "codex-ccc-drain", "label attribution lost"
    assert claimed["claimed_session_id"] == real_sid, "real session id not stored"

    # (b) label-only claim leaves the additive field empty (non-breaking).
    claimed2 = wq.claim_next("just-a-label", project="ZZ")
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


def test_archive_build_variants_share_one_global_singleflight_lock():
    """Different option keys must not parse the same corpus concurrently."""
    assert server._archive_build_lock("default") is server._archive_build_lock("with-prs")


def test_archive_serve_default_throttles_large_corpus_refreshes():
    """Live activity has its own overlay; full history needs at most 1/5min."""
    assert server._ARCHIVE_SERVE_TTL >= 300.0


def test_archive_spawned_cold_cache_does_not_block_request(monkeypatch):
    """A slow first spawned-session refresh must not hang ?all=1."""
    release = threading.Event()
    refreshed = threading.Event()

    def slow_list():
        release.wait(timeout=2)
        refreshed.set()
        return [{"spawn_id": "late"}]

    monkeypatch.setattr(server, "list_spawned_sessions", slow_list)
    monkeypatch.setattr(server, "_ARCHIVE_SERVE_TTL", 2.0)
    monkeypatch.setattr(server, "_archive_spawned_cache", {"ts": 0.0, "data": None})
    monkeypatch.setattr(server, "_archive_spawned_refreshing", False)
    timer = threading.Timer(0.5, release.set)
    timer.start()
    start = time.perf_counter()
    try:
        rows = server._archive_spawned_coalesced()
        elapsed = time.perf_counter() - start
        assert rows == [], "cold request should serve an empty stale snapshot"
        assert elapsed < 0.2, (
            f"cold spawned-session cache blocked for {elapsed:.3f}s instead of "
            "refreshing off-thread"
        )
    finally:
        release.set()
        timer.cancel()
        refreshed.wait(timeout=2)
        deadline = time.time() + 2
        while server._archive_spawned_refreshing and time.time() < deadline:
            time.sleep(0.01)
        assert server._archive_spawned_refreshing is False
        assert server._archive_spawned_cache["data"] == [{"spawn_id": "late"}]


def test_conversations_all_stale_ok_uses_coalesced_serve_cache(monkeypatch):
    """Dashboard archive polls must share the serve cache instead of each
    rehydrating the full archive response independently."""
    calls = []
    expected = [{"session_id": "sid-cached", "mtime": 1}]

    def serve_rows(key, options):
        calls.append((key, options))
        return expected, True, 0  # snap_ver 0: bypass the body cache in this test

    monkeypatch.setattr(server, "_archive_serve_rows_versioned", serve_rows)
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


def test_archive_mutation_restamps_warm_snapshot_and_refreshes_async(
    isolated_archive_cache, monkeypatch,
):
    """Archive/lane writes keep first paint current while refresh stays detached."""
    old_generation = server._archive_serve_generation
    cached_rows = [{
        "session_id": "sid-a", "archived": False, "trashed": False,
        "all_lane_override": "", "mtime": 1,
    }]
    assert server._archive_serve_cache_store(_ALL_KEY, cached_rows, old_generation)
    monkeypatch.setattr(
        server, "_load_conversation_lifecycle_sets", lambda: ({"sid-a"}, {"sid-a"}),
    )
    monkeypatch.setattr(server, "_load_session_lane_overrides", lambda: {"sid-a": "review"})

    server._restamp_archive_serve_cache_after_mutation()

    assert server._archive_serve_generation == old_generation + 1
    with server._archive_serve_lock:
        snapshot = server._archive_serve_cache[_ALL_KEY]
        assert snapshot["rows"] == [{
            "session_id": "sid-a", "archived": True, "trashed": True,
            "all_lane_override": "review", "mtime": 1,
        }]
        assert snapshot["ts"] == 0.0
    assert server._archive_serve_cache_store(
        _ALL_KEY, [{"session_id": "sid-a", "archived": False}], old_generation,
    ) is False

    spawned = []

    class FakeThread:
        def __init__(self, *, target, args=(), daemon=None, **_kwargs):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self):
            spawned.append(self)

    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    rows, from_cache, _ver = server._archive_serve_rows_versioned(_ALL_KEY, _ALL_OPTS)

    assert rows[0]["archived"] is True
    assert rows[0]["trashed"] is True
    assert rows[0]["all_lane_override"] == "review"
    assert from_cache is True
    assert len(spawned) == 1
    assert spawned[0].target is server._archive_serve_refresh


def test_archive_mutation_restamps_are_serialized_by_publish_order(
    isolated_archive_cache, monkeypatch,
):
    """A slower older mutation must not overwrite a newer restamped snapshot."""
    generation = server._archive_serve_generation
    assert server._archive_serve_cache_store(
        _ALL_KEY,
        [{"session_id": "sid-a", "archived": False, "trashed": False,
          "all_lane_override": "", "mtime": 1}],
        generation,
    )
    with server._archive_serve_lock:
        initial_version = server._archive_serve_cache[_ALL_KEY]["ver"]

    older_loaded = threading.Event()
    newer_loaded = threading.Event()
    release_older = threading.Event()

    def lifecycle_state():
        if threading.current_thread().name == "older-mutation":
            older_loaded.set()
            assert release_older.wait(timeout=2)
            return {"sid-a"}, {"sid-a"}
        newer_loaded.set()
        return set(), set()

    def lane_overrides():
        return {"sid-a": "review"} if threading.current_thread().name == "older-mutation" else {"sid-a": "queued"}

    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lifecycle_state)
    monkeypatch.setattr(server, "_load_session_lane_overrides", lane_overrides)
    older = threading.Thread(
        name="older-mutation", target=server._restamp_archive_serve_cache_after_mutation,
    )
    newer = threading.Thread(
        name="newer-mutation", target=server._restamp_archive_serve_cache_after_mutation,
    )
    older.start()
    assert older_loaded.wait(timeout=1)
    newer.start()

    # Before the fix, the newer mutation reads and publishes while the older
    # mutation waits, then the older pre-lock sidecar snapshot overwrites it.
    if newer_loaded.wait(timeout=1):
        deadline = time.time() + 1
        while time.time() < deadline:
            with server._archive_serve_lock:
                entry = server._archive_serve_cache[_ALL_KEY]
                if entry["ver"] > initial_version:
                    break
            time.sleep(0.01)

    release_older.set()
    older.join(timeout=2)
    newer.join(timeout=2)
    assert not older.is_alive() and not newer.is_alive()
    with server._archive_serve_lock:
        row = server._archive_serve_cache[_ALL_KEY]["rows"][0]
    assert row["archived"] is False
    assert row["trashed"] is False
    assert row["all_lane_override"] == "queued"


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


def test_archive_response_cache_restores_signature_stat_map(
    isolated_archive_cache,
):
    """A detached worker/restart must retain enough state for delta refreshes."""
    old_sig = "persisted-old-stat-map"
    new_sig = "current-new-stat-map"
    old_files = {"/repo/a.jsonl": (1, 10)}
    old_extras = {"/engine/store": 4}
    new_files = {
        "/repo/a.jsonl": (2, 12),
        "/repo/b.jsonl": (1, 8),
    }

    with server._archive_sig_lock:
        server._ARCHIVE_STATMAP_BY_SIG[old_sig] = (old_files, old_extras)
    try:
        server._archive_response_cache_put(
            _ALL_KEY,
            [{"session_id": "cached"}],
            signature=old_sig,
        )
        server._save_archive_response_cache(force=True)

        server._ARCHIVE_RESPONSE_CACHE.clear()
        server._ARCHIVE_RESPONSE_CACHE_LOADED = False
        with server._archive_sig_lock:
            server._ARCHIVE_STATMAP_BY_SIG.pop(old_sig, None)

        server._load_archive_response_cache()

        with server._archive_sig_lock:
            restored = server._ARCHIVE_STATMAP_BY_SIG.get(old_sig)
            server._ARCHIVE_STATMAP_BY_SIG[new_sig] = (new_files, old_extras)
        assert restored == (old_files, old_extras)
        changed, removed, engines = server._archive_signature_delta(old_sig, new_sig)
        assert changed == ["/repo/a.jsonl", "/repo/b.jsonl"]
        assert removed == []
        assert engines == set()
    finally:
        with server._archive_sig_lock:
            server._ARCHIVE_STATMAP_BY_SIG.pop(old_sig, None)
            server._ARCHIVE_STATMAP_BY_SIG.pop(new_sig, None)


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


def test_archive_warm_force_refresh_never_computes_on_request_thread(
    isolated_archive_cache, monkeypatch,
):
    """A warm freshness request must stale-serve and refresh off-thread.

    Real archive rehydrates repeatedly exceeded the one-second prediction
    budget (up to 32s), monopolizing the GIL and delaying unrelated session
    opens. Structural "cheapness" must never put archive work back onto an
    HTTP request thread once a serve snapshot exists.
    """
    cached_rows = [{"session_id": "cached", "mtime": 1}]
    assert server._archive_serve_cache_store(
        _ALL_KEY, cached_rows, server._archive_serve_generation,
    )
    with server._archive_serve_lock:
        server._archive_serve_cache[_ALL_KEY]["ts"] = 0.0

    spawned = []

    class FakeThread:
        def __init__(self, *, target, args=(), daemon=None, **_kwargs):
            self.target = target
            self.args = args
            spawned.append(self)

        def start(self):
            pass

    compute_calls = []

    def compute(*args, **kwargs):
        compute_calls.append((args, kwargs))
        return [{"session_id": "fresh"}], True

    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    monkeypatch.setattr(server, "_archive_compute_rows", compute)

    rows, from_cache, _ver = server._archive_serve_rows_versioned(
        _ALL_KEY,
        _ALL_OPTS,
        force_refresh=True,
    )

    assert rows == cached_rows
    assert from_cache is True
    assert compute_calls == [], "archive refresh ran synchronously on the request thread"
    assert len(spawned) == 1, "warm freshness request did not schedule one refresh"
    assert spawned[0].target is server._archive_serve_refresh


def test_archive_background_refresh_uses_detached_process(
    isolated_archive_cache, monkeypatch,
):
    """Archive parsing must not share the dashboard server's Python GIL."""
    detached_calls = []

    def detached(key, options, generation):
        detached_calls.append((key, options, generation))
        return [], True

    monkeypatch.setattr(
        server, "_archive_compute_rows_detached", detached, raising=False,
    )
    monkeypatch.setattr(
        server,
        "_archive_compute_rows",
        lambda *_args, **_kwargs: pytest.fail(
            "background archive refresh ran inside the dashboard process"
        ),
    )

    generation = server._archive_serve_generation
    server._archive_serve_refresh(_ALL_KEY, _ALL_OPTS, generation, time.time())

    assert detached_calls == [(_ALL_KEY, _ALL_OPTS, generation)]


def test_cold_persisted_snapshot_does_not_eagerly_rebuild(isolated_archive_cache, monkeypatch):
    """Opening CCC trusts its persisted rows; explicit freshness can rebuild."""
    entry = {
        "cached_at": time.time(),
        "conversations": [{"session_id": "persisted", "mtime": 1}],
        "signature": "old-sig",
    }
    spawned = []

    class FakeThread:
        def __init__(self, *, target, args=(), daemon=None, **_kwargs):
            self.target = target
            self.args = args
            spawned.append(self)

        def start(self):
            pass

    monkeypatch.setattr(server, "_archive_response_cache_get", lambda _key: entry)
    monkeypatch.setattr(server, "_rehydrate_archive_cached_rows", lambda rows: [dict(r) for r in rows])
    monkeypatch.setattr(server.threading, "Thread", FakeThread)

    rows, from_cache, _ver = server._archive_serve_rows_versioned(
        _ALL_KEY,
        _ALL_OPTS,
        force_refresh=False,
    )
    assert rows[0]["session_id"] == "persisted"
    assert from_cache is True
    assert spawned == [], "cold startup rebuilt despite having a persisted snapshot"

    server._archive_serve_cache.clear()
    server._archive_serve_refreshing.clear()
    server._archive_serve_rows_versioned(
        _ALL_KEY,
        _ALL_OPTS,
        force_refresh=True,
    )
    assert len(spawned) == 1, "explicit freshness did not schedule a refresh"


def test_cold_persisted_snapshot_does_not_rehydrate_on_request(
    isolated_archive_cache, monkeypatch,
):
    """First paint after a restart must not process every archived row."""
    entry = {
        "cached_at": time.time(),
        "conversations": [{"session_id": "persisted", "state": "ended"}],
        "signature": "persisted-sig",
    }
    monkeypatch.setattr(server, "_archive_response_cache_get", lambda _key: entry)
    monkeypatch.setattr(
        server,
        "_rehydrate_archive_cached_rows",
        lambda _rows: pytest.fail("cold request synchronously rehydrated the archive"),
    )
    # The cold-serve path re-stamps archived/trashed from lifecycle sidecars
    # (the trash-resurrect fix). Pin empty sets so the overlay is deterministic.
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: (set(), set()))

    rows, from_cache, _ver = server._archive_serve_rows_versioned(
        _ALL_KEY,
        _ALL_OPTS,
        force_refresh=False,
    )

    assert rows == [
        {"session_id": "persisted", "state": "ended", "archived": False, "trashed": False}
    ]
    assert from_cache is True


def test_cold_archive_variant_borrows_base_snapshot_and_refreshes_detached(
    isolated_archive_cache, monkeypatch,
):
    """A missing enriched cache key must not full-build on an HTTP thread."""
    variant_options = dict(_ALL_OPTS, include_prs=True)
    variant_key = server._archive_response_cache_key(**variant_options)
    base_entry = {
        "cached_at": time.time(),
        "conversations": [{"session_id": "base-snapshot"}],
        "signature": "base-sig",
    }
    spawned = []

    class FakeThread:
        def __init__(self, *, target, args=(), daemon=None, **_kwargs):
            self.target = target
            self.args = args
            spawned.append(self)

        def start(self):
            pass

    monkeypatch.setattr(
        server,
        "_archive_response_cache_get",
        lambda key: base_entry if key == _ALL_KEY else None,
    )
    monkeypatch.setattr(server.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        server,
        "_archive_compute_rows",
        lambda *_args, **_kwargs: pytest.fail(
            "missing archive variant full-built on the request thread"
        ),
    )
    # The cold-serve path re-stamps archived/trashed from lifecycle sidecars
    # (the trash-resurrect fix). Pin empty sets so the overlay is deterministic.
    monkeypatch.setattr(server, "_load_conversation_lifecycle_sets", lambda: (set(), set()))

    rows, from_cache, _ver = server._archive_serve_rows_versioned(
        variant_key,
        variant_options,
    )

    assert rows == [
        {"session_id": "base-snapshot", "archived": False, "trashed": False}
    ]
    assert from_cache is True
    assert len(spawned) == 1
    assert spawned[0].target is server._archive_serve_refresh
    assert spawned[0].args[:2] == (variant_key, variant_options)


def test_archive_signature_delta_engine_churn_is_engine_scoped_refresh():
    """Dir-granular engine churn must NOT force a full O(all-rows) rebuild.

    Hermes state.db mtime flips per internal event (the measured 60-95s-per-poll
    burn while hermes ran). Codex has the same shape: a newly spawned thread
    only ever appears as a rollout day-dir mtime bump, and routing that to the
    full-rebuild path meant a new session could not show up until a background
    rebuild finished and a later poll picked it up (measured 49.6s from spawn to
    the row existing in a built list). Both convert to an engine-scoped refresh.

    An engine WITHOUT an isolated rebuild path must still force the full
    rebuild — that is the part of this gate that must not erode.
    """
    hermes_key = server._ARCHIVE_HERMES_EXTRA_KEY
    codex_key = str(Path.home() / ".codex" / "sessions")
    other_key = str(Path.home() / ".cursor" / "projects")
    files = {"/p/a.jsonl": (1, 100)}
    old_extras = {hermes_key: 100, codex_key: 200, other_key: 300}
    with server._archive_sig_lock:
        server._ARCHIVE_STATMAP_BY_SIG["sig_old"] = (dict(files), dict(old_extras))
        server._ARCHIVE_STATMAP_BY_SIG["sig_hermes"] = (
            dict(files), {hermes_key: 300, codex_key: 200, other_key: 300})
        server._ARCHIVE_STATMAP_BY_SIG["sig_codex"] = (
            dict(files), {hermes_key: 100, codex_key: 999, other_key: 300})
        server._ARCHIVE_STATMAP_BY_SIG["sig_other"] = (
            dict(files), {hermes_key: 100, codex_key: 200, other_key: 999})
        server._ARCHIVE_STATMAP_BY_SIG["sig_both"] = (
            {"/p/a.jsonl": (2, 100), "/p/b.jsonl": (1, 10)},
            {hermes_key: 300, codex_key: 200, other_key: 300})
    try:
        changed, removed, engines = server._archive_signature_delta("sig_old", "sig_hermes")
        assert changed == [] and removed == []
        assert engines == {"hermes"}
        # A new Codex thread is a day-dir bump, and rebuilds codex rows only.
        changed, removed, engines = server._archive_signature_delta("sig_old", "sig_codex")
        assert changed == [] and removed == []
        assert engines == {"codex"}
        # An engine source with no isolated path still forces the full rebuild.
        assert server._archive_signature_delta("sig_old", "sig_other") is None
        # Claude transcript churn + hermes churn ride the same delta.
        changed, removed, engines = server._archive_signature_delta("sig_old", "sig_both")
        assert changed == ["/p/a.jsonl", "/p/b.jsonl"] and removed == []
        assert engines == {"hermes"}
    finally:
        with server._archive_sig_lock:
            for k in ("sig_old", "sig_hermes", "sig_codex", "sig_other", "sig_both"):
                server._ARCHIVE_STATMAP_BY_SIG.pop(k, None)


def test_archive_serve_snapshot_body_cache_replays_per_version(big_projects, isolated_archive_cache, monkeypatch):
    """The /api/conversations/all snapshot sender must serialize+etag+gzip ONCE
    per serve-snapshot version, not per poll (the ~8.5MB json.dumps churn on
    every parallel dashboard/COO poll)."""
    monkeypatch.setattr(server, "_ARCHIVE_SERVE_TTL", 60.0)
    _, _, ver = server._archive_serve_rows_versioned(_ALL_KEY, _ALL_OPTS)
    assert ver > 0, "cold build stores the snapshot with a version"
    # Same version across repeat polls within the TTL.
    _, _, ver2 = server._archive_serve_rows_versioned(_ALL_KEY, _ALL_OPTS)
    assert ver2 == ver
    # Simulate the sender's prebuilt body, then store a NEW snapshot: the new
    # entry must NOT inherit the old body (a body is only ever served against
    # the exact rows it was serialized from).
    with server._archive_serve_lock:
        server._archive_serve_cache[_ALL_KEY]["body_raw"] = b"{}"
    new_rows = [{"session_id": "s"}]
    gen = server._archive_serve_generation
    assert server._archive_serve_cache_store(_ALL_KEY, new_rows, gen) is True
    with server._archive_serve_lock:
        sc = server._archive_serve_cache[_ALL_KEY]
        assert "body_raw" not in sc
        assert sc["ver"] != ver
    # ver_for_rows is identity-based, never equality-based.
    assert server._archive_serve_ver_for_rows(_ALL_KEY, new_rows) == sc["ver"]
    assert server._archive_serve_ver_for_rows(_ALL_KEY, [{"session_id": "s"}]) == 0


def test_archive_snapshot_sender_replays_body_and_304s(isolated_archive_cache):
    """End-to-end on a fake handler: first serve serializes once and caches the
    body on the snapshot; an If-None-Match poll gets a 304 with no body; the
    same payload is replayed verbatim for repeat polls of that version."""
    import io

    key = "sender-test"
    rows = [{"session_id": "abc"}]
    assert server._archive_serve_cache_store(key, rows, server._archive_serve_generation)
    with server._archive_serve_lock:
        ver = server._archive_serve_cache[key]["ver"]

    class FakeHandler:
        _GZIP_MIN_BYTES = 1024

        def __init__(self, headers):
            self.headers = headers
            self.status = None
            self.out_headers = {}
            self.wfile = io.BytesIO()

        def send_response(self, status):
            self.status = status

        def send_header(self, k, v):
            self.out_headers[k] = v

        def end_headers(self):
            pass

    send = server.CommandCenterHandler._send_archive_snapshot_json
    data = {"conversations": rows, "count": 1, "cached": True}

    h1 = FakeHandler({"Accept-Encoding": "identity"})
    send(h1, key, data, ver)
    assert h1.status == 200
    assert h1.wfile.getvalue() == json.dumps(data).encode()
    etag = h1.out_headers["ETag"]
    with server._archive_serve_lock:
        assert server._archive_serve_cache[key].get("body_raw") is not None

    h2 = FakeHandler({"If-None-Match": etag})
    send(h2, key, data, ver)
    assert h2.status == 304
    assert h2.wfile.getvalue() == b""

    # Tamper with the data but keep the version: replay serves the CACHED body
    # (version == rows identity, guaranteed by the store/ver contract).
    h3 = FakeHandler({"Accept-Encoding": "identity"})
    send(h3, key, {"conversations": [{"session_id": "DIFFERENT"}], "count": 1}, ver)
    assert h3.wfile.getvalue() == json.dumps(data).encode()


def test_ux_fixes_health_payload_coalesces_concurrent_polls(monkeypatch):
    """Queue health is a dashboard poll target; concurrent clients must share
    one expensive queue/log scan instead of each request rebuilding it."""
    assert hasattr(server, "build_ux_fixes_health_payload")

    server._ux_fixes_health_snapshot["ts"] = 0.0
    server._ux_fixes_health_snapshot["data"] = None
    monkeypatch.setattr(server, "_UX_FIXES_HEALTH_TTL", 60.0)

    calls = []
    lock = threading.Lock()

    def slow_health(*args, **kwargs):
        with lock:
            calls.append(time.time())
        time.sleep(0.05)
        return [{"project": "CCC", "depth": 1, "stuck": False}]

    monkeypatch.setattr(server, "compute_ux_fixes_health", slow_health)
    monkeypatch.setattr(server, "_wt_read_workers", lambda: [])
    monkeypatch.setattr(server, "compute_queues_health", lambda *args, **kwargs: [{"queue": "CCC"}])
    monkeypatch.setattr(server, "_wt_read_worker_session_ids", lambda: [])
    monkeypatch.setattr(server, "_wt_past_workers", lambda hours=24: [])
    monkeypatch.setattr(server._q, "list_items", lambda *args, **kwargs: [])

    barrier = threading.Barrier(4)

    def call_payload():
        barrier.wait()
        return server.build_ux_fixes_health_payload()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: call_payload(), range(4)))

    assert len(calls) == 1, "concurrent /api/ux-fixes/health polls rebuilt independently"
    assert all(r["count"] == 1 for r in results)
    assert all(r["queues"] == [{"queue": "CCC"}] for r in results)


def _stub_repo_ship_status_deps(monkeypatch, *, ttl=60.0):
    """Isolate repo_ship_status from disk/git so the only measurable work left
    is the `git status --porcelain` subprocess (the CCC-614 fan-out cost)."""
    server._repo_ship_status_cache.clear()
    server._repo_ship_status_build_locks.clear()
    monkeypatch.setattr(server, "_REPO_SHIP_STATUS_TTL", ttl)
    monkeypatch.setattr(server, "resolve_repo_path", lambda p: p)
    monkeypatch.setattr(server, "_load_repo_ship_state", lambda: {})
    monkeypatch.setattr(server, "_load_ship_job", lambda repo_path: None)
    monkeypatch.setattr(server, "_resolve_vercel_project", lambda repo_path=None: "")


def test_repo_ship_status_coalesces_concurrent_polls(monkeypatch):
    """The conv-list folder burst fires one /api/repo/ship/status per repo
    header at once; concurrent requests for the same repo must share ONE
    `git status` subprocess, not each spawn their own (CCC-614)."""
    _stub_repo_ship_status_deps(monkeypatch)

    calls = []
    lock = threading.Lock()

    def slow_git(args, cwd, timeout=10):
        with lock:
            calls.append((args, cwd))
        time.sleep(0.05)
        return 0, "", ""

    monkeypatch.setattr(server, "_git", slow_git)

    barrier = threading.Barrier(8)

    def call_status():
        barrier.wait(timeout=2)
        return server.repo_ship_status("/repo/a")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: call_status(), range(8)))

    assert len(calls) == 1, "concurrent ship/status polls each ran git status"
    assert all(r == results[0] for r in results)
    assert results[0]["dirty"] is False


def test_repo_ship_status_ttl_covers_render_burst(monkeypatch):
    """Every conversation-list re-render re-hydrates every folder header; within
    the TTL those repeat polls must reuse the snapshot instead of re-running
    `git status` per render."""
    _stub_repo_ship_status_deps(monkeypatch)

    calls = _count_calls(monkeypatch, "_git", (0, "x", ""))
    for _ in range(10):
        server.repo_ship_status("/repo/a")
    assert len(calls) == 1, "render burst within TTL re-ran git status"

    # A second repo builds its own snapshot once (per-repo keying).
    server.repo_ship_status("/repo/b")
    assert len(calls) == 2

    # Past the TTL the live dirty check refreshes.
    monkeypatch.setattr(server, "_REPO_SHIP_STATUS_TTL", 0.0)
    server.repo_ship_status("/repo/a")
    assert len(calls) == 3


def test_repo_ship_status_live_job_overrides_cached_disk_job(monkeypatch):
    """The in-memory job (live phase ticks for the 3s ship poll) must never be
    flattened by the TTL cache — only the disk fallback is cached."""
    _stub_repo_ship_status_deps(monkeypatch)
    monkeypatch.setattr(server, "_load_ship_job", lambda repo_path: {"running": False, "phase": "pushed"})
    monkeypatch.setattr(server, "_git", lambda *a, **k: (0, "", ""))

    live_job = {"running": True, "phase": "pushing"}
    with server._ship_jobs_lock:
        server._ship_jobs["/repo/a"] = dict(live_job)
    try:
        first = server.repo_ship_status("/repo/a")
        second = server.repo_ship_status("/repo/a")  # served from the TTL cache
    finally:
        with server._ship_jobs_lock:
            server._ship_jobs.pop("/repo/a", None)

    assert first["job"] == live_job
    assert second["job"] == live_job, "cached poll must still see the live in-memory job"

    # With no in-memory job the cached disk copy is the fallback.
    third = server.repo_ship_status("/repo/a")
    assert third["job"] == {"running": False, "phase": "pushed"}


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
    # The refresh thread also calls _weekly_usage_block() for real. That is
    # several seconds of filesystem work on a cold cache — longer than the
    # wait below — and it has nothing to do with the metadata this test
    # asserts. Left unpatched, the test passes or fails on whether some other
    # process happened to warm the cache first.
    monkeypatch.setattr(server, "_weekly_usage_block", lambda *a, **k: {})
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


def test_shorten_display_name_warning_free_on_python_314():
    """A hot group-chat label path must not flood stderr with deprecations."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        value = server._shorten_display_name(
            "alpha beta gamma delta: trailing detail that is definitely long"
        )

    assert value == "alpha beta gamma delta"


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


def test_archive_signature_dedupes_symlinked_project_dirs(big_projects, tmp_path):
    """A symlink to an existing Claude project must not double-stat its corpus."""
    n, _ = big_projects
    projects = tmp_path / ".claude" / "projects"
    target = projects / "-tmp-perf-repo"
    alias = projects / "-tmp-perf-repo-alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    _sig, files, _extras = server._archive_corpus_signature_parts()

    assert len(files) == n


# ── /api/system/services (SERVER STATUS chip + System status panel) ──────────
# The chip polls this every 20s and the open panel every 5s. It aggregates four
# services, so the temptation is to fan out to whatever helper is closest —
# build_system_health() (a ps + lsof sweep), _ccc_last_updated_iso() (git log),
# a per-pid `ps`. All of those turn a status readout into a fork per poll.

def test_system_services_no_subprocess_on_warm_cache(monkeypatch):
    """A second call inside the 3s TTL must not fork anything."""
    server._system_services_cache = {"ts": 0.0, "payload": None}

    # Keep the cold build itself deterministic and fork-free.
    monkeypatch.setattr(server, "_control_plane_request", lambda *a, **k: {
        "ok": True, "available": True, "active": 0, "queued": 0, "uncertain": 0,
        "drain": {"enabled": False},
        "worker": {"pid": 42, "started_at": 1.0, "server_version": server.__version__,
                   "capabilities": ["engine-execution-v1"]},
    })
    monkeypatch.setattr(server, "_watchtower_service_status", lambda **k: {
        "ok": True, "installed": False, "running": False, "pid": None,
        "command_verified": False, "pid_reused": False, "api_ok": False,
        "state": "stopped", "host": "127.0.0.1", "port": 8787,
        "url": "http://127.0.0.1:8787", "started_at": None,
        "started_at_approx": False, "uptime_s": None,
    })
    server.build_system_services(force=True)

    import subprocess as _subprocess

    def boom(*a, **k):  # pragma: no cover - only runs on regression
        raise AssertionError(f"/api/system/services forked a subprocess: {a!r}")

    monkeypatch.setattr(_subprocess, "run", boom)
    monkeypatch.setattr(_subprocess, "Popen", boom)
    monkeypatch.setattr(_subprocess, "check_output", boom)

    health_calls = _count_calls(monkeypatch, "build_system_health", passthrough_return={})
    sessions_calls = _count_calls(monkeypatch, "find_all_conversations", passthrough_return=[])

    payload = server.build_system_services()

    assert payload["ok"] is True
    assert {row["id"] for row in payload["services"]} == {
        "dashboard", "worker", "watchtower", "app_server"
    }
    assert health_calls == [], (
        "build_system_services called build_system_health — that runs a ps+lsof "
        "sweep and turns a 5s poll into a machine-wide scan"
    )
    assert sessions_calls == [], (
        "build_system_services triggered the O(all sessions) conversation build"
    )


def test_system_services_does_not_call_ccc_last_updated_iso(monkeypatch):
    """_ccc_last_updated_iso() shells out to `git log -1`. Never on a poll."""
    server._system_services_cache = {"ts": 0.0, "payload": None}
    monkeypatch.setattr(server, "_control_plane_request", lambda *a, **k: {
        "ok": False, "available": False,
    })
    monkeypatch.setattr(server, "_watchtower_service_status", lambda **k: {
        "ok": True, "installed": False, "running": False, "state": "stopped",
        "port": 8787, "url": "http://127.0.0.1:8787", "started_at": None,
        "started_at_approx": False, "uptime_s": None,
    })

    git_calls = _count_calls(monkeypatch, "_ccc_last_updated_iso", passthrough_return="")

    server.build_system_services(force=True)

    assert git_calls == [], (
        "build_system_services called _ccc_last_updated_iso — one `git log` "
        "fork per poll, 3x/min from the chip alone"
    )


def test_watchtower_process_argv_is_memoised_per_pid(monkeypatch):
    """A WatchTower restart used to cost ~130 `ps -p` forks. Memoise by pid."""
    server._watchtower_forget_process_argv()
    calls = []

    def fake_uncached(pid):
        calls.append(pid)
        return ["/usr/local/bin/wt", "start"]

    monkeypatch.setattr(server, "_watchtower_process_argv_uncached", fake_uncached)

    for _ in range(50):
        assert server._watchtower_process_argv(4242) == ["/usr/local/bin/wt", "start"]

    assert calls == [4242], (
        f"_watchtower_process_argv probed {len(calls)}x for one live pid — "
        "the per-pid memo is gone and every status poll forks `ps` again"
    )

    # A different pid is a different process: it must be probed, and the stale
    # entry must not survive (pid reuse is the only way this cache can lie).
    server._watchtower_process_argv(4343)
    assert calls == [4242, 4343]
    assert 4242 not in server._WT_ARGV_CACHE

    server._watchtower_forget_process_argv()


def test_watchtower_api_probe_is_cached_and_never_blocks_twice(monkeypatch):
    """A degraded WatchTower must not cost 0.8s of an HTTP thread per poll.

    `_watchtower_api_probe` is a urlopen with a bounded timeout. When the
    daemon is up but its API is not answering, that timeout is paid in full — and the
    System status panel polls on a 5s cadence against a 3s payload TTL, so
    nearly every poll paid it, in exactly the degraded state the panel exists
    to report. It also stretched the window where a settled restart still
    looked unsettled. Cache the verdict; refresh it off the request thread.
    """
    server._watchtower_forget_api_probe()
    calls = []

    def fake_probe(base_url):
        calls.append(base_url)
        return False  # the degraded shape: daemon alive, API silent

    monkeypatch.setattr(server, "_watchtower_api_probe", fake_probe)
    # Keep the background refresh out of the assertion: this test is about the
    # request thread, and a real thread makes the count racy.
    monkeypatch.setattr(server.threading, "Thread", _NoopThread)

    url = "http://127.0.0.1:8787"
    for _ in range(40):
        assert server._watchtower_api_up(url) is False

    assert calls == [url], (
        f"_watchtower_api_up probed {len(calls)}x for 40 polls — the TTL cache "
        "is gone and every System status poll blocks an HTTP handler thread "
        "for up to 0.8s"
    )

    # The verdict must carry its own age so callers can be honest about it.
    age = server._watchtower_api_probe_age(url)
    assert age is not None and age >= 0

    # A restart invalidates it: the new daemon is a different process, so a
    # cached verdict about the old one is worse than no verdict.
    server._watchtower_forget_api_probe()
    assert server._watchtower_api_probe_age(url) is None
    assert server._watchtower_api_up(url) is False
    assert calls == [url, url]

    server._watchtower_forget_api_probe()


class _NoopThread:
    """threading.Thread stand-in that never actually runs the target."""

    def __init__(self, *args, **kwargs):
        self._name = kwargs.get("name", "noop")

    def start(self):
        return None

    def join(self, timeout=None):
        return None


def test_system_services_does_not_probe_watchtower_api_synchronously(monkeypatch):
    """Warm-cache /api/system/services must not touch the network at all."""
    server._system_services_cache = {"ts": 0.0, "payload": None}
    server._watchtower_forget_api_probe()
    monkeypatch.setattr(server, "_control_plane_request", lambda *a, **k: {
        "ok": True, "available": True, "active": 0, "queued": 0, "uncertain": 0,
        "drain": {"enabled": False},
        "worker": {"pid": 42, "started_at": 1.0, "server_version": server.__version__,
                   "capabilities": ["engine-execution-v1"]},
    })
    probes = []
    monkeypatch.setattr(server, "_watchtower_api_probe", lambda url: probes.append(url) or False)
    monkeypatch.setattr(server.threading, "Thread", _NoopThread)

    server.build_system_services(force=True)
    cold = len(probes)
    for _ in range(10):
        server.build_system_services(force=True)

    assert len(probes) == cold, (
        f"the WatchTower API was probed {len(probes) - cold} extra times across "
        "10 rebuilds — the probe cache is not covering the services payload"
    )

    server._watchtower_forget_api_probe()
    server._system_services_cache = {"ts": 0.0, "payload": None}
