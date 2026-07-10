# Security

## Threat model

Claude Command Center is a **single-user, single-host** dashboard. It is designed to run on the same machine as your editor and Claude Code sessions. The trust boundary is **loopback only** — there is no authentication, no per-user accounts, no permissions system.

The server:

- Binds to `127.0.0.1` by default (override only with `CCC_BIND_HOST` and at your own risk).
- Shells out to `gh`, `claude`, `git`, `osascript`, `tmux`, `pkood`, `vercel`, `lsof`, and `ps` on your behalf.
- Spawns headless Claude sessions with `--dangerously-skip-permissions`. Anyone who can reach the API can ask the headless Claude to read any file your user can read, write to disk, run commands, and reach the network.
- Reads Claude Code conversation transcripts under `~/.claude/projects/`.
- Writes per-user state under `~/.claude/command-center/` (renamed from `~/.claude/log-viewer/` — the server auto-migrates on first launch). These files are created with your default umask (typically world-readable on macOS). On a shared machine, run `chmod 700 ~/.claude/command-center/`.
- **Experimental ACP adapter**: The optional `ccc_acp.py` standalone script exposes a JSON-RPC bridge over stdio. If manually run, it accepts arbitrary working directories from the connecting client (e.g., your IDE) and spawns Claude with `--dangerously-skip-permissions` in those paths.

If you expose the port to the network, the LAN, or the internet, you are giving every reachable peer the ability to run arbitrary commands as your user. **Don't.**

## What we do to enforce the boundary

- **Localhost-only bind** — `server.py` binds `127.0.0.1` by default. Setting `CCC_BIND_HOST=0.0.0.0` prints a startup warning.
- **Same-origin POST check** — every `POST` is rejected unless the `Origin` header is missing (curl, programmatic) or matches `localhost` / `127.0.0.1` / `[::1]` on **any port**. The any-port match supports the multi-repo design (see `docs/superpowers/specs/2026-04-30-multirepo-design.md`), where sibling CCC servers run on their own loopback ports and the browser UI on one fetches across them. A malicious external site cannot set a loopback `Origin` (browsers set it from the page's actual URL), so the loopback wildcard stays inside the existing trust boundary — anything that can reach loopback can already run commands as you. The allowlist can be extended for non-loopback origins via three layers (all merged at startup): the `CCC_ALLOWED_ORIGIN` env var, the persisted `~/.claude/command-center/network.json` (`allowed_origins`), and Tailscale auto-detect when `CCC_TRUST_TAILNET=1` or `trust_tailnet: true` is set in the JSON. Each entry is a peer that can run commands as you — only list origins you fully trust. The **Network access…** modal in the UI writes the JSON; `POST /api/network-config` requires a localhost Origin even though other endpoints accept the broader allowlist, so a peer on a trusted network cannot expand its own trust further.
- **No wildcard CORS** — the SSE stream and JSON endpoints serve same-origin only.
- **Federation stays inside the loopback trust model** — pairing two CCC nodes (see `docs/federation.md`) opens no new listener: peer traffic reaches the remote CCC on *its own* loopback, over an already-authenticated SSH channel (or direct loopback for a second instance on the same machine). Pairing exchanges a per-peer secret stored in `~/.claude/command-center/peers.json`; every peer-facing `/api/federation/v1/*` endpoint except the read-only identity card (`hello`) and the pairing handshake itself validates it and returns 403 `unpaired_peer` otherwise. Session-bundle imports are hash-verified, staged, and confined by construction: the destination path must be an existing checkout of the same repository identity on the receiving node, transcript targets are derived by slug-encoding (which strips path separators), and traversal in bundle file names or session ids is rejected. Note that a paired peer can run commands as you — pair only machines you fully control.
- **No `/api/open` sandbox** — the "open file in OS" endpoint resolves the requested path and no longer restricts it to a specific repo or log directory, allowing all files to be opened. Relative transcript links (e.g. `renders/x.png`) that don't resolve directly fall back to a **bounded, depth-limited (≤3) reveal-only search** under the session's own worked-in directories: matches are restricted to Files-panel-safe media/doc extensions (never scripts/executables) and are **revealed in Finder (`open -R`), never launched/executed** — so this fallback is strictly narrower than the explicit-path behavior above. The one exception is **markdown**, which is plain text that cannot execute: a bounded-walk markdown hit may be opened when the link explicitly requests launch (CCC-146). Every other extension stays reveal-only.
- **No server repo switching** — `/api/repo/switch` is deprecated and returns 410. Repo-scoped APIs validate explicit `repo_path` values against known, recent, custom, or discoverable repos.
- **Subprocess discipline** — every `subprocess.run` / `Popen` call uses list-form arguments. No `shell=True`, no `eval`, no `exec`, no `os.system`.
- **Path-traversal protection** — every static-file handler resolves the target and verifies it lives under the served root before reading.

## What we don't do

- **No auth.** If you need multi-user access, fork and add it.
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

## For contributors

If you're adding a new endpoint:

1. **Use list-form subprocess args.** Never `shell=True`.
2. **Validate any path that comes from the request body or query string.** Use the pattern from `/image-cache/` and `/static/morning/`: `target.resolve()` + `Path.relative_to(base)` check before opening the file.
3. **Don't introduce wildcard CORS or weaken the same-origin check.** If you need cross-origin access, propose it in an issue first.
4. **Don't spawn subprocesses on attacker-controlled paths.** Use caution when allowing operations on unbounded file paths.
5. **Treat anything in a log file as potentially containing secrets.** Don't log raw request bodies or shell-out output if it's user-supplied.
