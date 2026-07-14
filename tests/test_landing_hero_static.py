from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_landing_hero_uses_public_safe_product_screenshot():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    asset = ROOT / "docs" / "images" / "ccc-live-session-workspace.png"

    assert asset.is_file()
    assert './images/ccc-live-session-workspace.png?v=1' in page
    assert "CCC live workspace showing sessions, a conversation, and its linked issue queue" in page
