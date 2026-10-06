# Security

## Threat model

Claude Command Center is a **single-user, single-host** dashboard. It is designed to run on the same machine as your editor and Claude Code sessions. The trust boundary is **loopback only** — there is no authentication, no per-user accounts, no permissions system.

The server:

- Binds to `127.0.0.1` by default (override only with `CCC_BIND_HOST` and at your own risk).
- Shells out to `gh`, `claude`, `git`, `osascript`, `tmux`, `pkood`, `vercel`, `lsof`, and `ps` on your behalf.
- Spawns headless Claude sessions with `--dangerously-skip-permissions`. Anyone who can reach the API can ask the headless Claude to read any file your user can read, write to disk, run commands, and reach the network.
- Reads Claude Code conversation transcripts under `~/.claude/projects/`.
- Writes into your agent config (`~/.claude/settings.json` hooks, `~/.codex/hooks.json`, `~/.claude/skills`, `~/.codex/skills`, WatchTower skill symlinks) **only after you approve each change** in the Agent config access dialog or with `ccc consent`. See "Agent config consent" below.
- Writes per-user state under `~/.claude/command-center/` (renamed from `~/.claude/log-viewer/` — the server auto-migrates on first launch). These files are created with your default umask (typically world-readable on macOS). On a shared machine, run `chmod 700 ~/.claude/command-center/`.
- **Experimental ACP adapter**: The optional `ccc_acp.py` standalone script exposes a JSON-RPC bridge over stdio. If manually run, it accepts arbitrary working directories from the connecting client (e.g., your IDE) and spawns Claude with `--dangerously-skip-permissions` in those paths.

If you expose the port to the network, the LAN, or the internet, you are giving every reachable peer the ability to run arbitrary commands as your user. **Don't.**

## What we do to enforce the boundary

