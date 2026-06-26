# Watchtower — Product Manifest

> Name: **Watchtower** (locked). Pairs with **Command Center** as an "ops / control-room"
> family (Command Center = the workspace; Watchtower = the autonomous-ops layer that
> watches the fleet).

## What it is

A way to run a fleet of AI coding agents **unattended** and **know which ones to trust.**

You drop work into a queue. Agent workers drain it one ticket at a time — claim, fix,
verify, commit, next. A watcher keeps an eye on the whole thing and **trusts the queue,
not the agent**: if there are open tickets and no worker is actually making progress, it
flags it and wakes the worker. You glance at one strip — green means draining, red means
stuck — instead of babysitting terminals.

It makes background agent work **legible**: a list that empties itself and turns red when
something's wrong. No new habits, no IDE to live in.

**The one rule that defines it:** detection is keyed to queue ground-truth, never to what
the agent claims about itself. A worker can swear it's done while 14 tickets sit open —
the watcher catches that.

## Features (condensed)

| # | Feature | What it does | Status |
|---|---------|--------------|--------|
| 1 | Task queue | One namespaced store of tickets per project (CCC/BYM/OPS/…), backend-agnostic API | ✅ Built |
| 2 | Autonomous workers | A `/goal` session drains one project's queue: claim → fix → verify → commit → next | ✅ Built |
| 3 | Queue-health watcher | Candidacy-gated scan: open tickets + no progress = STUCK (trusts queue, not agent) | ✅ Built |
| 4 | Health API | `GET /api/ux-fixes/health` — per-project depth, oldest-open age, fixer liveness, stuck flag | ✅ Built |
| 5 | Auto-nudge | Background watcher wakes a stuck worker automatically (no manual click) | ✅ Built |
| 6 | Worker-identity reach | Resolves the real session behind a worker so a stuck one can actually be nudged | ✅ Built |
| 7 | Queue ledger view | Right-sidebar list of all tickets with OPEN / IN_PROGRESS / CLOSED status | ✅ Built |
| 8 | Queue-health strip | Glanceable per-project depth + STUCK/LIVE badge atop the queue view; mobile-friendly | 🟡 In progress |
| 9 | File tickets | Add work via `+` or the visual **Annotate** button (point at a UI element) | ✅ Built |
| 10 | Transcript viewer | Read what any session did — worker or not — back over time | ✅ Built |
| 11 | Inject / steer | Send input into a running session to correct or unblock it | ✅ Built |
| 12 | GitHub Issues backend | Use your repo's GitHub Issues as the queue, drop-in behind the same API | ⬜ Planned |
| 18 | Queue registry API | First-class create/configure a queue (name, backend, drain policy, owner) instead of implicit create-on-first-write — the enabler for #12 and the "anyone declares a queue" platform move | ⬜ Planned |
| 19 | Dedup / merge pass | Append-only enqueue + post-hoc dedup: cheap exact-key pre-filter, then a semantic merge+rank agent pass before drain. Pre-enqueue dedup is a TOCTOU race under concurrency, so dedup is never the poster's job for correctness | ⬜ Planned |
| 20 | Start-a-worker primitive | `watchtower work -q <queue>` (+ API + STUCK-badge button): spawn worker(s) bound to a queue when none exists. The action half of the loop (detect → nudge-if-idle → **start-if-none** → drain → wait). Today only detect+nudge exist, so "stuck with zero workers" rots | ⬜ Planned |
| 13 | Cross-project mobile pane | All queues at a glance from your phone; pings only on STUCK | ⬜ Planned |
| 14 | Spawn-and-reply API | Call a worker as a function: sync `await` + async webhook round-trip | ⬜ Planned |
| 15 | Monitor-as-a-job | Scheduled sanity checks (e.g. a landing page) that file a fix ticket on failure → worker drains it | ⬜ Planned |
| 16 | Non-dev onboarding | Outcome-first copy; the health strip as the mental model (the word "headless" never appears) | ⬜ Planned |
| 17 | Off-laptop hosting | Run the fleet on a VM so your machine can be closed (thin layer over Docker, not a rebuild) | ⬜ Planned |

Closed loop today: **#15 → #1 → #2 → #3 → #5/#6 → done** (a check files a ticket, a worker
fixes it, the watcher guarantees it actually got done). #15 is the only piece of that loop
not yet wired.

## How we ship it

- **Open source.** Public repo, MIT-spirit. Companion-tool ethos — runs behind the scenes
  and you forget it's there (in a good way), like Token Optimizer / Total Recall.
- **Zero-dependency core.** Server is stdlib-only Python; frontend is a single static file.
  No bundler, no npm, no pip at runtime.
- **Install paths:** `curl | run.sh` (git-pull on launch), Homebrew, notarized DMG with
  Sparkle auto-update.
- **Local-first, private by default:** binds `127.0.0.1`, same-origin checks, path clamping.
- **Buildable-on:** the queue + health + spawn APIs are the stable surface others build on
  (this is why GitHub-Issues backend and the spawn-reply API matter — meet people where
  their work already lives).

## README — how it's used (short version)

```
# 1. Run it
./run.sh                       # opens the dashboard on 127.0.0.1:8090

# 2. Put a worker on a queue (paste into a session in your repo)
/goal Drain the <PROJECT> queue and keep it empty. (see docs/ux-fixes-worker-brief.md)

# 3. Add work
#    - click + in the Queue tab, or
#    - hit Annotate and point at the thing that's wrong

# 4. Watch the strip
#    green = draining, red = stuck. Stuck workers get woken automatically.
#    You only step in when it asks.
```

That's the whole loop: **file work → a worker drains it → the watcher guarantees it.**
You don't manage agents. You manage a queue, and watch it empty itself.
