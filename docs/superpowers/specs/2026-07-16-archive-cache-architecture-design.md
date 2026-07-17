# Archive cache architecture diagram

## Goal

Create a standalone, public HTML page that explains CCC's current
archive-related cache layers, their request flow, freshness bounds, and
invalidation rules. The page is for maintainers who need to reason about CPU
cost and data freshness without reading `server.py`.

## Scope

The page documents the cache stack used by archive, dashboard, and attention
feeds:

1. persisted archive response cache;
2. corpus-signature memo;
3. in-memory archive serve cache and its stale-while-revalidate behavior;
4. spawned-session coalescing cache;
5. per-engine and liveness TTL memos used during row rehydration.

It also labels the optional `CCC_ARCHIVE_CACHE_TTL_SEC`,
`CCC_ARCHIVE_SIG_TTL_SEC`, and `CCC_ARCHIVE_SERVE_TTL_SEC` settings, with
their defaults and trade-offs.

The unmerged CCC-415 per-file incremental cache is shown only as a clearly
separate, non-production alternative. It is not described as active behavior.

## Presentation

Add `docs/archive-caching.html` as a self-contained document with inline CSS
and SVG. It will include:

- a request-flow diagram from archive/dashboard callers through each cache
  tier to the expensive builder;
- a compact table of cache key, lifetime, invalidation, and cost avoided;
- a small freshness timeline that explains the two-second serve window and
  background refresh;
- a callout on why CCC-415 is not merged.

The page will not require JavaScript, a build step, or external assets, so it
can be opened directly from the repository or GitHub Pages.

## Correctness and safety

The diagram will distinguish current behavior from planned or experimental
work, avoid machine-specific paths and data, and link source helper names only
as explanatory labels. It will make clear that cache reuse does not remove
the first cold build and that a force/non-stale archive request can still invoke
the full builder.

## Verification

Open the page with the repository's Puppeteer snapshot harness or a focused
local browser capture, confirming that the SVG and the cache table fit at a
desktop viewport and remain readable at a narrow viewport.
