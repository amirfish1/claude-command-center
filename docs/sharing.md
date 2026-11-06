# Share a run

[Back to the README](../README.md) · [The ccc CLI](cli.md)

CCC can turn a session into a page or image you can post yourself. It also makes
a card with your weekly totals and a badge for your README.

Everything is built on your machine and saved as a file. **Nothing is uploaded.**
No link is created, and telemetry behaves the same whether you use this or not.

## Share a run

In the dashboard, open a session's menu and pick **Share this run**. You get a
preview, then **Save page** (one HTML file) or **Save image** (PNG). If you change
a box, press **Apply** or Enter: saving stays off until the preview matches it.

From the terminal:

```bash
ccc share <session>                       # replay page and card image, in this folder
ccc share <session> --out run.html        # choose the file name
ccc share <session> --hide "Acme,Project Blue" --keep "invoice"
ccc share --stats                         # this week's card
ccc share --stats --days 30
```

The page is one file with no outside requests, so it opens anywhere.

`ccc share` has no preview. It writes the files straight away, so open them and
read them before you post them.

## What the page contains

| Kept | Left out |
|------|----------|
| Text you typed and the agent's replies, redacted | Tool output and tool arguments |
| Tool names and the program a command ran (`pytest`, `git commit`) | Thinking, sub-agent turns, bookkeeping, IDE selections, hook output, skill text, messages from other sessions |
| Counts: tool calls, files edited, test runs, commits | Timestamps and the session id |
| Elapsed time | |

## How redaction works

Redaction is always on and there is no switch to turn it off.

- **Secrets, paths, emails, links, hosts, IPs, ids and code blocks** are replaced
  with placeholders such as `[secret]` and `[path]`.
- **Passwords:** whatever follows a password-style label or flag is hidden, up to
  the end of the clause, even if it is plain words. So is any word joined to
  digits, such as `Welcome123`, unless it is a known term like `sha256`. A sentence
  that mentions a password can lose more words than it needs to. That is deliberate.
- **Seed phrases, card numbers and PINs:** runs of wallet seed words, numbers split
  into three or more groups, and digits after labels like `pin` or `cvv` are hidden.
- **Names:** only words on a built-in list of common English and developer terms
  stay readable. Anything else becomes `[name-1]` or `[word-1]`. This is why a
  client name CCC has never seen is hidden anyway.
- **You:** your git name, git email, system user name and host name are always
  hidden, even if they are ordinary words. So are your repo and folder names.
- **Final check:** a separate pass reads the finished export. If it finds a known
  token format, a word joined to digits, a value after a password label, a seed
  phrase, a grouped number, a path, email, link or IP, or a word you asked to hide,
  the export stops with an error and no file is written. It checks those shapes
  only, so it cannot tell you that plain sentences are safe.

**Known limit: plain English stays readable.** Redaction hides shapes and unknown
names. It does not understand meaning, so these can survive:

- A private name that is also an ordinary word, such as a company called Summit,
  or a well-known brand that points to your employer or a client.
- Sensitive sentences made of ordinary words, such as `the lawsuit`, `fired` or
  `salary`.
- Street names without a house number, hosts made of ordinary words, and dates
  written out in words.

So read the export before you post it.

Use **Also hide** for this export, or list words in `share-hide.txt` in the CCC
state folder, one per line, to hide them every time. Words you list always apply,
however short or common. **Keep these words** brings back words that were hidden
as names or unknown words, including an unlabelled value you name exactly, so only
list words you are sure about. It does not bring back anything after a password
label, or a token, path, email or link.

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
