"""Dead-session toasts: a turn that stopped without finishing.

Fake transcripts on disk; the transcript resolver, boot time and clock are
injected so nothing depends on this machine's sessions.
"""

import json
import os

import pytest

from ccc_server import notify, popups
from ccc_server import session_death as sd

NOW = 1_800_000_000.0
BOOT = NOW - 86400


def _user(text, **extra):
    return {"type": "user", "message": {"role": "user", "content": text}, **extra}


def _tool_use(name="Bash", **inp):
    return {"type": "assistant", "cwd": "/w/app", "message": {
        "role": "assistant", "stop_reason": "tool_use",
        "content": [{"type": "tool_use", "id": "t1", "name": name, "input": inp}]}}


def _tool_result(text="ok"):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": text}]}}


def _reply(text="done"):
    return {"type": "assistant", "message": {
        "role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}}


def _api_error(text):
    return {"type": "assistant", "isApiErrorMessage": True, "message": {
        "role": "assistant", "content": [{"type": "text", "text": text}]}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(sd, "STATE_FILE", tmp_path / "death.json")
    monkeypatch.setattr(sd, "STOPS_FILE", tmp_path / "stops.json")
    monkeypatch.setattr(sd, "WT_WORKERS_FILE", tmp_path / "workers.json")
    monkeypatch.setattr(notify, "STATE_FILE", tmp_path / "notify-state.json")
    monkeypatch.setattr(notify, "LOG_FILE", tmp_path / "notify-log.json")
    monkeypatch.setattr(notify, "_session_identity", lambda sid: {"name": "Steward"})
    monkeypatch.setattr(notify, "_session_is_free_runtime", lambda sid, ident=None: False)
    monkeypatch.setattr(notify, "_session_cost", lambda sid: None)
    monkeypatch.setattr(popups, "APPROVED", frozenset(popups.ALL))
    monkeypatch.setattr(sd._rr, "boot_time", lambda: BOOT)
    paths = {}
    monkeypatch.setattr(sd, "_find_path", lambda sid: paths.get(sid))
    notify.reset_for_tests()
    sd.reset_for_tests()

    def write(sid, records, mtime=NOW - 5):
        p = tmp_path / (sid + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in records))
        os.utime(p, (mtime, mtime))
        paths[sid] = p
        return p

    yield write
    notify.reset_for_tests()


def test_classify():
    assert sd.classify([_user("go"), _reply()]) is None
    cut = sd.classify([_user("go"), _tool_use(command="make", description="Build")])
    assert cut["kind"] == "cut_off" and cut["step"]["detail"] == "Build"
    assert sd.classify([_user("go"), _tool_use(), _tool_result()])["kind"] == "cut_off"
    err = sd.classify([_user("go"), _api_error("API Error: 500 Internal server error")])
    assert err["kind"] == "api_error" and "500" in err["detail"]
    # Usage limits have their own banner and auto-resume.
    assert sd.classify([_user("go"), _api_error("Claude usage limit reached")]) is None
    # Esc, Stop and a rejected tool call are a person's choice, not a death.
    assert sd.classify([_user("go"), _tool_use(), _user("[Request interrupted by user]")]) is None
    assert sd.classify([_user("go"), _tool_use(),
                        _tool_result("[Request interrupted by user for tool use]")]) is None
    assert sd.classify([_user("go"), _tool_use(),
                        _tool_result("The user doesn't want to proceed with this tool use.")]) is None
    assert sd.classify([_user("go"), _reply(), _user("side", isSidechain=True)]) is None


def _tick(prev, cur, now):
    return notify.observe_session_states(prev, cur, publish=lambda item: None, now=now)


def test_a_session_that_dies_mid_turn_toasts_once_after_the_grace(env):
    env("s1", [_user("go"), _tool_use(command="pytest", description="Run tests")])
    working = {"s1": {"state": "working"}}
    _tick(None, working, NOW - 60)
    # Its process went away: no misleading "finished" toast.
    assert _tick(working, {}, NOW) == []
    assert _tick({}, {}, NOW + 30) == []  # still inside the grace period
    got = _tick({}, {}, NOW + sd.GRACE_S + 1)
    assert [i["kind"] for i in got] == ["session_died"]
    assert got[0]["title"] == "Steward stopped mid-turn"
    assert "Run tests" in got[0]["body"] and got[0]["url"] == "/?session=s1"
    assert _tick({}, {}, NOW + 2 * sd.GRACE_S) == []
    # The same incident seen again (say after a reconnect) is not re-toasted.
    _tick({}, working, NOW + 300)
    _tick(working, {}, NOW + 301)
    assert _tick({}, {}, NOW + 301 + sd.GRACE_S + 1) == []


def test_finished_sessions_still_toast_finished(env, monkeypatch):
    env("s1", [_user("go"), _reply()])
    monkeypatch.setattr(notify.time, "monotonic", lambda: 1000.0)
    _tick(None, {"s1": {"state": "working"}}, NOW)
    monkeypatch.setattr(notify.time, "monotonic", lambda: 1030.0)
    got = _tick({"s1": {"state": "working"}}, {"s1": {"state": "idle"}}, NOW + 30)
    assert [i["kind"] for i in got] == ["task"]


def test_api_error_on_a_live_idle_session(env):
    env("s1", [_user("go"), _api_error("API Error: 401 authentication_error")])
    _tick(None, {"s1": {"state": "working"}}, NOW)
    assert _tick({"s1": {"state": "working"}}, {"s1": {"state": "idle"}}, NOW) == []
    got = _tick({"s1": {"state": "idle"}}, {"s1": {"state": "idle"}}, NOW + sd.GRACE_S + 1)
    assert got[0]["title"] == "Steward hit an API error" and "401" in got[0]["body"]


def test_open_turn_on_a_live_idle_process_is_not_a_death(env):
    env("s1", [_user("go"), _tool_use()])
    _tick(None, {"s1": {"state": "working"}}, NOW)
    _tick({"s1": {"state": "working"}}, {"s1": {"state": "idle"}}, NOW)
    assert _tick({}, {"s1": {"state": "idle"}}, NOW + sd.GRACE_S + 1) == []


@pytest.mark.parametrize("case", ["resumed", "back", "stopped", "watchtower", "reboot"])
def test_false_positive_guards(env, case, tmp_path):
    p = env("s1", [_user("go"), _tool_use()], mtime=BOOT - 60 if case == "reboot" else NOW - 5)
    _tick(None, {"s1": {"state": "working"}}, NOW)
    _tick({"s1": {"state": "working"}}, {}, NOW)
    cur = {}
    if case == "resumed":
        with open(p, "a") as fh:
            fh.write(json.dumps(_tool_result()) + "\n")
    elif case == "back":
        cur = {"s1": {"state": "working"}}
    elif case == "stopped":
        sd.note_stop("s1", "force_restart", now=NOW)
    elif case == "watchtower":
        (tmp_path / "workers.json").write_text(json.dumps({"workers": [{"session_id": "s1"}]}))
    assert _tick({}, cur, NOW + sd.GRACE_S + 1) == []


def test_a_death_while_nobody_watched_is_caught_on_reconnect(env):
    env("s1", [_user("go"), _tool_use()])
    _tick(None, {"s1": {"state": "working"}}, NOW)
    _tick({"s1": {"state": "working"}}, {"s1": {"state": "working"}}, NOW)
    sd._STATE = None  # a fresh server process reads the persisted working set
    notify._work_started.clear()
    assert _tick(None, {}, NOW + 10) == []
    got = _tick({}, {}, NOW + 10 + sd.GRACE_S + 1)
    assert [i["session_id"] for i in got] == ["s1"]


def test_unapproved_popup_stays_silent(env, monkeypatch):
    monkeypatch.setattr(popups, "APPROVED", frozenset())
    env("s1", [_user("go"), _tool_use()])
    _tick(None, {"s1": {"state": "working"}}, NOW)
    _tick({"s1": {"state": "working"}}, {}, NOW)
    got = _tick({}, {}, NOW + sd.GRACE_S + 1)
    assert got and got[0].get("held") is True
