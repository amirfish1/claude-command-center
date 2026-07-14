# Measured Mode 2 Pagination Design

## Goal

Make Mode 2 fill each slide with as much readable content as visibly fits,
without splitting a semantic item or creating sparse multi-slide answers from
compact lists.

This refines the pagination section of the existing conversation presentation
modes design. Mode 1, transcript data, APIs, and model-token usage remain
unchanged.

## Problem

The current paginator estimates height with synthetic weights. It also marks
every ordered-list item as a mandatory page break. These rules can turn a short
numbered answer into many slides and can leave most of a tall slide empty.

The estimate is especially inaccurate across different pane heights, widths,
font sizes, inline code, and wrapping behavior. Adjusting constants would move
the failure rather than remove it.

## Packing behavior

Mode 2 packs completed answers greedily using the rendered height available in
the current conversation pane:

- Headings stay with their first following content item.
- Paragraphs, list items, code blocks, tables, images, diagrams, and
  blockquotes remain atomic.
- Multiple numbered or bulleted items share a slide whenever they fit.
- When another complete item would overflow, it begins the next slide.
- A single item taller than the available body area receives its own slide and
  scrolls internally rather than being clipped.
- A streaming answer remains one live slide and is measured only after it
  becomes a durable completed answer.

The prompt band appears only on the answer's first slide and participates in
that slide's available-height calculation. Details remain on the final slide
and participate in its measurement when open by default.

## Measurement architecture

The presentation stage owns a non-interactive measurement surface with the same
width, height, and typography as a visible slide. It remains layout-active but
uses hidden visibility, is removed from pointer flow, and is excluded from the
accessibility tree. Deck construction appends cloned semantic items to a
candidate slide one group at a time and measures the candidate's actual
rendered height.

When a candidate overflows, the most recently appended group moves to a new
slide. Heading-plus-content groups are tested together. Ordered and unordered
list items use list fragments that preserve numbering and bullet styling.

The existing weight paginator remains only as a defensive fallback when layout
measurement is unavailable. One malformed or unmeasurable answer falls back to
a Mode 1-style internally scrollable slide without affecting the rest of the
deck.

Mode 2 rebuilds its derived deck when the pane crosses a meaningful width or
height change. It preserves the current semantic slide key when possible and
continues following the tail only when the reader was already on the tail.

## Accessibility and safety

The measurement surface is hidden from assistive technology, cannot receive
focus, and never changes the source transcript. It clones only CCC's already
sanitized rendered nodes and performs no model or server request.

Reduced motion, keyboard navigation, split-pane isolation, and the existing
presentation fallback behavior remain unchanged.

## Verification

Automated tests cover:

- compact numbered content remaining on one page when it fits;
- compact bullet lists sharing a page;
- breaks occurring only between atomic items after overflow;
- headings staying with their following item;
- oversized atomic items receiving one scrollable slide;
- fallback behavior when measurement is unavailable; and
- cursor preservation across a measured repagination.

Puppeteer verification exercises Mode 2 at short, standard, and tall pane
heights. It asserts that compact examples produce one slide, visible slides do
not overflow, and navigation still reaches every generated slide.
