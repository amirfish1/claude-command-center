# Settings transaction boundaries

This source inventory accompanies Settings ownership guidance. It does **not**
establish that all Settings operations are atomic. An atomic file replacement,
a revision-checked configuration transaction, a recoverable audit journal and a transaction across
external processes/stores are different guarantees. The scope annotations do
not write configuration. The native full queue form, network document and feature switches use the publication path described below.

## Existing staged operational paths

| Canonical path | Existing guarantee | Boundary / limitation |
|---|---|---|
| Launch policy: default session/worker tuple, inheritance, fallback and managed profiles | `_routing_policy_request` validates the whole draft before one native defaults-file publication. Related engine/model/effort changes and materialized worker fields publish together; unknown unrelated native metadata is preserved. | One host file, not every configuration store. Another open draft must reload. Ordinary legacy callers do not acquire an arbitrary external writer's lock. |
| Scoped queue exceptions (`policy_action` on `/api/queue/config`) | `ccc_server/queue_policy.py` validates engine/model/effort/fallback/headroom changes and selected removals before one queue-file publication; checks reviewed revisions and host sources. | Selected queue policy only. Explicit Off differs from absence. Roles, worker counts, backend, tickets and service operations are outside this transaction. |
| Host profile import | `ccc_server/settings_profiles.py` re-previews through the same native policy validator; full-file/selection/source token, final source guard, one defaults-file publication and operational journal. | Host subset only. Browser applies, queue templates and credentials are separate. Unsupported source containers can make undo unavailable. |
| Operational history undo | Revalidates supported before/after fields, absence and worker inheritance snapshot against live revisions, defaults and model policy; restores through canonical native publication. | Journal intent and config are separate files: pending-intent protocol reports interrupted/unknown/finalization outcomes honestly, not a cross-file atomic commit. Unsupported/external/older edits are not blanket restore points. |
| Bulk queue routing-pin clear | `settings_insights.apply_worker_defaults` checks selected scope and revisions, clears engine/model/effort pins in memory, then replaces the queue file once. | Separate endpoint from host Apply/scoped queue history; not covered by operational undo. Subsequent log/reconcile effects are not a transaction with file publication. |

The actual policy consumers remain distinct: CCC session defaults, materialized
worker tuple consumed by worker dispatchers, queue pins, worker fallback provider
chains, and Claude-only managed profiles. One staged editor does not imply one
runtime consumer.

## Native publication and deliberate separate operations

| Editor / action | Inspected writer | Finding and next implementation boundary |
|---|---|---|
| Full native queue configuration (backend/repository/roles/drain/workers/claim types/product gate/routing/GitHub fields) | `/api/queue/config` normalizes the whole submitted form, then `settings_transactions.save_queue_form` validates GitHub shape/catch-all uniqueness, model policy before and after native alias resolution, and session links before one publication. | Unknown metadata, roles, headroom and omitted present-only fields survive; explicit removals remain removals. New queues stay drain-off; drain-on restores a parked worker count to one, matching native WT semantics. No writing WT setters or second session-link patch. Config publication is atomic; immediate reconciliation, native WT audit and nonreversible history observation are separate later effects. |
| Dashboard network binding/trust/origins | `_save_network_config` uses serialized durable publication. GET adds a document `revision` from the same byte snapshot as `stored`; POST optionally accepts `expected_revision` and returns 409 on a stale draft. Native UI supplies that revision and disables Save until a successful read. | Existing clients without a token retain serialized last-writer behavior. Known-key filtering, localhost/proxy/origin gates, hot origin reload, environment precedence and bind-change restart behavior are unchanged. Only the network document is committed; restart is a later native action. No preference journal/undo guarantee. |
| Host preview flags | `_save_feature_flag` serializes the complete read/modify/publish operation, merging one known switch into the native object. | Concurrent cooperating CCC processes retain unrelated switches and unknown metadata. Invalid/nonobject sources are rejected instead of overwritten. Effective env/file/default precedence and old boolean coercion remain. Independent switches are not one multi-switch or service transaction; no operational history promise. |
| Voice call defaults | `ccc_server/realtime_voice.py:voice_config_save` sanitizes whole patched config under a lock and replaces a temp file. | One host config publication; no optimistic stale-draft check, operational history or transaction with browser visibility. Already running calls retain their configuration. No cross-scope claim. |
| Hands-free operator keys and process Start/Stop | `_save_car_mode_config` replaces the operator config; Start/Stop is a separate native process action. | Saved keys and process state are intentionally distinct operations. Save/Start is not one atomic transaction; environment/external speech engine selection remains external. Do not silently restart an operator after saving. |
| Provider credentials and named credentials | Existing secure backend plus separate metadata/index writes (`byok.py`, `vault.py`, `secret_store.py`); explicit provider-to-named copy creates another entry. | Multiple secure/index stores and external keychain effects are not one preference transaction; no operational journal/undo promise. A future repair must preserve write-only APIs and credential lifecycle, not copy secrets into a preference journal. |
| Free-router provider onboarding / key registration / strategy / Start/Stop | `free-settings.js` can call router key validation followed by admin key registration; proxy admin calls operate on another service. | Independent service calls can partially succeed. The router needs its own batch/compensation contract before a global atomic claim. Host launch policy and credentials stay separate; no new implicit retries/actions here. |
| Phone sharing, federation pairing/mapping, agent-config consent | Native service/peer operations; consent applies across reviewed integration files and notice state. | Explicit multi-step/external effects; not a launch-policy transaction. Native target, partial/error results and confirmation remain authoritative. Scope help names this ownership rather than claiming rollback. |
| Browser profile/reset/undo and notification policy | Browser storage adapters validate first; workbench and notification policy attempt rollback on failed local writes. | `localStorage` does not provide a multi-key transaction or cross-tab/process atomic commit. Browser+host is explicitly separate. Session/list/voice presentation preferences and their related convenience writes do not acquire a global host transaction. |
| Repository avatar | `repo_avatars.save` replaces or removes the selected host avatar record and invalidates cache. | One repository record, separate from source-repository contents, browser display refresh and profile/history transactions. |
| Usage-limit recovery, spare-quota Start, engine install/auth/test/update and System service operations | Existing native runtime handlers. | Actions affect real sessions/processes/external apps. They are not configuration-field batches; no rollback implied by Settings ownership metadata. |

