# Codex Spawn Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second engine to CCC's "New session" path so the user can spawn a headless OpenAI Codex run instead of a headless Claude run, picking between them via a 2-way engine selector that replaces the existing `pkood spawn` toolbar checkbox.

**Architecture:** The Claude spawn pipeline at `server.py:4551` (`spawn_session`) is mirrored by a new `spawn_session_codex` next to it. A binary resolver tries `$CCC_CODEX_BIN` → `shutil.which("codex")` → `/Applications/Codex.app/Contents/Resources/codex`. Two new endpoints (`POST /api/sessions/spawn-codex`, `GET /api/sessions/spawn-codex/availability`) sit alongside the existing pkood routes. Spawned Codex children are tracked in the same `_spawned_sessions` list and `SPAWNED_PIDS_FILE` registry as Claude children, distinguished by a new `engine: "claude" | "codex"` field that the boot-time `_reattach_spawned_orphans` sweep branches on. The frontend replaces the `kptPkoodToggle` checkbox at `static/index.html:3064` with a 2-way `engine` segmented control, mirrors it into the new-session modal, and routes dispatcher calls based on its value. The `pkood:` prompt-prefix shortcut and `/api/pkood/spawn` endpoint stay untouched so the orchestration layer keeps working.

**Tech Stack:** Python 3 stdlib only (`server.py` is intentionally dependency-free per `CLAUDE.md`); single-file vanilla JS in `static/index.html`; OpenAI Codex CLI 0.125.0-alpha.3 (bundled inside `/Applications/Codex.app`).

**Spec:** [`docs/superpowers/specs/2026-04-28-codex-spawn-design.md`](../specs/2026-04-28-codex-spawn-design.md)

---

## File structure

Per the project's single-file-by-design conventions (`server.py` stdlib-only Python, `static/index.html` no-bundler HTML/JS), no new source files are created.

| File | Action | Responsibility for this feature |
|---|---|---|
| `server.py` | Modify | Adds `_resolve_codex_bin`, `spawn_session_codex`, `_pid_is_codex_process`, `engine` field plumbing in `_record_spawn_to_registry` + `_reattach_spawned_orphans`, two new endpoints, and minor-version bump. |
| `static/index.html` | Modify | Replaces `kptPkoodToggle` checkbox with a 2-way engine `<select>`, mirrors it in the new-session modal, routes dispatcher calls, adds `.source-badge.codex` CSS, threads `engine` through `insertPendingSpawnCard`, polls availability. |
| `pyproject.toml` | Modify | Version bump `0.2.1` → `0.3.0`. |
| `tests/test_smoke.py` | Modify | Adds `test_spawn_session_codex_exists` import-time assertion. |
| `changelog.d/added-codex-spawn-2026-04-30.md` | Create | Keep-a-Changelog snippet under the existing convention. |

The `pkood:` prompt-prefix shortcut, `/api/pkood/spawn`, `/api/pkood/inject`, `/api/pkood/kill`, and the `pkood` skill all stay as-is — only the toolbar/modal *engine choice* loses pkood.

---

## Task 1 — Backend: Codex binary resolver `_resolve_codex_bin()`

**Files:**
- Modify: `server.py` — insert just above `spawn_session` (current location: `server.py:4551`)
- Test: `tests/test_smoke.py` — add a unit test that exercises the env-override path

- [ ] **Step 1: Write the failing test**

Append to `tests/test_smoke.py` inside `class TestServerImports`:

```python
    def test_resolve_codex_bin_prefers_env_override(self):
        """`_resolve_codex_bin` must honour CCC_CODEX_BIN when it points
        at an executable file. Verifies the precedence head — env var
        always wins over `which codex` and the app-bundle fallback."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_resolve_codex_bin"))

        import tempfile, os, stat
        with tempfile.NamedTemporaryFile(prefix="codex-", suffix=".sh", delete=False) as f:
            f.write(b"#!/bin/sh\nexit 0\n")
            fake_bin = f.name
        os.chmod(fake_bin, os.stat(fake_bin).st_mode | stat.S_IXUSR)

        prev = os.environ.get("CCC_CODEX_BIN")
        try:
            os.environ["CCC_CODEX_BIN"] = fake_bin
            result = server._resolve_codex_bin()
            self.assertEqual(result["bin"], fake_bin)
            self.assertTrue(result["available"])
        finally:
            if prev is None:
                os.environ.pop("CCC_CODEX_BIN", None)
            else:
                os.environ["CCC_CODEX_BIN"] = prev
            os.unlink(fake_bin)

    def test_resolve_codex_bin_returns_unavailable_when_missing(self):
        """When CCC_CODEX_BIN points at a non-existent path AND the
        Codex.app bundle is absent AND `which codex` finds nothing,
        the resolver must return {available: False, reason: ...}
        rather than raising."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        prev = os.environ.get("CCC_CODEX_BIN")
        try:
            os.environ["CCC_CODEX_BIN"] = "/definitely/does/not/exist/codex"
            # Patch resolver helpers so we don't depend on host state.
            orig_which = server.shutil.which
            orig_isfile = server.os.path.isfile
            server.shutil.which = lambda name: None
            server.os.path.isfile = lambda p: False if "codex" in p else orig_isfile(p)
            try:
                result = server._resolve_codex_bin()
            finally:
                server.shutil.which = orig_which
                server.os.path.isfile = orig_isfile
            self.assertFalse(result["available"])
            self.assertIn("reason", result)
        finally:
            if prev is None:
                os.environ.pop("CCC_CODEX_BIN", None)
            else:
                os.environ["CCC_CODEX_BIN"] = prev
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_resolve_codex_bin_prefers_env_override tests.test_smoke.TestServerImports.test_resolve_codex_bin_returns_unavailable_when_missing -v
```

Expected: both FAIL with `AttributeError: module 'server' has no attribute '_resolve_codex_bin'`.

- [ ] **Step 3: Implement `_resolve_codex_bin`**

