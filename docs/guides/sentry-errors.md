# Have a coding agent fix Sentry errors

Save an event:read token in CCC Vault as `guide-sentry`. Set `SOURCE_URL` to your Sentry URL, normally `https://sentry.io`. Replace the organization and project slugs.

Run these from the code folder you want agents to fix, with CCC and WatchTower installed and the dashboard running. Set `NTFY_SERVER` to your ntfy server URL and `NTFY_TOPIC` to a private topic name. Subscribe to that server and topic in the ntfy app. The setup command enables the preview and On-call; it also configures a worker for this folder.

```console checked
$ ccc oncall setup --yes --queue SHOP --repo . \
    --channel ntfy --set server="$NTFY_SERVER" \
    --set topic="$NTFY_TOPIC"
On-call is on: alerts and replies are enabled. Turn it off: ccc oncall off
```

```console checked
$ ccc oncall source add sentry --set org=example-org --set project=sample \
    --set base="$SOURCE_URL" --vault token=guide-sentry
Added Sentry. Sentry answered for example-org/sample.
```

## What you will see

New unresolved issues become queue tickets. The report includes the error and its source link. The worker uses the code folder you selected during setup.

![Result from the local run](img/sentry-errors.png)

## Limits

Existing issues are baselined rather than backfilled by default. Matching source events does not guarantee a useful fix. Hosted Sentry credentials and account permissions were not exercised.

## How we checked this

The documented commands ran against a local API fixture. A seeded issue became real queue work and a real ntfy message. The separate monitoring proof uses local GlitchTip and a real gpt-6.1-terra worker.

### Not run here: needs a real account

For hosted Sentry, use your organization, project and event:read token with the default hosted address. The local run does not validate your token scopes.

[Run the guide checks and see prerequisites](README.md#repeat-the-checks).
