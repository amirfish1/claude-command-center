"""`_self_update()` has to move BOTH halves of the system.

CCC imports `watchtower.queue` in-process and shells out to `wt`, so pulling
CCC's own checkout and stopping there leaves the queue engine on whatever
revision it was installed at — the "bring me up to date" button leaving half
the system stale.

These tests pin the spec's restart order and its one guard:

  1. update WatchTower (scripts/install-watchtower.sh)
  2. `wt stop` && `wt start` — without it the daemon keeps executing the old
     modules it loaded at launch, making step 1 a silent no-op
  3. restart CCC — the caller's os.execvp; here we only assert that the
     process-lifetime capability memos are dropped

  Guard: live `wt workers` => defer the bounce, never kill a worker mid-ticket.

Nothing real is executed: `_git`, `subprocess` and `shutil` are shimmed and
every call is recorded onto one ordered event log, because the *order* is the
behaviour under test.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


class _SubprocessShim:
    """Stands in for `subprocess` inside server.py.

    Only `run` is faked; everything else (TimeoutExpired, CompletedProcess…)
    falls through to the real module so `except subprocess.TimeoutExpired`
    still catches the real class."""

    def __init__(self, handler, log):
        self._handler = handler
        self._log = log
        self.calls = []

    def run(self, cmd, **kw):
        cmd = list(cmd)
        self.calls.append((cmd, kw))
        self._log.append(("run", cmd))
        return self._handler(cmd, kw)

    def __getattr__(self, name):
        return getattr(subprocess, name)


class _ShutilShim:
    def __init__(self, wt_path):
        self._wt = wt_path

    def which(self, name):
        if name == "wt":
            return self._wt
        return shutil.which(name)

    def __getattr__(self, name):
        return getattr(shutil, name)


def _completed(cmd, rc=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, rc, stdout, stderr)


def _install_tree(tmp_path, with_script=True):
    """A minimal stand-in for the CCC clone: a .git marker plus (optionally)
    the shared WatchTower installer."""
    (tmp_path / ".git").mkdir()
    if with_script:
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "install-watchtower.sh").write_text("#!/usr/bin/env bash\n")
    return tmp_path


def _clean_git(log, branch="main"):
    """A `_git` that reports a clean tree on `branch` and succeeds at
    everything, recording each invocation."""
    def _fake(args, cwd, timeout=10):
        args = list(args)
        log.append(("git", args))
        if args[0] == "status":
            return 0, "", ""
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return 0, branch + "\n", ""
        if args[0] == "rev-parse":
            return 0, "deadbeef\n", ""
        return 0, "", ""
    return _fake


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """Wire server.py onto fakes and hand back the shared event log."""
    log = []
    state = {
        "installer_rc": 0,
        "installer_stdout": "watchtower: updated ~/.ccc/watchtower\n",
        "wt_path": "/usr/local/bin/wt",
        "workers_json": "[]",
        "workers_rc": 0,
        "daemon_rc": {"stop": 0, "start": 0},
    }

    def handler(cmd, kw):
        if cmd[:1] == ["bash"]:
            return _completed(cmd, state["installer_rc"], state["installer_stdout"], "boom")
        if cmd[1:3] == ["workers", "--json"]:
            return _completed(cmd, state["workers_rc"], state["workers_json"])
        if cmd[-1] in ("stop", "start"):
            return _completed(cmd, state["daemon_rc"][cmd[-1]])
        return _completed(cmd, 0)

    sub = _SubprocessShim(handler, log)
    _install_tree(tmp_path)
    monkeypatch.setattr(server, "_install_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_git", _clean_git(log))
    monkeypatch.setattr(server, "subprocess", sub)
    monkeypatch.setattr(server, "shutil", _ShutilShim(state["wt_path"]))
    # workers.json is the fallback path; keep it empty unless a test says so.
    monkeypatch.setattr(server, "_wt_read_workers", lambda: [])
    return {"log": log, "sub": sub, "state": state, "dir": tmp_path,
            "monkeypatch": monkeypatch}


def _cmds(log, kind="run"):
    return [c for k, c in log if k == kind]


# --- the order ---------------------------------------------------------------

def test_updates_watchtower_and_bounces_the_daemon_after_the_ccc_pull(harness):
    res = server._self_update()

    assert res["ok"] is True
    assert res["watchtower"]["ok"] is True
    assert res["wt_daemon"]["ok"] is True

    kinds = []
    for kind, cmd in harness["log"]:
        if kind == "git" and cmd[0] == "reset":
            kinds.append("ccc-reset")
        elif kind == "run" and cmd[:1] == ["bash"]:
            kinds.append("wt-update")
        elif kind == "run" and cmd[-1] in ("stop", "start"):
            kinds.append("wt-" + cmd[-1])
    # 1. CCC tree, 2. WatchTower, 3. daemon down, 4. daemon up. CCC's own
    # restart (step 3 of the spec) is the caller's job.
    assert kinds == ["ccc-reset", "wt-update", "wt-stop", "wt-start"]


def test_runs_the_shared_installer_from_the_install_dir(harness):
    server._self_update()

    bash = [c for c in _cmds(harness["log"]) if c[:1] == ["bash"]]
    assert len(bash) == 1
    assert bash[0][1] == str(harness["dir"] / "scripts" / "install-watchtower.sh")

    _, kw = [c for c in harness["sub"].calls if c[0][:1] == ["bash"]][0]
    env = kw["env"]
    # The user asked for this explicitly, so the installer's once-a-day
    # back-off must not swallow it.
    assert env["CCC_WATCHTOWER_FORCE"] == "1"
    # We own the daemon bounce (and its live-worker guard) — the installer
    # must not `wt start` the old code out from under us.
    assert env["CCC_SKIP_WATCHTOWER_DAEMON"] == "1"
    # CCC does `import watchtower.queue` in *this* interpreter.
    assert env["CCC_PYTHON"] == sys.executable


def test_clears_the_process_lifetime_watchtower_capability_memos(harness):
    server._WT_CLI_PATH_CACHE = "/usr/local/bin/wt"
    server._WT_IMPORT_AVAILABLE_CACHE = False

    server._self_update()

    # An update can install WatchTower where there was none, or upgrade one
    # that predates `wt import`; both memos are lifetime-cached otherwise.
    assert server._WT_CLI_PATH_CACHE is None
    assert server._WT_IMPORT_AVAILABLE_CACHE is None


# --- the live-worker guard ---------------------------------------------------

def test_defers_the_daemon_bounce_while_workers_are_mid_ticket(harness):
    harness["state"]["workers_json"] = (
        '[{"worker_id": "w-1", "queue": "CCC", "alive": true},'
        ' {"worker_id": "w-2", "queue": "WT", "alive": false}]'
    )

    res = server._self_update()

    daemon = res["wt_daemon"]
    assert daemon["deferred"] is True
    assert daemon["ok"] is True          # a deferral is not a failed update
    assert "1 WatchTower worker(s)" in daemon["reason"]
    assert daemon["workers"] == [{"worker_id": "w-1", "queue": "CCC"}]
    # The whole point: `wt stop` would take those workers down with the
    # watcher, losing uncommitted work and stranding a claimed ticket.
    assert not [c for c in _cmds(harness["log"]) if c[-1] in ("stop", "start")]
    # WatchTower itself is still refreshed — only the bounce waits.
    assert res["watchtower"]["ok"] is True


def test_dead_workers_do_not_block_the_bounce(harness):
    harness["state"]["workers_json"] = '[{"worker_id": "w-1", "alive": false}]'

    res = server._self_update()

    assert res["wt_daemon"].get("deferred") is None
    assert [c[-1] for c in _cmds(harness["log"]) if c[-1] in ("stop", "start")] == ["stop", "start"]


def test_falls_back_to_workers_json_when_the_wt_cli_is_missing(harness):
    harness["monkeypatch"].setattr(server, "shutil", _ShutilShim(None))
    harness["monkeypatch"].setattr(
        server, "_wt_read_workers",
        lambda: [{"worker_id": "w-9", "queue": "CCC", "alive": True}],
    )

    res = server._self_update()

    # No `wt` on PATH is the normal case (it lands in the user scripts dir),
    # and it must not turn the guard into "no workers, go ahead".
    assert res["wt_daemon"]["deferred"] is True
    assert res["wt_daemon"]["workers"] == [{"worker_id": "w-9", "queue": "CCC"}]


def test_uses_the_module_form_of_wt_when_it_is_not_on_path(harness):
    harness["monkeypatch"].setattr(server, "shutil", _ShutilShim(None))

    server._self_update()

    daemon = [c for c in _cmds(harness["log"]) if c[-1] in ("stop", "start")]
    assert daemon == [
        [sys.executable, "-m", "watchtower.cli", "stop"],
        [sys.executable, "-m", "watchtower.cli", "start"],
    ]


# --- degrading gracefully ----------------------------------------------------

def test_missing_installer_script_degrades_instead_of_failing(tmp_path, monkeypatch):
    log = []
    sub = _SubprocessShim(lambda cmd, kw: _completed(cmd, 0), log)
    _install_tree(tmp_path, with_script=False)
    monkeypatch.setattr(server, "_install_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_git", _clean_git(log))
    monkeypatch.setattr(server, "subprocess", sub)
    monkeypatch.setattr(server, "shutil", _ShutilShim("/usr/local/bin/wt"))
    monkeypatch.setattr(server, "_wt_read_workers", lambda: [])

    res = server._self_update()

    # An install that predates the shared script (or a partial checkout) still
    # gets its CCC update; it just does not get the WatchTower half.
    assert res["ok"] is True
    assert res["watchtower"] == {
        "ok": False, "skipped": True,
        "reason": "scripts/install-watchtower.sh not present in this install",
    }
    assert not [c for c in _cmds(log) if c[:1] == ["bash"]]
    # The daemon bounce is still worth doing: the CCC-managed clone may have
    # been refreshed by some other path since the daemon started.
    assert [c[-1] for c in _cmds(log) if c[-1] in ("stop", "start")] == ["stop", "start"]


def test_a_failing_installer_does_not_fail_the_ccc_update(harness):
    harness["state"]["installer_rc"] = 1

    res = server._self_update()

    assert res["ok"] is True
    assert res["new_sha"] == "deadbeef"
    assert res["watchtower"]["ok"] is False
    assert res["watchtower"]["error"] == "boom"


def test_installer_timeout_is_reported_not_raised(harness):
    def boom(cmd, kw):
        if cmd[:1] == ["bash"]:
            raise subprocess.TimeoutExpired(cmd, 180)
        return _completed(cmd, 0)
    harness["monkeypatch"].setattr(
        server, "subprocess", _SubprocessShim(boom, harness["log"]))

    res = server._self_update()

    assert res["ok"] is True
    assert res["watchtower"]["ok"] is False
    assert "timed out" in res["watchtower"]["error"]


def test_failed_wt_start_is_surfaced(harness):
    harness["state"]["daemon_rc"] = {"stop": 0, "start": 3}

    res = server._self_update()

    assert res["ok"] is True
    assert res["wt_daemon"]["ok"] is False
    assert res["wt_daemon"]["steps"] == {"stop": 0, "start": 3}


def test_a_nonzero_wt_stop_alone_is_tolerated(harness):
    # Stopping an already-stopped daemon is a no-op that can still exit
    # non-zero; `start` is the step that has to land.
    harness["state"]["daemon_rc"] = {"stop": 1, "start": 0}

    res = server._self_update()

    assert res["wt_daemon"]["ok"] is True


# --- the pre-flight guards stay in front of all of it ------------------------

def test_dirty_tree_refuses_before_touching_watchtower(tmp_path, monkeypatch):
    log = []
    sub = _SubprocessShim(lambda cmd, kw: _completed(cmd, 0), log)
    _install_tree(tmp_path)

    def dirty(args, cwd, timeout=10):
        log.append(("git", list(args)))
        if args[0] == "status":
            return 0, " M server.py\n", ""
        return 0, "", ""

    monkeypatch.setattr(server, "_install_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_git", dirty)
    monkeypatch.setattr(server, "subprocess", sub)
    monkeypatch.setattr(server, "shutil", _ShutilShim("/usr/local/bin/wt"))

    res = server._self_update()

    assert res["ok"] is False
    assert res["error"] == "local changes present"
    assert res["paths"] == ["server.py"]
    # Nothing external ran: no installer, no daemon bounce, no worker probe.
    assert sub.calls == []


def test_non_main_branch_refuses_before_touching_watchtower(tmp_path, monkeypatch):
    log = []
    sub = _SubprocessShim(lambda cmd, kw: _completed(cmd, 0), log)
    _install_tree(tmp_path)
    monkeypatch.setattr(server, "_install_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "_git", _clean_git(log, branch="feat/x"))
    monkeypatch.setattr(server, "subprocess", sub)
    monkeypatch.setattr(server, "shutil", _ShutilShim("/usr/local/bin/wt"))

    res = server._self_update()

    assert res["ok"] is False
    assert "not main" in res["error"]
    assert sub.calls == []


def test_not_a_git_clone_refuses_before_touching_watchtower(tmp_path, monkeypatch):
    log = []
    sub = _SubprocessShim(lambda cmd, kw: _completed(cmd, 0), log)
    monkeypatch.setattr(server, "_install_dir", lambda: tmp_path)
    monkeypatch.setattr(server, "subprocess", sub)

    res = server._self_update()

    assert res["ok"] is False
    assert res["error"] == "not a git clone"
    assert sub.calls == []