Insert the following block in `server.py` immediately above `def spawn_session(prompt, name=None, cwd=None):` (currently at line 4551). `shutil` is already imported at the top of the file — verify by searching for `import shutil`; if absent (it shouldn't be, but defensive), add it next to the other stdlib imports.

```python
# ---------------------------------------------------------------------------
# Codex CLI binary resolution
# ---------------------------------------------------------------------------
#
# Tested against codex-cli 0.125.0-alpha.3 (the version bundled inside
# /Applications/Codex.app on the dev machine that shipped this feature).
# Flag names may shift in future alpha bumps — if a smoke spawn fails with
# "unrecognized argument", check `<bin> exec --help` and patch the cmd
# construction in spawn_session_codex below.

CODEX_APP_BUNDLE_PATH = "/Applications/Codex.app/Contents/Resources/codex"


def _resolve_codex_bin():
    """Locate a usable Codex CLI binary.

    Priority order:
      1. $CCC_CODEX_BIN (env override) — if set and executable.
      2. `shutil.which("codex")` — picks up Homebrew / Cargo / npm-global.
      3. /Applications/Codex.app/Contents/Resources/codex (macOS Codex
         desktop app's bundled CLI).

    Returns a dict so the caller and the availability endpoint can share
    one shape:
      {available: True,  bin: "<abs path>", source: "env|path|bundle"}
      {available: False, reason: "<human readable>", bin: None}
    """
    env_bin = os.environ.get("CCC_CODEX_BIN")
    if env_bin:
        if os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
            return {"available": True, "bin": env_bin, "source": "env"}
        return {
            "available": False,
            "bin": None,
            "reason": f"CCC_CODEX_BIN is set to {env_bin!r} but it isn't an executable file",
        }
    which_bin = shutil.which("codex")
    if which_bin:
        return {"available": True, "bin": which_bin, "source": "path"}
    if os.path.isfile(CODEX_APP_BUNDLE_PATH) and os.access(CODEX_APP_BUNDLE_PATH, os.X_OK):
        return {"available": True, "bin": CODEX_APP_BUNDLE_PATH, "source": "bundle"}
    return {
        "available": False,
        "bin": None,
        "reason": (
            "Codex CLI not found. Install Codex.app, "
            "`npm i -g @openai/codex`, or set CCC_CODEX_BIN."
        ),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_resolve_codex_bin_prefers_env_override tests.test_smoke.TestServerImports.test_resolve_codex_bin_returns_unavailable_when_missing -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat(spawn): add Codex CLI binary resolver

First piece of the Codex spawn engine. Resolves the codex binary in
priority order (CCC_CODEX_BIN env → which → Codex.app bundle path) and
returns a uniform availability shape the upcoming spawn function and
availability endpoint can both consume.
EOF
)"
```

---

## Task 2 — Backend: `spawn_session_codex()` function

**Files:**
- Modify: `server.py` — insert just below the existing `spawn_session` function (current location: `server.py:4622` is the end of `spawn_session`)
- Test: `tests/test_smoke.py` — add an import-time existence check

- [ ] **Step 1: Write the failing smoke test**

Append to `tests/test_smoke.py` inside `class TestServerImports`:

```python
    def test_spawn_session_codex_exists(self):
        """`spawn_session_codex` must exist alongside `spawn_session`
        with the same (prompt, name=None, cwd=None) signature so the
        new endpoint can call it the same way."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "spawn_session_codex"))
        import inspect
        sig = inspect.signature(server.spawn_session_codex)
        self.assertEqual(list(sig.parameters), ["prompt", "name", "cwd"])
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_spawn_session_codex_exists -v
```

Expected: FAIL with `AttributeError: module 'server' has no attribute 'spawn_session_codex'`.

- [ ] **Step 3: Implement `spawn_session_codex`**

Insert in `server.py` immediately after the closing brace of `spawn_session` (after the line `return {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path)}` at the end of `spawn_session`, blank line, then this block):

```python
def spawn_session_codex(prompt, name=None, cwd=None):
    """Spawn a headless Codex CLI run and return tracking info.

    Mirrors `spawn_session` but invokes the Codex CLI's `exec`
    subcommand instead of `claude -p`. Codex `exec` is one-shot —
    the prompt comes from argv and the process exits when the model
    is done — so we use `subprocess.DEVNULL` for stdin (no FIFO,
    no mid-run inject support).

    Tested against codex-cli 0.125.0-alpha.3.

    If `cwd` is provided, the spawned subprocess runs there AND the
    Codex `--cd` flag is set so the agent's workspace root matches
    the launch directory. Otherwise we inherit CCC's repo_path
    (backwards-compatible default).

    Returns the same shape as spawn_session:
      {ok: True,  pid, name, log}                       — success
      {ok: False, error}                                — resolver failed
    """
    resolved = _resolve_codex_bin()
    if not resolved["available"]:
        return {"ok": False, "error": resolved["reason"]}
    bin_path = resolved["bin"]

    session_name = _slugify(name or prompt)
    if not session_name:
        session_name = "unnamed"
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    log_filename = f"spawn-codex-{session_name}-{timestamp}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_filename

    spawn_cwd = cwd if cwd else str(repo_path)
    model = os.environ.get("CCC_CODEX_MODEL", "gpt-5.5-codex")

    cmd = [
        bin_path, "exec",
        "--json",
        "--skip-git-repo-check",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model", model,
        "--cd", spawn_cwd,
        "--",
        prompt,
    ]

    log_fh = open(log_path, "w")
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=spawn_cwd,
        start_new_session=True,
    )

    entry = {
        "pid": proc.pid,
        "name": session_name,
        "log": str(log_path),
        "prompt": prompt[:200],
        "started": timestamp,
        "proc": proc,
        "log_fh": log_fh,
        "fifo": None,         # Codex exec is one-shot; no inject FIFO.
        "stdin_fd": None,
        "engine": "codex",
    }
    _spawned_sessions.append(entry)
    _record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=None,
        engine="codex",
    )

    return {"ok": True, "pid": proc.pid, "name": session_name, "log": str(log_path)}
```

Note: `_record_spawn_to_registry` does not yet accept an `engine` kwarg — Task 3 fixes that. Run the smoke test below; the existence check will pass even before Task 3 because Python doesn't validate kwargs until the function is *called*.

- [ ] **Step 4: Run the test to verify it passes**

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_spawn_session_codex_exists -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat(spawn): add headless Codex spawn function

spawn_session_codex shells out to `codex exec --json --cd <cwd>
--dangerously-bypass-approvals-and-sandbox` with the binary located by
_resolve_codex_bin. Codex exec is one-shot, so unlike the Claude path
there's no FIFO+stream-json stdin — DEVNULL is fine and mid-run inject
is not supported (deferred per the spec). Tracks the child in
_spawned_sessions with engine="codex".
EOF
)"
```

---

## Task 3 — Backend: thread `engine` through registry + reattach + ps-grep

**Files:**
- Modify: `server.py:5074` (`_record_spawn_to_registry`)
- Modify: `server.py:5103` (`_pid_is_claude_process` → generalize)
- Modify: `server.py:5130` (`_reattach_spawned_orphans`)
- Modify: `server.py:4612` (`spawn_session` — pass `engine="claude"` so existing rows get the field)
- Test: `tests/test_smoke.py` — registry round-trip + engine-aware ps check

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_smoke.py` inside `class TestServerImports`:

