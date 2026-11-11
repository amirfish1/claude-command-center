# On-call inside your agent

Install in Claude Code, after this branch is merged:

```text
/plugin install oncall --marketplace amirfish1/claude-command-center
```

The two-step form is `/plugin marketplace add amirfish1/claude-command-center`,
then `/plugin install oncall@claude-command-center`. The existing marketplace
keeps its dashboard plugin and adds `./plugins/oncall`. Nothing was published
to a marketplace or registry during development.

CCC and Python 3 must be on PATH. If CCC is missing, the first session hook
prints one sentence linking to its installation instructions. The plugin does
not download CCC, enable the preview or choose a phone channel for you.

Run `/oncall:setup`, then `/oncall:test` to check your chosen channel. Setup and
test send messages only when requested. `/oncall:status` shows On-call state.
`/oncall:pause` maps to `ccc oncall off`; `/oncall:resume` maps to `on`.
These commands are instructions to your agent to run local commands, so using
them can consume the agent's normal usage. The hook scripts make no model calls.

## Choose a mode

**Notify only is the default.** Hooks return promptly. CCC's existing observer
sends questions and plugin completion notices after 120 seconds of idle time.
Permission requests are recorded without waiting. The server must be able to
see the session. Missing CCC, a stopped service or malformed input does not
block the agent. This mode adds no terminal reply transport.

**Wait for my phone is opt-in.** Run `/oncall:wait on`, or:

```sh
ccc oncall phone-wait on --seconds 180
```

A Stop hook ending on an explicit question waits for your phone reply. A
PermissionRequest hook waits for exactly `allow` or `deny`. These alerts do not
wait for the normal idle grace: you have explicitly chosen to hold the session.
Existing channel polling, quiet hours and delivery caps still apply. A paused
or disabled On-call service refuses a new wait.

The wait defaults to 180 seconds and accepts 1 to 540 seconds. The two hook
entries declare a 600-second host timeout. All other hooks keep a three-second
host timeout. `ccc oncall phone-wait off` stops new waits. For one process only,
use `CCC_ONCALL_WAIT=1` and optionally `CCC_ONCALL_WAIT_SECONDS=180` when starting
the CLI; environment values override saved preferences.

The phone reply is returned through the waiting hook into the same CLI process.
No second agent session is launched. Answers are limited to 8,000 characters. A question answer becomes a Stop
continuation reason. Permission output applies to that one pending request;
it does not save an allow rule or change the permission mode. Interactive
question tools such as AskUserQuestion and ExitPlanMode are not handled by
this permission adapter because they need a different answer schema.

If the wait times out, the service goes down or the hook is canceled, the hook
returns no decision. Stop can finish; a permission request goes through the
CLI's normal approval flow. A resumed hook continuation is not held again when
`stop_hook_active` is true, preventing an automatic continuation loop. Start a
new human turn to use phone waiting again.

## Cancel a wait

From another terminal:

```sh
ccc oncall cancel-wait SESSION_ID
```

The session ID is included in the local On-call status API's waiting rows.
UserPromptSubmit and SessionEnd hooks also cancel waits for their session when
the CLI emits those events. SIGINT and SIGTERM cause the hook to cancel its
mailbox. We do not promise that typing Enter or Esc cancels a waiting Stop or
PermissionRequest hook: that keyboard behavior has not been established for
both CLIs. Use the explicit command or wait for the deadline.

Every wait has its own number and a private collection claim bound to its
session. A reply to another number cannot release it. A canceled or expired
reply is never sent through another transport. Server restart clears orphaned
hook alerts. Replies are collected at most once. A crash after collection but
before the CLI reads stdout can lose an answer; it is not replayed. A receipt
acknowledges the reply queue, not that the agent has already acted on it.

## Codex

```sh
ccc oncall install-codex
```

