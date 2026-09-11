# Kimi Code CLI — integration reference

Condensed from a source read of `MoonshotAI/kimi-code` (MIT, TypeScript monorepo).
Feeds the KIMI-FIXES queue. Citations are `file:line` inside that repo.

Checkout: **`~/dev/vendors/kimi-code`**. The original July 2026 read used a
shallow clone at `/tmp/kimi-code-ref/repo`, which macOS reaped; re-cloned to a
durable path 2026-09-01. Note `packages/acp-adapter` has since been renamed
`packages/acp-server` — paths below that say `acp-adapter` are pre-rename.

Repo layout that matters to CCC:

- ACP adapter: `packages/acp-adapter` → now `packages/acp-server` (event mapping
  in `src/events-map.ts`, dispatch in `src/session.ts`).
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

CCC-934: `:compact` here is an OUT-OF-BAND path for compacting a session with
no live ACP connection — it is NOT the only way to compact. Within an active
ACP session, "/compact" is one of the adapter's own BUILTIN slash commands
(`packages/acp-adapter/src/slash.ts` + `builtin-commands.ts`) — intercepted
before the model ever sees it, running `session.compact()` directly and
reporting via `session/update` (`compaction.started/completed/blocked`). CCC
sends it as plain `session/prompt` text, same as Claude's "/clear" (CCC-935).

Envelope `{ code, msg, data, request_id }`. Sessions CRUD + `:fork/:compact/:undo/
:archive/:restore`; `GET /sessions/{id}/status` → model, thinking_level,
permission, plan_mode, **context_tokens/max_context_tokens**; prompts POST +
`:steer` + `{pid}:abort` (interrupt); approvals GET pending + POST decision
(structured `tool_input_display`); `GET|PATCH /config`; `/models`, `/providers`,
`/workspaces`, `/healthz`. Streaming is WS-only (`event.*` frames incl.
`event.assistant.delta`, `event.session.usage_updated`, `event.approval.requested`).

## Steer: why ACP cannot, and what would make it possible

Verified against the checkout 2026-09-01.

- Steering is a **core** capability, not a kap-server one. The live engine is
  `agent-core-v2` (`agent-core` is the legacy v1 engine): `submitSteer()`
  (`packages/agent-core-v2/src/agent/prompt/promptService.ts:356`) enqueues the
  message and immediately steers it into the running turn via `steer()` (:394),
  falling back to an ordinary turn when nothing is active — i.e. exactly the
  "deliver this, steering if busy" primitive, in one call.
- The klient facade maps `agent.steer(input)` straight onto it
  (`packages/klient/src/core/facade/agent.ts:113` →
  `agentPromptService.submitSteer`), next to `agent.prompt(input)` →
  `agentPromptService.submit`.
- kap-server exposes it as `POST /sessions/{session_id}/prompts::steer`
  (`packages/kap-server/src/routes/prompts.ts:375`), "Steer queued prompts into
  the active turn" — this is what Ctrl/Cmd+S in the web UI calls.
- ACP does **not**: `rg -i steer packages/acp-server/src` returns nothing, and
  the bundled `@agentclientprotocol/sdk@0.23.0` `AGENT_METHODS` table has no
  steer/interject method at all.
- Instead acp-server rejects a concurrent prompt outright —
  `assertNoActiveTurn()` (`packages/acp-server/src/session.ts:631`) throws
  `TURN_AGENT_BUSY_CODE` → JSON-RPC -32600. Its comment gives the reason: the
  adapter tracks a single in-flight "driver" per session, so a second prompt
  would overwrite it and leave both turns' events unattributed.
- **The capability is already in reach of that file.** acp-server drives the
  engine through the klient facade (`this.agent = this.session.agent('main')`,
  `session.ts:267`), and that facade has `steer(input)`
  (`packages/klient/src/core/facade/agent.ts:64`). Nothing calls it — there is
  simply no ACP method to carry it. ACP has `unstable_*` methods and legacy
  `extMethod`/`extNotification` fallbacks (`packages/acp-server/src/server.ts:266,698`),
  so an extension method is the natural home if this is ever raised upstream.

