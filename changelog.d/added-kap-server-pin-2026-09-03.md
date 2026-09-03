`CCC_KIMI_KAP_SERVER=<port-or-server-id>` pins which kap daemon CCC routes
Kimi sessions through. With multiple live daemons registered (e.g. an
interactive TUI's embedded server next to a dedicated `kimi web`), routing
previously picked the newest heartbeat — prompts could land inside the TUI
process. A pin that matches no live daemon reads as "no daemon" and falls
back to ACP like any other miss.
