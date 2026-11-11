# Keep coding agents asking before money or customer messages

Try the demo and answer `zero`, then `yes`. Its first question and reporter approval wait for you; the final small decision does not. To enable only small reversible choices in your own queue:

```console checked
$ ccc oncall demo
Agent: Fix committed locally. The test passes.
```

Run these from the code folder you want agents to fix, with CCC and WatchTower installed and the dashboard running. Set `NTFY_SERVER` to your ntfy server URL and `NTFY_TOPIC` to a private topic name. Subscribe to that server and topic in the ntfy app. The setup command enables the preview and On-call; it also configures a worker for this folder.

```console checked
$ ccc oncall setup --yes --queue SHOP --repo . \
    --channel ntfy --set server="$NTFY_SERVER" \
    --set topic="$NTFY_TOPIC"
On-call is on: alerts and replies are enabled. Turn it off: ccc oncall off
```

```console checked
$ ccc oncall minor on
Agents now decide small reversible things and keep going. Money, messages to customers, public posts and anything that cannot be undone still come to you.
```

## What you will see

The policy allows minor reversible code choices while preserving consequential questions. A customer notice waits for your approval when that separate approval setting is enabled.

![Result from the local run](img/approval-boundaries.png)

## Limits

This is policy, not an operating-system sandbox. Agents retain their granted tool access. Production changes, money, public posts and irreversible work are not minor decisions. Customer approval can separately be disabled for non-GitHub channels. Never treat the minor switch as permission for those actions.

## How we checked this

The commands ran against an isolated real queue. The image shows an actual ntfy alert for a seeded consequential question. No money moved and no message left the local servers. The demo verifies the customer send is held until its local yes reply.

[Run the guide checks and see prerequisites](README.md#repeat-the-checks).
