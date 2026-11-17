"""Regression coverage for the Launch target menu viewport guard."""

from tests.dashboard_source import dashboard_source
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_launch_target_menu_flips_above_its_trigger_when_it_would_overflow():
    source = dashboard_source(ROOT / "static" / "app.js")
    css = (ROOT / "static" / "app.css").read_text(encoding="utf-8")

    assert "function positionLaunchChoiceMenu(menu)" in source
    assert "rect.bottom > window.innerHeight" in source
    assert "menu.classList.toggle('opens-up'" in source
    assert ".launch-choice-menu.opens-up" in css
    assert "bottom: calc(100% + 4px)" in css
