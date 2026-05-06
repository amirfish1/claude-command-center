# Model + Context Pill (Click-to-Switch) — Design

**Status:** in-progress
**Date:** 2026-05-05
**Owner:** in-session

## Goal

In the session strip / kanban-row context block, the **model name** is rendered as a read-only tooltip pill (`wp-model-pill`, `static/index.html:13467`). The **context-window pill** is clickable today but only flips a `localStorage` display override (`_getCtxLimitOverride`, `:13444`).

Make both clickable: tap the model display → engine-scoped dropdown → pick → CCC applies it to that session, best-effort. While we're in there, fix a related bug where the context-usage pill doesn't reset after `/compact`.

## User experience

> "I tap on the model name on a session card. A dropdown opens with the right list for that session's engine. I pick something else. CCC switches the session to that model — live for Claude TTY/spawned, queued for the next ask on Codex/Gemini/dormant Claude — and the pill updates immediately."

## Two paths, scoped by engine

```
Claude TTY     →  /model X[1m] via AppleScript keystrokes      (live)
Claude spawned →  /model X[1m] via FIFO stream-json msg         (live, verify)
Codex          →  no inject path; CLI is one-shot               (queued)
Gemini         →  no inject path; CLI is one-shot               (queued)
Dormant Claude →  no live process                               (queued)
```

If headless `/model` injection turns out to not work in stream-json mode, that path gracefully falls back to queued-override semantics — UX is identical from the user's side.

## Per-engine model lists (curated + escape hatch)

Codex/Gemini CLIs accept any string and pass it to the API; neither has an enum. So each menu is a curated short list plus an inline `Other…` text input.

| Engine | Models |
|---|---|
| Claude | `opus-4-7`, `sonnet-4-6`, `haiku-4-5` — 1M toggle for opus/sonnet only |
| Codex | `gpt-5.5` (default), `gpt-5-codex`, `o3`, `o3-mini` |
| Gemini | `gemini-2.5-pro`, `gemini-2.5-flash` |

The current model is bolded. Free-text input at the bottom lets the user try a model the day a vendor ships it.

## Override sidecar

New file: `~/.claude-command-center/session-overrides.json`

Schema:
```json
{
  "<session_id>": {
    "model": "claude-sonnet-4-6",
    "context_1m": true,
    "set_at": "2026-05-05T17:30:00Z",
    "engine": "claude"
  }
}
```

Atomic write (write to `.tmp` then rename) — same pattern as the morning store / archive index.

**Sticky** — set once, stays until the user changes again or clears. (Considered one-shot; rejected because it makes the pill display lie between asks.)

## Backend (`server.py`)

### New endpoints

`POST /api/session/<session_id>/model`
```json
{ "model": "claude-sonnet-4-6", "context_1m": true }
```
Server logic:
1. Validate `session_id` belongs to a known session; resolve its `engine`.
2. If `engine == "claude"` AND there's a live TTY or spawned process registered:
   - Build the `/model <alias>[1m]` slash command (alias derivation is a `_short_model_alias()` helper).
   - Call existing `_inject_text_into_session(session_id, slash_cmd)`.
   - Also write the override to the sidecar so a refresh shows the new value.
   - Return `{ok: true, applied: "live"}`.
3. Else:
   - Write override to sidecar.
   - Return `{ok: true, applied: "queued"}`.

`DELETE /api/session/<session_id>/model` — clears the override.

### Read paths that consume the override

Three spawn/ask paths read the sidecar before constructing CLI args. If an override is present, it wins over env-var defaults:

- Claude headless spawn — `spawn_session()` (server.py ~9000). Currently hardcoded `--model opus`. Read override; pass `--model <override.model>` (with `[1m]` suffix when `context_1m`).
- Codex `exec` ask — codex spawn path (server.py ~8964). Currently `CCC_CODEX_MODEL` env var. Override wins.
- Gemini ask path — gemini spawn (server.py ~8786). Currently `CCC_GEMINI_MODEL` env var only. Override wins.

The override is **not cleared** after use (sticky). User clears it explicitly via the menu's "Reset to default" item.

### Sessions endpoint surfaces the override

`GET /api/sessions` (and the per-session detail endpoint) gain a field:
```
"override": { "model": "...", "context_1m": true, "applied": "live" | "queued" }
```

Frontend reads this and renders the pill state accordingly.

## `/compact` reset (the bug fix)

