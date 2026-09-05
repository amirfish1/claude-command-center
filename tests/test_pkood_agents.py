"""Unit tests for `ccc_server/pkood.py` (pkood agent orchestration).

The interesting logic here is the reconciliation that links a pkood agent to
the Claude session it actually runs: pkood's meta.json never records the
session UUID, so the module recovers it from the remote-control token Claude
prints in its banner, falling back to a cwd + spawn-time window. Getting that
wrong merges two unrelated cards on the board. The module sat at ~14%
coverage.

Everything runs against temporary `~/.pkood` and `~/.claude/projects` trees
(the module's directory constants are monkeypatched), and every `pkood`
subprocess is faked — nothing here shells out for real.
"""
import json
import os
import subprocess
import sys
import types

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from ccc_server import pkood  # noqa: E402

BANNER = (
    "\x1b[38;5;208m ✻ Welcome to Claude Code\x1b[0m\n"
    "\x1b]0;claude\x07"
    "  Remote control: https://claude.ai/code/session_ABC123def\n"
    "  cwd: {cwd}\n"
    "─────────────────────────────────────────\n"
)


@pytest.fixture()
def pkood_home(tmp_path, monkeypatch):
    """Point the module's ~/.pkood constants at a temp tree."""
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    sockets = tmp_path / "sockets"
    for d in (state, logs, sockets):
        d.mkdir()
    monkeypatch.setattr(pkood, "PKOOD_STATE_DIR", state)
    monkeypatch.setattr(pkood, "PKOOD_LOGS_DIR", logs)
    monkeypatch.setattr(pkood, "PKOOD_SOCKETS_DIR", sockets)
    monkeypatch.setattr(pkood, "_PKOOD_LINK_CACHE", {})
    return types.SimpleNamespace(state=state, logs=logs, sockets=sockets, root=tmp_path)


def _write_meta(pkood_home, agent_id, **fields):
    meta = {"agent_id": agent_id, "target_dir": "/repo", "update_ts": 1_700_000_000}
    meta.update(fields)
    (pkood_home.state / f"{agent_id}_meta.json").write_text(json.dumps(meta))
    return meta


def _write_log(pkood_home, agent_id, text):
    (pkood_home.logs / f"{agent_id}.log").write_text(text)


def _stub_core(monkeypatch, projects_root, **overrides):
    stub = types.SimpleNamespace(
        PROJECTS_ROOT=projects_root,
        _encode_project_slug=lambda cwd: cwd.replace("/", "-"),
        _load_conversation_lifecycle_sets=lambda sweep=False: (set(), set()),
        _slugify=lambda text, max_len=30: text.lower().replace(" ", "-")[:max_len],
        _resolve_cwd_context=lambda target: {"cwd": target},
        RepoContextError=RuntimeError,
    )
    for k, v in overrides.items():
        setattr(stub, k, v)
    monkeypatch.setitem(sys.modules, "server", stub)
    return stub


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


# --------------------------------------------------------------------------
# log scraping
# --------------------------------------------------------------------------

def test_strip_ansi_removes_csi_and_osc_sequences():
    assert pkood._strip_ansi("\x1b[1;31mred\x1b[0m") == "red"
    assert pkood._strip_ansi("\x1b]0;a title\x07body") == "body"
    assert pkood._strip_ansi("plain") == "plain"


def test_log_header_is_ansi_stripped_and_byte_limited(pkood_home):
    _write_log(pkood_home, "a1", "\x1b[32m" + "x" * 100)
    assert pkood._pkood_log_header("a1", nbytes=10) == "x" * 5
    assert pkood._pkood_log_header("missing") == ""


def test_bridge_session_id_is_read_from_the_banner(pkood_home):
    _write_log(pkood_home, "a1", BANNER.format(cwd="/repo"))
    assert pkood._pkood_bridge_session_id("a1") == "session_ABC123def"


