# Codex Steer-to-Send Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve Codex input by routing definitive pre-delivery Steer rejections through the existing ordered Send/queue path.

**Architecture:** Keep fallback policy in the server's `_inject_text_into_session` router so every CCC client gets identical behavior. Retry only `codex_no_active_turn` and `codex_steer_unavailable`; return `codex_steer_failed` unchanged because `turn/steer` may already have been attempted.

**Tech Stack:** Python 3.12 standard library, `unittest`, existing CCC Codex app-server and pending-input queue helpers.

## Global Constraints

- Preserve per-conversation FIFO ordering through `resume_session_codex`.
- Never retry after `codex_steer_failed`.
- Do not add runtime dependencies.
- Stage only this task's hunks in shared dirty files.

---

### Task 1: Route definitive Steer rejection through Send

**Files:**
- Modify: `server.py` in `_inject_text_into_session`
- Test: `tests/test_smoke.py` in `TestRepoContextHelpers`
- Create: `changelog.d/fixed-codex-steer-send-fallback-2026-07-15.md`

**Interfaces:**
- Consumes: `resume_session_codex(session_id, text, *, steer=False)` and its existing result dictionaries.
- Produces: `_inject_text_into_session(...)` returns the fallback Send result for codes `codex_no_active_turn` and `codex_steer_unavailable`.

- [ ] **Step 1: Write failing regression tests**

```python
def test_codex_steer_unavailable_falls_back_to_send(self):
    sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
    with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
         mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
         mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
         mock.patch.object(self.server, "session_live_status", return_value={"live": False}), \
         mock.patch.object(
             self.server,
             "resume_session_codex",
             side_effect=[
                 {"ok": False, "code": "codex_steer_unavailable"},
                 {"ok": True, "via": "codex-app-turn"},
             ],
         ) as resume:
        result = self.server._inject_text_into_session(sid, "continue", mode="steer")

    self.assertTrue(result["ok"])
    self.assertEqual(result["via"], "codex-app-turn")
    self.assertEqual(
        resume.call_args_list,
        [mock.call(sid, "continue", steer=True), mock.call(sid, "continue")],
    )

def test_codex_steer_failed_does_not_retry_as_send(self):
    sid = "019e2bbb-d5e0-7df2-a1f7-26fbcf363484"
    with mock.patch.object(self.server, "_is_codex_session", return_value=True), \
         mock.patch.object(self.server, "_is_gemini_session", return_value=False), \
         mock.patch.object(self.server, "find_session_cwd", return_value=str(self.repo)), \
         mock.patch.object(self.server, "session_live_status", return_value={"live": False}), \
         mock.patch.object(
             self.server,
             "resume_session_codex",
             return_value={"ok": False, "code": "codex_steer_failed"},
         ) as resume:
        result = self.server._inject_text_into_session(sid, "continue", mode="steer")

    self.assertFalse(result["ok"])
    resume.assert_called_once_with(sid, "continue", steer=True)
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
/opt/homebrew/bin/python3.12 -m unittest \
  tests.test_smoke.TestRepoContextHelpers.test_codex_steer_unavailable_falls_back_to_send \
  tests.test_smoke.TestRepoContextHelpers.test_codex_steer_failed_does_not_retry_as_send
```

Expected: the unavailable test fails because only the Steer call occurs; the ambiguous-failure test passes.

- [ ] **Step 3: Implement the minimal server fallback**

Replace the direct Steer return with:

```python
if is_codex and mode == "steer":
    steer_result = resume_session_codex(session_id, text, steer=True)
    if steer_result.get("code") in (
        "codex_no_active_turn",
        "codex_steer_unavailable",
    ):
        return resume_session_codex(session_id, text)
    return steer_result
```

- [ ] **Step 4: Verify GREEN and related Codex behavior**

Run:

```bash
/opt/homebrew/bin/python3.12 -m unittest \
  tests.test_smoke.TestRepoContextHelpers.test_codex_steer_mode_routes_to_resume_steer \
  tests.test_smoke.TestRepoContextHelpers.test_codex_steer_unavailable_falls_back_to_send \
  tests.test_smoke.TestRepoContextHelpers.test_codex_steer_failed_does_not_retry_as_send \
  tests.test_smoke.TestPendingInputs
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 5: Add changelog and commit only owned hunks**

Create the changelog snippet with this line:

```markdown
Codex messages now fall back from a stale or unavailable Steer action to the normal ordered Send queue instead of failing with a misleading external-process error.
```

Stage only the new server/test hunks and the changelog path, then commit:

```bash
git commit -m "fix(codex): fall back from unavailable Steer"
```
