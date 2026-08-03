# Private queue diagnostics

Q2's **Report diagnostics** action is an explicit support-report flow, separate
from CCC's anonymous telemetry. The first click opens the existing Report a bug
window with the complete report as editable text. Nothing is transmitted until
the user reviews it and clicks **Send privately**. That second click sends the
exact text visible in Details; CCC appends no hidden context.

CCC builds the report from a closed allowlist: schema/time/request ID, CCC and
WatchTower versions and service health, OS and engine kind, non-identifying queue
policy, aggregate ticket counts, opaque hashed worker IDs with idle/lifecycle
state, and a bounded list of lifecycle verbs with relative times.

It never automatically includes prompts, ticket titles/bodies/comments,
transcript or tool content, files, absolute paths, repository identity, branches,
environment/auth state, command output, usernames, hostnames, contact details,
Git identity, session UUIDs, full worker IDs, source IPs, or raw logs.

The dedicated support endpoint accepts only `schema_version`, `request_id`,
`ccc_version`, and `report_text`. It rate-limits at the edge without application
storage of the source IP, sends the report to a maintainer-only inbox using a
server-side email binding, and stores only the returned report ID for seven days
to deduplicate retries. It stores no report body and exposes no report-list or
report-read endpoint. Its auditable source is in `infra/support-worker/`.
