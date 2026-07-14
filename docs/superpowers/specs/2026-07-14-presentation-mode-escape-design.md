# Presentation Mode Escape Design

## Goal

Pressing Escape exits the active conversation pane from presentation Mode 2 to Off, including while the conversation composer is focused.

## Behavior

- Escape changes only Mode 2 to Off. Mode 1 is unchanged.
- The active pane is the target; presentation state in other panes is unchanged.
- An Escape event already consumed by another UI layer is not reused to exit presentation mode.
- Existing left/right slide navigation is unchanged.

## Implementation

Extend the existing document-level presentation keyboard handler. When it receives an unmodified Escape key, defer the presentation action until the current keyboard event has finished dispatching. If the event has not been prevented and the active pane is still in Mode 2, call the existing `setPresentationMode(paneId, 'off')` path. Deferring the check lets modal and menu handlers consume Escape regardless of listener registration order.

The main composer normally maps Escape to the terminal Escape control and prevents the browser event. Its key handler must yield that mapping while the active pane is in Mode 2 so the presentation handler can exit the mode. Slash-command menus still run first and may consume Escape.

## Testing

Add a focused presentation-mode regression test that verifies the handler recognizes Escape before its editable-target exclusion, waits for event dispatch to settle, respects `defaultPrevented`, limits the behavior to Mode 2, exits through `setPresentationMode`, and takes priority over the composer's terminal Escape mapping.
