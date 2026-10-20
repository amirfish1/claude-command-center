#!/bin/sh
# Container entrypoint for the Level-hosted CCC prototype.
set -e
mkdir -p "$HOME/.claude" "$HOME/repos"
# Agents commit from here; give git an identity once (persisted on the volume).
git config --global user.name  >/dev/null 2>&1 || git config --global user.name  "CCC (Level prototype)"
git config --global user.email >/dev/null 2>&1 || git config --global user.email "ccc-prototype@level.agency"
git config --global --add safe.directory '*' 2>/dev/null || true

# Engine logins from env (keys come from the Dokploy env, never the image).
# Codex reads ~/.codex/auth.json, so log it in once from OPENAI_API_KEY.
if [ -n "${OPENAI_API_KEY:-}" ] && [ ! -f "$HOME/.codex/auth.json" ]; then
  printenv OPENAI_API_KEY | codex login --with-api-key >/dev/null 2>&1 \
    && echo "[level-entrypoint] codex logged in with OPENAI_API_KEY" \
    || echo "[level-entrypoint] WARNING: codex login failed"
fi
# Gemini CLI uses GEMINI_API_KEY from env; preselect that auth type so it
# never stops at the interactive auth picker.
if [ -n "${GEMINI_API_KEY:-}" ] && [ ! -f "$HOME/.gemini/settings.json" ]; then
  mkdir -p "$HOME/.gemini"
  printf '%s\n' '{"security":{"auth":{"selectedType":"gemini-api-key"}}}' > "$HOME/.gemini/settings.json"
fi

# A git repo under ~/repos so the new-session folder defaults here (CCC scans
# ~/repos for git repos) instead of falling back to /app, CCC's own code.
if [ ! -d "$HOME/repos/sandbox/.git" ]; then
  git init -q -b main "$HOME/repos/sandbox"
fi

# CCC only accepts agent-config consent (hooks + skills) from loopback, so a
# hosted (proxied) user can never approve it. Approve once from inside the
# container after the server is up. Config lives in this container's ~/.claude
# only. Set CCC_AUTO_CONSENT=0 to skip.
if [ "${CCC_AUTO_CONSENT:-1}" != "0" ]; then
  (
    i=0
    while [ $i -lt 60 ]; do
      if python3 /app/ccc --server "http://127.0.0.1:${PORT:-8090}" consent approve all >/dev/null 2>&1; then
        echo "[level-entrypoint] agent-config consent approved"
        exit 0
      fi
      i=$((i + 1)); sleep 2
    done
    echo "[level-entrypoint] WARNING: could not auto-approve agent-config consent"
  ) &
fi

cd /app
exec python3 server.py
