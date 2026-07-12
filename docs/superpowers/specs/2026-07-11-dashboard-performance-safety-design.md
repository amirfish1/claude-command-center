# Dashboard Performance Safety Design

## Goal

Keep CCC responsive while a user types by making background work proportional
to what the user can currently see or has explicitly requested.

## Non-negotiable user protections

- Typing must not compete with unbounded historical scans or invisible panels.
- A time filter must limit the work CCC performs, not merely hide data after it
  has already been loaded.
- Background work must have one owner, a visibility or activity reason, a
  bounded refresh rate, and shared results for all consumers.
- Expensive history must be explicit and progressively revealed.

## Group chats

Every group chat has one durable state:

- **active**: created, sent a message, or had a participant added within the
  last 15 minutes; it has not been paused, closed, or put away.
- **inactive**: no message and no participant change for 15 minutes. It stays
  in history but is absent from "In Group Chat" and does no background work.
- **paused**: explicitly paused. It stays inert; sending a message or adding a
  participant does not wake it.
- **closed**: explicitly closed. It is immediately removed from "In Group
  Chat", stays in ordinary history, and is inert.
- **put away**: explicitly archived. It appears only in archived history and
  is inert.

At dashboard start CCC makes one cheap active-chat check. If no active chat
exists, it performs no recurring group-chat work. If active chats exist, CCC
refreshes only their short list every 15 seconds. Messages, participant detail,
waiting state, and message counts for one chat are loaded only when the user
opens that chat. Opening an inactive chat does not wake it; only sending a
message or adding a participant does.

## Conversation archive

CCC starts in a 1-day window. The 1-day and 7-day choices are server-side
bounds: old transcript files must not be parsed, serialized, or transferred.

The All choice is explicit. Before the first all-history request CCC shows the
number of conversations that would be loaded. Approval starts with the newest
page; the user loads additional pages deliberately. Search may request older
matching pages without loading unrelated history. Existing ETags and cached
pages remain reusable, but no automatic refresh may expand the selected window.

## Working-now status

"Working now" is a short shared answer for agents with a fresh sign that they
are working, waiting for input, or waiting for approval. It excludes historical
conversations. Every dashboard consumer uses one shared snapshot, which may be
up to 10 seconds old. There must be one refresh owner and no fallback to a full
session scan on a normal timeout.

## Model Advisor

The footer indicator displays the latest saved advice; it does not start a new
scan. CCC asks for fresh advice only after an agent starts, ends, changes model,
or has substantial new work. It waits 30 seconds after the last qualifying
change, coalesces repeated changes, and runs no more than once every five
minutes while the dashboard is open. Opening the Model Advisor requests a
fresh answer immediately.

## Measurement and acceptance criteria

Record request duration, response size, and client long frames for these four
surfaces. The test fixture must cover a large archive and historical group
chats.

- With no active group chats, there are no recurring reads of group-chat
  messages or participant state.
- A 1-day or 7-day archive query never returns conversations outside its
  window; All is paginated and requires explicit confirmation.
- A normal dashboard has at most one in-flight working-now request.
- Model Advisor has no fixed 45-second background scan.
- CCC-originated interaction work must not produce a client frame longer than
  50ms while a message field has focus. A test or trace must report failures;
  it must not silently ignore them.

## Compatibility and safety

The server remains stdlib-only. Existing public API fields remain available;
new optional query parameters and response fields are additive. Existing group
chat records are mapped safely to an inert historical state unless there is a
fresh explicit active action. No automatic action sends messages, wakes agents,
or changes a model.

## Out of scope

This work does not redesign group chat, remove all-history access, or conduct a
general performance audit. A targeted follow-up audit is justified only if the
acceptance criteria still fail after these changes.
