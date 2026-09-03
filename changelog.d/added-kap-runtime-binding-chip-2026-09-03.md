The Kimi transport pill now warns when the kap daemon reports the session
bound to a non-`local` runtime (e.g. `acp:<sid>` left by an ACP attach): a
second amber `runtime: acp` chip appears next to the KAP pill, since that
binding means no Bash/Read/Write/Edit/Glob/Grep until rebound.
