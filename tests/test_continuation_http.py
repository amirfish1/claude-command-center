# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""HTTP-level coverage for the two new endpoints backing `ccc spawn
--continue-from` / `ccc send --new-if-large-and-stale`
(GET /api/sessions/continuation-decision/<sid>, POST
/api/sessions/spawn-continue-from), plus the /api/inject-input lineage
forward (MEMO-FIX-lineage) — all against a real bound ThreadingHTTPServer,
matching tests/test_report_routes.py's pattern.
"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

import server
from ccc_server import continuation


def _serve():
    httpd = server.http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), server.CommandCenterHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


def _request(httpd, path, payload=None, method=None):
    url = f"http://127.0.0.1:{httpd.server_port}{path}"
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                  method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def httpd():
    httpd, thread = _serve()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_continuation_decision_endpoint(httpd, monkeypatch):
    seen = {}

    def fake_decide(query, large_threshold=None, stale_seconds=None):
        seen["query"] = query
        seen["large_threshold"] = large_threshold
        seen["stale_seconds"] = stale_seconds
        return {"path": "new", "reason": "big and stale", "context_tokens": 200000,
                "idle_seconds": 7200, "resolved_session_id": "sid-1", "latest_session_id": "sid-1"}

    monkeypatch.setattr(continuation, "decide_send_path", fake_decide)
    status, body = _request(
        httpd, "/api/sessions/continuation-decision/sid-1?large_threshold=50000&stale_seconds=120",
    )
    assert status == 200
    assert body["path"] == "new"
    assert seen == {"query": "sid-1", "large_threshold": 50000, "stale_seconds": 120}


def test_continuation_decision_endpoint_defaults(httpd, monkeypatch):
    seen = {}

    def fake_decide(query, large_threshold=None, stale_seconds=None):
        seen["large_threshold"] = large_threshold
        seen["stale_seconds"] = stale_seconds
        return {"path": "normal"}

    monkeypatch.setattr(continuation, "decide_send_path", fake_decide)
    _request(httpd, "/api/sessions/continuation-decision/sid-1")
    assert seen == {
        "large_threshold": continuation.DEFAULT_LARGE_TOKENS,
        "stale_seconds": continuation.DEFAULT_STALE_SECONDS,
    }


def test_spawn_continue_from_missing_continue_from(httpd):
    status, body = _request(httpd, "/api/sessions/spawn-continue-from", {})
    assert status == 400
    assert not body["ok"]
    assert "continue_from" in body["error"]


def test_spawn_continue_from_success(httpd, monkeypatch):
    seen = {}

    def fake_spawn(continue_from, prompt="", model=None, effort=None, report_to=None,
                   dry_run=False, rebind_chain=True, name=None, task_key="", dedupe=False):
        seen.update(continue_from=continue_from, prompt=prompt, model=model,
                    effort=effort, report_to=report_to, dry_run=dry_run,
                    rebind_chain=rebind_chain, name=name, task_key=task_key, dedupe=dedupe)
        return {"ok": True, "dry_run": False, "continue_from": continue_from,
                "latest_session_id": continue_from, "new_session_id": "new-sid",
                "engine": "claude", "rebound": []}

    monkeypatch.setattr(continuation, "spawn_continuation", fake_spawn)
    status, body = _request(httpd, "/api/sessions/spawn-continue-from", {
        "continue_from": "old-sid", "prompt": "keep going", "model": "opus-5",
        "name": "worker", "task_key": "example-task", "dedupe": True,
    })
    assert status == 200
    assert body["ok"] is True
    assert body["new_session_id"] == "new-sid"
    assert seen["continue_from"] == "old-sid"
    assert seen["prompt"] == "keep going"
    assert seen["model"] == "opus-5"
    assert seen["name"] == "worker"
    assert seen["task_key"] == "example-task"
    assert seen["dedupe"] is True
    assert seen["dry_run"] is False


def test_spawn_continue_from_dry_run_flag_forwarded(httpd, monkeypatch):
    seen = {}

    def fake_spawn(continue_from, **kwargs):
        seen.update(kwargs)
        return {"ok": True, "dry_run": True, "continue_from": continue_from}

    monkeypatch.setattr(continuation, "spawn_continuation", fake_spawn)
    status, body = _request(httpd, "/api/sessions/spawn-continue-from", {
        "continue_from": "old-sid", "dry_run": True,
    })
    assert status == 200
    assert body["dry_run"] is True
    assert seen["dry_run"] is True


def test_spawn_continue_from_error_result_is_400(httpd, monkeypatch):
    monkeypatch.setattr(
        continuation, "spawn_continuation",
        lambda *a, **k: {"ok": False, "error": "no session found for 'nope'"},
    )
    status, body = _request(httpd, "/api/sessions/spawn-continue-from", {"continue_from": "nope"})
    assert status == 400
    assert not body["ok"]


def test_inject_input_lineage_forward(monkeypatch):
    seen = []

    def fake_alias(sid):
        seen.append(sid)
        return ""  # empty sid -> handler replies "missing session_id"

    monkeypatch.setattr(server, "_resolve_bridge_session_alias", fake_alias)
    monkeypatch.setattr(continuation, "forward_target", lambda sid: "new-sid" if sid == "old-sid" else sid)
    httpd, thread = _serve()
    try:
        _request(httpd, "/api/inject-input", {"session_id": "old-sid", "text": "hi"})
        _request(httpd, "/api/inject-input", {"session_id": "unrelated-sid", "text": "hi"})
        assert seen == ["old-sid", "unrelated-sid"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_inject_input_lineage_forward_actually_redirects_delivery(monkeypatch):
    """End-to-end: the sid _inject_text_into_session ultimately receives is
    the successor, not the one the request named."""
    delivered = []
    monkeypatch.setattr(
        server, "_inject_text_into_session",
        lambda sid, text, **kw: delivered.append(sid) or {"ok": True},
    )
    monkeypatch.setattr(continuation, "forward_target", lambda sid: "successor-sid")
    httpd, thread = _serve()
    try:
        _request(httpd, "/api/inject-input", {"session_id": "old-sid", "text": "hi"})
        assert delivered == ["successor-sid"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_ephemeral_forward_retry_returns_pane_successor(httpd, monkeypatch):
    seen = {}
    monkeypatch.setattr(server, '_resolve_bridge_session_alias', lambda sid: sid)
    monkeypatch.setattr(continuation, 'forward_target', lambda sid: 'new-codex')
    monkeypatch.setattr(server, '_codex_capture_thread_row', lambda sid: {'_ccc_capture': 'capture.log'})
    monkeypatch.setattr(server, '_handoff_lease_guard', lambda sid: None)
    monkeypatch.setattr(server, '_record_interaction', lambda sid: None)
    def inject(sid, text, **kwargs):
        seen.update(sid=sid, **kwargs)
        return {'ok': True, 'via': 'duplicate-suppressed', 'deduped': True}
    monkeypatch.setattr(server, '_inject_text_into_session', inject)
    code, result = _request(httpd, '/api/inject-input', {
        'session_id': 'old-codex', 'text': 'first task', 'idempotency_key': 'send-1',
    })
    assert code == 200
    assert result['via'] == 'codex-continuation'
    assert result['new_session_id'] == 'new-codex'
    assert result['continue_from'] == 'old-codex'
    assert result['deduped']
    assert seen['_dedupe_session_id'] == 'old-codex'


def test_continuation_envelope_keeps_author_and_actual_recipient(httpd,monkeypatch):
    import sender_envelope as attribution
    monkeypatch.setenv('CCC_ATTRIBUTION_EMIT','1')
    a=attribution.service('wt:notify','original')
    attribution.journal(a,app='wt')
    seen=[]
    monkeypatch.setattr(continuation,'spawn_continuation',lambda sid,**kw: seen.append(kw) or {'ok':True,'new_session_id':'successor'})
    status,result=_request(httpd,'/api/sessions/spawn-continue-from',{'continue_from':'original','prompt':'notice','attr':a})
    assert status==200 and result['ok']
    assert attribution.from_text(seen[0]['prompt'])['message_id']==a['message_id']
    stored=attribution.lookup(message_id=a['message_id'],recipient='successor')
    assert stored['author']==a['author']
    assert stored['intended_recipient']['id']=='original'
    assert stored['delivered_recipient']['id']=='successor'
