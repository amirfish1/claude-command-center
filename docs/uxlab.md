# UX lab

`ccc uxlab` runs interactive model usability testers against a disposable test
app. Each person gets a short persona, one goal and a fresh browser. They see
screenshots, visible page text and numbered visible controls, choose their own
next actions (including bounded waits for feedback), narrate expectations and results, and record evidence. This is
qualitative model feedback, not research with real people or an accessibility
audit. Real testers incur model usage on your Codex account.

```sh
ccc uxlab run http://127.0.0.1:8000 --goal "Find the return policy" --people 4
ccc uxlab run http://localhost:8000 --phone --goal "Save a favorite" --output ./trial
ccc uxlab run --again ./trial
```

`ccc uxlab --help` and `ccc uxlab run --help` show options. Goals can repeat;
people cycle through goals in order. Default: four people, alternating desktop
(1365×900) and phone (390×844). `--phone` or `--desktop` sets all people to that
device. People run serially, with one browser and one persistent `codex exec`
per person. Default engine: `gpt-6.1-sol`, medium reasoning. `--model` changes
the model; `--timeout` (default 240 seconds) and `--steps` (default 45 tool
calls) bound each tester. Failed testers preserve partial evidence and do not
prevent the other testers from running. Ctrl-C stops the active engine and
writes an incomplete report. No automatic fixes or dashboard screen.

## Install dependencies

Python 3.10+, Node.js, Codex CLI with a working login, and Puppeteer/Chromium
are needed. Python orchestration uses only the standard library. Install the
browser dependency in the repository root using `npm install --no-save
puppeteer` and, if necessary, `npx puppeteer browsers install chrome`. Browser
resolution uses the repository's `require-puppeteer.js`, including its worktree
fallback. Install and authenticate the Codex CLI separately with `codex login`.
The runner copies only the CLI's `auth.json` into an owner-only temporary home;
keychain-only authentication and custom providers may need separate support.
No global Codex configuration is modified.

Chromium keeps its sandbox enabled by default. If the host disallows its
user namespaces, launch will fail. For a trusted disposable local fixture,
`--no-browser-sandbox` explicitly disables that layer; it is saved in the plan
and preserved on reruns. Do not use this option for untrusted applications.

## Safety and limits

Use a disposable app and fake data. Default hosts are exactly `localhost`,
`127.0.0.1` and `::1`; schemes must be HTTP or HTTPS. `--allow-host HOST` adds
one exact hostname (no wildcard, URL or port). A hostname permits all ports on
that host, and allowed localhost apps can themselves contact external systems:
these filters do not sandbox server-side behavior. Never target an app backed
by real customer data, money or messages. DNS aliases are not an IP sandbox.

Every intercepted browser request, including redirects and frame requests,
is checked against the host list. WebSockets, service workers, popups, downloads,
WebRTC and background browser features are blocked or disabled using browser
settings and page guards where available. No old browser session or profile is
used. Only top-frame visible controls are actionable; iframe controls and file
uploads are unsupported. Inline data resources may render, but off-host fetched
assets are blocked, which can change the app's appearance or break it.

Default action and request patterns block pay, purchase, buy, checkout, account
deletion, send, message, invite and related labels/URLs. DELETE requests are blocked by default. Ordinary form writes still have to
pass the fake-value and action checks.
`--allow-risky` relaxes these action and DELETE restrictions; it never
permits off-host traffic, card-like digit sequences or real form values.
Only enumerated fake text values are accepted: Test User, tester@example.com,
sample, Sample test text, ExampleTestOnly!, 000-000-0000, Example Street,
Example City and ABCDE. Select controls accept options with these exact values.
Email fields require the example.com value; financial and identity fields are
prohibited. Request bodies must contain only these values or empty/boolean
values. Enter, arbitrary keys, arbitrary text, selectors, JavaScript, navigation
URLs, network tools and source inspection are not exposed to testers. Restrictive
forms may therefore be impossible to complete; that is not an app defect.

**This is not safe for hostile apps.** Patterns miss euphemisms and can block
innocent labels. A permitted GET can have side effects. Apps can react to typing
and can embed secrets in visible content. Browser defense features vary by
Chromium version. Page guards and prompts are not a security boundary.

Codex runs from an empty temporary working directory and temporary `CODEX_HOME`,
with only the browser MCP configured. Shell, unified exec, apps,
web search and multi-agent features are disabled; the legacy apply-patch flag is also set false, rules ignored, no inherited
plugins/instructions/hooks are copied, and `--sandbox read-only` adds a second
layer. **Builtin tool isolation is not independently guaranteed:** the installed
CLI must honor those feature settings; read-only is not filesystem secrecy,
and OS/system configuration or future builtin tools may still apply. There is
no OS network jail around the model CLI or browser. Authentication/inference
calls are expected; browser traffic is limited as described above. Use only
trusted apps and review `engine.jsonl` before trusting a trial. Do not treat
prompt instructions as enforcement. A hardened tool allowlist/OS sandbox is
needed before using this with untrusted apps or sensitive machine state.

## Artifacts and replay

A new output directory contains `plan.json`, per-person `result.json`,
`trace.jsonl`, `engine.jsonl`, stderr and screenshots, plus merged
`findings.json`, `comparison.json` and `report.html`. Default output is a new
child of `uxlab-runs/`. An existing output directory is refused. Artifacts may
include visible private data; keep them local and out of version control.
The self-contained HTML embeds screenshots and escapes model/app content,
uses light/dark colors, orders worst findings first, shows distinct tester
confirmations, steps, viewport, expected/actual and what worked.

Duplicate keys normalize page (without query/fragment), control and symptom.
All evidence is retained; multiple observations by one person count once.
This is deterministic text matching, not semantic merging; paraphrases can
remain separate and query-specific variants can merge.

`run --again DIR` reuses the exact saved people, goals, devices, model, budgets
and safety settings. It rejects replacement options, and saves a new uniquely
named child round without overwriting earlier artifacts. Only `--queue` may
be added explicitly. Each person receives their earlier reproduction steps
and must record a present/absent/not checked replay verdict with steps and a
current screenshot. A finding is absent only if every confirming tester
explicitly replays it and reports absent. Failed/missing replays are not
checked. These are model judgments; replay steps are not mechanically proven.
Simply failing to notice an issue never marks it fixed. An absent replay can
also reflect a corrected initial diagnosis, not an app change. See the trial
report for an example.

## Optional WatchTower filing

No tickets are filed unless `--queue NAME` is present on that invocation.
The runner calls `wt add -q NAME --title ... --note ... --dedupe-key ...
--no-dispatch` after saving the report. Filing outcomes are persisted in
`filing.json`, including failures; queue errors cannot lose the report.
`--no-dispatch` prevents immediate dispatch, but WatchTower may later pick up
tickets under normal queue policy. No automatic fixes run inside UX lab.

## Local trial fixture

See [the sample shop](../tests/fixtures/uxlab-shop/README.md). Its server picks
an ephemeral loopback port and has no dependencies. Its separate seed manifest
is for reviewers only and is never served or included in tester prompts.

See [the measured sample trial](uxlab-trial.md) for detections, false findings,
setup failures and verification limits.
