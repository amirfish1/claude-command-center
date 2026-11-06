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
| Text you typed and the agent's replies, redacted | Tool output, tool arguments, local command output, and console logs (Aider and the Antigravity CLI export no replies, because their reply is one capture of everything printed) |
| Tool names and the program a command ran (`pytest`, `git commit`) | Thinking, sub-agent turns and reports, private planning, bookkeeping, IDE selections, hook output, skill text, messages from other sessions, and anything else CCC can't show you wrote |
| Counts: tool calls, files edited, test runs, commits | Timestamps and the session id |
| Elapsed time | |

## Whose words count as yours

A prompt appears only if CCC can show you wrote it. If it can't, the prompt stays out.
Most engines write nothing that says who sent a message, so a row with no proof never
counts as yours.

- **Typed in a terminal:** Claude Code marks a row typed at its command line, and
  Codex names the program that opened the session. Those two count, including
  messages you queue while the agent is busy. No other engine's transcript shows this,
  so on other engines only dashboard prompts appear. The Claude desktop app, the Claude
  VS Code extension, Codex's desktop, VS Code and phone apps, and very old Claude Code
  transcripts have no such mark unless the row says it came from a person. A script
  driving a real terminal looks the same as you typing, so CCC can't tell them apart.
- **Typed in the dashboard, on any engine:** CCC records a fingerprint of the text when
  you send it (not the text itself) and the transcript row must match it word for
  word. The composer, reply chips, and the new session box all count, once the session
  has an id. Prompts that a button writes for you, such as starting a session from an
  issue, do not. If CCC added its own note to a first prompt, such as a warning that
  the work looks shipped, the row no longer matches and that prompt stays out.
- **Sessions from before this was added:** on Claude Code, a dashboard message is
  matched to its transcript row by time instead, within 30 seconds of the send. The
  first prompt of an older session is never matched this way, so it stays out, and
  the replay may start with a reply. A send with two possible rows matches neither.
- **Sent by another agent or a script:** left out, even if you asked for it. Clicking
  Steer on such a message doesn't change that: it keeps its sender, and if CCC can't
  tell who wrote a steered message, it stays out too.

The fingerprint shows the words match something sent from the dashboard form. It is
not a signature: a program on your machine could imitate that form on purpose. If you
paste someone else's words into the composer, they count as yours.

Some sessions can't be exported at all, and you get an error instead of a file:

- Any session with no prompt CCC can prove you wrote. That covers sessions an agent or
  a queue started, sub-agent sessions, and terminal sessions on engines other than
  Claude Code and Codex.
  Replies repeat what they answer, so they can't go out on their own.
- Any session holding a chat bridge message, such as Telegram, WhatsApp or iMessage,
  on any engine. Leaving those messages out would not be enough, because the replies
  answer those people and quote them.
- Hermes sessions from anywhere but the Hermes desktop app, including cron runs.
  `hermes` command line sessions export only if every message in them was sent from
  the CCC dashboard, which is rare, because the first prompt of a spawned Hermes
  session usually can't be matched. A script can forward someone else's message into
  one, and nothing in the session shows the difference.

**Known limit: replies to dropped messages stay.** In a session with at least one of
your prompts, a message from another agent is left out but the reply to it is not. The
reply is redacted like any other, and may still repeat what that agent said. Read the
page before you post it.

## How redaction works

Redaction is always on and there is no switch to turn it off.

- **Secrets, paths, emails, links, hosts, IPs, ids and code blocks** are replaced
  with placeholders such as `[secret]` and `[path]`.
- **Passwords:** everything after the word `password`, `passphrase`, `passcode` or
  `api key` on that line is hidden, and the words before `is my password` too. If the
  word ends the line, sits in a table or CSV header, or the line ends in a colon or a
  word like `is` or `below` (`The password for the router is:`), the whole block below
  it goes, up to the next blank line. So does whatever follows a label such as `secret:` or
  `token =`, and any word joined to digits, such as `Welcome123` or `Welcome.123`,
  unless it is a known term like `sha256`. A line that only talks about passwords,
  such as `fix the password reset flow`, loses its ending. That is deliberate: no
  rule can tell that sentence from a real password. The same goes for near misses
  such as `pasword`, `wifi:`, `p:`, `p/w`, `code word`, `vault key`, `signing key`,
  `log in with admin and ...`, `admin / ...` and a short value after `the key is`.
  A password made only of ordinary words can still survive with an unusual label, or
  with nothing near it that marks it as one, such as `sudo orange window`.
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
