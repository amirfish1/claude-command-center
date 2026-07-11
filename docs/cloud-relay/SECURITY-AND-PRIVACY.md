# CCC Cloud — security and privacy architecture

Audience: engineers and security reviewers. This document describes the trust
architecture of CCC Cloud in detail, public-safe. It is derived from the
normative sources and must not contradict them on any line:

- The wire contract: [`PROTOCOL.md`](./PROTOCOL.md) (and Appendix A).
- The hosted service's data classification, privacy statement, and threat
  model (in the `ccc-cloud` repository).
- The local client: `cloud_relay.py` in this repository.

The design's central claim is not "trust us." It is **the hosted service holds
almost nothing, and the laptop never trusts the relay blindly.** Every control
below serves one of those two ends.

---

## 1. Outbound-only transport

The local CCC device only ever makes **outbound** HTTPS requests. It opens no
inbound listener and creates no port. Concretely, the device:

- pushes a minimized state snapshot (`POST /v1/state`, every ~20 s plus
  coalesced on-change pushes),
- long-polls for commands (`GET /v1/poll`, held up to 25 s), and
- posts results back (`POST /v1/result`).

There is no path by which the relay, or anyone else, initiates a connection
*into* the machine. The relay can only place a command in a mailbox and wait for
the device to fetch it on its own outbound poll. Enabling CCC Cloud does not
touch `network.json`, `ALLOWED_ORIGINS`, Tailscale, same-origin checks, or
telemetry consent; it strictly adds an outbound loop.

## 2. The relay is not a proxy: closed capability list

The relay cannot make the laptop do anything outside a **closed, versioned
capability list**. The device's own allow-list in v1 is exactly four
capabilities:

- `session.send_input` — deliver bounded free-text input to a session.
- `question.answer` — answer a waiting question, routed to the local session.
- `session.wake` — wake a dormant session with provided text.
- `session.detail` — return a bounded recent-turn preview window on demand.

`notification.ack` is relay-side only and never reaches the device.
`session.stop` is **not** in v1 (deferred pending security review). Anything not
on this list is rejected locally as `unknown_capability`. There is no generic
shell execution, no arbitrary file read, no URL fetch, and no raw endpoint
proxying — those are explicitly out of scope.

### Local fail-closed validation order

For every command envelope the relay hands down, the **local** CCC validates
before executing anything, fail-closed, in this order:

1. **Protocol version** must match.
2. **Capability** must be in the device's own closed allow-list.
3. **Idempotency short-circuit:** if this `request_id` was already executed, the
   device returns the *recorded* result immediately and does not re-execute (a
   legitimate retry carries the same `seq` and must not be misread as a replay).
4. **Expiry:** `expires` must be in the future; a command whose lifetime exceeds
   the 10-minute cap (plus a 60-second clock-skew grace) is failed closed as
   `expired`.
5. **Sequence:** `seq` must be strictly greater than the last executed seq for
   this device; a regression is rejected as `replay`. (Gaps are fine; the relay
   issues a per-device monotonic sequence.)
6. **Payload schema and size:** a `session_ref` is required; input text must be
   a string within the 16 KiB cap or it is `payload_too_large`.

Only after all of that does the device dispatch the action to its own loopback
CCC API — exactly as a local caller would. A hostile or buggy relay is thus
confined to injecting the *same bounded actions the user could already take*,
never code execution, file exfiltration, or capability escalation.

## 3. Device credential handling

Pairing issues an opaque `device_id` and a 256-bit `device_secret`.

- **Hashed at rest on the server.** The relay stores only `sha256(secret)`. A
  database dump never yields usable device credentials.
- **0600 on the client.** The local client writes the secret to a credentials
  file created `0600` from its first byte (an `O_CREAT` open with an explicit
  mode, so there is no window where it is world-readable), written atomically,
  never logged, never echoed.
- **Rotation.** `POST /v1/device/rotate` issues a new secret; the old secret
  stays valid only until the first successful use of the new one, then is
  invalidated atomically. The client persists the new secret to disk *before*
  confirming the rotation. The relay may also request rotation by setting
  `rotate_requested` on a response.
- **Revocation semantics.** Revoking a device (from either side) tombstones the
  device row. Subsequent poll/state/result calls **fail closed** with a
  structured error (`device_revoked`); the local client stops its loop, marks
  itself disabled-pending-repair, and never retries with the same credentials.
  There is no degraded mode. Device-side unpair calls `self-revoke` best-effort
  and then wipes local credentials regardless of the outcome.

## 4. Replay and idempotency design

Two independent mechanisms prevent double-application and reordering harm:

- **Per-device monotonic sequence.** The relay stamps each command with a
  strictly increasing `seq`; the device tracks the last executed seq and rejects
  any regression as `replay`.
- **Persisted idempotency store.** The device keeps a local SQLite store mapping
  each executed `request_id` to its recorded result for 24 hours. A duplicate
  `request_id` returns the recorded result as a no-op — it is not re-executed —
  and this check runs *before* the seq comparison so a legitimate retry (same
  request_id, same seq) is never misclassified as a replay. In v1 the
  `idempotency_key` equals the `request_id`.

Combined with envelope expiry, a replayed, stale, or reordered command is either
rejected or is a no-op ack. Correct persistence of the idempotency store across
local restarts is a required invariant.

## 5. What the relay can technically see, and for how long

