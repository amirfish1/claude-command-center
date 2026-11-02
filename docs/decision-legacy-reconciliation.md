# Historical decision report reconciliation

`ccc_server.decision_legacy_reconciliation.reconcile_legacy_cards` is a pure,
stdlib-only annotation function. It performs no filesystem, database, network,
source, registry, action-handler, or process operations. It does not enroll,
withdraw, answer, reopen, merge, or rewrite any card or canonical question. This
module alone activates no product behavior.

## Input contract

```python
from ccc_server.decision_legacy_reconciliation import reconcile_legacy_cards

annotations = reconcile_legacy_cards(
    retained_cards,                 # complete cards.json id -> card mapping
    owner_source_observation,       # existing validated source refresh result
    canonical_requests=rows,        # optional detached registry records
    delivery_owners=owners_by_uuid, # optional canonical delivery-owner metadata
)
```

All inputs must contain JSON values. The function detaches them and returns
JSON-safe values; unsupported objects and nonfinite numbers raise. Cards may
also be a list of card objects with IDs. Mapping keys are preserved separately
as `card_key`, even when they differ from the card's explicit `id`. Invalid card
objects raise rather than silently reducing the inventory. Supply **all retained
cards**, including closed, expired and superseded cards; never use the
visibility-filtered/recent inbox subset. Live derived review cards have their
own lifecycle and should not be added to the retained-card input.

`owner_source_observation` is the `decision_sources.owner_source_payload()` /
`apply_owner_export()` envelope, obtained by the integrating parent through its
existing source workflow. Specifically:

- `ok: true`, `mode: "authoritative_read_only"`, and no `source_errors`;
- `source.status: "current"`, `source.complete: true`,
  `source.authoritative: true`;
- `source.scope.backend: "file"` and a canonical UUID `store_id`;
- `requests`: the complete observed source request list, with canonical UUID
  `request_id`, positive integer `revision`, adapter `watchtower-block`, exact
  ticket `origin_id`, nonempty `generation`, `source_revision`, `status`, and
  `answer_state`; the source snapshot must match that store and generation.

Currency is accepted only from that full envelope. It must originate from the
existing validated export path, never a caller-fabricated envelope. This helper
does not revalidate an upstream export digest or fetch a source itself. Failed,
partial, stale, cached, shadow, missing, or malformed observations produce
unavailable coverage, even when saved rows remain readable. An incomplete row or
duplicate UUID invalidates the comparison rather than being dropped. A complete
empty list establishes complete empty coverage **only for local owner waits**.
It does not establish that tickets closed or that historical questions were
answered, delivered, or withdrawn.

Do not pass `decision_coverage.coverage_payload()` (a shadow display snapshot)
or `decision_requests.requests_payload()` (which includes retained history and
omits frozen source fields) as this observation. Do not substitute the registry's
historical open rows for the source's current list. Optional `canonical_requests`
may contain historical registry rows, including `content` objects, for exact
binding checks; these rows never add candidates. Obtain optional rows and
UUID-to-owner metadata from one consistent read observation after refresh. If
they are unavailable, omit them; missing owner evidence remains unknown. The
source refresh currently omits delivery owner, so typed verification normally
requires canonical owner metadata. Reading a producer's owner claim is
insufficient evidence of canonical ownership.

## Output contract

The result has `version: 1`, `mode: "read_only"`, `source_complete`, scoped
`coverage`, `cards`, `counts`, and `replay_allowed: false`. Coverage is either
`complete` or `unavailable`, with the observed scope and a reason. Counts include
`total`, `by_status`, and `by_classification`. Known legacy statuses are `open`,
`snoozed`, `decided`, `dismissed`, `expired`, and `superseded`; unknown statuses
are also inventoried, and missing statuses use the counter key `<missing>`.
No status is filtered out or treated as proof of canonical source delivery.

Each `cards` entry contains:

- `card_key`, `card_id`, and a fully detached original `card`, preserving every
  original field, including D/Q aliases, cardrefs, status, title/context text,
  options/actions, original decided result, and arbitrary producer metadata;
- display-only `aliases`, exact `ticket_refs`, and `classification`;
- `binding: {state, issues}` for typed `source.source_request` provenance;
- `current_questions`, a list of exact canonical UUID navigation targets;
- `legacy_answer_state` (`decided` only for an original decided status, otherwise
  `unknown`) and `legacy_delivery_state: "unknown"`, irrespective of historical
  action results;