- **Localhost-only bind** — `server.py` binds `127.0.0.1` by default. Setting `CCC_BIND_HOST=0.0.0.0` prints a startup warning.
- **Same-origin POST check** — every `POST` is rejected unless the `Origin` header is missing (curl, programmatic) or matches `localhost` / `127.0.0.1` / `[::1]` on **any port**. The any-port match supports multiple sibling CCC servers running on their own loopback ports, where the browser UI on one can fetch across them. A malicious external site cannot set a loopback `Origin` (browsers set it from the page's actual URL), so the loopback wildcard stays inside the existing trust boundary — anything that can reach loopback can already run commands as you. The allowlist can be extended for non-loopback origins via four layers: the `CCC_ALLOWED_ORIGIN` env var (read at startup), the persisted `~/.claude/command-center/network.json` (`allowed_origins`, re-read whenever the file changes, no restart), the origin recorded by **Phone access** in `phone-access.json` (live, see below), and Tailscale auto-detect when `CCC_TRUST_TAILNET=1` or `trust_tailnet: true` is set in the JSON (on a miss for a Tailscale-looking origin the node's own MagicDNS name is re-detected, at most once per 30 s, so a renamed machine is recognised without a restart). Each entry is a peer that can run commands as you — only list origins you fully trust. The **Network access…** modal in the UI writes the JSON; `POST /api/network-config` requires a localhost Origin **and** a request that did not arrive through a reverse proxy (none of `X-Forwarded-For`, `X-Forwarded-Host`, `Forwarded`, `Tailscale-User-Login`, `Cf-Connecting-Ip`, `X-Real-Ip`, and not a `*.ts.net` Host), even though other endpoints accept the broader allowlist, so a peer on a trusted network cannot expand its own trust further. (A tailnet peer reaching CCC through `tailscale serve` arrives from loopback and may omit `Origin`; the proxy-header check is what stops it.)
- **Phone access (opt-in, Tailscale)** — **Settings > Phone access…** runs `tailscale serve --bg --https=<port> http://127.0.0.1:<port>` so the dashboard is reachable at `https://<node>.<tailnet>.ts.net[:port]` from **your tailnet only** (Serve, never Funnel; CCC still binds loopback). CCC records the entry it created in `~/.claude/command-center/phone-access.json` (mode 0600) and, on turn-off, removes only that entry and only if it still points at CCC; serve entries it did not create are never modified. The recorded `https://` origin is trusted immediately. **This means anyone on your tailnet who can reach the node can run commands as you: there is no login.** Restrict the node with [Tailscale ACLs](https://tailscale.com/kb/1018/acls) (remember shared-in nodes and invited users), and consider the optional PIN. The admin endpoints (`/api/phone-access/{enable,disable,pin,test,status,nodes}`) and `/api/network-config` refuse any caller that arrived through a proxy or from off the machine, so a phone you let in cannot change how CCC is exposed. See [`docs/phone-access.md`](docs/phone-access.md).
- **Optional PIN gate for off-machine requests** — when a PIN is set, every request that arrives from off this machine (a non-loopback peer address, or loopback carrying reverse-proxy headers such as `X-Forwarded-For` from `tailscale serve` or a tunnel, or a `*.ts.net` Host) must present a session cookie, otherwise it gets `401` (the PIN page for HTML, `{"error":"pin_required"}` for the API). Loopback requests from this machine are never gated. Only the unlock page/endpoint and the Test button's echo probe (which returns just the nonce it was sent) are exempt. The PIN (4 to 64 characters) is stored as salted PBKDF2-SHA256 (200k iterations); unlock attempts are rate limited to 5 failures per minute across all callers; a successful unlock mints a random 256-bit token returned once as an `HttpOnly; SameSite=Strict` cookie (`Secure` over HTTPS) valid 30 days and stored only as its SHA-256; setting a new PIN revokes every session. The gate is a speed bump on a trusted network, not an internet-grade login: it has no accounts, and a forwarding-header check is only as good as the proxy in front of CCC, so do not rely on it alone to expose CCC publicly.
- **Agent config consent** — CCC never edits files outside `~/.claude/command-center/` on its own. Every such write is an item in `ccc_server/config_consent.py` with its exact diff; nothing is applied until you approve it, and the decision is stored with a hash of the proposed content in `config-consent.json`, so an update that changes a hook command or skill text is shown again instead of applied. Writes keep the file's formatting and other entries, write the real file behind a symlink, and back up the previous bytes to `config-backups/`. Declining or revoking removes only CCC's own entries. `POST /api/config-consent/{decide,revoke,notice-ack}` refuse callers that arrived from off the machine or through a proxy, so a phone-access or tunnel peer cannot approve writes to your config. Installs that predate the gate are kept (recorded as approved) and listed once with a Remove option. Not gated, because they are runtime state rather than config: CCC's peer registration row in `~/.claude/sessions/<pid>.json` (the same registry every Claude session writes; `CCC_MESSAGING_BACKEND=legacy` turns it off), and state the Codex/Claude CLIs create in their own homes when CCC launches them. See [`docs/agent-config-consent.md`](docs/agent-config-consent.md).
- **No wildcard CORS** — the SSE stream and JSON endpoints serve same-origin only.
- **Federation stays inside the loopback trust model** — pairing two CCC nodes (see `docs/federation.md`) opens no new listener: peer traffic reaches the remote CCC on *its own* loopback, over an already-authenticated SSH channel (or direct loopback for a second instance on the same machine). Pairing exchanges a per-peer secret stored in `~/.claude/command-center/peers.json`; every peer-facing `/api/federation/v1/*` endpoint except the read-only identity card (`hello`) and the pairing handshake itself validates it and returns 403 `unpaired_peer` otherwise. Session-bundle imports are hash-verified, staged, and confined by construction: the destination path must be an existing checkout of the same repository identity on the receiving node, transcript targets are derived by slug-encoding (which strips path separators), and traversal in bundle file names or session ids is rejected. Note that a paired peer can run commands as you — pair only machines you fully control.
- **No `/api/open` sandbox** — the "open file in OS" endpoint resolves the requested path and no longer restricts it to a specific repo or log directory, allowing all files to be opened. Relative transcript links (e.g. `renders/x.png`) that don't resolve directly fall back to a **bounded, depth-limited (≤3) reveal-only search** under the session's own worked-in directories: matches are restricted to Files-panel-safe media/doc extensions (never scripts/executables) and are **revealed in Finder (`open -R`), never launched/executed** — so this fallback is strictly narrower than the explicit-path behavior above. The one exception is **markdown**, which is plain text that cannot execute: a bounded-walk markdown hit may be opened when the link explicitly requests launch (CCC-146). Every other extension stays reveal-only.
- **No server repo switching** — `/api/repo/switch` is deprecated and returns 410. Repo-scoped APIs validate explicit `repo_path` values against known, recent, custom, or discoverable repos.
- **Subprocess discipline** — every `subprocess.run` / `Popen` call uses list-form arguments. No `shell=True`, no `eval`, no `exec`, no `os.system`.
- **Path-traversal protection** — every static-file handler resolves the target and verifies it lives under the served root before reading.

## What we don't do

- **No auth** beyond the optional phone-access PIN above. If you need multi-user access, fork and add it.
- **No sandbox around spawned Claude sessions.** They run with full filesystem and Bash access. Be aware of what you ask them to do.
- **No encryption at rest.** State files (`~/.claude/command-center/*.json`) are plaintext.
- **No rate limiting.** A misbehaving local script can spam the API.

## Logs may contain secrets

Spawn logs under `<repo>/.claude/logs/spawn-*.log` capture the full prompt and Claude output. If Claude reads a file containing a token or credential, it ends up in that log. The `.gitignore` excludes `.claude/logs/`, but the files are local and readable by other users on the same machine. `chmod 700` the logs directory if that's a concern.

## Reporting a vulnerability

For non-sensitive issues, open a GitHub issue. For anything that could enable arbitrary code execution, credential theft, or escape the localhost boundary, **do not file a public issue**. Email the maintainer (see `LICENSE` for the contact handle) with:

- A description of the issue.
- Steps to reproduce.
- The commit hash you tested against.

We'll respond within a week. If the report is valid we'll cut a fix release before disclosing.

## Optional outbound call: new-session repo guess

When you start a new session, CCC guesses the right folder from your first prompt. Path and folder-name matching is local and never touches the network. Only if you add a TypeSafe Jev key (Settings > BYOK, or the `JEV_API_KEY` environment variable) does CCC also send the prompt (up to 3000 characters, with likely secrets such as tokens, API keys and passwords replaced by `[REDACTED]`) plus opaque repo labels and short repo descriptions to `https://api.typesafe.ai/v1/systemone`. No key means no call. The key is used only for this call and is not passed to spawned agent sessions unless you explicitly choose that BYOK profile at spawn; failures and timeouts (3 seconds) silently fall back to the local result.

## Optional outbound call: share card upload

The Throughput page has a Share button that renders a card image (totals only, never prompts, code, paths, session names or project names). Posting to X, LinkedIn, Bluesky or Threads, or clicking Copy link, uploads that one PNG and its one-line headline (for example "2.28B tokens processed this week") from your browser to the card worker at `https://ccc-card.claude-command-center.workers.dev` (source: [`infra/card-worker/`](infra/card-worker/)). Nothing is uploaded until you click one of those buttons, Copy image and Download PNG stay fully local, and CCC's own server makes no call. The worker stores the PNG and headline under a random unguessable id, serves them at a public page so social networks can show your card, and deletes them after one year. It accepts only real PNGs of exactly 1200x630 or 1080x1080, up to 1.5 MB, at most 10 uploads per IP per hour (the rate limiter keeps a salted hash of the IP for under an hour; no IP, user agent or account identifier is stored with a card). Browser uploads are accepted only from `localhost`, `127.0.0.1` and `[::1]` origins. Anyone with the link can view the card, so share it only if you are happy to share the numbers on it. If the upload fails, CCC falls back to text plus the repo link and copies the image so you can paste it. To use a different host, set `localStorage["ccc.share.cardBase"]` to an `https://` origin.

## Private queue diagnostics

Q2 can prepare a sanitized WatchTower queue/worker snapshot for explicit private
support. Diagnostic mode sends only the editable text visible in the existing
Report a bug window; it does not append browser, session, identity, screenshot,
file, prompt, ticket-text, path, or raw-log data. The dedicated intake service is
separate from telemetry, has no list/read endpoint, and stores only a seven-day
request-ID deduplication result—not report bodies. See
[`docs/private-diagnostics.md`](docs/private-diagnostics.md) for the complete
allowlist and delivery contract.

## For contributors

If you're adding a new endpoint:

1. **Use list-form subprocess args.** Never `shell=True`.
2. **Validate any path that comes from the request body or query string.** Use the pattern from `/image-cache/` and `/static/morning/`: `target.resolve()` + `Path.relative_to(base)` check before opening the file.
3. **Don't introduce wildcard CORS or weaken the same-origin check.** If you need cross-origin access, propose it in an issue first.
4. **Don't spawn subprocesses on attacker-controlled paths.** Use caution when allowing operations on unbounded file paths.
5. **Treat anything in a log file as potentially containing secrets.** Don't log raw request bodies or shell-out output if it's user-supplied.
