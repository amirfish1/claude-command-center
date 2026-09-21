Perf telemetry: `archive_load`/`conv_open` no longer count time the page was
suspended without a lifecycle event — an occluded window (another Space),
a frozen renderer, or system sleep can stop all JS for minutes while
`document.hidden` stays false and no `visibilitychange`/`freeze` fires
(the 143s "cold" archive_load that refiled a "[perf] slow archive load"
ticket after CCC-1169). A 2s heartbeat now flags any beat-to-beat gap over
15s as an inactive segment, and `_perfActiveElapsed` merges overlapping
segments so a heartbeat gap + hidden span recorded at the same wake can't
double-count.
