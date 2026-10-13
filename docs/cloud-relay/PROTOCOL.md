# CCC Cloud Relay Protocol v1

Status: Normative (2026-07-10). Owner: CCC maintainers.
This is the open wire contract between (a) a local CCC ("device") and the
hosted relay, and (b) a browser and the hosted relay. The hosted service
implementation lives in the ccc-cloud repository; this spec is the contract
both sides build against and is auditable independently of the hosted code.

Design invariants:

- The device only ever makes **outbound** HTTPS requests. No inbound listener.
- The relay is **not a proxy**. Only the capabilities named here exist; the
  device validates and executes every action itself against its own loopback
  API, exactly as a local caller would.
- Every remote datum carries an explicit freshness timestamp.
- Sensitive content (class D, see ccc-cloud data-classification) is transient:
  mailbox rows are deleted on delivery or TTL expiry.
- `v` is present on every envelope; unknown fields are ignored; unknown
  message types or capabilities are rejected with structured errors.

## 1. Identifiers

- `account_id` — opaque UUID, cloud-issued.
- `device_id` — opaque UUID, cloud-issued at pairing.
- `device_secret` — 256-bit urlsafe token; cloud stores SHA-256 only.
- `session_ref` — the device's native session UUID (already opaque).
- `request_id` — UUID per command, cloud-issued.
- `idempotency_key` — equals `request_id` in v1; the device persists executed
  keys for 24 h and returns the recorded result on replay.

## 2. Device authentication

Every device call sends `Authorization: Bearer <device_id>.<device_secret>`.
Errors are structured: `401 {"error":"bad_device_credentials"}`,
`403 {"error":"device_revoked"}`, `403 {"error":"account_deleted"}`,
`426 {"error":"protocol_upgrade_required","min":N}`.
On `device_revoked` / `account_deleted` the client stops its loop, marks cloud
state disabled-pending-repair locally, and never retries with the same
credentials.

### Rotation

`POST /v1/device/rotate` → `{new_secret, rotation_id}`. Old secret stays valid
until first successful use of the new one (`X-CCC-Rotation-Confirm:
<rotation_id>` header on the next call), then is invalidated atomically.
Client persists the new secret to disk *before* confirming.

## 3. Pairing

1. Signed-in browser: `POST /v1/pair/start` → `{pair_code, expires_at}` —
   8-char Crockford-base32 single-use code, 10-min expiry, rate-limited
   5/hour/account. Shown as text + QR (QR encodes
   `ccc-cloud-pair:<code>@<relay_base_url>`).
2. User enters/pastes the code into the local CCC Cloud settings panel (or the
   local UI scans nothing — paste-first; QR is for typing on the phone the
   other direction).
3. Local CCC (user-initiated, localhost-origin-gated):
   `POST /v1/pair/complete` body `{pair_code, platform, ccc_version,
   capabilities:[...], display_name}` → `{account_email_masked, account_ref,
   device_id, device_secret, relay_base_url}`.
4. Local CCC shows "Pair this computer with <masked email>?" and only persists
   credentials (0600 file, macOS keychain when available) after the user
   confirms. If declined: `POST /v1/pair/abandon` with the issued device_id.
5. Cloud marks the device active on its first `/v1/poll`.

`pair_code` is stored hashed, is single-use (row consumed on complete), and
attempts are rate-limited 10/hour/IP with constant-time compare.

## 4. Device ↔ relay loop

### 4.1 State sync (device → cloud)

`POST /v1/state` every 20 s while CCC runs (plus immediate on-change pushes,
min-interval 3 s, coalesced). Body (all lists capped; relay enforces 256 KiB):

