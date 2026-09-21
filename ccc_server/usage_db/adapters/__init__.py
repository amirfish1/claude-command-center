"""Per-engine read-only adapters: ``discover()`` source files, ``parse()`` one file.

Each adapter module exposes ``ENGINE``, ``default_root()``, ``discover(root)`` and
``parse(source_file)`` (returns a list of ``ParsedSession``).
"""

from . import claude_code, codex, kimi

ADAPTERS = {m.ENGINE: m for m in (claude_code, codex, kimi)}
