# Settings ownership and effect

**Where this applies** accompanies each surviving Settings section and canonical
contextual editor. Expand it to see the controls' owner, affected context,
when changes take effect and restart requirements. Mixed sections explain each
family separately; native voice tasks have their own compact guides. They do not
offer unsupported scope selectors. Existing queue,
repository and session selectors remain the authority for the target. Scope help
never copies a path, credential, account name, machine identity or control value.

The shared catalog is `static/settings-scope.js`. Each family has stable `id`,
`selector`, `scope`, `owner`, `impact`, `restart` and `source` metadata. The module
adds `data-settings-scope` and appends an accessible description without replacing
native descriptions, IDs, listeners, values or persistence. Nodes can move between
phone and desktop homes. `window.cccSettingsScope.describe(control)` returns the
same metadata used by the guide; `audit()` reports unclassified controls by
control ID and section, never value. An unknown family gets **Scope not verified**
rather than a fabricated browser/host claim. New controls within an existing
store-specific family inherit its description; a new family must be reviewed.

Scope labels distinguish this browser, this CCC host, the selected repository on
this host, selected queue, selected session, one-off inspection/action, and a
named external application/service. Session reading choices are stored in this
browser, not in a shared host session preference. The host's secure store owns
saved credentials; credential presence does not establish account validity.

## Source inventory

This inventory covers the Settings pane and the canonical contextual preference
editors it links to. Each row lists all controls in a store/consumer family,
including dynamic repetitions, followed by the native writer and reader. The
catalog's selectors are the executable mapping; labels are not used to infer
ownership. No setting is moved into a new editor or new store by this work.