```python
    def test_record_spawn_to_registry_persists_engine(self):
        """The on-disk spawn registry must round-trip an `engine` field
        so a CCC restart can branch claude-vs-codex reattach logic."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        import tempfile, json, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            registry_file = pathlib.Path(tmp) / "spawned-pids.json"
            orig = server.SPAWNED_PIDS_FILE
            server.SPAWNED_PIDS_FILE = registry_file
            try:
                server._record_spawn_to_registry(
                    pid=99999, name="t", log_path=pathlib.Path(tmp) / "x.log",
                    cwd=tmp, spawned_at="20260430T000000",
                    command_summary="test", fifo=None, engine="codex",
                )
                with registry_file.open() as f:
                    rows = json.load(f)
                self.assertEqual(rows[-1]["engine"], "codex")
            finally:
                server.SPAWNED_PIDS_FILE = orig

    def test_pid_is_engine_process_recognises_codex(self):
        """`_pid_is_engine_process` must accept an `engine` arg and match
        the right argv[0] basename for it (`claude` or `codex`)."""
        for mod in ("server", "morning", "morning_store"):
            sys.modules.pop(mod, None)
        server = importlib.import_module("server")
        self.assertTrue(hasattr(server, "_pid_is_engine_process"))
        # Stub `subprocess.run` to return a fake `ps` line for both engines.
        import types, subprocess as sp
        prev_run = sp.run
        def fake_run(args, **kw):
            class R: pass
            r = R(); r.returncode = 0; r.stdout = ""; r.stderr = ""
            if args[:2] == ["ps", "-p"]:
                pid = args[3]
                if pid == "11111":
                    r.stdout = "/usr/local/bin/claude -p --verbose\n"
                elif pid == "22222":
                    r.stdout = "/Applications/Codex.app/Contents/Resources/codex exec --json\n"
            return r
        sp.run = fake_run
        try:
            self.assertTrue(server._pid_is_engine_process(11111, "claude"))
            self.assertFalse(server._pid_is_engine_process(11111, "codex"))
            self.assertTrue(server._pid_is_engine_process(22222, "codex"))
            self.assertFalse(server._pid_is_engine_process(22222, "claude"))
        finally:
            sp.run = prev_run
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
python3 -m unittest tests.test_smoke.TestServerImports.test_record_spawn_to_registry_persists_engine tests.test_smoke.TestServerImports.test_pid_is_engine_process_recognises_codex -v
```

Expected: both FAIL — first with a `TypeError: _record_spawn_to_registry() got an unexpected keyword argument 'engine'`, second with `AttributeError: ... has no attribute '_pid_is_engine_process'`.

- [ ] **Step 3: Update `_record_spawn_to_registry` to accept `engine`**

In `server.py`, replace the function at line 5074 with:

```python
def _record_spawn_to_registry(pid, name, log_path, cwd, spawned_at, command_summary, fifo=None, engine="claude"):
    """Append a freshly-spawned session to the on-disk registry. The
    session_id is filled in lazily by the reattach sweep (it isn't known
    at fork time — Claude emits it in the first stream-json event, Codex
    emits it in its `--json` event stream).
    The fifo path is persisted so a fresh CCC instance can reopen the
    write side after a restart and continue injecting messages (Claude
    only — Codex exec is one-shot).
    `engine` ("claude" or "codex") tells the boot-time reattach sweep
    which ps-grep to use and which JSONL ingestion path to skip."""
    entries = _load_spawn_registry()
    entries.append({
        "pid": pid,
        "session_id": None,
        "name": name,
        "log": str(log_path),
        "fifo": str(fifo) if fifo else None,
        "cwd": str(cwd),
        "spawned_at": spawned_at,
        "command_summary": command_summary,
        "engine": engine,
    })
    _save_spawn_registry(entries)
```

- [ ] **Step 4: Pass `engine="claude"` from `spawn_session`**

In `server.py:4612` (the existing `_record_spawn_to_registry(...)` call inside `spawn_session`), add `engine="claude"` as the last argument. The existing call currently looks like:

```python
    _record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=fifo_path,
    )
```

Change it to:

```python
    _record_spawn_to_registry(
        pid=proc.pid,
        name=session_name,
        log_path=log_path,
        cwd=spawn_cwd,
        spawned_at=timestamp,
        command_summary=prompt[:200],
        fifo=fifo_path,
        engine="claude",
    )
```

There is a second call to `_record_spawn_to_registry` at `server.py:4990` (inside `resume_session_headless`) and a third at `server.py:5971` (inside `pkood_spawn`). Add `engine="claude"` to the resume one (it spawns a `claude --resume`). Leave the pkood one without an `engine` kwarg — `engine="claude"` is the default and pkood agents are still claude under the hood, so the default is correct.

- [ ] **Step 5: Replace `_pid_is_claude_process` with engine-aware `_pid_is_engine_process`**

In `server.py:5103`, replace the existing function with:

```python
def _pid_is_engine_process(pid, engine):
    """Verify a PID is actually a process for the given engine before
    treating it as one of ours. PIDs get reused, so a bare `os.kill(pid, 0)`
    isn't enough — we could end up trying to inject into someone's vim.
    Uses `ps -p <pid> -o command=` (works on macOS + Linux) and matches
    strictly on argv[0] basename — substring matching is too lenient
    (any python process whose argv mentions 'claude' would otherwise
    pass).

    `engine` is one of "claude" or "codex" — the basename we expect at
    argv[0]."""
    if engine not in ("claude", "codex"):
        return False
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
    if out.returncode != 0:
        return False
    cmd = out.stdout.strip()
    if not cmd:
        return False
    parts = cmd.split()
    if not parts:
        return False
    return parts[0].rsplit("/", 1)[-1] == engine


# Backwards-compat shim — older code paths (and any out-of-tree fork)
# that imports the old name keeps working without edits. Always asks
# the engine-aware version with engine="claude".
def _pid_is_claude_process(pid):
    return _pid_is_engine_process(pid, "claude")
```

- [ ] **Step 6: Teach `_reattach_spawned_orphans` to branch on engine**

In `server.py:5130`, find the line that says `if not _pid_is_claude_process(pid):` (around line 5166 in the current file) and replace it with:

```python
        # Step 2: is it actually a process of the engine we recorded?
        # Older registry entries pre-date the `engine` field — default
        # them to "claude" since that's all CCC spawned before Codex
        # support landed. PID reuse defence.
        engine = entry.get("engine", "claude")
        if not _pid_is_engine_process(pid, engine):
            dropped += 1
            continue
```

Then, in the same function, find the block that backfills `session_id` from the log (around line 5169-5177) and wrap it so Codex rows skip it (Codex's JSONL events have a different shape than Claude's `extract_session_id` regex expects, and the spec defers Codex JSONL ingestion to tier B):

```python
        # Step 3: try to backfill session_id from the log file if we don't
        # have it yet. Claude logs only — Codex's JSONL event shape
        # differs and ingestion is deferred to a later iteration.
        # Best-effort — failures don't block reattach.
        session_id = entry.get("session_id")
        log_path = entry.get("log")
        if engine == "claude" and not session_id and log_path:
            try:
                session_id = extract_session_id(log_path)
            except Exception:
                session_id = None
```

Finally, persist the `engine` field through to the survivor row (around line 5201, the `survivors.append({...})` block):

```python
        survivors.append({
            "pid": pid,
            "session_id": session_id,
            "name": entry.get("name"),
            "log": log_path,
            "fifo": fifo_path,
            "cwd": entry.get("cwd"),
            "spawned_at": entry.get("spawned_at"),
            "command_summary": entry.get("command_summary", ""),
            "engine": engine,
        })
```

And on the in-memory synthetic stub (around line 5186), add `"engine": engine` to the dict so live API responses can branch on it:

```python
        synthetic = {
            "pid": pid,
            "name": entry.get("name") or f"reattached-{pid}",
            "log": log_path or "",
            "prompt": entry.get("command_summary", "") or "",
            "started": entry.get("spawned_at", ""),
            "proc": stub,
            "log_fh": None,
            "fifo": fifo_path,
            "stdin_fd": stdin_fd,
            "reattached": True,
            "engine": engine,
        }
```

