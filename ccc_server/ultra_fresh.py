"""Bounded, message-only sidebar feed using the transcript/live readers.

One connection watches at most four sessions. No archive scans, subprocesses,
subscriptions to historical sessions, or global per-token event publication.
"""
import json
import re
import time
from pathlib import Path
from datetime import datetime

from ccc_server import core
from ccc_server.morning_launch import _SpawnEventNormalizer
from ccc_server.session_graph import _parse_session_state
from ccc_server.transcript_bookkeeping import is_compaction_bookkeeping

MAX_SESSIONS = 4
MAX_TEXT = 40000
# Transcript source-key checks resolve per-engine stores (Codex SQLite, Hermes
# and Devin DB keys); at the 100ms loop cadence across every open stream they
# starved the GIL and stalled /list for seconds (CCC-152). Live spawn-log text
# still tails every loop tick.
TRANSCRIPT_CHECK_S = 0.5
# Loop cadence: 100ms while text is streaming, backing off to 500ms while
# every watched session is quiet, so idle streams stop costing the dashboard
# ~1s of CPU per connection (CCC-142). The first update snaps back to 100ms.
ACTIVE_TICK_S = 0.1
IDLE_TICK_MAX_S = TRANSCRIPT_CHECK_S


def event_time(value):
    try:
        if isinstance(value, (int, float)):
            return float(value) / (1000 if value > 1e12 else 1)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0


def session_ids(query):
    values = (query.get("sessions") or [""])[0].split(",")
    return list(dict.fromkeys(v for v in values if re.fullmatch(r"[\w.:-]{1,160}", v)))[:MAX_SESSIONS]


def message_text(event):
    if event.get("parent_tool_use_id"):
        return ""
    if event.get("type") not in ("assistant", "assistant_block"):
        return ""
    return "\n".join(str(b.get("text") or "") for b in event.get("blocks") or []
                     if isinstance(b, dict) and (b.get("kind") or b.get("type")) == "text")