| Family / controls | Owner and effect | Native persistence / consumer trace |
|---|---|---|
| Theme and font segments; Simple mode; diagnostic mode and TTS cache markers; Live variant; per-folder chips; issue visibility; separate tabs; queue sidebar visibility; density, compact rows, row style/spacing/delineation; appearance/layout/list resets | Browser site storage; changes presentation here, not dispatch or other browsers. Some startup-only consumers require reload. | `static/app.js`: `settingsPreferenceClick`, `setSessionDensity`, `renderConvRowStylePalettes`, appearance/layout resets; `static/settings-context.js` keeps native nodes. |
| Reader A-/A+/Reset, including pane/status-rail and conversation-panel controls | Browser-wide reader scale across conversations and the conversation panel; **not** per-conversation. | `static/app.js`: `ccc-conv-font-scale`, `applyConvFontScale`; contextual Reset removes the same key. |
| Compact/Normal transcript controls, including phone copy | Browser-wide transcript presentation, not a live engine preference. | `static/app.js`: `setConvViewMode`, `ccc-conv-view`/legacy verbose adapter. |
| Conversation background palette | Conversation/session aliases in this browser's background map; presentation only. Popout overrides can be transient. | `static/app.js`: `conversationBgKeysForPane`, `persistConversationBgForPane`, `ccc-conv-bg-by-conversation`. |
| Choice evidence disclosures and engine setup/return links in Launch policy and queue policy | One-off cached inspection/navigation; saved host/queue policy and drafts remain unchanged. Setup refresh/install/authentication are separate explicit actions. | `static/choice-eligibility.js`; `static/engine-connection.js`; [choice evidence boundaries](settings-choice-eligibility.md). |
| Notification policy selector; notification enabled/tasks/digest/milestones; completion sounds; leftover offers | Browser choices; turning off reminders does not stop jobs. Sound enable can play a preview. | `static/notify.js`: policy and category storage/readers; `static/app.js`: sound helpers; `static/leftover.js`: `setEnabled`. |
| Request browser notification permission | External browser/OS action, not a saved CCC toggle. | `static/app.js` permission button; Notification API. |
| Monthly Claude/Codex subscription-cost inputs | Browser-only estimate inputs, not provider billing or plan limits. | `static/app.js`: `CCC_MONTHLY_SUBSCRIPTION_PRICE_KEY`, `CCC_MONTHLY_CODEX_PLAN_PRICE_KEY`, usage renderers. |
| Floating/mobile call shortcut and hidden compatibility controls | Browser visibility only; hiding the shortcut does not disable voice or end a call. | `static/voice-setup.js`: access composition; `static/app.js`: voice shortcut keys/getters/setters. |
| Default Speak output voice | Browser default; session choices take precedence. | `static/app.js`: `TTS_VOICE_DEFAULT_KEY`, `ttsVoiceForPane`, default voice select. |
| Reader session voice stepper | Session-keyed map in this browser; not the host live-call voice. | `static/app.js`: `TTS_VOICE_BY_SESSION_KEY`, `setTtsVoiceForPane`, `renderTtsVoiceStepper`. |
| Live-call voice, paid fallback/profile, duration, silence timeout, transcript retention, Save | Host call defaults for later calls; running calls keep their call configuration. Browser shortcut compatibility checkbox stays separate. | `/api/voice/config`; `ccc_server/realtime_voice.py`: `voice_config_save`, native call startup. |
| Operator Anthropic/Deepgram credential fields and Save | Host operator credential store; native blank handling retained. Speech engine can be environment-managed. Running process does not get new launch-time keys automatically. | `server.py`: `_save_car_mode_config`, `_car_mode_start`; native form behavior unchanged. |
| Operator Start/Stop; offline speech Install/Repair | Explicit host process/install actions. Offline output is not offline dictation or a readiness assertion. | `/api/car-mode/start`, `/stop`; `ccc_server/local_speech.py`; native installers. |
| Repository selector, avatar upload/file/reset | Selected repository's avatar record **on the host**, used across sessions and browsers served by that host; not a browser-only override. | `/api/repo/avatar`; `ccc_server/repo_avatars.py`: `save`, `avatar`; `static/app.js`: `saveRepoAvatar`. |
| Launch policy context, session/worker engine/model/custom model/effort/compaction, inheritance, fallback enable/routes, managed profile routes, Add/Remove/Reorder, Reload/Preview/Apply/Discard | Host operational draft; related fields publish together after explicit preview. Future matching launches; queue pins win and running work is unchanged. Managed profiles remain Claude-only; worker fallback keeps its distinct consumer. | `static/settings-routing-policy.js`; `server.py`: `_routing_policy_request`; `ccc_server/routing_policy.py`; native CCC/worker consumers. |
| Engine availability switches | Host picker/default configuration, not installation or authentication. | `static/app.js`: `[data-eng-enable]` handler; native `/api/spawn-defaults` save. |
| Automatic CLI updates | Host updater preference shared by dashboard/worker hourly loops; does not undo an in-flight update. | `ccc_server/hermes.py`: `_set_engine_auto_update`, `_engine_auto_update_enabled`, updater state. |
| Engine selection/status/filter/expand/copy, install/login/verify/update, Continue/Make default; optional Tailscale first-run step links | Selection/inspection or explicit native host action. Authentication belongs to the CLI/provider; Make default enters staged policy. | `static/engine-connection.js`, native engine hub in `static/app.js`; install/auth/update APIs. |
| Connection purpose/Add/Refresh/Done and compatibility links | Directory navigation/inspection; opening an editor saves nothing. | `static/service-connections.js`. |
| Provider profile/provider/key inputs, Save/replace/remove actions | Existing host provider credential backend. Named profile selected by supported consumers; Save does not select a default or verify an account. | `ccc_server/byok.py`, `ccc_server/secret_store.py`, native provider editor/launch injection. |
| Named credential metadata/value/form Save/Cancel, entry manage/remove, Copy provider credential | Existing host named-credential backend. Copy makes an independent entry; value remains write-only over HTTP. | `ccc_server/vault.py`, `ccc_server/secret_store.py`, native vault editor/CLI consumer. |
| Network bind-all, trust-tailnet, extra origins, Save & restart | Host listener/trust configuration; environment overrides can prevail. Affects host clients, not just current browser. | `server.py`: `_save_network_config`, `_resolve_runtime_network`; native Save/restart path. |
| Phone wizard local/peer target steps, sharing enable/disable, installation/sign-in links | Named local/paired host owns sharing; external Tailscale application owns authentication. Not a generic browser switch. | `static/phone-access.js`, `ccc_server/phone_access.py`; native wizard retains target/status. |
| Node self-name, pairing mode/host/port/name, Pair/Unpair/peer controls, repo identity/local mapping | This host's federation identity/trust/mapping records. Pairing can involve the selected peer; its own configuration stays there. | `static/app.js`: federation handlers; `server.py` federation endpoints/native stores. |
| Scoped queue Override/Follow default, engine/model/custom model/effort/fallback/headroom, Refresh/Preview/Apply, other-config link | Named queue's dispatcher policy. Unset and explicit Off differ; future decisions change, running workers retain their launch tuple. | `static/queue-policy.js`; `ccc_server/queue_policy.py`; scoped `/api/queue/config` protocol. |
| Full native queue name/workers/backend/repository/engine/model/effort/roles/drain/claim types/product gate/fallback/GitHub repo/assignee/queue label, Save/Cancel | Named queue's WatchTower/dispatcher configuration; external ticket backends retain ownership of their records. The full form publishes once; reconciliation and nonreversible history observation stay separate from the scoped policy transaction. | `static/app.js`: `openQueueManager`; `/api/queue/config` full-form branch; `ccc_server/settings_transactions.py`. |
| Cross-queue report selection/preview/confirm/refresh/cancel and per-queue Edit/Inspect links | Explicitly selected queues. Confirm clears only engine/model/effort pins; other policies remain. This is a separate batch action, not part of host Apply/history. | `ccc_server/settings_insights.py`: `apply_worker_defaults`; `/api/settings/queue-engines/apply`. |
| Preview feature switches | Host feature gate overrides with environment/query precedence; native status names effective source. Hiding a UI gate does not stop an existing service. | `ccc_server/log_parse.py`: `_save_feature_flag`, `_resolved_feature_flags`; `/api/features/flag`. |
| Free-model strategy selector | External configured free-router's strategy; not host launch defaults or queue pins. | `static/free-settings.js`: strategy action; `ccc_server/free_settings.py`: admin relay. |
| Free-router provider/key Add/enable/remove/pause/keyless controls; embedded credential wizard and external sign-up links | Router/provider-specific stores and explicit provider actions; separate from host provider profiles/named credentials. | `static/free-settings.js`, `static/free-key-wizard.js`, `static/domestic-providers.js`; router/proxy endpoints. |
| Free-router Install/Start/Stop, report ranges/More/links | Explicit runtime/install actions or tab-only inspection. Stop can break future routes pinned to that router; does not rewrite host policy. | Native free-router handlers and `static/free-settings.js`. |
| Spare-quota plan/repository selection | Tab's task context; last folder remembered in this browser. No host default or automatic launch. | `static/leftover.js`: `panel`, `selectedId`, `ccc-leftover-repo`. |
| Spare-quota Start/approve/dismiss; usage-limit selection/Continue/Switch/Arm/Always/hide | Explicit selected task/session/queue actions; arming belongs to selected sessions, hide is browser-local. Not global worker defaults. | `static/leftover.js`, `static/fleet-failover.js`, `static/limit-failover.js`; `ccc_server/free_failover.py`. |
| Browser profile JSON/file/refresh/export/import/reset/latest undo/preview/apply/cancel | Existing registered browser subset and local ledger; Apply reloads this browser. | `static/settings-preferences.js`; `ccc_server/settings_preferences.py` registry and adapters. |
| Host profile field selection/JSON/file/inspect/export/preview/apply/cancel/refresh/error retry | Allowlisted host operational policy/journal. Browser apply is separate; no queue template transfer. | `static/settings-profiles.js`; `ccc_server/settings_profiles.py`; native staged publication. |
| Operational history refresh/record details/undo preview/apply/cancel | Selected recorded host or queue entry; entry names scope/diff. Browser ledger separate; unsupported/older/external writes cannot be assumed undoable. | `static/settings-history.js`; `ccc_server/configuration_history.py`. |
| Changed-from-defaults refresh/Edit/Back | Read-only multi-scope review; each entry names its owner/activity. Edit navigates to canonical editor without applying anything. | `static/settings-changes.js`; `ccc_server/settings_changes.py`. |
| Capability inventory/setup gates, health refresh/repair/back, next-launch context/refresh, voice refresh/task links | Inspection/navigation; native target owns later edits/actions. No fake enable toggle or guessed readiness. | `static/settings-capabilities.js`, `static/settings-health.js`, `static/settings-launch-preview.js`, `static/voice-setup.js`. |
| Agent config consent decisions/apply/revoke/notice/snooze | Reviewed external agent integration files; browser snooze is local. Distinct from launch policy/history. | `static/config-consent.js`; `ccc_server/config_consent.py`. |
| Settings destinations/search/close/context links/legacy redirects | Tab navigation, not saved preference scope. | `static/app.js` grouping/search; `static/settings-context.js`. |
| Retired Settings tool/help/service/report entries | Explicit actions now in existing More/Help/System/Stats. They keep native handlers and do not become preferences. | `static/settings-actions.js`; native tool/service handlers. |

