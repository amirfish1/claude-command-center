# Decision answer controller

`ccc_server.decision_answer_api.DecisionAnswerAPI` is an inert, stdlib-only
controller used by the explicit answer HTTP handlers. The controller starts no
threads, selects no source executable, and dispatches no background work.
Installing these handlers does not activate answering in the dashboard. Source
writer compatibility, complete source episode identities and coordinated
producer ownership must be established before enabling delivery.

## HTTP surface (disabled by default)

The dashboard composes the controller through `decision_answer_http`:

| Method | Endpoint | Operation |
| --- | --- | --- |
| POST | `/api/decision-inbox/answers` | Record the exact answer body below; never send |
| GET | `/api/decision-inbox/answers?answer_id=UUID` | Look up local status; never contact the source |
| POST | `/api/decision-inbox/answers/send` | Explicitly send a pending frozen answer |
| POST | `/api/decision-inbox/answers/reconcile` | Look up an interrupted delivery receipt; never send |

POST actions use `{"answer_id":"UUID"}`. All POSTs enforce the dashboard's
same-origin policy and a 128 KiB body limit. Source selection and actor defaults
are server-owned; clients cannot provide an executable, command, source snapshot
or routing target. Only an explicit server environment setting
`CCC_DECISION_ANSWERS_ENABLED=1` permits controller operations. The default 503
`answers_disabled` response does not initialize a registry, discover a source
executable or contact WatchTower. The UI remains read-only in this phase; no
automatic dispatcher or retry loop is installed.

Construct it with an explicit `DecisionRegistry`, an optional read-only
`export_source()` callable returning the full owner export described by
`decision_sources.validate_export`, and a conditional, idempotent adapter such
as `WatchTowerAnswers`. Construction freezes the generic opt-in
`CCC_DECISION_ANSWERS_ENABLED`: only the exact string `1` enables calls. The
optional `environ` mapping supports isolated composition and testing; default
construction uses the process environment. Disabled construction does not
initialize an outbox; all operations return `answers_disabled` without parsing,
recording, refreshing, or contacting a source. No settings file is changed.

## Controller contract

Methods return JSON-compatible dictionaries or raise `AnswerAPIError`, whose
`status` is the proposed HTTP status and `as_dict()` returns
`{"ok": false, "error": "<code>"}`. Successful calls are suitable for HTTP 200.
There are no actual HTTP endpoints in this phase. Future handlers must enforce
transport body limits before reading, authorization and same-origin checks.

- `record(body)` accepts UTF-8 JSON bytes or a JSON string containing exactly
  `request_id`, `expected_revision`, `text`, and `idempotency_key`. Request IDs
  are canonical lowercase UUIDs. Revisions are integers from 1 through
  9223372036854775807; booleans and floats are rejected. Text must be nonblank
  and at most 24,000 characters. Keys must be nonblank and at most 200
  characters. Strings are preserved exactly, including whitespace. The entire
  body is limited to 131,072 UTF-8 bytes. Duplicate JSON properties, nonfinite
  numbers, malformed UTF-8, surrogate text, and unknown properties are rejected.
- `status(answer_id)` looks up a canonical answer UUID locally. It never
  refreshes, sends, reconciles, or changes an answer's delivery state.
- `act('send', body)` and `act('reconcile', body)` accept JSON containing only
  `answer_id`. Send can dispatch a pending answer. Reconcile can look up a
  sending or unknown answer's receipt. Other states return their current
  status. Reconciliation never sends, including when authoritative absence
  makes the answer pending again. Sending an unknown answer never resends it.

Successful results contain `ok`, `answer_id`, `request_id`, `state`, `answer`,
`receipt`, `replay`, and `work_completed`. `answer` contains the frozen request
ID, expected revision, exact text and server actor `owner`. Record sets
`replay=true` when its initial key lookup finds the identical canonical answer;
under a concurrent insertion, the atomic outbox may return the same answer
with `replay=false`. This flag never grants permission to send. Internal source
snapshots and attempt tokens are not exposed as caller-editable fields.
`work_completed` is always false: source acceptance establishes delivery,
not completion of downstream work. The source adapter freezes its established
human defaults (`by_kind=human`, empty session ID, no engine or ticket ID).
Caller actor, routing, wait, store, generation, and source overrides are invalid.

## Replay, freshness and ownership

Before source refresh, record checks the immutable idempotency key. An exact
canonical replay returns its original answer even after source withdrawal,
registry revision changes, or source unavailability. Different text, request
ID, revision, or actor under that key conflicts. Replay never dispatches,
including pending, sending, unknown, or accepted answers.

A new key requires a successful complete current full export. The existing
source validator and registry reconcile that export; failed or incomplete
refreshes never become empty/current source observations. The existing outbox
then atomically checks the current expected revision, authoritative store UUID,
source contract and delivery owner before recording. Only global owner
questions with an exact conditional WatchTower block snapshot are supported.
Routine-owned requests reject; local or promoted session questions, review,
rationale and legacy cards have no fallback delivery path.

`ControllerOutbox.get_by_key` is a small read-only extension of `AnswerOutbox`
using its existing registry transaction and row decoder. It is needed because
the foundation exposes only lookup by answer UUID. It can later move into the
shared outbox API; the controller writes no SQL state transitions. Atomic
record/claim/finish and the existing unique owner constraints remain the only
arbitrators of concurrent calls.

An explicit send submits only the frozen outbox payload and answer UUID,
including exact question body, generation, wait and source store UUID. A lost
response remains unknown. Receipt-first reconciliation may establish acceptance
or rejection; only authoritative absence may requeue the same answer ID. A
subsequent explicit send may retry that same frozen payload using the existing
conditional source contract. Accepted state and receipt remain sticky forever;
neither send nor reconcile can regress or replay acceptance.

## Errors

| Status | Code | Meaning |
| --- | --- | --- |
| 400 | `invalid_request` | Invalid JSON, shape, field type, identifier or bounds |
| 400 | `invalid_action` | Action other than send/reconcile |
| 404 | `answer_not_found` | No locally recorded answer with that UUID |
| 409 | `answer_conflict` | Key reuse, stale revision, unsupported request, existing answer or another delivery owner |
| 413 | `body_too_large` | Request exceeds the UTF-8 byte bound |
| 503 | `answers_disabled` | Explicit generic opt-in is absent |
| 503 | `source_unavailable` | New record lacks a valid full refresh, or eligible action lacks a conditional adapter |
| 503 | `registry_unavailable` | SQLite operation failed |

Source submission failures and ambiguous receipts use the existing outbox
`unknown` state rather than claiming rejection or acceptance. Failed receipt
lookup preserves the existing state. Rejection reasons remain in the durable
receipt. No scans, worker starts, child sessions or legacy answer commands are
part of this contract.
