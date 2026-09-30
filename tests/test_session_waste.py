"""Session waste: /api/session/waste over the external agent-throughput CLI,
and the Throughput page panel that shows it."""

import email
import io
import json
import os
import pathlib
import stat
import subprocess
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

ANALYSIS = {
    "session": {"source_session_id": "abcd1234-0000", "engine": "claude_code", "model_label": "Opus 5.5",
                "model_id": "claude-opus-5-5"},
    "list_usd": 118.0, "real_usd": 5.9, "list_to_real": 20.0, "ratio_source": "plan", "unpriced_calls": 0,
    "score": 38,
    "findings": [{"key": "push_not_pull", "title": "Switch from pull to push while waiting", "share_pct": 30.2,
                  "list_usd": 35.0, "real_usd": 1.75, "score_gain": 25, "evidence": "352 calls polled",
                  "fix": "Have the work report back."}],
    "minor_findings": [{"title": "Stay under the long-context price step", "share_pct": 1.2}],
    "activities": [{"activity": "checking on progress", "calls": 352, "list_usd": 57.0, "share_pct": 48.3}],
    "breakdown": [], "heaviest_turns": [],
}


def _waste():
    from ccc_server import session_waste
    session_waste._session_waste_cache.clear()
    return session_waste


def _fake_cli(tmp_path, body):
    """An executable that logs its argv and prints ``body`` (or fails when body is None)."""
    log = tmp_path / "calls.log"
    script = tmp_path / "throughput"
    out = "" if body is None else json.dumps(body)
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        + (f"print({out!r})\n" if body is not None else "sys.stderr.write('no session id starts with x\\n'); sys.exit(1)\n")
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, log


def test_analysis_is_slimmed_and_cached(tmp_path, monkeypatch):
    sw = _waste()
    cli, log = _fake_cli(tmp_path, ANALYSIS)
    monkeypatch.setenv("CCC_SESSION_WASTE_BIN", str(cli))

    first = sw.session_waste("abcd1234-0000")
    second = sw.session_waste("abcd1234-0000")

    assert first["ok"] and first["cached"] is False
    assert first["score"] == 38 and first["real_usd"] == 5.9
    assert first["findings"] == [{"title": "Switch from pull to push while waiting", "evidence": "352 calls polled",
                                  "fix": "Have the work report back.", "list_usd": 35.0, "real_usd": 1.75,
                                  "share_pct": 30.2}]
    assert "breakdown" not in first and "score_gain" not in first["findings"][0]
    assert second["cached"] is True
    assert log.read_text().splitlines() == ["analyze abcd1234-0000 --json"]  # one run for two reads


def test_cached_only_never_starts_a_process(tmp_path, monkeypatch):
    sw = _waste()
    cli, log = _fake_cli(tmp_path, ANALYSIS)
    monkeypatch.setenv("CCC_SESSION_WASTE_BIN", str(cli))

    r = sw.session_waste("abcd1234-0000", cached_only=True)

    assert r == {"ok": False, "cached": False, "error": "not analyzed yet"}
    assert not log.exists()


def test_failures_are_reported_and_not_cached(tmp_path, monkeypatch):
    sw = _waste()
    cli, log = _fake_cli(tmp_path, None)
    monkeypatch.setenv("CCC_SESSION_WASTE_BIN", str(cli))

    r = sw.session_waste("abcd1234-0000")
    sw.session_waste("abcd1234-0000")

    assert r["ok"] is False and r["error"] == "no session id starts with x"
    assert len(log.read_text().splitlines()) == 2


def test_missing_tool_says_how_to_install(monkeypatch, tmp_path):
    sw = _waste()
    monkeypatch.setenv("CCC_SESSION_WASTE_BIN", str(tmp_path / "nope"))
    r = sw.session_waste("abcd1234-0000")
    assert r["installed"] is False and "agent-throughput" in r["install"]


def test_ccc_own_throughput_script_is_never_used(monkeypatch):
    sw = _waste()
    monkeypatch.setenv("CCC_SESSION_WASTE_BIN", str(PROJECT_ROOT / "scripts" / "throughput"))
    assert sw._session_waste_bin() is None


@pytest.mark.parametrize("sid", ["", "x", "../../etc/passwd", "a b c d", "--json"])
def test_bad_session_ids_are_rejected_before_any_process(sid, tmp_path, monkeypatch):
    sw = _waste()
    cli, log = _fake_cli(tmp_path, ANALYSIS)
    monkeypatch.setenv("CCC_SESSION_WASTE_BIN", str(cli))
    assert sw.session_waste(sid) == {"ok": False, "error": "bad session_id"}
    assert not log.exists()


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


def test_endpoint_passes_cached_flag(monkeypatch):
    sys.argv = ["server.py"]
    import server
    seen = []
    monkeypatch.setattr(server, "session_waste", lambda sid, cached_only=False: seen.append((sid, cached_only)) or {"ok": True})

    assert _get_json(server, "/api/session/waste?session_id=abcd1234&cached=1") == {"ok": True}
    assert _get_json(server, "/api/session/waste?session_id=abcd1234") == {"ok": True}
    assert _get_json(server, "/api/session/waste")["error"] == "missing session_id"
    assert seen == [("abcd1234", True), ("abcd1234", False)]


def _render(payload):
    html = (PROJECT_ROOT / "static" / "throughput.html").read_text(encoding="utf-8")
    start, end = html.index("// SESSION_WASTE_START"), html.index("// SESSION_WASTE_END")
    # The block alone, so it cannot lean on helpers that are not global on the page.
    script = f"{html[start:end]}\nconsole.log(sessionWasteHtml({json.dumps(payload)}));"
    return subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True).stdout


def test_panel_names_each_fix_with_share_and_dollars():
    sys.path.insert(0, str(PROJECT_ROOT))
    out = _render(_waste()._session_waste_slim(ANALYSIS))
    assert '<span class="waste-score bad">38/100</span>' in out
    assert "$118 list ≈ $5.90 real" in out
    assert "1. Switch from pull to push while waiting</span> - 30% of spend ($35 list ≈ $1.75 real)" in out
    assert "went to: checking on progress 48%" in out


def test_panel_offers_a_run_button_until_analyzed_and_escapes_errors():
    assert 'data-waste-run="1">Analyze waste' in _render({"ok": False, "cached": False, "error": "not analyzed yet"})
    assert "<code>pipx install" in _render({"ok": False, "installed": False, "install": "pipx install x"})
    bad = _render({"ok": False, "error": "<img src=x onerror=alert(1)>"})
    assert "<img" not in bad and "&lt;img" in bad


def test_unpriced_session_has_no_score():
    out = _render({"ok": True, "score": None, "list_usd": 0, "real_usd": None, "unpriced_calls": 12,
                   "findings": [], "activities": []})
    assert "score n/a" in out and "12 calls unpriced" in out and "No fix would save" not in out
