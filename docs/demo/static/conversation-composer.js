/* Shared composer submission with explicit live-state and service adapters. */
(function (root) {
  'use strict';
  function createComposer({ state, helpers }) {
    const { setActivePaneById, liveStatusMatchesOpenConv, activePaneId, guardComposerSend, announcedFromForPane, hideSlashCommandMenu, rememberComposerCommand, stopTextToSpeech, spawnFromBacklogIssue, replyToIssueFromInputBar, closeIssueFromInputBar, currentConversationRow, applyOptimisticConversationGoalAction, renderConversationGoalStrip, showOpToast, _autosizeTextareaLike, clearInputDraftForConversation, spawnFromInlineInput, devinSteerCapableNow, hasPendingSendEchoBeforeCompact, getConvViewForPane, _compactRunBlocksResend, isCompactionCapableSource, beginCompactRun, appendPendingSendEcho, announcedInjectionPreview, markSessionSending, updateInputBar, composerUserEditRevision, looksDormantNoProcess, getConvView, startCodexWakeBreakdown, durableActionId, isClaudeSource, markPendingSendQueued, tapbackTakePending, fetchInjectWithRetry, tapbackRestorePending, _showTerminalSuspectedDialog, removePendingSendEcho, bindPendingSendDeliveryId, clearAcceptedComposerInput, renderConvWakeOutcome, clearOptimisticAgentIndicator, refreshConversationList, syncQueuedSteerTray, markPendingSendDelivered, refreshLiveStatus, scheduleFireAndWatchRefresh, applyClearSuccession, beginWtReceiptTracking, compactResponseIsComplete, adoptCompactRunResult, completeCompactRun, clearSessionSending, markCompactRunWorking, scheduleCompactUsageRefresh, handleCompactTimeout, formatInjectFailure, _isCompactTimeoutReason, restoreInputAfterSendFailure, failCompactRun, isCursorUsageLimitFailure, appendSendFailureNotice, renderInlineWakeError, isTouchPrimary } = helpers;
  async function sendToTerminal(paneId, mode = 'send') {
    if (paneId) {
      setActivePaneById(paneId);
    }
    let injectMode = mode === 'steer' ? 'steer' : (mode === 'answer' ? 'answer' : (mode === 'send_queue' ? 'send_queue' : 'send'));
    if ((injectMode === 'send' || injectMode === 'send_queue')
        && liveStatusMatchesOpenConv()
        && state.liveStatus
        && state.liveStatus.questionWaiting
        && state.liveStatus.tty
        && state.liveStatus.terminalPresent) {
      injectMode = 'answer';
    }
    // Look up the input and send-button scoped to the target pane.
    // The static-HTML p1 element retains the global ids; cloned panes
    // (built by buildPaneElement) had their ids stripped, so we have
    // to query by class/tag scoped to the pane element.
    const _paneEl = document.querySelector(`.conv-pane[data-pane-id="${paneId || activePaneId()}"]`);
    const $input = (_paneEl && _paneEl.querySelector('.conv-input-bar textarea, .conv-input-bar input[type="text"]')) || state.$convInput;
    const $sendBtn = (_paneEl && _paneEl.querySelector('.send-btn')) || state.$convSendBtn;
    const $steerBtn = (_paneEl && _paneEl.querySelector('.steer-btn')) || state.$convSteerBtn;
    const $actionBtn = injectMode === 'steer' ? ($steerBtn || $sendBtn) : $sendBtn;
    // Whether the composer held focus when Send was tapped — on touch devices
    // that equals "keyboard is up". If it was already dismissed, don't raise
    // it again at the end of the send.
    const _composerHadFocus = !!($input && document.activeElement === $input);
    if (!guardComposerSend($input)) return;
    const text = ($input && $input.value || '').trim();
    let announcedFrom = announcedFromForPane(paneId || activePaneId());
    const draftConversation = state.currentConversation;
    hideSlashCommandMenu();
    // Backlog GH issue: dispatch based on the action selector.
    if ((state.currentConversation || '').startsWith('backlog-issue-') ||
        state.conversationsData.some(x => x.id === state.currentConversation && x.source === 'backlog')) {
      const $actionSel = document.getElementById('convInputIssueAction');
      const action = $actionSel ? $actionSel.value : 'fix';
      if (action === 'spawn') {
        if (!text) return;
        rememberComposerCommand(text);
        if (state._ttsActive) await stopTextToSpeech();
        await spawnFromBacklogIssue(text);
      } else if (action === 'needs_attention' || action === 'close') {
        if (!text) return;
        rememberComposerCommand(text);
        if (state._ttsActive) await stopTextToSpeech();
        await replyToIssueFromInputBar(action, text);
      } else {
        if (text) rememberComposerCommand(text);
        if (state._ttsActive) await stopTextToSpeech();
        await closeIssueFromInputBar(action, text);
      }
      return;
    }
    if (!text) {
      // CCC-946: clicking Send/Send-queue/Steer with an empty composer used
      // to no-op with zero feedback, reading as "this button doesn't do
      // anything". Focus the input so it's obvious what's missing.
      if ($input) $input.focus();
      return;
    }
    rememberComposerCommand(text);
    // CCC-762: typing `/goal clear` directly (rather than clicking the goal
    // strip's Clear button) skipped applyOptimisticConversationGoalAction
    // entirely, so the strip only updated once the server re-parsed the
    // transcript -- which a slow archive rescan can lag by well over a
    // minute, and a refresh landing before then re-shows the stale
    // objective. Apply the same optimistic clear + anti-reversion guard here.
    if (/^\/goal\s+(clear|cancel|reset|remove)\s*$/i.test(text)) {
      const _goalRow = currentConversationRow();
      if (_goalRow) {
        applyOptimisticConversationGoalAction(_goalRow, 'clear');
        renderConversationGoalStrip(paneId || activePaneId(), _goalRow);
      }
    }
    // Join link (CCC-98): `ccc:join-gc:<chat id or path>` pasted into any
    // session's composer joins THAT session to the group chat instead of
    // being sent to the agent as text.
    const _joinMatch = text.match(/^ccc:join-gc:(\S+)$/);
    if (_joinMatch && state.currentSession.id) {
      const ref = _joinMatch[1];
      const body = { session_id: state.currentSession.id };
      if (ref.indexOf('/') !== -1 || ref.indexOf('~') === 0) body.chat_path = ref;
      else body.chat_id = ref;
      try {
        const jr = await fetch('/api/group-chat/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const jd = await jr.json().catch(() => ({}));
        if (jr.ok && jd.ok) {
          showOpToast('Session joined the group chat', 'success');
          $input.value = '';
          _autosizeTextareaLike($input, $input.closest('.conv-input-bar'));
          clearInputDraftForConversation(draftConversation);
        } else {
          showOpToast('Join failed: ' + ((jd && jd.error) || ('HTTP ' + jr.status)), 'error');
        }
      } catch (err) {
        showOpToast('Join failed: ' + ((err && err.message) || 'network'), 'error');
      }
      return;
    }
    // New-session mode: input doubles as the prompt for a fresh spawn.
    if (state.currentConversation === '__new__') {
      if (state._ttsActive) await stopTextToSpeech();
      await spawnFromInlineInput(text);
      return;
    }
    const sid = state.currentSession.id;
    if (!sid) return;
    if (injectMode === 'steer'
        && state.currentSession.source !== 'codex'
        && state.currentSession.source !== 'kimi'
        && !devinSteerCapableNow()
        && !(state.liveStatus.live && state.liveStatus.headlessPresent && !state.liveStatus.tty)) {
      // Claude headless steers via the FIFO interrupt control request; Devin
      // CLI steers over its live `devin acp` connection; every other
      // non-Codex/Kimi surface still has no interrupt channel.
      showOpToast('Steer needs a live headless, Codex, Kimi, or steer-ready Devin session.', 'error');
      return;
    }
    const compactCommand = /^\/compact(?:\s|$)/i.test(text);
    const clearCommand = /^\/clear(?:\s|$)/i.test(text);
    if (/^\//.test(text)) announcedFrom = '';
    if (compactCommand && hasPendingSendEchoBeforeCompact(getConvViewForPane(paneId || activePaneId()))) {
      showOpToast('Wait for the pending message to land in the transcript before compacting.', 'error');
      return;
    }
    if (compactCommand && _compactRunBlocksResend(sid)) {
      // A compact already in flight for this session — sending another
      // /compact here doesn't start a fresh one, it gets queued server-side
      // and fires the moment the first compact finishes (CCC-982: read as
      // "will this drive ANOTHER compact right after?"). Block it instead.
      showOpToast('Already compacting this session - wait for it to finish.', 'error');
      return;
    }
    // The F2 cold gate is purely informational — the verdict line states the
    // reload price and the pill offers the cheap continuation, but Enter and
    // Send always submit as usual.
    if ($actionBtn) $actionBtn.disabled = true;
    const flashRed = () => {
      $input.style.borderColor = 'var(--red)';
      setTimeout(() => { $input.style.borderColor = ''; }, 1500);
    };
    // Paint the optimistic echo + clear the input FIRST, synchronously, so the
    // message appears the instant Enter is pressed. Anything async (stopping
    // TTS, the inject-input fetch) happens after — previously an in-flight TTS
    // teardown was awaited before this line, so sends during playback felt laggy
    // ("sometimes ok" = only when TTS was off).
    // `/compact` gets the lifecycle card instead of a normal send echo. The
    // echo was actively harmful here: `/compact` is consumed by the engine and
    // never lands in the JSONL as durable user text, so it never deduped, and
    // the "keep unacknowledged echoes pinned above the composer" pass
    // re-anchored it to the tail on every render — which is why the resume
    // summary appeared ABOVE the command that produced it.
    const compactRunStarted = compactCommand && isCompactionCapableSource(state.currentSession.source);
    if (compactRunStarted) beginCompactRun(sid, state.currentSession.source);
    const pendingSend = compactRunStarted
      ? null
      : appendPendingSendEcho(announcedInjectionPreview(text, announcedFrom), sid, paneId || activePaneId());
    if (compactRunStarted && sid) markSessionSending(sid);
    updateInputBar();
    const submittedRevision = composerUserEditRevision($input);
    $input.value = '';
    _autosizeTextareaLike($input, $input.closest('.conv-input-bar'));
    clearInputDraftForConversation(draftConversation);
    // A dormant send receives its outcome from the server. Codex additionally
    // starts compact checkmark progress, while queued and error states remain
    // explicit inline feedback.
    if (!compactCommand && !clearCommand && looksDormantNoProcess()) {
      const $wv = getConvViewForPane(paneId || activePaneId()) || getConvView();
      if (state.currentSession.source === 'codex') {
        startCodexWakeBreakdown($wv, sid);
      }
    }
    if (state._ttsActive) await stopTextToSpeech();
    let sendAccepted = false;
    try {
      let res;
      let payload;
      let _tapbackPrefix = '';
      if (state.currentSession.source === 'pkood') {
        const agentId = state.currentConversation.replace(/^pkood-/, '');
        res = await fetch('/api/pkood/inject', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ agent_id: agentId, message: text }),
        });
      } else if (compactCommand && isCompactionCapableSource(state.currentSession.source)) {
        // Both Claude AND Codex compact through /api/session/compact — the
        // backend routes a codex session to thread/compact via its app-server
        // (returns via:"codex-compact"). Sending "/compact" as literal text via
        // /api/inject-input does NOT compact, so it must not fall through here.
        res = await fetch('/api/session/compact', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            session_id: sid,
            terminal_app: state.liveStatus && state.liveStatus.terminalApp,
            idempotency_key: durableActionId('compact'),
          }),
        });
      } else if (clearCommand && isClaudeSource(state.currentSession.source) && !(state.liveStatus && state.liveStatus.terminalPresent)) {
        // /clear DOES run over headless stream-json stdin (verified 2026-08-05,
        // claude 2.1.222) — the old assumption it "is just literal text and
        // never runs" was stale, the same false assumption /compact had until
        // it was corrected 2026-06-28. Always try the in-place path first —
        // clear_session_context on the backend is the authoritative check for
        // whether a live CCC-owned headless spawn exists (checked live, empirically:
        // liveStatus.headlessPresent tracks mid-turn steerability, not plain
        // existence, so it under-reports an idle-but-live headless and can't
        // be trusted to gate this client-side). Falls back to opening a
        // terminal only when the backend confirms there's truly no live spawn
        // to talk to. Any trailing text after /clear becomes the fresh
        // session's first turn.
        const clearRest = /^\/clear\s+([\s\S]+)$/i.exec(text);
        res = await fetch('/api/session/clear', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            session_id: sid,
            terminal_app: state.liveStatus && state.liveStatus.terminalApp,
            initial_message: clearRest ? clearRest[1].trim() : '',
            idempotency_key: durableActionId('clear'),
          }),
        });
        const preClearData = await res.clone().json().catch(() => ({}));
        if (!preClearData.ok && (preClearData.code === 'clear_no_headless_spawn' || preClearData.code === 'clear_unsupported_engine')) {
          // No live spawn to clear in-place — fall back to a terminal running
          // /clear the way it always has (CCC-44 fallback for this case only).
          try {
            const r = await fetch('/api/launch-terminal', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({
                session_id: sid,
                cwd: state.currentSession.cwd || '',
                terminal_app: (state.liveStatus && state.liveStatus.terminalApp) || null,
                post_slash_commands: ['/clear'],
              }),
            });
            const d = await r.json().catch(() => ({}));
            if (r.ok && d.ok) {
              markPendingSendQueued(pendingSend, 'Opened a terminal to run /clear (no live headless to clear in place).');
              showOpToast('Opened a terminal - /clear runs there.', 'info');
            } else {
              markPendingSendQueued(pendingSend, '/clear could not be routed to a terminal.');
              showOpToast('Could not open a terminal for /clear: ' + (d.error || ('HTTP ' + r.status)), 'error');
            }
          } catch (e) {
            showOpToast('Could not open a terminal for /clear: ' + (e && e.message ? e.message : e), 'error');
          }
          if ($actionBtn) $actionBtn.disabled = false;
          return;
        }
      } else {
        _tapbackPrefix = tapbackTakePending(sid);
        payload = {
          session_id: sid,
          text: _tapbackPrefix ? _tapbackPrefix + '\n' + text : text,
          source: 'composer', composer_form: 'ccc-composer-v1',
          // send_queue isn't a real backend mode - it's plain "send" plus an
          // explicit opt-in to always defer to the next turn boundary
          // (force_queue), even on a tty session where regular "send" lets
          // the Claude TUI accept input mid-turn on its own.
          mode: injectMode === 'send_queue' ? 'send' : injectMode,
          idempotency_key: durableActionId('inject'),
        };
        if (injectMode === 'send_queue') payload.force_queue = true;
        if (injectMode === 'send'
            && _paneEl
            && String(_paneEl.dataset.presentationMode || '').toLowerCase() === '3') {
          payload.presentation_mode3 = true;
        }
        if (announcedFrom) payload.announced_from = announcedFrom;
        res = await fetchInjectWithRetry('/api/inject-input', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload),
        });
      }
      let data = {};
      try { data = await res.json(); } catch (_) {}
      if (typeof _tapbackPrefix !== 'undefined' && _tapbackPrefix && !(res && res.ok)) tapbackRestorePending(sid, _tapbackPrefix);
      // Soft-block: CCC suspects a terminal is running on this session but
      // couldn't route to it. Ask the user before delivering to the headless.
      if (res.ok && data.soft_block && data.code === 'terminal_suspected') {
        const choice = await _showTerminalSuspectedDialog(data.message || 'A terminal may be running on this session. Send there instead?');
        if (choice === 'terminal') {
          // Retry with force_terminal — route to the terminal via osascript.
          payload.force_terminal = true;
          try {
            res = await fetchInjectWithRetry('/api/inject-input', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(payload),
            });
            data = await res.json();
          } catch (e) {
            removePendingSendEcho(pendingSend);
            showOpToast('Failed to send to terminal: ' + (e && e.message ? e.message : e), 'error');
            return;
          }
        } else if (choice === 'headless') {
          // Retry with force_headless — skip the soft-block and deliver.
          payload.force_headless = true;
          try {
            res = await fetchInjectWithRetry('/api/inject-input', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(payload),
            });
            data = await res.json();
          } catch (e) {
            removePendingSendEcho(pendingSend);
            showOpToast('Failed to send: ' + (e && e.message ? e.message : e), 'error');
            return;
          }
        } else {
          // User dismissed the dialog — remove the pending echo and abort.
          removePendingSendEcho(pendingSend);
          return;
        }
      }
      sendAccepted = !!(res.ok && data.ok && !data.soft_block && data.submitted !== false);
      if (sendAccepted && data.delivery_id) bindPendingSendDeliveryId(pendingSend, data.delivery_id);
      if (sendAccepted) clearAcceptedComposerInput($input, text, draftConversation, submittedRevision);
      if (res.ok && data.ok && data.submitted === false) {
        removePendingSendEcho(pendingSend);
        showOpToast(data.warning || 'Text typed into Terminal but was not submitted. Press Enter in that terminal tab.', 'error');
      } else if (res.ok && data.ok) {
        // Render the wake OUTCOME inline first (codex resuming / queued reason),
        // then let the existing per-transport branches add their toast/echo.
        renderConvWakeOutcome(getConvViewForPane(paneId || activePaneId()) || getConvView(), data);
        if (data.via === 'codex-goal-store') {
          // Dormant Codex /goal commands update the native goal store directly;
          // they deliberately do not write a user_message to the transcript.
          // Treat that durable store update as the echo's terminal state rather
          // than leaving a blue "Delivered — waiting" bubble forever.
          removePendingSendEcho(pendingSend);
          clearOptimisticAgentIndicator(getConvViewForPane(paneId || activePaneId()) || getConvView());
          const row = currentConversationRow();
          if (row) {
            if (data.action === 'set') {
              row.goal = text.replace(/^\/goal\s+/i, '').trim();
              row.goal_status = 'active';
            } else {
              applyOptimisticConversationGoalAction(row, data.action);
            }
            renderConversationGoalStrip(paneId || activePaneId(), row);
          }
          refreshConversationList();
          showOpToast('Goal updated.', 'success');
        } else if (data.via === 'codex-app-queued') {
          markPendingSendQueued(pendingSend, 'Queued for Codex - will send when the running turn is ready.');
          showOpToast('Queued for Codex.');
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3500);
        } else if (data.queued && data.cwd_missing) {
          // Server queued the message because the session's cwd is
          // gone (moved / renamed / deleted). Surface that the message
          // is preserved AND offer to locate the new directory so the
          // queue can drain.
          const missing = data.missing_path || '(unknown path)';
          markPendingSendQueued(pendingSend, 'Queued - session folder is missing. Restore or relocate it to deliver.');
          showOpToast('Queued - session folder is missing (' + missing + '). Restore it or relocate to resume.', 'info');
        } else if (data.queued) {
          // The session is busy (or headless mid-tool). Keep the echo visible
          // as "queued" — NOT "terminal idle" (a headless session has no
          // terminal) and NOT the not-acknowledged timeout — so the user sees
          // their message is parked and will be delivered, not lost. Prefer the
          // server's explicit reason (e.g. Antigravity can't take input
          // mid-turn) so "Queued" is never unexplained (CCC-42/43).
          const queuedMsg = data.queued_reason
            ? 'Queued - ' + data.queued_reason
            : (compactCommand
                ? 'Queued /compact - will run when the session finishes its current step.'
                : (clearCommand
                    ? 'Queued /clear - will run when the session finishes its current step.'
                    : 'Queued - will send when the session finishes its current step.'));
          markPendingSendQueued(pendingSend, queuedMsg);
          // Relocate the echo into the steer tray NOW rather than waiting for
          // the next transcript render. The message was painted into the
          // conversation optimistically the moment Enter was pressed (which is
          // what makes sending feel instant), so deferring the move meant it
          // sat there for a poll cycle and then visibly hopped back down into
          // the composer.
          try {
            const _qPane = paneId || activePaneId();
            syncQueuedSteerTray(getConvViewForPane(_qPane) || getConvView(), _qPane, false);
          } catch (_) {}
          showOpToast(queuedMsg);
        } else if (data.via === 'codex-steer') {
          markPendingSendDelivered(pendingSend, data);
          showOpToast('Steered running Codex turn.');
          try { refreshLiveStatus(); } catch (_) {}
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3500);
        } else if (data.via === 'codex-desktop' && data.confirmed) {
          removePendingSendEcho(pendingSend);
          clearOptimisticAgentIndicator(getConvViewForPane(paneId || activePaneId()) || getConvView());
          showOpToast('Sent to Codex.');
          refreshLiveStatus();
          setTimeout(refreshConversationList, 1500);
        } else if (data.via === 'codex-app-turn') {
          markPendingSendDelivered(pendingSend, data);
          showOpToast('Codex follow-up started.');
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3500);
        } else if (data.via === 'cursor-resume') {
          showOpToast('Cursor follow-up started.');
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3500);
        } else if (data.via === 'cursor-resume-queued') {
          showOpToast('Queued until the Cursor follow-up finishes.');
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3500);
        } else if (data.via === 'antigravity-resume') {
          showOpToast('Antigravity headless follow-up started.');
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3500);
        } else if (data.via === 'antigravity-app') {
          showOpToast('Sent to Antigravity app.');
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3500);
        } else if (data.via === 'hermes-resume') {
          markPendingSendDelivered(pendingSend, data);
          showOpToast('Hermes follow-up started.');
          scheduleFireAndWatchRefresh(paneId || activePaneId());
        } else if (data.via === 'opencode-resume') {
          markPendingSendDelivered(pendingSend, data);
          showOpToast('OpenCode follow-up started.');
          scheduleFireAndWatchRefresh(paneId || activePaneId());
        } else if (data.via === 'codex-continuation') {
          removePendingSendEcho(pendingSend);
          if (data.new_session_id) applyClearSuccession(sid, data.new_session_id, paneId || activePaneId());
          else refreshConversationList();
          showOpToast(data.message || 'Ephemeral Codex run - continued in a new session.', 'info');
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3500);
        } else if (data.via === 'wt-send' && !compactCommand) {
          // Routed through WatchTower (dormant session). Stage the echo copy
          // by transport and track the delivery receipt to landed/lost.
          beginWtReceiptTracking(pendingSend, data, {
            sid, rawText: text, announcedFrom,
            mode: injectMode || 'send',
            paneId: paneId || activePaneId(),
          });
          showOpToast('Accepted by WatchTower' + (data.transport ? ' - ' + data.transport + ' transport.' : '.'));
        } else if (compactCommand) {
          if ((state.currentSession.source === 'codex' || (data && data.via === 'live-spawn-stdin'))
              && compactResponseIsComplete(data)) {
            // Finished compacting server-side — nothing left to poll for, so
            // the card goes straight to its result instead of spinning.
            adoptCompactRunResult(sid, data);
            completeCompactRun(sid);
            clearOptimisticAgentIndicator(getConvViewForPane(paneId || activePaneId()) || getConvView());
            clearSessionSending(sid);
            setTimeout(refreshConversationList, 1500);
            setTimeout(refreshConversationList, 3500);
          } else {
            markCompactRunWorking(sid, data);
            scheduleCompactUsageRefresh(sid);
          }
        } else if (data.via === 'live-spawn-clear') {
          removePendingSendEcho(pendingSend);
          applyClearSuccession(sid, data.new_session_id, paneId || activePaneId());
          showOpToast('Cleared - the live session ran /clear itself.', 'success');
        } else {
          // Plain inject success — Claude TTY ('terminal-control') or headless
          // ('spawn-fifo'). The server confirmed delivery (ok:true) but the
          // message isn't in the JSONL yet. Previously this fell through and
          // did nothing, leaving the echo stuck in the ambiguous italic
          // "sending…" state until transcript dedup happened to match it (or
          // never did). Confirm it now: "delivered, awaiting Claude".
          markPendingSendDelivered(pendingSend, data);
        }
      } else if (compactCommand && data && data.code === 'compact_timeout') {
        handleCompactTimeout(sid);
      } else if (compactCommand) {
        // A real refusal from the engine (e.g. "Not enough messages to
        // compact."). Say so on the card, not only in a toast that fades.
        const _reason = (data && (data.compact_error || data.error))
          || formatInjectFailure(data, res.status) || 'unknown error';
        if (_isCompactTimeoutReason(_reason)) {
          // Not a refusal — nobody knows yet. Keep watching.
          handleCompactTimeout(sid);
        } else {
          removePendingSendEcho(pendingSend);
          restoreInputAfterSendFailure($input, text);
          flashRed();
          failCompactRun(sid, _reason);
        }
      } else {
        const reason = formatInjectFailure(data, res.status);
        if (isCursorUsageLimitFailure(data, reason)) {
          appendSendFailureNotice(pendingSend, 'Cursor usage limit hit. Cursor says: ' + reason, paneId || activePaneId());
        } else {
          removePendingSendEcho(pendingSend);
          // Headline fix: the previously-silent Codex resume failure becomes a
          // persistent, dismissible inline error in the pane (real reason + the
          // path that failed) — not just a toast.
          renderInlineWakeError(
            getConvViewForPane(paneId || activePaneId()) || getConvView(),
            '⚠ Resume failed: ' + reason
              + ([String(data && data.via || ''), String(data && data.stage || '')].filter(Boolean).join(' / ')
                  ? ' (' + [String(data && data.via || ''), String(data && data.stage || '')].filter(Boolean).join(' / ') + ')'
                  : ''),
          );
        }
        restoreInputAfterSendFailure($input, text);
        flashRed();
        const failurePrefix = compactCommand ? '/compact failed' : ((injectMode === 'steer' ? 'Steer' : (injectMode === 'answer' ? 'Answer' : 'Send')) + ' failed');
        showOpToast(failurePrefix + ': ' + reason, 'error');
      }
    } catch (err) {
      // Delivery and presentation are separate outcomes. A rendering error
      // after acceptance must never turn the sent message back into a draft.
      if (sendAccepted) {
        console.warn('Accepted message status could not be rendered', err);
        showOpToast('Message accepted. Reload to refresh its status.', 'info');
      } else {
        removePendingSendEcho(pendingSend);
        restoreInputAfterSendFailure($input, text);
        flashRed();
        if (compactRunStarted) {
          if (_isCompactTimeoutReason((err && (err.name + ' ' + err.message)) || '')) {
            // The request gave up; the engine may well still be compacting.
            handleCompactTimeout(sid);
          } else {
            failCompactRun(sid, (err && err.message) || 'network error');
          }
        } else {
          const failurePrefix = (injectMode === 'steer' ? 'Steer' : (injectMode === 'answer' ? 'Answer' : 'Send')) + ' failed';
          showOpToast(failurePrefix + ': ' + (err.message || 'network error'), 'error');
        }
      }
    }
    if ($actionBtn) $actionBtn.disabled = false;
    if (!isTouchPrimary() || _composerHadFocus) $input.focus();
  }
    return { sendToTerminal };
  }
  root.CCCConversationComposer = Object.freeze({ createComposer });
})(typeof window === 'undefined' ? globalThis : window);
