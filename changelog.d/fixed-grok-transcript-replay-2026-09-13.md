**Grok sessions render as real conversations.** `updates.jsonl` is now
replayed the way Grok Build's own TUI does: tool calls merge with their
updates into one row (with command/file detail, status, and decoded
output — no more raw byte arrays or JSON envelopes), hook executions only
surface failures, plans collapse into cards, and retries / turn ends /
image drops become compact system rows instead of protocol noise. The
session list shows real titles, model, branch, and edit/commit signals,
and the usage panel reads `usage.json` (tokens, context window, cost).
