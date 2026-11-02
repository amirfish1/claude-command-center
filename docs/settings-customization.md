# Reviewing customization

Settings → Workspace → Preferences → Changed from defaults reads supported
saved configuration. It does not save, migrate, reset, export, or probe services.
Edit opens the existing contextual, launch-policy, or queue editor. Review and
apply changes in those editors; queue policy still requires its native preview.

The review distinguishes an explicit saved value from an effective difference.
Native controls can seed saved values, so storage presence does not establish
that a user deliberately changed them. Values matching a known baseline are
under a disclosure alongside inherited and conditional values. Shadowed values
remain visible with their current controlling source.

## Covered configuration

- This browser: the existing twelve profile preferences, plus GitHub issue
  visibility, reader text scale, the legacy workers-density key, notification
  master/category preferences and the floating call-button preference. Coding
  and Workers density use the native legacy fallback without snapshotting it
  into storage. Master notification Off shadows category preferences. The
  current lane can shadow legacy compact/wrap preferences.
- This CCC host: saved session/worker engine, model, effort and compaction,
  explicit inheritance and retained independent worker overrides, worker
  headroom, ordered worker fallback and fast/standard/deep model profiles.
  Linked worker fields are the materialized saved tuple; this review never
  silently recomputes it. Retained overrides do not control the current tuple.
- Queues: nonarchived queues, including queues with auto-drain Off, with explicit
  engine/model/effort, failure-fallback and headroom overrides. Explicit fallback
  Off differs from an absent override. Blank routing pins inherit. Manual
  queue overrides affect future manual launches, not currently running workers.

Known machine baselines match reference defaults for the supported scalar
fields. Factory model defaults depend on environment and CLI configuration;
shared per-engine model baselines are explicitly unknown and are not inspected. Queue baselines
come from the saved worker/shared configuration for that queue context.
Runtime model/effort compatibility, implicit engine selection, quota decisions
and launch safeguards remain unresolved; the existing next-launch preview is
the canonical consumer-specific view.

## Protected and excluded data

The API returns an explicit field allowlist. It omits unknown metadata,
credentials, accounts, paths and arbitrary browser storage. Model identifiers
outside published in-memory CCC records are hidden, even if a native editor
supports that custom model. Unsupported queue identifiers are hidden and cannot
be opened from this review. Configuration errors are unavailable evidence,
not an empty or all-default result. Concurrent file changes reject the read.

Excluded stores include repository branding, network and voice service
configuration, provider accounts and credentials, budgets, schedules, engine availability, feature
flags, archived queues, per-session state, operating-system voice names and
unregistered preferences. Coverage text appears in the review. This is not an
inventory of every CCC setting, and it does not expand the existing browser
profile, import, reset or history boundary.

Snapshots become stale after thirty seconds. Refresh re-reads only covered
configuration; it does not run catalog builders, processes or network probes.
Browser storage events refresh only covered browser rows. Running sessions and
workers retain their existing settings.
