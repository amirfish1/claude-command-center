# Kimi Code CLI — integration reference

Condensed from a source read of `MoonshotAI/kimi-code` (MIT, TypeScript monorepo;
shallow clone at /tmp/kimi-code-ref/repo, July 2026). Feeds the KIMI-FIXES queue.
Citations are `file:line` inside that repo.

Repo layout that matters to CCC:

- ACP adapter: `packages/acp-adapter` (event mapping in `src/events-map.ts`,
  dispatch in `src/session.ts`).
- wire.jsonl writer: `packages/agent-core/src/agent/records/persistence.ts`.
- Daemon + web UI: `packages/kap-server` + `apps/kimi-web` (Vue 3).
  `kimi server` is **deprecated** → `kimi web` (`apps/kimi-code/src/cli/sub/web/deprecated-server.ts:13-16`).

## ACP `session/update` vocabulary (emitted by acp-adapter)

Every notification is `{ sessionId, update: { sessionUpdate: <kind>, … } }`.

| kind | payload | notes |
|---|---|---|
| `agent_message_chunk` | `{ content: { type:'text', text } }` | token-level, from `assistant.delta` |
| `agent_thought_chunk` | `{ content: { type:'text', text } }` | token-level |
| `tool_call` | `{ toolCallId, title, kind, status:'pending'\|'in_progress', rawInput?, content[] }` | lazy create (pending, no rawInput) on first args delta, else create at started with rawInput |
| `tool_call_update` | `{ toolCallId, title?, kind?, status?, content?, rawInput?, rawOutput? }` | content has REPLACE semantics; terminal update carries `rawOutput` (`events-map.ts:379-393`) |
| `plan` | `{ entries: [{ content, priority:'medium', status }] }` | whole-plan replace; status pending/in_progress/completed; emitted only when a TodoList tool call (`display.kind === 'todo_list'`) starts (`session.ts:1079-1088`). No clear-plan signal |
| `available_commands_update` | `{ availableCommands: [...] }` | pushed once after new/load/resume |
| `config_option_update` | `{ configOptions }` | after setModel/setMode/setThinking |
| `user_message_chunk` | `{ content: { type:'text', text } }` | **replay only** (`session.ts:592-603`) |
| `usage_update` | — | **NOT EMITTED** — usage lives in wire.jsonl `usage.record` / daemon REST+WS only |
| `current_mode_update` | — | **NOT EMITTED** — superseded by `config_option_update` |

Tool-call facts: ids are `${turnId}:${rawToolCallId}` prefixed (strip prefix to
correlate with wire.jsonl/SDK). `kind` inferred from tool name (read/edit/execute/
fetch/think/other, `events-map.ts:106-126`). Terminal statuses: `completed` |
`failed`. rawInput arrives at started (never on the lazy create); rawOutput only
on the terminal update. Content shapes: `{type:'content', content:{type:'text',text}}`
and `{type:'diff', path, oldText, newText}`. No `locations`.

## Permission requests

`session/request_permission` params: `{ sessionId, toolCall: { toolCallId, title: toolName, content }, options }`.
Canonical options: `approve_once`/`approve_always`/`reject` (kinds
`allow_once`/`allow_always`/`reject_once`; legacy `approve`/`approve_for_session`
still accepted inbound; plan-review variants `plan_opt_<i>`, `plan_approve`,
`plan_revise`, `plan_reject_and_exit`) — `approval.ts:41-118`.

**The structured bash command is dropped over ACP**: SDK has
`display: { kind:'command', command, cwd?, description? }` but
`displayBlockToAcpContent` returns null for it (`convert.ts:226-255`); the client
only sees `"Requesting approval to ${action}"` text. The daemon REST approvals
endpoint (`POST /api/v1/sessions/{sid}/approvals/{id}`) carries
`tool_input_display` verbatim — the only structured-command channel today.
(Upstream-PR candidate.)

## Config options (session/new, set_config_option, load/resume)

`configOptions` = up to 3 selects: `model`, `thinking` (binary on/off; present
only when the model is thinking-capable), `mode` (default/plan/auto/yolo)
(`config-options.ts:62-203`).

- `session/set_config_option` returns a fresh `configOptions` snapshot AND emits
  `config_option_update` (`server.ts:672-714`).
- **Persistence**: per-session, appended to that agent's wire.jsonl as
  `config.update` — durable across resume, session-scoped. config.toml untouched.
- **Mode is NOT persisted** — always resets to `default` on load/resume
  (`server.ts:353-355`).
