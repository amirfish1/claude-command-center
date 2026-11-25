"""CCC-680: a slow spawn records where it waited before the engine submit."""

import threading
import time
from pathlib import Path

from ccc_server import spawn_phases

ROOT = Path(__file__).resolve().parent.parent


def teardown_function(_fn):
    spawn_phases.end()


def test_steps_land_on_timeline_relative_to_click():
    t0 = time.time() - 5.0
    spawn_phases.begin(t0 + 1.0)
    spawn_phases.mark("validated")
    spawn_phases.mark(spawn_phases.ENGINE_SUBMIT)
    written = {}

    def mark(sid, name, value):
        written[name] = value
        return True

    assert spawn_phases.record_timeline("sid-1", lambda sid: {"t0": t0}, mark)
    assert set(written) == {
        "server_request_received", "server_validated", "server_engine_submit",
    }
    assert 999 <= written["server_request_received"] <= 1001
    assert written["server_validated"] >= written["server_request_received"]
    assert written["server_engine_submit"] >= written["server_validated"]


def test_first_occurrence_of_a_step_wins():
    spawn_phases.begin()
    spawn_phases.mark("validated")
    first = spawn_phases.steps()["validated"]
    time.sleep(0.01)
    spawn_phases.mark("validated")
    assert spawn_phases.steps()["validated"] == first


def test_watchdog_reports_last_step_while_still_waiting():
    fired = threading.Event()
    seen = {}

    def on_stall(**kw):
        seen.update(kw)
        fired.set()

    spawn_phases.begin(on_stall=on_stall, stall_after_s=0.05)
    spawn_phases.mark("shipped_check")
    assert fired.wait(2.0)
    assert seen["last_step"] == "shipped_check"
    assert seen["waited_s"] >= 0.05
    assert seen["submitted"]() is False


def test_watchdog_quiet_after_submit_or_end():
    fired = threading.Event()
    spawn_phases.begin(on_stall=lambda **kw: fired.set(), stall_after_s=0.05)
    spawn_phases.mark(spawn_phases.ENGINE_SUBMIT)
    spawn_phases.end()
    spawn_phases.begin(on_stall=lambda **kw: fired.set(), stall_after_s=0.05)
    spawn_phases.end()
    assert not fired.wait(0.2)


def test_no_tracking_outside_a_spawn_request():
    spawn_phases.end()
    spawn_phases.mark("validated")
    assert spawn_phases.steps() == {}
    assert spawn_phases.record_timeline("sid", lambda sid: {"t0": 1.0}, lambda *a: True) is False


def test_spawn_path_is_wired():
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    handler = server[server.index('elif path == "/api/sessions/spawn":'):]
    handler = handler[:handler.index('elif path == "/api/sessions/spawn-codex":')]
    assert "_spawn_phases.begin(" in handler
    assert 'on_stall=_spawn_presubmit_stall' in handler
    assert '_spawn_phases.mark("validated")' in handler
    # A person waiting on the composer does not pay the ~1.5 s shipped check.
    assert 'spawned_via != "ui"' in handler
    assert "_spawn_phases.record_timeline(" in handler
    assert "_spawn_phases.end()" in server
    engines = (ROOT / "ccc_server" / "engines.py").read_text(encoding="utf-8")
    spawn = engines[engines.index("def spawn_session(prompt"):]
    submit_at = spawn.index("_spawn_phases.mark(_spawn_phases.ENGINE_SUBMIT)")
    assert submit_at < spawn.index("_route_claude_call_with_kwarg_fallback(")


def test_composer_marks_and_bounds_pre_post_steps():
    js = (ROOT / "static" / "conversation-services.js").read_text(encoding="utf-8")
    fn = js[js.index("const spawnAskedAt = Date.now();"):]
    fn = fn[:fn.index("spawnStatsMark)(data.session_id, 'response_received')")]
    # Background reads are aborted before the first await, not after.
    assert fn.index("abortBackgroundApiReadsForSpawn") < fn.index("await settleRepoGuessBeforeSend")
    assert "spawnPhaseAt.repo_guess_settled" in fn
    assert "spawnPhaseAt.cwd_checked" in fn
    assert "spawnPhaseAt.request_sent = Date.now()" in fn
    # The folder check is raced against a timeout like the repo guess.
    assert "spawnCwdIsMissing(launchCwd),\n      new Promise(resolve => setTimeout(() => resolve(false), 800))" in fn


def _load_check():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "check_spawn_presubmit", ROOT / "scripts" / "check-spawn-presubmit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_watch_check_verdicts():
    check = _load_check()

    def row(t0, presubmit, network=50.0, engine="claude", client=True):
        marks = {"server_request_received": 100 + network,
                 "server_engine_submit": 100 + network + presubmit}
        if client:
            marks["client_request_sent"] = 100.0
        return {"engine": engine, "t0": t0, "marks": marks}

    assert check.analyse({}, 0)["status"] == "pending"
    fast = {f"s{i}": row(10 + i, 20) for i in range(5)}
    fast["api"] = row(30, 9000, client=False)       # scripted launch ignored
    fast["old"] = row(1, 9000)                        # before deploy ignored
    result = check.analyse(fast, 5)
    assert result["status"] == "pass" and result["real_data"]
    assert result["evidence"]["launches"] == 5
    slow = dict(fast, bad=row(40, 4000))
    assert check.analyse(slow, 5)["status"] == "fail"
    # A slow phone link is reported but is not the dashboard's stall.
    net = dict(fast, net=row(40, 20, network=12000))
    result = check.analyse(net, 5)
    assert result["status"] == "pass"
    assert result["evidence"]["click_to_submit_over_limit"][0]["network_ms"] == 12000
