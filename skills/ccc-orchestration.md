---
name: ccc-orchestration
description: Spawn, inject into, and ask questions of persistent sibling sessions via Claude Command Center (CCC).
allowed-tools: Bash
---

Interact with long-running peer sessions via the CCC HTTP server. **Use only for persistent tasks** (e.g., "marketing assistant", "deploy watcher") that need to show on the user's kanban. **For one-shot subtasks, use the built-in `Task` tool instead.**

## 1. Setup
Find the CCC URL. DO NOT try to start CCC yourself; if `curl` fails, tell the user to start it.
```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
```

## 2. API Operations
All operations (except List) use `curl -s -X POST "$CCC_URL<endpoint>" -H "Content-Type: application/json" -d '<json>'`.

- **List (GET):** `/api/conversations`
  *Returns session list. Always check if a session for your topic exists before spawning!*
- **Spawn:** `/api/sessions/spawn` 
  *Payload:* `{"prompt": "..."}`. Poll List to get the new `session_id`.
- **Inject (Fire & Forget):** `/api/inject-input`
  *Payload:* `{"session_id": "<uuid>", "text": "..."}`
- **Ask (Sync/Wait):** `/api/ask`
  *Payload:* `{"session_id": "<uuid>", "text": "...", "timeout_ms": 60000}`. 
  *Returns:* `{"ok": true, "text": "reply"}`. On timeout, work continues (you can re-ask or notify user).

## 3. Strict Rules
- **No one-shot tasks:** Use the built-in `Task` tool for quick delegation.
- **No tight polling:** `/api/ask` blocks until a reply or timeout.
- **No duplicate spawning:** Check List first. Users pay for each spawned session.
- **Handle Errors:** `curl: (7)` means CCC is offline. `timeout` means the assistant is still thinking.