- [ ] **Step 7: Run all smoke tests to verify the refactor**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: every test PASSES. This includes the new tests for engine plumbing AND the existing tests (which exercise the import path, so any syntax error or undefined name breaks them).

- [ ] **Step 8: Commit**

```bash
git add server.py tests/test_smoke.py
git commit -m "$(cat <<'EOF'
feat(spawn): thread engine field through spawn registry + reattach

Adds a per-row engine="claude"|"codex" field to SPAWNED_PIDS_FILE so
the boot-time _reattach_spawned_orphans sweep can branch on it: codex
rows skip the Claude-specific extract_session_id step, and the ps-grep
PID-reuse defence now matches the right argv[0] basename per engine.
_pid_is_claude_process is kept as a thin shim for backwards-compat
with any out-of-tree caller.
EOF
)"
```

---

## Task 4 — Backend: `POST /api/sessions/spawn-codex` endpoint

**Files:**
- Modify: `server.py` — insert next to the existing `/api/sessions/spawn` handler at `server.py:8725`

- [ ] **Step 1: Read the current `/api/sessions/spawn` handler so the new endpoint mirrors its validation exactly**

```bash
python3 -c "import sys; lines=open('server.py').readlines(); start=next(i for i,l in enumerate(lines) if 'path == \"/api/sessions/spawn\"' in l); print(''.join(lines[start:start+25]))"
```

Expected: prints the existing handler block, including the `if not prompt`, abs-path, and isdir validations. The new handler reuses these verbatim.

- [ ] **Step 2: Insert the new handler block**

In `server.py`, find the existing line `elif path == "/api/sessions/spawn":` (around `server.py:8725`). Find the end of its handler block (the `else: ... self.send_json(spawn_session(prompt, name=name, cwd=cwd or None))` then the next `elif`). Insert directly after that closing block, before the next `elif`:

```python
        elif path == "/api/sessions/spawn-codex":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            prompt = (payload.get("prompt") or "").strip()
            name = (payload.get("name") or "").strip() or None
            cwd_raw = payload.get("cwd")
            cwd = cwd_raw.strip() if isinstance(cwd_raw, str) else None
            if not prompt:
                self.send_json({"ok": False, "error": "missing prompt"}, 400)
            elif cwd and not os.path.isabs(cwd):
                self.send_json({"ok": False, "error": f"cwd must be an absolute path: {cwd}"}, 400)
            elif cwd and not os.path.isdir(cwd):
                self.send_json({"ok": False, "error": f"cwd does not exist or is not a directory: {cwd}"}, 400)
            else:
                try:
                    result = spawn_session_codex(prompt, name=name, cwd=cwd or None)
                    # Resolver missing-binary failures get a 503 so the
                    # frontend can distinguish them from generic 500s and
                    # offer the install hint without parsing the body.
                    if not result.get("ok") and "not found" in (result.get("error") or "").lower():
                        self.send_json(result, 503)
                    else:
                        self.send_json(result)
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)}, 500)
```

- [ ] **Step 3: Restart the server and curl the endpoint to verify the route exists**

```bash
# Manual smoke — the test_smoke suite doesn't drive HTTP. Start the
# server, fire a request, kill the server.
./run.sh > /tmp/ccc-server.log 2>&1 &
SERVER_PID=$!
# Wait up to 5s for the port to come up.
for i in 1 2 3 4 5; do nc -z 127.0.0.1 8765 && break; sleep 1; done
curl -s -X POST http://127.0.0.1:8765/api/sessions/spawn-codex \
  -H 'Content-Type: application/json' \
  -d '{"prompt":""}'
echo
# Empty-prompt 400 should come back with the same error string.
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: prints `{"ok": false, "error": "missing prompt"}`.

If the port isn't 8765 on your install (configurable via `CCC_PORT`), substitute the right port. Check `pyproject.toml` and `run.sh` if unsure.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "$(cat <<'EOF'
feat(api): POST /api/sessions/spawn-codex

Routes to spawn_session_codex with the same payload shape and
input validation as /api/sessions/spawn (prompt required,
cwd must be absolute and existing). Resolver "Codex CLI not found"
failures map to HTTP 503 so the frontend can render the install
hint without sniffing the response body.
EOF
)"
```

---

## Task 5 — Backend: `GET /api/sessions/spawn-codex/availability` endpoint

**Files:**
- Modify: `server.py` — add a GET handler. Find the existing `do_GET` dispatcher block (search for `def do_GET` to locate it).

- [ ] **Step 1: Locate the GET dispatcher**

```bash
python3 -c "import sys; [print(i+1,l,end='') for i,l in enumerate(open('server.py').readlines()) if 'do_GET' in l]"
```

Expected: prints the line(s) where `do_GET` lives — typically one method definition. Note the line number for use in Step 2.

- [ ] **Step 2: Add the availability GET route**

Inside `do_GET`, find the existing GET-route chain (a series of `elif self.path ==` / `elif re.match` blocks). Add the following new branch near the other `/api/sessions/...` GET routes (a sensible spot is alongside `/api/sessions` itself if one exists, otherwise alongside the first `/api/...` GET):

```python
        elif path == "/api/sessions/spawn-codex/availability":
            info = _resolve_codex_bin()
            # Echo the model the spawn function would use so the frontend
            # can show it in a tooltip without a second roundtrip.
            info["model"] = os.environ.get("CCC_CODEX_MODEL", "gpt-5.5-codex")
            self.send_json(info)
```

(If your `do_GET` uses a different variable than `path` for the parsed URL — check the surrounding routes — match its convention.)

- [ ] **Step 3: Restart and verify**

```bash
./run.sh > /tmp/ccc-server.log 2>&1 &
SERVER_PID=$!
for i in 1 2 3 4 5; do nc -z 127.0.0.1 8765 && break; sleep 1; done
curl -s http://127.0.0.1:8765/api/sessions/spawn-codex/availability
echo
kill $SERVER_PID 2>/dev/null
wait $SERVER_PID 2>/dev/null
```

Expected: a JSON object with `available`, `bin` (or null), `source`/`reason`, and `model` keys. On the dev machine where `/Applications/Codex.app` exists, `available: true` and `bin: "/Applications/Codex.app/Contents/Resources/codex"`.

- [ ] **Step 4: Commit**

```bash
git add server.py
git commit -m "$(cat <<'EOF'
feat(api): GET /api/sessions/spawn-codex/availability

Lets the frontend probe whether a Codex spawn would succeed
without making the user click "Run" first. Returns the resolver's
{available, bin, source|reason} shape plus the configured model
string so a tooltip can show it.
EOF
)"
```

---

## Task 6 — Frontend: replace `pkood spawn` checkbox with engine selector

**Files:**
- Modify: `static/index.html` — three regions: View-menu markup (`:3064`), CSS (`:1351, :2690`), and the global JS reference (`:10365`)

- [ ] **Step 1: Replace the markup at `static/index.html:3064`**

Find this line:

