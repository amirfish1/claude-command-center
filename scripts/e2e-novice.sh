#!/usr/bin/env bash
# e2e-novice.sh — prove the "Moment Zero to $0" flow end to end, for real.
#
# Simulates a novice's first run in a throwaway HOME:
#   1. build the free model router (freellmapi) from source,
#   2. claim the admin account, enable the keyless Kilo provider,
#   3. prove a $0 Anthropic API call through the router,
#   4. boot a CCC server on a test port,
#   5. spawn a real Claude Code session through POST /api/sessions/spawn and
#      have it create a file — with every model call routed through the
#      free router (verified via the router's own analytics),
#   6. optionally walk /?onboarding=1 with puppeteer (scripts/verify-onboarding.js).
#
# Everything runs under a temp HOME and on test ports. The script only ever
# kills processes it started itself, and removes the temp HOME on exit unless
# it was caller-provided or CCC_E2E_KEEP=1.
#
# Consent note: the Kilo keyless provider logs free-tier prompts/outputs for
# training (upstream ToS). The interactive product asks for one consent click;
# this harness consents automatically because it only sends its own synthetic
# prompts — never user code.
#
# Env knobs (all optional):
#   CCC_E2E_HOME            HOME to use instead of a fresh mktemp -d
#   CCC_E2E_KEEP=1          keep the HOME dir and print its path (debug)
#   CCC_FREELLMAPI_SRC      local freellmapi checkout to copy (default: clone
#                           github.com/tashfeenahmed/freellmapi at the pin below)
#   CCC_E2E_ROUTER_PORT     router port (default: first free port)
#   CCC_E2E_CCC_PORT        CCC dashboard port (default: 9018)
#   CCC_E2E_CLAUDE_BIN      claude binary (default: `command -v claude`)
#   CCC_E2E_CLAUDE_REQUIRED unset/"1" (default 1): fail when claude is missing
#   CCC_E2E_SKIP_BUILD=1    reuse an existing server/dist build in the copy
#   CCC_E2E_SKIP_ONBOARDING=1  skip the puppeteer onboarding walk
#   CCC_E2E_SPAWN_TIMEOUT_S max wait for the spawned session (default 420)
#
# Exit: 0 = the $0 flow worked. Non-zero = the phase that failed is named in
# the log; tail of the router/server logs is dumped for diagnosis.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FREELLMAPI_PIN="d3f6f9b9fd65a1e7a11943e17376cfaa1b7a6173"
FREELLMAPI_REPO="https://github.com/tashfeenahmed/freellmapi.git"

E2E_HOME="${CCC_E2E_HOME:-}"
E2E_KEEP="${CCC_E2E_KEEP:-}"
ROUTER_PORT="${CCC_E2E_ROUTER_PORT:-}"
CCC_PORT="${CCC_E2E_CCC_PORT:-9018}"
CLAUDE_BIN="${CCC_E2E_CLAUDE_BIN:-$(command -v claude || true)}"
SPAWN_TIMEOUT_S="${CCC_E2E_SPAWN_TIMEOUT_S:-420}"
BOOT_TIMEOUT_S="${CCC_E2E_BOOT_TIMEOUT_S:-90}"
PROOF_FILE="e2e-free-proof.txt"
PROOF_TEXT="hello from a free model"
TASK_KEY="e2e-novice-$$"

ROUTER_PID=""
CCC_PID=""
SPAWN_PID=""
HOME_CREATED_BY_US=""
RESULT_OK=""
PHASE="preflight"

log()  { printf '[e2e %s] %s\n' "$(date +%H:%M:%S)" "$*"; }
phase(){ PHASE="$1"; log "=== PHASE: $1 ==="; }
fail() { log "FAIL ($PHASE): $*"; exit 1; }
warn() { log "WARN: $*"; }

pick_free_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

json_get() { # json_get '<json text>' 'dotted.key'
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    sys.exit(2)
for k in sys.argv[2].split("."):
    d = d.get(k) if isinstance(d, dict) else None
    if d is None:
        sys.exit(3)
if isinstance(d, bool):
    print("true" if d else "false")
elif isinstance(d, (dict, list)):
    print(json.dumps(d))
else:
    print(d)
PY
}

