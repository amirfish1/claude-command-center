Added an experimental kap-server transport for Kimi (`ccc_server/kap.py`),
behind `CCC_KIMI_KAP=1` and off by default. CCC drives Kimi over ACP, which has
no steer method — and the Kimi daemon that does cannot steer a turn an ACP
subprocess owns, because the session store is shared but the live turn belongs
to whichever process holds the engine core. Running the session on the daemon
is therefore the only route to steering it. This lands the transport: daemon
adoption from Kimi's instance registry, a REST client, a stdlib WebSocket
client, and a mapper that reduces Kimi's transcript-op protocol into the same
conversation events the ACP path emits, so the frontend is unchanged. The
OpenAPI and AsyncAPI contracts are pinned under `docs/kimi-kap/` so a Kimi
upgrade shows up as a reviewable diff.
