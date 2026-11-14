"""In-app replay: only turns typed in the dashboard composer get typed out.

Injected briefs, lane reports and worker messages are user-role rows too; the
replay shows those arriving whole. All data is invented.
"""
import json
import time

import pytest

import sender_envelope
from ccc_server import share_run

NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@pytest.fixture
def journal_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("CCC_STATE_DIR", str(tmp_path / "ccc"))
    monkeypatch.setenv("WATCHTOWER_STATE_DIR", str(tmp_path / "wt"))
    (tmp_path / "ccc").mkdir()
    (tmp_path / "ccc" / "node.json").write_text(json.dumps({"node_id": "node-1"}))
    sender_envelope._cache.clear()
    return tmp_path


def row(text, line):
    return {"type": "user_text", "text": text, "ts": NOW, "line": line, "turn_origin": "sdk"}


def typed(events, sid="s1"):
    return share_run.typed_turns(sid, parse=lambda _sid: {"events": events}, transcript_path=None)


def test_only_composer_sends_are_typed(journal_dirs):
    sender_envelope.journal(sender_envelope.dashboard_human("s1", "fix the login bug"), app="ccc", event="inject")
    events = [
        row("Use CCC orchestration to land ticket WT-1 ...", 1),
        {"type": "assistant", "ts": NOW, "line": 2, "blocks": [{"kind": "text", "text": "ok"}]},
        row("fix the login bug", 3),
        row("Lane report: build finished, 3 files changed", 4),
    ]
    out = typed(events)
    assert out == {"ok": True, "typed_lines": [3], "evidence": True}


def test_no_send_record_reports_no_evidence(journal_dirs):
    out = typed([row("hello", 1)], sid="other")
    assert out["typed_lines"] == [] and out["evidence"] is False


def test_empty_session(journal_dirs):
    assert typed([]) == {"ok": True, "typed_lines": [], "evidence": False}