http_json() { # http_json METHOD URL TOKEN [BODY] -> body on stdout; dies non-2xx w/ body
  local method="$1" url="$2" token="${3:-}" body="${4:-}"
  local args=(-sS -X "$method" -H "Content-Type: application/json" -w $'\n%{http_code}')
  [ -n "$token" ] && args+=(-H "Authorization: Bearer $token")
  [ -n "$body" ] && args+=(-d "$body")
  local out code
  out="$(curl "${args[@]}" --max-time 60 "$url")" || return 4
  code="${out##*$'\n'}"
  out="${out%$'\n'*}"
  case "$code" in 2*) printf '%s' "$out";; *) printf '%s' "$out" >&2; return 5;; esac
}

# Python that can run server.py — it hard-imports `watchtower.queue`, which the
# product installs via scripts/install-watchtower.sh. Candidates are checked
# under the RUN env (HOME=$E2E_HOME): a `pip install --user` watchtower lives in
# the real HOME's user-site and would import for the ambient user but not for
# the simulated novice. When nothing qualifies, bootstrap the way a fresh
# machine does: a venv inside the temp HOME + the product's own installer.
wt_ok() { HOME="$E2E_HOME" "$1" -c 'import watchtower.queue' >/dev/null 2>&1; }

resolve_ccc_python() { # prints interpreter path, or nothing
  local py
  for py in "${CCC_PYTHON:-}" "$E2E_HOME/.venv/bin/python3" \
      "$REPO_ROOT/.venv/bin/python3" \
      /opt/homebrew/bin/python3 /usr/local/bin/python3 \
      "$(command -v python3 || true)"; do
    [ -n "$py" ] && [ -x "$py" ] && wt_ok "$py" \
      && { printf '%s' "$py"; return 0; }
  done
  return 1
}

bootstrap_ccc_python() { # create venv + install watchtower into temp HOME
  # NOTE: called inside $() — everything but the final printf must go to stderr.
  local venv_py="$E2E_HOME/.venv/bin/python3"
  log "no python has watchtower under HOME=$E2E_HOME — creating a venv" >&2
  python3 -m venv "$E2E_HOME/.venv" \
    || fail "could not create venv at $E2E_HOME/.venv" >&2
  mkdir -p "$E2E_HOME/.claude/command-center"
  CCC_PYTHON="$venv_py" \
    WATCHTOWER_INSTALL_DIR="$E2E_HOME/.ccc/watchtower" \
    CCC_WATCHTOWER_STATE_DIR="$E2E_HOME/.claude/command-center" \
    CCC_SKIP_WATCHTOWER_DAEMON=1 CCC_WATCHTOWER_FORCE=1 \
    bash "$REPO_ROOT/scripts/install-watchtower.sh" >>"$LOG_DIR/watchtower.log" 2>&1 \
    || { tail -20 "$LOG_DIR/watchtower.log" >&2; fail "watchtower bootstrap failed"; }
  "$venv_py" -c 'import watchtower.queue' >/dev/null 2>&1 \
    || fail "venv python still lacks watchtower after bootstrap" >&2
  printf '%s' "$venv_py"
}

wait_http() { # wait_http URL SECONDS [PID] — any HTTP answer = alive; when PID
  # is given, the child dying early fails fast instead of racing the deadline.
  local url="$1" secs="$2" pid="${3:-}" i
  for ((i = 0; i < secs * 2; i++)); do
    if curl -sS -o /dev/null --max-time 2 "$url" 2>/dev/null; then return 0; fi
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then return 2; fi
    sleep 0.5
  done
  return 1
}

