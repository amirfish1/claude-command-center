# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Tests for ccc_server/realtime_voice.py — the Codex realtime voice lane.

Drives a real subprocess: tests/fake_voice_appserver.py, a stdlib script
speaking the JSON-RPC subset the module needs. No OpenAI calls, no real
codex binary, no keychain reads — byok accessors are monkeypatched. The
fake key below is a fixture sentinel: it is checked for LEAKS (never
echoed), so it is deliberately obvious and valueless.
"""
import importlib
import json
import os
import stat
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest

server = importlib.import_module("server")
rv = importlib.import_module("ccc_server.realtime_voice")

FAKE_BIN = str(Path(__file__).parent / "fake_voice_appserver.py")
SENTINEL_KEY = "sk-voice-test-FAKEFATALEAKS"  # fixture sentinel, not a real key
SDP_OFFER = "v=0\r\no=- 123 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=fake\r\n"


@pytest.fixture()
def voice_env(tmp_path, monkeypatch):
    """Isolate the voice module: fake app-server bin, fake BYOK key,
    tiny timeouts, captured usage ledger, per-test log file."""
    log = tmp_path / "appserver.log"

    monkeypatch.setenv("FAKE_VOICE_LOG", str(log))
    monkeypatch.setenv("FAKE_VOICE_MODE", "")
    monkeypatch.setattr(server, "_resolve_codex_bin",
                        lambda: {"available": True, "bin": FAKE_BIN})
    monkeypatch.setattr(server, "byok_get_key",
                        lambda profile, provider: SENTINEL_KEY if provider == "openai" else None)
    monkeypatch.setattr(server, "byok_list_profiles",
                        lambda: [{"name": "TestProfile", "providers": ["openai"]}])
    # Cheap deterministic board data for the briefing + ccc_* tools.
    monkeypatch.setattr(server, "compute_attention_feed",
                        lambda **kw: {"items": [
                            {"kind": "question", "where": "ads", "repo": "byum",
                             "session_id": "sess_12345",
                             "question_text": "Approve the plan?"},
                        ]})
    monkeypatch.setattr(server, "_ux_fixes_list_items_cached", lambda *a, **kw: [])
    monkeypatch.setattr(server, "_watchtower_queue_rollup",
                        lambda: {"open_total": 4, "queues_total": 2,
                                 "workers_live": 1, "stuck_total": 0})
    usage_calls = []
    monkeypatch.setattr(server, "byok_record_usage",
                        lambda **kw: usage_calls.append(kw))
    monkeypatch.setattr(server, "compute_session_detail",
                        lambda sid: {"ok": True, "title": "demo", "engine": "claude",
                                     "session_state": {"summary": "working"}})

    # Fast watchdog knobs: 0.35s idle, ~0.5s max, 0.4s heartbeat loss.
    cfg = {"voice": "cove", "profile": "", "max_minutes": 15,
           "idle_seconds": 120, "save_transcripts": False,
           "allow_api_fallback": True}
    monkeypatch.setattr(rv, "voice_config_load", lambda: dict(cfg))
    monkeypatch.setattr(rv, "HEARTBEAT_TIMEOUT_SECONDS", 0.4)
    monkeypatch.setattr(rv, "SDP_ANSWER_TIMEOUT_SECONDS", 0.4)

    yield Simple(cfg=cfg, log=log, usage=usage_calls)
    # Never leave a child alive between tests.
    try:
        rv.voice_stop(reason="test_cleanup")
    except Exception:
        pass


class Simple:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _start(**over):
    params = {"sdp_offer": SDP_OFFER}
    params.update(over)
    return rv.voice_start(params)


def _wait(pred, timeout=8.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = pred()
        if val:
            return val
        time.sleep(interval)
    return None


def _sess_id(res):
    assert res[0].get("ok"), res
    return res[0]["session_id"]


def _events(sid, after=0, timeout=0.5):
    return rv.voice_events_wait(sid, after, timeout=timeout)


def test_start_happy_path_subscription(voice_env):
    """Default start = WebRTC on the ChatGPT subscription: the child env is
    stripped of OPENAI_API_KEY even though a BYOK key exists for fallback."""
    res, status = _start()
    assert status == 200
    sid = res["session_id"]
    assert res["sdp_answer"].startswith("v=0")
    assert res["voice"] == "cove"
    assert res["transport"] == "webrtc"
    assert res["billing"] == "subscription"
    assert res["profile"] is None

    sess = rv._session_by_id(sid)
    assert sess is not None
    # The key is absent from argv and from the child's env entirely —
    # billing must never silently switch to the API key.
    argv_str = " ".join(sess.proc.args)
    assert SENTINEL_KEY not in argv_str
    assert sess.proc.poll() is None
    # No API response carries the key.
    assert SENTINEL_KEY not in json.dumps(res)
    assert SENTINEL_KEY not in json.dumps(rv.voice_status())

    # Session reached listening via the fake's started notification.
    assert _wait(lambda: sess.state == "listening", timeout=5)
    log = voice_env.log.read_text()
    assert "initialize" in log and "thread/start" in log
    assert "thread/realtime/start" in log
    assert '"transport_type": "webrtc"' in log
    # The fake saw NO key in its environment.
    assert "has_openai_key:false" in log
    assert "has_openai_key:true" not in log
    assert SENTINEL_KEY not in log
    assert "has_initialItems\": true" in log


def test_subscription_works_without_key(voice_env, monkeypatch):
    """No BYOK key at all: auto still succeeds on the subscription path."""
    monkeypatch.setattr(server, "byok_get_key", lambda p, pr: None)
    monkeypatch.setattr(server, "byok_list_profiles",
                        lambda: [{"name": "Empty", "providers": []}])
    res, status = _start()
    assert status == 200
    assert res["transport"] == "webrtc"
    assert res["billing"] == "subscription"


def test_websocket_requires_key(voice_env, monkeypatch):
    """Explicit websocket start without a key -> typed voice_no_openai_key."""
    monkeypatch.setattr(server, "byok_get_key", lambda p, pr: None)
    monkeypatch.setattr(server, "byok_list_profiles",
                        lambda: [{"name": "Empty", "providers": []}])
    res, status = _start(transport="websocket")
    assert status == 400
    assert res["code"] == "voice_no_openai_key"
    assert SENTINEL_KEY not in json.dumps(res)


def test_sideband_hang_no_key_times_out(voice_env, monkeypatch):
    """Issue #35094 shape: sdp never arrives. With no BYOK key there is no
    fallback — a typed voice_connect_timeout (not a spin of retries)."""
    monkeypatch.setenv("FAKE_VOICE_MODE", "sideband_hang")
    monkeypatch.setattr(server, "byok_get_key", lambda p, pr: None)
    monkeypatch.setattr(server, "byok_list_profiles",
                        lambda: [{"name": "Empty", "providers": []}])
    res, status = _start()
    assert status == 502
    assert res["code"] == "voice_connect_timeout"
    assert not rv.voice_status()["active"]


def test_sideband_error_no_key(voice_env, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_MODE", "sideband_error")
    monkeypatch.setattr(server, "byok_get_key", lambda p, pr: None)
    monkeypatch.setattr(server, "byok_list_profiles",
                        lambda: [{"name": "Empty", "providers": []}])
    res, status = _start()
    assert status == 502
    assert res["code"] == "voice_sideband_failed"


def test_sideband_hang_falls_back_to_websocket(voice_env, monkeypatch):
    """WebRTC failed but a BYOK key exists: respawn with the key in the
    child env and switch to the websocket (paid) transport."""
    monkeypatch.setenv("FAKE_VOICE_MODE", "sideband_hang")
    res, status = _start()
    assert status == 200
    sid = res["session_id"]
    assert res["transport"] == "websocket"
    assert res["billing"] == "api_key"
    assert res["profile"] == "TestProfile"
    assert res["sdp_answer"] is None
    log = voice_env.log.read_text()
    # First child: no key (stripped). Second child: key injected.
    assert "has_openai_key:false" in log
    assert "has_openai_key:true" in log
    assert '"transport_type": "websocket"' in log
    assert SENTINEL_KEY not in log
    # The ws fallback's outputAudio/delta becomes a volatile audio event.
    ev = _wait(lambda: [e for e in rv.voice_events_wait(sid, 0, 0.1)[0]
                        if e["type"] == "audio"], timeout=5)
    assert ev and ev[-1]["data"]["sampleRate"] == 24000


def test_audio_append_ws_path(voice_env, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_MODE", "sideband_hang")
    res, _ = _start()
    sid = res["session_id"]
    res2, status2 = rv.voice_audio_append(sid, "AAEC", 24000, 1)
    assert status2 == 200 and res2["ok"]
    assert _wait(lambda: "appendAudio" in voice_env.log.read_text(), 5)


def test_audio_append_wrong_transport_rejected(voice_env):
    res, _ = _start()
    sid = res["session_id"]
    res2, status2 = rv.voice_audio_append(sid, "AAEC", 24000, 1)
    assert status2 == 400  # webrtc session — audio flows direct to voice host


def test_second_start_is_409(voice_env):
    res, status = _start()
    assert status == 200
    res2, status2 = _start()
    assert status2 == 409
    assert res2["code"] == "voice_busy"


def test_bad_sdp_rejected(voice_env):
    res, status = _start(sdp_offer="not an sdp")
    assert status == 400
    assert res["code"] == "voice_bad_request"


def test_stop_kills_child_and_closes(voice_env):
    res, _ = _start()
    sid = _sess_id((res, 200))
    sess = rv._session_by_id(sid)
    proc = sess.proc  # _kill_proc clears sess.proc before reaping
    res2, status2 = rv.voice_stop(sid)
    assert status2 == 200 and res2["stopped"] is True
    assert _wait(lambda: proc.poll() is not None, timeout=6), "child survived stop"
    d = rv.voice_status()
    assert d["active"] is False
    # Usage ledger recorded the session. Subscription path: duration only,
    # no provider charge attached.
    assert voice_env.usage, "byok_record_usage was not called"
    u = voice_env.usage[-1]
    assert u["provider"] is None
    assert u["extra"]["billing"] == "subscription"
    assert u["extra"]["duration_s"] >= 0
    assert SENTINEL_KEY not in json.dumps(u)


def test_child_crash_closes_session(voice_env, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_MODE", "crash_after_realtime")
    res, status = _start()
    assert status == 200  # SDP arrived before the exit
    sid = res["session_id"]
    sess = rv._session_by_id(sid)
    assert _wait(lambda: sess.state in ("closed", "error"), timeout=6)
    assert not rv.voice_status()["active"]


def test_idle_timeout(voice_env):
    voice_env.cfg["idle_seconds"] = 0.25  # watchdog reads config once at start
    res, _ = _start()
    sid = _sess_id((res, 200))
    sess = rv._session_by_id(sid)
    assert _wait(lambda: sess.state in ("closed",), timeout=8), sess.state
    assert sess.reason == "idle_timeout"


def test_max_duration(voice_env):
    voice_env.cfg["max_minutes"] = 0.005  # ~0.3s
    voice_env.cfg["idle_seconds"] = 120
    res, _ = _start()
    sid = _sess_id((res, 200))
    sess = rv._session_by_id(sid)
    assert _wait(lambda: sess.state in ("closed",), timeout=8), sess.state
    assert sess.reason == "max_duration"


def test_heartbeat_loss(voice_env):
    voice_env.cfg["idle_seconds"] = 120
    res, _ = _start()
    sid = _sess_id((res, 200))
    sess = rv._session_by_id(sid)
    rv.voice_heartbeat(sid)          # one beat, then silence
    assert _wait(lambda: sess.state in ("closed",), timeout=8), sess.state
    assert sess.reason == "heartbeat_lost"


def test_heartbeat_unknown_session(voice_env):
    res, status = rv.voice_heartbeat("voice_nope")
    assert status == 404
    assert res["code"] == "voice_no_session"


def test_welcome_waits_for_audio_connection_and_runs_once(voice_env):
    sid = _sess_id(_start())
    rv.voice_heartbeat(sid)
    assert "thread/realtime/appendSpeech" not in voice_env.log.read_text()
    for _ in range(3):
        result, status = rv.voice_heartbeat(sid, connected=True)
        assert status == 200 and result["ok"]
    assert _wait(lambda: "thread/realtime/appendSpeech" in voice_env.log.read_text())
    assert voice_env.log.read_text().splitlines().count("thread/realtime/appendSpeech") == 1
    rv.voice_stop(sid)
    assert rv.voice_heartbeat(sid, connected=True)[1] == 404


def test_welcome_retries_after_rejection(voice_env, monkeypatch):
    sid = _sess_id(_start())
    sess = rv._session_by_id(sid)
    calls = []
    original = sess.call

    def reject_once(method, params, **kwargs):
        if method == "thread/realtime/appendSpeech":
            calls.append(params)
            if len(calls) == 1:
                return None, {"message": "not ready"}
        return original(method, params, **kwargs)

    monkeypatch.setattr(sess, "call", reject_once)
    rv.voice_heartbeat(sid, connected=True)
    rv.voice_heartbeat(sid, connected=True)
    rv.voice_heartbeat(sid, connected=True)
    assert len(calls) == 2
    assert calls[-1]["threadId"] == sess.thread_id
    assert calls[-1]["text"] == "Hi, what's up?"


def test_ccc_ticket_reads_current_question_and_recent_comments(monkeypatch):
    class Queue:
        def get(self, ref):
            assert ref == "OPS-42"
            return {
                "ref": ref, "title": "Repair the relay", "status": "blocked",
                "body": "The relay times out after five minutes.",
                "block_question": "Should we raise the timeout or retry?",
                "comments": [
                    {"author": "worker", "text": "Checked the logs."},
                    {"by": "owner", "body": "Please keep the retry bounded."},
                ],
            }

    monkeypatch.setattr(rv._core, "_q", Queue())
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_ticket({"ref": "ops-42"})
    assert "OPS-42" in out and "needs input: Should we raise the timeout" in out
    assert "The relay times out" in out
    assert "worker: Checked the logs." in out
    assert "owner: Please keep the retry bounded." in out


def test_ccc_ticket_missing_question_and_stale_cached_fallback(monkeypatch):
    class Queue:
        def get(self, ref):
            return None

    monkeypatch.setattr(rv._core, "_q", Queue())
    monkeypatch.setattr(rv._core, "_ux_fixes_list_items_cached", lambda *a, **kw: [
        {"ref": "OPS-43", "title": "Cached ticket", "needs_input": True,
         "body": "Only the cached copy is available."},
    ])
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_ticket({"ref": "ops-43"})
    assert "stale cached fallback" in out
    assert "needs input: question not recorded" in out
    assert "Cached ticket" in out
    assert "error" in vs._tool_ticket({"ref": "bad ref!"})


def test_ccc_queues_reports_named_queue_and_needs_input_refs(monkeypatch):
    monkeypatch.setattr(rv._core, "build_ux_fixes_health_payload", lambda: {
        "queues": [{"queue": "OPS", "depth": 3, "workers": 1, "stuck": False}],
    })
    monkeypatch.setattr(rv._core, "_wt_read_config", lambda: {"OPS": {"repo_path": "/tmp"}})
    monkeypatch.setattr(rv._core, "_ux_fixes_list_items_cached", lambda *a, **kw: [
        {"project": "OPS", "ref": "OPS-2", "needs_input": True},
        {"project": "OPS", "ref": "OPS-3", "status": "blocked"},
    ])
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_queues({"queue": "ops"})
    assert "OPS: 3 open" in out
    assert "2 need input (OPS-2, OPS-3)" in out
    assert "error" in vs._tool_queues({"queue": "unknown"})
    assert "error" in vs._tool_queues({"queue": "bad queue!"})


def test_ccc_models_returns_approved_catalog_ids_and_efforts(monkeypatch):
    monkeypatch.setattr(rv._core, "_build_engine_model_catalog", lambda force_refresh=False: {
        "catalog": {
            "claude": {"models": [
                {"id": "opus-5-5", "label": "Opus 5.5", "available": True,
                 "policy_blocked": False, "reasoning_efforts": ["high", "max"]},
                {"id": "blocked", "label": "Blocked", "available": True,
                 "policy_blocked": True},
            ]},
            "codex": {"models": [
                {"id": "gpt-6.1-sol", "label": "GPT-6.1 Sol", "available": True,
                 "policy_blocked": False, "reasoning_efforts": ["medium", "high"]},
                {"id": "offline", "label": "Offline", "available": False,
                 "policy_blocked": False},
            ]},
        },
    })
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_models({"query": "sol"})
    assert "codex: gpt-6.1-sol (GPT-6.1 Sol); efforts: medium, high" in out
    assert "blocked" not in out and "offline" not in out
    assert "opus-5-5" in vs._tool_models({"engine": "claude"})


def test_ccc_triage_queue_proposes_but_never_spawns(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(rv._core, "_wt_queue_attend_resolve_repo", lambda queue: (str(repo), None))
    monkeypatch.setattr(rv._core, "_load_spawn_defaults", lambda: {
        "model_profiles": {"deep": {"models": [
            {"engine": "claude", "model": "senior-model", "effort": "max"},
        ]}},
    })
    proposed = []
    vs = object.__new__(rv.VoiceSession)
    vs._tool_propose = lambda args: proposed.append(args) or "proposed"
    out = vs._tool_triage_queue({"queue": "ops"})
    assert out == "proposed"
    assert len(proposed) == 1
    action = proposed[0]
    assert action["kind"] == "spawn_session"
    assert action["params"]["cwd"] == str(repo)
    assert action["params"]["engine"] == "claude"
    assert action["params"]["model"] == "senior-model"
    assert action["params"]["effort"] == "max"
    assert "no blindly reclaiming live workers" in action["params"]["prompt"].lower()
    assert "needs input" in action["params"]["prompt"].lower()


def test_ccc_triage_queue_requires_configured_deep_profile(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(rv._core, "_wt_queue_attend_resolve_repo", lambda queue: (str(repo), None))
    monkeypatch.setattr(rv._core, "_load_spawn_defaults", lambda: {"model_profiles": {}})
    vs = object.__new__(rv.VoiceSession)
    assert "configure a Deep profile" in vs._tool_triage_queue({"queue": "ops"})


def test_welcome_rejects_nonboolean_connection(voice_env):
    sid = _sess_id(_start())
    result, status = rv.voice_heartbeat(sid, connected="false")
    assert status == 400
    assert result["code"] == "voice_bad_request"
    assert "thread/realtime/appendSpeech" not in voice_env.log.read_text()


def test_malformed_notification_survives(voice_env, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_MODE", "malformed")
    res, status = _start()
    assert status == 200
    sid = res["session_id"]
    sess = rv._session_by_id(sid)
    events = _wait(lambda: [e for e in rv.voice_events_wait(sid, 0, 0.1)[0]
                            if e["type"] == "error"], timeout=5)
    assert events, "malformed line never surfaced as an error event"
    assert sess.state not in ("closed", "error")  # session kept running


def test_event_fanout(voice_env):
    res, _ = _start()
    sid = _sess_id((res, 200))
    events, latest, alive = _events(sid, 0, timeout=1.0)
    types = [e["type"] for e in events]
    assert "state" in types
    assert alive is True
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and latest >= seqs[-1]


def test_transcript_events(voice_env):
    res, _ = _start()
    sid = _sess_id((res, 200))
    sess = rv._session_by_id(sid)
    # Trigger a spoken line through appendSpeech path of the fake.
    sess._send({"method": "thread/realtime/appendSpeech",
                "params": {"threadId": sess.thread_id, "text": "hi"}, "id": 777})
    ev = _wait(lambda: [e for e in rv.voice_events_wait(sid, 0, 0.1)[0]
                        if e["type"] == "transcript"], timeout=5)
    assert ev and ev[-1]["data"]["text"] == "Done."
    assert sess.transcript and sess.transcript[-1]["role"] == "assistant"


def test_key_redaction_in_errors(voice_env, monkeypatch):
    # Only the websocket (API key) path loads a key — force the fallback so
    # the session actually holds one to scrub.
    monkeypatch.setenv("FAKE_VOICE_MODE", "sideband_hang")
    res, _ = _start()
    sid = _sess_id((res, 200))
    sess = rv._session_by_id(sid)
    # Inject a fake realtime error carrying the key value; the emitted
    # event must be scrubbed.
    sess._on_notification("thread/realtime/error",
                          {"message": f"auth failed for {SENTINEL_KEY}"})
    events, _, _ = _events(sid, 0, timeout=0.2)
    blob = json.dumps(events)
    assert SENTINEL_KEY not in blob
    assert "[REDACTED]" in blob


def test_tool_call_readonly(voice_env, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_MODE", "call_tool")
    res, _ = _start()
    sid = _sess_id((res, 200))
    ev = _wait(lambda: [e for e in rv.voice_events_wait(sid, 0, 0.1)[0]
                        if e["type"] == "tool"], timeout=6)
    assert ev and ev[-1]["data"]["tool"] == "ccc_attention"
    # The fake logged our DynamicToolCallResponse — it should carry the
    # fake feed content, proving the read-only tool ran.
    assert _wait(lambda: "Approve the plan" in voice_env.log.read_text(), 5)


def test_propose_action_creates_card(voice_env, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_MODE", "propose_action")
    res, _ = _start()
    sid = _sess_id((res, 200))
    ev = _wait(lambda: [e for e in rv.voice_events_wait(sid, 0, 0.1)[0]
                        if e["type"] == "action"], timeout=6)
    assert ev, "no action event"
    action = ev[-1]["data"]
    assert action["kind"] == "inject"
    assert action["status"] == "proposed"
    assert action["confirm_token"]  # browser needs it; never re-emitted
    sess = rv._session_by_id(sid)
    assert action["id"] in sess.pending_actions


def test_approval_requests_denied(voice_env, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_MODE", "approval_probe")
    res, _ = _start()
    _sess_id((res, 200))
    # The fake logs the client's response payload — a deny decision.
    assert _wait(lambda: '"decision": "denied"' in voice_env.log.read_text(), 5)


def test_notify_action_result_speaks(voice_env, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_MODE", "propose_action")
    res, _ = _start()
    sid = _sess_id((res, 200))
    ev = _wait(lambda: [e for e in rv.voice_events_wait(sid, 0, 0.1)[0]
                        if e["type"] == "action"], timeout=6)
    action = ev[-1]["data"]
    sess = rv._session_by_id(sid)
    assert _wait(lambda: sess.state == "listening", 5)
    # Simulate the browser confirming.
    rv.notify_action_result({"id": action["id"], "kind": "inject",
                             "status": "done", "effect": "Send a message",
                             "confirm_token": "SECRET_TOKEN",
                             "result": {"ok": True}})
    assert _wait(lambda: "appendSpeech" in voice_env.log.read_text(), 6)
    # The token must not ride the SSE update.
    events, _, _ = _events(sid, 0, 0.2)
    assert "SECRET_TOKEN" not in json.dumps(events)


def test_transcript_saved_only_when_opted_in(voice_env, tmp_path, monkeypatch):
    res, _ = _start()
    sid = _sess_id((res, 200))
    sess = rv._session_by_id(sid)
    sess._send({"method": "thread/realtime/appendSpeech",
                "params": {"threadId": sess.thread_id, "text": "x"}, "id": 778})
    _wait(lambda: sess.transcript, 5)
    rv.voice_stop(sid)
    assert not (rv.VOICE_TRANSCRIPT_DIR / f"{sid}.jsonl").exists()

    voice_env.cfg["save_transcripts"] = True
    res2, _ = _start()
    sid2 = _sess_id((res2, 200))
    sess2 = rv._session_by_id(sid2)
    sess2._send({"method": "thread/realtime/appendSpeech",
                 "params": {"threadId": sess2.thread_id, "text": "x"}, "id": 779})
    _wait(lambda: sess2.transcript, 5)
    rv.voice_stop(sid2)
    saved = rv.VOICE_TRANSCRIPT_DIR / f"{sid2}.jsonl"
    assert saved.exists()
    assert SENTINEL_KEY not in saved.read_text()


def test_config_save_roundtrip_and_clamps(voice_env):
    res = rv.voice_config_save({"voice": "coral", "profile": "P1",
                                "max_minutes": 999, "idle_seconds": 1,
                                "save_transcripts": True})
    assert res["ok"]
    c = res["config"]
    assert c["voice"] == "coral"
    assert c["profile"] == "P1"
    assert c["max_minutes"] == 120   # clamped
    assert c["idle_seconds"] == 15   # clamped
    assert c["save_transcripts"] is True
    # Unknown voice falls back to default.
    res2 = rv.voice_config_save({"voice": "not-a-voice"})
    assert res2["ok"] and res2["config"]["voice"] == rv.DEFAULT_VOICE


def test_briefing_is_bounded_and_compact(voice_env):
    feed_calls = []
    roll_calls = []

    def feed(**kw):
        feed_calls.append(kw)
        return {"items": [{"kind": "question", "where": "x",
                           "question_text": "y" * 5000}]}

    def roll():
        roll_calls.append(1)
        return {"open_total": 3, "queues_total": 2,
                "workers_live": 1, "stuck_total": 0}

    text = rv.build_briefing(feed_fn=feed, rollup_fn=roll)
    assert len(feed_calls) == 1 and len(roll_calls) == 1
    assert len(text) <= 3500
    assert "WatchTower" in text


def test_deny_result_shapes():
    assert rv.VoiceSession._deny_result("execCommandApproval")["decision"] == "denied"
    assert rv.VoiceSession._deny_result("mcpServer/elicitation/request")["action"] == "decline"
    assert rv.VoiceSession._deny_result("item/tool/requestUserInput")["answers"] == {}


def test_ccc_session_falls_back_to_archive_row_for_non_claude_ids(monkeypatch):
    """OPS-1341: attention lists kimi/codex ids that compute_session_detail 404s."""
    monkeypatch.setattr(server, "compute_session_detail",
                        lambda sid: ({"ok": False, "error": "session not found"}, 404))
    rows = [{"session_id": "session_abc", "engine": "kimi", "is_live": True,
             "title": "kimi job", "folder_label": "repo",
             "last_assistant_text": "waiting"}]
    monkeypatch.setattr(server, "_archive_all_rows_cached", lambda opts: (rows, True))
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_session({"session_id": "abc"})  # bare uuid still resolves
    assert "engine=kimi" in out and "session_abc" in out and "live" in out
    assert "not found" in vs._tool_session({"session_id": "nope"})
    inv = vs._tool_sessions({})
    assert "session=session_abc" in inv


def test_ccc_session_and_sessions_resolve_by_title(monkeypatch):
    """OPS-1342: voice users say a session's display name, not its id."""
    monkeypatch.setattr(server, "compute_session_detail",
                        lambda sid: ({"ok": False, "error": "session not found"}, 404))
    rows = [{"session_id": "session_abc", "engine": "kimi", "is_live": True,
             "title": "Build Two-way Realtime Voice Mode", "folder_label": "repo"},
            {"session_id": "session_def", "engine": "claude", "is_live": False,
             "title": "Other thing", "folder_label": "repo"}]
    monkeypatch.setattr(server, "_archive_all_rows_cached", lambda opts: (rows, True))
    vs = object.__new__(rv.VoiceSession)
    assert "session_abc" in vs._tool_session({"session_id": "Build Two-way Realtime Voice Mode"})
    inv = vs._tool_sessions({"query": "realtime voice"})
    assert "session=session_abc" in inv and "session_def" not in inv


