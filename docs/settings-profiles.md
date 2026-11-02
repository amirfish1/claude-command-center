# Portable preference profiles

Settings → Workspace → Preferences has separate browser and host profile
controls. Neither imports automatically. Loading a file or previewing an import
changes no configuration or history. Keep files private if you add your own
information to them; CCC exports only the supported fields described here.

Browser profiles retain their version 1 `ccc-preferences` format and supported
browser storage adapters. Their Apply action saves browser history and reloads
this browser. Host profiles use a separate format and transaction. Applying one
scope never applies the other; there is no cross-scope atomic transaction.

## Host profiles

Expand **Portable host profiles**, select the explicitly saved fields to export,
and download the JSON. Automatic defaults are not resolved for export. Missing,
unsupported or nonportable fields are unavailable, with a reason. Linked workers'
materialized fields are not exported: select the session fields and worker link
instead. Independent worker snapshots remain local to the receiving host.

To import, paste JSON or choose a file, then **Inspect host profile**. All supplied
known fields start selected. Explicitly uncheck any field you want to omit;
unsupported known values must be corrected or unchecked. Unknown fields or
metadata are rejected for the whole file. Preview checks selected fields against
the receiving host's current engine, model and effort policy. It lists currently
saved values, replacements, local dependencies, omitted fields and undo coverage.
Cancel makes no changes. Apply uses the same staged validation, atomic
configuration publication and journal as Launch policy. Running work is unchanged;
future matching launches use the saved values. Queue pins still take precedence.

A minimal file is:

```json
{
  "format": "ccc-operational-preferences",
  "version": 1,
  "scope": "machine",
  "values": {
    "auto_compact_k": 300,
    "worker_inherit_session": true
  }
}
```

The exact envelope is required. Files are limited to 128 KiB. Duplicate JSON
properties, nonfinite numbers, unknown versions and extra envelope properties
are rejected. The values map must be nonempty. Only explicitly selected fields
are applied; there is no profile activation, autosave or configuration overwrite.

## Supported saved fields

- `engine`, `reasoning_effort`, `auto_compact_k` for session defaults.
- `worker_inherit_session`, `worker_engine`, `worker_model`,
  `worker_reasoning_effort`, `worker_auto_compact_k` for workers.
- `models.<engine>` for individual registered engines, without transferring the
  whole shared model map.
- `worker_fallback`: an `enabled` boolean and `models` array of exact
  `{engine, model, effort}` rows.
- `model_profiles.fast`, `model_profiles.standard`, `model_profiles.deep`:
  individual `{models: [...]}` managed profiles with the same exact row shape.
  Managed profiles retain their native Claude-only restriction; worker fallback
  supports its native engine set. A maximum of four rows is portable.

All values are typed. Model identifiers must be in CCC's published in-memory
model records or be supported default literals; arbitrary custom identifiers
are excluded. Native validation also checks engine/model compatibility, effort,
and current model policy. Literal `auto`, `default` and empty/default values
retain their meaning on the receiving host; they do not transport environment
resolved values or assert that an engine is installed or authenticated. A
receiving host can reject a field exported by a different CCC version.

A linked receiving worker cannot accept independent worker fields unless the
selection explicitly includes `worker_inherit_session: false`. Opting in records
that host's independent overrides using the native inheritance rules. Preview
shows materialized worker values and retained local snapshot changes; the
snapshot is never a portable field.

## Preservation and safety boundaries

Credentials, accounts, service connections, local paths, machine identifiers,
queue names and templates, queue headroom flags, engine visibility, retained
worker snapshots and other stores are not exported or imported. Browser
contextual preferences outside the existing browser registry are also outside
these formats. This is a supported preference transfer, not a configuration
backup or a promise to cover every setting.

Unknown local configuration remains untouched. Route document metadata is
preserved; row metadata follows matching fallback engine or matching managed
engine/model identity. Imports reject ambiguous matches or deletion of unmatched
rows containing unknown metadata. The native policy editor handles those cases.
Preview hides unsupported local values and metadata rather than returning them
in a response. No catalog refresh, network validation, installation, login,
launch, permission prompt or service operation is performed.

Preview tokens cover the full file, selection, saved policy and default/model
policy sources. Apply revalidates and rejects stale previews, including changes
to unrelated local metadata. CCC rechecks sources before native publication;
external writers do not share a multi-file transaction or lock with CCC. Refresh
and preview again after a conflict. Existing open policy drafts must be reloaded
before another edit; successful applies refresh the canonical launch controls.

History stores only approved nonsecret operational diffs. Imports with initial
file creation or changes to nonportable native containers can be nonreversible;
preview says so. Supported imports can be undone from operational configuration
history with the normal source and revision checks. There is no backfilled
history. The configuration file and journal are not one atomic file transaction:
journal intent failure prevents publication; a configuration failure is not
reported as success; finalization failure reports the saved configuration with a
pending-history warning. History reads recover a pending intent without writes.

## API

`GET /api/settings/profiles` returns safe field metadata, export availability,
explicit portable saved values, coverage and a source revision. Unreadable or
malformed sources return unavailable, never an empty/all-default result.

`POST /api/settings/profiles` accepts these actions:

- `export`: `fields` and `expected_revision`; returns a selected `profile` object.
- `inspect`: `profile` as a JSON string; returns known field metadata and portable
  value eligibility, without echoing unsupported values.
- `preview_import`: JSON-string `profile` and selected `fields`; returns sanitized
  actual `changes`, `omitted`, `undo_supported` and `preview_token`.
- `apply_import`: the same profile and selection plus `preview_token`; returns
  `applied`, sanitized changes, omissions and native `configuration_history`.

Validation failures return 400, stale sources return 409, and unavailable sources
or unconfirmed writes return 503. POST uses CCC's existing origin protection.
The request envelope is bounded separately to allow JSON string escaping.
