# Claude Command Center — trial install path.
#
# CCC's own code is stdlib-only (see pyproject.toml: `dependencies = []`), but
# server.py hard-requires the `watchtower` package (watchtower.queue) as its
# queue engine with no standalone fallback — it raises ImportError and exits
# at startup if the module is missing. So the image installs exactly one thing:
# WatchTower, from the same upstream source scripts/install-watchtower.sh
# prefers (the GitHub tarball of main, not the lagging PyPI `watchtower-cli`).
# Do not add other pip installs — anything beyond WatchTower is a bug.
#
# Caveats vs. the native `./run.sh` path on macOS:
#   - macOS-only desktop conveniences (jump-to-terminal, screenshots, native
#     folder picker, Claude/Codex Desktop deep links) are unavailable on Linux.
#     The server reports a capabilities flag and the UI hides those controls,
#     so nothing is a broken button. See docs/linux-support-plan.md.
#   - No service install inside the container: the container IS the service.
#     On a native (non-container) Linux host, `./run.sh --install-service`
#     installs a systemd user service instead of a launchd agent.
#
# This image is for *evaluating* CCC. The kanban view + transcript ingestion
# work as long as ~/.claude is volume-mounted from the host (see compose file).

FROM python:3.12-slim

# Keep the image small and predictable.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8090 \
    # Inside a container the loopback interface is private to the container
    # namespace, so binding 127.0.0.1 would make the published port
    # unreachable from the host. Running CCC in Docker is an explicit opt-in
    # to the broader bind — pair it with `-p 127.0.0.1:8090:8090` (the
    # compose file does this) so the port stays loopback on the host.
    # See SECURITY.md for the full posture.
    CCC_BIND_HOST=0.0.0.0

WORKDIR /app

# WatchTower is CCC's queue engine and a hard import-time dependency of
# server.py (watchtower.queue). Install it from the upstream tarball of main,
# matching the preferred source in scripts/install-watchtower.sh. Override
# WATCHTOWER_TARBALL_URL at build time to pin a specific ref or point at a
# fork. Needs Python 3.11+ (satisfied by the 3.12-slim base).
ARG WATCHTOWER_TARBALL_URL=https://github.com/amirfish1/watchtower/archive/refs/heads/main.tar.gz
RUN pip install --no-cache-dir "${WATCHTOWER_TARBALL_URL}" \
    && python3 -c "import watchtower.queue" \
    && echo "watchtower.queue import OK"

# Copy the whole repo. .dockerignore strips caches, .git, gitignored plugin
# files, and other noise so the layer stays lean.
COPY . /app

EXPOSE 8090

# stdlib-only: just run the server. run.sh handles the launchd ceremony
# we don't need in a container, so invoke python directly.
CMD ["python3", "server.py"]
