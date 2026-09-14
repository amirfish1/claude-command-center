**Decision Inbox triage**: open cards now split into "Needs your decision"
(external / WatchTower / board) on top and a collapsed "Monitoring" section
(governor / idle-session nudges) with a count badge; cards mentioning a
dollar amount get a red money pill. Moment-in-time governor and idle-session
cards auto-expire after a configurable TTL (`governor_card_ttl_s`, default
12h) instead of piling up forever, and a recurring producer's new firing
(source id ending in a date, e.g. the daily check-in) supersedes its previous
open card instead of stacking.
