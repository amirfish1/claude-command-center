"""Continuation retry keeps repository selection explicit."""

from pathlib import Path


APP_JS = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(
    encoding="utf-8"
)


def test_failed_continuation_edit_requires_an_explicit_repo_path():
    assert "function _showPendingSpawnRepoPathEditor" in APP_JS
    assert "data-pending-spawn-repo-path" in APP_JS
    assert "body.repo_path = repoPath" in APP_JS
    assert "body.cwd = repoPath" in APP_JS
    assert "popoutRepoPath()" in APP_JS
