---
name: handoff
description: Use to reset context without losing knowledge — it packages this session's state into a file, then spawns ONE fresh successor sibling session via CCC that verifies the package against the repo and takes over the mission. A clean baton pass onto the kanban, not lossy compaction.
allowed-tools: Bash, Read, Write
---

Handoff packages the current session's state into a structured file, then spawns
a single fresh **successor** CCC session that reads the package, verifies its
claims against the actual repo, and continues the mission from a clean context.
The point is a clean successor session on the kanban with all the load-bearing
knowledge intact — not a lossy in-place compaction. It builds on the
spawn/report_to mechanics in the `ccc-orchestration` skill — read that first for
the full Spawn/Inject/Ask API.

## Cost

**1 spawned session.** Each spawn is a real billed session on the user's
kanban — tell the user the count before spawning. Do **not** hand off if the
work is nearly done (just finish it), and note that plain **compaction** already
handles pure context-length pressure for free. Reach for handoff when you
specifically want a **clean successor session** on the kanban — a fresh start
that still knows why every decision was made.

## Setup

```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt 2>/dev/null || echo "${CCC_URL:-http://127.0.0.1:8090}")"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_URL="http://127.0.0.1:8090"
curl -sf --max-time 2 "$CCC_URL/api/version" >/dev/null 2>&1 || CCC_DOWN=1
```

- (a) Run every CCC curl with the **network sandbox disabled** — the Bash
  sandbox blocks loopback and fails spuriously even when CCC is up.
- (b) **URL-encode `repo_path`** in query strings (`+` → `%2B`, space → `%20`).
- (c) Resolve your own session id: `$CLAUDE_SESSION_ID`, else the newest
  `*.jsonl` under `~/.claude/projects/<slugified-cwd>/` (see `ccc-orchestration`).

## Step 1 — write the handoff package to a file

Write it to `handoffs/<yyyy-mm-dd>-<slug>.md` in the repo (or a path the user
names). Note it may belong in `.gitignore` if the mission is private. Required
sections, each of which earns its place:

- **MISSION** — the original goal *and* the current definition of done.
- **DECISIONS** — each decision **with its WHY**. Decisions without whys get
  relitigated by the successor from scratch; the why is the whole point.
- **STATE** — done / in-flight / next, each item tagged with the **file paths**
  it touches.
- **FILE MAP** — the files that matter and why each one matters.
- **GOTCHAS** — dead ends already tried, environment quirks, things that look
  wrong but aren't.
- **OPEN QUESTIONS** — what's genuinely undecided.

**Hard rule:** every STATE claim must be **verifiable against the repo** — the
successor checks `git`, it does not trust prose. If you can't point a claim at a
commit, a file, or a test, it's a guess, not state.

## Step 2 — spawn the successor

Resolve your session id, then POST one spawn against the same repo, `report_to`
you:

```bash
curl -s -X POST "$CCC_URL/api/sessions/spawn" -H "Content-Type: application/json" -d @- <<'JSON'
{
  "prompt": "You are taking over from a previous session. Read the handoff package at /abs/path/to/repo/handoffs/<yyyy-mm-dd>-<slug>.md fully. Then VERIFY its STATE claims against reality: git log, git status, and run any tests it names — trust the repo over the prose, and note every discrepancy you find. Report back: TAKEOVER-ACK, what you verified, any discrepancies found, and your first planned action. Then continue the MISSION described in the package.",
  "repo_path": "/abs/path/to/repo",
  "report_to": "<your-session-id>"
}
JSON
```

Omit `"model"` — the server spawn default (Settings → Spawn defaults) applies;
the user can pass `"model"` explicitly to keep cost down — and pass `"engine"` alongside it, since model names are validated against the target engine (a bare `"model"` fails if the server default engine differs). CCC appends the
return-address footer (`report_to`), so the successor injects its ack back to
you when it's read and verified the package.

## Step 3 — waiting for the ack, then hand off

After spawning, **end your turn.** Tell the user a successor is taking over and
give its session id. The ack arrives by injection (`report_to`) — do **not**
poll or sleep-loop. If it never arrives, check `GET /api/sessions/spawned` for
whether the successor is still alive.

When the ack arrives: relay it to the user, hand them the successor's session
id, and **stop working the mission in this session** — the successor owns it now,
and the user closes this one.

## Dry run

If the arguments contain `dry-run`, print the exact plan — session count (1), the
handoff file path, the section list you'll write, and the full successor prompt —
and POST nothing (and don't write the file either).

## When CCC is down

If `CCC_DOWN=1` or the spawn curl returns connection refused (exit 7), **still
write the package** — it's valuable on its own. Tell the user to start a fresh
session pointed at the package path. Never pretend the successor spawn ran.

## What this is not

- Not compaction — that's lossy, unstructured, and stays in the same session.
  Handoff is structured and starts a clean one.
- Not a group chat — this is a **baton pass**, not collaboration; the old
  session steps aside.
- Not `fleet-lane-dispatch` — that fans out *new* missions to many lanes. Handoff
  continues **one** mission in a fresh session.
