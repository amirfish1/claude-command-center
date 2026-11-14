# Antigravity accounts

CCC offers two Antigravity authentication routes:

| Account | Authentication |
| --- | --- |
| Primary | Your existing Google Cloud configuration, environment and native login. An explicitly configured Gemini API key provider is also preserved. |
| Secondary | A separate personal Google OAuth login, for a Google AI subscription. |

Choose the account in the new-session composer, or save defaults in Settings
for new sessions and queue workers. Launch policy supports staged account
changes; its preview shows the account separately from the engine and model.
Workers can follow session defaults or keep an independent account choice.
Changing defaults affects future launches. Existing conversations, replies,
continuations and workers keep their original account.

From the CLI:

```sh
ccc spawn --engine antigravity --antigravity-account primary "Your task"
ccc spawn --engine antigravity --antigravity-account secondary "Your task"
```

Omitting the account uses the saved default for a new session, or inherits the
parent's account for a child. `--continue-from` preserves the source account;
it does not accept an account override. The spawn API accepts the additive
`antigravity_account_id` field (`primary` or `secondary`).
`GET /api/antigravity/accounts?session_id=…` returns account metadata without
credentials. Saved worker defaults use `worker_antigravity_account_id`.

Secondary uses a separate CLI home and native credential scope. It shares the
conversation store so CCC can display and resume its sessions. It does not
copy the Primary login, ADC credentials or API keys. The first Secondary run
may require a personal Google sign-in through CCC's credential prompt; choose
the Google account with your subscription. A Primary sign-in is not evidence
that Secondary is connected.
Complete that sign-in in CCC before starting headless Secondary queue workers.

CCC passes model and provider settings to each process without rewriting the
shared Antigravity settings file. Account choices and conversation bindings
contain identifiers only. An invalid account is rejected rather than launching
on another account.

Antigravity's [authentication guide](https://antigravity.google/docs/install?tab=cli)
describes its native Google sign-in, ADC and Gemini API key modes. The `auth.type`
setting documented for editor integrations is not a CLI setting; CCC does not
pass it to `agy --settings`.
