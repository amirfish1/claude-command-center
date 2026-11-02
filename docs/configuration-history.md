# Operational configuration history

Settings → Workspace → Preferences → Configuration history reviews supported
CCC saves and offers Preview undo, then Apply this undo. Preview is read-only.
A restore affects future matching launches, never running sessions or workers.
Browser preference history remains a separate, browser-only ledger.

## Coverage and protected data

Successful explicit legacy and staged launch-default saves record changes to
session/worker engine, per-engine models, effort and compaction, worker linking
and retained overrides, fallback, known managed profiles, engine visibility and
supported worker headroom fields. Read-time factory seeding/backfills are not
recorded. Scoped queue-policy saves record engine/model/effort, failure fallback
and headroom fields, including manual queues. Existing full queue-form saves are
observed after successful completion but are **not reversible**: their native
multi-step setters and service effects are outside this transaction protocol.
External writers, older saves, drain/service operations, credentials, provider
accounts, repository configuration and arbitrary settings are not reconstructed.

The ledger retains at most 50 entries across machine and queue scopes. Entries
contain a timestamp, scope, safe queue name, allowlisted typed before/after
values with explicit presence, operation, outcome and configuration fingerprints.
It never stores full configuration documents, paths, credentials or machine
identities. Models must be in the published in-memory registry. A changed value
containing a custom/unregistered identifier or unknown nested metadata is omitted
and the entry marked non-reversible. Unknown metadata remains in the authoritative
configuration file. This intentionally favors preservation over guessing how to
restore a future schema. Initial creation of the defaults file is non-reversible.

## Restore validation

Undo reconstructs only the recorded allowlisted fields in the current native
file, preserves other metadata, and uses the native engine, effort, routing,
model-policy and atomic publication validators/writers. It preserves a missing
field separately from an explicit empty, false or saved value. Linked workers
restore the exact session/worker tuple and retained override snapshot together;
a stale materialized tuple that no longer agrees with session sources is not
silently recomputed or restored.

The entire target file must still match the entry's saved result. Preview and
Apply also compare both configuration sources, history and model-policy
fingerprints and revalidate current consumer compatibility. This is deliberately
conservative: an unrelated edit in the same file can block an older undo. Changes
after Preview to another covered source also require a new preview. The native
model-policy reader's absent-file/environment behavior is retained; malformed or
unreadable policy files prevent undo. Restoring cannot bypass current model rules.
An already open policy editor must reload before editing the restored state.

## Journal and failure contract

`configuration-history.json` lives beside the caller-selected spawn-defaults
file; configuration remains authoritative in its existing files. Cooperating
history writers serialize with an OS file lock. Machine and scoped queue API
operations also share the native CCC defaults lock. There is **no cross-file
atomicity**, no background recovery process and no global rollback of queues and
machine defaults together.

For supported atomic saves the protocol is:

1. Validate the candidate and revision; durably write a pending allowlisted diff.
   If history cannot be prepared, do not publish configuration.
2. Publish through the native atomic file writer with a final revision check.
3. Sync the configuration directory, then atomically finalize bounded history.

A history read interprets pending intent without modifying any files. If live
configuration matches the intended result, it can expose the confirmed saved
entry and safe undo. If it matches the original, no successful change is claimed.
If it matches neither, outcome is unknown and undo is unavailable. The next write
consolidates this evidence. A final history-write failure reports that configuration
was saved with finalization pending. An uncertain publication reports an uncertain
outcome and requires rereading configuration/history; it never asserts rollback.
Malformed history blocks supported atomic saves and undo rather than discarding
recovery evidence. Legacy full-form saves already completed before observation
can only report history unavailable if observation fails.

External writers do not acquire CCC's lock. Final checks protect against observed
conflicts; the tiny final check-to-replace interval retains the existing native
last-writer contract. Filesystem durability depends on platform support (directory
fsync is used on POSIX). This is recovery evidence, not a distributed transaction,
backup system, broad reset or portable profile format.
