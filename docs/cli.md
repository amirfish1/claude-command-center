# The ccc CLI

[Back to the README](../README.md) · [Orchestration API](orchestration.md)

Once CCC is running, `ccc` lets you check sessions, start work, and send replies
without opening the dashboard.

```bash
ccc sessions              # live census: state, age, engine, repo, name
ccc list-sessions --since 5h   # only sessions active in the window
ccc sessions --json       # machine-readable census

ccc models                # every engine's models, effort ladders, cost,
                          # release dates where the id carries one
ccc quota                 # weekly quota left per engine + when it resets
ccc doctor                # per-engine CLI/auth/BYOK health, dry-run only
ccc vault exec --env VAR=<name> -- <cmd>   # run with a Vault secret, redacted from output
ccc spawn "fix the flaky login test" --engine claude --model opus-5
ccc spawn "drain the queue" --report-to <your-session-id>   # reports back

ccc send queue-drain "also update the changelog"   # fire-and-forget
ccc send <session-id> "stop, wrong branch" --steer # interrupt the turn
ccc ask queue-drain "what's your status?"          # block for the reply

ccc spawn --continue-from <old session> "keep going"       # new session, not a cold resume
ccc send <old session> "keep going" --new-if-large-and-stale # spawns a continuation only when warranted
```

## Find the running server

`ccc` reads `~/.claude/command-center/port.txt`, which holds a full base URL,
not a bare port. Override it with `--server` or `$CCC_SERVER`.

The installer links the repo-root script to `~/.local/bin/ccc`. From a checkout
you can run `./ccc`. With no known subcommand, it launches through `run.sh`,
the same behavior as the Homebrew launcher.

## Choose a session or model

`ccc sessions` shows live sessions, including spawned children under their
parent and sessions without a dashboard conversation row. `ccc send` and
`ccc ask` accept a session id, a unique id prefix, or a unique fragment of
its displayed name.

`ccc spawn` posts to `/api/sessions/spawn` using your current directory as the
repo context. Override it with `--cwd`. Run `ccc models` before choosing a
model id; it lists the live catalog and each engine's reasoning-effort ladder.

`ccc doctor` checks CLI presence, auth where a probe exists, BYOK profiles, and
whether a CLI binary resolves. It does not launch an agent or spend tokens.

For provider keys and other secrets, see [BYOK and Vault](byok-and-vault.md).
For local memory and recent-work tools, see [the release history](../CHANGELOG.md).
