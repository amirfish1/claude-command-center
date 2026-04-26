# Changelog

All notable changes to this project will be documented here.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Live "what's running" signal on cards and chat pane.** The kanban card now
  surfaces the currently-executing tool (e.g. `Bash npm test`, `Read foo.py`)
  as an animated badge while a session is live, instead of showing only a glow.
  The conversation detail pane gains a sticky strip that does the same, refreshed
  every 5s from `/api/session-status`. New `PreToolUse` hook (`hooks/pre-tool-use.py`)
  writes a `<sid>_in_flight.json` marker so long-running tools (Bash, WebFetch)
  read as "running 8s" instead of "8s ago"; PostToolUse clears it on completion.
  Hook auto-installs into `~/.claude/settings.json` on next server start.
- `CCC_ALLOWED_ORIGIN` env var — comma-separated list of additional origins
  added to the same-origin POST allowlist. Pair with `CCC_BIND_HOST=0.0.0.0`
  to reach the UI from a phone or other device over a trusted network
  (Tailscale, VPN). The same-origin check otherwise rejects POSTs from any
  Origin that isn't `localhost` / `127.0.0.1` / `[::1]`, which is what made
  Tailscale access stop working after the OSS-launch security hardening.
  Documented in `README.md` and `SECURITY.md`; startup prints the active
  allowlist when set. There is still no auth — every entry is a peer that
  can run commands as you.
- **First-class trusted-network access.** The `CCC_ALLOWED_ORIGIN` env var
  added in the previous commit is now joined by two more layers, all merged
  into the same-origin allowlist at startup: a persisted JSON config at
  `~/.claude/command-center/network.json` (so settings survive shell
  restarts), and a `CCC_TRUST_TAILNET=1` opt-in (or `trust_tailnet: true` in
  the JSON) that shells out to `tailscale status --json` and adds the local
  node's MagicDNS hostname + Tailscale IPs automatically. New endpoints
  `GET /api/network-config` (returns the live config plus a tailnet probe)
  and `POST /api/network-config` (writes the JSON, restarts in-place via
  `os.execvp`). The POST is **localhost-only** even though the broader
  allowlist accepts tailnet origins for everything else — a peer cannot
  expand its own trust further. New "Network access…" entry in the sidebar
  settings popover drives all of it from the UI: a checkbox to bind on all
  interfaces, a checkbox to trust the detected tailnet, and a free-text
  field for additional origins (e.g. other VPNs). Env vars still win when
  set, so CI overrides keep working. README and SECURITY.md updated, plus
  `run.sh` no longer defaults `CCC_BIND_HOST` (would otherwise clobber the
  JSON-config layer).

### Fixed
- Mobile: "Send to terminal…" input bar in the conversation panel was
  invisible on iOS Safari — the panel used `position: fixed; inset: 0`
  with no safe-area / dynamic-viewport handling, so the bottom of the
  panel (where the input lives) sat under the URL bar and home
  indicator. Now uses `100dvh` and `padding-bottom:
  env(safe-area-inset-bottom)` so the input stays visible above both,
  and resizes when the on-screen keyboard opens.

## [0.1.3] - 2026-04-24

