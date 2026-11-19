"""Exercise update preservation using real disposable Git repositories."""
import subprocess
from pathlib import Path

import pytest
import server


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


@pytest.fixture
def install(tmp_path, monkeypatch):
    remote = tmp_path / 'remote'
    remote.mkdir()
    git(remote, 'init', '-b', 'main')
    git(remote, 'config', 'user.name', 'Test')
    git(remote, 'config', 'user.email', 'test@example.invalid')
    for name in ('incoming.txt', 'local.txt'):
        (remote / name).write_text('base\n')
    git(remote, 'add', 'incoming.txt', 'local.txt')
    git(remote, 'commit', '-m', 'base')
    clone = tmp_path / 'install'
    subprocess.run(['git', 'clone', str(remote), str(clone)], check=True, capture_output=True)
    # A user's autostash preference must never override update preservation.
    git(clone, 'config', 'merge.autostash', 'true')
    (remote / 'incoming.txt').write_text('updated\n')
    (remote / 'new.txt').write_text('upstream\n')
    git(remote, 'add', 'incoming.txt', 'new.txt')
    git(remote, 'commit', '-m', 'fix(ui): current update <details>')
    monkeypatch.setattr(server, '_install_dir', lambda: clone)
    monkeypatch.setattr(server, '_update_channel', lambda: 'main')
    monkeypatch.setattr(server, '_restart_stale_worker', lambda: {})
    monkeypatch.setattr(server, '_update_watchtower', lambda: {'ok': True})
    monkeypatch.setattr(server, '_wt_live_workers', lambda: [{'worker_id': 'test'}])
    monkeypatch.setattr(server, '_reset_wt_capability_caches', lambda: None)
    return clone, remote


@pytest.mark.parametrize('staged', [False, True])
def test_update_preserves_unrelated_local_edits(install, staged):
    clone, remote = install
    (clone / 'local.txt').write_text('my unfinished work\n')
    (clone / 'scratch.txt').write_text('untracked work\n')
    if staged:
        git(clone, 'add', 'local.txt')
    before = git(clone, 'status', '--porcelain')
    result = server._self_update()
    assert result['ok'], result
    assert git(clone, 'rev-parse', 'HEAD') == git(remote, 'rev-parse', 'HEAD')
    assert git(clone, 'status', '--porcelain') == before
    assert (clone / 'local.txt').read_text() == 'my unfinished work\n'
    assert (clone / 'scratch.txt').read_text() == 'untracked work\n'
    assert not git(clone, 'stash', 'list')


@pytest.mark.parametrize('filename', ['incoming.txt', 'new.txt'])
def test_update_refuses_to_overwrite_local_work(install, filename):
    clone, _ = install
    (clone / filename).write_text('my unfinished work\n')
    before = git(clone, 'rev-parse', 'HEAD')
    result = server._self_update()
    assert not result['ok'], result
    assert git(clone, 'rev-parse', 'HEAD') == before
    assert (clone / filename).read_text() == 'my unfinished work\n'
    assert not git(clone, 'stash', 'list')


def test_update_preview_lists_only_incoming_commits(install):
    result = server._git_commits_behind(force=True)
    assert result['behind'] == 1
    assert result['commits'][0]['subject'] == 'fix(ui): current update <details>'
    assert len(result['commits']) == 1
    assert len(result['commits'][0]['sha']) == 40


def test_update_preserves_ignored_file_that_would_be_overwritten(install):
    clone, _ = install
    (clone / '.git' / 'info' / 'exclude').write_text('new.txt\n')
    (clone / 'new.txt').write_text('ignored unfinished work\n')
    before = git(clone, 'rev-parse', 'HEAD')
    result = server._self_update()
    assert not result['ok'], result
    assert git(clone, 'rev-parse', 'HEAD') == before
    assert (clone / 'new.txt').read_text() == 'ignored unfinished work\n'
