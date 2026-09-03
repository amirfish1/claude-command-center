# Runtime bindings: Kimi TUI vs `kimi web` vs CCC

Why a Kimi session sometimes loses Bash/Read/Write/Edit/Glob/Grep — and who
does what to cause (and cure) it. This documents the behavior verified live on
2026-09-03 against kap-server 0.39.1/0.40.1 plus the vendor source
(`kimi-code` monorepo, referenced by package-relative paths).

## The mechanism in one paragraph

Every Kimi session carries a **durable runtime binding**: a
`runtime.set_binding` record in the session's wire log
(`~/.kimi-code/sessions/<ws>/<sid>/agents/main/wire.jsonl`). Any process that
resumes the session replays that record and activates tools against the bound
runtime's capabilities.

- `local` — the normal runtime. Capabilities `fs` + `process`, which gate
  exactly **Bash, Read, Write, Edit, Glob, Grep, Agent, ReadMediaFile**
  (`packages/agent-core-v2/src/agent/toolActivation/toolActivationService.ts`,
  the `runtimeAllows` gate).
- `acp:<sid>` — a **virtual runtime that exists only inside the ACP
  subprocess that created it** (`packages/acp-server/src/start.ts`,
  `bindSessionRuntime`). In any other process it resolves to zero
  capabilities: the gate strips the eight tools above, leaving only
  coordination tools (AgentSwarm, TodoList, Cron*, goals, MCP). That is why a
  broken session "opens every turn in SWARM mode with no bash" — AgentSwarm is
  literally the most capable tool it has left.

Two layers matter, and confusing them is the root of most surprises:

1. **The wire record** — durable, shared, last writer wins. Read once, at
   resume time.
2. **Each process's in-memory copy** — set at resume, refreshed only by an
   in-process switch or a fresh resume. A warm process does **not** see
   another process's wire appends.

## Who writes the binding

| Component | Writes `acp:<sid>` | Writes `local` | Otherwise |
|---|---|---|---|
| Kimi TUI (`kimi`) | never | never | honors the persisted binding on resume |
| `kimi web` daemon | never | never | serves the persisted binding as-is |
| CCC over ACP | **every attach** | never | — |
| CCC over kap | never | **before every driven prompt** | — |

The asymmetry is the whole story: the ACP side rebinds on every attach (so
flip-flopping between transports is safe *by design*), while the TUI and the
daemon trust whatever the wire log says. The kap side simply never rebound —
fixed in CCC by `3083d496` (`kap_bind_runtime_local` in `ccc_server/kap.py`).

## When CCC touches the binding

**Viewing a conversation in the CCC UI writes nothing.** History replay reads
CCC's own transcript store and the Kimi wire log from disk. There is also no
background watcher that attaches sessions.

The `acp:<sid>` write happens lazily, on the first action that needs the agent
process:

| CCC action | Path | Binding write |
|---|---|---|
| Open/view a conversation | disk read only | none |
| Spawn a new Kimi session | ACP `session/new` | `acp:<sid>` (from birth) |
| Send a prompt (kap flag **off**) | `_acp_prompt` → attach | `acp:<sid>` |
| Press **Esc** | `_acp_cancel` attaches first | `acp:<sid>` |
| Change the model picker | `_acp_set_config` attaches first | `acp:<sid>` |
| WatchTower-injected message | `watchtower_msg.py` attach | `acp:<sid>` |
| Send a prompt (kap flag **on**, daemon owns the session) | `kap_prompt` → `kap_bind_runtime_local` | `local`, verified |

Note the trap: even *Esc* poisons the binding. A user who opens a session in
CCC and hits Esc once has durably rebound it for the TUI and `kimi web`.

## The permutation matrix

"Last driven by" = the transport that most recently ran a turn (or attached).
"Opened next in" = where you use the session afterwards. A session is only
poisoned for a consumer once that consumer *cold-resumes* it after the poison
record lands — warm in-memory copies keep their stale binding.

| Last driven by | Open next in TUI | Open next in `kimi web` | Open next in CCC (flag off) | Open next in CCC (flag on) |
|---|---|---|---|---|
| **TUI** | fine | fine | works in CCC; poisons TUI/web on next resume | kap rebinds `local` first; fine everywhere |
| **`kimi web`** | fine | fine | works in CCC; poisons TUI/web on next resume | fine |
| **CCC over ACP** | **broken**: no Bash/Read/…, opens with AgentSwarm | **broken** until something rebinds | works (every attach rebinds to a fresh `acp:<sid>`) | self-heals on the next prompt |
| **CCC over kap** | fine | fine | works in CCC; poisons TUI/web on next resume | fine |

Who wins:

- **The binding**: last writer wins, durably. ACP attach always writes
  `acp:<sid>`; a kap-driven prompt always writes `local` first. Nothing else
  writes.
- **The live turn**: whichever process holds the engine core in memory owns
  `_active`/`_queued`. A second process that resumes the same session gets its
  own warm copy — it cannot see or steer the first process's live turn.
- **Reads**: everyone replays the same wire log, so history is eventually
  consistent — but each reader only picks up other processes' appends on its
  next resume.

## Concurrent opens

- **TUI + Kimi web UI (same daemon)**: clean. The browser UI talks to the same
  daemon process; one owner, one queue. This is the intended pairing.
- **TUI + separate `kimi web` daemon**: two warm copies of the session (each
  process resumed its own). Interleaved wire records, no shared live-turn
  state, doubled token usage if both run turns. Nothing locks the session.