def test_bridge_session_id_is_none_without_a_log_or_a_url(pkood_home):
    assert pkood._pkood_bridge_session_id("missing") is None
    _write_log(pkood_home, "a1", "no remote control here")
    assert pkood._pkood_bridge_session_id("a1") is None


def test_spawn_time_uses_the_log_file_stat(pkood_home):
    _write_log(pkood_home, "a1", "hi")
    ts = pkood._pkood_log_spawn_time("a1")
    assert ts == pytest.approx((pkood_home.logs / "a1.log").stat().st_mtime)
    assert pkood._pkood_log_spawn_time("missing") is None


def test_log_cwd_returns_the_first_real_directory_in_the_banner(pkood_home, tmp_path):
    repo = tmp_path / "MyRepo"
    repo.mkdir()
    _write_log(pkood_home, "a1", BANNER.format(cwd=str(repo)))
    assert pkood._pkood_log_cwd("a1") == str(repo)


def test_log_cwd_expands_a_tilde_path(pkood_home, tmp_path, monkeypatch):
    repo = tmp_path / "MyRepo"
    repo.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_log(pkood_home, "a1", BANNER.format(cwd="~/MyRepo"))
    assert pkood._pkood_log_cwd("a1") == str(repo)


def test_log_cwd_ignores_urls_and_paths_below_the_banner_rule(pkood_home, tmp_path):
    below = tmp_path / "BelowTheRule"
    below.mkdir()
    _write_log(pkood_home, "a1",
               "  see https://example.com/docs\n"
               "──────────────────────────────\n"
               f"  {below}\n")
    assert pkood._pkood_log_cwd("a1") is None


def test_log_cwd_is_none_when_no_candidate_exists(pkood_home):
    assert pkood._pkood_log_cwd("missing") is None
    _write_log(pkood_home, "a1", "  /nonexistent/path/xyz\n")
    assert pkood._pkood_log_cwd("a1") is None


# --------------------------------------------------------------------------
# _peek_jsonl_meta
# --------------------------------------------------------------------------

def test_peek_reads_cwd_timestamp_and_bridge_id(tmp_path):
    path = _write_jsonl(tmp_path / "s.jsonl", [
        {"type": "system", "subtype": "bridge_status",
         "url": "https://claude.ai/code/session_XYZ"},
        {"type": "user", "cwd": "/repo", "timestamp": "2026-01-01T00:00:00Z"},
    ])
    cwd, ts, bridge = pkood._peek_jsonl_meta(path)
    assert cwd == "/repo"
    assert ts == pytest.approx(1767225600.0)
    assert bridge == "session_XYZ"


