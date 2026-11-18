/* Shared conversation selection orchestration with explicit host adapters. */
(function (root) {
  'use strict';
  function createController({ state, panes, transport, composer, navigation, chrome, replay, pkood }) {
    const { activePaneId, paneByPaneId, convPaneElById, getConvInputBarForPane, _closeWtLogPanel, getConvViewForPane, setActivePaneById, refreshConversationBackgroundForPane, saveSplitState, syncPaneLastUserMessage, updatePaneHeader, getConvView, syncActivePaneChrome } = panes;
    const { abortConvScopedRequests, stopConvStream, _prefetchConversationTail, stopSpawnStream, stopCodexLogPoller, fetchConversationEvents, startConvStream, startSpawnStream } = transport;
    const { syncPendingSendsMapForConv, rememberComposerDraftForPane, setCurrentSession, seedSessionInputContext, updateSplitInputBar, updateInputBar, restoreComposerDraftForPane, restoreSplitPanelDraft } = composer;
    const { ffcUpdateSidebar, stopGroupChatReader, stopFlowNodeInspector, mobileShowForCurrentMode, _simpleOnConversationOpen, rememberRecentConv, getLastConvKey, renderIssueInConvPane, refreshConversationList } = navigation;
    const { resolvedSpawnConversationId, _compactRunActive, discardCompactRun, rememberConvScroll, closeStatusRailFileViewer, _updateLastWrittenLine, _restoreCwdControlsToInputBar, updateTtsFloatingControl, syncMobileOriginalAsk, cleanIssuePrompt, _afterConvTail, rowRepoPath, setCopyableSessionId, popoutRepoPath, updateSplitToolbar, scheduleSessionMetadataFetches, pollLocalhost } = chrome;
    const { exitConvReplayMode } = replay;
    const { stopPkoodTailPoller, loadPkoodTail, _gated } = pkood;
  async function selectConversation(id, paneId) {
    id = resolvedSpawnConversationId(id);
    paneId = paneId || activePaneId();
    // Perf metric 2 start (conv_open): overwritten on every click, so a click
    // superseded by a later one before its render lands is simply dropped —
    // no event fires for the abandoned one.
    try { state._perfConvOpen = { id: id, paneId: paneId, t0: performance.now() }; } catch (_) {}
    try {
      const _clickT = window.__cccPerfClick && window.__cccPerfClick.get(String(id));
      // The delegated click listener already logged this open — a real row
      // click hits both paths and would print the arrow twice.
      const _dup = Number.isFinite(_clickT) && (performance.now() - _clickT) < 800;
      if (id && !_dup && typeof window.__cccPerfRow === 'function') {
        window.__cccPerfRow('open', {
          at: Date.now(),
          text: '→ ' + String(id).slice(0, 8) + ' opening…',
          title: 'selectConversation: ' + id,
          pending: true,
        });
      }
    } catch (_) {}
    const pane = paneByPaneId(paneId);
    if (!pane) return;
    window.dispatchEvent(new CustomEvent('ccc:conversation-selected', { detail: {
      threadId: state.sessionIdByConv[id] || id, paneId, paneEl: convPaneElById(paneId),
    } }));
    // Kick the tail fetch before the synchronous chrome work below (35-60ms
    // of DOM writes and forced reads): fetchConversationEvents consumes it
    // via _takePrefetchedConversationTail, so the server round-trip overlaps
    // the prep instead of starting after it. Hermes sessions skip the
    // prefetch path in fetchConversationEvents, so don't warm those.
    if (id && pane.conversationId !== id) {
      // Free the old row's connection slots first (see convScopeSignal): the
      // old stream, spawn-stream and scoped polls otherwise hold Chrome's six
      // per-host connections and the tail fetch below waits for one.
      try { abortConvScopedRequests(); } catch (_) {}
      try { stopConvStream(paneId); } catch (_) {}
      try { if (state.spawnEventSource) state.spawnEventSource.close(); } catch (_) {}
    }
    if (id && pane.conversationId !== id && typeof _prefetchConversationTail === 'function') {
      try {
        if ((state.sessionSourceByConv[id] || '') !== 'hermes') _prefetchConversationTail(id, true);
      } catch (_) {}
    }
    const staleQueuedTray = getConvInputBarForPane(paneId)?.closest('.conv-pane')?.querySelector('.queued-steer-tray');
    if (staleQueuedTray && staleQueuedTray.dataset.conversationId !== String(id || '')) {
      staleQueuedTray.remove();
    }
    // The Watchtower log overlay behaves like any other reader: opening another
    // session dismisses it (no need to hit ×), landing you on that session.
    try { _closeWtLogPanel(); } catch (_) {}
    const previousConvId = pane.conversationId;
    if (previousConvId && previousConvId !== id) {
      // The /compact lifecycle card is scoped to one conversation's view.
      // Leaving mid-run drops it; the compaction itself keeps running
      // server-side and the durable summary still lands in the transcript.
      if (state._compactRun && !_compactRunActive()) discardCompactRun();
      syncPendingSendsMapForConv(pane, previousConvId);
      // CCC-131: stash the outgoing transcript's scroll before the view is
      // wiped, so returning to this conversation restores where we left off.
      rememberConvScroll(previousConvId, getConvViewForPane(paneId));
      // CCC-822: only stamp the composer's current text as a draft when
      // actually leaving this conversation for a different one. Re-selecting
      // the SAME conversation (a boot-time restore of the last-open session,
      // or just re-clicking the already-open row) also hit this
      // unconditionally, and on a fresh page load the box is still empty at
      // that point — clobbering a real draft just restored from localStorage
      // with "" a split second before the user ever saw it.
      rememberComposerDraftForPane(paneId);
    }
    if (typeof ffcUpdateSidebar === 'function') ffcUpdateSidebar(null);
    if (typeof closeStatusRailFileViewer === 'function') closeStatusRailFileViewer();
    // CCC-187: cross-repo ready-to-merge rows live in archiveData, not the
    // repo-scoped conversationsData, so fall back to archiveData (keyed by id
    // or session_id) — otherwise the title/source resolve to nothing and the
    // breadcrumb shows the bare id.
    const selectedConv = (state.conversationsData || []).find(x => x.id === id)
      || (Array.isArray(state.archiveData) ? state.archiveData.find(x => (x.id || x.session_id) === id) : null)
      || {};
    const source = state.sessionSourceByConv[id] || selectedConv.source || 'interactive';
    try { _updateLastWrittenLine(id); } catch (_) {}
    // Make this pane active so the existing globals (which proxy through
    // splitState.activeIndex) target the right pane while we run.
    setActivePaneById(paneId, id);
    // Stop any existing SSE stream or pollers
    stopConvStream(paneId);
    stopSpawnStream();
    stopCodexLogPoller();
    // Rescue the new-session chooser's adopted CWD controls before this
    // selection wipes the conv view they were moved into (CCC-86).
    try { _restoreCwdControlsToInputBar(); } catch (_) {}
    // If a group chat reader was active, tear it down so its 3s polling
    // stops and the standard input bar is restored. The reader was
    // rendered INTO #conversationsView so the surrounding pane structure
    // is intact — selectConversation will repopulate the view normally.
    if (state._gcReaderInterval || state._gcReaderPath || state._gcReaderHiddenInputBar) {
      try { stopGroupChatReader({ rerenderSidebar: true }); } catch (_) {}
    }
    if (state._flowInspectorState) {
      try { stopFlowNodeInspector(); } catch (_) {}
    }
    // Boot auto-restore loads the last conversation into the pane but must
    // NOT slide `.main` over the list. On Simple Home that left the user
    // stuck in a blank overlay (ccc-simple-conv-open stayed off because
    // home was still "showing"). On Advanced mobile the same slide hid
    // the Sessions/Queues bottom nav behind the last-opened chat. A
    // deliberate tap still calls mobileShowForCurrentMode() because
    // _qfBootRestoreDepth is 0 then.
    const _isBootRestore = typeof state._qfBootRestoreDepth !== 'undefined'
      && state._qfBootRestoreDepth > 0;
    if (!_isBootRestore) mobileShowForCurrentMode();
    try { if (typeof _simpleOnConversationOpen === 'function') _simpleOnConversationOpen(id); } catch (_) {}
    state.currentConversation = id;
    // The terminal panel (index.html) keys its cwd off the open
    // conversation's repo — tell it the repo may have changed.
    try { window.dispatchEvent(new Event('ccc-repo-changed')); } catch (_) {}
    // Track recently-opened conversations for the mobile swipe-rotate gesture
    // (see _swipeRecent / wireConvSwipeRotate). Most-recent-first, de-duped,
    // capped at 4. Real conversation ids only — skip the new-session sentinel.
    // Suppress the re-sort while a swipe is rotating the ring, so swipe-left
    // and swipe-right stay true inverses (a stable ring). A normal open still
    // promotes the conversation to the front.
    if (id && id !== '__new__' && !state._swipeRotating) rememberRecentConv(id);
    // A read in progress for a DIFFERENT conversation keeps playing; surface
    // the floating pause/resume/stop control now that its conversation is no
    // longer focused (and hide the control if we just refocused it).
    if (typeof updateTtsFloatingControl === 'function') updateTtsFloatingControl();
    refreshConversationBackgroundForPane(paneId);
    // Remember which card was last opened so we can re-open it on the
    // next page load. Reads happen in loadConversationList once the list
    // is populated; misses (id no longer present) just fall through.
    try {
      if (id && !state.CONV_POPOUT_MODE) {
        localStorage.setItem(getLastConvKey(), id);
        localStorage.setItem('ccc-last-conv', id);
        // Also stamp the unified last-view marker so a hard refresh
        // picks whichever was most recent — group chat or conversation.
        localStorage.setItem('ccc-last-view', JSON.stringify({
          type: 'conv',
          id,
          at: Date.now(),
        }));
      }
    } catch (_) {}
    pane.restored = true;
    saveSplitState();
    state.convLastLine = 0;
    pane.firstLine = 0;
    pane.loadBeforeLine = 0;
    pane.wantFull = false;
    if (paneId === activePaneId()) syncMobileOriginalAsk('');
    // Seed the pane header with the original ask; a rendered user message
    // replaces it. Without the seed, a transcript whose loaded tail has no
    // user message (long Codex/worker runs, peer-only injects) showed no
    // top bar at all (CCC-1245).
    const _selRow = (state.conversationsData || []).find(x => x && (x.id === id || x.session_id === id));
    syncPaneLastUserMessage(paneId, _selRow && _selRow.first_message ? cleanIssuePrompt(_selRow.first_message) : '');
    // Drop any visual "Clear" watermark (CCC-474) — a fresh (re)select of a
    // conversation always shows full history.
    const _selClearView = getConvViewForPane(paneId);
    if (_selClearView) {
      delete _selClearView.dataset.videoClearLine;
      _selClearView.classList.remove('is-video-cleared');
    }
    state._firstUserMsgRendered = false;
    state._dynamicAskState = null;  // sticky-header scroll tracker - repopulated when the new sticky is built
    state._currentToolGroup = null;
    state._currentToolCount = 0;
    // Reset replay state and hide the button while the new conv loads. Only
    // tear down a replay that's actually running on THIS pane (or synced to
    // it, in a two-pane replay) — switching conversations in an unrelated
    // split pane shouldn't interrupt it.
    if (state._convReplayActive && state._convReplayPaneIds.includes(paneId)) try { exitConvReplayMode(); } catch (_) {}
    if (state.$convReplayBtn && paneId === activePaneId()) state.$convReplayBtn.style.display = 'none';
    // Detach in-memory pending echoes for the outgoing conv; they are
    // restored from pendingSendsByConv when this pane re-opens that conv.
    state._pendingSends = [];
    const selectedRow = state.conversationsData.find(x => x.id === id)
      || (Array.isArray(state.archiveData) ? state.archiveData.find(x => (x.id || x.session_id) === id) : null)
      || null;
    // WatchTower's "Open CCC" deep link can hand us a raw session UUID before
    // the sidebar request has populated sessionIdByConv. Treat that UUID as
    // the session id directly so currentSession remains sendable and the
    // composer does not disappear during this startup race.
    const isSessionId = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i
      .test(String(id || ''));
    const selectedSessionId = state.sessionIdByConv[id]
      || (selectedRow && selectedRow.session_id)
      || (isSessionId ? id : null);
    updatePaneHeader(paneId, selectedRow || Object.assign({ id, source }, selectedConv || {}));
    // Federation handoff ownership — one-shot fetch (no polling); paints a
    // "Moved to <node>" chip in the breadcrumb if owned elsewhere now.
    if (id && id !== '__new__' && typeof window._cccFetchHandoffStatus === 'function') {
      _afterConvTail(() => { try { window._cccFetchHandoffStatus(id); } catch (_) {} });
    }
    if (selectedRow && selectedRow.source === 'backlog' && selectedRow.issue_number) {
      await renderIssueInConvPane(selectedRow.issue_number, rowRepoPath(selectedRow), selectedRow.id);
      return;
    }
    // Leaving issue view — drop the body class and clear the synthetic
    // issue-header from the rail so the next conv's original-ask is
    // reconciled cleanly by `_applyStatusRailLayout`.
    document.body.classList.remove('is-issue-view');
    const _rail = document.getElementById('statusRail');
    if (_rail) {
      // Clear EVERY stale Original-ask / activity node the previous
      // conversation left mounted in the rail — not just the synthetic
      // issue-header. In right-rail mode the sticky's `.csh-ask-original`
      // is MOVED into #statusRail; wiping $view.innerHTML below destroys
      // the sticky but leaves these rail-hosted copies behind. If the
      // next conversation has no first user message yet (still loading,
      // or genuinely has none), the sticky never rebuilds and
      // `_applyStatusRailLayout` keeps treating the leftover rail node as
      // "live" — so the rail stays stuck on the PREVIOUS conversation's
      // ask. Clearing here makes the rail blank until the new conv's
      // first user message repaints it. `setActivePaneById` above already
      // made the switched pane active, so the single global rail belongs
      // to this selection.
      _rail.querySelectorAll('.csh-ask-original, .csh-col-activity').forEach(n => n.remove());
    }
    // Also pull #issueCloseActions out of the rail if it's still there
    // (left over from the previous issue view) — its parent issue body
    // is gone now, so the buttons would no-op anyway.
    const _ica = document.getElementById('issueCloseActions');
    if (_ica) _ica.remove();
    const $view = getConvView();
    if (previousConvId !== id) {
      if ($view._streamedAssistantMessageIds) $view._streamedAssistantMessageIds.clear();
    }
    $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Loading...</div>';
    // Reset per-Task subagent tabs — they belong to the previous conv.
    // The strip stays in the DOM (sibling of $view) and updates on next
    // tab event; clear its state + hide it now to avoid showing stale tabs.
    if ($view._tabState) {
      for (const tid of Object.keys($view._tabState.tasks || {})) {
        const t = $view._tabState.tasks[tid];
        if (t.closeTimer) clearTimeout(t.closeTimer);
      }
      $view._tabState = null;
    }
    const _strip = $view.parentNode && $view.parentNode.querySelector('.conv-tab-strip');
    if (_strip) { _strip.hidden = true; _strip.innerHTML = ''; }
    $view.dataset.activeTab = 'master';
    const loadToken = ++state.conversationPaneLoadToken;
    state.conversationPaneLoading = true;
    const finishConversationPaneLoad = () => {
      if (state.conversationPaneLoadToken === loadToken) state.conversationPaneLoading = false;
    };
    syncActivePaneChrome(id);
    // Update split panel session ID display
    if (state.$cpSessionId) {
      const sid = selectedRow && selectedRow.pending_spawn ? '' : (selectedSessionId || '');
      setCopyableSessionId(state.$cpSessionId, sid);
    }
    const paneEl = document.querySelector(`.conv-pane[data-pane-id="${paneId}"]`);
    if (paneEl) {
      paneEl.classList.toggle('is-codex-session', source === 'codex');
      paneEl.classList.toggle('is-gemini-session', source === 'gemini');
      paneEl.classList.toggle('is-cursor-session', source === 'cursor');
      paneEl.classList.toggle('is-antigravity-session', source === 'antigravity');
      paneEl.classList.toggle('is-hermes-session', source === 'hermes');
      // Web-UI rendering mode (kimi-web parity conversation renderer):
      // currently enabled for kimi + codex panes. Claude stays legacy.
      paneEl.classList.toggle('is-webui-session', source === 'kimi' || source === 'codex');
    }
    const isPendingSpawn = !!(selectedConv && selectedConv.pending_spawn);
    if (source === 'backlog') {
      setCurrentSession(null, null, null, false, null);
    } else if (isPendingSpawn) {
      setCurrentSession(
        source,
        null,
        state.sessionCwdByConv[id] || selectedConv.spawn_cwd || selectedConv.cwd,
        state.sessionCwdExistsByConv[id],
        state.sessionSpawnPidByConv[id],
        rowRepoPath(selectedConv) || selectedConv.spawn_cwd || popoutRepoPath()
      );
    } else {
      setCurrentSession(
        source,
        selectedSessionId,
        state.sessionCwdByConv[id] || (selectedRow && selectedRow.cwd) || null,
        state.sessionCwdExistsByConv[id],
        state.sessionSpawnPidByConv[id],
        rowRepoPath(selectedConv) || popoutRepoPath(),
        selectedRow && selectedRow.jsonl_path
      );
    }
    seedSessionInputContext(selectedSessionId, selectedRow || selectedConv, paneId);
    // Update split panel input bar visibility
    updateSplitInputBar();
    // Update the active pane's own input bar immediately so it shows on restore
    // without waiting for the first liveStatus poll (fixes second-pane on refresh).
    updateInputBar();
    // Update split panel toolbar buttons
    updateSplitToolbar();
    restoreComposerDraftForPane(paneId, id);
    restoreSplitPanelDraft(id);

    try {
      if (source === 'pkood') {
        stopPkoodTailPoller();
        const agentId = id.replace(/^pkood-/, '');
        if (state.$pkoodKillBtn) {
          state.$pkoodKillBtn.style.display = '';
          state.$pkoodKillBtn.onclick = async () => {
            if (!confirm('Kill pkood agent "' + agentId + '"?')) return;
            try {
              const res = await fetch('/api/pkood/kill', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ agent_id: agentId }),
              });
              const data = await res.json();
              if (data.ok) {
                state.$pkoodKillBtn.textContent = 'Killed';
                setTimeout(() => { state.$pkoodKillBtn.textContent = 'Kill'; refreshConversationList(); }, 1500);
              }
            } catch (err) {}
          };
        }
        await loadPkoodTail(agentId);
        state.pkoodTailPoller = setInterval(_gated('pkoodTail', () => loadPkoodTail(agentId)), 2000);
      } else {
        stopPkoodTailPoller();
        if (state.$pkoodKillBtn) state.$pkoodKillBtn.style.display = 'none';
        await fetchConversationEvents(paneId);
        // A boot restore can still be awaiting this fetch when the user opens
        // New Session or another card. fetchConversationEvents discards its
        // stale render, and this companion guard prevents the old selection
        // from reopening its canonical/spawn streams afterward.
        if (pane.conversationId !== id) return;
        if (source !== 'backlog' && source !== 'hermes' && !isPendingSpawn) startConvStream(paneId);
        // Block-level streaming from the spawn log — only succeeds if the
        // backend finds a CCC-spawned headless process for this session.
        // No-op for externally launched, IDE-launched, or pkood sessions.
        const sid = selectedSessionId || '';
        if (sid && source !== 'codex' && source !== 'cursor' && source !== 'antigravity' && source !== 'hermes' && source !== 'backlog') startSpawnStream(sid, paneId);
      }
    } finally {
      finishConversationPaneLoad();
      if (source !== 'backlog' && pane.conversationId === id) {
        const timelineSid = (selectedRow || selectedConv || {}).session_id || id;
        scheduleSessionMetadataFetches(id, timelineSid, paneId);
        try { if (typeof pollLocalhost === 'function') pollLocalhost(); } catch (_) {}
      }
    }
  }
    return { selectConversation };
  }
  root.CCCConversationSelection = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