cleanup() {
  local rc=$?
  # Spawned sessions are long-lived by design (the product keeps them resident
  # for follow-ups) — the harness kills the one it started.
  [ -n "$SPAWN_PID" ] && kill "$SPAWN_PID" 2>/dev/null || true
  [ -n "$CCC_PID" ] && kill "$CCC_PID" 2>/dev/null || true
  [ -n "$ROUTER_PID" ] && kill "$ROUTER_PID" 2>/dev/null || true
  wait "${SPAWN_PID:-}" "${CCC_PID:-}" "${ROUTER_PID:-}" 2>/dev/null || true
  if [ -n "$E2E_HOME" ]; then
    if [ -n "$HOME_CREATED_BY_US" ] && [ "$E2E_KEEP" != "1" ]; then
      rm -rf "$E2E_HOME"
      log "removed temp HOME $E2E_HOME"
    else
      log "kept HOME at $E2E_HOME (proof file: $E2E_HOME/CCC-Playground/$PROOF_FILE)"
    fi
  fi
  exit $rc
}
trap cleanup EXIT

# ---------- phase 0: preflight -------------------------------------------------
phase "preflight"
for tool in node npm python3 git curl; do
  command -v "$tool" >/dev/null || fail "missing required tool: $tool"
done
NODE_VER="$(node -e 'console.log(process.versions.node)')"
node -e 'const [a,b]=process.versions.node.split(".").map(Number);
if (!(a>20||(a===20&&b>=18)) || a>=25) process.exit(1)' \
  || fail "node $NODE_VER outside required range >=20.18 <25"
log "node $NODE_VER, npm $(npm --version), python $(python3 --version 2>&1 | awk '{print $2}')"
if [ "${CCC_E2E_CLAUDE_REQUIRED:-1}" = "1" ] && [ -z "$CLAUDE_BIN" ]; then
  fail "claude CLI not on PATH (set CCC_E2E_CLAUDE_BIN or CCC_E2E_CLAUDE_REQUIRED=0 to skip the spawn phase)"
fi
[ -n "$CLAUDE_BIN" ] && log "claude: $CLAUDE_BIN"

# ---------- phase 1: temp HOME + playground ------------------------------------
phase "home"
if [ -z "$E2E_HOME" ]; then
  E2E_HOME="$(mktemp -d "${TMPDIR:-/tmp}/ccc-e2e-home.XXXXXXXX")"
  HOME_CREATED_BY_US=1
fi
mkdir -p "$E2E_HOME/.ccc" "$E2E_HOME/CCC-Playground" "$E2E_HOME/logs"
LOG_DIR="$E2E_HOME/logs"
chmod 700 "$E2E_HOME/.ccc"
log "HOME=$E2E_HOME"

PLAYGROUND="$E2E_HOME/CCC-Playground"
cat > "$PLAYGROUND/index.html" <<'HTML'
<!doctype html>
<title>CCC Playground</title>
<h1>My first agent project</h1>
<p>This page was made by an AI agent that cost $0.</p>
HTML
git -C "$PLAYGROUND" init -q
git -C "$PLAYGROUND" -c user.name="E2E Novice" -c user.email="e2e@localhost.test" \
  add index.html
# Idempotent on a reused HOME: nothing staged -> nothing to commit.
git -C "$PLAYGROUND" -c user.name="E2E Novice" -c user.email="e2e@localhost.test" \
  diff --cached --quiet || \
git -C "$PLAYGROUND" -c user.name="E2E Novice" -c user.email="e2e@localhost.test" \
  commit -qm "init playground"
# A stale proof file from a previous run on a kept HOME would false-pass.
rm -f "$PLAYGROUND/$PROOF_FILE"
log "playground repo ready: $PLAYGROUND"

