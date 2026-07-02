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

---

## Slim down `/api/conversations/all` payload

**Parked:** 2026-07-01

**Context:** Surfaced while debugging a phone-over-Tailscale report of the mobile session view getting stuck on "Loading…". That specific bug turned out to be unrelated — the session/transcript view (`app.js:27199`), not the sidebar conversation list — so this is a real but separate perf issue, not an urgent fix. Decided explicitly *not* to bundle this optimization into the mobile bug fix: one change, one concern, and optimizing a path that isn't the actual failure risks a false "fixed it."

**Details:**
Measured directly against the local server (loopback, so this is a *floor*, not the worst case):
- `GET /api/conversations/all` returns **4.6MB raw / ~0.8MB gzipped**, **1543 rows**, and takes **2.3s even on loopback**.
- The sidebar (`#convList`) only renders ~234 rows from that payload — the other ~1300 rows are fetched but unused by the view that's waiting on them.
- This fires at boot alongside a herd of other slow endpoints seen in the server log: `ux-fixes/health` (1.5s), `group-chats/active` (1.9s), plus `session-status`, `sessions/live-activity`, `repo/worktrees`, `model-advisor` — all `[SLOW]`-logged.
- Over LTE (the phone-via-Tailscale-Serve path), this payload size turns a sub-second local load into a multi-second stall on the sidebar's "Loading…" placeholder — a real UX cost even though it isn't the bug that triggered the investigation.

This is exactly the class of issue `CLAUDE.md`'s "Performance gates" section warns about: an O(all-conversations) payload shipped whole and polled, invisible at test-fixture scale, real in production with 1000+ transcripts.

**Approach discussed (ranked):**
1. **Slim the list payload** — sidebar needs name/status/timestamp/repo, not full per-row data. Cut ~1543×3KB rows down to only the fields the list view renders.
   - Value: H. Risk: M — touches `/api/conversations/all`, which is a stable `/api/*` contract (`CLAUDE.md` treats field removal/shape change as breaking → major version bump), and touches the perf-budget call-count test (`tests/test_perf_budget.py`) that must be updated, not relaxed.
   - Confidence: H that this is the right lever (it's the literal envelope size problem).
2. **Scope the initial fetch to the current repo / paginate**, lazy-load the rest.
   - Value: H. Risk: M. Confidence: M.
3. **Paint from cache first** (localStorage/disk cache of the last-known list), reconcile once the live fetch lands.
   - Value: M. Risk: L — additive, no contract change. Good low-risk stopgap ahead of #1.

**Status:** Parked — do as its own deliberate slice with the perf-budget test updated in step, not "in case" bundled into an unrelated bug fix.
