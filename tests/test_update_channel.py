"""Update channel: managed installs track release tags, everything else main.

A managed install (the real, non-symlink clone at ~/.ccc/claude-command-center)
fast-forwards to the newest vX.Y.Z tag; a dev clone or a symlinked ~/.ccc
install keeps tracking origin/main. Both restart-pull and the update pill use
the same target, and the idle auto-update only acts when CCC is idle and the
opt-in env is set.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402

TAGS = "v1.2.0\nv1.10.0\nv1.9.3\nv2.0.0-rc1\nvnext\nv1.10.0-beta\n"


def _fake_git(log, tags=TAGS, ancestor=False, branch="main"):
    def _git(args, cwd, timeout=10):
        args = list(args)
        log.append(args)
        if args[0] == "status":
            return 0, "", ""
        if args[0] == "tag":
            return 0, tags, ""
        if args[0] == "merge-base":
            return (0 if ancestor else 1), "", ""
        if args[0] == "rev-parse" and "--abbrev-ref" in args:
            return 0, branch + "\n", ""
        if args[0] == "rev-parse":
            return 0, "abc123\n", ""
        return 0, "", ""
    return _git


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: h))
    monkeypatch.delenv("CCC_UPDATE_CHANNEL", raising=False)
    monkeypatch.delenv("CCC_RESTART_PULL", raising=False)
    monkeypatch.delenv("CCC_AUTO_UPDATE", raising=False)
    return h


@pytest.fixture
def managed(home, monkeypatch):
    d = home / ".ccc" / "claude-command-center"
    (d / ".git").mkdir(parents=True)
    monkeypatch.setattr(server, "_install_dir", lambda: d)
    return d


# ── channel detection ─────────────────────────────────────────────────────

def test_real_managed_clone_is_release_channel(managed):
    assert server._is_managed_install() is True
    assert server._update_channel() == "release"


def test_symlinked_managed_path_is_main_channel(home, tmp_path, monkeypatch):
    dev = tmp_path / "dev-clone"
    (dev / ".git").mkdir(parents=True)
    (home / ".ccc").mkdir()
    (home / ".ccc" / "claude-command-center").symlink_to(dev)
    monkeypatch.setattr(server, "_install_dir", lambda: dev)
    assert server._is_managed_install() is False
    assert server._update_channel() == "main"


def test_dev_clone_is_main_channel(home, tmp_path, monkeypatch):
    dev = tmp_path / "elsewhere"
    dev.mkdir()
    monkeypatch.setattr(server, "_install_dir", lambda: dev)
    assert server._update_channel() == "main"


def test_env_overrides_detection(managed, monkeypatch):
    monkeypatch.setenv("CCC_UPDATE_CHANNEL", "main")
    assert server._update_channel() == "main"
    monkeypatch.setenv("CCC_UPDATE_CHANNEL", "release")
    monkeypatch.setattr(server, "_install_dir", lambda: Path("/nowhere"))
    assert server._update_channel() == "release"
    monkeypatch.setenv("CCC_UPDATE_CHANNEL", "bogus")
    assert server._update_channel() == "main"


def test_newest_release_tag_is_semver_max_ignoring_prereleases(monkeypatch):
    monkeypatch.setattr(server, "_git", _fake_git([]))
    assert server._newest_release_tag(Path(".")) == "v1.10.0"
    monkeypatch.setattr(server, "_git", _fake_git([], tags="vnext\n"))
    assert server._newest_release_tag(Path(".")) is None


# ── the shared target in restart-pull and self-update ────────────────────

def test_restart_pull_fast_forwards_to_newest_tag(managed, monkeypatch):
    log = []
    monkeypatch.setattr(server, "_git", _fake_git(log))
    res = server._pull_before_restart()
    assert res["ok"] is True
    assert res["channel"] == "release" and res["target"] == "v1.10.0"
    assert ["fetch", "origin", "--tags", "--quiet"] in log
    assert ["merge", "--ff-only", "--quiet", "v1.10.0"] in log
    assert not any(a[0] in ("reset", "checkout") for a in log)


def test_self_update_fast_forwards_to_newest_tag(managed, monkeypatch):
    log = []
    monkeypatch.setattr(server, "_git", _fake_git(log))
    monkeypatch.setattr(server, "_restart_stale_worker", lambda: {"restarted": False})
    monkeypatch.setattr(server, "_update_watchtower", lambda: {"ok": True})
    monkeypatch.setattr(server, "_wt_live_workers", lambda: [])
    monkeypatch.setattr(server, "_restart_wt_daemon", lambda: {"ok": True})
    res = server._self_update()
    assert res["ok"] is True and res["target"] == "v1.10.0"
    assert ["merge", "--ff-only", "--quiet", "v1.10.0"] in log


def test_release_channel_without_tags_does_not_merge(managed, monkeypatch):
    log = []
    monkeypatch.setattr(server, "_git", _fake_git(log, tags=""))
    res = server._pull_before_restart()
    assert res["ok"] is False and "no vX.Y.Z" in res["error"]
    assert not any(a[0] == "merge" for a in log)


def test_main_channel_still_targets_origin_main(home, tmp_path, monkeypatch):
    dev = tmp_path / "dev"
    (dev / ".git").mkdir(parents=True)
    monkeypatch.setattr(server, "_install_dir", lambda: dev)
    log = []
    monkeypatch.setattr(server, "_git", _fake_git(log))
    res = server._pull_before_restart()
    assert res["target"] == "origin/main"
    assert ["fetch", "origin", "--quiet"] in log
    assert not any(a[0] == "tag" for a in log)


# ── idle auto-update ──────────────────────────────────────────────────────

@pytest.fixture
def auto(managed, monkeypatch):
    events = []
    monkeypatch.setenv("CCC_AUTO_UPDATE", "1")
    monkeypatch.setattr(server, "_log_activity",
                        lambda c, v, d: events.append((c, v, d)))
    monkeypatch.setattr(server, "_dashboard_owned_active_executions", lambda: [])
    monkeypatch.setattr(server, "_control_plane_request",
                        lambda *a, **k: {"ok": True, "active": 0, "queued": 0, "uncertain": 0,
                                                 "codex_live_turns": 0})
    monkeypatch.setattr(server, "_wt_live_workers", lambda: [])
    monkeypatch.setattr(server, "_load_last_interactions", lambda: {})
    monkeypatch.setattr(server, "_safe_worker_restart_precheck",
                        lambda **k: (True, None, {}, {}))
    calls = {"self_update": 0, "restart": 0}

    def _su():
        calls["self_update"] += 1
        return {"ok": True, "new_sha": "abc123"}
    monkeypatch.setattr(server, "_self_update", _su)
    monkeypatch.setattr(server, "_schedule_restart",
                        lambda *a, **k: calls.__setitem__("restart", calls["restart"] + 1))
    return {"events": events, "calls": calls}


def test_auto_update_is_on_by_default():
    assert server._auto_update_enabled() is True


def test_auto_update_opt_out(managed, monkeypatch):
    monkeypatch.setenv("CCC_AUTO_UPDATE", "0")
    log = []
    monkeypatch.setattr(server, "_git", _fake_git(log))
    assert server._auto_update_tick() == {"action": "skip", "reason": "disabled"}
    assert log == []
    assert server._start_auto_update_thread() is False


def test_auto_update_applies_when_idle(auto, monkeypatch):
    monkeypatch.setattr(server, "_git", _fake_git([]))
    res = server._auto_update_tick()
    assert res["action"] == "applied" and res["tag"] == "v1.10.0"
    assert auto["calls"] == {"self_update": 1, "restart": 1}
    assert [e[1] for e in auto["events"]] == ["apply", "restart"]


def test_auto_update_noop_when_already_at_tag(auto, monkeypatch):
    monkeypatch.setattr(server, "_git", _fake_git([], ancestor=True))
    assert server._auto_update_tick()["action"] == "none"
    assert auto["calls"]["self_update"] == 0
    assert auto["events"][0][1] == "check"


@pytest.mark.parametrize("busy_patch", [
    ("_dashboard_owned_active_executions", lambda: [{"engine": "claude"}]),
    ("_control_plane_request", lambda *a, **k: {"ok": True, "active": 1}),
    ("_wt_live_workers", lambda: [{"worker_id": "w1", "queue": "Q"}]),
    ("_load_last_interactions", lambda: {"sid": server.time.time() - 60}),
])
def test_auto_update_skips_when_busy(auto, monkeypatch, busy_patch):
    monkeypatch.setattr(server, "_git", _fake_git([]))
    monkeypatch.setattr(server, busy_patch[0], busy_patch[1])
    res = server._auto_update_tick()
    assert res["action"] == "busy" and res["reasons"]
    assert auto["calls"] == {"self_update": 0, "restart": 0}
    assert auto["events"][0][1] == "skip"


def test_old_interaction_does_not_block(auto, monkeypatch):
    monkeypatch.setattr(server, "_git", _fake_git([]))
    monkeypatch.setattr(server, "_load_last_interactions",
                        lambda: {"sid": server.time.time() - 3600})
    assert server._auto_update_tick()["action"] == "applied"


def test_auto_update_never_runs_on_main_channel(auto, monkeypatch):
    monkeypatch.setenv("CCC_UPDATE_CHANNEL", "main")
    log = []
    monkeypatch.setattr(server, "_git", _fake_git(log))
    assert server._auto_update_tick()["action"] == "skip"
    assert log == []


def test_failed_self_update_does_not_restart(auto, monkeypatch):
    monkeypatch.setattr(server, "_git", _fake_git([]))
    monkeypatch.setattr(server, "_self_update",
                        lambda: {"ok": False, "error": "local changes present"})
    res = server._auto_update_tick()
    assert res["action"] == "error"
    assert auto["calls"]["restart"] == 0


def test_refused_restart_precheck_skips_the_update(auto, monkeypatch):
    monkeypatch.setattr(server, "_git", _fake_git([]))
    monkeypatch.setattr(server, "_safe_worker_restart_precheck",
                        lambda **k: (False, {"error": "handoff failed"}, None, None))
    res = server._auto_update_tick()
    assert res["action"] == "busy"
    assert auto["calls"] == {"self_update": 0, "restart": 0}