```html
        <label class="kpt-label" title="Spawn via Pkood (background agent runner) instead of headless Claude — see docs" style="display:flex;align-items:center;gap:6px;padding:6px 10px;font-size:12px;cursor:pointer;color:var(--text);"><input type="checkbox" id="kptPkoodToggle" style="margin:0;"> pkood spawn</label>
```

Replace with:

```html
        <label class="kpt-label" title="Pick which engine spawns the next session — Claude (default) or OpenAI Codex" style="display:flex;align-items:center;gap:6px;padding:6px 10px;font-size:12px;cursor:pointer;color:var(--text);">
          Engine
          <select id="kptEngineSelect" style="background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:2px 6px;font-size:12px;">
            <option value="claude">claude</option>
            <option value="codex">codex</option>
          </select>
        </label>
```

- [ ] **Step 2: Update the global selector reference at `static/index.html:10365`**

Find:

```js
  const $kptPkoodToggle = document.getElementById('kptPkoodToggle');
```

Replace with:

```js
  const $kptEngineSelect = document.getElementById('kptEngineSelect');
  // Restore last-used engine from localStorage so the user's choice
  // persists across reloads. Defaults to 'claude' on first launch.
  if ($kptEngineSelect) {
    try {
      const saved = localStorage.getItem('ccc.spawnEngine');
      if (saved === 'claude' || saved === 'codex') $kptEngineSelect.value = saved;
    } catch (_) {}
    $kptEngineSelect.addEventListener('change', () => {
      try { localStorage.setItem('ccc.spawnEngine', $kptEngineSelect.value); } catch (_) {}
    });
  }
```

- [ ] **Step 3: Update the CSS rule at `static/index.html:2690`**

Find:

```css
    .kanban-panel-toolbar #kptPkoodToggle,
```

This selector is part of a multi-line rule that hides toolbar bits in compact mode. Change `#kptPkoodToggle` to `#kptEngineSelect` so the rule still applies:

```css
    .kanban-panel-toolbar #kptEngineSelect,
```

(Look at lines 2690-2700 for the surrounding context — there are likely several selectors comma-chained. Only the one that says `#kptPkoodToggle` needs to change; do not edit the others.)

- [ ] **Step 4: Manual smoke — restart server, open the UI, verify the View menu**

```bash
./run.sh > /tmp/ccc-server.log 2>&1 &
SERVER_PID=$!
for i in 1 2 3 4 5; do nc -z 127.0.0.1 8765 && break; sleep 1; done
echo "Server up; open http://127.0.0.1:8765 in a browser, click the View menu in the toolbar, confirm the 'Engine' dropdown shows 'claude' / 'codex' and the old 'pkood spawn' checkbox is gone."
# Leave the server running for Tasks 7+ manual smokes.
```

Expected: View menu shows an `Engine` dropdown with two options (`claude` selected by default, `codex` available). No `pkood spawn` checkbox in this dropdown.

Picking `codex`, reloading, then re-opening the View menu should show `codex` still selected (localStorage persistence working).

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): replace pkood checkbox with engine selector

The View menu's 'pkood spawn' checkbox is gone — its toggle was
rarely used and a 2-way claude|codex dropdown is honest about what
the user actually picks between. Selection persists in localStorage
under ccc.spawnEngine. The pkood: prompt-prefix shortcut and
/api/pkood/spawn endpoint stay intact for the orchestration layer.
EOF
)"
```

---

## Task 7 — Frontend: mirror the engine selector in the new-session modal

**Files:**
- Modify: `static/index.html:3296` (modal markup)
- Modify: `static/index.html:10626-10631` (modal binding + open-time sync)

- [ ] **Step 1: Replace the modal pkood checkbox markup at `static/index.html:3296`**

Find:

```html
        <label class="nsm-pkood"><input type="checkbox" id="nsmPkood"> pkood</label>
```

Replace with:

```html
        <label class="nsm-engine" style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--text);">
          Engine
          <select id="nsmEngineSelect" style="background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:2px 6px;font-size:12px;">
            <option value="claude">claude</option>
            <option value="codex">codex</option>
          </select>
        </label>
