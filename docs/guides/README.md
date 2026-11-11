# Coding agent how-to guides

Start with the problem you want to solve. On-call is a preview and stays off
until you enable it. You need CCC, WatchTower and a machine that stays on.
See [On-call setup](../oncall.md#turn-it-on) for installation and channel setup.

1. [Answer a Codex question from your phone](answer-codex-from-phone.md)
2. [Let an agent fix bugs reported by email](fix-email-reports.md)
3. [Turn GitHub issues into work for a coding agent](github-issues.md)
4. [Send Sentry errors to a coding agent](sentry-errors.md)
5. [Stop coding agents asking about small reversible decisions](small-decisions.md)
6. [Give a coding agent a bug to fix from your terminal](report-a-bug.md)
7. [Tell a customer their bug is fixed after approval](approve-fixed-message.md)
8. [Have a coding agent investigate a failing command](failed-checks.md)
9. [Stop coding agent notifications while you work](stop-notifications.md)
10. [Self-host coding agent notifications with ntfy](self-host-ntfy.md)
11. [What coding agents still need your approval for](approval-boundaries.md)
12. [Let known customers report bugs without approving every report](trusted-reporters.md)

## Read the evidence correctly

Every `console checked` block contains a command we ran and selected exact
output lines. Omitted lines are not assertions. Run commands from the repository
root. A block is a terminal capture, not invented example output.

The fast CLI examples use the checker's private HOME, state directory, local
On-call API and real WatchTower queue named GUIDE. No worker runs there. They
start from an enabled configuration with no channels. This is a test fixture,
not instructions to enable a real installation without a channel. SOURCE_URL
is the local fixture address supplied by the checker. Its GitHub and Sentry
endpoints return an empty issue list. The Sentry command receives the fake
value `test-only` on stdin. Do not put real secrets in command arguments.

The slow blocks run existing proof scripts. They use Docker, local ntfy and
TLS mail servers, and a real Codex gpt-6.1-terra session at medium effort.
They copy only Codex auth.json into a temporary home and make paid model
requests. They do not use Claude credentials or send to external accounts.
The email proof checks a real fix; the session proof checks a real reply.
A local push receipt does not establish that a phone showed a notification.

## Repeat the checks

Run `python3 scripts/check-guides.py --quick` for the CLI checks without Docker
or model requests. WatchTower must be installed. Run
`python3 scripts/check-guides.py` for all checks, including the two paid proofs.
Shared proof commands run once, even when several guides cite them. Allow up
to twelve minutes per proof after image downloads; missing tools, failed checks and output drift fail
the command. Quick mode explicitly reports that it skipped the slow proofs.

The checker uses temporary HOME, CCC_STATE_DIR, WATCHTOWER_STORE, an allow-listed
environment and a foreground watcher on port zero. WT_NO_AUTOSTART is set.
Cleanup kills processes carrying that exact temporary HOME marker and deletes
state. The slow proofs remove their Docker containers too. An uncatchable host
or parent-process failure cannot run cleanup. Use Linux for detached-process
cleanup. No real service needs a restart to run these checks.

## Topics we could not promise

There is no On-call overnight digest command. The command-monitoring guide
replaces that proposed topic. A ten-minute stuck-alert guide was dropped because there is no universal
alert deadline; queue health depends on WatchTower and delivery limits. The phone-question guide uses Codex because only that engine
was run in the real session proof. The phone-permission guide was dropped because normal Codex spawn uses
approvalPolicy=never and dangerFullAccess. A real permission round trip was
not verified. Supported approval handlers still have strict allow/deny tests.
Manual bug reporting and turning On-call off replace these two topics.

Telegram, SMS, WhatsApp, iMessage and hosted Sentry need accounts or devices
that were not used. GitHub configuration was checked locally only. Account
steps are labeled in the relevant guides. See each guide's limits before
turning a local proof into a production expectation.
