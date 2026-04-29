# Spawn sessions under Codex — design

Status: draft
Date: 2026-04-28
Owner: amirfish

## Problem

CCC's "New session" UI spawns a headless Claude Code process (`claude -p
--input-format stream-json …`) and tracks it on the kanban. The user wants
the same affordance for OpenAI's Codex CLI — pick `codex` instead of
`claude` from the toolbar, get a card, watch it run.

The Claude spawn pipeline is deeply Claude-specific: FIFO+stream-json
stdin for mid-run injection, `~/.claude/projects/*.jsonl` ingestion for
titles and conversation panels, the hooks pipeline (`pre-tool-use`,
`post-tool-use`, `stop`, `notification`), and the `claude --resume`
jump-into-TUI button. None of those translate to Codex without a
parallel implementation.

This spec scopes a **tier-A MVP** ("fire-and-watch") that earns its keep
on day one and defers the Claude-parity work until we know which gaps
the user actually misses.

## Out of scope

The following are deliberately deferred to a later iteration:

- `~/.codex/sessions/*.jsonl` ingestion (titles, conversation panel,
  branch/state detection for Codex cards).
- "Open in terminal" / "Open in Codex Desktop" jump-in for Codex cards
  (would require capturing the session id from the `--json` event stream
  and shelling out to `codex resume <id>`).
- Auto-titling via Haiku (currently wired to Claude's prompt format).
- Hooks pipeline equivalents (Codex doesn't call CCC's hooks).
- Mid-run `inject_into_spawned` (Codex `exec` is one-shot — the prompt
  comes from argv and the process exits when the model is done).
- Tighter sandbox modes than Codex's `dangerously-bypass-approvals-and-sandbox`
  (could be exposed via `CCC_CODEX_SANDBOX` later).

## Solution

Replace the existing "pkood spawn" checkbox in the kanban toolbar with a
**2-way engine selector** — `claude` (default) and `codex`. A new backend
function `spawn_session_codex()` sits next to `spawn_session()` and
shells out to the bundled Codex CLI. A new endpoint `POST
/api/sessions/spawn-codex` routes to it. Codex cards appear on the same
kanban as Claude cards but with a `codex` chip and a simplified
lifecycle (spawning → done).

### Why drop the pkood toggle

The pkood checkbox at `static/index.html:3028` is rarely used (the user
forgot it existed during this design pass). Replacing it cleans up the
toolbar without losing functionality:

- The `pkood:` prompt-prefix shortcut at `static/index.html:10290` and
  the `/api/pkood/spawn` endpoint stay — they still serve the
  orchestration layer and the `pkood` skill.
- Only the *toolbar/modal engine choice* loses pkood. Anyone who wants
  pkood spawning can prefix the prompt with `pkood:` exactly like today.

## Section 1 — Engine selector (UI)

Replace the single `kptPkoodToggle` checkbox with a 2-way radio (or
segmented button) labelled **Engine** with options `claude` (default)
and `codex`. The control lives in the same View-menu popover slot the
pkood checkbox occupies today, and is mirrored in the new-session
modal at `static/index.html:10362`.

**Persistence.** Selection is stored in `localStorage` under
`ccc.spawnEngine` (values: `"claude"` | `"codex"`). It survives reload
but is *not* sticky-per-card — every spawn reads the live value of the
selector at submit time.

**Codex unavailable.** If the backend reports the Codex binary can't be
resolved (see Section 2), the `codex` radio renders disabled with a
tooltip "Codex CLI not found — set `CCC_CODEX_BIN` or install Codex".
The frontend asks `/api/sessions/spawn-codex/availability` (a tiny GET
that runs the resolver and returns `{available: bool, reason?: string}`)
on initial load and after each visibility-change event so the UI catches
a Codex install happening mid-session.

**Card chip.** Cards spawned via Codex render a small `codex` chip next
to the existing branch chip on the sidebar row and on the kanban card.
The chip is decorative-only (no click handler). Background color is
picked at implementation time from the existing CCC palette — the only
constraint is that it must be visually distinct from the branch chip,
the PR-number chip, and the spawn-state pulse so a Codex card is
recognizable in a glance across a row of mixed Claude/Codex cards.

**Cleanup.** Remove the `kptPkoodToggle` checkbox markup and the
`$kptPkoodToggle.checked` branches in the dispatcher (around
`static/index.html:10290` and `:10385`). Replace with reads from the
new selector. Leave the `pkood:` prompt-prefix shortcut intact.

## Section 2 — Backend spawn (`server.py`)

A new function `spawn_session_codex(prompt, name=None, cwd=None)` mirrors
`spawn_session()` (around `server.py:4551`) and returns the same shape
`{ok: bool, pid: int, name: str, log: str, error?: str}`.

**Binary resolution** (in priority):

1. `os.environ["CCC_CODEX_BIN"]`, if set and executable.
2. `shutil.which("codex")` — picks up Homebrew / Cargo / npm-global
   installs that the login PATH exposes.
3. `/Applications/Codex.app/Contents/Resources/codex` — the macOS
   desktop app's bundled CLI (verified at `codex-cli 0.125.0-alpha.3`
   on the dev machine).
4. None of the above → `spawn_session_codex` returns `{ok: false, error:
   "Codex CLI not found — set CCC_CODEX_BIN or install Codex"}` and the
   POST handler returns 503.

**Command shape:**

```
<bin> exec
  --json
  --skip-git-repo-check
  --dangerously-bypass-approvals-and-sandbox
  --model <model>
  --cd <cwd>
  -- <prompt>
```

- `--model` from `os.environ.get("CCC_CODEX_MODEL", "gpt-5.5-codex")`.
  The default is a placeholder we verify against the live CLI at
  implementation time and patch if Codex names the model differently
  (e.g. `gpt-5.5` or `codex-5.5`). The env-var override means a user can
  swap models without a code change.
- `--cd <cwd>` — Codex's working-directory flag. Cleaner than relying
  on Popen's `cwd=`; keeps the workspace root explicit in the command
  log.
- `--dangerously-bypass-approvals-and-sandbox` matches the spirit of
  the Claude path's `--dangerously-skip-permissions`. Future work can
  expose `CCC_CODEX_SANDBOX` (`workspace-write` / `read-only`).
- `--skip-git-repo-check` is set unconditionally so spawning into a
  non-git working directory doesn't error out.
- `--json` produces JSONL events on stdout — useful for future
  ingestion (Section "Out of scope") even though MVP just tees it to
  the log.
- `--` before the prompt is critical: prompts can start with `-` and
  we don't want them swallowed as flags.

**Process management:**

- `subprocess.Popen` with `start_new_session=True` so the child
  outlives a CCC restart, mirroring the Claude path.
- `stdin=subprocess.DEVNULL` — Codex `exec` reads the prompt from argv
  and exits. No FIFO; no stdin injection. (See Out of scope.)
- `stdout=open(log_path, "w")`, `stderr=subprocess.STDOUT`, identical
  to Claude.
- `log_path = LOG_DIR / f"spawn-codex-{slug}-{timestamp}.log"`. The
  `codex-` prefix is grep-friendly and makes log triage trivial.

**Tracking.** Append to the existing `_spawned_sessions` list with a
new field `engine: "codex"`. Persist to `SPAWNED_PIDS_FILE`
(`server.py:1419`) with the same field so the boot-time
`_reattach_spawned_orphans` sweep can branch on engine and skip the
Claude-specific reattach steps (e.g. it must not try to ingest a
`~/.claude/projects` JSONL that doesn't exist for a Codex pid).

**Version pin comment.** A top-of-function comment names the Codex CLI
version we tested against (`codex-cli 0.125.0-alpha.3` as of this
spec) so future-us can grep for it when a flag rename breaks the spawn.

## Section 3 — Routing

A new endpoint `POST /api/sessions/spawn-codex` lives next to the
existing `/api/sessions/spawn` and `/api/pkood/spawn` (around
`server.py:8725`). Same payload, same validation:

- Body: `{prompt: string, name?: string, cwd?: string}`.
- Empty prompt → 400.
- `cwd` (if present) must be absolute and an existing directory → 400.
- Same-origin CSRF check (`_check_same_origin`).

A second tiny endpoint `GET /api/sessions/spawn-codex/availability`
returns `{available: bool, reason?: string, bin?: string}` so the
frontend can grey out the `codex` radio when the binary can't be
resolved (Section 1).

The dispatcher in `static/index.html` (around `:10302` and `:10385`)
selects the endpoint based on the engine selector value — `claude` →
`/api/sessions/spawn`, `codex` → `/api/sessions/spawn-codex`.

## Section 4 — Card lifecycle

Codex cards reuse the existing optimistic-pending placeholder
(`pending-spawn` class, `recently-born` sticky) — same code path as
Claude.

**State transitions.**

- **spawning** while `proc.poll() is None`.
- **done** once it exits. Exit code surfaces in the card's tooltip;
  non-zero exits are also reflected in a small red dot on the chip
  (the existing `pending-spawn` failure styling).
- No live message stream (would require JSONL ingestion — out of
  scope).
- No auto-rename (the slug from the prompt is the title).
- No needs-attention flag (no hooks).
- "Open in terminal" affordance is hidden for Codex cards (`if
  card.engine === 'codex'`); a future tier-B iteration adds it back
  with `codex resume <id>`.

## Section 5 — Tests / verification

Per `CLAUDE.md`'s "smoke is about import-time correctness" rule:

- Add an import-time assertion in `tests/test_smoke.py` that
  `spawn_session_codex` exists on the module.
- Drop a `changelog.d/added-codex-spawn-2026-04-28.md` snippet under
  the existing convention (`Keep a Changelog`, `added` category).
- No subprocess test — we don't mock external systems in the smoke
  test.

**Manual smoke** (run before merging):

1. Pick `codex` in the engine selector.
2. Type a small prompt (e.g. "list the files in this repo and exit").
3. Watch the card appear with a `codex` chip in the spawning state.
4. Tail the log file at `LOG_DIR/spawn-codex-*-*.log` and confirm
   JSONL events are streaming.
5. Confirm card transitions to `done` after the model finishes.
6. Restart CCC mid-spawn and confirm the card reattaches via
   `_reattach_spawned_orphans` without trying to read a
   `~/.claude/projects` JSONL.

## Section 6 — Risks / open questions

1. **Model string `"gpt-5.5-codex"`.** Placeholder. Verify the exact
   string at implementation time by running `<bin> exec --model
   <candidate> "echo hi"` and seeing what Codex accepts. Patch the
   default if needed; document the tested value in the CHANGELOG snippet.
2. **Codex CLI is alpha (`0.125.0-alpha.3`).** Flag names could
   change in a future bump. Mitigation: top-of-function comment naming
   the tested version, and the env override for the binary path so a
   user on a newer CLI can keep working while we patch the default.
3. **macOS-only fallback path.** `/Applications/Codex.app/...` is
   macOS-only, but CCC itself is macOS-only by design (Pkood,
   `osascript`-driven Terminal launches), so this is consistent. The
   resolver still tries `which codex` first, so a Linux user with the
   Codex CLI on PATH would just work.
4. **Reattach correctness.** If a Codex spawn is in-flight when CCC
   restarts, `_reattach_spawned_orphans` must branch on the new
   `engine` field and *not* try to find a `~/.claude/projects` JSONL.
   The implementation plan needs a unit step that exercises this path.

## Implementation order (preview for writing-plans)

1. Backend resolver + `spawn_session_codex` + new endpoint.
2. Boot-time reattach branch on `engine` field.
3. UI engine selector + Codex availability probe + Codex chip.
4. Remove `kptPkoodToggle` UI (keep `pkood:` prefix path).
5. Tests, changelog snippet, manual smoke.
