# Presentation Live Parity Design

## Goal

Presentation mode must expose every visible change that the regular conversation view exposes, with no update-type allowlist and no perceptible delay. A reader may navigate historical slides without losing awareness of new messages, streaming output, transient state, warnings, or actions.

## Root Cause

The regular conversation DOM is canonical and continuously mutated by transcript rendering, send-state reconciliation, live-status polling, wake polling, timers, and user actions. Presentation mode currently hides that DOM and renders selectively refreshed clones. Full deck refreshes run only on a few transcript paths, while a one-second activity helper copies one unfinished status row. Anything outside those paths can be omitted or stale.

The fix must replace selective refresh coverage with a generic projection of canonical DOM mutations. Adding more status selectors or more calls to `refreshPresentationForPane` cannot satisfy the goal.

## User Experience

The presentation selector becomes `Off | Present`; the existing semantic Mode 2 deck is the sole presentation experience. A stored legacy Mode 1 value migrates to Present so users who opted into presentation stay opted in.

The presentation stage has two simultaneous regions:

1. **Slide region** — the existing semantic pages for completed assistant answers.
2. **Live updates region** — an always-visible, independently scrollable region containing exact projections of canonical conversation roots that changed after presentation was activated or after the latest completed answer boundary.

The live region remains visible while the reader navigates older slides. It never changes the selected historical slide. On activation it is seeded with every canonical root after the latest completed assistant answer, so an already-running turn is visible immediately. New updates make the region visibly active and scroll it to the newest update only when the reader was already at its end.

## Generic Mutation Projection

Each active presentation view owns one `MutationObserver` watching its canonical `.conversations-view` with `childList`, `subtree`, `characterData`, and `attributes` enabled. The observer ignores mutations inside presentation-owned DOM.

For every mutation, the projector finds the nearest direct child of the canonical conversation view. That top-level root is the projection unit. Projection is generic: it does not decide whether a root is a pending message, tool, warning, approval, stream, status, or a future update type.

Projection work is coalesced into one animation frame. A per-view `WeakMap` assigns each source root an ephemeral projection identifier without mutating the source DOM. The live region maintains at most one mirror per source root and replaces that mirror from the current canonical root on every flush. Each flush also reconciles tracked roots against `view.contains(sourceRoot)`, so removed roots lose their mirror. Attribute changes, text timers, nested content, control enabled state, and class transitions therefore remain equivalent to the regular view.

Completed assistant roots already represented by the semantic deck are refreshed through the deck path and are not duplicated indefinitely in the live region. Transient roots and current-turn roots remain in the live region until removed from the canonical view or incorporated into the completed answer boundary.

## Interaction Parity

Mirrors retain the source structure, attributes, values, disabled state, and accessible labels. Source element IDs are rewritten with a projection-specific prefix, along with local `for` and ARIA references, to avoid duplicate document IDs. Native event listeners are not copied by `cloneNode`, so the live region delegates `click`, `input`, and `change` events.

For each mirrored event target, the projector computes its child-index path within the mirrored root, resolves the same path inside the canonical root, synchronizes form state, and invokes the canonical control. This preserves existing application behavior for approvals, denials, queued-message cancellation, question answers, dismissals, wake actions, links, details toggles, and future controls without maintaining a control allowlist.

The canonical node remains the sole owner of application state and event listeners. The mirror never calls server APIs directly.

## Slide Refresh and Cursor Stability

Mutations to assistant or stream roots schedule a presentation deck refresh in the same animation frame as the live projection. Existing semantic item keys preserve the current slide during repagination. If the reader is following the presentation tail, a newly completed answer advances to the first slide of that answer; otherwise the selected historical slide remains stable.

The projection observer is disconnected and all projection state is discarded when presentation turns Off, the pane switches conversations, the view is replaced, or the pane is destroyed.

## Failure Handling

- Mutation processing must never throw into regular transcript rendering.
- A source root that cannot be cloned is represented by a compact text fallback containing its visible text and class name, rather than disappearing.
- If `MutationObserver` is unavailable, a 250 ms full-source signature poll drives the same generic projector.
- Projection work is bounded to one flush per animation frame and deduplicated by source root.

## Required Parity Evidence

Automated browser verification must compare canonical source roots with their live mirrors after each state transition, not merely assert that a presentation element exists.

The parity matrix includes:

- pending, queued, delivered, failed, removed, and durable user messages;
- Sending, Thinking, long-thinking copy, generating, in-flight tool, token, and elapsed-time updates;
- streamed assistant text and completed assistant answers;
- tool groups, tool completion, `Done`, questions, approvals, and approval-state changes;
- wake/resume stages, queue reasons, warnings, errors, outcome banners, and dismissals;
- added, edited, class-changed, attribute-changed, disabled/enabled, and removed roots;
- clicks, input, change, details toggles, and buttons forwarded to canonical controls;
- navigation on an older slide while live updates continue;
- single pane, split pane, resize, Present-to-Off restoration, and legacy Mode 1 migration.

For every matrix row, the test must prove text, relevant classes, attributes, control state, and removal state agree between the canonical regular view and the presentation mirror within one animation frame. The objective is not complete until this matrix passes in Chromium and focused static tests pass.

## Scope

This remains a client-only derived view. Transcript data, `/api/*` contracts, model calls, and token usage are unchanged. Historical design documents remain as records; current UI copy and implementation remove Mode 1.
