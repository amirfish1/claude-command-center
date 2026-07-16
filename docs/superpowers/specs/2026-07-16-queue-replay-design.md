# Queue / ticket-appearance events in replay (W22 / B4)

Status: design + implementation (2026-07-16). Lane W22-queue-replay, Fable-5 batch.

## Problem

CCC replays **group chats** (`startReplay` / `_gcReplay*` in `static/app.js`) and
**single conversations** (`startConvReplay` / `_convReplay*`). A group-chat replay
animates the fleet's messages one at a time on a message-ordered timeline, each
message carrying an absolute `when` timestamp parsed from its markdown heading
(`## 2026-07-05 Sunday 06:12:35 PDT — 05cf2b20: LANE 💬`).

What replay does **not** show is the **queue**: while a fleet of workers chats,
WatchTower tickets are created, claimed, and resolved. Amir wants the queue
filling and draining to appear *on the same timeline* as the conversation, so a
replay tells the whole story — not just what the agents said, but the work
appearing and being burned down.

## Current systems (Phase 0 map)

### Replay (client — `static/app.js`)
- `startReplay(data)` @ ~18374: parses `data.content` (group-chat markdown) into
  `_gcReplayMessages[]`. Each message: `{heading, speaker, when, isSystem,
  isHuman, side, color, isPing, wrappedHtml, wordCount, rawBody, originalIndex}`.
- `playNextReplayStep()` @ ~18754: the playback loop. Walks `_gcReplayMessages`
  by `_gcReplayMsgIndex`, builds a message element, reveals it (hero animation +
  word-by-word), then schedules the next step after a per-message pause.
- Data source: `_gcReplayData = data` @ 20872, set from `/api/group-chat/read`.
  `startReplay` is (re)invoked from the ▶ Replay button @ 20303.
- The timeline is **message-index driven**, not wall-clock driven. Interleaving
  new event kinds = inserting pseudo-messages into `_gcReplayMessages` in the
  right position.

### Queue / tickets (server — `server.py`, `ux_fixes_queue.py`)
- Durable ticket store: one JSON file (`ux-fixes-queue.json`, overridable via
  `UX_FIXES_QUEUE_FILE`). WatchTower (`watchtower.queue`) is the primary engine
  when importable; `ux_fixes_queue` is the stdlib fallback. **Both read/write the
  same store.** The active engine is `_q`; `_q.list_items()` is the backend-
  agnostic reader (one JSON read, no per-row subprocess).
- Each item is the ticket event-log we reuse — no parallel store needed. Fields:
  `ref`, `note`, `queue`, `project`, `status` (open|in_progress|closed),
  `created_at`, `claimed_at`, `closed_at`, `updated_at`, `claimed_by`
  (ISO-8601 UTC). `_uxq_parse_ts()` parses ISO → epoch.
- `compute_ux_fixes_health()` @ 28476 already derives per-project depth/liveness
  from `_q.list_items()` with the perf pattern we mirror (single read, candidacy
  gating, no per-row fork).
- There is also a global `~/.watchtower/activity.log` (verb-per-line) surfaced by
  `/api/wt/activity-log`. We do **not** use it as the event source — the durable
  item timestamps are richer (per-ticket, structured) and already loaded.
- The canonical per-ticket event log is the item's own `history` array (spec:
  `watchtower/docs/superpowers/specs/2026-07-04-ticket-event-log-design.md` —
  it lives in the sibling `watchtower` repo, since WT is the engine). `_q.timeline`
  / `_uxq_item_timeline` normalize it. We reuse the same durable store but derive
  the three replay events from the always-present snapshot timestamps
  (`created_at`/`claimed_at`/`closed_at`) rather than computing the full per-item
  timeline — cheaper, dependency-free, and identical for the created/claimed/
  resolved mapping we need. **No parallel event store is introduced.**

## Events added

Derived from each ticket item's own timestamps (the existing event-log), three
event kinds map cleanly onto "appears / claimed / resolved":

| Event kind        | Source timestamp          | Meaning (replay caption)                    |
|-------------------|---------------------------|---------------------------------------------|
| `created`         | `created_at`              | Ticket **appeared** in the queue (queue +1) |
| `claimed`         | `claimed_at`              | A worker **claimed** it (in progress)       |
| `resolved`        | `closed_at`               | Ticket **resolved / closed** (queue −1)     |

Each derived event: `{kind, ts (epoch), iso, ref, note, queue, project,
claimed_by, depth_after}`. `depth_after` is the queue's open-ticket depth
*immediately after* this event, computed by folding events in time order
server-side — so the UI can show "queue: 3" without re-scanning all tickets per
frame.

Events with a missing/zero source timestamp are skipped (an open ticket has no
`claimed`/`resolved` event yet; that is correct — the queue is still filling).

## Interleaving with the replay timeline

Server returns `queue_events[]` (time-sorted, ISO + epoch) on the
`/api/group-chat/read` payload. Client:

1. Parses each conversation message's `when` heading → epoch
   (`_gcReplayParseWhen`, tolerant of the `Weekday`/`TZ` tokens).
2. Bounds queue events to the chat's span `[firstMsgEpoch, lastMsgEpoch]`
   (dropping events outside — old queue history does not dump at the start).
3. Inserts each in-window queue event into `_gcReplayMessages` as a pseudo-
   message `{isQueueEvent: true, kind, ref, note, queue, depthAfter, when, ts}`,
   positioned just after the last conversation message whose epoch ≤ event epoch.
4. `playNextReplayStep()` renders a queue-event pseudo-message as a distinct
   full-width **queue chip card** (`.gc-replay-queue-event`, kind modifier for
   color) with a short fixed pause, then advances — no hero/word animation.

Result: as the replay clock walks the conversation, a "▲ WT-40 appeared · queue 3"
card slides in at the moment the ticket was created, a "◆ claimed" card when a
worker took it, and a "✓ resolved · queue 1" card when it closed — synced to the
same scrubber/step clock as the messages.

## Performance (CLAUDE.md § Performance gates)

- **No O(all tickets) per frame.** Events are derived **once** per
  `/api/group-chat/read` build (already coalesced at 3.5s TTL), not per replay
  step. The replay loop reads a pre-built in-memory array.
- **Single read, cached by (mtime, size).** `_queue_replay_events()` stats the
  queue store file and memoises the derived+folded event list keyed on
  `(mtime, size)`; an unchanged store returns the cached list with zero JSON
  parse. One `_q.list_items()` read on change, no per-row subprocess.
- **Bounded output.** Server caps returned events to the most recent
  `_QUEUE_REPLAY_MAX_EVENTS` (newest wins) and a `_QUEUE_REPLAY_MAX_LOOKBACK_S`
  window so a months-deep store never ships a giant payload. Truncation is
  reported in the payload (`queue_events_truncated`).

## Scope / follow-ups

- Implemented for **group-chat replay** (the multi-agent timeline where queue
  activity is contextually meaningful). Conversation replay (`startConvReplay`)
  is a natural follow-up using the same server helper.
- Queue events are queue-agnostic within the window (whole-fleet view). A future
  slice can scope to one queue via a sidecar field / `?queue=` filter.
- Public-OSS: no private data in fixtures; server stays stdlib-only.
