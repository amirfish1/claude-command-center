# Level Agency deploy notes

This fork (`LevelInteractive/lat-claude-command-center`, upstream `amirfish1/claude-command-center`) hosts CCC as an internal **prototype** at `https://ccc.dash.level.agency`.

- **Image**: `deploy/Dockerfile` (NOT the upstream root `Dockerfile`). It adds watchtower, Node, the Claude Code CLI and git, runs as a non-root user, and sets `HOME=/data`.
- **State**: `/data` is a persistent Docker volume: `~/.claude` (sessions + CCC state) and `~/repos` (clones).
- **Auth**: CCC has none. The host is gated by Traefik forward-auth (Google SSO via the dashboard). Every SSO user shares one instance and one agent identity.
- **Auto-deploy**: Dokploy watchPaths = `deploy/**`. Syncing upstream source alone does not rebuild; bump something under `deploy/` (e.g. a pinned version ARG) to ship it.
- **Upstream sync**: `git fetch upstream && git merge upstream/main`. Our changes live only in `deploy/` and this file.
- Monorepo side: `scripts/dokploy/deploy-claude-command-center.sh`, `scripts/dokploy/gen-claude-command-center-forward-auth.py`, `_env-block.sh` arm `claude-command-center`.
