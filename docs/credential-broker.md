# Request credentials through CCC

An agent can ask for a password, token, SSH passphrase or one-time code in the
dashboard. The user enters it in a masked prompt on desktop or phone, outside
the conversation. No SSH session is needed to answer.

## Agent use

Start the local CCC server, then pipe the credential straight into a program
that accepts a password on stdin. For example (replace `example-cli` with your
consumer):

```bash
ccc secret request example-service --reason "Sign in to the example service" \
  --session-id YOUR_SESSION_ID --kind password --timeout 300 --stdout \
  | example-cli login --password-stdin
```

`--stdout` is required and refuses a terminal. It writes the exact value without
an added newline; the consumer must support that format and must not echo or log
it. Never run this command bare in an agent tool call that captures stdout into
the transcript. Do not put credentials in command arguments, reasons, service
names, session IDs, shell tracing, tickets or chat. Request metadata is visible
to dashboard users. The requesting session ID is caller-reported, not verified
identity; local programs already share CCC's single-user trust boundary.

The default wait is five minutes; `--timeout` allows 1–900 seconds. Cancellation,
denial and timeout fail without printing a value. The CLI uses only a direct
loopback HTTP connection, disables environment proxies and refuses redirects.
Select a local instance with `ccc --server http://127.0.0.1:PORT secret ...`.
If the session environment is unavailable, pass `--session-id` explicitly.

## Answering and remembering

A **credential requests** panel appears automatically, including the service
alias, reason, reported session and expiry. Submit the masked value or choose
**Deny**. Requests disappear automatically after answering, cancellation or
expiry. The browser clears typed values on submission and when the page is
hidden; it does not save them in browser storage.

Values stay in server memory by default and are delivered once. Restarting CCC
loses unanswered requests and unconsumed ephemeral values. Choose **Remember for
this service on this machine** only when you want future local requests with the
same service name and kind to reuse the value automatically. One-time codes
cannot be remembered. Use a distinct alias for each account or environment.

Remembered values are stored in the CCC Vault (macOS Keychain, or the Vault's
encrypted-file fallback elsewhere), as an entry named `<service>.<kind>` (for
example `jev-api-key.password`) that also shows up in Settings > Vault. Surrounding
whitespace is trimmed on save. Nothing is written to disk unless you choose
**Remember**.

Delete every saved kind for a service and revoke its outstanding deliveries:

```bash
ccc secret forget example-service
```

Deletion removes local files; it does not guarantee erasure from backups or
memory. The broker does not log values or retrieval tokens, publish them to
activity feeds, or place them in transcripts. The consuming program controls
what happens to the delivered value afterward.

Phone submission uses the existing trusted-origin and optional phone PIN gates,
and additionally requires an HTTPS Origin when the request arrives remotely.
Use [Phone access](phone-access.md) for the dashboard; retrieval and cancellation
remain direct-local-program operations. This feature adds no listener or remote
access configuration.

## API

All payloads are JSON objects. Every response has `Cache-Control: no-store`.
All POSTs pass through CCC's existing Origin and phone PIN checks. Credential
values and retrieval tokens belong only in JSON bodies, never URLs.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/secrets/request` | Local program only, no Origin/proxy headers. Body: `name`, `reason`, optional `kind`, `session_id`, `timeout`. Returns `id`, private `token`, and `status`. |
| `GET /api/secrets/pending` | Dashboard metadata only: `requests` containing `id`, `name`, `reason`, `kind`, `session_id`, `expires_at`. Never a value or retrieval token. |
| `POST /api/secrets/resolve` | Dashboard submits `{id, value, remember: false}` or `{id, deny: true}`. Never returns the value. |
| `POST /api/secrets/take` | Local program polls with `{id, token}`. Returns `status: pending`, `denied`, or `ready`; only `ready` includes `value` and consumes it atomically. |
| `POST /api/secrets/cancel` | Local program submits `{id, token}` to revoke and discard a request. |
| `POST /api/secrets/forget` | Local program submits `{name}` to delete saved kinds and revoke outstanding requests for that service. |

The retrieval token is returned only at creation; CCC retains its SHA-256 hash.
Invalid tokens receive 403, consumed/expired/unknown requests 410, repeated
answers 409, and the limit of 128 outstanding requests returns 429. Credential
values are limited to 16 KiB of UTF-8, with whitespace preserved. Timers clear
expired memory even if the browser and agent disconnect. Pending polling works
only on this bounded request map and does not scan session history.

This slice does not install askpass hooks, intercept sudo/SSH/Git prompts, send
push notifications, integrate with OS keychains, or forward credentials through
federation or cloud relays. Programs must explicitly request and consume a
credential through this interface.
