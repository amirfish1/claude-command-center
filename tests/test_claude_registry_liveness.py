"""An idle-but-open Claude session stays live while its pid is alive.

Hooks stop firing when a session idles, so its sidecar ages past
_SIDECAR_LIVE_WINDOW. The pid-verified session registry is the proof that the
process is still running.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def _stale_sidecar(tmp_path, sid):
    marker = tmp_path / f"{sid}.json"
    marker.write_text("{}")
    old = time.time() - server._SIDECAR_LIVE_WINDOW - 600
    os.utime(marker, (old, old))


def _isolate(monkeypatch, tmp_path, registry):
    monkeypatch.setattr(server, "SIDECAR_STATE_DIR", tmp_path)
    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "claude")
    monkeypatch.setattr(server, "_live_engine_session_ids", lambda: frozenset())
    monkeypatch.setattr(server, "_load_session_registry", lambda: registry)


def test_registry_pid_keeps_idle_claude_session_live(monkeypatch, tmp_path):
    sid = "7d87eff0-c999-4192-b1e9-84a4e7e34cda"
    _stale_sidecar(tmp_path, sid)
    _isolate(monkeypatch, tmp_path, {sid: {"pid": 1, "sessionId": sid}})
    assert server._archive_session_is_live_uncached(sid)


def test_dead_claude_session_without_registry_pid_stays_not_live(monkeypatch, tmp_path):
    sid = "dead-session-id"
    _stale_sidecar(tmp_path, sid)
    _isolate(monkeypatch, tmp_path, {})
    assert not server._archive_session_is_live_uncached(sid)


def test_registry_does_not_make_other_engines_live(monkeypatch, tmp_path):
    sid = "codex-thread"
    _isolate(monkeypatch, tmp_path, {sid: {"pid": 1}})
    monkeypatch.setattr(server, "_detect_session_engine", lambda s: "codex")
    monkeypatch.setattr(server, "_codex_state_fields", lambda s: {"codex_state": None})
    assert not server._archive_session_is_live_uncached(sid)
