**Conv-pane sticky header now tracks the most recent user message you've
scrolled past, and auto-sizes to fit that message.** Previously the sticky
pinned the *first* user message ("Original ask") at a manually-resizable
fixed height. Now, as you scroll down past later user messages, the sticky
body swaps to whichever user message has just fully cleared the sticky's
bottom edge, and the label flips from "Original ask" to "Earlier ask". The
"Original ask" rendering keeps its first-sentence/grey-rest split; "Earlier
ask" shows the full message in regular weight (no headline split for ad-hoc
later turns). The drag-to-resize handle at the bottom of the sticky is gone
— the box auto-sizes to whichever message it's currently showing, since
the swapping content makes a hand-tuned fixed height meaningless. Implemented
via a `requestAnimationFrame`-throttled scroll listener on
`.conversations-view`; only top-level user_text rows are tracked (messages
nested inside collapsed tool-call groups are ignored). Side effect: the
first user message's in-conversation chat bubble is hidden via a
`.is-pinned-in-sticky` class — it's already permanently rendered in the
sticky as "Original ask", so showing both was redundant.
