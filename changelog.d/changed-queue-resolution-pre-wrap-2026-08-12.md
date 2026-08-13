Queue timeline resolution blocks (Summary/Caveat/Follow-up/Unresolved) now
preserve line breaks via `white-space: pre-wrap` on `.uxq-tl-res-v`, so
structured multi-line `wt close --summary` text renders readably instead of
collapsing into one dense paragraph (the q2 board already did this).
