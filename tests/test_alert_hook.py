"""RESIL-6: off-dashboard alert hook for dead sessions and refused wakes.

Pins: off by default, one alert per incident, an hourly cap that reports what
it held back, the message reaching the command on stdin, and the dead-session
emitter calling it.
"""
import sys

import pytest

from ccc_server import alert_hook as ah

NOW = 1_000_000.0


@pytest.fixture
def ledger(tmp_path):
    return str(tmp_path / "alert-hook.json")


@pytest.fixture
def capture(tmp_path, monkeypatch):
    out = tmp_path / "got.txt"
    script = tmp_path / "notifier.py"
    script.write_text("import sys\nopen(sys.argv[1], 'a').write(sys.stdin.read() + '\\n--\\n')\n")
    monkeypatch.setenv(ah.COMMAND_ENV, f"{sys.executable} {script} {out}")
    return out


def test_off_unless_configured(monkeypatch, ledger):
    monkeypatch.delenv(ah.COMMAND_ENV, raising=False)
    assert ah.send("k", "t", path=ledger, wait=True) == ah.OFF


def test_message_goes_to_the_command_on_stdin_once_per_incident(capture, ledger):
    assert ah.send("death:s1:10", "Lane A stopped mid-turn", "Its process stopped.",
                   "https://ccc.example/?session=s1", now=NOW, path=ledger, wait=True) == ah.SENT
    assert ah.send("death:s1:10", "again", now=NOW + 5, path=ledger, wait=True) == ah.DUPLICATE
    got = capture.read_text()
    assert "Lane A stopped mid-turn\nIts process stopped.\nhttps://ccc.example/?session=s1" in got
    assert got.count("--") == 1


def test_hourly_cap_holds_back_and_reports_the_count(capture, ledger):
    for i in range(ah.RATE_MAX):
        assert ah.send(f"k{i}", f"alert {i}", now=NOW + i, path=ledger, wait=True) == ah.SENT
    assert ah.send("over1", "x", now=NOW + 20, path=ledger, wait=True) == ah.RATE_LIMITED
    assert ah.send("over2", "x", now=NOW + 21, path=ledger, wait=True) == ah.RATE_LIMITED
    assert ah.send("later", "after the window", now=NOW + ah.RATE_WINDOW_S + 1, path=ledger, wait=True) == ah.SENT
    assert "(+2 more alerts held back" in capture.read_text().split("--")[-2]
    # held-back incidents are not re-sent later
    assert ah.send("over1", "x", now=NOW + ah.RATE_WINDOW_S + 2, path=ledger, wait=True) == ah.DUPLICATE


def test_a_failing_command_never_raises(monkeypatch, ledger):
    monkeypatch.setenv(ah.COMMAND_ENV, "/nonexistent/notifier")
    logs = []
    assert ah.send("k", "t", path=ledger, wait=True, log=logs.append) == ah.SENT
    assert logs and logs[0].startswith("error")


def test_dashboard_link_prefers_public_url(monkeypatch):
    monkeypatch.setenv("CCC_PUBLIC_URL", "https://ccc.example/")
    assert ah.dashboard_link("/?session=s1") == "https://ccc.example/?session=s1"
    monkeypatch.delenv("CCC_PUBLIC_URL")
    monkeypatch.delenv("CCC_ALLOWED_ORIGIN", raising=False)
    assert ah.dashboard_link("/?session=s1") == "/?session=s1"


def test_dead_session_emitter_alerts_off_dashboard(monkeypatch):
    from ccc_server import notify, popups
    calls = []
    monkeypatch.setattr(popups, "APPROVED", frozenset(popups.ALL))
    monkeypatch.setattr(notify, "_dedupe_hit", lambda item, now: None)
    monkeypatch.setattr(notify._session_death, "track_working", lambda current: None)
    monkeypatch.setattr(notify._session_death, "due", lambda current, now=None: [
        {"session_id": "s9", "size": 42, "title": "Fix login", "verdict": {"kind": "cut_off", "step": {}}}])
    monkeypatch.setattr(notify, "_append_log", lambda item: None)
    monkeypatch.setattr(ah, "send", lambda key, title, body="", url="", **kw: calls.append((key, title, body, url)) or ah.SENT)
    notify._emit_session_deaths({}, NOW, publish=lambda item: None)
    assert calls and calls[0][0] == "death:s9:42"
    assert "stopped mid-turn" in calls[0][1] and "s9" in calls[0][3]
