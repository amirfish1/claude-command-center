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
