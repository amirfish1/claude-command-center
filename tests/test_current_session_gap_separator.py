"""Regression coverage for Current sessions' activity-gap divider."""

from pathlib import Path


APP_JS = Path(__file__).resolve().parents[1] / "static" / "app.js"


def test_current_sessions_gap_uses_the_same_activity_timestamp_as_row_age():
    source = APP_JS.read_text()

    assert "const _currentSessionActivityTs = (c) => Math.max(" in source
    assert "Number(c && c.last_interacted || 0)," in source
    assert "Number(c && c.modified || c && c.mtime || 0)" in source
    assert "const _sessionTs = _currentSessionActivityTs;" in source
    assert "mtime: Math.max(...cluster.rows.map(row => _currentSessionActivityTs(row.card)))," in source
    assert "mtime: _currentSessionActivityTs(cards[0])," in source
