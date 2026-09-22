- Opening a Hermes conversation no longer scans every session row in the
  profile's database just to resolve its parent/child lineage — it now
  walks the chain with one targeted lookup per hop, so open time stops
  growing with total Hermes session count.
