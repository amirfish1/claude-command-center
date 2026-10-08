# Free models: your data and account boundaries

**A free model is a different provider, not extra Claude subscription capacity.**
When you choose **Free ($0)** or approve **Continue free**, your coding agent
uses the local router to reach a non-Anthropic provider. Your normal Claude
plan stays separate.

## What the router carries

- The prompts and responses for the free session.
- Conversation history when you resume a session with **Continue free**.
- File contents and tool results that the agent includes in its requests.
- The router's own local unified key. If you enable a provider that needs an
  API key, the router uses that provider key for its upstream calls.

The router listens on your own machine. The model provider is remote, so
**the conversation can leave your machine**. Continuing an existing session
free can share earlier work, not just the next prompt. Review the conversation
before switching providers.

## What the router never carries

- Your Claude subscription OAuth token.
- Your Anthropic paid API key or Claude account login credentials.

CCC sets routing for one child process at a time. It does not write free-router
settings to `~/.claude/settings.json`. Free spawns and failover resumes remove
inherited paid credentials before adding the router's own key.

After **Switch back**, the free child retires, once its current turn finishes.
Your next normal resume uses your usual plan. Switching back does not send a
prompt or spend a paid turn just to change providers.

## Keyless does not mean private

**Kilo's keyless free tier logs prompts and outputs for training.** CCC shows
this disclosure and asks for consent before enabling it. No signup and no
payment card does not mean no data collection.

Only use that tier for work you are comfortable sharing with the provider.
Do not put passwords, API keys, customer data, or confidential code into the
conversation. Other providers have their own logging and retention terms;
check those before enabling them.

Free providers still have usage caps. They can rate-limit requests, change
models, or stop serving. CCC does not bypass those caps. Using a compatible
API does not establish that every vendor approves every use of its software.
Follow the terms of Claude Code, the router, and the provider you choose.
This page describes CCC's technical boundary, not legal advice or a promise
of vendor approval.

## Verify failover yourself

The opt-in test starts real Claude Code in a fresh test HOME, adds a synthetic
limit stop to its transcript, approves **Continue free**, checks a real file
written by the resumed agent, and then switches back. It uses the normal CCC
watcher and HTTP actions, not a fake model or a pre-filled failover store.

```bash
CCC_E2E_FREE=1 \
CCC_FREELLMAPI_SRC=/path/to/freellmapi \
CCC_E2E_CCC_PORT=9202 \
CCC_E2E_KEEP=1 \
./scripts/e2e-failover.sh
```

Use Python 3.12 or newer, Node 20.18 or newer (below 25), npm, Git, and the real
Claude Code CLI. Set `CCC_PYTHON` or `CCC_E2E_CLAUDE_BIN` if they are not on your
PATH. Your Python must have CCC's WatchTower dependency available. A built
router checkout can save the local build step. The test copies sources into
its own HOME and excludes the checkout's credentials and database. It never
installs a LaunchAgent or touches an existing router. Automatic agent CLI
updates are disabled in the test HOME before CCC starts. Port 8090 and port
3017 are refused so your main services are not used by mistake.

For an automated run:

```bash
CCC_E2E_FREE=1 CCC_FREELLMAPI_SRC=/path/to/freellmapi \
python3 -m pytest tests/test_e2e_failover.py -q -s
```

The real test is skipped without `CCC_E2E_FREE=1`. Setting it consents to sending
only the test's synthetic conversation to keyless Kilo. It does not consent
for your other projects. No real account-limit exhaustion or authenticated
paid turn is tested: the initial conversation also uses the free router, and
switch-back is checked without sending a paid request.

The test captures router request counters before and after failover. It
requires successful requests with tokens, served by eligible Kilo `:free`
models, plus `$0` and switch-back markers in the same transcript. This proves
free inference traffic; it is not an invoice check. The router's estimated
cost and savings figures are comparisons with paid API prices, **not charges**.
Missing or incomplete analytics fail the test instead of counting as zero.

With `CCC_E2E_KEEP=1`, private logs and sanitized analytics stay under the test
HOME printed by the script. Keep those artifacts local. The test sends no
report to a public service.

See [Free models](free-models.md) for setup and [Security](../SECURITY.md) for
the localhost trust boundary.
