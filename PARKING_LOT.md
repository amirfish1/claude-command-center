# Parking Lot

Ideas, fixes, and improvements deferred for later thought. Each entry has full context so it can be picked back up cold.

---

## Multi-agent shared-clone git hygiene rule rewrite
**Parked:** 2026-04-29
**Context:** On 2026-04-27 a sibling Claude session silently destroyed ~28 lines of my uncommitted deploy-swap edits in `static/index.html`. Both rules in the existing `~/.claude/CLAUDE.md` "Multi-agent git hygiene" section were followed by the sibling, yet the work was still lost. The current rules are insufficient and need a rewrite — but the right rewrite isn't obvious yet, so this needs more thought before landing.

**Details:**
Forensic timeline (reconstructed from reflog + commit graph):
- ~15:55–16:00 PDT: I edited `static/index.html` (deploy-swap: moved Vercel pill to header actions, "+ New session" to the panel slot). Edits were uncommitted.
- 16:37:33 PDT: Sibling ran `git add static/index.html` (technically "explicit path" per the existing rule — so the rule was not violated) and committed as `f04bfa6 "fix(titles): skip leading path/URL when deriving auto title"`. The `--stat` was `+28 / -29` (56 lines), but the actual title fix is only ~14 lines — the other ~42 lines were **my** deploy-swap CSS rewrite, swept in.
- 16:38:35 PDT: Sibling ran `git reset HEAD~1` to undo the bundled commit.
- 16:39:36 PDT: Sibling recommitted as `7766ef8` with `+13 / -2`. Only the title fix. **My deploy-swap was gone from the working tree** — proving the reset was `--hard` (or `--mixed` followed by `git checkout -- static/index.html` / `git restore`), which wiped the working tree.
- ~16:30+ PDT: User reloaded the dashboard, didn't see the Vercel pill. I had to redo all the edits from scratch and finally committed as `dc0a427` at 16:47:13.

**Approach discussed:** Three proposed additions to `~/.claude/CLAUDE.md` "Multi-agent git hygiene" (drafted but **not written** — need more thought):

1. **Strengthen the "explicit path" rule** — `git add <file>` stages the *whole file*, including someone else's hunks. Real safety needs hunk-level granularity:
   - Run `git diff path/to/file` and confirm every hunk is yours before staging.
   - If any hunks are not yours, use `git add -p path/to/file` and only `y` your own hunks.
   - If unsure which hunks are yours, **stop and ask the user**.

2. **New rule: never wipe the working tree in the shared clone** — these commands all blast-radius across all sessions' uncommitted hunks:
   - `git reset --hard`
   - `git checkout -- <path>`
   - `git restore <path>`
   - `git stash drop`
   - `git clean -f`
   Safer alternatives: `git reset --soft HEAD~1` (keeps everything staged), `git revert <sha>` (no working-tree blast radius), `git restore --staged <path>` (index-only).

3. **New rule: escalate to a worktree for non-trivial work** — `git worktree add ../<repo>-wt-<name> -b feat/<name>` gives an isolated checkout sibling sessions can't reach. The shared clone is for quick single-file fixes committed within a minute or two.

**Open questions (the "needs more thought" part):**
- Does the guidance belong only in `~/.claude/CLAUDE.md`, or should this repo's `CLAUDE.md` echo a shorter version (since CCC is the project most likely to have concurrent sessions)?
- Should there be a hard enforcement (a hook or permission denial) that blocks `reset --hard` / `checkout --` / `restore` in the shared clone? Written norms already failed once.
- Is "stop and ask the user" workable as an escape hatch, or should the rule be unconditional `git add -p` in the shared clone? `add -p` is awkward in headless agent flows.
- Could a pre-commit hook warn when a high-traffic file (e.g. `static/index.html`) is being committed with hunks the committer didn't author? Hard to detect without provenance metadata — possibly via per-session hunk-tagging in a sidecar file.
- Is there a way to make uncommitted work *durable* in a shared clone short of stashing — e.g. an auto-stash-with-name-per-session daemon?

**Related files:**
- `~/.claude/CLAUDE.md` — "Multi-agent git hygiene" section (the rules to revise)
- `/Users/amirfish/Apps/claude-command-center/CLAUDE.md` — project file (candidate for echoing the shorter version)
- `/Users/amirfish/Apps/claude-command-center/static/index.html` — the high-traffic file where the incident happened
- Reflog evidence: commits `f04bfa6` (the bundled commit, reset out of history but still in reflog), `7766ef8` (the clean recommit), `dc0a427` (my eventual deploy-swap recommit)

**Status:** Parked
