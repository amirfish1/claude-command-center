"""Router-side tests for the UDS peer transport (slice 2)."""

import json

import server


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_peer_receipt_delivered_when_msg_id_lands(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [
        {"type": "user", "isMeta": True, "origin": {"kind": "peer", "msg_id": "m-1", "body": "hi"}},
    ])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-1", "hi", timeout_s=0.2) == "delivered"


def test_peer_receipt_delivered_for_absorbed_attachment(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [
        {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "peer", "msg_id": "m-2"}}},
    ])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-2", "x", timeout_s=0.2) == "delivered"


def test_peer_receipt_held_when_preview_matches(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [
        {"type": "system", "subtype": "informational",
         "content": "Held peer message — from uds:/tmp/cc-socks/1.sock; preview: «please run tests now» — not delivered"},
    ])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-3", "please run tests now", timeout_s=0.2) == "held"


def test_peer_receipt_unknown_on_timeout(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    _write_jsonl(p, [{"type": "user", "message": {"content": "unrelated"}}])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-4", "x", timeout_s=0.2) == "unknown"


def test_peer_receipt_held_matches_truncated_and_escaped_preview(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    body = 'Please re-run "tests/test_ccc_peer_uds.py" with ünïcode and then report back to me'
    content = "Held peer message — from uds:/tmp/cc-socks/1.sock; preview: «" + body[:60] + "» — not delivered"
    _write_jsonl(p, [
        {"type": "system", "subtype": "informational", "content": content},
    ])
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt("sid", "m-5", body, timeout_s=0.2) == "held"


def test_peer_receipt_ignores_stale_held_row_before_start_offset(monkeypatch, tmp_path):
    """F2: a held row from an EARLIER message with the same preview must not
    be mistaken for this send's receipt: only bytes appended after
    start_offset count."""
    p = tmp_path / "s.jsonl"
    stale_held = {
        "type": "system", "subtype": "informational",
        "content": "Held peer message — from uds:/tmp/cc-socks/1.sock; preview: «please run tests now» — not delivered",
    }
    _write_jsonl(p, [stale_held])
    start_offset = p.stat().st_size
    with p.open("a") as fh:
        fh.write(json.dumps({
            "type": "user", "isMeta": True,
            "origin": {"kind": "peer", "msg_id": "m-6", "body": "please run tests now"},
        }) + "\n")
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt(
        "sid", "m-6", "please run tests now", start_offset=start_offset, timeout_s=0.3,
    ) == "delivered"


def test_peer_receipt_stale_held_row_alone_is_unknown(monkeypatch, tmp_path):
    """F2: the same stale held row, with nothing appended after
    start_offset, must NOT report "held" for a send that hasn't happened
    yet: it reports "unknown"."""
    p = tmp_path / "s.jsonl"
    stale_held = {
        "type": "system", "subtype": "informational",
        "content": "Held peer message — from uds:/tmp/cc-socks/1.sock; preview: «please run tests now» — not delivered",
    }
    _write_jsonl(p, [stale_held])
    start_offset = p.stat().st_size
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt(
        "sid", "m-7", "please run tests now", start_offset=start_offset, timeout_s=0.3,
    ) == "unknown"


def test_peer_receipt_queued_when_enqueue_row_lands_after_start_offset(monkeypatch, tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("")
    start_offset = p.stat().st_size
    body = "please review the diff"
    enqueue_row = {
        "type": "queue-operation", "operation": "enqueue",
        "timestamp": "2026-08-28T00:00:00Z", "sessionId": "sid", "content": body,
    }
    with p.open("a") as fh:
        fh.write(json.dumps(enqueue_row) + "\n")
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(p))
    assert server._transcript_peer_receipt(
        "sid", "m-8", body, start_offset=start_offset, timeout_s=0.3,
    ) == "queued"


import ccc_peer_uds


def test_uds_gate_reads_backend_env(monkeypatch):
    monkeypatch.delenv("CCC_MESSAGING_BACKEND", raising=False)
    assert server._uds_messaging_enabled() is True  # default-on, unset env
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    assert server._uds_messaging_enabled() is True
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "wt,uds")
    assert server._uds_messaging_enabled() is True
    assert server._wt_messaging_enabled() is False  # unchanged: exact "wt" only
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "legacy")
    assert server._uds_messaging_enabled() is False  # explicit opt-out


