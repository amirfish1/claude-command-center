"""Release an idle CCC-owned Codex writer before opening the desktop app."""

import sys
import threading
import time

from ccc_server import core as _core

_attempt_lock = threading.Lock()
_in_progress = False


def desktop_available():
    """Check the OS scheme registration without launching or releasing work."""
    if sys.platform != "darwin":
        return False
    import ctypes
    try:
        cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        ls = ctypes.CDLL('/System/Library/Frameworks/CoreServices.framework/CoreServices')
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        ls.LSCopyDefaultHandlerForURLScheme.argtypes = [ctypes.c_void_p]
        ls.LSCopyDefaultHandlerForURLScheme.restype = ctypes.c_void_p
        scheme = cf.CFStringCreateWithCString(None, b'codex', 0x08000100)
        if not scheme:
            return False
        handler = None
        try:
            handler = ls.LSCopyDefaultHandlerForURLScheme(scheme)
            buffer = ctypes.create_string_buffer(512)
            return bool(handler and cf.CFStringGetCString(handler, buffer, len(buffer), 0x08000100)
                        and buffer.value == b'com.openai.codex')
        finally:
            if handler:
                cf.CFRelease(handler)
            cf.CFRelease(scheme)
    except (AttributeError, OSError):
        return False


def in_progress():
    # Readers that send native RPCs check under the app-server condition.
    return _in_progress


def _waiting(message):
    return {"ok": True, "pending": True, "message": message}


def _local_work_pending():
    with _core._CODEX_APP_SERVER_LOCK:
        if _core._CODEX_APP_SERVER_INITIALIZING:
            return True
        for state in _core._CODEX_APP_SERVER_THREAD_STATE.values():
            if (state.get("active_turn_id") or state.get("ccc_turn_start_pending")
                    or str(state.get("status") or "").lower() == "active"
                    or state.get("thread_needs_approval")):
                return True
    with _core._pending_resume_lock:
        return any(_core._pending_resume_queue.values())


def handover_to_desktop(session_id, cwd=None):
    """One bounded attempt; the conversation button retries while waiting.

    Unsubscribe retains the native writer lease during the unload grace.
    Close only our stdio process, and only after every loaded thread is idle.
    A shared/remote transport is never terminated to transfer one thread.
    """
    global _in_progress
    if sys.platform != "darwin":
        return {"ok": False, "error": "Codex Desktop handover is available on macOS."}
    if not desktop_available():
        return {"ok": False, "error": "Install Codex Desktop before handing over. Nothing was released."}
    if not _attempt_lock.acquire(blocking=False):
        return _waiting("Another desktop handover is being checked.")
    try:
        if _core.find_headless_codex_exec_owner(session_id, session_cwd=cwd):
            return _waiting("Waiting for this Codex reply to finish.")
        # Freeze native writes using the same condition as send_json. A
        # mutation already on the wire counts as in-flight and defers us.
        with _core._CODEX_APP_SERVER_LOCK:
            transport = _core._CODEX_APP_SERVER_TRANSPORT
            if _core._CODEX_APP_SERVER_INITIALIZING:
                return _waiting("Waiting for the Codex connection to settle.")
            _in_progress = True
        if transport is not None:
            with _core._CODEX_APP_SERVER_INFLIGHT_LOCK:
                if _core._CODEX_APP_SERVER_INFLIGHT:
                    return _waiting("Waiting for Codex to finish its current action.")
            loaded, cursor, seen_cursors = [], None, set()
            deadline = time.monotonic() + 20
            while True:
                params = {"cursor": cursor} if cursor else {}
                reply = _core._codex_app_server_request_to_transport(
                    transport, "thread/loaded/list", params, timeout=3)
                page = (reply.get("result") or {}).get("data")
                if not _core._codex_response_succeeded(reply) or not isinstance(page, list):
                    return {"ok": False, "error": "Could not verify Codex ownership. Nothing was released."}
                loaded.extend(page)
                cursor = (reply.get("result") or {}).get("nextCursor")
                if not cursor:
                    break
                if cursor in seen_cursors or len(loaded) > 128 or time.monotonic() > deadline:
                    return {"ok": False, "error": "Could not verify all loaded Codex conversations. Nothing was released."}
                seen_cursors.add(cursor)
            if session_id in loaded:
                if transport.kind != "stdio" or transport.proc is None:
                    return {"ok": False, "error": "This Codex server is managed elsewhere. Close the conversation in its owning app first."}
                if _local_work_pending():
                    return _waiting("Waiting for CCC Codex replies and queued messages to finish.")
                goals = _core._codex_goals_snapshot()
                if any((goals.get(sid) or {}).get("status", "active") == "active"
                       for sid in loaded if goals.get(sid)):
                    return _waiting("Pause active CCC Codex goals before handing over.")
                for sid in loaded:
                    if time.monotonic() > deadline:
                        return {"ok": False, "error": "Ownership check took too long. Nothing was released; try again."}
                    reply = _core._codex_app_server_request_to_transport(
                        transport, "thread/read", {"threadId": sid, "includeTurns": False}, timeout=3)
                    thread = (reply.get("result") or {}).get("thread") or {}
                    status = thread.get("status") or {}
                    if thread.get("ephemeral"):
                        if sid == session_id:
                            return {"ok": False, "error": "This temporary Codex conversation cannot be reopened in Desktop. Nothing was released."}
                        return _waiting("Waiting for other temporary Codex conversations to close.")
                    if not _core._codex_response_succeeded(reply) or status.get("type") not in ("idle", "notLoaded"):
                        return _waiting("Waiting for CCC Codex replies to finish.")
                    if status.get("type") == "idle":
                        for method, message in (
                            ("thread/queue/list", "Waiting for queued Codex messages to finish."),
                            ("thread/backgroundTerminals/list", "Stop Codex background terminals before handing over."),
                        ):
                            reply = _core._codex_app_server_request_to_transport(
                                transport, method, {"threadId": sid}, timeout=3)
                            data = (reply.get("result") or {}).get("data")
                            if not _core._codex_response_succeeded(reply) or not isinstance(data, list):
                                return {"ok": False, "error": "Could not verify Codex queued or background work. Nothing was released."}
                            if data or (reply.get("result") or {}).get("nextCursor"):
                                return _waiting(message)
                # Notifications can arrive while the passive reads are on the
                # wire. Recheck before detaching; writes remain frozen.
                goals = _core._codex_goals_snapshot()
                if any((goals.get(sid) or {}).get("status", "active") == "active"
                       for sid in loaded if goals.get(sid)):
                    return _waiting("Pause active CCC Codex goals before handing over.")
                with _core._CODEX_APP_SERVER_LOCK:
                    if transport is not _core._CODEX_APP_SERVER_TRANSPORT or _local_work_pending():
                        return _waiting("Codex activity changed; waiting for it to finish.")
                    _core._CODEX_APP_SERVER_TRANSPORT = None
                    _core._CODEX_APP_SERVER_PROC = None
                    _core._CODEX_APP_SERVER_INITIALIZED = False
                # Closing outside the condition lets the reader receive EOF
                # and disconnect normally. The freeze prevents a replacement.
                transport.close()
        result = _core.open_session_in_codex_desktop(session_id, cwd=cwd)
        return {**result, "pending": False, "handover": bool(result.get("ok"))}
    except Exception:
        return {"ok": False, "error": "Could not complete desktop handover. Try again."}
    finally:
        with _core._CODEX_APP_SERVER_LOCK:
            _in_progress = False
            _core._CODEX_APP_SERVER_LOCK.notify_all()
        _attempt_lock.release()
