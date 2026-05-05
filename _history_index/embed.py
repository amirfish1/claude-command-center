"""Ollama HTTP client for text embeddings.

Uses Ollama's `/api/embed` endpoint (newer, supports batching). Falls back
to per-item calls on older Ollama versions. The whole module gracefully
no-ops if Ollama isn't reachable — `is_available()` is the canonical check.
"""
from __future__ import annotations

import array
import json
import urllib.error
import urllib.request
from typing import Sequence


class OllamaUnavailable(RuntimeError):
    pass


def is_available(url: str = "http://localhost:11434", timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _read_error_body(e: urllib.error.HTTPError) -> str:
    try:
        body = e.read().decode("utf-8", errors="replace")
        try:
            j = json.loads(body)
            return j.get("error") or body[:300]
        except Exception:
            return body[:300]
    except Exception:
        return ""


def embed_batch(
    texts: Sequence[str],
    model: str = "nomic-embed-text",
    url: str = "http://localhost:11434",
    timeout: float = 120.0,
) -> list[list[float]]:
    """Return one embedding per input text.

    Raises OllamaUnavailable with a useful message on connection failure, model
    missing, or any other Ollama-side error.
    """
    if not texts:
        return []
    # truncate=true asks Ollama to clip oversized inputs server-side.
    # keep_alive="30m" prevents Ollama from unloading the model between
    # batches — without it, each /api/embed reload kills throughput.
    body = json.dumps(
        {
            "model": model,
            "input": list(texts),
            "truncate": True,
            "keep_alive": "30m",
        }
    ).encode()
    req = urllib.request.Request(
        f"{url}/api/embed",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        msg = _read_error_body(e)
        raise OllamaUnavailable(
            f"Ollama at {url} returned HTTP {e.code}: {msg}"
        ) from e
    except urllib.error.URLError as e:
        raise OllamaUnavailable(f"Ollama at {url} unreachable: {e.reason}") from e
    embs = data.get("embeddings")
    if embs is None:
        # Older Ollama: only /api/embeddings (singular, one prompt at a time)
        return [_embed_one(t, model=model, url=url, timeout=timeout) for t in texts]
    return embs


def model_present(
    model: str = "nomic-embed-text",
    url: str = "http://localhost:11434",
    timeout: float = 5.0,
) -> bool:
    """Return True iff the named model is already pulled into Ollama."""
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception:
        return False
    # Ollama tags include version suffix like "nomic-embed-text:latest"
    names = {m.get("name", "").split(":")[0] for m in data.get("models", [])}
    return model.split(":")[0] in names


def _embed_one(text: str, *, model: str, url: str, timeout: float) -> list[float]:
    body = json.dumps({"model": model, "prompt": text}).encode()
    req = urllib.request.Request(
        f"{url}/api/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["embedding"]


def to_blob(embedding: Sequence[float]) -> bytes:
    """Pack a Python sequence of floats into the bytes format sqlite-vec expects."""
    return array.array("f", embedding).tobytes()


def truncate_for_embedding(text: str, max_chars: int = 6000) -> str:
    if not text:
        return ""
    return text if len(text) <= max_chars else text[:max_chars]
