#!/usr/bin/env bash
# Run the README's install line in a clean container, as a new user would.
#
# scripts/test-install.sh checks the working tree on every push, with `claude`
# and `uname` stubbed. This one checks what a stranger actually gets: it takes
# the command out of README.md, runs it word for word as a non-root user with
# nothing stubbed, and fails if the board does not come up. It reads the
# published installer and repository, so it catches a regression that is
# already on main, and one caused by something outside this repo.
#
# Usage:
#   ./scripts/test-readme-install.sh                  # ubuntu:24.04, published
#   ./scripts/test-readme-install.sh debian:stable-slim
#   ./scripts/test-readme-install.sh --checkout python:3.9-slim
#   ./scripts/test-readme-install.sh --checkout --macos-like
#
# --checkout installs this working tree instead of the published one, to try a
# change before pushing it. It still uses the README's own command shape.
#
# --macos-like runs on bash 3.2 (what macOS ships) with BusyBox instead of GNU
# tools and `uname` reporting Darwin. It catches bash 4 syntax and GNU-only
# flags without a Mac. It is not macOS: launchd, `open`, Gatekeeper and Apple's
# python are not exercised. python:3.9-slim covers the Python version.
#
# Environment:
#   CCC_README_INSTALL_TIMEOUT   seconds the board may take to answer (300)
#   CCC_README_INSTALL_LOG       copy the install log to this path

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE=published
IMAGE=ubuntu:24.04
MACOS_LIKE=""
for arg in "$@"; do
  case "$arg" in
    --checkout) SOURCE=checkout ;;
    --macos-like) MACOS_LIKE=1; IMAGE=bash:3.2 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "test-readme-install: unknown option: $arg" >&2; exit 2 ;;
    *) IMAGE="$arg" ;;
  esac
done
TIMEOUT="${CCC_README_INSTALL_TIMEOUT:-300}"

if ! command -v docker >/dev/null 2>&1; then
  echo "test-readme-install: docker is not on PATH" >&2
  exit 1
fi

# The first `curl ... | bash` line in the README is the promise under test.
INSTALL_LINE="$(grep -m1 -E '^curl .*install\.sh.*\| *.*bash' "$ROOT/README.md" || true)"
if [ -z "$INSTALL_LINE" ]; then
  echo "test-readme-install: no 'curl ... | bash' install line in README.md" >&2
  exit 1
fi
echo "test-readme-install: image=$IMAGE source=$SOURCE"
echo "test-readme-install: command: $INSTALL_LINE"

NAME="ccc-readme-install-$$"
LOG_DIR="$(mktemp -d)"
cleanup() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT

# Tracked and new files only, so gitignored local state stays out.
if [ "$SOURCE" = checkout ]; then
  git -C "$ROOT" ls-files -co --exclude-standard -z \
    | tar -C "$ROOT" --null --ignore-failed-read -T - -cf "$LOG_DIR/source.tar" 2>/dev/null
fi

# Runs inside the container. Everything after the prerequisites is what the
# new user does, so it runs as that user with a login-like environment.
cat > "$LOG_DIR/inside.sh" <<'INSIDE'
#!/bin/sh
set -eu
# Keep the image's own python3: python:3.9-slim is the point of that run.
command -v python3 >/dev/null 2>&1 || PY=python3
if command -v apk >/dev/null 2>&1; then
  apk add --quiet ca-certificates curl git ${PY:-}
  adduser -D -s "$(command -v bash)" stranger
else
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null
  apt-get install -y -qq --no-install-recommends \
    bash ca-certificates curl git procps ${PY:-} >/dev/null
  useradd -m -s /bin/bash stranger
fi
if [ -n "${CCC_MACOS_LIKE:-}" ]; then
  mkdir -p /opt/darwin
  printf '#!/bin/sh\n[ "${1:-}" = -s ] && { echo Darwin; exit 0; }\nexec /bin/uname "$@"\n' > /opt/darwin/uname
  printf '#!/bin/sh\nexit 0\n' > /opt/darwin/open
  chmod +x /opt/darwin/uname /opt/darwin/open
