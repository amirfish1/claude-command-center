"""Tiny JSON config at ~/.claude-index/config.json. All keys optional."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path.home() / ".claude-index" / "config.json"

DEFAULTS: dict[str, Any] = {
    "embedding_model": "nomic-embed-text",
    "embedding_dim": 768,
    "ollama_url": "http://localhost:11434",
    "max_embed_chars": 2000,
    "auto_embed_on_ingest": False,
}


def load(path: Path | str | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_PATH
    cfg = dict(DEFAULTS)
    if p.exists():
        try:
            cfg.update(json.loads(p.read_text()))
        except Exception:
            pass
    return cfg


def save(cfg: dict[str, Any], path: Path | str | None = None) -> None:
    p = Path(path) if path else DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n")
