"""Regression coverage for post-spawn sidebar reconciliation."""

from pathlib import Path


def test_chase_keeps_tracking_an_adopted_pending_spawn():
    """Continuation spawns re-key from a temporary id to the server pid."""
    app_js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(
        encoding="utf-8"
    )
    start = app_js.index("function chasePendingSpawn(pid, opts)")
    body = app_js[start : start + 3200]

    assert "_pendingSpawnStillWaiting(pid" in body
    assert "if (!pendingSpawns.has(pid)) return;" not in body