### Added
- Claude-Desktop-style UI chrome: prominent "+ New session" button at the
  top of the sidebar, a unified panel-toggle icon (replaces the legacy
  `×` / `◀` glyphs in the conv-panel and kanban-panel toolbars) with a
  `Cmd+\` keyboard shortcut, a `Cmd+K` / `Cmd+P` "Search chats and
  projects" command palette over the existing in-memory session list,
  a sun/moon appearance picker (Theme: Light / Dark / Match system,
  Font: System / Mono — persisted to localStorage), and a sidebar gear
  popover with View on GitHub / Get help / Search sessions entries.
  Light theme is now a first-class option; the existing dark palette is
  unchanged.
- In-app bug reporting — a "Report a bug" link in the topbar opens a modal
  that auto-attaches CCC version, browser user-agent, and the currently
  selected session id, then files a GitHub issue (label `bug`) against
  `amirfish1/claude-command-center` via `gh issue create`. If `gh` is
  missing or fails, the modal renders the issue markdown so the user can
  copy it to the clipboard and file the report manually. New endpoint:
  `POST /api/bug-report`. Pattern adapted from BookYourMat. (#5)

### Fixed
- Spawn experience feels snappy: the kanban toolbar `Run` button now inserts an
  optimistic placeholder immediately (it was previously waiting for the spawn
  POST to return), the placeholder→real-card swap inherits the column via a
  60 s sticky pin so fresh sessions don't bounce Planning↔Working↔Review while
  the server settles on sidecar/live/stage, and cards fade in + animate on
  legitimate column changes instead of snap-jumping. Closes the "card appears
  late, glows, jumps around" gripe.

## [0.1.2] - 2026-04-24

### Added
- In-app update: a subtle 'Update available' pill in the topbar when a newer
  release tag is published on GitHub. Clicking opens a modal with the
  changelog link and an 'Update now' button that runs `git fetch + reset
  --hard origin/main` in the install dir (pre-flight checked for local
  modifications and branch=main) and restarts the server in-place via
  `os.execvp`. Browser auto-reconnects when the new process binds the port.
  Closes #3.
- Browser tab favicon — inline SVG data URL showing the ⌘ glyph in Claude
  orange on the app's dark surface. No new file, no server route.
- Orchestration skill `ccc-orchestration` and `POST /api/ask` endpoint —
  any Claude Code session on the machine can now spawn, inject into, and
  synchronously ask sibling sessions through CCC over plain HTTP. The
  skill is auto-installed to `~/.claude/skills/ccc-orchestration/SKILL.md`
  on server startup (skip with `CCC_SKIP_SKILL_INSTALL=1`). CCC also
  writes its base URL to `~/.claude/command-center/port.txt` on startup
  so the skill (and any other scripted caller) can discover the running
  instance without hardcoding the port. `/api/ask` reuses the existing
  `resume_session_headless` infrastructure: it tails the spawned
  subprocess's stream-json log, resolves on the next `result` event, and
  returns `{ok, text, cost_usd, duration_ms, num_turns}`. Timeouts return
  any partial assistant text seen so far and leave the underlying session
  running.
- Fenced code blocks in assistant messages now render as proper syntax-
  highlighted blocks instead of plain text with literal backticks. Supported
  langs: ts/tsx/js/jsx, py, bash/sh/zsh, json. Includes language label, a
  copy-to-clipboard button (hover state for `Copied` feedback), horizontal
  scroll for long lines, and token colors adapted from the GitHub dark
  palette. Hand-rolled regex tokenizer — no library dependency.
- Newly-appeared session cards get a transient shimmer glow on the kanban
  for ~30 seconds after first detection. Signals "this card is still
  settling — it may jump to a different column shortly." Only triggered
  for sessions that show up during a live poll; initial page load doesn't
  glow everything. CSS-only (bounded iteration count) + one scheduled
  re-render to clean up the class so the gradient doesn't linger static.
- Conversation-pane input redesigned Claude-Desktop-style: pill-framed
  container with focus ring, multi-line auto-resizing textarea (caps at
  ~160px then scrolls), inline arrow send button, and a keyboard-hint
  footer showing `⏎ send · ⇧⏎ newline`. Enter submits (Shift+Enter adds
  a newline). Send button disables when the input is empty or no session
  is open. IME composition guarded so Chinese/Japanese candidate commits
  don't accidentally fire a send.
- Each message card in the conversation view now shows a relative timestamp
  next to its line number. Tiers: `just now` (<1 min) → `N minutes ago` (<1 h)
  → `N hours ago` (<5 h) → `HH:MM` (same day, older) → `Yesterday · HH:MM`
  → `MMM D · HH:MM`. Hover reveals the full localized date-time.

### Fixed
- Pkood-spawned agents no longer produce two kanban cards (a `pkood-*` one
  with working input plus a broken "Send to terminal…" claude-session one
  that can't reach the pty). Each pkood agent is now linked to its
  underlying `~/.claude/projects/*/<uuid>.jsonl` and the duplicate card is
  absorbed into the pkood card. Linking is primarily by the
  `claude.ai/code/session_*` bridge token printed in claude's banner and
  also recorded as a `bridge_status` event in its jsonl — the shared
  token is per-process and uniquely identifies each claude instance. When
  the bridge token isn't available we fall back to a cwd + spawn-time
  window heuristic. Dead pkood agents are left un-merged so their
  underlying jsonl stays resumable via the CLI. The merged card pulls in
  the jsonl's display name and tool-use signals so the user sees one
  richer card per running agent.
- "Launch in terminal" no longer builds a broken `cd` for repos whose name
  contains hyphens. `find_session_cwd` used to fall back to decoding the
  `~/.claude/projects/` directory name by replacing every `-` with `/`,
  which silently turns `claude-command-center` into `claude/command/center`.
  The fallback also triggered for very young sessions whose `.jsonl` hadn't
  logged a `cwd`-bearing event in its first 40 lines, and the wrong path
  was cached in-process for the lifetime of the server. The fallback now
  scans sibling `.jsonl` files in the same project dir (which share a cwd)
  instead of decoding the dir name, and a miss is no longer cached.
- Sending to a Terminal.app / iTerm2 session from the split-panel input no
  longer leaves the terminal stuck on top. The osascript inject now
  captures the previously-frontmost app before activating the terminal
  and restores it after the keystroke lands, so CCC (in the browser)
  regains focus automatically. Still briefly flickers — macOS's keystroke
  API fundamentally requires the target app to be frontmost — but the
  user ends up back where they were.
- Per-card ✨ "regenerate title" button now shows on every session card that
  has a first user message, not only un-summarized ones. Previously, once a
  card was user-renamed (`name_overridden`), the button was hidden and there
  was no in-UI way back to an AI-generated title. On renamed cards the
  button is dimmed and its tooltip flags the destructive intent
  ("Regenerate title — replaces your manual rename").
- Session → GitHub-issue auto-link no longer uses the jsonl tail
  (`tail_issue_number`) as a last-resort signal. The tail scan matches any
  `gh issue …` command, `Closes #N` commit, or `github.com/.../issues/N`
  URL Claude happens to run mid-conversation, which produced false links
  when an assistant turn merely *discussed* an unrelated issue. Auto-link
  now relies solely on spawn-time identity — `display_name`, the first
  user message, and the branch — where genuine "I'm working on #NNN"
  intent lives. Explicit side-car mappings remain authoritative.
- Haiku title-summarizer subsessions no longer leak into the kanban. The
  `/api/sessions` scan now skips conversations whose first user message
  starts with our internal `Produce a concise 4-8 word title…` prompt,
  so clicking the ✨ Titles button on the CCC repo (or any repo watched
  from the CCC working directory) stops filling the board with identical
  throwaway cards.
- Archived/verified cards no longer flash back into their old column
  briefly after the click. Previously the 10s `/api/sessions` poller
  could overwrite the optimistic `c.archived = true` mutation if a
  request was already in flight when the user clicked. A short-lived
  client-side override map (30s TTL, auto-cleared once the server
  agrees) shields the optimistic value across stale poll responses.
  Fixes both the explicit Archive/Verify buttons and the drag-drop paths.
- `run.sh` no longer clobbers the persisted watched repo when launched
  from the CCC source tree. It used to force `CCC_WATCH_REPO=$PWD`
  unconditionally, which overrode `~/.claude/command-center/last-repo.txt`
  whenever the script ran from its own install dir. Now: explicit env
  var still wins, otherwise `$PWD` wins unless `$PWD` is the install
  dir AND a persisted selection exists — in which case we defer to it.

## [0.1.1] - 2026-04-23

### Fixed
- Chat input at the bottom of the conversation pane was clipped by the fixed
  topbar's 33px body padding — only a 1px border-top sliver showed. The split
  kanban view now sizes to `calc(100vh - 33px)` so the input row is visible.

### Added
- Repo picker now has a "…" button for picking folders the `$HOME` scan
  can't reach (paths outside `~/`, or nested below a top-level dir).
  The picked path is persisted to `~/.claude/command-center/custom-repos.txt`
  via a new `POST /api/repo/add` endpoint and auto-switches on success.

## [0.1.0] - 2026-04-22

Initial public release.

### Added
- Kanban board over all live + dormant Claude Code sessions, classified by
  signals (commit / push / sidecar status / GitHub label).
- GitHub issue → session → verify → close pipeline with attention queue.
- Headless `claude -p` spawn with stdin-pipe follow-up, plus resume-on-demand.
- Optional Vercel deploy polling and auto-fix-deploy.
- Optional [`pkood`](https://github.com/anthropics/pkood) integration for
  background agent runners.
- Repo picker — live-switch the watched repo from the toolbar without restarting.
- AI title regeneration via `claude -p --model haiku`.
### Security
- `127.0.0.1` bind by default. `CCC_BIND_HOST=0.0.0.0` requires opt-in and
  prints a startup warning.
- Same-origin POST check (Origin header) on every state-changing request.
- `/api/open` clamped to paths under `REPO_ROOT` / `LOG_DIR`. Default action
  is `open -R` (Reveal in Finder), not launch.
- `/api/repo/switch` validates targets against the picker allow-list.
- See [`SECURITY.md`](SECURITY.md) for the full threat model.

[Unreleased]: https://github.com/amirfish1/claude-command-center/compare/v0.1.3...HEAD
[0.1.3]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.3
[0.1.2]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.2
[0.1.1]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.1
[0.1.0]: https://github.com/amirfish1/claude-command-center/releases/tag/v0.1.0