Today `extract_session_usage()` (server.py:12343) walks the whole JSONL and sets `latest = window` on each assistant turn, so `latest` is the last assistant turn's window. **Peak** is the all-time max. Both miss the fact that `/compact` (or auto-compact) materially shrinks the active context: pre-compact turns no longer contribute to the live context window.

JSONL signal: `{"type":"system","subtype":"compact_boundary","compactMetadata":{...}}` is emitted at each compaction. Verified in `~/.claude/projects/<repo>/<sid>.jsonl` files.

Fix: when walking the file, reset both `latest` (already gets overwritten naturally) and especially `peak` whenever a `compact_boundary` event is encountered. Track only the post-most-recent-compact segment for the displayed numbers.

Tooltip note added: "(reset at /compact)" when at least one compaction has happened, so the user understands the pill counts only post-compact context.

## Frontend (`static/index.html`)

### Model pill becomes the picker trigger

`renderSessionUsageIntoStrip()` at `:13449`. The `wp-model-pill` markup at `:13467` becomes a `<button>` with role and `aria-haspopup`. Click opens a popover anchored under the pill.

### Popover

Plain DOM, no framework. Inline-styled like the existing `wp-usage-pill`. Contents:

```
┌────────────────────────────────────┐
│ Switch model                       │
│                                    │
│ ● opus-4-7        [1M ▢]           │
│ ○ sonnet-4-6      [1M ▢]           │
│ ○ haiku-4-5                        │
│                                    │
│ Other:  [____________] [Apply]     │
│                                    │
│ ─────────────                      │
│ ↺ Reset to session default         │
└────────────────────────────────────┘
```

Engine determines the radio list. The 1M checkbox row is omitted for non-Claude engines and disabled for `haiku-*` (haiku has no 1M variant).

The current selection (from `override.model` if set, else `u.model`) is the bolded radio.

### Apply

`fetch('/api/session/<id>/model', { method: 'POST', body: JSON.stringify({ model, context_1m }) })`.

On success, optimistically update `_usageData.model` so the pill rerenders without waiting for the next refresh poll. If `applied: "queued"`, append a small `→ next` chip next to the model name.

### Pending chip

When `override.applied === "queued"`, render the model name with a subtle suffix:

```
sonnet-4-6 → next   (gold tone, tooltip: "Applied on next ask")
```

The 1M toggle on the existing context-pill stays — it remains a display-only override for "the server-detected limit is wrong, treat it as 1M for this view." That's distinct from the model-picker's `[1m]` toggle, which actually requests the 1M variant for the session.

### "Other…" input

Below the radio list. Free-text. On Apply, sent as-is to the backend. Server doesn't validate (the underlying CLI rejects bad names with its own error — surfaced via the toast on next ask).

## Testing

`tests/test_smoke.py` extension:
- Import the new endpoint handler. The bar is "doesn't break the import" (per `CLAUDE.md`).
- A small unit-style test for `extract_session_usage` against a fixture JSONL containing a `compact_boundary` event, asserting that `peak_input_tokens` reflects only post-compact turns.

Manual verification:
- Open the dashboard, pick a Claude session with usage history.
- Click model pill → switch from `sonnet-4-6` → `opus-4-7`. Confirm:
  - For a TTY session: characters appear in the terminal as `/model opus`, Enter, and the pill updates after the next assistant turn.
  - For a dormant session: pill shows `opus-4-7 → next`. Trigger an ask; spawn args include `--model opus`. Pill loses the chip after the assistant responds.
- Repeat on a Codex session with `gpt-5.5` → `gpt-5-codex`. Confirm queued chip and that the next codex `exec` uses the new model.
- Repeat on a Gemini session, same.
- Run `/compact` in a real Claude session. Confirm pill drops back to the post-compact `latest`/`peak` figures.

## Out of scope

- Catching up if user types `/model …` directly in TTY (we'd see it on next refresh; no special handling).
- Reasoning effort / `--effort` for headless Claude.
- Persisting overrides across CCC restarts (the sidecar JSON file already does this — but no migration story for older clients).
- Rich validation of "Other…" model strings.
- A separate "context limit" picker beyond the existing 200k/1M toggle.

## Versioning

Patch bump (`0.x.y` → `0.x.(y+1)`). New endpoints are additive (`POST /api/session/<id>/model` is new), no contracts broken.

Two changelog snippets:
- `changelog.d/added-model-picker-2026-05-05.md`
- `changelog.d/fixed-compact-context-reset-2026-05-05.md`
