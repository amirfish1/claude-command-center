# Hardened deployment (reverse proxy + auth + TLS)

The stock ways to run Claude Command Center — `./run.sh` or the trial
`docker-compose.yml` — expose the dashboard with **no authentication**. CCC's
own [`SECURITY.md`](../SECURITY.md) states the trust model plainly: anything
that can reach the port can drive the full API, and the agent-spawn endpoints
run with `--dangerously-skip-permissions`. On a shared or networked machine,
and even against other local processes on your own machine, that is a large
unauthenticated attack surface.

This document describes **Layer A hardening**: put CCC behind a
[Caddy](https://caddyserver.com/) reverse proxy that enforces **TLS** and
**HTTP Basic authentication**, and remove CCC's own host-facing port so the
only reachable surface is the authenticated proxy.

## What it protects (and what it doesn't)

| Threat | Stock (`docker-compose.yml` / `run.sh`) | Hardened (this doc) |
| --- | --- | --- |
| LAN / remote machine reaching the API | Exposed if bind widened | **Blocked** unless it authenticates through Caddy |
| Browser tab on another site (CSRF) | Same-origin check only | Same-origin check **+ auth** |
| Other **process on the host** connecting directly | **Exposed** (127.0.0.1:8090, `port.txt`) | **Blocked** — no host CCC port exists; CCC is on an `internal` Docker network |
| Capability once authenticated | Full API | **Full API** — auth gates *access*, not *capability* |

The last row is the important caveat: this is an **authentication** layer, not
a sandbox. Anyone who logs in through Caddy still has the full CCC API,
including agent spawn. Protect the credentials like you would an SSH key.

There is also a functional tradeoff inherent to containerizing CCC: inside the
container it can read the host's `~/.claude` transcripts (mounted in) and drive
the kanban, but it cannot use macOS-only desktop conveniences, and spawning
host-native agent CLIs (`claude`, `codex`, …) is not wired through the
container. This profile is aimed at a **locked-down, auth-gated dashboard**,
not full host orchestration. If you need host-native spawn, that requires the
non-container path plus Layer B (a listen-socket change), which is out of
scope here.

## Files

- `docker-compose.hardened.yml` — CCC on an `internal` network (no host port),
  Caddy in front on `127.0.0.1:8443`.
- `infra/caddy/Caddyfile` — TLS (`tls internal`) + `basic_auth`, reverse proxy
  to `ccc:8090`.
- `.env.hardened.example` — credential template (copy to `.env.hardened`).

## Setup

1. **Copy the env template:**

   ```bash
   cp .env.hardened.example .env.hardened
   ```

2. **Generate a bcrypt password hash** (never store the plaintext):

   ```bash
   docker run --rm caddy caddy hash-password --plaintext 'your-strong-password'
   ```

   Copy the resulting `$2a$...` string into `.env.hardened` as
   `CCC_BASIC_AUTH_HASH`, and set `CCC_BASIC_AUTH_USER`.

   > **Important:** Docker Compose expands `${}` in the env file, so every
   > literal `$` in the hash must be **doubled** to `$$`. A hash of
   > `$2a$14$abc…` goes into the file as `$$2a$$14$$abc…`.

3. **Start the stack:**

   ```bash
   docker compose --env-file .env.hardened -f docker-compose.hardened.yml up --build
   ```

4. **Open the dashboard:** <https://localhost:8443>. Your browser will warn
   about the self-signed cert (Caddy's internal CA) — accept it once. Log in
   with the credentials from step 2.

## Verification

Run these after `up`. They prove the three guarantees: no direct host port,
auth required, auth works.

**1. CCC has no host-facing port (local-process bypass is closed):**

```bash
# Expect: NO listener on 8090. (Only Caddy's 8443 should be bound, to loopback.)
lsof -nP -iTCP:8090 -sTCP:LISTEN || echo "OK: nothing listening on 8090"
lsof -nP -iTCP:8443 -sTCP:LISTEN   # should show caddy on 127.0.0.1:8443
```

**2. Unauthenticated request is rejected:**

```bash
# -k accepts the self-signed cert. Expect HTTP 401.
curl -sk -o /dev/null -w '%{http_code}\n' https://localhost:8443/api/sessions
# => 401
```

**3. Authenticated request succeeds:**

```bash
curl -sk -u "$CCC_BASIC_AUTH_USER:your-strong-password" \
  -o /dev/null -w '%{http_code}\n' https://localhost:8443/api/sessions
# => 200
```

**4. Confirm CCC is only reachable through the proxy network**, not from the
host, by trying to reach the container directly (there is no published port,
so this must fail):

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8090/api/sessions \
  || echo "OK: no direct route to CCC from the host"
```

## Reaching it from another trusted device

By default Caddy publishes to `127.0.0.1:8443` — loopback only. To reach it
from a phone or another machine over a VPN/Tailscale, change the published
address in `docker-compose.hardened.yml` (e.g. bind the tailnet interface) and
add that origin to `CCC_ALLOWED_ORIGIN`. Remember: you are exposing an
auth-gated, full-capability control plane. Only do this on a network you
trust, and use a strong, unique password.

## Rotating credentials

1. Generate a new hash (step 2 above).
2. Update `.env.hardened`.
3. `docker compose --env-file .env.hardened -f docker-compose.hardened.yml up -d`
   (Caddy reloads with the new hash; no rebuild needed).