This merges lifecycle handlers into `$CODEX_HOME/hooks.json`, or
`~/.codex/hooks.json`. It never edits config.toml. Existing unrelated handlers
are preserved. Review and trust the handlers in Codex `/hooks`, then run
`ccc oncall setup` and choose a mode as above. Normal installation never
bypasses the CLI's hook trust review.

Current Codex hooks document the same Stop continuation and permission decision
shapes used here. The older `notify` option covers turn completion only and is
not used. In notify-only mode, a standalone Codex terminal cannot receive a
phone reply through this plugin after its hook exits. For persistent delivery
without holding a hook, start a session through CCC instead:

```sh
ccc spawn --engine codex --model gpt-6.1-terra --effort medium "Your task"
```

That route uses CCC's existing session transport and needs no open dashboard.
The existing `scripts/oncall-session-proof.py --with-agent` checks that route;
it is separate from this plugin's standalone hook proof.

## Local service, restart and moving the checkout

SessionStart requests `ccc oncall agent-service` in the background. It reuses a
reachable loopback CCC service or starts `server.py --host 127.0.0.1` on a free
port. It opens no browser and keeps the server's binding and Origin checks.
It is the full CCC server, not a separate small notification daemon. A recorded
live service is reused even if its health endpoint is busy. An unknown busy
listener is not replaced. Logs are
in `oncall-agent.log`. This direct launch alone is not a reboot supervisor.

For crash recovery and startup at login, run this existing installer **before
starting your first agent session**:

```sh
CCC_BIND_HOST=127.0.0.1 ccc --install-service
```

This installs the existing launchd service on macOS or systemd user services on
Linux. The service definitions enable startup and restart after a crash. On
Linux, starting before login or staying up after logout additionally requires
`loginctl enable-linger` for your user, subject to your system's permissions.
This is login startup, not a claim that every machine runs it before login.
The installer was not run on the development machine. If an unmanaged CCC is
already running, stop that specific process before installing the managed
service; the installer does not take over an arbitrary existing process.

Hook markers, wait preferences and hook service discovery honor CCC_STATE_DIR.
Set the same value for the agent and the service. This does not relocate every
other CCC subsystem. If your supervisor uses a fixed port with a custom state
directory, set CCC_SERVER to that loopback URL for the agent too.

The installed Codex bootstrap lives in CODEX_HOME and discovers `ccc` through
PATH each time. Claude's cached bootstrap does the same. After moving the CCC
checkout, update the `ccc` symlink or PATH entry. The hooks need no path edit.
Re-run `ccc --install-service` if installed service definitions still point to
the old checkout. Running `install-codex` again upgrades the earlier path-based
On-call entries; unrelated hooks remain. A missing or broken CCC executable
fails open. To uninstall Codex hooks, remove only handlers referencing
`ccc-oncall-hook.py`, then remove that bootstrap file. Leave other handlers.

## Documented contracts

Official documentation checked on 2026-11-11:

- [Claude plugin manifest](https://code.claude.com/docs/en/plugins-reference),
  [marketplaces](https://code.claude.com/docs/en/plugin-marketplaces) and
  [installation](https://code.claude.com/docs/en/discover-plugins) define the
  manifest, commands, skills, component paths and GitHub marketplace resolution.
- [Claude Stop output](https://code.claude.com/docs/en/hooks#stop-decision-control)
  supports `decision: block` plus `reason` to continue the same conversation.
  [PermissionRequest output](https://code.claude.com/docs/en/hooks#permissionrequest-decision-control)
  supports allow and deny under `hookSpecificOutput.decision`.
  [Command timeouts](https://code.claude.com/docs/en/hooks#common-fields) default
  to 600 seconds on these events. A timeout discards output and normally makes
  no decision. We return before the configured host timeout.
- [Codex hooks](https://learn.chatgpt.com/docs/hooks) documents Stop continuation
  as a new continuation prompt, PermissionRequest allow/deny, 600-second default
  timeouts on these events, and required trust review. These are synchronous
  command hooks, not asynchronous notifications.
- [Codex notification configuration](https://learn.chatgpt.com/docs/config-file/config-advanced)
  distinguishes the older notify program from terminal notifications.

## Reproduce the checks

```sh
python3 scripts/oncall-plugin-proof.py
python3 scripts/oncall-plugin-session-proof.py
python3 scripts/oncall-plugin-session-proof.py --with-agent
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py -q
```

The install proof needs Linux, Python and the installed Claude CLI. It invokes
Claude's validator, local marketplace installation and listing commands in a
temporary CLAUDE_CONFIG_DIR. It makes no Claude model request and reads no
Claude credentials. The plugin includes a 1024px listing icon and its own
README instead of inheriting a localhost listing link.

The session proof adds Docker with pinned local ntfy v2.17.0 and the real CCC
server. It drives recorded hook payloads through real HTTP and ntfy delivery.
It checks question replies, permission allow/deny, two sessions waiting at
once, a wrong number, a wrong-session collection claim, timeout, service down,
opt-out, cancellation, loop prevention, marker cleanup and a foreign Origin.

`--with-agent` additionally permits one paid `gpt-6.1-terra` session at medium
effort. Only Codex auth.json is copied into the temporary CODEX_HOME. A random
answer is sent only through ntfy, then checked in the continuing agent's output.
The proof bypasses hook trust for that single vetted invocation; the installer
does not. No real-session permission prompt is manufactured.

Both proofs use temporary HOME, CCC_STATE_DIR, CODEX_HOME, WatchTower storage,
an allow-listed environment, WT_NO_AUTOSTART and free loopback ports. All
started processes and the named container are removed on exit, including
failure. Linux /proc identifies detached processes by their exact temporary
HOME. A host crash or uncatchable termination cannot run cleanup. The session
proof has a ten-minute total deadline. A renewed copied Codex login is discarded
and may require a later sign-in.

Recorded payload tests verify returned JSON, not that Claude consumes it.
A real Claude Code session must still confirm continuation, permission handling
and keyboard cancellation. No Claude usage or credentials were used. Actual
system-service installation, OS reboot and OS-managed crash recovery were not
executed; the documented behavior comes from the existing service definitions.

Observed on Linux with Claude Code 2.1.322 and Codex 0.167.1:

| Check | Result |
| --- | --- |
| Claude validation, local install and listing | Passed, with no icon or README warnings |
| Isolated install, reuse while suspended, relaunch after crash | Passed in 9.9 seconds |
| Recorded hooks through the real server and local ntfy | Passed in 63.5 seconds |
| Same protocol checks plus one real Codex exec session | Passed in 90.8 seconds |

The real Codex run was `exec`, not an interactive terminal keyboard test. It
proved continuation with a random phone-only answer. It did not test a real
Codex permission prompt. The final pure-protocol rerun also covers subsequent
duplicate-alert guards and lighter fast-path imports without another model run.

Busy parallel verification exposed short hook timing failures and an existing
demo's two-second performance gate. No timing bound was raised. A slow health
probe also exposed duplicate service startup; recorded-process reuse and the
suspended-service proof now cover that fix. These results do not promise that
an overloaded OS always schedules a hook within two seconds.

Final verification after the duplicate-alert and service-reuse fixes:

- Local server/ntfy payload proof: passed in 65.2 seconds.
- Required On-call and performance suite: 534 passed, one timing failure, in
  221.4 seconds. `test_scripted_full_loop_and_perf` took 2.928 seconds against
  its unchanged two-second bound. All its functional assertions passed.
- An isolated comparison ran the same demo from untouched merged main in
  7.530 seconds and this branch in 3.808 seconds while CPU pressure exceeded
  70 percent. This supports host contention as the cause, but does not replace
  a passing gate. An isolated rerun of the failing test also exceeded the bound
  at 2.682 seconds. Re-run the required suite on an uncontended host before merge.

The transport is suitable for an opt-in preview with the stated Claude limits.
Do not treat this branch as having a green final performance gate yet.