- `action: {kind, request_ids, legacy_action_policy,
  requires_reconfirmation, replay_allowed}`.

Classifications have these meanings:

| Classification | Meaning |
| --- | --- |
| `unlinked/no-ticket` | No strict structured ticket ref and no typed binding; unrelated legacy workflow is unchanged by this annotation. |
| `source-unavailable` | Currency is uncertain; no current navigation or inferred withdrawal. Includes unsupported typed adapters, foreign stores, and unusable typed identity without a ticket. |
| `current-source-candidate` | One or more currently observed eligible open owner questions share a ticket; owner must inspect and reconfirm the canonical question. |
| `no-longer-waiting` | Complete owner-wait coverage has no eligible current owner question on the linked ticket; says nothing about past delivery or the ticket's overall status. |

`ticket_refs()` uses only whole `source.ref` and individual `source.tags`
strings, plus the typed WatchTower origin during reconciliation. It never
searches title, detail, context, source ID, URLs, embedded refs, or Q tags.
`[Q620:example]`, `Q-620`, and `D-14` are not ticket refs. Matching is exact and
case sensitive; whitespace is not silently stripped. A ticket relationship
never proves question identity, even if text or Q aliases also match.

Candidates require an observed open, global owner question, with
`answer_state == "none"` or rejected delivery (matching the canonical retry
eligibility). Historical/withdrawn/answered episodes and external/rationale
waits are excluded. Multiple eligible episodes on one ticket remain separate
UUID targets; nothing chooses, merges, or aliases them. Each target includes
UUID, revision, origin, generation, `relationship: "same_ticket_only"`, and
`requires_reconfirmation: true`.

Typed binding states are `untyped`, `invalid`, `unavailable`, `stale`, or
`verified`. Verification compares UUID, adapter, origin, generation, request
revision, source revision, exact frozen snapshot (including store, whitespace
and wait data), and canonical delivery owner. A verified frozen historical
binding is independent of current eligibility; it never upgrades a ticket link
to authorization. A stale binding remains stale even when the same ticket has a
new current question. Missing UUID/owner evidence remains unavailable. No
binding status transfers routine ownership to the CCC outbox.

## HTTP integration and legacy action guard

`GET /api/decision-inbox/legacy-reconciliation` exposes these annotations for all
retained cards, preserving the existing reports API. It obtains one authoritative
observation for the whole inventory, never a source refresh per card.
If the GET path has only shadow coverage or cannot obtain a source observation,
pass `None` and show unavailable coverage. This function itself stays pure.
Do not write the annotations back into `cards.json` or import them as answers.

When canonical answers are enabled by the installation opt-in, the
**actual legacy decide handler** enforces this guard before option lookup or
action execution:

1. Load the original requested card and determine the active source mode using
   the existing settings contract on the server, not a browser-supplied flag.
2. Hold legacy actions for routine reports (`card.source.source == "routine"`),
   any typed source report, and cards annotated `legacy_action_policy == "hold"`.
   An unlinked routine report is still held by this mode-specific routine rule;
   unrelated unlinked governor/session cards keep their existing behavior.
3. Return a structured conflict such as `canonical_question_required`, with
   exact UUID navigation targets when a fresh full source comparison supplies
   them. On unavailable coverage, hold and offer the existing source refresh
   operation. Do not execute an old human prompt, inject, spawn, or chosen option.
4. Apply the same guard to every legacy option execution entry point; hiding a
   button alone does not close the competing answer path. Existing typed-outbox
   protection alone does not protect untyped retained routine reports.

In the UI, retain original status and content as history. Replace actionable
old option buttons with **Open current question**, targeting the supplied UUID
in the unified canonical workflow. If multiple questions exist, show the
separate targets. If none exist, show the scoped no-longer-waiting or unavailable
explanation with no executable old answer. Preserve old options/decisions as
read-only history. Never preselect, prefill, adopt, or replay the prior chosen
answer. The owner answers the current canonical question afresh using its
revision checks and canonical delivery-owner fence. Navigation does not bypass
those checks or make a routine-owned question outbox-owned.

The pure reconciliation module remains advisory. The integrated HTTP handler
and renderer enforce the action policy separately: ticket-linked, typed source
and untyped routine reports cannot execute old options while canonical answers
are enabled. Scripted health alerts and live review gates retain their distinct
actions. Inventory reads do not activate answers, initialize source identities,
or mutate source tickets.

An installation with persisted answer policy keeps the historical replay fence
even when new replies are disabled. Disabling delivery does not reactivate old
report options.