```json
{
  "v": 1,
  "snapshot_at": "2026-07-10T18:00:00Z",
  "device": {"ccc_version": "5.7.0", "platform": "darwin",
             "capabilities": ["sessions","attention","workers","questions",
                               "schedules","session_input","question_answer",
                               "session_wake"]},
  "sessions": [{"ref": "…uuid…", "title": "…redacted or opaque…",
                "title_source": "redacted|opaque",
                "engine": "claude|codex|gemini|cursor|hermes|other",
                "state": "working|waiting|idle|ended",
                "is_live": true, "recency": "…ISO…", "context_pct": 42,  // percent of context window USED
                "machine_label": "…", "repo_label": "…opaque-or-redacted…",
                "question_waiting": false}],
  "attention": [{"kind": "question|stuck|failed|completed|approval|offline",
                 "priority": 1, "session_ref": "…", "summary": "…bounded, redacted…",
                 "created": "…ISO…"}],
  "questions": [{"session_ref": "…", "question_id": "…", "text": "…redacted…",
                 "options": [{"label": "…"}], "asked_at": "…ISO…"}],
  "workers": [{"queue": "…bounded label…", "depth": 3, "open": 2,
               "workers_live": 1, "state": "ok|backlog|draining|stuck",
               "oldest_open_age_s": 1200}],
  "schedules": [{"label": "…bounded…", "kind": "…enum…", "next_run": "…ISO…",
                 "last_result": "ok|fail|none", "last_run": "…ISO…"}]
}
```

Response: `{"ok":true,"queued":N}` where `queued` hints pending commands (the
client may immediately poll). The relay upserts snapshot rows, sets
`last_seen`, and never merges snapshots — latest wins per device.

**Local minimization is mandatory**: titles/labels/summaries/question text run
through the device's redaction pass before leaving the machine; when
`cloud.share_titles` is off, titles/repo labels are replaced with opaque
labels. The relay independently enforces field length caps (title ≤ 120 chars,
summary ≤ 300, question text ≤ 2000, ≤ 4 options, ≤ 200 sessions, ≤ 50
attention items, ≤ 50 queues, ≤ 50 schedules).

### 4.2 Command poll (cloud → device)

`GET /v1/poll?wait=25` — held up to 25 s; returns immediately when a command
is queued. Response `{"ok":true,"commands":[Envelope,…]}` (batch ≤ 10) or
`{"ok":true,"commands":[]}` on timeout. One active poll per device: a second
concurrent poll releases the first with `{"ok":true,"commands":[],
"superseded":true}` — the newest connection wins (handles zombie connections
after network loss without dual delivery, because commands are only marked
in-flight when written to a poll response, and undelivered in-flight commands
re-queue after 30 s without a result).

### 4.3 Command envelope (cloud → device)

```json
{"v": 1, "request_id": "…uuid…", "capability": "session.send_input",
 "created": "…ISO…", "expires": "…ISO…", "seq": 118,
 "idempotency_key": "…= request_id…",
 "payload": {"session_ref": "…", "text": "…≤16 KiB…"},
 "requested_by": {"kind": "user", "ua_class": "mobile|desktop"}}
```

Device-side validation (fail-closed, in order): a finalized `request_id`
returns its recorded result immediately (idempotent, no re-execute); a
`request_id` reserved-but-not-finalized on a prior attempt resolves to an
`interrupted` error and is NOT re-dispatched (at-most-once across a crash);
otherwise protocol version; capability in the device's own closed allow-list;
`expires` in the future (max lifetime 10 min, clock-skew grace 60 s); payload
size and schema. The device then **reserves** the `request_id` and executes by
calling the local CCC loopback endpoint mapped below.

**Replay defense is idempotency + expiry, not sequence ordering.** The relay
issues a per-device monotonic `seq` for observability and in-batch ordering,
but the device does NOT reject a command whose `seq` is below the high-water
mark: the relay legitimately re-queues an earlier, never-delivered command
after a later one advanced the mark (delivery to a dead socket), and rejecting
it as `replay` would silently drop a real user action. A genuine replay of an
already-executed command carries a seen `request_id` and is caught by the
idempotency store (24 h ≫ the 10 min max lifetime); a never-seen command is
either still-valid (executes once) or past `expires` (rejected). Reserve-
before-dispatch guarantees at-most-once even if the device crashes between the
local action and persisting the result.

### 4.4 Command result (device → cloud)

`POST /v1/result` body `{"request_id": "…", "status":
"ok|error|expired|rejected", "error_code": null, "detail_code":
"delivered|resumed|queued_for_wake|…", "completed_at": "…ISO…"}`. The relay
marks the command terminal, notifies the browser, appends the audit row, and
deletes the mailbox payload. Results are idempotent by `request_id`.