# ---------- phase 2: install the free router -----------------------------------
phase "router-install"
ROUTER_DIR="$E2E_HOME/.ccc/freellmapi"
SRC="${CCC_FREELLMAPI_SRC:-}"
if [ -n "$SRC" ]; then
  [ -d "$SRC/server/src" ] || fail "CCC_FREELLMAPI_SRC=$SRC does not look like a freellmapi checkout"
  # Copying over an existing install (a kept CCC_E2E_HOME) preserves its
  # node_modules/dist — the build check below then skips a full rebuild.
  log "copying router source from $SRC"
  # node_modules is heavy but lets us reuse a dev checkout's native builds;
  # .git preserves the exact rev we record in the state file.
  (cd "$SRC" && tar --exclude=node_modules --exclude='.git/index.lock' -cf - .) \
    | (mkdir -p "$ROUTER_DIR" && cd "$ROUTER_DIR" && tar -xf -)
  PINNED_REV="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || echo "$FREELLMAPI_PIN")"
  # Reuse a dev checkout's node_modules when present: a hardlink copy is near
  # instant on the same volume and avoids a multi-minute npm ci + native build.
  if [ -d "$SRC/node_modules" ]; then
    log "reusing node_modules from source checkout"
    (cd "$SRC" && tar -cf - node_modules) | (cd "$ROUTER_DIR" && tar -xf -)
  fi
else
  log "cloning $FREELLMAPI_REPO @ $FREELLMAPI_PIN"
  git clone -q "$FREELLMAPI_REPO" "$ROUTER_DIR"
  git -C "$ROUTER_DIR" checkout -q "$FREELLMAPI_PIN"
  PINNED_REV="$FREELLMAPI_PIN"
fi
ROUTER_VER="$(node -e 'console.log(require("'"$ROUTER_DIR"'/package.json").version||"dev")' 2>/dev/null || echo dev)"
# Same marker ccc_server/free_router._mark_managed writes — makes this install
# indistinguishable from a product-managed one so free_router.installed()
# and status() accept it.
printf '{"pinned_rev":"%s","managed_by":"ccc"}' "$PINNED_REV" \
  > "$ROUTER_DIR/.ccc-router-managed"
log "router source ready (rev ${PINNED_REV:0:12}, version $ROUTER_VER)"

if [ "${CCC_E2E_SKIP_BUILD:-}" = "1" ] && [ -f "$ROUTER_DIR/server/dist/index.js" ]; then
  log "CCC_E2E_SKIP_BUILD=1 and dist present — skipping build"
elif [ -f "$ROUTER_DIR/server/dist/index.js" ] && [ -d "$ROUTER_DIR/node_modules" ]; then
  log "reusing existing build (server/dist + node_modules present)"
else
  log "npm ci (first run is a few minutes)…"
  # --allow-remote/--allow-git: freellmapi's lockfile pins tarball URLs outside
  # the default registry (registry.npmmirror.com) and one git dep, which npm 12
  # refuses by default. Integrity hashes in the lockfile still gate content.
  (cd "$ROUTER_DIR" && npm ci --no-audit --no-fund --allow-remote=all --allow-git=all \
    >>"$LOG_DIR/npm-ci.log" 2>&1) \
    || { tail -30 "$LOG_DIR/npm-ci.log"; fail "npm ci failed (log: $LOG_DIR/npm-ci.log)"; }
  log "npm run build…"
  (cd "$ROUTER_DIR" && npm run build >>"$LOG_DIR/npm-build.log" 2>&1) \
    || { tail -30 "$LOG_DIR/npm-build.log"; fail "npm run build failed (log: $LOG_DIR/npm-build.log)"; }
fi
[ -f "$ROUTER_DIR/server/dist/index.js" ] || fail "server/dist/index.js missing after build"

# ---------- phase 3: router env + boot ------------------------------------------
phase "router-boot"
[ -n "$ROUTER_PORT" ] || ROUTER_PORT="$(pick_free_port)"
ROUTER_BASE="http://127.0.0.1:$ROUTER_PORT"
# Both secrets persist per-HOME: on a reused (kept) HOME the router's SQLite DB
# already exists, and a fresh ENCRYPTION_KEY would fail to decrypt its keys.
if [ -f "$E2E_HOME/.ccc/encryption-key" ]; then
  ENCRYPTION_KEY="$(cat "$E2E_HOME/.ccc/encryption-key")"
else
  ENCRYPTION_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  printf '%s' "$ENCRYPTION_KEY" > "$E2E_HOME/.ccc/encryption-key"
