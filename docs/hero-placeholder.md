# Hero capture target

This doc describes the demo loop the README hero *wants*. The current
`docs/images/demo.gif` is fine as a stand-in, but it's not optimised for the
top-of-README slot. Re-capture when you have a clean moment.

## Target shot

**Length:** 20–30 seconds. Loops cleanly (last frame ≈ first frame).

**Format:** GIF (autoplays without audio, no JS needed). Width ≤ 1400px,
file size ≤ 8 MB so GitHub's image proxy doesn't downsample it badly.

**Captions:** burned in as on-screen text overlays — no audio track. Three
or four short captions, e.g.:

1. "Three sessions running. Claude on issue #42, Codex on a refactor, Gemini
   reviewing a PR."
2. "Drag a fourth one from the GH Issues column."
3. "All four sessions, one keyboard."
4. "Spawn the next while the first is still building."

## Frame composition

- **Kanban view** as the primary surface. Columns visible: GH Issues →
  Working → Review → Verified. Show at least one card per column.
- **Sidebar visible** on the left: the live-sessions list with the
  multi-engine chips (claude / codex / gemini).
- **Right pane** showing one conversation streaming — pick a Claude session
  emitting a tool call (Read or Edit) so the live tool-call preview chip is
  visible mid-frame.
- **Theme:** dark mode (the project's default; reads better as a GIF).
- **Window chrome:** Chromeless (`./run.sh --app`) so the recording isn't
  cluttered with browser UI. Use the `chromeless-launcher` to capture.

## Tooling

- macOS: `Cmd+Shift+5` → "Record selected portion" → crop tight to the
  app window → export `.mov`.
- Convert with `ffmpeg`:
  ```
  ffmpeg -i recording.mov -vf "fps=15,scale=1400:-1:flags=lanczos" \
    -loop 0 docs/images/demo.gif
  ```
- Tune `fps` (15 → smaller; 24 → smoother) and `scale` to land under 8 MB.

## When done

1. Replace `docs/images/demo.gif` (same path — no README edit needed).
2. Delete this file.
3. Commit message: `docs: refresh hero GIF`.
