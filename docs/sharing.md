# Share a run

[Back to the README](../README.md) · [The ccc CLI](cli.md)

CCC can turn a session into a page or image you can post yourself. It also makes
a card with your weekly totals and a badge for your README.

Everything is built on your machine and saved as a file. **Nothing is uploaded.**
No link is created, and telemetry behaves the same whether you use this or not.

## Share a run

In the dashboard, open a session's menu and pick **Share this run**. You get a
preview, then **Save page** (one HTML file) or **Save image** (PNG).

From the terminal:

```bash
ccc share <session>                       # replay page and card image, in this folder
ccc share <session> --out run.html        # choose the file name
ccc share <session> --hide "Acme,Project Blue" --keep "invoice"
ccc share --stats                         # this week's card
ccc share --stats --days 30
```

The page is one file with no outside requests, so it opens anywhere.

## What the page contains

| Kept | Left out |
|------|----------|
| Your prompts and the agent's replies, redacted | Tool output and tool arguments |
| Tool names and the program a command ran (`pytest`, `git commit`) | Thinking, sub-agent turns, bookkeeping |
| Counts: tool calls, files edited, test runs, commits | Timestamps and the session id |
| Elapsed time | |

## How redaction works

Redaction is always on and there is no switch to turn it off.

- **Secrets, paths, emails, links, hosts, IPs, ids and code blocks** are replaced
  with placeholders such as `[secret]` and `[path]`.
- **Names:** only words on a built-in list of common English and developer terms
  stay readable. Anything else becomes `[name-1]` or `[word-1]`. This is why a
  client name CCC has never seen is hidden anyway.
- **You:** your git name, git email, system user name and host name are always
  hidden, even if they are ordinary words. So are your repo and folder names.
- **Final check:** a separate pass reads the finished export. If it finds
  anything that looks like a secret, path, email, link or IP, the export stops
  with an error and no file is written.

**Known limit:** a private name that is also an ordinary word, such as a company
called Summit, can stay readable. That is why you get a preview. Use **Also hide**
for this export, or list words in `share-hide.txt` in the CCC state folder, one
per line, to hide them every time. **Keep these words** brings back words that
were hidden as names. It never brings back a secret, path, email or link.

## The weekly card

The card shows totals only: no session names, repos or paths.

- **Agents** are distinct Claude Code, Codex and Kimi sessions with activity in
  the period. Other engines are not counted yet.
- **Commits** are your commits that reached a remote.
- **Agent turns** come from the same local usage data as the throughput page.

## The badge

```markdown
[![Built with CCC](https://raw.githubusercontent.com/amirfish1/claude-command-center/main/static/built-with-ccc.svg)](https://github.com/amirfish1/claude-command-center)
```

It is a fixed image with no stats and no tracking.

## The first-week reminder

Between day 1 and day 7 after you first open this version, CCC may show one small
note with your totals, once, if you have run at least three agents. It offers to
make a card, and does not ask you to post, star, follow or sign up. Its state is
kept in your browser's local storage and it only calls your own dashboard.
