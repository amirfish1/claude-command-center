# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Extracted from server.py (originally lines 30052-30416).

Part of the server.py decomposition; see
CCC-private-docs/plans/server-py-decomposition.md. Names still living
in server.py are reached via `_core` at call time."""

from __future__ import annotations

from pathlib import Path
import json
import threading
import time

from ccc_server import core as _core

# ---------------------------------------------------------------------------
# wire.jsonl tail (KIMI-FIXES-3): TUI-originated turns for attached sessions.
#
# Each `kimi acp` process only streams ITS OWN turns. A session the user also
# drives from the kimi TUI writes every turn to
# ~/.kimi-code/sessions/<wd>/<sid>/agents/main/wire.jsonl (event-sourced; see
# docs/kimi-code-reference.md §5). This watcher tails the wire of ATTACHED
# sessions and folds new turns into the CCC conv stream, so the CCC pane shows
# TUI-originated work live too.
#
# Dedup: a CCC-driven turn writes the same content to the wire — so folding
# pauses while a turn is active or a replay is in flight, and every candidate
# row is content-matched against the recent in-memory events before emit.
# Wire events carry COMPLETE message parts (deltas are live-only, never
# persisted), so one row per message is the natural grain here.
# ---------------------------------------------------------------------------

_ACP_WIRE_TAIL = {"thread": None, "stop": False}
_ACP_WIRE_TAIL_POLL_S = 1.0
_ACP_WIRE_DEDUP_WINDOW = 50


def _acp_wire_path(harness, sid):
    """The harness session's wire.jsonl, or None when unknown/not applicable."""
    if harness != "kimi":
        return None
    try:
        idx = _core._kimi_session_index()
        session_dir = (idx.get(sid) or {}).get("session_dir") or ""
    except Exception:
        return None
    if not session_dir:
        return None
    return Path(session_dir) / "agents" / "main" / "wire.jsonl"


def _acp_remote_turn_busy_error(message):
    """True for the Kimi ACP rejection emitted while another turn owns a session."""
    text = str(message or "").lower()
    return "cannot launch a new turn while another turn" in text and "active" in text


