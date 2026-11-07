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
or routing target. An explicit installation opt-in (the environment setting
`CCC_DECISION_ANSWERS_ENABLED=1` or the persisted setting below) permits controller operations. The default 503
`answers_disabled` response does not initialize a registry, discover a source
executable or contact WatchTower. The default UI remains read-only. With the
same explicit opt-in, eligible source questions expose the controls below;
no automatic dispatcher or retry loop is installed.

An installation can instead persist `{"enabled":true}` in
`decision-inbox/answer-settings.json` under its CCC state directory. The read
model and answer endpoints read this setting on each operation, so it also
works with drain-aware restarts that preserve the process environment. Publish
the complete file with an atomic rename. Missing, unreadable or malformed
files disable answers; only a literal boolean `true` enables them. An explicit
`CCC_DECISION_ANSWERS_ENABLED` environment value takes precedence, including
`0` to disable. This opt-in does not migrate producer ownership: establish the
source and delivery prerequisites above first.

## Dashboard controls

The request response includes `answers_enabled`, per-row `can_answer`, and a
read-only `answer` summary (`answer_id`, `state`, exact `text`, and
`expected_revision`). Reading a summary never initializes an outbox or sends
an answer. New answer forms require a current source, a supported owner question and no
routine delivery owner. Queue questions require complete authoritative queue
coverage; explicitly promoted session questions are checked against the local
registry transaction. Ordinary local questions and approvals remain read-only.

**Save answer** records the exact text without delivery. **Send saved answer**
is a separate explicit action; **Check delivery** only reconciles an uncertain
attempt. A pending answer remains visible after saving. Source acceptance is
displayed separately from completion of the work.

Before recording, the browser retains the frozen payload and idempotency key
in session storage. An uncertain save can only retry that same submission;
refresh and reload never resend automatically. A lost send response exposes
receipt lookup rather than another Send action. Browser requests stop waiting
after 30 seconds so recovery controls become available even if the response
never arrives; a timeout does not establish source rejection. If session storage cannot
retain recovery state, the browser blocks the action. Unsaved drafts survive
routine refreshes in the current document; they are not persisted on reload.
Drafts stay attached to the revision shown when typing began. If that question
changes, saving is blocked until the owner reviews the current question and
explicitly selects **Use draft for current question**; refreshing alone never
rebinds an existing draft.

A definitive source rejection retains the original answer text. Once the
source is current and the request is eligible again, **Start new answer**
explicitly opens a new draft against its current revision while retaining the
previous rejection. An unknown submission cannot be reset this way. These
controls do not enable the server, change producer ownership or bypass source
validation.

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
The HTTP handlers listed above enforce the transport body limit before reading
and apply the dashboard's same-origin checks; they remain disabled by default.

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

A new WatchTower answer requires a successful complete current full export.
The source validator and registry reconcile that export; failed or incomplete
refreshes never become empty/current source observations. The outbox atomically
checks revision, authoritative store UUID, source contract and delivery owner.
For session questions, it instead checks the explicit promotion audit and exact
request/session/question/generation binding in the same registry transaction.
Routine-owned requests reject; ordinary local questions, reviews, rationale and
legacy cards have no fallback delivery path.

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
receipt. No scans, child sessions or legacy answer commands are part of this
contract. An explicitly promoted session answer may issue one advisory wake to
the exact originating session, as described below.

## Promoted session answers

[Session question commands](session-questions.md) keep creation local until an
explicit promotion. A reply freezes the complete source question and uses the
same Save, Send and Check operations as queue replies. A concurrent local edit,
withdrawal or local answer can win before source acceptance; stale remote sends
are rejected without overwriting that local resolution.

Source acceptance atomically records the reply in the originating session's
durable inbox. The UI says **saved for session; awaiting receipt** until that
session explicitly acknowledges the exact accepted answer. Receipt is separate
from completion of the requested work. The inbox remains readable after restart
and after consumption. Polling never consumes an answer.

An optional IDs-only wake targets the original session with same-session,
no-queue routing. It cannot start a substitute session. The notification journal
persists **uncertain** before calling transport; uncertain and notified attempts
never reroute or retry automatically. Unsupported engines or transport failures
leave the durable inbox available for polling on resume. Send replay and receipt
lookup never wake again. A transport success is not a session receipt.

The recipient reads the actual answer through `ccc question inbox`, verifies the
full binding, and explicitly uses `ccc question consume` with a real transcript
message ID. The UI then says **received by session**. Downstream actions still
require their own durable deduplication keyed by answer UUID.

An installation with persisted answer policy keeps the historical replay fence
even when new replies are disabled. Disabling delivery does not reactivate old
report options.

## Closing a question

`POST /api/decision-inbox/close` takes `request_id`, `expected_revision` and
`idempotency_key`, under the same opt-in as answers. A WatchTower question closes
through `wt close-conditional`, which refuses anything but the exact question
the owner saw, closes the ticket as Declined and wakes no worker. Undo it with
`wt reopen <ref>`. A session question has no source close, so it is hidden in CCC
only. A response with `state: "unknown"` means the outcome is not confirmed:
retry with the same key, which replays the source receipt. Rows report
`can_close`; a saved or accepted answer blocks closing until it is rejected.
