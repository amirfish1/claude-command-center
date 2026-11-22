# Copyright (c) 2026 Amir Fish. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-MIT
"""Persistent Antigravity (agy) conversation processes driven over stream-json.

Antigravity documents one way to send several turns to one process:
``agy --print= --input-format stream-json --output-format stream-json``.
stdin takes one NDJSON object per line (``{"event":"user","message":
{"content":"..."}}``), every turn ends with exactly one ``result`` event on
stdout, and the process exits once stdin is closed (after the current turn).

That gives the dashboard what a one-shot ``agy -p`` cannot: an explicit
end-of-turn signal that does not depend on the process exiting. A stream
that stops reporting after persisting its final response can be retired
without replaying the completed turn.

The caller keeps follow-ups on the durable queue until the stream reports
``result`` or a retired stream exits.

stream-json cannot steer: a line written mid-turn waits until the turn ends
(verified on agy 1.5.14, even with ``queuedMessages: send-immediately``).
``agy acp`` can: a second ``session/prompt`` sent while a turn runs ends the
first prompt and the agent reads the new text after its current step. So the
preferred process is an ACP one (``spawn_acp``), registered in the same
``_PROCS`` table with the same attributes as a stream process, so every
caller that reads busy/idle keeps working; ``steer`` is ACP-only. Any ACP
failure falls back to stream-json.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time

from ccc_server import core as _core

_LOCK = threading.Lock()
_PROCS = {}  # conversation id -> _StreamProc
_REAPER_STARTED = False
_DISABLED_UNTIL = 0.0   # set when a stream process dies before finishing a turn
_ACP_DISABLED_UNTIL = 0.0  # set when `agy acp` fails to start or dies early
_ACP_HANDSHAKE_S = 45.0


def enabled():
    if os.environ.get("CCC_ANTIGRAVITY_STREAM", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return time.time() >= _DISABLED_UNTIL


def acp_enabled():
    """Drive conversations through `agy acp` (steerable) before stream-json."""
    if os.environ.get("CCC_ANTIGRAVITY_ACP", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    return time.time() >= _ACP_DISABLED_UNTIL


def _idle_close_seconds():
    try:
        return max(30.0, float(os.environ.get("CCC_ANTIGRAVITY_STREAM_IDLE_S", "600")))
    except ValueError:
        return 600.0


class _StreamProc:
    def __init__(self, sid, proc, log_fh, key, cwd, cli_log_path):
        self.sid = sid
        self.proc = proc
        self.log_fh = log_fh
        self.key = key          # (model, add_dirs): a change needs a new process
        self.cwd = cwd
        self.cli_log_path = cli_log_path
        self.busy = False
        self.busy_since = 0.0
        self.idle_since = time.time()
        self.inflight = None    # text of the turn being run
        self.turns = 0
        self.last_result = None
        self.final_step_at = None
        self.retiring = False
        self.exited = False
        self.started = time.time()
        self.lock = threading.Lock()

    def alive(self):
        return not self.exited and self.proc.poll() is None

    def _read(self):
        try:
            for raw in self.proc.stdout:
                try:
                    self.log_fh.write(raw.decode("utf-8", "replace"))
                    self.log_fh.flush()
                except (OSError, ValueError):
                    pass
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if event.get("event") == "result":
                    with self.lock:
                        self.busy = self.retiring
                        self.inflight = None
                        self.final_step_at = None
                        self.idle_since = time.time()
                        self.last_result = event.get("result") or {}
                elif event.get("event") == "step_update":
                    step = event.get("step_update")
                    with self.lock:
                        self.final_step_at = (
                            time.time() if self.inflight
                            and isinstance(step, dict)
                            and step.get("step_type") == "agent_response"
                            and step.get("state") == "DONE" else None
                        )
        finally:
            self.exited = True
            with self.lock:
                lost, self.inflight = self.inflight, None
                self.busy = False
            if lost and self.turns <= 1 and time.time() - self.started < 30:
                # Died right after start with nothing run (signed out, bad
                # flags, older agy): let the one-shot path handle this for a
                # while instead of looping the same message into it.
                global _DISABLED_UNTIL
                _DISABLED_UNTIL = time.time() + 300
            if lost:
                # The process went away mid-turn without a result: the
                # message may never have run. Put it back on the durable
                # queue instead of dropping it.
                try:
                    queued = _core._apply_pending_input_operations(self.sid, [{
                        "field": "resume", "action": "append_tail", "value": lost,
                    }])
                    _core._log_activity(
                        "inject", "AGY_STREAM_LOST",
                        f"session={self.sid} pid={self.proc.pid} "
                        f"requeued={int(bool(queued.get('ok')))}",
                    )
                except Exception:
                    pass
            try:
                self.log_fh.close()
            except OSError:
                pass

    def write(self, text):
        line = json.dumps({"event": "user", "message": {"content": text}}) + "\n"
        with self.lock:
            if self.busy:
                return False
            self.proc.stdin.write(line.encode())
            self.proc.stdin.flush()
            self.busy = True
            self.busy_since = time.time()
            self.inflight = text
            self.final_step_at = None
            self.turns += 1
            return True

    def retire_if_missing_result(self, now):
        """Close stdin once a confirmed final response has gone quiet."""
        if self.final_step_at is None or self.cli_log_path is None:
            return
        idle_s = _core._antigravity_turn_idle_seconds()
        with self.lock:
            if self.final_step_at is None or now - self.final_step_at < idle_s:
                return
            try:
                last_io = max(os.stat(self.cli_log_path).st_mtime,
                              os.fstat(self.log_fh.fileno()).st_mtime)
                if now - last_io < idle_s:
                    return
            except (OSError, ValueError):
                return
            if not _core._antigravity_turn_finished(self.sid, pid=self.proc.pid, idle_s=idle_s):
                return
            # The previous input already ran. Keep ownership until this
            # process exits, and let the durable queue deliver the next turn.
            self.inflight = None
            self.final_step_at = None
            self.retiring = True
            self.close()
            _core._log_activity(
                "inject", "AGY_STREAM_MISSING_RESULT",
                f"session={self.sid} pid={self.proc.pid} confirmed_final=1",
            )

    def close(self):
        """Ask the process to exit: closing stdin ends it after the current turn."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass


