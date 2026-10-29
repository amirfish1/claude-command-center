---
name: standup
description: Use when you want a one-line status from every live sibling session in a repo (or across repos) collated into a standup digest — queries siblings via CCC's `/api/ask` and spawns nothing.
allowed-tools: Bash
---

Standup fans one status question out to every live sibling session and collates
the answers into a short digest, with blocked sessions surfaced at the top. It
uses CCC's `/api/ask` and spawns **nothing** — it only interrupts each target
session for a single turn. It builds on the spawn/report_to mechanics in the
`ccc-orchestration` skill — read that first for the full Spawn/Inject/Ask API.

## Cost

**0 spawned sessions** — this is the free one in the pack. BUT each ask consumes
a turn in the target session (it briefly interrupts whatever that session is
doing), so this is a 1-2x/day digest, not a monitor loop. Never run it on an
interval shorter than hours. If you just want to know whether one specific
session is alive, don't fan out — ask that one session directly.

## Setup

```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_URL="http://127.0.0.1:8090"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_DOWN=1
```

Notes: (a) run every CCC curl with the network sandbox **disabled** — the Bash
sandbox blocks loopback and fails spuriously even when CCC is up. (b) URL-encode
`repo_path` in query strings (`+` → `%2B`, space → `%20`). (c) Resolve your own
session id from `$CLAUDE_SESSION_ID`, else the newest `*.jsonl` under
`~/.claude/projects/<slugified-cwd>/` (see `ccc-orchestration`) — you need it to
exclude yourself from the roll-call. The newest-file fallback assumes you are
the most recently active session in that repo; in a subagent or multi-session
context that can pick the wrong file, so sanity-check the pick (its content
should be *your* conversation) before trusting it.

A green `/api/version` does not prove the session-list endpoint works — that's
a much heavier code path. Treat Step 1's own timeout handling as the real
health check for it.

## Step 1: list the sessions

`repo_path` is the absolute path of the repo whose sessions you want — for
"this repo" use `pwd -P` (URL-encoded).

```bash
curl -s --max-time 30 "$CCC_URL/api/sessions?repo_path=<url-encoded-abs-path>"
```

For a cross-repo standup use `?all=1` instead — warn the user it can be slow and
use `--max-time 30`.

**If the list call times out (curl exit 28), that is not "CCC is down"** — the
server is up but the list is hung or slow (large archives). Retry once with
`--max-time 120`; if it still times out, report the hang honestly and stop —
do not fabricate a roster. If you already know the target session ids another
way (e.g. `GET /api/sessions/children?parent=<your-id>` for sessions you
spawned), you can skip the list entirely and go straight to Step 3.

## Step 2: filter to live, exclude yourself

Keep only LIVE sessions (use the liveness field the API returns on each row) and
drop your own session id. Resolve display names from **`display_name`** — rows
have no `name` or `title` field (see `ccc-orchestration`). If nothing is live,
say so and stop; there's nothing to collate.

## Step 3: ask each live session, SEQUENTIALLY

Never parallel-hammer the sessions — one ask at a time.

```bash
curl -s -X POST "$CCC_URL/api/ask" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "session_id": "<uuid>",
  "text": "Standup check-in. Reply in ONE line: current task, status (on-track / blocked / done), and your blocker if any. Then resume your work.",
  "timeout_ms": 60000
}
JSON
```

Check the whole envelope before calling it a miss: a response of
`{"ok": false, "error": "timeout", "partial": "..."}` often carries the full
one-liner in `partial` — the target answered but ended its turn without a
formal reply. **A non-empty `partial` IS the answer.** Only a timeout with no
`partial` counts as `(no answer — busy)`. Some engines do not reply to asks at
all, so treat a timeout as **busy, never dead**.

## Step 4: collate the digest

Build a short markdown table — `display_name`, the one-liner, and a blocked
column. Put any BLOCKED rows **at the top** and flag them to the user explicitly.
Example:

| Session | Status | One-liner |
|---|---|---|
| deploy-watcher | 🔴 blocked | waiting on notary profile |
| ux-worker CCC | on-track | fixing Flow edge selection |
| docs-lane | (no answer — busy) | — |

## Dry run

If the arguments contain `dry-run`, list the live sessions that WOULD be asked
(display names + ids) and the exact question text — then POST no asks.

## When CCC is down

If `CCC_DOWN=1` or a curl returns connection refused (exit 7): there is nothing to
collate. Report that CCC is down and stop — do not fabricate a digest.

## What this is not

Not a group chat (that is a running discussion; this is one question fanned out
with a collated answer, and the sessions never see each other's replies). Not a
monitor — respect the no-tight-polling rule in `ccc-orchestration`; run it a
couple of times a day, not on a timer.
