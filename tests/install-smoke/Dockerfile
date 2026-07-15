# Claude Command Center — clean-install smoke test.
#
# Goal: every push validates that the README's `curl | bash` one-liner
# actually works end-to-end on a fresh machine. This Dockerfile simulates
# a clean Linux box, pipes scripts/install.sh into bash exactly the way a
# user would, then boots the server and probes a handful of endpoints.
#
# This is DIFFERENT from the production-style runtime image at the repo
# root (Dockerfile + docker-compose.yml, shipped via #54). That one is for
# evaluating CCC. THIS image is throwaway — it exists so `docker build`
# fails CI when the installer regresses.
#
# Caveats vs. a real macOS install:
#   - We stub uname to report Darwin and stub `claude` as a no-op so the
#     test follows the macOS path without requiring the Claude CLI.
#   - The repository URL is a real local Git checkout created inside the
#     image. That keeps the test offline while exercising the same clone,
#     atomic publish, pull, and launch path as a public-URL installation.

FROM debian:stable-slim

# Avoid interactive apt and noisy locales.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Minimal prereqs the installer expects: curl (for the one-liner shape,
# though we cat the local copy), bash, git (required by require_git),
# python3 (required by require_python3), ca-certificates (good hygiene).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      git \
      procps \
      python3 \
 && rm -rf /var/lib/apt/lists/*

# The installer assumes a user with $HOME. Use /root since debian:slim's
# default user is root — keeps the test simple, no useradd dance.
ENV HOME=/root

# Drop the repo in. .dockerignore on the repo root strips .git, caches,
# and other noise so we ship a clean copy of the working tree.
COPY . /repo

# ---------------------------------------------------------------------------
# Stubs for macOS-only bits of the installer.
# ---------------------------------------------------------------------------
# Each stub lives in /usr/local/sbin (earlier on PATH than /usr/bin) so it
# wins over the real binary inside the installer's environment.
#
# 1. uname -> always returns "Darwin" for `uname -s` so the installer
#    exercises its macOS branch inside the Linux container.
# 2. claude -> no-op. warn_if_no_claude_cli probes `command -v claude`; the
#    stub just has to exist + be executable so the warn line stays silent.
RUN set -eux; \
    mkdir -p /usr/local/sbin; \
    { \
      echo '#!/usr/bin/env bash'; \
      echo '# Test stub: always reports Darwin so install.sh require_macos passes.'; \
      echo 'if [ "${1:-}" = "-s" ]; then echo Darwin; exit 0; fi'; \
      echo 'exec /usr/bin/uname "$@"'; \
    } > /usr/local/sbin/uname; \
    chmod +x /usr/local/sbin/uname; \
    { \
      echo '#!/usr/bin/env bash'; \
      echo '# Test stub: no-op claude CLI so install.sh warn_if_no_claude_cli stays silent.'; \
      echo 'exit 0'; \
    } > /usr/local/sbin/claude; \
    chmod +x /usr/local/sbin/claude

# Put the stubs ahead of the real binaries.
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Turn the copied working tree into a real local remote. The final install
# destination intentionally does not exist before install.sh runs.
RUN cp -a /repo /repo-source \
 && git init -q /repo-source \
 && git -C /repo-source config user.email test@example.invalid \
 && git -C /repo-source config user.name "CCC Install Smoke" \
 && git -C /repo-source add -A \
 && git -C /repo-source commit -qm "install smoke fixture"

# ---------------------------------------------------------------------------
# The actual test: pipe install.sh into bash, then probe the server.
# ---------------------------------------------------------------------------
# Everything happens in ONE RUN so the layer fails the build on any check
# failure. We background the installer (it ends with `exec ./run.sh` which
# `exec python3 server.py` — both foreground) and poll the port.
#
# Probes:
#   - GET /                         -> 200, body contains <title>Command Center…
#   - GET /static/templates.json    -> 200, parses as JSON
#   - GET /api/version              -> 200 (cheap aliveness signal)
#   - $HOME/.claude/command-center/install-source contains the channel
#     we passed via CCC_FROM (proves persist_channel ran).
RUN set -eux; \
    # Pipe the installer in exactly the way the README shows. Backgrounding
    # the whole pipeline lets us probe the server it eventually launches.
    ( cat /repo/scripts/install.sh | CCC_FROM=readme CCC_REPO_URL=/repo-source PORT=8090 bash ) >/tmp/install.log 2>&1 & \
    INSTALL_PID=$!; \
    # Wait up to 60s for the port to bind.
    for i in $(seq 1 60); do \
      if curl -fsS -o /dev/null http://127.0.0.1:8090/api/version; then \
        echo "server up after ${i}s"; \
        break; \
      fi; \
      # If the install pipeline already exited, no point waiting longer.
      if ! kill -0 "$INSTALL_PID" 2>/dev/null; then \
        echo "install pipeline exited before port bound; log follows:"; \
        cat /tmp/install.log; \
        exit 1; \
      fi; \
      sleep 1; \
    done; \
    if ! curl -fsS -o /dev/null http://127.0.0.1:8090/api/version; then \
      echo "server never bound :8090; log follows:"; \
      cat /tmp/install.log; \
      exit 1; \
    fi; \
    # 1. Dashboard root: 200 + title marker.
    body=$(curl -fsS http://127.0.0.1:8090/); \
    echo "$body" | grep -q '<title>Command Center' \
      || { echo "dashboard root missing expected <title>"; echo "--- body ---"; echo "$body" | head -40; exit 1; }; \
    # 2. Static asset: templates.json parses as JSON.
    curl -fsS http://127.0.0.1:8090/static/templates.json | python3 -c 'import json,sys; json.load(sys.stdin)' \
      || { echo "/static/templates.json is not valid JSON"; exit 1; }; \
    # 3. The clean install is a real clone with the expected local origin.
    test -d /root/.ccc/claude-command-center/.git \
      || { echo "installed checkout is missing .git"; exit 1; }; \
    test "$(git -C /root/.ccc/claude-command-center remote get-url origin)" = /repo-source \
      || { echo "installed checkout has the wrong origin"; exit 1; }; \
    test -z "$(find /root/.ccc -maxdepth 1 -name 'claude-command-center.installing.*' -print -quit)" \
      || { echo "installer left a staging directory behind"; exit 1; }; \
    # 4. Channel persistence: install.sh wrote the attribution file.
    test -f /root/.claude/command-center/install-source \
      || { echo "install-source file missing"; exit 1; }; \
    grep -qx readme /root/.claude/command-center/install-source \
      || { echo "install-source content wrong: $(cat /root/.claude/command-center/install-source)"; exit 1; }; \
    echo "OK: install-smoke passed all checks"; \
    # Best-effort teardown so the layer doesn't keep a python process in
    # whatever state docker freezes layers in. Not load-bearing for the
    # exit code — every check above either passed or `exit 1`'d.
    kill "$INSTALL_PID" 2>/dev/null || true; \
    pkill -f 'python3.*server.py' 2>/dev/null || true; \
    true

# No CMD: this image is built-and-thrown-away. `docker build` returning 0
# IS the success signal. CI never `docker run`s it.
