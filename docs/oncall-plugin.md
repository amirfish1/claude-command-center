# On-call from your agent

This branch adds an agent entry point for the existing On-call preview. It does
not yet prove a phone round trip from a standalone Claude Code or Codex terminal.

After this branch is merged, install in Claude Code with one line:

```text
/plugin install oncall --marketplace amirfish1/claude-command-center
```

The two-step form is `/plugin marketplace add amirfish1/claude-command-center`,
then `/plugin install oncall@claude-command-center`. The repository is a marketplace
because its `.claude-plugin/marketplace.json` points to `./plugins/oncall`.
Nothing has been submitted to a public marketplace or registry.

Run `/oncall:setup`, `/oncall:status`, `/oncall:test`, `/oncall:pause`, or
`/oncall:resume`. These commands ask the agent to run the corresponding
`ccc oncall` command. Pause maps to `off`; resume maps to `on`. Setup still
needs your channel choices. Test sends a real message to your configured
channel. Commands are agent instructions, not a free local command interpreter.

CCC must be installed and on PATH. The first session hook prints one sentence
with the installation link if it is missing. Python 3 must also be on PATH.
The plugin does not download or install CCC itself.

`ccc oncall agent-service` reuses a reachable loopback service or starts
`server.py` in the background with `--host 127.0.0.1`. It opens no browser.
It keeps the existing server, origin checks, session observer, reply routing
and preview gate. It is the full CCC server without a browser, not a smaller
notification daemon. Logs are in `oncall-agent.log` in the CCC state directory.
It is not installed as a login service and is not restarted after a crash.
SessionStart requests this startup in a detached process so the hook returns.
Setup must still enable the preview and configure a channel. Installing alone
never enables messaging.

## Codex

```sh
ccc oncall install-codex
```

This merges SessionStart, Stop, PermissionRequest and PostToolUse handlers into
`$CODEX_HOME/hooks.json`, or `~/.codex/hooks.json` when CODEX_HOME is unset.
It preserves existing handlers and does not edit config.toml. Repeating the
command with the same CCC installation is idempotent. Moving CCC requires
removing its old handlers before reinstalling. Review the handlers in Codex
`/hooks` and trust them, then run `ccc oncall setup`. The installer does not
bypass Codex's trust review. To remove them, delete only the handlers whose
command points to CCC's `plugins/oncall/scripts/hook.py`.

The installer uses current lifecycle hooks, not the older `notify` setting.
`notify` currently covers only `agent-turn-complete` and receives JSON as an
argument. Lifecycle hooks receive JSON on stdin and also cover permission
requests. Neither mechanism by itself provides an asynchronous phone reply
transport. Replies rely on CCC's existing session delivery support.

## Behavior and limits

The adapter imports the installed `hooks/stop.py`, `permission-request.py`,
`notification.py` and `post-tool-use.py`. It does not implement a second alert
engine. `ccc_server/oncall/session_signals.py` remains responsible for question
selection, the 120-second idle grace and deduplication. Existing On-call
channels number messages and existing reply routing delivers answers.

- Plugin Stop hooks opt that idle turn into a completion notice even without
  a question. Both questions and completion notices keep the 120-second grace.
  Sessions without the plugin retain the existing question-only behavior.
- Permission hooks record a badge but never wait for a phone answer. A fast
  returning hook cannot later return an approval decision. On terminals without
  another CCC approval transport, answer permissions in the terminal.
- Standalone terminal reply delivery has not been verified. Existing dashboard
  session proof results do not establish that this plugin can resume every CLI
  terminal session. No new reply transport is included here.
- Hooks swallow failures and do not block the agent. Desktop notifications and
  auto-title calls are disabled in the adapter. Missing Python is outside the
  adapter's control; the host CLI may report a hook launch failure.
- Hooks use the existing live-state location under HOME. A custom CCC_STATE_DIR
  does not relocate those existing hook markers.

## Verification

Run from the checkout:

```sh
python3 scripts/oncall-plugin-proof.py
python3 -m pytest tests/test_oncall_agent_plugin.py -q
python3 -m pytest tests/test_oncall_*.py tests/test_perf_budget.py -q
```

The proof needs Linux, the installed Claude CLI and Python. It uses temporary
HOME, CLAUDE_CONFIG_DIR, CODEX_HOME, CCC_STATE_DIR and WatchTower state, an
allow-listed environment, no WatchTower autostart and a free loopback port.
It reads no Claude credentials and starts no model session. Cleanup uses the
same temporary HOME process marker as `oncall-session-proof.py`.

Observed with Claude Code 2.1.322 and Codex 0.167.1:

- Claude's own validator accepted the plugin and marketplace. It reported
  listing-only warnings about the missing icon and a repository README link.
- Local marketplace installation and `plugin list --json` reported the plugin
  enabled in the temporary Claude configuration.
- Headless startup and address reuse passed. The final proof took 4.7 seconds, including a fast SessionStart invocation.
- Fifteen adapter tests passed in about 3 seconds. They drive sample hook JSON,
  missing CCC, a stopped service, malformed input, permission cleanup and an
  idempotent Codex install that preserves config.toml.

The final required On-call and performance suite passed: 431 tests in 64 seconds.
An earlier run overlapped a server source edit and failed one source-inspection
assertion; that individual test and the final full run passed without relaxing
a bound.

No real Claude session was run. Hook discovery in a real Claude session,
question delivery to a phone, and phone replies continuing that session still
need a real-session proof. No paid Codex session or local ntfy round trip was
run for this plugin. The broader existing proofs are separate evidence.

## Sources checked

Official public documentation, read on 2026-11-11:

- [Claude plugin manifest](https://code.claude.com/docs/en/plugins-reference):
  `.claude-plugin/plugin.json`, commands, skills and component paths.
- [Claude marketplaces](https://code.claude.com/docs/en/plugin-marketplaces):
  marketplace metadata and repository-relative plugin sources.
- [Claude installation](https://code.claude.com/docs/en/discover-plugins):
  GitHub shorthand, marketplace add and one-command installation.
- [Claude hooks](https://code.claude.com/docs/en/hooks): lifecycle events,
  stdin payloads, timeouts and permission output.
- [Codex hooks](https://learn.chatgpt.com/docs/hooks): hooks.json, lifecycle
  handlers and required trust review.
- [Codex notification configuration](https://learn.chatgpt.com/docs/config-file/config-advanced):
  notify versus terminal notifications.

Local checks used `claude plugin --help`, `claude plugin install --help`,
`claude plugin validate`, `claude plugin list --json`, and `codex --help`.
The installed Codex CLI does not expose a `codex hooks` management subcommand;
its hook review UI is `/hooks` inside a session.
