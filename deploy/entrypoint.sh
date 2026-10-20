#!/bin/sh
# Container entrypoint for the Level-hosted CCC prototype.
set -e
mkdir -p "$HOME/.claude" "$HOME/repos"
# Agents commit from here; give git an identity once (persisted on the volume).
git config --global user.name  >/dev/null 2>&1 || git config --global user.name  "CCC (Level prototype)"
git config --global user.email >/dev/null 2>&1 || git config --global user.email "ccc-prototype@level.agency"
git config --global --add safe.directory '*' 2>/dev/null || true
cd /app
exec python3 server.py
