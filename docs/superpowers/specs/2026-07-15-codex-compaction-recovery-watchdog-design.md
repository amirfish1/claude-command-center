# Codex Compaction Recovery Watchdog Design

## Problem

A Codex turn can reach context compaction, stop producing activity, and never
deliver a normal continuation or final answer. CCC currently records the last
app-server event and eventually describes the thread as quiet or idle, but it
does not distinguish a successful terminal state from a stranded compaction
boundary. An active task or `/goal` can therefore remain unfinished until the
user manually sends another message.

The observed failure is concrete: a compaction item completed, the associated
turn ended, and no follow-up turn began for roughly 27 minutes. There was no
approval, question, rate-limit signal, final answer, or explicit stop.

## Requirements

1. Detect a compaction episode per Codex conversation from app-server
   `contextCompaction` item notifications.
2. Treat progress after that boundary as evidence that Codex recovered on its
   own. Progress includes a subsequent turn or any non-compaction model/tool
   activity.
3. If the episode becomes silent without useful progress, start exactly one
   continuation turn after a grace period.
4. If the stale compaction turn is still reported active, interrupt that turn
   before starting the continuation. Never start two writers on one thread.
5. Do not recover across approvals, questions, active non-compaction tools,
   active flags such as limits, paused/blocked/completed goals, explicit final
   output, or a queued user message.
6. Preserve the durable user-message FIFO. A recovery prompt must never jump
   ahead of queued user input.
7. Bound recovery to two attempts per compaction episode with a cooldown. A
   failed recovery must be visible and must not loop forever.
8. Persist the compact recovery record so a CCC restart does not lose an armed
   episode or duplicate an already-started recovery.
9. Surface `waiting`, `interrupting`, `recovering`, `recovered`, `suppressed`,
   and `exhausted` recovery states through the existing status and synthetic
   conversation-event paths. The UI must say “Recovering after compaction”
   rather than presenting recovery as ordinary idle time.
10. Continue an active native Codex goal with goal-aware wording. For a
    non-goal task, use a generic interrupted-task continuation prompt.

## Approaches Considered

### Event-only recovery

Schedule recovery directly from `turn/completed`. This is fast and simple but
misses the defining failure where no reliable terminal event arrives. It also
risks issuing nested app-server requests while the notification lock is held.

### Polling-only recovery

Repeatedly infer compaction and silence from rollout files. This survives lost
notifications but duplicates parsing, is slower, and cannot reliably identify
app-server approvals, active tools, or turn ownership.

### Hybrid event latch plus watchdog scan (selected)

App-server notifications arm and update a small per-thread recovery record.
The existing singleton pending-input watcher scans only armed records every
five seconds and performs recovery outside the app-server notification lock.
The watcher is process-singleton because it owns shared durable delivery, but
the episode record, retry budget, FIFO check, and writer lock are all keyed by
conversation. This combines precise events with recovery from a missing
terminal event.

## State Model

Each affected thread stores `compaction_recovery` in CCC's app-server state:

- `episode_id`: the compaction item id, used for idempotency.
- `compaction_turn_id`: the turn containing the compaction boundary.
- `compacted_at` and `last_progress_at`: silence calculation inputs.
- `status`: `waiting`, `interrupting`, `recovering`, `recovered`,
  `suppressed`, or `exhausted`.
- `attempts` and `next_attempt_at`: bounded retry/cooldown data.
- `recovery_turn_id`: the continuation turn accepted by Codex.
- `reason`: short user-visible explanation.

Starting a later turn or receiving later non-compaction activity updates the
episode. A normal post-compaction agent message followed by terminal completion
marks the episode recovered without an injected continuation. A new compaction
item creates a new episode and a fresh bounded retry budget.

## Recovery Policy

The watchdog processes only `waiting` or `interrupting` records whose grace or
cooldown has elapsed.

It holds without spending an attempt when:

- a non-compaction item is still in flight;
- the compaction item itself is still running;
- the thread has not yet been silent for the grace period; or
- the app-server still needs a short interval to settle after interruption.

It suppresses the episode when:

- approval or thread-level active flags are present;
- queued user input exists;
- the native goal is paused, blocked, or complete; or
- post-compaction final agent output proves the task produced a terminal reply.

When an otherwise recoverable stale turn remains active, the watchdog calls
`turn/interrupt` and records `interrupting`. Once the thread is idle, it starts
the continuation through CCC's normal write-gated Codex resume path. Accepted
recovery is recorded before another scan can attempt it again.

The active-goal prompt is:

> Continue working toward the active goal after context compaction. Resume from
> the current repository and conversation state, do not repeat completed work,
> and finish and verify the original objective.

The non-goal prompt replaces “active goal” with “task that was interrupted by
context compaction.”

## Failure Handling

An unavailable writer or transient turn-start failure increments the attempt
counter and waits for the cooldown. After two failed attempts the state becomes
`exhausted`; CCC does not retry until a new compaction episode occurs. An
accepted-but-queued recovery is treated as a failed attempt because the
watchdog itself never inserts recovery text into the user's durable FIFO.

All state mutations happen under `_CODEX_APP_SERVER_LOCK`. App-server calls and
resume calls happen outside it. The existing per-thread turn lock prevents a
watchdog recovery from racing a user send.

## UI and Diagnostics

Recovery state is additive in `/api/session-status` and
`/api/codex-wake-status`. While interrupting or recovering, the existing
sidecar activity surface reports tool `Recovery` and detail “Recovering after
compaction.” Synthetic coordination events show when recovery is armed,
started, succeeds, is suppressed, or exhausts its retries.

## Verification

Unit coverage must prove:

- compaction arms exactly one episode;
- later activity disarms or advances the episode;
- grace periods and active tools hold recovery;
- approvals, flags, goal terminal states, and queued user messages suppress it;
- an active stale turn is interrupted before resume;
- an idle stale turn gets the correct goal/non-goal continuation prompt;
- duplicate scans cannot create duplicate continuation turns;
- failures cool down and exhaust after two attempts;
- recovery records survive state reload; and
- status/synthetic events expose recovery instead of idle.

End-to-end verification must drive a disposable Codex thread through real
`thread/compact/start`, observe a `contextCompaction` episode, and confirm that
CCC automatically starts a later turn without a user message. The disposable
turn must use a harmless prompt and be allowed to complete before cleanup.
