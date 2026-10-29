# Ultra-fresh session rows

The first four session rows intersecting the session-list viewport receive
message explanations through `GET /api/sessions/ultra-fresh?sessions=<ids>`.
Scrolling, filtering, grouping, and ordering update that bounded selection.
Hidden tabs release their stream. One connection carries all four sessions;
older subscriptions cannot update a replacement selection.

The feed reuses the conversation parser's bounded tail, source-stat checks,
the spawn stream's text normalizer, and the Codex live transcript snapshot
mapper. It never scans the archive or launches an agent. It checks local
sources at 100 ms intervals and sends at most one message per session per
iteration. Open transcript message events also feed the row directly.
This is a fast path for local transcript/live sources, not a faster remote
cloud transcript fetch or a subscription to every historical session.

Only agent text blocks supply explanations. Tools, reasoning, plans, tool
results, and child-agent blocks do not replace the parent explanation.
New user messages and optimistic sends invalidate the previous turn;
timestamps and subscription generations reject stale frames. Message footers
use the existing completion-assessment parser. Active work, new user turns,
and unresolved approvals still prevent Close. Archive remains manual.

The browser changes only explanation text and its Working/Next/Close label
inside the existing desktop and phone row slots, coalesced once per animation
frame. It does not reorder rows, replace selection, or rebuild the list on
text deltas. Ordinary list snapshots reconcile the rest of the row; the four
live explanation overlays survive snapshots that lag the message stream.

Checks: `python3 -m pytest -q tests/test_ultra_fresh.py` and
`node --test tests/ultra-fresh.test.cjs tests/sidebar-outcome-working.test.cjs
 tests/session-ready-to-close.test.cjs`.
