"""ACP (Agent Client Protocol) adapter for Claude Command Center (CCC).

Bridges ACP — a JSON-RPC 2.0-over-stdio protocol for agent interoperability —
to CCC's session infrastructure.  Spawns ``claude -p`` in stream-json mode and
translates its event stream into ACP session/update notifications.

Usage::

    python ccc_acp.py                  # stdio mode (default)
    python ccc_acp.py --log-dir DIR    # also write raw logs to DIR
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import acp
from acp import (
    Agent,
    Client,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    RequestError,
)
from acp.schema import (
    AgentCapabilities,
    AllowedOutcome,
    CloseSessionResponse,
    DeniedOutcome,
    Implementation,
    ListSessionsResponse,
    PermissionOption,
    PlanEntry,
    PromptCapabilities,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionInfo,
    SessionListCapabilities,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("ccc-acp")

# ---------------------------------------------------------------------------
# Tool-kind mapping — Claude Code tool names → ACP ToolKind literals
# ---------------------------------------------------------------------------

_TOOL_KIND_MAP: Dict[str, str] = {
    "Bash": "execute",
    "Execute": "execute",
    "Write": "edit",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Read": "read",
    "Glob": "search",
    "Grep": "search",
    "LS": "read",
    "WebFetch": "fetch",
    "WebSearch": "search",
    "AskUserQuestion": "other",
    "TodoRead": "read",
    "TodoWrite": "edit",
    "Agent": "think",
    "NotebookEdit": "edit",
}


def _tool_kind(name: str) -> str:
    """Map a Claude Code tool name to an ACP ToolKind literal."""
    return _TOOL_KIND_MAP.get(name, "other")


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


class CCCACPSessionState:
    """Mutable bookkeeping for one live ACP session backed by a Claude proc."""

    def __init__(
        self,
        session_id: str,
        proc: subprocess.Popen,
        event_queue: queue.Queue,
        cwd: str,
        log_fh: Optional[Any] = None,
        log_path: Optional[str] = None,
    ):
        self.session_id = session_id
        self.proc = proc
        self.event_queue = event_queue
        self.cwd = cwd
        self.log_fh = log_fh
        self.log_path = log_path
        self.claude_session_id: Optional[str] = None
        self.done = False


# ---------------------------------------------------------------------------
# Claude binary resolution (mirrors CCC's _resolve_claude_bin)
# ---------------------------------------------------------------------------

_CLAUDE_CANDIDATE_DIRS = [
    Path.home() / ".claude" / "local" / "bin",
    Path.home() / ".npm-global" / "bin",
    Path.home() / ".volta" / "bin",
    Path.home() / ".asdf" / "shims",
    Path.home() / ".local" / "bin",
    Path.home() / "bin",
]


def _resolve_claude_bin() -> str:
    """Return the path to the Claude CLI binary, or raise FileNotFoundError."""
    env_bin = (os.environ.get("CCC_CLAUDE_BIN") or "").strip()
    if env_bin:
        expanded = os.path.expanduser(env_bin)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
        raise FileNotFoundError(f"CCC_CLAUDE_BIN={env_bin!r} is not executable")

    which = shutil.which("claude")
    if which:
        return which

    for d in _CLAUDE_CANDIDATE_DIRS:
        candidate = d / "claude"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)

    raise FileNotFoundError(
        "Claude Code CLI not found. Install it or set CCC_CLAUDE_BIN."
    )


# ---------------------------------------------------------------------------
# CCCACPAgent
# ---------------------------------------------------------------------------


class CCCACPAgent(Agent):
    """ACP agent backed by Claude Code headless sessions."""

    def __init__(self, log_dir: Optional[str] = None):
        super().__init__()
        self._conn: Optional[Client] = None
        self._sessions: Dict[str, CCCACPSessionState] = {}
        self._log_dir = Path(log_dir) if log_dir else None
        if self._log_dir:
            self._log_dir.mkdir(parents=True, exist_ok=True)

    # ── ACP lifecycle ─────────────────────────────────────────────────────

    def on_connect(self, conn: Client) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities=None,
        client_info=None,
        **kw,
    ) -> InitializeResponse:
        logger.info(
            "initialize: client=%s protocol=%s",
            getattr(client_info, "name", "unknown"),
            protocol_version,
        )
        return InitializeResponse(
            protocolVersion=1,
            agentCapabilities=AgentCapabilities(
                promptCapabilities=PromptCapabilities(embeddedContext=True),
                sessionCapabilities=SessionCapabilities(
                    close=SessionCloseCapabilities(),
                    list=SessionListCapabilities(),
                ),
            ),
            agentInfo=Implementation(
                name="ccc-acp",
                title="Claude Command Center",
                version="5.5.0",
            ),
        )

    # ── session/new ───────────────────────────────────────────────────────

    async def new_session(
        self, cwd: str = None, mcp_servers=None, **kw
    ) -> NewSessionResponse:
        """Create a new Claude Code headless session in *cwd*."""
        session_id = uuid.uuid4().hex
        if cwd is None:
            cwd = os.getcwd()
        cwd = os.path.abspath(cwd)
        # Security: ACP clients (IDEs) supply the cwd. Per SECURITY.md we note
        # the experimental adapter accepts client-supplied paths and spawns
        # Claude with full --dangerously-skip-permissions in them. Clamp to $HOME
        # by default (power users can opt out via env).
        _home = str(Path.home())
        if not (cwd == _home or cwd.startswith(_home + os.sep)):
            if not os.environ.get("CCC_ACP_ALLOW_OUTSIDE_HOME"):
                raise RequestError(
                    code=-32000,
                    message="ACP new_session cwd must be under user home (override with CCC_ACP_ALLOW_OUTSIDE_HOME=1). See SECURITY.md for the experimental ACP adapter risk surface.",
                )
        os.makedirs(cwd, exist_ok=True)

        log_fh: Optional[Any] = None
        log_path: Optional[str] = None
        if self._log_dir:
            log_path = str(self._log_dir / f"acp-{session_id[:12]}.log")
            log_fh = open(log_path, "w")

        try:
            claude_bin = _resolve_claude_bin()
        except FileNotFoundError as exc:
            if log_fh:
                log_fh.close()
            raise RequestError(code=-32000, message=str(exc))

        cmd = [
            claude_bin,
            "-p",
            "--verbose",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--dangerously-skip-permissions",
        ]

        env = dict(os.environ)
        # Disable CCC question relay inside the spawned Claude — the ACP
        # adapter handles permissions at the protocol level.
        env.pop("CCC_RELAY_QUESTIONS", None)
        env.pop("CCC_QUESTION_RELAY_DIR", None)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log_fh or subprocess.DEVNULL,
                cwd=cwd,
                start_new_session=True,
                env=env,
            )
        except (FileNotFoundError, OSError) as exc:
            if log_fh:
                log_fh.close()
            raise RequestError(
                code=-32000, message=f"Failed to start Claude: {exc}"
            )

        eq: queue.Queue = queue.Queue()
        state = CCCACPSessionState(
            session_id=session_id,
            proc=proc,
            event_queue=eq,
            cwd=cwd,
            log_fh=log_fh,
            log_path=log_path,
        )
        self._sessions[session_id] = state

        # Background reader → event queue
        threading.Thread(
            target=self._reader_thread,
            args=(state,),
            daemon=True,
            name=f"acp-reader-{session_id[:8]}",
        ).start()

        # Consume the initial startup event so the session is fully ready
        # before we return to the client.
        try:
            first = await asyncio.get_event_loop().run_in_executor(
                None, eq.get, True, 15.0
            )
            if first.get("type") == "system":
                state.claude_session_id = (
                    first.get("session_id") or first.get("sessionId")
                )
        except queue.Empty:
            logger.warning(
                "session %s: no startup event within 15 s", session_id[:8]
            )

        logger.info(
            "new_session: %s  cwd=%s  pid=%s  claude_sid=%s",
            session_id[:8],
            cwd,
            proc.pid,
            (state.claude_session_id or "?")[:12],
        )
        return NewSessionResponse(sessionId=session_id)

    # ── session/prompt ────────────────────────────────────────────────────

    async def prompt(
        self, prompt, session_id: str, message_id=None, **kw
    ) -> PromptResponse:
        """Inject a user turn and stream the Claude response as ACP updates."""
        state = self._sessions.get(session_id)
        if not state:
            raise RequestError(
                code=-32001, message=f"Unknown session: {session_id}"
            )
        if state.proc.poll() is not None:
            raise RequestError(code=-32002, message="Claude process has exited")

        conn = self._conn
        text = self._extract_text(prompt)

        # ── Send the user message via stream-json to Claude's stdin ───────
        user_msg = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }
        payload = (json.dumps(user_msg) + "\n").encode("utf-8")
        try:
            state.proc.stdin.write(payload)  # type: ignore[union-attr]
            state.proc.stdin.flush()  # type: ignore[union-attr]
        except (BrokenPipeError, OSError) as exc:
            raise RequestError(
                code=-32003, message=f"Cannot write to Claude: {exc}"
            )

        # ── Drain events until the turn ends ──────────────────────────────
        stop_reason: str = "end_turn"
        loop = asyncio.get_event_loop()
        active_tool_calls: Dict[str, str] = {}  # tc_id → name

        while True:
            if state.proc.poll() is not None and state.event_queue.empty():
                stop_reason = "cancelled"
                break

            try:
                ev: Dict[str, Any] = await loop.run_in_executor(
                    None, state.event_queue.get, True, 2.0
                )
            except queue.Empty:
                if state.proc.poll() is not None:
                    stop_reason = "cancelled"
                    break
                continue

            ev_type = ev.get("type", "")

            # ── system events (session_id, startup info) ──────────────────
            if ev_type == "system":
                sid = ev.get("session_id") or ev.get("sessionId")
                if sid:
                    state.claude_session_id = sid

            # ── assistant message (text, thinking, tool_use) ──────────────
            elif ev_type == "assistant" and conn:
                msg = ev.get("message") or {}
                for block in msg.get("content") or []:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")

                    if btype == "text":
                        t = block.get("text", "").strip()
                        if t:
                            await conn.session_update(
                                session_id,
                                acp.update_agent_message_text(t),
                            )

                    elif btype == "thinking":
                        t = block.get("thinking", "").strip()
                        if t:
                            await conn.session_update(
                                session_id,
                                acp.update_agent_thought_text(t),
                            )

                    elif btype == "tool_use":
                        tc_id = block.get("id", uuid.uuid4().hex)
                        name = block.get("name", "?")
                        inp = block.get("input") or {}
                        active_tool_calls[tc_id] = name
                        title = self._tool_title(name, inp)
                        kind = _tool_kind(name)

                        await conn.session_update(
                            session_id,
                            acp.start_tool_call(
                                tc_id,
                                title=title,
                                kind=kind,  # type: ignore[arg-type]
                                status="in_progress",
                                raw_input=inp,
                            ),
                        )

            # ── tool_result (completes a pending tool_use) ────────────────
            elif ev_type == "tool_result" and conn:
                tc_id = ev.get("tool_use_id") or ev.get("tool_use_id", "")
                is_err = bool(ev.get("is_error"))
                result_content = ev.get("content")
                result_text = ""
                if isinstance(result_content, str):
                    result_text = result_content
                elif isinstance(result_content, list):
                    parts = []
                    for sub in result_content:
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", ""))
                    result_text = "\n".join(p for p in parts if p)

                if tc_id:
                    await conn.session_update(
                        session_id,
                        acp.update_tool_call(
                            tc_id,
                            status="failed" if is_err else "completed",
                            raw_output=result_text[:4000] if result_text else None,
                        ),
                    )

            # ── result (turn complete) ────────────────────────────────────
            elif ev_type == "result":
                r = ev.get("result") or {}
                cost = duration = None
                if isinstance(r, dict):
                    cost = r.get("cost_usd")
                    duration = r.get("duration_ms")
                elif isinstance(r, str) and r and conn:
                    await conn.session_update(
                        session_id,
                        acp.update_agent_message_text(r),
                    )
                logger.info(
                    "turn complete: session=%s cost=$%s dur=%sms",
                    session_id[:8],
                    cost,
                    duration,
                )
                break

        return PromptResponse(stopReason=stop_reason)

    # ── cancel ────────────────────────────────────────────────────────────

    async def cancel(self, session_id: str, **kw) -> None:
        state = self._sessions.get(session_id)
        if not state:
            return
        proc = state.proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        state.done = True
        logger.info("cancel: session %s terminated", session_id[:8])

    # ── close_session ─────────────────────────────────────────────────────

    async def close_session(
        self, session_id: str, **kw
    ) -> CloseSessionResponse | None:
        state = self._sessions.pop(session_id, None)
        if not state:
            return CloseSessionResponse()
        proc = state.proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if state.log_fh:
            try:
                state.log_fh.close()
            except OSError:
                pass
        logger.info("close_session: %s", session_id[:8])
        return CloseSessionResponse()

    # ── list_sessions ─────────────────────────────────────────────────────

    async def list_sessions(
        self, cursor=None, cwd=None, **kw
    ) -> ListSessionsResponse:
        sessions: list[SessionInfo] = []
        for sid, state in self._sessions.items():
            if state.proc.poll() is not None:
                continue
            sessions.append(
                SessionInfo(
                    sessionId=sid,
                    cwd=state.cwd,
                    title=state.claude_session_id or f"Claude {state.proc.pid}",
                )
            )
        return ListSessionsResponse(sessions=sessions)

    # ── internals ─────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(prompt) -> str:
        """Pull the first text string from an ACP prompt content-block list."""
        if isinstance(prompt, str):
            return prompt
        parts: list[str] = []
        for block in prompt or []:
            if isinstance(block, dict):
                t = block.get("text")
                if t:
                    parts.append(t)
            elif hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts)

    @staticmethod
    def _tool_title(name: str, inp: Dict[str, Any]) -> str:
        """Best-effort one-line title for a tool_use block."""
        if name == "Bash":
            cmd = inp.get("command", "")
            if len(cmd) > 100:
                cmd = cmd[:100] + "…"
            return f"Bash: {cmd}" if cmd else "Bash"
        if name in ("Write", "Edit", "MultiEdit", "Read"):
            path = (
                inp.get("file_path")
                or inp.get("path")
                or inp.get("filePath", "")
            )
            return f"{name}: {path}" if path else name
        if name == "Grep":
            pattern = inp.get("pattern", "")
            return f"Grep: {pattern}" if pattern else "Grep"
        if name == "Glob":
            pattern = inp.get("pattern", "")
            return f"Glob: {pattern}" if pattern else "Glob"
        return name

    def _reader_thread(self, state: CCCACPSessionState) -> None:
        """Read stream-json lines from Claude's stdout into the event queue."""
        proc = state.proc
        out = proc.stdout
        if out is None:
            state.done = True
            return

        while True:
            raw_line = out.readline()
            if not raw_line:
                # EOF — process has exited
                state.done = True
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("unparseable line: %s", line[:200])
                continue
            state.event_queue.put(ev)
            # Also mirror to the log file if requested
            if state.log_fh:
                try:
                    state.log_fh.write(line + "\n")
                    state.log_fh.flush()
                except OSError:
                    pass

        state.done = True
        # Sentinel so any waiters in prompt() wake up
        try:
            state.event_queue.put_nowait({"type": "_eof"})
        except queue.Full:
            pass
        logger.debug("reader exited for session %s", state.session_id[:8])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="CCC ACP agent server")
    parser.add_argument(
        "--log-dir",
        help="Directory for raw session logs (optional)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    agent = CCCACPAgent(log_dir=args.log_dir)
    asyncio.run(acp.run_agent(agent, use_unstable_protocol=True))


if __name__ == "__main__":
    main()
