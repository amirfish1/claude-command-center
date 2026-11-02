# Selected operational resets

Workspace > Preferences > Reset selected operational overrides reviews one scope
at a time: this CCC host, or one existing queue (including a manual queue). It
is separate from the existing browser reset. Nothing changes on open, Refresh,
Preview or Cancel. Apply requires the exact preview and unchanged configuration,
queue/default sources and model policy. The normal same-origin and dashboard
access checks apply; the API takes no filesystem paths.

Host choices replace explicitly stored supported fields with known literals:
session engine `claude`, effort blank, compaction 250K; independent worker
engine/model/effort blank and compaction 250K; or inheritance false. Leaving
inheritance restores the retained independent tuple, not factory guesses. A
linked session reset previews all materialized worker dependencies and requires
a supported saved session model source. Independent worker fields cannot be
reset while linked. Blank engine/model choices remain native runtime defaults,
not a prediction of the next actual engine/model.

Worker fallback can be turned Off without deleting its chain or local metadata.
Queue On/Off pins still win. A selected managed profile can have its known route
list emptied while retaining document metadata and other profiles; managed
launches selecting it are then unavailable until configured. Rows with extra
metadata cannot be cleared here. Fallback normalization or an unsupported
dependent replacement blocks the entire selection rather than silently editing
more configuration.

Queue choices remove the selected engine, model, effort, fallback and/or headroom
exception. Explicit fallback Off is distinct from absence. The preview shows
before/after presence plus a redacted, configuration-only projection of saved
policy after removal. Runtime availability, quota/headroom decisions, model
safeguards and ambient defaults remain unresolved. Other queue configuration,
unknown metadata and auto-drain are preserved; this does not launch workers.
Queue targets must use the existing journal’s supported safe identifier format;
other queue names remain manageable in the canonical queue editor.

Shared per-engine models have installation-dependent baselines and are excluded.
Engine visibility, headroom host policy, retained snapshots, credentials,
network/services, repository appearance, unapproved custom IDs and arbitrary
configuration deletion are excluded. Unsupported values are hidden, not offered
as reset choices. Missing, malformed or unreadable sources do not become an
empty/all-default result. Registry output is strictly allowlisted and never
contains native document copies or private paths.

Apply uses the canonical staged routing/scoped queue validators, unique-file
atomic publication and the existing bounded configuration-history journal. A
preview states whether the complete change is reversible. Local metadata can
make a native container change nonreversible even though that metadata is
preserved; the user sees that limitation before Apply. Known supported changes
can be undone using Configuration history while its source/policy guards pass.
No-op selections do not write files or history, and send no applied event.
History intent failure prevents publication; final journal failure can leave a
saved configuration with pending history, explicitly reported by the UI. There
is no cross-file/global transaction. CCC requests serialize queue/default/reset
writes, but external writers retain the final check/replace race described in
[transaction boundaries](settings-transaction-boundaries.md).

## API

`GET /api/settings/resets?scope=machine` or
`?scope=queue&target=QUEUE` returns supported fields with exact known replacement,
current presence, availability/reason, scope choices, coverage and a source
revision. This is read-only and performs no migration or runtime probe.

`POST /api/settings/resets` accepts only `action` (`preview` or `apply`), `scope`,
`target`, nonempty unique `fields`, `expected_revision`, and (for apply)
`preview_token`. Requests are capped at 32 KiB and duplicate JSON keys rejected.
Preview returns allowlisted changes, dependencies, undo coverage and a token.
Apply revalidates/recomputes the preview before publication. Invalid selections
return 400, stale previews/sources 409, unavailable save/read outcomes 503. A
successful apply includes independent `configuration_history` status. Cancellation
is local and sends no request. No API exports a whole native document.
