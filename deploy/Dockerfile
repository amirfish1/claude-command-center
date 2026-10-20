# Level Agency hosting overlay for Claude Command Center (CCC).
#
# Lives under deploy/ so upstream's root Dockerfile stays untouched and
# upstream syncs never conflict. Dokploy builds THIS file (context = repo root)
# and only rebuilds on changes under deploy/** (watchPaths).
#
# Adds what upstream's trial image lacks for a hosted prototype:
#   - watchtower (CCC's queue engine — a hard import dependency despite the
#     "zero dependencies" README badge), pinned to a commit
#   - Node + the Claude Code, Codex (OpenAI) and OpenCode CLIs (pinned) + git.
#     Gemini runs through OpenCode (google/* models): CCC cannot spawn the
#     Gemini CLI (its "gemini" spawn alias routes to Antigravity's `agy` CLI)
#   - a non-root user (claude refuses some permission modes as root)
#   - HOME=/data on a persistent volume (~/.claude sessions + cloned repos)
#
# Auth: none in CCC. ccc.dash.level.agency is gated by Traefik forward-auth
# (Google SSO) — see scripts/dokploy/gen-claude-command-center-forward-auth.py
# in the monorepo.

FROM node:22-bookworm-slim AS node

FROM python:3.12-slim

ARG WATCHTOWER_REF=e764a2240ab01d96024645a6c490c91024458bda
ARG CLAUDE_CODE_VERSION=2.1.305
ARG CODEX_VERSION=0.163.0
ARG OPENCODE_VERSION=1.19.3

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/New_York \
    PORT=8090 \
    CCC_BIND_HOST=0.0.0.0 \
    CCC_ALLOWED_ORIGIN=https://ccc.dash.level.agency \
    CCC_TELEMETRY_DISABLED=1 \
    CCC_AUTO_UPDATE=0 \
    CCC_RESTART_PULL=0 \
    DISABLE_AUTOUPDATER=1 \
    OPENCODE_DISABLE_AUTOUPDATE=1 \
    CCC_OPENCODE_MODEL=google/gemini-3.8-flash \
    HOME=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl tzdata openssh-client procps \
 && rm -rf /var/lib/apt/lists/*

# Node runtime from the official image (no NodeSource apt dance).
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
 && npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
      "@openai/codex@${CODEX_VERSION}" "opencode-ai@${OPENCODE_VERSION}" \
 && npm cache clean --force

RUN pip install --no-cache-dir \
    "https://github.com/amirfish1/watchtower/archive/${WATCHTOWER_REF}.tar.gz" \
 && python3 -c "import watchtower.queue"

RUN useradd --uid 1000 --create-home --home-dir /data ccc \
 && mkdir -p /data/.claude /data/repos \
 && chown -R ccc:ccc /data

WORKDIR /app
# Root-owned and read-only to the agent user, so a spawned agent cannot edit
# CCC itself (CCC writes its state under $HOME, never /app).
COPY . /app

USER ccc
EXPOSE 8090
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
