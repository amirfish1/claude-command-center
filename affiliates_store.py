"""JSON-file store for the Affiliates section.

Studio-owner leads are persisted to ~/.claude/log-viewer/affiliates.json.
The file is a single list of lead dicts; each lead has a stable `id`.

Concurrency: single-process, stdlib HTTP server — good enough to wrap reads
and writes with a module-level lock.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


_STORE_PATH = Path.home() / ".claude" / "log-viewer" / "affiliates.json"
_LOCK = threading.Lock()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _default_lead():
    return {
        "id": "",
        "lead_name": "",
        "studio_name": "",
        "city_state": "",
        "num_locations": 0,
        "participants_per_location": 0,
        "reached_out": False,
        "reached_out_date": "",
        "interaction_log": [],
        "proposed_promo_monthly": "",
        "proposed_promo_duration_months": 3,
        "proposed_ongoing_monthly": "",
        "discussed_with_amir": False,
        "next_steps": "",
        "probability_close_month_pct": 0,
        "owner": "",
        "created_at": "",
        "updated_at": "",
    }


# Fields the client is allowed to set directly. `id`, `created_at`, and
# `updated_at` are server-managed so we drop them from incoming payloads.
_WRITABLE_FIELDS = set(_default_lead().keys()) - {"id", "created_at", "updated_at"}


def _coerce(field, value):
    """Coerce a single field to the right type — tolerates strings from forms."""
    if field in {"num_locations", "participants_per_location", "proposed_promo_duration_months", "probability_close_month_pct"}:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
    if field in {"reached_out", "discussed_with_amir"}:
        return bool(value)
    if field == "interaction_log":
        if not isinstance(value, list):
            return []
        out = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            out.append({
                "date": str(entry.get("date", "")),
                "note": str(entry.get("note", "")),
            })
        return out
    # strings
    return "" if value is None else str(value)


def _sanitize_payload(payload):
    clean = {}
    for k, v in (payload or {}).items():
        if k in _WRITABLE_FIELDS:
            clean[k] = _coerce(k, v)
    return clean


def _load_raw():
    if not _STORE_PATH.is_file():
        return []
    try:
        data = json.loads(_STORE_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return data


def _save_raw(leads):
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(leads, indent=2))
    os.replace(tmp, _STORE_PATH)


def _normalize(lead):
    """Fill in missing keys so the client gets a stable shape."""
    merged = _default_lead()
    merged.update({k: v for k, v in lead.items() if k in merged})
    # Coerce values so legacy entries with wrong types become consistent.
    for k in list(merged.keys()):
        if k in ("id", "created_at", "updated_at"):
            continue
        merged[k] = _coerce(k, merged[k])
    merged["id"] = lead.get("id") or ""
    merged["created_at"] = lead.get("created_at") or ""
    merged["updated_at"] = lead.get("updated_at") or ""
    return merged


def list_leads():
    with _LOCK:
        return [_normalize(l) for l in _load_raw()]


def create_lead(payload):
    data = _sanitize_payload(payload)
    lead = _default_lead()
    lead.update(data)
    lead["id"] = f"lead_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    now = _now_iso()
    lead["created_at"] = now
    lead["updated_at"] = now
    with _LOCK:
        leads = _load_raw()
        leads.append(lead)
        _save_raw(leads)
    return lead


def update_lead(lead_id, payload):
    data = _sanitize_payload(payload)
    with _LOCK:
        leads = _load_raw()
        for i, lead in enumerate(leads):
            if lead.get("id") == lead_id:
                merged = _normalize(lead)
                merged.update(data)
                merged["id"] = lead_id
                merged["created_at"] = lead.get("created_at") or _now_iso()
                merged["updated_at"] = _now_iso()
                leads[i] = merged
                _save_raw(leads)
                return merged
    return None


def delete_lead(lead_id):
    with _LOCK:
        leads = _load_raw()
        kept = [l for l in leads if l.get("id") != lead_id]
        if len(kept) == len(leads):
            return False
        _save_raw(kept)
        return True


__all__ = [
    "list_leads",
    "create_lead",
    "update_lead",
    "delete_lead",
]
