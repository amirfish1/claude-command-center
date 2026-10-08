# Configuration and diagnostics

[Back to the README](../README.md) · [Service setup](install.md) · [Security](../SECURITY.md)

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `8090` | HTTP port |
| `CCC_CLAUDE_BIN` | Auto-detected | Absolute Claude Code CLI path when a service cannot see your shell PATH |
| `CCC_CURSOR_BIN` | Auto-detected | `cursor-agent` path |
| `CCC_CURSOR_MODEL` | `auto` | Default Cursor model without a UI/API override |
| `CCC_KILO_BIN` | Auto-detected | Kilo CLI path |
| `CCC_KILO_MODEL` | `kilo/stepfun/step-3.7-flash:free` | Default Kilo model |
| `CCC_OPENCODE_BIN` | Auto-detected | OpenCode CLI path |
| `CCC_OPENCODE_MODEL` | `openrouter/anthropic/claude-sonnet-4.5` | Default OpenCode model |
| `DEVIN_API_KEY` | Unset | Lists Devin cloud sessions read-only; `CCC_DEVIN_API_KEY` is a fallback |
| `CCC_WORKER_SOCKET` | `~/.claude/command-center/worker.sock` | Dashboard-to-worker Unix socket |
| `CCC_WORK_LEDGER` | `~/.claude/command-center/control-plane.sqlite3` | Durable work graph and recovery ledger |
| `CCC_BIND_HOST` | `127.0.0.1` | Bind interface; `0.0.0.0` exposes it on the network, with no authentication |
| `CCC_ALLOWED_ORIGIN` | Empty | Comma-separated extra same-origin POST origins for a trusted network |
| `CCC_TRUST_TAILNET` | Off | Uses `tailscale status --json` at startup to add the local MagicDNS hostname and Tailscale IPs to allowed origins |
| `CCC_TAILSCALE_BIN` | Auto-detected | Tailscale CLI path, including Homebrew and app-bundle discovery |
| `CCC_PHONE_ACCESS_FILE` | `~/.claude/command-center/phone-access.json` | Phone access serve entry and PIN hash |
| `CCC_TITLE_STRIP` | Empty | Comma-separated prefixes stripped from GitHub issue titles |
| `CCC_SPAWN_IDLE_TTL_HOURS` | `3` | Retires persistent headless workers after all activity sources are quiet; `0` disables retirement |
| `CCC_ORG_PATTERNS` | Empty | Issue tags in `Label1:pat1a\|pat1b;Label2:pat2` form; the first match wins |
| `VERCEL_PROJECT` | Unset | Vercel project; leave empty to disable deploy polling |
| `CCC_TELEMETRY_DISABLED` | Unset | `1` disables the anonymous daily open beacon at process level; see [Telemetry](telemetry.md) |

Network settings have no authentication safety net. Read
[SECURITY.md](../SECURITY.md) before exposing the dashboard; every trusted
origin can run commands as you. [Phone access](phone-access.md) is the guided
trusted-network setup.

Idle retirement uses the spawn log, stdin FIFO, and session transcript, and
requires no running tool. Retired sessions remain resumable. This matters
because persistent workers keep stdin open and do not exit just because a
turn finishes.

## Models and effort defaults

Models have a `CCC_*_MODEL` variable per engine. Default reasoning effort does
not: set it in **Settings > Spawn defaults** or `GET` / `POST /api/spawn-defaults`.
The keys are `reasoning_effort` for sessions and `worker_reasoning_effort` for
queue workers. Per-call `reasoning_effort` on `/api/sessions/spawn` overrides
that default. The [live catalog](engine-support.md#models-and-reasoning-effort)
lists valid values.

## Persistent local settings

`CCC_BIND_HOST`, `CCC_ALLOWED_ORIGIN`, and `CCC_TRUST_TAILNET` can also live in
`~/.claude/command-center/network.json`, or be set through Network access in
Settings. Environment variables take precedence.

`run.sh` sources `~/.claude/command-center/config.local.env` before doing
anything else. Use plain `KEY=value` shell lines for other persistent values.
The file is machine-local, outside the checkout. Unlike `launchctl setenv` or
`systemctl --user set-environment`, it survives reboot. Re-run
`--install-service` to record its values in a launchd plist or systemd unit.

## Python stack diagnostics

On macOS and Linux, send `SIGUSR2` to the running server to dump all Python
thread stacks without restarting it or installing a debugger:

```bash
CCC_PORT="$(sed 's/.*://' ~/.claude/command-center/port.txt)"
CCC_PID="$(lsof -nP -iTCP:"$CCC_PORT" -sTCP:LISTEN -t | head -1)"
kill -USR2 "$CCC_PID"
tail -n 200 ~/.claude/command-center/logs/python-stacks.log
```

Each signal appends a traceback to the same log. Use it when the dashboard
is alive but a request seems stuck. The signal is unavailable on Windows.
