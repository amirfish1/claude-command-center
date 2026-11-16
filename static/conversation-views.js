/* Shared conversation views with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices, documentReady }) {
  function onDashboardDocumentReady(callback) {
    if (documentReady) return documentReady(callback);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', callback);
    else queueMicrotask(callback);
  }

  function requireConvRepo(actionLabel) {
    const repoPath = (0, sharedState.activeConvRepoPath)() || (0, sharedState.popoutRepoPath)();
    if (repoPath) return repoPath;
    (0, sharedState.showOpToast)((actionLabel || 'This action') + ' needs a repo - open a conversation in that repo first.', 'error');
    return '';
  }

  function setConvPanelOpen(open) {
    sharedState.convPanelOpen = open;
    if (sharedState.$convPanel) sharedState.$convPanel.style.display = open ? '' : 'none';
    if (sharedState.$splitResizer) sharedState.$splitResizer.style.display = open ? '' : 'none';
    if (sharedState.$kanbanPanel) {
      if (open) {
        if (sharedState.savedKanbanWidth) {
          sharedState.$kanbanPanel.style.flex = 'none';
          sharedState.$kanbanPanel.style.width = sharedState.savedKanbanWidth;
        }
      } else {
        sharedState.savedKanbanWidth = sharedState.$kanbanPanel.style.width || '';
        sharedState.$kanbanPanel.style.flex = '1';
        sharedState.$kanbanPanel.style.width = 'auto';
      }
    }
    if (sharedState.$kptOpenConvBtn) sharedState.$kptOpenConvBtn.style.display = open ? 'none' : '';
    try { localStorage.setItem('ccc-conv-panel-open', open ? '1' : '0'); } catch (_) {}
  }

  function _setMobileAskOpen(open) {
    let host = document.getElementById('mobileAskHost');
    const pane = document.getElementById('statusRailAskPane');
    if (!pane) return;
    if (open) {
      if (!host) {
        host = document.createElement('section');
        host.id = 'mobileAskHost';
        host.setAttribute('aria-label', 'Ask');
        document.body.appendChild(host);
      }
      if (!host._askHome) host._askHome = { parent: pane.parentNode, next: pane.nextSibling };
      host.appendChild(pane);
      pane.hidden = false;
      pane.classList.add('is-active');
      host.hidden = false;
      document.body.classList.add('mobile-ask-open');
    } else {
      document.body.classList.remove('mobile-ask-open');
      if (host) {
        if (host._askHome) {
          const home = host._askHome;
          home.parent.insertBefore(pane, home.next && home.next.parentNode === home.parent ? home.next : null);
          host._askHome = null;
          // Restore the rail's selected tab after sharing its pane on mobile.
          const selected = home.parent.querySelector('[data-rail-tab].is-active');
          pane.classList.toggle('is-active', !!selected && selected.dataset.railTab === 'ask');
          pane.hidden = !pane.classList.contains('is-active');
        }
        host.hidden = true;
      }
    }
  }

  function installConversationView1() {
if ((0, sharedState.isTouchPrimary)()) {
    let kdStartY = 0, kdArmed = false, kdFired = false;
    const _focusedComposer = () => {
      const ae = document.activeElement;
      return ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT')
        && ae.closest && ae.closest('.conv-input-bar, .conv-panel-input, .gc-reader-input-row')
        ? ae : null;
    };
    document.addEventListener('touchstart', (e) => {
      kdArmed = false; kdFired = false;
      if (!e.touches || e.touches.length !== 1) return;
      if (!_focusedComposer()) return;                 // keyboard not up → nothing to dismiss
      // A drag that begins on an interactive element inside the composer is
      // the user working the draft (selecting text, scrolling a long message,
      // tapping a button) — never dismiss on that. Drags starting on the
      // composer's own chrome (padding, gaps) DO count: that's the "drag the
      // composer down to drop the keyboard" gesture.
      const t = e.target;
      if (t && t.closest && t.closest('.conv-input-bar, .conv-panel-input, .gc-reader-input-row')
          && t.closest('textarea, input, button, a, select, [contenteditable], [role="button"]')) return;
      kdStartY = e.touches[0].clientY;
      kdArmed = true;
    }, { passive: true, capture: true });
    document.addEventListener('touchmove', (e) => {
      if (!kdArmed || kdFired || !e.touches || e.touches.length !== 1) return;
      if (e.touches[0].clientY - kdStartY > 24) {       // dragged down → drop it
        kdFired = true;
        const ae = _focusedComposer();
        if (ae) ae.blur();
      }
    }, { passive: true, capture: true });
  }
  }

  function applyConvFontScale() {
    if (sharedState.$conversationsView) sharedState.$conversationsView.style.zoom = sharedState.convFontScale;
    if (sharedState.$convPanelView) sharedState.$convPanelView.style.zoom = sharedState.convFontScale;
    if (sharedState._ttsCacheDebug) (0, sharedState._ttsScheduleCacheDebug)();
  }

  function installConversationView2() {
if (sharedState.$fontMinus) sharedState.$fontMinus.addEventListener('click', () => {
    sharedState.convFontScale = Math.max(0.7, +(sharedState.convFontScale - 0.1).toFixed(1));
    localStorage.setItem('ccc-conv-font-scale', String(sharedState.convFontScale));
    applyConvFontScale();
  });
  }

  function installConversationView3() {
if (sharedState.$fontPlus) sharedState.$fontPlus.addEventListener('click', () => {
    sharedState.convFontScale = Math.min(1.5, +(sharedState.convFontScale + 0.1).toFixed(1));
    localStorage.setItem('ccc-conv-font-scale', String(sharedState.convFontScale));
    applyConvFontScale();
  });
  }

  function installConversationView4() {
document.addEventListener('click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-role="rail-font-minus"], [data-role="rail-font-plus"]');
    if (!btn) return;
    const step = btn.dataset.role === 'rail-font-plus' ? 0.1 : -0.1;
    sharedState.convFontScale = Math.min(1.5, Math.max(0.7, +(sharedState.convFontScale + step).toFixed(1)));
    localStorage.setItem('ccc-conv-font-scale', String(sharedState.convFontScale));
    applyConvFontScale();
  });
  }

  function installConversationView5() {
document.addEventListener('click', (ev) => {
    const btn = ev.target && ev.target.closest && ev.target.closest('.wf-agent');
    if (!btn) return;
    ev.stopPropagation();
    const conv = btn.getAttribute('data-wf-conv') || '';
    if (!conv) return;
    window.open('/?ccc_popout=conversation&conv=' + encodeURIComponent(conv), '_blank',
      'width=900,height=800');
  });
  }

  function promptModal(title, defaultValue) {
    return new Promise(resolve => {
      const modal = document.getElementById('promptModal');
      if (!modal) return resolve(window.prompt(title, defaultValue));
      const titleEl = document.getElementById('promptTitle');
      const input = document.getElementById('promptInput');
      const okBtn = document.getElementById('promptOkBtn');
      const cancelBtn = document.getElementById('promptCancelBtn');
      const closeBtn = document.getElementById('promptCloseBtn');

      titleEl.textContent = title;
      input.value = defaultValue || '';
      modal.classList.add('open');

      let resolved = false;
      function cleanup() {
        modal.classList.remove('open');
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        closeBtn.removeEventListener('click', onCancel);
        input.removeEventListener('keydown', onKey);
      }
      function onOk() {
        if (resolved) return;
        resolved = true;
        cleanup();
        resolve(input.value);
      }
      function onCancel() {
        if (resolved) return;
        resolved = true;
        cleanup();
        resolve(null);
      }
      function onKey(e) {
        if ((0, sharedState.isImeKey)(e)) return;
        if (e.key === 'Enter') onOk();
        if (e.key === 'Escape') onCancel();
      }
      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      closeBtn.addEventListener('click', onCancel);
      input.addEventListener('keydown', onKey);

      setTimeout(() => input.focus(), 50);
    });
  }

  function _flushDeferredSidebarRenderIfAny() {
    if (!sharedState._sidebarRenderPendingWhilePaused) return;
    if ((0, sharedState._dashboardCoveredByApp)()) return;
    if (!sharedState._sidebarLifecycleRenderPending && (0, sharedState._pausePeriodicSidebarRender)()) return;
    sharedState._sidebarRenderPendingWhilePaused = false;
    (0, sharedState._scheduleSidebarRender)();
  }

  function startStatusRailTitleRename(opts = {}) {
    const row = opts.row || sharedState._statusRailActiveRow;
    if (!row || !row.id) return;
    const titleEl = opts.titleEl || document.getElementById('statusRailTitle');
    if (!titleEl || titleEl.querySelector('input')) return;
    titleEl.classList.remove('is-expanded');
    const currentText = titleEl.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'status-rail-title-input';
    input.value = row.display_name || currentText;
    titleEl.textContent = '';
    titleEl.appendChild(input);
    input.focus();
    input.select();
    let done = false;
    async function commit(save) {
      if (done) return;
      done = true;
      let finalText = currentText;
      if (save) {
        const newName = input.value.trim();
        if (newName !== currentText) {
          try {
            await fetch('/api/conversations/' + row.id + '/rename', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ session_id: row.session_id, name: newName }),
            });
            row.display_name = newName || null;
            row.name_overridden = !!newName;
            const _railSid = row.session_id || row.id;
            const c = sharedState.conversationsData.find(x => x.id === row.id
              || (_railSid && x.session_id === _railSid));
            if (c) { c.display_name = newName || null; c.name_overridden = !!newName; }
            // The Archived/All list renders from archiveData, and the row
            // object this rail held can be a stale snapshot — archiveData is
            // rebuilt as fresh copies by _mergeArchivePrSnapshot on every
            // refresh, which breaks the identity the `row` patch above
            // relies on. Patch the live entry so the renamed title paints
            // NOW, and pin the override so a stale server snapshot can't
            // snap it back (same contract as startInlineRename).
            if (typeof sharedState.archiveData !== 'undefined' && Array.isArray(sharedState.archiveData)) {
              const ac = sharedState.archiveData.find(x => x && (x.session_id === _railSid
                || x.id === _railSid || x.id === row.id));
              if (ac) { ac.display_name = newName || null; ac.name_overridden = !!newName; }
            }
            _rememberRenameOverride(_railSid, newName || null, !!newName);
            finalText = newName || currentText;
          } catch (_) {
            (0, sharedState.showOpToast)('Rename failed', 'error');
          }
        }
      }
      titleEl.textContent = finalText;
      if (opts.titleEl) titleEl.title = finalText;
      const _railTitleEl = document.getElementById('statusRailTitle');
      if (_railTitleEl && _railTitleEl !== titleEl && sharedState._statusRailActiveRow
          && sharedState._statusRailActiveRow.id === row.id) _railTitleEl.textContent = finalText;
      // Force: the pointer is often still hovering a sidebar row (or focus
      // is in a text control) right after commit, and the pause guard would
      // defer this exact paint — the rename the user is watching for.
      (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value), { force: true });
    }
    input.addEventListener('keydown', (e) => {
      if ((0, sharedState.isImeKey)(e)) return;
      if (e.key === 'Enter') { e.preventDefault(); commit(true); }
      else if (e.key === 'Escape') { e.preventDefault(); commit(false); }
    });
    input.addEventListener('blur', () => commit(true));
    input.addEventListener('click', (e) => e.stopPropagation());
  }

  function installConversationView6() {
if (sharedState.CONV_POPOUT_MODE && sharedState.CONV_POPOUT_TARGET) {
    (0, sharedState.updatePaneHeader)((0, sharedState.activePaneId)(), null, {
      category: sharedState.CONV_POPOUT_REPO_PATH ? ((0, sharedState._pathLeaf)(sharedState.CONV_POPOUT_REPO_PATH) || 'conversation') : 'conversation',
      title: sharedState.CONV_POPOUT_TARGET.slice(0, 8) || 'Conversation',
    });
  }
  }

  function tapbackToggle(sid, line, kind) {
    if (!sid || !line) return null;
    const all = (0, sharedState.tapbackLoad)();
    const m = all[sid] || (all[sid] = {});
    if (m[line] === kind) delete m[line]; else m[line] = kind;
    if (!Object.keys(m).length) delete all[sid];
    (0, sharedState.tapbackSave)(all);
    return m[line] || null;
  }

  function tapbackSyncBar(bar, sid, line) {
    const cur = ((0, sharedState.tapbackLoad)()[sid] || {})[line] || '';
    bar.querySelectorAll('button').forEach(b => b.classList.toggle('on', b.dataset.kind === cur));
  }

  function installConversationView7() {
document.addEventListener('mouseover', (ev) => {
    const evEl = ev.target && ev.target.closest ? ev.target.closest('.event.assistant[data-jsonl-line]') : null;
    if (!evEl || evEl.querySelector(':scope > .tapback-bar')) return;
    const paneEl = evEl.closest('.conv-pane[data-pane-id]');
    const pane = paneEl ? (0, sharedState.paneByPaneId)(paneEl.dataset.paneId) : null;
    const line = evEl.getAttribute('data-jsonl-line');
    const sid = pane && pane.conversationId ? (sharedState.sessionIdByConv[pane.conversationId] || pane.conversationId) : '';
    if (!sid || !line) return;
    const bar = document.createElement('div');
    bar.className = 'tapback-bar';
    bar.title = 'Sent with your next message (no extra turn). Shift-click sends now.';
    bar.innerHTML = '<button type="button" data-kind="up" aria-label="Thumbs up">' + sharedState.TAPBACK_EMOJI.up + '</button>'
      + '<button type="button" data-kind="down" aria-label="Thumbs down">' + sharedState.TAPBACK_EMOJI.down + '</button>';
    bar.addEventListener('click', (e) => {
      const b = e.target.closest('button');
      if (!b) return;
      e.stopPropagation();
      if (e.shiftKey) {
        // Immediate tier: inject now and accept the extra turn.
        fetch('/api/inject-input', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({session_id: sid, text: '[' + line + '] ' + sharedState.TAPBACK_LABEL[b.dataset.kind],
            source: 'composer', composer_form: 'ccc-composer-v1', mode: 'send', idempotency_key: (0, sharedState.durableActionId)('inject')}),
        }).then(r => { if (!r.ok) (0, sharedState.showOpToast)('Could not send reaction', 'error'); })
          .catch(() => (0, sharedState.showOpToast)('Could not send reaction', 'error'));
        return;
      }
      tapbackToggle(sid, line, b.dataset.kind);
      tapbackSyncBar(bar, sid, line);
    });
    tapbackSyncBar(bar, sid, line);
    evEl.appendChild(bar);
  });
  }

  function syncBodyTextFocusFlag() {
    const el = document.activeElement;
    const on = !!(el && el !== document.body && typeof el.matches === 'function' && el.matches(sharedState._TEXT_FOCUS_SEL));
    if (document.body.classList.contains('has-text-focus') !== on) document.body.classList.toggle('has-text-focus', on);
  }

  function syncBodyHasFlags(records) {
    const cl = document.body && document.body.classList;
    if (!cl) return;
    // Class/hidden flips elsewhere (scroll affordances, row chips) cannot
    // change a flag; skip the scan unless a watched element flipped or nodes
    // were added/removed.
    if (Array.isArray(records) && records.every((r) => r.type === 'attributes'
        && r.target.id !== 'settingsModal'
        && !sharedState._BODY_HAS_ATTR_TARGET.test(typeof r.target.className === 'string' ? r.target.className : ''))) return;
    for (const [flag, test] of sharedState._BODY_HAS_FLAGS) {
      const on = test();
      if (cl.contains(flag) !== on) cl.toggle(flag, on);
    }
  }

  function installConversationView8() {
(function _initHasFlagSync() {
    const init = () => {
      (0, sharedState.syncAllPaneHasFlags)();
      syncBodyTextFocusFlag();
      (0, sharedState.syncBodyGcReaderFlag)();
      syncBodyHasFlags();
      if (typeof MutationObserver === 'function' && !document.body._bodyHasFlagObserver) {
        document.body._bodyHasFlagObserver = new MutationObserver(syncBodyHasFlags);
        document.body._bodyHasFlagObserver.observe(document.body, {
          childList: true, subtree: true, attributes: true, attributeFilter: ['class', 'hidden'],
        });
      }
      const view = document.getElementById('conversationsView');
      if (view && typeof MutationObserver === 'function' && !view._gcFlagObserver) {
        view._gcFlagObserver = new MutationObserver(sharedState.syncBodyGcReaderFlag);
        view._gcFlagObserver.observe(view, { childList: true });
      }
    };
    document.addEventListener('focusin', syncBodyTextFocusFlag, true);
    document.addEventListener('focusout', () => setTimeout(syncBodyTextFocusFlag, 0), true);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
  })();
  }

  function installConversationView9() {
document.addEventListener('click', (ev) => {
    // Don't activate the pane on close-button clicks — the close handler
    // is about to destroy the pane anyway, and activating it first causes
    // a flicker (sidebar highlight briefly chases the doomed conv id).
    if (ev.target && ev.target.closest && ev.target.closest('[data-role="pane-close"]')) return;
    const pane = ev.target.closest && ev.target.closest('.conv-pane');
    if (!pane) return;
    const pid = pane.getAttribute('data-pane-id');
    const idx = (0, sharedState.paneIndexByPaneId)(pid);
    if (idx < 0) return;
    (0, sharedState.setActivePaneById)(pid);
  }, true);
  }

  function installConversationView10() {
window.addEventListener('resize', () => {
    // The browser has already reflowed the pane by the time 'resize' fires,
    // so scrollTop is stale relative to the new line-wrap. Restore against
    // each view's last-known-good top anchor (cached on scroll, so it
    // reflects the position from before this resize) instead of leaving
    // scrollTop untouched, which visually jumps the transcript (CCC-440).
    for (const view of document.querySelectorAll('.conversations-view')) {
      if (view._pinnedToBottom) (0, sharedState.scrollConversationToEnd)(view);
      else if (view._lastTopAnchor) _restoreAnchor(view, view._lastTopAnchor);
    }
    (0, sharedState.restoreConversationBottomAnchors)((0, sharedState.captureConversationBottomAnchors)());
  });
  }

  function _uxqSyncTicketMeta(items) {
    const previous = sharedState.uxFixesQueueMeta && sharedState.uxFixesQueueMeta._sig;
    const meta = (0, sharedState._setUxFixesQueueMeta)(items);
    if (meta._sig !== previous) (0, sharedState._scheduleSidebarRender)({ lifecycle: true });
  }

  function _uxqScopeKey() {
    try {
      const r = (0, sharedState.openConvRow)();
      const rowKey = r && (r.id || r.session_id);
      if (rowKey) return rowKey;
      if (typeof sharedState.currentConversation !== 'undefined' && sharedState.currentConversation) return sharedState.currentConversation;
      return '__queue_global__';
    } catch (_) { return '__queue_global__'; }
  }

  function _uxqSelectedScopeKey() {
    try {
      const repo = (typeof sharedState.activeConvRepoPath === 'function') ? (0, sharedState.activeConvRepoPath)() : '';
      if (repo) return repo;
    } catch (_) {}
    return _uxqScopeKey();
  }

  function _uxqLoadScopeMap() {
    try { return JSON.parse(localStorage.getItem(sharedState._UXQ_SCOPE_LS) || '{}') || {}; } catch (_) { return {}; }
  }

  function _uxqLoadSelectedScopeMap() {
    try { return JSON.parse(localStorage.getItem(sharedState._UXQ_SELECTED_SCOPE_LS) || '{}') || {}; } catch (_) { return {}; }
  }

  function _uxqSetScopeOverride(val) {
    const k = _uxqScopeKey(); if (!k) return;
    const map = _uxqLoadScopeMap();
    const v = (0, sharedState._uxqProjectKey)(val);
    if (!v || v === 'AUTO') delete map[k]; else map[k] = v;
    try {
      localStorage.setItem(sharedState._UXQ_SCOPE_LS, JSON.stringify(map));
      const sk = _uxqSelectedScopeKey();
      const selectedMap = _uxqLoadSelectedScopeMap();
      if (!v || v === 'AUTO') delete selectedMap[sk]; else selectedMap[sk] = v;
      localStorage.setItem(sharedState._UXQ_SELECTED_SCOPE_LS, JSON.stringify(selectedMap));
    } catch (_) {}
    _uxqResetHistoryPage();
  }

  function _uxqResetHistoryPage() { sharedState._uxqHistoryPage = 0; }

  function _uxqReviewLabel(it) {
    const stage = String((it && it.gate_pending) || 'review');
    const who = stage === 'verify' ? 'verifier'
      : stage.indexOf('review:') === 0 ? (stage.slice(7).trim() || 'submitter') : 'submitter';
    return 'awaits review · ' + who;
  }

  function _uxqWorkerSid(workerId) {
    const key = (0, sharedState._uxFixesIdentityKey)(workerId || '');
    if (!key) return '';
    const cache = sharedState._uxqHealthCache || {};
    const live = (Array.isArray(cache.wt_workers) ? cache.wt_workers : [])
      .concat(Array.isArray(cache.past_workers) ? cache.past_workers : [])
      .find(w => w && (0, sharedState._uxFixesIdentityKey)(w.worker_id || '') === key && String(w.session_id || '').trim());
    if (live) return String(live.session_id).trim();
    const map = cache.worker_session_map || {};
    for (const wid in map) {
      if ((0, sharedState._uxFixesIdentityKey)(wid) === key && map[wid]) return String(map[wid]);
    }
    return '';
  }

  function _uxqGateName(g) {
    if (g.indexOf('cmd:') === 0) return 'Command: ' + g.slice(4);
    if (g === 'verify') return 'Independent verifier';
    if (g.indexOf('review:') === 0) return 'Review by ' + (g.slice(7).trim() || 'submitter');
    return 'Review by submitter';
  }

  function _uxqChecksHtml(item) {
    if (!item) return '';
    const results = Array.isArray(item.gate_results) ? item.gate_results.filter(r => r && r.gate) : [];
    let gates = Array.isArray(item.effective_gates) ? item.effective_gates.slice()
      : Array.isArray(item.gates) ? item.gates.slice() : [];
    // Anything that ran or is owed but isn't in the list (a stale row, a
    // queue whose default changed) still shows, after the declared order.
    results.map(r => String(r.gate)).concat(Array.isArray(item.gate_stages) ? item.gate_stages : [])
      .concat(item.gate_pending ? [String(item.gate_pending)] : [])
      .forEach(g => { if (g && gates.indexOf(g) === -1) gates.push(g); });
    const accept = String(item.accept || '').trim();
    const status = String(item.status || '');
    const feedback = String(item.gate_feedback || '').trim();
    if (!accept && !gates.length && !feedback) return '';
    const inReview = status === 'in_review';
    const chip = (state, label) => '<span class="uxq-check-state is-' + state + '">' + (0, sharedState.escapeHtml)(label) + '</span>';
    const rows = gates.map(g => {
      g = String(g);
      const r = results.filter(x => String(x.gate) === g).pop();
      const pending = inReview && String(item.gate_pending || '') === g;
      let state = 'none', label = 'not run yet', meta = [], evidence = '';
      if (g.indexOf('review') === 0) {
        if (pending) { state = 'pending'; label = 'waiting'; }
        else if (status === 'closed' && item.gate_accepted_by) {
          state = 'passed'; label = 'accepted';
          meta.push('by ' + item.gate_accepted_by);
        } else if (/^rejected by /.test(feedback)) {
          state = 'failed'; label = 'rejected';
          evidence = feedback;
        }
      } else if (pending) {
        state = 'pending'; label = 'running';
      } else if (r) {
        state = r.passed ? 'passed' : 'failed';
        label = r.passed ? 'passed' : 'failed';
        evidence = String(r.output_tail || '');
      }
      if (r && r.exit_code != null && g.indexOf('cmd:') === 0) meta.push('exit ' + r.exit_code);
      if (r && r.seconds != null) meta.push(r.seconds + 's');
      if (r && r.at) meta.push(_uxqRelTime(r.at));
      let who = '';
      if (g === 'verify') {
        const v = item.verifier || {};
        const engine = (r && r.engine) || v.engine || '';
        const model = (r && r.model) || v.model || '';
        if (engine) meta.push(engine + (model ? ' / ' + model : ''));
        const wid = String(v.worker_id || (r && r.by) || '');
        if (wid) {
          const sid = _uxqWorkerSid(wid);
          who = sid
            ? ' <button type="button" class="uxq-check-session" data-open-sid="' + (0, sharedState.escapeAttr)(sid) + '" title="Open the verifier session">'
              + (0, sharedState.escapeHtml)(wid) + '</button>'
            : ' <span class="uxq-check-meta">' + (0, sharedState.escapeHtml)(wid) + '</span>';
        }
      }
      return '<li class="uxq-check">'
        + chip(state, label)
        + '<span class="uxq-check-name">' + (0, sharedState.escapeHtml)(_uxqGateName(g)) + '</span>'
        + (meta.length ? '<span class="uxq-check-meta">' + (0, sharedState.escapeHtml)(meta.join(' · ')) + '</span>' : '')
        + who
        + (evidence.trim()
          ? '<details class="uxq-check-evidence"><summary>' + (g === 'verify' ? 'findings' : g.indexOf('cmd:') === 0 ? 'output' : 'reason') + '</summary>'
            + '<pre>' + (0, sharedState.escapeHtml)(evidence.trim()) + '</pre></details>'
          : '')
        + '</li>';
    }).join('');
    const sentBack = feedback && status !== 'closed' && status !== 'in_review'
      ? '<div class="uxq-check-sentback">Sent back: ' + (0, sharedState.escapeHtml)(feedback) + '</div>'
      : '';
    return '<div class="uxq-td-sec uxq-td-checks-sec">'
      + '<div class="uxq-td-sec-label">Acceptance</div>'
      + (accept ? '<div class="uxq-check-accept">' + (0, sharedState.escapeHtml)(accept) + '</div>' : '')
      + sentBack
      + (rows ? '<ul class="uxq-checks">' + rows + '</ul>' : '')
      + '</div>';
  }

  function _uxqWithMachine(worker, machine) {
    const w = String(worker || '');
    const m = String(machine || '');
    return (m && w && !w.startsWith(m + '-')) ? m + '-' + w : w;
  }

  function _uxqReplaceCachedItem(item) {
    const ref = (0, sharedState._uxqItemRef)(item);
    if (!ref) return false;
    const items = Array.isArray(sharedState._uxqItemsCache.items) ? sharedState._uxqItemsCache.items : [];
    const index = items.findIndex(candidate => (0, sharedState._uxqItemRef)(candidate) === ref);
    if (index < 0) return false;
    const freshItems = items.slice();
    freshItems[index] = item;
    sharedState._uxqItemsVersion += 1;
    // A single-ticket patch, not a real resync — keep the last known
    // syncedAt so "Last synced" doesn't claim a full refresh just happened.
    sharedState._uxqItemsCache = { ts: Date.now(), items: freshItems, syncedAt: sharedState._uxqItemsCache.syncedAt };
    _uxqSyncTicketMeta(freshItems);
    return true;
  }

  async function _uxqOpenItemDetail(ref) {
    let fallback = (0, sharedState._uxqItemForRef)(ref);
    // A slim list row (closed ticket, prose trimmed by ?slim=1) must NEVER
    // reach the modal: its note is clipped, and the editable title re-saves
    // whatever it was handed (WT-67). Swap it for the untrimmed row from the
    // server's list memo first — milliseconds, no `gh` call. If that fails,
    // open from the live fetch below instead of from the clipped copy.
    if (fallback && fallback._slim) {
      let full = null;
      try {
        const cres = await fetch('/api/ux-fixes/item?cached=1&ref=' + encodeURIComponent(ref), { cache: 'no-store' });
        const cdata = await cres.json().catch(() => ({}));
        if (cres.ok && cdata.ok && cdata.item && !cdata.item._slim) full = cdata.item;
      } catch (_) {}
      fallback = full;
    }
    // Open instantly from the cached list row, then hydrate with the
    // canonical item (fresh timeline, full _github_body). The item endpoint
    // shells to `gh issue view` for GitHub-backed queues, which can take
    // 10s+ or fail outright during GraphQL quota storms — the user should
    // never wait on it just to read a ticket they can already see.
    if (fallback) _uxqOpenItemModal(fallback);
    try {
      const res = await fetch('/api/ux-fixes/item?ref=' + encodeURIComponent(ref), { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok && data.item) {
        if (!fallback) {
          _uxqOpenItemModal(data.item);
          return;
        }
        // Hydrate only if this ticket's modal is still open and the user
        // hasn't started typing into it — a rebuild would wipe their input.
        const modal = document.getElementById('uxqItemModal');
        if (!modal || (0, sharedState._uxqItemRef)(data.item) !== ref) return;
        if (modal.contains(document.activeElement)
            && /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName)) return;
        const typed = Array.from(modal.querySelectorAll('input, textarea'))
          .some(el => String(el.value || '').trim());
        if (typed) return;
        if (JSON.stringify(data.item) !== JSON.stringify(fallback)) {
          _uxqOpenItemModal(data.item);
        }
        return;
      }
      if (!fallback) {
        (0, sharedState.showOpToast)('Ticket not found: ' + (data.error || res.status), 'error');
      }
    } catch (e) {
      if (!fallback) {
        (0, sharedState.showOpToast)('Ticket load failed: ' + e, 'error');
      }
    }
  }

  function _uxqItemPrompt(item) {
    if (!item) return '';
    // _github_body is the full issue body; `text` can be a truncated copy.
    return String(item._github_body || item.text || item.note || '');
  }

  async function _uxqLoadLinkedConversation(modal, ref) {
    const sec = modal.querySelector('.uxq-td-conv-sec');
    const body = modal.querySelector('.uxq-td-conv-body');
    if (!sec || !body || !window.CCCTicketProse) return;
    try {
      const res = await fetch('/api/queue/context?ref=' + encodeURIComponent(ref), { cache: 'no-store' });
      const d = await res.json().catch(() => ({}));
      if (!modal.isConnected) return;
      if (d && d.ok && d.transcript && Array.isArray(d.transcript.turns) && d.transcript.turns.length) {
        sec.hidden = false;
        body.innerHTML = window.CCCTicketProse.renderTranscript(d.transcript);
        const conv = body.querySelector('.tp-conv');
        if (conv) conv.scrollTop = conv.scrollHeight;
      } else if (d && d.found && d.error) {
        // A provider matched a conversation key but the fetch failed — say so
        // instead of silently showing nothing.
        sec.hidden = false;
        body.innerHTML = '<div class="tp-conv-status is-error">' + (0, sharedState.escapeHtml)(d.error) + '</div>';
      }
    } catch (_) { /* no providers configured / server offline — stay hidden */ }
  }

  function _uxqItemTitle(item) {
    // Returns the full note/text (boilerplate stripped, no line/char cap) so
    // the editable modal field never displays or re-saves a truncated copy
    // of the ticket note. See WT-67: the old 220-char/first-line cap meant
    // clicking into the title and clicking away without editing could
    // silently overwrite the stored note with the truncated version.
    if (!item) return '';
    // GitHub-synced tickets: the issue title is the human headline; the body
    // head (note) is often machine meta (digest markers, "Studio: …" lines).
    // Their title isn't editable in the modal, so this can't feed a re-save.
    const githubItem = String(item.source || '') === 'github' || !!item.github_repo;
    const candidates = githubItem
      ? [item.title, item.note, item.text]
      : [item.note, item.text, item.title];
    for (const raw of candidates) {
      const lines = String(raw || '').split(/\r?\n/).map(s => s.trim()).filter(Boolean);
      const kept = [];
      for (let line of lines) {
        if (line === 'Fix the following UX issue based on this annotation:') continue;
        if (line.indexOf('Annotation:') === 0) line = line.slice('Annotation:'.length).trim();
        if (!line) continue;
        kept.push(line);
      }
      if (kept.length) return kept.join('\n');
    }
    return (0, sharedState._uxqItemRef)(item);
  }

  async function _uxqSaveField(ref, field, value) {
    try {
      const res = await fetch('/api/ux-fixes/edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ref, [field]: value }),
      });
      const d = await res.json().catch(() => ({}));
      if (res.ok && d.ok) {
        (0, sharedState.showOpToast)('Saved', 'success');
        if (_uxqReplaceCachedItem(d.item)) {
          (0, hostServices._renderQueuePanel)({ allowStale: true });
        } else {
          sharedState._uxqItemsCache.ts = 0;
          (0, hostServices._renderQueuePanel)();
        }
      } else {
        (0, sharedState.showOpToast)('Save failed: ' + (d.error || res.status), 'error');
      }
    } catch (e) {
      (0, sharedState.showOpToast)('Save failed: ' + e, 'error');
    }
  }

  function _uxqRelTime(iso) {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    if (diff < 0) return 'just now';
    const s = Math.floor(diff / 1000);
    if (s < 60) return s + 's ago';
    const m = Math.floor(s / 60);
    if (m < 60) return m + 'm ago';
    const h = Math.floor(m / 60);
    if (h < 24) return h + 'h ago';
    return Math.floor(h / 24) + 'd ago';
  }

  function _uxqExtractImages(text) {
    if (!text) return [];
    const found = [];
    const re = /((?:https?:\/\/[^\s"'<>]+?|(?:\/[^\s"'<>]+|~\/[^\s"'<>]+))\.(?:png|jpg|jpeg|gif|webp)(?:\?[^\s"'<>)]+)?)/gi;
    let m;
    while ((m = re.exec(text)) !== null) found.push(m[1]);
    return [...new Set(found)];
  }

  function _uxqCloseNoteModal() {
    if (!sharedState._uxqNoteModalEl) return;
    sharedState._uxqNoteModalEl.remove();
    sharedState._uxqNoteModalEl = null;
    if (sharedState._uxqNoteModalKeydownHandler) {
      document.removeEventListener('keydown', sharedState._uxqNoteModalKeydownHandler);
      sharedState._uxqNoteModalKeydownHandler = null;
    }
  }

  function _uxqOpenNoteModal(label, text, extraClass) {
    _uxqCloseNoteModal();
    const overlay = document.createElement('div');
    // Opened from inside the ticket-detail modal (.uxq-td-overlay, z-index
    // 100000) — needs a higher stacking context or it renders invisibly
    // behind it (CCC-1023).
    overlay.className = 'settings-modal-overlay uxq-note-overlay open' + (extraClass ? ' ' + extraClass : '');
    const body = window.CCCTicketProse ? window.CCCTicketProse.render(text) : ('<p class="tp-p">' + (0, sharedState.escapeHtml)(String(text)) + '</p>');
    overlay.innerHTML = '<div class="settings-modal-backdrop"></div>'
      + '<div class="uxq-note-modal" role="dialog" aria-modal="true" aria-label="' + (0, sharedState.escapeAttr)(label) + '">'
      +   '<div class="uxq-note-modal-header">'
      +     '<div class="uxq-note-modal-title">' + (0, sharedState.escapeHtml)(label) + '</div>'
      +     '<button type="button" class="uxq-note-modal-close" title="Close" aria-label="Close">&times;</button>'
      +   '</div>'
      +   '<div class="uxq-note-modal-body">' + body + '</div>'
      + '</div>';
    document.body.appendChild(overlay);
    sharedState._uxqNoteModalEl = overlay;
    const closeIt = () => _uxqCloseNoteModal();
    overlay.querySelector('.settings-modal-backdrop').addEventListener('click', closeIt);
    overlay.querySelector('.uxq-note-modal-close').addEventListener('click', closeIt);
    sharedState._uxqNoteModalKeydownHandler = (e) => { if ((0, sharedState.isImeKey)(e)) return; if (e.key === 'Escape') closeIt(); };
    document.addEventListener('keydown', sharedState._uxqNoteModalKeydownHandler);
  }

  function _uxqInitTicketZoom(modal) {
    const card = modal.querySelector('.uxq-td-card');
    if (!card) return;
    let zoom = parseFloat(localStorage.getItem(sharedState.UXQ_TD_ZOOM_KEY));
    if (!isFinite(zoom)) zoom = 1;
    zoom = Math.min(sharedState.UXQ_TD_ZOOM_MAX, Math.max(sharedState.UXQ_TD_ZOOM_MIN, zoom));
    const btns = modal.querySelectorAll('.uxq-td-zoom-btn');
    const apply = () => {
      card.style.setProperty('--uxq-td-zoom', zoom);
      btns.forEach(btn => {
        const dir = parseFloat(btn.getAttribute('data-zoom-dir'));
        btn.disabled = dir < 0 ? zoom <= sharedState.UXQ_TD_ZOOM_MIN + 1e-6 : zoom >= sharedState.UXQ_TD_ZOOM_MAX - 1e-6;
      });
    };
    apply();
    btns.forEach(btn => {
      btn.addEventListener('click', () => {
        const dir = parseFloat(btn.getAttribute('data-zoom-dir'));
        zoom = Math.min(sharedState.UXQ_TD_ZOOM_MAX, Math.max(sharedState.UXQ_TD_ZOOM_MIN, zoom + dir * sharedState.UXQ_TD_ZOOM_STEP));
        localStorage.setItem(sharedState.UXQ_TD_ZOOM_KEY, String(zoom));
        apply();
      });
    });
  }

  function _uxqAnswerDraft(ref, text) {
    if (text !== undefined) {
      if (text === null) sharedState._uxqAnswerDrafts.delete(ref);
      else sharedState._uxqAnswerDrafts.set(ref, text);
      try {
        if (text === null) sessionStorage.removeItem(sharedState.UXQ_ANSWER_DRAFT_PREFIX + ref);
        else sessionStorage.setItem(sharedState.UXQ_ANSWER_DRAFT_PREFIX + ref, text);
      } catch (_) { /* the in-memory draft still survives closing the dialog */ }
    }
    try { return sessionStorage.getItem(sharedState.UXQ_ANSWER_DRAFT_PREFIX + ref) || sharedState._uxqAnswerDrafts.get(ref) || ''; }
    catch (_) { return sharedState._uxqAnswerDrafts.get(ref) || ''; }
  }

  function _uxqAnswerNotice(ref, message) {
    const toast = (0, sharedState.showOpToast)((0, sharedState.escapeHtml)(ref + ': ' + message), 'warning', {
      label: 'Review ticket', ttlMs: 86400000,
      onClick: () => window.cccOpenTicket(ref),
    });
    toast?.querySelectorAll('button').forEach(button => { button.style.minHeight = '44px'; });
    return toast;
  }

  function _uxqWireTicketAnswer(modal, item, close) {
    const ref = (0, sharedState._uxqItemRef)(item);
    const input = modal.querySelector('.uxq-answer-input');
    const send = modal.querySelector('.uxq-answer-send');
    const hint = modal.querySelector('.uxq-td-answer-hint');
    if (!input || !send) return;
    input.value = _uxqAnswerDraft(ref);
    if (input.value && hint) hint.textContent = 'Previous answer was not confirmed. Check Activity before retrying.';
    if (sharedState._uxqAnswerPending.has(ref)) {
      input.value = sharedState._uxqAnswerPending.get(ref);
      input.disabled = send.disabled = true;
      send.textContent = 'Sending…';
      if (hint) hint.textContent = 'Sending in the background. You can close this ticket.';
    }
    const submit = () => {
      if (sharedState._uxqAnswerPending.has(ref) || sharedState._workersAnswersPending.has(ref) || send.disabled) return;
      const text = input.value.trim();
      if (!text) { input.focus(); return; }
      _uxqAnswerDraft(ref, text);
      sharedState._uxqAnswerPending.set(ref, text);
      sharedState._workersAnswersPending.add(ref);
      // Release the dialog before waiting for queue writes or worker delivery.
      close();
      const notice = _uxqAnswerNotice(ref, 'Sending answer in the background…');
      void (async () => {
        let confirmed = false;
        try {
          const body = JSON.stringify({ref, text});
          const res = await fetch('/api/ux-fixes/answer', {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body,
            keepalive: new TextEncoder().encode(body).length < 60000,
            cccRetryHandled: true,
          });
          const data = await res.json();
          if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
          confirmed = true;
          _uxqAnswerDraft(ref, null);
          sharedState._uxqItemsCache.ts = 0; sharedState._uxqHealthCache.ts = 0;
          try {
            if (data.item) _uxqReplaceCachedItem(data.item);
            (0, hostServices._renderQueuePanel)();
          } catch (_) { /* a refresh failure cannot undo a recorded answer */ }
          if (data.delivery && data.delivery.ok === false) {
            _uxqAnswerNotice(ref, 'Answer recorded; worker notification could not be confirmed. Check Activity before sending again.');
          } else {
            (0, sharedState.showOpToast)((0, sharedState.escapeHtml)(ref + (data.images_stripped
              ? ': Answer recorded; images were omitted for this GitHub ticket.'
              : ': Answer recorded.')), 'success');
          }
        } catch (error) {
          _uxqAnswerNotice(ref, 'Answer not confirmed: ' + (error.message || error)
            + '. Your text is saved. Check Activity before retrying.');
        } finally {
          notice?.remove();
          sharedState._uxqAnswerPending.delete(ref);
          sharedState._workersAnswersPending.delete(ref);
          const current = document.getElementById('uxqItemModal');
          if (current?.dataset.answerRef === ref) {
            const button = current.querySelector('.uxq-answer-send');
            const field = current.querySelector('.uxq-answer-input');
            const status = current.querySelector('.uxq-td-answer-hint');
            if (button) { button.disabled = confirmed; button.textContent = confirmed ? 'Answered' : 'Send'; }
            if (field) field.disabled = confirmed;
            if (status) status.textContent = confirmed ? 'Answer recorded.' : 'Answer not confirmed. Your text is saved; check Activity before retrying.';
          }
        }
      })();
    };
    modal.dataset.answerRef = ref;
    send.addEventListener('click', submit);
    input.addEventListener('keydown', e => {
      if (!(0, sharedState.isImeKey)(e) && e.key === 'Enter') { e.preventDefault(); submit(); }
    });
  }

  function _uxqSessionActivityHtml(data) {
    const count = n => Number(n || 0).toLocaleString();
    const tokens = usage => usage ? count(Object.values(usage).reduce((a, b) => a + Number(b || 0), 0)) : 'Unobserved';
    const who = event => event.sender_kind === 'human' ? 'You'
      : (event.sender_name || event.sender || 'Unknown') + ' (' + event.sender_kind + ')';
    const sessions = data.sessions || [];
    if (!sessions.length) return '<p>No linked sessions have been recorded for this ticket.</p>';
    return '<p>Recorded inputs and measured tokens. Times are UTC. Missing usage is shown as unobserved.</p>'
      + (data.gaps && data.gaps.length ? '<p role="status">' + count(data.gaps.length) + ' session transcripts unavailable; their history may be incomplete.</p>' : '')
      + sessions.map(session => {
        const sid = session.session_id;
        const link = (data.links || {})[sid];
        const events = (data.events || []).filter(event => event.receiver === sid);
        const messages = (data.messages || []).filter(event => event.receiver === sid);
        const drivers = (session.drivers || []).map(d => (0, sharedState.escapeHtml)(who({sender_kind: d.kind, sender: d.id, sender_name: d.name})) + ': ' + count(d.count)).join(' · ');
        return '<details class="uxq-session-record"><summary><span>' + (0, sharedState.escapeHtml)(sid) + '</span><span>'
          + count(session.wakes) + ' inputs · ' + tokens(session.tokens) + ' tokens</span></summary>'
          + (link ? '<p>' + (link.kind === 'continued' ? 'Continues ' : 'Spawned by ') + (0, sharedState.escapeHtml)(link.parent) + '</p>' : '')
          + '<p>' + (drivers || 'No recorded inputs in this period.') + '</p>'
          + (session.unmeasured_turns ? '<p>' + count(session.unmeasured_turns) + ' inputs have no measured token usage.</p>' : '')
          + '<details><summary>Inputs per hour (UTC)</summary><dl>'
          + Object.entries(session.hours || {}).map(([hour, total]) => '<dt>' + (0, sharedState.escapeHtml)(hour) + '</dt><dd>' + count(total) + '</dd>').join('') + '</dl></details>'
          + '<details><summary>' + count(messages.length) + ' message delivery records</summary><ul>'
          + messages.map(message => '<li>' + (0, sharedState.escapeHtml)(new Date(message.at * 1000).toISOString()) + ' · '
            + (0, sharedState.escapeHtml)(who(message)) + ' · ' + (0, sharedState.escapeHtml)(message.status) + ' · ' + (0, sharedState.escapeHtml)(message.message_id) + '</li>').join('') + '</ul></details>'
          + '<div class="uxq-session-events"><table><caption>Recorded inputs for ' + (0, sharedState.escapeHtml)(sid.slice(0, 8)) + '</caption>'
          + '<thead><tr><th scope="col">Time (UTC)</th><th scope="col">Driven by</th><th scope="col">Tokens</th><th scope="col">Message</th></tr></thead><tbody>'
          + events.map(event => '<tr><td>' + (0, sharedState.escapeHtml)(new Date(event.at * 1000).toISOString().replace('T', ' ').replace('Z', '')) + '</td><td>'
            + (0, sharedState.escapeHtml)(who(event)) + (event.sender && event.sender !== event.sender_name ? '<br>' + (0, sharedState.escapeHtml)(event.sender) : '') + (event.cause === 'ticket-comment' ? '<br>Ticket comment' : '') + '</td><td title="'
            + (0, sharedState.escapeAttr)(event.tokens ? JSON.stringify(event.tokens) : 'No usage record') + '">' + tokens(event.tokens) + '</td><td>'
            + (0, sharedState.escapeHtml)(event.message_id || 'No envelope ID') + '</td></tr>').join('')
          + '</tbody></table></div></details>';
      }).join('');
  }

  function _uxqBindSessionActivity(modal, ref) {
    const section = modal.querySelector('.uxq-session-activity');
    if (!section) return;
    const body = section.querySelector('[data-session-activity-body]');
    const date = section.querySelector('input');
    const refresh = section.querySelector('button');
    let loaded = false;
    let generation = 0;
    async function load() {
      const request = ++generation;
      body.textContent = 'Loading recorded session activity…';
      refresh.disabled = true;
      try {
        const query = new URLSearchParams({ref});
        if (date.value) {
          const after = Date.parse(date.value + 'T00:00:00Z') / 1000;
          query.set('after', after);
          query.set('before', after + 86400);
        }
        const response = await fetch('/api/idea-tickets/activity?' + query, {cccPriority: 'user'});
        const data = await response.json();
        if (request !== generation || !modal.isConnected) return;
        if (!response.ok || !data.ok) throw new Error(data.error || 'Could not load session activity.');
        body.innerHTML = _uxqSessionActivityHtml(data);
        loaded = true;
      } catch (error) {
        if (request === generation) body.textContent = (error.message || 'Could not load session activity.') + ' Use Refresh to try again.';
      } finally {
        if (request === generation) refresh.disabled = false;
      }
    }
    section.addEventListener('toggle', () => { if (section.open && !loaded) load(); });
    date.addEventListener('change', load);
    refresh.addEventListener('click', load);
  }

  function _uxqOpenItemModal(item) {
    const existing = document.getElementById('uxqItemModal');
    if (existing) existing.remove();
    if (!item) {
      (0, sharedState.showOpToast)('Queue item not found. Refresh Queue and try again.', 'error');
      return;
    }
    const ref = (0, sharedState._uxqItemRef)(item);
    const promptText = _uxqItemPrompt(item);
    const detailTitle = _uxqItemTitle(item);
    // GitHub-synced tickets: the note is the raw issue body head, often led by
    // machine markers like <!-- digest-finding-id: … -->. Their title is not
    // editable here anyway (edits 404 against the GitHub backend), so display
    // a cleaned title instead of the marker.
    const githubBacked = String(item.source || '') === 'github' || !!item.github_repo;
    const displayTitle = (githubBacked && window.CCCTicketProse)
      ? (window.CCCTicketProse.cleanForTitle(detailTitle) || detailTitle)
      : detailTitle;
    // Long ticket notes are prose, not one enormous heading. Keep the opening
    // sentence prominent while preserving the complete, editable note below it.
    const titleParts = (0, sharedState.splitFirstSentence)(displayTitle);
    // Closed wins: the GitHub backend historically never cleared needs_input
    // on close, so a closed-but-once-blocked ticket must not read as blocked.
    const status = item.status === 'closed' ? 'closed' : (item.needs_input ? 'blocked' : (item.status || 'open'));
    // Match the compact Queue row: request work only for an unclaimed open
    // ticket. A queued request stays reversible until WatchTower claims it.
    const hasActiveClaim = !!(item.claimed_by || item.claimed_at);
    const canRequestRun = status === 'open' && !hasActiveClaim;
    const runQueued = canRequestRun && !!item.run_requested;
    const runBusy = sharedState._uxqRunBusyRefs.has(ref);
    // List rows carry `history` but not `timeline`; the modal opens from the
    // row before the item fetch hydrates (or when it fails), so fall back to
    // history or a blocked ticket shows no question at all.
    const timeline = Array.isArray(item.timeline) ? item.timeline
      : (Array.isArray(item.history) ? item.history.filter(ev => ev && ev.event) : []);
    // Who it waits on (CCC-244), same projection as the queue row.
    const attention = (0, sharedState._uxqTicketAttention)(item);

    // Sidebar property row helpers
    function _propRow(key, valHtml) {
      if (!valHtml) return '';
      return '<div class="uxq-td-pr"><span class="uxq-td-pr-k">' + (0, sharedState.escapeHtml)(key) + '</span>'
           + '<span class="uxq-td-pr-v">' + valHtml + '</span></div>';
    }
    function _propSel(key, field, options, current) {
      const opts = options.map(([v, label]) =>
        '<option value="' + (0, sharedState.escapeAttr)(v) + '"' + (v === current ? ' selected' : '') + '>'
        + (0, sharedState.escapeHtml)(label) + '</option>'
      ).join('');
      return '<div class="uxq-td-pr"><span class="uxq-td-pr-k">' + (0, sharedState.escapeHtml)(key) + '</span>'
           + '<select class="uxq-td-ps" data-field="' + (0, sharedState.escapeAttr)(field) + '">' + opts + '</select></div>';
    }

    // Status / priority / type badges — clickable, jumping to the section of
    // the ticket that explains/acts on that badge (CCC-1027).
    const statusClass = { open: 'uxq-bs-open', in_progress: 'uxq-bs-wip', blocked: 'uxq-bs-blocked', closed: 'uxq-bs-closed' }[status] || 'uxq-bs-open';
    const priorityClass = { p0: 'uxq-bp-p0', p1: 'uxq-bp-p1', p2: 'uxq-bp-p2', p3: 'uxq-bp-p3' }[item.priority] || '';
    const statusJumpTarget = status === 'blocked'
      ? '.uxq-td-gate-sec, .uxq-td-answer-sec'
      : status === 'closed'
        ? '.uxq-td-reopen-sec'
        : '.uxq-timeline';
    function _jumpBadge(cls, label, target) {
      return '<span class="uxq-td-badge uxq-td-badge-jump ' + cls + '" data-jump-target="' + (0, sharedState.escapeAttr)(target)
        + '" role="button" tabindex="0" title="Jump to section">' + (0, sharedState.escapeHtml)(label) + '</span>';
    }
    const topBadges = _jumpBadge(statusClass, status === 'in_review' ? _uxqReviewLabel(item) : status,
        status === 'in_review' ? '.uxq-td-review-sec' : statusJumpTarget)
      + (attention && attention.key !== 'gate'
        ? '<span class="uxq-td-badge fq-chip fq-att fq-att-' + (0, sharedState.escapeAttr)(attention.tone) + '" data-attention="' + (0, sharedState.escapeAttr)(attention.key)
          + '" title="' + (0, sharedState.escapeAttr)(attention.title) + '">' + (0, sharedState.escapeHtml)(attention.label) + '</span>'
        : '')
      + (item.lane ? _jumpBadge('uxq-bs-lane', item.lane, '.uxq-td-pg-origin') : '')
      + (item.priority ? _jumpBadge(priorityClass, item.priority, '.uxq-td-pg-properties') : '')
      + (item.type ? _jumpBadge('uxq-bs-type', item.type, '.uxq-td-pg-properties') : '')
      + ((item.placement === 'mac' || item.placement === 'cloud') ? _jumpBadge('uxq-bs-type', item.placement + ' only', '.uxq-td-pg-properties') : '');

    // Images: screenshot_path + PNGs/images extracted from note/text
    const imagePaths = [];
    if (item.screenshot_path) imagePaths.push(item.screenshot_path);
    _uxqExtractImages((item.note || '') + ' ' + (item.text || '')).forEach(p => {
      if (!imagePaths.includes(p)) imagePaths.push(p);
    });
    const imagesHtml = imagePaths.length
      ? '<div class="uxq-td-sec">'
        + '<div class="uxq-td-sec-label">Screenshots</div>'
        + '<div class="uxq-td-images">'
        + imagePaths.map(p => {
            const src = /^https?:\/\//i.test(p)
              ? p
              : '/api/pasted-image?path=' + encodeURIComponent(p);
            return '<a href="' + src + '" target="_blank" rel="noopener" class="uxq-td-img-wrap">'
              + '<img class="uxq-td-img" src="' + src + '" alt="screenshot" loading="lazy"></a>';
          }).join('')
        + '</div></div>'
      : '';

    // Full prompt (only if different from note/title). GitHub-backed items
    // carry the FULL issue body in _github_body while `text` may be a
    // truncated copy — prefer the full one. Rendered through the shared
    // ticket-prose renderer (readable prose, meta chips, highlighted quotes)
    // with the old raw <pre> as fallback if the script failed to load.
    const fullBody = String(item._github_body || item.text || '');
    const showPrompt = !!(fullBody.trim() && fullBody.trim() !== (item.note || '').trim());
    const promptHtml = showPrompt
      ? '<div class="uxq-td-sec">'
        + '<div class="uxq-td-sec-label">Full prompt</div>'
        + (window.CCCTicketProse
            ? window.CCCTicketProse.render(fullBody)
            : '<pre class="uxq-td-prompt">' + (0, sharedState.escapeHtml)(fullBody) + '</pre>')
        + '</div>'
      : '';
    // Linked conversation (e.g. the owner/client thread a digest ticket
    // is about) — filled in async by _uxqLoadLinkedConversation after the
    // modal mounts; stays hidden unless the server resolves a transcript.
    const convSecHtml =
      '<div class="uxq-td-sec uxq-td-conv-sec" hidden>'
      + '<div class="uxq-td-sec-label">Linked conversation</div>'
      + '<div class="uxq-td-conv-body"></div>'
      + '</div>';

    // Timeline event builder
    function _tlEvt(cls, headHtml, bodyHtml) {
      return '<div class="uxq-tl-evt ' + cls + '">'
        + '<div class="uxq-tl-node"><span class="uxq-tl-dot"></span></div>'
        + '<div class="uxq-tl-body">'
        + '<div class="uxq-tl-head">' + headHtml + '</div>'
        + (bodyHtml ? '<div class="uxq-tl-detail">' + bodyHtml + '</div>' : '')
        + '</div></div>';
    }
    function _tlTime(iso) {
      if (!iso) return '';
      return ' <span class="uxq-tl-time" title="' + (0, sharedState.escapeAttr)(iso) + '">' + (0, sharedState.escapeHtml)(_uxqRelTime(iso)) + '</span>';
    }
    function _tlWorker(w, machine) {
      return w ? ' <span class="uxq-tl-worker">' + (0, sharedState.escapeHtml)(_uxqWithMachine(w, machine).slice(0, 20)) + '</span>' : '';
    }
    function _fmtRes(v) {
      const arr = Array.isArray(v) ? v : [v];
      return arr.filter(Boolean).map(x => (0, sharedState.escapeHtml)(String(x))).join('<br>');
    }
    function _sessionBtn(sid, label) {
      return '<button type="button" class="uxq-tl-session-btn" data-sid="' + (0, sharedState.escapeAttr)(sid) + '">' + (0, sharedState.escapeHtml)(label) + ' ↗</button>';
    }

    function _tlActor(ev) {
      const by = (ev && ev.by && typeof ev.by === 'object') ? ev.by : {};
      return {
        worker: by.worker || '',
        session: by.session_id || '',
        kind: by.kind || '',
        machine: by.machine || '',
      };
    }
    function _tlHead(label, ev) {
      const actor = _tlActor(ev);
      return '<span class="uxq-tl-verb">' + (0, sharedState.escapeHtml)(label) + '</span>'
        + _tlTime(ev && ev.at)
        + _tlWorker(actor.worker || actor.kind, actor.worker ? actor.machine : '')
        + (actor.session ? _sessionBtn(actor.session, 'open session') : '');
    }
    function _tlText(text, cls) {
      if (!text) return '';
      const body = window.CCCTicketProse ? window.CCCTicketProse.render(text) : (0, sharedState.escapeHtml)(String(text));
      return '<div class="' + (cls || 'uxq-tl-block-q') + '">' + body + '</div>';
    }
    // One chip per caveat/follow-up/unresolved entry, each with an ack
    // toggle — parity with WatchTower's own dashboard (queue.ack_resolution /
    // is_acked, CCC-997). An acked chip keeps its text (dimmed, struck
    // through) rather than disappearing: the point is to clear the visual
    // alarm without touching the close record. `ref` is omitted for
    // resolutions that aren't a closed ticket's own live `item.resolution`
    // (acks only make sense there), which suppresses the toggle button.
    function _tlResChips(res, field, cls, label, ref) {
      const list = res[field] || [];
      if (!list.length) return '';
      const chips = list.map((val, idx) => {
        const acked = !!ref && _q_isAcked(res, field, idx);
        const btn = ref
          ? '<button type="button" class="uxq-ack-btn' + (acked ? ' is-acked-btn' : '') + '" title="' + (acked ? 'Un-acknowledge' : 'Acknowledge (keeps the record)') + '" '
            + 'data-ack-ref="' + (0, sharedState.escapeAttr)(ref) + '" data-ack-field="' + (0, sharedState.escapeAttr)(field) + '" data-ack-index="' + idx + '" data-ack-acked="' + (acked ? 'true' : 'false') + '">'
            + (acked ? '↺ Acked' : '✓ Ack') + '</button>'
          : '';
        // Long notes (e.g. a multi-sentence unresolved writeup) are unreadable
        // crammed into an inline chip — expand into a focused, larger-font
        // modal instead of forcing the user to squint at wrapped chip text
        // (CCC-1023).
        const expandBtn = '<button type="button" class="uxq-note-expand-btn" title="Expand" '
          + 'data-expand-label="' + (0, sharedState.escapeAttr)(label) + '" data-expand-text="' + (0, sharedState.escapeAttr)(String(val)) + '">+</button>';
        return '<span class="uxq-tl-chip ' + cls + (acked ? ' is-acked' : '') + '">'
          + '<span class="uxq-tl-chip-k">' + (0, sharedState.escapeHtml)(label) + '</span> '
          + '<span class="uxq-tl-chip-v">' + (0, sharedState.escapeHtml)(String(val)) + '</span>' + expandBtn + btn + '</span>';
      });
      return '<div class="uxq-tl-res-row uxq-tl-res-' + cls + '"><div class="uxq-tl-chips">' + chips.join('') + '</div></div>';
    }
    function _q_isAcked(res, field, idx) {
      const acks = res && res[field + '_ack'];
      return !!(acks && typeof acks === 'object' && Object.prototype.hasOwnProperty.call(acks, String(idx)));
    }
    function _tlResolution(resolution, ref) {
      const res = (resolution && typeof resolution === 'object') ? resolution : {};
      const summary = res.summary || '';
      const caveats = res.caveats || res.caveat || [];
      const followUps = res.follow_ups || res.follow_up || [];
      const unresolved = res.unresolved || [];
      if (!(summary || caveats.length || followUps.length || unresolved.length)) return '';
      return '<div class="uxq-tl-res">'
        + (summary ? '<div class="uxq-tl-res-row"><span class="uxq-tl-res-k">Summary</span><div class="uxq-tl-res-v">' + _fmtRes(summary) + '</div></div>' : '')
        + _tlResChips(res, 'caveats', 'caveat', 'Caveat', ref)
        + _tlResChips(res, 'follow_ups', 'follow', 'Follow-up', ref)
        + _tlResChips(res, 'unresolved', 'unresolved', 'Unresolved', ref)
        + '</div>';
    }
    function _tlEditFields(fields) {
      const obj = (fields && typeof fields === 'object') ? fields : {};
      const keys = Object.keys(obj).sort();
      if (!keys.length) return '';
      return '<div class="uxq-tl-edit-fields">'
        + keys.map(k => '<div><span>' + (0, sharedState.escapeHtml)(k) + '</span> ' + (0, sharedState.escapeHtml)(String(obj[k])) + '</div>').join('')
        + '</div>';
    }
    function _tlRenderEvent(ev) {
      const type = String((ev && ev.event) || '');
      if (type === 'filed') {
        // Who opened the ticket (CCC-1166): wt folds the ticket's `submitter`
        // (or the GitHub issue author) onto the filed event at read time;
        // fall back to the item fields for a queue backend that doesn't.
        const filer = String(ev.submitter || item.submitter || item.github_author || '').trim();
        return _tlEvt('uxq-tl-filed', _tlHead('Filed', ev)
          + (filer ? ' <span class="uxq-tl-meta">by ' + (0, sharedState.escapeHtml)(filer) + '</span>' : '')
          + (ev.source ? ' <span class="uxq-tl-meta">via ' + (0, sharedState.escapeHtml)(ev.source) + '</span>' : ''),
          ev.project ? '<span class="uxq-tl-tag">' + (0, sharedState.escapeHtml)(ev.project) + '</span>' : '');
      }
      if (type === 'claim') return _tlEvt('uxq-tl-claimed', _tlHead('Claimed', ev), '');
      if (type === 'progress') return _tlEvt('uxq-tl-progress', _tlHead('Progress', ev), _tlText(ev.text, 'uxq-tl-sub-note'));
      if (type === 'block') return _tlEvt('uxq-tl-blocked', _tlHead('Blocked', ev),
        window.CCCTicketProse && window.CCCTicketProse.renderBlocked
          ? window.CCCTicketProse.renderBlocked(ev.question) : _tlText(ev.question));
      if (type === 'answer') return _tlEvt('uxq-tl-answer', _tlHead('Answered', ev), _tlText(ev.text, 'uxq-tl-sub-note uxq-tl-sub-answer'));
      if (type === 'comment') return _tlEvt('uxq-tl-comment', _tlHead('Comment', ev), _tlText(ev.text));
      if (type === 'reopen') return _tlEvt('uxq-tl-reopen', _tlHead('Reopened', ev), _tlText(ev.reason));
      // WT-5 gate events: the close landed in review, then accept / reject.
      if (type === 'in_review') return _tlEvt('uxq-tl-progress', _tlHead('Awaiting review', ev), '');
      if (type === 'accept') return _tlEvt('uxq-tl-closed', _tlHead('Accepted', ev), '');
      if (type === 'reject') return _tlEvt('uxq-tl-reopen', _tlHead('Rejected', ev), _tlText(ev.reason || ev.text));
      if (type === 'verify') return _tlEvt(ev.passed === false ? 'uxq-tl-reopen' : 'uxq-tl-closed',
        _tlHead(ev.passed === false ? 'Verifier failed' : 'Verifier passed', ev), _tlText(ev.findings));
      // Prefer the ticket's live `resolution` over the close event's
      // snapshot: `wt ack` mutates the live field (see queue.ack_resolution)
      // and never rewrites the historical close record, so the snapshot
      // alone would never show an acked chip as acked (CCC-997).
      if (type === 'close') return _tlEvt('uxq-tl-closed', _tlHead('Closed', ev), _tlResolution(item.resolution || ev.resolution, ref));
      if (type === 'move') {
        const body = [ev.from_ref, ev.to_ref].filter(Boolean).join(' -> ');
        return _tlEvt('uxq-tl-move', _tlHead('Moved', ev), body ? '<div class="uxq-tl-sub-note">' + (0, sharedState.escapeHtml)(body) + '</div>' : '');
      }
      if (type === 'edit') return _tlEvt('uxq-tl-edit', _tlHead('Edited', ev), _tlEditFields(ev.fields));
      return _tlEvt('uxq-tl-comment', _tlHead(type || 'Event', ev), ev.text ? _tlText(ev.text) : '');
    }

    const editCount = timeline.filter(ev => ev && ev.event === 'edit').length;
    let tlHtml = timeline.map(_tlRenderEvent).join('');
    const editToggleHtml = editCount
      ? '<label class="uxq-show-edits"><input type="checkbox" class="uxq-show-edits-input"> show edits (' + editCount + ')</label>'
      : '';

    if (!item.closed_at) {
      const verb = status === 'in_progress' ? 'In progress' : status === 'blocked'
        ? (attention && !attention.owner && attention.key !== 'gate' ? 'Blocked: ' + attention.label : 'Agent needs your input')
        : status === 'in_review' ? 'Closed by worker, ' + _uxqReviewLabel(item) : 'Open';
      tlHtml += _tlEvt('uxq-tl-open',
        '<span class="uxq-tl-verb">' + verb + '</span>' + _tlWorker(item.claimed_by), '');
    }

    // Sidebar: editable selects + info
    const sideHtml =
      '<div class="uxq-td-pg uxq-td-pg-properties">'
      + '<div class="uxq-td-pg-label">Properties</div>'
      + _propSel('Priority', 'priority',   [['','-'],['p0','p0 - urgent'],['p1','p1'],['p2','p2'],['p3','p3']], item.priority || '')
      + _propSel('Type', 'type',           [['','-'],['feature','feature'],['bug','bug']], item.type || '')
      + _propSel('Readiness', 'readiness', [['','-'],['ready','ready'],['needs-spec','needs-spec'],['needs-shaping','needs-shaping'],['needs-rationale','needs-rationale (parked)']], item.readiness || '')
      + _propSel('Value', 'value',         [['','-'],['H','H - High'],['M','M - Med'],['L','L - Low']], item.value || '')
      + _propSel('Confidence', 'confidence',[['','-'],['H','H - High'],['M','M - Med'],['L','L - Low']], item.confidence || '')
      + _propSel('Runs on', 'placement',    [['','any machine (default)'],['any','any (cloud ok)'],['mac','Mac only'],['cloud','cloud only']], item.placement || '')
      + '</div>'
      + '<div class="uxq-td-pg">'
      + '<div class="uxq-td-pg-label">Assignment</div>'
      + (item.claimed_by ? _propRow('Worker', '<span class="uxq-td-worker-id">' + (0, sharedState.escapeHtml)(item.claimed_by) + '</span>')
        : (!item.closed_at && item.parked && item.parked.worker_id)
          ? _propRow('Worker', '<span class="uxq-td-worker-id">' + (0, sharedState.escapeHtml)(item.parked.worker_id) + '</span> <span class="uxq-td-pr-empty">(parked)</span>')
          : _propRow('Worker', '<span class="uxq-td-pr-empty">unassigned</span>'))
      + (function () {
          // claimed_session_id is backfilled by the queue reconciler and can
          // stay empty for a worker-id claim (e.g. "acmeprod-cda69c9a") even
          // long after claim — the button then silently vanishes (CCC-937).
          // Fall back to matching claimed_by against a live conversation's
          // _worker_id so the link still resolves; when the conversation
          // isn't loaded yet (filtered list, cold start) or lives in another
          // repo, ask the live worker roster — it carries the session id the
          // claim runs under (CCC-1148).
          const raw = String(item.claimed_session_id || '').trim();
          let sid = raw;
          if (!sid && item.claimed_by) {
            const key = (0, sharedState._uxFixesIdentityKey)(item.claimed_by);
            const hit = (sharedState.conversationsData || []).find(c => c && (0, sharedState._uxFixesRowIdentityKeys)(c).indexOf(key) !== -1);
            if (hit) sid = hit.session_id || hit.id || '';
            if (!sid) {
              const roster = Array.isArray((sharedState._uxqHealthCache || {}).wt_workers)
                ? sharedState._uxqHealthCache.wt_workers : [];
              const w = roster.find(w => w && (0, sharedState._uxFixesIdentityKey)(w.worker_id || '') === key
                && String(w.session_id || '').trim());
              if (w) {
                sid = String(w.session_id).trim();
                // WT stores a devin worker's session_id as the raw CLI slug;
                // the conversation id is devincli-<slug> (CCC-1176).
                if (String(w.engine || '').toLowerCase() === 'devin'
                    && sid.indexOf('devin') !== 0) sid = 'devincli-' + sid;
              }
            }
            if (!sid) {
              // The worker already exited (e.g. viewing a closed ticket): the
              // live roster no longer carries it. past_workers covers ~24h
              // via the server-side log scan; the durable worker_session_map
              // covers anything older and engines whose logs have no
              // extractable session id (devin -p — CCC-1179).
              const past = Array.isArray((sharedState._uxqHealthCache || {}).past_workers)
                ? sharedState._uxqHealthCache.past_workers : [];
              const p = past.find(p => p && (0, sharedState._uxFixesIdentityKey)(p.worker_id || '') === key
                && String(p.session_id || '').trim());
              if (p) sid = String(p.session_id).trim();
            }
            if (!sid) {
              const map = (sharedState._uxqHealthCache || {}).worker_session_map || {};
              for (const wid in map) {
                if ((0, sharedState._uxFixesIdentityKey)(wid) === key && String(map[wid] || '').trim()) {
                  sid = String(map[wid]).trim();
                  break;
                }
              }
            }
          }
          // `wt block` drops the claim (claimed_by and claimed_session_id go
          // null) and records the session under `parked`, so a blocked ticket
          // lost its link exactly when the user needed it. Fall back to the
          // parked session, then the newest timeline actor, like q2.js
          // sessionOf().
          if (!sid && item.parked && item.parked.session_id) {
            sid = String(item.parked.session_id).trim();
          }
          if (!sid && !item.claimed_by) {
            const tl = Array.isArray(item.timeline) ? item.timeline : [];
            for (let i = tl.length - 1; i >= 0 && !sid; i--) {
              const by = tl[i] && tl[i].by;
              if (by && typeof by === 'object' && by.session_id) sid = String(by.session_id).trim();
            }
          }
          if (!sid) return '';
          return '<div class="uxq-td-pr"><span class="uxq-td-pr-k">Session</span>'
            + '<button type="button" class="uxq-td-session-btn" data-sid="' + (0, sharedState.escapeAttr)(sid) + '">open in CCC ↗</button>'
            + '</div>';
        })()
      + (function () {
          // Live engine/model/effort of the worker's session, so the
          // detail page shows who is actually doing the work right now
          // (not just the worker id string) — CCC-735.
          if (item.closed_at || !item.claimed_session_id) return '';
          const sid = item.claimed_session_id;
          const conv = (sharedState.conversationsData || []).find(c => c && (c.session_id === sid || c.id === sid));
          if (!conv) return '';
          const ovr = conv.override || null;
          const engine = conv.engine || (ovr && ovr.engine) || '';
          const model = conv.model || (ovr && ovr.model) || '';
          const effort = conv.reasoning_effort || (ovr && ovr.reasoning_effort) || '';
          const bits = [engine, model, effort].filter(Boolean);
          if (!bits.length) return '';
          return _propRow('Engine', '<span class="uxq-td-mono">' + (0, sharedState.escapeHtml)(bits.join(' · ')) + '</span>');
        })()
      + (item.claimed_at ? _propRow('Claimed', '<span class="uxq-td-pr-time" title="' + (0, sharedState.escapeAttr)(item.claimed_at) + '">' + (0, sharedState.escapeHtml)(_uxqRelTime(item.claimed_at)) + '</span>') : '')
      + (item.closed_at  ? _propRow('Closed',  '<span class="uxq-td-pr-time" title="' + (0, sharedState.escapeAttr)(item.closed_at)  + '">' + (0, sharedState.escapeHtml)(_uxqRelTime(item.closed_at))  + '</span>') : '')
      + '</div>'
      + '<div class="uxq-td-pg uxq-td-pg-origin">'
      + '<div class="uxq-td-pg-label">Origin</div>'
      + (item.project  ? _propRow('Project', (0, sharedState.escapeHtml)(item.project)) : '')
      + (function () {
          // Who opened the ticket (CCC-1166). `submitter` is wt's filer
          // field -- `wt add` auto-captures the filing session, the CCC
          // annotate path records the operator name; `github_author` is the
          // issue author on GitHub-backed queues. A filer that resolves to a
          // loaded conversation gets an open-in-CCC link like the Session row.
          const sub = String(item.submitter || '').trim();
          const gh = String(item.github_author || '').trim();
          if (!sub && !gh) return '';
          let html = '';
          if (sub) {
            html = '<span class="uxq-td-mono">' + (0, sharedState.escapeHtml)(sub) + '</span>';
            const conv = (sharedState.conversationsData || []).find(c => c && (c.session_id === sub || c.id === sub));
            if (conv) {
              const sid = conv.session_id || conv.id;
              html += ' <button type="button" class="uxq-td-session-btn" data-sid="' + (0, sharedState.escapeAttr)(sid) + '">open in CCC ↗</button>';
            }
          }
          if (gh) {
            html += (html ? ' · ' : '')
              + '<a class="uxq-td-link" href="https://github.com/' + (0, sharedState.escapeAttr)(gh)
              + '" target="_blank" rel="noopener">@' + (0, sharedState.escapeHtml)(gh) + '</a>';
          }
          return _propRow('Filed by', html);
        })()
      + (item.project  ? _propRow('Learnings', '<button type="button" class="uxq-td-learnings-btn" data-queue="' + (0, sharedState.escapeAttr)(item.project) + '">edit learning file ↗</button>') : '')
      + (item.source   ? _propRow('Source',  (0, sharedState.escapeHtml)(item.source)) : '')
      + (item.lane     ? _propRow('Lane',    (0, sharedState.escapeHtml)(item.lane)) : '')
      + (item.repo_path ? _propRow('Repository', '<span class="uxq-td-mono">' + (0, sharedState.escapeHtml)(item.repo_path) + '</span>') : '')
      + (item.url      ? _propRow('URL',     '<a href="' + (0, sharedState.escapeAttr)(item.url) + '" target="_blank" rel="noopener" class="uxq-td-link">' + (0, sharedState.escapeHtml)(item.url) + '</a>') : '')
      + (item.selector ? _propRow('Selector','<span class="uxq-td-mono">' + (0, sharedState.escapeHtml)(item.selector) + '</span>') : '')
      + '</div>';

    // Answer section (only when currently blocked)
    const isGated = item.needs_input && item.block_kind === 'rationale';
    const answerSectionHtml = item.needs_input
      ? (isGated
          ? '<div class="uxq-td-sec uxq-td-gate-sec">'
            + '<div class="uxq-td-sec-label">Product Gate Decision</div>'
            + '<div class="uxq-gate-row" style="display:flex;gap:8px;align-items:center;">'
            + '<button type="button" class="ann-btn ann-primary uxq-gate-ack" data-ref="' + (0, sharedState.escapeAttr)(ref) + '">Ack</button>'
            + '<button type="button" class="ann-btn uxq-gate-ack-plus" data-ref="' + (0, sharedState.escapeAttr)(ref) + '">Ack+</button>'
            + '<button type="button" class="ann-btn ann-warn uxq-gate-nack" data-ref="' + (0, sharedState.escapeAttr)(ref) + '">Nack</button>'
            + '</div>'
            + '<div class="uxq-td-answer-hint" style="margin-top:6px;">Approve or decline this product-gate pitch. CLI: <code>wt ack ' + (0, sharedState.escapeHtml)(ref) + '</code> / <code>wt nack ' + (0, sharedState.escapeHtml)(ref) + '</code></div>'
            + '</div>'
          : '<div class="uxq-td-sec uxq-td-answer-sec">'
            + '<div class="uxq-td-sec-label">' + (attention && !attention.owner
              ? (0, sharedState.escapeHtml)('Not waiting on you: ' + attention.label + '. Answer only to override.')
              : 'Your Decision') + '</div>'
            + '<div class="uxq-answer-row">'
            + '<input class="uxq-answer-input" type="text" placeholder="Type your decision / answer…" aria-label="Answer this blocked ticket">'
            + '<button type="button" class="ann-btn ann-primary uxq-answer-send">Send</button>'
            + '</div>'
            + '<div class="uxq-td-answer-hint">Sends your decision to the worker. CLI: <code>wt discuss ' + (0, sharedState.escapeHtml)(ref) + '</code></div>'
            + '</div>')
      : '';

    // Reopen section (only when closed) — a real textarea in the main
    // column next to Activity instead of a single-line input squeezed into
    // the footer button row (CCC-422: "need more space... why not as part
    // of the chronological view?"). Mirrors the Answer section's placement.
    const reopenSectionHtml = item.status === 'closed'
      ? '<div class="uxq-td-sec uxq-td-reopen-sec">'
        + '<div class="uxq-td-sec-label">Reopen</div>'
        + '<textarea class="uxq-td-reopen-input" rows="3" placeholder="Reason for reopening (optional)" aria-label="Reason for reopening"></textarea>'
        + '<div class="uxq-reopen-row">'
        + '<button type="button" class="ann-btn ann-primary uxq-td-reopen-confirm">Reopen ticket</button>'
        + '</div>'
        + '</div>'
      : '';

    // Comment section — always available (open, in_progress, blocked, or
    // closed): a plain status update, not a resolution (CCC-436). Same
    // textarea treatment as Reopen/Mark-as-Closed above.
    const commentSectionHtml =
      '<div class="uxq-td-sec uxq-td-comment-sec">'
      + '<div class="uxq-td-sec-label">Add comment</div>'
      + '<textarea class="uxq-td-comment-input" rows="2" placeholder="Log an update - not a resolution" aria-label="Add a comment"></textarea>'
      + '<div class="uxq-reopen-row">'
      + '<button type="button" class="ann-btn uxq-td-comment-confirm">Add comment</button>'
      + '</div>'
      + '</div>';

    // Mark-as-closed section (only when NOT closed, CCC-423): a human
    // resolving a ticket by hand from the CCC UI had no way to leave a
    // resolution summary the way a worker's `wt close --summary` does —
    // "Close" in the footer only closed the modal. Same textarea treatment
    // as Reopen above.
    // in_review (WT-5 gate): the worker already closed it; what is left is
    // Accept (close + unblock dependents) or Reject (back to open with a
    // reason, worker resumes) -- `wt accept` / `wt reject` server-side.
    const reviewSectionHtml = item.status === 'in_review'
      ? '<div class="uxq-td-sec uxq-td-review-sec">'
        + '<div class="uxq-td-sec-label">Review (' + (0, sharedState.escapeHtml)(_uxqReviewLabel(item)) + ')</div>'
        + (item.accept ? '<div class="uxq-tl-sub-note">Accept when: ' + (0, sharedState.escapeHtml)(item.accept) + '</div>' : '')
        + '<textarea class="uxq-td-review-input" rows="2" placeholder="Reject reason: what must change (required to reject)" aria-label="Reject reason"></textarea>'
        + '<div class="uxq-reopen-row">'
        + '<button type="button" class="ann-btn ann-primary uxq-td-review-accept">Accept</button>'
        + '<button type="button" class="ann-btn uxq-td-review-reject">Reject</button>'
        + '</div>'
        + '</div>'
      : '';
    const closeSectionHtml = item.status === 'in_review' ? reviewSectionHtml : item.status !== 'closed'
      ? '<div class="uxq-td-sec uxq-td-close-sec">'
        + '<div class="uxq-td-sec-label">Mark as Closed</div>'
        + '<textarea class="uxq-td-mark-closed-input" rows="3" placeholder="Resolution summary (optional)" aria-label="Resolution summary"></textarea>'
        + '<div class="uxq-reopen-row">'
        + '<button type="button" class="ann-btn ann-primary uxq-td-mark-closed-confirm">Mark as closed</button>'
        + '</div>'
        + '</div>'
      : '';

    // Build modal DOM
    const modal = document.createElement('div');
    modal.id = 'uxqItemModal';
    modal.className = 'uxq-td-overlay';
    modal.innerHTML =
      '<div class="uxq-td-card" role="dialog" aria-modal="true" aria-label="Ticket ' + (0, sharedState.escapeAttr)(ref) + '">'
      + '<div class="uxq-td-topbar">'
      +   '<div class="uxq-td-topbar-left"><span class="uxq-td-ref">' + (0, sharedState.escapeHtml)(ref) + '</span>' + topBadges + '</div>'
      +   '<div class="uxq-td-zoom" role="group" aria-label="Font size">'
      +     '<button type="button" class="uxq-td-zoom-btn" data-zoom-dir="-1" aria-label="Decrease font size" title="Decrease font size">−</button>'
      +     '<button type="button" class="uxq-td-zoom-btn" data-zoom-dir="1" aria-label="Increase font size" title="Increase font size">+</button>'
      +   '</div>'
      +   '<button type="button" class="uxq-td-x" aria-label="Close" data-ux-close>×</button>'
      + '</div>'
      + '<div class="uxq-td-title-wrap">'
      +   '<div class="uxq-td-title"' + (githubBacked ? '' : ' contenteditable="true"')
      +   ' spellcheck="true" data-field="note" role="textbox" aria-label="Ticket title">'
      +   '<span class="uxq-td-title-first">' + (0, sharedState.escapeHtml)(titleParts[0]) + '</span>'
      +   (titleParts[1] ? '<span class="uxq-td-title-rest">' + (0, sharedState.escapeHtml)(titleParts[1]) + '</span>' : '')
      +   '</div>'
      + '</div>'
      + '<div class="uxq-td-cols">'
      +   '<div class="uxq-td-main">'
      +     promptHtml
      +     _uxqChecksHtml(item)
      +     convSecHtml
      +     '<details class="uxq-td-sec uxq-session-activity"><summary>Session activity</summary>'
      +       '<div class="uxq-session-controls"><label>UTC day <input type="date" aria-label="Filter session activity by UTC day"></label>'
      +       '<button type="button" class="ann-btn">Refresh</button></div>'
      +       '<div data-session-activity-body aria-live="polite">Open to load recorded inputs, drivers and tokens.</div></details>'
      +     imagesHtml
      +     '<div class="uxq-td-sec"><div class="uxq-td-sec-label">Activity' + editToggleHtml + '</div>'
      +       '<div class="uxq-timeline">' + tlHtml + '</div>'
      +     '</div>'
      +     answerSectionHtml
      +     commentSectionHtml
      +     reopenSectionHtml
      +     closeSectionHtml
      +   '</div>'
      +   '<aside class="uxq-td-sidebar">' + sideHtml + '</aside>'
      + '</div>'
      + '<div class="uxq-td-footer">'
      +   '<div class="uxq-td-footer-meta">'
      +     '<span title="' + (0, sharedState.escapeAttr)(item.created_at || '') + '">Filed ' + (0, sharedState.escapeHtml)(_uxqRelTime(item.created_at)) + '</span>'
      +     (item.updated_at && item.updated_at !== item.created_at ? ' · <span title="' + (0, sharedState.escapeAttr)(item.updated_at) + '">updated ' + (0, sharedState.escapeHtml)(_uxqRelTime(item.updated_at)) + '</span>' : '')
      +   '</div>'
      +   '<div class="uxq-td-footer-btns">'
      +     (canRequestRun
        ? '<button type="button" class="ann-btn ann-primary uxq-td-run" data-ux-run data-run-cancel="' + (runQueued ? '1' : '0') + '"'
          + (runBusy ? ' disabled aria-busy="true"' : '') + '>'
          + (runQueued ? 'Cancel queued run' : '▶ Run now') + '</button>'
        : '')
      +     '<button type="button" class="ann-btn" data-ux-latest>Latest activity</button>'
      +     '<button type="button" class="ann-btn" data-ux-copy>Copy prompt</button>'
      +   '</div>'
      + '</div>'
      + '</div>';

    document.body.appendChild(modal);
    _uxqBindSessionActivity(modal, ref);
    if (window.CCCTicketProse && window.CCCTicketProse.hydrateBlocked) {
      const latestBlock = timeline.filter(ev => ev && ev.event === 'block').slice(-1)[0];
      window.CCCTicketProse.hydrateBlocked(modal,
        status === 'blocked' && item.needs_input && latestBlock ? String(latestBlock.question || '').trim() : '');
    }
    if (window.cccBlockParams && (item.needs_input || status === 'blocked')) {
      const bp = window.cccBlockParams(ref);
      const host = modal.querySelector('.uxq-td-main');
      const tl = modal.querySelector('.uxq-timeline');
      if (bp && host) host.insertBefore(bp, tl ? tl.parentNode : null);
    }
    _uxqLoadLinkedConversation(modal, ref);
    _uxqInitTicketZoom(modal);

    const close = () => { modal.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if ((0, sharedState.isImeKey)(e)) return; if (e.key === 'Escape') { e.preventDefault(); close(); } };
    document.addEventListener('keydown', onKey, true);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });

    modal.querySelectorAll('[data-ux-close]').forEach(btn => btn.addEventListener('click', close));

    modal.querySelector('[data-ux-latest]').addEventListener('click', () => {
      const events = Array.from(modal.querySelectorAll('.uxq-timeline > .uxq-tl-evt:not(.uxq-tl-open)'))
        .filter(el => el.getClientRects().length);
      const target = events[events.length - 1] || modal.querySelector('.uxq-timeline');
      if (!target) return;
      target.setAttribute('tabindex', '-1');
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: 'start', behavior: 'instant' });
    });

    // Top badges (status/lane/priority/type) jump to the section that
    // explains or acts on that badge, instead of sitting there inert
    // (CCC-1027).
    modal.querySelectorAll('.uxq-td-badge-jump').forEach(badge => {
      const jump = () => {
        const sel = badge.getAttribute('data-jump-target');
        const target = sel ? modal.querySelector(sel) : null;
        if (!target) return;
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.classList.add('uxq-jump-flash');
        setTimeout(() => target.classList.remove('uxq-jump-flash'), 900);
      };
      badge.addEventListener('click', jump);
      badge.addEventListener('keydown', (e) => {
        if ((0, sharedState.isImeKey)(e)) return;
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); jump(); }
      });
    });

    const showEditsInput = modal.querySelector('.uxq-show-edits-input');
    if (showEditsInput) {
      const timelineEl = modal.querySelector('.uxq-timeline');
      showEditsInput.addEventListener('change', () => {
        if (timelineEl) timelineEl.classList.toggle('uxq-show-edits', showEditsInput.checked);
      });
    }

    // Ack/un-ack a caveat/follow-up/unresolved chip in place (CCC-997) — no
    // full modal rebuild, just flip the button + chip classes and mirror the
    // change into `item.resolution` so reopening this same modal (from the
    // still-cached item) doesn't show a stale un-acked chip.
    modal.querySelectorAll('.uxq-ack-btn').forEach(btn => {
      btn.addEventListener('click', async () => {
        const field = btn.getAttribute('data-ack-field');
        const index = parseInt(btn.getAttribute('data-ack-index'), 10);
        const wasAcked = btn.getAttribute('data-ack-acked') === 'true';
        btn.disabled = true;
        try {
          const res = await fetch('/api/ux-fixes/ack', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, field, index, undo: wasAcked }),
          });
          const d = await res.json().catch(() => ({}));
          if (!res.ok || !d.ok) {
            (0, sharedState.showOpToast)('Ack failed: ' + (d.error || res.status), 'error');
            btn.disabled = false;
            return;
          }
          const nowAcked = !wasAcked;
          const chip = btn.closest('.uxq-tl-chip');
          if (chip) chip.classList.toggle('is-acked', nowAcked);
          btn.setAttribute('data-ack-acked', nowAcked ? 'true' : 'false');
          btn.title = nowAcked ? 'Un-acknowledge' : 'Acknowledge (keeps the record)';
          btn.textContent = nowAcked ? '↺' : '✓';
          if (item.resolution && typeof item.resolution === 'object') {
            const key = field + '_ack';
            if (nowAcked) {
              item.resolution[key] = item.resolution[key] || {};
              item.resolution[key][index] = { at: new Date().toISOString(), by: 'dashboard' };
            } else if (item.resolution[key]) {
              delete item.resolution[key][index];
            }
          }
          _uxqReplaceCachedItem(d.item);
        } catch (e) {
          (0, sharedState.showOpToast)('Ack failed: ' + e, 'error');
        } finally {
          btn.disabled = false;
        }
      });
    });

    modal.querySelectorAll('.uxq-note-expand-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        _uxqOpenNoteModal(btn.getAttribute('data-expand-label') || 'Note', btn.getAttribute('data-expand-text') || '');
      });
    });

    const copyBtn = modal.querySelector('[data-ux-copy]');
    if (copyBtn) {
      copyBtn.addEventListener('click', async () => {
        try { await navigator.clipboard.writeText(promptText); (0, sharedState.showOpToast)('Copied', 'success'); }
        catch (_) {}
      });
    }

    // Keep the ticket-detail action on the same persisted, reconciler-owned
    // path as the Queue-row play control. CCC queues (or cancels) work; it
    // never spawns a worker itself.
    const runBtn = modal.querySelector('[data-ux-run]');
    if (runBtn) {
      runBtn.addEventListener('click', async () => {
        if (sharedState._uxqRunBusyRefs.has(ref)) return;
        const cancel = runBtn.getAttribute('data-run-cancel') === '1';
        const verb = cancel ? 'Cancel' : 'Run';
        sharedState._uxqRunBusyRefs.add(ref);
        runBtn.disabled = true;
        try {
          const res = await fetch('/api/ux-fixes/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, cancel }),
          });
          const data = await res.json().catch(() => ({}));
          if (res.ok && data.ok) {
            if (data.warning) (0, sharedState.showOpToast)(data.warning, 'error');
            else (0, sharedState.showOpToast)(cancel ? ('Cancelled the queued run for ' + ref)
              : ('Queued ' + ref + ' to run'));
            sharedState._uxqItemsCache.ts = 0;
            sharedState._uxqHealthCache.ts = 0;
            (0, hostServices._renderQueuePanel)();
            runBtn.setAttribute('data-run-cancel', cancel ? '0' : '1');
            runBtn.textContent = cancel ? '▶ Run now' : 'Cancel queued run';
          } else {
            (0, sharedState.showOpToast)(verb + ' failed: ' + (data.error || res.status), 'error');
          }
        } catch (e) {
          (0, sharedState.showOpToast)(verb + ' failed: ' + e, 'error');
        } finally {
          sharedState._uxqRunBusyRefs.delete(ref);
          runBtn.disabled = false;
        }
      });
    }

    // Reopen (closed tickets only) — full-width textarea section next to
    // Activity, always visible when closed (no extra reveal click needed).
    const reopenNoteInput = modal.querySelector('.uxq-td-reopen-input');
    const reopenConfirmBtn = modal.querySelector('.uxq-td-reopen-confirm');
    if (reopenConfirmBtn) {
      const doReopen = async () => {
        reopenConfirmBtn.disabled = true;
        try {
          const res = await fetch('/api/ux-fixes/reopen', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, note: (reopenNoteInput ? reopenNoteInput.value.trim() : '') }),
          });
          const d = await res.json().catch(() => ({}));
          if (res.ok && d.ok) {
            (0, sharedState.showOpToast)('Ticket reopened', 'success');
            sharedState._uxqItemsCache.ts = 0; sharedState._uxqHealthCache.ts = 0;
            (0, hostServices._renderQueuePanel)(); close();
          } else {
            (0, sharedState.showOpToast)('Reopen failed: ' + (d.error || res.status), 'error');
            reopenConfirmBtn.disabled = false;
          }
        } catch (e) {
          (0, sharedState.showOpToast)('Reopen failed: ' + e, 'error');
          reopenConfirmBtn.disabled = false;
        }
      };
      reopenConfirmBtn.addEventListener('click', doReopen);
      if (reopenNoteInput) {
        reopenNoteInput.addEventListener('keydown', (e) => {
          if ((0, sharedState.isImeKey)(e)) return;
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doReopen(); }
        });
      }
    }

    // Mark as closed (open/in_progress tickets only) — same textarea
    // treatment as Reopen above, POSTs a resolution summary a human can
    // leave the way `wt close --summary` does for a worker.
    const markClosedInput = modal.querySelector('.uxq-td-mark-closed-input');
    const markClosedBtn = modal.querySelector('.uxq-td-mark-closed-confirm');
    if (markClosedBtn) {
      const doMarkClosed = async () => {
        markClosedBtn.disabled = true;
        try {
          const res = await fetch('/api/ux-fixes/close', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, note: (markClosedInput ? markClosedInput.value.trim() : '') }),
          });
          const d = await res.json().catch(() => ({}));
          if (res.ok && d.ok) {
            (0, sharedState.showOpToast)('Ticket closed', 'success');
            sharedState._uxqHealthCache.ts = 0;
            if (_uxqReplaceCachedItem(d.item)) {
              (0, hostServices._renderQueuePanel)({ allowStale: true }); close();
            } else {
              sharedState._uxqItemsCache.ts = 0;
              (0, hostServices._renderQueuePanel)(); close();
            }
          } else {
            (0, sharedState.showOpToast)('Close failed: ' + (d.error || res.status), 'error');
            markClosedBtn.disabled = false;
          }
        } catch (e) {
          (0, sharedState.showOpToast)('Close failed: ' + e, 'error');
          markClosedBtn.disabled = false;
        }
      };
      markClosedBtn.addEventListener('click', doMarkClosed);
      if (markClosedInput) {
        markClosedInput.addEventListener('keydown', (e) => {
          if ((0, sharedState.isImeKey)(e)) return;
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doMarkClosed(); }
        });
      }
    }

    // Verifier session link in the Acceptance checks.
    modal.querySelectorAll('.uxq-check-session[data-open-sid]').forEach(btn => {
      btn.addEventListener('click', () => {
        const sid = btn.getAttribute('data-open-sid');
        if (!sid) return;
        close();
        (0, sharedState.selectConversation)(sid);
      });
    });

    // Accept / Reject an in_review ticket (WT-5 review gate).
    const reviewInput = modal.querySelector('.uxq-td-review-input');
    modal.querySelectorAll('.uxq-td-review-accept, .uxq-td-review-reject').forEach(btn => {
      btn.addEventListener('click', async () => {
        const accept = btn.classList.contains('uxq-td-review-accept');
        const reason = reviewInput ? reviewInput.value.trim() : '';
        if (!accept && !reason) {
          (0, sharedState.showOpToast)('Say what must change before rejecting', 'error');
          if (reviewInput) reviewInput.focus();
          return;
        }
        btn.disabled = true;
        try {
          const res = await fetch(accept ? '/api/ux-fixes/accept' : '/api/ux-fixes/reject', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, reason }),
          });
          const d = await res.json().catch(() => ({}));
          if (res.ok && d.ok) {
            (0, sharedState.showOpToast)(accept ? 'Accepted' : 'Rejected, back to open', 'success');
            sharedState._uxqHealthCache.ts = 0;
            sharedState._uxqItemsCache.ts = 0;
            (0, hostServices._renderQueuePanel)(); close();
          } else {
            (0, sharedState.showOpToast)((accept ? 'Accept' : 'Reject') + ' failed: ' + (d.error || res.status), 'error');
            btn.disabled = false;
          }
        } catch (e) {
          (0, sharedState.showOpToast)((accept ? 'Accept' : 'Reject') + ' failed: ' + e, 'error');
          btn.disabled = false;
        }
      });
    });

    // Add comment (any status) — logs a timestamped status update to the
    // canonical Activity timeline, without touching item.status.
    const commentInput = modal.querySelector('.uxq-td-comment-input');
    const commentBtn = modal.querySelector('.uxq-td-comment-confirm');
    if (commentBtn) {
      const doComment = async () => {
        const text = commentInput ? commentInput.value.trim() : '';
        if (!text) return;
        commentBtn.disabled = true;
        try {
          const res = await fetch('/api/ux-fixes/comment', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, text }),
          });
          const d = await res.json().catch(() => ({}));
          if (res.ok && d.ok) {
            // GitHub-backed tickets relay text only; pasted-image path tokens
            // are stripped server-side rather than failing (issue #101).
            (0, sharedState.showOpToast)(d.images_stripped
              ? 'Comment added - images not supported for GitHub-backed tickets, text sent without them'
              : 'Comment added', 'success');
            sharedState._uxqItemsCache.ts = 0;
            (0, hostServices._renderQueuePanel)();
            _uxqOpenItemModal(d.item || item);
          } else {
            (0, sharedState.showOpToast)('Comment failed: ' + (d.error || res.status), 'error');
            commentBtn.disabled = false;
          }
        } catch (e) {
          (0, sharedState.showOpToast)('Comment failed: ' + e, 'error');
          commentBtn.disabled = false;
        }
      };
      commentBtn.addEventListener('click', doComment);
      if (commentInput) {
        commentInput.addEventListener('keydown', (e) => {
          if ((0, sharedState.isImeKey)(e)) return;
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); doComment(); }
        });
      }
    }

    // Inline title editing via contenteditable
    const titleEl = modal.querySelector('.uxq-td-title[contenteditable]');
    if (titleEl) {
      // Compare against detailTitle (what's actually displayed/editable), not
      // raw item.note — they can differ (boilerplate stripping), and
      // comparing against the wrong baseline caused spurious saves on blur.
      const origVal = detailTitle;
      titleEl.addEventListener('blur', () => {
        const newVal = titleEl.textContent.trim();
        if (newVal !== origVal) _uxqSaveField(ref, 'note', newVal);
      });
      titleEl.addEventListener('keydown', (e) => {
        if ((0, sharedState.isImeKey)(e)) return;
        if (e.key === 'Enter') { e.preventDefault(); titleEl.blur(); }
        if (e.key === 'Escape') { titleEl.textContent = origVal; titleEl.blur(); }
      });
    }

    // Sidebar selects: auto-save on change
    modal.querySelectorAll('.uxq-td-ps[data-field]').forEach(sel => {
      sel.addEventListener('change', () => _uxqSaveField(ref, sel.getAttribute('data-field'), sel.value));
    });

    // Session links (in both timeline and sidebar)
    modal.querySelectorAll('[data-sid]').forEach(btn => {
      btn.addEventListener('click', () => {
        const sid = btn.getAttribute('data-sid');
        if (sid && typeof sharedState.selectConversation === 'function') { close(); (0, sharedState.selectConversation)(sid); }
      });
    });

    // Learning file link (queue detail screen — WT-29 follow-up)
    const learningsBtn = modal.querySelector('.uxq-td-learnings-btn');
    if (learningsBtn) {
      learningsBtn.addEventListener('click', async () => {
        const queue = learningsBtn.getAttribute('data-queue');
        if (!queue) return;
        learningsBtn.disabled = true;
        try {
          const res = await fetch('/api/queue/learnings/open', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ queue }),
          });
          const d = await res.json().catch(() => ({}));
          if (!res.ok || !d.ok) {
            (0, sharedState.showOpToast)('Could not open learning file: ' + (d.error || res.status), 'error');
          }
        } catch (e) {
          (0, sharedState.showOpToast)('Could not open learning file: ' + e, 'error');
        } finally {
          learningsBtn.disabled = false;
        }
      });
    }

    _uxqWireTicketAnswer(modal, item, close);

    // Gate actions (product gate)
    const gateAckBtn = modal.querySelector('.uxq-gate-ack');
    const gateAckPlusBtn = modal.querySelector('.uxq-gate-ack-plus');
    const gateNackBtn = modal.querySelector('.uxq-gate-nack');
    if (gateAckBtn) {
      gateAckBtn.addEventListener('click', async () => {
        gateAckBtn.disabled = true;
        try {
          const res = await fetch('/api/ux-fixes/gate-ack', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, comment: '' }),
          });
          const d = await res.json().catch(() => ({}));
          if (res.ok && d.ok) {
            (0, sharedState.showOpToast)('Product gate approved', 'success');
            sharedState._uxqItemsCache.ts = 0; sharedState._uxqHealthCache.ts = 0;
            (0, hostServices._renderQueuePanel)(); close();
          } else {
            (0, sharedState.showOpToast)('Gate Ack failed: ' + (d.error || res.status), 'error');
            gateAckBtn.disabled = false;
          }
        } catch (e) {
          (0, sharedState.showOpToast)('Gate Ack failed: ' + e, 'error');
          gateAckBtn.disabled = false;
        }
      });
    }
    if (gateAckPlusBtn) {
      gateAckPlusBtn.addEventListener('click', async () => {
        const comment = prompt('Ack with comment - steering note for the worker:');
        if (comment === null) return;
        gateAckPlusBtn.disabled = true;
        try {
          const res = await fetch('/api/ux-fixes/gate-ack', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, comment }),
          });
          const d = await res.json().catch(() => ({}));
          if (res.ok && d.ok) {
            (0, sharedState.showOpToast)('Product gate approved with comment', 'success');
            sharedState._uxqItemsCache.ts = 0; sharedState._uxqHealthCache.ts = 0;
            (0, hostServices._renderQueuePanel)(); close();
          } else {
            (0, sharedState.showOpToast)('Gate Ack failed: ' + (d.error || res.status), 'error');
            gateAckPlusBtn.disabled = false;
          }
        } catch (e) {
          (0, sharedState.showOpToast)('Gate Ack failed: ' + e, 'error');
          gateAckPlusBtn.disabled = false;
        }
      });
    }
    if (gateNackBtn) {
      gateNackBtn.addEventListener('click', async () => {
        const reason = prompt('Nack - WHY is this not being built? (required)');
        if (!reason) return;
        const closeTicket = confirm('OK = icebox (not now).\nCancel then re-Nack with --close in the CLI for "not ever".\n\nIcebox this ticket?');
        if (!closeTicket) return;
        gateNackBtn.disabled = true;
        try {
          const res = await fetch('/api/ux-fixes/gate-nack', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ref, reason, close: false }),
          });
          const d = await res.json().catch(() => ({}));
          if (res.ok && d.ok) {
            (0, sharedState.showOpToast)('Product gate declined (iceboxed)', 'success');
            sharedState._uxqItemsCache.ts = 0; sharedState._uxqHealthCache.ts = 0;
            (0, hostServices._renderQueuePanel)(); close();
          } else {
            (0, sharedState.showOpToast)('Gate Nack failed: ' + (d.error || res.status), 'error');
            gateNackBtn.disabled = false;
          }
        } catch (e) {
          (0, sharedState.showOpToast)('Gate Nack failed: ' + e, 'error');
          gateNackBtn.disabled = false;
        }
      });
    }
  }

  function _uxqLegacySessionCreatedQueue() {
    try {
      const map = JSON.parse(localStorage.getItem(sharedState._UXQ_SESSION_CREATED_QUEUE_LS) || '{}') || {};
      return String(map[_uxqScopeKey()] || '').toUpperCase();
    } catch (_) { return ''; }
  }

  function _uxqSuggestSessionQueueName(title, fallback) {
    const words = String(title || '').match(/[A-Za-z0-9]+/g) || [];
    const picked = words.filter(w => !sharedState._UXQ_NAME_STOPWORDS.has(w.toLowerCase()));
    let name = (picked.length ? picked : words).slice(0, 3).join('-').toUpperCase().slice(0, 64);
    if (!name) name = String(fallback || '').toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
    if (!name || !/^[A-Z0-9]/.test(name)) name = 'Q-' + (name || 'SESSION');
    return name.slice(0, 64);
  }

  function _uxqNormalizeQueueName(raw) {
    return String(raw || '').trim().toUpperCase().replace(/[^A-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
  }

  function _uxqSessionQueueInstructions(name, repoPath) {
    return 'Created WatchTower queue ' + name + ' for this session (repo ' + repoPath + '). '
      + 'Auto-drain is off: nothing runs until the human starts workers (the dashboard asks '
      + 'once your first tickets land). Use it to split this chat into tasks:\n'
      + '- File one ticket per self-contained task: `wt add -q ' + name + ' "<title>" --accept "<one-line acceptance check>"`. '
      + 'The --accept line is what a worker verifies before closing, so make it concrete.\n'
      + '- Order dependent work with `--after <REF>` (repeatable), so a ticket is not claimed before the one it builds on closes.\n'
      + '- Run `wt config -q ' + name + '` to see which checks/gates this queue runs, and write tickets a worker can pass them with.\n'
      + '- Keep each item in exactly one place: the queue is the home for these tasks, so do not also track them in your todo list or notes.\n'
      + '- If you work a ticket yourself, claim it first (`wt claim -q ' + name + ' <REF> --worker <your-id>`) so parallel workers don\'t collide, '
      + 'and close it with `wt close <REF> --worker <your-id> --summary "..."`.';
  }

  async function _createQueueForSession() {
    const repoPath = requireConvRepo('Create queue for this session');
    if (!repoPath) return '';
    const sessionId = (typeof sharedState.currentConversation !== 'undefined') ? String(sharedState.currentConversation || '') : '';
    let options;
    try {
      const res = await fetch('/api/queue/config-options', { method: 'POST' });
      options = await res.json();
      if (!res.ok || !options.ok) throw new Error((options && options.error) || res.status);
    } catch (e) {
      (0, sharedState.showOpToast)('Could not load queue configuration: ' + e, 'error');
      return '';
    }
    const queues = options.queues || [];
    const existing = new Set(queues.map(q => String(q.queue).toUpperCase()));
    const linked = sessionId
      ? queues.find(q => q && q.config && String(q.config.session_id || '') === sessionId)
      : null;
    const already = linked ? String(linked.queue).toUpperCase() : _uxqLegacySessionCreatedQueue();
    if (already && existing.has(already)) {
      if (typeof sharedState.setStatusRailTab === 'function') (0, sharedState.setStatusRailTab)('queue');
      if (typeof _uxqSetScopeOverride === 'function') _uxqSetScopeOverride(already);
      sharedState._uxqItemsCache.ts = 0;
      sharedState._uxqHealthCache.ts = 0;
      await (0, hostServices._renderQueuePanel)();
      (0, sharedState.showOpToast)('This session already has queue ' + already + ' - switched to it.', 'info');
      return already;
    }
    const row = (0, sharedState.openConvRow)();
    const rawTitle = (typeof sharedState.paneTitleForRow === 'function' ? (0, sharedState.paneTitleForRow)(row) : '') || '';
    const base = _uxqSuggestSessionQueueName(rawTitle, (0, sharedState._pathLeaf)(repoPath) || 'Session');
    let suggested = base;
    for (let n = 2; existing.has(suggested); n++) suggested = (base + '-' + n).slice(0, 64);
    const typed = await promptModal('Name the queue for this session', suggested);
    if (typed == null) return '';
    const name = _uxqNormalizeQueueName(typed);
    if (!name || !/^[A-Z0-9]/.test(name)) { (0, sharedState.showOpToast)('Queue name needs letters or numbers.', 'error'); return ''; }
    if (existing.has(name)) { (0, sharedState.showOpToast)('Queue ' + name + ' already exists - pick another name.', 'error'); return ''; }
    try {
      const res = await fetch('/api/queue/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          queue: name, repo_path: repoPath, auto_drain: false,
          backend: 'file', desired_workers: 1, claim_types: [],
          ...(sessionId ? { session_id: sessionId } : {}),
          offer_workers: true,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error((data && data.error) || res.status);
    } catch (e) {
      (0, sharedState.showOpToast)('Could not create queue: ' + e, 'error');
      return '';
    }
    if (typeof sharedState.setStatusRailTab === 'function') (0, sharedState.setStatusRailTab)('queue');
    if (typeof _uxqSetScopeOverride === 'function') _uxqSetScopeOverride(name);
    sharedState._uxqItemsCache.ts = 0;
    sharedState._uxqHealthCache.ts = 0;
    await (0, hostServices._renderQueuePanel)();
    (0, sharedState.showOpToast)('Queue ' + name + ' created (auto-drain off) - ready for tickets from this session.', 'success');
    // The whole point is a place THIS session can hand off follow-up work, so
    // it needs to be told the queue exists — a silent toast only the human
    // sees defeats that (CCC-769 feedback). Goes through the normal send
    // channel (queued if the session is mid-turn), same as typing a message.
    if (sessionId && typeof sharedState.injectToSession === 'function') {
      (0, sharedState.injectToSession)(sessionId, _uxqSessionQueueInstructions(name, repoPath));
    }
    return name;
  }

  function _restoreAnchor($view, anchor) {
    if (!anchor || anchor.line == null) return;
    const esc = (window.CSS && CSS.escape) ? CSS.escape(String(anchor.line)) : String(anchor.line);
    const el = $view.querySelector('.event[data-jsonl-line="' + esc + '"]');
    // Subtract the original delta so the anchor sits exactly where it did
    // before, not pinned to the very top (which lands a few lines too high).
    if (el) $view.scrollTop = el.offsetTop - (anchor.delta || 0);
  }

  function _frozenAskText(el) {
    const msgEl = el.querySelector('.user-msg');
    return msgEl ? ((msgEl.dataset && msgEl.dataset.rawText) || msgEl.textContent || '').trim() : '';
  }

  function _frozenAskPick(view) {
    if (view._pinnedToBottom !== false) return null;
    const items = view.querySelectorAll(':scope > ' + sharedState.CONV_USER_MESSAGE_SELECTOR);
    const top = view.getBoundingClientRect().top;
    for (let i = items.length - 1; i >= 0; i--) {
      const el = items[i];
      // display:none bubbles (the original ask mirrored elsewhere) sit
      // logically at the top of the transcript.
      const above = !el.offsetParent || el.getBoundingClientRect().bottom <= top + 4;
      if (!above) continue;
      const text = _frozenAskText(el);
      if (text) return { text, el, original: false };
    }
    if (items.length) return null;
    const pane = view.closest('.conv-pane');
    const conv = (pane && (0, sharedState.convRowForPane)(pane.dataset.paneId)) || {};
    const first = String(conv.original_ask || conv.first_message || '').trim();
    return (first && view.querySelector(':scope > .event')) ? { text: first, el: null, original: true } : null;
  }

  function _frozenAskUpdate(view) {
    view._frozenAskRaf = false;
    let bar = view.querySelector(':scope > .conv-frozen-ask');
    const pick = view.isConnected ? _frozenAskPick(view) : null;
    if (!pick) {
      if (bar) bar.hidden = true;
      return;
    }
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'conv-frozen-ask';
      bar.innerHTML = '<div class="conv-frozen-ask__bubble" role="button" tabindex="0"'
        + ' title="Jump to this message">'
        + sharedState.ORIGINAL_ASK_EXPAND_BTN
        + '<div class="user-msg" dir="auto"></div></div>';
      const jump = () => {
        const target = bar._askEl;
        if (target && target.isConnected && target.offsetParent) {
          target.scrollIntoView({ block: 'start' });
        } else {
          view.scrollTop = 0;
        }
      };
      bar.addEventListener('click', jump);
      bar.addEventListener('keydown', (e) => {
        if (e.target === e.currentTarget.firstChild && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault();
          jump();
        }
      });
    }
    if (bar.parentNode !== view) view.insertBefore(bar, view.firstChild);
    bar._askEl = pick.el;
    if (pick.original) bar.dataset.original = '1'; else delete bar.dataset.original;
    const msg = bar.querySelector('.user-msg');
    if (msg.dataset.rawText !== pick.text) {
      msg.dataset.rawText = pick.text;
      msg.innerHTML = (0, sharedState.linkifyPastedImages)((0, sharedState.escapeHtml)((0, sharedState.cleanIssuePrompt)(pick.text)));
    }
    bar.hidden = false;
  }

  function _frozenAskSchedule(view) {
    if (!view || view._frozenAskRaf) return;
    view._frozenAskRaf = true;
    requestAnimationFrame(() => _frozenAskUpdate(view));
  }

  function installConversationView11() {
document.addEventListener('scroll', (e) => {
    const view = e.target;
    if (view && view.classList && view.classList.contains('conversations-view')) _frozenAskSchedule(view);
  }, { capture: true, passive: true });
  }

  function _renderWorktreesModal({ worktrees, summaryItems, emptyText, orphanPrs, repoPath }) {
    const $modal = document.getElementById('worktreesModal');
    const $summary = document.getElementById('worktreesSummary');
    const $list = document.getElementById('worktreesList');
    if (!$modal || !$summary || !$list) return;
    $summary.innerHTML = (summaryItems || []).join(' ');
    let html = '';
    if (!worktrees || worktrees.length === 0) {
      html += '<div class="wt-empty">' + (0, sharedState.escapeHtml)(emptyText || 'No worktrees.') + '</div>';
    } else {
      const rows = worktrees.map(function (wt) {
        const tildePath = (wt.path || '').replace(/^\/Users\/[^/]+/, '~');
        const branch = wt.branch || '';
        const tags = [];
        if (wt.dirty) {
          tags.push('<span class="wt-tag wt-tag-dirty" title="Has uncommitted changes">uncommitted</span>');
          if (repoPath) {
            tags.push('<button type="button" class="wt-tag wt-tag-who" data-wt-attribute="'
              + (0, sharedState.escapeAttr)(wt.path || '') + '" title="Find which session likely made these edits">who?</button>');
          }
        }
        if (wt.is_agent) tags.push('<span class="wt-tag wt-tag-agent">subagent</span>');
        else if (wt.locked) tags.push('<span class="wt-tag wt-tag-locked">locked</span>');
        if (wt.detached) tags.push('<span class="wt-tag wt-tag-detached">detached</span>');
        if (wt.pr && wt.pr.number) {
          const prUrl = wt.pr.url || '';
          const draftCls = wt.pr.isDraft ? ' wt-tag-pr-draft' : '';
          const prTitle = (wt.pr.title || '').trim();
          const tipBase = (wt.pr.isDraft ? 'Draft PR' : 'Open PR') + ' #' + wt.pr.number;
          const tip = prTitle ? tipBase + ' - ' + prTitle : tipBase;
          tags.push('<a class="wt-tag wt-tag-pr' + draftCls + '" href="' + (0, sharedState.escapeHtml)(prUrl)
            + '" target="_blank" rel="noopener" title="' + (0, sharedState.escapeHtml)(tip) + '">'
            + (wt.pr.isDraft ? 'draft ' : '') + 'PR #' + wt.pr.number + '</a>');
        }
        const branchHtml = branch
          ? '<span class="wt-row-branch"><span class="wt-icon">⎇</span>' + (0, sharedState.escapeHtml)(branch) + '</span>'
          : '<span class="wt-row-branch" style="opacity:0.5;">-</span>';
        const reason = (wt.lock_reason || '').trim();
        const reasonHtml = (reason && !wt.is_agent)
          ? '<div class="wt-lock-reason">' + (0, sharedState.escapeHtml)(reason) + '</div>'
          : '';
        let cls = 'wt-row';
        if (wt.is_agent) cls += ' wt-row-agent';
        if (wt.dirty) cls += ' wt-row-dirty';
        return '<div class="' + cls + '">'
          + '<span class="wt-row-path" title="' + (0, sharedState.escapeHtml)(wt.path || '') + '">' + (0, sharedState.escapeHtml)(tildePath) + '</span>'
          + branchHtml
          + '<span>' + (tags.join(' ') || '') + '</span>'
          + reasonHtml
          + '</div>';
      });
      html += rows.join('');
    }
    if (orphanPrs && orphanPrs.length > 0) {
      html += '<div class="wt-section-heading">Open PRs without a worktree ('
        + orphanPrs.length + ')</div>';
      html += orphanPrs.map(function (pr) {
        const draftCls = pr.isDraft ? ' wt-tag-pr-draft' : '';
        const draftLabel = pr.isDraft
          ? '<span class="wt-tag wt-tag-pr' + draftCls + '">draft</span>'
          : '';
        return '<div class="wt-pr-row">'
          + '<a class="wt-pr-num" href="' + (0, sharedState.escapeHtml)(pr.url || '') + '" target="_blank" rel="noopener">#'
          + pr.number + '</a>'
          + '<span class="wt-pr-title" title="' + (0, sharedState.escapeHtml)(pr.title || '') + '">'
          + (0, sharedState.escapeHtml)(pr.title || '') + '</span>'
          + '<span class="wt-pr-branch">⎇ ' + (0, sharedState.escapeHtml)(pr.headRefName || '') + ' ' + draftLabel + '</span>'
          + '</div>';
      }).join('');
    }
    $list.innerHTML = html;
    $modal.classList.add('open');
    Array.prototype.forEach.call($list.querySelectorAll('[data-wt-attribute]'), function (btn) {
      btn.addEventListener('click', function () {
        _attributeWorktreeDirty(btn, repoPath, btn.getAttribute('data-wt-attribute'));
      });
    });
  }

  async function _attributeWorktreeDirty(btn, repoPath, worktreePath) {
    if (!repoPath || !worktreePath) return;
    const prevLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = '…';
    let data;
    try {
      const res = await fetch('/api/fleet/attribute', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_path: repoPath, worktree_path: worktreePath }),
      });
      data = await res.json();
    } catch (_) {
      btn.disabled = false;
      btn.textContent = prevLabel;
      (0, sharedState.showOpToast)('Attribution request failed', 'error');
      return;
    }
    btn.disabled = false;
    btn.textContent = prevLabel;
    if (!data || !data.ok) {
      (0, sharedState.showOpToast)('Could not attribute: ' + ((data && (data.detail || data.error)) || 'unknown error'), 'error');
      return;
    }
    const candidates = data.candidates || [];
    if (!candidates.length) {
      (0, sharedState.showOpToast)('No session could be matched to these edits (unknown origin)', 'info');
      return;
    }
    const top = candidates[0];
    const name = top.display_name || top.session_id || 'unknown session';
    const msg = 'Likely ' + (0, sharedState.escapeHtml)(name) + ' (' + (0, sharedState.escapeHtml)(top.confidence || 'low') + ' confidence)'
      + (candidates.length > 1 ? ' · +' + (candidates.length - 1) + ' other candidate' + (candidates.length > 2 ? 's' : '') : '');
    (0, sharedState.showOpToast)(msg, 'info', top.session_id ? {
      label: 'Open',
      onClick: function () { if (typeof sharedState.selectConversation === 'function') (0, sharedState.selectConversation)(top.session_id); },
    } : undefined);
  }

  function openWorktreesModal(paneId) {
    const w = sharedState._workspaceDataByPane[paneId || ((typeof sharedState.activePaneId === 'function') ? (0, sharedState.activePaneId)() : 'p1')];
    if (!w) return;
    const wts = w.worktrees || [];
    const agentN = w.worktrees_agent_count || 0;
    const manualN = w.worktrees_manual_count || 0;
    const total = wts.length;
    const items = ['<span><strong>' + total + '</strong> worktree' + (total === 1 ? '' : 's') + '</span>'];
    if (agentN > 0) items.push('<span><strong>' + agentN + '</strong> subagent</span>');
    if (manualN > 0) items.push('<span><strong>' + manualN + '</strong> manual</span>');
    if (w.is_worktree && w.cwd) {
      const here = (w.cwd || '').replace(/^\/Users\/[^/]+/, '~');
      items.push('<span style="margin-left:auto;opacity:0.7;">this session: ' + (0, sharedState.escapeHtml)(here) + '</span>');
    }
    _renderWorktreesModal({
      worktrees: wts,
      summaryItems: items,
      emptyText: 'No sibling worktrees.',
    });
  }

  function closeWorktreesModal() {
    const $modal = document.getElementById('worktreesModal');
    if ($modal) $modal.classList.remove('open');
  }

  function installConversationView12() {
document.addEventListener('click', function (ev) {
    const btn = ev.target && ev.target.closest && ev.target.closest('.conv-input-context[data-role="input-context"] [data-action="open-worktrees"]');
    if (!btn) return;
    ev.preventDefault();
    const paneEl = btn.closest('.conv-pane[data-pane-id]');
    openWorktreesModal(paneEl ? paneEl.getAttribute('data-pane-id') : '');
  });
  }

  function installConversationView13() {
document.addEventListener('keydown', function (e) {
    if ((0, sharedState.isImeKey)(e)) return;
    const $modal = document.getElementById('worktreesModal');
    if (e.key === 'Escape' && $modal && $modal.classList.contains('open')) {
      closeWorktreesModal();
    }
  });
  }

  function applyConvVerbose(on) {
    document.querySelectorAll('.tool-call-group').forEach(g => {
      // Never re-collapse groups that carry conversation/control-plane
      // context rather than routine tool noise.
      if (!on && (0, sharedState.toolGroupCarriesConversationContext)(g)) return;
      g.classList.toggle('collapsed', !on);
    });
    document.querySelectorAll('details.tool-command-disclosure').forEach(d => { d.open = on; });
    // CCC-942: thinking stays expanded regardless of Verbose — only tool
    // output/groups are gated by this toggle.
  }

  function persistPresentationModeForSession(sessionId, mode) {
    const id = String(sessionId || '');
    const normalized = (0, sharedState.normalizePresentationMode)(mode);
    if (!id) return normalized;
    const state = (0, sharedState.presentationModeState)();
    state.modes[id] = normalized;
    try {
      localStorage.setItem(sharedState.PRESENTATION_MODE_BY_SESSION_KEY, JSON.stringify(state));
    } catch (_) {}
    return normalized;
  }

  function setPresentationMode(paneId, mode, opts) {
    const targetPaneId = paneId || (0, sharedState.activePaneId)();
    const pane = (0, sharedState.presentationPaneElement)(targetPaneId);
    const view = (0, sharedState.getConvViewForPane)(targetPaneId);
    if (!pane || !view) return;
    const normalized = (0, sharedState.presentationFeatureEnabled)() ? (0, sharedState.normalizePresentationMode)(mode) : 'off';
    const conversationId = (0, sharedState.presentationConversationIdForPane)(targetPaneId);
    const oldMode = (0, sharedState.normalizePresentationMode)(pane.dataset.presentationMode || 'off');
    const sessionId = (0, sharedState.presentationSessionIdForPane)(targetPaneId);
    if (oldMode === 'off' && normalized !== 'off') {
      view.dataset.presentationRestoreScroll = String(view.scrollTop || 0);
      view.dataset.presentationRestorePinned = view._pinnedToBottom ? '1' : '0';
    }
    const restorePinned = normalized === 'off'
      && view.dataset.presentationRestorePinned === '1';
    if (normalized === 'off') view._pinnedToBottom = false;
    pane.dataset.presentationMode = normalized;
    pane.dataset.presentationSessionId = sessionId;
    persistPresentationModeForSession(sessionId, normalized);
    (0, sharedState.refreshPresentationForPane)(targetPaneId, {
      followTail: normalized !== 'off' && !(opts && opts.preserveCursor),
      startAtLatestTurn: normalized !== 'off' && oldMode !== normalized && !(opts && opts.preserveCursor),
    });
    if (normalized === '3' && oldMode !== '3') {
      (0, sharedState.requestMode3Bootstrap)(targetPaneId, conversationId);
    }
    if (normalized === 'off') {
      const restore = Number(view.dataset.presentationRestoreScroll || 0);
      requestAnimationFrame(() => {
        if (restorePinned) {
          (0, sharedState.scrollConversationToEnd)(view);
        } else {
          view.scrollTop = restore;
          (0, sharedState.updateConversationEndAffordance)(view);
          view._pinnedToBottom = false;
        }
      });
    }
  }

  function installConversationView14() {
document.addEventListener('click', (ev) => {
    const mobileToggle = ev.target && ev.target.closest
      && ev.target.closest('[data-role="mobile-view-toggle"]');
    if (mobileToggle) {
      ev.preventDefault();
      ev.stopPropagation();
      const list = mobileToggle.nextElementSibling;
      const open = list && list.classList.contains('is-open');
      if (list) list.classList.toggle('is-open', !open);
      mobileToggle.setAttribute('aria-pressed', open ? 'false' : 'true');
      mobileToggle.setAttribute('aria-expanded', open ? 'false' : 'true');
      return;
    }
    const modeButton = ev.target && ev.target.closest
      && ev.target.closest('.conv-presentation-mode[data-presentation-mode]');
    if (modeButton) {
      ev.preventDefault();
      ev.stopPropagation();
      const pane = modeButton.closest('.conv-pane[data-pane-id]');
      const paneId = pane ? pane.dataset.paneId : (0, sharedState.activePaneId)();
      if (paneId !== (0, sharedState.activePaneId)()) (0, sharedState.setActivePaneById)(paneId);
      setPresentationMode(paneId, modeButton.dataset.presentationMode);
      // Mobile: collapse the picker back down after a choice, same as the
      // toggle's own open/close — the toolbar shouldn't stay open once a
      // decision is made.
      if (modeButton.classList.contains('mobile-view-btn')) {
        const list = modeButton.closest('.conv-presentation-mobile-list');
        const toggle = list && list.previousElementSibling;
        if (list) list.classList.remove('is-open');
        if (toggle && toggle.matches('[data-role="mobile-view-toggle"]')) {
          toggle.setAttribute('aria-pressed', 'false');
          toggle.setAttribute('aria-expanded', 'false');
        }
      }
      return;
    }
    const navButton = ev.target && ev.target.closest && ev.target.closest('[data-presentation-nav]');
    if (navButton) {
      ev.preventDefault();
      ev.stopPropagation();
      const pane = navButton.closest('.conv-pane[data-pane-id]');
      (0, sharedState.stepPresentationSlide)(pane && pane.dataset.paneId, Number(navButton.dataset.presentationNav));
    }
  });
  }

  function schedulePresentationEscape(ev) {
    if (!ev || ev.key !== 'Escape') return false;
    setTimeout(() => {
      if (ev.defaultPrevented) return;
      const paneId = (0, sharedState.activePaneId)();
      const pane = (0, sharedState.presentationPaneElement)(paneId);
      if (!pane || (0, sharedState.normalizePresentationMode)(pane.dataset.presentationMode) === 'off') return;
      setPresentationMode(paneId, 'off');
    }, 0);
    return true;
  }

  function installConversationView15() {
document.addEventListener('keydown', (ev) => {
    if ((0, sharedState.isImeKey)(ev)) return;
    if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey) return;
    if (schedulePresentationEscape(ev)) return;
    if (ev.key !== 'ArrowLeft' && ev.key !== 'ArrowRight') return;
    const target = ev.target;
    if (target && target.closest && target.closest('input,textarea,select,button,a,summary,pre,code,[contenteditable="true"]')) return;
    const paneId = (0, sharedState.activePaneId)();
    const pane = (0, sharedState.presentationPaneElement)(paneId);
    if (!pane || (0, sharedState.normalizePresentationMode)(pane.dataset.presentationMode) === 'off') return;
    ev.preventDefault();
    (0, sharedState.stepPresentationSlide)(paneId, ev.key === 'ArrowRight' ? 1 : -1);
  });
  }

  function installConversationView16() {
window.addEventListener('resize', () => {
    if (sharedState._presentationResizeTimer) clearTimeout(sharedState._presentationResizeTimer);
    sharedState._presentationResizeTimer = setTimeout(() => {
      document.querySelectorAll('.conv-pane[data-pane-id]').forEach(pane => {
        const mode = (0, sharedState.normalizePresentationMode)(pane.dataset.presentationMode);
        if (mode !== 'off' && (mode !== '2' || typeof ResizeObserver !== 'function')) {
          (0, sharedState.refreshPresentationForPane)(pane.dataset.paneId, { preserveCursor: true });
        }
      });
    }, 140);
  });
  }

  function _hiFmtRel(unix) {
    if (!unix) return 'never';
    const diffSec = Math.max(0, (Date.now() / 1000) - unix);
    if (diffSec < 60) return 'just now';
    if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm ago';
    if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h ago';
    return Math.floor(diffSec / 86400) + 'd ago';
  }

  function _hiRenderPill(st) {
    const $pill = document.getElementById('historyStatusPill');
    if (!$pill) return;
    if (!st || !st.available) {
      $pill.style.display = 'none';
      return;
    }
    $pill.style.display = '';
    const $label = document.getElementById('hiPillLabel');
    const $icon = document.getElementById('hiPillIcon');
    const $sem = document.getElementById('hiPillSem');
    $pill.classList.toggle('is-indexing', !!(st.indexing || st.embedding));
    let label = 'History';
    let title = '';
    if (!st.exists) {
      label = 'No index';
      title = 'No history index yet. Click to build one - runs in the background.';
    } else if (st.indexing) {
      label = 'Indexing…';
      title = 'Indexing in progress: scanning JSONL transcripts for new messages.';
    } else if (st.embedding) {
      const p = st.embed_progress || {};
      label = 'Embedding ' + (p.done || 0) + '/' + (p.total || '?');
      title = 'Computing semantic embeddings via Ollama.';
    } else {
      const fresh = _hiFmtRel(st.latest_message_unix);
      label = 'History · ' + fresh;
      const stale = st.latest_message_unix && (Date.now() / 1000 - st.latest_message_unix > 86400);
      $pill.classList.toggle('is-stale', !!stale);
      title = 'History index: ' + (st.message_count || 0) + ' messages.\n'
            + 'Latest indexed message: ' + fresh + '.\n'
            + 'Click to re-run an ingest now.';
    }
    if (st.semantic && st.semantic.available) {
      $sem.style.display = '';
      const semFresh = _hiFmtRel(st.semantic.latest_embed_unix);
      const semMsgFresh = _hiFmtRel(st.semantic.latest_embedded_message_unix);
      title += '\n\nSemantic embeddings: ' + (st.semantic.embedded_count || 0) + ' messages.\n'
             + 'Last embedding run: ' + semFresh + '.\n'
             + 'Freshest embedded message: ' + semMsgFresh + '.';
    } else {
      $sem.style.display = 'none';
    }
    const retWarn = _hiRetentionShort(st);
    if (retWarn && st.exists) {
      title += '\n\nClaude Code deletes conversations older than '
             + st.transcript_retention_days + ' days, so older history drops out of search.';
    }
    $label.textContent = label;
    $pill.title = title;
  }

  function _hiRetentionShort(st) {
    const d = st && st.transcript_retention_days;
    return typeof d === 'number' && d <= 30;
  }

  async function _hiRefreshStatus() {
    try {
      const r = await (0, sharedState.backgroundApiFetch)('/api/history/status');
      if (!r.ok) return;
      const st = await r.json();
      window._historyIndexStatus = st;
      _hiRenderPill(st);
    } catch (_) {}
  }

  function _hiStartPolling() {
    _hiRefreshStatus();
    if (sharedState._hiPollTimer) clearInterval(sharedState._hiPollTimer);
    // 8s during indexing, 60s otherwise — adaptive interval set after each tick.
    sharedState._hiPollTimer = setInterval(() => { if ((0, sharedState._pollerOff)('hiStatus')) return;
      const st = window._historyIndexStatus;
      const fast = st && (st.indexing || st.embedding);
      _hiRefreshStatus();
      // Re-arm with appropriate cadence.
      if ((fast && sharedState._hiPollTimer.__cadence !== 'fast')
          || (!fast && sharedState._hiPollTimer.__cadence !== 'slow')) {
        clearInterval(sharedState._hiPollTimer);
        sharedState._hiPollTimer = setInterval((0, sharedState._gated)('hiStatus', () => _hiRefreshStatus()), fast ? 4000 : 60000);
        sharedState._hiPollTimer.__cadence = fast ? 'fast' : 'slow';
      }
    }, 8000);
    sharedState._hiPollTimer.__cadence = 'slow';
  }

  async function _hiTriggerSetup() {
    try {
      const r = await fetch('/api/history/setup', { method: 'POST' });
      if (!r.ok) return false;
      _hiRefreshStatus();
      return true;
    } catch (_) { return false; }
  }

  function _setRailLogEntriesOpen(open) {
    const body = document.getElementById('railLogBody');
    if (!body) return;
    body.querySelectorAll('.activity-log-entry').forEach(entry => { entry.open = open; });
    (0, sharedState._syncRailLogToggleAll)();
  }

  function initRailLogPane() {
    const body = document.getElementById('railLogBody');
    const filter = document.getElementById('railLogFilter');
    const pane = document.getElementById('statusRailLogPane');
    if (!body || !pane) return;
    pane.querySelectorAll('[data-rail-log-scope]').forEach((btn) => {
      btn.addEventListener('click', () => {
        pane.querySelectorAll('[data-rail-log-scope]').forEach((b) => b.classList.remove('is-active'));
        btn.classList.add('is-active');
        sharedState._railLogScope = btn.getAttribute('data-rail-log-scope') || 'all';
        (0, sharedState.refreshRailLogPane)();
      });
    });
    const bindToggle = (id, label, read, write, activeTitle) => {
      const button = document.getElementById(id);
      if (!button) return;
      const sync = () => {
        button.setAttribute('aria-pressed', String(read()));
        button.title = read() && activeTitle ? activeTitle : (read() ? 'Hide ' : 'Show ') + label;
      };
      sync();
      button.addEventListener('click', () => {
        write(!read());
        sync();
        (0, sharedState._renderRailLogPane)();
      });
    };
    bindToggle('railLogShowHeartbeats', 'successful heartbeats',
      () => sharedState._railLogShowHeartbeats, value => { sharedState._railLogShowHeartbeats = value; });
    bindToggle('railLogShowInjects', 'routine inject events (delivery problems stay visible)',
      () => sharedState._railLogShowInjects, value => { sharedState._railLogShowInjects = value; });
    bindToggle('railLogOnlyAttention', 'only warnings and errors',
      () => sharedState._railLogOnlyAttention, value => { sharedState._railLogOnlyAttention = value; }, 'Show all event levels');
    const toggleAll = document.getElementById('railLogToggleAll');
    if (toggleAll) {
      toggleAll.addEventListener('click', () => {
        const entries = [...body.querySelectorAll('.activity-log-entry')];
        _setRailLogEntriesOpen(!entries.length || !entries.every(entry => entry.open));
      });
    }
    body.addEventListener('toggle', () => (0, sharedState._syncRailLogToggleAll)(), true);
    if (filter) {
      filter.addEventListener('input', () => {
        sharedState._railLogFilter = filter.value || '';
        if (sharedState._railLogEvents.length) (0, sharedState._renderRailLogPane)();
      });
    }
    window.addEventListener('ccc-repo-changed', () => {
      if (sharedState._railLogScope === 'session' && (0, sharedState._railLogPaneVisible)()) (0, sharedState.refreshRailLogPane)();
    });
    setInterval(() => {
      if ((0, sharedState._railLogPaneVisible)()) (0, sharedState.refreshRailLogPane)();
    }, sharedState.RAIL_LOG_POLL_MS);
  }

  function orchSetMoreOpen(open) {
    try { localStorage.setItem('ccc-orch-more', open ? '1' : '0'); } catch (_) {}
    (0, sharedState.renderOrchPlaybooks)();
  }

  function orchSetExecutor(id) {
    // If the composer still holds the previous model's spawn draft (task
    // placeholder untouched), swap the model in place so "pick, then send"
    // reads the chip that is lit, not the one it was drafted with.
    const before = orchPrompt('spawn');
    try { localStorage.setItem('ccc-orch-executor', id); } catch (_) {}
    const input = orchComposerInput();
    if (input && before && input.value === before) {
      input.value = orchPrompt('spawn');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      orchSelectSpawnPlaceholder(input);
    }
    (0, sharedState.renderOrchPlaybooks)();
  }

  function orchComposerInput() {
    const paneId = (0, sharedState.activePaneId)();
    const paneEl = document.querySelector('.conv-pane[data-pane-id="' + paneId + '"]');
    return paneEl && paneEl.querySelector('.conv-input-bar textarea, .conv-input-bar input[type="text"]');
  }

  function orchSelectSpawnPlaceholder(input) {
    const at = input.value.indexOf(sharedState.ORCH_SPAWN_PLACEHOLDER);
    input.focus();
    if (at < 0 || typeof input.setSelectionRange !== 'function') return;
    try { input.setSelectionRange(at, at + sharedState.ORCH_SPAWN_PLACEHOLDER.length); } catch (_) {}
  }

  function orchAltExecutor(executor) {
    return sharedState.ORCH_EXECUTORS.find(e => e.id === (executor.id === 'glm-5.2' ? 'sonnet-5' : 'glm-5.2'));
  }

  function orchPrompt(id) {
    const ex = (0, sharedState.orchExecutor)();
    const tester = (0, sharedState.orchTester)(ex);
    const critics = (0, sharedState.orchCritics)(ex);
    // Short, but complete: 'CCC orchestration' is the phrase the session's
    // skill keys on; the rest names the models and the one rule that
    // matters (lanes can ask the orchestrator questions).
    const alt = orchAltExecutor(ex);
    switch (id) {
      case 'delegate':
        return 'When ready to execute, feel free to use CCC orchestration to spawn an execution lane, e.g. '
          + (0, sharedState.orchExecLabel)(ex) + (alt ? ' or ' + (0, sharedState.orchExecLabel)(alt) : '') + '.\n'
          + 'Remind them they can ask you questions through CCC orchestration.';
      case 'verify':
        return 'When execution lands, use CCC orchestration to verify it with a fresh lane on a different model family, e.g. '
          + (0, sharedState.orchExecLabel)(tester) + '. It can ask you questions through CCC orchestration.';
      case 'critique':
        return 'Before landing, use CCC orchestration to get two blunt outside critiques, e.g. '
          + critics.map(sharedState.orchExecLabel).join(' and ') + '.';
      case 'status':
        return 'Use CCC orchestration to check the status of the agents you spawned and tell me where each one stands.';
      case 'spawn':
        return 'Spawn a new ' + (0, sharedState.orchExecLabel)(ex) + ' sub-agent with CCC to do ' + sharedState.ORCH_SPAWN_PLACEHOLDER + '.';
    }
    return '';
  }

  async function orchInject(playbookId, sendNow) {
    const ctx = (0, sharedState.orchContext)();
    if (!ctx.convId) { (0, sharedState.showOpToast)('Select a session first.', 'error'); return; }
    const text = orchPrompt(playbookId);
    if (!text) return;
    const paneId = (0, sharedState.activePaneId)();
    const input = orchComposerInput();
    if (!input) { (0, sharedState.showOpToast)('This session has no composer to inject into.', 'error'); return; }
    input.value = text;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    const pbDef = sharedState.ORCH_PLAYBOOKS.find(p => p.id === playbookId);
    // Draft playbooks (Spawn) never send, not even on Shift-click: the
    // placeholder is selected so the next keystroke replaces it, and the
    // composer's own send is the second step.
    if (pbDef && pbDef.draft) { orchGlowPlaybook(playbookId); orchSelectSpawnPlaceholder(input); return; }
    if (!sendNow) { orchGlowPlaybook(playbookId); input.focus(); return; }
    (0, sharedState.orchFlashPlaybook)(playbookId);
    try {
      await (0, sharedState.sendToTerminal)(paneId, 'send');
      const pb = sharedState.ORCH_PLAYBOOKS.find(p => p.id === playbookId);
      (0, sharedState.showOpToast)('Playbook injected: ' + (pb ? pb.title : playbookId), 'info');
    } catch (e) {
      (0, sharedState.showOpToast)('Inject failed: ' + (e && e.message ? e.message : e), 'error');
    }
  }

  function orchGlowPlaybook(playbookId) {
    const btn = (0, sharedState.orchPlaybookEl)(playbookId);
    if (!btn) return;
    btn.classList.remove('is-drafted');
    void btn.offsetWidth;
    btn.classList.add('is-drafted');
    setTimeout(() => btn.classList.remove('is-drafted'), 320);
  }

  async function orchQueueAction() {
    if (!sharedState.currentConversation) { (0, sharedState.showOpToast)('Select a session first.', 'error'); return; }
    if (typeof _createQueueForSession !== 'function') { (0, sharedState.showOpToast)('Queue tools are not available.', 'error'); return; }
    (0, sharedState.orchFlashPlaybook)('queue');
    const name = await _createQueueForSession();
    if (!name) return;   // creation failed; its toast already said why
    try {
      const res = await fetch('/api/queue/config-options', { method: 'POST' });
      const options = await res.json();
      const q = ((options && options.queues) || []).find(x => String(x.queue).toUpperCase() === name.toUpperCase()) || {};
      const conf = q.config || q;
      if (conf.auto_drain === true) return;
      const body = {
        queue: name,
        repo_path: conf.repo_path || (typeof requireConvRepo === 'function' ? requireConvRepo('Queue') : ''),
        backend: conf.backend || 'file',
        github_repo: conf.github_repo || '',
        github_assignee: conf.github_assignee || '',
        engine: conf.engine || '',   // blank = CCC worker default; never re-pin to claude
        model: conf.model || '',
        effort: conf.effort || '',
        desired_workers: Number(conf.desired_workers) || 1,
        claim_types: Array.isArray(conf.claim_types) ? conf.claim_types : [],
        auto_drain: true,
        offer_workers: false,   // this tap already answered "start workers?"
      };
      const r2 = await fetch('/api/queue/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d2 = await r2.json().catch(() => ({}));
      if (!r2.ok || !d2.ok) throw new Error((d2 && d2.error) || r2.status);
      if (typeof sharedState._uxqHealthCache !== 'undefined') sharedState._uxqHealthCache.ts = 0;
      if (typeof hostServices._renderQueuePanel === 'function') await (0, hostServices._renderQueuePanel)();
      (0, sharedState.showOpToast)('Queue ' + name + ': auto-drain on. Drop tasks in the Queue tab.', 'success');
    } catch (e) {
      (0, sharedState.showOpToast)('Queue created, but auto-drain could not be enabled: ' + (e && e.message ? e.message : e), 'error');
    }
  }

  function orchWatchList() {
    const list = document.getElementById('convList');
    if (!list || !window.MutationObserver) return;
    new MutationObserver(() => {
      if (sharedState._orchFamilyRaf || (!sharedState._orchFamily.root && !sharedState._orchFamily.lanes.length)) return;
      sharedState._orchFamilyRaf = requestAnimationFrame(() => { sharedState._orchFamilyRaf = 0; (0, sharedState.orchApplyFamilyRows)(); });
    }).observe(list, { childList: true, subtree: true });
  }

  function orchStopPreview() {
    sharedState._orchSimTimers.forEach(t => clearTimeout(t));
    sharedState._orchSimTimers = [];
    sharedState._orchSim = null;
    const btn = document.getElementById('orchMapPreview');
    if (btn) { btn.textContent = 'Preview'; btn.classList.remove('is-playing'); }
    (0, sharedState.updateOrchestrationPane)(sharedState.currentConversation);
  }

  function orchPlayPreview() {
    if (sharedState._orchSim) { orchStopPreview(); return; }
    const ctx = (0, sharedState.orchContext)();
    const ex = (0, sharedState.orchExecutor)();
    const tester = (0, sharedState.orchTester)(ex);
    const rootName = (ctx.row && ctx.row.display_name) || 'This session';
    sharedState._orchSim = {
      root: { name: rootName, engine: (ctx.row && ctx.row.engine) || 'claude', model: (ctx.row && ctx.row.model) || '' },
      lanes: [],
    };
    const mk = (id, name, e, status) => ({ id: 'sim-' + id, convId: '', name, engine: e.engine, model: e.model, status, mtime: Date.now() / 1000 + id });
    const script = [
      [0,     () => { sharedState._orchSim.lanes.push(mk(1, 'Step 1 · rail tabs', ex, 'working')); }],
      [900,   () => { sharedState._orchSim.lanes.push(mk(2, 'Step 2 · lane map', ex, 'working')); }],
      [1800,  () => { sharedState._orchSim.lanes.push(mk(3, 'Step 3 · prompts', ex, 'working')); }],
      [4200,  () => { sharedState._orchSim.lanes[2].status = 'waiting'; }],
      [5400,  () => { sharedState._orchSim.lanes[0].status = 'done'; }],
      [6300,  () => { sharedState._orchSim.lanes[2].status = 'working'; }],
      [7400,  () => { sharedState._orchSim.lanes.push(mk(4, 'Verify · fresh eyes', tester, 'working')); }],
      [8600,  () => { sharedState._orchSim.lanes[1].status = 'done'; }],
      [9800,  () => { sharedState._orchSim.lanes[2].status = 'done'; }],
      [11600, () => { sharedState._orchSim.lanes[3].status = 'done'; }],
      [14000, () => { orchStopPreview(); }],
    ];
    const btn = document.getElementById('orchMapPreview');
    if (btn) { btn.textContent = 'Stop'; btn.classList.add('is-playing'); }
    const wrap = document.getElementById('orchMapWrap');
    if (wrap && wrap.scrollIntoView) wrap.scrollIntoView({ block: 'end', behavior: 'smooth' });
    sharedState._orchLastKeys = new Set(['seeded']);   // so the very first sim lane is "born", not a first paint
    script.forEach(([t, fn]) => {
      sharedState._orchSimTimers.push(setTimeout(() => { if (!sharedState._orchSim && t !== 14000) return; fn(); if (sharedState._orchSim) (0, sharedState.updateOrchestrationPane)(sharedState.currentConversation); }, t));
    });
    (0, sharedState.updateOrchestrationPane)(sharedState.currentConversation);
  }

  function orchStartPolling() {
    if (sharedState._orchPollTimer) return;
    sharedState._orchPollTimer = setInterval(() => {
      if (!(0, sharedState.orchPaneVisible)()) return;
      if (document.hidden) return;
      const c = (0, sharedState.orchContext)();
      (0, sharedState.orchRefreshRegistry)(false, c.rootSid || c.sid, c.sid).then(() => (0, sharedState.updateOrchestrationPane)(sharedState.currentConversation));
    }, 6000);
  }

  function installConversationView17() {
onDashboardDocumentReady(() => {
    const pane = document.getElementById('statusRailOrchestrationPane');
    if (!pane) return;
    (0, sharedState.renderOrchPlaybooks)();
    pane.addEventListener('click', (ev) => {
      const chip = ev.target.closest('[data-orch-executor]');
      if (chip) { orchSetExecutor(chip.getAttribute('data-orch-executor')); return; }
      if (ev.target.closest('[data-orch-more]')) { orchSetMoreOpen(!(0, sharedState.orchMoreOpen)()); return; }
      const pb = ev.target.closest('[data-orch-playbook]');
      if (pb) { orchInject(pb.getAttribute('data-orch-playbook'), ev.shiftKey); return; }
      if (ev.target.closest('[data-orch-action="queue"]')) { orchQueueAction(); return; }
      if (ev.target.closest('#orchMapPreview')) { orchPlayPreview(); return; }
      // Video replay, joined with the orchestration replay (Preview) in the
      // lane-map header: replays the active conversation message by message.
      if (ev.target.closest('#orchMapReplay')) {
        if (typeof sharedState.startConvReplay === 'function') {
          (0, sharedState.startConvReplay)();
          if (!sharedState._convReplayActive) (0, sharedState.showOpToast)('Nothing to replay yet.', 'info');
        }
        return;
      }
      const node = ev.target.closest('.orch-node[data-conv-id]');
      if (node) {
        const cid = node.getAttribute('data-conv-id');
        if (!cid) return;
        // Task-tool subagent transcripts are read-only and non-resumable —
        // they open the same way the task-notification "agent transcript"
        // button does (CCC-112): a standalone popout, not selectConversation
        // (which expects a normal row in conversationsData/archiveData).
        if (node.classList.contains('is-task-subagent')) {
          window.open('/?ccc_popout=conversation&conv=' + encodeURIComponent(cid), '_blank',
            'width=900,height=800');
        } else if (typeof sharedState.selectConversation === 'function') {
          (0, sharedState.selectConversation)(cid);
        }
      }
    });
    window.addEventListener('resize', () => { if ((0, sharedState.orchPaneVisible)()) (0, sharedState.orchDrawEdges)(); });
    orchWatchList();
    orchStartPolling();
  });
  }

  function askEscapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function askNeutralizeMarkers(x) {
    return String(x == null ? '' : x).replace(/\[\[/g, '[ [');
  }

  function askResultTime(tsUnix) {
    if (!tsUnix) return '';
    const ms = Number(tsUnix) * 1000;
    const diffSec = Math.floor((Date.now() - ms) / 1000);
    if (diffSec < 60) return 'now';
    if (diffSec < 3600) return Math.floor(diffSec / 60) + 'm';
    if (diffSec < 86400) return Math.floor(diffSec / 3600) + 'h';
    const d = new Date(ms);
    return d.getDate() + ' ' + sharedState.ASK_MONTHS[d.getMonth()];
  }

  function askResultsHtml(t, selectedId) {
    const sources = t.sources || [];
    const count = Number.isFinite(t.hitCount) ? t.hitCount : sources.length;
    const bits = [count + (count === 1 ? ' source' : ' sources')];
    if (Number.isFinite(t.ttftMs)) bits.push('first token ' + (t.ttftMs / 1000).toFixed(1) + 's');
    if (t.processMode === 'warm') bits.push('warm');
    const inline = new Set();
    for (const m of String(t.a || '').matchAll(/\[\[session:([0-9A-Za-z_.-]{5,128})\]\]/g)) inline.add(m[1]);
    const rest = sources.filter(src => src && src.id && !inline.has(src.id));
    if (!sources.length) return '';
    const more = rest.length
      ? '<details class="ask-more-sources"><summary>' + rest.length + ' other candidate' + (rest.length === 1 ? '' : 's') + ' <span class="ask-more-caret">▸</span></summary>'
        + '<div class="ask-top-sources">' + rest.map(src => askSessionChipHtml(src, selectedId)).join('') + '</div></details>'
      : '';
    return '<div class="ask-sources" aria-label="Sources">' + more
      + '<div class="ask-result-count">' + askEscapeHtml(bits.join(' · ')) + '</div></div>';
  }

  function askTraceHtml(t) {
    const trace = t.trace || [];
    if (!trace.length) return '';
    return '<ul class="ask-trace" aria-label="How Mazkir answered">' + trace.map(step =>
      '<li><span class="ask-trace-tool">' + askEscapeHtml(askNeutralizeMarkers(step.tool || '')) + '</span>'
      + (step.detail ? ' ' + askEscapeHtml(askNeutralizeMarkers(step.detail)) : '') + '</li>').join('') + '</ul>';
  }

  function askConfirmCardsHtml(t) {
    const acts = t.confirmActions || [];
    if (!acts.length) return '';
    return '<div class="ask-confirms">' + acts.map(a => {
      const pending = a.status === 'proposed';
      const res = a.result || {};
      let status = '';
      if (a.status === 'done') status = '✓ Done' + (res.ref ? ' · ' + res.ref : '') + (res.session_id ? ' · ' + String(res.session_id).slice(0, 8) : '');
      else if (a.status === 'failed') status = '✗ ' + (res.error || 'failed');
      else if (a.status === 'dismissed') status = 'Dismissed';
      else if (a.status === 'running') status = 'Running…';
      else if (a.status === 'expired') status = 'Expired: ask again';
      return '<div class="ask-confirm is-' + askEscapeHtml(a.status) + '" data-ask-action-id="' + askEscapeHtml(a.id) + '">'
        + '<div class="ask-confirm-head"><span class="ask-confirm-label">' + askEscapeHtml(a.label || a.kind) + '</span>'
        + '<span class="ask-confirm-effect">' + askEscapeHtml(askNeutralizeMarkers(a.effect || '')) + '</span></div>'
        + (a.detail ? '<pre class="ask-confirm-detail">' + askEscapeHtml(askNeutralizeMarkers(a.detail)) + '</pre>' : '')
        + '<div class="ask-confirm-actions">'
        + (pending
          ? '<button type="button" class="ask-action" data-ask-confirm="' + askEscapeHtml(a.id) + '">Confirm</button>'
            + '<button type="button" class="ask-sel-btn" data-ask-dismiss="' + askEscapeHtml(a.id) + '">Dismiss</button>'
          : '<span class="ask-confirm-status">' + askEscapeHtml(status) + '</span>')
        + '</div></div>';
    }).join('') + '</div>';
  }

  function askChipTooltip(src, title) {
    const meta = [src.repo, askResultTime(src.ts_unix), src.status === 'live' ? 'working' : '', (src.id || '').slice(0, 8)]
      .filter(Boolean).join(' · ');
    const snippet = askNeutralizeMarkers(src.snippet || '');
    return 'Open session: ' + title + (meta ? '\n' + meta : '') + (snippet ? '\n' + snippet : '') + '\n⌘-click to select';
  }

  function askSessionChipHtml(src, selectedId) {
    const title = askNeutralizeMarkers(src.title || src.id.slice(0, 8));
    return '<button type="button" class="ask-session-chip' + (src.id === selectedId ? ' is-selected' : '') + '"'
      + ' data-ask-open="' + askEscapeHtml(src.id) + '" data-ask-title="' + askEscapeHtml(title) + '"'
      + ' data-ask-cwd="' + askEscapeHtml(src.cwd || '') + '" title="' + askEscapeHtml(askChipTooltip(src, title)) + '">'
      + '<span class="ask-dot' + (src.status === 'live' ? ' is-live' : '') + '"></span>'
      + '<span>' + askEscapeHtml(title) + '</span></button>';
  }

  function renderAskVerdict(answer, sources, spawned) {
    const byId = Object.create(null), byTitle = new Map();
    (sources || []).forEach(src => {
      if (!src || !src.id) return;
      byId[src.id] = src;
      const title = String(src.title || '').trim();
      if (title.length >= 5) byTitle.set(title, byTitle.has(title) ? null : src);
    });
    const titles = [...byTitle.keys()].filter(t => byTitle.get(t)).sort((a, b) => b.length - a.length);
    const escapeRx = text => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const token = /\[\[(session|action:spawn-continue):([0-9A-Za-z_.-]{5,128})\]\]/;
    const pattern = new RegExp(token.source + (titles.length ? '|' + titles.map(escapeRx).join('|') : ''), 'g');
    const template = document.createElement('template');
    template.innerHTML = (0, sharedState.renderMarkdown)(String(answer || '').replace(sharedState.ASK_CONFIRM_MARKER_RE, '').trim());
    const walker = document.createTreeWalker(template.content, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement || !node.parentElement.closest('a, button, code, pre, script, style')) nodes.push(node);
    }
    nodes.forEach(node => {
      const text = node.textContent;
      let cursor = 0, html = '', matched = false;
      for (const match of text.matchAll(pattern)) {
        // Plain names must be whole references, not part of a longer word.
        if (!match[1] && (/[\p{L}\p{N}_]/u.test(text[match.index - 1] || '')
          || /[\p{L}\p{N}_]/u.test(text[match.index + match[0].length] || ''))) continue;
        matched = true;
        html += askEscapeHtml(text.slice(cursor, match.index));
        const src = match[1] ? byId[match[2]] : byTitle.get(match[0]);
        if (src && match[1] === 'action:spawn-continue') {
          const done = !!(spawned || {})[src.id];
          html += '<button type="button" class="ask-action" data-ask-continue="' + askEscapeHtml(src.id)
            + '" data-ask-cwd="' + askEscapeHtml(src.cwd || '') + '"' + (done ? ' disabled' : '') + '>'
            + (done ? '✓ Spawned' : '▶ Spawn follow-up of ' + askEscapeHtml(askNeutralizeMarkers(src.title || src.id.slice(0, 8)))) + '</button>';
        } else if (src) html += askSessionChipHtml(src);
        cursor = match.index + match[0].length;
      }
      if (!matched) return;
      html += askEscapeHtml(text.slice(cursor));
      const fragment = document.createElement('template');
      fragment.innerHTML = html;
      node.replaceWith(fragment.content);
    });
    return template.innerHTML;
  }

  function askUsageHtml(t) {
    const u = t && t.usage;
    if (!u || typeof u.cache_adjusted_tokens !== 'number') return '';
    const n = (v) => (Number(v) || 0).toLocaleString();
    const title = n(u.input_tokens) + ' fresh in + ' + n(u.cache_creation_input_tokens) + ' cache write + '
      + n(u.cache_read_input_tokens) + ' cache read + ' + n(u.output_tokens) + ' out'
      + (u.model ? ' (' + u.model + ')' : '');
    return '<div class="ask-turn-usage" title="' + askEscapeHtml(title) + '">'
      + n(u.cache_adjusted_tokens) + ' cache-adjusted tokens</div>';
  }

  function askMessageActionsHtml() {
    return '<span class="assistant-message-actions" data-role="assistant-message-actions">'
      + '<button type="button" class="assistant-message-action" data-ask-file-issue title="File an issue about this response" aria-label="File an issue">+</button>'
      + '<button type="button" class="assistant-message-action" data-read-assistant-message title="Read answer aloud" aria-label="Read answer aloud">&#128266;</button>'
      + '<button type="button" class="assistant-message-action" data-copy-assistant-message title="Copy answer" aria-label="Copy answer">&#128203;</button>'
      + '</span>';
  }

  function askOpenFileIssue(turn, idx, turnEl) {
    const existing = document.getElementById('askIssueModal');
    if (existing) existing.remove();
    const when = turn.at ? new Date(turn.at).toLocaleString() : 'unknown (predates turn timestamps)';
    const selector = turnEl
      ? '.ask-log .ask-turn[data-ask-turn-index="' + idx + '"]'
      : '.ask-log .ask-turn';
    const context = [
      '--- auto-context (do not edit) ---',
      'Ask (Mazkir) turn ' + idx + (turn.id ? ' · turn id ' + turn.id : ''),
      'Time: ' + when,
      'Page: ' + location.href,
      'Selector: ' + selector,
      '',
      'Question:',
      turn.q,
      '',
      'Response:',
      String(turn.a || ''),
    ].join('\n');
    const modal = document.createElement('div');
    modal.id = 'askIssueModal';
    modal.className = 'ann-ux-preview-modal';
    modal.innerHTML =
      '<div class="ann-ux-preview-card">' +
        '<div class="ann-ux-preview-title">File an issue (Mazkir queue)</div>' +
        '<div class="ann-ux-preview-label">Issue - the question, response, time, and ids below are included automatically. Paste an image with ⌥-V:</div>' +
        '<textarea class="ann-ux-preview-text" rows="5" id="askIssueText" placeholder="Describe the issue…"></textarea>' +
        '<div class="ann-ux-preview-label">Included automatically:</div>' +
        '<textarea class="ann-ux-preview-text" rows="12" spellcheck="false" readonly></textarea>' +
        '<div class="ann-ux-preview-actions">' +
          '<button type="button" class="ann-btn" data-ask-issue-cancel>Cancel</button>' +
          '<button type="button" class="ann-btn ann-primary" data-ask-issue-submit>File issue</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
    const issueEl = modal.querySelector('#askIssueText');
    const contextEl = modal.querySelectorAll('.ann-ux-preview-text')[1];
    contextEl.value = context;
    try { if (typeof sharedState.attachImagePaste === 'function') (0, sharedState.attachImagePaste)(issueEl); } catch (_) {}
    const close = () => { modal.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if ((0, sharedState.isImeKey)(e)) return; if (e.key === 'Escape') { e.preventDefault(); close(); } };
    document.addEventListener('keydown', onKey, true);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    modal.querySelector('[data-ask-issue-cancel]').addEventListener('click', close);
    const submitBtn = modal.querySelector('[data-ask-issue-submit]');
    let submitting = false;
    submitBtn.addEventListener('click', async () => {
      if (submitting) return;
      const issue = (issueEl.value || '').trim();
      if (!issue) {
        if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Describe the issue first', 'warn');
        return;
      }
      if (typeof sharedState.guardComposerSend === 'function' && !(0, sharedState.guardComposerSend)(issueEl)) return;
      submitting = true;
      submitBtn.disabled = true;
      submitBtn.textContent = 'Filing…';
      try {
        const shotMatch = issue.match(/(\/[^\s'"()]+\.(?:png|jpe?g|gif|webp))/i);
        const firstLine = issue.split('\n')[0].trim();
        const res = await fetch('/api/annotations/mazkir-queue', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text: issue + '\n\n' + context,
            note: firstLine.slice(0, 400),
            title: firstLine.slice(0, 160),
            url: location.href,
            selector: selector,
            screenshot_path: shotMatch ? shotMatch[1] : '',
            source: 'ccc',
            engine: (typeof sharedState.getSpawnEngine === 'function') ? (0, sharedState.getSpawnEngine)() : 'claude',
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
        if (typeof sharedState.showOpToast === 'function') {
          (0, sharedState.showOpToast)(data.number ? ('Filed as MAZKIR #' + data.number) : 'Issue filed to MAZKIR queue', 'success');
        }
        close();
      } catch (err) {
        submitting = false;
        submitBtn.disabled = false;
        submitBtn.textContent = 'File issue';
        if (typeof sharedState.showOpToast === 'function') {
          (0, sharedState.showOpToast)('File issue failed: ' + ((err && err.message) || 'unknown'), 'error');
        }
      }
    });
    setTimeout(() => issueEl.focus(), 0);
  }

  function askLoadHistory() {
    try { return JSON.parse(localStorage.getItem(sharedState.ASK_HISTORY_KEY) || '[]') || []; }
    catch (_) { return []; }
  }

  function askSaveHistory(turns) {
    try { localStorage.setItem(sharedState.ASK_HISTORY_KEY, JSON.stringify(turns.slice(-12))); } catch (_) {}
  }

  function initAskPane() {
    const log = document.getElementById('askLog');
    const form = document.getElementById('askForm');
    const input = document.getElementById('askInput');
    const send = document.getElementById('askSend');
    const clearBtn = document.getElementById('askClear');
    const selBar = document.getElementById('askSelBar');
    const rangeBtn = document.getElementById('askRangeBtn');
    if (!log || !form || !input) return;

    let turns = askLoadHistory();
    // Global selection across the whole log (⌘/ctrl-click a result card) —
    // one at a time, surfaced as an actions bar above the composer.
    let selection = null;
    let rangeKey = 'any';
    try { rangeKey = localStorage.getItem('ccc-ask-range') || 'any'; } catch (_) {}
    if (!sharedState.ASK_RANGE_LABELS[rangeKey]) rangeKey = 'any';

    function drawRangeBtn() {
      if (rangeBtn) rangeBtn.textContent = sharedState.ASK_RANGE_LABELS[rangeKey] + ' ▾';
    }

    function drawSelBar() {
      if (!selBar) return;
      if (!selection) { selBar.hidden = true; selBar.innerHTML = ''; return; }
      selBar.hidden = false;
      selBar.innerHTML =
        '<div class="ask-sel-row1">' +
        '<span class="ask-sel-title">' + askEscapeHtml(selection.title || selection.id) + '</span>' +
        '<button type="button" class="ask-sel-close" data-ask-sel-action="clear" title="Clear selection">✕</button>' +
        '</div>' +
        '<div class="ask-sel-id">' + askEscapeHtml(selection.id) + '</div>' +
        '<div class="ask-sel-actions">' +
        '<button type="button" class="ask-sel-btn is-primary" data-ask-sel-action="spawn">+ Spawn</button>' +
        '<button type="button" class="ask-sel-btn" data-ask-sel-action="copy">Copy id</button>' +
        '<button type="button" class="ask-sel-btn" data-ask-sel-action="pin">Pin</button>' +
        '</div>';
    }

    // Sticky-bottom: auto-scroll on every redraw, EXCEPT once the user has
    // deliberately scrolled up to read something earlier — then leave the
    // view alone until they scroll back near the bottom themselves.
    // Set from /api/assistant/warm: true only where a check-in agenda exists.
    let askCheckinEnabled = false;
    let stickToBottom = true;
    log.addEventListener('scroll', () => {
      stickToBottom = (log.scrollHeight - log.scrollTop - log.clientHeight) < 40;
    });

    function draw() {
      if (!turns.length) {
        log.innerHTML =
          '<div class="ask-empty-state">' +
          '<div class="ask-empty-icon"><span class="ask-empty-glyph">✨</span></div>' +
          '<div class="ask-empty-title">Ask Mazkir</div>' +
          '<div class="ask-empty-desc">Search conversation transcripts, sessions, and fleet history across all your projects.</div>' +
          '<div class="ask-empty-prompts">' +
          (askCheckinEnabled ? '<button type="button" class="ask-prompt-chip" data-ask-prompt="Daily check-in: what is open on my agenda and what should we discuss first?">Daily check-in</button>' : '') +
          '<button type="button" class="ask-prompt-chip" data-ask-prompt="What did I work on today?">What did I work on today?</button>' +
          '<button type="button" class="ask-prompt-chip" data-ask-prompt="Is anything stuck or waiting on me?">Anything stuck?</button>' +
          '<button type="button" class="ask-prompt-chip" data-ask-prompt="Show recent Antigravity sessions">Recent Antigravity sessions</button>' +
          '<button type="button" class="ask-prompt-chip" data-ask-prompt="Which sessions are currently working?">Working sessions</button>' +
          '</div>' +
          '</div>';
        drawSelBar();
        return;
      }
      // Quick prompts stay reachable after the first turn: the empty state
      // (with the full chip list) never shows again once history exists.
      const quickStrip = '<div class="ask-quick-prompts">' +
        (askCheckinEnabled ? '<button type="button" class="ask-prompt-chip" data-ask-prompt="Daily check-in: what is open on my agenda and what should we discuss first?">☀️ Daily check-in</button>' : '') +
        '<button type="button" class="ask-prompt-chip" data-ask-prompt="What did I work on today?">Today</button>' +
        '<button type="button" class="ask-prompt-chip" data-ask-prompt="Which sessions are currently working?">Working</button>' +
        '</div>';
      log.innerHTML = quickStrip + turns.map((t, idx) => {
        if (t.pending) {
          return '<div class="ask-turn is-pending" data-ask-turn-index="' + idx + '">' +
            '<div class="ask-turn-q"><span class="ask-user-badge">YOU</span><div class="ask-q-text">' + askEscapeHtml(t.q) + '</div></div>' +
            '<div class="ask-turn-a assistant-text ask-turn-pending">' +
            '<div class="ask-assistant-header"><span class="ask-assistant-glyph">M</span><span class="ask-assistant-name">Mazkir</span></div>' +
            '<div class="ask-thinking-wrap"><span class="ask-thinking">Thinking' + (t.pendingElapsed ? ' · ' + t.pendingElapsed + 's' : '') + '</span></div></div>' +
            '</div>';
        }
        const elapsedSec = Number.isFinite(t.elapsedMs) ? (t.elapsedMs / 1000).toFixed(1) + 's' : '';
        return '<div class="ask-turn" data-ask-turn-index="' + idx + '">' +
          '<div class="ask-turn-q"><span class="ask-user-badge">YOU</span><div class="ask-q-text">' + askEscapeHtml(t.q) + '</div></div>' +
          '<div class="ask-turn-a assistant-text' + (t.error ? ' is-error' : '') + '">' +
          '<div class="ask-assistant-header"><span class="ask-assistant-glyph">M</span><span class="ask-assistant-name">Mazkir</span>' + (elapsedSec ? '<span class="ask-turn-time">' + elapsedSec + '</span>' : '') + '</div>' +
          (t.error ? '' : askTraceHtml(t)) +
          '<div class="ask-turn-body">' + (t.error ? askEscapeHtml(t.a) : renderAskVerdict(t.a, t.sources, t.spawned)) + '</div>' +
          (!t.error && String(t.a || '').trim() ? askMessageActionsHtml() : '') +
          (t.error ? '' : askUsageHtml(t)) + '</div>' +
          (t.error ? '' : askConfirmCardsHtml(t)) +
          (t.error ? '' : askResultsHtml(t, selection && selection.id)) +
          '</div>';
      }).join('');
      // renderAskVerdict/renderMarkdown already escaped the answer into HTML;
      // stash the raw text as a JS property (not an attribute) so the shared
      // copy/read-aloud handlers get clean prose instead of re-scraping the
      // rendered markup (which would include button labels, table pipes…).
      log.querySelectorAll('.ask-turn-a').forEach((el, idx) => {
        const t = turns[idx];
        if (t && !t.error && !t.pending) el._agentAnswerText = String(t.a || '').replace(sharedState.ASK_CONFIRM_MARKER_RE, '').trim();
      });
      if (stickToBottom) log.scrollTop = log.scrollHeight;
      drawSelBar();
    }

    log.addEventListener('click', (ev) => {
      const promptBtn = ev.target.closest('[data-ask-prompt]');
      if (promptBtn) {
        const text = promptBtn.getAttribute('data-ask-prompt');
        if (text && input) {
          input.value = text;
          submit();
        }
        return;
      }

      const decideBtn = ev.target.closest('[data-ask-confirm], [data-ask-dismiss]');
      if (decideBtn && !decideBtn.disabled) {
        const confirm = decideBtn.hasAttribute('data-ask-confirm');
        const aid = decideBtn.getAttribute(confirm ? 'data-ask-confirm' : 'data-ask-dismiss');
        const turnEl = decideBtn.closest('[data-ask-turn-index]');
        const turn = turns[turnEl ? parseInt(turnEl.getAttribute('data-ask-turn-index'), 10) : -1];
        const act = turn && (turn.confirmActions || []).find(a => a.id === aid);
        if (!act) return;
        decideBtn.disabled = true;
        // Recorded on the turn (not just the DOM) so a redraw can't re-enable
        // the button and fire the action twice.
        act.status = confirm ? 'running' : 'dismissed';
        draw();
        (0, sharedState.ccPostJson)('/api/assistant/actions/' + encodeURIComponent(aid) + (confirm ? '/confirm' : '/dismiss'),
          { token: act.confirm_token })
          .then(d => {
            const next = (d && d.action) || {};
            act.status = next.status || (confirm ? 'failed' : 'dismissed');
            act.result = next.result || (d && d.ok ? null : { error: (d && d.error) || 'failed' });
          })
          .catch(e => { act.status = 'failed'; act.result = { error: e.message || String(e) }; })
          .finally(() => { delete act.confirm_token; askSaveHistory(turns); draw(); });
        return;
      }

      const card = ev.target.closest('[data-ask-open]');
      if (card) {
        const id = card.getAttribute('data-ask-open');
        if (ev.metaKey || ev.ctrlKey) {
          selection = (selection && selection.id === id) ? null : {
            id, title: card.getAttribute('data-ask-title') || id,
            cwd: card.getAttribute('data-ask-cwd') || '',
          };
          draw();
        } else {
          _setMobileAskOpen(false);
          (0, sharedState.selectConversation)(id);
        }
        return;
      }

      const act = ev.target.closest('[data-ask-continue]');
      if (act && !act.disabled) {
        act.disabled = true;
        act.textContent = 'Spawning…';
        const sid = act.getAttribute('data-ask-continue');
        const cwd = act.getAttribute('data-ask-cwd') || '';
        // The confirm-once state (disabled + ✓ Spawned) has to survive the
        // next draw() — draw() rebuilds log.innerHTML from `turns` on every
        // answer/load, which would silently re-enable a DOM-only disabled
        // flag and let one click fire two spawns. Record it on the turn
        // instead so renderAskVerdict can render it done on redraw.
        const turnEl = act.closest('[data-ask-turn-index]');
        const turnIdx = turnEl ? parseInt(turnEl.getAttribute('data-ask-turn-index'), 10) : -1;
        fetch('/api/sessions/spawn', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: 'Continue the work from session ' + sid +
              '. Start by reading that session\'s transcript/context to pick up where it left off.',
            repo_path: cwd || undefined,
          }),
        }).then(r => r.json()).then(d => {
          if (d && d.ok) {
            act.textContent = '✓ Spawned';
            const turn = turns[turnIdx];
            if (turn) {
              turn.spawned = turn.spawned || {};
              turn.spawned[sid] = true;
              askSaveHistory(turns);
            }
          } else {
            act.textContent = '✗ ' + ((d && d.error) || 'spawn failed');
          }
        }).catch(e => { act.textContent = '✗ ' + e; });
        return;
      }

      const issueBtn = ev.target.closest('[data-ask-file-issue]');
      if (issueBtn) {
        ev.preventDefault();
        ev.stopPropagation();
        const turnEl = issueBtn.closest('[data-ask-turn-index]');
        const idx = turnEl ? parseInt(turnEl.getAttribute('data-ask-turn-index'), 10) : -1;
        const turn = turns[idx];
        if (turn && !turn.pending) askOpenFileIssue(turn, idx, turnEl);
        return;
      }
    });

    if (selBar) selBar.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-ask-sel-action]');
      if (!btn || !selection) return;
      const action = btn.getAttribute('data-ask-sel-action');
      const sel = selection;
      if (action === 'clear') { selection = null; drawSelBar(); return; }
      if (action === 'copy') {
        if (navigator.clipboard) navigator.clipboard.writeText(sel.id).catch(() => {});
        (0, sharedState.showOpToast)('Copied ' + sel.id);
        return;
      }
      if (action === 'pin') {
        btn.disabled = true;
        (0, sharedState.ccPostJson)('/api/conversations/' + encodeURIComponent(sel.id) + '/pin', { session_id: sel.id })
          .then(d => (0, sharedState.showOpToast)(d && d.pinned ? 'Pinned to top' : 'Unpinned'))
          .catch(e => (0, sharedState.showOpToast)('Pin failed: ' + e.message, 'error'))
          .finally(() => { btn.disabled = false; });
        return;
      }
      if (action === 'spawn') {
        btn.disabled = true;
        btn.textContent = 'Spawning…';
        fetch('/api/sessions/spawn', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: 'Continue the work from session ' + sel.id +
              '. Start by reading that session\'s transcript/context to pick up where it left off.',
            repo_path: sel.cwd || undefined,
          }),
        }).then(r => r.json()).then(d => {
          btn.textContent = d && d.ok ? '✓ Spawned' : '✗ ' + ((d && d.error) || 'spawn failed');
        }).catch(e => { btn.textContent = '✗ ' + e; })
          .finally(() => { setTimeout(() => { btn.disabled = false; }, 1200); });
        return;
      }
    });

    if (rangeBtn) rangeBtn.addEventListener('click', () => {
      const i = sharedState.ASK_RANGE_ORDER.indexOf(rangeKey);
      rangeKey = sharedState.ASK_RANGE_ORDER[(i + 1) % sharedState.ASK_RANGE_ORDER.length];
      try { localStorage.setItem('ccc-ask-range', rangeKey); } catch (_) {}
      drawRangeBtn();
    });

    let busy = false;
    // CCC-1214: tell Mazkir which session is open so "this session" /
    // "the session on the screen" resolves instead of being guessed.
    function askFocusedSession() {
      const convId = (typeof sharedState.currentConversation !== 'undefined') ? sharedState.currentConversation : '';
      if (!convId || convId === '__new__') return null;
      const row = (sharedState.conversationsData || []).find(c => c && c.id === convId) || null;
      const sid = (row && row.session_id) || (typeof sharedState.sessionIdByConv !== 'undefined' && sharedState.sessionIdByConv[convId]) || '';
      if (!sid) return null;
      return { session_id: sid, title: (row && row.display_name) || '' };
    }
    async function submit() {
      const q = (input.value || '').trim();
      if (!q || busy) return;
      busy = true;
      if (send) send.disabled = true;
      input.value = '';
      // Echo the question immediately as a pending turn — the model call
      // takes 20-40s, and the user should see what they typed land in the
      // log right away instead of staring at an empty pane.
      const turn = { q, a: '', pending: true };
      turns.push(turn);
      stickToBottom = true;
      draw();
      const started = Date.now();
      const tick = setInterval(() => {
        turn.pendingElapsed = Math.round((Date.now() - started) / 1000);
        const el = log.querySelector('.ask-turn-pending .ask-thinking');
        if (el) el.textContent = 'Thinking · ' + turn.pendingElapsed + 's';
      }, 1000);
      try {
        const history = turns.filter(t => !t.error && !t.pending).slice(-sharedState.ASK_HISTORY_TURNS)
          .map(t => ({ q: t.q, a: t.a }));
        const res = await fetch('/api/assistant/ask', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question: q, history, range: rangeKey, focused: askFocusedSession() }),
        });
        const data = await res.json();
        delete turn.pending;
        if (data && data.ok) {
          turn.a = data.answer;
          turn.sources = data.sources || [];
          turn.hitCount = data.hit_count;
          turn.elapsedMs = data.elapsed_ms;
          turn.ttftMs = data.ttft_ms;
          turn.processMode = data.process_mode;
          turn.usage = data.usage || null;
          turn.trace = data.trace || [];
          turn.confirmActions = data.confirm_actions || [];
          // CCC-1048: "File an issue" embeds when/id in the ticket context.
          turn.at = Date.now();
          turn.id = turn.at.toString(36) + '-' + Math.random().toString(36).slice(2, 8);
        } else {
          turn.a = (data && data.error) || ('HTTP ' + res.status);
          turn.error = true;
          turn.at = Date.now();
        }
      } catch (e) {
        delete turn.pending;
        turn.a = 'Request failed: ' + e;
        turn.error = true;
      } finally {
        clearInterval(tick);
        busy = false;
        if (send) send.disabled = false;
        askSaveHistory(turns);
        draw();
      }
    }

    form.addEventListener('submit', (ev) => { ev.preventDefault(); submit(); });
    input.addEventListener('keydown', (ev) => {
      if ((0, sharedState.isImeKey)(ev)) return;
      if (ev.isComposing) return;
      if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); submit(); }
    });
    if (clearBtn) clearBtn.addEventListener('click', () => {
      turns = [];
      selection = null;
      askSaveHistory(turns);
      draw();
    });
    // Boot the warm Mazkir process before the first question: the CLI and its
    // MCP servers take seconds to start. Idempotent server-side; throttled here.
    let lastWarm = 0;
    const warmUp = () => {
      if (Date.now() - lastWarm < 60000) return;
      lastWarm = Date.now();
      (0, sharedState.ccPostJson)('/api/assistant/warm', {}).then((d) => {
        // The check-in chip only makes sense where an agenda file exists.
        const on = !!(d && d.daily_checkin);
        if (on !== askCheckinEnabled) { askCheckinEnabled = on; draw(); }
      }).catch(() => {});
    };
    input.addEventListener('focus', warmUp);
    sharedState.askWarmUp = warmUp;
    const askPane = document.querySelector('[data-rail-pane="ask"]');
    if (askPane && !askPane.hidden) warmUp();
    drawRangeBtn();
    draw();
  }

  function installConversationView18() {
onDashboardDocumentReady(() => {
    const $pill = document.getElementById('historyStatusPill');
    if ($pill) {
      $pill.addEventListener('click', () => _hiTriggerSetup());
      $pill.addEventListener('keydown', (e) => {
        if ((0, sharedState.isImeKey)(e)) return;
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _hiTriggerSetup(); }
      });
    }
    if (!sharedState.READER_ONLY_POPOUT) _hiStartPolling();

    const $rail = document.getElementById('statusRail');
    if ($rail) {
      $rail.querySelectorAll('[data-rail-tab]').forEach(btn => {
        btn.addEventListener('click', () => (0, sharedState.setStatusRailTab)(btn.getAttribute('data-rail-tab') || 'metadata'));
      });
      let savedRailTab = 'orchestration';
      try { savedRailTab = localStorage.getItem('ccc-status-rail-tab') || 'orchestration'; } catch (_) {}
      (0, sharedState.setStatusRailTab)(savedRailTab);
    }
    initAskPane();
    initRailLogPane();

    const $fileViewerClose = document.getElementById('fileViewerCloseBtn');
    if ($fileViewerClose) {
      $fileViewerClose.addEventListener('click', () => {
        if (typeof sharedState.closeStatusRailFileViewer === 'function') (0, sharedState.closeStatusRailFileViewer)();
      });
    }
    const $fileViewerOpenExt = document.getElementById('fileViewerOpenExternalBtn');
    if ($fileViewerOpenExt) {
      $fileViewerOpenExt.addEventListener('click', async () => {
        const viewer = document.getElementById('statusRailFileViewer');
        const path = viewer && viewer.getAttribute('data-current-path');
        if (!path) return;
        const sid = (sharedState.currentSession && sharedState.currentSession.id) || '';
        $fileViewerOpenExt.disabled = true;
        try {
          const r = await fetch('/api/reveal-file', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ path, session_id: sid }),
          });
          if (!r.ok) {
            const j = await r.json().catch(() => ({ error: 'open failed' }));
            (0, sharedState.showOpToast)('Open failed: ' + (j.error || ('HTTP ' + r.status)), 'error');
          }
        } catch (err) {
          (0, sharedState.showOpToast)('Open failed: ' + (err && err.message || 'network error'), 'error');
        } finally {
          $fileViewerOpenExt.disabled = false;
        }
      });
    }
    const $fileViewerRefresh = document.getElementById('fileViewerRefreshBtn');
    if ($fileViewerRefresh) {
      $fileViewerRefresh.addEventListener('click', async () => {
        const viewer = document.getElementById('statusRailFileViewer');
        const path = viewer && viewer.getAttribute('data-current-path');
        if (!path) return;
        const label = (document.getElementById('fileViewerFilename') || {}).textContent || null;
        $fileViewerRefresh.disabled = true;
        $fileViewerRefresh.classList.add('is-spinning');
        try {
          await (0, sharedState.loadMarkdownIntoFileViewer)(path, label);
        } catch (err) {
          (0, sharedState.showOpToast)('Refresh failed: ' + (err && err.message || 'network error'), 'error');
        } finally {
          $fileViewerRefresh.disabled = false;
          $fileViewerRefresh.classList.remove('is-spinning');
        }
      });
    }

    // The right rail is desktop-only. The responsive breakpoint moves its
    // content back to the top layout automatically on mobile.
    const $statusRail = document.getElementById('statusRail');
    const $statusRailResizer = document.getElementById('statusRailResizer');
    const $statusRailRestore = document.getElementById('statusRailRestoreBtn');
    const $popoutSidePanel = document.getElementById('popoutSidePanelBtn');
    const STATUS_RAIL_DEFAULT_WIDTH = 260;
    const STATUS_RAIL_MIN_WIDTH = 220;
    const FILE_VIEWER_RAIL_EXPAND_FACTOR = 2.5;
    // Generous cap so the rail can host the MD file viewer at a
    // readable width on big screens. The natural ceiling is
    // (paneWidth - 260) so the conversation never shrinks below
    // ~260px of legible space.
    const STATUS_RAIL_MAX_WIDTH = 1600;
    const STATUS_RAIL_COLLAPSE_WIDTH = 130;
    const _statusRailMaxWidth = () => {
      const pane = document.querySelector('.conv-pane.is-active')
        || document.querySelector('.conv-pane[data-pane-id="' + (0, sharedState.activePaneId)() + '"]')
        || document.querySelector('.conv-pane');
      const paneWidth = pane ? pane.getBoundingClientRect().width : window.innerWidth;
      return Math.max(STATUS_RAIL_MIN_WIDTH, Math.min(STATUS_RAIL_MAX_WIDTH, Math.round(paneWidth - 260)));
    };
    const _clampStatusRailWidth = (width) => {
      const n = Number(width);
      const raw = Number.isFinite(n) && n > 0 ? n : STATUS_RAIL_DEFAULT_WIDTH;
      return Math.max(STATUS_RAIL_MIN_WIDTH, Math.min(_statusRailMaxWidth(), Math.round(raw)));
    };
    const _savedStatusRailWidth = () => {
      const saved = parseInt(localStorage.getItem('ccc-status-rail-width') || '0', 10);
      return saved >= STATUS_RAIL_MIN_WIDTH ? saved : STATUS_RAIL_DEFAULT_WIDTH;
    };
    let fileViewerPreviousRailWidth = null;
    let fileViewerPreviousRailStoredWidth = null;
    // Resizing/collapsing the rail re-flows the conversation reader (its pane
    // gets wider/narrower → text re-wraps → content height changes), which
    // drifts the reading position under a fixed scrollTop. Capture each open
    // reader's top-visible message (or its bottom, if it was tailing), run the
    // layout mutation, then restore — so the position stays put. Reading
    // scrollHeight/offsetTop in the restore forces the pending reflow to flush
    // against the post-mutation layout.
    const _preserveReadersAcross = (mutate) => {
      const readers = Array.from(document.querySelectorAll('.conversations-view')).map(($v) => ({
        $v,
        atBottom: (0, sharedState.isConversationAtBottom)($v),
        anchor: (0, sharedState._topVisibleAnchor)($v),
      }));
      mutate();
      for (const r of readers) {
        if (r.atBottom) r.$v.scrollTop = r.$v.scrollHeight;
        else if (r.anchor) _restoreAnchor(r.$v, r.anchor);
      }
    };
    const _setStatusRailWidth = (width, persist) => {
      const next = _clampStatusRailWidth(width);
      _preserveReadersAcross(() => {
        document.documentElement.style.setProperty('--status-rail-width', next + 'px');
      });
      if ($statusRailResizer) {
        $statusRailResizer.setAttribute('aria-valuemin', String(STATUS_RAIL_MIN_WIDTH));
        $statusRailResizer.setAttribute('aria-valuemax', String(_statusRailMaxWidth()));
        $statusRailResizer.setAttribute('aria-valuenow', String(next));
      }
      if (persist) {
        try { localStorage.setItem('ccc-status-rail-width', String(next)); } catch (_) {}
      }
      return next;
    };
    const _expandStatusRailForFileViewer = () => {
      if (!$statusRail || !document.body.classList.contains('status-pos-right')) return;
      if (fileViewerPreviousRailWidth == null) {
        fileViewerPreviousRailWidth = $statusRail.getBoundingClientRect().width || _savedStatusRailWidth();
        try { fileViewerPreviousRailStoredWidth = localStorage.getItem('ccc-status-rail-width'); } catch (_) { fileViewerPreviousRailStoredWidth = null; }
      }
      _setStatusRailCollapsed(false, false);
      _setStatusRailWidth(fileViewerPreviousRailWidth * FILE_VIEWER_RAIL_EXPAND_FACTOR, false);
    };
    const _restoreStatusRailAfterFileViewer = () => {
      if (fileViewerPreviousRailWidth == null) return;
      const previousWidth = fileViewerPreviousRailWidth;
      const previousStoredWidth = fileViewerPreviousRailStoredWidth;
      fileViewerPreviousRailWidth = null;
      fileViewerPreviousRailStoredWidth = null;
      _setStatusRailWidth(previousWidth, false);
      try {
        if (previousStoredWidth == null) localStorage.removeItem('ccc-status-rail-width');
        else localStorage.setItem('ccc-status-rail-width', previousStoredWidth);
      } catch (_) {}
    };
    window._cccExpandStatusRailForFileViewer = _expandStatusRailForFileViewer;
    window._cccRestoreStatusRailAfterFileViewer = _restoreStatusRailAfterFileViewer;
    const _setStatusRailCollapsed = (collapsed, persist) => {
      _preserveReadersAcross(() => {
        document.body.classList.toggle('status-rail-collapsed', !!collapsed);
        if (!collapsed) _setStatusRailWidth(_savedStatusRailWidth(), false);
      });
      if ($statusRailRestore) {
        $statusRailRestore.setAttribute('aria-expanded', String(!collapsed));
      }
      if (persist) {
        try {
          if (collapsed) localStorage.setItem('ccc-status-rail-collapsed', '1');
          else localStorage.removeItem('ccc-status-rail-collapsed');
        } catch (_) {}
      }
    };
    _setStatusRailWidth(_savedStatusRailWidth(), false);
    _setStatusRailCollapsed(document.body.classList.contains('status-rail-collapsed'), false);
    // Quick-close X inside the rail (top-right). Persisted so a
    // reload keeps the rail closed unless the user re-opens it via
    // the topbar restore button.
    const $statusRailClose = document.getElementById('statusRailCloseBtn');
    if ($statusRailClose) {
      $statusRailClose.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        _setStatusRailCollapsed(true, true);
      });
    }
    if ($statusRailRestore) {
      $statusRailRestore.addEventListener('click', () => {
        document.body.classList.add('status-pos-right');
        _setStatusRailCollapsed(false, true);
        (0, sharedState._applyStatusRailLayout)();
        if (typeof window._cccApplyToolbarRailLayout === 'function') {
          window._cccApplyToolbarRailLayout();
        }
      });
    }
    if ($popoutSidePanel) {
      $popoutSidePanel.addEventListener('click', () => {
        if (document.body.classList.contains('status-rail-collapsed')) {
          if ($statusRailRestore) $statusRailRestore.click();
          return;
        }
        // Narrow popout window: no collapsed-rail state to restore, the
        // rail is just hidden by the mobile width breakpoint. Force it
        // back on (CCC-831) rather than delegating to the restore button,
        // which only clears `status-rail-collapsed`.
        document.body.classList.toggle('popout-rail-forced');
        (0, sharedState._applyStatusRailLayout)();
      });
    }
    if ($statusRail && $statusRailResizer) {
      let railStartX = 0;
      let railStartWidth = STATUS_RAIL_DEFAULT_WIDTH;
      let railCollapseOnRelease = false;
      let railPointerId = null;
      const _railShouldCollapse = (e, rawWidth) => {
        const pane = document.querySelector('.conv-pane.is-active')
          || document.querySelector('.conv-pane[data-pane-id="' + (0, sharedState.activePaneId)() + '"]')
          || document.querySelector('.conv-pane');
        const right = pane ? pane.getBoundingClientRect().right : window.innerWidth;
        return rawWidth <= STATUS_RAIL_COLLAPSE_WIDTH || e.clientX >= right - 28;
      };
      const _railMove = (e) => {
        const raw = railStartWidth + (railStartX - e.clientX);
        railCollapseOnRelease = _railShouldCollapse(e, raw);
        if (!railCollapseOnRelease) {
          _setStatusRailWidth(raw, false);
        }
      };
      const _railEnd = (e) => {
        if (railPointerId !== e.pointerId) return;
        $statusRailResizer.classList.remove('dragging');
        document.body.classList.remove('status-rail-resizing');
        try { $statusRailResizer.releasePointerCapture(e.pointerId); } catch (_) {}
        railPointerId = null;
        if (railCollapseOnRelease) {
          _setStatusRailCollapsed(true, true);
        } else {
          const finalWidth = $statusRail.getBoundingClientRect().width;
          _setStatusRailWidth(finalWidth, true);
          _setStatusRailCollapsed(false, true);
        }
      };
      $statusRailResizer.addEventListener('pointerdown', (e) => {
        if (!document.body.classList.contains('status-pos-right') || !e.isPrimary
            || (e.pointerType !== 'touch' && e.button !== 0)) return;
        e.preventDefault();
        railPointerId = e.pointerId;
        railStartX = e.clientX;
        railStartWidth = $statusRail.getBoundingClientRect().width || _savedStatusRailWidth();
        railCollapseOnRelease = false;
        $statusRailResizer.classList.add('dragging');
        document.body.classList.add('status-rail-resizing');
        try { $statusRailResizer.setPointerCapture(e.pointerId); } catch (_) {}
      });
      $statusRailResizer.addEventListener('pointermove', (e) => {
        if (railPointerId !== e.pointerId) return;
        _railMove(e);
      });
      $statusRailResizer.addEventListener('pointerup', _railEnd);
      $statusRailResizer.addEventListener('pointercancel', _railEnd);
      $statusRailResizer.addEventListener('dblclick', (e) => {
        e.preventDefault();
        _setStatusRailCollapsed(false, true);
        _setStatusRailWidth(STATUS_RAIL_DEFAULT_WIDTH, true);
      });
      $statusRailResizer.addEventListener('keydown', (e) => {
        if ((0, sharedState.isImeKey)(e)) return;
        if (!document.body.classList.contains('status-pos-right')) return;
        const current = $statusRail.getBoundingClientRect().width || _savedStatusRailWidth();
        const step = e.shiftKey ? 40 : 20;
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
          e.preventDefault();
          _setStatusRailCollapsed(false, true);
          _setStatusRailWidth(current + (e.key === 'ArrowLeft' ? step : -step), true);
        } else if (e.key === 'Home') {
          e.preventDefault();
          _setStatusRailCollapsed(false, true);
          _setStatusRailWidth(STATUS_RAIL_DEFAULT_WIDTH, true);
        } else if (e.key === 'End') {
          e.preventDefault();
          _setStatusRailCollapsed(true, true);
        }
      });
      window.addEventListener('resize', () => {
        if (document.body.classList.contains('status-pos-right') && !(0, sharedState.isMobile)()
            && !document.body.classList.contains('status-rail-collapsed')) {
          _setStatusRailWidth($statusRail.getBoundingClientRect().width || _savedStatusRailWidth(), false);
        }
        (0, sharedState.scheduleInputContextFit)();
      });
    }
    // Chip color toggle — cycles color → hierarchy-level → muted, persists,
    // no re-render needed (CSS handles the palette swap).
    const $chipsToggle = document.getElementById('chipsColorToggle');
    const $chipsIcon = document.getElementById('chipsColorIcon');
    const _chipsMode = () => document.body.classList.contains('chips-muted')
      ? 'muted'
      : (document.body.classList.contains('chips-level') ? 'level' : 'color');
    const _applyChipsMode = (mode) => {
      document.body.classList.toggle('chips-muted', mode === 'muted');
      document.body.classList.toggle('chips-level', mode === 'level');
    };
    const _syncChipsIcon = () => {
      if (!$chipsIcon) return;
      const mode = _chipsMode();
      // 4-dot swatch icon. In colored mode the dots have distinct hues so
      // the toggle visually mirrors what it controls; in level mode pairs
      // share hues; in muted mode the same dots collapse to a uniform grey.
      const dotColors = mode === 'muted'
        ? ['var(--text-muted)', 'var(--text-muted)', 'var(--text-muted)', 'var(--text-muted)']
        : mode === 'level'
          ? ['#58a6ff', '#58a6ff', '#3fb950', '#3fb950']
        // Saturated-but-not-loud hues. Match the rough palette used by
        // the per-folder chips (orange / green / blue / purple) so the
        // icon previews what the chips will look like.
          : ['#d29922', '#3fb950', '#58a6ff', '#bc8cff'];
      $chipsIcon.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">' +
          '<circle cx="4"  cy="4"  r="2.4" fill="' + dotColors[0] + '"/>' +
          '<circle cx="12" cy="4"  r="2.4" fill="' + dotColors[1] + '"/>' +
          '<circle cx="4"  cy="12" r="2.4" fill="' + dotColors[2] + '"/>' +
          '<circle cx="12" cy="12" r="2.4" fill="' + dotColors[3] + '"/>' +
        '</svg>';
      if ($chipsToggle) {
        $chipsToggle.setAttribute('aria-pressed', mode === 'level' ? 'mixed' : String(mode === 'muted'));
        $chipsToggle.title = mode === 'muted'
          ? 'Folder chips are muted. Click to colorize them per-folder.'
          : mode === 'level'
            ? 'Object and repo chips share colors by hierarchy level. Click to mute them.'
            : 'Folder chips are colored per-folder. Click for hierarchy-level colors.';
      }
    };
    if ($chipsToggle) {
      $chipsToggle.addEventListener('click', () => {
        const cur = _chipsMode();
        const next = cur === 'color' ? 'level' : (cur === 'level' ? 'muted' : 'color');
        _applyChipsMode(next);
        try { localStorage.setItem('ccc-chips-mode', next); } catch (_) {}
        _syncChipsIcon();
      });
    }
    // Settings > Appearance picks a mode directly instead of cycling. Exposed
    // rather than duplicated so both entry points share one persist+apply path.
    window.cccGetChipsMode = _chipsMode;
    window.cccSetChipsMode = (mode) => {
      _applyChipsMode(mode);
      try { localStorage.setItem('ccc-chips-mode', mode); } catch (_) {}
      _syncChipsIcon();
    };
    _syncChipsIcon();
    (0, sharedState._applyStatusRailLayout)();

    // #1 — Collapse the conv toolbar when it has no visible content.
    // After moving everything out (rail / sidebar / settings menu), the
    // remaining children are #convStatus (a status span, usually empty)
    // and #cccTopbar (now empty). Watch for any visible width/height in
    // the children and toggle `.is-empty` accordingly so the bar's
    // 12px+12px padding + 1px border don't eat ~25px of dead space.
    const $convToolbar = document.getElementById('convToolbar');
    function _refreshToolbarEmptiness() {
      if (!$convToolbar) return;
      let hasVisibleContent = false;
      for (const c of $convToolbar.children) {
        const r = c.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        // The element's own direct text (e.g. the mobile back button's
        // "←" glyph) counts as visible even when its only element child —
        // the " Back" label — is display:none on narrow phones. Without
        // this, a lone back button reads as "empty", the whole toolbar
        // collapses to height:0, and mobile users lose the only way back
        // to the sidebar (CCC-4).
        let directText = '';
        for (const node of c.childNodes) {
          if (node.nodeType === 3) directText += node.textContent;
        }
        if (directText.trim()) { hasVisibleContent = true; break; }
        // Treat empty containers (cccTopbar with no kids) as invisible
        // even if their flex layout reports >0 size.
        if (c.children.length > 0) {
          // recurse one level: any visible grandchild?
          for (const cc of c.children) {
            const rr = cc.getBoundingClientRect();
            if (rr.width > 0 && rr.height > 0) { hasVisibleContent = true; break; }
          }
          if (hasVisibleContent) break;
        } else if ((c.textContent || '').trim()) {
          hasVisibleContent = true; break;
        }
      }
      $convToolbar.classList.toggle('is-empty', !hasVisibleContent);
    }
    _refreshToolbarEmptiness();
    // Re-check when DOM mutations could change visibility (state-driven
    // toolbar buttons appearing/disappearing). Each check reads layout, and
    // a read right after a mutation forces a full synchronous layout of the
    // page (seconds in total while a long conversation renders), so batches
    // of mutations share one check on the next frame.
    if (window.MutationObserver && $convToolbar) {
      let toolbarCheckQueued = false;
      const mo = new MutationObserver(() => {
        if (toolbarCheckQueued) return;
        toolbarCheckQueued = true;
        requestAnimationFrame(() => { toolbarCheckQueued = false; _refreshToolbarEmptiness(); });
      });
      mo.observe($convToolbar, { childList: true, subtree: true, attributes: true, attributeFilter: ['style', 'class', 'hidden'] });
    }

    // Toolbar reorganization. Two flavors of move:
    //  1) Permanent: global (not conversation-scoped) buttons go to the
    //     left sidebar's `sidebarGlobalActions` and stay there in both
    //     modes. They aren't tied to the rail at all.
    //  2) Conditional: session-level buttons live in the right rail when
    //     `status-pos-right` is on, and back in their original toolbar
    //     slots when in top mode (so the rail can be removed entirely
    //     for mobile / narrow windows).
    //
    // We capture each conditional element's original parent at boot,
    // before any moves, so we can put them back exactly where they came
    // from when the user toggles back to top mode.
    const $sidebarActions = document.getElementById('sidebarGlobalActions');
    const $railActions = document.getElementById('railActions');
    const $toolbar = document.getElementById('convToolbar');

    // PERMANENT moves — these items have a single home regardless of the
    // status-pos toggle. Per user spec:
    //   • Top-left alerts (#sidebarTopAlerts): Update pill (notification,
    //     should always be visible until the user acts on it).
    //   • Settings menu (#settingsModalToolsSlot, W93 settings modal's
    //     Tools section): Terminal panel toggle, History indexing status,
    //     Worktrees, Stats, Report a bug, Font A-/A+. These are
    //     infrequent-use or "set and forget" — burying them in the
    //     settings modal reclaims rail space.
    const $topAlerts = document.getElementById('sidebarTopAlerts');
    const $settingsSlot = document.getElementById('settingsModalToolsSlot');
    const $settingsFontSlot = document.getElementById('settingsModalFontSlot');
    const _moveToHome = (id, host) => {
      const el = document.getElementById(id);
      if (el && host && el.parentElement !== host) host.appendChild(el);
    };
    if ($topAlerts) {
      _moveToHome('updPill', $topAlerts);
      _moveToHome('runawayWatchPill', $topAlerts);
      _moveToHome('workerCompatPill', $topAlerts);
      _moveToHome('configConsentPill', $topAlerts);
    }
    if ($settingsSlot) {
      _moveToHome('termToggleBtn',     $settingsSlot);
      _moveToHome('historyStatusPill', $settingsSlot);
      _moveToHome('kptWorktreesBtn',   $settingsSlot);
      _moveToHome('statsBtn',          $settingsSlot);
      _moveToHome('todayToggleBtn',    $settingsSlot);
      _moveToHome('annotationNotesBtn', $settingsSlot);
      // Clicking any launcher/action item in the Tools slot closes the
      // settings modal — except the A-/A+ font stepper (not actually
      // homed in this slot, but guarded defensively) which must stay
      // open across repeated presses.
      $settingsSlot.addEventListener('click', (e) => {
        if (e.target.closest('.font-size-controls')) return;
        if (typeof window._cccCloseSettingsModal === 'function') window._cccCloseSettingsModal();
      });
    }
    if ($toolbar && $settingsFontSlot) {
      const fontCtrls = $toolbar.querySelector('.font-size-controls');
      if (fontCtrls) $settingsFontSlot.appendChild(fontCtrls);
    }
    // Report a bug sits at the very left of the sidebar header action
    // row — closer to where bugs are noticed (the conversation list)
    // than buried in the settings gear. The static Refresh+Restart
    // split-button and History button stay to its right.
    const $sidebarHeaderActions = document.querySelector('.sidebar-header-actions');
    const $bugLinkEl = document.getElementById('bugReportLink');
    if ($sidebarHeaderActions && $bugLinkEl && $bugLinkEl.parentElement !== $sidebarHeaderActions) {
      $sidebarHeaderActions.prepend($bugLinkEl);
    }

    // CONDITIONAL set (toggle-aware): rail in right-mode, original toolbar
    // slot in top-mode. Trimmed to just the genuinely session-scoped
    // items now that the global ones moved to settings menu.
    const _railSet = [];
    const _captureRailEl = (el) => {
      if (!el) return;
      _railSet.push({
        el,
        origParent: el.parentElement,
        origNext: el.nextSibling,
      });
    };
    // #8 — Order the rail by frequency-of-use:
    //   1. Live signal (visual anchor — "is this session active?")
    //   2. Launch terminal (frequent action)
    //   3. Vercel deploy status
    //   4. Close & announce (rarer)
    //   5. Jump / Pkood-kill (state-conditional)
    //   6. Footer cluster: Session ID + overflow menu
    // Keep the breadcrumb in the top bar so its pop-out action remains at
    // the far right even while the status rail is open.
    _captureRailEl(document.getElementById('convStatus'));
    _captureRailEl(document.getElementById('liveBadgeConv'));
    _captureRailEl(document.getElementById('topbarTtsControl'));
    // Voice mode is global, not session-scoped, but its topbar home is the
    // same hidden #convToolbar — capture it here so it stays reachable in
    // the rail on desktop right-mode and returns to the topbar otherwise.
    _captureRailEl(document.getElementById('voiceModeBtn'));
    _captureRailEl(document.getElementById('launchWrapConv'));
    _captureRailEl(document.getElementById('deployPill'));
    _captureRailEl(document.getElementById('annotationStartBtn'));
    _captureRailEl(document.getElementById('annotationScreenBtn'));
    // Capture the WHOLE localhost wrap (pill + caret dropdown + menu), not just
    // the pill — otherwise the rail relocates the bare pill and orphans the
    // CCC-172 caret/menu back in the (hidden) topbar, so the rail view shows no
    // dropdown. Mirrors launchWrapConv, which is captured as a wrap above.
    _captureRailEl(document.querySelector('.localhost-wrap') || document.getElementById('localhostPill'));
    _captureRailEl(document.getElementById('announceBtnConv'));
    _captureRailEl(document.getElementById('jumpBtnConv'));
    _captureRailEl(document.getElementById('pkoodKillBtn'));
    // Replay's topbar home is otherwise entirely hidden in right-rail mode
    // (the whole #convToolbar is display:none there) — migrate the same
    // button node into the rail instead of leaving it unreachable.
    _captureRailEl(document.getElementById('convReplayBtn'));
    if ($toolbar) {
      _captureRailEl(document.getElementById('convSessionId'));
      _captureRailEl($toolbar.querySelector('.conv-overflow-wrap'));
    }

    // Apply current layout based on body class. Idempotent — safe to
    // call repeatedly. Also called by the toggle click handler.
    function _applyToolbarRailLayout() {
      const inRail = document.body.classList.contains('status-pos-right');
      for (const item of _railSet) {
        const target = inRail ? $railActions : item.origParent;
        if (!target || !item.el) continue;
        if (item.el.parentElement === target) continue;
        if (inRail) {
          target.appendChild(item.el);
        } else {
          // Re-insert at the original position relative to its sibling.
          if (item.origNext && item.origNext.parentElement === target) {
            target.insertBefore(item.el, item.origNext);
          } else {
            target.appendChild(item.el);
          }
        }
      }
    }
    // Expose so the toggle click handler can re-fire it.
    window._cccApplyToolbarRailLayout = _applyToolbarRailLayout;
    _applyToolbarRailLayout();
  });
  }

  function installConversationView19() {
document.addEventListener('click', ev => {
    const ticket = ev.target.closest && ev.target.closest('.conv-worker-history [data-worker-ticket], .conv-avatar-ticket[data-worker-ticket], .conv-pane-ticket[data-worker-ticket]');
    if (!ticket) return;
    ev.stopPropagation();
    ev.preventDefault();
    _uxqOpenItemDetail(ticket.getAttribute('data-worker-ticket'));
  }, true);
  }

  function installConversationView20() {
document.addEventListener('keydown', ev => {
    if ((0, sharedState.isImeKey)(ev)) return;
    if (ev.target.closest && ev.target.closest('.conv-worker-history')) ev.stopPropagation();
  }, true);
  }

  function _rememberRenameOverride(sessionId, displayName, nameOverridden) {
    if (!sessionId) return;
    sharedState._recentRenameOverrides.set(sessionId, {
      display_name: displayName, name_overridden: nameOverridden, at: Date.now(),
    });
  }

  function annCssEscape(value) {
    const s = String(value == null ? '' : value);
    if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(s);
    return s.replace(/[^a-zA-Z0-9_-]/g, '\\$&');
  }

  function annText(value, maxLen) {
    const s = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    if (!maxLen || s.length <= maxLen) return s;
    return s.slice(0, maxLen).trim() + '…';
  }

  function annSelectorFor(el) {
    if (!el || !el.tagName) return '';
    if (el.id) return '#' + annCssEscape(el.id);
    const parts = [];
    let cur = el;
    while (cur && cur.nodeType === 1 && cur !== document.body && cur !== document.documentElement && parts.length < 7) {
      let part = cur.tagName.toLowerCase();
      if (cur.classList && cur.classList.length) {
        part += '.' + Array.from(cur.classList).slice(0, 2).map(annCssEscape).join('.');
      }
      const parent = cur.parentElement;
      if (parent) {
        const sameTag = Array.from(parent.children).filter(ch => ch.tagName === cur.tagName);
        if (sameTag.length > 1) part += ':nth-of-type(' + (sameTag.indexOf(cur) + 1) + ')';
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(' > ') || el.tagName.toLowerCase();
  }

  function annElementText(el) {
    if (!el) return '';
    const tag = (el.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea') {
      return annText(el.value || el.getAttribute('placeholder') || '', 2000);
    }
    if (tag === 'select') {
      return annText(el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex].text : '', 2000);
    }
    return annText(el.innerText || el.textContent || '', 2000);
  }

  function annElementSummary(el) {
    if (!el || !el.tagName) return {};
    // Capture identifying attributes beyond the bare selector so the worker
    // can anchor on a meaningful element even when the dot lands on a generic
    // container (e.g. #convInputBar): classes, aria-label, title, data-* keys.
    const data = {};
    if (el.dataset) {
      let n = 0;
      for (const k in el.dataset) {
        if (n++ >= 8) break;
        data[k] = annText(el.dataset[k], 80);
      }
    }
    return {
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      role: el.getAttribute('role') || '',
      selector: annSelectorFor(el),
      href: el.getAttribute('href') || '',
      classes: el.classList ? Array.from(el.classList).slice(0, 8).join(' ') : '',
      aria: el.getAttribute('aria-label') || '',
      title: el.getAttribute('title') || '',
      data,
      text: annText(annElementText(el), 200),
    };
  }

  function annNormalizeRect(a, b) {
    const left = Math.max(0, Math.min(a.x, b.x));
    const top = Math.max(0, Math.min(a.y, b.y));
    const right = Math.min(window.innerWidth, Math.max(a.x, b.x));
    const bottom = Math.min(window.innerHeight, Math.max(a.y, b.y));
    return { x: left, y: top, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
  }

  function annSelectionRectForElement(el, x, y) {
    if (!el || !el.getBoundingClientRect || el === document.body || el === document.documentElement) {
      return {
        x: Math.max(0, x - 40),
        y: Math.max(0, y - 24),
        width: Math.min(80, window.innerWidth),
        height: Math.min(48, window.innerHeight),
      };
    }
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) {
      return { x: Math.max(0, x - 40), y: Math.max(0, y - 24), width: 80, height: 48 };
    }
    return {
      x: Math.max(0, r.left),
      y: Math.max(0, r.top),
      width: Math.min(r.width, window.innerWidth - Math.max(0, r.left)),
      height: Math.min(r.height, window.innerHeight - Math.max(0, r.top)),
    };
  }

  function annElementAtPoint(x, y) {
    if (!sharedState.annotationState || !sharedState.annotationState.overlay) return document.elementFromPoint(x, y);
    const overlay = sharedState.annotationState.overlay;
    const prev = overlay.style.pointerEvents;
    overlay.style.pointerEvents = 'none';
    let el = null;
    try { el = document.elementFromPoint(x, y); } finally { overlay.style.pointerEvents = prev; }
    return el;
  }

  function annUpdateSelection(rect) {
    if (!sharedState.annotationState || !sharedState.annotationState.selectionEl) return;
    const el = sharedState.annotationState.selectionEl;
    el.style.left = rect.x + 'px';
    el.style.top = rect.y + 'px';
    el.style.width = rect.width + 'px';
    el.style.height = rect.height + 'px';
    el.hidden = false;
  }

  function annUpdateHoverLabel(rect, target) {
    if (!sharedState.annotationState || !sharedState.annotationState.hoverLabelEl) return;
    const labelEl = sharedState.annotationState.hoverLabelEl;
    if (!target || !target.tagName || sharedState.annotationState.dragging || sharedState.annotationState.editor) {
      labelEl.hidden = true;
      return;
    }
    const summary = annElementSummary(target);
    const label = summary.selector || summary.tag || 'selected area';
    labelEl.textContent = label.length > 90 ? label.slice(0, 89) + '…' : label;
    const margin = 8;
    let left = rect.x;
    let top = rect.y - 28;
    if (top < margin) top = rect.y + rect.height + margin;
    const maxLeft = window.innerWidth - 280 - margin;
    if (left > maxLeft) left = maxLeft;
    if (left < margin) left = margin;
    labelEl.style.left = left + 'px';
    labelEl.style.top = top + 'px';
    labelEl.hidden = false;
  }

  function annPreviewAtPoint(x, y) {
    if (!sharedState.annotationState || sharedState.annotationState.dragging || sharedState.annotationState.editor) return;
    const target = annElementAtPoint(x, y);
    const rect = annSelectionRectForElement(target, x, y);
    sharedState.annotationState.hoverElement = target;
    sharedState.annotationState.hoverRect = rect;
    annUpdateSelection(rect);
    annUpdateHoverLabel(rect, target);
  }

  function annContextRect(element, rect) {
    if (!element || !rect) return rect;
    const chosenEl = (0, sharedState.annContextElement)(element, rect);
    if (!chosenEl || !chosenEl.getBoundingClientRect) return rect;
    const chosen = chosenEl.getBoundingClientRect();
    const pad = 8;
    const x = Math.max(0, Math.floor(chosen.left - pad));
    const y = Math.max(0, Math.floor(chosen.top - pad));
    const width = Math.max(rect.width, Math.min(window.innerWidth - x, Math.ceil(chosen.width + pad * 2)));
    const height = Math.max(rect.height, Math.min(window.innerHeight - y, Math.ceil(chosen.height + pad * 2)));
    return { x, y, width, height };
  }

  function annBeginPreEditorScreenshotCapture() {
    if (!sharedState.annotationState || !sharedState.annotationState.rect) return Promise.resolve(null);
    const contextRect = annContextRect(sharedState.annotationState.element, sharedState.annotationState.rect);
    const captureElement = sharedState.annotationState.element;
    const captureRoot = (0, sharedState.annContextElement)(captureElement, sharedState.annotationState.rect) || captureElement;
    return (0, sharedState.annCaptureDomRegionB64)(contextRect, captureElement, captureRoot)
      .catch(() => null)
      .then((b64) => {
        if (sharedState.annotationState) sharedState.annotationState.preEditorScreenshotB64 = b64 || '';
        return b64 || null;
      });
  }

  async function annResolvePreEditorScreenshot() {
    if (sharedState.annotationState && sharedState.annotationState.preEditorScreenshotB64) {
      return sharedState.annotationState.preEditorScreenshotB64;
    }
    if (!sharedState.annotationState || !sharedState.annotationState.preEditorScreenshotPromise) return null;
    try { return await sharedState.annotationState.preEditorScreenshotPromise; }
    catch (_) { return null; }
  }

  function annBuildPayload(note, screenshotB64) {
    const rect = sharedState.annotationState.rect;
    const element = sharedState.annotationState.element;
    // Use the element's tight rect for selector / positioning data, but
    // capture the screenshot over a wider context rect so the reader can
    // see where the element lives in the page.
    const contextRect = annContextRect(element, rect);
    const screen = (0, sharedState.annEstimateScreenRect)(contextRect);
    // Snapshot the session's live process state AT ANNOTATION TIME so
    // liveness bugs ("is this headless? is the terminal alive?") arrive with
    // evidence instead of needing reconstruction later.
    const _ls = (typeof sharedState.liveStatus !== 'undefined' && sharedState.liveStatus) || {};
    const liveStatusSnapshot = {
      source: (typeof sharedState.currentSession !== 'undefined' && sharedState.currentSession && sharedState.currentSession.source) || '',
      live: !!_ls.live,
      headless_present: !!_ls.headlessPresent,
      headless_pid: _ls.headlessPid || null,
      headless_stale: !!_ls.headlessStale,
      terminal_present: !!_ls.terminalPresent,
      bg_present: !!_ls.bgPresent,
      bg_pid: _ls.bgPid || null,
      tty: _ls.tty || null,
    };
    const caps = (sharedState.APP_CONFIG && sharedState.APP_CONFIG.capabilities) || {};
    const canNativeScreenshot = caps.screenshots !== false;
    const payload = {
      note,
      url: window.location.href,
      title: document.title || '',
      session_id: sharedState.annotationState.paneId
        ? (_annPaneSessionId(sharedState.annotationState.paneId) || sharedState.annotationState.paneSessionId || '')
        : ((typeof sharedState.currentSession !== 'undefined' && sharedState.currentSession && sharedState.currentSession.id) || ''),
      repo_path: (typeof sharedState.popoutRepoPath === 'function' && (0, sharedState.popoutRepoPath)()) || '',
      rect,
      viewport_crop: contextRect,
      device_pixel_ratio: screen.device_pixel_ratio || (window.devicePixelRatio || 1),
      screen,
      element: annElementSummary(element),
      live_status: liveStatusSnapshot,
      capture_screen: !screenshotB64 && canNativeScreenshot,
    };
    if (screenshotB64) payload.screenshot_b64 = screenshotB64;
    return payload;
  }

  function annStop() {
    if (!sharedState.annotationState) return;
    if (sharedState.annotationState.overlay) sharedState.annotationState.overlay.remove();
    document.removeEventListener('keydown', annHandleKeydown, true);
    if (sharedState.$annotationStartBtn) sharedState.$annotationStartBtn.classList.remove('active');
    if (sharedState.$annotationFabBtn) sharedState.$annotationFabBtn.classList.remove('active');
    sharedState.annotationState = null;
  }

  function annPositionEditor(editor, rect) {
    const margin = 12;
    const w = Math.min(340, window.innerWidth - margin * 2);
    editor.style.width = w + 'px';
    let left = rect.x + rect.width + margin;
    if (left + w > window.innerWidth - margin) left = Math.min(window.innerWidth - w - margin, rect.x);
    if (left < margin) left = margin;
    let top = rect.y + rect.height + margin;
    const h = editor.offsetHeight || 220;
    if (top + h > window.innerHeight - margin) top = Math.max(margin, rect.y - h - margin);
    // Flipping above the element can still overflow (element near the top,
    // or the editor grew after the screenshot preview loaded): keep the whole
    // editor, including its action buttons, inside the viewport.
    top = Math.max(margin, Math.min(top, window.innerHeight - margin - h));
    editor.style.left = left + 'px';
    editor.style.top = top + 'px';
  }

  async function annRenderPreEditorScreenshotPreview(editor) {
    if (!editor) return;
    const shotPreviewEl = editor.querySelector('.ann-editor-shot-preview');
    const shotPreviewImg = shotPreviewEl && shotPreviewEl.querySelector('img');
    if (!shotPreviewEl || !shotPreviewImg) return;
    const screenshotB64 = await annResolvePreEditorScreenshot();
    if (!sharedState.annotationState || sharedState.annotationState.editor !== editor || !screenshotB64) return;
    shotPreviewImg.src = 'data:image/png;base64,' + screenshotB64;
    shotPreviewEl.hidden = false;
    annPositionEditor(editor, sharedState.annotationState.rect);
  }

  function annOpenNewSessionWithContext(ann, closeFn, errEl) {
    if (!ann) return;
    const text = annContextForClipboard(ann);
    if (!text) return;
    try {
      if (typeof sharedState.enterNewSessionMode !== 'function') throw new Error('new session mode is unavailable');
      (0, sharedState.enterNewSessionMode)(text);
      if (typeof closeFn === 'function') closeFn();
      (0, sharedState.showOpToast)('Annotation loaded into new session', 'success');
    } catch (err) {
      if (errEl) {
        errEl.textContent = 'Open new session failed: ' + ((err && err.message) || 'unknown');
        errEl.hidden = false;
      }
    }
  }

  function annUxFixesQueuePrompt(ann) {
    return 'Fix the following UX issue based on this annotation:\n\n' + annContextForClipboard(ann);
  }

  function annShowUxFixesPreview(ann, onSubmit) {
    const existing = document.getElementById('annUxPreviewModal');
    if (existing) existing.remove();
    const promptText = annUxFixesQueuePrompt(ann);
    const el = (ann && ann.element) || {};
    // Limits annContextForClipboard applies when building the stored text.
    const trunc = [];
    if ((ann.note || '').length > 2000) trunc.push('note → first 2000 chars');
    if ((el.selector || '').length > 200) trunc.push('selector → 200');
    if ((el.text || '').length > 160) trunc.push('element text → 160');
    if ((ann.url || '').length > 240) trunc.push('URL → 240');
    const hasShot = !!(ann && ann.screenshot_path);
    const shotSrc = hasShot ? ('/api/pasted-image?path=' + encodeURIComponent(ann.screenshot_path)) : '';
    const modal = document.createElement('div');
    modal.id = 'annUxPreviewModal';
    modal.className = 'ann-ux-preview-modal';
    modal.innerHTML =
      '<div class="ann-ux-preview-card">' +
        '<div class="ann-ux-preview-title">Review before adding to UX fixes queue</div>' +
        '<div class="ann-ux-preview-shot">' +
          (hasShot
            ? '<span class="ann-ux-ok">✓ Screenshot attached</span>'
              + '<a href="' + shotSrc + '" target="_blank" rel="noopener">'
              + '<img class="ann-ux-preview-thumb" src="' + shotSrc + '" alt="annotation screenshot"></a>'
            : '<span class="ann-ux-warn">⚠ No screenshot. Use the Screen button for a manual region capture, or paste an image into this prompt before submitting.</span>') +
        '</div>' +
        (trunc.length
          ? '<div class="ann-ux-warn">⚠ Stored truncated: ' + (0, sharedState.escapeHtml)(trunc.join(', ')) + '</div>'
          : '') +
        '<div class="ann-ux-preview-label">Exactly what the worker receives - edit before submitting if needed:</div>' +
        '<textarea class="ann-ux-preview-text" rows="14" spellcheck="false"></textarea>' +
        '<div class="ann-ux-preview-actions">' +
          '<button type="button" class="ann-btn" data-ux-copy>Copy</button>' +
          '<button type="button" class="ann-btn" data-ux-cancel>Cancel</button>' +
          '<button type="button" class="ann-btn ann-primary" data-ux-submit>Submit to queue</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
    const textArea = modal.querySelector('.ann-ux-preview-text');
    textArea.value = promptText;
    try { if (typeof sharedState.attachImagePaste === 'function') (0, sharedState.attachImagePaste)(textArea); } catch (_) {}
    const close = () => { modal.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if ((0, sharedState.isImeKey)(e)) return; if (e.key === 'Escape') { e.preventDefault(); close(); } };
    document.addEventListener('keydown', onKey, true);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    modal.querySelector('[data-ux-copy]').addEventListener('click', async () => {
      // Copy the LIVE textarea value — the user may have edited it.
      try { await navigator.clipboard.writeText(textArea.value); (0, sharedState.showOpToast)('Annotation copied', 'success'); }
      catch (_) { try { textArea.select(); document.execCommand('copy'); } catch (e) {} }
    });
    modal.querySelector('[data-ux-cancel]').addEventListener('click', close);
    const submitBtn = modal.querySelector('[data-ux-submit]');
    let submitting = false;
    submitBtn.addEventListener('click', async () => {
      if (submitting) return;
      if (!(0, sharedState.guardComposerSend)(textArea)) return;
      submitting = true;
      submitBtn.disabled = true;
      submitBtn.textContent = 'Submitting…';
      const edited = textArea.value;
      // Hand the (possibly edited) prompt to the submitter so what the
      // user reviewed is exactly what the worker receives.
      const queued = typeof onSubmit === 'function' && await onSubmit(edited);
      if (queued) {
        close();
        return;
      }
      submitting = false;
      submitBtn.disabled = false;
      submitBtn.textContent = 'Submit to queue';
    });
  }

  function annAnnotateTarget() {
    const t = sharedState.APP_CONFIG && sharedState.APP_CONFIG.annotate_target;
    return t === 'github' ? 'github' : 'queue';
  }

  function annScrubLocalPaths(text) {
    return String(text || '').replace(/(?:\/Users|\/home)\/[^\/\s]+\//g, '~/');
  }

  function annGithubIssueTitle(ann) {
    const first = annScrubLocalPaths((ann && ann.note) || '').split('\n')[0].trim();
    return first.length > 80 ? first.slice(0, 79) + '…' : (first || 'Annotation from CCC');
  }

  function annGithubIssueBody(ann, version, hasShot) {
    const el = (ann && ann.element) || {};
    const selector = el.selector || (ann && ann.selector) || '';
    let page = '';
    try { page = new URL((ann && ann.url) || '', window.location.origin).pathname; } catch (_) {}
    const caps = (sharedState.APP_CONFIG && sharedState.APP_CONFIG.capabilities) || {};
    const env = ['CCC ' + (version || 'unknown'), caps.platform || navigator.platform || '',
      (typeof sharedState.isCccMacApp === 'function' && (0, sharedState.isCccMacApp)()) ? 'Mac app' : 'browser']
      .filter(Boolean).join(' · ');
    const pasteKey = /Mac/i.test(navigator.platform || '') ? '⌘V' : 'Ctrl+V';
    const lines = [
      annScrubLocalPaths(((ann && ann.note) || '').slice(0, 2000)),
      '',
      '**Where**',
      page ? '- Page: `' + page + '`' : '',
      selector ? '- Element: `' + String(selector).slice(0, 200).replace(/`/g, "'") + '`' : '',
      '- ' + env,
    ].filter((line, i) => line !== '' || i === 1);
    if (hasShot) {
      lines.push('', '**Screenshot**', '_The screenshot is on your clipboard. Click here and press ' + pasteKey + ' to attach it, then delete this line._');
    }
    return lines.join('\n');
  }

  async function annScreenshotPngBlob(src) {
    const res = await fetch(src, { cache: 'no-store' });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const blob = await res.blob();
    if (blob.type === 'image/png') return blob;
    const bmp = await createImageBitmap(blob);
    const canvas = document.createElement('canvas');
    canvas.width = bmp.width;
    canvas.height = bmp.height;
    canvas.getContext('2d').drawImage(bmp, 0, 0);
    return await new Promise((resolve, reject) => canvas.toBlob(
      b => (b ? resolve(b) : reject(new Error('PNG conversion failed'))), 'image/png'));
  }

  function annOpenExternalUrl(url) {
    if (typeof sharedState.isCccMacApp === 'function' && (0, sharedState.isCccMacApp)()) {
      // The app's web view can't sign in to GitHub; hand off to the default
      // browser, where the user already is.
      return fetch('/api/open-browser', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url }),
      }).then(r => r.json()).then(d => !!(d && d.ok)).catch(() => false);
    }
    const a = document.createElement('a');
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
    return Promise.resolve(true);
  }

  function annShowGithubReport(ann) {
    const existing = document.getElementById('annUxPreviewModal');
    if (existing) existing.remove();
    const hasShot = !!(ann && ann.screenshot_path);
    const shotSrc = hasShot ? ('/api/pasted-image?path=' + encodeURIComponent(ann.screenshot_path)) : '';
    // Fetch the PNG now so the click handler can hand the clipboard a
    // ready blob instead of racing the user gesture against a download.
    const shotBlob = hasShot ? annScreenshotPngBlob(shotSrc) : null;
    if (shotBlob) shotBlob.catch(() => {});
    const modal = document.createElement('div');
    modal.id = 'annUxPreviewModal';
    modal.className = 'ann-ux-preview-modal';
    modal.innerHTML =
      '<div class="ann-ux-preview-card">' +
        '<div class="ann-ux-preview-title">Report this on GitHub</div>' +
        '<div class="ann-ux-preview-label">This opens a new issue on the public CCC repo. Nothing is posted until you press Submit there.</div>' +
        '<div class="ann-ux-preview-shot">' +
          (hasShot
            ? '<span class="ann-ux-ok">✓ Screenshot will be copied to your clipboard</span>'
              + '<img class="ann-ux-preview-thumb" src="' + shotSrc + '" alt="Annotation screenshot">'
            : '<span class="ann-ux-warn">No screenshot captured</span>') +
        '</div>' +
        '<div class="ann-ux-preview-label">Title</div>' +
        '<input type="text" class="ann-gh-title" spellcheck="true">' +
        '<div class="ann-ux-preview-label">Body (edit anything you don\'t want public)</div>' +
        '<textarea class="ann-ux-preview-text" rows="12" spellcheck="true"></textarea>' +
        '<div class="ann-gh-status" hidden></div>' +
        '<div class="ann-ux-preview-actions">' +
          '<button type="button" class="ann-btn" data-gh-cancel>Cancel</button>' +
          '<button type="button" class="ann-btn ann-primary" data-gh-open>' +
            (hasShot ? 'Copy screenshot & open GitHub' : 'Open GitHub') +
          '</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);
    const titleEl = modal.querySelector('.ann-gh-title');
    const bodyEl = modal.querySelector('.ann-ux-preview-text');
    const statusEl = modal.querySelector('.ann-gh-status');
    const openBtn = modal.querySelector('[data-gh-open]');
    titleEl.value = annGithubIssueTitle(ann);
    bodyEl.value = annGithubIssueBody(ann, '', hasShot);
    fetch('/api/version', { cache: 'no-store' }).then(r => r.json()).then(d => {
      // Fill in the version only if the user hasn't started editing.
      if (d && d.version && bodyEl.value === annGithubIssueBody(ann, '', hasShot)) {
        bodyEl.value = annGithubIssueBody(ann, d.version, hasShot);
      }
    }).catch(() => {});
    const close = () => { modal.remove(); document.removeEventListener('keydown', onKey, true); };
    const onKey = (e) => { if ((0, sharedState.isImeKey)(e)) return; if (e.key === 'Escape') { e.preventDefault(); close(); } };
    document.addEventListener('keydown', onKey, true);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    modal.querySelector('[data-gh-cancel]').addEventListener('click', close);
    openBtn.addEventListener('click', async () => {
      openBtn.disabled = true;
      let copied = false;
      if (shotBlob && navigator.clipboard && typeof navigator.clipboard.write === 'function'
          && typeof ClipboardItem === 'function') {
        try {
          // Pass the promise straight in: Safari/WebKit only honours a
          // clipboard write started synchronously inside the click.
          // Bounded: an unfocused or headless page can leave the write
          // pending forever, and that must not block opening the issue.
          await Promise.race([
            navigator.clipboard.write([new ClipboardItem({ 'image/png': shotBlob })]),
            new Promise((_, reject) => setTimeout(() => reject(new Error('clipboard timeout')), 3000)),
          ]);
          copied = true;
        } catch (_) {}
      }
      const params = new URLSearchParams({ title: titleEl.value.trim() || 'Annotation from CCC', body: bodyEl.value });
      const opened = await annOpenExternalUrl(sharedState.ANN_GITHUB_ISSUES_URL + '?' + params.toString());
      if (!opened) {
        statusEl.textContent = 'Could not open the browser. Copy the text above and file it at github.com/amirfish1/claude-command-center/issues.';
        statusEl.hidden = false;
        openBtn.disabled = false;
        return;
      }
      if (!hasShot || copied) {
        close();
        (0, sharedState.showOpToast)(copied
          ? 'Screenshot copied. Paste it into the GitHub issue body.'
          : 'GitHub issue opened', 'success');
        return;
      }
      // Clipboard refused the image: keep the dialog open with a way to get
      // the file into the issue by hand.
      statusEl.innerHTML = 'Your browser blocked copying the screenshot. '
        + '<a href="' + shotSrc + '" download="ccc-annotation.png">Download it</a> '
        + 'and drag the file into the issue.';
      statusEl.hidden = false;
      openBtn.textContent = 'Done';
      openBtn.disabled = false;
      openBtn.onclick = close;
    });
    setTimeout(() => titleEl.focus(), 0);
  }

  async function annOpenUxFixesQueue(ann, closeFn, _errEl, textOverride) {
    if (!ann) return;
    // Require an actionable anchor before sending. A note alone leaves the
    // fix-worker with nothing to locate, so it blocks the ticket and the queue
    // silts up (CCC-396..402). An anchor is at least one of: a CSS selector, a
    // screenshot path, or a URL. Give instant feedback instead of a silent
    // junk ticket; the server enforces the same rule authoritatively.
    const _annSelector = (ann.element && ann.element.selector) || ann.selector || '';
    const _hasAnchor = !!(
      String(_annSelector || '').trim()
      || String(ann.screenshot_path || '').trim()
      || String(ann.url || '').trim()
    );
    if (!_hasAnchor) {
      if (typeof sharedState.showOpToast === 'function') {
        (0, sharedState.showOpToast)('Annotation needs an element, screenshot, or URL before it can go to the fix queue', 'warn');
      }
      return;
    }
    // Close the editor up front — the user clicked the button and
    // doesn't need to stare at the modal while the fetch is in flight.
    // The annotation is already persisted (persistAnnotation runs
    // before we get here) so closing now doesn't lose it. Feedback —
    // success OR failure — arrives via showOpToast a moment later.
    if (typeof closeFn === 'function') {
      try { closeFn(); } catch (_) {}
    }
    if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Sending annotation to UX fixes queue…', 'info');
    try {
      const res = await fetch('/api/annotations/ux-fixes-queue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          annotation_id: ann.id || '',
          text: (typeof textOverride === 'string' && textOverride.trim())
            ? textOverride
            : annUxFixesQueuePrompt(ann),
          note: ann.note || '',
          url: ann.url || '',
          title: ann.title || '',
          selector: (ann.element && ann.element.selector) || ann.selector || '',
          screenshot_path: ann.screenshot_path || '',
          source: 'ccc',
          engine: (0, sharedState.getSpawnEngine)(),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
      // Optimistically mark the receiving UX-fixes-queue session as
      // sending so its row shows "Sending…" / WIP immediately, just like
      // a user-typed message would. Without this, the inject lands in
      // the target session's terminal but the conv list shows no
      // activity signal until the next sidecar tick — reads as "did
      // anything actually happen?"
      const _targetSid = data.session_id
        || (data.inject && data.inject.session_id)
        || '';
      if (_targetSid && typeof sharedState.markSessionSending === 'function') {
        (0, sharedState.markSessionSending)(_targetSid);
      }
      if (typeof sharedState.refreshUxFixesQueueMeta === 'function') {
        (0, sharedState.refreshUxFixesQueueMeta)({ force: true }).catch(() => {});
      }
      (0, sharedState.showOpToast)(
        data.number
          ? ('Queued as UX fix #' + data.number)
          : (data.action === 'spawned' ? 'UX fixes queue session created' : 'Annotation sent to UX fixes queue'),
        'success'
      );
      // When a NEW session was spawned, force-pull the sessions list so
      // the new row appears in the conv sidebar immediately — don't wait
      // for the next poll cycle. Also schedule a few tighter polls so a
      // session created just after the immediate refresh window still
      // surfaces fast.
      if (data.action === 'spawned' && typeof hostServices.refreshConversationList === 'function') {
        try { (0, hostServices.refreshConversationList)(); } catch (_) {}
        // This path stages no pending placeholder, so there is nothing for
        // chasePendingSpawn to track and it keeps the plain refresh burst.
        setTimeout(hostServices.refreshConversationList, 600);
        setTimeout(hostServices.refreshConversationList, 1500);
        setTimeout(hostServices.refreshConversationList, 3000);
      }
      try {
        if (typeof sharedState.refreshArchiveData === 'function') {
          await (0, sharedState.refreshArchiveData)({ force: true });
          const $search = document.getElementById('convSearch');
          if (typeof hostServices.renderArchiveList === 'function') (0, hostServices.renderArchiveList)($search ? $search.value : '');
        }
      } catch (_) {}
      return true;
    } catch (err) {
      // Modal is already closed; the toast is the only feedback channel
      // left. Use it for both transports (in-page editor + screen-cap).
      (0, sharedState.showOpToast)('UX fixes queue failed: ' + ((err && err.message) || 'unknown'), 'error');
      return false;
    }
  }

  function annShowEditor() {
    if (!sharedState.annotationState || !sharedState.annotationState.overlay) return;
    if (sharedState.annotationState.editor) sharedState.annotationState.editor.remove();
    const summary = annElementSummary(sharedState.annotationState.element);
    const editor = document.createElement('div');
    editor.className = 'ann-editor';
    editor.innerHTML =
      '<div class="ann-editor-title">Annotation</div>' +
      '<textarea class="ann-editor-note" rows="4" placeholder="Write a note for Claude…"></textarea>' +
      '<div class="ann-editor-meta">' + (0, sharedState.escapeHtml)(summary.selector || summary.tag || 'selected area') + '</div>' +
      '<div class="ann-editor-shot-preview" hidden><img alt="Annotation screenshot preview"></div>' +
      '<div class="ann-editor-error" hidden></div>' +
      '<div class="ann-editor-actions">' +
        '<button type="button" class="ann-btn" data-ann-cancel>Cancel</button>' +
        '<button type="button" class="ann-btn" data-ann-open-session>Open new session</button>' +
        // CCC-179: split the single "Add to UX fixes queue" into two — a 1-click
        // "Queue" that enqueues the note as-is, and "Queue + edit" that opens the
        // pre-submit preview to tweak the prompt first (the prior flow).
        (annAnnotateTarget() === 'github'
          ? '<button type="button" class="ann-btn ann-primary" data-ann-github title="Open a pre-filled issue on the CCC GitHub repo, with the screenshot on your clipboard">Report on GitHub</button>'
          : '<button type="button" class="ann-btn ann-queue-btn" data-ann-ux-queue title="Edit the prompt before adding to the UX-fixes queue">Queue + edit</button>' +
            '<button type="button" class="ann-btn ann-primary ann-queue-btn" data-ann-ux-queue-now title="Add this annotation to the UX-fixes queue immediately, no edit">Queue</button>') +
        // CCC-171: the standalone "Save" button was redundant — both action
        // buttons above already persist the annotation first (persistAnnotation),
        // and ⌘/Ctrl+Enter still saves a note-only annotation for agent context.
      '</div>';
    sharedState.annotationState.overlay.appendChild(editor);
    sharedState.annotationState.editor = editor;
    annPositionEditor(editor, sharedState.annotationState.rect);
    // Content grows after first paint (screenshot preview, hints, errors, the
    // resizable note): re-clamp so the action buttons never fall off-screen.
    if (typeof ResizeObserver === 'function') {
      const ro = new ResizeObserver(() => {
        if (!editor.isConnected) { ro.disconnect(); return; }
        if (sharedState.annotationState && sharedState.annotationState.editor === editor) annPositionEditor(editor, sharedState.annotationState.rect);
      });
      ro.observe(editor);
    }
    annRenderPreEditorScreenshotPreview(editor);
    const noteEl = editor.querySelector('.ann-editor-note');
    // Cmd-V image paste in the note (CCC-89): same uploader as the composer
    // — pasted screenshots land as a path token the worker can open.
    try { if (typeof sharedState.attachImagePaste === 'function') (0, sharedState.attachImagePaste)(noteEl); } catch (_) {}
    const errEl = editor.querySelector('.ann-editor-error');
    // CCC-171: the visible Save button was removed (redundant — the two action
    // buttons persist, and ⌘/Ctrl+Enter saves a note-only annotation). Fall
    // back to a detached <button> so the shared persist flow's busy/label
    // writes and the click wiring below are harmless no-ops when it's absent.
    const saveBtn = editor.querySelector('[data-ann-save]') || document.createElement('button');
    const cancelBtn = editor.querySelector('[data-ann-cancel]');
    const openSessionBtn = editor.querySelector('[data-ann-open-session]');
    const uxQueueBtn = editor.querySelector('[data-ann-ux-queue]');
    const uxQueueNowBtn = editor.querySelector('[data-ann-ux-queue-now]');
    let savedAnnotation = null;
    const copySaved = async () => {
      if (!savedAnnotation) return;
      try {
        await navigator.clipboard.writeText(annContextForClipboard(savedAnnotation));
        annStop();
      } catch (_) {
        errEl.textContent = 'Copy failed.';
        errEl.hidden = false;
      }
    };
    const persistAnnotation = async (busyLabel) => {
      if (savedAnnotation) return savedAnnotation;
      if (!(0, sharedState.guardComposerSend)(noteEl)) return null;
      const note = noteEl.value.trim();
      if (!note) {
        errEl.textContent = 'Note is required.';
        errEl.hidden = false;
        noteEl.focus();
        return null;
      }
      errEl.hidden = true;
      if (saveBtn) saveBtn.disabled = true;
      if (openSessionBtn) openSessionBtn.disabled = true;
      if (uxQueueBtn) uxQueueBtn.disabled = true;
      if (saveBtn) saveBtn.textContent = busyLabel || 'Saving…';
      const screenshotB64 = await annResolvePreEditorScreenshot();
      const payload = annBuildPayload(note, screenshotB64);
      try {
        const res = await fetch('/api/annotations', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
        // Merge the payload we built (enriched element, live_status) under the
        // server's saved record (id, screenshot_path) so downstream text —
        // preview, queue prompt, copy, new-session — carries the full context
        // even if the server doesn't echo every custom field. Drop the heavy
        // base64 so it doesn't linger on the in-memory annotation.
        savedAnnotation = Object.assign({}, payload, data.annotation || {});
        savedAnnotation.screenshot_warning = (data && data.screenshot_warning) || '';
        delete savedAnnotation.screenshot_b64;
        noteEl.disabled = true;
        if (cancelBtn) cancelBtn.hidden = true;
        if (openSessionBtn) openSessionBtn.disabled = false;
        if (uxQueueBtn) uxQueueBtn.disabled = false;
        if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Copy'; }
        // A missing screenshot is not an error: the note is saved, and the
        // capture warning stays on savedAnnotation.screenshot_warning. The
        // old red permissions toast fired on every annotate and was noise.
        (0, sharedState.showOpToast)(savedAnnotation && savedAnnotation.screenshot_path
          ? 'Annotation saved with screenshot' : 'Annotation saved', 'success');
        return savedAnnotation;
      } catch (err) {
        errEl.textContent = 'Save failed: ' + ((err && err.message) || 'unknown');
        errEl.hidden = false;
        saveBtn.disabled = false;
        if (openSessionBtn) openSessionBtn.disabled = false;
        if (uxQueueBtn) uxQueueBtn.disabled = false;
        saveBtn.textContent = 'Save';
        return null;
      }
    };
    const save = async () => {
      if (savedAnnotation) {
        await copySaved();
        return;
      }
      await persistAnnotation('Saving…');
    };
    if (cancelBtn) cancelBtn.addEventListener('click', annStop);
    if (openSessionBtn) {
      openSessionBtn.addEventListener('click', async () => {
        const ann = await persistAnnotation('Saving…');
        if (ann) annOpenNewSessionWithContext(ann, annStop, errEl);
      });
    }
    if (uxQueueBtn) {
      uxQueueBtn.addEventListener('click', async () => {
        const ann = await persistAnnotation('Saving…');
        if (!ann) return;
        // "Queue + edit": show a pre-submit preview (stored text + screenshot +
        // truncation + copy) so the user can tweak the prompt. The annotation is
        // already persisted; the overlay can close, the preview is its own modal.
        annStop();
        annShowUxFixesPreview(ann, (editedText) => annOpenUxFixesQueue(ann, null, errEl, editedText));
      });
    }
    const githubBtn = editor.querySelector('[data-ann-github]');
    if (githubBtn) {
      githubBtn.addEventListener('click', async () => {
        githubBtn.disabled = true;
        githubBtn.textContent = 'Saving…';
        const ann = await persistAnnotation('Saving…');
        if (!ann) {
          githubBtn.disabled = false;
          githubBtn.textContent = 'Report on GitHub';
          return;
        }
        annStop();
        annShowGithubReport(ann);
      });
    }
    if (uxQueueNowBtn) {
      uxQueueNowBtn.addEventListener('click', async () => {
        // Persisting can include a screenshot upload. A Queue click must
        // acknowledge immediately and become single-shot before that await,
        // otherwise the unchanged button invites a confusing second click.
        uxQueueNowBtn.disabled = true;
        uxQueueNowBtn.textContent = 'Saving…';
        const ann = await persistAnnotation('Saving…');
        if (!ann) {
          uxQueueNowBtn.disabled = false;
          uxQueueNowBtn.textContent = 'Queue';
          return;
        }
        // "Queue" (1-click, CCC-179): enqueue immediately with the default
        // prompt — no preview. annOpenUxFixesQueue closes the editor itself.
        annOpenUxFixesQueue(ann, annStop, errEl, null);
      });
    }
    saveBtn.addEventListener('click', save);
    noteEl.addEventListener('keydown', (e) => {
      if ((0, sharedState.isImeKey)(e)) return;
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') save();
      e.stopPropagation();
    });
    setTimeout(() => noteEl.focus(), 0);
  }

  function annPointerDown(e) {
    if (!sharedState.annotationState || e.target.closest('.ann-editor') || e.target.closest('.ann-hud')) return;
    e.preventDefault();
    sharedState.annotationState.dragging = true;
    sharedState.annotationState.start = { x: e.clientX, y: e.clientY };
    sharedState.annotationState.rect = { x: e.clientX, y: e.clientY, width: 1, height: 1 };
    if (sharedState.annotationState.hoverLabelEl) sharedState.annotationState.hoverLabelEl.hidden = true;
    if (sharedState.annotationState.editor) { sharedState.annotationState.editor.remove(); sharedState.annotationState.editor = null; }
    annUpdateSelection(sharedState.annotationState.rect);
    try { sharedState.annotationState.overlay.setPointerCapture(e.pointerId); } catch (_) {}
  }

  function annPointerMove(e) {
    if (!sharedState.annotationState) return;
    if (!sharedState.annotationState.dragging) {
      if (e.target.closest('.ann-editor') || e.target.closest('.ann-hud')) return;
      annPreviewAtPoint(e.clientX, e.clientY);
      return;
    }
    e.preventDefault();
    const rect = annNormalizeRect(sharedState.annotationState.start, { x: e.clientX, y: e.clientY });
    sharedState.annotationState.rect = rect;
    annUpdateSelection(rect);
  }

  async function annPointerUp(e) {
    if (!sharedState.annotationState || !sharedState.annotationState.dragging) return;
    e.preventDefault();
    sharedState.annotationState.dragging = false;
    const raw = annNormalizeRect(sharedState.annotationState.start, { x: e.clientX, y: e.clientY });
    const clicked = raw.width < 8 && raw.height < 8;
    const target = annElementAtPoint(e.clientX, e.clientY);
    sharedState.annotationState.element = target;
    sharedState.annotationState.rect = clicked ? annSelectionRectForElement(target, e.clientX, e.clientY) : raw;
    annUpdateSelection(sharedState.annotationState.rect);
    if (sharedState.annotationState.hoverLabelEl) sharedState.annotationState.hoverLabelEl.hidden = true;
    sharedState.annotationState.preEditorScreenshotPromise = annBeginPreEditorScreenshotCapture();
    await sharedState.annotationState.preEditorScreenshotPromise;
    if (!sharedState.annotationState) return;
    annShowEditor();
  }

  function annHandleKeydown(e) {
    if ((0, sharedState.isImeKey)(e)) return;
    if (e.key === 'Escape' && sharedState.annotationState) {
      e.preventDefault();
      annStop();
    }
  }

  function _annPaneSessionId(paneId) {
    if (!paneId) return '';
    const pane = typeof sharedState.paneByPaneId === 'function' ? (0, sharedState.paneByPaneId)(paneId) : null;
    const convId = (pane && pane.conversationId) || '';
    if (!convId) return '';
    return String((typeof sharedState.sessionIdByConv !== 'undefined' && sharedState.sessionIdByConv[convId]) || convId);
  }

  function annStart(paneId) {
    if (sharedState.annotationState) { annStop(); return; }
    const overlay = document.createElement('div');
    overlay.className = 'ann-overlay';
    overlay.innerHTML =
      '<div class="ann-hud"><span>Mark area</span><button type="button" class="ann-hud-close" aria-label="Close annotation mode">&times;</button></div>' +
      '<div class="ann-selection" hidden></div>' +
      '<div class="ann-hover-label" hidden></div>';
    document.body.appendChild(overlay);
    sharedState.annotationState = {
      overlay,
      selectionEl: overlay.querySelector('.ann-selection'),
      hoverLabelEl: overlay.querySelector('.ann-hover-label'),
      editor: null,
      dragging: false,
      start: null,
      rect: null,
      element: null,
      hoverElement: null,
      hoverRect: null,
      preEditorScreenshotPromise: null,
      paneId: paneId || null,
      paneSessionId: _annPaneSessionId(paneId),
    };
    overlay.addEventListener('pointerdown', annPointerDown);
    overlay.addEventListener('pointermove', annPointerMove);
    overlay.addEventListener('pointerup', annPointerUp);
    overlay.querySelector('.ann-hud-close').addEventListener('click', annStop);
    document.addEventListener('keydown', annHandleKeydown, true);
    if (sharedState.$annotationStartBtn) sharedState.$annotationStartBtn.classList.add('active');
    if (sharedState.$annotationFabBtn) sharedState.$annotationFabBtn.classList.add('active');
  }

  function annClipText(value, maxLen) {
    const s = String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
    if (!s || !maxLen || s.length <= maxLen) return s;
    return s.slice(0, maxLen).trim() + '…';
  }

  function annContextForClipboard(ann) {
    const element = ann.element || {};
    const lines = ['Annotation: ' + annClipText(ann.note || '', 2000)];
    const anchors = [];
    if (ann.url) anchors.push('URL: ' + annClipText(ann.url, 240));
    if (ann.session_id) anchors.push('Session ID: ' + ann.session_id);
    if (element.selector) anchors.push('Selector: ' + annClipText(element.selector, 200));
    if (element.text) anchors.push('Element: ' + annClipText(element.text, 160));
    if (element.classes) anchors.push('Classes: ' + annClipText(element.classes, 160));
    if (element.aria) anchors.push('Aria-label: ' + annClipText(element.aria, 120));
    if (element.title) anchors.push('Title: ' + annClipText(element.title, 120));
    if (element.data && Object.keys(element.data).length) {
      const dataStr = Object.keys(element.data).map(k => k + '=' + element.data[k]).join(', ');
      anchors.push('Data-attrs: ' + annClipText(dataStr, 200));
    }
    const ls = ann.live_status;
    if (ls && (ls.source || ls.headless_present || ls.terminal_present || ls.live)) {
      const bits = [];
      if (ls.source) bits.push(ls.source);
      bits.push(ls.live ? 'live' : 'not-live');
      if (ls.headless_present) bits.push('headless' + (ls.headless_pid ? ' pid ' + ls.headless_pid : '') + (ls.headless_stale ? ' (STALE)' : ''));
      if (ls.terminal_present) bits.push('terminal' + (ls.tty ? ' ' + ls.tty : ''));
      if (ls.bg_present) bits.push('Claude-app terminal' + (ls.bg_pid ? ' pid ' + ls.bg_pid : ''));
      if (!ls.headless_present && !ls.terminal_present && !ls.bg_present) bits.push('no headless/terminal process');
      anchors.push('Session state: ' + bits.join(' · '));
    }
    if (ann.screenshot_path) anchors.push('Screenshot: ' + ann.screenshot_path);
    else if (ann.screenshot_warning) anchors.push('Screenshot warning: ' + annClipText(ann.screenshot_warning, 240));
    if (anchors.length) {
      lines.push('', 'Anchors:', anchors.map(v => '- ' + v).join('\n'));
    }
    return lines.join('\n');
  }

  function installConversationView21() {
if (sharedState.$statusRailAnnotateBtn) sharedState.$statusRailAnnotateBtn.addEventListener('click', annStart);
  }

  function installConversationView22() {
if (sharedState.$statusRailTitleRenameBtn) sharedState.$statusRailTitleRenameBtn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    startStatusRailTitleRename();
  });
  }

  function clearPaneScreenForVideo(paneId) {
    const $view = (0, sharedState.getConvViewForPane)(paneId) || sharedState.$conversationsView;
    if (!$view) return;
    let maxLine = parseInt($view.dataset.videoClearLine || '0', 10) || 0;
    $view.querySelectorAll('.event[data-jsonl-line]').forEach((el) => {
      const n = parseInt(el.dataset.jsonlLine, 10);
      if (!isNaN(n) && n > maxLine) maxLine = n;
    });
    $view.dataset.videoClearLine = String(maxLine);
    // .tool-call-group too: tool events nest inside group wrappers whose
    // "Ran N commands" header is not itself an .event and would stay visible.
    $view.querySelectorAll('.event, .stream-bubble, .conv-load-earlier, .tool-call-group')
      .forEach((el) => el.classList.add('ccc-video-cleared'));
    $view.classList.add('is-video-cleared');
  }

  function installConversationView23() {
document.addEventListener('click', (ev) => {
    const btn = ev.target && ev.target.closest
      ? ev.target.closest('[data-role="pane-annotate"], [data-role="pane-clear"], [data-role="pane-replay"]') : null;
    if (!btn) return;
    ev.stopPropagation();
    const menu = btn.closest('.conv-pane-more[open]');
    if (menu) menu.removeAttribute('open');
    const pane = btn.closest('.conv-pane[data-pane-id]');
    const paneId = (pane && pane.dataset.paneId) || (0, sharedState.activePaneId)();
    if (btn.dataset.role === 'pane-annotate') { annStart(paneId); return; }
    if (btn.dataset.role === 'pane-replay') { (0, sharedState.startConvReplay)(paneId); return; }
    clearPaneScreenForVideo(paneId);
  });
  }

  function _ccScheduleRuns() {
    if (sharedState._ccRunsRaf) return;
    sharedState._ccRunsRaf = requestAnimationFrame(() => {
      sharedState._ccRunsRaf = 0;
      document.querySelectorAll('.conversations-view').forEach(sharedState._ccMarkRuns);
    });
  }

  function _ccSyncRunsObserver(on) {
    sharedState._ccRunsObserver.disconnect();
    if (on) {
      sharedState._ccRunsObserver.observe(document.body, { childList: true, subtree: true });
      _ccScheduleRuns();
    }
  }

  function installConversationView24() {
document.addEventListener('click', (ev) => {
    if (!document.body.classList.contains('conv-compact')) return;
    const t = ev.target;
    if (!t || !t.closest) return;
    const chip = t.closest('.cc-run-chip');
    const head = chip ? null : t.closest('.cc-run-head');
    if (!chip && !head) return;
    if (head && head.classList.contains('tool-call-group')
        && !t.closest('.tool-call-group-header, .cc-run-preview')) return;
    if (head?.classList.contains('tool-call-group') && head.classList.contains('cc-run-open')
        && !t.closest('.cc-run-fold, .cc-run-tools')) return;
    // Once revealed, let the tool/think controls handle their own clicks.
    // A chip or the standalone head's fold label still closes the run.
    if (head?.matches('.kimi-tool, .kimi-tool-group, .kimi-thinking')
        && head.classList.contains('cc-run-open') && t !== head
        && !t.closest('.cc-run-fold, .cc-run-tools')) return;
    const view = (chip || head).closest('.conversations-view');
    if (!view) return;
    ev.stopImmediatePropagation();
    const id = chip ? chip.dataset.ccChipFor : head.dataset.ccRun;
    const tools = t.closest('.cc-run-tools');
    const open = tools ? true : !(chip || head).classList.contains('cc-run-open');
    const hasThoughts = !!view.querySelector('[data-cc-run="' + id + '"].cc-run-thought');
    const thinking = tools ? !head.classList.contains('cc-run-thinking') : open && hasThoughts;
    view.querySelectorAll('[data-cc-run="' + id + '"], .cc-run-chip[data-cc-chip-for="' + id + '"]')
      .forEach(el => {
        el.classList.toggle('cc-run-open', open);
        el.classList.toggle('cc-run-thinking', thinking);
        if (open && !thinking && el.matches('.tool-call-group, .kimi-tool-group')) {
          // Revealing tools should reveal their rows in the same action.
          el.classList.remove('collapsed');
          el.querySelector(':scope > .kimi-tool-group-head')?.setAttribute('aria-expanded', 'true');
        }
      });
    (0, sharedState._ccMarkRuns)(view);
  }, true);
  }

  function _syncConvViewSegs(mode) {
    // The highlight is driven by body[data-conv-view] in CSS, so panes
    // cloned later render the right segment without a re-sync.
    document.body.dataset.convView = mode;
    document.body.classList.toggle('conv-compact', mode === 'compact');
    _ccSyncRunsObserver(mode === 'compact');
    document.querySelectorAll('.conv-view-seg [data-conv-view]').forEach((b) => {
      b.setAttribute('aria-checked', b.dataset.convView === mode ? 'true' : 'false');
    });
  }

  function setConvViewMode(mode) {
    if (!sharedState.CONV_VIEW_MODES.includes(mode) || mode === (0, sharedState.convViewMode)()) return;
    const on = mode === 'verbose';
    try {
      localStorage.setItem('ccc-conv-view', mode);
      localStorage.setItem('ccc-conv-verbose', on ? '1' : '0');
    } catch (_) {}
    // Keep the reader's place: remember the top visible message in each
    // transcript and put it back at the same offset after the reflow.
    const anchors = [];
    document.querySelectorAll('.conversations-view').forEach((view) => {
      if (!view.scrollTop) return;
      // Pinned to the bottom stays pinned, whatever the new height is.
      if (view.scrollHeight - view.scrollTop - view.clientHeight < 8) { anchors.push({ view, bottom: true }); return; }
      const vr = view.getBoundingClientRect();
      const el = Array.from(view.querySelectorAll('.event[data-jsonl-line]'))
        .find(n => n.getBoundingClientRect().bottom > vr.top + 1);
      if (el) anchors.push({ view, line: Number(el.dataset.jsonlLine), off: el.getBoundingClientRect().top - vr.top });
    });
    // Mode switches re-render the transcript, so anchor by jsonl line, not
    // node. If the anchor message is folded away (thinking, tool runs), use
    // the nearest line that is still rendered.
    const restoreAnchors = () => anchors.forEach(({ view, line, off }) => {
      if (!line && anchors.find(a => a.view === view && a.bottom)) { view.scrollTop = view.scrollHeight; return; }
      let best = null, bestD = Infinity, exact = null;
      view.querySelectorAll('.event[data-jsonl-line]').forEach((n) => {
        const h = n.getBoundingClientRect().height;
        const d = Math.abs(Number(n.dataset.jsonlLine) - line);
        if (d === 0) exact = n;
        if (d < bestD && h >= 20) { best = n; bestD = d; }
      });
      // Exact message still takes real space: keep it where it was. If it
      // folded to nothing, put the nearest substantive message at the top.
      const useExact = exact && exact.getBoundingClientRect().height >= 20;
      const el = useExact ? exact : best;
      if (el) view.scrollTop += el.getBoundingClientRect().top - view.getBoundingClientRect().top - (useExact ? off : 0);
    });
    _syncConvViewSegs(mode);
    applyConvVerbose(on);
    restoreAnchors();
    requestAnimationFrame(() => { restoreAnchors(); setTimeout(restoreAnchors, 150); });
    document.querySelectorAll('.conv-pane[data-pane-id]').forEach(pane => {
      if ((0, sharedState.normalizePresentationMode)(pane.dataset.presentationMode) !== 'off') {
        (0, sharedState.refreshPresentationForPane)(pane.dataset.paneId, { preserveCursor: true });
      }
    });
  }

  function installConversationView25() {
_syncConvViewSegs((0, sharedState.convViewMode)());
  }

  function installConversationView26() {
document.addEventListener('click', (ev) => {
    if (!document.body.classList.contains('conv-compact')) return;
    const t = ev.target;
    if (!t || !t.closest || !t.closest('.conversations-view')) return;
    if (t.closest('a, button, summary, input, textarea, select, .tool-call-group-header')) return;
    const sel = window.getSelection && window.getSelection();
    if (sel && String(sel).trim()) return;
    const el = t.closest('.assistant-text, .event.user_text, .thinking-block, .tool-call');
    // Latest-thought slots fold and unfold through their own handler.
    if (el && !el.classList.contains('thinking-run')) el.classList.toggle('cc-open');
  }, true);
  }

  function installConversationView27() {
document.addEventListener('click', (ev) => {
    const b = ev.target && ev.target.closest ? ev.target.closest('.conv-view-seg [data-conv-view]') : null;
    if (!b) return;
    ev.stopPropagation();
    setConvViewMode(b.dataset.convView);
  });
  }

  function isSettingsFormField(el) {
    return !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA'
      || el.tagName === 'SELECT' || el.isContentEditable);
  }

  function openCmdk() {
    if (!sharedState.$cmdkModal) return;
    sharedState.$cmdkModal.classList.add('open');
    sharedState.$cmdkInput.value = '';
    renderCmdkList('');
    setTimeout(() => sharedState.$cmdkInput.focus(), 20);
  }

  function closeCmdk() {
    if (!sharedState.$cmdkModal) return;
    sharedState.$cmdkModal.classList.remove('open');
  }

  function _cmdkLabel(c) {
    if (!c) return '';
    if (typeof sharedState.stripTitle === 'function' && c.display_name) return (0, sharedState.stripTitle)(c.display_name) || c.display_name;
    return c.display_name || c.id || '';
  }

  function renderCmdkList(query) {
    if (!sharedState.$cmdkList) return;
    const q = (query || '').toLowerCase().trim();
    // Pull from the same in-memory store the sidebar uses; sort by mtime
    // (recency) and exclude archived / pending placeholders for clarity.
    const data = (Array.isArray(sharedState.conversationsData) ? sharedState.conversationsData : [])
      .filter(c => c && !c.archived && !c.pending);
    const matches = data.filter(c => {
      if (!q) return true;
      const name = (c.display_name || '').toLowerCase();
      const first = (c.first_message || '').toLowerCase();
      const branch = (c.branch || '').toLowerCase();
      return name.includes(q) || first.includes(q) || branch.includes(q);
    });
    matches.sort((a, b) => (b.modified || 0) - (a.modified || 0));
    sharedState._cmdkItems = matches.slice(0, 100);
    sharedState._cmdkIndex = 0;
    if (!sharedState._cmdkItems.length) {
      sharedState.$cmdkList.innerHTML = '<div class="cmdk-empty">' + (q ? 'No sessions match.' : 'No sessions yet.') + '</div>';
      return;
    }
    const rows = sharedState._cmdkItems.map((c, i) => {
      const title = _cmdkLabel(c) || '(untitled)';
      const meta = [c.branch, c.source, c.first_message].filter(Boolean)
        .map(s => String(s).slice(0, 80)).join(' · ');
      return '<div class="cmdk-item' + (i === 0 ? ' selected' : '') + '" role="option" data-idx="' + i + '">'
        + '<div class="cmdk-title">' + (0, sharedState.escapeHtml)(title) + '</div>'
        + (meta ? '<div class="cmdk-meta">' + (0, sharedState.escapeHtml)(meta) + '</div>' : '')
        + '</div>';
    }).join('');
    sharedState.$cmdkList.innerHTML = rows;
  }

  function installConversationView28() {
document.addEventListener('keydown', (e) => {
    if ((0, sharedState.isImeKey)(e)) return;
    const meta = e.metaKey || e.ctrlKey;
    if (meta && (e.key === 'k' || e.key === 'K' || e.key === 'p' || e.key === 'P')) {
      // Don't hijack the browser address bar combo when not focused on us.
      e.preventDefault();
      if (sharedState.$cmdkModal && sharedState.$cmdkModal.classList.contains('open')) closeCmdk();
      else openCmdk();
      return;
    }
    if (meta && e.key === '\\') {
      e.preventDefault();
      // Reuse cpCloseBtn handler (toggles when in kanban-split mode); fall
      // back to direct setConvPanelOpen for safety.
      if (typeof setConvPanelOpen === 'function') setConvPanelOpen(!sharedState.convPanelOpen);
    }
    if (meta && (e.key === 'f' || e.key === 'F')) {
      e.preventDefault();
      e.stopPropagation();
      const modal = document.getElementById('chatFindModal');
      const input = document.getElementById('chatFindInput');
      if (modal && input) {
        modal.style.display = 'flex';
        input.focus();
        input.select();
        requestAnimationFrame(() => (0, sharedState.refreshChatFind)({ rebuild: true }));
      }
      return;
    }
    if (meta && e.key === ',') {
      // Cmd/Ctrl+, opens Settings — but not while the user is typing
      // somewhere else (a text field, the conversation composer, etc).
      const ae = document.activeElement;
      if (isSettingsFormField(ae)) return;
      e.preventDefault();
      if (typeof hostServices.openSettingsModal === 'function') (0, hostServices.openSettingsModal)();
      return;
    }
  });
  }

  function installConversationView29() {
if (sharedState.$chatFindInput) {
    const $chatFindModal = document.getElementById('chatFindModal');
    let _chatFindRanges = [];
    let _chatFindMatchIndex = 0;
    let _chatFindLastBuiltQuery = '';
    let _chatFindInputTimer = null;
    let _chatFindBuildGeneration = 0;
    let _chatFindRoot = null;
    const CHAT_FIND_FULL_LOAD_MAX_LINES = 2000;

    function chatFindGetRoot() {
      try {
        return (typeof sharedState.getConvView === 'function' ? (0, sharedState.getConvView)() : null)
          || document.getElementById('conversationsView');
      } catch (_) {
        return document.getElementById('conversationsView');
      }
    }

    // "4/22" current-match counter in the find bar. Empty when no query;
    // "0/0" when the query has no matches.
    function chatFindUpdateCount() {
      const el = document.getElementById('chatFindCount');
      if (!el) return;
      if (!sharedState.$chatFindInput.value) { el.textContent = ''; return; }
      el.textContent = _chatFindRanges.length
        ? (_chatFindMatchIndex + 1) + '/' + _chatFindRanges.length
        : '0/0';
    }

    function chatFindClear() {
      _chatFindRanges = [];
      _chatFindMatchIndex = 0;
      _chatFindLastBuiltQuery = '';
      chatFindUpdateCount();
      try {
        if (window.CSS && CSS.highlights) {
          if (CSS.highlights.has('chat-find')) CSS.highlights.delete('chat-find');
          if (CSS.highlights.has('chat-find-active')) CSS.highlights.delete('chat-find-active');
        }
      } catch (_) {}
    }

    function chatFindCollectRanges(root, query) {
      _chatFindRoot = root;
      chatFindWatch(root);
      const ranges = [];
      if (!root || !query) return ranges;
      const needle = query.toLowerCase();
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let node;
      while ((node = walker.nextNode())) {
        const parent = node.parentElement;
        if (!parent) continue;
        if (parent.closest(
          '#chatFindModal, script, style, textarea, input, select, button:not(.qtag-chip), .conv-input-bar,'
          + ' [hidden], [aria-hidden="true"]'
        )) continue;
        const hay = node.nodeValue || '';
        if (!hay) continue;
        const hayLower = hay.toLowerCase();
        let from = 0;
        for (;;) {
          const at = hayLower.indexOf(needle, from);
          if (at < 0) break;
          try {
            const r = document.createRange();
            r.setStart(node, at);
            r.setEnd(node, at + query.length);
            // The transcript keeps collapsed tool details and replay-future
            // messages in the DOM. Those text nodes used to count as hits even
            // though stepping to them could not show the word on screen.
            if (r.getClientRects().length) ranges.push(r);
          } catch (_) {}
          from = at + needle.length;
        }
      }
      return ranges;
    }

    async function chatFindLoadFullTranscript() {
      const paneId = (0, sharedState.activePaneId)();
      const root = chatFindGetRoot();
      const pane = (0, sharedState.paneByPaneId)(paneId);
      if (!root || !pane || !root.querySelector('.conv-load-earlier')) return;
      // Opening find on a huge session (one was 700 MB / 60k lines) fetched
      // and rendered the whole transcript and froze the tab. Only pull in
      // hidden history when it is small; otherwise search what is loaded.
      if (Number(pane.firstLine || 0) > CHAT_FIND_FULL_LOAD_MAX_LINES) return;
      pane.wantFull = true;
      pane.lastLine = 0;
      await (0, sharedState.fetchConversationEvents)(paneId);
    }

    // Ranges are live: when a re-render removes a matched text node, the
    // browser moves the range onto the parent and collapses it. It stays
    // connected but holds no text, so check the content, not just isConnected.
    function chatFindRangesStale(root) {
      if (_chatFindRoot !== root) return true;
      const needle = _chatFindLastBuiltQuery.toLowerCase();
      return _chatFindRanges.some(r =>
        r.collapsed
        || r.startContainer.nodeType !== Node.TEXT_NODE
        || !r.startContainer.isConnected
        || r.toString().toLowerCase() !== needle);
    }

    // While the find bar is open, re-collect after the transcript re-renders
    // so highlights survive live polling. Repaints without scrolling, so it
    // never yanks the view away from where the user is reading.
    let _chatFindObserver = null;
    let _chatFindObservedRoot = null;
    let _chatFindMutationTimer = null;
    function chatFindWatch(root) {
      if (!root || typeof MutationObserver === 'undefined') return;
      if (_chatFindObserver && _chatFindObservedRoot === root) return;
      chatFindUnwatch();
      _chatFindObservedRoot = root;
      _chatFindObserver = new MutationObserver(() => {
        if (_chatFindMutationTimer) return;
        _chatFindMutationTimer = setTimeout(() => {
          _chatFindMutationTimer = null;
          const query = sharedState.$chatFindInput.value;
          if (!query || query !== _chatFindLastBuiltQuery) return;
          const cur = chatFindGetRoot();
          if (!cur || !chatFindRangesStale(cur)) return;
          const keep = _chatFindMatchIndex;
          _chatFindRanges = chatFindCollectRanges(cur, query);
          _chatFindMatchIndex = Math.min(keep, Math.max(0, _chatFindRanges.length - 1));
          chatFindPaint({ scroll: false });
          chatFindUpdateCount();
        }, 250);
      });
      _chatFindObserver.observe(root, { childList: true, subtree: true, characterData: true });
    }
    function chatFindUnwatch() {
      if (_chatFindObserver) _chatFindObserver.disconnect();
      _chatFindObserver = null;
      _chatFindObservedRoot = null;
      if (_chatFindMutationTimer) clearTimeout(_chatFindMutationTimer);
      _chatFindMutationTimer = null;
    }

    function chatFindScrollToRange(range) {
      if (!range) return;
      let el = range.startContainer;
      if (el.nodeType === Node.TEXT_NODE) el = el.parentElement;
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'center', behavior: 'auto' });
    }

    function chatFindPaint(opts) {
      try {
        if (window.CSS && CSS.highlights) {
          if (CSS.highlights.has('chat-find')) CSS.highlights.delete('chat-find');
          if (CSS.highlights.has('chat-find-active')) CSS.highlights.delete('chat-find-active');
        }
      } catch (_) {}
      if (!_chatFindRanges.length) return;
      const idx = Math.max(0, Math.min(_chatFindMatchIndex, _chatFindRanges.length - 1));
      _chatFindMatchIndex = idx;
      if (typeof Highlight !== 'undefined' && window.CSS && CSS.highlights) {
        try {
          CSS.highlights.set('chat-find', new Highlight(..._chatFindRanges));
          CSS.highlights.set('chat-find-active', new Highlight(_chatFindRanges[idx]));
        } catch (_) {}
      }
      if (!opts || opts.scroll !== false) chatFindScrollToRange(_chatFindRanges[idx]);
    }

    // DOM + CSS Highlight search — never calls window.find(), so WebKit
    // cannot move focus or selection out of the find field.
    async function chatFindStep(opts) {
      opts = opts || {};
      const query = sharedState.$chatFindInput.value;
      if (!query) {
        chatFindClear();
        return;
      }
      const generation = ++_chatFindBuildGeneration;
      if (opts.rebuild) {
        try { await chatFindLoadFullTranscript(); } catch (_) {}
        if (generation !== _chatFindBuildGeneration || query !== sharedState.$chatFindInput.value) return;
      }
      const root = chatFindGetRoot();
      if (!root) return;

      if (!opts.rebuild && query === _chatFindLastBuiltQuery && chatFindRangesStale(root)) {
        // A live re-render replaced the matched text nodes. Re-collect so the
        // step lands on real on-screen text instead of a detached node (the
        // counter used to advance while nothing highlighted or scrolled).
        const keep = _chatFindMatchIndex;
        _chatFindRanges = chatFindCollectRanges(root, query);
        _chatFindMatchIndex = Math.min(keep, Math.max(0, _chatFindRanges.length - 1));
      }

      if (opts.rebuild || query !== _chatFindLastBuiltQuery) {
        _chatFindRanges = chatFindCollectRanges(root, query);
        _chatFindLastBuiltQuery = query;
        _chatFindMatchIndex = opts.backward ? Math.max(0, _chatFindRanges.length - 1) : 0;
      } else if (_chatFindRanges.length) {
        if (opts.backward) {
          _chatFindMatchIndex = (_chatFindMatchIndex - 1 + _chatFindRanges.length) % _chatFindRanges.length;
        } else {
          _chatFindMatchIndex = (_chatFindMatchIndex + 1) % _chatFindRanges.length;
        }
      }
      chatFindPaint();
      chatFindUpdateCount();
    }
    sharedState.refreshChatFind = chatFindStep;

    function closeChatFindModal() {
      if (_chatFindInputTimer) clearTimeout(_chatFindInputTimer);
      _chatFindInputTimer = null;
      _chatFindBuildGeneration++;
      if ($chatFindModal) $chatFindModal.style.display = 'none';
      chatFindUnwatch();
      chatFindClear();
      _flushDeferredSidebarRenderIfAny();
      if (typeof sharedState._flushComposerDeferredUpdates === 'function' && !(0, sharedState._isComposerFocused)()) {
        (0, sharedState._flushComposerDeferredUpdates)();
      }
    }

    sharedState.$chatFindInput.addEventListener('keydown', e => {
      if ((0, sharedState.isImeKey)(e)) return;
      if (e.key === 'Enter') {
        e.preventDefault();
        if (_chatFindInputTimer) clearTimeout(_chatFindInputTimer);
        _chatFindInputTimer = null;
        chatFindStep({ backward: e.shiftKey });
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        chatFindStep({ backward: false });
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        chatFindStep({ backward: true });
      }
      if (e.key === 'Escape') {
        closeChatFindModal();
        const cpInput = document.getElementById('cpInput');
        if (cpInput) cpInput.focus();
      }
    });
    sharedState.$chatFindInput.addEventListener('input', () => {
      if (_chatFindInputTimer) clearTimeout(_chatFindInputTimer);
      if (!sharedState.$chatFindInput.value) {
        _chatFindInputTimer = null;
        _chatFindBuildGeneration++;
        chatFindClear();
        return;
      }
      // Large transcripts can contain tens of thousands of text nodes. Wait
      // for a short typing pause instead of rescanning after every character.
      _chatFindInputTimer = setTimeout(() => {
        _chatFindInputTimer = null;
        chatFindStep({ rebuild: true });
      }, 300);
    });
    if (sharedState.$chatFindNext) {
      sharedState.$chatFindNext.addEventListener('click', () => chatFindStep({ backward: false }));
    }
    if (sharedState.$chatFindPrev) {
      sharedState.$chatFindPrev.addEventListener('click', () => chatFindStep({ backward: true }));
    }
    if (sharedState.$chatFindClose) sharedState.$chatFindClose.addEventListener('click', () => closeChatFindModal());
  }
  }

  function installConversationView30() {
document.querySelectorAll('.conv-pane[data-pane-id="p1"] [data-role="pane-close"]').forEach(btn => {
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      // Closing p1 while split is engaged: route the survivor's conv
      // into p1's slot via selectConversation, which handles transcript
      // clear, sticky header reset, workspace/usage pills, session id
      // label, pkood tail polling, and localStorage sync. Manually
      // transplanting state (and skipping selectConversation) leaves
      // those surfaces stale.
      if (sharedState.splitState.panes.length < 2) return;
      const survivor = sharedState.splitState.panes.find(p => p.id !== 'p1');
      if (!survivor) return;
      const survivorConvId = survivor.conversationId;
      // Tear down both panes' SSE before we mutate state.
      if (survivor.eventSource) { try { survivor.eventSource.close(); } catch (e) {} }
      if (sharedState.splitState.panes[0].eventSource) {
        try { sharedState.splitState.panes[0].eventSource.close(); } catch (e) {}
        sharedState.splitState.panes[0].eventSource = null;
      }
      // Remove survivor's DOM (p1's static element stays put).
      (0, sharedState.mountStatusRailForPaneId)('p1');
      const survivorEl = document.querySelector(`.conv-pane[data-pane-id="${survivor.id}"]`);
      if (survivorEl) survivorEl.remove();
      // Collapse splitState back to single-pane mode and reset p1's
      // per-pane data so selectConversation runs against a clean slate.
      sharedState.splitState.panes.splice(1);
      sharedState.splitState.orientation = null;
      sharedState.splitState.activeIndex = 0;
      Object.assign(sharedState.splitState.panes[0], (0, sharedState._newPaneState)('p1'));
      (0, sharedState.renderSplitLayout)();
      // Open the survivor's conv in p1 — fully exercised code path
      // that refreshes every UI surface (transcript, sticky header,
      // workspace pills, session id, pkood polling, etc.).
      if (survivorConvId) (0, sharedState.selectConversation)(survivorConvId, 'p1');
    });
  });
  }
  function bindConversationViews() {
    installConversationView1();
    installConversationView2();
    installConversationView3();
    installConversationView4();
    installConversationView5();
    installConversationView6();
    installConversationView7();
    installConversationView8();
    installConversationView9();
    installConversationView10();
    installConversationView11();
    installConversationView12();
    installConversationView13();
    installConversationView14();
    installConversationView15();
    installConversationView16();
    installConversationView17();
    installConversationView18();
    installConversationView19();
    installConversationView20();
    installConversationView21();
    installConversationView22();
    installConversationView23();
    installConversationView24();
    installConversationView25();
    installConversationView26();
    installConversationView27();
    installConversationView28();
    installConversationView29();
    installConversationView30();
  }

    return { bindConversationViews, installConversationView1, installConversationView2, installConversationView3, installConversationView4, installConversationView5, installConversationView6, installConversationView7, installConversationView8, installConversationView9, installConversationView10, installConversationView11, installConversationView12, installConversationView13, installConversationView14, installConversationView15, installConversationView16, installConversationView17, installConversationView18, installConversationView19, installConversationView20, installConversationView21, installConversationView22, installConversationView23, installConversationView24, installConversationView25, installConversationView26, installConversationView27, installConversationView28, installConversationView29, installConversationView30, applyConvFontScale, tapbackToggle, tapbackSyncBar, syncBodyTextFocusFlag, syncBodyHasFlags, _restoreAnchor, _frozenAskSchedule, openWorktreesModal, closeWorktreesModal, setPresentationMode, schedulePresentationEscape, onDashboardDocumentReady, orchSetExecutor, orchSetMoreOpen, orchInject, orchQueueAction, orchPlayPreview, orchWatchList, orchStartPolling, _hiTriggerSetup, _hiStartPolling, initAskPane, initRailLogPane, _uxqOpenItemDetail, annStart, startStatusRailTitleRename, clearPaneScreenForVideo, _syncConvViewSegs, setConvViewMode, closeCmdk, openCmdk, setConvPanelOpen, isSettingsFormField, _flushDeferredSidebarRenderIfAny, _frozenAskUpdate, _renderWorktreesModal, persistPresentationModeForSession, orchPrompt, orchComposerInput, orchSelectSpawnPlaceholder, orchGlowPlaybook, _createQueueForSession, requireConvRepo, orchStopPreview, _hiRefreshStatus, askLoadHistory, askEscapeHtml, askTraceHtml, renderAskVerdict, askMessageActionsHtml, askUsageHtml, askConfirmCardsHtml, askResultsHtml, askSaveHistory, _setMobileAskOpen, askOpenFileIssue, _setRailLogEntriesOpen, _uxqOpenItemModal, annStop, _annPaneSessionId, annPointerDown, annPointerMove, annPointerUp, annHandleKeydown, _rememberRenameOverride, _ccSyncRunsObserver, applyConvVerbose, renderCmdkList, _frozenAskPick, _attributeWorktreeDirty, orchAltExecutor, _uxqLegacySessionCreatedQueue, _uxqSetScopeOverride, _uxqSuggestSessionQueueName, promptModal, _uxqNormalizeQueueName, _uxqSessionQueueInstructions, _hiRenderPill, askNeutralizeMarkers, askSessionChipHtml, _uxqItemPrompt, _uxqItemTitle, _uxqReviewLabel, _uxqExtractImages, _uxqRelTime, _uxqWithMachine, _uxqChecksHtml, _uxqBindSessionActivity, _uxqLoadLinkedConversation, _uxqInitTicketZoom, _uxqReplaceCachedItem, _uxqOpenNoteModal, _uxqSaveField, _uxqWireTicketAnswer, annUpdateSelection, annPreviewAtPoint, annNormalizeRect, annElementAtPoint, annSelectionRectForElement, annBeginPreEditorScreenshotCapture, annShowEditor, _ccScheduleRuns, _cmdkLabel, _frozenAskText, _uxqScopeKey, _uxqLoadScopeMap, _uxqSelectedScopeKey, _uxqLoadSelectedScopeMap, _uxqResetHistoryPage, _hiFmtRel, _hiRetentionShort, askChipTooltip, _uxqWorkerSid, _uxqGateName, _uxqSessionActivityHtml, _uxqSyncTicketMeta, _uxqCloseNoteModal, _uxqAnswerDraft, _uxqAnswerNotice, annUpdateHoverLabel, annContextRect, annElementSummary, annAnnotateTarget, annPositionEditor, annRenderPreEditorScreenshotPreview, annContextForClipboard, annResolvePreEditorScreenshot, annBuildPayload, annOpenNewSessionWithContext, annShowUxFixesPreview, annOpenUxFixesQueue, annShowGithubReport, askResultTime, annText, annSelectorFor, annElementText, annClipText, annUxFixesQueuePrompt, annScreenshotPngBlob, annGithubIssueTitle, annGithubIssueBody, annOpenExternalUrl, annCssEscape, annScrubLocalPaths };
  }
  root.CCCConversationViews = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
