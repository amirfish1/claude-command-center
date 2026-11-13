"""Regression coverage for the Workers lane's activity-block order."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_workers_lane_has_no_separate_working_now_strip():
    """CCC-369: the session list is the one live view; the standalone strip
    and queue warning chips are omitted from the activity block."""
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    block_start = app_js.index('class="conv-workers-activity"')
    block_end = app_js.index("const _arcToolsLeft", block_start)
    block = app_js[block_start:block_end]

    assert 'data-role="workers-working-now"' not in block
    assert 'data-role="workers-queue-health"' not in block


def test_workers_tools_follow_ticket_activity():
    """Time filters sit below ticket activity and above the remaining sessions."""
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    section = app_js.index("'<div class=\"conv-archived-section\"")
    activity = app_js.index("+ _workersActivityHtml", section)
    tools = app_js.index("+ _arcTools", section)

    assert activity < tools
