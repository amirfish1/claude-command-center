**Codex placeholder card now persists and renders the run log.**
Codex `exec` is one-shot and writes no Claude-JSONL, so before this
the optimistic kanban placeholder vanished after 30s with no real
card to take its place — a codex spawn looked like it had failed
even though the run had completed. The placeholder is now permanent
for codex (no auto-cleanup; the user archives it manually), and
clicking it loads the spawn log into the right pane: parsed
`item.completed` agent messages, a token-usage footer, and a
collapsible stderr section. The pane polls `/api/sessions/spawned/
<pid>/log` every 1.5s while the codex process is alive and locks
to the final transcript on exit. New endpoint:
`GET /api/sessions/spawned/<pid>/log` returns
`{ok, pid, engine, log_path, text, running, exit_code}` looked up
from the in-memory spawn registry. State is client-side only — a
page reload still drops the card; full codex JSONL ingestion remains
the proper follow-up.
