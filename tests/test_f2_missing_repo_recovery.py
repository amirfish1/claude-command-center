"""Continuation retry keeps repository selection explicit."""

from tests.dashboard_source import dashboard_source
from pathlib import Path


APP_JS = dashboard_source(Path(__file__).resolve().parents[1] / "static" / "app.js")


def test_failed_continuation_edit_requires_an_explicit_repo_path():
    assert "function _showPendingSpawnRepoPathEditor" in APP_JS
    assert "data-pending-spawn-repo-path" in APP_JS
    assert "body.repo_path = repoPath" in APP_JS
    assert "body.cwd = repoPath" in APP_JS
    assert "popoutRepoPath()" in APP_JS
