# Disposable managed restart proof preparation

The preparation command writes data and starts no network listener, native
process, container, or service:

```sh
python3 -m scripts.codex_restart_preflight prepare input.json packet.json
python3 -m scripts.codex_restart_preflight execute # deliberately refuses
```

`input.json` has format `ccc-disposable-proof-preparation-v1` and these fields:

| Field | Required value |
| --- | --- |
| `grant` | Existing operator reference, timezone-qualified `expires_at`, `max_lifetimes` <= 2, `max_completions` <= 48, `max_lifetime_seconds` <= 1200; expiry may be null in preparation only |
| `images` | `worker`, `replacement`, `control`, `native`, each with immutable `image_id`, canonical rootfs `artifact_sha256`, and local `manifest` filename |
| `native` | Supported `version`, `binary_sha256`, `schema_sha256`, and local `schema_index` filename |
| `state_root` | Exact absolute private data mount source |
| `user` | Nonroot numeric `uid:gid` |

The native image contains the official pinned executable at
`/opt/ccc-native/bin/codex`, its schema index and schema tree, and the same
committed CCC source as the worker/control images. Build images from committed
bytes using `execution_artifact.committed_source_tar`, retaining the measured
interpreter, dependencies and generated-source catalog. Remeasure the resulting
stored image with `DockerImages.measure`; labels and Dockerfile intentions are
insufficient. Packaging must not start native or run a proof. The guarded
worker's execution policy remains unchanged.

Preparation checks all role source hashes and reports `rebuild_required`. It
leaves process IDs, namespaces, inode identities, measured profile hashes and
acceptances unknown. `execution_ready` and `runtime_authorized` are false.
The printed packet hash covers canonical JSON bytes **without** the output
file's trailing newline. The output is private, exclusively created and never
overwritten. A preparation packet is not a launch authorization.

## Independently controlled execution boundary

A separately authorized operator owns the following steps. This document does
not grant permission to perform them:

1. Measure the existing one-shot root authority, retain its exact capabilities
   and mounts, provision the private `noexec,nosuid,nodev` data mount, and create
   the fixed nonroot, cap-zero, read-only native/harness launch with no host
   sockets. Put the actual host namespace witness and run ID in a fresh packet.
2. Independently accept those exact canonical bytes in a
   `ccc-root-authority-proof-v1` anchor's `execution_acceptance`. Set readiness
   only on the exact packet being accepted. Runtime authorization stays false.
3. Join guarded worker and replacement launches to the actual harness network
   namespace using `bound_profiles(packet, harness_container_id)`. IDs remain
   lookup hints until root/kernel observation verifies them. Native and fixture
   stay outside the guarded workers' lifetime and cgroup.
4. Supply a root-controlled controller contract and attach the existing harness
   to the bounded relay. The relay neither creates those launches nor accepts
   a manifest. No command strings, host code or configuration mounts are
   accepted through worker/control input.

The controller contract format is `ccc-disposable-proof-controller-v1`. It has
`packet`, its canonical `packet_sha256`, `harness_container_id`,
`proof_anchor_path`, `fixture_anchor_path`, `roles`, `owned_roles`,
`fixture_control`, `acceptance_wait_seconds` (at most 60), and `scenarios`.
Each role is `{role, identity, profile}` with full kernel/container identities
and the closed requested policy. Scenario keys are `case/path`: cases `idle`,
`active`, `ack-loss`, `queue`, `goal`, `approval` each use `planned` and `crash`;
`corrupt` and `C6` use `refusal`. Each independently controlled scenario names
`old`, `replacement`, the existing authority `request`, and where needed
`replacement_bootstrap` or `expected_refusal`. The operator updates exact role
contracts between scenarios; changing the accepted packet mid-run refuses.

```sh
python3 -m scripts.codex_restart_bridge --serve --attach \
  --contracts /operator/controller.json --drafts /private/proof-drafts
```

`--drafts` must be an existing private directory. Default bridge invocation
refuses. Without `--attach`, `--serve` uses bounded JSONL stdin/stdout; its
caller must invoke `Bridge.finish()` after harness exit. Attach mode does that
in `finally`. Retain the separate post-run cleanup receipt: a harness cannot
observe its own container already absent.

The fixed harness first verifies closure and structural isolation **before**
fixture listening. Full E4, including the double-fork descendant, runs before
each of two native lifetimes. E5 and outer launch remeasurement follow each
run. Any E4/E5 failure, unexpected response, missing capability, unknown write,
or uncertain cleanup permanently halts the durable grant. Nothing resets it.
The schedule has 48 reservation credits; pending busy key races share one
held turn and one native follow-through. A native implementation that cannot
perform that bounded schedule fails; exhausting credits never yields a partial
PASS. Case and P6 branch records retain FAILED/UNRUN states.

P6 invokes the actual CCC HTTP input handler without a second listening
server, and uses authenticated worker RPC for native reads, recovery, and
approval. Raw frames, native history, durable inject receipts and activity
markers determine observations. A configured socket does not authenticate a
managed delivery peer. Missing local CLI bytes are allowed only after the
initialized managed transport, pinned peer and compatible writers are verified.
Pending-approval restart refusal remains a refusal; offline tests cannot turn
that branch into delivered continuity.

After real P1-P5/E4/E5 evidence, the bridge emits fresh planned-P6 and bootstrap
drafts. Their separate root/operator acceptances are required within a bounded
wait; the runner never accepts its own evidence. C6 requires raw, hash-bound
compatibility receipts for stdio, Kimi and other workers, naming the same source
closure, plus an actual runtime-scope refusal. `compatibility_receipts` contains
`passed`, `evidence_sha256`, `source_sha256` per role; the scenario's
`compatibility_evidence` maps hashes to base64 raw receipts. A boolean alone
cannot establish compatibility.

A fixture execution receipt is not a P6 verdict. Runtime still requires completed
native P6, the exact ordered measured profiles, a runtime-scoped manifest and
separate runtime acceptance. Rebuilding source, images, dependencies or profiles
requires new measurements and acceptance. No service restart or selector
activation is part of preparation.
