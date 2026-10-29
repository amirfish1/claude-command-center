# Queues (WatchTower)

If you've used CCC for more than a few sessions, you've probably seen the word "queue" float by. Here's what it actually means, and how to use it without reading a manual.

## What a queue even is

Think of a queue as a to-do list that AI agents pull work from — instead of you telling one session "do this," you drop tickets into a named list, and workers (agent sessions) pick them up and close them out.

- A **queue** is just a named bucket of tickets (e.g. `BUGS`, `CONTENT`, `RELEASE`).
- A **ticket** is one unit of work — a ref like `BUGS-42`.
- Multiple agent sessions can drain the same queue in parallel, like a shared inbox everyone's chipping away at.
- This is powered by **WatchTower** (`wt` on the command line) — CCC's queue engine. It's a separate tool CCC talks to, not something baked into CCC itself.

Why bother? Because it turns "I need to remember to ask 5 agents to do 5 things" into "I filed 5 tickets, go drain the queue" — and the queue survives even if your session doesn't.

## The basics: add, check, drain

Every queue command takes the queue name with `-q`. (Checked against `wt 0.8.3`; run `wt <command> --help` for the full flags.)

**Add** a ticket to a queue:
```
wt add -q QUEUE_NAME --title "do the thing" --note "any detail a worker needs"
```

**Check status** to see each queue's depth, oldest-ticket age and stuck flag, then list one queue's tickets:
```
wt status -q QUEUE_NAME
wt ls -q QUEUE_NAME
```

**Drain**: turn on auto-drain so the WatchTower service spawns workers that claim tickets until the queue is empty. `off` puts the queue in backlog mode, where tickets wait and nothing is spawned:
```
wt drain on QUEUE_NAME
wt drain off QUEUE_NAME
```
Auto-drain only spawns workers while the WatchTower service is running (`wt start`). To work a ticket by hand in your own session, claim it with `wt claim -q QUEUE_NAME`, then `wt close REF --summary "..."` when it's done.

**Wait** blocks until a queue is fully drained, which is handy in scripts or automations:
```
wt wait -q QUEUE_NAME
```

That's genuinely most of it. Everything else is a variation on these four moves.

## Common patterns

- **Batch work** — got 10 similar small fixes? File all 10 as tickets in one queue, then dispatch a few CCC sessions to drain it in parallel. Way faster than doing them one at a time in a single session.
- **Automation handoff** — a script or hook files a ticket automatically when something needs human-in-the-loop or agent-in-the-loop attention (e.g. "flag this for review"), instead of pinging you directly.
- **Fan-out / fan-in** — one planning session files a bunch of independent tasks, several worker sessions drain them concurrently, and a final session runs `wt status` to confirm everything closed before moving on.
- **"Park it for later"** — if you're mid-task and think of something unrelated, file it with `wt add` instead of context-switching. Come back to the queue when you're free.

## Troubleshooting

**Queue looks stuck (tickets claimed but nothing's happening):**
- Someone claimed a ticket and then their session died, crashed, or got killed mid-task — the claim never got released.
- Run `wt status -q QUEUE_NAME` (stuck flag) and `wt liveness -q QUEUE_NAME` (who owns each claimed ticket and whether it's provably still working).
- The WatchTower reconciler releases dead claims on its own while the service is running. To free one by hand, run `wt release REF` and another worker can pick it up.

**A worker restart marks in-flight tickets "needs reconciliation":**
- That's expected, not a bug — if you restart the CCC worker mid-drain, any ticket it was actively working gets flagged so nothing silently disappears. Just hit Reconcile once and re-drain.

**Nothing's picking up new tickets:**
- Check that a session is actually pointed at the right queue name — typos in the queue name are the #1 cause of "I filed it and nothing happened."
- Confirm auto-drain is on (`wt config -q QUEUE_NAME --json`; turn it on with `wt drain on QUEUE_NAME`) and the service is running (`wt start`).
- Check `wt blocked` too: tickets parked for a human decision wait for `wt answer` and won't be picked up.

**Tickets pile up faster than they drain:**
- That's a signal to run more parallel workers on the same queue (`wt config -q QUEUE_NAME --workers N`), not to panic — queues are designed for concurrent draining.

## Going deeper

This page is the "get moving" version. For the full CLI reference, fleet management, and advanced queue configuration, see the WatchTower docs (`wt --help` is also a good starting point from the terminal).
