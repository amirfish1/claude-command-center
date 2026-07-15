# Queued Steer Cancel Design

## Problem

CCC surfaces durable queued messages inside the composer as Steer candidates,
but offers no way to withdraw one. Hiding the row client-side is unsafe because
the server will still deliver the persisted message later.

## Design

Each queued row gets a neutral **Cancel** button beside **Steer**. Cancel sends
the session id and queued text to a dedicated same-origin POST endpoint. The
server removes exactly one matching entry, preferring the Codex resume queue and
then the terminal queue, and immediately persists the remaining FIFO queue.

The browser removes the row only after the server confirms cancellation. While
the request is active both row actions are disabled. If the entry is no longer
queued because delivery already began, CCC keeps the row until refresh and
shows an explicit error rather than claiming cancellation succeeded. The
composer draft is never changed.

## Steer transaction

Steering a durable row follows the same confirmation rule. CCC attempts the
live Codex steer before removing anything from the FIFO. Only a confirmed
`codex-steer` response consumes one matching entry and lets the browser remove
the row. If the active turn cannot be steered, the entry stays in its original
position and the row remains visible with a **Still queued** explanation.

This deliberately disables the normal Steer-to-Send fallback for durable queue
rows. Falling back there is a no-op—the message is already queued—and consuming
then appending it would silently reorder the FIFO.

## Alternatives

- Client-only dismissal was rejected because it would not cancel delivery.
- Clearing the conversation's entire queue was rejected because it could drop
  later, unrelated messages.
- Adding durable queue-entry ids was deferred; the existing queue stores text
  strings, and removing one matching copy already preserves duplicate-message
  semantics and FIFO order.

## Verification

Server tests prove that only one matching copy is removed and persisted, and
that a missing entry is reported as a conflict. Static UI tests prove that each
queued row receives Cancel, calls the dedicated endpoint, and removes the row
only after success. Transaction tests prove failed steering leaves the full FIFO
unchanged and successful steering removes exactly one matching entry.
