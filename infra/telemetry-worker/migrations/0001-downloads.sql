CREATE TABLE IF NOT EXISTS downloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at TEXT NOT NULL,
  artifact TEXT NOT NULL CHECK (artifact = 'ccc.dmg'),
  source TEXT NOT NULL CHECK (source = 'landing-hero')
);

CREATE INDEX IF NOT EXISTS downloads_received_at_idx
  ON downloads(received_at);
