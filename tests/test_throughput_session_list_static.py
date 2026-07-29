from pathlib import Path


def test_throughput_sidebar_can_expand_sessions_beyond_top_twenty():
    html = Path("static/throughput.html").read_text(encoding="utf-8")

    assert "let sessionListExpanded = false;" in html
    assert "const visible = sessionListExpanded ? sorted : sorted.slice(0, TOP_N);" in html
    assert "sessionListExpanded = !sessionListExpanded;" in html
    assert "Show all ${rest.length} sessions" in html