- `session/load` and `session/resume` BOTH return `configOptions`
  (`server.ts:363-378, 399-408`); only `load` replays history (`replayHistory()`
  → batch of user/agent/thought/tool updates, `session.ts:526-701`).
- Thinking effort: wire.jsonl `config.update.thinkingEffort` is an open string
  (`'off' | 'on' | effort names`); ACP surface is binary on/off, `on` maps to the
  model's default effort. Daemon `GET /status` exposes the raw `thinking_level`.

## wire.jsonl event vocabulary (`packages/agent-core/src/agent/records/types.ts:36-200`)

`metadata`, `forked`, `turn.prompt` `{input, origin}`, `turn.steer`, `turn.cancel`,
`config.update` `{cwd?, modelAlias?, profileName?, thinkingEffort?, systemPrompt?}`,
`permission.set_mode` `{mode: manual|yolo|auto}`, `permission.record_approval_result`,
`full_compaction.*`, `micro_compaction.apply`, `plan_mode.*`, `swarm_mode.*`,
`tools.*`, `usage.record` `{model, usage, usageScope?}`,
`context.append_message`, `context.append_loop_event` (event: `step.begin` /
`step.end {usage, finishReason, latency}` / `content.part {TextPart|ThinkPart}` /
`tool.call {toolCallId, name, args, display?}` / `tool.result`),
`context.update_token_count {tokenCount}`, `context.clear/apply_compaction/undo`,
`goal.create/update/clear`, observability-only `llm.tools_snapshot`, `llm.request`,
`mcp.tools_discovered`.

Deltas (`text.delta`, `thinking.delta`, `tool.call.delta`, `tool.progress`,
`turn.interrupted`, `step.retrying`) are **live-only, never in wire.jsonl**.

External reader: `reduceWireRecords()` — pure wire→full-transcript reducer at
`packages/agent-core/src/services/message/transcript.ts:106`.

## Web UI (apps/kimi-web) — parity targets for CCC

- Transport: WebSocket `/api/v1/ws` + REST `/api/v1/*` (no SSE). Frames
  `{type, seq, session_id, timestamp, payload, volatile?, offset?}`; subscribe
  handshake with cursor resync (`ws.ts`).
- Fold chain: `agentEventProjector.ts:527` (raw events → UI frames, with
  offset-gap detection) → `mappers.ts` → `eventReducer.ts` →
  `messagesToTurns.ts` (assistant runs + tool results → ChatTurn groups).
- Thinking: `ThinkingBlock.vue` — 5-line live window, folds to teaser, full text
  in side panel.
- Tool groups: `ToolGroup.vue` — header `{count} tool calls · {running|error|done}`,
  auto-merge consecutive calls.
- Plan: `TodoCard.vue` — read-only rows, done → strikethrough.
- Context ring: `ContextRing.vue` in `Composer.vue:1100`;
  `pct = ceil(used/max*100)` from `GET /sessions/{id}/status`
  (`context_tokens`/`max_context_tokens`) + live status events.
- Steer: Ctrl/Cmd+S (`Composer.vue:494-495`) → `POST /sessions/{id}/prompts` then
  `:steer`.
- Input expand: 70vh multi-line mode toggle (`Composer.vue:120-182`).
- Settings: dialog with left-rail tabs (general/agent/account/advanced/archived),
  `SettingsDialog.vue`.

## Daemon REST (kap-server, `/api/v1`) — what ACP cannot do

Envelope `{ code, msg, data, request_id }`. Sessions CRUD + `:fork/:compact/:undo/
:archive/:restore`; `GET /sessions/{id}/status` → model, thinking_level,
permission, plan_mode, **context_tokens/max_context_tokens**; prompts POST +
`:steer` + `{pid}:abort` (interrupt); approvals GET pending + POST decision
(structured `tool_input_display`); `GET|PATCH /config`; `/models`, `/providers`,
`/workspaces`, `/healthz`. Streaming is WS-only (`event.*` frames incl.
`event.assistant.delta`, `event.session.usage_updated`, `event.approval.requested`).

## Integration caveats (from the study)

1. No usage streaming over ACP — context % needs wire.jsonl
   (`context.update_token_count` / `usage.record`) or the daemon `/status`.
2. ACP permission requests lose the structured bash command — daemon approvals
   keep it; CCC parses the `"Requesting approval to …"` text today.
3. toolCall ids are `turnId:rawId` prefixed — strip when correlating with
   wire.jsonl.
4. `plan` updates come only from TodoList tool calls, at tool-call start.
5. Mode resets to default on load/resume; model/thinking persist per-session in
   wire.jsonl.
