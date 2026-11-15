# Bounded read-aloud CPU trial (Linux)

This opt-in experiment lowers the relative CPU weight of the current user's
`agents.slice` to at most 30 while neural read-aloud playback is active.
It leaves CPU quotas and persistent service settings unchanged. Other agent
work can run more slowly during speech. Nothing changes until an operator
starts the trial. Hosts without a delegated cgroup-v2 `agents.slice` are
unsupported and leave their policy unchanged.

From the CCC checkout, start a 30-minute trial in an independent user service:

```bash
trial_python=$(command -v python3)
systemd-run --user --quiet --collect --unit=ccc-tts-priority-trial \
  --working-directory="$PWD" --property=RuntimeMaxSec=3700 \
  --property="ExecStopPost=$trial_python -m ccc_server.tts_priority --restore" \
  "$trial_python" -m ccc_server.tts_priority --watch 30
```

Trials are capped at 60 minutes. The browser must load the updated dashboard
and its server must support `/api/free-runtime/tts-playback`. The endpoint only
renews playback leases; it cannot start or extend a trial. Playback sends a
heartbeat every five seconds after the first sound, including buffering gaps.
Pause, Stop, completion and page departure release the lease. Missing heartbeats
expire after 15 seconds, so an abandoned tab cannot retain priority. Background
timer suspension can expire a legitimate read's lease; this favors restoration
over retaining a stale priority change. Multiple tabs have independent leases.
Preparation alone never enables priority, and the trial does not accelerate a
cold first sound before playback begins.

The independent watchdog checks leases twice a second. It restores the exact
original weight on release, expiry or termination. `ExecStopPost` also restores
it after a watchdog crash or forced termination. If another operator changes
the weight, the trial stops and preserves that newer value.

Stop early without restarting anything:

```bash
python3 -m ccc_server.tts_priority --disable
python3 -m ccc_server.tts_priority --status
```

The watchdog restores within its next half-second tick. Confirm `enabled` and
`boosted` are false and check the slice's `cpu.weight` file. The systemd
`CPUWeight` property retains its configured value during this runtime-only
trial. Events, baseline and leases are stored under
`~/.ccc/tts-priority/`; `events.jsonl` records boost/release/recovery transitions.
If the watchdog service cannot start, no priority policy is applied.

For measurement, compare existing `tts_start` and `tts_gap` perf events with
trial transition times, preserving all samples, load and warmup attribution,
and the existing targets. Report the trial as an experiment until actual
playback measurements establish the outcome. Improving one synthesis timing
does not prove the read-aloud gap target has passed.