class _AcpError(Exception):
    """`agy acp` refused a request or went away before answering it."""


class _AcpSessionError(_AcpError):
    """ACP works but not for this conversation (unknown id, model not offered).

    Falls back to stream-json for this conversation only; unlike a failed
    start it must not pause ACP for every other conversation.
    """


def _model_key(text):
    return re.sub(r"[^a-z0-9]+", "", str(text or "").lower())


class _AcpProc:
    """One `agy acp` process serving one conversation over JSON-RPC stdio.

    Exposes the attributes callers read from ``_StreamProc`` (``proc``,
    ``busy``, ``busy_since``, ``idle_since``, ``inflight``, ``turns``,
    ``key``, ``last_result``, ``alive``, ``write``, ``close``). Busy means a
    ``session/prompt`` request is unanswered. A steer sends another prompt
    while one is running; agy answers the first with ``end_turn`` and keeps
    working on the new one, so busy stays true until the last prompt ends.
    """

    supports_steer = True

    def __init__(self, sid, proc, log_fh, key, cwd, cli_log_path, skip_permissions=True):
        self.sid = sid
        self.proc = proc
        self.log_fh = log_fh
        self.key = key
        self.cwd = cwd
        self.cli_log_path = cli_log_path
        self.skip_permissions = skip_permissions
        self.busy = False
        self.busy_since = 0.0
        self.idle_since = time.time()
        self.inflight = None
        self.turns = 0
        self.last_result = None
        self.retiring = False
        self.exited = False
        self.started = time.time()
        self.lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._waiters = {}   # request id -> [threading.Event, response]
        self._prompts = {}   # session/prompt request id -> text, oldest first
        self.prompt_started_at = 0.0
        self.last_activity_at = 0.0
        self.active_tools = {}  # toolCallId -> name, in start order

    def alive(self):
        return not self.exited and self.proc.poll() is None

    def _send(self, obj):
        line = (json.dumps(obj) + "\n").encode()
        with self._write_lock:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()

    def _new_id(self):
        with self.lock:
            self._next_id += 1
            return self._next_id

    def _request(self, method, params, timeout=None):
        timeout = _ACP_HANDSHAKE_S if timeout is None else timeout
        rid = self._new_id()
        waiter = [threading.Event(), None]
        with self.lock:
            self._waiters[rid] = waiter
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        except (OSError, ValueError) as exc:
            with self.lock:
                self._waiters.pop(rid, None)
            raise _AcpError(f"{method}: write failed: {exc}")
        if not waiter[0].wait(timeout):
            with self.lock:
                self._waiters.pop(rid, None)
            raise _AcpError(f"{method}: no answer in {timeout:.0f}s")
        msg = waiter[1] or {}
        if "error" in msg:
            err = msg.get("error") or {}
            raise _AcpError(f"{method}: {err.get('message') or err}")
        return msg.get("result") or {}

    def handshake(self, model_label="", timeout=None):
        """initialize, attach to the existing conversation, apply the model."""
        self._request("initialize", {"protocolVersion": 1, "clientCapabilities": {}}, timeout)
        try:
            resumed = self._request("session/resume", {
                "sessionId": self.sid, "cwd": self.cwd, "mcpServers": [],
            }, timeout)
        except _AcpError as exc:
            if not self.alive():
                raise
            raise _AcpSessionError(str(exc))
        if model_label:
            models = resumed.get("models") or {}
            want = _model_key(model_label)
            match = None
            for item in models.get("availableModels") or []:
                if want in (_model_key(item.get("name")), _model_key(item.get("modelId"))):
                    match = item.get("modelId")
                    break
            if not match:
                raise _AcpSessionError(f"model {model_label!r} is not offered over ACP")
            if match != models.get("currentModelId"):
                try:
                    self._request("session/set_model", {"sessionId": self.sid, "modelId": match}, timeout)
                except _AcpError as exc:
                    if not self.alive():
                        raise
                    raise _AcpSessionError(str(exc))

    def _answer(self, msg):
        """Reply to a request the agent sends us; never leave one hanging."""
        method = msg.get("method")
        if method == "session/request_permission":
            options = (msg.get("params") or {}).get("options") or []
            kinds = ("allow_always", "allow_once") if self.skip_permissions else ("reject_once", "reject_always")
            chosen = next((o for k in kinds for o in options if o.get("kind") == k), None)
            outcome = ({"outcome": "selected", "optionId": chosen.get("optionId")}
                       if chosen else {"outcome": "cancelled"})
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "result": {"outcome": outcome}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32601, "message": f"{method} is not supported by this client"}}
        try:
            self._send(reply)
        except (OSError, ValueError):
            pass

    def _note_activity(self, params):
        if not isinstance(params, dict) or params.get("sessionId") != self.sid:
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        kind = update.get("sessionUpdate")
        if kind not in ("agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"):
            return
        with self.lock:
            if not self.busy:
                return
            self.last_activity_at = time.time()
            call_id = update.get("toolCallId")
            if isinstance(call_id, str) and kind in ("tool_call", "tool_call_update"):
                status = update.get("status")
                name = update.get("name")
                if status in ("completed", "failed"):
                    self.active_tools.pop(call_id, None)
                elif isinstance(name, str) and name and status in (None, "pending", "in_progress"):
                    self.active_tools[call_id] = name[:80]

    def _read(self):
        try:
            for raw in self.proc.stdout:
                try:
                    self.log_fh.write(raw.decode("utf-8", "replace"))
                    self.log_fh.flush()
                except (OSError, ValueError):
                    pass
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg:
                    if msg["method"] == "session/update":
                        self._note_activity(msg.get("params"))
                    if "id" in msg:
                        self._answer(msg)
                    continue
                rid = msg.get("id")
                with self.lock:
                    waiter = self._waiters.pop(rid, None)
                    if rid in self._prompts:
                        self._prompts.pop(rid)
                        if self._prompts:
                            self.inflight = list(self._prompts.values())[-1]
                        else:
                            self.busy = False
                            self.inflight = None
                            self.idle_since = time.time()
                            err = msg.get("error")
                            self.last_result = (
                                {"status": "ERROR", "error": (err or {}).get("message") or str(err)}
                                if err else dict(msg.get("result") or {}, status="SUCCESS"))
                if waiter is not None:
                    waiter[1] = msg
                    waiter[0].set()
        finally:
            self.exited = True
            with self.lock:
                lost = list(self._prompts.values())
                self._prompts.clear()
                self.inflight = None
                self.busy = False
                waiters = list(self._waiters.values())
                self._waiters.clear()
            for waiter in waiters:
                waiter[1] = {"error": {"message": "agy acp exited"}}
                waiter[0].set()
            if lost and self.turns <= 1 and time.time() - self.started < 30:
                global _ACP_DISABLED_UNTIL
                _ACP_DISABLED_UNTIL = time.time() + 300
            for text in lost:
                try:
                    queued = _core._apply_pending_input_operations(self.sid, [{
                        "field": "resume", "action": "append_tail", "value": text,
                    }])
                    _core._log_activity(
                        "inject", "AGY_ACP_LOST",
                        f"session={self.sid} pid={self.proc.pid} "
                        f"requeued={int(bool(queued.get('ok')))}",
                    )
                except Exception:
                    pass
            try:
                self.log_fh.close()
            except OSError:
                pass

    def _prompt(self, text, *, only_if_idle):
        rid = self._new_id()
        with self.lock:
            if only_if_idle and self.busy:
                return False
            self.prompt_started_at = time.time()
            self.last_activity_at = self.prompt_started_at
            self.active_tools.clear()
            if not self.busy:
                self.busy_since = self.prompt_started_at
            self._prompts[rid] = text
            self.busy = True
            self.inflight = text
            self.turns += 1
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": "session/prompt", "params": {
                "sessionId": self.sid, "prompt": [{"type": "text", "text": text}],
            }})
        except (OSError, ValueError):
            with self.lock:
                self._prompts.pop(rid, None)
                self.turns -= 1
                if not self._prompts:
                    self.busy = False
                    self.inflight = None
            raise
        return True

    def write(self, text):
        return self._prompt(text, only_if_idle=True)

    def steer(self, text):
        return self._prompt(text, only_if_idle=False)

    def retire_if_missing_result(self, now):
        """ACP answers every prompt; nothing to infer from the transcript."""
        return

    def close(self):
        """Closing stdin makes `agy acp` exit."""
        try:
            self.proc.stdin.close()
        except OSError:
            pass


