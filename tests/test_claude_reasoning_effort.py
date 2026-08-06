"""Regression coverage for Claude's model-picker reasoning effort."""

from pathlib import Path

import server


def test_claude_accepts_max_reasoning_effort(monkeypatch):
    monkeypatch.setattr(server, "_detect_session_engine", lambda _sid: "claude")
    monkeypatch.setattr(server, "_set_session_override", lambda *args: None)
    monkeypatch.setattr(server, "session_live_status", lambda *_args: {})
    monkeypatch.setattr(server, "find_session_cwd", lambda _sid: "")

    result = server._set_session_model("sid", "opus-5", False, "max", effort_only=True)

    assert result["ok"] is True
    assert result["reasoning_effort"] == "max"


def test_claude_picker_uses_effort_only_requests_and_resume_flag():
    app_js = Path("static/app.js").read_text(encoding="utf-8")
    server_py = Path("server.py").read_text(encoding="utf-8")

    assert "CLAUDE_REASONING_LEVELS" in app_js
    assert "effort_only = true" in app_js
    assert 'f"/effort {reasoning_effort}"' in server_py
    assert 'cmd.extend(["--effort", effort])' in server_py


def test_effort_ladder_is_per_engine():
    assert "max" in server._engine_reasoning_efforts("claude")
    assert "max" not in server._engine_reasoning_efforts("codex")
    # An engine with no effort concept accepts nothing, not the Codex ladder.
    assert server._engine_reasoning_efforts("gemini") == set()

    assert server._validate_reasoning_effort("max", "claude") == "max"
    assert server._validate_reasoning_effort("max", "codex") == ""
    assert server._validate_reasoning_effort("max", "codex", strict=True) is None
    assert server._validate_reasoning_effort("high", "gemini") == ""


