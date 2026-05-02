**Workspace strip shows a single pill** instead of "launch cwd · via
tool calls · effective cwd". The strip's job is to answer "where does
this session's `Edit` actually go?" — now it does that with one pill,
preferring the tool-call-inferred effective cwd when it differs from
the launch cwd, falling back to the launch cwd otherwise. A small
"inferred from N/M tool-call paths" tooltip on the kind label keeps
the disclosure without spending real estate on a second pill. Removed
the `+N worktrees (X subagent · Y manual)` button from the per-session
strip — the topbar Worktrees button is the single entry point.
