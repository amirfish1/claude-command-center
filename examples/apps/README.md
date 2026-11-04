# Public marketplace examples

Each child directory is an independently installable static package. No example
runs build scripts, stores data, or calls external services.

| Package | Based on | Permission |
| --- | --- | --- |
| `effects-kit` | CCC's existing `static/fx-demo.html`, `fx.js` and `fx.css`; asset URLs made relative and sound preferences kept in memory | None |
| `session-summary` | CCC's live session census projection and L6 bridge example | `sessions.read` |
| `queue-summary` | CCC's cached queue projection and session bridge example | `queues.read` |

The two summaries show real approved data, handle empty/unavailable snapshots,
and render user-authored titles as text. Their packages contain only generic
source code; browser tests provide synthetic records.

See [Building and submitting CCC apps](../../docs/building-ccc-apps.md) for local
installation, package rules, validation and the catalog PR process.