def test_spawn_defaults_hold_a_claude_max_effort(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SPAWN_DEFAULTS_FILE", tmp_path / "spawn-defaults.json")

    saved = server._save_spawn_defaults({"engine": "claude", "reasoning_effort": "max"})
    assert saved["ok"] is True
    assert server._load_spawn_defaults()["reasoning_effort"] == "max"

    rejected = server._save_spawn_defaults({"engine": "codex", "reasoning_effort": "max"})
    assert rejected["ok"] is False
    assert "codex" in rejected["error"]


def test_claude_spawn_request_resolves_effort_like_codex(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SPAWN_DEFAULTS_FILE", tmp_path / "spawn-defaults.json")
    server._save_spawn_defaults({"engine": "claude", "reasoning_effort": "xhigh"})

    assert server._spawn_request_reasoning_effort({}, "claude") == "xhigh"
    assert server._spawn_request_reasoning_effort({"effort": "max"}, "claude") == "max"
    # Codex has no "max"; the spawn falls back to the CLI default rather than
    # sending a level the engine would reject.
    assert server._spawn_request_reasoning_effort({"effort": "max"}, "codex") == ""


def test_cold_claude_spawn_command_carries_the_effort_flag():
    cmd = server._claude_spawn_command(
        "/fake/claude", "claude-opus-5", "demo", "sid-1", {}, effort="max",
    )
    assert cmd[cmd.index("--effort") + 1] == "max"

    plain = server._claude_spawn_command(
        "/fake/claude", "claude-opus-5", "demo", "sid-1", {},
    )
    assert "--effort" not in plain


def test_prewarm_with_a_different_effort_is_not_adopted(monkeypatch):
    class Proc:
        def poll(self):
            return None

    entry = {
        "prewarm_id": "warm-effort",
        "cwd": "/tmp/project",
        "model": "claude-opus-5",
        "reasoning_effort": "",
        "proc": Proc(),
    }
    monkeypatch.setattr(server, "_prune_claude_prewarms", lambda: None)
    monkeypatch.setattr(server, "_CLAUDE_PREWARMS", {"warm-effort": entry})

    # The reservation launched without --effort, so a max spawn must miss and
    # go cold instead of silently inheriting the default effort.
    assert server._take_claude_prewarm(
        "warm-effort", "/tmp/project", "claude-opus-5", effort="max",
    ) is None
    assert server._take_claude_prewarm(
        "warm-effort", "/tmp/project", "claude-opus-5",
    ) is entry


def test_stale_worker_kwarg_is_shed_instead_of_failing_the_spawn(monkeypatch):
    calls = []
    replies = [
        {"ok": False, "error": "spawn_session() got an unexpected keyword argument 'reasoning_effort'"},
        {"ok": True, "pid": 7},
    ]

    def routed(engine, operation, args, **kwargs):
        calls.append(args)
        return replies.pop(0)

    monkeypatch.setattr(server, "_control_plane_engine_call", routed)

    result = server.spawn_session(
        "go", name="skew", cwd="/tmp", repo_path="/tmp", reasoning_effort="max",
    )

    assert result["ok"] is True
    assert calls[0]["reasoning_effort"] == "max"
    assert "reasoning_effort" not in calls[1]
    assert "prewarm_fallback" not in result


def test_conv_row_effort_reads_hoisted_maps_without_touching_disk(monkeypatch):
    # List builders call this once per row, so a disk read here is the exact
    # O(all-sessions) regression tests/test_perf_budget.py exists to catch.
    def _boom():
        raise AssertionError("_load_session_overrides must be hoisted out of the row loop")

    monkeypatch.setattr(server, "_load_session_overrides", _boom)

    overrides = {"sid-a": {"reasoning_effort": "xhigh"}}
    assert server._conv_row_reasoning_effort("sid-a", overrides) == "xhigh"
    assert server._conv_row_reasoning_effort("sid-b", overrides) == ""
    # A spawn that has not been re-picked since still knows its effort.
    assert server._conv_row_reasoning_effort(
        "sid-b", overrides, {"reasoning_effort": "high"}
    ) == "high"
    # The picked value wins over the one the session was spawned with.
    assert server._conv_row_reasoning_effort(
        "sid-a", overrides, {"reasoning_effort": "low"}
    ) == "xhigh"


def test_archive_list_projection_carries_reasoning_effort():
    # The allowlist is what reaches the sidebar; a field missing here is
    # emitted by the builder and then silently dropped on the way out.
    assert "reasoning_effort" in server._ARCHIVE_LIST_FIELDS
    projected = server._archive_list_project_row(
        {"id": "s", "model": "opus-5", "reasoning_effort": "max", "secret": 1}
    )
    assert projected["reasoning_effort"] == "max"
    assert "secret" not in projected
    # Rows cached before this field existed must still project.
    assert "reasoning_effort" not in server._archive_list_project_row({"id": "s"})


def test_effort_ladder_is_published_next_to_the_models():
    catalog = server._build_engine_model_catalog()
    by_engine = catalog["efforts_by_engine"]
    assert by_engine["claude"] == ["low", "medium", "high", "xhigh", "max"]
    assert by_engine["codex"] == ["low", "medium", "high", "xhigh"]
    # No effort concept is an empty ladder, which means "hide the control".
    assert by_engine["cursor"] == []
    # Kept for back-compat alongside the new map.
    assert "kimi_thinking" in catalog

    options = server._queue_config_options()
    assert set(options["efforts_by_engine"]) == set(options["models_by_engine"])
    assert options["efforts_by_engine"]["codex"] == ["low", "medium", "high", "xhigh"]


def test_queue_config_effort_union_stays_flat():
    # Scoping this per engine would 400 an already-saved codex queue on its
    # next edit. See the audit's breaking-changes note.
    saved = server._queue_config_from_payload(
        {"queue": "DEMO", "engine": "codex", "effort": "max"}
    )
    assert saved["config"]["effort"] == "max"


def test_spawned_session_rows_carry_effort(monkeypatch):
    monkeypatch.setattr(server, "_control_plane_routes_engines", lambda: False)
    monkeypatch.setattr(server, "_poll_spawn_entry", lambda _s: None)
    monkeypatch.setattr(server, "_spawn_session_id_from_entry", lambda _s: "sid-1")
    monkeypatch.setattr(
        server, "_spawned_sessions",
        [{"pid": 1, "engine": "claude", "model": "opus-5", "reasoning_effort": "max"}],
    )

    row = server.list_spawned_sessions()[0]
    assert (row["engine"], row["model"], row["reasoning_effort"]) == (
        "claude", "opus-5", "max",
    )
