# Engine support

[Back to the README](../README.md#engine-support) · [CLI and model catalog](cli.md)

CCC began with Claude Code. Eight engines now support spawns from the
dashboard, but follow-up, transcript detail, and model controls vary.

| Engine | Spawn | Resume / follow-up | Transcript reading | Model and effort controls |
|---|---|---|---|---|
| Claude Code | Headless from UI | Terminal inject and headless resume | First-class JSONL from `~/.claude/projects/` | Per-session model, 1M-context toggle; `low`, `medium`, `high`, `xhigh`, `max` |
| Codex | Headless from UI | Terminal inject and headless resume | JSONL; connected app-server streams tools, diffs, images, and approvals into the shared view | Per-session model, default `CCC_CODEX_MODEL`; `low`, `medium`, `high`, `xhigh`, no `max` |
| Cursor | `cursor-agent` | `cursor-agent --resume` | Partial agent transcripts from `~/.cursor/projects/` | Model only; default `CCC_CURSOR_MODEL` |
| Antigravity | `agy` print mode | AGY CLI or running app language-server RPC | JSONL from `~/.gemini/antigravity/brain/` | Model detected from transcript metadata; no effort ladder |
| Kilo Code | `kilo run --auto` | No resume; fire-and-forget | Kilo SQLite store at `~/.local/share/kilo/kilo.db`, including external sessions | Model only; default `CCC_KILO_MODEL` |
| Kimi Code | ACP via `kimi acp`, token-level streaming | Live ACP steering and inline permission answers; TUI attach | `~/.kimi-code/sessions/`, live list and archive | Model, default `CCC_KIMI_MODEL`; effort from `config.toml` `support_efforts` |
| OpenCode | `opencode run --auto` | `opencode run --session <id> --auto` | External opencode.ai sessions | Model only; default `CCC_OPENCODE_MODEL` |
| Devin | Local CLI `devin -p` | Local CLI resume; cloud sessions stay read-only | Local `devincli-` sessions from SQLite `message_nodes`; cloud `devin-` sessions when `DEVIN_API_KEY` is set | Consult the live catalog for available controls |

## Read-only engines

**GitHub Copilot CLI**, **VS Code Copilot Chat**, and **Grok CLI** sessions
appear with their transcripts, but cannot be spawned or steered from CCC yet.
Devin cloud API sessions are also read-only; local Devin CLI sessions are
spawnable.

## Models and reasoning effort

Where an engine has no effort ladder, CCC hides the control. An API
`reasoning_effort` that the engine does not accept is dropped, not rejected.
Do not infer that an effort was applied just because a spawn succeeded.

`GET /api/engines/models` publishes the current model catalog and legal
`efforts_by_engine`. `ccc models` reads that catalog. Confirm a spawn's resolved
model and effort through `/api/sessions/spawned`.

## Codex conversations

Codex uses the shared transcript pane and composer. A live overlay merges
in-progress turns as they happen. Available features depend on the installed
Codex version and connected host; desktop-owned tasks connect through their
desktop owner when available. See [Codex workspace](codex-workspace.md) for
connection limits, previews, queue ownership, and verification coverage.

## Cursor IDE limits

Cursor CLI sessions can be bookmarked in the IDE sidebar with titles and
timestamps. This is metadata-only integration, not two-way chat sync: the
IDE's proprietary Protobuf Merkle tree in `store.db` is not a safe writable
chat interface. Use CCC for full CLI history, not the Cursor IDE window.

If an adapter is missing a capability you need, open an issue describing the
engine, CLI version, and workflow.
