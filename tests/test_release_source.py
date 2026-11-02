"""The release gate tests immutable source and refuses an untested revision."""
import os
from pathlib import Path
import shutil
import subprocess

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
