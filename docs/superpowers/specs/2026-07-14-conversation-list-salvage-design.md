# Conversation List Performance Salvage Design

## Goal

Add `/api/conversations/list` as the archive sidebar's lightweight payload while
keeping `/api/conversations/all` unchanged and preserving every field used by
the current sidebar renderer and tri-state lifecycle.

## Chosen approach

Project the rows returned by `_archive_all_rows_cached()` with a deliberately
audited allowlist. Apply `1d`, `7d`, or `all` to that already-warm snapshot;
pinned and Hermes rows remain visible in bounded windows. The browser fetches
the selected window, refreshes when the window changes, and widens to `all`
before rendering a non-empty search so history search remains global.

## Contract

The projection retains identity, title, source/engine, folder and worktree
metadata, timestamps, lifecycle (`archived`, `trashed`, `pinned`, pin rank),
All-lane placement, state/session/goal/lineage metadata, and row-status,
approval, context, quality, and Codex fields read by the renderer. It omits
transcript text, filesystem paths used only for reading, and cache/debug-only
payload data such as `last_assistant_text` and `jsonl_path`.

## Verification

Behavioral tests exercise projection fidelity, bulky-data removal, window
semantics, warm-cache reuse, and the HTTP route. Static UI tests assert that
the renderer uses the list endpoint, preserves the selected window, and
widening search fetches all history. Existing tri-state lifecycle tests remain
part of focused verification.