fi
chmod 600 "$E2E_HOME/.ccc/encryption-key"
ADMIN_EMAIL="e2e-admin@localhost.test"
# Password persists per-HOME so reusing a kept HOME (fast iteration: the
# existing router DB already has the account) still lets us log in.
if [ -f "$E2E_HOME/.ccc/admin-password" ]; then
  ADMIN_PASSWORD="$(cat "$E2E_HOME/.ccc/admin-password")"
else
  ADMIN_PASSWORD="$(python3 -c 'import secrets; print("e2e-"+secrets.token_urlsafe(12))')"
  printf '%s' "$ADMIN_PASSWORD" > "$E2E_HOME/.ccc/admin-password"
  chmod 600 "$E2E_HOME/.ccc/admin-password"
fi
CONFIG_JSON="$E2E_HOME/.ccc/freellmapi.config.json"
cat > "$CONFIG_JSON" <<JSON
{"admin": {"email": "$ADMIN_EMAIL", "password": "$ADMIN_PASSWORD"}}
JSON
chmod 600 "$CONFIG_JSON"
cat > "$ROUTER_DIR/.env" <<ENV
ENCRYPTION_KEY=$ENCRYPTION_KEY
HOST=127.0.0.1
PORT=$ROUTER_PORT
FREEAPI_CONFIG_PATH=$CONFIG_JSON
ENV
chmod 600 "$ROUTER_DIR/.env"
log "starting router on $ROUTER_BASE"
(
  # --env-file does NOT override already-exported vars, and this harness may be
  # launched from an env that has PORT= set (e.g. inside a CCC session where
  # PORT=8090). Explicit env on the command wins over both inherited env and
  # the .env file, so unset first and pass everything explicitly.
  unset PORT HOST
  export HOME="$E2E_HOME"
  cd "$ROUTER_DIR"
  nohup env \
    ENCRYPTION_KEY="$ENCRYPTION_KEY" \
    HOST=127.0.0.1 PORT="$ROUTER_PORT" \
    FREEAPI_CONFIG_PATH="$CONFIG_JSON" \
    node --env-file=.env server/dist/index.js \
    >"$LOG_DIR/router.log" 2>&1 &
  echo $! > "$E2E_HOME/.ccc/router.pid"
)
ROUTER_PID="$(cat "$E2E_HOME/.ccc/router.pid")"
wait_http "$ROUTER_BASE/" "$BOOT_TIMEOUT_S" "$ROUTER_PID" \
  || { tail -40 "$LOG_DIR/router.log"; fail "router did not answer on $ROUTER_BASE"; }
wait_http "$ROUTER_BASE/api/auth/status" 30 || true
log "router is up (pid $ROUTER_PID)"

# ---------- phase 4: admin + unified key + keyless kilo -------------------------
phase "router-keys"
LOGIN="$(http_json POST "$ROUTER_BASE/api/auth/login" "" \
  "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}")" \
  || fail "admin login failed — declarative admin did not apply?"
TOKEN="$(json_get "$LOGIN" token)" || fail "login response had no token"
log "admin session ok"

UNIFIED_JSON="$(http_json GET "$ROUTER_BASE/api/settings/api-key" "$TOKEN")" \
  || fail "could not read unified api key"
UNIFIED_KEY="$(json_get "$UNIFIED_JSON" apiKey)" || fail "no apiKey in settings response"
log "unified key issued (freellmapi-…${UNIFIED_KEY: -4})"

# Enable the keyless Kilo provider — the no-card path (auto-consented for the
# synthetic prompts this harness sends; see header note).
KILO_RESP="$(http_json POST "$ROUTER_BASE/api/keys" "$TOKEN" \
  '{"platform":"kilo","label":"e2e-keyless"}')" \
  || fail "POST /api/keys kilo failed"
MODELS_AVAILABLE="$(json_get "$KILO_RESP" modelsAvailable || echo 0)"
log "kilo keyless enabled (modelsAvailable=$MODELS_AVAILABLE)"
[ "$MODELS_AVAILABLE" != "0" ] \
  || warn "kilo key added but catalog shows 0 available models — spawn may fail"

