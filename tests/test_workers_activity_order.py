"""Regression coverage for the Workers lane's activity-block order."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_working_now_strip_leads_the_workers_activity_block():
    """CCC-1163: WORKING NOW is the lane's headline — it must precede the
    queue-health chips and the recently-worked details inside the block."""
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    block_start = app_js.index('class="conv-workers-activity"')
    block_end = app_js.index("'</details>'", block_start)
    block = app_js[block_start:block_end]

    assert block.index('data-role="workers-working-now"') \
        < block.index('data-role="workers-queue-health"') \
        < block.index('data-role="workers-recent-work"')


def test_workers_activity_is_the_archived_sections_first_child():
    """The activity block sits directly under the tab bar — ahead of the
    archived tools row, the uniform banner, and the pending-worker rows."""
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")

    section = app_js.index("'<div class=\"conv-archived-section\"")
    activity = app_js.index("+ _workersActivityHtml", section)
    tools = app_js.index("+ _arcTools", section)

    assert activity < tools
