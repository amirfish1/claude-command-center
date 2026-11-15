"""Isolated release tests: real git worktrees, fake signing/GitHub/SSH transports."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import types

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_backfill", ROOT / "scripts/release_backfill.py")
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


def executable(filename, text):
    filename.write_text(textwrap.dedent(text))
    filename.chmod(0o755)


def cmd(*args, cwd):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


@pytest.fixture
def transport(tmp_path, monkeypatch):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    store = tmp_path / "assets"
    store.mkdir()
    calls = tmp_path / "calls"
    monkeypatch.setenv("FAKE_ASSETS", str(store))
    monkeypatch.setenv("FAKE_CALLS", str(calls))
    monkeypatch.setenv("PATH", f"{binaries}:{os.environ['PATH']}")
    monkeypatch.setenv("CCC_RELEASE_SIGNING_HOST", "signer.example.invalid")
    executable(binaries / "gh", '''\
        #!/usr/bin/env python3
        import json,os,pathlib,shutil,sys
        a=sys.argv[1:]; store=pathlib.Path(os.environ['FAKE_ASSETS'])
        with open(os.environ['FAKE_CALLS'],'a') as f: f.write(json.dumps(['gh']+a)+'\\n')
        if a[:2]==['release','view']:
            print(json.dumps({'assets':[{'name':p.name} for p in store.iterdir()]}))
        elif a[:2]==['release','upload']:
            source=pathlib.Path(a[3]); target=store/source.name
            fail=store.parent/'fail-stable-once'
            if source.name=='ccc.dmg' and fail.exists():
                fail.unlink(); sys.exit(1)
            if target.exists() and '--clobber' not in a: sys.exit(1)
            shutil.copyfile(source,target)
        elif a[:2]==['release','download']:
            dest=pathlib.Path(a[a.index('--dir')+1])
            for i,arg in enumerate(a):
                if arg=='--pattern':
                    shutil.copyfile(store/a[i+1],dest/a[i+1])
                    if a[i+1]=='ccc.dmg' and os.environ.get('FAKE_CORRUPT')=='1':
                        (dest/a[i+1]).write_bytes(b'corrupted')
        else: sys.exit(2)
    ''')
    executable(binaries / "ssh", '''\
        #!/usr/bin/env python3
        import json,os,sys
        with open(os.environ['FAKE_CALLS'],'a') as f: f.write(json.dumps(['ssh']+sys.argv[1:])+'\\n')
        tag,token=sys.argv[-1].split()[1:]
        if os.environ.get('FAKE_SSH_OK')=='1': print('BACKFILL-OK '+tag+' '+token)
        elif os.environ.get('FAKE_SSH_WRONG')=='1': print('BACKFILL-OK '+tag+' '+'0'*32)
        else: sys.exit(255)
    ''')
    executable(binaries / "wt", '''\
        #!/usr/bin/env python3
        import json,os,pathlib,sys
        with open(os.environ['FAKE_CALLS'],'a') as f: f.write(json.dumps(['wt']+sys.argv[1:])+'\\n')
        key=sys.argv[sys.argv.index('--dedupe-key')+1]
        (pathlib.Path(os.environ['FAKE_ASSETS']).parent/key).touch()
    ''')
    return tmp_path, store, calls


def read_calls(calls):
    return [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []


def test_pending_keeps_failure_clears_only_matching_receipt(transport, monkeypatch):
    directory, _, calls = transport
    backfill.enqueue("v1.0.0", directory)
    original = (directory / "dmg-pending").read_bytes()
    backfill.enqueue("v1.0.0", directory)
    assert (directory / "dmg-pending").read_bytes() == original
    backfill.pending(directory)
    assert "v1.0.0" in json.loads((directory / "dmg-pending").read_text())
    monkeypatch.setenv("FAKE_SSH_WRONG", "1")
    backfill.pending(directory)
    assert "v1.0.0" in json.loads((directory / "dmg-pending").read_text())
    monkeypatch.setenv("FAKE_SSH_OK", "1")
    backfill.pending(directory)
    assert json.loads((directory / "dmg-pending").read_text()) == {}
    assert len([c for c in read_calls(calls) if c[0] == "ssh"]) == 3


def test_stale_queue_escalates_with_same_dedupe_key_and_dry_run_is_read_only(transport):
    directory, _, calls = transport
    backfill.atomic_json(directory / "dmg-pending", {"v1.0.0": {"since": 1}})
    before = (directory / "dmg-pending").stat()
    backfill.pending(directory, dry=True)
    assert not calls.exists()
    assert (directory / "dmg-pending").stat().st_mtime_ns == before.st_mtime_ns
    backfill.pending(directory)
    backfill.pending(directory)
    tickets = [c for c in read_calls(calls) if c[0] == "wt"]
    assert len(tickets) == 2
    assert all(c[-2:] == ["--dedupe-key", "dmg-backfill-v1.0.0"] for c in tickets)
    assert len(list(directory.glob("dmg-backfill-*"))) == 1


def test_pending_preserves_tags_enqueued_during_remote_build(transport, monkeypatch):
    directory, _, _ = transport
    backfill.enqueue("v1.0.0", directory)
    monkeypatch.setenv("FAKE_SSH_OK", "1")
    original = backfill.run

    def enqueue_during_ssh(*args, **kwargs):
        if args[0] == "ssh":
            backfill.enqueue("v2.0.0", directory)
        return original(*args, **kwargs)

    monkeypatch.setattr(backfill, "run", enqueue_during_ssh)
    backfill.pending(directory)
    assert list(json.loads((directory / "dmg-pending").read_text())) == ["v2.0.0"]


def feed(ver, size=8):
    return f'''<rss xmlns:sparkle="{backfill.SPARKLE}"><channel><title>Test updates</title>
      <item><title>v{ver}</title><sparkle:version>{ver}</sparkle:version>
      <enclosure url="https://example.invalid/v{ver}/ccc-v{ver}.dmg" length="{size}"
      sparkle:edSignature="test-signature" /></item></channel></rss>'''


@pytest.fixture
def publication(transport, monkeypatch):
    directory, assets, calls = transport
    repo = directory / "source"
    repo.mkdir()
    remote = directory / "origin.git"
    cmd("git", "init", "--bare", str(remote), cwd=directory)
    cmd("git", "--git-dir", str(remote), "config", "core.hooksPath", str(remote / "hooks"), cwd=directory)
    cmd("git", "init", "-b", "main", cwd=repo)
    cmd("git", "config", "user.email", "release@example.invalid", cwd=repo)
    cmd("git", "config", "user.name", "Release test", cwd=repo)
    cmd("git", "config", "core.hooksPath", str(repo / ".git/hooks"), cwd=repo)
    (repo / "docs").mkdir()
    (repo / "scripts").mkdir()
    (repo / "docs/appcast.xml").write_text(feed("0.9.0"))
    (repo / "scripts/release-dmg.sh").write_text('''#!/usr/bin/env bash
set -e
if [ "${SKIP_BUILD:-0}" != 1 ]; then
  if [ -f "$FAKE_ASSETS/../fail-build-once" ]; then
    printf partial > "ccc-v$1.dmg"
    rm "$FAKE_ASSETS/../fail-build-once"
    exit 1
  fi
  printf 'test-dmg' > "ccc-v$1.dmg"
  echo build >> "$FAKE_ASSETS/../builds"
fi
python3 - "$1" <<'PY'
from pathlib import Path
import sys
ver=sys.argv[1]; name=f'ccc-v{ver}.dmg'
Path('docs/appcast.xml').write_text(f'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item><sparkle:version>{ver}</sparkle:version><enclosure url="https://example.invalid/v{ver}/{name}" length="{Path(name).stat().st_size}" sparkle:edSignature="test-signature" /></item></channel></rss>')
PY
''')
    cmd("git", "add", "docs/appcast.xml", "scripts/release-dmg.sh", cwd=repo)
    cmd("git", "commit", "-m", "test: initial source", cwd=repo)
    cmd("git", "tag", "v1.0.0", cwd=repo)
    (repo / "docs/appcast.xml").write_text(feed("2.0.0"))
    cmd("git", "add", "docs/appcast.xml", cwd=repo)
    cmd("git", "commit", "-m", "test: newer release", cwd=repo)
    cmd("git", "remote", "add", "origin", str(remote), cwd=repo)
    cmd("git", "push", "origin", "main", "--tags", cwd=repo)
    monkeypatch.setattr(backfill, "ROOT", repo)
    monkeypatch.setattr(backfill, "sys", types.SimpleNamespace(platform="darwin", stderr=sys.stderr))
    return directory, repo, remote, assets, calls


def test_isolated_build_and_partial_upload_recovery_keep_latest_first(publication):
    directory, repo, remote, assets, calls = publication
    head = cmd("git", "rev-parse", "HEAD", cwd=repo)
    (repo / "unrelated.txt").write_text("another worker's edit")
    (directory / "fail-stable-once").touch()
    with pytest.raises(subprocess.CalledProcessError):
        backfill.publish("v1.0.0", directory)
    assert (assets / "ccc-v1.0.0.dmg").read_bytes() == b"test-dmg"
    assert not (assets / "ccc.dmg").exists()
    backfill.publish("v1.0.0", directory)
    assert (assets / "ccc.dmg").read_bytes() == b"test-dmg"
    assert (directory / "builds").read_text().splitlines() == ["build"]
    remote_feed = cmd("git", "--git-dir", str(remote), "show", "main:docs/appcast.xml", cwd=repo)
    assert remote_feed.index("2.0.0") < remote_feed.index("1.0.0")
    assert cmd("git", "rev-parse", "HEAD", cwd=repo) == head
    assert (repo / "unrelated.txt").read_text() == "another worker's edit"
    # A completed duplicate is safe: immutable versioned asset is never overwritten.
    remote_head = cmd("git", "--git-dir", str(remote), "rev-parse", "main", cwd=repo)
    backfill.publish("v1.0.0", directory)
    assert cmd("git", "--git-dir", str(remote), "rev-parse", "main", cwd=repo) == remote_head
    uploads = [c for c in read_calls(calls) if c[:3] == ["gh", "release", "upload"]]
    assert len([c for c in uploads if c[4].endswith("ccc-v1.0.0.dmg")]) == 1


def test_push_failure_retries_without_rebuilding(publication):
    directory, repo, remote, assets, _ = publication
    hook = remote / "hooks/pre-receive"
    executable(hook, "#!/bin/sh\nexit 1\n")
    with pytest.raises(subprocess.CalledProcessError):
        backfill.publish("v1.0.0", directory)
    assert (assets / "ccc.dmg").exists()
    hook.unlink()
    backfill.publish("v1.0.0", directory)
    assert (directory / "builds").read_text().splitlines() == ["build"]
    assert "1.0.0" in cmd("git", "--git-dir", str(remote), "show", "main:docs/appcast.xml", cwd=repo)


def test_failed_signing_build_is_not_reused_or_published(publication):
    directory, _, _, assets, _ = publication
    (directory / "fail-build-once").touch()
    with pytest.raises(subprocess.CalledProcessError):
        backfill.publish("v1.0.0", directory)
    assert list(assets.iterdir()) == []
    assert not (directory / "dmg-builds/v1.0.0/backfill-ready.json").exists()
    backfill.publish("v1.0.0", directory)
    assert (assets / "ccc-v1.0.0.dmg").read_bytes() == b"test-dmg"


def test_lost_local_cache_recovers_immutable_uploaded_bytes(publication):
    directory, _, _, assets, _ = publication
    (directory / "fail-stable-once").touch()
    with pytest.raises(subprocess.CalledProcessError):
        backfill.publish("v1.0.0", directory)
    backfill.publish("v1.0.0", directory / "new-machine-state")
    assert (directory / "builds").read_text().splitlines() == ["build"]
    assert (assets / "ccc.dmg").read_bytes() == b"test-dmg"


def test_corrupt_download_never_publishes_appcast(publication, monkeypatch):
    directory, repo, remote, _, _ = publication
    before = cmd("git", "--git-dir", str(remote), "rev-parse", "main", cwd=repo)
    monkeypatch.setenv("FAKE_CORRUPT", "1")
    with pytest.raises(ValueError, match="do not match signed bytes"):
        backfill.publish("v1.0.0", directory)
    assert cmd("git", "--git-dir", str(remote), "rev-parse", "main", cwd=repo) == before


@pytest.mark.parametrize("dry", [False, True])
def test_macos_request_bypasses_new_release_gate(transport, monkeypatch, dry):
    directory, _, calls = transport
    repo = directory / "mac-daily"
    (repo / "scripts").mkdir(parents=True)
    for name in ("release-daily.sh", "release_backfill.py"):
        shutil.copy2(ROOT / "scripts" / name, repo / "scripts" / name)
    executable(repo / "scripts/cut-release.sh", '''\
        #!/bin/sh
        printf '%s\\n' "$*" > "$FAKE_CALLS"
    ''')
    mode = directory / "mode"
    token = "a" * 32
    mode.write_text(f"backfill v1.0.0 {token}\n")
    monkeypatch.setenv("CCC_RELEASE_LOG_DIR", str(directory))
    monkeypatch.setenv("CCC_RELEASE_MODE_FILE", str(mode))
    monkeypatch.setenv("TMPDIR", str(directory))
    result = subprocess.run(["bash", str(repo / "scripts/release-daily.sh")] + (["--dry-run"] if dry else []),
                            env=os.environ, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert calls.read_text().strip() == "--dmg-only v1.0.0" + (" --dry-run" if dry else "")
    assert (f"BACKFILL-OK v1.0.0 {token}" in result.stdout) == (not dry)
    assert mode.exists() == dry


def test_appcast_conflict_refuses_to_change_signed_version():
    with pytest.raises(ValueError, match="different signed item"):
        backfill.merge_feed(feed("1.0.0", 9), feed("1.0.0", 8), "v1.0.0")


def test_daily_pending_runs_even_when_release_is_waived(transport, monkeypatch):
    directory, _, calls = transport
    repo = directory / "daily"
    (repo / "scripts").mkdir(parents=True)
    for name in ("release-daily.sh", "release_backfill.py"):
        shutil.copy2(ROOT / "scripts" / name, repo / "scripts" / name)
    executable(repo / "scripts/repo-clean-check.sh", "#!/bin/sh\necho 'BLOCK uncommitted fixture'\nexit 1\n")
    monkeypatch.setenv("CCC_RELEASE_LOG_DIR", str(directory))
    monkeypatch.setenv("HOME", str(directory / "home"))
    monkeypatch.setenv("CCC_RELEASE_MODE_FILE", str(directory / "mode"))
    monkeypatch.setenv("TMPDIR", str(directory))
    monkeypatch.setenv("FAKE_SSH_OK", "1")
    backfill.enqueue("v1.0.0", directory)
    result = subprocess.run(["bash", str(repo / "scripts/release-daily.sh")],
                            env=os.environ, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "WAIVED" in result.stdout
    assert json.loads((directory / "dmg-pending").read_text()) == {}
    assert any(c[0] == "ssh" for c in read_calls(calls))


def test_trigger_correlates_receipt_and_does_not_kill_signing(transport, monkeypatch):
    directory, _, calls = transport
    home = directory / "home"
    monkeypatch.setenv("HOME", str(home))
    token = "a" * 32
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", f"backfill v1.0.0 {token}")
    executable(directory / "bin/launchctl", '''\
        #!/usr/bin/env python3
        import json,os,pathlib,sys,time
        with open(os.environ['FAKE_CALLS'],'a') as f: f.write(json.dumps(sys.argv)+'\\n')
        home=pathlib.Path.home(); mode=(home/'.claude/command-center/release-daily.mode').read_text().strip()
        tag,token=mode.split()[1:]
        log=home/'Library/Logs/ccc-release'/time.strftime('%Y-%m-%d.log')
        with log.open('a') as f:
            f.write('00:00:00 WAIVED: unrelated run\\n')
            f.write('00:00:00 BACKFILL-OK '+tag+' '+token+'\\n')
    ''')
    executable(directory / "bin/sleep", "#!/bin/sh\nexit 0\n")
    result = subprocess.run(["bash", str(ROOT / "scripts/release-trigger.sh")],
                            env=os.environ, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"BACKFILL-OK v1.0.0 {token}"
    assert not any("-k" in c for c in read_calls(calls))


def test_dmg_only_dry_run_does_not_cut_new_version(tmp_path):
    result = subprocess.run(["bash", str(ROOT / "scripts/cut-release.sh"),
                             "--dmg-only", "v1.0.0", "--dry-run"],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert "isolated tag build" in result.stdout
    assert "1/9" not in result.stdout