Selected operational reset controls additionally use the `operational-resets`
family in the shared catalog. Their scope is the host or the queue explicitly
selected in that workbench; preview/cancel are read-only and apply affects future
matching launches through the native operational journal. Browser reset remains
its own browser family. The catalog does not invent a reset for other controls.

## Deliberate exclusions and safeguards

This is an ownership inventory, not export/history/reset coverage. It does not
widen either profile schema or the operational journal. Scope metadata carries
no secret values or local identifiers and performs no API/storage read, write,
service check or permission request. It does not add target selectors where a
consumer has no such concept.

General tools (terminal, annotations, worktrees, search, fleet viewers), help,
usage reports and runtime controls remain actions, not settings. Their existing
entry points are preserved and classified as actions when reached from Settings.
Their unrelated internal task forms are outside this preference inventory.
External sign-in pages, browser/OS permission dialogs, CLI prompts, third-party
service configuration and optional plugin-only pages retain native ownership;
CCC cannot assert their effective state or annotate an external application's UI.
Credential requests remain the secure broker's explicit one-time delivery flow,
not a persistent preference editor. Environment-only values have no invented
editor or scope switch. Unsupported extensions/new families are visibly marked
unverified until reviewed.

Regression checks use the actual Settings markup, grouping/search functions and
contextual/voice/connection composition, plus source-derived optional-control
templates. Unclassified native known controls fail the audit test. Existing
native browser suites remain the behavior checks for their own editors; source
template coverage is not an assertion that a remote service has been exercised.
