Kimi sessions driven over the kap transport no longer lose their tools. An
ACP attach durably rebinds a session to the virtual `acp:<sid>` runtime (no
fs/process capabilities), which left daemon-served turns without
Bash/Read/Write/Edit/Glob/Grep — the model opened every turn with AgentSwarm
because coordination tools were all it had. CCC now rebinds the session to
the `local` runtime (POST `/api/v1/sessions/{id}/runtime`, verified by a
follow-up GET with a retry for the cold-session case where the daemon's first
POST answers success but persists nothing) before driving a prompt over kap.
Best-effort: daemons without the route degrade to the previous behavior.
