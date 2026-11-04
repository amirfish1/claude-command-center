# Allowances

Open **Allowances** from the Applications rail. The app keeps each account and
product separate and recommends the earliest usable deadline. It does not launch
work or switch the account used by existing sessions.

## Two Codex accounts

In **Add allowance** (or **Edit** on a manual report), enter an account label,
choose **Automatic · sign in with ChatGPT**, then click **Sign in with ChatGPT**.
Open the OpenAI link, sign into the intended account, and enter the one-time code.
Enable device code login in ChatGPT Settings → Security if required.

CCC creates an isolated CLI home under its local state directory and replaces the
manual report only after authentication and a quota read succeed. Cancellation,
expiry, and failed sign-ins keep existing entries. Passwords are entered only on
OpenAI's site; the Codex CLI stores credentials in the isolated home. Existing
sessions keep their current login. This connects a usage reader, not a session
account switcher.

For an existing CLI login, choose **Automatic · existing Codex home**. Each
account needs a separate CLI home. Terminal setup is also available:

```sh
CODEX_HOME="$HOME/.codex-secondary" codex login
```

Add an allowance with **Automatic · existing Codex home**, an account label, and the
absolute directory used above. The directory must exist on the dashboard host.
For two live accounts, configure two distinct homes. CCC runs a bounded, isolated
`codex app-server` reader for each home and caches its readings for five minutes.
It never copies credentials or changes the fleet's active account. The CLI may
refresh its own credentials while reading. Log in with ChatGPT rather than an
API key to obtain subscription quotas.

Weekly and short-window balances are displayed separately when returned. An
exhausted or elapsed window excludes that entire account from recommendations.
Provider errors show **No reading**; missing values never become zero or full.
The plan label is descriptive: percentages from different plans are not summed
or converted into equivalent capacity.

## Research, cloud sessions, and other providers

Use **Manual report** for amounts not supplied by the Codex reader: research
queries, cloud sessions, promotional credits, or another provider's allowance.
Enter the account, product, remaining amount, unit, and reset or expiration date.
The form uses the browser's timezone and stores an explicit timestamp. Leave an
unknown amount blank. Notes can describe suitable work or the report's source.

Manual reports show their observation time. A reset passing marks the report as
needing an update; it never restores the balance automatically. Expired entries,
unknown balances, and entries without deadlines cannot win recommendations.
Recommendations sort by deadline, not model quality or inferred dollar value.
Only use an allowance for work suited to that product.

The ledger is saved on the dashboard host in its local state directory as
`allowances.json`, outside the repository. It contains metadata and configured
CLI home paths, not tokens. `GET /api/allowances` returns entries and derived
readings; `POST /api/allowances` saves one entry or deletes one by ID with
`{"id":"…","delete":true}`. Writes use CCC's existing same-origin check.
