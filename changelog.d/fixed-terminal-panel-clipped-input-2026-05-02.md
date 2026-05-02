**Terminal panel: input row no longer clipped.** The placeholder text
on the input row was rendering with its top half cut off in some
layouts. Two fixes: (a) the row now has `flex-shrink: 0` and a
`min-height: 32px` so it can't be squished by the flex container; (b)
when the multi-repo left rail is visible, the panel slides right by
48px so the rail's repo dots stay above (and clickable) instead of
being painted over.