def _kimi_wire_turn_active(sid):
    """Read Kimi's latest durable step boundary.

    CCC's ACP state only knows about turns CCC launched. A Kimi TUI or another
    client can own the same session, so the wire is the authoritative pre-send
    guard for those external turns.
    """
    if time.time() < float(_core._KIMI_WIRE_BUSY_SUPPRESS_UNTIL.get(sid, 0) or 0):
        return False
    path = _core._acp_wire_path("kimi", sid)
    if path is None:
        return False
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - (4 * 1024 * 1024)))
            raw = fh.read()
    except OSError:
        return False
    for line in reversed(raw.decode("utf-8", "replace").splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "context.append_loop_event":
            continue
        loop = event.get("event") or {}
        kind = loop.get("type")
        if kind == "step.begin":
            return True
        if kind == "step.end":
            # tool_use is an intermediate model step; Kimi still owns the
            # enclosing turn while it runs the tool and starts the next step.
            return str(loop.get("finishReason") or "") == "tool_use"
    return False


def _acp_wire_recent_texts(state, limit=_ACP_WIRE_DEDUP_WINDOW):
    """(kind, normalized-text, tool-id-suffix) tuples of the last N events,
    used to suppress re-folding rows CCC already emitted via the ACP stream."""
    seen_user, seen_assistant, seen_tools = set(), set(), set()
    for ev in list(state.get("events") or [])[-limit:]:
        etype = ev.get("type")
        if etype == "user_text":
            seen_user.add(" ".join(str(ev.get("text") or "").split()))
        elif etype == "assistant":
            for b in ev.get("blocks") or []:
                if b.get("kind") in ("text", "thinking"):
                    seen_assistant.add(" ".join(str(b.get("text") or "").split()))
                elif b.get("kind") == "tool_use":
                    tid = str(b.get("id") or "")
                    if tid:
                        seen_tools.add(tid.rsplit(":", 1)[-1])
        elif etype == "tool_result":
            tid = str(ev.get("tool_use_id") or "")
            if tid:
                seen_tools.add(tid.rsplit(":", 1)[-1])
    return seen_user, seen_assistant, seen_tools


def _kimi_wire_token_usage(raw):
    """Normalize Kimi's durable wire usage into the shared chip shape."""
    if not isinstance(raw, dict):
        return None
    fresh = _core._codex_int(raw.get("inputOther"))
    cached = _core._codex_int(raw.get("inputCacheRead"))
    created = _core._codex_int(raw.get("inputCacheCreation"))
    output = _core._codex_int(raw.get("output"))
    if not (fresh or cached or created or output):
        return None
    return {
        "input_tokens": fresh,
        "cache_read_input_tokens": cached,
        "cache_creation_input_tokens": created,
        "output_tokens": output,
    }


def _attach_kimi_wire_usage(state, usage):
    """Attach one completed Kimi model-call's usage to its visible row."""
    if not isinstance(usage, dict):
        return False
    for event in reversed(list(state.get("events") or [])):
        if event.get("type") != "assistant":
            continue
        fresh = _core._codex_int(usage.get("input_tokens"))
        cached = _core._codex_int(usage.get("cache_read_input_tokens"))
        created = _core._codex_int(usage.get("cache_creation_input_tokens"))
        output = _core._codex_int(usage.get("output_tokens"))
        event["tokens_in"] = fresh + cached + created
        event["tokens_cached"] = cached
        event["tokens_out"] = output
        event["token_usage"] = dict(usage)
        return True
    return False


def _acp_wire_fold(harness, sid, events):
    """Fold a batch of wire.jsonl events into the conv stream (dedup'd)."""
    with _core._ACP_LOCK:
        state = _core._acp_session(harness, sid)
        if state is None or not (state.get("wire_watch") or state.get("attached")):
            return
        if state.get("status") == "active" or state.get("replay") is not None:
            return
        seen_user, seen_assistant, seen_tools = _acp_wire_recent_texts(state)
        # Pre-index this batch's tool results so a tool.call folded below can
        # carry its output inline — call and result almost always land in the
        # same tail batch. Leftover results emit standalone tool_result rows.
        tool_results = {}
        for ev in events:
            if ev.get("type") != "context.append_loop_event":
                continue
            loop = ev.get("event") or {}
            if loop.get("type") != "tool.result":
                continue
            rid = str(loop.get("toolCallId") or "")
            if not rid:
                continue
            res = loop.get("result") or {}
            out = res.get("output")
            if isinstance(out, str):
                out_text = out
            elif isinstance(out, list):
                out_text = "\n".join(
                    str(p.get("text") or "") for p in out
                    if isinstance(p, dict) and p.get("type") == "text"
                )
            else:
                out_text = ""
            tool_results[rid] = (out_text.strip(), bool(res.get("isError")))

        def _attach_result(rid, res):
            """Best-effort in-place attach to an already-folded tool_use row
            (covers results whose call landed in an earlier tail batch)."""
            out_text, is_error = res
            for ev in reversed(list(state.get("events") or [])[-200:]):
                if ev.get("type") != "assistant":
                    continue
                for b in ev.get("blocks") or []:
                    if b.get("kind") != "tool_use":
                        continue
                    bid = str(b.get("id") or "")
                    if bid == rid or bid.rsplit(":", 1)[-1] == rid:
                        if out_text:
                            b["output_preview"] = out_text[:400]
                        b["tool_status"] = "failed" if is_error else "completed"
                        return True
            return False

        for ev in events:
            etype = ev.get("type")
            if etype == "context.append_message":
                msg = ev.get("message") or {}
                if msg.get("role") != "user":
                    continue
                origin = msg.get("origin") or {}
                if origin and origin.get("kind") not in (None, "user"):
                    continue
                text = "".join(
                    str(b.get("text") or "") for b in (msg.get("content") or [])
                    if isinstance(b, dict) and b.get("type") == "text"
                ).strip()
                if not text or _core._is_transcript_control_text(text):
                    continue
                norm = " ".join(text.split())
                if norm in seen_user:
                    continue
                seen_user.add(norm)
                _core._acp_emit_event_unlocked(harness, sid, {
                    "type": "user_text", "text": text, "via": "wire-tail",
                }, save=True)
            elif etype == "context.append_loop_event":
                loop = ev.get("event") or {}
                ltype = loop.get("type")
                if ltype == "content.part":
                    part = loop.get("part") or {}
                    if part.get("type") == "text":
                        text = str(part.get("text") or "").strip()
                        kind = "text"
                    elif part.get("type") == "think":
                        text = str(part.get("think") or "").strip()
                        kind = "thinking"
                    else:
                        continue
                    if not text:
                        continue
                    norm = " ".join(text.split())
                    # Containment, not just equality: the finalized turn text
                    # can be a truncated tail (ACP text cap) or a multi-step
                    # concatenation of this wire part, so exact matching lets
                    # the same reply render twice (partial + full). The 80-char
                    # floor keeps a short legit message from being swallowed
                    # by an unrelated long one.
                    if norm in seen_assistant or any(
                        (len(norm) >= 80 and norm in prev)
                        or (len(prev) >= 80 and prev in norm)
                        for prev in seen_assistant
                    ):
                        continue
                    seen_assistant.add(norm)
                    _core._acp_emit_event_unlocked(harness, sid, {
                        "type": "assistant",
                        "message_id": f"acp-wire-{state['next_line']}",
                        "blocks": [{"kind": kind, "text": text}],
                    }, save=True)
                elif ltype == "tool.call":
                    raw_id = str(loop.get("toolCallId") or "")
                    if raw_id and raw_id in seen_tools:
                        res = tool_results.pop(raw_id, None)
                        if res is not None:
                            _attach_result(raw_id, res)
                        continue
                    if raw_id:
                        seen_tools.add(raw_id)
                    name = str(loop.get("name") or "tool")
                    args = loop.get("args")
                    detail = (
                        _core._tool_use_detail(name, args, max_len=160)
                        if isinstance(args, dict) and args else ""
                    )
                    block = {
                        "kind": "tool_use", "name": name,
                        "detail": detail, "id": raw_id,
                    }
                    res = tool_results.pop(raw_id, None)
                    if res is not None:
                        out_text, is_error = res
                        if out_text:
                            block["output_preview"] = out_text[:400]
                        block["tool_status"] = "failed" if is_error else "completed"
                    else:
                        # No result yet — the tool is genuinely in flight
                        # (TUI-originated turn). Mark it running so the pane
                        # shows the pulsing dot instead of a false "done";
                        # _attach_result flips it when the result lands, and
                        # the client settles dangling rows once idle.
                        block["tool_status"] = "running"
                    _core._acp_emit_event_unlocked(harness, sid, {
                        "type": "assistant",
                        "message_id": f"acp-wire-{state['next_line']}",
                        "blocks": [block],
                    }, save=True)
                elif ltype == "step.end":
                    _attach_kimi_wire_usage(state, _kimi_wire_token_usage(loop.get("usage")))
                    finish = str(loop.get("finishReason") or "end_turn")
                    # Dedup: step.end carries no content to key on — skip when
                    # the last folded row is the same result (re-fold of an
                    # already-covered step).
                    recent = list(state.get("events") or [])[-1:]
                    if (recent and recent[0].get("type") == "result"
                            and recent[0].get("subtype") == finish):
                        continue
                    _core._acp_emit_event_unlocked(harness, sid, {
                        "type": "result", "subtype": finish,
                    }, save=True)
            elif etype == "usage.record" and ev.get("usageScope") in (None, "turn"):
                _attach_kimi_wire_usage(state, _kimi_wire_token_usage(ev.get("usage")))
            # turn.prompt duplicates context.append_message(user); unknown and
            # observability records (llm.*, config.update, …)
            # are skipped by design.
        # Results whose call wasn't in this batch: attach to the already-folded
        # row when possible, else emit a Claude-shaped standalone tool_result.
        for rid, res in tool_results.items():
            if rid in seen_tools:
                _attach_result(rid, res)
                continue
            out_text, is_error = res
            _core._acp_emit_event_unlocked(harness, sid, {
                "type": "tool_result",
                "text": out_text[:400],
                "tool_use_id": rid,
                "is_error": is_error,
                "via": "wire-tail",
            }, save=True)


def _acp_wire_tail_loop():
    while not _ACP_WIRE_TAIL.get("stop"):
        try:
            _acp_wire_tail_tick()
        except Exception:
            pass
        time.sleep(_ACP_WIRE_TAIL_POLL_S)


def _acp_wire_tail_tick():
    with _core._ACP_LOCK:
        sessions = [
            (sid, dict(state))
            for sid, state in (_core._ACP_SESSION_STATE.get("kimi") or {}).items()
            if state.get("wire_watch") or state.get("attached")
        ]
    for sid, snap in sessions:
        path = _core._acp_wire_path("kimi", sid)
        if path is None:
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        with _core._ACP_LOCK:
            state = _core._acp_session("kimi", sid)
            if state is None:
                continue
            tail = state.setdefault("wire_tail", {"offset": -1, "partial": ""})
            if tail["offset"] < 0:
                # First sight: skip history (already backfilled by the
                # session/load replay) — only NEW appends fold.
                tail["offset"] = size
                continue
            if size < tail["offset"]:
                tail["offset"] = 0  # rewritten/truncated file
            offset = tail["offset"]
        if size <= offset:
            continue
        try:
            with path.open("rb") as f:
                f.seek(offset)
                raw = f.read(size - offset)
        except OSError:
            continue
        text = raw.decode("utf-8", "replace")
        partial = tail["partial"] + text
        lines = partial.split("\n")
        tail["partial"] = lines.pop() if lines else ""
        with _core._ACP_LOCK:
            state = _core._acp_session("kimi", sid)
            if state is None:
                continue
            state["wire_tail"]["offset"] = size
            state["wire_tail"]["partial"] = tail["partial"]
        batch = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                batch.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        if batch:
            _core._acp_wire_fold("kimi", sid, batch)


def _acp_wire_tail_start(harness):
    """Start the wire tail watcher once (kimi only, no-op otherwise)."""
    if harness != "kimi":
        return
    if _ACP_WIRE_TAIL.get("thread") is not None:
        return
    t = threading.Thread(target=_acp_wire_tail_loop, daemon=True, name="acp-wire-tail")
    _ACP_WIRE_TAIL["thread"] = t
    t.start()


def _acp_shutdown_all():
    try:
        harnesses = list(_core._ACP_HARNESSES)
    except AttributeError:
        # atexit can outlive the server module (test suite pops it)
        return
    for harness in harnesses:
        conn = _core._ACP_CONNS.pop(harness, None)
        if conn and conn.get("transport"):
            conn["transport"].close()
