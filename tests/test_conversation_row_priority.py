"""Regression guard for compact sidebar conversation-row priority (CCC-724)."""

from pathlib import Path


CSS = Path(__file__).resolve().parents[1] / "static" / "app.css"


def test_narrow_conversation_rows_shed_secondary_chips_before_title():
    """Responsive rules remove metadata in the annotation's priority order."""
    source = CSS.read_text()
    agents = source.index(".conv-item .conv-subagent-cluster-toggle,\n    .conv-item .conv-signal.subagents,")
    running = source.index(".conv-item .conv-signal-sub-flight")
    activity = source.index(".conv-item .conv-live-tool,\n    .conv-item .conv-signal.codex-stuck,")
    size = source.index(".conv-item .conv-lifetime-tokens,\n    .conv-item .conv-row-size")
    assert agents < running < activity < size
    assert "ccc-724: agent counts yield before the conversation title" in source[agents:running]
    assert "ccc-724: running indicators yield after agent counts" in source[running:activity]
    assert "ccc-724: stale and working state yield after running indicators" in source[activity:size]
    assert "ccc-724: token and size-like metrics yield last" in source[size:]
