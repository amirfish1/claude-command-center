# How CCC compares

Picking an AI coding tool? First, separate two jobs:

- **A router connects your coding agent to models.** It handles model requests,
  provider choices, and fallback when a provider is unavailable.
- **A session tool helps you work with coding agents.** It shows their progress,
  manages their workspaces, or helps you review their changes.

**CCC is a local dashboard for coding sessions, including sessions you started
outside CCC.** For its Free ($0) runs, it also manages a freellmapi router.
The router supplies the models; CCC shows the work. [CCC README][ccc]

![How it compares: CCC shows coding sessions; freellmapi, 9Router, and OpenRouter route model requests; Claude Squad, Conductor, and Nimbalyst manage parallel coding work. Crystal has been replaced by Nimbalyst.](images/how-it-compares.svg)

[Open the full-size diagram](images/how-it-compares.svg). The tables below are
its text equivalent, with a source for each tool.

## Routers: the model connection

These are backends, not rivals to CCC's session board. They have their own
interfaces, but the comparison here concerns their model-routing role.

| Tool | What its public README describes | How it fits with CCC |
|---|---|---|
| **freellmapi** | One OpenAI-compatible API over multiple providers' free tiers, with provider fallback and per-key usage tracking. [README][freellmapi] | CCC installs and manages it for Free ($0) sessions. You can also keep your existing freellmapi. |
| **9Router** | A local gateway with OpenAI/Claude format translation, quota tracking, automatic fallback, and rotation across provider accounts. [README][9router] | CCC can detect an existing 9Router gateway. Detection is not an endorsement of every authentication method the gateway supports. |
| **OpenRouter** | A single API for models from multiple providers, with automatic failover and load balancing. Its setup uses an account and API key. [Official app-directory README][openrouter] | OpenRouter is one of the providers available through CCC's free router. Check the selected model's price and data policy; not every model is free. |

See [Free models](free-models.md) for CCC's setup, supported providers, limits,
and privacy tradeoffs. A free tier is not unlimited capacity.

## Session tools: the coding workflow

These tools overlap with CCC on running or managing coding sessions. This table
summarizes documented workflows, not a ranking or a list of missing features.
A **git worktree** is a separate working folder for a branch, so parallel tasks
can edit different copies of a repository.

| Tool | Workflow described in its public README | Source |
|---|---|---|
| **CCC** | A local session board across multiple agent engines. It reads the state agents already write, including sessions launched by hand in a terminal. | [CCC README][ccc] |
| **Claude Squad** | A terminal app for multiple local agents, including Claude Code, Codex, Gemini, and Aider. Tasks use isolated git workspaces, with background work and change review. | [README][squad] |
| **Conductor** | A macOS app for running coding agents in parallel in isolated git worktrees. Each workspace has its own worktree and branch. | [Official starter-project README][conductor] |
| **Nimbalyst** | A desktop visual workspace with editors for code, markdown, mockups, and diagrams; parallel agent sessions in git worktrees; a session board and task tracking. | [README][nimbalyst] |
| **Crystal** | Replaced by Nimbalyst. Its README says Crystal was deprecated in February 2026 and directs users to Nimbalyst for active updates. | [Migration README][crystal] |

Choose by the workflow you want:

- Want one board for sessions you already run in different tools? Look at CCC.
- Prefer managing parallel tasks in your terminal? Look at Claude Squad.
- Want parallel, branch-isolated workspaces in a macOS app? Look at Conductor.
- Want to edit documents, mockups, and code alongside your agents? Look at
  Nimbalyst. If you found an older Crystal recommendation, start there too.

## How CCC and a router work together

For a CCC-managed Free ($0) session:

```text
CCC starts and shows the coding agent
                         |
                  model requests
                         v
                    freellmapi
                         |
                         v
                  model provider
```

CCC is not the model and does not replace your coding agent. The router carries
model requests; the agent edits files and runs tools. You can still use your
normal paid sessions alongside free sessions.

**CCC never sends your Claude subscription OAuth token through its free router.**
Free routing uses the router's own credentials, set for that session only.
Your model requests can still reach third-party providers, whose limits and
data-use terms apply. Read the [privacy and ToS boundary](free-models.md#privacy-and-the-tos-boundary)
before choosing a free provider.

## Sources and scope

README snapshot checked **6 October 2026**. Links below pin the exact revisions
reviewed, so later README changes do not silently change the evidence. The
Conductor source is its official starter-project README, not a comprehensive
product feature list. The OpenRouter source is its official app-directory
README, not a claim that the hosted service is a local router.

This is a documentation comparison, not a performance test. We have not
benchmarked these tools against one another. A feature omitted from a README
is not treated as absent. Model catalogs, pricing, and product features change;
check each project's current documentation before choosing.

- [CCC README][ccc]: local board, multiple engines, externally launched sessions,
  and managed free runs.
- [freellmapi README][freellmapi]: provider aggregation, API compatibility,
  fallback, and usage tracking.
- [9Router README][9router]: local gateway, format translation, quota tracking,
  fallback, and multi-account rotation.
- [OpenRouter official app-directory README][openrouter]: unified model API,
  provider failover, load balancing, and API-key setup.
- [Claude Squad README][squad]: terminal interface, agents, isolated workspaces,
  background tasks, and review.
- [Conductor official starter-project README][conductor]: macOS app, parallel
  agents, and per-workspace worktrees and branches.
- [Nimbalyst README][nimbalyst]: visual editors, parallel sessions, session board,
  and task tracking.
- [Crystal migration README][crystal]: deprecation and replacement by Nimbalyst.

[ccc]: https://github.com/amirfish1/claude-command-center/blob/62310cf63a007991be9b24a2d03e82216b7664ed/README.md
[freellmapi]: https://github.com/tashfeenahmed/freellmapi/blob/653082378728d83f1bb2bc49a79e14986f3c8768/README.md
[9router]: https://github.com/decolua/9router/blob/aa2bc53f3a1f3ba481649bf8f4c5997df1fc55f3/README.md
[openrouter]: https://github.com/OpenRouterTeam/awesome-openrouter/blob/17b880faab67db699594c84c9ffb68578ec972e4/README.md
[squad]: https://github.com/smtg-ai/claude-squad/blob/52aa2dd50f1a86494f0413aa04decb5a36e3ebb5/README.md
[conductor]: https://github.com/meltylabs/Starter-Project/blob/ff5caa01cf4502f9819553a193e4cc7ac4ec40f3/README.md
[nimbalyst]: https://github.com/nimbalyst/nimbalyst/blob/6ae43a594d162096fa6bf2e67fd7fbbb51793b52/README.md
[crystal]: https://github.com/stravu/crystal/blob/1ffa091fde5c0779ed4d5b08b7dacbc39eeb2224/README.md
