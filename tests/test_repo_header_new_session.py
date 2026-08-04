"""Static regression coverage for repository-scoped new sessions."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "app.css").read_text(encoding="utf-8")


def test_repo_header_new_session_uses_the_group_repository_path():
    """The compact header action opens the normal composer in its repo."""
    assert 'data-role="folder-new-session"' in APP_JS
    assert 'data-folder-path="' in APP_JS
    assert "enterNewSessionMode();" in APP_JS
    assert "setSpawnCwdInputValue(repoPath, { focus: false });" in APP_JS
    assert ".conv-folder-repo-actions" in APP_CSS
    assert ".conv-folder-new-session-btn" in APP_CSS
