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


def test_ticket_detail_emphasizes_only_the_first_sentence_of_a_long_note():
    app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    app_css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")
    start = app_js.index("function _uxqOpenItemModal(item)")
    end = app_js.index("function _renderQueuePanel", start)
    modal = app_js[start:end]

    assert "const titleParts = splitFirstSentence(detailTitle);" in modal
    assert 'class="uxq-td-title-first"' in modal
    assert 'class="uxq-td-title-rest"' in modal
    assert ".uxq-td-title-first" in app_css
    assert ".uxq-td-title-rest" in app_css
    assert "max-height: 180px" in app_css
    assert "overflow-y: auto" in app_css