### 4.5 Capabilities → local execution map (closed list)

| Capability | Local execution (device's own loopback API) |
|---|---|
| `session.send_input` | `POST /api/inject-input {session_id, text, mode:"send", origin:"cloud-relay"}` |
| `question.answer` | `POST /api/answer-question` when a relayed question is pending; else `POST /api/inject-input {mode:"answer"}` |
| `session.wake` | `POST /api/inject-input` with the provided text against a dormant session (existing resume path) |
| `session.detail` | assembled read: bounded recent-turn window (≤ 20 turns, tail-read), returned via `/v1/result` `detail` field, relay caches ≤ 5 min then deletes |
| `session.history` | same as `session.detail` with a longer window (≤ 120 turns, ≤ 4000 chars each, redacted); the device trims oldest turns so `detail` stays under the relay's 48 KiB `detail_json` bound and sets `detail.truncated` |
| `notification.ack` | relay-side only; never reaches the device |

Anything else → `{"status":"rejected","error_code":"unknown_capability"}`.
`session.stop` is not in v1 (deferred pending security review).

## 5. Browser ↔ relay API (summary)

Cookie-session authenticated (HttpOnly, SameSite=Lax, Secure in prod); every
state-changing call requires `X-CCC-CSRF` matching the session's token; strict
same-origin checked against the service's own canonical origin.

- `POST /v1/auth/request-code {email}` → 204 (always; no account enumeration)
- `POST /v1/auth/verify {email, code}` → session cookie + `{account}`
- `POST /v1/auth/signout`; `GET /v1/me`
- `GET /v1/overview` → devices + attention (merged, each item `{…,
  device_id, observed_at, stale}`); `stale` = snapshot older than 90 s or
  device offline (no poll/state for 60 s)
- `GET /v1/sessions`, `GET /v1/workers`, `GET /v1/schedules`,
  `GET /v1/questions` — same freshness envelope: `{data, observed_at, stale,
  device_offline}` per device
- `POST /v1/commands {device_id, capability, payload}` → `{request_id,
  state:"queued"}`; `GET /v1/commands/<id>` → `queued|delivered|ok|error|
  expired|rejected` (browser shows "sending" until `delivered`, "done" only on
  `ok`)
- `POST /v1/questions/answer {device_id, session_ref, question_id, answer}` —
  sugar over commands with capability `question.answer`
- Devices: `GET /v1/devices`, `POST /v1/devices/<id>/revoke`
- Privacy: `GET /v1/privacy/summary`, `GET /v1/audit?device_id=`,
  `GET /v1/export`, `POST /v1/account/delete {confirm_email}`
- Notifications: `GET/PUT /v1/notifications/prefs`,
  `POST /v1/notifications/subscribe` (Web Push subscription),
  `POST /v1/notifications/test`
- Consent: recorded at signup (terms) and at first pairing (cloud relay
  consent, versioned); `GET /v1/consent/history`

## 6. Error codes (shared vocabulary)

`bad_device_credentials, device_revoked, account_deleted, rate_limited,
payload_too_large, unknown_capability, expired, replay, invalid_payload,
device_offline, protocol_upgrade_required, not_paired, pair_code_invalid,
pair_code_expired, session_not_found, handoff_leased`.

## 7. Local client behavior (claude-command-center side)

- Module `cloud_relay.py` (stdlib-only), state under
  `~/.claude/command-center/cloud/` (`config.json`, `credentials` 0600,
  `idempotency.sqlite3`).
- Disabled by default. Enabling requires explicit UI action (localhost-origin
  POST, same escalation gate as `/api/network-config`).
- Loop: state push + poll threads; reconnect with exponential backoff
  (1 s → 60 s cap, ±20 % jitter); heartbeat is the state push itself.
- Local API for the dashboard UI: `GET /api/cloud-relay/status`,
  `GET/POST /api/cloud-relay/config` (POST localhost-only),
  `POST /api/cloud-relay/pair {pair_code, relay_url}` (localhost-only),
  `POST /api/cloud-relay/unpair` (localhost-only; also calls cloud revoke).
- Cloud enablement must not touch `network.json`, `ALLOWED_ORIGINS`,
  Tailscale, telemetry consent, or same-origin checks. `CCC_TELEMETRY_DISABLED`
  does not affect the relay; `CCC_CLOUD_DISABLED=1` kills the relay loop
  unconditionally (parallel, equally hard switch).
- Every executed command appends to a local audit log
  (`cloud/audit.jsonl`): `{ts, request_id, capability, session_ref, outcome}`
  — user-visible in the local dashboard.

## 8. Versioning

`v` bumps on breaking envelope/endpoint changes; the relay advertises
`min_supported` in `426` responses; the device advertises its protocol version
in `/v1/state`. Additive fields are non-breaking. This file is the changelog
anchor for the protocol.

## Appendix A — v1 clarifications (2026-07-10, maintainer rulings)

1. **Rotation trigger.** The relay may set `"rotate_requested": true` on any
   `/v1/state` or `/v1/poll` response; the client then performs §2 rotation.
2. **Device-side unpair.** `POST /v1/device/self-revoke` (device-authed)
   tombstones the calling device. Local unpair calls it best-effort, then
   wipes local credentials regardless of the outcome.
3. **Idempotency ordering.** For a request_id already recorded, the device
   returns the recorded result immediately after version+capability checks
   (a legit retry carries the same seq and must not be rejected as replay).
   New request_ids validate expiry → seq → payload, in that order.
4. **Worker fields.** `depth` = total items in the queue, `open` = unclosed
   items, `workers_live` = live worker count.
5. **Attention kind mapping (canonical).** Local classifier → wire enum:
   question_blocked / soft_block / sidecar_waiting → `question`;
   pending_tool / stale_tool_call / needs-approval states → `approval`;
   stuck queues/workers → `stuck`; session died mid-question or job failed →
   `failed`; pushed_open / committed_not_pushed / uncommitted_edits /
   open_backlog / needs_attention_label → `completed` (finished work awaiting
   human follow-up); device offline items are cloud-computed → `offline`.
6. **Capability tokens.** The §4.1 capability list may additionally include
   `session_detail` when the device supports on-demand detail windows.

## Appendix B: client notes (2026-10)

- Relay URL: `https://` for any host; plain `http://` only for a loopback host
  (local staging). No userinfo, query, or fragment. The device secret is sent
  only to the origin the user chose: a `relay_base_url` in the pairing answer
  is used only when it has the same origin. There is no built-in service URL;
  the user types it, or sets `CCC_CLOUD_SERVICE_URL`.
- `POST /v1/pair/abandon` and `POST /v1/device/self-revoke` are device-bearer
  authenticated. Unpair calls self-revoke best-effort, then wipes local
  credentials whether or not the call lands.
- `question.answer` payloads carry `answer` as `{index?: int, text?: str}`;
  the device maps it to `/api/answer-question` `answers[]` (index `-1` when
  only text is given).
- Three consecutive `401 bad_device_credentials` responses, `device_revoked`,
  or `account_deleted` stop the loop and wipe the local credential
  (`disabled_reason` says why); network errors never do.
- Only one CCC process per machine runs the loop (an exclusive lock in the
  cloud state directory).
- A failed local delivery reports the loopback endpoint's own snake_case
  `code` as `error_code` (default `delivery_failed`), with a redacted
  `detail.local_error`.
- Provenance: an envelope may carry `issued_by: {type:"agent", name}` when an
  agent token issued the command. For `session.send_input` and `session.wake`
  the device then prepends the line `[From <name> via CCC Cloud, request <id>]`
  to the injected text (name stripped of brackets and control characters, 40
  chars). A missing field means the old behaviour (no header). Retries are
  deduplicated by the cloud (`idempotency_key` on the caller's request), not by
  the device; the envelope's own `idempotency_key` is still the request id.
- Session replies: `session.send_input` text reaches a Claude session as a
  peer message from CCC, with a line asking it to answer by `SendMessage` to
  `ccc`. For 24h after the relay messages a session, a peer message from that
  session to CCC is sent up in a top-level snapshot field `replies`:
  `[{id, session_ref, text, at}]` (at most 20; `text` redacted and capped at
  4000 chars; `at` is ISO-8601). Each reply also gets a top attention item of
  kind `reply` (not `question`, so no `question_waiting` notification). The
  relay's ask API (`POST /v1/ask`) matches a new reply id to the newest
  unanswered ask for that `session_ref`; replies with no matching ask are
  ignored. The next `session.send_input` or `question.answer` to that session
  clears its replies. A change in `replies` changes the snapshot digest, so
  it is always pushed (as part of a delta or full push) and CCC requests a prompt one.
- Heartbeat pushes: when a full `POST /v1/state` response carries
  `unchanged_ok: true` and the next snapshot is identical (ignoring
  `snapshot_at`), CCC sends `{"v": 1, "snapshot_at": ..., "unchanged": true}`
  instead. The relay only moves the stored snapshot's freshness; it answers
  `need_full: true` when it has no stored snapshot, and CCC resends in full.
  CCC still sends a full snapshot at least every 10 minutes.
- Delta pushes: when a state response carries `delta_ok: true` and the
  snapshot changed, CCC sends `{"v": 1, "snapshot_at": ..., "delta": true,
  "base": <digest of the last pushed snapshot>, "digest": <digest of this
  one>, ...}` with only what changed: `device` if changed; `sessions` as
  `{"upsert": [rows], "remove": [refs], "order": [refs]}` keyed by `ref`
  (`order` only when the result is not "previous order minus removed, new
  rows appended"); every other list section (`attention`, `questions`,
  `workers`, `schedules`, `replies`, ...) whole when it changed (`[]` when
  emptied), omitted otherwise. Full pushes carry their `digest` too. The
  relay stores the last digest, rebuilds the full snapshot, and processes it
  exactly like a full push. A missing row, a `base` that is not the stored
  digest, or a malformed delta gets `{"ok": true, "need_full": true}` and
  CCC resends in full. CCC sends a full push after any push error, every 10
  minutes, and whenever the delta would not be smaller.
- Attention item context (additive, all optional): items for a Task-tool
  sub-agent carry `subagent: true`, `parent_ref`, `parent_title`,
  `agent_label` (its description) and `covered_by_parent` (true while the
  parent session is live or working, so a human is already watching it); the
  `title` reads `<agent_label> (sub-agent of <parent_title>)` and `repo_label`
  falls back to the parent's. Approval items carry `action`, the pending tool
  plus a short preview, for example `Bash: git push origin main`. Text fields
  are redacted and capped; with `share_titles` off only `subagent`,
  `parent_ref` and `covered_by_parent` are sent. A sub-agent ref is the
  composite `<parent>:agent-<hex>`, the same as in `sessions`.
- `session.detail` results (`detail`) now also carry `title`, `repo_label`,
  `state`, `is_live`, `parent_ref`/`parent_title`/`agent_label`/`subagent` for
  sub-agents, and `pending` (`{type: "question", text, options}` or
  `{type: "approval", tool, action}`) next to `turns`. If a sub-agent
  transcript cannot be read, `turns` come from the parent and
  `fallback: "parent"` is set.

### Presence and alert routing (2026-10)

State bodies gain two additive fields.

- Top-level `presence: {present: bool, last_input_at: iso|null}`. `present` is
  true when the person gave input in the last 5 minutes. Delta pushes carry it
  whenever it changes (it is a normal top-level key). A client that omits it is
  treated as away.
- Per-session `last_human_input_at` (iso, key absent when unknown).

"Human input" is a prompt the person typed into a terminal session (a
transcript user turn that is typed text, not a tool result, meta or sub-agent
turn, not a provenance marker such as `[From <name> via CCC Cloud, ...]`,
`Announced from:` or a system tag, and not text CCC itself injected) or a send
from the dashboard composer. Cloud-relay, peer, WatchTower and other API
injections never count. Transcripts are scanned only for live sessions, once
per change of the session's modification time. The dashboard tab visibility is
not a signal: the server has none.

The cloud uses these to route each approval or question to one owner. An
item with `covered_by_parent` has owner `parent` and wakes nobody. While the
person is present the owner is `amir` with a 5-minute grace, then it moves to
the agent (`instinct`) and the wake email is sent. When the person is away the
agent is woken at once. Items are exposed at `GET /v1/attention`,
`GET /v1/attention/<id>` and `POST /v1/attention/<id>/claim`, with
`resolved_by` of `instinct`, `amir`, `self` or `unknown`. An agent-issued
`session.send_input`, `session.wake`, `question.answer` or ask to a session
whose `last_human_input_at` is within 5 minutes gets `409 operator_active`
unless the request sets `force: true` (only for cases the person approved).

### Approval answers and rights policy (2026-10)

A new capability, `approval.answer`, lets the cloud answer a Claude Code
permission prompt. Payload: `{session_ref, decision: "allow"|"deny",
action_sha256, attention_id?}`. The device advertises `approval_answer`.

The cloud decides whether an agent token may send it (rights policy, written
rules, a built-in classifier that never allows deploy, publish, delete, push
to main and similar actions, and a requirement that the full action text is
known). The device does not trust that decision blindly. It acts only when:

1. The session is a top-level Claude session (a sub-agent's prompt lives in its
   parent's terminal; other engines have their own approval controls).
2. A real permission prompt is pending now (the Notification-hook marker, not
   just a running tool).
3. The pinned hash equals the hash of the pending tool call. Current clouds
   send `hash_kind: "input"`: the sha256 of the complete canonical tool input
   (see "Approval input" below). Older clouds omit `hash_kind` and pin the
   sha256 of the redacted `action` display text. A mismatch is `rejected` with
   `error_code: "action_changed"` and nothing is delivered.

Delivery, in order:

1. **PermissionRequest hook.** CCC's `hooks/permission-request.py` runs when
   Claude Code is about to open a permission dialog. While the relay loop is
   running it parks (up to 15 minutes, then falls through to the normal prompt)
   and watches `~/.claude/command-center/cloud-approvals/decisions/`. When the
   device accepts a `hash_kind: "input"` command and a hook is parked on that
   exact tool call, it writes a decision `{sha256, decision, created, expires}`.
   The hook answers `allow`/`deny` only if the decision's hash equals the hash
   of the tool call it was given, the decision is under 120 s old, and it can
   claim the file with an atomic rename (single use). The result is `ok` with
   `detail_code: "hook"`. No keystroke, no focus change, works headless. If the
   hook does not claim the decision within 4 s the device withdraws it and
   falls back to 2.
2. **Keystroke fallback**, only when no hook is parked on that tool call (hook
   not installed or approved, the wait window elapsed, or an older cloud): the
   same path as the dashboard Approve/Deny buttons (`POST /api/claude/permission`:
   Return approves once, Esc denies), which drives the session's own terminal tab
   through System Events. That works on macOS with a live tty only. Elsewhere the
   result is `rejected` with `error_code: "unsupported"` and `detail_code:
   "macos_only"` (or `subagent_prompt`, `not_claude`). A successful result is
   `ok` with `detail_code: "delivered"`. There is a short gap between the check
   and the keystroke in which the prompt could change; the hook path has none.

Other rejections: `no_pending_approval`, `not_live_tty`, `no_pending_prompt`,
`invalid_payload`. The command is at-most-once like every other capability.

#### Approval input (complete tool input)

With `share_titles` on, an approval attention item and the `pending` object of
`session.detail` also carry the complete tool call:

- `action_input`: canonical JSON text of `{"tool": <name>, "input": <tool
  input>}` (sorted keys, compact separators, UTF-8). Hard cap 16384 characters
  (detail bodies may shrink it further to stay within their size bound).
- `action_input_truncated`: `true` when the text was cut. Never silently
  dropped: a MultiEdit keeps every edit; an oversize Write is flagged.
- `action_input_sha256`: sha256 over the WHOLE canonical input, even when the
  text was cut. The cloud recomputes the hash from `action_input` and refuses
  an agent allow when the input is truncated or the hashes differ.

`action_input` is not run through the path/email/token minimizer (that would
blank every absolute path and long identifier, making edits unreadable). Only
strong secret shapes (credentialed URLs, private key headers, API key
prefixes, JWTs, bearer tokens) are masked; a masked input no longer hashes to
`action_input_sha256`, so such an item cannot be allowed remotely. The
display `action` stays minimized and capped at 2000 chars.
