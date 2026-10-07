# Free models ($0 runs)

CCC can run coding sessions on free-tier models so a run costs **$0**
instead of drawing down your paid plan or API budget. It does this with a
small local "free router" that CCC installs and manages for you: an
Anthropic-compatible endpoint on `127.0.0.1` that forwards each request to
one of the free LLM providers you have enabled.

Free models are not frontier models. They are great for routine work:
small fixes, boilerplate, tests, chores, a first task while you learn the
tool. CCC ranks them so the router sends your session to the best free
model that can actually use tools. For a hard problem, spawn on a paid
engine instead; nothing about free runs is mandatory.

Under the hood the router is [FreeLLMAPI](https://github.com/tashfeenahmed/freellmapi)
(MIT), pinned to a commit CCC has tested. You never have to install or
configure it by hand; the onboarding wizard and **Settings → Free models**
do it for you.

## How it works

```
┌──────────────┐   ANTHROPIC_BASE_URL=http://127.0.0.1:3017   ┌─────────────┐
│ spawned      │ ──────────────────────────────────────────> │ free router │
│ agent        │                                             │ (freellmapi)│
│ (claude -p)  │ <────────────────────────────────────────── │ :3017       │
└──────────────┘                                             └──────┬──────┘
      env set per session, never in your settings                   │ picks best
                                                                    v
                                                          ┌───────────────────┐
                                                          │ free providers    │
                                                          │ kilo · google ·   │
                                                          │ groq · cerebras … │
                                                          └───────────────────┘
```

- The router lives at `~/.ccc/freellmapi`, binds `127.0.0.1:3017`
  (loopback only, never the network), and is supervised for you by a
  LaunchAgent on macOS, a managed child process elsewhere. If it stops,
  **Settings → Free models** shows it and can start it again.
- When you spawn a session on the **Free ($0)** runtime, CCC injects three
  environment variables into that one child process:
  `ANTHROPIC_BASE_URL=http://127.0.0.1:3017`,
  `ANTHROPIC_AUTH_TOKEN=<the router's unified key>` and `ANTHROPIC_MODEL`.
- Your `~/.claude/settings.json` is **never** touched for free routing, and
  your normal paid sessions are unaffected; the env only exists inside
  $0 children.
- Claude Code sends its usual model names (`sonnet`, `opus`, `haiku`); the
  router maps each family onto a free catalog model: the best
  tool-capable one by default (see the leaderboard below), or the
  router's own pick.
- If a provider is rate-limited or down, the router falls over to the next
  available free model instead of failing your session.

Other engines that speak OpenAI-compatible APIs (OpenCode, Aider, Codex)
can use the same router through its `/v1` endpoints.

## Providers and keys

The router aggregates free tiers from many providers. Some need a free API
key (no credit card, a minute or two of signup); one works with no key at
all. **Settings → Free models** walks you through each: pick a provider,
hit **Open signup page**, paste the key, and CCC validates it live through
the router.

| Provider | Key needed | Where to get it | Notes |
|---|---|---|---|
| **Kilo Gateway** | none | works anonymously | Easiest start. **Kilo logs prompts and outputs on its free tier for training**. CCC shows you this and asks for one click of consent before enabling it. Rate-limited per IP. |
| **Google (Gemini)** | yes | [aistudio.google.com](https://aistudio.google.com/) | Generous free tier; strong free coding models. |
| **Groq** | yes | [console.groq.com](https://console.groq.com/) | Very fast inference; free daily quotas. |
| **Cerebras** | yes | [cloud.cerebras.ai](https://cloud.cerebras.ai/) | Fast; free tier on selected models. |
| **Mistral** | yes | [console.mistral.ai](https://console.mistral.ai/) | Free tier on La Plateforme. |
| **NVIDIA** | yes | [build.nvidia.com](https://build.nvidia.com/) | Free hosted endpoints for open models. |
| **OpenRouter** | yes | [openrouter.ai/keys](https://openrouter.ai/keys) | Aggregator; models tagged `:free` cost $0. |
| **GitHub Models** | yes | [github.com/settings/tokens](https://github.com/settings/tokens) | Free model endpoints with a GitHub token. |
| **HuggingFace** | yes | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | Free inference via the router meta-endpoint. |

Free tiers change often (providers launch, retire and re-cap models
without notice), and the router's own catalog covers more providers than
this table; CCC surfaces the ones that work well for coding. The
provider list in **Settings → Free models** is always the live truth.

More keys = more capacity and better failover. One key is enough to start.

## Which free model runs my session?

CCC can benchmark the free models on a handful of tiny coding tasks
(edit a file, fix a failing test, use a tool) and keeps the results in
`~/.ccc/free-eval.json`. The **Free model leaderboard** (linked from
Settings → Free models) shows pass rate and speed per model, and CCC sets
the router's Claude mapping to the top tool-capable model so a "Free
($0)" spawn gets the best free brain you have keys for.

## Privacy and the ToS boundary

Read this before you enable free runs: free tiers are paid for in ways
that aren't money. See [your data and account boundaries](tos-boundary.md)
for what the router carries, what stays separate, and how to verify failover.

- **Prompts leave your machine.** A $0 session's prompts, file contents
  and outputs go to whichever third-party provider serves the model. Each
  provider has its own logging and data-use terms; CCC shows the relevant
  note on each provider card in Settings → Free models.
- **Kilo's keyless tier logs your prompts and outputs for training.** It
  is the only provider that works with zero signup, which is why CCC
  offers it first, but it stays off until you click consent on that
  exact disclosure.
- **Your Claude subscription never goes through the router.** Routing is
  per-session environment only. CCC never sends a Claude OAuth token or
  your Anthropic login through it. The router carries only non-Anthropic
  traffic, and only inside sessions you explicitly spawned as free or
  approved with **Continue free**.
- **Don't paste secrets into free runs.** Treat free-tier prompts like
  public paste: no API keys, private credentials, or code you wouldn't
  share with the provider. Paid sessions have the same caution in
  reverse: they're bound by the provider agreement you pay for.
- **Keys are stored locally.** Provider keys live in the router's own
  store under `~/.ccc/freellmapi`; CCC keeps the router's admin
  credentials and unified key in `~/.ccc/free-router.json` (mode `0600`).
  Keys are never echoed back by the API, never written to logs, and never
  leave your machine except inside the provider calls themselves.

See [SECURITY.md](../SECURITY.md) for the full threat model.

## Using it day to day

- **Spawn**: the new-session dialog has a **Free ($0)** runtime option
  next to the paid engines.
- **Badge**: free sessions carry a `$0` badge in the session list, and
  finished runs report what they *would* have cost at API prices
  ("This run cost $0. At API prices it would have cost $1.80.").
- **Savings**: the header savings ticker adds up the API-priced value of
  your $0 runs alongside the rest of your fleet's work.
- **Limit-hit failover**: when a paid Claude session hits its usage
  limit, CCC offers "Continue this session on a free model?"; one click
  resumes it on the free runtime instead of waiting for the reset.
- **Your own router**: if you already run a freellmapi of your own on
  port 3001 (or another free gateway like Ollama on :11434), CCC detects
  it and offers to use it instead of installing a second one.

## Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| Free option greyed out | Router not installed or not running | Settings → Free models → Install / Start. |
| "no healthy providers" | No provider keys configured, or every key failed its check | Add a key (or enable keyless Kilo) in Settings → Free models. |
| Rate-limit errors on a $0 session | The provider's free cap for the hour/day is hit | Wait for the cap to reset, or add another provider key so the router can fail over. |
| Key rejected on paste | Wrong format or the provider check failed | Re-copy from the signup page; the wizard shows the expected shape (e.g. `sk-or-…`). |
| Free runs feel dumb | Free models are weaker than frontier models | Run the leaderboard (Settings → Free models) so CCC maps to the best one, or use a paid engine for hard tasks. |

## Related

- [`docs/onboarding.md`](onboarding.md) — the first-run wizard that sets
  all of this up in about five minutes.
- [`SECURITY.md`](../SECURITY.md) — binding, key storage, and the consent
  model.
- [FreeLLMAPI docs](https://github.com/tashfeenahmed/freellmapi) — the
  router's own documentation (full provider list, admin API, catalog).
