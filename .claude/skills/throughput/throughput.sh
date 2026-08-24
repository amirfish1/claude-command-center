#!/usr/bin/env bash
# CCC throughput helper.
#
# Resolves the running CCC dashboard, then either prints the throughput page URL
# or fetches token totals for the 7-day aggregate or a single session.
#
# Usage:
#   throughput.sh url                 # print dashboard + throughput page URL
#   throughput.sh total [engine]      # 7-day aggregate token summary
#   throughput.sh session <id|prefix> [engine]
#
# engine defaults to "claude". Override the base URL with CCC_BASE_URL, or the
# host/port with CCC_HOST / PORT. The throughput API needs a *full* session id,
# so `session` resolves an 8-char prefix (e.g. ffbbeef8) to the full uuid first.
set -euo pipefail

# ── Resolve the dashboard base URL ────────────────────────────────────────────
ccc_base() {
  if [ -n "${CCC_BASE_URL:-}" ]; then echo "${CCC_BASE_URL%/}"; return; fi
  local host="${CCC_HOST:-127.0.0.1}" port="" pid=""

  # 1. Port of the live server.py process (most reliable when local).
  pid=$(pgrep -f 'claude-command-center/server\.py' 2>/dev/null | head -1 || true)
  if [ -n "$pid" ]; then
    port=$(ss -ltnpH 2>/dev/null \
      | awk -v p="pid=$pid," '$0 ~ p {n=split($4,a,":"); print a[n]; exit}')
  fi

  # 2. Explicit PORT env, then the two conventional defaults — probe each.
  if [ -z "$port" ]; then
    for cand in "${PORT:-}" 8090 8091; do
      [ -z "$cand" ] && continue
      if curl -s -m3 "http://$host:$cand/api/conversations?all=1" \
           2>/dev/null | grep -q '"ok": *true'; then
        port="$cand"; break
      fi
    done
  fi

  [ -z "$port" ] && port="${PORT:-8090}"
  echo "http://$host:$port"
}

# ── Resolve a session id or short prefix to the full uuid ─────────────────────
resolve_sid() {
  local base="$1" q="$2"
  # Full uuid already? pass through.
  if [[ "$q" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}- ]]; then echo "$q"; return; fi
  curl -s -m8 "$base/api/conversations?all=1" 2>/dev/null | CCC_Q="$q" python3 -c '
import sys, json, os
q = os.environ["CCC_Q"].lower()
rows = json.load(sys.stdin).get("conversations", [])
hits = [r["session_id"] for r in rows if r["session_id"].lower().startswith(q)]
print(hits[0] if hits else "")
'
}

# ── Format a throughput summary payload into a human line ─────────────────────
fmt_summary() {
  python3 -c '
import sys, json
d = json.load(sys.stdin)
if not d.get("ok"):
    print("error:", d.get("error", "unknown")); sys.exit(1)
s = d.get("summary", {})
scope = d.get("scope", {})
label = scope.get("range", "Session")
sid = d.get("session_id")
engine = scope.get("engine", "claude")
def k(n):
    n = float(n or 0)
    for u in ("", "K", "M", "B"):
        if abs(n) < 1000: return f"{n:.1f}{u}".replace(".0", "")
        n /= 1000
    return f"{n:.1f}T"
turns = s.get("total_turns", 0)
with_tok = s.get("turns_with_tokens", 0)
total = k(s.get("total_tokens"))
inp = k(s.get("total_input_tokens"))
cache = k(s.get("total_cache_read_tokens"))
out = k(s.get("total_output_tokens"))
hit = round(100 * float(s.get("cache_hit_ratio") or 0))
cost = float(s.get("cost_usd") or 0)
print(f"scope:   {sid} ({label}, {engine})")
print(f"turns:   {turns} ({with_tok} with tokens)")
print(f"total:   {total} tokens")
print(f"  input:  {inp}  (cache read {cache}, hit {hit}%)")
print(f"  output: {out}")
print(f"cost:    ${cost:.2f}")
'
}

cmd="${1:-total}"
base="$(ccc_base)"

case "$cmd" in
  url)
    echo "dashboard:  $base"
    echo "throughput: $base/throughput.html"
    ;;
  total)
    engine="${2:-claude}"
    curl -s -m12 "$base/api/throughput?session_id=all_7_days&engine=$engine" | fmt_summary
    ;;
  session)
    q="${2:?usage: throughput.sh session <id|prefix> [engine]}"
    engine="${3:-claude}"
    sid="$(resolve_sid "$base" "$q")"
    if [ -z "$sid" ]; then
      echo "No session matches '$q' in the current conversation list." >&2
      exit 1
    fi
    curl -s -m12 "$base/api/throughput?session_id=$sid&engine=$engine" | fmt_summary
    ;;
  *)
    echo "usage: throughput.sh {url|total [engine]|session <id|prefix> [engine]}" >&2
    exit 2
    ;;
esac
