"""RESIL-5: one ledger caps automatic session wakes and stops a wake loop.

The failure being guarded: CCC misjudges a session as dead/stuck and wakes it
again and again (limit-reset "continue", stuck-inject recovery), burning
tokens. These tests pin the cap, the progress rule, escalation and reset.
"""
import json

import pytest

from ccc_server import wake_guard as wg

NOW = 1_000_000.0


def _assistant(text="done"):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _tool_use():
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}


def _write(path, *records):
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.delenv(wg.KILL_SWITCH_ENV, raising=False)
    sent = []
    transcript = tmp_path / "s1.jsonl"
    _write(transcript, {"type": "user", "message": {"content": "go"}}, _assistant())
    ledger = str(tmp_path / "session-wakes.json")

    def req(now=NOW, src="inject-recovery"):
        return wg.request("s1", source=src, incident="i1", transcript_path=str(transcript),
                          now=now, path=ledger, notify=sent.append)
    return req, transcript, ledger, sent


def test_first_wake_is_allowed_and_recorded(env):
    req, _, ledger, sent = env
    assert req() == (True, "ok")
    assert len(wg.get("s1", ledger)["wakes"]) == 1
    assert sent == []


def test_second_wake_without_progress_is_refused_and_escalates_once(env):
    req, _, ledger, sent = env
    req()
    assert req(NOW + 60) == (False, "no_progress")
    assert req(NOW + 120) == (False, "escalated")
    assert len(sent) == 1 and sent[0]["kind"] == "needs_input"
    assert "s1" in sent[0]["url"]


def test_growth_with_an_open_turn_is_not_progress(env):
    req, transcript, _, _ = env
    req()
    _write(transcript, _tool_use())
    assert req(NOW + 60) == (False, "no_progress")


def test_progress_allows_a_second_wake_then_the_cap_holds(env):
    req, transcript, _, sent = env
    req()
    _write(transcript, {"type": "user", "message": {"content": "continue"}}, _assistant("more"))
    assert req(NOW + 60, src="free-failover-reset") == (True, "ok")
    _write(transcript, _assistant("even more"))
    assert req(NOW + 120) == (False, "cap")
    assert len(sent) == 1


def test_window_expires_old_wakes(env):
    req, _, ledger, _ = env
    req()
    assert req(NOW + wg.WINDOW_S + 1) == (True, "ok")
    assert len(wg.get("s1", ledger)["wakes"]) == 1


def test_unreadable_transcript_never_counts_as_progress(env, tmp_path):
    _, _, ledger, sent = env
    kw = dict(source="x", transcript_path=str(tmp_path / "missing.jsonl"), path=ledger, notify=sent.append)
    assert wg.request("s2", now=NOW, **kw) == (True, "ok")
    assert wg.request("s2", now=NOW + 60, **kw) == (False, "no_progress")


def test_kill_switch_refuses_without_escalating(env, monkeypatch):
    req, _, ledger, sent = env
    monkeypatch.setenv(wg.KILL_SWITCH_ENV, "0")
    assert req() == (False, "disabled")
    assert sent == [] and wg.get("s1", ledger) == {}


def test_clear_gives_a_human_restarted_session_a_fresh_budget(env):
    req, _, ledger, _ = env
    req(); req(NOW + 60)
    wg.clear("s1", ledger)
    assert req(NOW + 120) == (True, "ok")
