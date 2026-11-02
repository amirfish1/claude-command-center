# Staged launch policy

Settings → Launch policy is one editor for saved local session/worker defaults,
worker inheritance, worker fallback and Fast/Standard/Deep capability profiles.
Switching the context keeps all edits in the same draft. Preview validates the
whole draft and shows effective changes, including materialized worker fields;
Apply writes them together. Editing controls, switching context, discarding a
draft and previewing do not save configuration.

Session defaults now links to this editor. Engine “default” shortcuts stage an
engine choice here. Disabling a default engine stages its replacement and the
disable operation for review. First-run setup links here instead of automatically
preselecting or persisting a default during status polling. Existing default
control IDs and fallback/profile anchors resolve to this workflow.

## Different consumers, shared workflow

- Ordinary local sessions use saved session engine/model/effort. Default routes
  allow manual model identifiers under the existing model ownership/policy rules;
  availability and authentication are not checked by this editor.
- Workers use independent defaults unless the user explicitly opts into session
  inheritance. Effective worker fields are stored for readers that consume the
  JSON directly. Saved independent overrides restore when inheritance is turned
  off; unknown override metadata remains intact. Queue pins still win.
- Worker fallback supports Claude, Codex, Kimi and Grok, one route per engine,
  at most four routes. Queue On/Off and effort pins override this machine policy.
- Managed capability profiles remain Claude-only under CCC's existing validation
  contract. Each has up to four distinct engine/model pairs, allowing several
  models on Claude. The first is primary. Later candidates require the existing
  shared worker/managed fallback switch. Profiles do not route ordinary sessions.

Editing a valid context does not normalize other legacy or future policy
sections. Unknown top-level fields, unknown capability names and metadata on
retained policies/routes survive. Removing a route intentionally removes that
row and its metadata. Unsupported stored routes stay visible and cannot be
silently converted into supported routes. Runtime quota, headroom, cooldown and
scheduling decisions remain separate; no workers or sessions are launched.

## Existing endpoint, opt-in protocol

Legacy `GET /api/spawn-defaults` and partial `POST` requests remain compatible.
The editor uses `GET /api/spawn-defaults?policy=1`, which adds `policy_revision`
and `policy_documents` and never seeds or migrates defaults. A malformed file or
concurrent read change returns 409 instead of offering an editable guess.

The editor posts an envelope to the same endpoint:

```json
{
  "policy_action": "preview",
  "expected_revision": "opaque revision from GET",
  "changes": {"reasoning_effort": "high"},
  "origins": {}
}
```

`changes` is restricted to default-route fields, inheritance, fallback, profiles
and engine disable handoffs. `model_profiles` patches only the named profiles;
unknown profiles remain stored. All existing strict route/profile validators run
before writing, along with combined engine/effort validation and changed default
model ownership/policy checks.

For edited fallback/profile lists, `origins.worker_fallback` or
`origins.model_profiles.<name>` is a parallel array of original row indexes.
Use `null` for a new row. Reordering or changing an existing row retains its
original index, preserving unknown row metadata without adding schema fields.
Invalid, duplicated or stale indexes are rejected.

Preview returns `preview_token`, effective policy and a complete before/after
change list. Apply submits the same envelope with `policy_action: "apply"` and
the token. Validation runs again; stale revisions or changed preview results
return 409. A no-op apply performs no write. A failed atomic replacement returns
503 with the previous file intact. Requests share the existing CCC defaults
lock and atomic file writer. External writers do not share that process lock;
the revision check is not a cross-process transaction or a reservation of future
runtime conditions.

The browser retains failed drafts, disables stale apply actions and exposes
errors beside the editor. Successful applies refresh normal saved-state readers;
failed/no-op applies do not dispatch an applied-state notification. Drafts are
memory-only: reloading the page discards an unapplied draft.

## Verification

`python3 -m pytest -q tests/test_routing_policy.py` exercises the real HTTP API
on temporary files, metadata preservation, distinct native consumers, revision
checks, exact override restore and failed atomic replacement. The optional native
WatchTower contract test skips when that package is unavailable.

`node --test tests/settings-routing-policy.test.cjs` runs actual Settings markup,
styles, legacy listener interception and the staged editor against a disposable
server. It covers desktop/phone batches, inheritance, preview/apply, validation,
409/503 recovery, source-row metadata and keyboard/legacy entry points. No real
configuration, engine installation, authentication or launch is exercised.