def state(sid):
    """None when no live stream process owns ``sid``; else its busy/idle state."""
    with _LOCK:
        sp = _PROCS.get(sid)
    if sp is None:
        return None
    if not sp.alive():
        with _LOCK:
            if _PROCS.get(sid) is sp:
                _PROCS.pop(sid, None)
        return None
    now = time.time()
    if sp.busy:
        sp.retire_if_missing_result(now)
    if sp.busy and now - sp.busy_since > _core._antigravity_resume_stale_seconds():
        # A turn that outlived the print timeout is hung, not working.
        sp.close()
        try:
            sp.proc.terminate()
        except OSError:
            pass
        return None
    return {
        "pid": sp.proc.pid, "busy": sp.busy, "turns": sp.turns,
        "key": sp.key, "idle_for_s": 0.0 if sp.busy else time.time() - sp.idle_since,
        "last_result": sp.last_result,
    }


def acp_session_ids():
    with _LOCK:
        return {sid for sid, proc in _PROCS.items() if isinstance(proc, _AcpProc) and proc.alive()}


def acp_activity(sid):
    """Read the attached process without altering its turn or queue."""
    with _LOCK:
        proc = _PROCS.get(sid)
    if not isinstance(proc, _AcpProc) or not proc.alive():
        return None
    with proc.lock:
        if not proc.busy:
            return {"busy": False, "ts": proc.idle_since}
        last_tool_id = next(reversed(proc.active_tools), None)
        return {
            "busy": True, "ts": proc.last_activity_at,
            "working_since": proc.prompt_started_at,
            "tool": proc.active_tools.get(last_tool_id),
        }


