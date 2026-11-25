"""The release gate tests immutable source and refuses an untested revision."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mutation", ["none", "dirty", "commit"])
def test_release_source_stays_fixed_and_rejects_concurrent_changes(tmp_path, mutation):
    repo = tmp_path / "source"
    (repo / "scripts").mkdir(parents=True)
    script = repo / "scripts" / "test-release-source.sh"
    shutil.copy2(ROOT / "scripts" / script.name, script)
    (repo / "candidate.txt").write_text("original\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                              capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.name", "Release Test")
    git("config", "user.email", "release@example.invalid")
    git("add", "scripts/test-release-source.sh", "candidate.txt")
    git("commit", "-qm", "candidate")
    revision = git("rev-parse", "HEAD").stdout.strip()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runner = bin_dir / "python3"
    runner.write_text('''#!/usr/bin/env bash
set -eu
[ "$(git rev-parse HEAD)" = "$RELEASE_EXPECTED_SHA" ]
[ "$PWD" != "$RELEASE_ORIGIN" ]
if [ "$RELEASE_MUTATION" != none ]; then
  printf 'changed\\n' > "$RELEASE_ORIGIN/candidate.txt"
  if [ "$RELEASE_MUTATION" = commit ]; then
    git -C "$RELEASE_ORIGIN" commit --only candidate.txt -qm updated
  fi
fi
[ "$(cat candidate.txt)" = original ]
printf '%s' "$PWD" > "$RELEASE_RECEIPT"
''')
    runner.chmod(0o755)
    receipt = tmp_path / "receipt"
    env.update(PATH=f"{bin_dir}:{env['PATH']}", RELEASE_ORIGIN=str(repo),
               RELEASE_MUTATION=mutation, RELEASE_EXPECTED_SHA=revision,
               RELEASE_RECEIPT=str(receipt))
    result = subprocess.run([str(script)], cwd=repo, env=env, capture_output=True,
                            text=True, check=False)

    assert receipt.exists(), result.stderr
    assert not Path(receipt.read_text()).exists(), "temporary checkout must be cleaned"
    assert (result.returncode == 0) == (mutation == "none"), result.stderr
    if mutation != "none":
        assert "changed during tests" in result.stderr


@pytest.mark.parametrize("concurrent_change", ["source", "version", "tag"])
def test_daily_source_release_publishes_only_the_tested_candidate(tmp_path, concurrent_change):
    repo = tmp_path / "source"
    remote = tmp_path / "remote.git"
    peer = tmp_path / "peer"
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Release Fixture",
        GIT_AUTHOR_EMAIL="release@example.invalid",
        GIT_COMMITTER_NAME="Release Fixture",
        GIT_COMMITTER_EMAIL="release@example.invalid",
        CCC_RELEASE_ANNOUNCE="0",
        CCC_RELEASE_MIN_ENTRIES="1",
        CCC_RELEASE_CUT_FLAGS="--skip-dmg --skip-brew",
        CCC_RELEASE_REPOS=str(peer),
        CCC_RELEASE_PREFLIGHT=str(tmp_path / "missing-preflight"),
        CCC_RELEASE_MODE_FILE=str(tmp_path / "missing-mode"),
        CCC_RELEASE_LOG_DIR=str(tmp_path / "logs"),
        TMPDIR=str(tmp_path),
        RELEASE_ORIGIN=str(repo),
        RELEASE_PEER=str(peer),
        RELEASE_CHANGE=concurrent_change,
        RELEASE_NOTES=str(tmp_path / "release-notes"),
        RELEASE_PYTHON=sys.executable,
    )

    def git(*args, cwd=repo):
        return subprocess.run(
            ["git", *args], cwd=cwd, env=env, check=True, capture_output=True, text=True,
        ).stdout.strip()

    (repo / "scripts").mkdir(parents=True)
    for name in ("release-daily.sh", "cut-release.sh", "test-release-source.sh",
                 "release_backfill.py", "release.py"):
        shutil.copy2(ROOT / "scripts" / name, repo / "scripts" / name)
    clean_check = repo / "scripts/repo-clean-check.sh"
    clean_check.write_text("#!/bin/sh\nexit 0\n")
    clean_check.chmod(0o755)
    for name, content in {
        "pyproject.toml": 'version = "0.0.0"\n',
        "server.py": '__version__ = "0.0.0"\n',
        "CHANGELOG.md": "## [Unreleased]\n\n[Unreleased]: test\n",
        "changelog.d/README.md": "Release note snippets.\n",
        "changelog.d/added-tested.md": "Tested change.\n",
        "docs/index.html": "release fixture\n",
        "docs/appcast.xml": "<sparkle:version>0.0.0</sparkle:version>\n",
    }.items():
        target = repo / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(content)
    git("init", "-qb", "main")
    git("add", "scripts", "pyproject.toml", "server.py", "CHANGELOG.md", "changelog.d", "docs")
    git("commit", "-qm", "tested candidate")
    candidate = git("rev-parse", "HEAD")
    env["RELEASE_EXPECTED_SHA"] = candidate
    git("init", "--bare", "-qb", "main", str(remote), cwd=tmp_path)
    git("remote", "add", "origin", str(remote))
    git("push", "-qu", "origin", "main")
    git("clone", "-q", str(remote), str(peer), cwd=tmp_path)

    # Inject only the expensive test invocation and the external publication.
    # Git runs against a real bare remote, with a concurrent independent writer.
    fixture = r'''
    python3() {
      if [ "$1" != -m ] || [ "$2" != pytest ]; then
        "$RELEASE_PYTHON" "$@"
        return
      fi
      [ "$(git rev-parse HEAD)" = "$RELEASE_EXPECTED_SHA" ] || return 1
      [ ! -f changelog.d/added-later.md ] || return 1
      git -C "$RELEASE_PEER" checkout -qb concurrent || return 1
      printf 'Later change.\n' > "$RELEASE_PEER/changelog.d/added-later.md"
      git -C "$RELEASE_PEER" add changelog.d/added-later.md || return 1
      if [ "$RELEASE_CHANGE" = version ]; then
        printf 'version = "9.9.9"\n' > "$RELEASE_PEER/pyproject.toml"
        git -C "$RELEASE_PEER" add pyproject.toml || return 1
      fi
      git -C "$RELEASE_PEER" commit -qm concurrent || return 1
      git -C "$RELEASE_PEER" push -q origin HEAD:main || return 1
      if [ "$RELEASE_CHANGE" = tag ]; then
        git -C "$RELEASE_PEER" push -q origin "$RELEASE_EXPECTED_SHA:refs/tags/v0.1.0" || return 1
      fi
      git -C "$RELEASE_ORIGIN" pull --ff-only -q origin main
    }
    gh() {
      if [ "$1 $2" = "release create" ]; then cat > "$RELEASE_NOTES"; fi
    }
    curl() { return 1; }
    export -f python3 gh curl
    exec bash scripts/release-daily.sh
    '''
    result = subprocess.run(["bash", "-c", fixture], cwd=repo, env=env,
                            capture_output=True, text=True, check=False)
    log = "\n".join(file.read_text() for file in (tmp_path / "logs").glob("*.log"))
    upstream = git("rev-parse", "HEAD", cwd=peer)
    assert git("rev-parse", "HEAD") == upstream, log
    assert git("status", "--porcelain") == ""
    assert git("worktree", "list", "--porcelain").count("worktree ") == 1
    assert not list(tmp_path.glob("ccc-release-cut.*"))
    main = git("--git-dir", str(remote), "rev-parse", "main")
    tags = git("--git-dir", str(remote), "tag", "--list")
    notes = tmp_path / "release-notes"
    if concurrent_change != "source":
        assert result.returncode != 0, log
        assert main == upstream
        assert tags == ("v0.1.0" if concurrent_change == "tag" else "")
        if concurrent_change == "tag":
            assert "Failed to publish pinned release" in log
            assert git("--git-dir", str(remote), "rev-parse", "v0.1.0") == candidate
        else:
            assert "Merge conflict in pyproject.toml" in log
        assert not notes.exists()
        return

    assert result.returncode == 0, result.stdout + result.stderr + log
    assert tags == "v0.1.0"
    assert git("--git-dir", str(remote), "rev-parse", "v0.1.0^") == candidate
    assert git("--git-dir", str(remote), "rev-parse", "main^1") == upstream
    assert git("--git-dir", str(remote), "ls-tree", "--name-only", "v0.1.0:changelog.d") == "README.md"
    assert git("--git-dir", str(remote), "show", "main:changelog.d/added-later.md") == "Later change."
    assert "Tested change." in notes.read_text()
    assert "Later change." not in notes.read_text()
