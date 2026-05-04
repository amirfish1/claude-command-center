**In-UI terminal panel.** A new ⌨ Terminal button on the topbar opens a
small one-shot terminal at the bottom of the page — type a command, hit
Enter, output streams back. `cd` is parsed server-side so the prompt's
cwd survives between commands; the path is clamped to the selected repo so
`cd /etc` is rejected. Cancel kills the whole process group, so a
runaway `make -j` or `./deploy.sh` doesn't leave orphans behind.
Up/down arrows recall the last 50 commands. Hotkey: Cmd/Ctrl+`.

Not a real PTY — `vim`, `top`, and any program that prompts for
interactive input will hang. Use `--yes` flags, pipe input on the
command line, or run those from a real terminal.

New endpoints (gated by the existing same-origin check): `GET
/api/term/cwd`, `POST /api/term/run` (SSE), `POST /api/term/cancel`.
This is the most security-sensitive surface in CCC — strictly more
powerful than `/api/inject-input` because there's no Claude permission
prompt in the loop. Do **not** enable network bind (`CCC_BIND_HOST=
0.0.0.0`) without a trusted network. See
`docs/superpowers/specs/2026-05-01-in-ui-terminal-design.md`.