class FreshSession:
    def __init__(self, sid):
        self.sid = sid
        self.key = None
        self.last_line = 0
        self.user_ts = ""
        self.last_ts = 0
        self.last_text = ""
        self.live_text = ""
        self.live_id = ""
        self.log_path = None
        self.offset = 0
        self.leftover = b""
        self.normalizer = _SpawnEventNormalizer()
        self.last_resolve = 0
        self.last_transcript_check = 0
        self.codex_key = None
        self.codex_turn = ""
        self.retired_turns = set()
        self.codex_text = ""

    def _message(self, text, ts="", **extra):
        text = text[:MAX_TEXT]
        at = event_time(ts)
        if not text.strip() or (at and self.last_ts and at < self.last_ts):
            return None
        if text == self.last_text:
            return None
        self.last_text = text
        if at:
            self.last_ts = at
        return {"session_id": self.sid, "text": text, "ts": ts,
                "user_ts": self.user_ts, "session_state": _parse_session_state(text), **extra}

    def read(self):
        updates = []
        now = time.monotonic()
        key = self.key
        if self.key is None or now - self.last_transcript_check >= TRANSCRIPT_CHECK_S:
            self.last_transcript_check = now
            key = core._conv_parse_jsonl_mtime(self.sid)
        if key != self.key:
            initial = self.key is None
            self.key = key
            # Reuse the normal bounded transcript tail and its absolute line
            # cursor. Tools advance the cursor but emit no explanation text.
            result = core.parse_conversation(self.sid, tail=80)
            previous_line = 0 if initial or result.get("last_line", 0) < self.last_line else self.last_line
            self.last_line = result.get("last_line", self.last_line)
            for ev in result.get("events") or []:
                if not initial and int(ev.get("line") or 0) <= previous_line:
                    continue
                ts = str(ev.get("ts") or "")
                if (ev.get("type") in ("user_text", "user") and not ev.get("pending")
                        and not is_compaction_bookkeeping(ev)):
                    if ts and event_time(ts) >= event_time(self.user_ts):
                        self.user_ts = ts
                        self.last_ts = max(self.last_ts, event_time(ts))
                        self.last_text = ""
                        self.live_id = self.live_text = ""
                        updates.append({"session_id": self.sid, "user_ts": ts, "clear": True})
                text = message_text(ev)
                if text:
                    update = self._message(text, ts, line=int(ev.get("line") or 0))
                    if update:
                        updates.append(update)

        # Reuse the pane's stream-json normalizer. Seek to EOF on discovery:
        # earlier messages come from the transcript, never replay an old spawn.
        now = time.monotonic()
        if now - self.last_resolve > 2:
            self.last_resolve = now
            log_path, alive = core._resolve_spawn_log_for_session(self.sid)
            candidate = Path(log_path) if log_path and alive else None
            if candidate != self.log_path:
                self.log_path = candidate
                self.offset = candidate.stat().st_size if candidate else 0
                self.leftover = b""
                self.normalizer = _SpawnEventNormalizer()
                self.live_id = self.live_text = ""
        if self.log_path:
            with self.log_path.open("rb") as handle:
                if self.log_path.stat().st_size < self.offset:
                    self.offset = 0
                    self.leftover = b""
                    self.normalizer = _SpawnEventNormalizer()
                    self.live_id = self.live_text = ""
                handle.seek(self.offset)
                chunk = handle.read(256 * 1024)
                self.offset = handle.tell()
            lines = (self.leftover + chunk).split(b"\n")
            self.leftover = lines.pop()[-256 * 1024:]
            for raw in lines:
                try:
                    original = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
                content = (original.get("message") or {}).get("content") if isinstance(original, dict) and isinstance(original.get("message"), dict) else None
                user_text = content.strip() if isinstance(content, str) else ""
                if isinstance(content, list):
                    user_text = "".join(str(block.get("text") or "") for block in content
                                        if isinstance(block, dict) and block.get("type") == "text").strip()
                if (user_text and original.get("type") == "user" and not original.get("parent_tool_use_id")
                        and not is_compaction_bookkeeping(original)):
                    ts = original.get("timestamp") or datetime.now().astimezone().isoformat()
                    if event_time(ts) > event_time(self.user_ts):
                        self.user_ts = str(ts)
                        self.last_ts = max(self.last_ts, event_time(ts))
                        self.live_id = self.live_text = self.last_text = ""
                        updates.append({"session_id": self.sid, "user_ts": self.user_ts, "clear": True})
                ev = self.normalizer.normalize(original)
                text = message_text(ev or {})
                if not text:
                    continue
                mid = ev.get("message_id")
                self.live_text = ((self.live_text if mid == self.live_id else "") + text)[:MAX_TEXT]
                self.live_id = mid
                ts = str(original.get("timestamp") or "")
                update = self._message(self.live_text, ts, live=True)
                if update:
                    updates.append(update)
        # The same in-memory snapshot mapper used by the open Codex pane.
        # Baseline the cursor first: an old retained snapshot is not new work.
        from ccc_server.codex_conversation import CODEX_CONVERSATIONS
        from ccc_server.codex_live_events import live_turns_from_snapshot
        snapshot = CODEX_CONVERSATIONS.snapshot(self.sid) if core._detect_session_engine(self.sid) == "codex" else {}
        live_key = (snapshot.get("generation"), snapshot.get("cursor"))
        if live_key != self.codex_key:
            turns = live_turns_from_snapshot(snapshot)
            if turns:
                turn = turns[-1]
                tid = turn["turn_id"]
                if tid not in self.retired_turns:
                    texts = [message_text(event) for event in turn["events"]]
                    text = next((text for text in reversed(texts) if text), "")
                    signature = (tid, text)
                    changed = self.codex_key is not None and signature != self.codex_text
                    self.codex_text = signature
                    if tid != self.codex_turn:
                        if self.codex_turn:
                            self.retired_turns.add(self.codex_turn)
                        self.codex_turn = tid
                    if text and changed:
                        update = self._message(text, live=True, turn_id=tid)
                        if update:
                            updates.append(update)
        self.codex_key = live_key
        if not updates:
            return []
        last = updates[-1]
        # The list can retain a pre-fix user tail from compaction. Prove which
        # transcript snapshot this frame read so the browser can supersede it
        # without allowing a delayed reply to cross a genuinely newer request.
        if (isinstance(self.key, tuple) and len(self.key) == 2
                and isinstance(self.key[0], (int, float)) and self.key[0] > 1e15):
            last["source_mtime"] = self.key[0] / 1e9
        if last.get("clear"):
            return [last]
        # Every text frame carries the current user watermark, so intermediate
        # chunks and clears can collapse without losing the newer-turn barrier.
        return [last]


def stream(handler, query):
    readers = [FreshSession(sid) for sid in session_ids(query)]
    if not readers:
        handler.send_json({"error": "sessions is required"}, 400)
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()
    last_ping = 0
    tick = ACTIVE_TICK_S
    try:
        # Reconnect periodically so a lost client cannot retain readers forever.
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            updates = []
            for reader in readers:
                try:
                    updates.extend(reader.read())
                except (OSError, ValueError, KeyError):
                    continue
            tick = ACTIVE_TICK_S if updates else min(tick * 1.5, IDLE_TICK_MAX_S)
            if updates:
                handler.wfile.write(("data: " + json.dumps({"updates": updates}) + "\n\n").encode())
            elif time.monotonic() - last_ping >= 5:
                handler.wfile.write(b"event: keepalive\ndata: {}\n\n")
            else:
                time.sleep(tick)
                continue
            handler.wfile.flush()
            last_ping = time.monotonic()
            time.sleep(tick)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
