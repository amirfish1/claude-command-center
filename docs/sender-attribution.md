# Sender attribution protocol v1

Attribution describes how a sender was recorded. It is never authentication or
an access-control decision. Programs under the same OS user can forge session
environment variables and registry rows.

The stdlib module `sender_envelope.py` is vendored identically in WatchTower and
CCC. Both repositories run the same PII-free `tests/fixtures/envelope_v1` vectors
and hash manifest. Update both copies together.

An envelope contains `v=1`, a UUID `message_id`, `author` (`kind`, `id`, `node`,
`name_at_send`, `engine`), `basis`, `evidence`, `intended_recipient`, nullable
`delivered_recipient`, nullable `reply_to`, `relay[]`, `created_at`, and
`transport_ids[]`. Snapshot names at send time. Relays preserve the logical ID
and author; delivery and receipt IDs remain separate journal links. Relays record
`attested_mode` when they assert a UDS permission mode.

`basis` is one of `runtime`, `peer_cred`, `claimed`, or `unknown`; `evidence` is
supporting detail, not another category. Runtime means a same-user registry match
at the CLI boundary. Explicit arguments and unmatched headers are claims. A
request cannot upgrade itself by setting `source`. CCC composer input with the
browser Origin and form marker is `human/claimed`, displayed as You (dashboard).
A shell without identity variables is unknown. Services supply fixed identities.

The first controlled sending API creates the UUID before transport. Queues,
workers, federation, and continuation forwarding retain it. A delivered or
queued UDS receipt confirms acceptance. Held frames fall back immediately and
journal `possible_duplicate` with reason `held`: a held frame can later land.
Unknown receipts reconcile for `WT_UDS_RECONCILE_S` (default 30 seconds), then fall
back once and journal the uncertainty. Socket errors fall back directly.

Non-slash text from a session, service or unknown sender carries one short
`[sender: <name> (<kind>) · msg <uuid>]` line (plus `· reply: ccc send <addr>`
when a reply route exists); readers resolve the full record from the journal
by message ID. Text a person types (`human` author) carries no line. Readers
still accept the legacy `[Message attribution v1: <compact JSON>]` line.
Outgoing order is ask correlation line (when present), attribution, body, then
receipt nonce last. Slash commands receive neither attribution headers nor
nonces; their attribution remains in the delivery journal/queue metadata.

Monthly append-only journals live at `~/.watchtower/attribution/YYYY-MM.jsonl`
and `~/.claude/command-center/attribution/YYYY-MM.jsonl`. Each reader checks both
on the same node, read-only, joining logical IDs, transport IDs, or receipt
nonces. Missing journals are tolerated. A foreign-node record never upgrades a
claim. Journal failure logs a diagnostic and delivery continues; the in-band
line remains a name-and-kind claim without durable evidence. Nothing prunes
automatically.
For deliberate retention management, use `python -m watchtower.sender_envelope
prune --app wt --before YYYY-MM`, or `python sender_envelope.py prune --app ccc
--before YYYY-MM` from CCC. Deleted evidence cannot be reconstructed.

CCC's persistent node UUID takes precedence. Standalone WT persists its own UUID;
the earlier WT node file remains a local alias if CCC is installed later. Session
links require local node evidence and a resolving registry row. Reply addresses
are independent of authorship: a live socket or registered CCC route is usable;
otherwise the UI says no reply path. Native and historical messages without a
matching record remain unattributed or sender-provided claims.

For mixed versions, CCC passes the envelope to `wt send` through the one-shot
`WT_MESSAGE_ENVELOPE` environment variable. Old WT ignores it and still accepts
the same CLI arguments; CCC reports a missing `attribution_v=1` acknowledgement.
New WT consumes and deletes the carrier before any child process. Child launchers
also scrub session identity variables. Envelopes are capped at 32 KiB, with at
most 20 recent relay hops.

Set `WT_ATTRIBUTION_EMIT=0` or `CCC_ATTRIBUTION_EMIT=0` in the relevant process to
restore legacy emission. Journal readers remain compatible with old messages.
Deploy readers with emission disabled before enabling writers on both ends.
