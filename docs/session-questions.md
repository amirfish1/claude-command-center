# Explicit session questions

`ccc question` registers questions explicitly. A `[Q42:choice]` tag is an
optional display alias, never an identity or an enrollment trigger. The CLI
does not scan transcripts, send agent input, or promote questions automatically.
`ccc decisions` continues to search recorded decisions; it is a separate command.

All commands use `--server URL`, then `$CCC_SERVER`, then the URL in
`~/.claude/command-center/port.txt`. Put `--server` before `question`.
Successful commands print the server's JSON envelope, including stable request
IDs and revisions; event commands also print `event_id`. Failures return nonzero
and print a generic JSON error without echoing question bodies, answer text,
server error bodies, or local file contents. Argument errors use argparse's
stderr and exit code 2.

## Create, inspect, and promote

```sh
ccc question create --session-id "$SESSION_ID" --title 'Choose an approach' \
  --body-file question.txt --alias '[Q42:choice]'
ccc question inbox --session-id "$SESSION_ID"
ccc question promote --session-id "$SESSION_ID" --request-id "$REQUEST_ID" \
  --revision 1 --reason 'Owner input is needed across sessions'
```

`create` generates new canonical UUIDs for the event, question, and generation.
The server creates a stable `request_id`, initially at revision 1 with
`request.content.visibility` equal to `local`. Creation writes the local
registry only; it does not publish the question to the global inbox. Omit
`--alias` for no aliases, or repeat it for multiple full tags. Aliases must match
`[Q<number>:<lowercase-kebab-slug>]`, with at most 20 per create event.

Promotion is an explicit local-to-global transition, checked against the
current request revision. Read the returned revision after each lifecycle
change. A stale revision or a resolved question causes failure; inspect the
inbox before deciding whether another event is appropriate.

`--body-file -` reads stdin. UTF-8 files preserve whitespace and line endings,
including trailing newlines. A body or title cannot be empty or whitespace-only.
Titles are limited to 500 characters, bodies to 24,000 characters, and promotion
or withdrawal reasons to 2,000 characters.

## Exact event JSON and replay

```sh
ccc question event --json-file event.json
cat event.json | ccc question event --json-file -
```

This posts the caller's JSON object to `POST /api/decision-inbox/session-events`
without adding or replacing fields or IDs. JSON serialization may change
insignificant JSON formatting; string values, including whitespace, remain
unchanged. The server validates against `SessionQuestions._validate`.
Use canonical lowercase UUID strings, not names, prefixes, aliases, or session
display titles. Never reuse an event UUID for different content.

For reliable retries, save a complete event with its IDs before submitting it.
Replaying the same event UUID and payload returns its original result without
repeating the transition; changing its payload conflicts. Convenience commands
create a fresh event UUID on every invocation, so use saved event JSON when
retrying after an uncertain response. Repeating `create` creates a new question
identity; it is not a retry of the previous create.

A complete create event looks like this (generate your own UUIDs):

```json
{
  "event_id": "10000000-0000-4000-8000-000000000001",
  "operation": "create",
  "session_id": "20000000-0000-4000-8000-000000000001",
  "question_id": "30000000-0000-4000-8000-000000000001",
  "generation": "40000000-0000-4000-8000-000000000001",
  "title": "Choose an approach",
  "body": "Which approach should this session use?\n",
  "aliases": ["[Q42:choice]"]
}
```

Every event includes `event_id`, `operation`, and owning `session_id`. Only the
following additional fields are supported; unknown fields are rejected.

| Operation | Additional fields |
| --- | --- |
| `create` | `question_id`, `generation`, `title`, `body`, optional `aliases` (defaults to `[]`) |
| `edit` | `request_id`, `expected_revision`, `title`, `body` |
| `promote` | `request_id`, `expected_revision`, `reason` |
| `withdraw` | `request_id`, `expected_revision`, `reason` |
| `answer_local` | `request_id`, `expected_revision`, `text`, `source_message_id` |
| `consume` | `request_id`, `answer_id`, `question_id`, `generation`, `source_message_id` |

`expected_revision` is a positive JSON integer. Local edits, withdrawals, and
answers use the same exact-event command. For example, after substituting the
real owning session and request UUIDs and current revision:

```json
{
  "event_id": "10000000-0000-4000-8000-000000000002",
  "operation": "edit",
  "session_id": "20000000-0000-4000-8000-000000000001",
  "request_id": "50000000-0000-4000-8000-000000000001",
  "expected_revision": 1,
  "title": "Choose the revised approach",
  "body": "Updated question with its original whitespace.\n"
}
```

To withdraw, generate a fresh `event_id`, set `operation` to `withdraw`, keep the
three binding/revision fields (`session_id`, `request_id`, `expected_revision`),
and replace `title` and `body` with `reason`. To report an answer already
received in the owning conversation, use `operation: "answer_local"`, keep
those binding/revision fields, and supply `text` and the actual transcript
`source_message_id` instead of `reason`. This reports existing local input; it
does not deliver an answer to an engine. Local answer text is limited to 24,000
characters; source message IDs must be nonempty text of at most 200 characters.

## Receiving and consuming an accepted remote answer

`GET /api/decision-inbox/session-questions?session_id=UUID`, exposed by
`ccc question inbox`, returns `requests` and `remote_answers`. Each accepted
remote answer has separate `receipt`, `answer`, `source`, `consumption`, and
`notification` objects. Polling is read-only: it never promotes a question,
consumes an answer, sends a notification, or starts work.

An advisory notification is only a prompt to inspect this inbox. The recipient
must:

1. Poll using its own full session UUID. Match the notification's request and
   answer UUIDs to an accepted inbox record. Validate that the receipt and
   source belong to that owning session, request, question UUID, and generation;
   a display alias is insufficient. The receipt's `source_revision` identifies
   the question revision the accepted answer addressed.
2. Read the actual accepted `answer.text` and its source question. Do not infer
   answer content from a notification, delivery status, or receipt alone.
3. Check `consumption` and a durable downstream deduplication record keyed by
   the accepted receipt's `answer_id`. An already handled answer must not cause
   another action. Accepted answers remain visible after consumption.
4. After reading, explicitly consume before downstream work, supplying the real
   transcript `source_message_id` recording this session's receipt of the
   answer. Do not invent an ID or use the notification attempt ID as a substitute.

```sh
ccc question consume --session-id "$SESSION_ID" --answer-id "$ANSWER_ID" \
  --request-id "$REQUEST_ID" --question-id "$QUESTION_ID" \
  --generation "$GENERATION" --source-message-id "$TRANSCRIPT_MESSAGE_ID"
```

Consumption creates a fresh event UUID and posts only that explicit operation.
It checks the accepted answer's full binding. Repeated consumption of the same
answer returns its existing consumption record; it does not authorize another
action. Preserve and replay an exact consume event for uncertain HTTP outcomes.
Serialize handling per answer UUID and persist downstream progress using that
UUID as the idempotency key. If a crash occurs after consumption but before
work finishes, recover from that progress record rather than treating a
replayed notification as a new task. Consumption alone does not make external
side effects exactly-once.

An accepted-source-mailbox receipt means durable source inbox acceptance. It
is distinct from engine consumption and from completed work. The frozen receipt
may still say `consumed: false` after acknowledgment: consult the separate
current `consumption` object. Notification success likewise does not prove that
the engine read the answer. Neither acceptance nor consumption asserts work
completion; report completion separately after doing and verifying the work.
