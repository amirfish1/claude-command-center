# Session attach

The "it doesn't care how your agents were launched" claim boils down to three
ingestion paths that all land in the same card model. This doc walks through
each.

## 1. Terminal session you started yourself

You open a shell and run `claude` (or `claude --resume <sid>`). Claude Code:

- Writes its session transcript to `~/.claude/projects/<project-slug>/<sid>.jsonl`.
- Writes a registry entry at `~/.claude/sessions/<pid>.json` with the
  session id, working directory, and some metadata.

No extra action needed. When the command center's server serves
`/api/sessions`, it scans the project directory and matches each `.jsonl`
against the live `~/.claude/sessions/` registry (cross-referenced with
`ps -A` to confirm the pid is still alive). Live TTY sessions get a card
with `is_live: true` and a `tty` field; dormant ones get the same card
with `is_live: false`.

Because the sidecar hooks are installed at the user level (in
`~/.claude/settings.json`), any terminal `claude` process on the machine
starts firing into the command center's `live-state/` directory — even if
the command center itself wasn't running when the session started.

## 2. Headless session spawned from the UI

The "Launch" button (or dragging a GitHub issue into Working) fires:

```python
claude -p --verbose \
  --input-format stream-json \
  --output-format stream-json \
  --model opus \
  --dangerously-skip-permissions \
  --name <slugified-prompt>
```

The server keeps `Popen.stdin` and `Popen.stdout` open, pipes the initial
prompt in as a stream-json `{"type":"user", ...}` line, and appends the pid
to an in-memory `_spawned_sessions` list. Follow-up messages typed into the
conversation panel's input bar are routed to `POST /api/sessions/spawned/<pid>/inject`,
which writes another line to stdin.

No terminal is opened. The session's JSONL still lands in
`~/.claude/projects/<slug>/` just like a terminal session, so it shows up
in the kanban the same way.

Caveat: the stream-json follow-up channel dies on server restart (stdin pipe
closes). The Claude process keeps running, and you can recover by jumping
into it with "Launch in terminal".

## 3. Dormant session resumed on demand

If you inject input into a session whose process isn't alive anymore, the
server silently spawns:

```python
claude -p --verbose \
  --resume <sid> \
  --input-format stream-json \
  --output-format stream-json \
  --dangerously-skip-permissions
```

The message is piped in, the resumed process is added to `_spawned_sessions`
tagged `resumed_sid=<sid>`, and subsequent injects reuse the same process
while it's alive. This lets you "ping" a quiet session from the UI without
leaving the browser.

## Classification regardless of origin

All three paths produce the same card shape. The classifier doesn't know or
care where the session came from — it only looks at:

- Is there a live pid in `~/.claude/sessions/`?
- Is there a recent sidecar update (`~/.claude/log-viewer/live-state/<sid>.json`)?
- Does the JSONL contain `has_push` / `has_commit` markers?
- Are there manual overrides (verified, archived, column drag)?

That's the whole story. There's no "attach protocol" — the tool is
downstream of a filesystem convention Claude Code already establishes.
