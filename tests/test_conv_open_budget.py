"""Source invariants on the click -> conversation-open path in static/app.js.

Same shape as tests/test_dashboard_startup_budget.py: slice a function out of
the app source and assert the guard that keeps the open path cheap is present.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def _function(name, next_marker, *, start_at=0):
    start = SOURCE.index(name, start_at)
    return SOURCE[start:SOURCE.index(next_marker, start)]


def test_files_pill_fetch_dedupes_in_flight_requests():
    # Every SSE tick invalidates the Files-pill cache and re-renders, so two
    # ticks in flight produced two identical /files requests at once
    # (service log 2026-09-12: pairs of 8-15 s Devin walks one second apart).
    source = _function("async function ffcFetch(", "function ffcInvalidate(")

    assert "_ffcInFlight" in source
    assert "_ffcInFlight.get(convId)" in source
    assert "_ffcInFlight.set(convId," in source
    assert "_ffcInFlight.delete(convId)" in source
