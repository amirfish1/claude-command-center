# Shared-clone Git guard

When several agents work in the same checkout, a reset can discard another
session's edits and broad staging can include its unfinished work. The optional
`hooks/shared-clone-guard.py` Claude Code hook blocks these common accidents and
explains how to use an isolated worktree and explicit paths instead.

## Enable it

The hook is off until you register it. It needs Python 3.9+ and works independently
of the CCC dashboard. Merge this entry into `hooks.PreToolUse` in your Claude
Code user settings (`~/.claude/settings.json`) or this project's local settings
(`.claude/settings.local.json`). Keep your other hooks. Replace the script path
with the absolute path to your CCC checkout:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"/absolute/path/to/ccc/hooks/shared-clone-guard.py\"",
            "timeout": 2
          }
        ]
      }
    ]
  }
}
```

Start a new Claude Code session after changing settings. No CCC dashboard,
worker, or WatchTower restart is required. CCC does not automatically install
this hook or change your agent settings.

The hook uses the documented
[PreToolUse deny decision](https://code.claude.com/docs/en/hooks#pretooluse-decision-control):
matching calls return a JSON `permissionDecision` of `deny`, with a reason shown
to the agent. Other calls return no decision and continue through Claude Code's
normal permission checks.

## When protection applies

Automatic mode protects a Git worktree when both conditions hold:

1. Its current branch is `main`.
2. Another live Claude Code session's registry entry points at the same
   worktree. The current session ID is excluded; subdirectories and symlinks
   resolve to the same worktree. A separate linked worktree is independent.

Registry entries come from `~/.claude/sessions/<pid>.json`, or
`$CLAUDE_CONFIG_DIR/sessions/<pid>.json` when set. The process must currently
identify as Claude Code, including its native versioned binary. Stale registry
files and CCC's own peer entry do not activate protection.

For a known shared checkout, including teams with other engines, set
`CCC_SHARED_CLONE=1` in the hook command:

```json
"command": "CCC_SHARED_CLONE=1 python3 \"/absolute/path/to/ccc/hooks/shared-clone-guard.py\""
```

This flag protects every matching Git command seen by that hook, regardless of
branch or session discovery. Put a forced hook in project-local settings if you
only want to protect one project. `true` and `yes` also enable the flag; other
values use automatic detection. Removing the hook entry disables the feature.

## Commands covered

| Blocked in a shared worktree | Allowed alternatives |
|---|---|
| `git reset --hard` | Inspect with `git diff`; do discard work in an isolated worktree |
| `git checkout [revision] -- <path>` | Review the diff before editing explicit files |
| `git restore <path>` | `git restore --staged <path>` to unstage only |
| `git clean -f` (including `-fdx`) | `git clean -n` to preview |
| `git stash drop` and `git stash pop` | `git stash list`, `show`, or `apply` |
| `git add -A`, `git add --all`, `git add .`, `git add ./`, `git add :/` | `git add -- src/file.py tests/test_file.py` |
| `git commit -a`, `git commit --all`, `git commit -am ...` | `git commit --only <paths> -m ...` |

`git restore --staged --worktree` also changes working files and is blocked.
Supported dry runs (`clean -n`, `add -n`, and `commit --dry-run`) and help calls
are allowed. Read-only Git commands and explicit-path staging pass through.

Commands in shell lists (`&&`, `||`, `;`, pipelines, newlines, and subshells) are
checked. Literal `cd`, Git's `-C`/`--work-tree`, common `env`/`command`/`nice`
wrappers, and literal `sh`/`bash`/`zsh -c` commands are understood. Quoted messages
and examples printed with `echo` or `printf` do not activate the guard. Denials
contain only a fixed command label, never the full command or commit message.

## Performance and limits

The hook parses commands first. Safe commands, forced mode, and automatic mode
outside `main` do no session discovery. Automatic detection uses one `ps`
snapshot with a 0.2-second timeout, then reads only registry files for up to 256
live candidate processes within a 0.4-second detection budget. It never scans
transcripts or contacts CCC or any remote service. Parsed registry metadata is
cached by modification time and size in
`$CLAUDE_CONFIG_DIR/command-center/git-guard-cache.json` (default
`~/.claude/command-center/git-guard-cache.json`). This local cache contains session
IDs and working directories, is written with mode `0600`, and may be deleted.

This is an accident guard for literal shell commands, not a shell interpreter or
security sandbox. It does not resolve Git aliases, shell functions, variables,
`eval`, remote commands, commands inside scripts, here-documents, or every form
of command substitution. Conditional directory changes are approximated in
text order. Git directory overrides and unusual process launchers may prevent
automatic detection. Input is capped at 128 KiB, shell recursion at three nested
wrappers, and registry rows at 16 KiB. Malformed input and unavailable, timed-out,
or incomplete discovery return no decision. Use forced mode for a checkout that
must be protected even when automatic discovery is unavailable. Worktree
isolation remains the reliable way to keep parallel agents' files and indexes
separate.

## Verify without changing files

This sends a command string to the hook; it does not execute Git:

```bash
printf '%s\n' '{"tool_name":"Bash","session_id":"example-session","cwd":"/tmp","tool_input":{"command":"git reset --hard"}}' \
  | CCC_SHARED_CLONE=1 python3 /absolute/path/to/ccc/hooks/shared-clone-guard.py
```

Expect `"permissionDecision": "deny"`. Change the command string to
`git restore --staged example.txt` and expect no output. Run the targeted tests
with `python3 -m pytest tests/test_shared_clone_guard.py -q`.
