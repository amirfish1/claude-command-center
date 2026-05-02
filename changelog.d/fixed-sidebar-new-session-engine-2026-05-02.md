**Sidebar "+ New session" button now honors the engine dropdown.**
The prominent sidebar CTA was hardcoded to `/api/sessions/spawn`
(Claude) regardless of whether the toolbar **Engine** selector was
set to `codex`, so picking Codex and then clicking + New session
silently produced a Claude run. `spawnFromInlineInput` now reads
`$kptEngineSelect.value` and routes to `/api/sessions/spawn-codex`
when codex is selected, the optimistic placeholder card gets the
right `codex` chip, and the empty-state copy ("…spawn a fresh
Claude agent") swaps to "Codex" or "Claude" based on the current
selection. Toolbar **Run** and the New Session modal already did
this — only the sidebar CTA path was broken.