def test_peek_skips_blank_and_broken_lines(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('\n{broken\n{"cwd": "/repo"}\n')
    assert pkood._peek_jsonl_meta(path) == ("/repo", None, None)


def test_peek_ignores_an_unparseable_timestamp(tmp_path):
    path = _write_jsonl(tmp_path / "s.jsonl", [{"timestamp": "not-a-date"}])
    assert pkood._peek_jsonl_meta(path) == (None, None, None)


def test_peek_stops_after_max_lines(tmp_path):
    path = _write_jsonl(
        tmp_path / "s.jsonl",
        [{"noise": i} for i in range(50)] + [{"cwd": "/late"}],
    )
    assert pkood._peek_jsonl_meta(path, max_lines=10)[0] is None
    assert pkood._peek_jsonl_meta(path, max_lines=100)[0] == "/late"


def test_peek_on_a_missing_file_is_empty(tmp_path):
    assert pkood._peek_jsonl_meta(tmp_path / "nope.jsonl") == (None, None, None)


# --------------------------------------------------------------------------
# _resolve_claude_session_for_pkood
# --------------------------------------------------------------------------

@pytest.fixture()
def projects_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    return root


def _project_session(projects_root, slug, session_id, records):
    proj = projects_root / slug
    proj.mkdir(exist_ok=True)
    return _write_jsonl(proj / f"{session_id}.jsonl", records)


def test_bridge_id_match_wins_over_everything_else(
        pkood_home, projects_root, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_core(monkeypatch, projects_root)
    _write_log(pkood_home, "a1", BANNER.format(cwd=str(repo)))
    jsonl = _project_session(projects_root, str(repo).replace("/", "-"), "uuid-1", [
        {"subtype": "bridge_status", "url": "https://claude.ai/code/session_ABC123def"},
        {"cwd": str(repo), "timestamp": "2000-01-01T00:00:00Z"},
    ])

    link = pkood._resolve_claude_session_for_pkood("a1")
    assert link == {
        "claude_session_id": "uuid-1",
        "claude_cwd": str(repo),
        "claude_jsonl": str(jsonl),
    }


def test_a_bridge_id_with_no_match_refuses_the_timestamp_fallback(
        pkood_home, projects_root, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_core(monkeypatch, projects_root)
    _write_log(pkood_home, "a1", BANNER.format(cwd=str(repo)))
    spawn_ts = pkood._pkood_log_spawn_time("a1")
    _project_session(projects_root, str(repo).replace("/", "-"), "uuid-1", [
        {"cwd": str(repo), "timestamp": _iso(spawn_ts)},
    ])

    assert pkood._resolve_claude_session_for_pkood("a1") is None, (
        "a fresh claude always emits bridge_status; a time match would be a guess")


def _iso(epoch):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z")


def test_timestamp_fallback_picks_the_closest_session_in_the_same_cwd(
        pkood_home, projects_root, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_core(monkeypatch, projects_root)
    _write_log(pkood_home, "a1", f"  cwd: {repo}\n────────────────────\n")
    spawn_ts = pkood._pkood_log_spawn_time("a1")
    slug = str(repo).replace("/", "-")
    _project_session(projects_root, slug, "uuid-far",
                     [{"cwd": str(repo), "timestamp": _iso(spawn_ts - 30)}])
    _project_session(projects_root, slug, "uuid-near",
                     [{"cwd": str(repo), "timestamp": _iso(spawn_ts - 2)}])
    _project_session(projects_root, slug, "uuid-other-repo",
                     [{"cwd": "/somewhere/else", "timestamp": _iso(spawn_ts)}])
    _project_session(projects_root, slug, "uuid-way-off",
                     [{"cwd": str(repo), "timestamp": _iso(spawn_ts - 3600)}])

    link = pkood._resolve_claude_session_for_pkood("a1")
    assert link["claude_session_id"] == "uuid-near"


def test_no_match_at_all_returns_none(pkood_home, projects_root, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _stub_core(monkeypatch, projects_root)
    _write_log(pkood_home, "a1", f"  cwd: {repo}\n────────────────────\n")
    spawn_ts = pkood._pkood_log_spawn_time("a1")
    _project_session(projects_root, str(repo).replace("/", "-"), "uuid-old",
                     [{"cwd": str(repo), "timestamp": _iso(spawn_ts - 3600)}])
    assert pkood._resolve_claude_session_for_pkood("a1") is None


def test_unknown_cwd_scans_every_project_with_a_tighter_window(
        pkood_home, projects_root, monkeypatch):
    _stub_core(monkeypatch, projects_root)
    _write_log(pkood_home, "a1", "no banner here")
    spawn_ts = pkood._pkood_log_spawn_time("a1")
    _project_session(projects_root, "-some-repo", "uuid-just-outside",
                     [{"cwd": "/some/repo", "timestamp": _iso(spawn_ts - 30)}])
    assert pkood._resolve_claude_session_for_pkood("a1") is None, (
        "±15s window applies when the pkood log didn't reveal a cwd")

    _project_session(projects_root, "-some-repo", "uuid-inside",
                     [{"cwd": "/some/repo", "timestamp": _iso(spawn_ts - 5)}])
    link = pkood._resolve_claude_session_for_pkood("a1")
    assert link["claude_session_id"] == "uuid-inside"
    assert link["claude_cwd"] == "/some/repo"


# --------------------------------------------------------------------------
# _cached_claude_session_for_pkood
# --------------------------------------------------------------------------

def test_link_lookup_is_cached_until_the_meta_file_changes(
        pkood_home, projects_root, monkeypatch):
    _stub_core(monkeypatch, projects_root)
    calls = []
    monkeypatch.setattr(pkood, "_resolve_claude_session_for_pkood",
                        lambda agent_id: calls.append(agent_id) or {"claude_session_id": "u"})
    _write_meta(pkood_home, "a1")

    assert pkood._cached_claude_session_for_pkood("a1") == {"claude_session_id": "u"}
    assert pkood._cached_claude_session_for_pkood("a1") == {"claude_session_id": "u"}
    assert len(calls) == 1

    meta = pkood_home.state / "a1_meta.json"
    os.utime(meta, (0, 0))
    assert pkood._cached_claude_session_for_pkood("a1") == {"claude_session_id": "u"}
    assert len(calls) == 2, "an mtime change must invalidate the cache"


def test_link_lookup_expires_after_the_ttl(pkood_home, projects_root, monkeypatch):
    _stub_core(monkeypatch, projects_root)
    calls = []
    monkeypatch.setattr(pkood, "_resolve_claude_session_for_pkood",
                        lambda agent_id: calls.append(agent_id) or None)
    _write_meta(pkood_home, "a1")

    assert pkood._cached_claude_session_for_pkood("a1") is None
    pkood._PKOOD_LINK_CACHE["a1"]["cached_at"] -= pkood._PKOOD_LINK_TTL + 1
    assert pkood._cached_claude_session_for_pkood("a1") is None
    assert len(calls) == 2


def test_link_lookup_works_without_a_meta_file(pkood_home, projects_root, monkeypatch):
    _stub_core(monkeypatch, projects_root)
    monkeypatch.setattr(pkood, "_resolve_claude_session_for_pkood", lambda agent_id: None)
    assert pkood._cached_claude_session_for_pkood("ghost") is None
    assert pkood._PKOOD_LINK_CACHE["ghost"]["meta_mtime"] == 0.0


# --------------------------------------------------------------------------
# find_pkood_agents
# --------------------------------------------------------------------------

def test_no_state_dir_means_no_agents(monkeypatch, tmp_path):
    monkeypatch.setattr(pkood, "PKOOD_STATE_DIR", tmp_path / "absent")
    assert pkood.find_pkood_agents() == []


def test_agents_are_shaped_as_cards_and_sorted_newest_first(
        pkood_home, projects_root, monkeypatch):
    _stub_core(monkeypatch, projects_root)
    monkeypatch.setattr(pkood, "_cached_claude_session_for_pkood", lambda a: None)
    _write_meta(pkood_home, "older", update_ts=1_000, command="first task",
                status="IDLE", last_output_snippet="x" * 300)
    _write_meta(pkood_home, "newer", update_ts=2_000, command="second task",
                status="IDLE")

    agents = pkood.find_pkood_agents()
    assert [a["display_name"] for a in agents] == ["newer", "older"]
    card = agents[1]
    assert card["id"] == card["session_id"] == "pkood-older"
    assert card["source"] == "pkood"
    assert card["first_message"] == "first task"
    assert len(card["last_prompt"]) == 200
    assert card["pkood_status"] == "IDLE" and card["is_live"] is True
    assert card["claude_session_id"] is None


def test_a_running_agent_without_a_live_tmux_socket_is_dead(
        pkood_home, projects_root, monkeypatch):
    _stub_core(monkeypatch, projects_root)
    monkeypatch.setattr(pkood, "_cached_claude_session_for_pkood", lambda a: None)
    _write_meta(pkood_home, "a1", status="RUNNING")

    agent = pkood.find_pkood_agents()[0]
    assert agent["pkood_status"] == "DEAD" and agent["is_live"] is False


@pytest.mark.parametrize("probe,expected", [
    (lambda *a, **kw: types.SimpleNamespace(returncode=0), "RUNNING"),
    (lambda *a, **kw: types.SimpleNamespace(returncode=1), "DEAD"),
    (lambda *a, **kw: (_ for _ in ()).throw(subprocess.TimeoutExpired("tmux", 2)), "DEAD"),
    (lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError()), "DEAD"),
])
def test_tmux_probe_decides_liveness_for_running_agents(
        pkood_home, projects_root, monkeypatch, probe, expected):
    _stub_core(monkeypatch, projects_root)
    monkeypatch.setattr(pkood, "_cached_claude_session_for_pkood", lambda a: None)
    monkeypatch.setattr(pkood.subprocess, "run", probe)
    _write_meta(pkood_home, "a1", status="RUNNING")
    (pkood_home.sockets / "a1.sock").touch()

    assert pkood.find_pkood_agents()[0]["pkood_status"] == expected


def test_unreadable_meta_files_are_skipped(pkood_home, projects_root, monkeypatch):
    _stub_core(monkeypatch, projects_root)
    monkeypatch.setattr(pkood, "_cached_claude_session_for_pkood", lambda a: None)
    (pkood_home.state / "broken_meta.json").write_text("{not json")
    _write_meta(pkood_home, "good", status="IDLE")

    assert [a["display_name"] for a in pkood.find_pkood_agents()] == ["good"]


def test_lifecycle_sets_mark_pkood_cards(pkood_home, projects_root, monkeypatch):
    _stub_core(monkeypatch, projects_root,
               _load_conversation_lifecycle_sets=lambda sweep=False: (
                   {"pkood-a1"}, {"pkood-a2"}))
    monkeypatch.setattr(pkood, "_cached_claude_session_for_pkood", lambda a: None)
    _write_meta(pkood_home, "a1", status="IDLE", update_ts=2)
    _write_meta(pkood_home, "a2", status="IDLE", update_ts=1)

    cards = {a["id"]: a for a in pkood.find_pkood_agents()}
    assert cards["pkood-a1"]["archived"] is True
    assert cards["pkood-a2"]["trashed"] is True


def test_a_resolved_link_supplies_the_session_id_and_cwd(
        pkood_home, projects_root, monkeypatch, tmp_path):
    repo = tmp_path / "resolved"
    repo.mkdir()
    _stub_core(monkeypatch, projects_root)
    monkeypatch.setattr(pkood, "_cached_claude_session_for_pkood", lambda a: {
        "claude_session_id": "uuid-1",
        "claude_cwd": str(repo),
        "claude_jsonl": "/tmp/uuid-1.jsonl",
    })
    _write_meta(pkood_home, "a1", status="IDLE", target_dir="")

    card = pkood.find_pkood_agents()[0]
    assert card["claude_session_id"] == "uuid-1"
    assert card["claude_jsonl"] == "/tmp/uuid-1.jsonl"
    assert card["session_cwd"] == str(repo) and card["session_cwd_exists"] is True


# --------------------------------------------------------------------------
# CLI wrappers
# --------------------------------------------------------------------------

def _fake_run(monkeypatch, calls, returncode=0, stdout="", stderr=""):
    def run(cmd, **kw):
        calls.append((cmd, kw))
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(pkood.subprocess, "run", run)
    return calls


def test_spawn_builds_the_command_and_derives_an_agent_id(monkeypatch, projects_root):
    _stub_core(monkeypatch, projects_root)
    calls = _fake_run(monkeypatch, [])
    assert pkood.pkood_spawn("Fix The Parser", repo_path="/repo") == {
        "ok": True, "agent_id": "fix-the-parser"}
    assert calls[0][0] == [
        pkood.PKOOD_BIN, "spawn", "--name", "fix-the-parser", "--dir", "/repo",
        "Fix The Parser",
    ]


def test_spawn_requires_a_target_directory(monkeypatch, projects_root):
    _stub_core(monkeypatch, projects_root)
    assert pkood.pkood_spawn("prompt") == {
        "ok": False, "error": "repo_path or target_dir is required"}


def test_spawn_surfaces_a_repo_context_error(monkeypatch, projects_root):
    class _RepoContextError(Exception):
        def as_payload(self):
            return {"ok": False, "error": "repo is not trusted"}

    def _boom(target):
        raise _RepoContextError()

    _stub_core(monkeypatch, projects_root,
               RepoContextError=_RepoContextError, _resolve_cwd_context=_boom)
    assert pkood.pkood_spawn("prompt", repo_path="/repo") == {
        "ok": False, "error": "repo is not trusted"}


def test_spawn_reports_cli_failure_output(monkeypatch, projects_root):
    _stub_core(monkeypatch, projects_root)
    _fake_run(monkeypatch, [], returncode=1, stderr="  boom\n")
    assert pkood.pkood_spawn("p", agent_id="a1", target_dir="/repo") == {
        "ok": False, "error": "boom"}


@pytest.mark.parametrize("fn,expected_cmd", [
    (lambda: pkood.pkood_inject("a1", "hello"), ["inject", "a1", "hello"]),
    (lambda: pkood.pkood_kill("a1"), ["kill", "a1"]),
    (lambda: pkood.pkood_tail("a1"), ["tail", "a1"]),
])
def test_cli_wrappers_pass_through_success(monkeypatch, fn, expected_cmd):
    calls = _fake_run(monkeypatch, [], stdout="recent output")
    result = fn()
    assert result["ok"] is True
    assert calls[0][0] == [pkood.PKOOD_BIN] + expected_cmd
    if expected_cmd[0] == "tail":
        assert result["output"] == "recent output"


@pytest.mark.parametrize("fn", [
    lambda: pkood.pkood_inject("a1", "hi"),
    lambda: pkood.pkood_kill("a1"),
    lambda: pkood.pkood_tail("a1"),
])
@pytest.mark.parametrize("failure,expected", [
    ({"returncode": 2, "stdout": "stdout detail"}, "stdout detail"),
    ({"returncode": 2}, "unknown error"),
])
def test_cli_wrappers_report_nonzero_exits(monkeypatch, fn, failure, expected):
    _fake_run(monkeypatch, [], **failure)
    assert fn() == {"ok": False, "error": expected}


@pytest.mark.parametrize("exc,fragment", [
    (subprocess.TimeoutExpired("pkood", 10), "timed out"),
    (FileNotFoundError(), "pkood not found on PATH"),
])
@pytest.mark.parametrize("fn", [
    lambda: pkood.pkood_inject("a1", "hi"),
    lambda: pkood.pkood_kill("a1"),
    lambda: pkood.pkood_tail("a1"),
])
def test_cli_wrappers_report_timeouts_and_missing_binaries(
        monkeypatch, fn, exc, fragment):
    def run(cmd, **kw):
        raise exc
    monkeypatch.setattr(pkood.subprocess, "run", run)
    result = fn()
    assert result["ok"] is False and fragment in result["error"]


@pytest.mark.parametrize("exc,fragment", [
    (subprocess.TimeoutExpired("pkood", 10), "pkood spawn timed out"),
    (FileNotFoundError(), "pkood not found on PATH"),
])
def test_spawn_reports_timeouts_and_missing_binaries(
        monkeypatch, projects_root, exc, fragment):
    _stub_core(monkeypatch, projects_root)

    def run(cmd, **kw):
        raise exc
    monkeypatch.setattr(pkood.subprocess, "run", run)
    assert pkood.pkood_spawn("p", target_dir="/repo") == {"ok": False, "error": fragment}
