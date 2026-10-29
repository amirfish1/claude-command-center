#!/usr/bin/env python3
# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: LicenseRef-CCC-Software-License
"""Fake `codex app-server` for tests/test_realtime_voice.py.

Speaks the JSON-RPC-over-stdio subset the voice module needs:
initialize, thread/realtime/listVoices, thread/start,
thread/realtime/start (+ started/sdp notifications), stop, appendSpeech,
and outbound item/tool/call requests. Every inbound line's method is
appended to the file named by FAKE_VOICE_LOG so tests can assert what the
client sent (never any key material — the fake echoes only a boolean).

Behavior knobs (env):
  FAKE_VOICE_MODE:
    ""                 normal flow (webrtc succeeds, ws fails w/o key)
    crash_after_realtime  exit(2) right after realtime/start succeeds
    malformed          emit one non-JSON line mid-stream, keep going
    call_tool          after realtime started, issue item/tool/call
                       ccc_attention
    propose_action     after realtime started, issue item/tool/call
                       ccc_propose_action (inject)
    approval_probe     after realtime started, issue an approval request
                       and echo the client's decision as a client_response log
    sideband_hang      webrtc: never send the sdp notification (timeout path)
    sideband_error     webrtc: emit thread/realtime/error instead of sdp
  FAKE_VOICE_LOG       file to append seen method names to

The fake mirrors the real auth gate: `transport:{type:"websocket"}` fails
with "realtime conversation requires API key auth" unless the child env
carries OPENAI_API_KEY. `webrtc` never needs a key (subscription path).
"""
import base64
import json
import os
import sys

MODE = os.environ.get("FAKE_VOICE_MODE", "")
LOG = os.environ.get("FAKE_VOICE_LOG", "")
THREAD_ID = "thr_fake_voice_1"
HAS_KEY = bool(os.environ.get("OPENAI_API_KEY"))


def _log(line):
    if LOG:
        try:
            with open(LOG, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def respond(rid, result=None, error=None):
    out = {"id": rid}
    if error is not None:
        out["error"] = error
    else:
        out["result"] = result if result is not None else {}
    send(out)


def start_realtime(transport):
    send({"method": "thread/realtime/started", "params": {"version": "v3"}})
    if transport == "webrtc":
        if MODE == "sideband_hang":
            return  # created the call, sideband join silently fails (issue #35094)
        if MODE == "sideband_error":
            send({"method": "thread/realtime/error",
                  "params": {"message": "sideband join failed: 404 call_id_not_found"}})
            return
        send({"method": "thread/realtime/sdp",
              "params": {"sdp": "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=fake\r\nt=0 0\r\n"}})
    else:
        # Websocket transport streams audio back over the JSON-RPC lane.
        chunk = base64.b64encode(b"\x00\x01" * 1200).decode()
        send({"method": "thread/realtime/outputAudio/delta",
              "params": {"audio": {"data": chunk, "sampleRate": 24000,
                                   "numChannels": 1, "samplesPerChannel": 1200}}})


def post_start():
    """Secondary behavior after realtime/start completes."""
    if MODE == "malformed":
        sys.stdout.write("this is not json\n")
        sys.stdout.flush()
    elif MODE == "call_tool":
        send({"id": 900, "method": "item/tool/call", "params": {
            "tool": "ccc_attention", "arguments": {"limit": 3},
            "threadId": THREAD_ID, "turnId": "turn1", "callId": "c1"}})
    elif MODE == "propose_action":
        send({"id": 901, "method": "item/tool/call", "params": {
            "tool": "ccc_propose_action",
            "arguments": {"kind": "inject",
                          "params": {"session_id": "sess_12345", "text": "say hi"},
                          "reason": "user asked"},
            "threadId": THREAD_ID, "turnId": "turn1", "callId": "c2"}})
    elif MODE == "approval_probe":
        send({"id": 902, "method": "execCommandApproval", "params": {
            "conversationId": THREAD_ID, "callId": "c3",
            "command": ["rm", "-rf", "/"], "cwd": "/"}})
    elif MODE == "crash_after_realtime":
        sys.exit(2)


def main():
    _log("has_openai_key:" + str(HAS_KEY).lower())
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        method = msg.get("method")
        rid = msg.get("id")
        _log(str(method or ("response:" + str(rid))))

        if method == "initialize":
            respond(rid, {"serverInfo": {"name": "fake-appserver", "version": "0"}})
        elif method == "initialized":
            pass
        elif method == "thread/realtime/listVoices":
            # Mirrors codex-cli 0.160.0's live catalog.
            respond(rid, {"voices": {
                "v1": ["juniper", "maple", "spruce", "ember", "vale",
                       "breeze", "arbor", "sol", "cove"],
                "v2": ["alloy", "ash", "ballad", "coral", "echo", "sage",
                       "shimmer", "verse", "marin", "cedar"],
                "defaultV1": "cove", "defaultV2": "marin"}})
        elif method == "thread/start":
            respond(rid, {"thread": {"id": THREAD_ID}})
        elif method == "thread/realtime/start":
            params = msg.get("params") or {}
            transport = ((params.get("transport") or {}).get("type")) or "websocket"
            # Surface params for the test to inspect via the log file.
            _log("rt_params:" + json.dumps({
                "version": params.get("version"),
                "voice": params.get("voice"),
                "outputModality": params.get("outputModality"),
                "transport_type": transport,
                "has_initialItems": bool(params.get("initialItems")),
            }))
            if transport == "websocket" and not HAS_KEY:
                # The real API-key gate: WS transport demands key auth.
                respond(rid, error={"code": -32000,
                                    "message": "realtime conversation requires API key auth"})
                continue
            respond(rid, {"realtimeSessionId": "rts_fake"})
            start_realtime(transport)
            post_start()
        elif method == "thread/realtime/appendAudio":
            respond(rid, {})
        elif method == "thread/realtime/appendSpeech":
            respond(rid, {})
            # Simulate the spoken confirmation outcome.
            send({"method": "thread/realtime/transcript/delta",
                  "params": {"role": "assistant", "delta": "Done. "}})
            send({"method": "thread/realtime/transcript/done",
                  "params": {"role": "assistant", "text": "Done."}})
        elif method == "thread/realtime/stop":
            respond(rid, {})
            send({"method": "thread/realtime/closed", "params": {"reason": "user"}})
        elif method == "item/tool/call":
            respond(rid, {})
        elif method in ("execCommandApproval", "applyPatchApproval",
                        "item/commandExecution/requestApproval",
                        "item/fileChange/requestApproval",
                        "item/permissions/requestApproval",
                        "mcpServer/elicitation/request",
                        "item/tool/requestUserInput"):
            # The client response is a reply to OUR request id — it arrives
            # with rid set and result/decision fields; this branch is for the
            # inbound *request* case only, which shouldn't happen.
            pass
        elif rid is not None and "result" in msg:
            # A response to a request WE sent (tool call / approval probe).
            # Log the payload so tests can assert what CCC answered.
            _log("client_response:" + json.dumps(msg.get("result")))
            send({"method": "test/client_response",
                  "params": {"for_id": rid, "result": msg.get("result")}})
        # Unknown methods: ignore.


if __name__ == "__main__":
    main()

