- Fixed a silent failure mode where a composer message accepted into the
  terminal queue could be retried or dropped forever with zero log output —
  the retry loop now logs a throttled hold line on every outcome, matching
  every other queue-hold reason.
- Added a durable per-session inject delivery receipt, exposed via
  `GET /api/session/<sid>/inject-receipt`, so it's possible to prove whether
  a queued message ever reached a session instead of inferring it from
  interaction timestamps.
