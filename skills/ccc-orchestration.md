---
name: ccc-orchestration
description: Spawn, inject into, and ask questions of persistent sibling sessions via Claude Command Center (CCC).
allowed-tools: Bash
---

Interact with long-running peer sessions via the CCC HTTP server. **Use only for persistent tasks** (e.g., "marketing assistant", "deploy watcher") that need to show on the user's kanban. **For one-shot subtasks, use the built-in `Task` tool instead.**

## 1. Setup
### Port and Path Discovery
Find the CCC URL. DO NOT try to start CCC yourself.
```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
REPO_PATH="${CCC_REPO_PATH:-$(pwd -P)}"
```

### 1.2 Network Sandbox Constraint
**CRITICAL:** The Bash sandbox blocks loopback connections. All CCC curls from the Bash tool can fail purely due to network sandboxing, not because CCC is down. **You must run all CCC curls with the network sandbox disabled (localhost IPC).** Ensure your tool execution options disable sandboxing for these requests.

### Identifying Your Own Session UUID
If you need to know your own session UUID (e.g. to register yourself in a chat or trace your own session) and `$CLAUDE_SESSION_ID` is empty or unset in your Bash shell:
1. Locate the project directory under `~/.claude/projects/` corresponding to your slugified current working directory (e.g., `/Users/amirfish/Apps/BYM+Finie` becomes `~/.claude/projects/-Users-amirfish-Apps-BYM+Finie`).
2. Find the newest `*.jsonl` file in that directory; because you are actively executing, your active session transcript is the most recently modified.
3. The basename of that file (without the `.jsonl` extension) is your session UUID.

## 2. API Operations
All operations (except List) use `curl -s -X POST "$CCC_URL<endpoint>" -H "Content-Type: application/json" -d '<json>'`.

### URL-Encoding repo_path
**CRITICAL:** Any parameter or payload value for `repo_path` must be properly URL-encoded (especially `+` to `%2B` and spaces to `%20`).
- *Example:* `/Users/amirfish/Apps/BYM+Finie` must be sent as `/Users/amirfish/Apps/BYM%2BFinie`.
- Failure to URL-encode characters like `+` will result in HTTP 400 Bad Request because `+` is decoded as a space by the server.

### Avoiding Slow List Calls
- **List endpoints can be slow:** `/api/sessions?all=1` and `/api/sessions?repo_path=...` can exceed 15s on repositories with many transcripts.
- **List is skippable:** You only need List as an anti-double-spawn guard. If you already have the target `session_id`, do not call List; proceed directly to **Inject** or **Ask**.
- If List is genuinely needed, use a generous timeout (e.g. `curl --max-time 30`).
- **Lightweight Health Probe:** Do not run `/api/sessions?all=1` to check if CCC is alive. Use `/api/version` instead, which is lightweight and fast.

### Operations
- **Lightweight Health Check (GET):** `/api/version`
  *Returns the version of CCC. Use this to quickly verify CCC is up and running.*
- **List Current Repo (GET):** `/api/sessions?repo_path=<URL-encoded abs path>`
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

### Group Chat Creation (UI-Only)
There is no programmatic API (such as `/api/group-chat/create` or `/api/group-chat/add`) to create group chats or register participants by UUID. Chat creation must be done manually via the CCC UI:
1. Open the CCC UI in your browser at `http://127.0.0.1:8090`.
2. Navigate to the **Chats** or **Group Chats** tab.
3. Click **Create New Chat**.
4. Enter the topic/title.
5. Select or search for the participant sessions by UUID to invite them.
6. Click **Create/Start**. This generates the `.md` chat file and its `.json` sidecar (setting up `name_map` and enabling CCC wakeups and participant re-injection).

## 3. Strict Rules
- **No one-shot tasks:** Use the built-in `Task` tool for quick delegation.
- **No tight polling:** `/api/ask` blocks until a reply or timeout.
- **No duplicate spawning:** Check List Current Repo first. Users pay for each spawned session.
- **Triage Errors:**
  If a `curl` call fails, do not immediately assume CCC is offline. Triage the failure based on the exit code or HTTP status:
  * **exit 7 (Connection Refused):** CCC is genuinely down. Ask the user to start CCC.
  * **exit 28 (Timeout):** CCC is up but slow/under load. Retry with a longer timeout (e.g., `--max-time 30`), do not declare it down.
  * **HTTP status 000 (or connection failure) while sandboxed:** The loopback connection is blocked by the sandboxed environment. Retry with the network sandbox disabled before concluding anything.
