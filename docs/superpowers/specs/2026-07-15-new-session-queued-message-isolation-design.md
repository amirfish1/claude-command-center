# New-session queued-message isolation

## Problem

The queued-steer tray is mounted inside the persistent conversation composer,
outside the transcript container. Entering new-session mode rebuilds the
transcript and clears `currentSession`, but it does not remove the tray. Queued
messages from the previously open session can therefore remain visible above a
fresh-session composer.

This is a session-isolation failure: session-bound controls and message text
must not appear in a composer that has no session yet.

## Design

`enterNewSessionMode()` will remove any `.queued-steer-tray` belonging to the
active pane before it displays the fresh-session composer. Queue rendering,
delivery, and steering behavior for existing sessions will remain unchanged.

Cleanup belongs at the new-session lifecycle boundary because the tray is a
persistent composer child and cannot be removed by rebuilding the transcript.
The change will use the pane-aware composer lookup so split-pane state remains
isolated.

## Alternatives considered

1. Rebuild the entire composer whenever its conversation changes. This would
   clear the tray, but it risks losing draft text and control state.
2. Add a general-purpose teardown framework for every session-bound composer
   widget. That could be useful later, but it is broader than this defect and
   would increase regression risk.
3. Remove the queued tray explicitly on new-session entry. This is the chosen
   approach because it fixes the ownership boundary with the smallest change.

## Testing

A static regression test will assert that `enterNewSessionMode()` locates the
active pane's input bar and removes its queued-steer tray before continuing.
The existing smoke suite will verify that queue rendering and other dashboard
behavior remain intact.

## Success criteria

- Opening a new session never shows queued or steered messages from another
  session.
- Existing-session queued trays still render and remain steerable.
- Draft and spawn controls in the new-session composer are preserved.
