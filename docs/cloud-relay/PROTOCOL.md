# CCC Cloud Relay Protocol v1

Status: Normative (2026-07-10). Owner: Fable.
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

## Appendix A — v1 clarifications (2026-07-10, Fable rulings)

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