## Publication and concurrency contract

`ccc_server/settings_transactions.py` reuses the existing advisory directory
lock and directory-sync helper. Each publication encodes the complete document,
writes a unique temporary file in the same directory, preserves an existing
file's mode (0600 for new files), flushes/fsyncs it, rechecks the original source,
then replaces once and fsyncs the parent directory where supported by the OS. Failure before/at replace
leaves the previous document intact and removes the temporary file. A directory
sync failure **after** replace returns a saved-but-durability-unconfirmed warning,
not a false claim that publication failed. Network responses include the saved
revision/warnings; queue responses include warnings and independent history
status; feature responses include warnings.

CCC queue HTTP handlers already share the queue-settings request lock. Full-form
saves additionally cooperate across CCC processes through the advisory directory
lock. Model alias resolution uses the explicit engine or a read-only saved
worker-engine snapshot, rechecked before publication. If neither is configured,
the alias stays literal for the installation-dependent native worker resolver;
no guessed engine is stored. Existing WT pure canonicalizers/policy readers are
used when available; without that optional config module, normalized model IDs
retain the existing pass-through contract. Native WT readers consume the same
JSON schema. WT's optional audit helper is called only when its resolved config
path matches the published document; a mismatch is reported and never written.

External WT/manual writers do not take CCC's advisory lock; the final revision
check detects earlier edits, **not** an edit in the final check/replace interval.
Other legacy CCC queue operations share the in-process request lock but do not
all take this cross-process advisory lock. Old tokenless network clients also
cannot detect a draft that became stale before their request began. These are
explicit concurrency limits, not a cross-service transaction guarantee.

Full-form operational history remains an after-publication, allowlisted,
nonreversible observation. Its broader native fields and service effects are not
supported policy undo, and there is no pending-intent/crash replay guarantee for
this form. Failed history or immediate reconciliation does not turn a successful
config publication into a failed-save response. Native service reconciliation,
WT audit metadata, restart and other stores cannot be rolled back atomically
with the document. The remaining separate operations above retain their own
boundaries; this milestone does not make every Settings workflow atomic.

Selected operational resets now share the routing/scoped queue publication and
pending-intent journal boundaries. Queue/default/reset HTTP requests take the
same CCC request lock. Reset previews bind the selection, target, native
candidate and host/queue/model-policy sources; apply recomputes this before
publication. Scoped queue publication also checks the journal source guard
immediately before replace. Host replacement versus queue absence, dependent
worker tuples and undo availability are shown explicitly. This does not add a
global reset or cross-scope transaction; see
[selected operational reset coverage](settings-operational-resets.md).
