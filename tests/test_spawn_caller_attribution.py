"""A spawn started by an agent must be attributed to that agent's session.

`ccc spawn` ships its PID ancestry; the server maps it back to the enclosing
session. The interesting case is Codex: its thread id is minted at runtime, so
while the parent is still running its spawn-registry entry has no session_id
yet and only the spawn log knows the thread.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import server  # noqa: F401  (binds ccc_server._core)
from ccc_server import engines


def _write_codex_spawn_log(tmp_path, thread_id):
    log = tmp_path / "spawn-codex.log"
    log.write_text(
        "codex starting\n"
        + json.dumps({"type": "thread.started", "thread_id": thread_id})
        + "\n"
        + json.dumps({"type": "item.completed"})
        + "\n",
        encoding="utf-8",
    )
    return log


def test_thread_id_read_from_spawn_log_head(tmp_path):
    log = _write_codex_spawn_log(tmp_path, "01a09661-4897-7d91-989e-48672bc38c33")
    assert engines._codex_thread_id_from_spawn_log(str(log)) == (
        "01a09661-4897-7d91-989e-48672bc38c33"
    )


def test_thread_id_read_is_bounded_and_tolerant(tmp_path):
    log = tmp_path / "noise.log"
    log.write_text("not json\n" * 500, encoding="utf-8")
    assert engines._codex_thread_id_from_spawn_log(str(log), max_lines=10) == ""
    assert engines._codex_thread_id_from_spawn_log("") == ""
    assert engines._codex_thread_id_from_spawn_log(str(tmp_path / "missing")) == ""


def test_caller_resolves_to_codex_parent_whose_session_id_is_pending(
    tmp_path, monkeypatch
):
    """The registry entry has no session_id yet — the log already has it."""
    log = _write_codex_spawn_log(tmp_path, "01a0966f-dead-beef-cafe-000000000001")
    monkeypatch.setattr(engines._core, "_load_session_registry", lambda: {})
    monkeypatch.setattr(
        engines._core,
        "_load_spawn_registry",
        lambda: [{"pid": 4242, "engine": "codex", "session_id": "", "log": str(log)}],
    )
    monkeypatch.setattr(engines._core, "_scan_engine_processes", lambda: [])

    resolved = engines._resolve_spawn_caller_session_id([9999, 4242, 1234], str(tmp_path))
    assert resolved == "01a0966f-dead-beef-cafe-000000000001"


def test_known_session_id_still_wins_over_the_pending_log(tmp_path, monkeypatch):
    log = _write_codex_spawn_log(tmp_path, "from-log")
    monkeypatch.setattr(engines._core, "_load_session_registry", lambda: {})
    monkeypatch.setattr(
        engines._core,
        "_load_spawn_registry",
        lambda: [
            {"pid": 4242, "engine": "codex", "session_id": "from-registry", "log": str(log)}
        ],
    )
    monkeypatch.setattr(engines._core, "_scan_engine_processes", lambda: [])

    assert engines._resolve_spawn_caller_session_id([4242], str(tmp_path)) == (
        "from-registry"
    )


def test_no_ancestry_resolves_to_nothing(monkeypatch):
    monkeypatch.setattr(engines._core, "_load_session_registry", lambda: {})
    monkeypatch.setattr(engines._core, "_load_spawn_registry", lambda: [])
    monkeypatch.setattr(engines._core, "_scan_engine_processes", lambda: [])
    assert engines._resolve_spawn_caller_session_id([], "") == ""
    assert engines._resolve_spawn_caller_session_id(None, "") == ""


def test_placeholder_with_a_known_session_id_never_fuzzy_matches():
    """static/app.js: a placeholder that knows its target id must not bind to
    an older session that merely ran the same prompt in the same repo."""
    app_js = (REPO / "static" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function pendingSpawnMatchesRow(pid, placeholder, row)")
    body = app_js[start : app_js.index("function reconcilePendingSpawnsWithRows", start)]
    guard = body.index("if (placeholder.expected_session_id) {")
    fuzzy = body.index("normalizePendingPrompt(placeholder.first_message")
    assert guard < fuzzy, "the known-id short circuit must precede the prompt heuristic"
    assert "return !!(row.spawn_pid && String(row.spawn_pid) === String(pid));" in body