def test_ccc_session_resolves_eight_char_id_prefix(monkeypatch):
    """OPS-1343: voice users dictate only the first 8 chars of a session id."""
    monkeypatch.setattr(server, "compute_session_detail",
                        lambda sid: ({"ok": False, "error": "session not found"}, 404))
    rows = [{"session_id": "01a103a3-1111-2222-3333-444444444444", "engine": "claude",
             "is_live": True, "title": "Alpha", "folder_label": "repo"},
            {"session_id": "01a103b9-1111-2222-3333-444444444444", "engine": "claude",
             "is_live": True, "title": "Beta", "folder_label": "repo"},
            {"session_id": "01a103b9-aaaa-2222-3333-444444444444", "engine": "claude",
             "is_live": False, "title": "Gamma", "folder_label": "repo"}]
    monkeypatch.setattr(server, "_archive_all_rows_cached", lambda opts: (rows, True))
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_session({"session_id": "01a103a3"})
    assert "01a103a3-1111" in out and "Alpha" in out
    amb = vs._tool_session({"session_id": "01a103b9"})
    assert "multiple matches" in amb and "Beta" in amb and "Gamma" in amb


def test_ccc_sessions_unions_pid_verified_registry_and_cites_proof(monkeypatch):
    """A process-verified session missing from the archive rows is still listed."""
    now = time.time()
    rows = [{"session_id": "aaa", "engine": "claude", "is_live": True, "title": "In archive",
             "modified": now - 30, "state": "working"}]
    monkeypatch.setattr(server, "_archive_all_rows_cached", lambda opts: (rows, True))
    monkeypatch.setattr(server, "_load_session_registry", lambda: {
        "aaa": {"pid": 11, "sessionId": "aaa"},
        "bbb": {"pid": 22, "sessionId": "bbb", "name": "idle-open", "cwd": "/x/repo",
                "status": "idle", "updatedAt": (now - 3000) * 1000},
    })
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_sessions({})
    assert "2 of 2 session(s) as of" in out and "2 Claude process(es) verified alive" in out
    assert "session=bbb" in out and "proof=process pid 22" in out
    assert "session=aaa" in out and "state=working" in out
    # live_only=false must not invent registry rows
    assert "session=bbb" not in vs._tool_sessions({"live_only": False})


