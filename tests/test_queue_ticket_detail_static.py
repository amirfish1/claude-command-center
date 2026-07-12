"""Static regression coverage for the WatchTower ticket detail modal."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ticket_detail_shows_structured_repo_path_in_origin():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("function _uxqOpenItemModal(item)")
    end = app_js.index("// Answer section", start)
    modal = app_js[start:end]

    assert "item.repo_path" in modal
    assert "Repository" in modal
