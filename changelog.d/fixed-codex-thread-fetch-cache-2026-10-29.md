Codex thread lookups and custom-repo reads are cached by file signature, so polled stuck-summary and repo lists stop reopening SQLite and re-resolving paths on every call.
