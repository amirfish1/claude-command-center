"""Regression coverage for the throughput sidebar's shared time window."""

import pathlib

import server


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_throughput_scope_supports_rolling_sidebar_windows():
    is_aggregate, cutoff, label = server._throughput_scope("all", "2d")

    assert is_aggregate is True
    assert label == "Last 2 days"
    assert cutoff is not None


def test_throughput_sidebar_uses_shared_window_for_rankings_and_aggregate():
    html = (PROJECT_ROOT / "static/throughput.html").read_text(encoding="utf-8")

    assert "let activeThroughputRange = '7d';" in html
    assert 'data-throughput-range="2d"' in html
    assert "/api/throughput/window?start=" in html
    assert "&range=${encodeURIComponent(activeThroughputRange)}" in html
