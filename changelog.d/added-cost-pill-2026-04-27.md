**Cost pill in the conv-pane input strip.** Next to the existing `ctx` pill,
a small `$0.34` chip surfaces the Anthropic API list-price equivalent for
the session's tokens. Hover for a per-category breakdown (input, cache
write, cache read, output) with token counts. Subscription users (Claude
Pro/Max) pay flat, but the figure is the cleanest cross-model "how
expensive was this session" comparison. Server: `extract_session_usage` now
returns `cost_usd`, `cost_breakdown_usd`, and the per-category token totals
on `/api/session/<id>/usage`. Rate table covers Opus 4 / Sonnet 4 / Haiku 4
and falls back to Sonnet rates for unknown models.
