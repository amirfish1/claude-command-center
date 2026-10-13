**Cloud relay presence.** The state snapshot now carries `presence`
(`present`, `last_input_at`) and a per-session `last_human_input_at`, computed
only from prompts you type in a terminal session or send from the dashboard
composer. Relay, peer and WatchTower injections never count. The cloud uses it
to wake an outside agent only when you are away, and to block that agent from
typing into a session you are using. See `docs/cloud-relay/PROTOCOL.md`.
