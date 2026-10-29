"""CCC-1248: the Claude Desktop visibility sweep must not rerun on every
server start (each CCC.app restart restarts server.py)."""

import time

import server
from ccc_server import codex


def _patch(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    calls = []

    def fake_backfill(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "updated": 0}

    monkeypatch.setattr(server, "backfill_claude_desktop_visibility", fake_backfill)
    return calls


def test_startup_backfill_runs_once_then_skips_within_interval(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path)
    codex._claude_desktop_visibility_backfill_once()
    codex._claude_desktop_visibility_backfill_once()
    codex._claude_desktop_visibility_backfill_once()
    assert len(calls) == 1
    assert (tmp_path / "claude-desktop-backfill.json").is_file()


def test_startup_backfill_runs_again_after_interval(monkeypatch, tmp_path):
    calls = _patch(monkeypatch, tmp_path)
    codex._claude_desktop_note_backfill_ran(now=time.time() - 13 * 3600)
    codex._claude_desktop_visibility_backfill_once()
    assert len(calls) == 1


def test_failed_backfill_does_not_arm_the_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)

    def boom(**kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(server, "backfill_claude_desktop_visibility", boom)
    codex._claude_desktop_visibility_backfill_once()
    assert codex._claude_desktop_startup_backfill_due()


def test_corrupt_or_future_marker_counts_as_due(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "COMMAND_CENTER_STATE_DIR", tmp_path)
    (tmp_path / "claude-desktop-backfill.json").write_text("not json")
    assert codex._claude_desktop_startup_backfill_due()
    codex._claude_desktop_note_backfill_ran(now=time.time() + 86400)
    assert codex._claude_desktop_startup_backfill_due()


def test_session_jsonl_path_cache_warming_and_lookup(monkeypatch, tmp_path):
    projects_dir = tmp_path / "projects"
    proj_a = projects_dir / "-Users-alice-repo1"
    proj_a.mkdir(parents=True)
    sid1 = "session-12345"
    f1 = proj_a / f"{sid1}.jsonl"
    f1.write_text("{}")

    from ccc_server import session_graph
    monkeypatch.setattr(session_graph._core, "PROJECTS_ROOT", projects_dir)
    session_graph._session_jsonl_path_cache.clear()
    session_graph._session_jsonl_negative_cache.clear()

    session_graph._warm_session_jsonl_path_cache(force=True)
    assert sid1 in session_graph._session_jsonl_path_cache
    assert session_graph._session_jsonl_path_cache[sid1] == f1

    # Look up via _claude_session_jsonl_path
    assert session_graph._claude_session_jsonl_path(sid1) == f1

    # Non-existent session hits negative cache
    assert session_graph._claude_session_jsonl_path("missing-sid") is None
    assert "missing-sid" in session_graph._session_jsonl_negative_cache

