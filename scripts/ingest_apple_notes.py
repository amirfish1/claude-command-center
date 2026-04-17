#!/usr/bin/env python3
"""Extract todo-shaped items from recent Apple Notes into the morning inbox.

Flow:
1. `osascript -l JavaScript` lists Apple Notes modified in the last 14 days.
2. For each note with substantive body text, invoke `claude -p --model haiku`
   with an extraction prompt. Parse the JSON array returned.
3. Write unique candidates to `~/.claude/log-viewer/morning/inbox/<date>.jsonl`.

Run manually:
    python3 scripts/ingest_apple_notes.py

Or from the Morning UI's "Scan now" button, which POSTs to
/api/morning/ingest/run (server.py) which shell-execs this script.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


JXA_NOTES = r"""
var Notes = Application("Notes");
var notes = Notes.notes();
var cutoffDays = 14;
var cutoff = new Date(Date.now() - cutoffDays * 86400 * 1000);
var out = [];
for (var i = 0; i < notes.length; i++) {
  var n = notes[i];
  try {
    var md = n.modificationDate();
    if (md > cutoff) {
      out.push({
        id: n.id().toString(),
        title: n.name(),
        body_html: n.body(),
        modified: md.toISOString(),
      });
    }
  } catch (e) {
    continue;
  }
}
JSON.stringify(out);
"""


def _fetch_notes():
    try:
        r = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", JXA_NOTES],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[apple-notes] osascript launch failed: {e}", file=sys.stderr)
        return []
    if r.returncode != 0:
        print(f"[apple-notes] osascript error: {r.stderr.strip()[:200]}", file=sys.stderr)
        return []
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError as e:
        print(f"[apple-notes] parse failed: {e}", file=sys.stderr)
        return []


_TAG_BR = re.compile(r"<br\s*/?>", re.I)
_TAG_BLOCK = re.compile(r"</?(p|div|li|ul|ol|h[1-6])[^>]*>", re.I)
_TAG_ANY = re.compile(r"<[^>]+>")


def _html_to_text(s):
    if not s:
        return ""
    s = _TAG_BR.sub("\n", s)
    s = _TAG_BLOCK.sub("\n", s)
    s = _TAG_ANY.sub("", s)
    s = html.unescape(s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()


EXTRACT_PROMPT_TEMPLATE = (
    "Extract any todo-shaped items, ideas to pursue, or clearly actionable notes "
    "from the Apple Note below. Skip journaling/reflection/meeting notes without "
    "a clear action.\n\n"
    "Return ONLY a JSON array (no prose, no markdown code fences). Each item "
    "must have:\n"
    "- \"text\": a short (<= 120 chars) rephrasing of the action\n"
    "- \"suggested_goal\": optional slug if you can guess "
    "(bym-growth, nvidia-course, ai-forms, amirfish-ai, taxes), else null\n\n"
    "If there's nothing actionable, return [].\n\n"
    "## Title\n{title}\n\n## Body\n\n{body}\n"
)


def _extract(title, body, source_id, modified_iso):
    prompt = EXTRACT_PROMPT_TEMPLATE.format(title=title or "(untitled)", body=body)
    try:
        r = subprocess.run(
            ["claude", "-p", "--model", "haiku"],
            input=prompt, capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as e:
        print(f"[apple-notes] claude -p failed for {source_id}: {e}", file=sys.stderr)
        return []
    if r.returncode != 0:
        return []
    out = r.stdout.strip()
    out = re.sub(r"^```(?:json)?\s*|\s*```$", "", out, flags=re.M).strip()
    m = re.search(r"\[.*\]", out, flags=re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []

    try:
        mod_dt = datetime.fromisoformat(modified_iso.replace("Z", "+00:00"))
        age_days = max(0, (datetime.now(timezone.utc) - mod_dt).days)
    except (ValueError, AttributeError):
        age_days = 0

    result = []
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        cid = hashlib.sha1(f"apple-notes:{source_id}:{text}".encode()).hexdigest()[:16]
        result.append({
            "id": cid,
            "source": "Apple Notes",
            "source_id": source_id,
            "source_title": title,
            "text": text,
            "age_days": age_days,
            "suggested_goal": it.get("suggested_goal"),
        })
    return result


def _existing_ids(inbox_dir):
    ids = set()
    if not inbox_dir.is_dir():
        return ids
    for f in inbox_dir.glob("*.jsonl"):
        try:
            for line in open(f, "r", encoding="utf-8", errors="replace"):
                try:
                    ids.add(json.loads(line).get("id"))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    return ids


def main():
    notes = _fetch_notes()
    if not notes:
        print("[apple-notes] no recent notes (or osascript unavailable)")
        return 0
    print(f"[apple-notes] fetched {len(notes)} notes from last 14 days")

    inbox_dir = Path.home() / ".claude" / "log-viewer" / "morning" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    existing = _existing_ids(inbox_dir)

    today = datetime.now().strftime("%Y-%m-%d")
    out_path = inbox_dir / f"{today}.jsonl"
    written = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for n in notes:
            body = _html_to_text(n.get("body_html") or "")
            if len(body) < 20:
                continue
            candidates = _extract(n.get("title", ""), body, n["id"], n.get("modified") or "")
            for c in candidates:
                if c["id"] in existing:
                    continue
                f.write(json.dumps(c) + "\n")
                existing.add(c["id"])
                written += 1
    print(f"[apple-notes] wrote {written} new candidates to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
