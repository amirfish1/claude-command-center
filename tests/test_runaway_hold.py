"""Runaway Hold / Release (ccc_server/runaway_hold.py + hooks/user-prompt-submit.py).

The hook is run as a real subprocess with HOME pointed at a temp dir, the same
way Claude Code runs it: a held session's prompt gets a block decision, any
other session's prompt passes with no output.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from ccc_server import runaway_hold as rh

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "user-prompt-submit.py"


def run_hook(home, sid):
    env = dict(os.environ, HOME=str(home))
    out = subprocess.run([sys.executable, str(HOOK)], input=json.dumps({"session_id": sid, "prompt": "TICK"}),
                         capture_output=True, text=True, env=env, timeout=10)
    assert out.returncode == 0
    return out.stdout.strip()


def test_hold_release_round_trip(tmp_path):
    f = tmp_path / "holds.json"
    assert not rh.is_held("s1", holds_file=f)
    assert rh.hold("s1", holds_file=f, goal_fn=lambda *a: False)["ok"]
    assert rh.is_held("s1", holds_file=f)
    assert not rh.is_held("s2", holds_file=f)
    out = rh.release("s1", holds_file=f, goal_fn=lambda *a: False)
    assert out["released"] and not out["goal_resumed"]
    assert not rh.is_held("s1", holds_file=f)
    assert rh.release("s1", holds_file=f)["released"] is False


def test_codex_hold_pauses_goal_and_release_resumes_only_what_it_paused(tmp_path):
    f, calls = tmp_path / "holds.json", []

    def goal(sid, action):
        calls.append((sid, action))
        return True

    entry = rh.hold("cx", engine="codex", holds_file=f, goal_fn=goal)["hold"]
    assert entry["goal_paused"] and calls == [("cx", "pause")]
    rh.hold("cx", engine="codex", holds_file=f, goal_fn=goal)  # no second pause
    assert calls == [("cx", "pause")]
    assert rh.release("cx", holds_file=f, goal_fn=goal)["goal_resumed"]
    assert calls[-1] == ("cx", "resume")
    # No goal was active at Hold: Release must not start one.
    calls.clear()
    rh.hold("cx", engine="codex", holds_file=f, goal_fn=lambda *a: False)
    rh.release("cx", holds_file=f, goal_fn=goal)
    assert calls == []


def test_hook_blocks_only_held_sessions(tmp_path):
    state = tmp_path / ".claude" / "command-center"
    assert run_hook(tmp_path, "held") == ""  # no holds file at all
    state.mkdir(parents=True)
    (state / "runaway-holds.json").write_text(json.dumps({"held": {"at": 1, "engine": "claude"}}))
    decision = json.loads(run_hook(tmp_path, "held"))
    assert decision["decision"] == "block" and "Hold" in decision["reason"]
    assert run_hook(tmp_path, "other") == ""
    assert (state / "runaway-hold-blocks.log").read_text().split()[1] == "held"
    (state / "runaway-holds.json").write_text("{not json")
    assert run_hook(tmp_path, "held") == ""  # a broken file never blocks


def test_hook_is_registered_and_synced():
    from ccc_server import config_consent
    import server
    assert ("UserPromptSubmit", "user-prompt-submit.py", 5) in config_consent.CLAUDE_HOOK_SPECS
    assert "user-prompt-submit.py" in server.CCC_HOOK_SCRIPT_NAMES
