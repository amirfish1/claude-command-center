# Coding agent how-to guides

Start with a problem. Each page gives the commands to use, the result to look
for, and the limits. On-call is a preview and stays off until setup enables it.

1. [Try coding agent bug fixes without an account](try-oncall.md)
2. [Let an agent fix bugs reported by email](fix-email-reports.md)
3. [Answer a Codex question from your phone](answer-codex-from-phone.md)
4. [Turn GitHub issues into fixes](github-issues.md)
5. [Have an agent fix Sentry errors](sentry-errors.md)
6. [Fix a red build on main](fix-red-build.md)
7. [Stop agents asking about small reversible decisions](small-decisions.md)
8. [Tell a customer their bug is fixed after approval](approve-fixed-message.md)
9. [Self-host notifications with ntfy](self-host-ntfy.md)
10. [Get a daily summary](daily-summary.md)
11. [See what your agents did this week](agents-this-week.md)
12. [Keep approval for money and customer messages](approval-boundaries.md)

## Before using the commands

The demo needs Python and Git. It does not need a dashboard, account or Docker.
The other guides assume a running CCC dashboard and WatchTower. Run setup from
the code folder agents should change. A real fixing worker also needs an
installed, logged-in agent. See [installation](../install.md) and
[On-call setup](../oncall.md#turn-it-on).

The shell variables in the examples hold your own service addresses:

| Variable | Your value |
|---|---|
| NTFY_SERVER | Your ntfy server URL |
| NTFY_TOPIC | A private topic you subscribe to |
| MAIL_HOST | Your mail server hostname |
| SMTP_PORT | Your implicit-TLS SMTP port, usually 465 |
| IMAP_PORT | Your implicit-TLS IMAP port, usually 993 |
| SOURCE_URL | The GitHub or Sentry API address named in that guide |
| CCC_SERVER | Your local dashboard URL |
| TICKET | The customer-originated ticket to message |

Set these in your shell before running the relevant commands. Replace
example.test addresses, repository names, organization and project slugs with
yours. Do not use the example addresses for real mail. Store mailbox and source
secrets in CCC Vault under the entry names used in the guide. Never put real
secrets in command arguments.

The checked output is trimmed to stable lines from real runs. Your queue
references, counts, dates and service addresses will differ. Some results are
seeded to make them repeatable; each page says which ones.

## Repeat the checks

From this repository, run `python3 scripts/check-guides.py --quick` for the fast
check. It parses every guide and replays the standalone demo and weekly-count
commands, reusing the result of identical demo commands. It needs WatchTower
for the seeded weekly result, but no Docker or model login. Each guide prints
which transport commands it skipped. It does not validate network setup.

Run `python3 scripts/check-guides.py --commands-only` to replay every documented
command against disposable local ntfy, TLS mail and WatchTower. This needs
Docker and OpenSSL. Each guide prints one result line. Commands run in temporary
code folders. Dispatch is disabled before fixture reports are introduced.

Run `python3 scripts/check-guides.py` for those checks plus real model proofs
for email, Codex questions, Sentry-compatible monitoring and CI. Full mode copies
only Codex auth.json to temporary homes and uses gpt-6.1-terra at medium effort.
It can incur model charges. Allow several minutes plus image-download time.
It does not read Claude credentials or contact external message accounts.
The CI check calls only the existing proof's local Gitea stage, not its public
GitHub probe.

Add `--capture` to the full or commands-only run to save real browser result captures in img/. This requires
Node and the repository's Puppeteer setup. Use `--guide SLUG --commands-only --capture`
to recapture one page. The ntfy captures use its real web app at phone size.
The mail image renders the MIME message fetched from the local reporter inbox.
The weekly image renders the actual CLI result. The decisions image uses the
real On-call settings component backed by the isolated API. The demo image uses
its real local phone page. None are drawings of expected messages.

The shared local ntfy container allows a burst of 1,000 requests so twelve
concurrent guide checks and browser subscribers do not exhaust its default
60-request allowance. On-call delivery limits are unchanged.

The checker uses a temporary HOME, CCC_STATE_DIR, WATCHTOWER_STORE, an allow-listed
environment and a foreground watcher on port zero. Cleanup removes containers,
processes marked with that temporary HOME, and temporary files. Uncatchable host
or process termination can prevent cleanup. Linux is required for the detached
process cleanup used by the agent proofs. Real services are never restarted.

Extractor tests run with `python3 -m pytest tests/test_guides.py -q`.

## Current boundaries

Daily summaries now exist, but they do not replace decision alerts with one
message a day. There is no universal ten-minute stuck-alert guarantee. Normal
Codex spawn uses approvalPolicy=never and dangerFullAccess, so the guides do not
promise a real phone permission round trip through that path. Real Claude
session questions were not exercised. The Codex question guide covers the
engine that was run.

Hosted Sentry, private GitHub permissions, Telegram, SMS, WhatsApp and iMessage
need accounts or devices not used here. The account-dependent sections identify
those limits. Local fixtures are labeled separately from real fixing agents.

## Recorded verification boundary

The latest command replay passed all 30 documented commands across twelve
guides. Local CI and Sentry-compatible monitoring also passed separate real
agent proofs. The latest combined full run stopped in the email proof when
GreenMail rejected its SMTP login; it did not reach the later model proofs.
Earlier email-fix and Codex-question proofs passed, but the combined run needs
to be repeated before treating it as a current full pass. The checker reports
that failure rather than accepting partial proof output.
