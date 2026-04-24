---
name: ccc-orchestration
description: Spawn, inject into, and ask questions of sibling Claude Code sessions through the Claude Command Center (CCC). Use when you want a persistent peer session that survives the current turn — e.g. delegate "marketing assistant", "deploy babysitter", "research doc writer" work to a long-running sibling whose progress shows up on the user's kanban. Not for one-shot internal subtasks (use the built-in Task tool for those).
---

# CCC Orchestration

Claude Command Center (CCC) is a local HTTP server that watches every Claude Code session on the user's machine and exposes them as a kanban board. This skill lets one Claude session talk to another via CCC's HTTP API.

## When to use

Use CCC when the user asks for **persistent peer sessions** that should survive past the current turn and show up on the kanban:

- "Spin up a marketing assistant session and have it draft three LinkedIn posts."
- "Ask the deploy-watcher session whether the Vercel build is green."
- "Start a research session and check back on it later."

**Don't use CCC for one-shot internal delegation.** If you need to fan out a quick subtask, use the built-in `Task` tool — it's purpose-built for that. CCC sessions live on disk under `~/.claude/projects/` and are visible to the user; treat them as first-class siblings, not throwaway helpers.

## Discovering CCC's URL

CCC writes its base URL on startup. Resolve in this order:

1. `~/.claude/command-center/port.txt` — single line, e.g. `http://127.0.0.1:8090`
2. `$CCC_URL` env var
3. Default: `http://127.0.0.1:8090`

```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
```

If `curl` to the base URL gives "connection refused", CCC isn't running. Tell the user: "CCC isn't running. Start it with `cd ~/dev/claude-command-center && ./run.sh` (or wherever you cloned it) and try again." Don't try to start it yourself.

## Operations

### List existing sessions

```bash
curl -s "$CCC_URL/api/conversations" | jq '.[] | {id, display_name, last_prompt, modified_human}' | head -40
```

Each session has a `session_id` (uuid). Use it as the handle for subsequent calls.

### Spawn a new sibling session

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "You are a marketing assistant. Draft three LinkedIn posts about <topic>. Wait for me to ask follow-ups."}'
```

Returns `{"ok": true, "pid": <int>, "name": "<slug>", "log": "<path>"}`. The new session shows up on the kanban within ~10s. To get its `session_id`, poll `/api/conversations` and match by `last_prompt` or `display_name`.

### Fire-and-forget inject

Send a follow-up message to an existing session without waiting for the reply. Good for "kick it off, check back later":

```bash
curl -s -X POST "$CCC_URL/api/inject-input" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<uuid>", "text": "Also include a Twitter thread variant."}'
```

If the session has a live terminal, CCC types into it. If dormant, CCC auto-resumes it headlessly.

### Ask and wait for the reply

Synchronous "inject and wait for the assistant's final turn":

```bash
curl -s -X POST "$CCC_URL/api/ask" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<uuid>", "text": "What is 2+2?", "timeout_ms": 60000}'
```

Returns:

```json
{
  "ok": true,
  "text": "4",
  "cost_usd": 0.0123,
  "duration_ms": 1840,
  "num_turns": 1
}
```

On timeout: `{"ok": false, "error": "timeout", "partial": "<any text seen so far>"}`. The session keeps running — the timeout only stops your wait, not the work.

`timeout_ms` defaults to 30000 if omitted. Bump it for sessions that do real research / multiple tool calls.

## Patterns

**Delegate and check back later** (preferred for long work):

```bash
# 1. Spawn
curl -s -X POST "$CCC_URL/api/sessions/spawn" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Audit the last 30 days of Vercel deploys for our main project. Report root causes for any failures. Take your time."}'

# 2. (later, possibly after the user nudges you) — find the session_id and ask
SID="$(curl -s "$CCC_URL/api/conversations" | jq -r '.[] | select(.display_name | test("Audit.*Vercel")) | .session_id' | head -1)"
curl -s -X POST "$CCC_URL/api/ask" \
  -H "Content-Type: application/json" \
  -d "{\"session_id\": \"$SID\", \"text\": \"Status update — what have you found?\", \"timeout_ms\": 120000}"
```

**Quick question to a known session**:

```bash
curl -s -X POST "$CCC_URL/api/ask" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "ae4c6da8-2657-498c-bc68-686f01ee760e", "text": "Briefly: what are you currently working on?", "timeout_ms": 30000}'
```

## Error handling

- **`curl: (7) Failed to connect`** — CCC not running. Tell the user, don't auto-start.
- **`{"ok": false, "error": "missing session_id or text"}`** — payload typo.
- **`{"ok": false, "error": "timeout", "partial": "..."}`** — work is ongoing; either retry `/api/ask` with a longer timeout (the same session_id can be re-asked), or tell the user "still working, here's what it's said so far."
- **`{"ok": false, "error": "claude CLI not in PATH"}`** — the spawned/resumed subprocess can't run; user's environment is broken.
- **HTTP 403 with `cross-origin POST rejected`** — only happens if you set an `Origin` header pointing somewhere weird. Don't set `Origin` on these curls.

## Don'ts

- **Don't poll `/api/ask` in a tight loop.** It's synchronous; one call blocks until the result event arrives or the timeout fires.
- **Don't use this for one-shot subtasks.** The built-in `Task` tool is faster and doesn't pollute the user's kanban with throwaway cards.
- **Don't try to read the spawned session's log file directly** to fish out the reply — `/api/ask` already does that for you, and the log path/format is internal to CCC.
- **Don't spawn dozens of sibling sessions at once.** Each one is a real `claude -p` subprocess; the user pays for their tokens. Check `/api/conversations` before spawning to see if one already exists for the topic.
