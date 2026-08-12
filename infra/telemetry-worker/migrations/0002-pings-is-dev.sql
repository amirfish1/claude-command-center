-- Maintainer dev-mode marker on the opt-in ping, mirroring opens.is_dev.
-- Set when the client runs with CCC_TELEMETRY_DEV_MODE=1, so the public
-- stats page can report "with me" and "without me" side by side instead of
-- quietly counting the maintainer's own machine as a user.
-- Historical rows stay 0: we cannot retroactively tell whose they were, and
-- guessing would be worse than a known small overcount on old days.
ALTER TABLE pings ADD COLUMN is_dev INTEGER DEFAULT 0;
