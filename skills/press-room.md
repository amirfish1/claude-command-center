---
name: press-room
description: Use when one release needs to go out across several channels at once — spawns one writer sibling session per channel via CCC (changelog, LinkedIn, X thread, optional blog) off a single source pack, then collates the drafts.
allowed-tools: Bash, Read, Write
---

Press-room turns one release into N channel drafts in parallel: you build a
single source pack, spawn one writer session per channel each constrained to its
format, and collate the returned drafts into per-channel files. It builds on the
spawn/report_to mechanics in the `ccc-orchestration` skill — read that first for
the full Spawn/Inject/Ask API.

## Cost

**1 spawned session per channel** — default **3** (changelog, LinkedIn, X); the
optional blog post makes **4**. Each spawn is a real billed session on the user's
kanban — state the count before spawning.

Not for a one-channel announcement — write that yourself, or use `a-b-copy` if you
want to compare two voices on it.

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
`~/.claude/projects/<slugified-cwd>/` (see `ccc-orchestration`).

## Step 1 (you): build the source pack ONCE

Write a single source-of-truth file (e.g. `/tmp/press-room-<version>.md`) so the
channels can't drift apart or invent claims. Include:

- The release diff or commit list — `git log vPREV..vNEW --oneline` plus the key
  diffs.
- What user-visible pain each change removes.
- Relevant links/URLs the posts may use.

Every writer reads this file; none of them re-derives the facts.

## Step 2: spawn one writer per channel, in parallel

All with `report_to` set to your own session id, each pointing at the source-pack
file plus its channel constraints. Omit `"model"` so the server spawn default
applies — the user can pass `"model"` explicitly to keep cost down.

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "Write the <CHANNEL> draft for this release. Read the source pack at /tmp/press-room-<version>.md — it is the ONLY source of facts; invent nothing. Channel constraints: <CONSTRAINTS>. Report the draft only, no commentary.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Bake in the per-channel `<CONSTRAINTS>`:

- **CHANGELOG** — Keep-a-Changelog voice, user-visible changes only, no
  marketing adjectives.
- **LINKEDIN** — 150-250 words, hook in the first line, one claim + one proof,
  CTA link, no hashtag spam.
- **X / THREAD** — first post stands alone, every post ≤280 chars, 3-6 posts,
  one idea per post.
- **BLOG** (opt-in) — 500-900 words, problem → what changed → how to use it, code
  examples only from the source pack.

## Waiting for the report(s)

After spawning, **end your turn** and tell the user which channels are being
drafted. Drafts arrive by injection (the `report_to` footer CCC appends). Never
poll or sleep-loop (see `ccc-orchestration`). If a draft never arrives, check
`GET /api/sessions/spawned` for whether that writer's session is still alive.

## Step 3: collate

As drafts arrive, write each into `release-comms/<version>/<channel>.md` (one file
per channel) and present the full set to the user. **Drafts only** — nothing is
posted or published; publishing is a human decision.

## Dry run

If the arguments contain `dry-run`, print the channel list and session count, the
source-pack path, and each channel's constraint block and prompt — then POST
nothing and write no comms files.

## When CCC is down

If `CCC_DOWN=1` or a curl returns connection refused (exit 7): do not pretend it
ran. Fall back to one `Task`-tool subagent per channel with the same prompts —
same output, less visibility — and tell the user CCC was unreachable.

## What this is not

Not one session writing all channels — the voices bleed and the X thread reads
like the blog. Not `a-b-copy` (that compares two voices on ONE piece; this fans
one release out to MANY channels).
