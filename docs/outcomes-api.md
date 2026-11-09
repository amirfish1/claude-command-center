# Outcomes API (preview, off by default)

CCC exposes a thin HTTP adapter over WatchTower's outcome records. There is no
dashboard screen for it: use the endpoints below, or WatchTower's own
`wt outcome` commands. Everything is refused until the installation setting is
enabled, and a WatchTower version with outcome support must be installed.

An outcome is a durable record of an intended result: a goal, acceptance
criteria, the next action and who owns it, plus linked tickets, sessions and
evidence. WatchTower is the authority for these records; CCC only validates
and forwards requests.

## Endpoints

These endpoints are additive:

| Endpoint | Purpose |
|---|---|
| `GET /api/outcomes/settings` | Read effective enablement and backend availability |
| `POST /api/outcomes/settings` | Persist an explicit boolean `enabled` value |
| `GET /api/outcomes` | Read outcomes, optionally filtered by `repo_path` |
| `GET /api/outcomes/ID` | Read one outcome |
| `POST /api/outcomes/create` | Create an outcome with a goal and acceptance criteria |
| `POST /api/outcomes/update` | Edit using `id`, `expected_revision`, and `patch` |
| `POST /api/outcomes/link` | Explicitly associate a supporting ticket |
| `POST /api/outcomes/event` | Request an owner review with type `owner_review` |

All POSTs use CCC's existing same-origin checks. Outcome operations are refused
when disabled or unavailable; the settings endpoint remains reachable. These
endpoints cannot write verification evidence or mark an outcome delivered.
Revision conflicts must be refreshed and reconciled rather than overwritten.

Enabling the setting also allows WatchTower to start outcome agents on its own
schedule. Money, customer sends, public publication, irreversible operations,
and existing repository approval boundaries remain in force; the setting is not
a blanket authorization for those actions.

## Linking a session

`ccc spawn --outcome-id OUTCOME_ID` attaches a new session to an outcome and
prepends the goal and finish line to its prompt. The context explains the
finish line without adding authority. The flag is rejected while the setting is
off or when the outcome is delivered, stopped or parked.