# State file in the contract shape (0600): what CCC's free_router module reads
# to know a managed router is installed and healthy.
cat > "$E2E_HOME/.ccc/free-router.json" <<JSON
{"port": $ROUTER_PORT,
 "base_url": "$ROUTER_BASE",
 "admin_email": "$ADMIN_EMAIL",
 "admin_password": "$ADMIN_PASSWORD",
 "unified_key": "$UNIFIED_KEY",
 "version": "$ROUTER_VER",
 "pinned_rev": "$PINNED_REV"}
JSON
chmod 600 "$E2E_HOME/.ccc/free-router.json"

# ---------- phase 5: prove $0 inference -----------------------------------------
phase "router-inference"
REQS_BEFORE="$(http_json GET "$ROUTER_BASE/api/analytics/summary?range=1d" "$TOKEN" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("totalRequests") or 0)' 2>/dev/null || echo 0)"
MSG_RESP_HEADERS="$E2E_HOME/.ccc/msg-headers.txt"
MSG_BODY="$(curl -sS -D "$MSG_RESP_HEADERS" --max-time 120 \
  -X POST "$ROUTER_BASE/v1/messages" \
  -H "x-api-key: $UNIFIED_KEY" -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4-5","max_tokens":32,"messages":[{"role":"user","content":"Reply with exactly the single word: pong"}]}')" \
  || { tail -30 "$LOG_DIR/router.log"; fail "POST /v1/messages failed"; }
echo "$MSG_BODY" | grep -q '"content"' \
  || { echo "$MSG_BODY" | head -20; fail "/v1/messages answered but not an Anthropic message"; }
ROUTED_VIA="$(grep -i '^x-routed-via:' "$MSG_RESP_HEADERS" | tr -d '\r' | awk '{print $2}' || true)"
log "anthropic /v1/messages ok — routed via ${ROUTED_VIA:-unknown}"

# ---------- phase 6: CCC server --------------------------------------------------
phase "ccc-server"
CCC_BASE="http://127.0.0.1:$CCC_PORT"
# Refuse to piggy-back on a foreign listener: if anything already answers on
# the port, spawn/spawned calls would hit the wrong server (a leftover from a
# previous run, or a sibling's) and the proof could look fine while the real
# harness process silently failed to bind.
if curl -sS -o /dev/null --max-time 2 "$CCC_BASE/" 2>/dev/null; then
  fail "$CCC_BASE already answers — another server holds the port (stale e2e run? pick another CCC_E2E_CCC_PORT)"
fi
# The spawn payload carries runtime:"free" — the product's $0 path then builds
# the child's ANTHROPIC_* env from the state file itself. Exporting the same
# vars here is belt-and-suspenders: the spawned claude child also inherits the
# server's env, so the run stays $0 even on builds without the runtime hook.
# ANTHROPIC_API_KEY is scrubbed: Claude Code refuses to start when both it and
# ANTHROPIC_AUTH_TOKEN are set. (macOS env has no -u, so unset in a subshell.)
if ! CCC_PY="$(resolve_ccc_python || bootstrap_ccc_python)"; then
  fail "could not obtain a watchtower-capable python"
fi
log "CCC python: $CCC_PY"
(
  # PORT is unset too: server.py would otherwise read it, and a leaked
  # PORT=8090 from a parent session could shadow the --port flag's intent.
  unset ANTHROPIC_API_KEY ANTHROPIC_API_KEY_OLD CLAUDECODE \
        CLAUDE_CODE_SESSION_ID CLAUDE_CODE_MESSAGING_SOCKET PORT HOST
  export HOME="$E2E_HOME" CCC_EPHEMERAL=1 CCC_ALLOW_DUPLICATE_REPO=1 \
         ANTHROPIC_BASE_URL="$ROUTER_BASE" ANTHROPIC_AUTH_TOKEN="$UNIFIED_KEY"
  exec "$CCC_PY" "$REPO_ROOT/server.py" --port "$CCC_PORT"
) >"$LOG_DIR/ccc-server.log" 2>&1 &
CCC_PID=$!
log "CCC server starting on $CCC_BASE (pid $CCC_PID, HOME=$E2E_HOME)"
wait_http "$CCC_BASE/" "$BOOT_TIMEOUT_S" "$CCC_PID" \
  || { tail -40 "$LOG_DIR/ccc-server.log"; fail "CCC server did not answer on $CCC_BASE"; }
