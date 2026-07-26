**Plan-to-fleet import preview now shows where each ticket came from.** `wt
import` emits the source anchor and the dependency edge as indented
continuation lines rather than inline in parentheses, so the preview was
parsing both away and rendering a bare title. The parser accepts both shapes,
and a preview row now shows `after: <title>` for a dependency alongside the
`plan.md#L8-L12` anchor. New: `docs/plan-to-fleet.md`, the end-to-end guide.
