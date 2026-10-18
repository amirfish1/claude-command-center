# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Unit coverage for ccc_server/continuation.py: `ccc spawn --continue-from`,
`ccc send --new-if-large-and-stale`, and the delivery-time lineage forward
every continuation entry point shares (MEMO-FIX-lineage).

No mocking of claude/gh — the engine spawn calls (spawn_session /
spawn_session_codex / spawn_session_kimi) are monkeypatched directly on the
real `server` module, exactly as tests/test_usage_limit_auto_resume.py
already does for the sibling usage-limit auto-resume path (continuation.py
reaches them via the _core proxy, which resolves against sys.modules
["server"] at call time — patching the proxy itself is not possible, it is
__slots__-only by design).
"""

import sqlite3

import pytest

import server
from ccc_server import continuation
from ccc_server import report_routes as rr
from ccc_server import ship_graph


@pytest.fixture(autouse=True)
def routes_store(tmp_path, monkeypatch):
    path = str(tmp_path / "report-routes.json")
    monkeypatch.setattr(rr, "_default_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def manual_forward_store(tmp_path, monkeypatch):
    path = str(tmp_path / "manual-forwards.json")
    monkeypatch.setattr(continuation, "_manual_forward_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def lineage_conn(monkeypatch):
    conn = sqlite3.connect(":memory:")
    ship_graph._init_db(conn)
    monkeypatch.setattr(continuation._sg, "_get_connection", lambda: conn)
    monkeypatch.setattr(continuation._sg, "_sync_all", lambda conn, force=False: None)
    yield conn
    conn.close()


def _insert_session_meta(conn, sid, start_ts=0.0, continuation_origin=""):
    conn.execute(
        "INSERT OR REPLACE INTO session_meta VALUES (?,?,?,?,?,?,?,?,?)",
        (sid, "repo", "/tmp/repo", start_ts, start_ts, "[]", "{}", "[]", continuation_origin),
    )
    conn.commit()


def _brief_dict(sid, latest=None, title="Fix the thing", cwd="/repo", engine="claude"):
    return {
        "query": sid, "session_id": sid, "alternates": [], "found": True,
        "title": title, "repo": "repo", "cwd": cwd, "engine": engine,
        "start_date": "", "end_date": "", "tickets": [], "last_user_asks": [],
        "last_assistant_reply": "", "files_touched": [], "commits": [],
        "artifacts_outside_repos": [], "resume_command": "", "indexing": False,
        "parent": "", "latest": latest or "", "continuation_ancestors": [],
    }


def _patch_context_sources(monkeypatch, briefs, row=None, path="/tmp/x.jsonl", report_to=""):
    """briefs: {query: brief_dict}. row: the fake archive row for every sid."""
    monkeypatch.setattr(continuation, "_brief", lambda q: briefs.get(q, {"found": False}))
    monkeypatch.setattr(continuation, "_transcript_path", lambda conn, sid: path)
    monkeypatch.setattr(
        continuation._usage_limit, "_usage_limit_row_for_session",
        lambda sid, engine=None: row or {},
    )
    monkeypatch.setattr(continuation._lineage, "report_to_of", lambda sid, path=None: report_to)


# -- session_context ---------------------------------------------------------

def test_session_context_not_found(monkeypatch):
    _patch_context_sources(monkeypatch, {})
    assert continuation.session_context("nope") is None


def test_capture_only_continuation_dry_run_uses_capture(monkeypatch, tmp_path):
    capture = tmp_path / "spawn-codex-example.log"
    capture.write_text('{"type":"turn.completed"}\n')
    monkeypatch.setattr(continuation, "_brief", lambda q: _brief_dict(q, engine="codex"))
    monkeypatch.setattr(continuation, "_transcript_path", lambda c, sid: str(capture))
    monkeypatch.setattr(continuation._usage_limit, "_usage_limit_row_for_session", lambda *a, **k: {})
    monkeypatch.setattr(continuation._usage_limit, "_usage_limit_context_tokens", lambda *a: 0)
    monkeypatch.setattr(server, "_codex_capture_thread_row", lambda sid: {"_ccc_capture": str(capture)})
    monkeypatch.setattr(server, "_codex_thread_row", lambda sid: None)
    monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda sid: None)
    result = continuation.spawn_continuation("capture-sid", prompt="finish it", dry_run=True)
    assert "CCC capture log" in result["prompt"]
    assert str(capture) in result["prompt"]
    assert "find ~/.codex/sessions" not in result["prompt"]


def test_session_context_basic_fields(monkeypatch):
    briefs = {"abc": _brief_dict("abc", title="Migrate the DB", cwd="/repo/x")}
    row = {"mtime": 1000.0, "model": "opus-5", "reasoning_effort": "high",
           "live_context_tokens": 42000}
    monkeypatch.setattr("time.time", lambda: 1000.0 + 500)
    _patch_context_sources(monkeypatch, briefs, row=row, report_to="dispatcher-1")
    ctx = continuation.session_context("abc")
    assert ctx["session_id"] == "abc"
    assert ctx["latest"] == "abc"
    assert ctx["title"] == "Migrate the DB"
    assert ctx["cwd"] == "/repo/x"
    assert ctx["engine"] == "claude"
    assert ctx["context_tokens"] == 42000
    assert ctx["model"] == "opus-5"
    assert ctx["effort"] == "high"
    assert ctx["idle_seconds"] == 500
    assert ctx["report_to"] == "dispatcher-1"


def test_session_context_rebriefs_on_latest_successor(monkeypatch):
    briefs = {
        "old-sid": _brief_dict("old-sid", latest="new-sid", title="Old title", cwd="/old"),
        "new-sid": _brief_dict("new-sid", title="New title", cwd="/new"),
    }
    _patch_context_sources(monkeypatch, briefs)
    ctx = continuation.session_context("old-sid")
    assert ctx["session_id"] == "old-sid"
    assert ctx["latest"] == "new-sid"
    assert ctx["title"] == "New title"
    assert ctx["cwd"] == "/new"


# -- build_continuation_prompt ------------------------------------------------

def test_build_continuation_prompt_includes_preamble_and_shared_block():
    ctx = {
        "latest": "sid-123", "title": "Fix flaky test", "engine": "claude",
        "context_tokens": 200_000, "transcript_path": "/path/to/sid-123.jsonl",
    }
    prompt = continuation.build_continuation_prompt("keep going", ctx)
    assert "You continue the work of session sid-123 (Fix flaky test)." in prompt
    assert "Previous owner transcript: /path/to/sid-123.jsonl" in prompt
    assert "Origin session id: sid-123" in prompt
    assert "ccc brief sid-123" in prompt
    assert "Task: keep going" in prompt


def test_build_continuation_prompt_defaults_task_when_no_user_prompt():
    ctx = {"latest": "sid", "title": "", "engine": "codex", "context_tokens": 0,
           "transcript_path": ""}
    prompt = continuation.build_continuation_prompt("", ctx)
    assert "Task: Continue the work from where it left off." in prompt


# -- decide_send_path ----------------------------------------------------------

def test_decide_send_path_large_and_stale_is_new(monkeypatch):
    briefs = {"sid": _brief_dict("sid")}
    monkeypatch.setattr("time.time", lambda: 10_000.0)
    row = {"mtime": 10_000.0 - 7200, "live_context_tokens": 200_000}
    _patch_context_sources(monkeypatch, briefs, row=row)
    decision = continuation.decide_send_path("sid", large_threshold=150_000, stale_seconds=3600)
    assert decision["path"] == "new"
    assert decision["context_tokens"] == 200_000
    assert decision["idle_seconds"] == 7200
    assert "150,000" in decision["reason"] or "150000" in decision["reason"]


def test_decide_send_path_large_but_fresh_is_normal(monkeypatch):
    briefs = {"sid": _brief_dict("sid")}
    monkeypatch.setattr("time.time", lambda: 10_000.0)
    row = {"mtime": 10_000.0 - 60, "live_context_tokens": 200_000}
    _patch_context_sources(monkeypatch, briefs, row=row)
    decision = continuation.decide_send_path("sid", large_threshold=150_000, stale_seconds=3600)
    assert decision["path"] == "normal"
    assert "idle" in decision["reason"]


def test_decide_send_path_small_but_stale_is_normal(monkeypatch):
    briefs = {"sid": _brief_dict("sid")}
    monkeypatch.setattr("time.time", lambda: 10_000.0)
    row = {"mtime": 10_000.0 - 7200, "live_context_tokens": 1000}
    _patch_context_sources(monkeypatch, briefs, row=row)
    decision = continuation.decide_send_path("sid", large_threshold=150_000, stale_seconds=3600)
    assert decision["path"] == "normal"
    assert "threshold" in decision["reason"]


def test_decide_send_path_unknown_session(monkeypatch):
    _patch_context_sources(monkeypatch, {})
    decision = continuation.decide_send_path("nope")
    assert decision["path"] == "normal"
    assert decision["resolved_session_id"] is None


# -- spawn_continuation --------------------------------------------------------

def test_spawn_continuation_dry_run_never_spawns(monkeypatch):
    briefs = {"sid": _brief_dict("sid")}
    row = {"mtime": 1.0, "model": "opus-5", "reasoning_effort": "high"}
    _patch_context_sources(monkeypatch, briefs, row=row, report_to="dispatcher-1")
    calls = []
    monkeypatch.setattr(server, "spawn_session", lambda *a, **k: calls.append((a, k)))
    result = continuation.spawn_continuation("sid", prompt="finish it", dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["continue_from"] == "sid"
    assert result["model"] == "opus-5"
    assert result["effort"] == "high"
    assert result["report_to"] == "dispatcher-1"
    assert "Task: finish it" in result["prompt"]
    assert calls == []


def test_spawn_continuation_claude_spawns_and_creates_report_route(monkeypatch):
    briefs = {"sid": _brief_dict("sid", title="Fix it")}
    row = {"mtime": 1.0, "model": "opus-5", "reasoning_effort": ""}
    _patch_context_sources(monkeypatch, briefs, row=row, report_to="dispatcher-1")
    spawned = {}

    def fake_spawn(prompt, **kwargs):
        spawned["prompt"] = prompt
        spawned["kwargs"] = kwargs
        return {"ok": True, "session_id": "new-sid-1"}

    monkeypatch.setattr(server, "spawn_session", fake_spawn)
    result = continuation.spawn_continuation("sid", prompt="keep going")
    assert result["ok"] is True
    assert result["new_session_id"] == "new-sid-1"
    assert result["engine"] == "claude"
    assert spawned["kwargs"]["parent_session_id"] == "sid"
    assert spawned["kwargs"]["model"] == "opus-5"
    assert '"session_id": "dispatcher-1"' not in spawned["prompt"]  # addressed via route id
    route_id = result["report_route"]
    assert rr.is_route_id(route_id)
    assert rr.resolve(route_id) == "dispatcher-1"
    assert [e["route_id"] for e in rr.list_routes(child_session_id="new-sid-1")] == [route_id]


def test_spawn_continuation_codex_uses_reasoning_effort_kwarg(monkeypatch):
    briefs = {"sid": _brief_dict("sid", engine="codex")}
    row = {"mtime": 1.0, "model": "gpt-5", "reasoning_effort": "medium"}
    _patch_context_sources(monkeypatch, briefs, row=row)
    spawned = {}

    def fake_spawn_codex(prompt, **kwargs):
        spawned["kwargs"] = kwargs
        return {"ok": True, "session_id": "codex-new-1"}

    monkeypatch.setattr(server, "spawn_session_codex", fake_spawn_codex)
    result = continuation.spawn_continuation("sid")
    assert result["ok"] is True
    assert result["engine"] == "codex"
    assert spawned["kwargs"]["reasoning_effort"] == "medium"


def test_spawn_continuation_unsupported_engine(monkeypatch):
    briefs = {"sid": _brief_dict("sid", engine="cursor")}
    _patch_context_sources(monkeypatch, briefs, row={"mtime": 1.0})
    result = continuation.spawn_continuation("sid")
    assert result["ok"] is False
    assert "cursor" in result["error"]
    assert "supported_engines" in result


def test_spawn_continuation_rebinds_old_chains_report_routes(monkeypatch):
    _insert_session_meta(continuation._sg._get_connection(), "old-sid", start_ts=1.0)
    briefs = {"old-sid": _brief_dict("old-sid")}
    _patch_context_sources(monkeypatch, briefs, row={"mtime": 1.0})
    child_route = rr.create("old-sid")
    rr.set_child(child_route, "child-1")

    monkeypatch.setattr(
        server, "spawn_session",
        lambda *a, **k: {"ok": True, "session_id": "new-sid"},
    )
    result = continuation.spawn_continuation("old-sid")
    assert result["ok"] is True
    assert child_route in result["rebound"]
    assert rr.resolve(child_route) == "new-sid"


def test_spawn_continuation_rebind_chain_false_skips_rebind(monkeypatch):
    briefs = {"sid": _brief_dict("sid")}
    _patch_context_sources(monkeypatch, briefs, row={"mtime": 1.0})
    route = rr.create("sid")
    monkeypatch.setattr(
        server, "spawn_session",
        lambda *a, **k: {"ok": True, "session_id": "new-sid"},
    )
    result = continuation.spawn_continuation("sid", rebind_chain=False)
    assert result["rebound"] == []
    assert rr.resolve(route) == "sid"


def test_spawn_continuation_not_found(monkeypatch):
    _patch_context_sources(monkeypatch, {})
    result = continuation.spawn_continuation("nope")
    assert result["ok"] is False
    assert "nope" in result["error"]


# -- rebind_chain_to -----------------------------------------------------------

def test_rebind_chain_to_moves_every_members_children(lineage_conn):
    _insert_session_meta(lineage_conn, "session-a", start_ts=1.0)
    _insert_session_meta(lineage_conn, "session-b", start_ts=2.0, continuation_origin="session-a")
    ra = rr.create("session-a")
    rb = rr.create("session-b")
    unrelated = rr.create("dispatcher-x")

    moved = continuation.rebind_chain_to("session-b", "session-c")

    assert sorted(moved) == sorted([ra, rb])
    assert rr.resolve(ra) == "session-c"
    assert rr.resolve(rb) == "session-c"
    assert rr.resolve(unrelated) == "dispatcher-x"


def test_rebind_chain_to_single_session_no_chain(lineage_conn):
    _insert_session_meta(lineage_conn, "lone", start_ts=1.0)
    r = rr.create("lone")
    moved = continuation.rebind_chain_to("lone", "new-sid")
    assert moved == [r]
    assert rr.resolve(r) == "new-sid"


# -- forward_target -------------------------------------------------------------

def test_forward_target_no_successor_is_unchanged(lineage_conn):
    _insert_session_meta(lineage_conn, "solo", start_ts=1.0)
    assert continuation.forward_target("solo") == "solo"


def test_forward_target_forwards_to_latest_successor(lineage_conn, monkeypatch):
    _insert_session_meta(lineage_conn, "old-sid", start_ts=1.0)
    _insert_session_meta(lineage_conn, "new-sid", start_ts=2.0, continuation_origin="old-sid")
    monkeypatch.setattr(
        server, "_sessions_state_snapshot",
        lambda: {"old-sid": {"state": "idle"}},
    )
    assert continuation.forward_target("old-sid") == "new-sid"


def test_forward_target_skips_while_origin_still_working(lineage_conn, monkeypatch):
    _insert_session_meta(lineage_conn, "old-sid", start_ts=1.0)
    _insert_session_meta(lineage_conn, "new-sid", start_ts=2.0, continuation_origin="old-sid")
    monkeypatch.setattr(
        server, "_sessions_state_snapshot",
        lambda: {"old-sid": {"state": "working"}},
    )
    assert continuation.forward_target("old-sid") == "old-sid"


def test_forward_target_follows_multi_hop_chain(lineage_conn, monkeypatch):
    _insert_session_meta(lineage_conn, "a", start_ts=1.0)
    _insert_session_meta(lineage_conn, "b", start_ts=2.0, continuation_origin="a")
    _insert_session_meta(lineage_conn, "c", start_ts=3.0, continuation_origin="b")
    monkeypatch.setattr(server, "_sessions_state_snapshot", lambda: {})
    assert continuation.forward_target("a") == "c"
    assert continuation.forward_target("b") == "c"


def test_forward_target_empty_sid_is_noop():
    assert continuation.forward_target("") == ""


def test_forward_target_exception_falls_back_to_sid(monkeypatch):
    def boom():
        raise RuntimeError("db unavailable")
    monkeypatch.setattr(continuation._sg, "_get_connection", boom)
    assert continuation.forward_target("any-sid") == "any-sid"


# -- manual forwards (MEMORY-5) ------------------------------------------------

def test_manual_forward_target_no_record_is_unchanged():
    assert continuation.manual_forward_target("solo") == "solo"


def test_manual_forward_target_follows_recorded_forward():
    continuation.record_manual_forward("old-sid", "new-sid")
    assert continuation.manual_forward_target("old-sid") == "new-sid"


def test_manual_forward_target_follows_multi_hop_chain():
    continuation.record_manual_forward("a", "b")
    continuation.record_manual_forward("b", "c")
    assert continuation.manual_forward_target("a") == "c"


def test_record_manual_forward_noop_for_empty_or_self():
    continuation.record_manual_forward("", "new-sid")
    continuation.record_manual_forward("old-sid", "")
    continuation.record_manual_forward("same", "same")
    assert continuation.manual_forward_target("old-sid") == "old-sid"
    assert continuation.manual_forward_target("same") == "same"


def test_manual_rebind_covers_every_chain_member(lineage_conn):
    _insert_session_meta(lineage_conn, "a", start_ts=1.0)
    _insert_session_meta(lineage_conn, "b", start_ts=2.0, continuation_origin="a")
    continuation.manual_rebind("a", "new-dispatcher")
    assert continuation.manual_forward_target("a") == "new-dispatcher"
    assert continuation.manual_forward_target("b") == "new-dispatcher"


def test_forward_target_honors_manual_forward(lineage_conn, monkeypatch):
    _insert_session_meta(lineage_conn, "old-sid", start_ts=1.0)
    monkeypatch.setattr(server, "_sessions_state_snapshot", lambda: {"old-sid": {"state": "idle"}})
    continuation.record_manual_forward("old-sid", "new-sid")
    assert continuation.forward_target("old-sid") == "new-sid"


def test_forward_target_composes_manual_forward_with_later_continuation(lineage_conn, monkeypatch):
    _insert_session_meta(lineage_conn, "old-sid", start_ts=1.0)
    _insert_session_meta(lineage_conn, "rebound-sid", start_ts=2.0)
    _insert_session_meta(lineage_conn, "continued-sid", start_ts=3.0, continuation_origin="rebound-sid")
    monkeypatch.setattr(server, "_sessions_state_snapshot", lambda: {"old-sid": {"state": "idle"}})
    continuation.record_manual_forward("old-sid", "rebound-sid")
    assert continuation.forward_target("old-sid") == "continued-sid"


def _dedupe_context(monkeypatch):
    monkeypatch.setattr(continuation, "session_context", lambda query: {
        "session_id": "source", "latest": "source", "title": "Example task",
        "cwd": "/repo", "engine": "claude", "context_tokens": 0,
        "idle_seconds": 0, "transcript_path": "", "model": None,
        "effort": None, "report_to": None,
    })
    monkeypatch.setattr(continuation, "rebind_chain_to", lambda *args: [])


def test_continuation_task_key_retry_returns_existing_and_tags_registry(monkeypatch):
    _dedupe_context(monkeypatch)
    rows = []
    calls = []
    monkeypatch.setattr(server, "_spawn_dedupe_lookup", lambda **kw: (
        next((r for r in rows if r["task_key"] == kw["task_key"]), None), None,
    ))

    def spawn(prompt, **kwargs):
        calls.append(kwargs)
        assert continuation._usage_limit._spawn_request_meta()["task_key"] == "example-task"
        return {"ok": True, "session_id": "child", "pid": 123}

    def tag(pid=None, session_id=None, meta=None):
        rows.append(dict(meta, session_id=session_id, pid=pid, cwd="/repo"))

    monkeypatch.setattr(server, "spawn_session", spawn)
    monkeypatch.setattr(server, "_tag_spawn_task_meta_in_registry", tag)
    first = continuation.spawn_continuation("source", prompt="finish", task_key="example-task", name="worker")
    retry = continuation.spawn_continuation("source", prompt="finish", task_key="example-task", name="worker")
    assert len(calls) == 1
    assert calls[0]["name"] == "worker"
    assert first["session_id"] == first["new_session_id"] == retry["new_session_id"] == "child"
    assert retry["existing"] is True
    assert continuation._usage_limit._spawn_request_meta() == {}


def test_continuation_concurrent_key_waits_for_first_spawn(monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    _dedupe_context(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    calls = []
    monkeypatch.setattr(server, "_spawn_dedupe_lookup", lambda **kw: (None, None))
    monkeypatch.setattr(server, "_tag_spawn_task_meta_in_registry", lambda **kw: None)

    def spawn(prompt, **kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(5)
        return {"ok": True, "session_id": "child"}

    monkeypatch.setattr(server, "spawn_session", spawn)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(continuation.spawn_continuation, "source", task_key="concurrent")
        assert entered.wait(5)
        # Wait until the second caller has reached the shared in-flight slot.
        original_wait = server._SPAWN_DEDUPE_INFLIGHT[("concurrent", "/repo")]["event"].wait
        waiting = threading.Event()
        def wait(timeout):
            waiting.set()
            return original_wait(timeout)
        monkeypatch.setattr(server._SPAWN_DEDUPE_INFLIGHT[("concurrent", "/repo")]["event"], "wait", wait)
        second = pool.submit(continuation.spawn_continuation, "source", task_key="concurrent")
        assert waiting.wait(5)
        release.set()
        assert first.result(timeout=5)["session_id"] == "child"
        result = second.result(timeout=5)
    assert len(calls) == 1
    assert result["existing"] is True
    assert result["new_session_id"] == "child"


def test_unkeyed_continuations_remain_independent(monkeypatch):
    _dedupe_context(monkeypatch)
    calls = []
    def spawn(prompt, **kwargs):
        calls.append(kwargs)
        return {"ok": True, "session_id": f"child-{len(calls)}"}
    monkeypatch.setattr(server, "spawn_session", spawn)
    assert continuation.spawn_continuation("source")["session_id"] == "child-1"
    assert continuation.spawn_continuation("source")["session_id"] == "child-2"
