# Agent config consent

CCC edits nothing outside its own `~/.claude/command-center/` directory until
you approve it. This page lists every such change, how the approval works, and
how to undo it.

## What CCC may change

| Item id | Target | What it adds | Without it |
|---|---|---|---|
| `claude-hooks` | `~/.claude/settings.json` | 8 hook entries (PreToolUse, PermissionRequest, PostToolUse, Notification, Stop, SessionStart, PreCompact, PostCompact), each running a script in `~/.claude/command-center/hooks/` | no live "running X for Ns" tool status, Needs-approval badges, AskUserQuestion answers from the dashboard, Compacting badge, or precise turn-end detection for Claude sessions |
| `codex-hooks` | `~/.codex/hooks.json` | 1 PostCompact entry. Codex asks you to trust it once on the next `codex` launch. Offered only when Codex is installed. | Codex sessions are not re-oriented on their task after `/compact` |
| `skill:ccc-orchestration`, `skill:group-chat-checkin`, `skill:superpowers-to-watchtower`, `skill:fleet-verify` | `~/.claude/skills/<name>/SKILL.md`, plus `~/.codex/skills/<name>/SKILL.md` when Codex is installed | the bundled skill file | agents don't know that skill exists |
| `watchtower-skills` | `wt skills sync` symlinks into each agent's skills folder | WatchTower's bundled skills (WatchTower owns the list) | agents don't know the `wt` commands |

A skill folder that is a symlink belongs to another tool (for example
WatchTower's own `group-chat-checkin`). CCC never writes or removes through it.

### Not gated (runtime state, not config)

- `~/.claude/sessions/<pid>.json` + key file: CCC's peer registration so Claude
  sessions can message it, the same registry row every running Claude session
  writes. Turn it off with `CCC_MESSAGING_BACKEND=legacy`.
- Per-repo `.claude/logs/` written by the hooks once you approve them.
- Files the Codex and Claude CLIs create in their own homes when CCC launches
  them (for example Codex's sqlite state).
- Changes you trigger explicitly in the UI: keeping transcripts longer
  (`cleanupPeriodDays`), picking an Antigravity CLI model, editing Codex
  config from the Codex settings panel.

## How approval works

- **First run**: if Claude Code or Codex is installed, the dashboard shows one
  short message ("CCC will add N skills and M hooks to your ... setup") with
  **OK** (approve everything waiting) and **Not now** (hides it for a day; the
  topbar pill keeps counting). **Details** expands the full **Agent config
  access** list: every item, what it is for, the files it touches, and the
  exact diff, with Approve or Skip each. Items for an agent that is not
  installed are not offered automatically; Settings lists them.
- **Headless**: `ccc consent` lists items, `ccc consent show <item>` prints the
  diff, `ccc consent approve|decline <item...|all>` decides. The server log
  prints a `[consent]` line on start while anything is waiting.
- **Stored decision**: `~/.claude/command-center/config-consent.json` keeps
  each decision with a hash of what CCC proposed (hook commands, skill text,
  target folders). On start, CCC re-applies only items approved at the current
  hash. If an update changes an item, it shows as **Changed** with the new diff
  and is not applied until you approve it again; the older version stays in
  place meanwhile. Declined items stay declined.
- **Declining an installed item removes it.**
- **Upgrading from a version that installed silently**: anything already in
  your config is recorded as approved, so it keeps working, and a one-time
  "Already in your config" list offers Keep / Remove.
- **Live status off**: if you decline the Claude Code hooks, the topbar pill
  says which features are off and opens the dialog with Approve preselected.
- `CCC_SKIP_SKILL_INSTALL=1` still turns every skill item off.

## How writes are done

- Only CCC's own entries are added or removed; your other hooks, settings, key
  order, indentation (including tabs and one-line files), trailing newline,
  and non-ASCII text are kept.
- A symlinked `settings.json` stays a symlink; the real file behind it is
  edited in place, with its file mode kept.
- The previous bytes are saved to
  `~/.claude/command-center/config-backups/<timestamp>/` first (last 30 kept).
- Files that are not valid JSON are never edited; the item shows the error.
- Revoke deletes files and folders CCC itself created once they are empty
  again (a `settings.json` that is only `{}`, an empty `skills/` folder), and
  leaves them if you have added anything.

## Undo everything

**Settings > Maintenance > Agent config access > Remove everything CCC
installed**, or `ccc consent revoke all`. This strips CCC's hook entries,
removes its skill files, runs `wt skills remove` if WatchTower skills were
approved, and marks every item declined.

## API

- `GET /api/config-consent`: items (with `engines`, `engine_installed`, `hook_count`, `auto_review`; top level `auto_review`, `engines_present`) with `status` (`pending`, `changed`,
  `enabled`, `declined`), `changes` / `removal` diffs, and the one-time notice.
- `POST /api/config-consent/decide` `{"decisions": {"<id>": "approve"|"decline"}}`
- `POST /api/config-consent/revoke` `{"ids": [...]}` (omit for all)
- `POST /api/config-consent/notice-ack`

The POST routes refuse requests from off the machine or through a proxy
(phone access, tunnels).
