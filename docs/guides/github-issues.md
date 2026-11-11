# Turn GitHub issues into fixes with a coding agent

Choose the folder agents should change, then add the repository whose issues you want to watch. Set `SOURCE_URL` to `https://api.github.com` for GitHub; the check used a local API address instead.

Run these from the code folder you want agents to fix, with CCC and WatchTower installed and the dashboard running. Set `NTFY_SERVER` to your ntfy server URL and `NTFY_TOPIC` to a private topic name. Subscribe to that server and topic in the ntfy app. The setup command enables the preview and On-call; it also configures a worker for this folder.

```console checked
$ ccc oncall setup --yes --queue SHOP --repo . \
    --channel ntfy --set server="$NTFY_SERVER" \
    --set topic="$NTFY_TOPIC"
On-call is on: alerts and replies are enabled. Turn it off: ccc oncall off
```

```console checked
$ ccc oncall source add github --set repo=example-org/sample \
    --set base="$SOURCE_URL"
Added GitHub issues. GitHub answered for example-org/sample.
```

## What you will see

New open issues become tickets. Issues from owners, members and collaborators can go straight to the queue. Other authors wait for your go-ahead. The agent works in the folder you selected during setup.

![Result from the local run](img/github-issues.png)

## Limits

Existing issues are skipped by default. Only the newest 25 open issues are read per check. Private repositories need a token with issue read access. Posting a reply needs a user token with issue write access and always waits for approval. CCC does not close the GitHub issue.

## How we checked this

The commands used a local HTTP issue fixture, real WatchTower and real ntfy. A seeded issue became a ticket and its question arrived in ntfy. No live GitHub account was used; this does not prove a GitHub-originated agent fix.

### Not run here: needs a real account

For a private repository, save its token in Vault and add the source token entry in Settings > On-call. Public intake needs no token. No issue or comment was posted outside the local lab.

[Run the guide checks and see prerequisites](README.md#repeat-the-checks).
