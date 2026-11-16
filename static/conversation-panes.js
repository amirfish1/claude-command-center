/* Shared conversation panes with explicit live-state and host adapters. */
(function (root) {
  'use strict';
  function createController({ state, helpers }) {
  function activePaneId() { return state.splitState.panes[state.splitState.activeIndex].id; }

  function paneByPaneId(pid) { return state.splitState.panes.find(p => p.id === pid) || null; }

  function paneIndexByPaneId(pid) {
    for (let i = 0; i < state.splitState.panes.length; i++) if (state.splitState.panes[i].id === pid) return i;
    return -1;
  }

  function otherPaneConvIds() {
    const ids = new Set();
    if (!state.splitState || !state.splitState.orientation || !Array.isArray(state.splitState.panes) || state.splitState.panes.length < 2) return ids;
    state.splitState.panes.forEach((p, i) => {
      if (i !== state.splitState.activeIndex && p && p.conversationId) ids.add(p.conversationId);
    });
    return ids;
  }

  function syncActivePaneChrome(activeConvId) {
    const activePid = activePaneId();
    const newConvId = (arguments.length > 0 && activeConvId !== undefined) ? activeConvId : state.currentConversation;
    if (typeof state._sttRecording !== 'undefined' && state._sttRecording) {
      const isGc = (state._sttActivePaneId === 'gc');
      const paneChanged = (state._sttActivePaneId !== activePid);
      const convChanged = (!isGc && state._sttActiveConvId !== newConvId);
      if (paneChanged || convChanged) {
        (0, helpers.stopSpeechRecognition)();
      }
    }
    (0, state._convPaneHost)().querySelectorAll('.conv-pane').forEach(el => {
      const on = el.getAttribute('data-pane-id') === activePid;
      if (el.classList.contains('is-active') !== on) el.classList.toggle('is-active', on);
    });
    const convId = arguments.length > 0 ? activeConvId : state.currentConversation;
    (0, helpers.syncConversationNavigationFocus)(convId, otherPaneConvIds());
    try { (0, helpers.updateSubagentsPanel)(convId); } catch (_) { /* defensive - panel is non-critical */ }
    try { if (typeof helpers.updateOrchestrationPane === 'function') (0, helpers.updateOrchestrationPane)(convId); } catch (_) {}
  }

  function setActivePaneById(paneId, activeConvId) {
    const idx = paneIndexByPaneId(paneId);
    if (idx < 0) return false;
    const changed = state.splitState.activeIndex !== idx;
    state.splitState.activeIndex = idx;
    if (typeof helpers.mountStatusRailForActivePane === 'function') (0, helpers.mountStatusRailForActivePane)();
    if (changed) (0, helpers.saveSplitState)();
    if (arguments.length > 1) syncActivePaneChrome(activeConvId);
    else syncActivePaneChrome();
    if (changed) {
      const pane = paneByPaneId(paneId);
      const convId = pane && pane.conversationId;
      const row = convId
        ? ((state.conversationsData || []).find(x => x && x.id === convId)
          || (Array.isArray(state.archiveData) ? state.archiveData.find(x => x && ((x.id || x.session_id) === convId)) : null))
        : null;
      if (convId || row) (0, helpers.updatePaneHeader)(paneId, row || { id: convId, session_id: convId });
      // CCC-215: the rail's session-id chip and Codex diagnostics are
      // singletons last written for whichever pane loaded most recently;
      // repaint them for the newly focused pane.
      const cs = pane && pane.currentSession;
      const csId = (cs && cs.id) || '';
      if (typeof helpers.setCopyableSessionId === 'function') {
        (0, helpers.setCopyableSessionId)(state.$convSessionId, csId, (row && row.jsonl_path) || '');
      }
      if (typeof helpers.fetchCodexDiagnostics === 'function') {
        if (csId && cs.source === 'codex') (0, helpers.fetchCodexDiagnostics)(csId, paneId);
        else (0, helpers._codexDiagHidePanel)();
      }
      if (typeof helpers.updateInputBar === 'function') (0, helpers.updateInputBar)();
      if (typeof helpers.updateResumeButton === 'function') (0, helpers.updateResumeButton)();
      if (typeof helpers.updateAnnounceButton === 'function') (0, helpers.updateAnnounceButton)();
      if (typeof helpers.updateJumpButton === 'function') (0, helpers.updateJumpButton)();
      if (typeof helpers.updateSplitToolbar === 'function') (0, helpers.updateSplitToolbar)();
      (0, helpers.scheduleTtsPrefetch)();
      try { if (typeof helpers.pollVercelDeploy === 'function') (0, helpers.pollVercelDeploy)(); } catch (_) {}
      try {
        if (typeof helpers.resetLocalhostPill === 'function') (0, helpers.resetLocalhostPill)();
        if (typeof helpers.pollLocalhost === 'function') (0, helpers.pollLocalhost)();
      } catch (_) {}
      try { if (typeof helpers._applyStatusRailLayout === 'function') (0, helpers._applyStatusRailLayout)(); } catch (_) {}
      try { if (typeof window._cccApplyToolbarRailLayout === 'function') window._cccApplyToolbarRailLayout(); } catch (_) {}
    }
    return true;
  }

  function getConvView() {
    return (0, state.getConvViewForPane)(activePaneId()) || state.$conversationsView;
  }

  function getConvInputBarForPane(pid) {
    const pane = (0, state.convPaneElById)(pid);
    return pane ? pane.querySelector('.conv-input-bar') : null;
  }
    return { activePaneId, paneByPaneId, paneIndexByPaneId, otherPaneConvIds, getConvView, getConvInputBarForPane, syncActivePaneChrome, setActivePaneById };
  }
  root.CCCConversationPanes = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
