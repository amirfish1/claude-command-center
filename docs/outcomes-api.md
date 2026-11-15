# Outcomes API removal in 6.0

The dormant outcomes adapter was removed in CCC 6.0. There is no outcomes
screen or enablement setting in CCC.

## Removed contracts

- `GET /api/outcomes` and `GET /api/outcomes/ID` return 404.
- `GET` and `POST /api/outcomes/settings` return 404.
- `POST /api/outcomes/{create,update,link,event}` return 404.
- `ccc spawn --outcome-id OUTCOME_ID` is an unrecognized CLI argument (exit 2).
- `POST /api/sessions/spawn` containing `outcome_id` returns 400 with code
  `unsupported_spawn_field`, even when its value is empty or null.
- Spawn no longer prepends outcome goal/criteria context, inherits an outcome
  association from a parent, or writes an association on new or deduplicated
  sessions. Responses no longer include `outcome_id`, `outcome_association` or
  `outcome_association_error`.

Remove the flag or JSON field from callers that need ordinary session spawning.
Parent-session lineage, return routing, deduplication, generic decision APIs,
session census and queue APIs retain their existing contracts. Outcome records
and settings already stored outside CCC are left untouched; upgrading CCC does
not migrate data or enable an engine.
