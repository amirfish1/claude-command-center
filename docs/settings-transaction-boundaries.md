# Settings transaction boundary audit

This audit accompanies the ownership inventory. It does **not** establish that
all Settings writes are atomic. An atomic file replacement, a revision-checked
configuration transaction, a recoverable audit journal and a transaction across
external processes/stores are different guarantees. No new writer is introduced
by the scope annotations.

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

## Remaining gaps and deliberate separate operations

| Editor / action | Inspected writer | Finding and next implementation boundary |
|---|---|---|
| Full native queue configuration (backend/repository/roles/drain/workers/claim types/product gate/routing/GitHub fields) | Legacy full-form branch of `/api/queue/config` in `server.py` calls `ensure_entry` and multiple WatchTower setters, then other effects. Fallback direct JSON branch has different guarantees. | **Coupled partial-save gap.** A later setter can fail after earlier writes. Scope help explicitly calls this a legacy full-form boundary. A future change needs a canonical validated WatchTower-compatible batch contract and isolated failure tests; do not call it atomic based on CCC's request lock or scoped-policy implementation. |
| Dashboard network binding/trust/origins | `server.py:_save_network_config` uses direct `write_text`; native Save may restart. | **Coupled file-durability gap.** Multiple fields form one document but publication is not atomic replace/revision checked. Remediation needs explicit network/security review and temporary-state failure checks; no network writer changed in this inventory task. |
| Host preview flags | `ccc_server/log_parse.py:_save_feature_flag` reads overrides then directly writes the file. | **Publication/concurrency gap.** Each switch is an independent save, but read-modify-write can lose another change and interruption can truncate. Future work should use canonical atomic replacement/serialization without promising a multi-switch or service transaction. |
| Voice call defaults | `ccc_server/realtime_voice.py:voice_config_save` sanitizes whole patched config under a lock and replaces a temp file. | One host config publication; no optimistic stale-draft check, operational history or transaction with browser visibility. Already running calls retain their configuration. No cross-scope claim. |
| Hands-free operator keys and process Start/Stop | `_save_car_mode_config` replaces the operator config; Start/Stop is a separate native process action. | Saved keys and process state are intentionally distinct operations. Save/Start is not one atomic transaction; environment/external speech engine selection remains external. Do not silently restart an operator after saving. |
| Provider credentials and named credentials | Existing secure backend plus separate metadata/index writes (`byok.py`, `vault.py`, `secret_store.py`); explicit provider-to-named copy creates another entry. | Multiple secure/index stores and external keychain effects are not one preference transaction; no operational journal/undo promise. A future repair must preserve write-only APIs and credential lifecycle, not copy secrets into a preference journal. |
| Free-router provider onboarding / key registration / strategy / Start/Stop | `free-settings.js` can call router key validation followed by admin key registration; proxy admin calls operate on another service. | Independent service calls can partially succeed. The router needs its own batch/compensation contract before a global atomic claim. Host launch policy and credentials stay separate; no new implicit retries/actions here. |
| Phone sharing, federation pairing/mapping, agent-config consent | Native service/peer operations; consent applies across reviewed integration files and notice state. | Explicit multi-step/external effects; not a launch-policy transaction. Native target, partial/error results and confirmation remain authoritative. Scope help names this ownership rather than claiming rollback. |
| Browser profile/reset/undo and notification policy | Browser storage adapters validate first; workbench and notification policy attempt rollback on failed local writes. | `localStorage` does not provide a multi-key transaction or cross-tab/process atomic commit. Browser+host is explicitly separate. Session/list/voice presentation preferences and their related convenience writes do not acquire a global host transaction. |
| Repository avatar | `repo_avatars.save` replaces or removes the selected host avatar record and invalidates cache. | One repository record, separate from source-repository contents, browser display refresh and profile/history transactions. |
| Usage-limit recovery, spare-quota Start, engine install/auth/test/update and System service operations | Existing native runtime handlers. | Actions affect real sessions/processes/external apps. They are not configuration-field batches; no rollback implied by Settings ownership metadata. |

## Delivery status

Ownership guidance is a frontend composition milestone. The full native queue
form, network config publication and feature-flag writer are confirmed gaps, not
completed atomic-edit work. External service and secure-store sequences require
separate explicit boundaries. This audit makes those limits reviewable and
avoids changing risky writers merely to count an atomicity milestone complete.
