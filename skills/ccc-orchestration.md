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
REPO_PATH="${CCC_REPO_PATH:-$(pwd -P)}"
```

## 2. API Operations
All operations (except List) use `curl -s -X POST "$CCC_URL<endpoint>" -H "Content-Type: application/json" -d '<json>'`.

- **List Current Repo (GET):** `/api/sessions?repo_path=<abs path>`
  *Returns the unified session list for one repo. Always check if a session for your topic exists before spawning!*
- **List Spawned Runs (GET):** `/api/sessions/spawned`
  *Returns recent CCC-owned spawns with `spawn_id`, `session_id`, `engine`, `repo_path`, `cwd`, and `spawned_at`. Use this if a spawn response has `session_id_pending: true`.*
- **List All (GET):** `/api/sessions?all=1` (optional `&engine=codex|antigravity|claude`)
  *Returns cross-repo sessions plus the spawned-run registry.*
- **Spawn:** `/api/sessions/spawn` 
  *Payload:* `{"prompt": "...", "repo_path": "/abs/repo", "engine": "claude|codex|antigravity", "model": "..."}`. `repo_path` (or `cwd`) is required. `engine` and `model` are optional; when omitted, CCC uses the server-side defaults from **Settings → Spawn defaults…**. Legacy `gemini` maps to `antigravity`.
  *Returns:* `{"ok": true, "session_id": "...", "spawn_id": "123", "engine": "...", "repo_path": "...", "cwd": "...", "session_id_pending": false}`. Prefer `session_id` immediately; if pending, poll Spawned Runs by `spawn_id`.
- **Inject (Fire & Forget):** `/api/inject-input`
  *Payload:* `{"session_id": "<uuid>", "text": "..."}`. CCC detects the target session's engine.
- **Ask (Sync/Wait):** `/api/ask`
  *Payload:* `{"session_id": "<uuid>", "text": "...", "timeout_ms": 60000}`. 
  *Returns:* `{"ok": true, "text": "reply"}`. On timeout, work continues (you can re-ask or notify user). Requires a real engine `session_id`, not only a pending `spawn_id`.

## 3. Strict Rules
- **No one-shot tasks:** Use the built-in `Task` tool for quick delegation.
- **No tight polling:** `/api/ask` blocks until a reply or timeout.
- **No duplicate spawning:** Check List Current Repo first. Users pay for each spawned session.
- **Handle Errors:** `curl: (7)` means CCC is offline. `timeout` means the assistant is still thinking.