def owns_pid(pid):
    with _LOCK:
        return any(sp.proc.pid == pid and sp.alive() for sp in _PROCS.values())


def send(sid, text):
    """Write ``text`` as the next turn of the live stream process.

    Returns {"ok": True, "via": "antigravity-stream"} or an error dict; the
    caller must have checked ``state(sid)`` says idle.
    """
    with _LOCK:
        sp = _PROCS.get(sid)
    if sp is None or not sp.alive():
        return {"ok": False, "error": "no live Antigravity stream process"}
    try:
        if not sp.write(text):
            return {"ok": False, "code": "busy", "error": "turn in progress"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"stream write failed: {exc}"}
    via = "antigravity-acp" if isinstance(sp, _AcpProc) else "antigravity-stream"
    return {"ok": True, "pid": sp.proc.pid, "via": via, "turn": sp.turns}


def steer(sid, text):
    """Deliver ``text`` into the running turn (ACP processes only).

    Idle processes take it as a normal turn. Returns an error dict with
    ``code: steer_unsupported`` for a stream-json process.
    """
    with _LOCK:
        sp = _PROCS.get(sid)
    if sp is None or not sp.alive():
        return {"ok": False, "error": "no live Antigravity process"}
    if not getattr(sp, "supports_steer", False):
        return {"ok": False, "code": "steer_unsupported",
                "error": "this Antigravity process runs stream-json, which can't steer"}
    try:
        sp.steer(text)
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"steer write failed: {exc}"}
    return {"ok": True, "pid": sp.proc.pid, "via": "antigravity-acp-steer", "turn": sp.turns}