**CCC already drives the live turn.** The `kimi acp` subprocess CCC spawns owns
the engine core and is the process running the turn, so the steer capability is
live inside it — `this.agent.steer()` is one call away in `session.ts`. Nothing
about process topology blocks us; the stdio surface we speak simply has no verb
for it. Two ways to close that, in preference order:

1. **Upstream:** ask MoonshotAI to expose it as `unstable_steer` (or via the
   `extMethod` fallback). The engine-side diff is one word: `driveTurn` calls
   `this.agent.prompt({ input })` (`session.ts:647`); the steering variant is
   `this.agent.steer({ input })` — same facade, same `PromptLaunchResult`, same
   settlement path, and `submitSteer` already degrades to a normal turn when
   idle. Fixes every ACP client, not just CCC.
2. **Local patched build:** MIT source, ordinary pnpm monorepo (`pnpm build`,
   Node >= 24.15). Either add the same `unstable_steer` method, or call
   `serveKlientIpc({ scope: core, socketPath })` inside `runAcpServer` to expose
   the *whole* klient facade over a unix socket — that helper already exists and
   is tested (`packages/klient/test/ipc.test.ts`) but is never called by shipped
   code. Prefer the `unstable_steer` shape: it is what you would upstream
   anyway, so the fork stays a one-commit patch that rebases cleanly.

**Why the kap-server daemon is NOT the way in:** acp-server builds
its klient over the *in-memory* transport (`createKlient` from
`@moonshot-ai/klient/memory`, `packages/acp-server/src/start.ts:42,128`), so a
`kimi acp` subprocess owns its own engine core. A separate `kimi web` /
kap-server daemon is a different process with a different core, and
`PromptService`'s `_active` / `_queued` are plain in-memory `Map`s
(`promptService.ts:244,246`). Both processes can *load* the same persisted
session, but only the one driving the live turn can steer it. Using the daemon
steer would mean moving CCC's Kimi transport off ACP entirely, not adding a call
alongside it.

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

## Stage 0 probe (2026-09-01, measured against a live daemon)

Started `~/.kimi-code/bin/kimi web --no-open --port 58627` against the default
`~/.kimi-code` home — the same home CCC's `kimi acp` subprocess uses
(`ccc_server/acp.py:50-58`, `home_env: KIMI_CODE_HOME`). Meta reports
`server_version 0.39.1`, `backend "v2"`, `dangerous_bypass_auth false`. The
bearer token is printed to the startup log and also lives at
`~/.kimi-code/server.token`. `/openapi.json` is served live (401 without the
bearer, 200 with it): OpenAPI 3.0.3, 100 paths, schemas fully inlined
(`components.schemas` is empty), so a client can be generated straight from the
running binary.

**Session store is shared, and the daemon reads ACP-created sessions.**
Both surfaces index off `~/.kimi-code/session_index.jsonl` and store transcripts
under `~/.kimi-code/sessions/<workspace_key>/<session_id>/`. `GET
/api/v1/sessions` returned 340 sessions whose newest entries matched the on-disk
dirs exactly, in order. `session_f0b5692f-…` — which CCC drove over ACP, and for
which CCC holds its own normalized copy at
`~/.claude/command-center/acp/kimi/session_f0b5692f-….jsonl` — resolved on the
daemon for `GET {sid}`, `{sid}/status`, `{sid}/messages` and `{sid}/prompts`
(all 200). So the two transports **can coexist during migration**; this is not a
hard cutover. Note `{sid}/events` is 404 — the transcript routes are
`{sid}/transcript`, `/transcript/ops`, `/transcript/plan`,
`/transcript/user-messages`.

The caveat above still stands and is the real boundary: the *store* is shared,
the *live turn* is not. Whichever process holds the core in memory owns
`_active`/`_queued`, so the daemon can read an ACP session but cannot steer a
turn the ACP subprocess is currently driving.

**The steer API is queue-first, which matches CCC's tray one-to-one.** The
served path is single-colon `…/prompts:steer` (the `::` in the route source is
declaration syntax). Two steps, not one:

- `POST /api/v1/sessions/{sid}/prompts` — required `content` (array); optional
  `prompt_id`, `model`, `thinking`, `permission_mode`, `plan_mode`,
  `swarm_mode`, `agent_id`, `profile`, `skills`, `disabled_tools`,
  `goal_objective`, `goal_control`, `metadata`.
- `POST /api/v1/sessions/{sid}/prompts:steer` — required `prompt_ids` (array).
  It promotes *already-queued* prompts into the running turn.
- `GET /api/v1/sessions/{sid}/prompts` — returns `{active, queued}`, the durable
  server-side queue CCC currently has to reconstruct client-side.

That is exactly CCC's queued-steer tray: messages sit queued, the user clicks
Steer to promote one, and "Steer all" is a single call with several
`prompt_ids`. The queue CCC renders today would become a read of `{active,
queued}` rather than a local model, which is also what would kill the
optimistic-move bounce at its source.

## Stage 1 spike (2026-09-01): what the daemon actually streams

Built `ccc_server/kap.py` and drove a session end-to-end against the live
0.39.1 daemon: create → prompt → stream → map → CCC events. Four findings, each
of which would have been a wrong assumption baked into the transport.

**1. The agent event union is not what you subscribe to.** `events-zod.ts`
defines 58 agent event types (`assistant.delta`, `turn.ended`, …) and it is
tempting to map them. Subscribing with `subscribe_v2` delivers **none** of
them. What arrives is `transcript.ops` — a seven-op document protocol that
Kimi's own UI renders from:

| op | carries |
|---|---|
| `prompt.upsert` | prompt + status (`running` → `completed`) — the queue state |
| `turn.upsert` | turn `t0`, state `running` → `completed` |
| `step.upsert` | a step inside a turn (`t0.1`) |
| `frame.upsert` | a content frame, `kind` thinking/text, id `t0.1.f1` |
| `append` | text into a frame **at a byte offset** — the streaming delta |
| `marker.upsert` | undo markers |
| `meta.merge` | activity, agent phase, usage, contextTokens |

This is the better surface. Frames are addressable, appends carry offsets so a
gap can be *reconciled* rather than replayed blind, and `meta.merge` streams
usage and `contextTokens` live — **integration caveat #1 above (no usage over
ACP, needs wire.jsonl) is resolved for free.** Turn boundaries also arrive as
`event.session.work_changed` with `busy`.

**2. WS auth rides the subprotocol.** Not a header:
`Sec-WebSocket-Protocol: kimi-code.bearer.<token>`
(`transport/ws/bearerProtocol.ts`). A bearer header alone fails the upgrade.

**3. The heartbeat is an application frame, not the RFC 6455 ping opcode.**
The daemon sends `{"type":"ping","payload":{"nonce":…}}` and expects
`{"type":"pong","payload":{"nonce":…}}`. `wsConnectionV1.onHeartbeat` closes
the socket after two missed replies, and *any* inbound frame resets the timer.
A client that answers only the protocol-level ping is dropped ~20s in, mid-turn,
with no error — the stream just stops. This cost a full debugging cycle.

**4. `POST /sessions` advertises an `agent_config` it does not apply.** A
session created without a model accepts a prompt, stores it, and then never
runs a turn: status reports `model: null`, `busy` stays false, and nothing on
the wire explains it. The effective route is `POST /sessions/{id}/profile`, so
creating a usable session is two calls.

Two smaller ones: the event type is on the frame envelope (`session_event` is
the AsyncAPI *message* name, never sent literally); and the terminal
`turn.upsert` is sent twice, so a mapper must emit once. A `frame.upsert`
carrying text at turn end is a reconciliation of the appends — concatenating it
duplicates the whole message.

`tests/fixtures/kap_turn_frames.jsonl` is a real captured turn; the test
replays it and asserts it reduces to one assistant message plus a result, so a
protocol change fails loudly rather than silently producing empty turns.

**Not yet done:** tool-call frames were not exercised (the smoke turn used no
tools), so `frame.kind` for tool frames is passed through rather than mapped.
Daemon supervision, per-session routing, and adoption of ACP-created sessions
are the rest of Stage 1; approvals and steer are Stage 2.
