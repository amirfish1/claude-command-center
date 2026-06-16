---
name: spawn-ux-worker
description: Spawn a repo-scoped UX-fixes worker session that drains only one repo's UX-fixes queue, via the CCC API.
allowed-tools: Bash
---

Spawn a persistent UX-fixes WORKER session for a single repo. The worker drains
**only that repo's project queue** (one ticket at a time, forever, via idle
self-wakeups) — it claims, applies the fix, then closes and advances. Use this
when the user wants a repo to "start working its UX-fixes queue" or "spawn a UX
worker for <repo>".

For one-shot subtasks use the built-in `Task` tool instead — this skill is for a
long-running peer that shows on the kanban.

## 1. Setup

### Port / URL discovery
Do NOT try to start CCC yourself. Find the URL:
```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
```

### Network sandbox constraint
**CRITICAL:** The Bash sandbox blocks loopback connections. Run the CCC curl with
the network sandbox **disabled** (localhost IPC), or it will fail spuriously even
when CCC is up.

### Resolve the target repo
Take the repo from `$ARGUMENTS` (an absolute path, or a repo name to resolve). If
it is a name, resolve it to an absolute path (e.g. under `~/Apps/<name>` or the
user's known repo roots). The path must be an existing directory.

**URL/JSON-encode `repo_path`:** when it contains `+` or spaces, those must be
preserved exactly in the JSON body (do not pre-decode `+` to a space).

## 2. Spawn the worker

```bash
curl -s -X POST "$CCC_URL/api/ux-fixes/spawn-worker" \
  -H 'Content-Type: application/json' \
  -d '{"repo_path": "/abs/path/to/repo"}'
```

Optional fields:
- `"project"`: override the project code (default is derived from the repo
  basename, so you rarely need this).
- `"model"`: model for the spawned session (omit to use CCC's spawn default).
- `"name"`: session name (default `UX worker · <PROJECT>`).
- `"message"`: extra context appended to the worker's starting prompt (e.g.
  `"dev server runs on :3001"`, `"prioritise mobile tickets"`). It is added as
  its own section and does not change how the worker claims/closes tickets.
  Aliases: `"note"`, `"extra"`.

The response is the standard spawn shape plus the resolved project:
`{"ok": true, "session_id": "...", "spawn_id": "123", "engine": "claude",
"repo_path": "...", "cwd": "...", "session_id_pending": false, "project": "CCC"}`.

If `session_id_pending` is true, the session is launching — its id will appear
shortly under `GET /api/sessions/spawned` keyed by `spawn_id`.

## 3. Report back

Tell the user: which repo + project the worker is draining, its `session_id` (or
that it's pending), and that it will keep claiming this project's tickets via idle
wakeups until the queue is empty. The worker scopes every `/api/ux-fixes/claim`
and `/api/ux-fixes/next` call to its project, so it never touches other repos'
tickets.
