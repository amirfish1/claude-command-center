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
#   - The installer is macOS-only (require_macos / warn_if_no_claude_cli /
#     git clone of a public URL). We stub uname to report Darwin, stub
#     `claude` as a no-op so warn_if_no_claude_cli stays silent, and stub `git` so
#     `git pull --ff-only` against the pre-staged checkout is a no-op
#     (we already COPYed the repo in, so there's nothing to fetch).
#   - This means we exercise everything in scripts/install.sh that runs
#     AFTER the prereq checks: channel parsing/persistence, repo path
#     wiring, and the launch handoff to run.sh. The clone/pull step is
#     bypassed by design — see the "no network" hard constraint in the
#     test spec.

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
# Stubs for macOS-only / network-dependent bits of the installer.
# ---------------------------------------------------------------------------
# Each stub lives in /usr/local/sbin (earlier on PATH than /usr/bin) so it
# wins over the real binary inside the installer's environment.
#
# 1. uname -> always returns "Darwin" for `uname -s`. The installer's
#    require_macos check is `[ "$uname_s" != "Darwin" ] && exit 2`.
# 2. claude -> no-op. warn_if_no_claude_cli probes `command -v claude`; the
#    stub just has to exist + be executable so the warn line stays silent.
# 3. git -> wrapper that intercepts `pull` and `clone` (the only network
#    calls in scripts/install.sh) and no-ops them. Everything else falls
#    through to the real /usr/bin/git so any future installer logic that
#    needs real git (e.g. `git -C ... rev-parse`) keeps working.
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
    chmod +x /usr/local/sbin/claude; \
    { \
      echo '#!/usr/bin/env bash'; \
      echo '# Test stub: short-circuits network-touching git subcommands so'; \
      echo '# the install layer can run with no internet access. Anything'; \
      echo '# else falls through to the real /usr/bin/git.'; \
      echo '#'; \
      echo '# install.sh calls `git clone URL DIR` and `git -C DIR pull ...`,'; \
      echo '# so the subcommand can be $1 or $3 (after `-C DIR`).'; \
      echo 'skip_next=0'; \
      echo 'for arg in "$@"; do'; \
      echo '  if [ "$skip_next" = 1 ]; then skip_next=0; continue; fi'; \
      echo '  case "$arg" in'; \
      echo '    clone|pull|fetch) exit 0 ;;'; \
      echo '    -C) skip_next=1 ;;'; \
      echo '    -*) ;;'; \
      echo '    *) break ;;'; \
      echo '  esac'; \
      echo 'done'; \
      echo 'exec /usr/bin/git "$@"'; \
    } > /usr/local/sbin/git; \
    chmod +x /usr/local/sbin/git

# Put the stubs ahead of the real binaries.
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Pre-stage the repo at $HOME/.ccc/claude-command-center — install.sh
# clones to that exact path. Pre-staging means the stubbed `git pull`
# is what runs (vs. `git clone`), and the post-clone working tree the
# installer hands off to run.sh is the one we COPYed in above.
RUN mkdir -p /root/.ccc \
 && cp -r /repo /root/.ccc/claude-command-center \
 && chmod +x /root/.ccc/claude-command-center/run.sh \
 && chmod +x /root/.ccc/claude-command-center/scripts/install.sh

# ---------------------------------------------------------------------------
# The actual test: pipe install.sh into bash, then probe the server.
# ---------------------------------------------------------------------------
# Everything happens in ONE RUN so the layer fails the build on any check
# failure. We background the installer (it ends with `exec ./run.sh` which
# `exec python3 server.py` — both foreground) and poll the port.
#
# Probes:
#   - GET /                         -> 200, body contains <title>Claude Command Center</title>
#   - GET /static/templates.json    -> 200, parses as JSON
#   - GET /api/version              -> 200 (cheap aliveness signal)
#   - $HOME/.claude/command-center/install-source contains the channel
#     we passed via CCC_FROM (proves persist_channel ran).
RUN set -eux; \
    # Pipe the installer in exactly the way the README shows. Backgrounding
    # the whole pipeline lets us probe the server it eventually launches.
    ( cat /repo/scripts/install.sh | CCC_FROM=readme PORT=8090 bash ) >/tmp/install.log 2>&1 & \
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
    echo "$body" | grep -q '<title>Claude Command Center</title>' \
      || { echo "dashboard root missing expected <title>"; echo "--- body ---"; echo "$body" | head -40; exit 1; }; \
    # 2. Static asset: templates.json parses as JSON.
    curl -fsS http://127.0.0.1:8090/static/templates.json | python3 -c 'import json,sys; json.load(sys.stdin)' \
      || { echo "/static/templates.json is not valid JSON"; exit 1; }; \
    # 3. Channel persistence: install.sh wrote the attribution file.
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
