"""Engine ownership and durable dispatch inside the persistent CCC worker."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path


# A worker restart re-adopts every live transport within seconds, so an
# uncertain item still unexplained a quarter of an hour later is not coming
# back. Long enough that a slow Codex app-server handshake is never retired
# out from under a reconcile; short enough that the count actually drains.
RETIRE_UNCERTAIN_AFTER_S = 900.0


ASYNC_OPERATIONS = {
    ("kimi", "spawn"),
    ("kimi", "prompt"),
    ("codex", "spawn"),
    ("codex", "resume"),
    ("claude", "spawn"),
    ("claude", "inject"),
    ("claude", "inject_pid"),
    ("claude", "resume"),
}


class EngineHost:
    """Whitelist adapter from worker RPC to CCC's mature engine functions.

    The legacy functions are imported only inside the worker process.  This is
    an intentional migration seam: behavior remains stable while ownership of
    subprocesses, pipes, and in-memory protocol state moves out of the
    dashboard.
    """

    def __init__(self, runtime):
        self.runtime = runtime
        self.ledger = runtime.ledger
        self._module = None
        self._module_lock = threading.Lock()
        self._dispatch_lock = threading.RLock()

    def _legacy(self):
        if self._module is not None:
            return self._module
        with self._module_lock:
            if self._module is None:
                os.environ["CCC_WORKER_PROCESS"] = "1"
                import server
                self._module = server
                # Capture a fingerprint of the server.py this process actually
                # loaded.  run.sh compares it to the file on disk so code changes
                # that do not bump __version__ still trigger a worker restart.
                try:
                    server_path = Path(server.__file__).resolve()
                    server._ccc_content_hash = hashlib.sha256(
                        server_path.read_bytes()
                    ).hexdigest()[:16]
                except Exception:
                    server._ccc_content_hash = None
                # server.main() installs this for the dashboard process, but
                # the worker never calls main() -- it only imports server as
                # a library here. Without this, SIGUSR2-triggered dumps (used
                # by e.g. _codex_app_server_dump_stacks_on_liveness_miss) are
                # silent no-ops in the one process that actually owns Codex
                # app-server execution and liveness churn.
                server._install_python_stack_dump_handler()
                # The first prewarm request can arrive immediately after this
                # lazy import. Finish the tiny one-time help probe now so that
                # request cannot observe a false "unsupported" result and
                # cache it for the entire composer lifetime.
                resolved = server._resolve_claude_bin()
                if resolved.get("available"):
                    server._probe_claude_spawn_capabilities(resolved.get("bin"))
                # Reservations are disposable and are persisted only so a
                # replacement worker can kill any process the old owner left.
                server._reap_orphaned_claude_prewarms()
                # A worker restart can inherit still-live CLI children. Reopen
                # their durable FIFOs here, in the process that owns engine
                # execution, rather than in the restartable dashboard.
                server._reattach_spawned_orphans(
                    skip_engines=("kimi",),
                    only_engines=("claude", "codex"),
                )
        return self._module

    def execute(self, params):
        engine = str(params.get("engine") or "").strip().lower()
        operation = str(params.get("operation") or "").strip().lower()
        args = params.get("args") if isinstance(params.get("args"), dict) else {}
        key = str(params.get("idempotency_key") or "").strip()
        if not engine or not operation or not key:
            raise ValueError("engine, operation, and idempotency_key are required")

        parent_work_id = params.get("parent_work_id") or None
        if not parent_work_id and args.get("parent_session_id"):
            parent = self.ledger.latest_for_session(args.get("parent_session_id"))
            parent_work_id = (parent or {}).get("id")
        item, created = self.ledger.submit(
            engine=engine,
            idempotency_key=key,
            session_id=str(args.get("session_id") or args.get("sid") or ""),
            kind=operation,
            payload={"operation": operation, "args": args},
            parent_id=parent_work_id,
            relation=params.get("relation") or "spawned",
        )
        if not created:
            return self._deduplicated(item)
        if self.ledger.drain_state().get("enabled"):
            return {
                "ok": True,
                "queued": True,
                "deferred": True,
                "code": "draining",
                "work_id": item["id"],
                "work": item,
            }

        return self._dispatch_item(item)

    def dispatch_queued(self):
        """Dispatch only work that is provably still unsent.

        Running/dispatching work recovered from a dead worker is marked
        ``uncertain`` by WorkLedger and intentionally excluded: replaying it
        could duplicate an already accepted model turn.
        """
        dispatched = []
        with self._dispatch_lock:
            if self.ledger.drain_state().get("enabled"):
                return dispatched
            for item in reversed(self.ledger.list(states=["queued"], limit=2000)):
                payload = (
                    item.get("payload")
                    if isinstance(item.get("payload"), dict) else {}
                )
                if not payload.get("operation") or not isinstance(
                    payload.get("args"), dict
                ):
                    continue
                dispatched.append(self._dispatch_item(item))
        return dispatched

    def reconcile_uncertain(self):
        """Reclaim work only when live execution evidence still exists."""
        reconciled = []
        legacy = self._legacy()
        for item in reversed(
            self.ledger.list(states=["uncertain", "interrupted"], limit=2000)
        ):
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            engine = item.get("engine") or ""
            operation = payload.get("operation") or item.get("kind") or ""
            sid = item.get("session_id") or args.get("session_id") or ""
            live = False
            if engine in ("claude", "codex"):
                pid = result.get("pid")
                if pid:
                    live = legacy._pid_alive(pid)
                if engine == "codex" and sid:
                    live = live or legacy._codex_app_server_thread_is_active(
                        sid, start_if_needed=True
                    )
            elif engine == "kimi" and sid:
                legacy._acp_maybe_attach_on_view("kimi", sid)
                snap = legacy._acp_session_snapshot("kimi", sid) or {}
                live = snap.get("status") == "active"
            if not live:
                continue
            running = self.ledger.transition(
                item["id"], "running",
                owner_epoch=self.runtime.epoch,
                lease_seconds=30,
                result=result,
                event_payload={"reason": "live execution evidence"},
            )
            self._track_async(item["id"], engine, operation, args, result)
            reconciled.append(running)
        return reconciled

    def retire_stale_uncertain(self, older_than_s=None):
        """Close out uncertain work that no reconcile can ever reclaim.

        ``reconcile_uncertain`` only *reclaims*; anything without live
        execution evidence was left in ``uncertain`` forever. Every worker
        restart adds a fresh batch, none of them ever leave, and the count is
        monotonic — so "N items need reconciliation" pinned the System status
        chip amber permanently and stopped meaning anything. Items measured up
        to 16 days old, all of them from processes that exited long ago.

        The terminal state is ``cancelled``, not ``failed``: the outcome is
        genuinely unknown (an inject may well have landed before the worker
        died), and recording an unknown as a failure would corrupt the failure
        count the same way this corrupted the uncertain one. The real reason
        goes on the item and into the event log.
        """
        cutoff = time.time() - (
            RETIRE_UNCERTAIN_AFTER_S if older_than_s is None else float(older_than_s)
        )
        retired = []
        for item in self.ledger.list(states=["uncertain"], limit=2000):
            touched = item.get("updated_at") or item.get("created_at") or 0
            if float(touched or 0) > cutoff:
                continue
            age_h = max(0.0, (time.time() - float(touched or 0)) / 3600.0)
            try:
                retired.append(self.ledger.transition(
                    item["id"], "cancelled",
                    error=(
                        "retired by reconciler: no live execution evidence "
                        f"{age_h:.0f}h after the worker restart that orphaned it"
                    ),
                    event_payload={"resolution": "reconciler retired stale uncertain"},
                ))
            except (KeyError, ValueError):
                # Raced another reconcile or an operator resolve. Either way it
                # left `uncertain`, which is the only outcome this wants.
                continue
        return retired

    def adopt_registry(self):
        """Adopt live legacy-owned transports before a dashboard restart."""
        legacy = self._legacy()
        before = {
            str(item.get("pid") or "")
            for item in legacy._spawned_sessions
            if item.get("pid") is not None
        }
        legacy._reattach_spawned_orphans(
            skip_engines=("kimi",),
            only_engines=("claude", "codex"),
        )
        attached_kimi = 0
        for entry in legacy._load_spawn_registry():
            if (entry.get("engine") or "") != "kimi":
                continue
            sid = entry.get("session_id") or entry.get("resumed_sid")
            if not sid:
                continue
            result = legacy._acp_maybe_attach_on_view("kimi", sid)
            if result is None or not isinstance(result, dict) or result.get("ok", True):
                attached_kimi += 1
        after = {
            str(item.get("pid") or "")
            for item in legacy._spawned_sessions
            if item.get("pid") is not None
        }
        return {
            "ok": True,
            "adopted": len(after - before),
            "tracked": len(after),
            "kimi_attached": attached_kimi,
        }

    def resolve_uncertain(self, work_id, action):
        item = self.ledger.get(work_id)
        if not item:
            raise KeyError("work item not found")
        action = str(action or "").strip().lower()
        if item.get("state") not in ("uncertain", "interrupted"):
            raise ValueError("only uncertain or interrupted work can be resolved")
        if action == "retry":
            queued = self.ledger.transition(
                work_id, "queued",
                owner_epoch=self.runtime.epoch,
                event_payload={"resolution": "operator retry"},
            )
            if self.ledger.drain_state().get("enabled"):
                return self._deduplicated(queued)
            return self._dispatch_item(queued)
        target = {
            "complete": "completed",
            "fail": "failed",
            "cancel": "cancelled",
        }.get(action)
        if not target:
            raise ValueError("action must be retry, complete, fail, or cancel")
        return {
            "ok": True,
            "resolution": target,
            "work_id": work_id,
            "work": self.ledger.transition(
                work_id, target,
                owner_epoch=self.runtime.epoch,
                error="" if target == "completed" else f"operator marked {target}",
                event_payload={"resolution": f"operator {action}"},
            ),
        }

    def _dispatch_item(self, item):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        engine = str(item.get("engine") or "")
        operation = str(payload.get("operation") or item.get("kind") or "")
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        with self._dispatch_lock:
            fresh = self.ledger.get(item["id"])
            if not fresh or fresh.get("state") != "queued":
                return self._deduplicated(fresh or item)
            if self.ledger.drain_state().get("enabled"):
                return self._deduplicated(fresh)
            self.ledger.transition(
                item["id"], "dispatching",
                owner_epoch=self.runtime.epoch,
                lease_seconds=30,
            )
        if engine == "claude":
            baseline_entry = self._claude_entry(
                self._legacy(),
                args.get("session_id") or "",
                None,
            )
            args["_work_result_baseline"] = (
                self._legacy()._headless_log_result_count(baseline_entry)
                if baseline_entry is not None else 0
            )
            self.ledger.update_payload(
                item["id"], {"operation": operation, "args": args}
            )
        try:
            call_args = dict(args)
            call_args.pop("_work_result_baseline", None)
            result = self._call(engine, operation, call_args)
        except Exception as exc:
            failed = self.ledger.transition(
                item["id"], "failed",
                owner_epoch=self.runtime.epoch,
                error=str(exc),
            )
            return {
                "ok": False,
                "error": str(exc),
                "code": "engine_dispatch_failed",
                "work_id": item["id"],
                "work": failed,
            }
        if not isinstance(result, dict):
            result = {"ok": False, "error": "engine returned a non-object response"}
        if not result.get("ok"):
            failed = self.ledger.transition(
                item["id"], "failed",
                owner_epoch=self.runtime.epoch,
                result=result,
                error=result.get("error") or "engine request failed",
            )
            return {**result, "work_id": item["id"], "work": failed}

        if (engine, operation) in ASYNC_OPERATIONS and not result.get("queued"):
            if result.get("session_id"):
                self.ledger.bind_session(item["id"], result.get("session_id"))
            running = self.ledger.transition(
                item["id"], "running",
                owner_epoch=self.runtime.epoch,
                lease_seconds=30,
                result=result,
            )
            self._track_async(item["id"], engine, operation, args, result)
            return {**result, "work_id": item["id"], "work": running}

        completed = self.ledger.transition(
            item["id"], "completed",
            owner_epoch=self.runtime.epoch,
            result=result,
        )
        return {**result, "work_id": item["id"], "work": completed}

    def query(self, params):
        engine = str(params.get("engine") or "").strip().lower()
        operation = str(params.get("operation") or "").strip().lower()
        args = params.get("args") if isinstance(params.get("args"), dict) else {}
        result = self._call(engine, operation, args, query=True)
        if isinstance(result, dict):
            return result
        return {"ok": True, "result": result}

    def _deduplicated(self, item):
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        if item["state"] == "completed":
            return {
                **result,
                "ok": bool(result.get("ok", True)),
                "deduplicated": True,
                "work_id": item["id"],
                "work": item,
            }
        if item["state"] in ("failed", "cancelled"):
            return {
                **result,
                "ok": False,
                "deduplicated": True,
                "error": item.get("last_error") or result.get("error") or item["state"],
                "work_id": item["id"],
                "work": item,
            }
        return {
            "ok": True,
            "queued": item["state"] == "queued",
            "accepted": item["state"] in ("dispatching", "running"),
            "deduplicated": True,
            "work_id": item["id"],
            "work": item,
            **result,
        }

    def _call(self, engine, operation, args, query=False):
        legacy = self._legacy()
        if engine == "kimi":
            if operation == "availability":
                info = legacy._resolve_kimi_bin()
                info["model"] = legacy._spawn_model_for_engine("kimi")
                conn = legacy._acp_conn("kimi")
                info["acp"] = bool(
                    conn
                    and conn.get("initialized")
                    and conn["transport"].alive()
                )
                return {"ok": True, "availability": info}
            if operation == "verify":
                return legacy._kimi_setup_verify()
            if operation == "spawn":
                return legacy.spawn_session_kimi(**args)
            if operation == "prompt":
                return legacy._acp_prompt(
                    "kimi",
                    args.get("session_id") or args.get("sid"),
                    args.get("text") or "",
                    mode=args.get("mode") or "send",
                    from_queue=bool(args.get("from_queue")),
                )
            if operation == "ask":
                return legacy._acp_ask_and_wait(
                    "kimi",
                    args.get("session_id") or args.get("sid"),
                    args.get("text") or "",
                    timeout_ms=int(args.get("timeout_ms") or 30000),
                )
            if operation == "cancel":
                return legacy._acp_cancel(
                    "kimi", args.get("session_id") or args.get("sid")
                )
            if operation == "approval":
                return legacy._acp_resolve_approval(
                    "kimi",
                    args.get("session_id") or args.get("sid"),
                    args.get("request_id"),
                    args.get("option_id"),
                )
            if operation == "config":
                return legacy._acp_set_config(
                    "kimi",
                    args.get("session_id") or args.get("sid"),
                    args.get("config_id"),
                    args.get("value"),
                )
            if operation == "attach":
                return legacy._acp_maybe_attach_on_view(
                    "kimi", args.get("session_id") or args.get("sid")
                ) or {"ok": True}
            if operation == "snapshot":
                snap = legacy._acp_session_snapshot(
                    "kimi", args.get("session_id") or args.get("sid")
                )
                return {"ok": True, "snapshot": snap}
            if operation == "bridge_status":
                return legacy._engine_bridge_status_local(
                    "kimi", args.get("session_id") or args.get("sid")
                )
            if operation == "bridge_restart":
                return legacy._restart_engine_bridge_local(
                    "kimi", args.get("session_id") or args.get("sid")
                )
            if operation == "deltas":
                sid = args.get("session_id") or args.get("sid")
                after = int(args.get("after") or 0)
                with legacy._ACP_LOCK:
                    state = (
                        legacy._ACP_SESSION_STATE.get("kimi") or {}
                    ).get(sid) or {}
                    deltas = [
                        delta for delta in (state.get("deltas") or [])
                        if int(delta.get("seq") or 0) > after
                    ]
                return {"ok": True, "deltas": deltas}
        if engine == "codex":
            if operation == "availability":
                info = legacy._resolve_codex_bin()
                info["model"] = legacy._spawn_model_for_engine("codex")
                return {"ok": True, "availability": info}
            if operation == "rpc":
                return {
                    "ok": True,
                    "response": legacy._codex_app_server_request(
                        args.get("method") or "",
                        args.get("params") or {},
                        timeout=int(args.get("timeout") or 20),
                    ),
                }
            if operation == "spawn":
                return legacy.spawn_session_codex(**args)
            if operation == "resume":
                return legacy.resume_session_codex(
                    args.get("session_id"),
                    args.get("text") or "",
                    steer=bool(args.get("steer")),
                    _from_queue=bool(args.get("from_queue")),
                )
            if operation == "approval":
                return legacy._codex_app_server_resolve_approval(
                    args.get("session_id"), args.get("decision")
                )
            if operation == "status":
                return {
                    "ok": True,
                    "status": legacy._codex_app_server_thread_public_status(
                        args.get("session_id")
                    ),
                }
            if operation == "active":
                return {
                    "ok": True,
                    "active": legacy._codex_app_server_thread_is_active(
                        args.get("session_id"),
                        start_if_needed=bool(args.get("start_if_needed")),
                    ),
                }
            if operation == "activity":
                return {
                    "ok": True,
                    "activity": legacy._codex_app_server_activity_fields(
                        args.get("session_id")
                    ),
                }
            if operation == "bridge_status":
                return legacy._engine_bridge_status_local(
                    "codex", args.get("session_id")
                )
            if operation == "bridge_restart":
                return legacy._restart_engine_bridge_local(
                    "codex", args.get("session_id")
                )
        if engine == "claude":
            if operation == "input_state":
                spawn = legacy._find_live_spawn_entry_for_session(
                    args.get("session_id")
                )
                if spawn is None or (spawn.get("engine") or "claude") != "claude":
                    return {"ok": True, "owned": False, "busy": False}
                tool_child = legacy._spawn_entry_active_tool_child(spawn)
                return {
                    "ok": True,
                    "owned": True,
                    "busy": bool(
                        legacy._headless_turn_in_progress(spawn) or tool_child
                    ),
                    "pid": spawn.get("pid"),
                    "active_child_pid": (
                        tool_child.get("pid") if isinstance(tool_child, dict) else None
                    ),
                }
            if operation == "prewarm":
                return legacy._start_claude_prewarm(**args)
            if operation == "spawn":
                return legacy.spawn_session(**args)
            if operation == "inject":
                return legacy._inject_text_into_session(
                    args.get("session_id"),
                    args.get("text") or "",
                    _from_terminal_queue=bool(args.get("from_terminal_queue")),
                    mode=args.get("mode") or "send",
                    wt_origin=bool(args.get("wt_origin")),
                    skip_wt=bool(args.get("skip_wt")),
                    preserve_queued_steer=bool(args.get("preserve_queued_steer")),
                    force_queue=bool(args.get("force_queue")),
                )
            if operation == "interrupt":
                return legacy._interrupt_claude_headless_local(
                    args.get("session_id")
                )
            if operation == "model":
                return legacy._set_session_model_headless_local(
                    args.get("session_id"),
                    args.get("model"),
                    bool(args.get("context_1m")),
                    args.get("reasoning_effort"),
                    bool(args.get("effort_only")),
                )
            if operation == "inject_pid":
                return legacy.inject_into_spawned(
                    int(args.get("pid")), args.get("text") or ""
                )
            if operation == "resume":
                return legacy.resume_session_headless(
                    args.get("session_id"),
                    args.get("text") or "",
                    cwd=args.get("cwd"),
                )
            if operation == "compact":
                return legacy.compact_session_context(
                    args.get("session_id"),
                    terminal_app=args.get("terminal_app"),
                    _from_terminal_queue=bool(args.get("from_terminal_queue")),
                )
            if operation == "clear":
                return legacy.clear_session_context(
                    args.get("session_id"),
                    terminal_app=args.get("terminal_app"),
                    initial_message=args.get("initial_message"),
                    _from_terminal_queue=bool(args.get("from_terminal_queue")),
                )
            if operation == "auto_handover_fire":
                return legacy._fire_auto_handover_local(args.get("session_id"))
        raise ValueError(f"unsupported worker engine operation: {engine}.{operation}")

    def _track_async(self, work_id, engine, operation, args, result):
        thread = threading.Thread(
            target=self._track_async_loop,
            args=(work_id, engine, operation, dict(args), dict(result)),
            daemon=True,
            name=f"ccc-work-{work_id[:8]}",
        )
        thread.start()

    def _track_async_loop(self, work_id, engine, operation, args, result):
        legacy = self._legacy()
        sid = (
            result.get("session_id")
            or args.get("session_id")
            or args.get("sid")
            or ""
        )
        seen_active = False
        started = time.time()
        while True:
            try:
                if not sid and result.get("pid"):
                    entry = next(
                        (
                            item for item in legacy._spawned_sessions
                            if str(item.get("pid")) == str(result.get("pid"))
                        ),
                        None,
                    )
                    if entry is not None:
                        sid = legacy._spawn_entry_session_id(entry) or ""
                        if sid:
                            result["session_id"] = sid
                            self.ledger.bind_session(work_id, sid)
                active, finished, completion = self._async_state(
                    legacy, engine, operation, sid, result, args
                )
                seen_active = seen_active or active
                if finished and (seen_active or time.time() - started > 1.0):
                    active_children = self.ledger.children(
                        work_id,
                        states=["queued", "dispatching", "running", "interrupted", "uncertain"],
                    )
                    if active_children:
                        self.ledger.renew_lease(work_id, self.runtime.epoch, 30)
                        time.sleep(2.0)
                        continue
                    self.ledger.transition(
                        work_id, "completed",
                        owner_epoch=self.runtime.epoch,
                        result={**result, **completion},
                    )
                    return
                self.ledger.renew_lease(work_id, self.runtime.epoch, 30)
            except Exception as exc:
                try:
                    self.ledger.transition(
                        work_id, "uncertain",
                        owner_epoch=self.runtime.epoch,
                        error=f"completion reconciliation failed: {exc}",
                    )
                except Exception:
                    pass
                return
            time.sleep(2.0)

    @staticmethod
    def _async_state(legacy, engine, operation, sid, result, args=None):
        args = args if isinstance(args, dict) else {}
        if engine == "kimi":
            snap = legacy._acp_session_snapshot("kimi", sid) or {}
            active = snap.get("status") == "active"
            return active, not active, {
                "stop_reason": snap.get("stop_reason"),
                "session_id": sid,
            }
        if engine == "codex":
            app_active = legacy._codex_app_server_thread_is_active(
                sid, start_if_needed=False
            )
            if result.get("via") in ("codex-app-spawn", "codex-app-server"):
                return bool(app_active), not bool(app_active), {"session_id": sid}
            pid = result.get("pid")
            entry = next(
                (
                    item for item in legacy._spawned_sessions
                    if str(item.get("pid")) == str(pid)
                ),
                None,
            )
            if entry is None:
                return False, True, {"session_id": sid}
            poll = legacy._poll_spawn_entry(entry)
            return poll is None, poll is not None, {
                "session_id": sid,
                "exit_code": poll,
            }
        if engine == "claude":
            pid = result.get("pid")
            entry = EngineHost._claude_entry(legacy, sid, pid)
            if entry is None:
                return False, True, {"session_id": sid}
            poll = legacy._poll_spawn_entry(entry)
            baseline = int(args.get("_work_result_baseline") or 0)
            result_count = legacy._headless_log_result_count(entry)
            completed_turn = result_count > baseline
            return poll is None and not completed_turn, (
                poll is not None or completed_turn
            ), {
                "session_id": sid,
                "exit_code": poll,
                "result_count": result_count,
            }
        return False, True, {}

    @staticmethod
    def _claude_entry(legacy, sid, pid):
        for item in reversed(legacy._spawned_sessions):
            if pid and str(item.get("pid")) == str(pid):
                return item
            if sid and (
                item.get("session_id") == sid
                or item.get("resumed_sid") == sid
                or legacy._spawn_entry_session_id(item) == sid
            ):
                return item
        return None
