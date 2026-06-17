# Claude Command Center — trial install path.
#
# CCC's runtime is stdlib-only (see pyproject.toml: `dependencies = []`), so
# this image does NOT run `pip install`. Anything beyond the slim base is a
# bug — keep it that way.
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

# Copy the whole repo. .dockerignore strips caches, .git, gitignored plugin
# files, and other noise so the layer stays lean.
COPY . /app

EXPOSE 8090

# stdlib-only: just run the server. run.sh handles the launchd ceremony
# we don't need in a container, so invoke python directly.
CMD ["python3", "server.py"]
