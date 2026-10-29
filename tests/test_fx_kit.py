"""Static contract checks for the sound + motion kit (static/fx.js, fx.css).

The kit is client-side only: no endpoints of its own, so these tests pin the
window.cccFx contract other features call, the wiring of its assets into
index.html, and the narrow /fx-demo.html route that serves its demo page.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FX_JS = ROOT / "static" / "fx.js"
FX_CSS = ROOT / "static" / "fx.css"
FX_DEMO = ROOT / "static" / "fx-demo.html"
INDEX = ROOT / "static" / "index.html"
SERVER = ROOT / "server.py"


def test_fx_kit_files_exist():
    assert FX_JS.is_file()
    assert FX_CSS.is_file()
    assert FX_DEMO.is_file()


def test_cccfx_contract_is_exposed():
    js = FX_JS.read_text(encoding="utf-8")
    assert "window.cccFx" in js
    api_block = js.split("window.cccFx", 1)[1]
    for key in ("play", "confetti", "countUp", "reducedMotion", "muted"):
        assert re.search(rf"\b{key}\b", api_block), f"cccFx.{key} missing"


def test_all_six_sound_names_are_defined():
    js = FX_JS.read_text(encoding="utf-8")
    for name in ("welcome", "step", "success", "coin", "error", "whoosh"):
        assert re.search(rf"\b{name}\s*:\s*function\b", js), f"sound {name} missing"


def test_sounds_respect_the_shared_mute_pref():
    js = FX_JS.read_text(encoding="utf-8")
    # Same localStorage flag the footer pill and Settings toggle already use.
    assert "ccc-sounds-enabled" in js


def test_motion_respects_prefers_reduced_motion():
    js = FX_JS.read_text(encoding="utf-8")
    css = FX_CSS.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in js
    assert "prefers-reduced-motion" in css


def test_no_audio_files_or_external_assets():
    js = FX_JS.read_text(encoding="utf-8")
    assert "new Audio(" not in js
    for ext in (".mp3", ".wav", ".ogg", ".m4a", ".flac"):
        assert ext not in js, f"audio asset {ext} referenced"
    assert "fetch(" not in js


def test_index_html_loads_the_kit():
    html = INDEX.read_text(encoding="utf-8")
    assert '<link rel="stylesheet" href="/static/fx.css">' in html
    assert '<script src="/static/fx.js"></script>' in html
    # Loaded before app.js so cccFx exists when app code runs.
    assert html.index("/static/fx.js") < html.index('src="/static/app.js"')


def test_index_html_asset_urls_get_cache_stamps():
    server = SERVER.read_text(encoding="utf-8")
    assert '_static_asset_url("fx.css")' in server
    assert '_static_asset_url("fx.js")' in server


def test_fx_demo_route_registered():
    server = SERVER.read_text(encoding="utf-8")
    assert '"/fx-demo.html"' in server


def test_fx_demo_page_uses_the_kit():
    demo = FX_DEMO.read_text(encoding="utf-8")
    assert '/static/fx.js' in demo
    assert '/static/fx.css' in demo
    assert "cccFx" in demo


def test_css_exposes_the_effect_classes():
    css = FX_CSS.read_text(encoding="utf-8")
    for cls in (".fx-confetti-canvas", ".fx-shimmer", ".fx-glow", ".fx-pop"):
        assert cls in css, f"{cls} missing from fx.css"
