# Mobile Back Button Reliability Design

## Problem

On narrow viewports, the conversation reader is a full-screen overlay. Back is
the only route from that overlay to the home/session list, so it must remain
available throughout the reader lifecycle.

The current implementation moves the sole `#mobileBackBtn` node from the stable
conversation toolbar into a dynamically rendered subagent-tab strip. Switching
conversations clears that strip with `innerHTML`. If the button is inside the
strip at that moment, it is detached from the document. The otherwise empty
toolbar then collapses, leaving the user trapped in the conversation reader.

## Design

Keep `#mobileBackBtn` permanently owned by `#convToolbar`. Subagent-tab rendering
must never move, remove, or recreate it. The existing mobile toolbar remains the
single navigation header and continues to sit outside the scrolling transcript.

Remove the JavaScript that reparents Back into `.conv-tab-strip` and the CSS that
styles that temporary placement. Task tabs remain horizontally scrollable below
the toolbar, while Back stays in the toolbar's leading position at every point in
conversation loading, switching, tab creation, tab removal, and viewport changes.

## Error Handling and Compatibility

No new state or recovery path is needed because the failure mode is removed.
Desktop behavior is unchanged: existing media queries continue to hide the
mobile-only control. Conversation and group-chat popout rules remain unchanged.

## Testing

Add a regression assertion that establishes the structural invariant:

- `#mobileBackBtn` is declared inside `#convToolbar`.
- application JavaScript never reparents `#mobileBackBtn`.
- clearing or rendering `.conv-tab-strip` cannot affect Back.

Run the focused smoke test, then the full smoke suite. Use the repository's
Puppeteer harness at an iPhone-sized viewport to verify that Back remains visible
after opening a session with task tabs and switching to another session.
