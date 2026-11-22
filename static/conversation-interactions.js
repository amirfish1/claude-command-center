/* Shared conversation interactions with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices }) {
  function withRepoPath(body, repoPath) {
    const concrete = repoPath || (0, sharedState.popoutRepoPath)();
    const out = Object.assign({}, body || {});
    if (concrete) out.repo_path = concrete;
    return out;
  }

  function f2ManualOpen(paneId) {
    try {
      const sid = (sharedState.currentSession && sharedState.currentSession.id) || '';
      if (!sid || sharedState.currentConversation === '__new__') return;
      sharedState.f2ManualPanes.set((0, sharedState.f2PaneKey)(paneId), sid);
      (0, sharedState.f2RenderComposer)(paneId, { force: true });
      const st = sharedState.f2PaneState.get((0, sharedState.f2PaneKey)(paneId));
      if (st) {
        st.configOpen = true;
        (0, sharedState.f2RenderComposer)(paneId, { force: true });
      }
    } catch (_) {}
  }

  function installConversationInteraction1() {
window.addEventListener('resize', () => {
    document.querySelectorAll('.f2c-panel').forEach(sharedState.f2FitSheet);
  });
  }

  function installConversationInteraction2() {
document.addEventListener('click', (ev) => {
    // CCC-763: "Continued in new session" link -- jump straight to the
    // child session instead of re-showing "Continue new" on the origin.
    const continuationLink = ev.target && ev.target.closest && ev.target.closest('[data-f2-open-continuation]');
    if (continuationLink) {
      ev.preventDefault();
      const targetSid = continuationLink.getAttribute('data-target-sid');
      const clPane = continuationLink.closest('.conv-pane');
      const clPaneId = (clPane && clPane.getAttribute('data-pane-id')) || null;
      if (targetSid && typeof sharedState.selectConversation === 'function') {
        try { (0, sharedState.selectConversation)(targetSid, clPaneId); } catch (_) {}
      }
      return;
    }
    // Manual "Continue in a new session" button — always in the toolbar,
    // independent of the token/idle gate (CCC-744).
    const continueBtn = ev.target && ev.target.closest && ev.target.closest('.continue-new-btn');
    if (continueBtn) {
      ev.preventDefault();
      const cbPane = continueBtn.closest('.conv-pane');
      const cbPaneId = (cbPane && cbPane.getAttribute('data-pane-id')) || null;
      if (cbPaneId && typeof sharedState.setActivePaneById === 'function') { try { (0, sharedState.setActivePaneById)(cbPaneId); } catch (_) {} }
      // Asking for it explicitly overrides an earlier "Continue here".
      sharedState.f2DismissedPanes.delete((0, sharedState.f2PaneKey)(cbPaneId));
      try { f2ManualOpen(cbPaneId); } catch (_) {}
      return;
    }
    // Launch chip (and the dialog's Done button): toggle the follow-up
    // config dialog. Open/closed lives in pane state so a re-rank can't
    // slam it shut mid-choice.
    const chip = ev.target && ev.target.closest && ev.target.closest('.f2c-panel [data-f2-chip]');
    if (chip) {
      const chipPanel = chip.closest('.f2c-panel');
      const chipPane = chipPanel && chipPanel.closest('.conv-pane');
      const chipPaneId = (chipPane && chipPane.getAttribute('data-pane-id')) || null;
      const chipSt = sharedState.f2PaneState.get((0, sharedState.f2PaneKey)(chipPaneId));
      if (chipSt) {
        ev.preventDefault();
        chipSt.configOpen = !chipSt.configOpen;
        try { (0, sharedState.f2RenderComposer)(chipPaneId, { force: true }); } catch (_) {}
        // Load the persisted usage history only when this chooser is opened.
        // The first render can use its local cache immediately; a resolved
        // fetch repaints the same open pane with the durable top picks.
        if (chipSt.configOpen && typeof sharedState.fetchModelPickerPicksFromServer === 'function') {
          (0, sharedState.fetchModelPickerPicksFromServer)().then(() => {
            if (chipSt.configOpen) (0, sharedState.f2RenderComposer)(chipPaneId, { force: true });
          }).catch(() => {});
        }
      }
      return;
    }
    const quickPick = ev.target && ev.target.closest && ev.target.closest('.f2c-panel [data-f2-launch-pick]');
    if (quickPick) {
      const quickPanel = quickPick.closest('.f2c-panel');
      const quickPane = quickPanel && quickPanel.closest('.conv-pane');
      const quickPaneId = (quickPane && quickPane.getAttribute('data-pane-id')) || null;
      const quickSt = sharedState.f2PaneState.get((0, sharedState.f2PaneKey)(quickPaneId));
      const engine = quickPick.getAttribute('data-engine') || '';
      const model = quickPick.getAttribute('data-model') || '';
      const effort = quickPick.getAttribute('data-effort') || '';
      if (quickSt && quickSt.launch && (0, sharedState.f2LaunchEngines)().some(e => e.id === engine)
          && (0, sharedState.f2ModelsForEngine)(engine).some(option => option.id === model)
          && (!effort || (0, sharedState.f2EffortsForEngine)(engine).some(option => option.id === effort))) {
        ev.preventDefault();
        quickSt.launch = { engine, model, effort };
        try { (0, sharedState.f2RenderComposer)(quickPaneId, { force: true }); } catch (_) {}
      }
      return;
    }
    const el = ev.target && ev.target.closest && ev.target.closest('.f2c-panel [data-f2-act]');
    if (!el) return;
    const panel = el.closest('.f2c-panel');
    const pane = panel && panel.closest('.conv-pane');
    const paneId = (pane && pane.getAttribute('data-pane-id')) || null;
    const st = sharedState.f2PaneState.get((0, sharedState.f2PaneKey)(paneId));
    if (!st) return;
    ev.preventDefault();
    // "Continue here" is a real choice, not a no-op close: it suppresses the
    // offer for this pane+sid so it cannot immediately repaint on the next
    // keystroke. Switching sessions in the pane clears it (keyed by sid).
    if (el.getAttribute('data-f2-act') === 'dismiss') {
      sharedState.f2ManualPanes.delete((0, sharedState.f2PaneKey)(paneId));
      st.configOpen = false;
      sharedState.f2DismissedPanes.set((0, sharedState.f2PaneKey)(paneId), st.sid);
      try { (0, sharedState.f2RenderComposer)(paneId, { force: true }); } catch (_) {}
      return;
    }
    try { (0, sharedState.f2RunContinue)(paneId, st, el); } catch (_) {}
  });
  }

  function installConversationInteraction3() {
document.addEventListener('input', (ev) => {
    const ta = ev.target && ev.target.closest && ev.target.closest('.f2c-panel [data-f2-prompt]');
    if (!ta) return;
    const pane = ta.closest('.conv-pane');
    const st = sharedState.f2PaneState.get((0, sharedState.f2PaneKey)((pane && pane.getAttribute('data-pane-id')) || null));
    if (!st) return;
    st.promptDraft = ta.value;
    st.promptEdited = true;
  });
  }

  function installConversationInteraction4() {
document.addEventListener('change', (ev) => {
    const sel = ev.target && ev.target.closest && ev.target.closest('.f2c-panel [data-f2-launch]');
    if (!sel) return;
    const panel = sel.closest('.f2c-panel');
    const pane = panel && panel.closest('.conv-pane');
    const paneId = (pane && pane.getAttribute('data-pane-id')) || null;
    const st = sharedState.f2PaneState.get((0, sharedState.f2PaneKey)(paneId));
    if (!st || !st.launch) return;
    const kind = sel.getAttribute('data-f2-launch');
    st.launch[kind] = sel.value;
    if (kind === 'engine') {
      // Engine changed — the old model id belongs to a different family and
      // would be rejected by the CLI. Snap to the new engine's first model
      // rather than leaving a stale label in the box.
      const models = (0, sharedState.f2ModelsForEngine)(sel.value);
      st.launch.model = models[0] ? models[0].id : '';
      // Same for the effort: the ladders differ in extent (Max exists for
      // Claude and Kimi, not Codex), so a held rung the new engine has never
      // heard of has to come down to one it has.
      const efforts = (0, sharedState.f2EffortsForEngine)(sel.value);
      if (!efforts.some(e => e.id === st.launch.effort)) {
        st.launch.effort = efforts[0] ? efforts[0].id : '';
      }
    }
    // Repaint on every change so the chip label tracks the choice; the open
    // dialog survives because configOpen lives in state, not the DOM.
    try { (0, sharedState.f2RenderComposer)(paneId, { force: true }); } catch (_) {}
  });
  }

  function installConversationInteraction5() {
window.f2OpenDemoCostMenu = function (opts) {
    opts = opts || {};
    try {
      const sid = opts.sid || (sharedState.currentSession && sharedState.currentSession.id) || '';
      if (sid) localStorage.setItem('ccc-f2-demo-force', sid);
      else localStorage.setItem('ccc-f2-demo-force', '*');
    } catch (_) {}
    try { (0, sharedState.f2RenderComposer)(opts.paneId || null, { force: true }); } catch (_) {}
  };
  }

  async function _pollInterruptAsks() {
    if (sharedState.READER_ONLY_POPOUT || document.hidden) return;
    try {
      const res = await fetch('/api/interrupt-asks', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      (0, sharedState._renderInterruptAsks)((data && data.asks) || []);
    } catch (_) {}
  }

  function installConversationInteraction6() {
setInterval(_pollInterruptAsks, 15000);
  }

  function installConversationInteraction7() {
setTimeout(_pollInterruptAsks, 2500);
  }

  function installConversationInteraction8() {
if (sharedState.$convReplayBtn) sharedState.$convReplayBtn.addEventListener('click', () => startConvReplay());
  }

  async function handOverToCodexDesktop() {
    if (!sharedState.$codexHandoverBtn || !sharedState.currentSession.id || sharedState.currentSession.source !== 'codex') return;
    if (sharedState.codexHandoverWait) {
      (0, sharedState.cancelCodexDesktopHandover)();
      return;
    }
    const request = {
      id: sharedState.currentSession.id, timer: null,
      context: { thread_id: sharedState.currentSession.id,
        repo_path: sharedState.currentSession.repoPath || sharedState.currentSession.cwd,
        cwd: sharedState.currentSession.cwd },
    };
    sharedState.codexHandoverWait = request;
    async function attempt() {
      if (sharedState.codexHandoverWait !== request || sharedState.currentSession.id !== request.id) return;
      sharedState.$codexHandoverBtn.disabled = true;
      sharedState.$codexHandoverBtn.textContent = 'Checking handover…';
      try {
        const response = await fetch('/api/codex/client/handover', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ context: request.context }),
        });
        const data = await response.json();
        if (sharedState.codexHandoverWait !== request || sharedState.currentSession.id !== request.id) return;
        if (!data.ok) throw new Error(data.error || 'Could not hand over this conversation.');
        if (data.pending) {
          sharedState.$codexHandoverBtn.textContent = 'Waiting to hand over · Cancel';
          sharedState.$codexHandoverBtn.title = (data.message || 'Waiting for Codex replies to finish.') + ' Click to cancel.';
          sharedState.$codexHandoverBtn.disabled = false;
          request.timer = setTimeout(attempt, 3000);
          return;
        }
        sharedState.codexHandoverWait = null;
        sharedState.$codexHandoverBtn.textContent = 'Opened in Codex Desktop';
        sharedState.$codexHandoverBtn.title = 'Continue this conversation in Codex Desktop.';
        sharedState.$codexHandoverBtn.disabled = false;
        (0, sharedState.showOpToast)('Conversation handed over to Codex Desktop.', 'success');
      } catch (error) {
        if (sharedState.codexHandoverWait !== request) return;
        (0, sharedState.cancelCodexDesktopHandover)();
        (0, sharedState.showOpToast)(error.message || 'Could not hand over this conversation.', 'error');
      }
    }
    await attempt();
  }

  function installConversationInteraction9() {
if (sharedState.$codexHandoverBtn) sharedState.$codexHandoverBtn.addEventListener('click', handOverToCodexDesktop);
  }

  function installConversationInteraction10() {
if (sharedState.$announceBtnConv) {
    sharedState.$announceBtnConv.addEventListener('click', async (e) => {
      e.stopPropagation();
      const sid = sharedState.$announceBtnConv.dataset.sessionId;
      if (!sid) return;
      const feature = window.prompt('Announce what? (short feature name)\n\nLeave blank and /announce-feature will ask.', '');
      if (feature === null) return;
      sharedState.$announceBtnConv.disabled = true;
      sharedState.$announceBtnConv.textContent = 'Sending...';
      const cmd = feature.trim() ? '/announce-feature ' + feature.trim() : '/announce-feature';
      try {
        const res = await fetch('/api/inject-input', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ session_id: sid, text: cmd }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) {
          throw new Error(data.error || data.message || ('HTTP ' + res.status));
        }
        sharedState.$announceBtnConv.textContent = 'Sent ✓';
        sharedState.$announceBtnConv.style.opacity = '0.6';
      } catch (err) {
        const reason = (err && err.message) || 'unknown error';
        sharedState.$announceBtnConv.textContent = 'Failed';
        (0, sharedState.showOpToast)('Close & announce failed: ' + reason, 'error');
        setTimeout(() => {
          sharedState.$announceBtnConv.textContent = 'Close & announce';
          sharedState.$announceBtnConv.disabled = false;
          sharedState.$announceBtnConv.style.opacity = '';
        }, 2000);
      }
    });
  }
  }

  function _renderConvOverflowMenu() {
    if (!sharedState.$convOverflowMenu) return;
    // Gate to the open conversation so the menu and its "(current)" repo marker
    // reflect what you're viewing, not a stale previously-open session.
    const sess = (0, sharedState.activeConvSession)();
    const sid = sess && sess.id;
    const isPkood = sess && sess.source === 'pkood';
    const isCodex = sess && sess.source === 'codex';
    const isGemini = sess && sess.source === 'gemini';
    const isCursor = sess && sess.source === 'cursor';
    const isAntigravity = sess && sess.source === 'antigravity';
    const repos = (typeof sharedState.repoListState !== 'undefined' && sharedState.repoListState.repos) || [];
    const activeRepo = (0, sharedState.activeConvRepoPath)();
    let html = '';
    html += '<div class="com-section-label">Move to repo</div>';
    if (!sid) {
      html += '<div class="com-item com-current">No session selected</div>';
    } else if (!repos.length) {
      html += '<div class="com-item com-current">No known repos. Add one in the Repo picker.</div>';
    } else {
      for (const r of repos) {
        const isCurrent = r.path === activeRepo;
        const cls = 'com-item' + (isCurrent ? ' com-current' : '');
        const label = (0, sharedState.escapeHtml)(r.label || r.path);
        const path = (0, sharedState.escapeHtml)(r.path);
        const suffix = isCurrent ? ' <span class="com-repo-path">(current)</span>' : ' <span class="com-repo-path">' + path + '</span>';
        html += '<button type="button" class="' + cls + '" data-target="' + path + '"'
          + (isCurrent ? ' disabled' : '')
          + '>' + label + suffix + '</button>';
      }
    }
    // "Continue on…" — hand this session off to a paired federation node.
    // Shown for any real session; non-Claude engines surface the backend's
    // unsupported_capability error inside the modal rather than being hidden.
    if (sid) {
      html += '<div class="com-divider"></div>';
      html += '<button type="button" class="com-item" data-handoff-continue>'
        + '<span>Continue on…</span></button>';
    }
    sharedState.$convOverflowMenu.innerHTML = html;
    const $handoffContinueBtn = sharedState.$convOverflowMenu.querySelector('[data-handoff-continue]');
    if ($handoffContinueBtn) {
      $handoffContinueBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        (0, sharedState._closeConvOverflow)();
        const sidNow = sid || (sharedState.currentSession && sharedState.currentSession.id);
        if (!sidNow) return;
        const row = (0, sharedState.openConvRow)();
        const name = (row && typeof sharedState.paneTitleForRow === 'function') ? (0, sharedState.paneTitleForRow)(row) : '';
        if (typeof openHandoffModal === 'function') openHandoffModal(sidNow, name || '');
      });
    }
    sharedState.$convOverflowMenu.querySelectorAll('button[data-target]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const target = btn.dataset.target;
        const sidNow = sharedState.currentSession && sharedState.currentSession.id;
        if (!sidNow || !target) return;

        const isNonClaude = isPkood || isCodex || isGemini || isCursor || isAntigravity;
        const confirmMsg = isNonClaude
          ? 'Pin session to ' + target + '?\n\nThe session will visually display under the new repo.'
          : 'Move session to ' + target + '?\n\nThe JSONL file gets relocated; resume will then run in the new repo.';

        if (!confirm(confirmMsg)) return;
        btn.disabled = true;
        btn.textContent = isNonClaude ? 'Pinning…' : 'Moving…';
        try {
          if (isNonClaude) {
            const res = await fetch('/api/repo/pin', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ session_id: sidNow, path: target }),
            });
            const data = await res.json();
            if (data.ok) {
              (0, sharedState._closeConvOverflow)();
              if (typeof hostServices.refreshConversationList === 'function') (0, hostServices.refreshConversationList)();
              else if (typeof hostServices.loadConversationList === 'function') (0, hostServices.loadConversationList)();
            } else {
              btn.textContent = 'Failed: ' + (data.error || 'unknown');
            }
          } else {
            const res = await fetch('/api/sessions/' + encodeURIComponent(sidNow) + '/move', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ repo_path: target }),
            });
            const data = await res.json();
            if (data.ok) {
              (0, sharedState._closeConvOverflow)();
              if (typeof hostServices.refreshConversationList === 'function') (0, hostServices.refreshConversationList)();
              else if (typeof hostServices.loadConversationList === 'function') (0, hostServices.loadConversationList)();
            } else {
              btn.textContent = 'Failed: ' + (data.error || 'unknown');
            }
          }
        } catch (err) {
          btn.textContent = 'Failed: ' + err.message;
        }
      });
    });
  }

  function installConversationInteraction11() {
if (sharedState.$convOverflowBtn) {
    sharedState.$convOverflowBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      if (!(0, sharedState._convOverflowHasActions)()) {
        (0, sharedState.updateConvOverflowButton)();
        return;
      }
      const isOpen = sharedState.$convOverflowMenu.classList.contains('open');
      if (isOpen) {
        (0, sharedState._closeConvOverflow)();
      } else {
        _renderConvOverflowMenu();
        sharedState.$convOverflowMenu.classList.add('open');
        sharedState.$convOverflowMenu.setAttribute('aria-hidden', 'false');
        sharedState.$convOverflowBtn.setAttribute('aria-expanded', 'true');

        // Escape the status-rail overflow-y container using fixed coords
        const rect = sharedState.$convOverflowBtn.getBoundingClientRect();
        sharedState.$convOverflowMenu.style.position = 'fixed';
        sharedState.$convOverflowMenu.style.top = (rect.bottom + 4) + 'px';
        sharedState.$convOverflowMenu.style.right = (window.innerWidth - rect.right) + 'px';
        sharedState.$convOverflowMenu.style.maxHeight = `calc(100vh - ${rect.bottom + 10}px)`;
      }
    });
    document.addEventListener('click', (e) => {
      if (!sharedState.$convOverflowMenu) return;
      if (!sharedState.$convOverflowMenu.classList.contains('open')) return;
      if (e.target.closest('.conv-overflow-wrap')) return;
      (0, sharedState._closeConvOverflow)();
    });
    document.addEventListener('keydown', (e) => {
      if ((0, sharedState.isImeKey)(e)) return;
      if (e.key === 'Escape') (0, sharedState._closeConvOverflow)();
    });
  }
  }

  function installConversationInteraction12() {
try {
    window.__cccRelayState = function () {
      return {
        sessionId: sharedState._relayedQuestionState.sessionId,
        nonce: sharedState._relayedQuestionState.nonce,
        fetching: sharedState._relayedQuestionState.fetching,
        fetchStartedAt: sharedState._relayedQuestionState.fetchStartedAt || null,
        lastAnswered: sharedState._lastAnsweredQuestionNonce,
        cardEl: !!(0, sharedState._relayedQuestionInlineEl)(),
      };
    };
  } catch (_) {}
  }

  function installConversationInteraction13() {
setInterval(() => {
    const els = document.querySelectorAll('[data-working-since]');
    if (!els.length) return;
    const nowMs = Date.now();
    els.forEach((el) => {
      const since = Number(el.getAttribute('data-working-since') || 0);
      if (since > 0) el.textContent = (0, sharedState._optimisticAgeLabel)(nowMs - since * 1000);
    });
  }, 1000);
  }

  function focusLiveQuestionComposer(paneId) {
    const input = (0, sharedState.composerInputForPane)(paneId || (0, sharedState.activePaneId)()) || sharedState.$convInput;
    if (!input) return false;
    input.focus();
    (0, sharedState.moveInputCaretToEnd)(input);
    return true;
  }

  async function submitLiveQuestionAnswer(answer, paneId) {
    const text = (0, sharedState.cleanLiveActivityDetail)(answer);
    if (!text) return false;
    const input = (0, sharedState.composerInputForPane)(paneId || (0, sharedState.activePaneId)()) || sharedState.$convInput;
    if (!input) return false;
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await (0, sharedState.sendToTerminal)(paneId || (0, sharedState.activePaneId)(), 'answer');
    return true;
  }

  async function handleLiveQuestionActionClick(ev) {
    const btn = ev.target && ev.target.closest
      ? ev.target.closest('[data-live-question-action]')
      : null;
    if (!btn) return;
    ev.preventDefault();
    const pane = btn.closest && btn.closest('.conv-pane[data-pane-id]');
    const paneId = (pane && pane.dataset && pane.dataset.paneId) || (0, sharedState.activePaneId)();
    if (paneId) (0, sharedState.setActivePaneById)(paneId);
    const action = btn.getAttribute('data-live-question-action') || 'answer';
    if (action === 'type') {
      focusLiveQuestionComposer(paneId);
      return;
    }
    const answer = btn.getAttribute('data-live-question-answer') || btn.textContent || '';
    btn.disabled = true;
    btn.classList.add('is-submitting');
    try {
      await submitLiveQuestionAnswer(answer, paneId);
    } finally {
      btn.disabled = false;
      btn.classList.remove('is-submitting');
    }
  }

  function installConversationInteraction14() {
document.addEventListener('click', handleLiveQuestionActionClick);
  }

  function installConversationInteraction15() {
document.addEventListener('click', async (ev) => {
    const pick = ev.target && ev.target.closest && ev.target.closest('.ask-user-option-pick');
    if (!pick) return;
    ev.preventDefault();
    const sid = sharedState.currentSession && sharedState.currentSession.id;
    if (!sid) return;
    const view = pick.closest('.conversations-view');
    const thisBlock = pick.closest('.tool-call.ask-user-question');
    const allAsk = view ? view.querySelectorAll('.tool-call.ask-user-question') : [];
    const lastAsk = allAsk.length ? allAsk[allAsk.length - 1] : null;
    if (!sharedState.liveStatus || !sharedState.liveStatus.questionWaiting || !lastAsk || thisBlock !== lastAsk) {
      (0, sharedState.showOpToast)('This question is no longer waiting for an answer.');
      return;
    }
    if (pick.classList.contains('is-submitting')) return;
    const oi = parseInt(pick.getAttribute('data-ask-opt') || '0', 10);
    pick.classList.add('is-submitting');
    try {
      const res = await fetch('/api/answer-question', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, answers: [{ index: oi, text: '' }] }),
      });
      const data = await res.json().catch(() => ({}));
      if (data && data.ok) {
        (0, sharedState.showOpToast)('Answer sent.');
        try { (0, sharedState.closeRelayedQuestionInline)(); } catch (_) {}
      } else {
        (0, sharedState.showOpToast)('Could not send answer: ' + ((data && data.error) || 'unknown'), 'error');
      }
    } catch (e) {
      (0, sharedState.showOpToast)('Could not send answer: ' + ((e && e.message) || e), 'error');
    } finally {
      pick.classList.remove('is-submitting');
    }
  });
  }

  function installConversationInteraction16() {
document.addEventListener('click', async (ev) => {
    const pick = ev.target && ev.target.closest && ev.target.closest('.acp-perm-opt');
    if (!pick) return;
    ev.preventDefault();
    const sid = sharedState.currentSession && sharedState.currentSession.id;
    if (!sid) return;
    if (pick.classList.contains('is-submitting')) return;
    pick.classList.add('is-submitting');
    try {
      const res = await fetch('/api/acp/approval', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          harness: pick.getAttribute('data-acp-harness') || 'kimi',
          session_id: sid,
          request_id: pick.getAttribute('data-acp-req'),
          option_id: pick.getAttribute('data-acp-opt'),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (data && data.ok) {
        (0, sharedState.showOpToast)('Response sent.');
        const wrap = pick.closest('.acp-perm-options');
        if (wrap) wrap.classList.add('is-answered');
      } else {
        (0, sharedState.showOpToast)('Could not send response: ' + ((data && data.error) || 'unknown'), 'error');
      }
    } catch (e) {
      (0, sharedState.showOpToast)('Could not send response: ' + ((e && e.message) || e), 'error');
    } finally {
      pick.classList.remove('is-submitting');
    }
  });
  }

  function installConversationInteraction17() {
document.addEventListener('click', async (ev) => {
    const card = ev.target && ev.target.closest && ev.target.closest('.codex-request-card');
    if (!card) return;
    const optBtn = ev.target.closest('.codex-request-opt');
    const actionBtn = ev.target.closest('.codex-request-btn[data-decision]');
    if (!optBtn && !actionBtn) return;
    ev.preventDefault();
    if (card.classList.contains('is-resolved') || card.classList.contains('is-submitting')) return;
    const method = card.getAttribute('data-request-method') || '';
    const key = card.getAttribute('data-request-key') || '';
    const generationRaw = card.getAttribute('data-request-generation');
    const generation = generationRaw ? Number(generationRaw) : undefined;
    const threadId = card.getAttribute('data-thread-id') || '';
    let decision;
    const extra = {};
    if (optBtn) {
      decision = 'send-answer';
      const qid = optBtn.getAttribute('data-question-id') || '0';
      extra.answers = {};
      extra.answers[qid] = optBtn.getAttribute('data-answer') || '';
    } else {
      decision = actionBtn.getAttribute('data-decision') || 'cancel';
      if (decision === 'send-answer') {
        const input = card.querySelector('.codex-request-free-text');
        const qid = (input && input.getAttribute('data-question-id')) || '0';
        extra.answers = {};
        extra.answers[qid] = (input && input.value) || '';
      } else if (decision === 'allow-selected') {
        extra.permissions = {};
        card.querySelectorAll('.codex-request-perm input[type="checkbox"]').forEach((cb) => {
          extra.permissions[cb.getAttribute('data-perm-key')] = !!cb.checked;
        });
      }
    }
    const result = _codexRequestResultForDecision(method, decision, extra);
    card.classList.add('is-submitting');
    try {
      const res = await fetch('/api/codex/client/respond', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, generation, result, context: { thread_id: threadId } }),
      });
      const data = await res.json().catch(() => ({}));
      if (data && data.ok !== false) {
        card.classList.add('is-resolved');
        (0, sharedState.showOpToast)('Response sent.');
      } else {
        (0, sharedState.showOpToast)('Could not send response: ' + ((data && data.error) || 'unknown'), 'error');
      }
    } catch (e) {
      (0, sharedState.showOpToast)('Could not send response: ' + ((e && e.message) || e), 'error');
    } finally {
      card.classList.remove('is-submitting');
    }
  });
  }

  function installConversationInteraction18() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.cl-working-wake-btn, .cl-agy-wake-btn');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const sid = sharedState.currentSession && sharedState.currentSession.id;
    if (!sid || btn.disabled) return;
    btn.disabled = true;
    const origText = btn.textContent;
    btn.textContent = 'Sending…';
    const engine = ((sharedState.currentSession && (sharedState.currentSession.source || sharedState.currentSession.engine)) || '').toLowerCase();
    const wakeText = (engine === 'kimi')
      ? 'continue'
      : (engine === 'codex')
        ? sharedState.CODEX_WAKE_TEXT
        : (engine === 'antigravity')
          ? AGY_WAKE
          : 'Status check: your last action has not returned for a while. If you are stuck, say what you were waiting on and continue with the next concrete step.';
    try {
      let data;
      if (engine === 'codex' && typeof sharedState.postInjectInput === 'function') {
        data = await (0, sharedState.postInjectInput)(sid, wakeText, 'steer');
        if (!data.ok && (
          data.code === 'codex_no_active_turn'
          || data.code === 'codex_steer_unavailable'
          || data.code === 'codex_steer_failed'
        )) {
          data = await (0, sharedState.postInjectInput)(sid, wakeText, 'send');
        }
      } else {
        const res = await fetch('/api/inject-input', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sid, text: wakeText }),
        });
        data = await res.json().catch(() => ({}));
      }
      if (!data.ok) throw new Error(data.error || 'nudge failed');
      btn.textContent = 'Sent';
      (0, sharedState.showOpToast)('Nudge sent to session.');
      setTimeout(hostServices.refreshConversationList, 1500);
      setTimeout(() => {
        btn.textContent = origText;
        btn.disabled = false;
      }, 3000);
    } catch (err) {
      (0, sharedState.showOpToast)('Nudge failed: ' + (err && err.message || 'unknown'), 'error');
      btn.textContent = 'Failed';
      setTimeout(() => {
        btn.textContent = origText;
        btn.disabled = false;
      }, 3000);
    }
  });
  }

  async function _nudgeFixerBySid(sid) {
    if (!sid) return;
    const now = Date.now();
    const last = sharedState._uxNudgeLast[sid] || 0;
    if (now - last < sharedState._UX_NUDGE_THROTTLE_MS) {
      const ago = Math.round((now - last) / 1000);
      if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Already nudged ' + ago + 's ago - skipping.', 'info');
      return;
    }
    sharedState._uxNudgeLast[sid] = now;
    const WAKE = 'Queue nudge: re-check your UX-fixes queue now - claim the next ticket, '
      + 'fix + lean-commit + close via /api/ux-fixes/next, then keep draining. '
      + 'If it is empty, schedule an idle wakeup. Never busy-loop.';
    try {
      const res = await fetch('/api/inject-input', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sid, text: WAKE }),
      });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) throw new Error(data.error || 'nudge failed');
      if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Nudged worker to re-check the queue.', 'success');
    } catch (err) {
      delete sharedState._uxNudgeLast[sid];  // failed - allow an immediate retry
      if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Nudge failed: ' + (err && err.message || 'unknown'), 'error');
    }
  }

  async function _handleUxQueueNudge(el) {
    const item = el.closest('.conv-item');
    const sid = item ? item.getAttribute('data-session-id') : '';
    if (!sid) return;
    if (el.dataset.uxNudge === 'working') {
      if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Worker is mid-fix - no nudge needed.', 'info');
      return;
    }
    _nudgeFixerBySid(sid);
  }

  function installConversationInteraction19() {
document.addEventListener('click', (ev) => {
    const el = ev.target.closest('.conv-ux-fix-progress[data-ux-nudge]');
    if (!el) return;
    ev.preventDefault();
    ev.stopPropagation();   // don't also open/select the conversation row
    _handleUxQueueNudge(el);
  });
  }

  function installConversationInteraction20() {
document.addEventListener('keydown', (ev) => {
    if ((0, sharedState.isImeKey)(ev)) return;
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const el = ev.target.closest && ev.target.closest('.conv-ux-fix-progress[data-ux-nudge]');
    if (!el) return;
    ev.preventDefault();
    ev.stopPropagation();
    _handleUxQueueNudge(el);
  });
  }

  function installConversationInteraction21() {
document.addEventListener('keydown', (e) => {
    const t = e.target;
    if (t && (t.tagName === 'TEXTAREA'
      || (t.tagName === 'INPUT' && /^(text|search|email|url|tel|password)$/i.test(t.type || 'text'))
      || t.isContentEditable)) {
      sharedState._liveStripLastKeyTs = Date.now();
    }
  }, true);
  }

  function agentAnswerTextBeforeResult(resultEl) {
    return (0, sharedState.assistantNodeTextForCopy)((0, sharedState.agentAnswerNodeBeforeResult)(resultEl));
  }

  function handleBreadcrumbSpawnedByClick(ev) {
    const chip = ev.target && ev.target.closest && ev.target.closest('.ccc-breadcrumb-spawned-by[data-parent-sid], .ccc-worker-origin[data-parent-sid]');
    if (!chip) return;
    ev.preventDefault();
    ev.stopPropagation();
    const sid = chip.getAttribute('data-parent-sid') || '';
    if (sid) (0, sharedState.selectConversation)(sid);
  }

  function installConversationInteraction22() {
document.addEventListener('click', handleBreadcrumbSpawnedByClick);
  }

  async function handleSidebarSessionIdCopyClick(ev) {
    const btn = ev.target.closest('[data-copy-row-session-id]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const sid = btn.dataset.copyRowSessionId || '';
    const shortId = btn.dataset.sessionIdShort || sid.slice(0, 8);
    const ok = await (0, sharedState.copyTextValue)(sid);
    if (!ok) {
      (0, sharedState.showOpToast)('Copy failed - select and copy manually', 'error');
      return;
    }
    const label = btn.querySelector('code') || btn;
    btn.classList.add('copied');
    btn.setAttribute('aria-label', 'Copied session ID');
    btn.title = 'Copied session ID';
    label.textContent = 'copied';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.setAttribute('aria-label', 'Copy session ID ' + shortId);
      btn.title = 'Copy session ID: ' + sid;
      label.textContent = shortId;
    }, 1000);
    (0, sharedState.showOpToast)('Copied session ID', 'ok');
  }

  function installConversationInteraction23() {
document.addEventListener('click', handleSidebarSessionIdCopyClick, true);
  }

  function installConversationInteraction24() {
document.addEventListener('click', async (ev) => {
    const el = ev.target.closest('[data-copy-session-id]');
    if (!el) return;
    ev.stopPropagation();
    const sid = el.dataset.copySessionId || el.textContent || '';
    // CCC-1051: include the transcript path so another session can locate
    // the transcript regardless of which engine store it lives in.
    const path = (el.dataset.copyTranscriptPath || '').trim();
    const copyText = path ? (sid + ' (' + path + ')') : sid;
    const ok = await (0, sharedState.copyTextValue)(copyText);
    if (!ok) {
      (0, sharedState.showOpToast)('Copy failed - select and copy manually', 'error');
      return;
    }
    // Preserve the previous innerHTML (e.g. the breadcrumb's labeled
    // "Session <short> 📋" markup) — restoring textContent would leave the
    // raw UUID in place of the formatted chip after the flash.
    const prevHtml = el.innerHTML;
    el.textContent = 'copied!';
    el.classList.add('copied');
    setTimeout(() => {
      if (el.dataset.copySessionId === sid) el.innerHTML = prevHtml;
      el.classList.remove('copied');
    }, 1000);
    (0, sharedState.showOpToast)(path ? 'Copied session ID + transcript path' : 'Copied session ID', 'ok');
  });
  }

  function installConversationInteraction25() {
document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-read-agent-answer]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    if ((0, sharedState._ttsReadSelectionIfAny)(btn.closest('.conv-pane')?.getAttribute('data-pane-id') || (0, sharedState.activePaneId)())) return;
    if (btn === sharedState._ttsDirectBtn && sharedState._ttsUtterance) {
      if (sharedState._ttsActive && !sharedState._ttsPaused) {
        try { (0, sharedState._ttsEnginePause)(); } catch (_) {}
        sharedState._ttsPaused = true;
        (0, sharedState._setTtsDirectBtnState)(btn, 'paused');
      } else if (sharedState._ttsPaused) {
        try { (0, sharedState._ttsEngineResume)(); } catch (_) {}
        sharedState._ttsPaused = false;
        (0, sharedState._setTtsDirectBtnState)(btn, 'speaking');
      }
      return;
    }
    const resultEl = btn.closest('.event.result');
    const text = agentAnswerTextBeforeResult(resultEl);
    if (!text) {
      (0, sharedState.showOpToast)('No agent answer to read', 'error');
      return;
    }
    const pane = resultEl && resultEl.closest ? resultEl.closest('.conv-pane') : null;
    const paneId = (pane && pane.getAttribute('data-pane-id')) || (0, sharedState.activePaneId)();
    const paneState = paneId && typeof sharedState.paneByPaneId === 'function' ? (0, sharedState.paneByPaneId)(paneId) : null;
    const convId = (paneState && paneState.conversationId) || sharedState.currentConversation || '';
    const ok = (0, sharedState.speakTextDirect)(text, convId, paneId, btn, (0, sharedState._ttsPlaybackElements)((0, sharedState.agentAnswerNodeBeforeResult)(resultEl)));
    if (!ok) (0, sharedState.showOpToast)('Your browser does not support text-to-speech.', 'error');
  });
  }

  function installConversationInteraction26() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-copy-agent-answer]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const resultEl = btn.closest('.event.result');
    const text = agentAnswerTextBeforeResult(resultEl);
    if (!text) {
      (0, sharedState.showOpToast)('No agent answer to copy', 'error');
      return;
    }
    const ok = await (0, sharedState.copyTextValue)(text);
    if (!ok) {
      (0, sharedState.showOpToast)('Copy failed - select and copy manually', 'error');
      return;
    }
    btn.classList.add('copied');
    btn.innerHTML = '&#10003;';
    btn.setAttribute('aria-label', 'Copied agent answer');
    btn.title = 'Copied agent answer';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = '&#128203;';
      btn.setAttribute('aria-label', 'Copy agent answer');
      btn.title = 'Copy agent answer';
    }, 1200);
    (0, sharedState.showOpToast)('Copied agent answer', 'ok');
  });
  }

  function installConversationInteraction27() {
document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-toggle-tool-result]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const out = btn.previousElementSibling;
    if (!out || !out.classList.contains('tool-result-output')) return;
    const expanded = out.classList.toggle('is-expanded');
    btn.textContent = expanded ? 'Show less' : 'Show full result';
  });
  }

  function installConversationInteraction28() {
document.addEventListener('click', (ev) => {
    if (ev.target.closest('.conv-kebab-btn')) return;
    document.querySelectorAll('.conv-item.is-actions-open').forEach(row => {
      if (!row.contains(ev.target)) row.classList.remove('is-actions-open');
    });
  });
  }

  function installConversationInteraction29() {
document.addEventListener('keydown', (ev) => {
    if ((0, sharedState.isImeKey)(ev)) return;
    if (ev.key !== 'Escape') return;
    document.querySelectorAll('.conv-item.is-actions-open').forEach(row => row.classList.remove('is-actions-open'));
  });
  }

  function installConversationInteraction30() {
document.addEventListener('scroll', () => {
    document.querySelectorAll('.conv-item.is-actions-open').forEach(row => row.classList.remove('is-actions-open'));
  }, { capture: true, passive: true });
  }

  function installConversationInteraction31() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-copy-assistant-message]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const eventEl = btn.closest('.event.assistant') || btn.closest('.kimi-answer-meta') || btn.closest('.ask-turn-a');
    const text = (0, sharedState.assistantNodeTextForCopy)(eventEl);
    if (!text) {
      (0, sharedState.showOpToast)('No assistant message to copy', 'error');
      return;
    }
    const ok = await (0, sharedState.copyTextValue)(text);
    if (!ok) {
      (0, sharedState.showOpToast)('Copy failed - select and copy manually', 'error');
      return;
    }
    btn.classList.add('copied');
    btn.innerHTML = '&#10003;';
    btn.setAttribute('aria-label', 'Copied assistant message');
    btn.title = 'Copied assistant message';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.innerHTML = '&#128203;';
      btn.setAttribute('aria-label', 'Copy assistant message');
      btn.title = 'Copy assistant message';
    }, 1200);
    (0, sharedState.showOpToast)('Copied assistant message', 'ok');
  });
  }

  function installConversationInteraction31b() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-open-assistant-md]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const eventEl = btn.closest('.event.assistant') || btn.closest('.kimi-answer-meta') || btn.closest('.ask-turn-a');
    const text = (0, sharedState.assistantNodeTextForCopy)(eventEl);
    if (!text) {
      (0, sharedState.showOpToast)('No assistant message to open', 'error');
      return;
    }
    const copied = await (0, sharedState.copyTextValue)(text);
    let saved;
    try {
      const r = await fetch('/api/message-md', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text}),
      });
      saved = await r.json().catch(() => ({ok: false, error: 'HTTP ' + r.status}));
      if (!r.ok || !saved.ok) throw new Error(saved.error || ('HTTP ' + r.status));
      await (0, sharedState.loadMarkdownIntoFileViewer)(saved.path, saved.name);
    } catch (e) {
      (0, sharedState.showOpToast)('Open as MD failed: ' + (e && e.message || e), 'error');
      return;
    }
    (0, sharedState.showOpToast)((copied ? 'Copied and opened ' : 'Opened ') + saved.name, 'ok');
  });
  }

  function installConversationInteraction32() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-copy-user-message]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const row = btn.closest('.event.user_text');
    const msg = row && row.querySelector('.user-msg');
    const text = (msg && (msg.getAttribute('data-raw-text') || msg.textContent || '')).trim();
    if (!text) {
      (0, sharedState.showOpToast)('No message text to copy', 'error');
      return;
    }
    const ok = await (0, sharedState.copyTextValue)(text);
    if (!ok) {
      (0, sharedState.showOpToast)('Copy failed - select and copy manually', 'error');
      return;
    }
    const original = btn.innerHTML;
    btn.classList.add('copied');
    btn.innerHTML = '&#10003;';
    btn.title = 'Copied';
    setTimeout(() => {
      if (!btn.isConnected) return;
      btn.classList.remove('copied');
      btn.innerHTML = original;
      btn.title = 'Copy message';
    }, 1200);
    (0, sharedState.showOpToast)('Copied message', 'ok');
  });
  }

  function installConversationInteraction33() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-stop-held-action]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const block = btn.closest('.stop-held-block');
    const sid = btn.dataset.sessionId || '';
    const action = btn.dataset.stopHeldAction === 'release' ? 'release' : 'cancel';
    const id = btn.dataset.stopHeldId || '';
    if (!sid || !id) return;
    const buttons = block ? Array.from(block.querySelectorAll('[data-stop-held-action]')) : [btn];
    buttons.forEach(b => { b.disabled = true; });
    try {
      const response = await fetch('/api/stop-held/' + action, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(id === 'all' ? { session_id: sid } : { session_id: sid, ids: [id] }),
      });
      let data = {};
      try { data = await response.json(); } catch (_) {}
      const remaining = new Set(((data.held && data.held.items) || []).map(item => String(item.id)));
      if (block) {
        block.querySelectorAll('[data-stop-held-item]').forEach(row => {
          if (!remaining.has(row.dataset.stopHeldItem)) row.remove();
        });
        if (!remaining.size) block.hidden = true;
      }
      if (!response.ok || !data.ok) throw new Error(data.error || ((data.failed || 0) + ' not sent; still held'));
      (0, sharedState.showOpToast)(action === 'release'
        ? ('Released ' + (data.released || 0) + ' held message' + (data.released === 1 ? '' : 's') + '.')
        : ('Cancelled ' + (data.cancelled || 0) + ' held message' + (data.cancelled === 1 ? '' : 's') + '.'));
    } catch (err) {
      (0, sharedState.showOpToast)((action === 'release' ? 'Release' : 'Cancel') + ' failed: ' + ((err && err.message) || 'unknown'), 'error');
    } finally {
      buttons.forEach(b => { if (b.isConnected) b.disabled = false; });
    }
  });
  }

  function installConversationInteraction34() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-cancel-queued-message]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const row = btn.closest('.event.user_text');
    const tray = row && row.closest('.queued-steer-tray');
    const msg = row && row.querySelector('.user-msg');
    const text = (msg && (msg.getAttribute('data-raw-text') || msg.textContent || '')).trim();
    const sid = btn.dataset.sessionId || '';
    if (!sid || !text) {
      (0, sharedState.showOpToast)('Queued message is missing its session or text.', 'error');
      return;
    }
    const actions = row ? Array.from(row.querySelectorAll(
      '[data-cancel-queued-message], [data-steer-queued-message]'
    )) : [btn];
    const original = btn.textContent;
    actions.forEach(action => { action.disabled = true; });
    btn.textContent = 'Cancelling…';
    try {
      const response = await fetch('/api/pending-input/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The id names this one queued message, so cancelling one of two
        // identical sends removes that one; text alone is the fallback.
        body: JSON.stringify({ session_id: sid, text, delivery_id: (0, sharedState.queuedRowDeliveryId)(row) || undefined }),
      });
      let data = {};
      try { data = await response.json(); } catch (_) {}
      if (!response.ok || !data.ok) {
        throw new Error(data.error || ('HTTP ' + response.status));
      }
      if (row && row._pendingRef) (0, sharedState.removePendingSendEcho)(row._pendingRef);
      else if (row) row.remove();
      if (tray) (0, sharedState.syncQueuedSteerAllControl)(tray, sid);
      (0, sharedState.showOpToast)(data.already_consumed
        ? 'Queued card was already delivered.'
        : 'Queued message cancelled.');
      setTimeout(hostServices.refreshConversationList, 500);
    } catch (err) {
      btn.textContent = '!';
      (0, sharedState.showOpToast)('Cancel failed: ' + ((err && err.message) || 'unknown'), 'error');
    } finally {
      setTimeout(() => {
        if (!btn.isConnected) return;
        btn.textContent = original || 'Cancel';
        actions.forEach(action => { action.disabled = false; });
      }, 900);
    }
  });
  }



  function installConversationInteraction36() {
document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-read-assistant-message]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    if ((0, sharedState._ttsReadSelectionIfAny)(btn.closest('.conv-pane')?.getAttribute('data-pane-id') || (0, sharedState.activePaneId)())) return;
    // Second click on the SAME speaker button toggles pause/resume on the
    // in-flight utterance instead of restarting the read from the top
    // (CCC-408). A click on a DIFFERENT message's speaker still cancels
    // whatever is playing and starts fresh, same as before.
    if (btn === sharedState._ttsDirectBtn && sharedState._ttsUtterance) {
      if (sharedState._ttsActive && !sharedState._ttsPaused) {
        try { (0, sharedState._ttsEnginePause)(); } catch (_) {}
        sharedState._ttsPaused = true;
        (0, sharedState._setTtsDirectBtnState)(btn, 'paused');
      } else if (sharedState._ttsPaused) {
        try { (0, sharedState._ttsEngineResume)(); } catch (_) {}
        sharedState._ttsPaused = false;
        (0, sharedState._setTtsDirectBtnState)(btn, 'speaking');
      }
      return;
    }
    const eventEl = btn.closest('.event.assistant') || btn.closest('.kimi-answer-meta') || btn.closest('.ask-turn-a');
    const text = (0, sharedState.assistantNodeTextForCopy)(eventEl);
    if (!text) {
      (0, sharedState.showOpToast)('No assistant message to read', 'error');
      return;
    }
    const pane = eventEl && eventEl.closest ? eventEl.closest('.conv-pane') : null;
    const paneId = (pane && pane.getAttribute('data-pane-id')) || (0, sharedState.activePaneId)();
    const paneState = paneId && typeof sharedState.paneByPaneId === 'function' ? (0, sharedState.paneByPaneId)(paneId) : null;
    const convId = (paneState && paneState.conversationId) || sharedState.currentConversation || '';
    const ok = (0, sharedState.speakTextDirect)(text, convId, paneId, btn, (0, sharedState._ttsPlaybackElements)(eventEl));
    if (!ok) (0, sharedState.showOpToast)('Your browser does not support text-to-speech.', 'error');
  });
  }

  function installConversationInteraction37() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-steer-queued-message]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    let row = btn.closest('.event.user_text');
    const msg = row && row.querySelector('.user-msg');
    const text = (msg && (msg.getAttribute('data-raw-text') || msg.textContent || '')).trim();
    const sid = btn.dataset.sessionId || '';
    if (!sid || !text) {
      (0, sharedState.showOpToast)('Queued message is missing its session or text.', 'error');
      return;
    }
    if (!(0, sharedState.sessionSupportsQueuedSteer)((0, sharedState.queuedSteerSource)(sharedState.currentSession && sharedState.currentSession.source, sharedState.currentSession && sharedState.currentSession.engine), sharedState.currentSession && sharedState.currentSession.acp_steer_ready)) {
      (0, sharedState.showOpToast)('Steer is only available for Codex and ACP sessions.', 'error');
      return;
    }
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Steering…';
    const steerPaneId = row.closest('.conv-pane')?.dataset.paneId || (0, sharedState.activePaneId)();
    const $view = trayConversationView(row.closest('.queued-steer-tray'));
    if (row?.dataset.busySend === 'true') {
      const ready = await readyQueuedSteerRows([row]);
      if (!ready.length) return;
      row = ready[0];
    }
    markSteerInFlight(sid, [text]);
    clearQueuedSteerError(sid, text);
    const moved = (0, sharedState.beginOptimisticSteerMove)([row], $view);
    // Release the in-flight hold BEFORE reverting: the reverted card is a
    // queued candidate again, and the in-flight filter would delete it.
    const giveBack = () => {
      clearSteerInFlight(sid, [text]);
      revertOptimisticSteerMove(moved, steerPaneId);
    };
    try {
      const data = await (0, sharedState.postInjectInput)(sid, text, 'steer', { replaceQueued: true, userInitiated: true, replaceQueuedIds: [(0, sharedState.queuedRowDeliveryId)(row)] });
      if (data && data.code === 'queued_message_missing') {
        giveBack();
        // This card can predate the visible transcript tail. Re-read the
        // history once so a delivered message retires its stale saved echo,
        // rather than repeatedly offering Steer for a row the queue lacks.
        const pid = row.closest('.conv-pane')?.dataset.paneId || (0, sharedState.activePaneId)();
        const pane = (0, sharedState.paneByPaneId)(pid);
        if (pane) { pane.wantFull = true; pane.lastLine = 0; }
        await (0, sharedState.fetchConversationEvents)(pid);
        (0, sharedState.showOpToast)('Queue changed; conversation refreshed.');
        return;
      }
      if (data && data.queue_pump_started) {
        giveBack();
        (0, sharedState.showOpToast)('Turn ended; sending the oldest queued message now.');
        setTimeout(hostServices.refreshConversationList, 500);
        return;
      }
      if (data && data.queued_preserved) {
        if (data.error) setQueuedSteerError(sid, text, data.error);
        giveBack();
        (0, sharedState.showOpToast)(data.error ? ('Still queued: ' + data.error) : 'Queued', 'error');
        setTimeout(hostServices.refreshConversationList, 500);
        return;
      }
      if (!data || !data.ok) throw new Error((data && ((0, sharedState.formatInjectFailure)(data, 0) || data.error)) || 'steer failed');
      if (!data.queued_consumed) {
        giveBack();
        (0, sharedState.showOpToast)('Queue changed while steering; refreshing.', 'error');
        setTimeout(hostServices.refreshConversationList, 200);
        return;
      }
      // Delivered. Keep the optimistic row standing as "✓ Delivered" so the
      // message never blinks out of the conversation between the steer and the
      // durable transcript event that dedupes it away.
      if (row) {
        row.classList.remove('steering-optimistic');
        if (data.steer_kept_sender) row.dataset.steerKeptSender = '1';
        if (row._pendingRef) (0, sharedState.markPendingSendDelivered)(row._pendingRef, data);
        else settleSteeredServerCard(row);
      }
      (0, sharedState.showOpToast)(steeredToastText(data));
      setTimeout(hostServices.refreshConversationList, 500);
    } catch (err) {
      setQueuedSteerError(sid, text, (err && err.message) || 'steer failed');
      giveBack();
      btn.textContent = '!';
      (0, sharedState.showOpToast)('Steer failed: ' + ((err && err.message) || 'unknown'), 'error');
    } finally {
      // Outlive the refresh kicked off above: its event payload may have been
      // fetched before the server withdrew the queued copy.
      setTimeout(() => clearSteerInFlight(sid, [text]), 2000);
      setTimeout(() => {
        if (!btn.isConnected) return;
        btn.disabled = false;
        btn.textContent = original || 'Steer';
      }, 900);
    }
  });
  }

  function installConversationInteraction38() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-steer-user-message]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const sid = sharedState.currentSession && sharedState.currentSession.id;
    const steerableSource = sharedState.currentSession && (
      sharedState.currentSession.source === 'codex'
      || (0, sharedState.devinSteerCapableNow)()
    );
    if (!sid || !steerableSource) {
      (0, sharedState.showOpToast)('Steer is only available for Codex and steer-ready Devin sessions.', 'error');
      return;
    }
    const row = btn.closest('.event.user_text');
    const msg = row && row.querySelector('.user-msg');
    const text = (msg && (msg.getAttribute('data-raw-text') || msg.textContent || '')).trim();
    if (!text) {
      (0, sharedState.showOpToast)('No message text to steer', 'error');
      return;
    }
    // If this steer is being clicked on an optimistic/queued echo, it is
    // meant to replace the durable queued copy of the same message. Use
    // replaceQueued so the backend withdraws the queued copy before steering
    // and the echo is removed.
    const isPending = row && (
      row.classList.contains('pending')
      || row.classList.contains('send-queued')
      || row.classList.contains('not-acknowledged')
      || row._pendingRef
    );
    // A pending echo may have been acknowledged since this button rendered.
    if (!isPending) {
      btn.remove();
      return;
    }
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'Steering…';
    try {
      const data = await (0, sharedState.postInjectInput)(sid, text, 'steer',
        { replaceQueued: true, userInitiated: true, replaceQueuedIds: [(0, sharedState.queuedRowDeliveryId)(row)] });
      if (data && data.queue_pump_started) {
        (0, sharedState.showOpToast)('Turn ended; sending the oldest queued message now.');
        setTimeout(hostServices.refreshConversationList, 500);
        return;
      }
      if (data && data.queued_preserved) {
        (0, sharedState.showOpToast)(data.error ? ('Still queued: ' + data.error) : 'Queued', 'error');
        setTimeout(hostServices.refreshConversationList, 500);
        return;
      }
      if (!data || !data.ok) throw new Error((data && ((0, sharedState.formatInjectFailure)(data, 0) || data.error)) || 'steer failed');
      if (isPending && !data.queued_consumed) {
        (0, sharedState.showOpToast)('Queue changed while steering; refreshing.', 'error');
        setTimeout(hostServices.refreshConversationList, 200);
        return;
      }
      if (isPending) {
        if (row && row._pendingRef) (0, sharedState.removePendingSendEcho)(row._pendingRef);
        else if (row) row.remove();
      }
      btn.textContent = '✓';
      (0, sharedState.showOpToast)(subagentRelayToastText(data) || (
        data.via === 'codex-steer' ? 'Steered running Codex turn.'
        : sharedState.currentSession.source === 'devin-cli' ? 'Steered running Devin turn.'
        : 'Sent to Codex.'));
      setTimeout(hostServices.refreshConversationList, 1500);
      setTimeout(hostServices.refreshConversationList, 3500);
    } catch (err) {
      btn.textContent = '!';
      (0, sharedState.showOpToast)('Steer failed: ' + ((err && err.message) || 'unknown'), 'error');
    } finally {
      setTimeout(() => {
        if (!btn.isConnected) return;
        btn.disabled = false;
        btn.textContent = original || 'Steer';
      }, 900);
    }
  });
  }

  function installConversationInteraction39() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('[data-steer-all-queued]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const tray = btn.closest('.queued-steer-tray');
    const sid = btn.dataset.sessionId || '';
    const original = btn.textContent;
    const steerPaneId = tray.closest('.conv-pane')?.dataset.paneId || (0, sharedState.activePaneId)();
    const $view = trayConversationView(tray);
    let rows = tray ? Array.from(tray.querySelectorAll('.event.user_text')) : [];
    if (rows.some(row => row.dataset.busySend === 'true')) {
      btn.disabled = true;
      btn.textContent = 'Steering…';
      rows = await readyQueuedSteerRows(rows);
      if (!rows.length) return;
      btn.disabled = false;
    }
    const texts = rows.map(row => {
      const msg = row.querySelector('.user-msg');
      return ((msg && (msg.getAttribute('data-raw-text') || msg.textContent)) || '').trim();
    }).filter(Boolean);
    if (!sid || !texts.length) {
      (0, sharedState.showOpToast)('Nothing queued to steer.', 'error');
      return;
    }
    if (!(0, sharedState.sessionSupportsQueuedSteer)((0, sharedState.queuedSteerSource)(sharedState.currentSession && sharedState.currentSession.source, sharedState.currentSession && sharedState.currentSession.engine), sharedState.currentSession && sharedState.currentSession.acp_steer_ready)) {
      (0, sharedState.showOpToast)('Steer all is only available for Codex and ACP sessions.', 'error');
      return;
    }
    btn.disabled = true;
    btn.textContent = 'Steering…';
    markSteerInFlight(sid, texts);
    clearQueuedSteerError(sid, texts);
    const moved = (0, sharedState.beginOptimisticSteerMove)(rows, $view);
    const giveBack = () => {
      clearSteerInFlight(sid, texts);
      revertOptimisticSteerMove(moved, steerPaneId);
    };
    try {
      const data = await (0, sharedState.postInjectInput)(sid, texts.join('\n\n'), 'steer', {
        replaceQueuedTexts: texts,
        replaceQueuedIds: rows.map(sharedState.queuedRowDeliveryId),
        userInitiated: true,
      });
      if (data && (data.queue_pump_started || data.queued_preserved)) {
        giveBack();
        (0, sharedState.showOpToast)(data.queue_pump_started
          ? 'Turn ended; sending the oldest queued message now.'
          : (data.error ? ('Still queued: ' + data.error) : 'Queued'), 'error');
        setTimeout(hostServices.refreshConversationList, 500);
        return;
      }
      if (!data || !data.ok) throw new Error((data && ((0, sharedState.formatInjectFailure)(data, 0) || data.error)) || 'steer failed');
      // The delivered text is the concatenation, so no individual card can
      // dedupe against the durable event -- drop them all and let the combined
      // message render as one turn.
      moved.forEach(row => {
        if (row._pendingRef) (0, sharedState.removePendingSendEcho)(row._pendingRef);
        else if (row.parentNode) row.parentNode.removeChild(row);
      });
      (0, sharedState.showOpToast)(subagentRelayToastText(data) || ('Steered ' + texts.length + ' queued messages as one turn.'));
      setTimeout(hostServices.refreshConversationList, 500);
    } catch (err) {
      setQueuedSteerError(sid, texts, (err && err.message) || 'steer failed');
      giveBack();
      btn.textContent = '!';
      (0, sharedState.showOpToast)('Steer all failed: ' + ((err && err.message) || 'unknown'), 'error');
    } finally {
      setTimeout(() => clearSteerInFlight(sid, texts), 2000);
      setTimeout(() => {
        if (!btn.isConnected) return;
        btn.disabled = false;
        btn.textContent = original || 'Steer all';
      }, 900);
    }
  });
  }

  function conversationGoalActionCommand(action) {
    if (action === 'clear') return '/goal clear';
    if (action === 'resume') return '/goal resume';
    if (action === 'edit') return '/goal edit';
    if (action === 'pause') return '/goal pause';
    return '';
  }

  async function sendConversationGoalAction(btn) {
    if (!btn || btn.disabled) return;
    const action = btn.getAttribute('data-goal-action') || '';
    const command = conversationGoalActionCommand(action);
    const row = (0, sharedState.currentConversationRow)();
    const sid = (row && (row.session_id || row.id)) || (sharedState.currentSession && sharedState.currentSession.id);
    if (!command || !sid) {
      (0, sharedState.showOpToast)('No session is selected for this goal action.', 'error');
      return;
    }
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '...';
    try {
      const data = await (0, sharedState.postInjectInput)(sid, command, undefined, { userInitiated: true });
      if (!data.ok) throw new Error(data.error || 'goal action failed');
      (0, sharedState.applyOptimisticConversationGoalAction)(row, action);
      (0, sharedState.renderConversationGoalStrip)((0, sharedState.activePaneId)(), row);
      if (typeof hostServices.renderSidebar === 'function' && typeof sharedState.filterConversations === 'function' && typeof sharedState.$convSearch !== 'undefined') {
        (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value));
      }
      (0, sharedState.showOpToast)('Sent ' + command + '.', 'success');
      setTimeout(hostServices.refreshConversationList, 1200);
      setTimeout(hostServices.refreshConversationList, 3200);
    } catch (err) {
      (0, sharedState.showOpToast)('Goal action failed: ' + ((err && err.message) || 'unknown'), 'error');
      btn.textContent = original;
      btn.disabled = false;
    }
  }

  function installConversationInteraction40() {
document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-role="conv-goal-action"]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    sendConversationGoalAction(btn);
  });
  }

  function installConversationInteraction41() {
document.addEventListener('click', (ev) => {
    if (!sharedState._slashMenuEl) return;
    if (sharedState._slashMenuEl.contains(ev.target) || (sharedState._slashMenuInput && sharedState._slashMenuInput.contains(ev.target))) return;
    (0, sharedState.hideSlashCommandMenu)();
  });
  }

  function installConversationInteraction42() {
window.addEventListener('resize', () => (0, sharedState.positionSlashCommandMenu)(sharedState._slashMenuInput));
  }

  async function readyQueuedSteerRows(rows) {
    if (rows.some(row => row._pendingRef?.steerWaiting)) return [];
    const claimed = [];
    rows.forEach(row => {
      const pending = row._pendingRef;
      if (pending) { pending.steerWaiting = true; claimed.push(pending); }
      const text = row.querySelector('.user-msg');
      markSteerInFlight(pending?.sid || row.querySelector('[data-session-id]')?.dataset.sessionId,
        [text?.getAttribute('data-raw-text') || text?.textContent || '']);
    });
    const view = trayConversationView(rows[0]?.closest('.queued-steer-tray'));
    (0, sharedState.beginOptimisticSteerMove)(rows, view);
    try {
      const ready = await Promise.all(rows.map(async row => {
        const pending = row._pendingRef;
        if (row.dataset.busySend !== 'true') return row;
        if (!pending?.queueReady) return null;
        const queued = await pending.queueReady;
        return queued && pending.entry?.queued && !pending.entry.delivered
          && pending.element?.isConnected ? pending.element : null;
      }));
      rows.forEach((row, i) => {
        if (ready[i]?.isConnected && !ready[i]._pendingRef?.entry?.delivered) return;
        const pending = row._pendingRef;
        clearSteerInFlight(pending?.sid, [pending?.text]);
        row.classList.remove('steering-optimistic');
        row.querySelector('.steer-background-note')?.remove();
        // Direct delivery, refusal and durable receipt each finish the
        // original Send themselves; none should create a second injection.
      });
      return ready.filter(row => row?.isConnected && !row._pendingRef?.entry?.delivered);
    } finally {
      claimed.forEach(pending => { pending.steerWaiting = false; });
    }
  }

  function _closestAnnotatableEvent(node) {
    if (!node || !node.closest) return null;
    const direct = node.closest('.conv-pane .event');
    if (direct) return direct;
    const turn = node.closest('.conv-pane .kimi-turn');
    if (!turn) return null;
    let child = node;
    while (child && child.parentElement !== turn) child = child.parentElement;
    while (child) {
      if (child.classList && child.classList.contains('event')) return child;
      child = child.previousElementSibling;
    }
    return null;
  }

  function _selectionSingleEventInfo() {
    const sel = window.getSelection && window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const text = String(sel).trim();
    if (!text) return null;
    const range = sel.getRangeAt(0);
    const startNode = range.startContainer.nodeType === 3 ? range.startContainer.parentElement : range.startContainer;
    const endNode = range.endContainer.nodeType === 3 ? range.endContainer.parentElement : range.endContainer;
    const startEvent = _closestAnnotatableEvent(startNode);
    const endEvent = _closestAnnotatableEvent(endNode);
    if (!startEvent || startEvent !== endEvent) return null;
    if (startEvent.dataset.jsonlLine == null) return null;
    return { range: range.cloneRange(), eventEl: startEvent, text: text };
  }

  function _hideAnnotateTrigger() {
    if (sharedState._annotateTriggerEl && sharedState._annotateTriggerEl.parentNode) {
      sharedState._annotateTriggerEl.parentNode.removeChild(sharedState._annotateTriggerEl);
    }
    sharedState._annotateTriggerEl = null;
  }

  function _showAnnotateTrigger(rect) {
    _hideAnnotateTrigger();
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'ccc-annotation-trigger';
    btn.setAttribute('aria-label', 'Add annotation');
    btn.textContent = '+';
    btn.style.top = Math.max(4, rect.top - 28) + 'px';
    btn.style.left = Math.min(window.innerWidth - 32, Math.max(4, rect.right - 8)) + 'px';
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      const range = sharedState._annotateCapturedRange;
      const eventEl = sharedState._annotateCapturedEventEl;
      _hideAnnotateTrigger();
      if (range && eventEl) _openAnnotatePopover(range, eventEl);
    });
    document.body.appendChild(btn);
    sharedState._annotateTriggerEl = btn;
  }

  function installConversationInteraction43() {
document.addEventListener('mouseup', (ev) => {
    if (ev.target && ev.target.closest && ev.target.closest('.ccc-annotation-trigger, .ccc-annotation-popover')) return;
    const info = _selectionSingleEventInfo();
    if (!info) { _hideAnnotateTrigger(); return; }
    const rect = info.range.getBoundingClientRect();
    if (!rect || (!rect.width && !rect.height)) { _hideAnnotateTrigger(); return; }
    sharedState._annotateCapturedRange = info.range;
    sharedState._annotateCapturedEventEl = info.eventEl;
    _showAnnotateTrigger(rect);
  });
  }

  function _closeAnnotatePopover() {
    document.removeEventListener('mousedown', _annotatePopoverOutsideHandler, true);
    if (sharedState._annotatePopoverEl && sharedState._annotatePopoverEl.parentNode) {
      sharedState._annotatePopoverEl.parentNode.removeChild(sharedState._annotatePopoverEl);
    }
    sharedState._annotatePopoverEl = null;
  }

  function _annotatePopoverOutsideHandler(ev) {
    if (sharedState._annotatePopoverEl && !sharedState._annotatePopoverEl.contains(ev.target)) {
      _closeAnnotatePopover();
    }
  }

  function _openAnnotatePopover(range, eventEl) {
    _closeAnnotatePopover();
    const rect = range.getBoundingClientRect();
    const pop = document.createElement('div');
    pop.className = 'ccc-annotation-popover';
    pop.style.top = Math.max(4, rect.bottom + 6) + 'px';
    pop.style.left = Math.min(window.innerWidth - 260, Math.max(4, rect.left)) + 'px';
    pop.innerHTML = '<textarea class="ccc-annotation-popover-input" placeholder="Add annotation…" rows="2"></textarea>';
    document.body.appendChild(pop);
    sharedState._annotatePopoverEl = pop;
    const ta = pop.querySelector('textarea');
    ta.focus();
    ta.addEventListener('keydown', (ev) => {
      if ((0, sharedState.isImeKey)(ev)) return;
      if (ev.key === 'Enter' && !ev.shiftKey) {
        ev.preventDefault();
        const note = ta.value.trim();
        _closeAnnotatePopover();
        if (note) _saveAnnotation(range, eventEl, note);
      } else if (ev.key === 'Escape') {
        ev.preventDefault();
        _closeAnnotatePopover();
      }
    });
    setTimeout(() => document.addEventListener('mousedown', _annotatePopoverOutsideHandler, true), 0);
  }

  function _saveAnnotationsForSession(sessionId, list) {
    try {
      const raw = JSON.parse(localStorage.getItem(sharedState.ANNOTATIONS_STORAGE_KEY) || '{}');
      const store = (raw && typeof raw === 'object') ? raw : {};
      store[sessionId] = list;
      localStorage.setItem(sharedState.ANNOTATIONS_STORAGE_KEY, JSON.stringify(store));
    } catch (_) {
      // localStorage unavailable or quota exceeded — annotation still shows
      // in-DOM and in the composer for this session; just won't survive reload.
    }
  }

  function _annotationExcerpt(fullText) {
    const trimmed = fullText.trim().replace(/\s+/g, ' ');
    const LIMIT = 24;
    if (trimmed.length <= LIMIT) return trimmed;
    return trimmed.slice(0, LIMIT).trim() + '…';
  }

  function _annotationComposerText(jsonlLine, fullText, note) {
    return '[L' + jsonlLine + '] "' + _annotationExcerpt(fullText) + '": ' + note;
  }

  function _saveAnnotation(range, eventEl, note) {
    const paneEl = eventEl.closest('.conv-pane');
    const paneId = paneEl ? paneEl.dataset.paneId : (0, sharedState.activePaneId)();
    const pane = (0, sharedState.paneByPaneId)(paneId);
    const sessionId = (pane && (sharedState.sessionIdByConv[pane.conversationId] || pane.conversationId)) || paneId;
    const jsonlLine = eventEl.dataset.jsonlLine;
    const fullText = String(range);
    const id = 'ann-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);

    const markEl = (0, sharedState._wrapRangeInAnnotationMark)(range, id);
    (0, sharedState._insertAnnotationNote)(markEl, note, id, sessionId);

    const composerEl = (typeof sharedState.composerInputForPane === 'function' && (0, sharedState.composerInputForPane)(paneId)) || document.getElementById('convInput');
    if (composerEl) (0, sharedState.insertAtCursor)(composerEl, _annotationComposerText(jsonlLine, fullText, note));

    const list = (0, sharedState._annotationsForSession)(sessionId);
    list.push({ id: id, jsonlLine: jsonlLine, excerpt: _annotationExcerpt(fullText), note: note, createdAt: Date.now() });
    _saveAnnotationsForSession(sessionId, list);
  }

  function installConversationInteraction44() {
document.addEventListener('click', (ev) => {
    const delBtn = ev.target && ev.target.closest && ev.target.closest('.ccc-annotation-note-delete');
    if (!delBtn) return;
    const noteEl = delBtn.closest('.ccc-annotation-note');
    if (!noteEl) return;
    _deleteAnnotation(noteEl.dataset.annotationId, noteEl.dataset.sessionId);
  });
  }

  function _deleteAnnotation(id, sessionId) {
    const escId = (window.CSS && CSS.escape) ? CSS.escape(id) : id;
    const noteEl = document.querySelector('.ccc-annotation-note[data-annotation-id="' + escId + '"]');
    if (noteEl && noteEl.parentNode) noteEl.parentNode.removeChild(noteEl);
    const markEl = document.querySelector('.ccc-annotation-mark[data-annotation-id="' + escId + '"]');
    if (markEl && markEl.parentNode) {
      const parent = markEl.parentNode;
      while (markEl.firstChild) parent.insertBefore(markEl.firstChild, markEl);
      parent.removeChild(markEl);
    }
    const list = (0, sharedState._annotationsForSession)(sessionId).filter((a) => a.id !== id);
    _saveAnnotationsForSession(sessionId, list);
  }

  function installConversationInteraction45() {
window.addEventListener('ccc:browser-voice', event => {
    (0, sharedState._ttsLog)('BROWSER-STATE', JSON.stringify(event.detail));
  });
  }





  function installConversationInteraction48() {
try { sharedState._ttsEnabled = localStorage.getItem('ccc-tts-enabled') !== '0'; } catch (_) {}
  }

  function installConversationInteraction49() {
window.__cccSetTtsEnabled = sharedState._ttsSetEnabled;
  }

  function installConversationInteraction50() {
window.__cccIsTtsEnabled = () => sharedState._ttsEnabled;
  }











  function installConversationInteraction56() {
document.getElementById('settingsTtsEnabledToggle')?.addEventListener('click', () => (0, sharedState._ttsSetEnabled)(!sharedState._ttsEnabled));
  }



  function installConversationInteraction58() {
(0, sharedState._ttsSyncEnabledControls)();
  }









  function installConversationInteraction62() {
window.addEventListener('pagehide', () => { try { (0, sharedState._ttsPerfFlush)(sharedState._ttsPerfState); } catch (_) {} });
  }

  function installConversationInteraction63() {
document.addEventListener('click', ev => {
    const btn = ev.target.closest && ev.target.closest('[data-role="popout-row-session"]');
    if (!btn) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    const row = btn.closest('.conv-item[data-session-id]');
    const base = row && (0, sharedState._ultraFreshRow)(row.dataset.sessionId);
    if (!base || base.pending_spawn || base.source === 'backlog' || base.source === 'github_pr') return;
    openConversationPopout(base.id || base.session_id, base.repo_path, btn);
  }, true);
  }

  function rowDecisionReportSnapshot(row, c) {
    const visible = selector => [...row.querySelectorAll(selector)].filter(el => el.getClientRects().length);
    const actions = visible('[data-role="quick-action"]').map(btn => ({
      label: btn.textContent.trim(),
      ...(btn.dataset.reviewUrl ? { href: btn.dataset.reviewUrl }
        : { reply: (btn.title || '').replace(/^Send to this session: /, '') }),
      disabled: btn.disabled,
    }));
    const r = row.getBoundingClientRect();
    const rect = { x: r.x, y: r.y, width: r.width, height: r.height };
    const fresh = sharedState._ultraFresh.entries.get(String(c.session_id || c.id));
    const fields = ['session_id', 'id', 'engine', 'source', 'node_id', 'node_name', 'ref',
      'display_name', 'ai_title', 'task_title', 'name_overridden', 'session_state', 'state', 'row_data_unavailable',
      'last_event_type', 'modified', 'mtime', 'sidecar_ts', 'sidecar_status',
      'needs_approval', 'question_waiting', 'question_text', 'question_options',
      'continued_from_session_id', 'continuation_topic_title', 'continuation_topic_session_id', 'parent_session_id', 'archived', 'trashed'];
    const clientState = Object.fromEntries(fields.filter(k => c[k] !== undefined).map(k => [k, c[k]]));
    clientState.row_outcome = (0, sharedState.rowOutcomeView)(c);
    clientState.live_overlay = fresh ? { at: fresh.at, userAt: fresh.userAt, sendAt: fresh.sendAt,
      line: fresh.line, sessionState: fresh.sessionState } : null;
    return {
      session_id: String(c.session_id || c.id), url: location.href,
      visible_row: {
        title: visible('.wa-title, .conv-title').map(el => el.textContent.trim()).join(' | ') || (0, sharedState.rowRawTitle)(c).rawTitle,
        text: row.innerText.slice(0, 24000),
        outcome: visible('.conv-outcome, .wa-preview').map(el => el.innerText.trim()),
        labels: visible('.conv-outcome-step, .wa-explanation-label').map(el => ({
          label: el.dataset.label || el.textContent.trim(),
          before: getComputedStyle(el, '::before').content,
        })),
        actions, lifecycle_group: row.dataset.lifecycleGroup || '', class_name: row.className,
      },
      client_state: clientState,
      latest_assistant_text: String((fresh && fresh.text) || c.last_assistant_text || c.last_assistant_preview || '').slice(0, 160000),
      rect, screen: annEstimateScreenRect(rect),
      viewport: { width: innerWidth, height: innerHeight, device_pixel_ratio: devicePixelRatio,
        browser: navigator.userAgent, captured_at: new Date().toISOString() },
      native_app: typeof sharedState.isCccMacApp === 'function' && (0, sharedState.isCccMacApp)(),
    };
  }

  function captureRowDecisionScreenshot(row, rect) {
    return annCaptureDomRegionB64(rect, row, row, clone => {
      // Freeze reply-chip widths so SVG serialization cannot reflow them,
      // and materialize status pseudo-elements that live outside the clone.
      const originals = row.querySelectorAll('.conv-quick-action, .conv-outcome-step');
      const copies = clone.querySelectorAll('.conv-quick-action, .conv-outcome-step');
      originals.forEach((el, i) => {
        const copy = copies[i];
        if (!copy) return;
        if (el.classList.contains('conv-quick-action')) {
          const box = el.getBoundingClientRect();
          if (!box.width) return;
          for (const prop of ['width', 'min-width', 'max-width', 'inline-size', 'min-inline-size', 'max-inline-size']) {
            copy.style.setProperty(prop, box.width + 'px', 'important');
          }
          copy.style.setProperty('height', box.height + 'px', 'important');
          copy.style.setProperty('box-sizing', 'border-box', 'important');
          copy.style.setProperty('flex-shrink', '0', 'important');
          const range = document.createRange();
          range.selectNodeContents(el);
          if (range.getClientRects().length === 1) {
            copy.style.setProperty('white-space', 'nowrap', 'important');
          }
        } else {
          const before = getComputedStyle(el, '::before');
          if (!before.content || before.content === 'none' || before.content === 'normal') return;
          let text;
          try { text = JSON.parse(before.content); } catch (_) { text = (el.dataset.label || '') + ': '; }
          const label = document.createElement('span');
          label.textContent = text;
          label.style.color = before.color; label.style.fontWeight = before.fontWeight;
          copy.prepend(label);
        }
      });
      let parent = row;
      while (parent) {
        const bg = getComputedStyle(parent).backgroundColor;
        if (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)') {
          clone.style.backgroundColor = bg; break;
        }
        parent = parent.parentElement;
      }
    });
  }

  async function followRowReportReceipt(result, btn) {
    // The editor is already closed: slow queue locks no longer trap its controls.
    for (let attempt = 0; attempt < 30; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 2000));
      try {
        const response = await fetch(result.status_url, { cache: 'no-store', signal: AbortSignal.timeout(10000) });
        const receipt = await response.json();
        if (!response.ok) throw new Error('Receipt unavailable');
        if (receipt.state === 'complete' && receipt.ref) {
          (0, sharedState.showOpToast)('Decisions Fix: reported as ' + receipt.ref, 'success');
          btn.title = 'Reported as ' + receipt.ref + '; click to report another mismatch';
          return;
        }
        if (receipt.state === 'failed') {
          (0, sharedState.showOpToast)('Report saved, but ticket filing failed. Click Report again to retry this saved report.', 'error');
          return;
        }
      } catch (_) { /* The durable worker continues even if this tab disconnects. */ }
    }
    (0, sharedState.showOpToast)('Report is saved. Ticket filing is still pending; it continues in the background.', 'warning');
  }

  function openRowDecisionReportEditor(btn, submit) {
    const form = document.createElement('form');
    form.className = 'conv-report-editor';
    form.setAttribute('role', 'dialog');
    form.setAttribute('aria-label', 'Report a row problem');
    form.setAttribute('popover', 'auto');
    form.innerHTML = '<label for="rowReportNote">What is wrong? <span>(optional)</span></label>'
      + '<textarea id="rowReportNote" name="reporter_note" rows="3" maxlength="2000"'
      + ' placeholder="For example: this row needs an approval button."></textarea>'
      + '<p class="conv-report-error" role="alert" hidden></p>'
      + '<div class="conv-report-controls"><button type="button" data-report-cancel>Cancel</button>'
      + '<button type="submit">Send report</button></div>';
    const input = form.querySelector('textarea');
    const send = form.querySelector('[type="submit"]');
    const cancel = form.querySelector('[data-report-cancel]');
    const error = form.querySelector('[role="alert"]');
    const sid = btn.closest('.conv-item')?.dataset.sessionId;
    let busy = false;
    const position = () => {
      const viewport = window.visualViewport;
      const left = viewport?.offsetLeft || 0, top = viewport?.offsetTop || 0;
      const width = viewport?.width || innerWidth, height = viewport?.height || innerHeight;
      const anchor = btn.isConnected ? btn.getBoundingClientRect() : { right: left + width / 2, bottom: top + 12, top: top + 12 };
      form.style.width = Math.min(340, width - 24) + 'px';
      form.style.maxHeight = Math.max(120, height - 24) + 'px';
      form.style.left = Math.max(left + 12, Math.min(anchor.right - form.offsetWidth, left + width - form.offsetWidth - 12)) + 'px';
      const below = anchor.bottom + 8;
      form.style.top = Math.max(top + 12, Math.min(below + form.offsetHeight > top + height - 12
        ? anchor.top - form.offsetHeight - 8 : below, top + height - form.offsetHeight - 12)) + 'px';
    };
    const close = () => {
      if (sharedState._rowDecisionReportEditor !== form) return;
      sharedState._rowDecisionReportEditor = null;
      window.removeEventListener('resize', position);
      window.visualViewport?.removeEventListener('resize', position);
      window.visualViewport?.removeEventListener('scroll', position);
      btn.setAttribute('aria-expanded', 'false');
      form.remove();
      const trigger = btn.isConnected ? btn : document.querySelector('.conv-item[data-session-id="' + CSS.escape(sid || '') + '"] [data-role="report-decision"]');
      trigger?.focus({ preventScroll: true });
    };
    cancel.addEventListener('click', close);
    form.addEventListener('toggle', ev => { if (ev.newState === 'closed' && !busy) close(); });
    form.addEventListener('keydown', ev => {
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); if (!busy) close(); }
    });
    form.addEventListener('click', ev => ev.stopPropagation());
    form.addEventListener('submit', async ev => {
      ev.preventDefault();
      if (busy) return;
      busy = true; send.disabled = true; cancel.disabled = true; input.disabled = true;
      send.textContent = 'Sending…'; error.hidden = true;
      try {
        await submit(input.value.trim());
        close();
      } catch (failure) {
        error.textContent = String(failure.message || failure);
        error.hidden = false;
        if (form.showPopover && !form.matches(':popover-open')) form.showPopover();
        position();
      } finally {
        busy = false; send.disabled = false; cancel.disabled = false; input.disabled = false;
        send.textContent = 'Send report';
      }
    });
    sharedState._rowDecisionReportEditor = form;
    document.body.appendChild(form);
    btn.setAttribute('aria-expanded', 'true');
    if (form.showPopover) form.showPopover();
    position();
    window.addEventListener('resize', position);
    window.visualViewport?.addEventListener('resize', position);
    window.visualViewport?.addEventListener('scroll', position);
    const savedReport = sharedState._rowDecisionReportPending.get(sid);
    if (savedReport) input.value = savedReport.reporter_note || '';
    input.focus({ preventScroll: true });
  }

  function installConversationInteraction64() {
document.addEventListener('click', async ev => {
    const btn = ev.target.closest && ev.target.closest('[data-role="report-decision"]');
    if (!btn) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    const row = btn.closest('.conv-item[data-session-id]');
    const sid = row && row.dataset.sessionId;
    if (!sid || sharedState._rowDecisionReportSending.has(sid)) return;
    if (sharedState._rowDecisionReportEditor) { sharedState._rowDecisionReportEditor.querySelector('textarea').focus(); return; }
    const base = (0, sharedState._ultraFreshRow)(sid) || { session_id: sid, row_data_unavailable: true };
    // Stale list data is itself reportable; the DOM still anchors the report.
    // Capture the displayed DOM and its data before any async refresh or send.
    const pending = sharedState._rowDecisionReportPending.get(sid);
    const payload = pending || rowDecisionReportSnapshot(row, base);
    if (!payload.submission_id) payload.submission_id = Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('');
    payload.background = true;
    const screenshot = Promise.race([
      captureRowDecisionScreenshot(row, payload.rect).catch(() => null),
      new Promise(resolve => setTimeout(() => resolve(null), 1800)),
    ]);
    let submittedNote = pending ? payload.reporter_note : null;
    openRowDecisionReportEditor(btn, async note => {
      if (submittedNote !== null && note !== submittedNote) {
        throw new Error('This saved report is awaiting filing. Retry its original note, or wait for its receipt before reporting another issue.');
      }
      submittedNote = note;
      payload.reporter_note = note;
      sharedState._rowDecisionReportSending.add(sid);
      btn.disabled = true; btn.setAttribute('aria-busy', 'true');
      try {
        if (!payload.screenshot_b64) payload.screenshot_b64 = await screenshot;
        if (!payload.screenshot_b64) payload.screenshot_warning = 'Browser row screenshot was unavailable; structured row and message evidence were captured.';
        sharedState._rowDecisionReportPending.set(sid, payload);
        const response = await fetch('/api/decisions-fix/report', {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
          signal: AbortSignal.timeout(10000),
        });
        const result = await response.json();
        if (!response.ok || !result.ok || (!result.ref && !result.saved)) throw new Error(result.error || 'Could not save the report');
        if (result.saved && !result.ref) {
          sharedState._rowDecisionReportPending.set(sid, payload);
          (0, sharedState.showOpToast)('Report saved. Filing the ticket in the background…', 'success');
          btn.title = 'Report saved; ticket filing is pending';
          void followRowReportReceipt(result, btn).then(() => {
            // Keep failed/pending receipts available for an explicit same-ID retry.
            if (btn.title.startsWith('Reported as ')) sharedState._rowDecisionReportPending.delete(sid);
          });
          return;
        }
        sharedState._rowDecisionReportPending.delete(sid);
        (0, sharedState.showOpToast)('Decisions Fix: reported as ' + result.ref + (result.screenshot_warning ? ' (screenshot unavailable)' : ''), 'success');
        btn.title = 'Reported as ' + result.ref + '; click to report another mismatch';
      } finally {
        sharedState._rowDecisionReportSending.delete(sid);
        btn.disabled = false; btn.removeAttribute('aria-busy');
      }
    });
  }, true);
  }

  function installConversationInteraction65() {
document.addEventListener('click', async ev => {
    const btn = ev.target.closest && ev.target.closest('[data-role="quick-action"]');
    if (!btn) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    const row = btn.closest('.conv-item[data-session-id]');
    const current = row && (0, sharedState.currentRowQuickActionSource)(row.dataset.sessionId);
    if (!current || current.key !== btn.dataset.quickActionKey) {
      if (row) (0, sharedState.syncRowQuickActions)(row.dataset.sessionId);
      (0, sharedState.showOpToast)('This action changed. Review the updated row before sending.', 'error');
      return;
    }
    const { sid } = current;
    const action = current.actions[Number(btn.dataset.quickActionIndex) || 0] || current.action;
    if (action.href) {
      window.open(action.href, '_blank', 'noopener,noreferrer');
      return;
    }
    const feedback = { key: current.key, action, state: 'sending' };
    sharedState._rowQuickActionFeedback.set(sid, feedback);
    sharedState._rowQuickActionSending.add(sid);
    (0, sharedState.syncRowQuickActions)(sid);
    let accepted = false;
    try {
      if (current.investigation && !await (0, sharedState.checkRowInvestigation)(current, true)) {
        throw new Error('The transcript changed or the agent is already running. Refresh the row.');
      }
      if (current.investigation) {
        const base = (0, sharedState._ultraFreshRow)(sid);
        const latest = base && (0, sharedState.rowQuickActionSource)((0, sharedState._ultraFreshCurrent)(base), true);
        if (!latest || latest.key !== current.key) {
          throw new Error('This action changed. Review the updated row before sending.');
        }
      }
      const ticketAnswer = window.cccQuestionTags?.answerTicket(action.reply, sid);
      if (ticketAnswer) await ticketAnswer;
      const data = ticketAnswer ? { ok: true } : await (0, sharedState.postInjectInput)(sid, action.reply, 'send', { userInitiated: true });
      if (!data.ok) throw new Error(data.error || 'Send failed');
      accepted = true;
      feedback.state = data.queued ? 'queued' : 'sent';
      if (!ticketAnswer) (0, sharedState.markSessionSending)(sid);
      (0, sharedState.showOpToast)((data.queued ? 'Queued: ' : 'Sent: ') + action.label);
      (0, hostServices.refreshConversationList)();
    } catch (error) {
      if (error.ticketAnswered) {
        accepted = true;
        feedback.state = 'sent';
        (0, sharedState.showOpToast)(error.message, 'warning');
      } else if (!accepted) {
        sharedState._rowQuickActionFeedback.delete(sid);
        (0, sharedState.showOpToast)('Could not send ' + action.label + ': ' + String(error.message || error), 'error');
      } else {
        (0, sharedState.showOpToast)('Sent: ' + action.label + '. Refresh the row to see progress.');
      }
    } finally {
      sharedState._rowQuickActionSending.delete(sid);
      (0, sharedState.syncRowQuickActions)(sid);
      if (accepted) setTimeout(() => {
        if (sharedState._rowQuickActionFeedback.get(sid) !== feedback) return;
        sharedState._rowQuickActionFeedback.delete(sid);
        (0, sharedState.syncRowQuickActions)(sid);
      }, 8000);
    }
  }, true);
  }

  function installConversationInteraction66() {
document.addEventListener('click', async ev => {
    const btn = ev.target.closest && ev.target.closest(
      '[data-role="handoff"], [data-role="handoff-answer"], [data-role="handoff-takeback"]');
    if (!btn || !btn.closest('#convList')) return;
    ev.preventDefault(); ev.stopImmediatePropagation();
    const row = btn.closest('.conv-item[data-session-id]');
    const sid = row && row.dataset.sessionId;
    if (!sid || btn.disabled) return;
    const role = btn.dataset.role;
    const body = { session_id: sid };
    let url = '/api/idea-tickets/handoff';
    if (role === 'handoff-takeback') url = '/api/idea-tickets/takeback';
    if (role === 'handoff-answer') {
      const step = row.querySelector('.conv-outcome-step');
      const text = window.prompt((step && step.textContent ? step.textContent + '\n\n' : '') + 'Your answer:');
      if (!text || !text.trim()) return;
      url = '/api/idea-tickets/answer';
      body.text = text.trim();
    }
    btn.disabled = true;
    // The AUTO switch shows its new state immediately, not after the server
    // round trip and list refresh; a failure puts it back.
    const autoSwitch = btn.classList.contains('conv-auto-switch') && role !== 'handoff-answer';
    const autoBefore = autoSwitch ? btn.className : '';
    if (autoSwitch) {
      const on = role === 'handoff';
      sharedState._handoffOptimistic.set(sid, { on, until: Date.now() + 60000 });
      btn.classList.remove('is-on', 'is-off', 'is-paused');
      btn.classList.add(on ? 'is-on' : 'is-off');
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    try {
      const data = await ccPostJson(url, body);
      if (!data || !data.ok) throw new Error((data && data.error) || 'Request failed');
      (0, sharedState.showOpToast)(role === 'handoff' ? 'Takeover accepted for ' + (data.ref || '')
        : role === 'handoff-takeback' ? 'Takeover cancelled for ' + (data.ref || '')
        : 'Answer accepted for delivery');
      (0, hostServices.refreshConversationList)();
    } catch (error) {
      btn.disabled = false;
      if (autoSwitch) {
        // A repaint may have replaced the clicked switch: restore every copy.
        sharedState._handoffOptimistic.delete(sid);
        document.querySelectorAll('#convList .conv-item[data-session-id="' + CSS.escape(sid) + '"] .conv-auto-switch')
          .forEach(el => { el.className = autoBefore; el.setAttribute('aria-pressed', role === 'handoff' ? 'false' : 'true'); });
        btn.className = autoBefore;
      }
      (0, sharedState.showOpToast)(String(error.message || error), 'error');
    }
  }, true);
  }

  function installConversationInteraction67() {
document.addEventListener('click', async (ev) => {
    const btn = ev.target.closest && ev.target.closest('button.ssb-action[data-ssb-act]');
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const act = btn.dataset.ssbAct;
    const value = btn.dataset.ssbValue || '';
    const paneEl = btn.closest('.conv-pane[data-pane-id]');
    const paneId = paneEl ? paneEl.getAttribute('data-pane-id') : (0, sharedState.activePaneId)();
    if (act === 'close') {
      await closeReadyConversation(btn);
    } else if (act === 'copy') {
      const ok = await (0, sharedState.copyTextValue)(value);
      (0, sharedState.showConvToast)(ok ? 'Copied' : 'Copy failed');
    } else if (act === 'reload') {
      location.reload();
    } else if (act === 'restart') {
      if (!confirm('Restart CCC (dashboard + worker) and reload?')) return;
      restartServerRun('/api/restart/all');
    } else if (act === 'reply') {
      const input = (0, sharedState.composerInputForPane)(paneId) || sharedState.$convInput;
      if (input) input.focus();
    } else if (act === 'say') {
      const input = (0, sharedState.composerInputForPane)(paneId) || sharedState.$convInput;
      if (!input) return;
      if (btn.disabled) return;
      const sourcePane = (0, sharedState.paneByPaneId)(paneId);
      const sid = sourcePane?.currentSession?.id || sourcePane?.conversationId;
      const ticketAnswer = window.cccQuestionTags?.answerTicket(value, sid);
      if (ticketAnswer) {
        btn.disabled = true;
        try {
          (0, sharedState.showOpToast)('Answered ' + await ticketAnswer + ': block cleared', 'success');
        } catch (error) {
          btn.disabled = !!error.ticketAnswered;
          (0, sharedState.showOpToast)(error.ticketAnswered ? error.message : 'Answer failed: ' + String(error.message || error),
            error.ticketAnswered ? 'warning' : 'error');
        }
        return;
      }
      const draft = (input.value || '').trim();
      if (draft && draft !== value && !confirm('Replace your draft and send "' + value + '"?')) return;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      (0, sharedState.sendToTerminal)(paneId);
    } else if (act === 'run') {
      if (!confirm('Run this in a new terminal window?\n\n' + value)) return;
      const ctx = _pathLinkSessionContext(btn);
      btn.disabled = true;
      try {
        const res = await fetch('/api/run-in-terminal', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: value, cwd: (ctx && ctx.cwd) || '' }),
        });
        const data = await res.json().catch(() => ({}));
        (0, sharedState.showConvToast)(data.ok ? 'Running in ' + (data.terminal_app || 'Terminal') : 'Run failed: ' + (data.error || res.status));
      } catch (e) {
        (0, sharedState.showConvToast)('Run failed: ' + String(e));
      } finally {
        btn.disabled = false;
      }
    }
  });
  }

  function installConversationInteraction68() {
document.addEventListener('click', (ev) => {
    const btn = ev.target && ev.target.closest && ev.target.closest('.compact-run-retry');
    if (!btn) return;
    ev.preventDefault();
    const card = btn.closest('.compact-run-card');
    const sid = card && card.dataset.sid;
    if (sid && !(sharedState.currentSession && sharedState.currentSession.id === sid)) return;
    if (typeof sharedState.compactCurrentSession === 'function') (0, sharedState.compactCurrentSession)();
  });
  }

  function installConversationInteraction69() {
document.addEventListener('click', (ev) => {
    const toggle = ev.target && ev.target.closest && ev.target.closest('.compact-resume-toggle');
    if (!toggle) return;
    const card = toggle.closest('.compact-resume-card');
    if (!card) return;
    ev.preventDefault();
    const wasCollapsed = card.dataset.collapsed === '1';
    card.dataset.collapsed = wasCollapsed ? '0' : '1';
    toggle.setAttribute('aria-expanded', wasCollapsed ? 'true' : 'false');
    const iconEl = toggle.querySelector('.compact-resume-icon');
    if (iconEl) iconEl.textContent = wasCollapsed ? '▾' : '▸';
    const hintEl = toggle.querySelector('.compact-resume-hint');
    if (hintEl) hintEl.textContent = wasCollapsed ? 'click to collapse' : 'click to expand';
  });
  }

  function installConversationInteraction70() {
document.addEventListener('click', (ev) => {
    const btn = ev.target && ev.target.closest && ev.target.closest('.tn-view-transcript');
    if (!btn) return;
    ev.stopPropagation();
    const sid = (typeof sharedState.currentConversation === 'string' && sharedState.currentConversation) || '';
    const taskId = btn.getAttribute('data-task-id') || '';
    if (!sid || !taskId) return;
    const conv = sid + ':agent-' + taskId;
    window.open('/?ccc_popout=conversation&conv=' + encodeURIComponent(conv), '_blank',
      'width=900,height=800');
  });
  }

  function _bidiMajorityDir(text) {
    if (!text) return null;
    // Word-level, not character-level: a short Hebrew sentence that embeds
    // one long English term/acronym ("App Password", "Google") has more
    // LATIN CHARACTERS than Hebrew ones even though a reader calls it a
    // Hebrew sentence. Counting which SCRIPT each whitespace-delimited word
    // belongs to (majority-vote across words) matches that reading.
    const words = text.split(/\s+/);
    let rtl = 0, ltr = 0;
    for (let i = 0; i < words.length; i++) {
      const w = words[i];
      if (!w) continue;
      if (sharedState._RTL_CHAR_RE.test(w)) rtl++;
      else if (sharedState._LTR_CHAR_RE.test(w)) ltr++;
    }
    if (!rtl && !ltr) return null;
    return rtl >= ltr ? 'rtl' : 'ltr';
  }

  function tagBlocksForRtl(root) {
    if (!root || typeof root.querySelectorAll !== 'function') return;
    const containers = [];
    if (root.matches && root.matches(sharedState.RTL_TARGET_SEL)) containers.push(root);
    root.querySelectorAll(sharedState.RTL_TARGET_SEL).forEach(el => containers.push(el));
    if (!containers.length) return;
    containers.forEach(el => {
      const dir = _bidiMajorityDir(el.textContent) || 'auto';
      el.setAttribute('dir', dir);
      el.querySelectorAll(sharedState.RTL_BLOCK_TAGS).forEach(child => {
        const childDir = _bidiMajorityDir(child.textContent) || 'auto';
        child.setAttribute('dir', childDir);
      });
    });
  }

  function installConversationInteraction71() {
if (window.MutationObserver) {
    // SCOPED observer: only watch the conversation view containers
    // (where assistant/user message HTML actually lives). The earlier
    // version observed document.body, which fired on every sidebar
    // re-render keystroke and starved the input thread — surfaced as
    // "convSearch stopped working". Sidebar mutations no longer
    // trigger any RTL work.
    const _rtlScopeIds = ['conversationsView', 'p1ConvView', 'p2ConvView', 'p3ConvView'];
    const _rtlObserver = new MutationObserver(records => {
      for (const rec of records) {
        rec.addedNodes.forEach(n => {
          if (n.nodeType === 1) {
            tagBlocksForRtl(n);
            // Lazy-render any new mermaid blocks added to the conv
            // view. Fire-and-forget — the loader Promise is cached so
            // repeated calls don't refetch the lib.
            try { _renderMermaidBlocks(n); } catch (_) {}
          }
        });
      }
    });
    const _attachRtlObserver = () => {
      _rtlScopeIds.forEach(id => {
        const el = document.getElementById(id);
        if (el && !el.dataset.rtlObserverAttached) {
          el.dataset.rtlObserverAttached = '1';
          _rtlObserver.observe(el, { childList: true, subtree: true });
          tagBlocksForRtl(el);
          try { _renderMermaidBlocks(el); } catch (_) {}
        }
      });
    };
    _attachRtlObserver();
    // Some conv-view nodes are recreated on pane open / split — re-attach
    // shortly after page load and again periodically (cheap; idempotent
    // via the dataset flag).
    setTimeout(_attachRtlObserver, 500);
    setTimeout(_attachRtlObserver, 2000);
  }
  }

  function _loadMermaid() {
    if (sharedState._mermaidLoadPromise) return sharedState._mermaidLoadPromise;
    sharedState._mermaidLoadPromise = new Promise((resolve, reject) => {
      if (window.mermaid && typeof window.mermaid.render === 'function') {
        try {
          const theme = (document.documentElement.dataset && document.documentElement.dataset.theme === 'light')
            ? 'default' : 'dark';
          window.mermaid.initialize({
            startOnLoad: false,
            securityLevel: 'strict',
            suppressErrorRendering: true,
            theme,
          });
        } catch (_) {}
        resolve(window.mermaid);
        return;
      }
      // Use the UMD build so we don't need module=type loading. jsDelivr
      // has solid uptime; if it ever fails the user just sees the source
      // as a fallback pre, which is honest about the failure.
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js';
      s.async = true;
      s.onload = () => {
        try {
          const theme = (document.documentElement.dataset && document.documentElement.dataset.theme === 'light')
            ? 'default' : 'dark';
          window.mermaid.initialize({
            startOnLoad: false,
            securityLevel: 'strict',
            suppressErrorRendering: true,
            theme,
          });
          resolve(window.mermaid);
        } catch (err) {
          reject(err);
        }
      };
      s.onerror = () => reject(new Error('failed to load mermaid'));
      document.head.appendChild(s);
    });
    return sharedState._mermaidLoadPromise;
  }

  async function _renderMermaidBlocks(root) {
    if (!root || typeof root.querySelectorAll !== 'function') return;
    const pending = [];
    if (root.matches && root.matches('.mermaid-block[data-mermaid-pending]')) pending.push(root);
    root.querySelectorAll('.mermaid-block[data-mermaid-pending]').forEach(n => pending.push(n));
    if (!pending.length) return;
    let mermaid;
    try {
      mermaid = await _loadMermaid();
    } catch (_) {
      // Offline / CDN blocked: leave the fallback pre in place but
      // mark the block done so we don't keep retrying.
      pending.forEach(el => {
        el.removeAttribute('data-mermaid-pending');
        el.setAttribute('data-mermaid-error', 'load-failed');
      });
      return;
    }
    for (const el of pending) {
      const src = (el.querySelector('.mermaid-source') || {}).textContent || '';
      if (!src.trim()) {
        el.removeAttribute('data-mermaid-pending');
        continue;
      }
      el.removeAttribute('data-mermaid-pending');
      el.setAttribute('data-mermaid-rendered', '');
      const id = 'mermaid-svg-' + (++sharedState._mermaidIdSeq);
      try {
        const result = await mermaid.render(id, src);
        // mermaid@10 returns { svg, bindFunctions }. Preserve the head
        // (lang badge + copy button) and replace the source with SVG.
        const head = el.querySelector('.cb-head');
        el.innerHTML = '';
        if (head) el.appendChild(head);
        const svgWrap = document.createElement('div');
        svgWrap.className = 'mermaid-svg';
        svgWrap.innerHTML = result.svg;
        el.appendChild(svgWrap);
        if (typeof result.bindFunctions === 'function') {
          try { result.bindFunctions(svgWrap); } catch (_) {}
        }
      } catch (err) {
        // Clean up any stray error nodes Mermaid mounts directly onto document.body
        const stray = document.getElementById('d' + id) || document.getElementById(id);
        if (stray) stray.remove();
        document.querySelectorAll('body > [id^="dmermaid-svg-"], body > [id^="mermaid-svg-"]').forEach(n => n.remove());

        // Render error — put the source back so the user still sees the
        // diagram intent, with a small error note.
        el.setAttribute('data-mermaid-error', 'render-failed');
        const errNote = document.createElement('div');
        errNote.className = 'mermaid-error';
        errNote.textContent = 'mermaid render failed: ' + (err && err.message || 'unknown');
        el.appendChild(errNote);
      }
    }
  }

  function watchtowerTicketUrl(ref, baseUrl) {
    const ticket = String(ref || '').trim();
    const queue = ticket.replace(/-\d+$/, '');
    const base = String(baseUrl || 'http://127.0.0.1:8787').replace(/\/+$/, '');
    return base + '/q/' + encodeURIComponent(queue) + '#' + encodeURIComponent(ticket);
  }

  function installConversationInteraction72() {
document.addEventListener('click', (ev) => {
    const link = ev.target.closest('a.watchtower-ticket-link');
    if (!link) return;
    ev.preventDefault();
    const ref = link.dataset.watchtowerTicket;
    if (!ref) return;
    // Stay in CCC: the ticket view leads to the worker's session. A modified
    // click still reaches the WatchTower page.
    if (!ev.metaKey && !ev.ctrlKey && !ev.shiftKey && window.cccOpenTicket && window.cccOpenTicket(ref)) return;
    const target = watchtowerTicketUrl(ref, sharedState.watchtowerServiceUrl);
    const tmp = document.createElement('a');
    tmp.href = target;
    tmp.target = '_blank';
    tmp.rel = 'noopener noreferrer';
    tmp.style.display = 'none';
    document.body.appendChild(tmp);
    tmp.click();
    document.body.removeChild(tmp);
  });
  }

  function installConversationInteraction73() {
document.addEventListener('click', (ev) => {
    const link = ev.target.closest('a.session-ref-chip');
    if (!link || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
    ev.preventDefault();
    (0, sharedState.selectConversation)(link.dataset.sessionRef);
  });
  }

  function installConversationInteraction74() {
document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.cb-copy');
    if (!btn) return;
    ev.preventDefault();
    const wrap = btn.closest('.cb-wrap');
    const codeEl = wrap && wrap.querySelector('code');
    const code = codeEl ? codeEl.textContent : '';
    const flash = (ok) => {
      const orig = btn.textContent;
      btn.classList.toggle('copied', ok);
      btn.textContent = ok ? 'Copied' : 'Failed';
      setTimeout(() => { btn.classList.remove('copied'); btn.textContent = orig; }, 1200);
    };
    if (!code) { flash(false); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        navigator.clipboard.writeText(code).then(() => flash(true)).catch(() => flash(false));
      } catch (_) {
        flash(false);
      }
    } else {
      // Fallback for older browsers / insecure contexts: temp textarea + execCommand.
      const ta = document.createElement('textarea');
      ta.value = code; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      let ok = false;
      try { ok = document.execCommand('copy'); } catch (_) {}
      document.body.removeChild(ta);
      flash(ok);
    }
  });
  }

  function installConversationInteraction75() {
window.openMediaLightbox = sharedState.openMediaLightbox;
  }

  function installConversationInteraction76() {
window.closeMediaLightbox = sharedState.closeMediaLightbox;
  }

  function installConversationInteraction77() {
window.addEventListener('resize', () => (0, sharedState.setMediaLightboxZoom)(sharedState._lightboxZoom));
  }

  function installConversationInteraction78() {
window.addEventListener('popstate', (ev) => {
    if (!sharedState._lightboxHistoryPushed) return;
    sharedState._lightboxHistoryPushed = false;
    (0, sharedState.closeMediaLightbox)({ fromPopState: true });
    // Closing media is not an app switch. In particular, the Sessions
    // restore handler hides the current conversation on phones.
    ev.stopImmediatePropagation();
  }, true);
  }

  function installConversationInteraction79() {
document.addEventListener('click', (ev) => {
    const stack = ev.target.closest('.conversation-media-stack');
    const closeGallery = ev.target.closest('.conversation-media-close');
    if (stack || closeGallery) {
      const rail = (stack || closeGallery).closest('.conversation-media-rail');
      const gallery = rail.querySelector('.conversation-media-gallery');
      if (stack) {
        gallery.showPopover();
        gallery.querySelector('.conversation-media-close').focus({ preventScroll: true });
      } else {
        gallery.hidePopover();
        rail.querySelector('.conversation-media-stack').focus({ preventScroll: true });
      }
      return;
    }
    const _openArtifact = (src, opts) => {
      if (typeof sharedState.openArtifactViewer === 'function') {
        (0, sharedState.openArtifactViewer)(src, opts);
      } else {
        (0, sharedState.openMediaLightbox)(src, opts);
      }
    };
    const shotLink = ev.target.closest('a.uxq-td-img-wrap, .ann-ux-preview-shot a');
    if (shotLink && shotLink.getAttribute('href')) {
      ev.preventDefault();
      _openArtifact(shotLink.href, { kind: 'image', title: 'screenshot.png' });
      return;
    }
    // CCC-286: (+) enlarge button on tool-result image thumbnails.
    const zoomBtn = ev.target.closest('.tool-result-image-zoom[data-zoom-src]');
    if (zoomBtn) {
      ev.preventDefault();
      const gallery = zoomBtn.closest('.conversation-media-gallery');
      if (gallery && gallery.matches(':popover-open')) gallery.hidePopover();
      _openArtifact(zoomBtn.getAttribute('data-zoom-src'), { kind: 'image', title: zoomBtn.getAttribute('data-zoom-title') || 'image.png' });
      return;
    }
    const img = ev.target.closest('img.msg-image');
    if (img) {
      ev.preventDefault();
      const gallery = img.closest('.conversation-media-gallery');
      if (gallery && gallery.matches(':popover-open')) gallery.hidePopover();
      _openArtifact(img.dataset.fullSrc || img.src, { kind: 'image', title: img.alt || 'image.png' });
      return;
    }
    const webLink = ev.target.closest('.conversations-view a[href], .conv-sticky-header a[href]');
    if (webLink
        && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey && !ev.altKey && ev.button === 0
        && !webLink.classList.contains('path-link')
        && !webLink.classList.contains('session-ref-chip')
        && !webLink.classList.contains('watchtower-ticket-link')
        && !webLink.hasAttribute('download')
        && typeof sharedState._canUseStatusRailViewer === 'function'
        && (0, sharedState._canUseStatusRailViewer)()) {
      const rawHref = webLink.getAttribute('href') || '';
      if (/^https?:\/\//i.test(rawHref)) {
        let blocked = false;
        try {
          const host = new URL(rawHref).hostname.toLowerCase();
          blocked = /(?:^|\.)(?:github\.com|githubusercontent\.com|docs\.google\.com|drive\.google\.com|mail\.google\.com|calendar\.google\.com|accounts\.google\.com|slack\.com|discord\.com|discord\.gg|x\.com|twitter\.com|linkedin\.com|facebook\.com|instagram\.com|figma\.com|notion\.so|linear\.app)$/i.test(host);
        } catch (_) {}
        if (!blocked) {
          ev.preventDefault();
          const isVid = typeof sharedState._isVideoPath === 'function' && (0, sharedState._isVideoPath)(rawHref);
          const isPdf = typeof sharedState._isPdfPath === 'function' && (0, sharedState._isPdfPath)(rawHref);
          const isImg = !isPdf && typeof sharedState._isImageOrPdfPath === 'function' && (0, sharedState._isImageOrPdfPath)(rawHref);
          const kind = isVid ? 'video' : (isImg ? 'image' : 'frame');
          const label = (webLink.textContent || '').trim() || rawHref.replace(/^https?:\/\//i, '').replace(/\/$/, '');
          _openArtifact(rawHref, { kind, title: label, externalUrl: rawHref });
          return;
        }
      }
    }
  });
  }

  function _lightboxGallery() {
    const out = [];
    document.querySelectorAll('img.msg-image, .tool-result-image-zoom[data-zoom-src], a.uxq-td-img-wrap, .ann-ux-preview-shot a').forEach((el) => {
      let src = '', title = 'image.png';
      if (el.matches('img.msg-image')) { src = el.dataset.fullSrc || el.src; title = el.alt || title; }
      else if (el.matches('.tool-result-image-zoom')) { src = el.getAttribute('data-zoom-src'); title = el.getAttribute('data-zoom-title') || title; }
      else { src = el.getAttribute('href') ? el.href : ''; title = 'screenshot.png'; }
      if (!src) return;
      try { src = new URL(src, window.location.href).href; } catch (_) {}
      if (!out.some((o) => o.src === src)) out.push({ src, title });
    });
    return out;
  }

  function stepMediaLightbox(dir) {
    if (!sharedState._imgLightbox || !sharedState._imgLightbox.classList.contains('open')) return false;
    if (!sharedState._imgLightbox.querySelector('.img-lightbox-stage.is-image')) return false;
    const items = _lightboxGallery();
    let cur = sharedState._imgLightbox.dataset.origSrc || '';
    try { cur = new URL(cur, window.location.href).href; } catch (_) {}
    const i = items.findIndex((o) => o.src === cur);
    if (i < 0 || items.length < 2) return false;
    const n = items[(i + dir + items.length) % items.length];
    (0, sharedState.openMediaLightbox)(n.src, { kind: 'image', title: n.title });
    return true;
  }

  function installConversationInteraction80() {
document.addEventListener('keydown', (ev) => {
    if ((0, sharedState.isImeKey)(ev)) return;
    if (ev.key === 'Escape' && sharedState._imgLightbox && sharedState._imgLightbox.classList.contains('open')) {
      (0, sharedState.closeMediaLightbox)();
      return;
    }
    if (ev.key === 'Escape') {
      const rail = document.getElementById('statusRail');
      const active = document.activeElement;
      const inEditable = active && (active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable);
      if (!inEditable && rail && rail.classList.contains('file-viewer-active') && typeof sharedState.closeStatusRailFileViewer === 'function') {
        (0, sharedState.closeStatusRailFileViewer)();
        return;
      }
    }
    if ((ev.key === 'ArrowRight' || ev.key === 'ArrowLeft') && !ev.metaKey && !ev.ctrlKey && !ev.altKey && !ev.shiftKey
        && sharedState._imgLightbox && sharedState._imgLightbox.classList.contains('open')) {
      if (stepMediaLightbox(ev.key === 'ArrowRight' ? 1 : -1)) ev.preventDefault();
    }
  });
  }

  function _isAbsoluteFilesystemPath(p) {
    const clean = String(p || '').trim().replace(/^file:\/\//i, '').split(/[?#]/)[0];
    return /^\/(?:Users|Volumes|Applications|Library|System|private|tmp|var|etc|opt|usr|bin|sbin|home)(?:\/|$)/.test(clean);
  }

  function _isWebRoutePath(p) {
    if (!p.startsWith('/')) return false;
    if (_isAbsoluteFilesystemPath(p)) return false;
    const last = p.split('/').pop() || '';
    // Strip query/fragment before extension check.
    const cleanLast = last.split(/[?#]/)[0];
    return !/\.[a-zA-Z0-9]{1,8}$/.test(cleanLast);
  }

  function _bareMediaBaseDir(a, p) {
    if (String(p).indexOf('/') !== -1) return '';
    const box = a.closest('.assistant-text') || a.closest('.msg') || a.parentElement;
    if (!box) return '';
    let dir = '';
    const nodes = box.querySelectorAll('a.path-link, code');
    for (const el of nodes) {
      if (el === a || (el.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_PRECEDING)) break;
      const t = (el.getAttribute('data-path') || el.textContent || '').trim();
      if (/^(?:~|\/)[^\s]*\/$/.test(t)) dir = t;
    }
    return dir;
  }

  function _pathLinkSessionContext(el) {
    try {
      const paneEl = el && el.closest ? el.closest('.conv-pane[data-pane-id]') : null;
      const pid = paneEl ? paneEl.getAttribute('data-pane-id') : '';
      if (pid && typeof sharedState.paneByPaneId === 'function') {
        const pane = (0, sharedState.paneByPaneId)(pid);
        if (pane && pane.currentSession) return pane.currentSession;
      }
    } catch (_) {}
    try { return sharedState.currentSession || {}; } catch (_) { return {}; }
  }

  function installConversationInteraction81() {
document.addEventListener('click', async (ev) => {
    const a = ev.target.closest('a.path-link');
    if (!a) return;
    ev.preventDefault();
    ev.stopPropagation();
    const p = a.dataset.path;
    if (!p) return;
    if (_isWebRoutePath(p)) {
      const base = sharedState._vercelDeployUrl || '';  // empty → relative to current origin
      const target = base ? base + p : p;
      if (!ev.metaKey && !ev.ctrlKey && !ev.shiftKey && base
          && typeof sharedState._canUseStatusRailViewer === 'function'
          && (0, sharedState._canUseStatusRailViewer)()
          && typeof sharedState.openArtifactViewer === 'function') {
        (0, sharedState.openArtifactViewer)(target, {
          kind: 'frame',
          title: target.replace(/^https?:\/\//i, ''),
          externalUrl: target,
        });
        return;
      }
      // Synthesise an <a target="_blank"> click instead of window.open().
      // window.open() got eaten by Safari's popup blocker because the call
      // sat behind an async stack; a real anchor click in user-gesture
      // context is honoured every time.
      const tmp = document.createElement('a');
      tmp.href = target;
      tmp.target = '_blank';
      tmp.rel = 'noopener noreferrer';
      tmp.style.display = 'none';
      document.body.appendChild(tmp);
      tmp.click();
      document.body.removeChild(tmp);
      return;
    }
    if ((0, sharedState._isVideoPath)(p) || (0, sharedState._isHtmlDocPath)(p) || (0, sharedState._isImageOrPdfPath)(p)) {
      // Open video/HTML/image/PDF links in the right-hand rail viewer on desktop
      // (falling back to the in-page media lightbox on mobile) so the session
      // stays visible on the left while viewing the artifact on the right.
      const ctx = _pathLinkSessionContext(a);
      (0, sharedState.openFilePathInMediaLightbox)((_bareMediaBaseDir(a, p) || '') + p, '', ctx);
      return;
    }
    if ((0, sharedState._isMarkdownPath)(p)
        && typeof sharedState._canUseStatusRailViewer === 'function'
        && (0, sharedState._canUseStatusRailViewer)()
        && typeof sharedState.loadMarkdownIntoFileViewer === 'function') {
      const ctx = _pathLinkSessionContext(a);
      const cleanPath = String(p).replace(/^file:\/\//i, '').split(/[?#]/)[0].replace(/:\d+(?::\d+)?$/, '');
      let resolvedPath = cleanPath;
      if (!resolvedPath.startsWith('/')) {
        const baseDir = (ctx && (ctx.cwd || ctx.repoPath || ctx.repo_path))
          || (typeof sharedState.activeConvRepoPath === 'function' && (0, sharedState.activeConvRepoPath)())
          || '';
        if (baseDir) resolvedPath = baseDir.replace(/\/+$/, '') + '/' + resolvedPath.replace(/^\.\//, '');
      }
      if (resolvedPath.startsWith('/')) {
        try {
          const cleanName = resolvedPath.split('/').pop() || resolvedPath;
          await (0, sharedState.loadMarkdownIntoFileViewer)(resolvedPath, cleanName);
          return;
        } catch (_) {
          // Fall through to /api/open if the file couldn't be read directly.
        }
      }
    }
    a.style.opacity = '0.5';
    try {
      const ctx = _pathLinkSessionContext(a);
      const payload = { path: p };
      if ((0, sharedState._isMarkdownPath)(p)) payload.launch = true;
      if (ctx && ctx.id) payload.session_id = ctx.id;
      if (ctx && ctx.cwd) payload.cwd = ctx.cwd;
      if (ctx && (ctx.repoPath || ctx.repo_path)) payload.repo_path = ctx.repoPath || ctx.repo_path;
      const res = await fetch('/api/open', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!data.ok && res.status === 501) {
        await handleOpenUnavailable(data, p);
      } else if (!data.ok) {
        const err = data.error || 'open failed';
        a.title = err;
        a.style.color = 'var(--red)';
        // Surface the failure — a brief opacity flicker alone is too easy
        // to miss when nothing happens on disk. The path is included so it's
        // obvious which link refused to open.
        try {
          const detail = err === 'not found' ? 'File not found: ' + p : 'Open failed: ' + err;
          if (typeof sharedState.showConvToast === 'function') (0, sharedState.showConvToast)(detail);
        } catch (_) {}
      }
    } catch (e) {
      a.title = String(e);
      try {
        if (typeof sharedState.showConvToast === 'function') (0, sharedState.showConvToast)('Open failed: ' + String(e));
      } catch (_) {}
    } finally {
      setTimeout(() => { a.style.opacity = ''; }, 600);
    }
  });
  }

  function installConversationInteraction82() {
document.addEventListener('keydown', (ev) => {
    if ((0, sharedState.isImeKey)(ev)) return;
    const a = ev.target.closest && ev.target.closest('a.path-link[role="button"]');
    if (!a) return;
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    a.click();
  });
  }

  async function closeReadyConversation(button) {
    if (!button || button.disabled) return;
    const pane = button.closest('.conv-pane');
    if (pane) (0, sharedState.setActivePaneById)(pane.dataset.paneId);
    if (button.dataset.ssbAct === 'close') {
      const cards = pane && pane.querySelectorAll('.session-state-block');
      if (!cards || button.closest('.session-state-block') !== cards[cards.length - 1]) return;
    }
    const row = (0, sharedState._openConversationRow)();
    if (!(0, sharedState.sessionReadyToClose)(row, (0, sharedState._applyLiveOverlayToRow)(row))
        || (0, sharedState.sessionIsOptimisticallySending)(row && (row.session_id || row.id))) return;
    button.disabled = true;
    try { await _setOpenSessionLifecycle('archive'); }
    finally { button.disabled = false; }
  }

  function returnToConversationList() {
    (0, sharedState.stopConvStream)((0, sharedState.activePaneId)());
    (0, sharedState.stopSpawnStream)();
    (0, sharedState.stopCodexLogPoller)();
    sharedState.currentConversation = null;
    (0, sharedState.setCurrentSession)(null, null, null, false, null);
    (0, sharedState.updatePaneHeader)((0, sharedState.activePaneId)(), null);
    const view = (0, sharedState.getConvView)();
    if (view) view.innerHTML = '<div class="empty-state">Select a conversation</div>';
    try { localStorage.removeItem('ccc-last-view'); } catch (_) {}
    if (sharedState.$mobileBackBtn) sharedState.$mobileBackBtn.click();
    (0, sharedState.mobileShowConv)(false);
    (0, sharedState.mobileShowMain)(false);
    (0, sharedState.updateInputBar)();
    (0, sharedState.saveSplitState)();
  }

  async function _setOpenSessionLifecycle(kind, targetRow) {
    const row = targetRow || (0, sharedState._openConversationRow)();
    if (!row) { (0, sharedState.showOpToast)('No open task', 'error'); return; }
    const convId = row.id;
    const sessionId = row.session_id || row.id;
    const lists = [sharedState.conversationsData, sharedState.archiveData, sharedState.currentRepoBacklogData, sharedState._simpleHistoryRows];
    const applyPatch = (group, patch) => {
      Object.assign(row, patch);
      lists.forEach(rows => {
        if (!Array.isArray(rows)) return;
        for (const r of rows) {
          if (r && (r.id === convId || group.has(r.session_id || r.id))) Object.assign(r, patch);
        }
      });
      for (const member of group) setOptimisticOverride(member, patch);
      (0, sharedState.updateInputBar)();
      (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value), { force: true });
      if ((0, sharedState.isSimpleMode)()) { (0, sharedState._simpleHistoryApply)(); (0, sharedState._simpleHomeRefresh)(); }
    };
    // Move the row now: the POST can queue for seconds behind in-flight polls,
    // and a menu click that changes nothing reads as "it didn't work".
    const prior = { archived: !!row.archived, trashed: !!row.trashed, explicitly_archived: !!(row.explicitly_archived ?? row.idea_ticket?.explicitly_archived) };
    const optimistic = kind === 'untrash' ? { archived: true, trashed: false }
      : (kind === 'archive' ? { archived: true, explicitly_archived: true } : { archived: false, explicitly_archived: false, trashed: false });
    applyPatch(new Set([sessionId]), optimistic);
    (0, sharedState.showOpToast)(kind === 'untrash' ? 'Untrashing…' : (kind === 'archive' ? 'Archiving…' : 'Unarchiving…'));
    try {
      const data = kind === 'untrash'
        ? await ccPostJson('/api/conversations/' + encodeURIComponent(convId) + '/trash', {
          session_id: sessionId, repo_path: (0, sharedState.rowRepoPath)(row) || '', trashed: false,
        })
        : await ccPostJson('/api/conversations/' + encodeURIComponent(convId) + '/archive',
          archivePayloadForRow(row, sessionId, kind === 'archive'));
      if (!data.ok) throw new Error(data.error || (kind + ' failed'));
      const patch = kind === 'untrash'
        ? { archived: !!data.archived, trashed: false }
        : { archived: !!data.archived, explicitly_archived: !!data.archived, ...(!data.archived ? { trashed: false } : {}) };
      applyPatch(new Set(data.session_ids || [sessionId]), patch);
      // A slow archive must not navigate away from another conversation.
      if (kind === 'archive' && data.archived
          && (sharedState.currentConversation === convId || sharedState.currentConversation === sessionId)) {
        returnToConversationList();
      }
      if (kind === 'archive' && data.archived) _showArchivedUndoToast(row);
      else (0, sharedState.showOpToast)(kind === 'untrash' ? 'Untrashed to Archived'
        : (data.archived ? 'Archived' : 'Restored to Active'));
    } catch (err) {
      applyPatch(new Set([sessionId]), prior);
      const label = kind === 'untrash' ? 'Untrash' : (kind === 'archive' ? 'Archive' : 'Unarchive');
      (0, sharedState.showOpToast)(label + ' failed (' + err.message + ')', 'error');
    }
  }

  function _showArchivedUndoToast(row) {
    const title = String(row.display_name || row.first_message || 'session').slice(0, 60);
    (0, sharedState.showOpToast)('Archived ' + (0, sharedState.escapeHtml)(title), 'success', {
      label: 'Undo',
      ttlMs: 8000,
      onClick: () => _setOpenSessionLifecycle('unarchive', row),
    });
  }

  function setOptimisticOverride(sid, patch) {
    if (!sid) return;
    const existing = sharedState._optimisticOverrides.get(sid) || {};
    sharedState._optimisticOverrides.set(sid, Object.assign({}, existing, patch, { ts: Date.now() }));
  }

  function startConvReplay(paneId) {
    const targetPaneId = paneId || (0, sharedState.activePaneId)();
    // Two open panes each showing a real conversation → one Replay drives
    // both, merged into actual chronological order. A single pane (or a
    // split where the other pane is still an empty/new-session slot) just
    // replays the pane that was clicked, as before.
    const paneIds = (sharedState.splitState.orientation && sharedState.splitState.panes.length >= 2
      && sharedState.splitState.panes.every(p => p.conversationId))
      ? sharedState.splitState.panes.map(p => p.id)
      : [targetPaneId];

    const tagged = paneIds.flatMap(pid => (0, sharedState._convReplayTagWithTs)((0, sharedState._convReplayCollectItems)((0, sharedState._convReplayViewForPane)(pid)), pid));
    const meaningful = tagged.filter(item => item.meaningful);
    if (!meaningful.length) return;
    tagged.sort((a, b) => a.ts - b.ts); // stable: ties keep same-pane order

    if (sharedState._convReplayActive) (0, sharedState.exitConvReplayMode)();
    sharedState._convReplayActive = true;
    sharedState._convReplayPaneId = targetPaneId;
    sharedState._convReplayPaneIds = paneIds;
    sharedState._convReplayPaused = false;
    sharedState._convReplaySpeed = 1;
    sharedState._convReplayMsgIndex = 0;
    sharedState._convReplaySeenEls = new WeakSet();
    sharedState._convReplayEvents = tagged;
    (0, sharedState._convReplayClearWordReveal)();
    sharedState._convReplayHumanTypeIndex = 0;
    sharedState._convReplayHumanSendPending = false;
    sharedState._convReplayHumanSent = false;
    tagged.forEach(item => { sharedState._convReplaySeenEls.add(item.el); item.el.classList.add('conv-replay-hidden'); });
    if (typeof sharedState._orchReplayStep === 'function') (0, sharedState._orchReplayStep)(null);

    let controls = document.getElementById('convReplayControls');
    if (!controls) {
      controls = document.createElement('div');
      controls.id = 'convReplayControls';
      controls.className = 'gc-replay-controls conv-replay-controls';
      if (paneIds.length > 1) {
        // Synced two-pane replay: mount above the whole split, not inside
        // either pane, so one control visibly drives both transcripts.
        const $split = document.getElementById('convSplit');
        $split.parentNode.insertBefore(controls, $split);
      } else {
        const $view = (0, sharedState._convReplayViewForPane)(targetPaneId);
        $view.parentNode.insertBefore(controls, $view);
      }
    }
    (0, sharedState.renderConvReplayControls)();

    const btn = document.getElementById('convReplayBtn');
    if (btn) btn.style.display = 'none';

    sharedState._onConvReplayKeyRef = (e) => {
      if (!sharedState._convReplayActive) return;
      if ((0, sharedState.isImeKey)(e)) return;
      // Typing a line number into the jump-to-line input: this listener is
      // capture-phase on `document`, so it fires before the input's own
      // keydown handler — without this guard, Space/Escape while typing "172"
      // would step the replay or exit it instead of reaching the input.
      if (e.target && e.target.classList && e.target.classList.contains('conv-replay-jump-input')) return;
      if (e.key === 'Escape') { e.preventDefault(); (0, sharedState.exitConvReplayMode)(); return; }
      if (e.key === ' ' || e.key === 'Spacebar') {
        e.preventDefault();
        if (sharedState._convReplayTimeout) { clearTimeout(sharedState._convReplayTimeout); sharedState._convReplayTimeout = null; }
        _advanceConvReplayToNextMeaningful();
      }
    };
    document.addEventListener('keydown', sharedState._onConvReplayKeyRef, true);

    // The replay's own "human is typing" illusion sets .value directly,
    // which never fires a native 'input' event — so any 'input' event that
    // reaches a real composer during replay is genuine user typing. Treat it
    // as "I want to take over" and stop the replay rather than fighting the
    // user for control of the box.
    sharedState._onConvReplayInputRef = (e) => {
      if (!sharedState._convReplayActive) return;
      const t = e.target;
      if (!t || !t.closest) return;
      if (t.classList && t.classList.contains('conv-replay-jump-input')) return;
      if (t.closest('.conv-input-bar')) (0, sharedState.exitConvReplayMode)();
    };
    document.addEventListener('input', sharedState._onConvReplayInputRef, true);

    const runId = ++sharedState._convReplayRunId;
    sharedState._convReplayTypedLoading = true;
    _convReplayLoadTypedTurns(paneIds).then(() => {
      if (runId !== sharedState._convReplayRunId) return;
      sharedState._convReplayTypedLoading = false;
      if (sharedState._convReplayActive && !sharedState._convReplayPaused && !sharedState._convReplayTimeout) (0, sharedState.playNextConvReplayStep)();
    });
  }

  function _advanceConvReplayToNextMeaningful() {
    // Space is an explicit manual step — finish whatever message is
    // mid-type-in instantly rather than leaving it half-revealed while we
    // jump to the next one.
    if (sharedState._convReplayWordItem) {
      (0, sharedState._convReplayRevealAllWordsInstantly)(sharedState._convReplayWordContainer);
      (0, sharedState._convReplayClearWordReveal)();
    }
    // Also bail out of a "human is typing" animation that might be
    // mid-flight for the CURRENT item — otherwise a stray draft is left in
    // the composer and the next user turn resumes from the wrong index.
    const _pendingItem = sharedState._convReplayEvents[sharedState._convReplayMsgIndex];
    (0, sharedState._convReplayResetHumanTyping)(_pendingItem ? _pendingItem.paneId : null);
    while (sharedState._convReplayMsgIndex < sharedState._convReplayEvents.length) {
      const item = sharedState._convReplayEvents[sharedState._convReplayMsgIndex];
      item.el.classList.remove('conv-replay-hidden');
      sharedState._convReplayMsgIndex++;
      if (typeof sharedState._orchReplayStep === 'function') (0, sharedState._orchReplayStep)(item);
      if (item.meaningful) {
        (0, sharedState._convReplayRevealAllWordsInstantly)((0, sharedState._convReplayPrepareWordsForItem)(item));
        (0, sharedState._scrollConvReplayToBottom)(item.paneId);
        if (!sharedState._convReplayPaused) {
          sharedState._convReplayTimeout = setTimeout(() => { sharedState._convReplayTimeout = null; (0, sharedState.playNextConvReplayStep)(); }, 800 / sharedState._convReplaySpeed);
        }
        return;
      }
    }
    (0, sharedState.exitConvReplayMode)();
  }

  function _convReplayLoadTypedTurns(paneIds) {
    sharedState._convReplayTypedByPane = {};
    const loads = paneIds.map(pid => {
      const cid = (0, sharedState.presentationConversationIdForPane)(pid);
      if (!cid) return Promise.resolve();
      return fetch('/api/conversations/' + encodeURIComponent(cid) + '/typed-turns', { cache: 'no-store' })
        .then(r => (r.ok ? r.json() : null))
        .then(data => {
          if (data && data.ok && Array.isArray(data.typed_lines)) {
            sharedState._convReplayTypedByPane[pid] = { lines: new Set(data.typed_lines.map(Number)), evidence: !!data.evidence };
          }
        })
        .catch(() => {});
    });
    // Never hold the replay hostage to a slow server: fall back to the shape
    // check after a short wait.
    return Promise.race([Promise.all(loads), new Promise(res => setTimeout(res, 2500))]);
  }

  async function ccPostJson(url, body) {
    const res = await fetch(url, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(res.status + ' ' + (text || res.statusText));
    }
    try { return await res.json(); } catch (_) { return {}; }
  }

  function archivePayloadForRow(row, sessionId, archived) {
    const payload = { session_id: sessionId };
    if (typeof archived === 'boolean') payload.archived = archived;
    return withRepoPath(payload, (0, sharedState.rowRepoPath)(row));
  }

  async function handleOpenUnavailable(data, fallbackPath) {
    const p = (data && data.path) || fallbackPath || '';
    if (!p) return;
    if (typeof sharedState._canUseStatusRailViewer === 'function' && (0, sharedState._canUseStatusRailViewer)()) {
      const cleanName = String(p).split(/[?#]/)[0].split('/').pop() || p;
      if ((0, sharedState._isMarkdownPath)(p) && typeof sharedState.loadMarkdownIntoFileViewer === 'function') {
        try {
          await (0, sharedState.loadMarkdownIntoFileViewer)(p, cleanName);
          return;
        } catch (_) {}
      }
      if ((0, sharedState._isVideoPath)(p) || (0, sharedState._isHtmlDocPath)(p) || (0, sharedState._isImageOrPdfPath)(p)) {
        (0, sharedState.openFilePathInMediaLightbox)(p, cleanName, sharedState.currentSession);
        return;
      }
      try {
        const res = await fetch('/api/fs/browse?path=' + encodeURIComponent(p), { cache: 'no-store', cccPriority: 'user' });
        const d = await res.json();
        if (d && d.ok && d.selected && Array.isArray(d.files)) {
          const picked = d.files.find(x => x.name === d.selected);
          if (picked) {
            if (picked.category === 'markdown' && typeof sharedState.loadMarkdownIntoFileViewer === 'function') {
              await (0, sharedState.loadMarkdownIntoFileViewer)(picked.path, picked.name);
              return;
            }
            if (picked.category === 'images' || picked.category === 'videos' || picked.category === 'pdfs' || picked.category === 'html') {
              (0, sharedState.openFilePathInMediaLightbox)(picked.path, picked.name, sharedState.currentSession);
              return;
            }
            if (picked.category === 'text' && typeof sharedState.loadTextIntoFileViewer === 'function') {
              const u = new URL('/api/media', window.location.origin);
              u.searchParams.set('path', picked.path);
              const repo = d.media_repo_path || (typeof sharedState.activeConvRepoPath === 'function' && (0, sharedState.activeConvRepoPath)()) || '';
              if (repo) u.searchParams.set('repo_path', repo);
              await (0, sharedState.loadTextIntoFileViewer)(u.href, picked.name, picked.path);
              return;
            }
          }
        }
      } catch (_) {}
    }
    openFolderViewer(p);
  }

  function _ffvSize(n) {
    if (n < 1024) return n + ' B';
    if (n < 1048576) return Math.round(n / 1024) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  async function openFolderViewer(startPath) {
    let root = document.getElementById('ffvOverlay');
    if (root) root.remove();
    root = document.createElement('div');
    root.id = 'ffvOverlay';
    root.className = 'ffv-overlay';
    root.innerHTML = '<div class="ffv" role="dialog" aria-label="Folder viewer">'
      + '<div class="ffv-bar"><button type="button" class="ffv-btn" data-ffv="up">\u2191 Up</button>'
      + '<span class="ffv-crumb"></span>'
      + '<button type="button" class="ffv-btn" data-ffv="copy">Copy path</button>'
      + '<button type="button" class="ffv-btn" data-ffv="close" aria-label="Close">\u2715</button></div>'
      + '<div class="ffv-body"><div class="ffv-list"></div><div class="ffv-view"></div></div></div>';
    document.body.appendChild(root);
    const $crumb = root.querySelector('.ffv-crumb');
    const $list = root.querySelector('.ffv-list');
    const $view = root.querySelector('.ffv-view');
    let cur = { path: startPath, parent: null };
    let repo = (typeof sharedState.activeConvRepoPath === 'function' && (0, sharedState.activeConvRepoPath)()) || '';
    const mediaUrl = (fp) => {
      const u = new URL('/api/media', window.location.origin);
      u.searchParams.set('path', fp);
      if (repo) u.searchParams.set('repo_path', repo);
      return u.href;
    };
    const close = () => { root.remove(); document.removeEventListener('keydown', onKey); };
    const onKey = (ev) => { if (ev.key === 'Escape') close(); };
    document.addEventListener('keydown', onKey);

    function preview(f) {
      const url = mediaUrl(f.path);
      let inner;
      if (f.category === 'images') inner = '<img src="' + (0, sharedState.escapeAttr)((0, sharedState._mediaPreviewUrl)(url) || url) + '" alt="' + (0, sharedState.escapeAttr)(f.name) + '">';
      else if (f.category === 'videos') inner = '<video src="' + (0, sharedState.escapeAttr)(url) + '" controls></video>';
      else if (f.category === 'markdown' || f.category === 'text') inner = '<div class="ffv-md tp-body">Loading\u2026</div>';
      else if (f.category === 'pdfs' || f.category === 'html') inner = '<iframe src="' + (0, sharedState.escapeAttr)(url) + '"></iframe>';
      else inner = '<div class="ffv-nopreview">No inline preview for this file type.</div>';
      $view.innerHTML = '<div class="ffv-pv-head"><span>' + (0, sharedState.escapeHtml)(f.name) + '</span>'
        + '<a class="ffv-btn" href="' + (0, sharedState.escapeAttr)(url) + '" data-ffv-fullscreen="' + (0, sharedState.escapeAttr)(f.category || '') + '" data-ffv-name="' + (0, sharedState.escapeAttr)(f.name) + '">Full screen</a></div>' + inner;
      const $fs = $view.querySelector('[data-ffv-fullscreen]');
      if ($fs) {
        $fs.addEventListener('click', (ev) => {
          ev.preventDefault();
          const cat = $fs.getAttribute('data-ffv-fullscreen');
          const kind = cat === 'videos' ? 'video' : (cat === 'pdfs' || cat === 'html' || cat === 'text' || cat === 'markdown') ? 'frame' : 'image';
          (0, sharedState.openMediaLightbox)(url, { kind, title: f.name, forceModal: true });
        });
      }
      $list.querySelectorAll('.ffv-row').forEach(r => r.classList.toggle('sel', r.getAttribute('data-path') === f.path));
      if (f.category === 'markdown' || f.category === 'text') {
        fetch(url, { cache: 'no-store', cccPriority: 'user' }).then(r => r.ok ? r.text() : Promise.reject(new Error('HTTP ' + r.status))).then(t => {
          const el = $view.querySelector('.ffv-md');
          if (!el || $list.querySelector('.ffv-row.sel')?.getAttribute('data-path') !== f.path) return;
          if (f.category === 'text') {
            const pre = document.createElement('pre');
            pre.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere;margin:0';
            pre.textContent = t;
            el.replaceChildren(pre);
          } else el.innerHTML = window.CCCTicketProse ? window.CCCTicketProse.render(t) : '<pre>' + (0, sharedState.escapeHtml)(t) + '</pre>';
        }).catch(e => {
          const el = $view.querySelector('.ffv-md');
          if (el && $list.querySelector('.ffv-row.sel')?.getAttribute('data-path') === f.path) el.textContent = 'Could not load: ' + e.message;
        });
      }
    }

    // Movable + resizable window: drag the title bar; resize via the corner
    // grip (CSS resize). Geometry persists across opens.
    const $win = root.querySelector('.ffv');
    try {
      const g = JSON.parse(localStorage.getItem('ccc-ffv-geom') || 'null');
      if (g && g.w && g.h) {
        const w = Math.min(g.w, window.innerWidth - 20), h = Math.min(g.h, window.innerHeight - 20);
        $win.style.width = w + 'px'; $win.style.height = h + 'px';
        $win.style.left = Math.max(0, Math.min(g.x, window.innerWidth - 80)) + 'px';
        $win.style.top = Math.max(0, Math.min(g.y, window.innerHeight - 40)) + 'px';
        $win.style.margin = '0';
      }
    } catch (e) {}
    const saveGeom = () => {
      const r = $win.getBoundingClientRect();
      try { localStorage.setItem('ccc-ffv-geom', JSON.stringify({ x: r.left, y: r.top, w: r.width, h: r.height })); } catch (e) {}
    };
    root.querySelector('.ffv-bar').addEventListener('pointerdown', (ev) => {
      if (ev.target.closest('button, a') || ev.button !== 0) return;
      const r = $win.getBoundingClientRect();
      const dx = ev.clientX - r.left, dy = ev.clientY - r.top;
      $win.style.width = r.width + 'px'; $win.style.height = r.height + 'px';
      $win.style.margin = '0';
      $win.style.left = r.left + 'px'; $win.style.top = r.top + 'px';
      const bar = ev.currentTarget;
      bar.setPointerCapture(ev.pointerId);
      const mv = (e) => {
        $win.style.left = Math.max(0, Math.min(e.clientX - dx, window.innerWidth - 80)) + 'px';
        $win.style.top = Math.max(0, Math.min(e.clientY - dy, window.innerHeight - 40)) + 'px';
      };
      const up = () => { bar.removeEventListener('pointermove', mv); bar.removeEventListener('pointerup', up); saveGeom(); };
      bar.addEventListener('pointermove', mv);
      bar.addEventListener('pointerup', up);
    });
    if (window.ResizeObserver) new ResizeObserver(() => { if ($win.style.margin === '0px') saveGeom(); }).observe($win);

    async function load(path, selected) {
      $list.innerHTML = '<div class="ffv-msg">Loading\u2026</div>';
      let d;
      try {
        const res = await fetch('/api/fs/browse?path=' + encodeURIComponent(path), { cache: 'no-store', cccPriority: 'user' });
        d = await res.json();
      } catch (e) { d = { ok: false, error: String(e) }; }
      if (!d.ok) { $list.innerHTML = '<div class="ffv-msg">' + (0, sharedState.escapeHtml)(d.error || 'failed') + '</div>'; return; }
      cur = d;
      if (!repo) repo = d.media_repo_path || '';
      $crumb.textContent = d.path;
      const rows = [];
      d.dirs.forEach(x => rows.push('<div class="ffv-row" data-dir="1" data-path="' + (0, sharedState.escapeAttr)(x.path) + '">\uD83D\uDCC1 ' + (0, sharedState.escapeHtml)(x.name) + '</div>'));
      d.files.forEach(x => rows.push('<div class="ffv-row" data-path="' + (0, sharedState.escapeAttr)(x.path) + '">'
        + (sharedState._FFV_ICON[x.category] || '\uD83D\uDCC3') + ' <span class="ffv-name">' + (0, sharedState.escapeHtml)(x.name) + '</span><small>' + _ffvSize(x.size) + '</small></div>'));
      $list.innerHTML = rows.join('') || '<div class="ffv-msg">Empty folder</div>';
      const pick = d.files.find(x => x.name === (selected || d.selected))
        || d.files.find(x => x.category === 'images');
      if (pick) preview(pick);
      else $view.innerHTML = '<div class="ffv-nopreview">Select a file to preview it.</div>';
      if (d.truncated) $list.insertAdjacentHTML('beforeend', '<div class="ffv-msg">Listing truncated.</div>');
    }

    root.addEventListener('click', async (ev) => {
      const act = ev.target.closest('[data-ffv]');
      if (act) {
        const a = act.getAttribute('data-ffv');
        if (a === 'close') close();
        else if (a === 'up' && cur.parent) load(cur.parent);
        else if (a === 'copy') {
          const ok = await (0, sharedState.copyTextValue)(cur.path);
          act.textContent = ok ? 'Copied \u2713' : 'Copy failed';
          setTimeout(() => { act.textContent = 'Copy path'; }, 1500);
        }
        return;
      }
      const row = ev.target.closest('.ffv-row');
      if (!row) return;
      const rp = row.getAttribute('data-path');
      if (row.getAttribute('data-dir')) return load(rp);
      const f = cur.files.find(x => x.path === rp);
      if (f) preview(f);
    });
    load(startPath);
  }

  function steeredToastText(data) {
    const relayed = subagentRelayToastText(data);
    if (relayed) return relayed;
    const via = String((data && data.via) || '');
    if (via === 'codex-steer') return 'Steered running Codex turn.';
    if (via.indexOf('acp') === 0) return 'Steered - the running turn was interrupted to take it.';
    return 'Steered.';
  }

  function subagentRelayToastText(data) {
    if (!data || !data.relayed_via_parent) return '';
    const agent = data.relay_agent_path || 'this sub-agent';
    return 'Sent to ' + agent + ' through its parent agent (Codex sub-agents only take input that way).';
  }

  function repoPathForConversationPopout(convId, explicitRepoPath) {
    const row = (0, sharedState.rowForConversationId)(convId);
    return explicitRepoPath || (0, sharedState.rowRepoPath)(row) || (0, sharedState.popoutRepoPath)() || '';
  }

  function conversationPopoutUrl(convId, repoPath) {
    const u = new URL(window.location.pathname || '/', window.location.href);
    const row = (0, sharedState.rowForConversationId)(convId);
    const rowRepo = (0, sharedState.rowRepoPath)(row) || repoPath || (0, sharedState.popoutRepoPath)() || '';
    const addParam = (key, value, maxLen) => {
      if (value === undefined || value === null || value === '') return;
      const s = String(value);
      u.searchParams.set(key, maxLen ? s.slice(0, maxLen) : s);
    };
    u.search = '';
    u.hash = '';
    u.searchParams.set('ccc_popout', 'conversation');
    u.searchParams.set('conv', convId);
    if (rowRepo) u.searchParams.set('repo_path', rowRepo);
    if (row) {
      const source = row.source || '';
      addParam('source', source, 40);
      // A CCC-spawned Codex session is a registry row with source
      // 'interactive' and engine 'codex'; the popout decides queued-card
      // Steer from the engine, so it must travel with the source.
      addParam('engine', row.engine || '', 40);
      addParam('session_id', row.session_id || (source === 'backlog' ? '' : convId), 120);
      addParam('title', (0, sharedState.paneTitleForRow)(row), 180);
      addParam('status_rail_title', row.status_rail_title || '', 500);
      addParam('category', (0, sharedState.paneCategoryForRow)(row), 180);
      addParam('folder_label', row.folder_label_chip || row.folder_label || '', 120);
      addParam('cwd', row.session_cwd || row.cwd || '', 500);
      if (row.session_cwd_exists) addParam('cwd_exists', '1');
      addParam('spawn_pid', row.spawn_pid, 20);
      addParam('issue_number', row.issue_number, 20);
    }
    return u.toString();
  }

  function popoutPositionFeature(anchor, width, height) {
    if (!anchor) return '';
    const left = Math.round(anchor.x - (width / 2));
    const top = Math.round(anchor.y - 80);
    return ',left=' + left + ',top=' + top + ',screenX=' + left + ',screenY=' + top;
  }

  function tryOpenNativeMacPopout(url, opts) {
    const bridge = window.webkit
      && window.webkit.messageHandlers
      && window.webkit.messageHandlers.cccNative;
    if (!bridge || typeof bridge.postMessage !== 'function') return false;
    try {
      bridge.postMessage({ action: 'openPopout', url: url });
      (0, sharedState.showOpToast)((opts && opts.successToast) || 'Conversation opened in a new window');
      return true;
    } catch (_) {
      return false;
    }
  }

  function openConversationPopout(convId, repoPath, anchor) {
    if (!convId) return false;
    const row = (0, sharedState.rowForConversationId)(convId);
    if (row && row.source === 'github_pr') {
      if (row.tail_pr_url) {
        const popup = window.open(row.tail_pr_url, '_blank', 'noopener');
        if (!popup) {
          fetch('/api/open-browser', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: row.tail_pr_url })
          });
        }
      }
      return true;
    }
    const url = conversationPopoutUrl(convId, repoPathForConversationPopout(convId, repoPath));
    const name = 'ccc-conversation-' + String(convId).replace(/[^a-zA-Z0-9_-]/g, '').slice(0, 48);
    const width = 920;
    const height = 900;
    const features = 'popup=yes,width=' + width + ',height=' + height
      + ',menubar=no,toolbar=no,location=no,status=no,scrollbars=yes,resizable=yes'
      + popoutPositionFeature(anchor, width, height);
    if ((0, sharedState.isCccMacApp)()) {
      const popup = window.open(url, name, features);
      if (popup) {
        try { popup.focus(); } catch (_) {}
        (0, sharedState.showOpToast)('Conversation opened in a new window');
        return true;
      }
      if (tryOpenNativeMacPopout(url)) return true;
      fetch('/api/open-browser', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url })
      }).then(r => r.json()).then(data => {
        if (data.ok) {
          (0, sharedState.showOpToast)(data.via === 'ccc-app'
            ? 'Conversation opened in a new window'
            : 'Conversation opened in external browser');
        } else {
          (0, sharedState.showOpToast)('Could not open a new Command Center window', 'error');
        }
      }).catch(() => {
        (0, sharedState.showOpToast)('Could not open a new Command Center window', 'error');
      });
      return false;
    }
    const popup = window.open(url, name, features);
    if (popup) {
      try { popup.focus(); } catch (_) {}
      (0, sharedState.showOpToast)('Conversation opened in a pop-up');
      return true;
    }
    fetch('/api/open-browser', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    }).then(r => r.json()).then(data => {
      if (data.ok) (0, sharedState.showOpToast)('Conversation opened in external browser');
      else (0, sharedState.showOpToast)('Pop-up blocked. Allow pop-ups for CCC.', 'error');
    }).catch(err => {
      (0, sharedState.showOpToast)('Pop-up blocked. Allow pop-ups for CCC.', 'error');
    });
    return false;
  }

  function _sysServiceMap(payload) {
    const map = new Map();
    const rows = (payload && Array.isArray(payload.services)) ? payload.services : [];
    for (const svc of rows) {
      if (svc && svc.id) map.set(String(svc.id), svc);
    }
    return map;
  }

  function trayConversationView(tray) {
    const pane = tray && tray.closest('.conv-pane');
    return (pane && pane.querySelector('.conversations-view'))
      || (0, sharedState.getConvViewForPane)((0, sharedState.activePaneId)())
      || (0, sharedState.getConvView)();
  }

  function steerTextKeys(texts) {
    return (texts || []).map(t => (0, sharedState._normSend)(String(t || ''))).filter(Boolean);
  }

  function markSteerInFlight(sid, texts) {
    if (!sid) return;
    let set = sharedState._steerInFlightTexts.get(sid);
    if (!set) { set = new Set(); sharedState._steerInFlightTexts.set(sid, set); }
    steerTextKeys(texts).forEach(key => set.add(key));
  }

  function clearSteerInFlight(sid, texts) {
    const set = sid && sharedState._steerInFlightTexts.get(sid);
    if (!set) return;
    steerTextKeys(texts).forEach(key => set.delete(key));
    if (!set.size) sharedState._steerInFlightTexts.delete(sid);
  }

  function setQueuedSteerError(sid, texts, message) {
    (Array.isArray(texts) ? texts : [texts]).forEach(text => {
      const key = (0, sharedState.queuedSteerErrorKey)(sid, text);
      if (key) sharedState._queuedSteerErrors.set(key, String(message || 'Steer failed'));
    });
  }

  function clearQueuedSteerError(sid, texts) {
    (Array.isArray(texts) ? texts : [texts]).forEach(text => {
      const key = (0, sharedState.queuedSteerErrorKey)(sid, text);
      if (key) sharedState._queuedSteerErrors.delete(key);
    });
  }

  function settleSteeredServerCard(row) {
    if (!row) return;
    row.querySelector('.steer-background-note')?.remove();
    row.dataset.steerSettled = '1';
    delete row.dataset.queuedSteerServer;
    row.classList.remove('pending', 'server-queued', 'send-queued');
    row.classList.add('send-delivered');
    const note = row.querySelector('.send-queued-note');
    if (note) note.remove();
    row.querySelector('.queued-steer-actions')?.remove();
  }

  function revertOptimisticSteerMove(moved, paneId) {
    (moved || []).forEach(row => {
      if (!row) return;
      row.querySelector('.steer-background-note')?.remove();
      row.classList.remove('steering-optimistic');
      row.classList.add('send-queued');
      const note = row.querySelector('.send-queued-note');
      if (note) note.hidden = false;
      const actions = row.querySelector('.queued-steer-actions');
      if (actions) actions.hidden = false;
    });
    // Re-running the sync is the whole revert: it re-collects every
    // `.send-queued` row back into the tray, rebuilding it if it was dropped
    // for being empty.
    try {
      const pid = paneId || (0, sharedState.activePaneId)();
      (0, sharedState.syncQueuedSteerTray)((0, sharedState.getConvViewForPane)(pid) || (0, sharedState.getConvView)(), pid, false);
    } catch (_) {}
  }

  function _codexRequestResultForDecision(method, decision, extra) {
    method = String(method || '');
    extra = extra || {};
    if (/requestUserInput/i.test(method)) {
      if (decision === 'cancel') return { answers: {} };
      return { answers: extra.answers || {} };
    }
    if (/permission/i.test(method) && !/approval|requestApproval/i.test(method)) {
      if (decision === 'decline' || decision === 'cancel') return { permissions: {}, scope: 'turn' };
      return { permissions: extra.permissions || {}, scope: 'turn' };
    }
    if (/elicitation/i.test(method)) {
      return { action: decision === 'accept' ? 'accept' : (decision === 'cancel' ? 'cancel' : 'decline') };
    }
    return { decision: decision || 'cancel' };
  }

  function sysSay(text) {
    if (sharedState.$sysLiveRegion) sharedState.$sysLiveRegion.textContent = text || '';
  }

  function sysRowSay(id, text, kind) {
    const ids = sharedState.SYS_ROWS[id];
    if (!ids) return;
    const el = document.getElementById(ids.result);
    if (!el) return;
    el.hidden = !text;
    el.textContent = text || '';
    el.className = 'sys-svc-result' + (kind ? ' is-' + kind : '');
  }

  function sysRestartBlockReason(id, svc) {
    if (!svc) return 'Still checking this service.';
    if (sharedState._sysArmedRestart && sharedState._sysArmedRestart !== id) {
      return 'Another restart is about to start.';
    }
    if (sharedState._sysArmedRestart === id) {
      return 'Restarting in a moment. Choose Undo to cancel.';
    }
    if (sharedState._sysRestartInFlight) return 'Another restart is in progress.';
    if (svc.restart_blocking && id === 'dashboard') {
      return 'Waiting for the running Kimi turn to finish.';
    }
    if (svc.restart_blocking && id === 'watchtower') {
      return 'CCC cannot verify the WatchTower process identity, so it will '
        + 'not restart it. Restart it from the terminal with wt restart.';
    }
    return '';
  }

  function sysPaintRestartButton(id, svc) {
    const ids = sharedState.SYS_ROWS[id];
    const btn = ids && document.getElementById(ids.btn);
    if (!btn) return;
    if (id === 'watchtower' && svc) {
      // Not installed is not a failure anybody fixes from here.
      btn.hidden = svc.installed === false;
    }
    if (sharedState._sysRestartInFlight === id) {
      btn.disabled = true;
      btn.textContent = 'Restarting…';
      btn.title = 'Restart in progress.';
      return;
    }
    const why = sysRestartBlockReason(id, svc);
    btn.disabled = !!why;
    btn.title = why;
    btn.textContent = (id === 'watchtower' && svc && svc.state === 'offline')
      ? 'Start' : 'Restart';
  }

  function _sysFocusables() {
    if (!sharedState.$sysDialog) return [];
    return Array.prototype.filter.call(
      sharedState.$sysDialog.querySelectorAll(
        'button:not([disabled]):not([hidden]), summary, [href], [tabindex]:not([tabindex="-1"])'
      ),
      el => {
        if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden') return false;
        const closed = el.closest('details:not([open])');
        return !closed || el === closed.querySelector(':scope > summary');
      }
    );
  }

  function _sysTrapTab(e) {
    if ((0, sharedState.isImeKey)(e)) return;
    if (e.key !== 'Tab') return;
    if (!sharedState.$sysModal || !sharedState.$sysModal.classList.contains('open')) return;
    const items = _sysFocusables();
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  function sysCloseModal() {
    if (!sharedState.$sysModal) return;
    // An armed dashboard restart is cancelled by closing the panel: nothing
    // that reloads the page should survive the dialog that offered the Undo.
    if (typeof sysCancelDashArm === 'function') sysCancelDashArm(true);
    sharedState.$sysModal.classList.remove('open');
    if (sharedState.$sysChip) sharedState.$sysChip.setAttribute('aria-expanded', 'false');
    if (sharedState._sysPollTimer) { clearInterval(sharedState._sysPollTimer); sharedState._sysPollTimer = null; }
    if (sharedState._sysSpawnedTickTimer) { clearInterval(sharedState._sysSpawnedTickTimer); sharedState._sysSpawnedTickTimer = null; }
    if (sharedState._sysHealthTimer) { clearInterval(sharedState._sysHealthTimer); sharedState._sysHealthTimer = null; }
    if (sharedState._sysProcessesTimer) { clearInterval(sharedState._sysProcessesTimer); sharedState._sysProcessesTimer = null; }
    if (sharedState._sysJobsTimer) { clearInterval(sharedState._sysJobsTimer); sharedState._sysJobsTimer = null; }
    if (sharedState.$sysDialog) sharedState.$sysDialog.classList.remove('is-wide');
    document.removeEventListener('keydown', _sysTrapTab, true);
    const back = [sharedState._sysReturnFocus, sharedState.$sysChip, document.querySelector('[data-mobile-nav="more"]'),
      document.querySelector('[data-mobile-nav-menu]'), document.getElementById('sidebarMoreBtn')]
      .find(element => {
        if (!element?.isConnected || element.disabled || !element.getClientRects().length || getComputedStyle(element).visibility === 'hidden') return false;
        const rect = element.getBoundingClientRect();
        const top = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return top === element || element.contains(top);
      });
    if (back) { try { back.focus(); } catch (_) {} }
    sharedState._sysReturnFocus = null;
  }

  function sysApplyRestartLocks() {
    const busy = !!(sharedState._sysRestartInFlight || sharedState._sysArmedRestart);
    if (sharedState.$sysRestartAllBtn) sharedState.$sysRestartAllBtn.disabled = busy;
    const cpRestart = document.getElementById('controlPlaneRestartBtn');
    if (cpRestart) cpRestart.disabled = busy;
    const map = _sysServiceMap(sharedState._sysServicesCache.payload);
    for (const id of Object.keys(sharedState.SYS_ROWS)) {
      sysPaintRestartButton(id, map.get(id));
    }
  }

  function sysCancelDashArm(quiet) {
    if (sharedState._sysDashArmTimer) { clearInterval(sharedState._sysDashArmTimer); sharedState._sysDashArmTimer = null; }
    if (sharedState._sysArmedRestart !== 'dashboard') return false;
    sharedState._sysArmedRestart = null;
    sysRowSay('dashboard', '');
    if (!quiet) sysSay('Dashboard restart cancelled.');
    sysApplyRestartLocks();
    return true;
  }

  function restartServerSetPort(port) {
    const clean = String(port || '').replace(/[^\d]/g, '');
    sharedState.restartServerPort = clean;
    if (sharedState.$restartServerLabel) {
      sharedState.$restartServerLabel.textContent = 'Restart dashboard' + (clean ? ' (:' + clean + ')' : '');
    }
    if (sharedState.$restartServerBtn) {
      sharedState.$restartServerBtn.title = clean
        ? 'Restart the Command Center dashboard on :' + clean
        : 'Restart the Command Center dashboard';
    }
  }

  function restartServerShowOverlay(title, detail) {
    const $overlay = document.getElementById('cccLoadingOverlay');
    const $label = document.getElementById('cccLoadingLabel');
    if (!$overlay) return;
    $overlay.classList.remove('fade-out', 'gone');
    if ($label) {
      $label.innerHTML =
        '<strong>' + title + '</strong>' +
        '<div class="ccc-loading-detail">' + detail + '</div>';
    }
  }

  async function restartWaitForGeneration(before, maxSeconds) {
    const deadline = Date.now() + maxSeconds * 1000;
    while (Date.now() < deadline) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      let version;
      try {
        const response = await fetch('/api/version', {
          cache: 'no-store', signal: AbortSignal.timeout(5000),
        });
        if (!response.ok) continue;
        version = await response.json();
      } catch (_) { continue; }
      if ((before.started_at && version.started_at && version.started_at !== before.started_at)
          || (before.pid && version.pid && version.pid !== before.pid)) return true;
      const outcome = version.restart_outcome || {};
      if (outcome.state === 'cancelled' || outcome.state === 'failed') {
        throw new Error('Restart ' + outcome.state + ': ' + (outcome.reason || 'unknown reason'));
      }
    }
    return false;
  }

  async function restartServerRun(endpoint) {
    endpoint = (typeof endpoint === 'string' && endpoint) ? endpoint : '/api/restart';
    const withWorker = endpoint.endsWith('/all');
    if (!sharedState.$restartServerBtn) return;
    closeSettingsModal();
    if (typeof sysCloseModal === 'function') sysCloseModal();
    sharedState.$restartServerBtn.disabled = true;
    restartServerShowOverlay(
      'Restarting&hellip;',
      withWorker
        ? 'Restarting the execution worker and the dashboard'
          + (sharedState.restartServerPort ? ' on :' + sharedState.restartServerPort : '')
          + '. Running queue items may need reconciliation.'
        : 'Agent work stays in the persistent worker while the dashboard'
          + (sharedState.restartServerPort ? ' on :' + sharedState.restartServerPort : '')
          + ' comes back.'
    );
    try {
      localStorage.setItem(sharedState.ARCHIVE_WINDOW_KEY, '7d');
    } catch (_) {}

    let postError = null;
    let before = null;
    try {
      // A stuck version read must not prevent the recovery POST. Its reply
      // normally supplies the baseline; this read also covers a lost reply.
      try {
        const baseline = await fetch('/api/version', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
        if (baseline.ok) before = await baseline.json();
      } catch (_) {}
      const r = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await r.json().catch(() => ({}));
      if (data.before) before = data.before;
      if (data && data.port) restartServerSetPort(data.port);
      if (!r.ok || !data.ok) {
        postError = new Error((data && data.error) || ('restart failed (' + r.status + ')'));
      }
    } catch (error) {
      // A lost POST reply may still have restarted; require a known baseline.
      if (!before) postError = error;
    }

    if (postError) {
      try { sessionStorage.removeItem('ccc-restarting'); } catch (_) {}
      const $overlay = document.getElementById('cccLoadingOverlay');
      if ($overlay) $overlay.classList.add('fade-out', 'gone');
      sharedState.$restartServerBtn.disabled = false;
      (0, sharedState.showOpToast)('Restart failed: ' + postError.message, 'error');
      return;
    }

    let ok = false;
    let failure = 'New dashboard generation not observed within 120s; restart outcome unknown.';
    try {
      if (before) ok = await restartWaitForGeneration(before, 120);
    } catch (error) { failure = error.message; }
    if (ok) {
      try { sessionStorage.setItem('ccc-restarting', '1'); } catch (_) {}
      location.reload();
    } else {
      try { sessionStorage.removeItem('ccc-restarting'); } catch (_) {}
      const $overlay = document.getElementById('cccLoadingOverlay');
      if ($overlay) $overlay.classList.add('fade-out', 'gone');
      sharedState.$restartServerBtn.disabled = false;
      (0, sharedState.showOpToast)(failure, 'error');
    }
  }

  function annEstimateScreenRect(rect) {
    const outerW = window.outerWidth || window.innerWidth;
    const outerH = window.outerHeight || window.innerHeight;
    const borderX = Math.max(0, (outerW - window.innerWidth) / 2);
    const chromeY = Math.max(0, (outerH - window.innerHeight) - borderX);
    const sx = typeof window.screenX === 'number' ? window.screenX : (window.screenLeft || 0);
    const sy = typeof window.screenY === 'number' ? window.screenY : (window.screenTop || 0);
    const vv = window.visualViewport;
    const vvLeft = vv && typeof vv.offsetLeft === 'number' ? vv.offsetLeft : 0;
    const vvTop = vv && typeof vv.offsetTop === 'number' ? vv.offsetTop : 0;
    const dpr = window.devicePixelRatio || 1;
    return {
      x: Math.round(sx + borderX + rect.x + vvLeft),
      y: Math.round(sy + chromeY + rect.y + vvTop),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      estimated: true,
      window_screen_x: sx,
      window_screen_y: sy,
      outer_width: outerW,
      outer_height: outerH,
      chrome_border_x: borderX,
      chrome_offset_y: chromeY,
      device_pixel_ratio: dpr,
    };
  }

  function annContextElement(element, rect) {
    if (!element || !rect) return element || null;
    const elArea = Math.max(1, rect.width * rect.height);
    let node = element.parentElement;
    let chosenEl = null;
    let depth = 0;
    while (node && node !== document.body && depth < 4) {
      const pr = node.getBoundingClientRect();
      const prArea = pr.width * pr.height;
      if (prArea >= Math.max(elArea * 1.8, elArea + 4000)) {
        chosenEl = node;
        break;
      }
      node = node.parentElement;
      depth++;
    }
    return chosenEl || element.parentElement || element;
  }

  function annInlineCloneStyles(src, dst) {
    if (!src || !dst || src.nodeType !== 1 || dst.nodeType !== 1) return;
    try {
      const cs = getComputedStyle(src);
      let cssText = '';
      for (let i = 0; i < cs.length; i++) {
        const prop = cs[i];
        cssText += prop + ':' + cs.getPropertyValue(prop) + ';';
      }
      dst.setAttribute('style', cssText);
    } catch (_) {}
    const srcKids = src.children;
    const dstKids = dst.children;
    for (let i = 0; i < srcKids.length && i < dstKids.length; i++) {
      annInlineCloneStyles(srcKids[i], dstKids[i]);
    }
  }

  function annCaptureDomRegionB64(rect, element, root, prepareClone) {
    if (!rect || rect.width < 2 || rect.height < 2) return Promise.resolve(null);
    root = root || annContextElement(element, rect) || element;
    if (!root || !root.cloneNode || !root.getBoundingClientRect) return Promise.resolve(null);
    if (root.getElementsByTagName('*').length > sharedState.ANN_DOM_CAPTURE_MAX_NODES) return Promise.resolve(null);
    const w = Math.max(1, Math.round(rect.width));
    const h = Math.max(1, Math.round(rect.height));
    if (w * h > 6_000_000) return Promise.resolve(null);
    const scale = window.devicePixelRatio || 1;
    const rootRect = root.getBoundingClientRect();
    const offsetX = rect.x - rootRect.left;
    const offsetY = rect.y - rootRect.top;
    const clone = root.cloneNode(true);
    annInlineCloneStyles(root, clone);
    if (prepareClone) prepareClone(clone);
    const xhtmlNs = 'http://www.w3.org/1999/xhtml';
    const svgNs = 'http://www.w3.org/2000/svg';
    const wrap = document.createElementNS(xhtmlNs, 'div');
    wrap.setAttribute('xmlns', xhtmlNs);
    const bg = getComputedStyle(root).backgroundColor || getComputedStyle(document.body).backgroundColor || '#0d1117';
    wrap.style.cssText = [
      'margin:0',
      'padding:0',
      'background:' + bg,
      'width:' + Math.ceil(root.scrollWidth) + 'px',
      'height:' + Math.ceil(root.scrollHeight) + 'px',
      'transform:translate(' + (-offsetX) + 'px,' + (-offsetY) + 'px)',
      'transform-origin:top left',
    ].join(';');
    wrap.appendChild(clone);
    const fo = document.createElementNS(svgNs, 'foreignObject');
    fo.setAttribute('width', String(w));
    fo.setAttribute('height', String(h));
    fo.appendChild(wrap);
    const svg = document.createElementNS(svgNs, 'svg');
    svg.setAttribute('xmlns', svgNs);
    svg.setAttribute('width', String(w));
    svg.setAttribute('height', String(h));
    svg.appendChild(fo);
    const svgText = new XMLSerializer().serializeToString(svg);
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        try {
          const canvas = document.createElement('canvas');
          canvas.width = Math.round(w * scale);
          canvas.height = Math.round(h * scale);
          const ctx = canvas.getContext('2d');
          if (!ctx) { resolve(null); return; }
          ctx.scale(scale, scale);
          ctx.drawImage(img, 0, 0, w, h);
          const parts = canvas.toDataURL('image/png').split(',');
          resolve(parts[1] || null);
        } catch (_) {
          resolve(null);
        }
      };
      img.onerror = () => resolve(null);
      img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgText);
    });
  }

  function fedEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }

  function fedShortId(id) {
    return String(id || '').slice(0, 8);
  }

  function handoffSetError(msg) {
    if (!sharedState.$handoffError) return;
    sharedState.$handoffError.textContent = msg || '';
    sharedState.$handoffError.classList.toggle('visible', !!msg);
  }

  async function handoffLoadPeers() {
    if (!sharedState.$handoffDest) return;
    sharedState.$handoffDest.innerHTML = '';
    try {
      const r = await fetch('/api/federation/peers', { cache: 'no-store' });
      const data = await r.json().catch(() => ({}));
      sharedState.handoffPeers = (data && data.peers) || [];
    } catch (_) {
      sharedState.handoffPeers = [];
    }
    const hasPeers = sharedState.handoffPeers.length > 0;
    if (sharedState.$handoffNoPeers) sharedState.$handoffNoPeers.hidden = hasPeers;
    if (sharedState.$handoffDest) sharedState.$handoffDest.style.display = hasPeers ? '' : 'none';
    if (sharedState.$handoffPreflightBtn) sharedState.$handoffPreflightBtn.style.display = hasPeers ? '' : 'none';
    if (!hasPeers) return;
    sharedState.$handoffDest.innerHTML = sharedState.handoffPeers.map((p) =>
      '<option value="' + fedEsc(p.node_id) + '">'
      + fedEsc((p.name || '(unnamed)') + ' · ' + fedShortId(p.node_id))
      + '</option>'
    ).join('');
  }

  function openHandoffModal(sessionId, displayName) {
    if (!sharedState.$handoffModal) return;
    sharedState.handoffSessionId = sessionId || null;
    sharedState.handoffReady = false;
    handoffSetError('');
    if (sharedState.$handoffSubtitle) {
      sharedState.$handoffSubtitle.textContent = displayName
        ? 'Move "' + displayName + '" to a paired node - its git state and working copy travel with it.'
        : 'Move this session to a paired node - its git state and working copy travel with it.';
    }
    if (sharedState.$handoffPlan) { sharedState.$handoffPlan.hidden = true; sharedState.$handoffPlan.innerHTML = ''; }
    if (sharedState.$handoffConfirmBtn) { sharedState.$handoffConfirmBtn.hidden = true; sharedState.$handoffConfirmBtn.disabled = true; sharedState.$handoffConfirmBtn.textContent = 'Hand off'; }
    if (sharedState.$handoffRecheckBtn) sharedState.$handoffRecheckBtn.hidden = true;
    if (sharedState.$handoffPreflightBtn) { sharedState.$handoffPreflightBtn.disabled = false; sharedState.$handoffPreflightBtn.textContent = 'Preflight'; }
    if (sharedState.$handoffDest) sharedState.$handoffDest.disabled = false;
    sharedState.$handoffModal.classList.add('open');
    handoffLoadPeers();
  }

  function finishEnginesFirstRun() {
    if (!window.__cccEnginesFirstRun) return;
    window.__cccEnginesFirstRun = false;
    try { localStorage.setItem(sharedState.ENGINES_FIRST_RUN_KEY, String(Date.now())); } catch (_) {}
    const box = sharedState.$settingsModal && sharedState.$settingsModal.querySelector('.settings-modal');
    if (box) box.classList.remove('is-first-run');
    endTailscaleStep();
    const intro = document.getElementById('engFirstRunIntro');
    const foot = document.getElementById('engFirstRunFoot');
    if (intro) intro.hidden = true;
    if (foot) foot.hidden = true;
    // The guide (and the agent-config dialog, which polls) go next.
    setTimeout(() => { if (typeof maybeStartFirstFlight === 'function') maybeStartFirstFlight(0); }, 400);
  }

  function _etsStop() { clearTimeout(sharedState._etsTimer); sharedState._etsTimer = null; }

  function endTailscaleStep() {
    _etsStop();
    try { localStorage.setItem(sharedState.TAILSCALE_STEP_KEY, String(Date.now())); } catch (_) {}
    const box = sharedState.$settingsModal && sharedState.$settingsModal.querySelector('.settings-modal');
    const step = document.getElementById('engTailscaleStep');
    if (box) box.classList.remove('is-step-tailscale');
    if (step) step.hidden = true;
  }

  function closeSettingsModal() {
    if(sharedState.choiceEligibilityReturn){(0, sharedState.choiceEligibilityReturn)();return;}
    if (!sharedState.$settingsModal) return;
    window.cccVoiceSetup?.leave();
    window.cccServiceConnections?.leave();
    finishEnginesFirstRun();
    sharedState.$settingsModal.classList.remove('open');
    sharedState.$settingsModal.hidden = true;
    if (sharedState.$settingsBtn) sharedState.$settingsBtn.setAttribute('aria-expanded', 'false');
    const restoreFocus = [sharedState.$settingsBtn, sharedState._settingsModalPrevFocus,
      ...document.querySelectorAll('[data-mobile-nav="more"], [data-mobile-nav-menu]'), document.getElementById('sidebarMoreBtn')]
      .find(element => {
        if (element === document.body || element === document.documentElement || !element?.isConnected
            || element.disabled || !element.getClientRects().length || getComputedStyle(element).visibility === 'hidden') return false;
        const rect = element.getBoundingClientRect();
        const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
        return hit === element || element.contains(hit);
      });
    restoreFocus?.focus();
    sharedState._settingsModalPrevFocus = null;
  }

  function loadFirstFlightTour(force) {
    if (window.cccTour) { try { window.cccTour.start({ force: !!force }); } catch (_) {} return; }
    const s = document.createElement('script');
    s.src = '/static/tour.js';
    s.onload = () => { try { window.cccTour.start({ force: !!force }); } catch (_) {} };
    document.head.appendChild(s);
  }

  function maybeStartFirstFlight(attempt) {
    let done = null;
    try { done = localStorage.getItem(sharedState.FFT_DONE_KEY); } catch (_) { return; }
    if (done || window.cccFirstRunLoad || document.getElementById('cccStartView')) return;
    // Never AUTO-start on small viewports. The tour's spotlight anchors
    // assume the desktop split layout, and its fixed welcome card sits on
    // top of the transcript swallowing every touch gesture — drag-to-scroll
    // dies underneath it (worst with the keyboard open, where the card can
    // be panned out of the visible frame and block invisibly). The Settings
    // "Take the tour" button (force=true) still works everywhere.
    if (window.matchMedia && window.matchMedia('(max-width: 1200px)').matches) return;
    // The engines screen comes first; finishing it re-invokes this.
    if (window.__cccEnginesFirstRun) return;
    // Defer while any modal is open so the guide is not covered.
    if (document.querySelector('.upd-overlay.open')) {
      if ((attempt || 0) < 50) setTimeout(() => maybeStartFirstFlight((attempt || 0) + 1), 4000);
      return;
    }
    loadFirstFlightTour(false);
  }
  function bindConversationInteractions() {
    installConversationInteraction1();
    installConversationInteraction2();
    installConversationInteraction3();
    installConversationInteraction4();
    installConversationInteraction5();
    installConversationInteraction6();
    installConversationInteraction7();
    installConversationInteraction8();
    installConversationInteraction9();
    installConversationInteraction10();
    installConversationInteraction11();
    installConversationInteraction12();
    installConversationInteraction13();
    installConversationInteraction14();
    installConversationInteraction15();
    installConversationInteraction16();
    installConversationInteraction17();
    installConversationInteraction18();
    installConversationInteraction19();
    installConversationInteraction20();
    installConversationInteraction21();
    installConversationInteraction22();
    installConversationInteraction23();
    installConversationInteraction24();
    installConversationInteraction25();
    installConversationInteraction26();
    installConversationInteraction27();
    installConversationInteraction28();
    installConversationInteraction29();
    installConversationInteraction30();
    installConversationInteraction31();
    installConversationInteraction31b();
    installConversationInteraction32();
    installConversationInteraction33();
    installConversationInteraction34();
    ;
    installConversationInteraction36();
    installConversationInteraction37();
    installConversationInteraction38();
    installConversationInteraction39();
    installConversationInteraction40();
    installConversationInteraction41();
    installConversationInteraction42();
    installConversationInteraction43();
    installConversationInteraction44();
    installConversationInteraction45();
    ;
    ;
    installConversationInteraction48();
    installConversationInteraction49();
    installConversationInteraction50();
    ;
    ;
    ;
    ;
    ;
    installConversationInteraction56();
    ;
    installConversationInteraction58();
    ;
    ;
    ;
    installConversationInteraction62();
    installConversationInteraction63();
    installConversationInteraction64();
    installConversationInteraction65();
    installConversationInteraction66();
    installConversationInteraction67();
    installConversationInteraction68();
    installConversationInteraction69();
    installConversationInteraction70();
    installConversationInteraction71();
    installConversationInteraction72();
    installConversationInteraction73();
    installConversationInteraction74();
    installConversationInteraction75();
    installConversationInteraction76();
    installConversationInteraction77();
    installConversationInteraction78();
    installConversationInteraction79();
    installConversationInteraction80();
    installConversationInteraction81();
    installConversationInteraction82();
  }

    return { bindConversationInteractions, installConversationInteraction1, installConversationInteraction2, installConversationInteraction3, installConversationInteraction4, installConversationInteraction5, installConversationInteraction6, installConversationInteraction7, installConversationInteraction8, installConversationInteraction9, installConversationInteraction10, installConversationInteraction11, installConversationInteraction12, installConversationInteraction13, installConversationInteraction14, installConversationInteraction15, installConversationInteraction16, installConversationInteraction17, installConversationInteraction18, installConversationInteraction19, installConversationInteraction20, installConversationInteraction21, installConversationInteraction22, installConversationInteraction23, installConversationInteraction24, installConversationInteraction25, installConversationInteraction26, installConversationInteraction27, installConversationInteraction28, installConversationInteraction29, installConversationInteraction30, installConversationInteraction31, installConversationInteraction32, installConversationInteraction33, installConversationInteraction34, installConversationInteraction36, installConversationInteraction37, installConversationInteraction38, installConversationInteraction39, installConversationInteraction40, installConversationInteraction41, installConversationInteraction42, installConversationInteraction43, installConversationInteraction44, installConversationInteraction45, installConversationInteraction48, installConversationInteraction49, installConversationInteraction50, installConversationInteraction56, installConversationInteraction58, installConversationInteraction62, installConversationInteraction63, installConversationInteraction64, installConversationInteraction65, installConversationInteraction66, installConversationInteraction67, installConversationInteraction68, installConversationInteraction69, installConversationInteraction70, installConversationInteraction71, installConversationInteraction72, installConversationInteraction73, installConversationInteraction74, installConversationInteraction75, installConversationInteraction76, installConversationInteraction77, installConversationInteraction78, installConversationInteraction79, installConversationInteraction80, installConversationInteraction81, installConversationInteraction82, f2ManualOpen, _pollInterruptAsks, startConvReplay, handOverToCodexDesktop, _renderConvOverflowMenu, handleLiveQuestionActionClick, _codexRequestResultForDecision, _handleUxQueueNudge, handleBreadcrumbSpawnedByClick, handleSidebarSessionIdCopyClick, agentAnswerTextBeforeResult, trayConversationView, readyQueuedSteerRows, markSteerInFlight, clearQueuedSteerError, clearSteerInFlight, revertOptimisticSteerMove, setQueuedSteerError, settleSteeredServerCard, steeredToastText, subagentRelayToastText, sendConversationGoalAction, _selectionSingleEventInfo, _hideAnnotateTrigger, _showAnnotateTrigger, _deleteAnnotation, openConversationPopout, rowDecisionReportSnapshot, captureRowDecisionScreenshot, openRowDecisionReportEditor, followRowReportReceipt, ccPostJson, closeReadyConversation, restartServerRun, _pathLinkSessionContext, tagBlocksForRtl, _renderMermaidBlocks, watchtowerTicketUrl, stepMediaLightbox, _isWebRoutePath, _bareMediaBaseDir, handleOpenUnavailable, _advanceConvReplayToNextMeaningful, _convReplayLoadTypedTurns, openHandoffModal, focusLiveQuestionComposer, submitLiveQuestionAnswer, _nudgeFixerBySid, steerTextKeys, conversationGoalActionCommand, _closestAnnotatableEvent, _openAnnotatePopover, _saveAnnotationsForSession, conversationPopoutUrl, repoPathForConversationPopout, popoutPositionFeature, tryOpenNativeMacPopout, annEstimateScreenRect, annCaptureDomRegionB64, _setOpenSessionLifecycle, closeSettingsModal, sysCloseModal, restartServerShowOverlay, restartServerSetPort, restartWaitForGeneration, _bidiMajorityDir, _loadMermaid, _lightboxGallery, _isAbsoluteFilesystemPath, openFolderViewer, handoffSetError, handoffLoadPeers, _closeAnnotatePopover, _saveAnnotation, _annotatePopoverOutsideHandler, annContextElement, annInlineCloneStyles, setOptimisticOverride, archivePayloadForRow, returnToConversationList, _showArchivedUndoToast, finishEnginesFirstRun, sysCancelDashArm, _sysTrapTab, _ffvSize, fedEsc, fedShortId, _annotationComposerText, _annotationExcerpt, withRepoPath, endTailscaleStep, maybeStartFirstFlight, sysRowSay, sysSay, sysApplyRestartLocks, _sysFocusables, _etsStop, loadFirstFlightTour, _sysServiceMap, sysPaintRestartButton, sysRestartBlockReason };
  }
  root.CCCConversationInteractions = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
