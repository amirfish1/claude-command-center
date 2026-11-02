# Explicit session questions

Session questions remain local unless explicitly promoted. Q tags in ordinary
transcripts, notifications, and session summaries do not publish questions.
There is no automatic backfill. This additive event API stages the shared
lifecycle without changing the existing Decisions page or its count.

`POST /api/decision-inbox/session-events` accepts a JSON event. Every event has
a canonical UUID `event_id`, `session_id`, and an `operation`. Reusing an event
ID with the exact payload returns its original result; changing the payload
returns HTTP 409. The API writes only CCC's registry. It never sends session
input, creates legacy cards, invokes a model, or changes a ticket.

Supported operations:

| Operation | Additional fields | Result |
| --- | --- | --- |
| `create` | `question_id`, `generation`, `title`, `body`, optional `aliases` | New local question |
| `edit` | `request_id`, `expected_revision`, `title`, `body` | Same identity, new revision |
| `promote` | `request_id`, `expected_revision`, `reason` | Same identity, global visibility |
| `answer_local` | `request_id`, `expected_revision`, `text`, `source_message_id` | Records an answer already received in the originating session |
| `withdraw` | `request_id`, `expected_revision`, `reason` | Withdraws the question without claiming an answer |

Creation requires caller-supplied UUIDs for `question_id` and `generation`.
Persist them when asking the question; reuse them for retries. They identify
the question and its episode, separately from its wording and readable Q tag.
Aliases must be full question tags with a number and slug. Aliases never merge
requests, including identical tags from different sessions.

Subsequent operations require the same originating session and the current
integer request revision. Creation always starts local; promotion is a separate
explicit event with a reason. A local answer resolves the same request after
promotion, so it stops contributing to the global open set. Repeated wording
in a later episode gets a new identity. Resolve or withdraw the preceding
episode explicitly before creating another for the same question ID.

`answer_local` is a source report, not a delivery command. Submit it only after
the user's answer actually appears in that conversation, referencing the
received message. Its `delivery_state: "local"` does not claim worker resumption
or completed work. Remote answers to session questions remain unsupported until
their source can enforce conditional, idempotent delivery. The generic answer
outbox refuses these questions.

`GET /api/decision-inbox/session-questions?session_id=<UUID>` returns only
explicitly registered records for that session, including local and historical
records. It never reads transcripts or creates registry state on an unused
installation. Unavailable persistence returns HTTP 503 with `requests: null`;
it is never reported as a successful empty list. Invalid events return 400;
stale revisions, wrong origins, or reused event IDs return 409. Event bodies
are limited to 128 KiB. The normal same-origin POST boundary applies.

The internal `SessionQuestions.events(request_id)` audit retains immutable
event payloads and timestamps. Idempotent replay cannot reopen an answered
question. Neither this API nor promotion replaces existing session input or
enables a second answer-delivery mechanism.