def test_uds_source_eligibility():
    for src in ("ask", "group-chat-coordinate", "group-chat-auto-nudge", "group-chat-manual-nudge", "announced_from", "wt"):
        assert server._uds_source_eligible(src) is True
    # "report_to" is not a literal source any caller passes: report-back
    # footers arrive at /api/inject-input with an announced_from field, so
    # they are classified as "announced_from" instead (see
    # _inject_source_for_request below). The literal string stays ineligible.
    for src in ("api", "user", "manual", "ccc-spawn", "terminal-queue-watcher", "report_to", "", None):
        assert server._uds_source_eligible(src) is False


def test_inject_source_for_request_classifies_announced_from_wt_and_api():
    assert server._inject_source_for_request("dispatcher", False) == "announced_from"
    assert server._inject_source_for_request("dispatcher", True) == "announced_from"  # announced_from wins
    assert server._inject_source_for_request(None, True) == "wt"
    assert server._inject_source_for_request(None, False) == "api"
    assert server._inject_source_for_request("", False) == "api"


def _enable_uds(monkeypatch, tmp_path, registry_row, sent):
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setattr(server, "_load_session_registry", lambda: {"target-sid": registry_row})
    monkeypatch.setattr(server, "SESSIONS_REGISTRY", tmp_path)
    monkeypatch.setattr(ccc_peer_uds, "load_peer_token", lambda d, pid, sp: "tok")

    def fake_send(socket_path, lines, timeout_s=3.0):
        sent.append((socket_path, [json.loads(l) for l in lines]))
        return {"ok": True, "error": ""}

    monkeypatch.setattr(ccc_peer_uds, "send_lines", fake_send)


def _dialable_row(tmp_path):
    sock = tmp_path / "t.sock"
    sock.touch()
    return {"pid": 4242, "messagingSocketPath": str(sock), "peerProtocol": 1, "version": "2.1.251"}


def test_try_uds_delivers_and_returns_receipt(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda sid, mid, body, start_offset=0, timeout_s=2.0: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    result = server._try_uds_peer_delivery("target-sid", "please review", source="ask")
    assert result["ok"] is True and result["via"] == "uds" and result["receipt"] == "delivered"
    assert result["receipt_id"]
    assert len(sent) == 1
    socket_path, frames = sent[0]
    assert frames[0] == {"type": "auth", "token": "tok"}
    user = frames[1]
    assert user["msg_id"] == result["receipt_id"]
    assert user["priority"] == "next"
    assert "from" not in user  # no sender session known, no address claimed
    assert user["message"]["content"].startswith("<cross-session-message>")
    assert "please review" in user["message"]["content"]


def test_try_uds_steer_uses_priority_now(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    server._try_uds_peer_delivery("target-sid", "stop", source="ask", mode="steer")
    assert sent[0][1][-1]["priority"] == "now"


def test_try_uds_attests_bypass_only_for_ccc_spawned_sender(monkeypatch, tmp_path):
    sent = []
    sender_sock = tmp_path / "sender.sock"
    sender_sock.touch()
    rows = {
        "target-sid": _dialable_row(tmp_path),
        "sender-sid": {"pid": 77, "messagingSocketPath": str(sender_sock), "peerProtocol": 1, "version": "2.1.251", "name": "dispatcher"},
    }
    _enable_uds(monkeypatch, tmp_path, rows["target-sid"], sent)
    monkeypatch.setattr(server, "_load_session_registry", lambda: rows)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session",
                        lambda sid: {"engine": "claude"} if sid == "sender-sid" else None)
    server._try_uds_peer_delivery("target-sid", "go", source="group-chat-coordinate", peer_sender_sid="sender-sid")
    user = sent[0][1][-1]
    assert user["from"] == "uds:" + str(sender_sock)
    content = user["message"]["content"]
    assert 'from="uds:' + str(sender_sock) + '"' in content
    assert 'from-name="dispatcher"' in content
    assert 'from-mode="bypass"' in content

    sent.clear()
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    server._try_uds_peer_delivery("target-sid", "go", source="group-chat-coordinate", peer_sender_sid="sender-sid")
    assert "from-mode=" not in sent[0][1][-1]["message"]["content"]


def test_try_uds_falls_through_when_held_or_unknown(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    for verdict in ("held", "unknown"):
        monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: verdict)
        assert server._try_uds_peer_delivery("target-sid", "x", source="ask") is None


def test_try_uds_returns_ok_for_queued_and_none_for_unknown(monkeypatch, tmp_path):
    """F2: "queued" is a confirmed-in-inbox receipt just like "delivered":
    the caller must not fall through to a legacy transport and duplicate the
    frame."""
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "queued")
    result = server._try_uds_peer_delivery("target-sid", "x", source="ask")
    assert result["ok"] is True and result["via"] == "uds" and result["receipt"] == "queued"

    sent.clear()
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "unknown")
    assert server._try_uds_peer_delivery("target-sid", "x", source="ask") is None


