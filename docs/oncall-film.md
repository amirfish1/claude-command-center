# Make a film of a recorded fix

With the On-call preview enabled, run:

```sh
ccc oncall film STOREFRONT-12 --output fix.mp4
ccc oncall film 12 --output fix-dark.mp4 --dark
```

The reference is a ticket reference or an On-call report number. This command
reads local records only. It does not start an agent, answer an alert, send a
message, fetch commits or contact a service. No model is used. It runs only
when requested. The preview stays off by default.

A source checkout with Python, Git, Node and the repository's Puppeteer installation is required. The packaged wheel does not currently include the root Puppeteer resolver.
The command saves an MP4, a poster PNG, an HTML player, a frames directory and
a JSON report beside the chosen output. Existing outputs are not overwritten.
If ffmpeg is missing, it saves the player, frames, poster and report and says
that no MP4 was made. Open the HTML file and press Play. Each caption stays for
at least three seconds. There is no audio. The default theme is light.

The scenes show an excerpt of the report, scoped file operations, a few lines
of the local commit diff, a recorded passing check, the Fixed notice and elapsed
time. A customer message appears only when its stored state confirms it was
sent. The film never sends it again.

## Evidence and privacy limits

The local On-call store must retain the report and Fixed alert. The configured
repository supplies the commit. The local WatchTower JSON or SQLite store
supplies the fixing session, commit and claim-to-close timestamps. GitHub-only
tickets without a local record have missing evidence scenes; no remote lookup
is attempted. Missing commits and transcript details are labelled, not invented.

Transcript extraction currently supports Claude JSONL file tools, repository
filenames referenced in Bash tools, and pytest passing summaries from their
paired results. It reads only the named fixing transcript (up to 64 MB) and
selects the ticket's claim-to-close time window. It does not replay conversation
prose, tool commands, or arbitrary tool output. Codex transcript evidence is not
yet supported. A passing summary is historical evidence, not a fresh test run.

The Fixed notice uses today's `alerts.render` wording. The store does not save
the exact historical channel payload, so this is a reconstruction, not a
screenshot of the owner's phone. Delivery is labelled unconfirmed when no
successful receipt exists. Elapsed time ends at ticket closure, or at the Fixed
alert timestamp when closure is unavailable.

Known reporter names and addresses are replaced with “a customer”. Email and
phone patterns are also removed. `--show-names` keeps these identities. URLs,
common secret patterns and absolute paths outside the repository are removed
in both modes. Free text can contain unlabelled names, postal addresses or
unrecognized secrets; automated redaction cannot guarantee anonymity. Review
the HTML and JSON before sharing. No sharing happens in this command.

## Verification

Run `python3 -m pytest tests/test_oncall_film.py`. On Linux with gcc, Node,
Puppeteer and ffmpeg installed, set `CCC_FILM_BROWSER_TEST=1` to include the
real command test. It removes model CLIs from PATH and blocks IPv4/IPv6
connections in the command and its children. The normal unit tests use a
capture substitute and also check scene selection and the missing-ffmpeg path.
