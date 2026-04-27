**Headless spawns survive CCC restart.** Replaced `subprocess.PIPE`
for `claude -p` stdin with a FIFO opened RDWR (`<log>.stdin`). Because
the child inherits the RDWR fd as fd 0, the kernel's writer count
stays ≥ 1 for the FIFO's lifetime, so a CCC restart no longer EOFs
the subprocess. The reattach sweep reopens a fresh writer end from
`entry["fifo"]`, restoring the inject channel to long-running agents.
The on-disk spawn registry now persists the FIFO path; FIFOs are
unlinked when their subprocess exits. Pre-FIFO entries reattach
without an inject channel — same behavior as before.
