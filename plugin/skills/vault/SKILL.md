---
name: vault
description: Use when a task needs an API key, token, or login the user saved in CCC Settings > Vault. Run commands with the secret injected and redacted instead of reading the value.
allowed-tools: Bash
---

CCC's Vault holds the user's saved secrets (API keys, tokens, logins) in the
OS keychain. The `ccc vault` CLI reads it locally; no server is needed.

1. Find the entry. This prints names, kinds, services and env vars, never values:
   `ccc vault list --json`
2. Run your command with the secret in its environment. The value is redacted
   from stdout and stderr, so it never reaches the transcript:
   `ccc vault exec --env STRIPE_API_KEY=stripe-live -- ./deploy.sh`
   `ccc vault exec --env stripe-live -- ./deploy.sh` (uses the entry's stored env var)
3. To check an entry has a value without reading it: `ccc vault get <name>` (exit 0 = saved).

Rules:
- Never use `get --reveal`, and never `echo`, log, commit, or write a secret to a file.
- Do not pipe secret output out of the `exec`. Do any piping inside the wrapped command.
- If the entry is missing, do not ask for the value in chat. Run
  `ccc secret request NAME --reason "..."` to show the user a masked prompt in the dashboard.

Details: `docs/byok-and-vault.md`.
