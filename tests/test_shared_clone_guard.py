"""Exercise hook stdin and Git detection without executing blocked commands."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "shared-clone-guard.py"


@pytest.fixture
def guard(monkeypatch):
    spec = importlib.util.spec_from_file_location("shared_clone_guard", HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("CCC_SHARED_CLONE", raising=False)
    return module


def payload(command, cwd):
    return {"hook_event_name": "PreToolUse", "session_id": "current-session",
            "tool_name": "Bash", "cwd": str(cwd), "tool_input": {"command": command}}


def checkout(root, branch="main"):
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/%s\n" % branch)
    return root


@pytest.mark.parametrize("command", [
    "git reset --hard", "git reset HEAD~1 --hard", "git reset --hard=HEAD",
    "git checkout -- file.py", "git checkout HEAD -- src/file.py",
    "git restore file.py", "git restore --worktree -- file.py",
    "git restore --staged --worktree file.py", "git restore -SW file.py",
    "git restore -- --staged", "git clean -f", "git clean -dfx", "git clean --force",
    "git stash drop", "git stash pop stash@{1}", "git stash --quiet pop",
    "git add -A", "git add --all", "git add .", "git add -- ./", "git add -vA src",
    "git commit -a", "git commit --all -m 'message'", "git commit -am 'message'",
    "git commit -va -m message", "git status && git reset --hard",
    "git status; git stash pop", "git status\ngit clean -fd", "git status || git add .",
    "git reset --hard | cat", "git status & git add .", "(git reset --hard)",
    "if true; then git reset --hard; fi", "FOO=bar git reset --hard",
    "env -u FOO BAR=baz git reset --hard", "command -- git reset --hard",
    "nice -n 10 git clean -fd", "/usr/bin/git reset --hard",
    "git --no-pager -c core.quotePath=false reset --hard",
    "bash -lc 'git reset --hard'", "sh -c 'git add .'", "git reset \\\n --hard",
    "git reset --hard # comment", "# harmless\ngit reset --hard",
])
def test_blocks_risky_commands_when_forced(guard, monkeypatch, tmp_path, command):
    monkeypatch.setenv("CCC_SHARED_CLONE", "1")
    assert guard.check(payload(command, tmp_path)), command


@pytest.mark.parametrize("command", [
    "git status", "git diff", "git diff --stat", "git log -3",
    "git checkout topic", "git switch topic", "git worktree add ../topic -b topic",
    "git reset --soft HEAD~1", "git reset -- file.py", "git restore --staged file.py",
    "git restore -S -- file.py", "git restore -s HEAD --staged file.py",
    "git restore --help", "git clean -ndf", "git clean --force --dry-run",
    "git stash list", "git stash show", "git stash push", "git stash apply",
    "git add src/file.py tests/test_file.py", "git add -n .", "git add --dry-run -A",
    "git add -- -A", "git commit --only src/file.py -m 'fix: message'",
    "git commit -m 'git commit -a'", "git commit -m --all", "git commit -ma",
    "git commit --author --all", "git commit -a --dry-run", "git --help reset --hard",
    "printf '%s\\n' 'git reset --hard'", "echo git reset --hard",
    "echo ';' git reset --hard", "echo '# git reset --hard'", "# git reset --hard",
    "echo fine # git reset --hard", "git diff -- --hard", "echo 'unterminated",
])
def test_safe_commands_do_not_probe_sessions(guard, monkeypatch, tmp_path, command):
    monkeypatch.setenv("CCC_SHARED_CLONE", "1")
    def forbidden(*args):
        pytest.fail("safe command performed repository/session detection")
    monkeypatch.setattr(guard, "checkout_root", forbidden)
    monkeypatch.setattr(guard, "peer_roots", forbidden)
    assert guard.check(payload(command, tmp_path)) is None


def test_literal_directories_and_subshell_scope(guard, tmp_path):
    root = str(tmp_path)
    other = str(tmp_path / "other repo")
    command = "(cd 'other repo' && git reset --hard); git add ."
    assert guard.risky_commands(command, root) == [
        ("git reset --hard", other), ("git add -A / .", root)]
    assert guard.risky_commands("git -C 'other repo' -C sub reset --hard", root) == [
        ("git reset --hard", other + "/sub")]
    assert guard.risky_commands("git -Cother reset --hard", root)[0][1] == root + "/other"
    assert guard.risky_commands("git --work-tree='other repo' reset --hard", root)[0][1] == other


@pytest.fixture
def shared_env(guard, monkeypatch, tmp_path):
    repo = checkout(tmp_path / "repo")
    config = tmp_path / "config"
    sessions = config / "sessions"
    sessions.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(guard, "_live_claude_pids", lambda: [11, 22])
    for pid, sid in [(11, "current-session"), (22, "peer-session")]:
        (sessions / (str(pid) + ".json")).write_text(json.dumps({
            "pid": pid, "sessionId": sid, "cwd": str(repo)}))
    return repo, config


def test_main_plus_live_peer_blocks_and_subdirectories_match(guard, shared_env):
    repo, _ = shared_env
    subdir = repo / "src"
    subdir.mkdir()
    assert guard.check(payload("git reset --hard", subdir))
    assert guard.check(payload("git -C repo reset --hard", repo.parent))
    assert guard.check(payload("cd repo && git reset --hard", repo.parent))


def test_missing_current_sid_does_not_mistake_self_for_peer(guard, shared_env):
    repo, _ = shared_env
    data = payload("git reset --hard", repo)
    data.pop("session_id")
    assert guard.check(data) is None


def test_feature_branch_skips_detection_and_force_overrides(guard, shared_env, monkeypatch):
    repo, _ = shared_env
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/topic\n")
    monkeypatch.setattr(guard, "peer_roots", lambda *a: pytest.fail("scanned on topic"))
    assert guard.check(payload("git reset --hard", repo)) is None
    monkeypatch.setenv("CCC_SHARED_CLONE", "true")
    assert guard.check(payload("git reset --hard", repo))


@pytest.mark.parametrize("peer", [
    None, {}, {"pid": 22, "sessionId": "current-session"},
    {"pid": 99, "sessionId": "peer-session"},
    {"pid": 22, "sessionId": "peer-session", "cwd": "/nonexistent/repo"},
    {"pid": 22, "sessionId": "peer-session", "cwd": None},
])
def test_absent_stale_or_self_registry_does_not_block(guard, shared_env, peer):
    repo, config = shared_env
    file = config / "sessions" / "22.json"
    if peer is None:
        file.unlink()
    else:
        file.write_text(json.dumps({"cwd": str(repo), **peer}))
    assert guard.check(payload("git reset --hard", repo)) is None


def test_dead_process_and_other_worktree_do_not_block(guard, shared_env, monkeypatch, tmp_path):
    repo, config = shared_env
    monkeypatch.setattr(guard, "_live_claude_pids", lambda: [11])
    assert guard.check(payload("git reset --hard", repo)) is None
    monkeypatch.setattr(guard, "_live_claude_pids", lambda: [11, 22])
    linked = tmp_path / "linked"
    linked.mkdir()
    gitdir = repo / ".git" / "worktrees" / "linked"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/topic\n")
    (linked / ".git").write_text("gitdir: %s\n" % gitdir)
    (config / "sessions" / "22.json").write_text(json.dumps({
        "pid": 22, "sessionId": "peer-session", "cwd": str(linked)}))
    assert guard.checkout_root(str(linked)) == (str(linked), False)
    assert guard.check(payload("git reset --hard", repo)) is None


def test_symlink_and_detached_head(guard, shared_env, tmp_path):
    repo, _ = shared_env
    alias = tmp_path / "alias"
    alias.symlink_to(repo, target_is_directory=True)
    assert guard.check(payload("git reset --hard", alias))
    (repo / ".git" / "HEAD").write_text("a" * 40 + "\n")
    assert guard.check(payload("git reset --hard", repo)) is None


def test_cache_invalidates_changed_metadata(guard, shared_env):
    repo, config = shared_env
    assert guard.check(payload("git reset --hard", repo))
    (config / "sessions" / "22.json").write_text("{}")
    assert guard.check(payload("git reset --hard", repo)) is None


def test_unwritable_cache_still_blocks(guard, shared_env):
    repo, config = shared_env
    (config / "command-center").write_text("not a directory")
    assert guard.check(payload("git reset --hard", repo))


def test_corrupt_oversized_registry_and_cache_are_ignored(guard, shared_env):
    repo, config = shared_env
    cache = config / "command-center" / "git-guard-cache.json"
    cache.parent.mkdir()
    cache.write_text('{"rows": []}')
    for content in ["invalid", "[]", "x" * (guard.MAX_ROW_BYTES + 1)]:
        (config / "sessions" / "22.json").write_text(content)
        assert guard.check(payload("git reset --hard", repo)) is None


def test_process_snapshot_is_batched_and_timeout_is_short(guard, monkeypatch):
    calls = []
    def snapshot(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0,
            "11 claude claude -p\n22 /opt/bin/claude /opt/bin/claude\n"
            "33 python python helper.py --claude\n44 ps ps -A\n")
    monkeypatch.setattr(guard.subprocess, "run", snapshot)
    assert guard._live_claude_pids() == [11, 22]
    assert len(calls) == 1
    assert calls[0][1]["timeout"] <= 0.2
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("ps", 0.2)
    monkeypatch.setattr(guard.subprocess, "run", timeout)
    assert guard._live_claude_pids() == []


def test_native_version_processes_and_candidate_cap(guard, monkeypatch):
    native = Path.home() / ".local" / "share" / "claude" / "versions" / "2.1.144"
    output = "11 %s %s -p\n" % (native, native)
    output += "22 python python helper.py\n"
    output += "33 /unrelated/versions/2.1.144 /unrelated/versions/2.1.144\n"
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 0, output))
    assert guard._live_claude_pids() == [11]
    output = "\n".join("%d claude claude -p" % pid for pid in range(1, 1001))
    assert len(guard._live_claude_pids()) == guard.MAX_ROWS


@pytest.mark.parametrize("raw", ["invalid", "[]", "null", "{}", '{"tool_name":"Read"}',
    '{"tool_name":"Bash","tool_input":null}', "x" * 131073],
    ids=["invalid", "list", "null", "empty", "read", "null-input", "oversized"])
def test_malformed_stdin_exits_silently(raw, tmp_path):
    result = subprocess.run([sys.executable, str(HOOK)], input=raw, text=True,
                            capture_output=True, cwd=tmp_path, timeout=15)
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_real_hook_protocol_keeps_working_files_and_index_untouched(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["CCC_SHARED_CLONE"] = "1"
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], env=env, check=True)
    (tmp_path / "tracked.txt").write_text("staged version\n")
    subprocess.run(["git", "add", "--", "tracked.txt"], cwd=tmp_path, env=env, check=True)
    (tmp_path / "tracked.txt").write_text("another session's edits\n")
    before_index = (tmp_path / ".git" / "index").read_bytes()
    before_file = (tmp_path / "tracked.txt").read_bytes()
    result = subprocess.run([sys.executable, str(HOOK)], env=env, cwd=tmp_path,
        input=json.dumps(payload("git reset --hard", tmp_path)), text=True,
        capture_output=True, timeout=15)
    assert result.returncode == 0 and result.stderr == ""
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "git reset --hard" in decision["permissionDecisionReason"]
    assert "git commit --only" in decision["permissionDecisionReason"]
    assert (tmp_path / ".git" / "index").read_bytes() == before_index
    assert (tmp_path / "tracked.txt").read_bytes() == before_file
