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
| Text you typed and the agent's replies, redacted | Tool output, tool arguments, local command output, and messages other people sent through a chat bridge |
| Tool names and the program a command ran (`pytest`, `git commit`) | Thinking, sub-agent turns and reports, private planning, bookkeeping, IDE selections, hook output, skill text, messages from other sessions, queued messages |
| Counts: tool calls, files edited, test runs, commits | Timestamps and the session id |
| Elapsed time | |

## How redaction works

Redaction is always on and there is no switch to turn it off.

- **Secrets, paths, emails, links, hosts, IPs, ids and code blocks** are replaced
  with placeholders such as `[secret]` and `[path]`.
- **Passwords:** everything after the word `password`, `passphrase`, `passcode` or
  `api key` on that line is hidden, and the words before `is my password` too. If the
  word ends the line or sits in a table or CSV header, the whole block below it goes,
  up to the next blank line. So does whatever follows a label such as `secret:` or
  `token =`, and any word joined to digits, such as `Welcome123` or `Welcome.123`,
  unless it is a known term like `sha256`. A line that only talks about passwords,
  such as `fix the password reset flow`, loses its ending. That is deliberate: no
  rule can tell that sentence from a real password.
- **Seed phrases, card numbers and PINs:** runs of wallet seed words are hidden,
  whether punctuation, numbers or bullets like `a)` and `2nd` separate them. So are
  three or more groups of digits that add up to at least seven, across any punctuation
  or line breaks. After `pin`, `cvv`, `otp`, `2fa` or a phrase like `door code` or
  `verification code`, every digit on the line goes, and the block below if the word
  ends the line. `pin the version to 3.2` is left alone.
- **Numbers:** a bare number of four or more digits becomes `[number]`, because it
  could be a PIN or part of a card. That includes years, ports and counts, so
  `PORT=8080` loses its value. Numbers with a thousands comma go too (`185,000`). A
  number with a unit stays (`1200ms`, `250MB`). Add a number to **Keep these words**
  to bring it back.
- **Names:** only words on a built-in list of common English and developer terms
  stay readable. Anything else becomes `[name-1]` or `[word-1]`. This is why a
  client name CCC has never seen is usually hidden anyway. So is a capitalized
  compound such as `BrightPath`, unless it ends like a type name (`ConfigParser`).
- **You:** your git name, git email, system user name and host name are always
  hidden, even if they are ordinary words. So are your repo and folder names, apart
  from generic ones such as `src` or `apps`.
- **Final check:** a separate pass reads the finished export and splits it into
  words and numbers for itself. It stops the export with an error, and writes no
  file, if it finds a known token format, letters and digits fused into one word,
  readable text after a password or `secret:` style label, digits after a PIN word,
  a run of seed words, a run of ten or more digits, a number of five or more digits,
  an email, IP, home-style path, link with a scheme, or a word you asked to hide. It
  is a backstop for those shapes only: it does not recheck every detail above, such
  as a bare domain or a relative folder path, and it cannot tell you that plain
  sentences are safe.

**Known limit: plain English stays readable.** Redaction hides shapes and unknown
names. It does not understand meaning, so these can survive:

- A private name that is also an ordinary word, such as a company called Summit,
  or a well-known brand that points to your employer or a client.
- Sensitive sentences made of ordinary words, such as `the lawsuit`, `fired` or
  `salary`.
- A private name built from ordinary words in lower case or with an underscore,
  such as `bright_path`. It looks the same as a variable name.
- Personal names that are also on the word list. Common ones are hidden when
  capitalized; a rare one may not be.
- Street names without a house number, and host names that end in no known
  suffix (`prod-db-01` and `vault.home` are hidden; `billing` is not).

Dates with a year (`March 14, 1985`, `14 Mar 85`) and a day after a birth word
(`dob 3-14`) are hidden.

So read the export before you post it. Beside the preview, the dialog lists the
capitalized words and numbers that are still readable, most frequent first, leaving
out common sentence openers and any word also written in lowercase. It shows 60 at
most, so it does not replace reading. Click one to add it to **Also hide**, then
press Apply.

Use **Also hide** for this export, or list words in `share-hide.txt` in the CCC
state folder, one per line, to hide them every time. Words you list always apply,
however short or common, and beat **Keep these words**. A list too long to use
whole (over 1,000 entries, or an entry over 400 characters) stops the export rather
than being shortened. **Keep these words** brings back words that were hidden
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
