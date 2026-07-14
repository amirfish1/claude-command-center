# Conversation Presentation Modes Design

## Goal

Add an optional slide-style reading experience to every CCC conversation pane
without changing transcript data, API responses, agent prompts, or model token
usage.

## User interface

Every populated conversation pane exposes a compact segmented selector:

`Off | Mode 1 | Mode 2`

- **Off** preserves today's transcript reader exactly.
- **Mode 1** presents each completed assistant answer as one scrollable slide.
- **Mode 2** divides each completed assistant answer into a small set of slides
  using the already-rendered Markdown structure and the pane's available height.

The selection applies only to the pane where it was made. The most recently
selected mode is stored locally and becomes the default for newly opened panes.
No preference is written to the transcript or server.

## Deck behavior

Slides form one flat chronological deck across the conversation. A compact
prompt band on the first slide of each assistant answer preserves the user
question without adding a separate prompt slide. The bottom navigation dock
contains previous/next buttons, overall progress, and an answer-local label such
as `Answer 8 · 2 of 4`.

Left and right arrow keys navigate when focus is outside editable or interactive
controls. Buttons remain keyboard accessible and expose descriptive ARIA labels.

While an answer is streaming, it is shown as one live slide. Mode 2 paginates it
only after the durable assistant event arrives, avoiding content reshuffling
during generation.

## Pagination

Mode 2 derives semantic blocks from CCC's sanitized Markdown DOM. Headings stay
with the next block when possible. Paragraphs, list blocks, code blocks, tables,
images, diagrams, and blockquotes move as units and are never split internally.
The page budget scales with the conversation viewport. Oversized blocks scroll
inside their slide instead of being clipped.

Tool groups and non-answer assistant details are placed in a collapsed `Details`
section on the final slide for that answer. CCC's existing Verbose preference
opens those sections without leaving presentation mode.

If a single answer cannot be paginated, it falls back to a Mode 1 slide while the
rest of the deck remains usable. Returning to Off removes the derived deck and
reveals the untouched source DOM immediately.

## Architecture

The feature is client-only. `static/index.html` provides clone-safe per-pane
toolbar markup. `static/app.js` owns pane-local mode state, semantic pagination,
deck construction, streaming refresh, keyboard navigation, and restoration.
`static/app.css` provides the slide stage, prompt band, details area, selector,
bottom dock, split-pane behavior, mobile behavior, and reduced-motion treatment.

Generated slides clone already-rendered nodes; they never evaluate answer HTML or
send content back to a model. The original conversation DOM remains the source of
truth and stays mounted underneath the presentation stage.

## Verification

Automated coverage checks the pure paginator, selector and pane-state wiring,
source-DOM preservation, streaming refresh hook, navigation controls, and the
required presentation CSS. Browser verification uses the repository's Puppeteer
harness against the local CCC server in single-pane, split-pane, Mode 1, and Mode
2 states.