def test_ccc_sessions_reports_truncation(monkeypatch):
    rows = [{"session_id": f"s{i}", "engine": "claude", "is_live": True, "title": f"t{i}",
             "modified": i + 1} for i in range(5)]
    monkeypatch.setattr(server, "_archive_all_rows_cached", lambda opts: (rows, True))
    monkeypatch.setattr(server, "_load_session_registry", lambda: {})
    out = rv.VoiceSession._tool_sessions(object.__new__(rv.VoiceSession), {"limit": 2})
    assert "2 of 5" in out and "3 more not shown" in out
    assert "proof=engine/hook signal" in out


def _ticket(ref, status, ago_s, **extra):
    when = datetime.fromtimestamp(time.time() - ago_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return dict({"ref": ref, "project": ref.rsplit("-", 1)[0], "status": status,
                 "needs_input": True, "blocked_at": when, "updated_at": when,
                 "block_kind": "input", "block_question": f"question for {ref}"}, **extra)


def test_ccc_attention_reconciles_with_recorded_ticket_questions(monkeypatch):
    """Empty session feed must not read as 'nothing needs input' while tickets are parked."""
    monkeypatch.setattr(server, "compute_attention_feed", lambda **kw: {"items": []})
    monkeypatch.setattr(server, "_ux_fixes_list_items_cached", lambda *a, **kw: [
        _ticket("OPS-2", "awaiting_answer", 3 * 86400),
        _ticket("PROJECTS-9", "in_progress", 600),
        {"ref": "OPS-5", "project": "OPS", "status": "closed", "needs_input": True},
    ])
    monkeypatch.setattr(server, "_ux_fixes_list_synced_at", lambda *a: time.time() - 20)
    out = rv.VoiceSession._tool_attention(object.__new__(rv.VoiceSession), {})
    assert "No live session is waiting" in out
    assert "recorded reports, not verified current" in out
    assert "OPS-2 parked on the user for 3d" in out and "question for OPS-2" in out
    assert "PROJECTS-9" in out and "may be answered already" in out
    assert "OPS-5" not in out  # closed tickets never need input
    assert "synced" in out


def test_ccc_attention_says_none_only_when_tickets_are_clear(monkeypatch):
    monkeypatch.setattr(server, "compute_attention_feed", lambda **kw: {"items": []})
    monkeypatch.setattr(server, "_ux_fixes_list_items_cached", lambda *a, **kw: [])
    out = rv.VoiceSession._tool_attention(object.__new__(rv.VoiceSession), {})
    assert "No tickets are recorded as needing input" in out

    def boom(*a, **kw):
        raise RuntimeError("down")
    monkeypatch.setattr(server, "_ux_fixes_list_items_cached", boom)
    out = rv.VoiceSession._tool_attention(object.__new__(rv.VoiceSession), {})
    assert "unavailable" in out and "unknown" in out


def test_ccc_queues_cites_sources_and_recorded_block_age(monkeypatch):
    monkeypatch.setattr(server, "build_ux_fixes_health_payload", lambda: {
        "queues": [{"queue": "OPS", "depth": 3, "workers": 0, "stuck": False,
                    "last_activity_seconds": 7200}]})
    monkeypatch.setattr(server, "_wt_read_config", lambda: {"OPS": {}})
    monkeypatch.setattr(server, "_ux_fixes_list_items_cached", lambda *a, **kw: [
        _ticket("OPS-2", "awaiting_answer", 2 * 86400, block_question="Deploy v5.38 needs a credential"),
        _ticket("OPS-9", "in_progress", 60),
    ])
    monkeypatch.setattr(server, "_ux_fixes_health_snapshot", {"ts": time.time() - 5, "data": {}})
    out = rv.VoiceSession._tool_queues(object.__new__(rv.VoiceSession), {"queue": "ops"})
    assert "queue health snapshot" in out and "ticket records synced" in out
    assert "not verified current state" in out
    assert "last activity 2h ago" in out
    assert "OPS-2 awaiting_answer [input] for 2d (recorded): Deploy v5.38 needs a credential" in out
    assert "flagged while a worker holds them" in out and "OPS-9" in out


def test_ccc_sessions_registry_overrides_lagging_archive_liveness(monkeypatch):
    """Archive row says not live, but the pid is alive: the session must be listed."""
    now = time.time()
    rows = [{"session_id": "ccc", "engine": "claude", "is_live": False, "title": "Idle but open",
             "modified": now - 3000, "state": "ended"}]
    monkeypatch.setattr(server, "_archive_all_rows_cached", lambda opts: (rows, True))
    monkeypatch.setattr(server, "_load_session_registry", lambda: {"ccc": {"pid": 7, "status": "idle"}})
    out = rv.VoiceSession._tool_sessions(object.__new__(rv.VoiceSession), {})
    assert "1 of 1 session(s)" in out and "Idle but open" in out and "proof=process pid 7" in out
    assert "state=idle" in out and "state=ended" not in out
    assert rows[0]["is_live"] is False  # archive cache rows are not mutated


def _job(name, status, **kw):
    now = time.time()
    iso = lambda d: datetime.fromtimestamp(now + d, timezone.utc).isoformat()
    return dict({"id": f"hermes:{name}.service", "name": name, "scope": "user", "manager": "systemd",
                 "schedule": "Daily 15:00", "status": status, "description": f"{name} job",
                 "last_run_at": iso(-3600), "next_run_at": iso(39600), "exit_code": 0,
                 "outcome": "", "events": [], "tickets": []}, **kw)


def _stub_feed(monkeypatch, jobs, host=None):
    from ccc_server import scheduled_jobs_feed as feed
    hosts = {"hermes": dict({"status": "online", "collected_at": datetime.now(timezone.utc).isoformat(),
                             "stale_data": False, "error": None}, **(host or {}))}
    monkeypatch.setattr(feed, "get_jobs_feed", lambda wait_s=0, force=False:
                        {"jobs": jobs, "hosts": hosts, "local_host": "hermes"})


def test_ccc_jobs_explains_why_a_release_is_not_out(monkeypatch):
    _stub_feed(monkeypatch, [
        _job("bym-ship", "ok", outcome="PR #2371 held for a risky migration batch (needs Amir).",
             tickets=[{"ref": "PR #2371", "url": "https://example.test/pull/2371"}]),
        _job("bym-digest-send", "failed", exit_code=1, outcome="sqlite3.OperationalError"),
        _job("other", "ok"),
    ])
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_jobs({"query": "bym ship"})
    assert "bym-ship" in out and "bym-digest-send" not in out
    assert "held for a risky migration batch" in out
    assert "on schedule: last ran 60m ago" in out and "collected" in out
    assert "https://example.test/pull/2371" in out
    bad = vs._tool_jobs({"problems_only": True})
    assert "bym-digest-send" in bad and "FAILED with exit code 1" in bad and "bym-ship" not in bad
    assert "No scheduled jobs matching 'nope'" in vs._tool_jobs({"query": "nope"})


def test_ccc_jobs_reports_disabled_cron_and_stale_host(monkeypatch):
    _stub_feed(monkeypatch, [
        _job("off", "disabled", next_run_at=None),
        _job("tab", "unknown", scope="cron", manager="cron"),
        _job("dead", "stale", next_run_at=None),
    ], host={"status": "offline", "stale_data": True, "error": "ssh timeout"})
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_jobs({"limit": 5})
    assert "HOST OFFLINE" in out and "ssh timeout" in out
    assert "timer is disabled" in out
    assert "cron keeps no run history" in out
    assert "no upcoming run is scheduled" in out


def test_ccc_jobs_reports_loading_and_is_registered(monkeypatch):
    from ccc_server import scheduled_jobs_feed as feed
    monkeypatch.setattr(feed, "get_jobs_feed", lambda wait_s=0, force=False:
                        {"jobs": [], "hosts": {"hermes": {"status": "loading"}}})
    vs = object.__new__(rv.VoiceSession)
    assert "still loading" in vs._tool_jobs({})
    assert "ccc_jobs" in rv.TOOL_NAMES
    assert "ccc_jobs" in [t["name"] for t in rv._tool_specs()]


def test_ccc_session_reports_pid_verified_liveness(monkeypatch):
    monkeypatch.setattr(server, "compute_session_detail",
                        lambda sid: ({"ok": True, "title": "demo", "engine": "claude"}, 200))
    monkeypatch.setattr(server, "_archive_all_rows_cached", lambda opts: ([], True))
    monkeypatch.setattr(server, "_load_session_registry",
                        lambda: {"abc": {"pid": 42, "status": "idle"}})
    vs = object.__new__(rv.VoiceSession)
    out = vs._tool_session({"session_id": "abc"})
    assert "process pid 42 verified alive" in out and "Claude reports idle" in out
    assert "no running Claude process" in vs._tool_session({"session_id": "zzz"})
