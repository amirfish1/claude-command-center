"""Bundled conversation-history indexer.

Vendored from the standalone `claude-index` project so CCC can drive
ingest, embedding, and search without an external pip dependency.
The on-disk SQLite file lives at ~/.claude-index/index.db (shared with
any standalone claude-index install — they coexist on the same file).
"""
