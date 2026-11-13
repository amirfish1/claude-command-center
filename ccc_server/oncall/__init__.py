# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""On-call: agents fix reported bugs and reach a person only when they must.

Preview feature, off unless the ``oncall`` preview flag is on.

    store      config file, SQLite state, the shared message types
    channels   email, Telegram, ntfy, iMessage, Twilio (SMS/WhatsApp), command
    wt         the WatchTower calls On-call makes (one seam for tests)
    alerts     what needs the owner -> one numbered message per incident
    replies    an inbound message -> the answer it carries
    customers  reporters, held reports, ask/tell with owner approval
    sources    Sentry, PostHog, URL and command checks -> tickets
    engine     one tick that runs all of the above; the background loop
    api        the HTTP surface wired in server.py

Stdlib-only. Nothing runs at import.
"""

FLAG = "oncall"