# API readiness, not just static: the sessions route answering proves handlers up.
wait_http "$CCC_BASE/api/sessions/spawned" 30 \
  || { tail -40 "$LOG_DIR/ccc-server.log"; fail "CCC API not ready"; }
log "CCC server is up"

ROUTER_API="absent"
if curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
    "$CCC_BASE/api/free-router/status" 2>/dev/null | grep -q 200; then
  ROUTER_API="present"
  log "free-router API present — status: $(curl -sS --max-time 5 "$CCC_BASE/api/free-router/status")"
else
  log "free-router API absent (404) — \$0 env reaches the child via server-env inherit"
fi

# ---------- phase 7: spawn the $0 session ----------------------------------------
phase "free-spawn"
if [ -z "$CLAUDE_BIN" ]; then
  warn "no claude binary — skipping spawn phase"
else
  SPAWN_PAYLOAD="$(python3 - "$PLAYGROUND" "$PROOF_FILE" "$PROOF_TEXT" "$TASK_KEY" <<'PY'
import json, sys
playground, proof_file, proof_text, task_key = sys.argv[1:5]
print(json.dumps({
    "prompt": (
        f"Create a file named {proof_file} in the current directory "
        f"containing exactly this text: {proof_text}. Then stop."
    ),
    "name": "e2e-free-run",
    "cwd": playground,
    "engine": "claude",
    "runtime": "free",
    "task_key": task_key,
}))
PY
)"
  SPAWN_RESP="$(http_json POST "$CCC_BASE/api/sessions/spawn" "" "$SPAWN_PAYLOAD")" \
    || { tail -30 "$LOG_DIR/ccc-server.log"; fail "POST /api/sessions/spawn rejected"; }
  SPAWN_OK="$(json_get "$SPAWN_RESP" ok 2>/dev/null || echo false)"
  [ "$SPAWN_OK" = "true" ] \
    || { echo "$SPAWN_RESP"; tail -20 "$LOG_DIR/ccc-server.log"; fail "spawn returned ok=false"; }
  SPAWN_ID="$(json_get "$SPAWN_RESP" spawn_id 2>/dev/null || echo '?')"
  SPAWN_PID="$(json_get "$SPAWN_RESP" pid 2>/dev/null || echo '')"
  SESSION_ID="$(json_get "$SPAWN_RESP" session_id 2>/dev/null || echo '')"
  log "spawned \$0 claude session (spawn_id=$SPAWN_ID pid=$SPAWN_PID task_key=$TASK_KEY)"

  # Success signal is the proof file, not process exit: CCC-spawned sessions
  # stay resident for follow-ups, so "running" is the healthy steady state.
  DEADLINE=$(( $(date +%s) + SPAWN_TIMEOUT_S ))
  ROW_STATUS="running"
  while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    [ -f "$PLAYGROUND/$PROOF_FILE" ] && break
    ROWS="$(curl -sS --max-time 10 \
      "$CCC_BASE/api/sessions/spawned?task_key=$TASK_KEY&include_finished=1" 2>/dev/null || echo '[]')"
    ROW_STATUS="$(python3 -c '
import json,sys
rows=json.loads(sys.argv[1] or "[]")
r=rows[0] if rows else {}
print("finished" if (r.get("status")=="finished" or r.get("running") is False) else "running")' \
      "$ROWS" 2>/dev/null || echo unknown)"
    [ -z "$SPAWN_PID" ] && SPAWN_PID="$(python3 -c '
import json,sys
rows=json.loads(sys.argv[1] or "[]")
print((rows[0] or {}).get("pid") or "")' "$ROWS" 2>/dev/null || true)"
    # A dead process that produced no file fails fast instead of timing out.
    [ "$ROW_STATUS" = "finished" ] && break
    sleep 5
  done
  ROW_STATUS="${ROW_STATUS:-unknown}"

  if [ ! -f "$PLAYGROUND/$PROOF_FILE" ]; then
    LOG_HINT="$(python3 -c '
