# Codex Steer-to-Send Fallback Design

## Problem

CCC can offer or execute Steer from slightly stale live-status data. The
server may then reject the request with `codex_no_active_turn` or
`codex_steer_unavailable`, including the misleading message that another Codex
process owns the turn. The user's text remains unsent even though normal Send
could start a follow-up turn or queue behind a genuine external writer.

## Considered Approaches

1. Keep the error and require the user to press Send again. This avoids
   automatic behavior but loses the user's requested action and exposes a
   transient ownership implementation detail.
2. Retry every Steer failure as Send. This is convenient but risks duplicate
   delivery when `turn/steer` was attempted and its result is ambiguous.
3. Retry only definitive pre-delivery Steer rejections as Send. This preserves
   the message while avoiding retries after an actual `turn/steer` attempt.

Approach 3 is selected.

## Behavior

The server remains the single routing authority. When a Codex Steer request
returns `codex_no_active_turn` or `codex_steer_unavailable`, CCC immediately
routes the same text through ordinary `resume_session_codex(..., steer=False)`.

- If the thread is idle, Send starts a follow-up turn.
- If another writer is genuinely active, Send appends the text to the durable
  per-conversation FIFO queue.
- If earlier queued text exists, the existing queue ordering rules apply.
- `codex_steer_failed` does not retry because `turn/steer` was already
  attempted and delivery may be ambiguous.
- Successful Steer behavior is unchanged.

The fallback result is returned directly, so existing clients render the real
outcome (`codex-app-turn` or queued) instead of an ownership error.

## Verification

Add a server regression test proving that a definitive Steer rejection calls
the normal Send path exactly once with the same session and text. Retain the
existing test proving successful Steer does not invoke Send. Run the focused
Codex injection tests and the queue-pump regression suite.
