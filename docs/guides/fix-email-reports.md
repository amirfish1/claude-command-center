# Let an agent fix bugs reported by email

Add an intake email channel in Settings > On-call, and add trusted reporter addresses. Keep WatchTower running. Run this local example to watch email trigger a real fixing agent:

```console checked slow
$ python3 scripts/oncall-proof.py --with-agent
email SMTP / IMAP / reply              PASS
agent execution / approved fixed notice PASS (automatic WatchTower dispatch)
```

![Checked terminal output](img/fix-email-reports.png)

Terminal capture rendered from the checked output above, with lines wrapped.

## What you will see

A local TLS mailbox receives an addition bug. WatchTower starts gpt-6.1-terra at medium effort. The proof requires a new commit, unchanged tests, a passing test, and ticket closure with that commit.

## Limits

The example uses GreenMail, not an external mailbox. It fixes one seeded Python bug and does not prove recovery after a watcher restart. Unknown reporters need approval. No automatic acknowledgement goes to the reporter.

## Not run here: needs a real account

For a real mailbox, choose Email in Settings > On-call. Set the address, owner address, username, and app password. Enable customer intake. Custom servers need SMTP and IMAP hosts and ports. SMTP uses TLS or STARTTLS; IMAP uses TLS. The fields come from the email channel implementation and its transport tests.

[Guide setup and verification](README.md)
