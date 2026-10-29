# Orchestration skill and API

[Back to the README](../README.md) · [CLI](cli.md) · [Skill pack](../skills/README.md)

The `ccc-orchestration` skill lets one agent spawn a sibling, send input, or
wait for a reply over local HTTP. Use persistent peer sessions for ongoing
work; use your engine's built-in subtask tool for short internal subtasks.

The server installs bundled skills only after [approval](agent-config-consent.md).
`CCC_SKIP_SKILL_INSTALL=1` disables skill installation. The running server's
full base URL is in `~/.claude/command-center/port.txt`.

## Spawn a session

`POST /api/sessions/spawn` requires `repo_path` or `cwd`. Optional fields are
`engine`, `model`, `reasoning_effort`, `report_to`, and `parent_session_id`.

Engines include `claude`, `codex`, `cursor`, `antigravity`, `kilo`, `kimi`,
`opencode`, and `devin`. Legacy `gemini` maps to Antigravity. Omitted engine,
model, and effort use the dashboard's server-side defaults.

Read the legal models and effort ladders from `GET /api/engines/models`.
Claude's ladder runs from `low` through `max`; Codex stops at `xhigh`; Kimi
uses its own declared ladder. Engines without effort controls have an empty
ladder. An unrecognized model returns 400 on Codex. Unsupported effort values
are dropped, so a successful spawn does not prove that your effort was applied.

A successful response includes `spawn_id`, `engine`, `repo_path`, `cwd`, an
optional `parent_session_id`, and `session_id` once the engine has emitted it.
If `session_id_pending` is true, poll `/api/sessions/spawned`. Spawned rows
also report the resolved `model` and `reasoning_effort`.

Passing `report_to` or `parent_session_id` links the child under the dispatcher
in Current Sessions. Clients that may retry a spawn or `/api/inject-input`
should send a stable `idempotency_key` for that user action. CCC returns the
original work record instead of dispatching a duplicate engine turn.

## Read and ask

```bash
CCC_URL="$(cat ~/.claude/command-center/port.txt)"
REPO_PATH="$(pwd -P)"
curl -s "$CCC_URL/api/sessions?repo_path=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$REPO_PATH")"

curl -s -X POST "$CCC_URL/api/ask" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "<uuid>", "text": "What is 2+2?", "timeout_ms": 30000}'
# -> {"ok": true, "text": "4", "cost_usd": ..., "duration_ms": ..., "num_turns": 1}
```

`POST /api/inject-input` sends a message without waiting for an answer.
`POST /api/ask` waits for a reply. The CLI equivalents are `ccc send` and
`ccc ask`. URL-encode the repo path in query strings.

## Read provider usage

`GET /api/usage/current` returns consolidated local usage: Claude plan
windows, Codex rate-limit windows, Kimi windows, pace projections, calibration
metadata, recent reset events, and `fetched_at`. Fields can be null when a
provider has not emitted usage; callers should handle that without guessing.

For worker recovery, idempotency, and uncertain work, see
[Architecture](architecture.md). For other agent skill packs and app
integration recipes, see [Features](features.md#agents-working-together).
