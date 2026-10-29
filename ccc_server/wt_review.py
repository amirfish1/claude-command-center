"""WatchTower ``in_review`` tickets (WT-5 acceptance gates) for CCC.

WatchTower added a fourth ticket status between ``in_progress`` and
``closed``: a ticket with gates lands in ``in_review`` on ``wt close`` and
stays there until every gate passes. ``gate_pending`` names the stage it is
waiting on: ``verify`` (an independent verifier session), ``review`` (the
submitter) or ``review:<target>`` (a named reviewer). ``cmd:`` gates run
inside ``wt close`` itself, so they are never the pending stage.

``in_review`` is NOT closed: dependents still wait on it and every count that
splits open from closed must keep it on the open side.

A review-stage ticket is a decision for a person, so it surfaces as a
Decision Inbox card with Accept / Reject. Those cards are derived live from
the cached queue snapshot on each read (no subprocess), never
persisted: the card disappears the moment the ticket leaves ``in_review``.
Accept / Reject shell to ``wt accept`` / ``wt reject`` so WatchTower's own
resume path (reject re-binds and resumes the original worker) runs unchanged.
"""

from __future__ import annotations

import json
import subprocess

from ccc_server import core as _core

IN_REVIEW = "in_review"
CARD_ID_PREFIX = "wtr:"
CARD_KIND = "wt_review"


def is_in_review(item):
    return isinstance(item, dict) and item.get("status") == IN_REVIEW


def pending_stage(item):
    """The gate an ``in_review`` ticket waits on ('' when not in review).
    A missing ``gate_pending`` reads as ``review``, WatchTower's default."""
    if not is_in_review(item):
        return ""
    return str(item.get("gate_pending") or "review").strip() or "review"


def stage_label(stage):
    """Who the pending stage waits on, in WatchTower's words
    (watchtower.queue.gate_stage_label)."""
    stage = str(stage or "")
    if stage == "verify":
        return "independent verifier"
    if stage.startswith("review:"):
        return stage[7:].strip() or "submitter"
    return "submitter"


def needs_review(item):
    """True when the ticket waits on a person's accept/reject (not a verifier)."""
    stage = pending_stage(item)
    return stage == "review" or stage.startswith("review:")


def review_cards(items):
    """Decision Inbox cards for every review-stage ticket, newest first."""
    out = []
    for it in items or []:
        if not needs_review(it):
            continue
        ref = str(it.get("ref") or "").strip()
        if not ref:
            continue
        res = it.get("resolution") if isinstance(it.get("resolution"), dict) else {}
        summary = str(res.get("summary") or "").strip()
        title = str(it.get("title") or it.get("note") or "").strip().split("\n")[0]
        accept_line = str(it.get("accept") or "").strip()
        reviewer = stage_label(pending_stage(it))
        context = summary or title
        if accept_line:
            context = (context + "\n\nAccept when: " + accept_line).strip()
        at = str(it.get("updated_at") or it.get("closed_at") or it.get("created_at") or "")
        out.append({
            "id": CARD_ID_PREFIX + ref,
            "source_id": "wt-review:" + ref,
            "kind": CARD_KIND,
            "title": f"{ref} awaits review: {title}"[:200],
            "context": context[:2000],
            "severity": "warn",
            "status": "open",
            "created_at": at,
            "updated_at": at,
            "seen_count": 1,
            "live": True,
            "options": [
                {"label": "Accept", "detail": "Close it and unblock its dependents.",
                 "cost": "", "recommended": True,
                 "action": {"kind": "wt_accept", "ref": ref}},
                {"label": "Reject", "detail": "Send it back to open with your reason; its worker resumes.",
                 "cost": "", "recommended": False,
                 "action": {"kind": "wt_reject", "ref": ref}},
            ],
            "source": {
                "ref": ref, "queue": str(it.get("project") or ""),
                "reviewer": reviewer, "commit": str(res.get("commit") or ""),
                "detail": summary,
            },
            "analyst": None,
        })
    out.sort(key=lambda c: c["updated_at"], reverse=True)
    return out


def live_review_cards():
    # The memoized display snapshot (<=30s old), not a raw list_items(): this
    # runs on every Decision Inbox / canvas poll and list_items() can block on
    # a GitHub-backed queue.
    try:
        return review_cards(_core._wt_list_items_display_cached() or [])
    except Exception:
        return []


def run_review_verb(ref, verb, *, reason="", by="dashboard", runner=None, timeout=60):
    """``wt accept REF`` / ``wt reject REF --reason ...``. Returns
    ``{ok, item?, error?}``. ``runner`` is injectable for tests."""
    ref = str(ref or "").strip()
    if not ref:
        return {"ok": False, "error": "ref required"}
    if verb not in ("accept", "reject"):
        return {"ok": False, "error": "verb must be accept or reject"}
    reason = str(reason or "").strip()
    if verb == "reject" and not reason:
        return {"ok": False, "error": "a reject needs a reason"}
    args = [verb, ref, "--by", by, "--json"]
    if verb == "reject":
        args += ["--reason", reason[:2000]]
    if runner is not None:
        return runner(args)
    wt = _core._wt_cli_path()
    if not wt:
        return {"ok": False, "error": "wt CLI not found"}
    try:
        proc = subprocess.run([wt] + args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return {"ok": False, "error": str(e)[:300]}
    return parse_verb_output(verb, proc.returncode, proc.stdout, proc.stderr)


def parse_verb_output(verb, returncode, stdout, stderr):
    """Success needs exit 0 AND the ticket JSON ``--json`` prints on success.
    Exit status alone is not trusted: a `wt` wrapper that drops main()'s
    return code reports every refusal ("not in_review") as exit 0."""
    out = (stdout or "").strip()
    item = None
    if "{" in out:
        try:
            # reject may print resume notes after the JSON; decode the first object.
            item = json.JSONDecoder().raw_decode(out[out.index("{"):])[0]
        except ValueError:
            item = None
    if returncode != 0 or not isinstance(item, dict):
        err = (stderr or stdout or "").strip().splitlines()
        return {"ok": False, "error": (err[-1] if err else f"wt {verb} failed")[:300]}
    return {"ok": True, "effect": "accepted" if verb == "accept" else "rejected", "item": item}