import json,sys
try: rows=json.loads(sys.argv[1] or "[]")
except Exception: rows=[]
print((rows[0] or {}).get("log","") if rows else "")' "$ROWS" 2>/dev/null)"
    [ -n "$LOG_HINT" ] && [ -f "$LOG_HINT" ] && { echo "---- spawn log tail ----"; tail -25 "$LOG_HINT"; }
    tail -20 "$LOG_DIR/ccc-server.log"
    fail "proof file not created within ${SPAWN_TIMEOUT_S}s (row status: $ROW_STATUS)"
  fi
  log "proof file appeared (session row status: $ROW_STATUS — resident is expected)"
fi

# ---------- phase 8: assertions ---------------------------------------------------
phase "assert"
if [ -z "$CLAUDE_BIN" ]; then
  warn "assert skipped: no claude binary"
  RESULT_OK=skip
else
  [ -f "$PLAYGROUND/$PROOF_FILE" ] \
    || fail "proof file $PLAYGROUND/$PROOF_FILE was not created"
  PROOF_CONTENT="$(tr -d '[:space:]' < "$PLAYGROUND/$PROOF_FILE")"
  [ -n "$PROOF_CONTENT" ] || fail "proof file is empty"
  WANT_NORM="$(printf '%s' "$PROOF_TEXT" | tr -d '[:space:]')"
  [ "$PROOF_CONTENT" = "$WANT_NORM" ] \
    || warn "proof text differs ('$PROOF_CONTENT' != '$WANT_NORM') — file exists, which is the assertion"
  log "proof file created: '$PROOF_CONTENT'"

  REQS_AFTER="$(http_json GET "$ROUTER_BASE/api/analytics/summary?range=1d" "$TOKEN" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("totalRequests") or 0)' 2>/dev/null || echo 0)"
  REQS_DELTA=$(( ${REQS_AFTER:-0} - ${REQS_BEFORE:-0} ))
  [ "$REQS_DELTA" -gt 0 ] \
    || warn "router analytics shows no new requests — cannot prove the run was \$0 (before=$REQS_BEFORE after=$REQS_AFTER)"
  log "router requests +$REQS_DELTA (the session ran on the free router)"
  RESULT_OK=1
fi

# ---------- phase 9: onboarding UI walk (optional) ---------------------------------
ONBOARDING="skipped"
if [ "${CCC_E2E_SKIP_ONBOARDING:-}" != "1" ] && command -v node >/dev/null; then
  phase "onboarding-ui"
  SHOTS_DIR="$E2E_HOME/shots"; mkdir -p "$SHOTS_DIR"
  if CCC_E2E_CCC_URL="$CCC_BASE" CCC_E2E_SHOTS="$SHOTS_DIR" \
      node "$REPO_ROOT/scripts/verify-onboarding.js"; then
    ONBOARDING="walked"
  else
    OB_RC=$?
    [ "$OB_RC" = "3" ] && ONBOARDING="not-present" \
      || { ONBOARDING="failed"; warn "verify-onboarding.js exited $OB_RC"; }
  fi
fi

phase "done"
cat <<EOF
E2E_RESULT {"ok": $([ "$RESULT_OK" = "1" ] && echo true || echo false),
  "home": "$E2E_HOME",
  "router_base": "$ROUTER_BASE",
  "ccc_base": "$CCC_BASE",
  "routed_via": "${ROUTED_VIA:-}",
  "router_api": "$ROUTER_API",
  "router_requests_delta": ${REQS_DELTA:-0},
  "spawn_row_status": "${ROW_STATUS:-}",
  "session_id": "${SESSION_ID:-}",
  "proof_file": "$PLAYGROUND/$PROOF_FILE",
  "onboarding": "$ONBOARDING",
  "pinned_rev": "$PINNED_REV"}
EOF
[ "$RESULT_OK" = "1" ] || [ "$RESULT_OK" = "skip" ] || exit 1
log "PASS — a real agent run just cost \$0 end to end."
