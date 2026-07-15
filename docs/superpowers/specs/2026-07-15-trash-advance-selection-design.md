# Advance After Trashing the Open Conversation

## Behavior

When the user moves the currently open conversation to Trash, open the next
visible conversation in the sidebar. If there is no conversation below it,
open the visible conversation above it.

Trashing a conversation that is not currently open must not change the open
conversation. Untrash and failed Trash requests must not change selection. If
the list has no other visible conversation, leave the conversation pane as-is.

The same Trash behavior applies to every session lane under All: Coding,
Workers, and Messages. A successful Trash action must survive refresh. The
persisted lifecycle invariant is `trashed => archived`; a trashed session must
never return to an All lane as an active row.

## Implementation

Before the Trash request, capture the neighboring visible conversation ID from
the sidebar's rendered order. After the request succeeds and the sidebar is
rerendered, select that captured conversation. Reuse the same neighbor lookup
for the existing Archive behavior so grouped and filtered lists follow their
visual order consistently.

Serialize conversation lifecycle read-modify-write operations on the server so
rapid Trash actions cannot overwrite one another. When lifecycle state is read,
repair any legacy split-brain record by restoring every trashed session to the
archived set. Conversation builders must also derive `archived` as true whenever
`trashed` is true.

## Verification

Cover selection of the next row, fallback to the previous row, no selection
change for a non-open row, and no selection change for Untrash or failure.
Cover Trash from the Workers lane, concurrent Trash operations, refresh-time
state reconstruction, and repair of existing `trashed: true, archived: false`
records.
