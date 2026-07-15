# Archive Search Scope Fix

## Problem

The archive API can return successfully while the sidebar remains on
"Loading archive…". Browser verification shows that archive rendering throws
`ReferenceError: _ipSearchActive is not defined`.

`renderConversationList()` currently declares `_ipSearchActive` inside the
object-grouping branch, but the archived-section renderer reads it regardless
of which grouping branch ran. On fresh or differently configured browser
profiles, the declaration is never evaluated and the render aborts.

## Design

Compute `_ipSearchActive` once near the start of `renderConversationList()`,
before any grouping branch. Reuse that value in the object-grouping and
archived-section paths, and remove the narrower declaration.

This preserves the existing definition of active search: a non-empty,
trimmed value in `#convSearch`. It changes only the variable's scope, not the
search or grouping behavior.

## Alternatives Considered

- Recompute the value at each use. This avoids the exception but duplicates
  the source of truth and risks inconsistent behavior.
- Guard archived uses with `typeof _ipSearchActive`. This masks the scoping
  error and silently changes behavior when the declaration is unavailable.

The single function-scoped value is the smallest and clearest fix.

## Verification

- Add a regression test proving the declaration is outside the conditional
  object-grouping branch and appears only once in the renderer.
- Run the focused test and the relevant smoke/static tests.
- Restart the local CCC service and use the repository Puppeteer harness to
  confirm the archive placeholder clears without page exceptions.

## Scope

No API response shape, storage format, search semantics, or visual design
changes. The in-progress archive-cache edits from the parallel performance
session remain untouched.
