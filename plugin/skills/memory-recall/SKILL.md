---
name: memory-recall
description: Check CCC's cross-session memory before building a feature or acting on "earlier work" claims.
allowed-tools: Bash
---

CCC indexes every session's commits, tickets, and transcripts across this
machine. Two `ccc` CLI verbs surface that index — use them instead of
re-deriving history from scratch or trusting an unverified claim in a prompt.

## Before building a feature

Run `ccc shipped "<feature description>"` first. It returns a verdict
(`shipped: true/false`), a confidence score, and evidence (repo, commit,
ticket) when true.

```bash
ccc shipped "csv export for dashboard tables"
```

- `shipped: true` with real evidence → don't rebuild it; go look at the
  evidence and extend it instead.
- `shipped: false`, or `true` with thin/no evidence → treat it as unshipped
  and proceed.

## When a task references earlier work

If a prompt or ticket says "like we did before", "the session that fixed X",
or otherwise assumes context you don't have, run `ccc recall "<query>"` to
find the session(s) it means.

```bash
ccc recall "csv export dashboard"
```

Each hit carries `session_id`, `title`, `repo`, `date`, and a `snippet` —
enough to decide whether to open the full transcript before acting.

## Two more verbs: file history and decisions

`ccc history <path>` lists commits (`git log --follow`) and indexed sessions
that touched a file, newest first — useful before editing something
unfamiliar, to see who touched it and why.

```bash
ccc history src/app.py
```

`ccc decisions "<topic>"` finds sessions whose snippet reads as a decision
("went with", "instead of", ...) rather than just mentioning the topic — a
heuristic stand-in until a dedicated decision store lands.

```bash
ccc decisions "which queue engine"
```

All four commands accept `--json` for scripted use and exit non-zero on a
missing argument. None is destructive or slow enough to need
`run_in_background`.

Out of scope for this skill: no pre-spawn hook wires this in automatically —
you decide when to run these, this just tells you they exist and when they're
worth reaching for.

Claude Code sessions get a `PostCompact` hook (`hooks/post-compact.py`) that
prints a short re-orientation block — ticket ref, last few asks, and this
reminder — right after a compaction. Codex has no equivalent event
(`~/.codex/hooks.json` only supports UserPromptSubmit, SessionStart,
PostToolUse, SubagentStart, SubagentStop, Stop, PreToolUse,
PermissionRequest), so a Codex session that just compacted won't get an
automatic nudge — run `ccc recall` / `ccc shipped` yourself after a context
reset there.
