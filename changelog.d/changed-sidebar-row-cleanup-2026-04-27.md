**Sidebar row cleanup** — chips, branch pill, and archive grouping.

Chips: dropped `working` / `idle` / `waiting for input` / `planning` /
`coding` and the non-pkood `blocked`. The yellow live-tool pill already
shows what a session is doing right now, so the activity chips were
redundant; `planning` and `coding` were defaults dressed as signals.
Non-pkood rows now show 0 chips by default and just `committed` /
`pushed` when those carry meaning. Pkood rows keep their full state
machine (`running` / `idle` / `blocked` / `stuck`) since pkood owns
that truth.

Branch pill: worktree-aware. When tool-call inference detects that a
session is editing in a different worktree than its launch cwd
(launched in shared clone, but `Edit` paths land in `feat/x`), the row
shows the inferred branch in orange with a 🌿 leaf instead of the
launch branch in purple. Sessions launched directly inside a worktree
get the same treatment via a cheap `.git`-is-file check. The inference
is cached by `(session_id, jsonl_mtime)` so idle sessions don't repay
the JSONL walk on every refresh.

Archive section: archived rows now sit in a collapsible `Archived (N)`
section at the bottom of the list (default collapsed, state in
`localStorage`), instead of being filtered out by a top-bar toggle.
Same source of truth as the kanban Archived column, so tapping the
per-row archive button drops the card to that section visibly.
