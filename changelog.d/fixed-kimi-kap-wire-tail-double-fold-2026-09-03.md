Kimi sessions no longer render a turn twice when both kap observers are live.
The wire-log tail (folds TUI-originated turns) and the kap pump (folds
daemon-streamed turns) both wrote the same turn into the CCC transcript when
a prompt was sent over kap while the session was also live in the TUI. While
a kap pump is streaming a session, the wire tail now stays silent but keeps
its cursor moving, so turns after the pump exits still fold.