def test_try_uds_skips_ineligible_source_without_dialing(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    assert server._try_uds_peer_delivery("target-sid", "x", source="user") is None
    assert server._try_uds_peer_delivery("target-sid", "x", source="api") is None
    assert sent == []


def test_try_uds_skips_when_gate_off_or_target_not_dialable(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "legacy")
    assert server._try_uds_peer_delivery("target-sid", "x", source="ask") is None
    monkeypatch.setenv("CCC_MESSAGING_BACKEND", "uds")
    monkeypatch.setattr(server, "_load_session_registry", lambda: {})
    assert server._try_uds_peer_delivery("target-sid", "x", source="ask") is None
    assert sent == []


def test_try_uds_loads_registry_once_and_never_forks(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    calls = {"registry": 0}
    row = _dialable_row(tmp_path)

    def counting_registry():
        calls["registry"] += 1
        return {"target-sid": row}

    monkeypatch.setattr(server, "_load_session_registry", counting_registry)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    monkeypatch.setattr(server.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess in uds path")))
    monkeypatch.setattr(server.subprocess, "Popen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("subprocess in uds path")))
    server._try_uds_peer_delivery("target-sid", "x", source="ask")
    assert calls["registry"] == 1


def _stub_inject_router_seams(monkeypatch, *, is_codex=False):
    """Stub the router seams _inject_text_into_session runs before the UDS
    hook, so a call against server._inject_text_into_session exercises the
    real wiring around the hook instead of a fully mocked-out function."""
    monkeypatch.setattr(server, "_federation_resolve_target", lambda sid: (sid, None))
    monkeypatch.setattr(server, "_claude_subagent_parent_session_id", lambda sid: None)
    monkeypatch.setattr(server, "_canonical_kimi_session_id", lambda sid: sid)
    monkeypatch.setattr(server, "_inject_budget_check", lambda *a, **k: None)
    monkeypatch.setattr(server, "_is_codex_session", lambda sid: is_codex)
    monkeypatch.setattr(server, "find_session_cwd", lambda sid: None)
    monkeypatch.setattr(server, "session_live_status", lambda sid, cwd: {})


def _stub_inject_router_engine_detectors(monkeypatch):
    """F1: after the move, the UDS hook sits AFTER the worker hand-off
    check, so a headless-target test now reaches the engine-detector calls
    the hand-off condition guards on. Stub every one so the test exercises
    only the ordering, not real filesystem/registry lookups."""
    monkeypatch.setattr(server, "_is_cursor_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_hermes_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_kimi_session", lambda sid: False)
    monkeypatch.setattr(server, "_session_acp_harness", lambda sid: None)
    monkeypatch.setattr(server, "_is_opencode_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_gemini_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_antigravity_session", lambda sid: False)
    monkeypatch.setattr(server, "_is_devin_cli_session", lambda sid: False)


def test_inject_router_returns_uds_result_for_eligible_source(monkeypatch):
    _stub_inject_router_seams(monkeypatch, is_codex=False)
    # session_live_status returns {} (no tty key) -> has_tty is False, so the
    # worker hand-off branch runs first (F1 order). Decline it here so the
    # call falls through to the UDS hook, same as "worker down".
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    calls = []
    uds_reply = {"ok": True, "via": "uds", "source": "uds", "receipt_id": "m-9", "receipt": "delivered"}

    def fake_try_uds(session_id, text, *, source, mode="send", peer_sender_sid=None):
        calls.append({
            "session_id": session_id,
            "text": text,
            "source": source,
            "mode": mode,
            "peer_sender_sid": peer_sender_sid,
        })
        return uds_reply

    monkeypatch.setattr(server, "_try_uds_peer_delivery", fake_try_uds)
    result = server._inject_text_into_session(
        "target-sid", "please review", source="ask", mode="steer", peer_sender_sid="sender-sid",
    )
    assert result == uds_reply
    assert len(calls) == 1
    assert calls[0]["source"] == "ask"
    assert calls[0]["mode"] == "steer"
    assert calls[0]["peer_sender_sid"] == "sender-sid"


def test_inject_worker_handoff_sends_once_dashboard_side(monkeypatch, tmp_path):
    """F1: a headless target hands off to the worker (routed is not None).
    The dashboard-side router must NOT also dial the peer socket: the
    worker runs its own copy of this same hook, so a dashboard-side send
    here would be a second frame with a second msg_id."""
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    _stub_inject_router_seams(monkeypatch, is_codex=False)
    _stub_inject_router_engine_detectors(monkeypatch)
    monkeypatch.setattr(server, "session_live_status", lambda sid, cwd: {"live": True, "tty": None})
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: {"ok": True, "via": "worker"})
    result = server._inject_text_into_session("target-sid", "hi", source="ask")
    assert result == {"ok": True, "via": "worker"}
    assert len(sent) == 0


def test_inject_dashboard_sends_uds_when_worker_declines(monkeypatch, tmp_path):
    """F1: when the worker hand-off declines (worker down, or _control_plane_
    engine_call otherwise returns None), the dashboard is the only sender
    and must still deliver over uds."""
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    _stub_inject_router_seams(monkeypatch, is_codex=False)
    _stub_inject_router_engine_detectors(monkeypatch)
    monkeypatch.setattr(server, "session_live_status", lambda sid, cwd: {"live": True, "tty": None})
    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    result = server._inject_text_into_session("target-sid", "hi", source="ask")
    assert result["ok"] is True and result["via"] == "uds"
    assert len(sent) == 1


def test_inject_router_skips_uds_hook_for_codex_sessions(monkeypatch):
    _stub_inject_router_seams(monkeypatch, is_codex=True)

    def fail_if_called(*a, **k):
        raise AssertionError("uds hook called for codex")

    monkeypatch.setattr(server, "_try_uds_peer_delivery", fail_if_called)
    codex_reply = {"ok": True, "via": "stub"}
    monkeypatch.setattr(server, "resume_session_codex", lambda sid, text: codex_reply)
    result = server._inject_text_into_session("target-sid", "please review", source="ask")
    assert result == codex_reply


def test_ask_live_tail_prefers_uds_and_skips_keystrokes(monkeypatch, tmp_path):
    sent = []
    _enable_uds(monkeypatch, tmp_path, _dialable_row(tmp_path), sent)
    monkeypatch.setattr(server, "_transcript_peer_receipt", lambda *a, **k: "delivered")
    monkeypatch.setattr(server, "_find_live_spawn_entry_for_session", lambda sid: None)
    monkeypatch.setattr(server, "inject_input_via_keystroke",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("keystroke used")))
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("")
    monkeypatch.setattr(server, "_resolve_conversation_path", lambda sid: str(transcript))
    # ask_session_via_live_tail locates the transcript via _find_session_jsonl
    # (a real ~/.claude/projects scan), not _resolve_conversation_path above.
    # Stub it too so this test does not depend on the real filesystem.
    monkeypatch.setattr(server, "_find_session_jsonl", lambda sid: transcript)
    monkeypatch.setattr(server, "_is_real_tty", lambda tty: True)
    # Make the transcript "reply" as soon as the tail loop looks.
    monkeypatch.setattr(server, "_ask_live_tail_wait_for_reply",
                        lambda *a, **k: {"ok": True, "text": "PONG", "cost_usd": None, "duration_ms": 1, "num_turns": 1, "source": "live-tail"},
                        raising=False)
    status = {"live": True, "tty": "/dev/ttys004", "terminal_app": "Terminal"}
    result = server.ask_session_via_live_tail("target-sid", "ping?", 2000, status)
    assert result.get("ok") is True
    assert result.get("via") == "uds"
    assert len(sent) == 1


def test_ask_and_wait_forwards_peer_sender_to_control_plane(monkeypatch):
    seen = {}

    def fake_route(engine, operation, args, **kw):
        seen["engine"], seen["op"], seen["args"] = engine, operation, args
        return {"ok": True, "text": "routed"}

    monkeypatch.setattr(server, "_control_plane_engine_call", fake_route)
    monkeypatch.setattr(server, "_resolve_local_spawn_session_prefix", lambda sid: sid)
    monkeypatch.setattr(server, "_detect_session_engine", lambda sid: "claude")
    result = server.ask_session_and_wait("target-sid", "ping?", timeout_ms=1500, peer_sender_sid="sender-sid")
    assert result == {"ok": True, "text": "routed"}
    assert (seen["engine"], seen["op"]) == ("claude", "ask")
    assert seen["args"]["peer_sender_sid"] == "sender-sid"
    assert seen["args"]["session_id"] == "target-sid"


def test_worker_ask_consumer_forwards_peer_sender():
    import inspect
    import worker_engines
    src = inspect.getsource(worker_engines)
    idx = src.index("legacy.ask_session_and_wait(")
    assert 'peer_sender_sid=args.get("peer_sender_sid")' in src[idx:idx + 400]


def test_spawn_command_accepts_peer_inbound():
    cmd = server._claude_spawn_command("/usr/local/bin/claude", "claude-sonnet-5", "worker-a", "", {}, effort="")
    idx = cmd.index("--settings")
    assert json.loads(cmd[idx + 1]) == {"crossSessionInbound": "accept"}


def test_headless_resume_command_accepts_peer_inbound(monkeypatch, tmp_path):
    seen = {}

    def fake_popen(cmd, *a, **k):
        seen["cmd"] = cmd
        raise RuntimeError("stop here")

    def fake_repo_from_session(sid):
        return {"cwd": str(tmp_path), "repo_path": str(tmp_path)}

    def fake_ensure_session_jsonl(sid, cwd):
        return {"ok": True}

    monkeypatch.setattr(server, "_control_plane_engine_call", lambda *a, **k: None)
    monkeypatch.setattr(server, "_resolve_claude_bin", lambda: {"available": True, "bin": "/usr/local/bin/claude"})
    monkeypatch.setattr(server, "repo_from_session", fake_repo_from_session)
    monkeypatch.setattr(server, "_ensure_session_jsonl_for_cwd", fake_ensure_session_jsonl)
    monkeypatch.setattr(server.subprocess, "Popen", fake_popen, raising=False)
    try:
        server.resume_session_headless("11111111-2222-4333-8444-555555555555", "hi")
    except RuntimeError:
        pass
    cmd = seen.get("cmd") or []
    assert "--settings" in cmd
    assert json.loads(cmd[cmd.index("--settings") + 1]) == {"crossSessionInbound": "accept"}
