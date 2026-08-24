"""Regression coverage for All-tab project-collapse behavior."""

from pathlib import Path


APP_CSS = Path(__file__).resolve().parents[1] / "static" / "app.css"


def test_collapsed_project_hides_direct_subagent_cluster():
    app_css = APP_CSS.read_text(encoding="utf-8")

    assert "#convList .conv-folder-group.collapsed > .conv-subagent-cluster" in app_css
    assert "#convList .conv-folder-group.sessions-collapsed > .conv-subagent-cluster" in app_css
