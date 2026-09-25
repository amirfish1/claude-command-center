"""First-run New Session suggestions: with zero CCC/Claude history the folder
picker must still offer real git repos, and the model strip one row of
installed engines."""
import email
import io
import json
import os
import subprocess
import sys
from pathlib import Path


def _server():
    sys.argv = ["server.py"]
    import server
    return server


def _repo_paths():
    from ccc_server import repo_paths
    return repo_paths


def _make_repo(path, activity):
    (path / ".git" / "logs").mkdir(parents=True)
    for rel in ("HEAD", "index", "logs/HEAD"):
        target = path / ".git" / rel
        target.write_text("x")
        os.utime(target, (activity, activity))


def _fresh_cache(monkeypatch, rp):
    monkeypatch.setitem(rp._WORKSPACE_REPOS_CACHE, "repos", None)
    monkeypatch.setitem(rp._WORKSPACE_REPOS_CACHE, "key", None)


def test_workspace_repos_rank_by_git_activity_and_skip_non_repos(tmp_path, monkeypatch):
    rp = _repo_paths()
    home = tmp_path / "home"
    _make_repo(home / "Apps" / "stale", 1_000_000)
    _make_repo(home / "Apps" / "fresh", 3_000_000)
    _make_repo(home / "projects" / "middle", 2_000_000)
    _make_repo(home / "top-level", 1_500_000)
    _make_repo(home / "Apps" / ".hidden", 9_000_000)
    _make_repo(home / "Apps" / "group" / "too-deep", 9_000_000)
    (home / "Apps" / "not-a-repo").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CCC_WORKSPACE_ROOTS", raising=False)
    _fresh_cache(monkeypatch, rp)

    repos = rp._discover_workspace_repos()

    assert [r["label"] for r in repos] == ["fresh", "middle", "top-level", "stale"]
    assert all(Path(r["path"]).is_absolute() for r in repos)


def test_same_root_under_two_spellings_is_scanned_once(tmp_path, monkeypatch):
    # A symlinked root stands in for macOS's case-insensitive Apps == apps
    # (which a case-sensitive CI filesystem cannot reproduce directly).
    rp = _repo_paths()
    home = tmp_path / "home"
    _make_repo(home / "Apps" / "only", 1_000_000)
    (home / "code").symlink_to(home / "Apps")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CCC_WORKSPACE_ROOTS", raising=False)
    _fresh_cache(monkeypatch, rp)

    assert [r["label"] for r in rp._discover_workspace_repos()] == ["only"]


def test_workspace_roots_env_replaces_conventional_roots(tmp_path, monkeypatch):
    rp = _repo_paths()
    home = tmp_path / "home"
    _make_repo(home / "Apps" / "conventional", 1_000_000)
    _make_repo(tmp_path / "elsewhere" / "custom", 1_000_000)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CCC_WORKSPACE_ROOTS", str(tmp_path / "elsewhere"))
    _fresh_cache(monkeypatch, rp)

    assert [r["label"] for r in rp._discover_workspace_repos()] == ["custom"]


def test_workspace_scan_is_memoised(tmp_path, monkeypatch):
    rp = _repo_paths()
    home = tmp_path / "home"
    _make_repo(home / "Apps" / "one", 1_000_000)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CCC_WORKSPACE_ROOTS", raising=False)
    _fresh_cache(monkeypatch, rp)
    calls = []
    real_scan = rp._scan_workspace_repos
    monkeypatch.setattr(rp, "_scan_workspace_repos", lambda: calls.append(1) or real_scan())

    for _ in range(5):
        rp._discover_workspace_repos()

    assert len(calls) == 1


def _get_json(server, path):
    handler = server.CommandCenterHandler.__new__(server.CommandCenterHandler)
    handler.path = path
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"GET {path} HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    handler.close_connection = True
    handler.headers = email.message_from_string("\r\n")
    handler.rfile = io.BytesIO()
    handler.wfile = io.BytesIO()
    handler._do_GET()
    _, _, body = handler.wfile.getvalue().partition(b"\r\n\r\n")
    return json.loads(body)


def test_repo_list_offers_suggested_repos_instead_of_cwd_fallback(monkeypatch):
    server = _server()
    fallback = {"path": "/srv/ccc-install", "label": "ccc-install", "fallback": True}
    suggested = [{"path": "/work/Apps/shop", "label": "shop", "git_activity": 2.0}]
    monkeypatch.setattr(server, "load_known_repos", lambda: [dict(fallback)])
    monkeypatch.setattr(server, "_known_repo_paths", lambda: [])
    monkeypatch.setattr(server, "_compute_repo_usage_signals", lambda paths: {})
    monkeypatch.setattr(server, "_load_recent_repos", lambda: [])
    monkeypatch.setattr(server, "_discover_workspace_repos", lambda: [dict(s) for s in suggested])

    data = _get_json(server, "/api/repo/list")

    assert data["suggested"] == suggested
    assert data["repos"] == []

    monkeypatch.setattr(server, "_discover_workspace_repos", lambda: [])
    data = _get_json(server, "/api/repo/list")
    assert [r["path"] for r in data["repos"]] == [fallback["path"]], "nothing better found: keep the fallback"


def test_load_known_repos_marks_the_cwd_fallback(tmp_path, monkeypatch):
    server = _server()
    monkeypatch.setattr(server.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(server, "_load_custom_repos", lambda: [])
    monkeypatch.setattr(server, "_load_recent_repos", lambda: [])

    repos = server.load_known_repos()

    assert len(repos) == 1 and repos[0]["fallback"] is True


def test_first_run_model_picks_fit_one_row(monkeypatch, tmp_path):
    server = _server()
    engines = ["claude", "codex", "cursor", "antigravity", "opencode", "hermes", "devin", "grok"]
    inventory = {"engines": [{"engine": e, "installed": True, "kind": "spawn"} for e in engines]}
    defaults = {"engine": "claude", "models": {}, "disabled_engines": []}
    monkeypatch.setattr(server, "MODEL_PICKER_HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(server, "_mine_real_model_history_last_7_days", lambda: [])
    monkeypatch.setattr(server, "_load_spawn_defaults", lambda: defaults)
    monkeypatch.setattr(server, "_engines_installed", lambda: inventory)

    picks = server.get_model_picker_picks()

    assert [p["engine"] for p in picks] == engines[:6]
