Keep takeover observation responsive by sharing one owner snapshot per request and bounding worker snapshot reads; unavailable owner state pauses delivery without starting a competing native writer.
Dashboard-owned ACP engines keep their local owner instead of querying an unrelated worker.
