# Security

## Threat model

Claude Command Center is a **single-user, single-host** dashboard. It is designed to run on the same machine as your editor and Claude Code sessions. The trust boundary is **loopback only** — there is no authentication, no per-user accounts, no permissions system.

The server:

- Binds to `127.0.0.1` by default (override only with `CCC_BIND_HOST` and at your own risk).
- Shells out to `gh`, `claude`, `git`, `osascript`, `tmux`, `pkood`, `vercel`, `lsof`, and `ps` on your behalf.
- Spawns headless Claude sessions with `--dangerously-skip-permissions`. Anyone who can reach the API can ask the headless Claude to read any file your user can read, write to disk, run commands, and reach the network.
- Reads Claude Code conversation transcripts under `~/.claude/projects/`.
- Writes per-user state under `~/.claude/command-center/` (renamed from `~/.claude/log-viewer/` — the server auto-migrates on first launch). These files are created with your default umask (typically world-readable on macOS). On a shared machine, run `chmod 700 ~/.claude/command-center/`.

If you expose the port to the network, the LAN, or the internet, you are giving every reachable peer the ability to run arbitrary commands as your user. **Don't.**

## What we do to enforce the boundary

- **Localhost-only bind** — `server.py` binds `127.0.0.1` by default. Setting `CCC_BIND_HOST=0.0.0.0` prints a startup warning.
- **Same-origin POST check** — every `POST` is rejected unless the `Origin` header is missing (curl, programmatic) or matches `localhost` / `127.0.0.1` / `[::1]` on the server's port. This blocks CSRF from any browser tab on an unrelated site. The `CCC_ALLOWED_ORIGIN` env var (comma-separated origins) opts additional hostnames into the allowlist for trusted-network access (Tailscale, VPN). Each entry there is a peer that can run commands as you — only list origins you fully trust, and pair with `CCC_BIND_HOST` so the bind surface matches what you're allowing.
- **No wildcard CORS** — the SSE stream and JSON endpoints serve same-origin only.
- **`/api/open` sandbox** — the "open file in OS" endpoint resolves the requested path under `REPO_ROOT` or `LOG_DIR` and rejects anything outside. Default action is `open -R` (reveal in Finder), not launch.
- **`/api/repo/switch` allow-list** — repo switching only accepts paths the picker would offer (anything under `~/` with a `.git/` or `.claude/` directory).
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
4. **Don't spawn subprocesses on attacker-controlled paths.** If you must, sandbox the path against `REPO_ROOT` first (see `/api/open`).
5. **Treat anything in a log file as potentially containing secrets.** Don't log raw request bodies or shell-out output if it's user-supplied.
