"""Tests for the worktree env-setup hook (issue #47).

Isolated to its own file so it doesn't collide with sibling WIP in
test_smoke.py. Exercises the hook helper directly rather than driving a
full session spawn — we just want to prove the hook runs, exports the
documented env vars, and tolerates absent/failing scripts.
"""

import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import server  # noqa: E402


def _write_hook(worktree: Path, body: str) -> Path:
    hook_dir = worktree / ".ccc"
    hook_dir.mkdir(parents=True, exist_ok=True)
    hook = hook_dir / "worktree-init"
    hook.write_text(body)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hook


def test_missing_hook_is_silent(tmp_path: Path) -> None:
    log = tmp_path / "spawn.log"
    with log.open("w") as fh:
        server._run_worktree_init_hook(tmp_path, tmp_path, "demo", fh)
    assert log.read_text() == ""


def test_non_executable_hook_is_skipped(tmp_path: Path) -> None:
    hook = tmp_path / ".ccc" / "worktree-init"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\necho ran\n")
    # Intentionally not chmod +x.
    log = tmp_path / "spawn.log"
    with log.open("w") as fh:
        server._run_worktree_init_hook(tmp_path, tmp_path, "demo", fh)
    assert "ran" not in log.read_text()


def test_successful_hook_logs_output_and_env(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    worktree = tmp_path / "worktree"
    parent.mkdir()
    worktree.mkdir()
    _write_hook(
        worktree,
        '#!/usr/bin/env bash\n'
        'echo "wt=$CCC_WORKTREE_PATH"\n'
        'echo "parent=$CCC_PARENT_REPO"\n'
        'echo "name=$CCC_SESSION_NAME"\n',
    )
    log = tmp_path / "spawn.log"
    with log.open("w") as fh:
        server._run_worktree_init_hook(worktree, parent, "demo-slug", fh)
    text = log.read_text()
    assert f"wt={worktree}" in text
    assert f"parent={parent}" in text
    assert "name=demo-slug" in text
    assert "[worktree-init] exit 0" in text


def test_failing_hook_does_not_raise(tmp_path: Path) -> None:
    _write_hook(tmp_path, '#!/usr/bin/env bash\necho boom 1>&2\nexit 17\n')
    log = tmp_path / "spawn.log"
    with log.open("w") as fh:
        server._run_worktree_init_hook(tmp_path, tmp_path, "demo", fh)
    text = log.read_text()
    assert "boom" in text
    assert "[worktree-init] exit 17" in text


if __name__ == "__main__":
    # Lightweight runner so this file works without pytest installed.
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
