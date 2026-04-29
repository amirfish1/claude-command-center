- **Dropped the `Inactive` column; replaced with a small "no edits" chip.**
  Sessions that used to land in `Inactive` (dead, no commits, no edits) now
  sit inside `Working`. A small lowercase blue **"no edits"** chip — sitting
  alongside `pushed` / `committed` in the list view, and next to the stage
  chip in the kanban — flags any session whose Claude has never touched a
  file. Liveness deliberately doesn't matter: a freshly-spawned session with
  no tool calls yet shows the chip just like a dormant shell does. Driven by
  a small `hasNoEdits(c)` helper: `!c.has_edit && !c.verified && !c.archived`
  (no labels, no stage, no liveness checks — the chip describes one thing).
  Stale `inactive` localStorage overrides drop on first render.
