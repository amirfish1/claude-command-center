# Tell a customer their bug is fixed after you approve the message

Keep customer message approval enabled in Settings > On-call. After a reported ticket closes, reply yes to its numbered fixed notice. Run the local mail example:

```console checked slow
$ python3 scripts/oncall-proof.py --with-agent
email SMTP / IMAP / reply              PASS
agent execution / approved fixed notice PASS (automatic WatchTower dispatch)
```

![Checked terminal output](img/approve-fixed-message.png)

Terminal capture rendered from the checked output above, with lines wrapped.

## What you will see

The proof checks the reporter mailbox before approval, approves through local ntfy, and requires one new fixed notice afterward. Its real agent must fix and commit the seeded bug first.

## Limits

This covers one local email reporter. It does not deploy the fix. Customer message approval can be disabled for non-GitHub channels, so this is not an unconditional send prohibition. GitHub comments always require approval. Owner-created reports have no reporter address and cannot receive a fixed notice.

[Guide setup and verification](README.md)