- **CCC + TUI, kap flag off**: CCC's ACP subprocess is a third warm copy, and
  every CCC action rewrites the binding to `acp:<sid>`, poisoning the TUI's
  *next* resume. The TUI's current open session keeps working until it
  restarts — then loses its tools.
- **CCC + TUI, kap flag on**: depends on which daemon `kap_discover()` picks.
  The TUI (0.40.1) registers itself as a kap server; if its heartbeat is
  newest, CCC's prompt lands **inside the TUI process** — single owner,
  coherent queue, arguably the best concurrent case. If the dedicated `kimi
  web` daemon wins, it resumes its own copy alongside the TUI's — two warm
  copies again.

## Pitfalls

1. **`acp:<sid>` is sticky poison.** Durable, written on every ACP attach,
   honored by TUI and daemon, healed by neither.
2. **Warm staleness cuts both ways.** A daemon holding a warm `local` copy can
   report "all good" while the wire says `acp:<sid>` — and vice versa. Binding
   checks are only truthful on a cold resume or an in-process switch.
3. **Cold-session POST `/runtime` silently no-ops** (kimi-code bug): returns
   `code:0`, persists nothing — the switch races the wire replay that
   re-asserts the old binding. CCC mitigates with POST → GET-verify → retry
   (the POST itself resumes the session, so the retry lands warm). Never trust
   the POST's status code alone.
4. **`kap_discover()` picks the newest heartbeat**, which can be the user's
   interactive TUI rather than a dedicated `kimi web` daemon. The instance
   record carries no field distinguishing the two.
5. **Two warm copies double-spend**: queued prompts, turns, and tokens are
   per-process; history interleaves in one wire log.
6. **Diagnostics**: `GET /api/v1/sessions/{sid}/runtime` shows the binding;
   `GET /api/v1/tools?session_id=<sid>` shows the consequence (degraded: ~19
   tools, no Bash; healthy: ~51). Both need the bearer token at
   `~/.kimi-code/server.token`.

## What would make this smooth everywhere

Done:

- **CCC rebinds to `local` before every kap-driven prompt** (`3083d496`),
  verified end-to-end: 19 → 51 tools, daemon persisted the binding, the turn
  used Bash.
- **CCC heals on view** (`kap_heal_binding_on_view`, wired into the
  conversation-open endpoint): opening a Kimi conversation in CCC rebinds a
  poisoned session to `local` in the background — threaded, throttled per
  session, skipped while a turn is running. Sessions you only ever *view* no
  longer stay broken for the TUI and `kimi web`.
- **The System status panel shows the kap daemon** (read-only row: state,
  pid, version, port, heartbeat age — CCC adopts this daemon, it never
  supervises it, so no Restart button). It flags the multi-daemon ambiguity
  and only raises the attention banner when kap routing is on.
- **The transport pill warns on a non-`local` binding**: a kap-routed session
  the daemon still sees as `acp:<sid>` gets an amber `runtime: acp` chip next
  to the KAP pill.
- **`CCC_KIMI_KAP_SERVER=<port-or-server-id>` pins the routing target.**
  Auto-preferring the dedicated daemon turned out to be impossible today: the
  instance record carries no kind field, the SEA binary rewrites its argv (so
  `ps` can't tell `kimi web` from the TUI), and `/api/v1/meta` is identical
  between them. The pin is the deterministic answer; the status row shows
  which daemon prompts would land on.

Still open, roughly cheapest-first:

1. **Upstream (kimi-code): fix the cold-session `/runtime` POST** so it
   persists instead of answering `code:0` into the void; and **have the TUI
   rebind to `local` on resume**, symmetric to the ACP attach rebind — then
   every consumer self-heals and the poison stops being sticky.
2. **Upstream: a per-session live-owner lock.** Refusing (or cleanly handing
   off) a second warm copy would kill the interleaved-wire/double-spend class
   of problems outright.
3. **Upstream: a `kind` field in the instance record** (`web` vs `tui`) so
   `kap_discover()` can prefer a dedicated daemon without a manual pin.

## Two questions that come up

**Why do `kimi web` + the TUI get along, but CCC UI + TUI didn't?** Because
Kimi's browser UI is a *thin client*: it talks REST+WS to a kap-server process
that owns every session's engine core. The TUI (0.40.1) embeds that same
server — one process, one owner per session, both UIs are views. CCC's default
transport is different in kind: it spawns its **own** `kimi acp` subprocess —
a second engine with its own warm copy of the session and its own binding
writes. Two engines sharing one session store is where the poisoning and the
interleaving come from. With kap routing on, CCC becomes a thin client of the
daemon too, and CCC + TUI get along exactly as well as the web UI + TUI —
provided discovery lands on the right daemon (hence the pin).

**Why does CCC keep the ACP transport at all?** Three reasons. (1) ACP is a
published spec with a pinned SDK (`@agentclientprotocol/sdk`); kap is a
private product API with no compatibility promise — the pinned
`openapi.json`/`asyncapi.json` snapshots in this directory exist precisely
because it can move under us. (2) ACP needs nothing but the `kimi` binary
every user already has; kap requires a running `kimi web` daemon CCC cannot
assume or start. (3) The kap transport is still a Stage-1 spike: approvals,
adoption of ACP-created sessions, and daemon supervision are deliberately not
there yet. So ACP is the compatibility floor, `kap_routes()` fails open into
it, and every downgrade is supposed to be visible (the transport pill, the
status row) rather than silent.
