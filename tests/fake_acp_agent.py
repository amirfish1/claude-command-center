#!/usr/bin/env python3
"""Fake ACP agent for CCC smoke tests.

Speaks newline-delimited JSON-RPC 2.0 on stdio, just enough to exercise
server.py's generic ACP client: initialize, session/new, session/prompt
(streams two chunks then answers), session/cancel, and one
session/request_permission roundtrip when the prompt text is "perm".
"""

import json
import sys


def send(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main():
    sessions = 0
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": False}},
                "authMethods": [],
                "agentInfo": {"name": "fake-acp", "version": "0.0.1"},
            }})
        elif method == "session/new":
            sessions += 1
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "sessionId": f"fake-session-{sessions}",
                "configOptions": [{"type": "select", "id": "model", "currentValue": "fake/model", "options": []}],
            }})
        elif method == "session/prompt":
            sid = params.get("sessionId")
            text = ""
            for block in params.get("prompt") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text") or ""
            if text == "perm":
                send({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission", "params": {
                    "sessionId": sid,
                    "toolCall": {"toolCallId": "tc-1", "title": "Bash", "kind": "execute"},
                    "options": [{"optionId": "allow", "name": "Allow"}, {"optionId": "deny", "name": "Deny"}],
                }})
            # One full tool sequence: pending call, streamed rawInput, completed.
            send({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": sid,
                "update": {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "title": "Bash", "kind": "execute", "status": "pending"},
            }})
            send({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": sid,
                "update": {"sessionUpdate": "tool_call_update", "toolCallId": "tc-1", "status": "in_progress", "rawInput": {"command": "echo TEST"}},
            }})
            send({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": sid,
                "update": {"sessionUpdate": "tool_call_update", "toolCallId": "tc-1", "status": "completed",
                           "content": [{"type": "content", "content": {"type": "text", "text": "TEST\n"}}], "rawOutput": "TEST\n"},
            }})
            for chunk in ("Hel", "lo"):
                send({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": sid,
                    "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": chunk}},
                }})
            send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        elif method == "session/cancel":
            pass
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}})


if __name__ == "__main__":
    main()