def spawn(sid, cmd, cwd, log_path, key, env=None, cli_log_path=None):
    """Start the stream process for ``sid``. ``cmd`` already ends with the
    stream-json flags; the first message is sent with ``send``."""
    log_fh = open(log_path, "a")
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log_fh,
            cwd=cwd, start_new_session=True, env=env,
        )
    except (OSError, ValueError):
        log_fh.close()
        raise
    sp = _StreamProc(sid, proc, log_fh, key, cwd, cli_log_path)
    threading.Thread(target=sp._read, daemon=True, name=f"agy-stream-{sid[:8]}").start()
    with _LOCK:
        old = _PROCS.get(sid)
        _PROCS[sid] = sp
    if old is not None and old is not sp:
        old.close()
    _start_reaper()
    return sp


def spawn_acp(sid, cmd, cwd, log_path, key, env=None, cli_log_path=None,
              model_label="", skip_permissions=True, handshake_timeout=None):
    """Start `agy acp` for ``sid`` and attach it to the existing conversation.

    Raises OSError/ValueError/_AcpError on any failure (the caller falls back
    to stream-json); a failed start also pauses ACP for five minutes so each
    message doesn't pay for another doomed handshake.
    """
    global _ACP_DISABLED_UNTIL
    log_fh = open(log_path, "a")
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log_fh,
            cwd=cwd, start_new_session=True, env=env,
        )
    except (OSError, ValueError):
        log_fh.close()
        _ACP_DISABLED_UNTIL = time.time() + 300
        raise
    sp = _AcpProc(sid, proc, log_fh, key, cwd, cli_log_path, skip_permissions)
    threading.Thread(target=sp._read, daemon=True, name=f"agy-acp-{sid[:8]}").start()
    try:
        sp.handshake(model_label, handshake_timeout)
    except _AcpError as exc:
        sp.close()
        try:
            proc.terminate()
        except OSError:
            pass
        if not isinstance(exc, _AcpSessionError):
            _ACP_DISABLED_UNTIL = time.time() + 300
        try:
            _core._log_activity("inject", "AGY_ACP_FALLBACK",
                                f"session={sid} error=\"{str(exc)[:160]}\"")
        except Exception:
            pass
        raise
    with _LOCK:
        old = _PROCS.get(sid)
        _PROCS[sid] = sp
    if old is not None and old is not sp:
        old.close()
    _start_reaper()
    return sp


def retire(sid):
    """Close the idle process for ``sid`` (settings changed). Safe if absent."""
    with _LOCK:
        sp = _PROCS.pop(sid, None)
    if sp is not None:
        sp.close()


def _start_reaper():
    global _REAPER_STARTED
    with _LOCK:
        if _REAPER_STARTED:
            return
        _REAPER_STARTED = True
    threading.Thread(target=_reap_idle_forever, daemon=True, name="agy-stream-reaper").start()


def _reap_idle_forever():
    while True:
        time.sleep(30)
        limit = _idle_close_seconds()
        with _LOCK:
            items = list(_PROCS.items())
        for sid, sp in items:
            if not sp.alive():
                with _LOCK:
                    if _PROCS.get(sid) is sp:
                        _PROCS.pop(sid, None)
            elif not sp.busy and time.time() - sp.idle_since > limit:
                with _LOCK:
                    if _PROCS.get(sid) is sp:
                        _PROCS.pop(sid, None)
                sp.close()  # exits by itself once stdin is closed
