# Pinned kap-server contract

Snapshots of the two machine-readable contracts Kimi's `kimi web` daemon
serves live, captured from **server_version 0.39.1** on 2026-09-01:

| file | spec | source route |
|---|---|---|
| `openapi.json` | OpenAPI 3.0.3, 100 paths | `GET /openapi.json` |
| `asyncapi.json` | AsyncAPI 3.1.0, 32 messages on `/api/v1/ws` | `GET /asyncapi.json` |

Both require the bearer token at `~/.kimi-code/server.token`.

These are pinned because kap-server is a **private product API with no
compatibility promise** — unlike ACP, which pins `@agentclientprotocol/sdk`
and is a published spec. Trading ACP for kap-server buys steer and structured
approvals at the cost of a surface that can move under us. Diffing these files
on each Kimi upgrade is the mitigation, so they are stored pretty-printed with
sorted keys: a minified blob would diff as one changed line and tell us
nothing.

Refresh both after a Kimi upgrade:

```sh
TOK=$(cat ~/.kimi-code/server.token)
for s in openapi asyncapi; do
  curl -s -H "Authorization: Bearer $TOK" "http://127.0.0.1:58627/$s.json" \
    | python3 -c 'import json,sys; json.dump(json.load(sys.stdin),sys.stdout,indent=2,sort_keys=True); print()' \
    > docs/kimi-kap/$s.json
done
git diff docs/kimi-kap/
```

The consumer is `ccc_server/kap.py`. `tests/test_kap_transport.py` mirrors the
event field names from `packages/kap-server/src/protocol/events-zod.ts`, so a
rename fails a test rather than silently producing empty turns.
