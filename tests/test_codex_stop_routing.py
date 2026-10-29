"""Routing decisions for Stop/steer/compact on Codex threads.

Covers the 2026-09-30 incident: Stop reported "not live" for threads hosted
by the worker's app-server, and headless `codex exec` workers were neither
interruptible nor refused. No codex binary is mocked; only decision logic and
real file/command-line parsing are exercised.
"""
import json
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import server  # noqa: E402
from ccc_server import session_graph as sg  # noqa: E402
from ccc_server import watchtower_msg as wm  # noqa: E402
from ccc_server import codex as codex_mod  # noqa: E402

EXEC_CMD = ("/vendor/bin/codex -c model_context_window=1000000 exec "
            "--dangerously-bypass-approvals-and-sandbox --json -- do the thing exec")
APP_CMD = "/vendor/bin/codex -c model_context_window=1000000 app-server --listen stdio://"


def test_subcommand_detection():
    assert sg.codex_command_is_headless_exec(EXEC_CMD)
    assert not sg.codex_command_is_headless_exec(APP_CMD)
    assert not sg.codex_command_is_headless_exec("/x/codex exec-server --remote u")
    assert not sg.codex_command_is_headless_exec("/x/codex")
    # "exec" inside the prompt (after --) must not count for a TUI process
    assert not sg.codex_command_is_headless_exec("/x/codex -- exec")


def test_exec_resume_of_other_session():
    cmd = "/x/codex exec resume --json 11111111-1111-1111-1111-111111111111 hi"
    assert sg._codex_exec_resumes_other_session(cmd, "22222222-2222-2222-2222-222222222222")
    assert not sg._codex_exec_resumes_other_session(cmd, "11111111-1111-1111-1111-111111111111")
    assert not sg._codex_exec_resumes_other_session(EXEC_CMD, "x")


def test_etime_parse():
    assert sg._parse_ps_etime("05:09") == 309
    assert sg._parse_ps_etime("01:02:03") == 3723
    assert sg._parse_ps_etime("2-00:00:10") == 172810
    assert sg._parse_ps_etime("junk") is None


def test_pick_owner_single_and_ambiguous():
    a, b, c = ({"pid": n} for n in (1, 2, 3))
    assert sg.pick_headless_exec_owner([a], None, {}, 1000) is None
    assert sg.pick_headless_exec_owner([a], 900, {1: 100}, 1000) is a
    assert sg.pick_headless_exec_owner([a], 100, {1: 100}, 1000) is None
    assert sg.pick_headless_exec_owner([a, b], None, {}, 1000) is None
    # a started 100s ago, b 5000s ago; session started 120s before "now"
    now = 10000.0
    assert sg.pick_headless_exec_owner([a, b], now - 110, {1: 100, 2: 5000}, now) is a
    # two processes equally close: refuse to guess
    assert sg.pick_headless_exec_owner([a, b], now - 100, {1: 100, 2: 110}, now) is None
    assert sg.pick_headless_exec_owner([a, b, c], now - 100, {1: 4000, 2: 5000}, now) is None


def test_rollout_meta_reads_originator_and_start(tmp_path):
    f = tmp_path / "rollout.jsonl"
    f.write_text(json.dumps({
        "timestamp": "2026-09-30T14:13:00.273Z", "type": "session_meta",
        "payload": {"id": "s", "timestamp": "2026-09-30T14:12:59.535Z",
                    "originator": "codex_exec", "source": "exec"},
    }) + "\n")
    meta = sg.codex_rollout_exec_meta(f)
    assert meta["originator"] == "codex_exec"
    assert abs(meta["started_at"] - 1790777579.535) < 1
    g = tmp_path / "tui.jsonl"
    g.write_text(json.dumps({"timestamp": "2026-09-30T14:13:00Z", "payload": {
        "timestamp": "2026-09-30T14:12:59Z", "originator": "codex_cli_rs"}}) + "\n")
    assert sg.codex_rollout_exec_meta(g)["originator"] == "codex_cli_rs"


def test_interrupt_route_prefers_sigint_for_headless_exec():
    live_exec = {"live": True, "headless_exec": True, "pid": 123}
    assert wm._codex_interrupt_route(live_exec) == "sigint-exec"
    assert wm._codex_interrupt_route({"live": True, "pid": 123}) == "app-server"
    assert wm._codex_interrupt_route({"live": False, "headless_exec": True, "pid": 1}) == "app-server"
    assert wm._codex_interrupt_route({}) == "app-server"


