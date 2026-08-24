# Private diagnostics intake

This Worker is deliberately separate from CCC telemetry. It accepts only
`schema_version`, `request_id`, `ccc_version`, and the exact user-reviewed
`report_text` (maximum 48,000 characters) at `POST /v1/report`.

Application code never logs request bodies, headers, or source IPs. The source
IP is used only as an ephemeral Cloudflare rate-limit key (five submissions per
minute). A Durable Object stores only `{ok, report_id}` for seven days so retrying
the same request UUID does not send duplicate email. It never stores the report
text or inbox addresses. There is no report-list or report-read endpoint.

Delivery uses Cloudflare Email Service. The destination and sender are secrets:

```bash
npm install
npx wrangler secret put SUPPORT_TO
npx wrangler secret put SUPPORT_FROM
npm test
npm run deploy
```

`SUPPORT_FROM` must belong to a sender domain onboarded to Cloudflare Email
Service. `SUPPORT_TO` must be an allowed, verified maintainer-only destination.
