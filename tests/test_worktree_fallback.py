"""Tests for falling back to the parent repo when a worktree is deleted."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


def test_fallback_cwd_for_deleted_worktree_current_layout(tmp_path: Path) -> None:
    """CCC layout: <parent>/<repo>-wt/<slug> -> parent repo."""
    parent = tmp_path / "parent"
    repo = parent / "demo-repo"
    worktree = parent / "demo-repo-wt" / "feature-x"
    repo.mkdir(parents=True)
    worktree.mkdir(parents=True)
    worktree.rmdir()
    (parent / "demo-repo-wt").rmdir()
    assert server._fallback_cwd_for_deleted_worktree(str(worktree)) == str(repo)


def test_fallback_cwd_for_deleted_worktree_legacy_layout(tmp_path: Path) -> None:
    """Legacy layout: <parent>/<repo>-wt-<slug> -> parent repo."""
    parent = tmp_path / "parent"
    repo = parent / "demo-repo"
    worktree = parent / "demo-repo-wt-feature-x"
    repo.mkdir(parents=True)
    worktree.mkdir(parents=True)
    worktree.rmdir()
    assert server._fallback_cwd_for_deleted_worktree(str(worktree)) == str(repo)


def test_fallback_cwd_for_deleted_worktree_nested_layout(tmp_path: Path) -> None:
    """Nested layout: <repo>/.claude/worktrees/<name> -> repo."""
    repo = tmp_path / "demo-repo"
    worktree = repo / ".claude" / "worktrees" / "feature-x"
    repo.mkdir(parents=True)
    worktree.mkdir(parents=True)
    worktree.rmdir()
    (repo / ".claude" / "worktrees").rmdir()
    (repo / ".claude").rmdir()
    assert server._fallback_cwd_for_deleted_worktree(str(worktree)) == str(repo)


def test_fallback_cwd_for_deleted_worktree_via_parent_session(tmp_path: Path) -> None:
    """The parent session's spawn registry repo_path is the preferred fallback."""
    repo = tmp_path / "demo-repo"
    worktree = tmp_path / "demo-repo-wt" / "feature-x"
    repo.mkdir(parents=True)
    worktree.mkdir(parents=True)
    worktree.rmdir()
    (tmp_path / "demo-repo-wt").rmdir()

    entry = {
        "pid": 12345,
        "session_id": "parent-session-1",
        "name": "parent",
        "log": str(tmp_path / "parent.log"),
        "fifo": None,
        "cwd": str(worktree),
        "repo_path": str(repo),
        "spawned_at": "2026-01-01T00:00:00",
        "command_summary": "claude",
        "engine": "claude",
        "model": "sonnet-5",
        "parent_session_id": "",
    }
    with patch.object(server, "_load_spawn_registry", return_value=[entry]):
        assert server._fallback_cwd_for_deleted_worktree(
            str(worktree), parent_session_id="parent-session-1"
        ) == str(repo)


def test_fallback_cwd_returns_none_when_no_worktree_match(tmp_path: Path) -> None:
    """A plain missing directory that isn't a known worktree layout returns None."""
    missing = tmp_path / "just-gone"
    assert server._fallback_cwd_for_deleted_worktree(str(missing)) is None


def test_fallback_cwd_prefers_existing_path(tmp_path: Path) -> None:
    """If the cwd still exists, the first-existing-dir strategy returns it."""
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    assert server._first_existing_dir(str(repo)) == str(repo)


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(Path(td))
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"ERROR {name}: {exc!r}")
            else:
                print(f"ok   {name}")
    raise SystemExit(1 if failed else 0)