Honesty about the hosted trust boundary (see the privacy statement's "honest
limits" section): payloads are encrypted in transit (TLS) and minimized, but the
relay process can technically read **class C** operational metadata and **class
D** transient payloads while routing them. We do **not** claim end-to-end
encryption in protocol v1.

- **Class C (operational metadata), stored durably but bounded.** Session
  registry as opaque refs plus status enums, counts, timestamps, and short
  redacted labels; queue/worker health; schedule state; attention items;
  notification-delivery and product-event records. These are enums, counts, and
  bounded redacted labels — not free-form content.
- **Class D (sensitive session content), transient only:**

| Payload | Maximum lifetime | Notes |
|---|---|---|
| Session input text (user → device) | Deleted on delivery, or 10-minute expiry | 16 KiB cap |
| Question text + options (device → user) | Superseded on next sync, else ≤ 24 h | A bounded rendering, not the transcript |
| Session detail window (device → user) | 5-minute cache, then deleted | User-triggered; "content left your laptop" surfaced locally |

Relay logs carry envelope metadata only — ids, sizes, and codes — never payload
bodies, never tokens, never emails (only `account_id`). The mailbox is deleted
on delivery; a daily retention job expires anything past TTL as defense in
depth.

## 6. What the relay can never see (class E)

Never uploaded, under any setting: tokens, environment files, source files,
diffs, raw absolute paths, git remotes with credentials, transcripts, and tool
output. Because class E never reaches the cloud, no cloud-side compromise can
exfiltrate it.

Free-form fields that *could* accidentally carry a secret — titles and
queue/schedule labels — are minimized by the **local** client before anything
leaves the machine. A redaction pass strips path-like strings, credentialed
URLs, emails, and long hex/base64 tokens; the per-device `share_titles` toggle
(default on, with redaction) can be switched off to send opaque "Session on
`<machine>`" labels instead. Redaction is heuristic, so opaque labels are the
absolute control for sensitive repositories.

## 7. Separation from anonymous telemetry

CCC's anonymous install telemetry and CCC Cloud are **separate systems** with
separate contracts, different repositories, different deployments, and
different identifier spaces. There is **no join, import, or shared key** between
them, in either direction. Enabling or disabling one has no effect on the
other; `CCC_CLOUD_DISABLED=1` and the telemetry kill switch are independent.
The anonymous telemetry install ID is itself class E and is never uploaded to
the relay.

## 8. Notification payload privacy

Notifications never carry free-form content. Previews are fixed, generic copy
(for example *"An agent is waiting for your answer."*) with no titles, prompt
text, repo names, or machine names. Web Push bodies are encrypted to the
subscription keys, so the push provider relays ciphertext, not content. The
delivery log stores only `(kind, timestamp, delivered/opened)` — never the
preview text or body. Notifications are opt-in behind a master switch that is
**off by default**, gated further by per-kind toggles, quiet hours, dedupe, and
a per-hour rate limit. Deep links open the app to the relevant view but still
require the user's signed-in session.

## 9. Residual risks (stated honestly)

The threat model accepts these residual risks rather than hiding them:

- **Compromised relay process (T10).** A hostile relay can inject *bounded,
  valid* commands (fresh nonces) to the user's own sessions and read class D
  payloads during their TTL, and can degrade availability. It **cannot** achieve
  code execution, file exfiltration, or capability escalation, because the
  laptop validates the closed capability list, expiry, sequence, idempotency,
  and size locally before executing. Envelopes are **not** end-to-end
  authenticated from the browser through the relay to the laptop in v1.
- **Insider access to hosted infrastructure (T18).** An insider with live
  host/DB access can read email (plaintext class A), class C metadata, and any
  class D payload inside its TTL window. Data minimization is the primary
  defense — the service holds little, secrets/tokens/codes are hashed, class E
  is never present, class D is short-TTL, and logs carry metadata only. The
  admin surface is bearer-token only (no cookies) and every admin endpoint hit
  is recorded in an admin-audit table.
- **No at-rest encryption in v1.** This is a documented residual; disk
  encryption is treated as a deployment concern. Magic and pairing codes are
  stored as HMAC-SHA256 with a server-held pepper (not bare SHA-256), so a DB
  dump alone cannot brute-force live codes.
- **Compromised paired laptop (T13).** Full laptop compromise is game-over for
  *that* machine's local data and is explicitly out of scope to prevent; the
  design goal is containment — the device's cloud authority is scoped to its own
  account/device rows and the bounded capability surface, so it can never reach
  another user or another unpaired machine, and can be tombstoned from the cloud
  at any time.

### Planned for v2

The single strongest hardening lever is a **device-verifiable signed envelope**:
a MAC issued at pairing that lets the laptop cryptographically verify
relay-originated system messages, closing the browser→relay→laptop
authentication gap. The envelope `v` field is reserved so this can land without
a breaking change. `session.stop` and step-up re-authentication on sensitive
actions are revisited together in v2. Because the browser is served by the relay
itself, browser-held keys cannot defend against a hostile relay in v1 (it could
serve hostile JS) — which is precisely why v1 does not claim end-to-end
encryption and instead leans on minimization, short TTLs, the closed capability
list, and local validation.

---

## Reporting

Report security issues per the repository's [`SECURITY.md`](../../SECURITY.md).
Anything that could enable code execution, credential theft, or a boundary
escape should be reported privately to the maintainer, not filed as a public
issue.
