# CPU priorities

Interactive dashboard work, live session status, and requested speech retain
normal scheduling priority. The maintenance helper (indexing and background
warmers) and archive refresh processes use POSIX nice 10. Archive startup lowers
its priority before compiling or loading server code and metadata. Threads and
subprocesses created afterward inherit that priority.

Set `CCC_BACKGROUND_NICE` in the dashboard environment to tune maintenance
priority (1 through 19), or `0` to keep the launcher's priority. Existing lower
priorities are preserved. Unsupported platforms or denied scheduler changes do
not prevent startup. Restart the dashboard to apply a changed setting.

The local speech queue already serves waiting foreground requests before
background preparation, between synthesized pieces. A piece already running
finishes first. Foreground work in the main dashboard process shares Python's
GIL with background threads; process priority cannot remove that contention.

Nice values are relative weights under CPU contention, not CPU usage limits.
Background work can use idle cores. Service-manager CPU weights apply at a
separate cgroup level on Linux; nice values distribute CPU within those groups.
Agent workers and external compilers retain the policy of their launcher.
