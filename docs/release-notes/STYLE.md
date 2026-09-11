# Release notes: value-first rules

Rules `scripts/release_notes.py` encodes. Follow these when hand-editing
its output or writing release notes by hand.

- **Lead with what the user can now do, not what the commit fixed.**
  "You can now see who spawned a session" beats "fixed spawned_via
  inference bug". The commit message is evidence, not the sentence.
- **Every bullet earns a "why it matters."** If you can't say why a user
  would care, it's not release-note material — it belongs in "Under the
  hood," not the headline sections.
- **Group by capability, not by commit.** Five commits that shipped one
  feature in slices are one bullet to the user, not five.
- **Bugs fixed silently still get a value statement**, not an apology.
  "Steer now delivers on an idle Codex thread" is a capability
  statement even though the underlying change was a bug fix — say what
  works now, not what was broken before.
- **Collapse pure plumbing.** `chore`/`test`/`docs`/`ci`/`build`
  commits, and `fix` commits scoped to internal-only concerns
  (`restart`, `sprint`, `ci`, `hunch`, `perf-budget`), go in a
  collapsed "Under the hood" section as a one-line commit list — they
  exist for the record, not for the reader.
- **One line of "how" per capability, evidence not exposition.** Name
  the touched files or the mechanism in a single line; don't re-explain
  the implementation in prose.
- **No commit-log vocabulary in the headline sections.** Ban "fix",
  "add", "refactor", "wire", "bump" as the first word of a bullet — say
  what the user does or sees instead.
- **Deterministic first, LLM second.** The rule-based pass must produce
  something honest and shippable on its own; an `--llm` pass is prose
  polish only — it must not be allowed to invent claims the commits
  don't support.