fi
if [ -f /tests/source.tar ]; then
  mkdir -p /tmp/source
  tar -C /tmp/source -xf /tests/source.tar
  chown -R stranger /tmp/source
fi
exec su stranger -c 'PATH="/opt/darwin:$PATH" bash /tests/as-stranger.sh'
INSIDE

cat > "$LOG_DIR/as-stranger.sh" <<'STRANGER'
#!/usr/bin/env bash
set -uo pipefail
cd "$HOME"
LOG=/tests/out/install.log
: > "$LOG"
fail() {
  echo "FAIL: $*"
  echo "--- install log (last 40 lines) ---"
  tail -n 40 "$LOG"
  exit 1
}

if [ -d /tmp/source ]; then
  git -C /tmp/source init -q
  git -C /tmp/source config user.email test@example.invalid
  git -C /tmp/source config user.name "README install test"
  git -C /tmp/source add -A >/dev/null
  git -C /tmp/source commit -qm fixture
  CCC_INSTALL_LINE="cat /tmp/source/scripts/install.sh | CCC_REPO_URL=/tmp/source bash"
fi

started=$SECONDS
bash -c "$CCC_INSTALL_LINE" >>"$LOG" 2>&1 &
install_pid=$!

up=""
while [ $((SECONDS - started)) -lt "$CCC_TIMEOUT" ]; do
  if curl -fs -o /dev/null --max-time 5 http://127.0.0.1:8090/api/version; then
    up=1
    break
  fi
  kill -0 "$install_pid" 2>/dev/null || fail "installer exited before the board came up"
  sleep 2
done
[ -n "$up" ] || fail "board did not answer within ${CCC_TIMEOUT}s"
echo "board up after $((SECONDS - started))s on $(python3 --version), bash $BASH_VERSION"

body="$(curl -fsS --max-time 30 http://127.0.0.1:8090/)" || fail "GET / failed"
case "$body" in
  *'<title>Command Center'*) ;;
  *) fail "GET / has no Command Center title" ;;
esac
curl -fsS --max-time 30 http://127.0.0.1:8090/static/templates.json \
  | python3 -c 'import json, sys; json.load(sys.stdin)' \
  || fail "static asset is not served"

# Step 3 of the installer promises the queue engine.
curl -fsS --max-time 30 http://127.0.0.1:8090/api/queue/import-doc | grep -q '"ok": *true' \
  || fail "queue API is not answering"

[ -d "$HOME/.ccc/claude-command-center/.git" ] || fail "no checkout at ~/.ccc"
[ -f "$HOME/.claude/command-center/install-source" ] || fail "install channel not recorded"
"$HOME/.local/bin/ccc" --help >/dev/null 2>&1 || fail "the ccc command does not run"

# A new user has to be told where to go.
grep -q 'http://localhost:8090' "$LOG" || fail "installer never printed the dashboard URL"
if grep -E 'Traceback|command not found|unbound variable|syntax error' "$LOG"; then
  fail "install log contains an error"
fi
echo "OK: README install reached a working board"
STRANGER

mkdir -p "$LOG_DIR/out"
chmod -R a+rwX "$LOG_DIR"

rc=0
docker run --rm --name "$NAME" \
  -v "$LOG_DIR:/tests" \
  -e CCC_INSTALL_LINE="$INSTALL_LINE" \
  -e CCC_TIMEOUT="$TIMEOUT" \
  -e CCC_MACOS_LIKE="$MACOS_LIKE" \
  --entrypoint sh "$IMAGE" /tests/inside.sh || rc=$?

if [ -n "${CCC_README_INSTALL_LOG:-}" ] && [ -f "$LOG_DIR/out/install.log" ]; then
  cp "$LOG_DIR/out/install.log" "$CCC_README_INSTALL_LOG"
fi
exit "$rc"
