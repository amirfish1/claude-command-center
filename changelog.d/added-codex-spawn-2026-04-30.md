**OpenAI Codex as a spawn engine.** The kanban toolbar now has an
**Engine** dropdown (`claude` | `codex`) where the old `pkood spawn`
checkbox used to live, and the new-session modal mirrors it.
Selecting `codex` routes the next spawn through `codex exec --json
--dangerously-bypass-approvals-and-sandbox` instead of `claude -p`,
runs in the chosen working directory, and tracks the child on the
same kanban with a green `codex` chip.

Codex spawns are fire-and-watch in this iteration — no mid-run
inject (Codex `exec` is one-shot), no `claude --resume`-style
jump-in, and Codex JSONL ingestion isn't wired up yet. The
selector greys out automatically when the Codex CLI binary
can't be located (looked up via `$CCC_CODEX_BIN` →
`which codex` → `/Applications/Codex.app/Contents/Resources/codex`).

The `pkood:` prompt-prefix shortcut and `/api/pkood/spawn` endpoint
are unchanged. New endpoints: `POST /api/sessions/spawn-codex`,
`GET /api/sessions/spawn-codex/availability`. New env vars:
`CCC_CODEX_BIN` (binary override), `CCC_CODEX_MODEL` (model name,
default `gpt-5.5` — verified at release time against
codex-cli 0.125.0-alpha.3; note that `gpt-5.5-codex` is rejected
with a ChatGPT account).
