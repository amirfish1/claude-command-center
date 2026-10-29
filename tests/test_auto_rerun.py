"""RESIL-9: dead sessions are notify-only unless they opted in to one rerun.

Pins: no opt-in -> nothing happens; a session opt-in resumes that session; a
rule reruns its job command; every rerun goes through the wake guard (a second
death without progress is refused and escalated); re-saving the opt-in gives
a fresh budget; the HTTP endpoint cannot create command rules.
"""
import io
import json
import sys

import pytest

from ccc_server import auto_rerun as ar
from ccc_server import wake_guard as wg

NOW = 1_000_000.0
RULE = {"name": "steward", "cwd": "/w/bym", "title_prefix": "# BYM outreach steward",
        "command": ["systemctl", "--user", "start", "--no-block", "steward.service"]}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.delenv(wg.KILL_SWITCH_ENV, raising=False)
    monkeypatch.setattr(ar, "_log", lambda verb, detail: None)
    return str(tmp_path / "auto-rerun.json")


def _death(tmp_path, sid="s-dead-0001", cwd="/w/bym", title="# BYM outreach steward You are", size=10):
    t = tmp_path / (sid + ".jsonl")
    t.write_text(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
    return {"session_id": sid, "cwd": cwd, "title": title, "size": size, "path": str(t),
            "verdict": {"kind": "cut_off", "step": {"kind": "prompt_unanswered"}}}


def _plan(inc, cfg, now=NOW):
    escalations = []
    req = lambda key, **kw: wg.request(key, notify=escalations.append, **kw)  # noqa: E731
    return ar.plan(inc, path=cfg, now=now, request=req), escalations


def test_no_opt_in_is_notify_only(cfg, tmp_path):
    assert ar.plan(_death(tmp_path), path=cfg, now=NOW) is None
    assert ar.note_for(None) == ""


def test_rule_reruns_the_job_once_then_stops_without_progress(cfg, tmp_path):
    ar.set_rule(RULE, path=cfg)
    first, esc = _plan(_death(tmp_path, "run-aaaa-0001"), cfg)
    assert first["allowed"] and first["key"] == "rule:steward"
    ran = []
    ar.execute(first, _death(tmp_path), run=lambda argv: ran.append(argv) or {"ok": True})
    assert ran == [RULE["command"]]
    assert "rerunning it once" in ar.note_for(first)
    # The rerun (a new session) died too: no progress -> refused, owner told once.
    second, esc = _plan(_death(tmp_path, "run-bbbb-0002"), cfg, NOW + 1800)
    assert (second["allowed"], second["reason"]) == (False, "no_progress")
    assert len(esc) == 1 and "steward" in esc[0]["title"] and "auto-rerun.json" in esc[0]["body"]
    assert ar.execute(second, _death(tmp_path), run=lambda argv: pytest.fail("ran")) is None
    # Re-saving the rule is the human reset.
    ar.set_rule(RULE, path=cfg)
    assert _plan(_death(tmp_path, "run-cccc-0003"), cfg, NOW + 3600)[0]["allowed"]


def test_rule_needs_matching_cwd_and_title(cfg, tmp_path):
    ar.set_rule(RULE, path=cfg)
    assert ar.plan(_death(tmp_path, cwd="/w/other"), path=cfg, now=NOW) is None
    assert ar.plan(_death(tmp_path, title="fix the login bug"), path=cfg, now=NOW) is None


def test_session_opt_in_resumes_that_session(cfg, tmp_path):
    ar.set_session("s-dead-0001", True, path=cfg, now=NOW)
    inc = _death(tmp_path)
    decision, _ = _plan(inc, cfg)
    assert decision["kind"] == "session" and decision["allowed"]
    sent = []
    ar.execute(decision, inc, inject=lambda sid, text: sent.append((sid, text)) or {"ok": True})
    assert sent and sent[0][0] == "s-dead-0001" and "before redoing" in sent[0][1]
    ar.set_session("s-dead-0001", False, path=cfg)
    assert ar.plan(inc, path=cfg, now=NOW + 60) is None


def test_kill_switch_refuses_reruns(cfg, tmp_path, monkeypatch):
    ar.set_rule(RULE, path=cfg)
    monkeypatch.setenv(wg.KILL_SWITCH_ENV, "0")
    decision, esc = _plan(_death(tmp_path), cfg)
    assert (decision["allowed"], decision["reason"]) == (False, "disabled") and esc == []


def test_real_command_runner_reports_failure():
    assert ar._run_command([sys.executable, "-c", "pass"])["ok"]
    bad = ar._run_command([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert not bad["ok"] and "rc=3" in bad["error"]


class _Handler:
    def __init__(self, body):
        raw = json.dumps(body).encode()
        self.headers = {"Content-Length": str(len(raw))}
        self.rfile = io.BytesIO(raw)
        self.sent = None

    def send_json(self, data, status=200):
        self.sent = (status, data)


def test_http_toggles_sessions_but_cannot_create_rules(cfg, monkeypatch):
    monkeypatch.setattr(ar, "_default_path", lambda: cfg)
    h = _Handler({"rule": RULE})
    ar.handle_api_post(h)
    assert h.sent[0] == 400 and ar.load(cfg)["rules"] == []
    h = _Handler({"session_id": "abcdef12-3456", "enabled": True})
    ar.handle_api_post(h)
    assert h.sent[0] == 200 and "abcdef12-3456" in ar.load(cfg)["sessions"]


def test_death_emitter_reruns_an_opted_in_session(monkeypatch, cfg, tmp_path):
    from ccc_server import alert_hook, notify, popups
    monkeypatch.setattr(ar, "_default_path", lambda: cfg)
    ar.set_rule(RULE, path=cfg)
    inc = _death(tmp_path)
    monkeypatch.setattr(popups, "APPROVED", frozenset(popups.ALL))
    monkeypatch.setattr(notify, "_dedupe_hit", lambda item, now: None)
    monkeypatch.setattr(notify._session_death, "track_working", lambda current: None)
    monkeypatch.setattr(notify._session_death, "due", lambda current, now=None: [inc])
    monkeypatch.setattr(notify, "_append_log", lambda item: None)
    monkeypatch.setattr(alert_hook, "send", lambda *a, **k: alert_hook.OFF)
    ran = []
    monkeypatch.setattr(ar, "execute_async", lambda decision, incident: ran.append(decision["key"]))
    items = []
    notify._emit_session_deaths({}, NOW, publish=items.append)
    assert ran == ["rule:steward"]
    assert "rerunning it once" in items[0]["body"] and "Nothing will restart" not in items[0]["body"]
