# Codex Reattached Writer Attribution Design

## Problem

CCC can restart while a Codex turn is still active. The replacement CCC
app-server receives the active turn but no longer has the transient
`ccc_turn_start_pending` marker that proves CCC started it. Today that absence
is treated as proof that an external process owns the turn, producing false
messages such as “Message queued while another writer was active.”

## Decision

Use three ownership states for active turns: `ccc`, `desktop`, and `unknown`.
An app-server `turn/started` notification without a pending CCC start is
`unknown`, not `external`. Desktop ownership remains positive only when the
rollout is attached to a Codex desktop app-server. Unknown active turns still
block a second `turn/start`, so FIFO queue safety is unchanged.

Coordination messages must describe observable behavior rather than inferred
identity. An unknown active turn is “an active Codex turn,” and a message is
“queued behind the active turn.” Existing persisted `external` events remain
readable but use neutral copy when rendered.

## Alternatives Considered

- Persist a CCC ownership lease across restart. Rejected because a stale lease
  could incorrectly authorize concurrent writes after a crash.
- Infer ownership from the most recent coordination event. Rejected because
  events can be incomplete at process death and do not establish current
  ownership.
- Change only the banner text. Safe, but leaves the status API and future UI
  consumers with a false `external` attribution.

## Verification

Focused tests cover notification attribution, active-writer snapshots, and
coordination copy. Existing queue-gate tests verify that unknown/desktop turns
still queue without issuing a second app-server request.
