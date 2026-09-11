"""Bounded Codex history and event state, owned by the transport process.

Snapshots and cursors are local to one connection generation. They are not
upstream replay tokens. Reconnects require authoritative history hydration.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from collections import OrderedDict, deque


class CodexConversationStore:
    def __init__(self, *, event_capacity=512, max_threads=16, text_limit=65536,
                 max_turns=64, max_items=256, thread_bytes=2 * 1024 * 1024):
        self._lock = threading.RLock()
        self._events = deque(maxlen=max(1, event_capacity))
        self._threads = OrderedDict()
        self._generation = ""
        self._seq = 0
        self._connected = False
        self._max_threads = max(1, max_threads)
        self._text_limit = max(1, text_limit)
        self._max_turns = max(1, max_turns)
        self._max_items = max(1, max_items)
        self._thread_bytes = max(1024, thread_bytes)

    @property
    def cursor(self):
        with self._lock:
            return self._seq

    @property
    def generation(self):
        with self._lock:
            return self._generation

    def connect(self, generation):
        if not isinstance(generation, str) or not 1 <= len(generation) <= 128:
            raise ValueError("Invalid connection generation")
        with self._lock:
            if self._generation != str(generation):
                self._generation = str(generation)
                self._events.clear()
                self._threads.clear()
                self._seq = 0
            self._connected = True

    def disconnect(self, generation):
        with self._lock:
            if self._generation == str(generation):
                self._connected = False

    def invalidate_history(self, thread_id):
        with self._lock:
            self._threads.pop(thread_id, None)

    def _bounded(self, value):
        remaining = [256 * 1024, 4096]
        clipped = [False]

        def walk(obj, depth=0):
            remaining[1] -= 1
            if depth > 16 or remaining[0] <= 0 or remaining[1] <= 0:
                clipped[0] = True
                return None
            if isinstance(obj, str):
                limit = min(self._text_limit, remaining[0])
                result = obj[:limit]
                remaining[0] -= len(result.encode("utf-8"))
                clipped[0] |= len(result) < len(obj)
                return result
            if isinstance(obj, dict):
                clipped[0] |= len(obj) > 512
                return {str(k)[:256]: walk(v, depth + 1)
                        for k, v in list(obj.items())[:512]}
            if isinstance(obj, (list, tuple)):
                clipped[0] |= len(obj) > 512
                return [walk(v, depth + 1) for v in obj[:512]]
            if obj is None or isinstance(obj, (bool, int, float)):
                return obj
            return None
        result = walk(value)
        return result, clipped[0]

    def _thread(self, tid):
        if tid not in self._threads:
            self._threads[tid] = {"meta": {}, "meta_seq": {}, "meta_sizes": {},
                                  "turns": OrderedDict(), "truncated": False, "bytes": 64}
            self._metadata(self._threads[tid], self._threads[tid], {"id": tid}, 0)
        self._threads.move_to_end(tid)
        return self._threads[tid]

    def _trim_global(self):
        def newest(thread):
            return max([*thread["meta_seq"].values(),
                        *(seq for turn in thread["turns"].values() for seq in turn["meta_seq"].values()),
                        *(item["seq"] for turn in thread["turns"].values() for item in turn["items"].values()), 0])
        while len(self._threads) > self._max_threads:
            key = min(self._threads, key=lambda tid: newest(self._threads[tid]))
            self._threads.pop(key)

    def _turn(self, thread, turn_id):
        turns = thread["turns"]
        if turn_id not in turns:
            turns[turn_id] = {"meta": {}, "meta_seq": {}, "meta_sizes": {}, "items": OrderedDict()}
            self._metadata(thread, turns[turn_id], {"id": turn_id}, 0)
        return turns[turn_id]

    def _metadata(self, thread, target, fields, seq, *, before_seq=None):
        for key, value in fields.items():
            if key == "id" and (not isinstance(value, str) or len(value) > 256
                                or ("id" in target["meta"] and target["meta"]["id"] != value)):
                continue
            if before_seq is not None and target["meta_seq"].get(key, 0) > before_seq:
                continue
            size = len(json.dumps({key: value}, ensure_ascii=False).encode("utf-8")) + 64
            thread["bytes"] += size - target["meta_sizes"].get(key, 0)
            target["meta"][key] = value
            target["meta_seq"][key] = seq
            target["meta_sizes"][key] = size

    def _trim(self, thread):
        # Hydration is merged first. Sequence provenance makes old snapshot
        # entries the first eviction candidates, even if they arrived last.
        turns = thread["turns"]
        def turn_seq(turn):
            return max([*turn["meta_seq"].values(),
                        *(item["seq"] for item in turn["items"].values()), 0])
        while len(turns) > self._max_turns:
            key = min(turns, key=lambda key: turn_seq(turns[key]))
            old = turns.pop(key)
            thread["bytes"] -= sum(old["meta_sizes"].values()) + sum(i["size"] for i in old["items"].values())
            thread["truncated"] = True
        for turn in turns.values():
            while len(turn["items"]) > self._max_items:
                key = min(turn["items"], key=lambda key: turn["items"][key]["seq"])
                thread["bytes"] -= turn["items"].pop(key)["size"]
                thread["truncated"] = True
        if thread["bytes"] <= self._thread_bytes:
            return
        candidates = []
        for turn in turns.values():
            candidates.extend((v["seq"], "item", turn, k) for k, v in turn["items"].items())
        for target in [thread, *turns.values()]:
            candidates.extend((seq, "meta", target, key) for key, seq in target["meta_seq"].items() if key != "id")
        # Stable sorting preserves history order between same-cursor entries.
        for _, kind, target, key in sorted(candidates, key=lambda row: row[0]):
            if thread["bytes"] <= self._thread_bytes:
                break
            if kind == "item":
                thread["bytes"] -= target["items"].pop(key)["size"]
            else:
                target["meta"].pop(key, None)
                target["meta_seq"].pop(key, None)
                thread["bytes"] -= target["meta_sizes"].pop(key)
            thread["truncated"] = True
        # Structural metadata for many empty turns is bounded too.
        while thread["bytes"] > self._thread_bytes and turns:
            key = min(turns, key=lambda key: turn_seq(turns[key]))
            old = turns.pop(key)
            thread["bytes"] -= sum(old["meta_sizes"].values()) + sum(i["size"] for i in old["items"].values())
            thread["truncated"] = True

    def _put_item(self, thread, turn, item, *, complete=False, seq=None):
        iid = item.get("id")
        if not isinstance(iid, str) or not 1 <= len(iid) <= 256:
            return
        items = turn["items"]
        previous = items.get(iid)
        if previous and previous["complete"] and not complete:
            return
        if previous:
            item = {**previous["value"], **item}
        bounded, clipped = self._bounded(item)
        size = len(json.dumps(bounded, ensure_ascii=False).encode("utf-8")) + 64
        thread["bytes"] += size - (previous["size"] if previous else 0)
        items[iid] = {"value": bounded, "complete": complete, "size": size,
                      "seq": self._seq if seq is None else seq}
        thread["truncated"] |= clipped

    def hydrate(self, thread, before_seq, *, generation=None):
        """Merge a read taken after before_seq without overwriting newer events."""
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not 1 <= len(thread["id"]) <= 256:
            raise ValueError("Thread snapshot requires an id")
        bounded, clipped = self._bounded(thread)
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            existing = self._threads.get(thread["id"])
            if existing and existing.get("meta", {}).get("deleted"):
                return False
            target = self._thread(thread["id"])
            target["truncated"] |= clipped
            self._metadata(target, target, {k: v for k, v in bounded.items() if k != "turns"},
                           before_seq, before_seq=before_seq)
            history_order = []
            for raw in bounded.get("turns") or []:
                if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not 1 <= len(raw["id"]) <= 256:
                    continue
                turn_id = raw["id"]
                history_order.append(turn_id)
                turn = self._turn(target, turn_id)
                # Events observed while the read was in flight win over a
                # snapshot's older turn lifecycle as well as its older text.
                self._metadata(target, turn, {k: v for k, v in raw.items() if k != "items"},
                               before_seq, before_seq=before_seq)
                for item in raw.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    existing = turn["items"].get(item.get("id"))
                    if existing is None or existing["seq"] <= before_seq:
                        self._put_item(target, turn, item, complete=raw.get("status") in
                                       ("completed", "failed", "interrupted"), seq=before_seq)
            ordered = [key for key in history_order if key in target["turns"]]
            ordered += [key for key in target["turns"] if key not in ordered]
            target["turns"] = OrderedDict((key, target["turns"][key]) for key in ordered)
            self._trim(target)
            self._trim_global()
            return True

    def record(self, method, params):
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        if not 1 <= len(method) <= 256:
            raise ValueError("Invalid event method")
        p, clipped = self._bounded(params)
        with self._lock:
            thread_data = p.get("thread") if isinstance(p.get("thread"), dict) else {}
            tid = p.get("threadId") or p.get("thread_id") or thread_data.get("id")
            turn_data = p.get("turn") if isinstance(p.get("turn"), dict) else {}
            turn_id = p.get("turnId") or p.get("turn_id") or turn_data.get("id")
            if method == "thread/deleted" and isinstance(tid, str) and 1 <= len(tid) <= 256:
                old = self._threads.pop(tid, None)
                old_meta = old.get("meta", {}) if isinstance(old, dict) else {}
                cwd = thread_data.get("cwd") or old_meta.get("cwd")
                self._events = deque(
                    (event for event in self._events if event.get("thread_id") != tid),
                    maxlen=self._events.maxlen,
                )
                self._seq += 1
                self._events.append({
                    "seq": self._seq,
                    "method": method,
                    "params": {"threadId": tid},
                    "thread_id": tid,
                    "ts": time.time(),
                    "truncated": False,
                })
                thread = self._thread(tid)
                fields = {"archived": False, "deleted": True}
                if isinstance(cwd, str) and cwd:
                    fields["cwd"] = cwd
                self._metadata(thread, thread, fields, self._seq)
                self._trim_global()
                return
            stored = self._threads.get(tid) if isinstance(tid, str) else None
            if stored and stored.get("meta", {}).get("deleted"):
                return
            self._seq += 1
            self._events.append({"seq": self._seq, "method": method, "params": p,
                                 "thread_id": tid, "ts": time.time(), "truncated": clipped})
            if not isinstance(tid, str) or not 1 <= len(tid) <= 256:
                return
            if method == "thread/reverted":
                self._threads.pop(tid, None)
                return
            thread = self._thread(tid)
            thread["truncated"] |= clipped
            fields = {k: v for k, v in thread_data.items() if k != "turns"}
            if method == "thread/status/changed":
                fields["status"] = p.get("status")
            elif method == "thread/tokenUsage/updated":
                fields["tokenUsage"] = p.get("tokenUsage")
            elif method == "thread/name/updated":
                fields["name"] = p.get("threadName", p.get("name"))
            elif method in ("thread/archived", "thread/unarchived", "thread/deleted"):
                fields["archived"] = method == "thread/archived"
                fields["deleted"] = method == "thread/deleted"
            elif method.startswith("thread/goal/"):
                fields["goal"] = p.get("goal")
            self._metadata(thread, thread, fields, self._seq)
            if not isinstance(turn_id, str) or not 1 <= len(turn_id) <= 256:
                self._trim(thread)
                self._trim_global()
                return
            turn = self._turn(thread, turn_id)
            if method in ("turn/started", "turn/completed"):
                self._metadata(thread, turn, {k: v for k, v in turn_data.items() if k != "items"}, self._seq)
                for item in turn_data.get("items") or []:
                    if isinstance(item, dict):
                        self._put_item(thread, turn, item, complete=method == "turn/completed")
            elif method == "turn/plan/updated":
                self._metadata(thread, turn, {"plan": p.get("plan"), "explanation": p.get("explanation")}, self._seq)
            elif method == "turn/diff/updated":
                self._metadata(thread, turn, {"diff": p.get("diff")}, self._seq)
            elif method in ("item/started", "item/completed"):
                item = p.get("item")
                if isinstance(item, dict):
                    self._put_item(thread, turn, item, complete=method == "item/completed")
            elif method.startswith("item/"):
                self._delta(thread, turn, method, p)
            self._trim(thread)
            self._trim_global()

    def _delta(self, thread, turn, method, params):
        iid = params.get("itemId") or params.get("item_id")
        if not isinstance(iid, str) or not iid:
            return
        previous = turn["items"].get(iid)
        is_output = method in ("item/commandExecution/outputDelta", "item/fileChange/outputDelta")
        if previous and previous["complete"]:
            return
        kinds = {"agentMessage": "agentMessage", "plan": "plan", "reasoning": "reasoning",
                 "commandExecution": "commandExecution", "fileChange": "fileChange",
                 "mcpToolCall": "mcpToolCall"}
        typ = kinds.get(method.split("/")[1])
        if typ is None:
            return
        item = copy.deepcopy(previous["value"]) if previous else {"id": iid, "type": typ}
        delta = params.get("delta")
        if method == "item/fileChange/patchUpdated":
            item["changes"] = params.get("changes", [])
        elif isinstance(delta, str):
            if typ == "reasoning":
                field = "summary" if "summary" in method else "content"
                index = params.get("summaryIndex", params.get("contentIndex", 0))
                if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 128:
                    return
                parts = item.setdefault(field, [])
                if not isinstance(parts, list):
                    parts = item[field] = []
                while len(parts) <= index:
                    parts.append("")
                parts[index] = str(parts[index]) + delta
            else:
                field = "aggregatedOutput" if is_output else "text"
                item[field] = str(item.get(field) or "") + delta
        elif typ == "mcpToolCall":
            item["progress"] = params.get("message", params.get("progress"))
        self._put_item(thread, turn, item, complete=bool(previous and previous["complete"]))

    def snapshot(self, thread_id):
        with self._lock:
            stored = self._threads.get(thread_id)
            value = None
            if stored:
                value = {**stored["meta"], "turns": [
                    {**turn["meta"], "items": [entry["value"] for entry in turn["items"].values()]}
                    for turn in stored["turns"].values()]}
            return copy.deepcopy({"ok": True, "generation": self._generation,
                                  "cursor": self._seq, "connected": self._connected,
                                  "thread": value, "truncated": bool(stored and stored["truncated"])})

    def events_since(self, cursor, generation=None, thread_id=None):
        with self._lock:
            oldest = self._events[0]["seq"] if self._events else self._seq + 1
            stored = self._threads.get(thread_id) if thread_id else None
            deleted_scope = bool(stored and stored.get("meta", {}).get("deleted"))
            gap = ((generation is not None and generation != self._generation)
                   or (cursor < oldest - 1 and not deleted_scope) or cursor > self._seq)
            events = [] if gap else [event for event in self._events if event["seq"] > cursor
                                     and (not thread_id or not event["thread_id"] or event["thread_id"] == thread_id)]
            return copy.deepcopy({"ok": True, "generation": self._generation,
                                  "cursor": self._seq, "connected": self._connected,
                                  "resync_required": bool(gap), "events": events})


CODEX_CONVERSATIONS = CodexConversationStore()
