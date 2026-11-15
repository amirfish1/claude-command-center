# Measured managed restart control

The explicit `POST /api/restart/managed` endpoint and
`scripts/managed_restart_control.py` launcher submit data to the existing
one-shot root authority. They do not grant restart permission themselves.
Ordinary dashboard and worker restart paths retain the conservative native
turn barrier. No service, container, authority anchor or accepted proof is
installed automatically.

## Trust and launch boundaries

The operator-controlled anchor must use `ccc-root-authority-v2`, pin the exact
authority image, executable-artifact digest and **actual** authority profile
digest, and contain `managed_acceptance` with `manifest_sha256` and a nonempty
acceptance `reference`. Version 1 anchors can still attest ordinary launches
but cannot select a managed restart. Changing the source, policy, dependency,
actual profile, native binary/schema or ordered transition requires new proof
and acceptance. Requested profile hashes and source ancestry are insufficient.

The authority itself is the distinct, measured control role. It retains its
existing fixed Docker socket and root anchor mounts and capabilities; workers
receive neither. Worker and replacement use `worker_profile(...,
control_stdio=True)`. An optional `managed_socket` names an exact private Unix
socket below `/state`; it never launches or adopts a native daemon. Native
subprocess execution and plugins remain denied by the kernel/source guard.
The profile permits reads of the existing, validated claim-journal filename
family, alongside exact state filenames. It never permits state imports or
mutable configuration/code reads.

Generic attestation `release` refuses this managed profile, including in the
root operation itself. Only the accepted coordinator can release a replacement.
Initial worker bootstrap and the first legacy transition remain separately
gated; preparing a stopped launch is not permission to run its recovery code.

The request body has `anchor_path` and `request`. The latter has exact `worker`
and `replacement` entries in `profiles` and `identities`, the base64-encoded
manifest bytes in `manifest`, and base64 evidence bytes keyed by digest in
`evidence`. Process identities include the application and guardian PID/start,
boot ID and exact container launch. A bounded, hashed stdin packet avoids the
environment variable size limit. No request field can provide root acceptance
or manufacture authenticated control facts.

## Transaction

1. Authenticate all roles, including the stopped replacement admission guard.
2. Ask the measured worker's fixed Docker stdio handler to take the existing
   admission freeze and ledger drain and persist a continuity receipt.
3. Re-observe all exact execution identities under the freeze. Validate the
   root-accepted runtime manifest, all required native proofs and evidence,
   exact official binary/schema, per-thread native queue/turn/goal state, and
   acknowledged claim receipts. Unknown classes and outstanding writes refuse.
4. Seal the durable fence, take the final native snapshot, and re-authenticate
   all roles. Any changed identity, epoch, acceptance or receipt refuses.
5. Stop only that exact worker container. Re-verify the replacement guard and
   release its first application/recovery input only after the final proof.
6. Re-subscribe through the existing managed client, verify native continuity
   again, and complete only the exact inherited fence and owned drain. Changed
   native goal classes refuse before admissions reopen.

The stdio protocol accepts only begin/final/abort/ready/resume observations.
It cannot dispatch generic worker methods, commands, code or process signals.
Its capture replies cannot be mistaken for a successful legacy restart gate.
Claim observations contain receipt metadata and hashes, not prompt bytes.

A lost reply is uncertain. The launcher never retries a seal, stop, release or
resume; the durable receipt and original claim journals remain the recovery
source. Before sealing, an abort can release only its own freeze and drain.
After sealing, refusal holds the fence and replacement admission rather than
interpreting unknown state as idle. An API error reports an unknown outcome
and a queue fallback; it does not assert that no action happened.

## Verification limits

Offline negative controls and ordinary Python worker/container checks establish
source behavior and transport boundaries. Synthetic proof fixtures do not
establish native semantics or authorize runtime operation. Activation still
requires actual accepted native lifecycle, queue, goal and ordered-transition
proofs, an operator-provisioned root anchor, and complete compatibility checks.