```

- [ ] **Step 2: Update the modal binding at `static/index.html:10626-10631`**

Find:

```js
  const $nsmPkood = document.getElementById('nsmPkood');
  function openNewSessionModal(body = '') {
    if (!$nsm) return;
    _clearNsmError();
    $nsmBody.value = body || '';
    if ($nsmPkood && $kptPkoodToggle) $nsmPkood.checked = $kptPkoodToggle.checked;
```

Replace with:

```js
  const $nsmEngineSelect = document.getElementById('nsmEngineSelect');
  function openNewSessionModal(body = '') {
    if (!$nsm) return;
    _clearNsmError();
    $nsmBody.value = body || '';
    // Sync from the toolbar selector so the modal opens on the same
    // engine the user just picked. Falls back to 'claude' if the
    // toolbar element is missing (defensive — both should always exist).
    if ($nsmEngineSelect && $kptEngineSelect) {
      $nsmEngineSelect.value = $kptEngineSelect.value || 'claude';
    }
```

- [ ] **Step 3: Manual smoke — open the modal, verify the dropdown**

Reload the running CCC tab (server should still be up from Task 6). Trigger the new-session modal (click the `+` button in the sidebar). Confirm:

- The modal shows an `Engine` dropdown (no more `pkood` checkbox).
- The dropdown's selected value matches the toolbar's selector.
- Changing the toolbar selector → reopening the modal → modal's selector matches.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): mirror engine selector in new-session modal

The modal now syncs from the toolbar selector on open instead of
mirroring the pkood checkbox. Single source of truth for the user's
engine choice across both spawn entry points.
EOF
)"
```

---

## Task 8 — Frontend: route dispatcher calls based on engine

**Files:**
- Modify: `static/index.html:10550-10615` (Run-button dispatcher)
- Modify: `static/index.html:10642-10685` (modal-submit dispatcher)
- Modify: `static/index.html:5033-5071` (`insertPendingSpawnCard` — accept `engine`)
- Modify: `static/index.html:7361-7362` (sourceBadge rendering)

- [ ] **Step 1: Update `insertPendingSpawnCard` to accept `engine`**

In `static/index.html:5033`, change the function signature and the `source` field:

Find:

```js
  function insertPendingSpawnCard(pid, subject, usePkood) {
    if (!pid) return;
    const id = 'spawning-' + pid;
    const card = {
      id, session_id: id,
      display_name: subject || ('Spawning #' + pid),
      first_message: '',
      source: usePkood ? 'pkood' : 'interactive',
```

Replace with:

```js
  function insertPendingSpawnCard(pid, subject, sourceOrEngine) {
    if (!pid) return;
    const id = 'spawning-' + pid;
    // Backwards-compat: this used to take `usePkood: bool`. Accept
    // either the legacy boolean (true → 'pkood') or a new explicit
    // string ('claude' | 'codex' | 'pkood' | 'interactive').
    let source;
    if (sourceOrEngine === true) source = 'pkood';
    else if (sourceOrEngine === false || sourceOrEngine == null) source = 'interactive';
    else if (typeof sourceOrEngine === 'string') source = sourceOrEngine;
    else source = 'interactive';
    const card = {
      id, session_id: id,
      display_name: subject || ('Spawning #' + pid),
      first_message: '',
      source,
```

- [ ] **Step 2: Update the Run-button dispatcher at `static/index.html:10551-10614`**

Find:

```js
      const usePkood = ($kptPkoodToggle && $kptPkoodToggle.checked) || prompt.startsWith('pkood:');
      const $kptWorktreeToggle = document.getElementById('kptWorktreeToggle');
      const useWorktree = !!($kptWorktreeToggle && $kptWorktreeToggle.checked);
      if (prompt.startsWith('pkood:')) prompt = prompt.slice(6).trim();
      if (!prompt) return;
      // Show the placeholder immediately — don't wait for the spawn POST to
      // return. Users were staring at a blank board for a beat because the
      // placeholder only materialized after /api/sessions/spawn responded.
      const subject = prompt.length > 60 ? prompt.slice(0, 60) + '…' : prompt;
      const tempPid = 'tmp-' + Date.now();
      insertPendingSpawnCard(tempPid, subject, usePkood);
      $kptRunBtn.disabled = true;
      $kptRunBtn.textContent = 'Spawning...';
      try {
        const endpoint = usePkood ? '/api/pkood/spawn' : '/api/sessions/spawn';
        // pkood doesn't support the worktree flag — silently drop it for that path.
        const body = usePkood ? { prompt } : { prompt, worktree: useWorktree };
```

Replace with:

```js
      const isPkoodPrefix = prompt.startsWith('pkood:');
      const engine = isPkoodPrefix
        ? 'pkood'
        : (($kptEngineSelect && $kptEngineSelect.value) || 'claude');
      const $kptWorktreeToggle = document.getElementById('kptWorktreeToggle');
      const useWorktree = !!($kptWorktreeToggle && $kptWorktreeToggle.checked);
      if (isPkoodPrefix) prompt = prompt.slice(6).trim();
      if (!prompt) return;
      // Show the placeholder immediately — don't wait for the spawn POST to
      // return. Users were staring at a blank board for a beat because the
      // placeholder only materialized after /api/sessions/spawn responded.
      const subject = prompt.length > 60 ? prompt.slice(0, 60) + '…' : prompt;
      const tempPid = 'tmp-' + Date.now();
      // Source for the optimistic card: 'codex' renders the codex chip;
      // 'pkood' keeps the pkood chip; 'claude' uses 'interactive' (no chip).
      const cardSource = engine === 'codex' ? 'codex'
                       : engine === 'pkood' ? 'pkood'
                       : 'interactive';
      insertPendingSpawnCard(tempPid, subject, cardSource);
      $kptRunBtn.disabled = true;
      $kptRunBtn.textContent = 'Spawning...';
      try {
        const endpoint = engine === 'pkood' ? '/api/pkood/spawn'
                       : engine === 'codex' ? '/api/sessions/spawn-codex'
                       : '/api/sessions/spawn';
        // pkood and codex don't support the worktree flag — drop it for those.
        const body = engine === 'claude'
          ? { prompt, worktree: useWorktree }
          : { prompt };
```

- [ ] **Step 3: Update the modal-submit dispatcher at `static/index.html:10642-10685`**

Find:

```js
    const usePkood = !!($nsmPkood && $nsmPkood.checked);
    const $nsmWorktree = document.getElementById('nsmWorktree');
    const useWorktree = !!($nsmWorktree && $nsmWorktree.checked);
    $nsmSubmit.disabled = true;
    $nsmSubmit.textContent = 'Launching...';
    try {
      const endpoint = usePkood ? '/api/pkood/spawn' : '/api/sessions/spawn';
      const body = usePkood
        ? { prompt, name: effectiveSubject }
        : { prompt, name: effectiveSubject, worktree: useWorktree };
      const res = await fetch(endpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
      if (data.ok) {
        closeNewSessionModal();
        insertPendingSpawnCard(data.pid, effectiveSubject, usePkood);
```

Replace with:

```js
    const engine = ($nsmEngineSelect && $nsmEngineSelect.value) || 'claude';
    const $nsmWorktree = document.getElementById('nsmWorktree');
    const useWorktree = !!($nsmWorktree && $nsmWorktree.checked);
    $nsmSubmit.disabled = true;
    $nsmSubmit.textContent = 'Launching...';
    try {
      const endpoint = engine === 'codex' ? '/api/sessions/spawn-codex'
                                          : '/api/sessions/spawn';
      const body = engine === 'codex'
        ? { prompt, name: effectiveSubject }
        : { prompt, name: effectiveSubject, worktree: useWorktree };
      const res = await fetch(endpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
      if (data.ok) {
        closeNewSessionModal();
        const cardSource = engine === 'codex' ? 'codex' : 'interactive';
        insertPendingSpawnCard(data.pid, effectiveSubject, cardSource);
```

(Note: the modal no longer offers `pkood` as an engine — only `claude` and `codex`. Users who want pkood spawning still have the toolbar `Run` button's `pkood:` prompt-prefix shortcut.)

- [ ] **Step 4: Manual smoke — spawn one Claude and one Codex session**

With the server still running (or restart it):

```bash
# If server isn't running:
./run.sh > /tmp/ccc-server.log 2>&1 &
SERVER_PID=$!
for i in 1 2 3 4 5; do nc -z 127.0.0.1 8765 && break; sleep 1; done
```

In the browser:
1. Set toolbar Engine = `claude`. Type `say hello and exit` in the kanban toolbar input. Click `Run`. Confirm a new card appears and the spawn log lands in CCC's log dir as `spawn-<slug>-<ts>.log`.
2. Set toolbar Engine = `codex`. Type `list the files in this repo and exit` and click `Run`. Confirm a new card appears (with the `codex` source field — Task 9 adds the visible chip) and the log file is named `spawn-codex-<slug>-<ts>.log`.
3. Tail the codex log:
   ```bash
   tail -f ~/.claude/command-center/logs/spawn-codex-*-*.log | head -30
   ```
   Expected: Codex JSONL events stream as the model runs (or, if the model name is wrong, a clear error from the CLI — see Task 11 for verification of the model string).

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): route spawn dispatcher by engine selector

Run button and modal-submit now read the engine selector
('claude' | 'codex') and dispatch to /api/sessions/spawn or
/api/sessions/spawn-codex accordingly. The pkood: prompt-prefix
shortcut still routes to /api/pkood/spawn untouched.

insertPendingSpawnCard's third arg is now an explicit source
string (with backwards-compat for the legacy boolean), so the
optimistic placeholder card carries the right `source` from the
moment it appears.
EOF
)"
```

---

## Task 9 — Frontend: `codex` source badge + CSS

**Files:**
- Modify: `static/index.html:1351` (CSS — add the codex variant)
- Modify: `static/index.html:7361-7362` (rendering — add the codex branch)

- [ ] **Step 1: Add CSS for `.source-badge.codex`**

Find this line at `static/index.html:1351`:

```css
  .conv-item .source-badge.pkood { background: rgba(210,153,34,0.2); color: var(--orange); }
```

Add a new line directly below it:

```css
  .conv-item .source-badge.codex { background: rgba(63, 185, 80, 0.2); color: var(--green); }
```

(Green-on-dark stands out cleanly next to the orange `pkood` chip and the purple branch chip — the spec required only "visually distinct from the branch / PR / spawn-state pulse"; green satisfies that.)

- [ ] **Step 2: Add the `codex` rendering branch**

At `static/index.html:7361-7362`, find:

```js
      let sourceBadge = '';
      if (c.source === 'pkood') sourceBadge = '<span class="source-badge pkood">pkood</span>';
```

Replace with:

```js
      let sourceBadge = '';
      if (c.source === 'pkood') sourceBadge = '<span class="source-badge pkood">pkood</span>';
      else if (c.source === 'codex' || c.engine === 'codex') sourceBadge = '<span class="source-badge codex">codex</span>';
```

(Reading `c.engine` as a fallback covers reattached rows whose `source` field comes from the server-side `engine` field — Task 10's server changes will populate this.)

- [ ] **Step 3: Plumb `engine` through the server-side card data**

The server's `/api/sessions` response builds card dicts that the frontend renders. Find where pkood cards set `source`:

```bash
python3 -c "lines=open('server.py').readlines(); [print(i+1, l, end='') for i,l in enumerate(lines) if \"'pkood'\" in l or '\"pkood\"' in l][:20]"
```

Look for places where the spawn list is folded into the conversations response — specifically, list_spawned_sessions's consumers in the do_GET dispatcher and the function that merges spawn entries into the kanban payload. The pattern in this codebase is to set `source` on the conversation dict for each spawned/pkood session. Find the merging function (search for `'source':` near `pkood` or near the kanban payload assembly) and add `engine` to its output.

A concrete edit (line numbers approximate — adapt to current file state):

```bash
python3 -c "
import re
with open('server.py') as f: src = f.read()
# Locate any place where a card dict is built from a _spawned_sessions entry
# and the 'source' field gets set. Print 6 lines of context for the first
# few matches so the engineer can pick the right insertion point.
for m in re.finditer(r\"['\\\"]source['\\\"]\\s*:\\s*['\\\"](?:interactive|pkood|spawned)\", src):
    start = src.rfind('\n', 0, max(0, m.start()-200))
    end = src.find('\n', m.end()+200)
    print('---')
    print(src[start+1:end])
"
```

For each match where the dict is for a spawn-list-derived card, add an `"engine": s.get("engine", "claude"),` field next to the existing `"source":` field. The frontend reads either `source === "codex"` or `engine === "codex"` (Step 2) — surfacing `engine` is the more honest signal.

Concretely: the `/api/sessions` handler iterates `_spawned_sessions` to merge in spawn-side cards. Inside whatever dict construction produces a card from a spawn entry `s`, add:

```python
                "engine": s.get("engine", "claude"),
```

If you find no obvious place to add it, ship Task 9 with just the frontend change (Step 1+2) — the optimistic placeholder already passes `source: 'codex'` from Task 8, so freshly spawned cards render the chip correctly. The `engine` plumbing is a nice-to-have for reattached cards after a CCC restart and can be its own follow-up commit.

- [ ] **Step 4: Manual smoke — spawn a codex session, see the chip**

Reload the UI. Set engine to `codex`, click Run on a small prompt, watch the new card. The card row should show a small green `codex` chip next to the existing badges/timestamps.

- [ ] **Step 5: Commit**

```bash
git add static/index.html server.py
git commit -m "$(cat <<'EOF'
feat(ui): codex source badge on spawned cards

A small green 'codex' chip on cards spawned via the Codex engine,
mirroring the existing orange 'pkood' chip. Renders for both
optimistic placeholders (source field) and reattached rows
(engine field carried over from the spawn registry).
EOF
)"
```

---

## Task 10 — Frontend: probe Codex availability and disable selector when missing

**Files:**
- Modify: `static/index.html` near `:10365` (the engine-selector init block from Task 6)

- [ ] **Step 1: Add the availability probe**

Right after the `$kptEngineSelect.addEventListener('change', ...)` block from Task 6 Step 2, add:

```js
  // Probe Codex availability so a user with no Codex install sees the
  // option greyed out instead of getting a 503 mid-spawn. Polled on
  // window focus too — handles "I just installed it; refresh the UI"
  // without a hard reload.
  async function refreshCodexAvailability() {
    try {
      const r = await fetch('/api/sessions/spawn-codex/availability');
      const d = await r.json();
      const codexOpt = $kptEngineSelect && $kptEngineSelect.querySelector('option[value="codex"]');
      const nsmCodexOpt = $nsmEngineSelect && $nsmEngineSelect.querySelector('option[value="codex"]');
      const reason = d.available ? '' : (d.reason || 'Codex CLI not found');
      [codexOpt, nsmCodexOpt].forEach(opt => {
        if (!opt) return;
        opt.disabled = !d.available;
        opt.title = reason;
        opt.textContent = d.available ? 'codex' : 'codex (unavailable)';
      });
      // If the user had codex selected but it's no longer available,
      // fall back to claude so the next spawn doesn't 503.
      if (!d.available && $kptEngineSelect && $kptEngineSelect.value === 'codex') {
        $kptEngineSelect.value = 'claude';
        try { localStorage.setItem('ccc.spawnEngine', 'claude'); } catch (_) {}
      }
    } catch (_) {
      // Probe is best-effort; a network blip shouldn't disable the dropdown.
    }
  }
  refreshCodexAvailability();
  window.addEventListener('focus', refreshCodexAvailability);
```

- [ ] **Step 2: Manual smoke — exercise the unavailable path**

```bash
# Kill the running server, point CCC_CODEX_BIN at a non-existent path,
# restart, reload the UI.
kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
CCC_CODEX_BIN=/tmp/does-not-exist ./run.sh > /tmp/ccc-server.log 2>&1 &
SERVER_PID=$!
for i in 1 2 3 4 5; do nc -z 127.0.0.1 8765 && break; sleep 1; done
# In the browser, reload, open the View menu. The codex option must
# show as 'codex (unavailable)' and be disabled. The toolbar selector
# must default to 'claude' (the previous value if codex was saved
# in localStorage gets coerced to claude).
```

Expected: the codex option is greyed out and unselectable; if it had been the saved choice, the dropdown auto-falls-back to claude.

Then restart without the broken env var to confirm it re-enables:

```bash
kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
./run.sh > /tmp/ccc-server.log 2>&1 &
SERVER_PID=$!
for i in 1 2 3 4 5; do nc -z 127.0.0.1 8765 && break; sleep 1; done
# Reload the browser tab; the codex option should be 'codex' (no
# parens) and selectable again.
```

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "$(cat <<'EOF'
feat(ui): probe Codex availability on load + window focus

Greys out the codex engine option when /api/sessions/spawn-codex/availability
reports the binary missing. Auto-coerces the saved selection back to
'claude' so the next spawn doesn't 503. Re-runs on window focus so a
fresh Codex install becomes selectable without a hard reload.
EOF
)"
```

---

## Task 11 — Version bump, changelog snippet, model-name verification, final smoke

**Files:**
- Modify: `pyproject.toml:3`
- Modify: `server.py:15`
- Create: `changelog.d/added-codex-spawn-2026-04-30.md`

- [ ] **Step 1: Verify the model name with a real spawn**

With the server running and codex available, send a tiny spawn through the API and tail the log. If the default `gpt-5.5-codex` is wrong, the JSONL events will contain a model-error message in the first few hundred bytes; if it's right, you'll see normal task-execution events.

```bash
RESP=$(curl -s -X POST http://127.0.0.1:8765/api/sessions/spawn-codex \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"reply with exactly one word: ok"}')
echo "$RESP"
LOG=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['log'])")
echo "Tailing $LOG (5 seconds):"
( tail -f "$LOG" & T=$!; sleep 5; kill $T 2>/dev/null ) | head -40
```

If you see a clear "model not found" / "unknown model" error, try these fallbacks in order until one works (each via `CCC_CODEX_MODEL=<name> ./run.sh`):
1. `gpt-5-codex`
2. `gpt-5.5-codex`
3. `gpt-5-codex-2025` or whatever the resolver suggests in its error message
4. Run `<bin> exec --help` and grep for the model option's value list — Codex's CLI usually lists supported models there

If a different name works, update the default literal in `server.py` inside `spawn_session_codex` AND in the availability handler in `do_GET`. Commit that change separately:

```bash
# Only if you change the default — skip otherwise.
git add server.py
git commit -m "fix(spawn): default Codex model is <NAME>, not gpt-5.5-codex"
```

If the documented `gpt-5.5-codex` works as-is, skip this commit.

- [ ] **Step 2: Bump versions**

In `pyproject.toml:3`, change:

```toml
version = "0.2.1"
```

to:

```toml
version = "0.3.0"
```

In `server.py:15`, change:

```python
__version__ = "0.2.1"
```

to:

```python
__version__ = "0.3.0"
```

- [ ] **Step 3: Write the changelog snippet**

Create `changelog.d/added-codex-spawn-2026-04-30.md` with:

```markdown
**OpenAI Codex as a spawn engine.** The kanban toolbar now has an
**Engine** dropdown (`claude` | `codex`) where the old `pkood spawn`
checkbox used to live, and the new-session modal mirrors it.
Selecting `codex` routes the next spawn through `codex exec --json
--dangerously-bypass-approvals-and-sandbox` instead of `claude -p`,
runs in the chosen working directory, and tracks the child on the
same kanban with a green `codex` chip.

Codex spawns are fire-and-watch in this iteration — no mid-run
inject (Codex `exec` is one-shot), no `claude --resume`-style
jump-in, and Codex JSONL ingestion isn't wired up yet. The
selector greys out automatically when the Codex CLI binary
can't be located (looked up via `$CCC_CODEX_BIN` →
`which codex` → `/Applications/Codex.app/Contents/Resources/codex`).

The `pkood:` prompt-prefix shortcut and `/api/pkood/spawn` endpoint
are unchanged. New endpoints: `POST /api/sessions/spawn-codex`,
`GET /api/sessions/spawn-codex/availability`. New env vars:
`CCC_CODEX_BIN` (binary override), `CCC_CODEX_MODEL` (model name,
default `gpt-5.5-codex` — verified at release time against
codex-cli 0.125.0-alpha.3).
```

(If the model-name verification in Step 1 produced a different working default, replace the `gpt-5.5-codex` literal in this snippet with the one you committed.)

- [ ] **Step 4: Run the smoke test suite one last time**

```bash
python3 -m unittest tests.test_smoke -v
```

Expected: every test PASSES, including the four new ones added in Tasks 1, 2, and 3.

- [ ] **Step 5: Final manual smoke — full happy path**

```bash
# Server up
./run.sh > /tmp/ccc-server.log 2>&1 &
SERVER_PID=$!
for i in 1 2 3 4 5; do nc -z 127.0.0.1 8765 && break; sleep 1; done

# Browser steps:
# 1. Open http://127.0.0.1:8765
# 2. Engine = claude → spawn "say hi and exit". Card appears, log lands at
#    ~/.claude/command-center/logs/spawn-<slug>-*.log
# 3. Engine = codex → spawn "list this repo's top-level files and exit".
#    Card appears with green 'codex' chip; log lands at
#    spawn-codex-<slug>-*.log; JSONL events stream.
# 4. Restart CCC mid-flight on a Codex spawn (kill SERVER_PID, run.sh again).
#    The Codex card reattaches via _reattach_spawned_orphans without
#    raising — verify by checking ./run.sh stdout for the
#    "[spawn-registry] reattached N orphans" line.

kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null
```

Expected: all four manual checks pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml server.py changelog.d/added-codex-spawn-2026-04-30.md
git commit -m "$(cat <<'EOF'
chore(release): 0.3.0 — codex spawn engine

Bumps both version locations in lockstep (pyproject.toml + server.py
__version__). Adds the changelog snippet under the Keep-a-Changelog
convention so `python3 scripts/release.py 0.3.0` will roll it into
CHANGELOG.md at release time.
EOF
)"
```

---

## Self-review

**Spec coverage check:** Each spec section maps to one or more plan tasks:
- Spec §1 (engine selector UI) → Tasks 6, 7, 10
- Spec §2 (`spawn_session_codex` + resolver + tracking) → Tasks 1, 2, 3
- Spec §3 (routing endpoints) → Tasks 4, 5
- Spec §4 (card lifecycle, `codex` chip) → Tasks 8, 9
- Spec §5 (out-of-scope cuts) → no tasks (deliberate)
- Spec §6 (tests + changelog + manual smoke) → Tasks 1, 2, 3 (smoke tests) + Task 11 (changelog + manual smoke)
- Spec §7 risks 1, 2, 3 → Task 11 Step 1 (model-name verification), top-of-function version-pin comment in Task 1 Step 3, macOS-only fallback path documented in Task 1 Step 3

**Placeholder scan:** No `TBD`, `TODO`, `implement later`, or `add error handling` lurking in tasks. Every code step shows the actual code. The one ambiguous spot — chip color — is resolved to a concrete green palette entry in Task 9 Step 1 with rationale.

**Type / signature consistency:**
- `_resolve_codex_bin` returns `{available, bin, source|reason}` (Task 1) — consumed identically by `spawn_session_codex` (Task 2) and `/api/sessions/spawn-codex/availability` (Task 5). ✓
- `_record_spawn_to_registry` gains an `engine="claude"` kwarg (Task 3) — used by both `spawn_session` (Task 3 Step 4 patch) and `spawn_session_codex` (Task 2). ✓
- `_pid_is_engine_process(pid, engine)` (Task 3) — called from `_reattach_spawned_orphans` with the per-row engine field (Task 3 Step 6). The shim `_pid_is_claude_process` keeps the old call sites working. ✓
- `insertPendingSpawnCard(pid, subject, sourceOrEngine)` (Task 8 Step 1) — accepts both legacy boolean and new explicit string; existing call sites unchanged in semantics, new dispatcher (Task 8 Steps 2-3) passes explicit strings. ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-30-codex-spawn.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
