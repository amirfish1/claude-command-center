"""One liveness snapshot per delivery attempt (slice 4 of the delivery-owner work).

The terminal-queue watcher and the inject router used to probe
``session_live_status``, the live spawn entry and the tool-child ``ps`` scan
independently, a beat apart; a flicker between the two reads re-parked a
message the watcher had just cleared (CCC-799). These tests pin that a
``liveness.Probe`` memoises each read, that the router honours a probe it is
handed instead of re-probing, that the watcher hands its own probe down, and
that the resume-style engines share one transport table.
"""
import importlib
import inspect
import time

import pytest

server = importlib.import_module("server")
liveness = importlib.import_module("ccc_server.liveness")
delivery = importlib.import_module("ccc_server.delivery")
pending_inputs = importlib.import_module("ccc_server.pending_inputs")

SID = "sess-probe-0001"


@pytest.fixture
def counted(monkeypatch):
    """Patch every liveness read on `server` with a counting fake."""
    calls = {"status": 0, "cwd": 0, "spawn": 0, "child": 0, "turn": 0}
    spawn = {"pid": 4242, "engine": "claude", "log": None}

    def _status(sid, cwd):
        calls["status"] += 1
        return {"live": True, "tty": None, "status": "running", "pid": 4242}

    def _cwd(sid):
        calls["cwd"] += 1
        return "/tmp/probe-cwd"

    def _spawn(sid):
        calls["spawn"] += 1
        return spawn

    def _child(entry):
        calls["child"] += 1
        return {"pid": 4343, "started_at": time.time(), "command": "sleep 5"}

    def _turn(entry):
        calls["turn"] += 1
        return True

    monkeypatch.setattr(server, "session_live_status", _status)
    monkeypatch.setattr(server, "find_session_cwd", _cwd)
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", _spawn)
    monkeypatch.setattr(server, "_spawn_entry_active_tool_child", _child)
    monkeypatch.setattr(server, "_headless_turn_in_progress", _turn)
    return calls, spawn


# ── Probe ────────────────────────────────────────────────────────────────────

def test_probe_reads_each_fact_once(counted):
    calls, spawn = counted
    probe = liveness.Probe(SID)
    assert calls == {"status": 0, "cwd": 0, "spawn": 0, "child": 0, "turn": 0}, "lazy"
    for _ in range(3):
        assert probe.status["live"] is True
        assert probe.busy is True
        assert probe.has_tty is False
        assert probe.spawn() is spawn
        assert probe.tool_child()["pid"] == 4343
        assert probe.headless_turn() is True
    assert calls == {"status": 1, "cwd": 1, "spawn": 1, "child": 1, "turn": 1}


def test_probe_accepts_a_known_status_and_cwd(counted):
    calls, _spawn = counted
    probe = liveness.Probe(SID, cwd="/elsewhere", status={"live": False, "tty": None})
    assert probe.live is False and probe.cwd == "/elsewhere"
    assert calls["status"] == 0 and calls["cwd"] == 0


def test_probe_without_spawn_has_no_child_and_no_turn(counted, monkeypatch):
    calls, _spawn = counted
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    probe = liveness.Probe(SID)
    assert probe.tool_child() is None and probe.headless_turn() is False
    assert calls["child"] == 0 and calls["turn"] == 0


def test_ensure_keeps_a_supplied_probe():
    probe = liveness.Probe(SID, status={"live": False})
    assert liveness.ensure(probe, SID) is probe
    fresh = liveness.ensure(None, SID)
    assert isinstance(fresh, liveness.Probe) and fresh.session_id == SID


# ── Watcher gates reuse the probe ────────────────────────────────────────────

def test_tool_child_gate_reads_through_the_probe(counted):
    calls, spawn = counted
    probe = liveness.Probe(SID)
    assert server._tool_child_blocks_inject(spawn, require_open_turn=True, probe=probe) is True
    assert server._tool_child_blocks_inject(spawn, require_open_turn=True, probe=probe) is True
    assert calls["child"] == 1 and calls["turn"] == 1, "the gate must not fork a second ps"
    # Without a probe the gate still works the old way.
    assert server._tool_child_blocks_inject(spawn, require_open_turn=True) is True
    assert calls["child"] == 2


def test_headless_turn_hold_reads_through_the_probe(counted):
    calls, _spawn = counted
    probe = liveness.Probe(SID)
    status = {"live": True, "tty": None}
    assert server._terminal_queue_waits_for_headless_turn(SID, status, probe=probe) is True
    assert server._terminal_queue_waits_for_headless_turn(SID, status, probe=probe) is True
    assert calls["spawn"] == 1 and calls["turn"] == 1
    assert server._terminal_queue_waits_for_headless_turn(SID, status) is True
    assert calls["spawn"] == 2


# ── Router honours a handed-in probe ─────────────────────────────────────────

def _quiet_router(monkeypatch):
    monkeypatch.setattr(server, "_federation_resolve_target", lambda sid: (sid, None))
    monkeypatch.setattr(server, "_claude_subagent_parent_session_id", lambda sid: None)
    monkeypatch.setattr(server, "_canonical_kimi_session_id", lambda sid: sid)
    for name in (
        "_is_codex_session", "_is_cursor_session", "_is_hermes_session",
        "_is_kimi_session", "_is_opencode_session", "_is_devin_cli_session",
        "_is_gemini_session", "_is_antigravity_session", "_is_aider_session",
    ):
        monkeypatch.setattr(server, name, lambda sid: False)
    monkeypatch.setattr(server, "_session_acp_harness", lambda sid: "")
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(server, "_try_uds_peer_delivery", lambda *a, **k: None)
    monkeypatch.setattr(server, "_ask_question_blocking_inject", lambda sid, status: False)
    monkeypatch.setattr(server, "_notification_blocks_inject", lambda *a, **k: False)
    monkeypatch.setattr(server, "_session_has_live_terminal", lambda *a, **k: False)
    monkeypatch.setattr(server, "_headless_spawn_is_stale", lambda *a, **k: False)
    monkeypatch.setattr(server, "_update_spawn_transcript_watermark", lambda *a, **k: None)
    monkeypatch.setattr(server, "_terminal_input_queue_has_pending", lambda sid: False)
    monkeypatch.setattr(server, "_log_activity", lambda *a, **k: None)