def test_interrupt_does_not_require_local_transport_when_routed(monkeypatch):
    # Dashboard: engine calls route to the worker, which hosts the app-server.
    monkeypatch.setattr(server, "_control_plane_routes_engines", lambda: True)
    assert codex_mod._codex_interrupt_needs_local_transport() is False
    # Worker / routing disabled: the local transport is the only path.
    monkeypatch.setattr(server, "_control_plane_routes_engines", lambda: False)
    assert codex_mod._codex_interrupt_needs_local_transport() is True


def test_headless_exec_refusal_message(monkeypatch):
    monkeypatch.setattr(server, "find_headless_codex_exec_owner",
                        lambda sid, cwd=None: {"pid": 42})
    res = codex_mod._codex_headless_exec_refusal("sid", "steered")
    assert res["ok"] is False and res["code"] == "codex_headless_exec"
    assert "can't be steered" in res["error"] and "Stop" in res["error"]
    monkeypatch.setattr(server, "find_headless_codex_exec_owner", lambda sid, cwd=None: None)
    assert codex_mod._codex_headless_exec_refusal("sid", "steered") is None


def test_single_exec_in_same_directory_needs_matching_rollout_start(tmp_path, monkeypatch):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text(json.dumps({"type": "session_meta", "payload": {
        "timestamp": "2026-09-30T14:12:59Z", "originator": "codex_exec"}}) + "\n")
    started = sg.codex_rollout_exec_meta(rollout)["started_at"]
    monkeypatch.setattr(server, "_resolve_codex_rollout_path", lambda sid: rollout)
    proc = {"pid": 123, "command": EXEC_CMD, "cwd": str(tmp_path)}
    monkeypatch.setattr(sg, "find_live_codex_processes", lambda: [proc])
    monkeypatch.setattr(sg.time, "time", lambda: started + 5000)
    monkeypatch.setattr(sg, "_ps_etimes", lambda pids: {123: 10})
    assert sg.find_headless_codex_exec_owner("native-session", str(tmp_path)) is None
    monkeypatch.setattr(sg, "_ps_etimes", lambda pids: {123: 5000})
    assert sg.find_headless_codex_exec_owner("native-session", str(tmp_path)) is proc


def test_steer_to_wt_app_server_worker_goes_through_wt_not_ccc_app_server(monkeypatch, tmp_path):
    """CODEX-CCC-WT-10: a thread hosted by a WatchTower app-server worker is
    steered via `wt codex-app steer` (the worker's own server); CCC's own
    app-server would be a second writer. Threads without a worker record
    never spawn `wt`."""
    sid = "11111111-2222-7333-8444-555555555555"
    calls = tmp_path / "calls.txt"
    fake_wt = tmp_path / "wt"
    fake_wt.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"open({str(calls)!r}, 'a').write(json.dumps([sys.argv[1:], sys.stdin.read()]) + '\\n')\n"
        "print(json.dumps({'ok': True, 'via': 'wt-app-worker-steer', 'turn_id': 't1'}))\n"
    )
    fake_wt.chmod(0o755)
    monkeypatch.setattr(server, "_WT_HOME", tmp_path)
    monkeypatch.setattr(server, "_wt_cli_path", lambda: str(fake_wt))

    def _must_not_run(*a, **k):
        raise AssertionError("CCC app-server path reached for a WT-hosted thread")
    monkeypatch.setattr(server, "_codex_headless_exec_refusal", _must_not_run)
    monkeypatch.setattr(server, "_resume_session_codex_native_delivery", _must_not_run)

    assert codex_mod._codex_wt_app_worker_delivery(sid, "hi") is None
    assert not calls.exists()

    (tmp_path / "codex-app").mkdir()
    (tmp_path / "codex-app" / f"{sid}.json").write_text("{}")
    res = server.resume_session_codex(sid, "REPORT-TOKEN", steer=True, _native_delivery=True)
    assert res["ok"] is True and res["via"] == "wt-app-worker-steer"
    argv, text = json.loads(calls.read_text().splitlines()[0])
    assert argv == ["codex-app", "steer", sid] and text == "REPORT-TOKEN"
