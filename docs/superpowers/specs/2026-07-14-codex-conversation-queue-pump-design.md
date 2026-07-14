# Codex Conversation Queue Pump Design

## Goal

Make a new message immediately push its conversation's pending queue forward
while preserving strict FIFO order. Codex app-server lifecycle notifications
become the normal delivery triggers; the periodic watcher remains recovery-only.

## Scope

This change keeps the existing durable `pending-inputs.json` representation.
It does not introduce SQLite or change the explicit **Steer** interaction.

## Architecture

Add one conversation-scoped pump for Codex resume messages. A non-blocking
per-conversation lock prevents concurrent triggers from delivering two messages
from the same conversation. Different conversations may pump independently.

The pump owns the delivery transition for the queue head:

1. Inspect, but do not remove, the oldest queued message.
2. Stop if the Codex thread has an active turn or the retry backoff is active.
3. Start a new turn for the queue head.
4. Remove exactly that message only after Codex accepts the turn.
5. Leave failures at the head and apply retry backoff.

## Triggers

- After a new message is durably appended, schedule the pump immediately.
- After `turn/completed`, schedule the pump for that thread.
- After `thread/status/changed` reports idle, schedule the pump.
- On server startup and periodically, the existing watcher invokes the same
  pump as reconciliation for restarts or missed notifications.

Triggering is idempotent. Repeated notifications or simultaneous enqueue and
completion events collapse through the per-conversation lock.

## Ordering and Steer Semantics

Ordinary input never jumps ahead of older queued messages. If a turn is active,
ordinary input remains queued. The existing explicit **Steer** action may remove
one matching queued message and send it to the active turn; that behavior stays
user-directed.

## Failure Handling

Messages remain durable until app-server acceptance. Transport errors, active
writer races, or rejected starts retain the queue head and set retry backoff.
The recovery watcher later invokes the same pump instead of implementing a
second delivery path.

## Verification

Focused tests must prove:

- enqueue immediately schedules the conversation pump;
- the oldest queued message is delivered first;
- active turns retain queued input;
- `turn/completed` and idle status notifications schedule delivery;
- concurrent triggers cannot deliver twice for one conversation;
- failed delivery retains the queue head;
- successful acceptance removes only the delivered head.