def test_router_delivers_through_the_watchers_probe_without_reprobing(counted, monkeypatch):
    calls, spawn = counted
    _quiet_router(monkeypatch)
    writes = []
    monkeypatch.setattr(
        server, "_write_stream_json_user_message",
        lambda entry, text: writes.append((entry["pid"], text)) or True,
    )
    probe = liveness.Probe(SID)
    probe.status  # the watcher's gate already probed
    probe.spawn()
    assert calls["status"] == 1 and calls["spawn"] == 1

    result = server._inject_text_into_session_router(
        SID, "hello", _from_terminal_queue=True, probe=probe,
    )

    assert result == {"ok": True, "pid": 4242, "via": "spawn-fifo"}
    assert writes == [(4242, "hello")]
    assert calls["status"] == 1, "router re-probed session_live_status"
    assert calls["spawn"] == 1, "router re-ran the spawn registry scan"


def test_router_builds_its_own_probe_when_none_is_given(counted, monkeypatch):
    calls, _spawn = counted
    _quiet_router(monkeypatch)
    monkeypatch.setattr(server, "_write_stream_json_user_message", lambda entry, text: True)

    result = server._inject_text_into_session_router(SID, "hello", _from_terminal_queue=True)

    assert result["ok"] is True and result["via"] == "spawn-fifo"
    assert calls["status"] == 1 and calls["spawn"] == 1


def test_router_queues_a_busy_headless_from_the_probe_snapshot(counted, monkeypatch):
    """force_queue consults the probe's busy/turn facts, not a fresh read."""
    calls, _spawn = counted
    _quiet_router(monkeypatch)
    queued = []
    monkeypatch.setattr(
        server, "_queue_terminal_input",
        lambda sid, text, status, **kw: queued.append((sid, text, dict(status))) or
        {"ok": True, "queued": True, "via": "terminal-queued"},
    )
    probe = liveness.Probe(SID)
    result = server._inject_text_into_session_router(SID, "later", force_queue=True, probe=probe)
    assert result["queued"] is True
    assert queued[0][2]["active_child_pid"] == 4343
    # busy status short-circuits before the headless-turn read; nothing probed twice
    assert (calls["status"], calls["spawn"], calls["child"]) == (1, 1, 1)


def test_watcher_hands_its_probe_to_the_router():
    source = inspect.getsource(server._start_resume_queue_watcher)
    probe_at = source.find("probe = _liveness.Probe(sid)")
    status_at = source.find("status = probe.status")
    inject_at = source.find("_core._inject_text_into_session(")
    assert probe_at != -1 and status_at != -1 and inject_at != -1
    assert probe_at < status_at < inject_at
    assert "probe=probe," in source[inject_at:inject_at + 400], (
        "the watcher must hand the probe it gated on to the router"
    )
    assert "require_open_turn=True, probe=probe" in source


# ── Resume transports: one table for router and watcher ──────────────────────

def test_resume_transport_table_keeps_router_order():
    assert [row[0] for row in delivery.RESUME_TRANSPORTS] == [
        "gemini", "cursor", "antigravity", "hermes", "opencode", "aider",
    ]


def test_resume_via_transport_calls_the_matching_resumer(monkeypatch):
    seen = []
    for _engine, predicate, resumer in delivery.RESUME_TRANSPORTS:
        monkeypatch.setattr(server, predicate, lambda sid: False)
        monkeypatch.setattr(
            server, resumer,
            lambda sid, text, _name=resumer, **kw: seen.append((_name, sid, text, kw))
            or {"ok": True, "via": _name},
        )
    assert delivery.resume_via_transport(SID, "x") is None, "no engine claimed it"

    monkeypatch.setattr(server, "_is_hermes_session", lambda sid: True)
    assert delivery.resume_via_transport(SID, "x") == {"ok": True, "via": "resume_session_hermes"}
    assert seen[-1] == ("resume_session_hermes", SID, "x", {})

    monkeypatch.setattr(server, "_is_antigravity_session", lambda sid: True)
    assert delivery.resume_via_transport(SID, "x", mode="steer")["via"] == "resume_session_antigravity"
    assert seen[-1][3] == {"mode": "steer"}, "antigravity is the one mode-aware resumer"


def test_router_and_watcher_both_use_the_table():
    router = inspect.getsource(server._inject_text_into_session_router)
    watcher = inspect.getsource(server._start_resume_queue_watcher)
    assert "_delivery.resume_via_transport(session_id, text, mode=mode)" in router
    assert "_delivery.resume_via_transport(sid, text)" in watcher
    for engine, _predicate, resumer in delivery.RESUME_TRANSPORTS:
        if engine != "cursor":
            # cursor keeps one earlier call: a busy LIVE TTY resumes instead
            # of typing keystrokes; that branch is about the TTY, not routing.
            assert f"{resumer}(" not in router, f"router still hand-dispatches {resumer}"
        assert f"{resumer}(" not in watcher, f"watcher still hand-dispatches {resumer}"


def test_every_resumer_in_the_table_exists():
    for _engine, predicate, resumer in delivery.RESUME_TRANSPORTS:
        assert callable(getattr(server, predicate))
        assert callable(getattr(server, resumer))
