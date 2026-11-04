# Lane W3c — ccc recap (shareable day recap)

## Shipped
- `ccc recap [--date YYYY-MM-DD] [--out file.html|-] [--repo NAME ...] [--json]`
  generates a standalone, share-safe HTML recap of one local day's agent work:
  session/turn/commit counts, lines changed, token + agent-time totals,
  per-engine and per-model mix, and shipped items (feat/fix deliveries)
  grouped by repo.
- `GET /api/recap?date=YYYY-MM-DD&repos=a,b&format=html|json` — new server
  endpoint that reuses the productivity pipeline
  (`_productivity_known_repos`, `collect_git_commits`,
  `_productivity_turn_rows`) scoped to a single day.
- Pure builder + renderer in `productivity.py` (`build_day_recap`,
  `render_recap_html`), stdlib-only and unit-tested without importing
  `server.py`.

## Redaction (default-on)
- No filesystem paths, prompts, or session names anywhere in payload or HTML.
- Commit subjects pass through `sanitize_evidence_title` (paths/emails
  scrubbed).
- Repo names render as a stable anonymized `Private project ·<hash>` label
  unless the user passes the name (or project id) via `--repo`/`repos=`.
- Turn rows now carry `session_id` and `model` (additive fields;
  `aggregate_productivity` ignores them) so the recap can count distinct
  sessions and build the model mix without session names.

## Commits
- (see `git log` on branch `astra/W3c`)

## How to verify
- `python3 -m pytest tests/test_recap.py` — 11 tests: day filtering,
  totals/grouping, engine+model mix, sha dedupe, allowlist naming,
  anonymization, title sanitization, HTML escaping/no-leak.
- With a running CCC on this branch: `python3 ccc recap --date $(date -u +%F)
  --out /tmp/recap.html --repo claude-command-center` then open the file.
- `GET /api/recap?format=json` returns the same payload the HTML renders.

- STRETCH (partial): the "⋯ more" menu now links "Share today's recap" to
  `/api/recap?format=html`. OG image not done (no SVG→PNG tool confirmed
  available on this machine).

## Left / next
- Sessions counted = sessions with ≥1 agent turn that day (no turn → not
  counted), matching the productivity turn pipeline.

## Owner decisions
- None outstanding.
