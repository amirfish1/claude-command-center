# Queues (WatchTower)

If you've used CCC for more than a few sessions, you've probably seen the word "queue" float by. Here's what it actually means, and how to use it without reading a manual.

## What a queue even is

Think of a queue as a to-do list that AI agents pull work from — instead of you telling one session "do this," you drop tickets into a named list, and workers (agent sessions) pick them up and close them out.

- A **queue** is just a named bucket of tickets (e.g. `BUGS`, `CONTENT`, `RELEASE`).
- A **ticket** is one unit of work — a ref like `BUGS-42`.
- Multiple agent sessions can drain the same queue in parallel, like a shared inbox everyone's chipping away at.
- This is powered by **WatchTower** (`wt` on the command line) — CCC's queue engine. It's a separate tool CCC talks to, not something baked into CCC itself.

Why bother? Because it turns "I need to remember to ask 5 agents to do 5 things" into "I filed 5 tickets, go drain the queue" — and the queue survives even if your session doesn't.

## The basics: enqueue, check, drain

**Enqueue** — add a ticket to a queue:
```
wt enqueue QUEUE_NAME "do the thing"
```

**Check status** — see what's open, claimed, or stuck:
```
wt status QUEUE_NAME
```

**Drain** — have a worker pull tickets and work through them until empty:
```
wt drain QUEUE_NAME
```

**Wait** — block until a queue is fully drained (handy in scripts or automations):
```
wt wait QUEUE_NAME
```

That's genuinely most of it. Everything else is a variation on these four moves.

## Common patterns

- **Batch work** — got 10 similar small fixes? File all 10 as tickets in one queue, then dispatch a few CCC sessions to drain it in parallel. Way faster than doing them one at a time in a single session.
- **Automation handoff** — a script or hook files a ticket automatically when something needs human-in-the-loop or agent-in-the-loop attention (e.g. "flag this for review"), instead of pinging you directly.
- **Fan-out / fan-in** — one planning session enqueues a bunch of independent tasks, several worker sessions drain them concurrently, and a final session checks `wt status` to confirm everything closed before moving on.
- **"Park it for later"** — if you're mid-task and think of something unrelated, enqueue it instead of context-switching. Come back to the queue when you're free.

## Troubleshooting

**Queue looks stuck (tickets claimed but nothing's happening):**
- Someone claimed a ticket and then their session died, crashed, or got killed mid-task — the claim never got released.
- Run `wt status QUEUE_NAME` and look for tickets claimed a long time ago with no recent activity.
- Use **Reconcile** to release stale claims so another worker can pick them back up. In CCC's UI this is a one-click action; on the CLI it's the reconcile step in `wt`.

**A worker restart marks in-flight tickets "needs reconciliation":**
- That's expected, not a bug — if you restart the CCC worker mid-drain, any ticket it was actively working gets flagged so nothing silently disappears. Just hit Reconcile once and re-drain.

**Nothing's picking up new tickets:**
- Check that a session is actually pointed at the right queue name — typos in the queue name are the #1 cause of "I enqueued it and nothing happened."
- Confirm the queue isn't paused or held.

**Tickets pile up faster than they drain:**
- That's a signal to spin up more parallel workers on the same queue, not to panic — queues are designed for concurrent draining.

## Going deeper

This page is the "get moving" version. For the full CLI reference, fleet management, and advanced queue configuration, see the WatchTower docs (`wt --help` is also a good starting point from the terminal).
