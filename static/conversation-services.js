/* Shared conversation services with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices }) {
    const { restoreLastViewOrConversation, conversationRowsContainId, restoreLastConversation, _signalSnapshot, stampFreshSessionsAsBorn, renderPopoutMissingConversation, maybeSelectPopoutConversation, carryFlowPendingSpawnNode, followPendingSpawnTicket, loadConversationList = (...args) => hostServices.loadConversationList(...args), persistFlowNodePositions, reconcilePendingNewSessionObjectAssignments, gcFallbackName, gcDisplayName, gcInitials, extractHash, buildAgentFallbackNames, getParticipantColor, formatWaitingOn, updateGcInfoBar, showUpdatedJustNowTick, startReplay, renderReplayControls, stepReplayBack, exitReplayMode, _gcReplayClearHero, _gcReplayCreateHero, _gcReplayMessagePauseMs, _gcReplayHoldThenLandHero, _gcReplayLandHero, _gcReplayHumanStep, _gcReplayParseWhen, _mergeReplayQueueEvents, _renderReplayQueueEvent, playNextReplayStep, _gcReplayReceiptsHtml, _gcReplayAlertsHtml, _gcMentionClose, _gcMentionParticipants, _gcMentionDetectToken, _gcMentionRefresh, _gcMentionCommit, _gcMentionMenuHandleKeydown, wireGcMentionAutocomplete, markSystemBlockquotes, gcExtractPingedNames, renderGroupChatMarkdown, openGcParticipantPicker, openGroupChatReader, _gcExpandHashIds, replaceParticipantMentions, updateOrchestratorPanel, gcParseChatTimestamp, gcRelativeTime, gcTimeChip, _gcShowNewPostsPill, pollGroupChatReader, sendHumanGcPost, getActiveGroupChatPillEl, gcEpochMs, gcTriggerMs, gcSortMs, gcShouldShowActivePill, activeGroupChatPillTarget, updateActiveGroupChatPill, markActiveGroupChatTrigger, _gcChatsKey, pollGcActive, setGroupChatPaused, addSessionToGroupChat, renameGroupChatParticipant, _hasSid } = root.CCCConversationDetails
      ? root.CCCConversationDetails.createController({
          state: sharedState, helpers: hostServices,
          functions: {
          get persistFlowNodeParents() { return persistFlowNodeParents; },
          get _currentSidebarTabKey() { return _currentSidebarTabKey; },
          get loadPendingNewSessionObjectAssignments() { return loadPendingNewSessionObjectAssignments; },
          get savePendingNewSessionObjectAssignments() { return savePendingNewSessionObjectAssignments; },
          get assignSessionNodeToParent() { return assignSessionNodeToParent; },
          get stopFlowNodeInspector() { return stopFlowNodeInspector; },
          get syncBodyGcReaderFlag() { return syncBodyGcReaderFlag; },
          get setStatusRailTab() { return setStatusRailTab; },
          },
        }, "conversation-services.js")
      : {};

  function rowsSpacing() {
    let v = null;
    try { v = localStorage.getItem(sharedState.ROWS_SPACING_KEY); } catch (_) {}
    return (v === 'cozy' || v === 'airy') ? v : 'roomy';
  }

  function _currentSidebarTabKey() {
    try { return normalizeSidebarTabKey(window.CCCNavigation.getTab()); }
    catch (_) { return ''; }
  }

  function newSessionLaunchStillCurrent(context) {
    return (0, sharedState.activePaneId)() === context.paneId
      && sharedState.newSessionComposerRevision === context.revision
      && sharedState.currentConversation === '__new__';
  }

  function pendingSpawnIsSelected(card) {
    if (!card) return false;
    const paneId = card.spawn_pane_id || (0, sharedState.activePaneId)();
    const pane = (0, sharedState.paneByPaneId)(paneId);
    return (0, sharedState.activePaneId)() === paneId && !!pane
      && (pane.conversationId === card.id
        || !!(card.expected_session_id && pane.conversationId === card.expected_session_id));
  }

  function refreshSelectedPendingSpawn(card) {
    if (card && card.source === 'codex' && card._landedResolved) return;
    if (pendingSpawnIsSelected(card) && typeof sharedState.selectConversation === 'function') {
      const paneId = card.spawn_pane_id || (0, sharedState.activePaneId)();
      (0, sharedState.selectConversation)((0, sharedState.paneByPaneId)(paneId).conversationId, paneId);
    }
  }

  function insertPendingSpawnCard(pid, subject, sourceOrEngine, logPath, meta) {
    if (!pid) return;
    const id = 'spawning-' + pid;
    // Backwards-compat: this used to take `usePkood: bool`. Accept
    // either the legacy boolean (true → 'pkood') or a new explicit
      // string ('claude' | 'codex' | 'gemini' | 'cursor' | 'antigravity' | 'pkood' | 'interactive').
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
      is_live: true, archived: false, verified: false,
      spawn_pid: pid, pending_spawn: true,
      spawn_requested_at: Date.now(),
      spawn_pane_id: (0, sharedState.activePaneId)(),
      has_edit: false, has_commit: false, has_push: false,
      sidecar_status: 'active', sidecar_has_writes: false,
      question_waiting: false, question_text: '', question_header: '', question_preamble: '', question_options: [], question_option_details: [],
      modified: Date.now() / 1000, size: 0, branch: '',
      last_event_type: null, pending_tool: null, pending_file: null,
      name_overridden: false,
      // Fire-and-watch engines also get durable engine-native sessions; the
      // log path is only a fallback while the real row is materializing.
      agent_log_path: (0, sharedState.isSpawnLogPlaceholderSource)(source) ? (logPath || null) : null,
      codex_log_path: source === 'codex' ? (logPath || null) : null,
    };
    if (meta && typeof meta === 'object') {
      Object.assign(card, meta);
      card.id = id;
      card.session_id = id;
      card.source = source;
      card.spawn_pid = pid;
      card.pending_spawn = true;
      card.is_live = true;
      card.archived = false;
    }
    // The browser knows this launch is human-owned before the server has a
    // session id or ticket. Keep that provisional row in its topic lane.
    const userIdeaSpawn = (0, sharedState.ideaTicketsOn)() && (!card.external_spawn || card.spawned_via === 'ui')
      && !card.spawned_lane;
    if (userIdeaSpawn && !card.idea_ticket) {
      const parent = sharedState.conversationsData.find(c => c && (c.session_id || c.id) === card.parent_session_id);
      card.idea_ticket = parent && parent.idea_ticket
        ? Object.assign({}, parent.idea_ticket, { closed: false })
        : { ref: '', queue: 'IDEAS', pending: true, closed: false, sessions: 1 };
    }
    if (userIdeaSpawn && !card.no_auto_select) {
      sharedState.$convSearch.value = '';
      try { window.CCCNavigation.setTab(card.idea_ticket.queue === 'SMALL-BUGS' ? 'smallbugs' : 'ideas'); } catch (_) {}
      if (typeof _setSharedQueuePanelHost === 'function') _setSharedQueuePanelHost('rail');
    }
    if (!card.no_auto_select && !userIdeaSpawn && !(0, sharedState.ideaTicketsOn)()) {
      // Land on a tab that actually renders the placeholder. Spawning while
      // Other/Jobs/Queues was active left the new session invisible — worst
      // on phones, where the tab bar is hidden and the stored tab is the
      // only list view.
      const _laneTab = card.spawned_lane === 'workers' ? 'workers'
        : card.spawned_lane === 'other' ? 'archived' : 'coding';
      const _curTab = _currentSidebarTabKey();
      if (_curTab !== 'inprogress' && _curTab !== _laneTab) {
        try { window.CCCNavigation.setTab(_laneTab); } catch (_) {}
      }
    }
    sharedState.pendingSpawns.set(pid, card);
    if (source === 'codex' || source === 'antigravity') trackSpawnView(card);
    sharedState.conversationsData = [card, ...sharedState.conversationsData];
    // Pin to Working column for the moment
    sharedState.columnOverrides[id] = 'working';
    try { localStorage.setItem('ccc-column-overrides', JSON.stringify(sharedState.columnOverrides)); } catch (_) {}
    // Auto-select the placeholder so the right pane lands on the new
    // session immediately. Once the real session materializes, the
    // placeholder→real swap in loadConversationList re-binds the right
    // pane in place — selection follows the spawn end-to-end.
    const selectPending = () => {
      // An externally-initiated spawn (`ccc spawn`, an agent, a queue lane)
      // must NOT steal the pane: the user did not ask for this session, they
      // just need to see it appear. Only spawns this tab started auto-select.
      if (card.no_auto_select) return;
      if (typeof sharedState.selectConversation === 'function') (0, sharedState.selectConversation)(id);
    };
    if (source === 'codex' && !card.no_auto_select) {
      selectPending();
      requestAnimationFrame(() => setTimeout(() => (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value)), 0));
    } else if (card.fast_path && !card.no_auto_select) {
      // Rebuilding a sidebar with thousands of sessions can occupy the main
      // thread for a full second. Claude's main pane is the useful immediate
      // feedback, so select it now and let the complete sidebar rebuild run
      // only after the first assistant text has painted. Ideas launches need
      // their pending row now, so paint that sidebar after selecting the pane.
      selectPending();
      if (userIdeaSpawn) (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value), { force: true });
    } else {
      (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value));
      selectPending();
    }
    // Registration watch for Claude placeholders. Fire-and-watch placeholders
    // stick around until the durable thread row appears, with the spawn log
    // as a fallback if the CLI exits before creating a thread.
    if (!(0, sharedState.isSpawnLogPlaceholderSource)(source)) {
      (0, sharedState._watchPendingSpawnRegistration)(pid, id);
    }
  }

  function loadDeletedFlowDraftSessionIds() {
    try {
      const raw = JSON.parse(localStorage.getItem(sharedState.FLOW_DRAFT_DELETED_KEY) || '[]');
      if (Array.isArray(raw)) return new Set(raw.filter(Boolean));
    } catch (_) {}
    return new Set();
  }

  function saveDeletedFlowDraftSessionIds(ids) {
    try {
      const list = Array.from(ids || []).filter(Boolean);
      if (list.length) localStorage.setItem(sharedState.FLOW_DRAFT_DELETED_KEY, JSON.stringify(list));
      else localStorage.removeItem(sharedState.FLOW_DRAFT_DELETED_KEY);
    } catch (_) {}
  }

  function clearDeletedFlowDraftSessionIds(ids) {
    if (!ids || typeof ids.forEach !== 'function') return;
    const saved = loadDeletedFlowDraftSessionIds();
    ids.forEach(id => { if (id) saved.delete(id); });
    saveDeletedFlowDraftSessionIds(saved);
  }

  function persistFlowNodeParents() {
    try { localStorage.setItem('ccc-flow-node-parents', JSON.stringify(sharedState.flowNodeParents)); } catch (_) {}
    syncObjectsToServer();
  }

  function persistFlowCustomObjects() {
    try { localStorage.setItem('ccc-flow-custom-objects', JSON.stringify(sharedState.flowCustomObjects)); } catch (_) {}
    syncObjectsToServer();
  }

  function _objectsApiPost(path, body) {
    return fetch('/api/objects/' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body || {}),
    }).then(r => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))));
  }

  function ensureNewSessionDefaultObject() {
    const existing = (sharedState.flowCustomObjects || []).find(o => o && o.id === sharedState.NEW_SESSION_DEFAULT_OBJECT_ID);
    if (existing) return existing;
    const now = Date.now();
    const obj = {
      id: sharedState.NEW_SESSION_DEFAULT_OBJECT_ID,
      title: sharedState.NEW_SESSION_DEFAULT_OBJECT_TITLE,
      created_at: now,
      updated_at: now,
      status: 'active',
      objective: 'Triage newly started sessions',
    };
    sharedState.flowCustomObjects.push(obj);
    persistFlowCustomObjects();
    return obj;
  }

  function loadNewSessionObjectByCwd() {
    try {
      const raw = JSON.parse(localStorage.getItem(sharedState.NEW_SESSION_OBJECT_BY_CWD_KEY) || '{}');
      return raw && typeof raw === 'object' ? raw : {};
    } catch (_) {
      return {};
    }
  }

  function newSessionObjectScopeKey() {
    const cwd = (typeof sharedState.getSpawnCwd === 'function' && (0, sharedState.getSpawnCwd)()) || (0, sharedState.popoutRepoPath)() || '';
    return (0, sharedState.normalizeSpawnCwdPath)(cwd) || '__default__';
  }

  function findActiveFlowObject(objectId) {
    if (!objectId) return null;
    return (sharedState.flowCustomObjects || []).find(o =>
      o && o.id === objectId && !o.archived && !isArchivedFlowObjectId(o.id)) || null;
  }

  function newSessionObjectRepoMatchIds(scopeKeyOverride) {
    const repoPath = scopeKeyOverride || newSessionObjectScopeKey();
    const matches = new Set();
    if (!repoPath || repoPath === '__default__') return matches;
    const addObjectAndAncestors = (nodeId) => {
      const seen = new Set();
      for (let node = nodeId; node && !seen.has(node); node = sharedState.flowNodeParents[node] || '') {
        seen.add(node);
        if (node.indexOf('object:') === 0) matches.add(node.slice(7));
      }
    };
    (sharedState.conversationsData || []).forEach(row => {
      const sid = row && (row.session_id || row.id);
      if (!sid || (0, sharedState.normalizeSpawnCwdPath)((0, sharedState.rowRepoPath)(row)) !== repoPath) return;
      addObjectAndAncestors(sharedState.flowNodeParents[(0, sharedState.flowNodeKey)('session', sid)]);
    });
    (sharedState.flowDraftSessions || []).forEach(draft => {
      if (!draft || (0, sharedState.normalizeSpawnCwdPath)(draft.repo_path) !== repoPath) return;
      const nodeId = (0, sharedState.flowNodeKey)('draft-session', draft.id || '');
      addObjectAndAncestors(sharedState.flowNodeParents[nodeId] || draft.parent_node_id);
    });
    return matches;
  }

  function resolveAutoParentNodeForRepoPath(repoPath) {
    const scopeKey = (0, sharedState.normalizeSpawnCwdPath)(repoPath) || '';
    if (!scopeKey) return null;
    const map = loadNewSessionObjectByCwd();
    const overrideObj = findActiveFlowObject(map[scopeKey]);
    if (overrideObj) return (0, sharedState.flowNodeKey)('object', overrideObj.id);
    const repoMatches = newSessionObjectRepoMatchIds(scopeKey);
    if (repoMatches.size) return (0, sharedState.flowNodeKey)('object', repoMatches.values().next().value);
    return (0, sharedState.flowNodeKey)('repo', repoPath);
  }

  function getNewSessionSelectedObject() {
    const map = loadNewSessionObjectByCwd();
    const obj = findActiveFlowObject(map[newSessionObjectScopeKey()]);
    return obj || ensureNewSessionDefaultObject();
  }

  function loadPendingNewSessionObjectAssignments() {
    try {
      const raw = JSON.parse(localStorage.getItem(sharedState.NEW_SESSION_PENDING_OBJECT_KEY) || '{}');
      return raw && typeof raw === 'object' ? raw : {};
    } catch (_) {
      return {};
    }
  }

  function savePendingNewSessionObjectAssignments(map) {
    try { localStorage.setItem(sharedState.NEW_SESSION_PENDING_OBJECT_KEY, JSON.stringify(map || {})); } catch (_) {}
  }

  async function assignSessionNodeToParent(sid, parentNodeId) {
    if (!sid || !parentNodeId) return false;
    const nodeId = (0, sharedState.flowNodeKey)('session', sid);
    sharedState.flowNodeParents[nodeId] = parentNodeId;
    persistFlowNodeParents();
    if (parentNodeId.indexOf('object:') === 0) {
      _objectsApiPost('assign', { session_node_id: nodeId, object_id: parentNodeId.slice(7) }).catch(() => {});
    }
    return true;
  }

  function rememberPendingNewSessionObjectAssignment(spawnId, parentNodeId) {
    if (!spawnId || !parentNodeId) return;
    const pending = loadPendingNewSessionObjectAssignments();
    pending[String(spawnId)] = { parent_node: parentNodeId, created_at: Date.now() };
    savePendingNewSessionObjectAssignments(pending);
  }

  function assignSpawnedSessionToDefaultObject(data, repoPath) {
    const parentNodeId = resolveAutoParentNodeForRepoPath(repoPath)
      || (0, sharedState.flowNodeKey)('object', (getNewSessionSelectedObject() || {}).id);
    const sid = data && data.session_id;
    if (sid) {
      assignSessionNodeToParent(sid, parentNodeId);
      return;
    }
    const spawnId = (data && (data.spawn_id || data.pid)) || '';
    rememberPendingNewSessionObjectAssignment(spawnId, parentNodeId);
  }

  function _objectsGet() {
    return fetch('/api/objects', { headers: { 'Accept': 'application/json' } })
      .then(r => (r.ok ? r.json() : null))
      .catch(() => null);
  }

  function _objectsLocalState() {
    let order = {};
    try { order = JSON.parse(localStorage.getItem('ccc-objects-order') || '{}'); } catch (_) {}
    return {
      objects: Array.isArray(sharedState.flowCustomObjects) ? sharedState.flowCustomObjects : [],
      parents: sharedState.flowNodeParents || {},
      order: order || {},
      drafts: Array.isArray(sharedState.flowDraftSessions) ? sharedState.flowDraftSessions : [],
    };
  }

  function mergeServerDraftSessions(serverDrafts, skipDraftIds) {
    const draftById = new Map((sharedState.flowDraftSessions || []).filter(d => d && d.id).map(d => [d.id, d]));
    let changed = false;
    for (const sd of (serverDrafts || [])) {
      if (!sd || !sd.id || (skipDraftIds && skipDraftIds.has(sd.id))) continue;
      const serverDraftNode = (0, sharedState.flowNodeKey)('draft-session', sd.id);
      const serverDraftParent = sd.parent_node_id || '';
      const localDraft = draftById.get(sd.id);
      if (!localDraft) {
        const mergedDraft = Object.assign({ repo_path: '' }, sd);
        sharedState.flowDraftSessions.push(mergedDraft);
        draftById.set(sd.id, mergedDraft);
        changed = true;
      } else {
        if (serverDraftParent && !localDraft.parent_node_id) {
          localDraft.parent_node_id = serverDraftParent;
          changed = true;
        }
        if (sd.prompt
            && sd.prompt !== localDraft.prompt
            && sd.prompt.length > (localDraft.prompt || '').length
            && (!localDraft.prompt || localDraft.prompt === (localDraft.title || ''))) {
          // Self-heal: the local copy has an empty or flattened prompt (prompt
          // never set, or collapsed to the title by an older title-edit) while
          // the server holds a richer one. Adopt it. Guarded so a genuine local
          // edit (prompt that differs from the title) is never clobbered.
          localDraft.prompt = sd.prompt;
          changed = true;
        }
      }
      if (serverDraftParent && !sharedState.flowNodeParents[serverDraftNode]) {
        sharedState.flowNodeParents[serverDraftNode] = serverDraftParent;
        changed = true;
      }
    }
    return changed;
  }

  async function _reconcileObjectsToServer() {
    if (sharedState._objectsSyncInFlight) { sharedState._objectsSyncQueued = true; return; }
    sharedState._objectsSyncInFlight = true;
    try {
      const local = _objectsLocalState();
      await _objectsApiPost('import', local);            // upserts objects/parents/order
      const server = await _objectsGet();                // diff to find removals
      if (server) {
        const localIds = new Set(local.objects.map(o => o && o.id).filter(Boolean));
        for (const so of (server.objects || [])) {
          if (so && so.id && !localIds.has(so.id)) {
            await _objectsApiPost('delete', { id: so.id }).catch(() => {});
          }
        }
        const localParents = new Set(Object.keys(local.parents || {}));
        for (const sid of Object.keys(server.parents || {})) {
          if (!localParents.has(sid)) {
            await _objectsApiPost('unassign', { session_node_id: sid }).catch(() => {});
          }
        }
        const deletedDraftIds = loadDeletedFlowDraftSessionIds();
        const clearedDraftIds = new Set();
        const serverDraftIds = new Set((server.drafts || []).map(d => d && d.id).filter(Boolean));
        deletedDraftIds.forEach(id => { if (!serverDraftIds.has(id)) clearedDraftIds.add(id); });
        for (const sd of (server.drafts || [])) {
          if (sd && sd.id && deletedDraftIds.has(sd.id)) {
            await _objectsApiPost('draft-delete', { id: sd.id })
              .then(() => { clearedDraftIds.add(sd.id); })
              .catch(() => {});
          }
        }
        if (clearedDraftIds.size) clearDeletedFlowDraftSessionIds(clearedDraftIds);
        if (mergeServerDraftSessions(server.drafts || [], deletedDraftIds)) {
          try { localStorage.setItem('ccc-flow-node-parents', JSON.stringify(sharedState.flowNodeParents)); } catch (_) {}
          try { localStorage.setItem('ccc-flow-draft-sessions', JSON.stringify(sharedState.flowDraftSessions)); } catch (_) {}
          try { (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch ? sharedState.$convSearch.value : '')); } catch (_) {}
        }
      }
    } catch (_) {
      // Offline / server down — localStorage stays source of truth, next change retries.
    } finally {
      sharedState._objectsSyncInFlight = false;
      if (sharedState._objectsSyncQueued) { sharedState._objectsSyncQueued = false; syncObjectsToServer(); }
    }
  }

  function syncObjectsToServer() {
    if (sharedState._objectsSyncTimer) clearTimeout(sharedState._objectsSyncTimer);
    sharedState._objectsSyncTimer = setTimeout(() => { sharedState._objectsSyncTimer = null; _reconcileObjectsToServer(); }, 400);
  }

  function isArchivedFlowObjectId(id) {
    const obj = (sharedState.flowCustomObjects || []).find(o => o && o.id === id);
    return !!(obj && obj.archived);
  }

  function stopFlowNodeInspector() {
    sharedState._flowInspectorState = null;
    sharedState._flowInspectorHiddenInputEls.forEach(item => {
      if (item && item.el) item.el.style.display = item.display || '';
    });
    sharedState._flowInspectorHiddenInputEls = [];
  }

  function stopGroupChatReader(opts = {}) {
    if (sharedState._gcReaderInterval) { clearInterval(sharedState._gcReaderInterval); sharedState._gcReaderInterval = null; }
    sharedState._gcReaderGeneration += 1;  // kill any mid-flight backoff chain
    sharedState._gcReaderPath = null;
    sharedState._gcReaderId = null;
    sharedState._gcReaderTopic = '';
    sharedState._gcReaderMode = 'topic';
    sharedState._gcLastMtime = null;
    sharedState._gcPollFailCount = 0;
    if (sharedState._gcReaderHiddenInputBar) {
      const inputBar = document.getElementById('convInputBar');
      const inputCtx = document.getElementById('convInputContext');
      if (inputBar) inputBar.style.display = '';
      if (inputCtx) inputCtx.style.display = '';
      sharedState._gcReaderHiddenInputBar = false;
    }
    if (sharedState._gcReaderHiddenRailItems) {
      const rail = document.getElementById('statusRail');
      if (rail) {
        const filesPanel = rail.querySelector('#filesPanel');
        if (filesPanel) filesPanel.style.display = '';
        const railActions = rail.querySelector('#railActions');
        if (railActions) railActions.style.display = '';
        const orchPanel = document.getElementById('gcOrchestratorPanel');
        if (orchPanel) orchPanel.style.display = 'none';
      }
      sharedState._gcReaderHiddenRailItems = false;
    }
    if (opts && opts.rerenderSidebar && typeof hostServices.renderSidebar === 'function'
        && typeof sharedState.filterConversations === 'function'
        && typeof sharedState.$convSearch !== 'undefined' && sharedState.$convSearch) {
      // Suppress the reentrant "restore last view" this cosmetic re-render
      // would otherwise trigger (CCC-508) — it would reopen this same chat
      // from the stale localStorage entry before the caller gets a chance
      // to select whatever it's actually switching to.
      sharedState._suppressRestoreLastView = true;
      try { (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value)); }
      finally { sharedState._suppressRestoreLastView = false; }
    }
  }

  function showClaudePrewarmFallbackRecovery() {
    (0, sharedState.showOpToast)(
      'Started normally. Restart the execution worker to restore fast Claude launches.',
      'info',
      {
        label: 'Restart worker',
        onClick: () => {
          if (typeof sysRestartWorker === 'function') sysRestartWorker({ toastErrors: true });
        },
      },
    );
  }

  function ensureAllConversationEndAffordances() {
    (0, sharedState.conversationScrollViews)().forEach(sharedState.attachConversationEndAffordance);
    (0, sharedState.conversationScrollViews)().forEach(sharedState.updateConversationEndAffordance);
  }

  function captureConversationBottomAnchors() {
    ensureAllConversationEndAffordances();
    return (0, sharedState.conversationScrollViews)().map(view => ({
      view,
      wasAtBottom: (0, sharedState.isConversationAtBottom)(view),
    }));
  }

  function restoreConversationBottomAnchors(anchors) {
    if (!anchors || !anchors.length) {
      ensureAllConversationEndAffordances();
      return;
    }
    const raf = window.requestAnimationFrame || ((fn) => setTimeout(fn, 16));
    const restore = () => {
      for (const anchor of anchors) {
        if (!anchor.view || !anchor.view.isConnected) continue;
        if (anchor.wasAtBottom && anchor.view._pinnedToBottom !== false) (0, sharedState.scrollConversationToEnd)(anchor.view);
        else (0, sharedState.updateConversationEndAffordance)(anchor.view);
      }
      ensureAllConversationEndAffordances();
    };
    raf(() => {
      restore();
      raf(restore);
    });
  }

  function syncBodyGcReaderFlag() {
    const view = document.getElementById('conversationsView');
    const gc = document.getElementById('gcReader');
    const on = !!(view && gc && gc.parentElement === view);
    if (document.body.classList.contains('has-gc-reader') !== on) document.body.classList.toggle('has-gc-reader', on);
  }

  function removeSplitPaneSingletonChrome(paneEl) {
    if (!paneEl) return;
    paneEl.querySelectorAll('.status-rail, .status-rail-restore').forEach(el => el.remove());
    (0, sharedState.syncPaneHasFlags)(paneEl);
  }

  function buildPaneElement(paneId) {
    const tmpl = document.querySelector('.conv-pane[data-pane-id="p1"]');
    const clone = tmpl.cloneNode(true);
    clone.querySelectorAll('[data-context-singleton]').forEach(node => node.remove());
    clone.setAttribute('data-pane-id', paneId);
    clone.removeAttribute('data-presentation-mode');
    clone.removeAttribute('data-presentation-conversation-id');
    clone.classList.remove('has-pane-title', 'has-conv-bg');
    ['data-conv-bg', 'data-conv-bg-key', 'data-conv-id'].forEach(name => clone.removeAttribute(name));
    [
      '--conv-bg', '--conv-surface', '--conv-surface-2', '--conv-border',
      '--conv-text', '--conv-text-muted', '--conv-accent', '--conv-user-bg',
      '--conv-user-text', '--conv-shadow',
    ].forEach(name => clone.style.removeProperty(name));
    // Strip every id in the clone — HTML mandates id uniqueness, and any
    // future code that does getElementById('convInput') etc. would resolve
    // to p1's element, never the clone's. Task 6 will re-find the cloned
    // chrome elements via class/tag selectors scoped to the clone.
    clone.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
    removeSplitPaneSingletonChrome(clone);
    clone.querySelectorAll('.conv-transcript-nav').forEach(el => el.remove());
    const presentationToolbar = clone.querySelector('[data-role="presentation-toolbar"]');
    if (presentationToolbar) {
      presentationToolbar.hidden = true;
      presentationToolbar.querySelectorAll('[data-presentation-mode]').forEach(button => {
        const off = button.dataset.presentationMode === 'off';
        button.classList.toggle('is-active', off);
        button.setAttribute('aria-pressed', off ? 'true' : 'false');
      });
    }
    // Replace the transcript with a Loading… empty state; the dropped
    // conversation will be loaded by selectConversation(id, paneId)
    // immediately after attach.
    const view = clone.querySelector('.conversations-view');
    if (view) {
      view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Loading…</div>';
    }
    const paneCat = clone.querySelector('[data-role="pane-category"]');
    const paneTitle = clone.querySelector('[data-role="pane-title"]');
    if (paneCat) paneCat.textContent = '';
    if (paneTitle) paneTitle.textContent = '';
    const paneSid = clone.querySelector('[data-role="pane-session-id"]');
    if (paneSid) { paneSid.hidden = true; paneSid.innerHTML = ''; delete paneSid.dataset.copyRowSessionId; }
    const paneAvatar = clone.querySelector('[data-role="pane-avatar"]');
    if (paneAvatar) paneAvatar.innerHTML = '';
    const bgPalette = clone.querySelector('[data-role="conv-bg-palette"]');
    if (bgPalette) bgPalette.innerHTML = '';
    (0, sharedState.renderConversationBackgroundPalette)(clone);
    // Cloned nodes lose their listeners — rebuild the row-style picker too.
    try { renderConvRowStylePalettes(); } catch (_) {}
    // In cloned panes, hide the spawn elements that rely on p1-singleton IDs
    // (mountNewSessionChrome moves the real ones in when this pane composes a
    // new session). [data-workspace] stays: renderSessionWorkspaceIntoSticky
    // paints each pane's own strip (CCC-477), and hiding it here left the
    // right pane with no repo path (CCC-1194).
    const ctxBar = clone.querySelector('.conv-input-context');
    if (ctxBar) {
      ['.spawn-cwd-quick-chips', '.spawn-cwd-row', '.spawn-worktree-row'].forEach(sel => {
        ctxBar.querySelectorAll(sel).forEach(el => { el.style.display = 'none'; });
      });
    }
    // Reveal the close button (was inline display:none in p1's static HTML).
    const closeBtn = clone.querySelector('[data-role="pane-close"]');
    if (closeBtn) {
      closeBtn.style.display = '';
      closeBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        closePane(paneId);
      });
    }
    // Wire the cloned input bar to send into this specific pane.
    const sendBtn = clone.querySelector('.send-btn');
    const steerBtn = clone.querySelector('.steer-btn');
    const compactBtn = clone.querySelector('.compact-btn');
    const escBtn = clone.querySelector('.esc-btn');
    const ttsBtn = clone.querySelector('.tts-btn');
    const input = clone.querySelector('.conv-input-bar textarea, .conv-input-bar input[type="text"]');
    if (sendBtn) {
      sendBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        (0, sharedState.sendToTerminal)(paneId);
      });
    }
    // Submit+ (phone mode) and Send-queue are bound by id on p1 only; the
    // clone keeps the buttons but not the listeners, so they did nothing in
    // split view (CCC-1192).
    const submitPlusBtn = clone.querySelector('.submit-plus-btn');
    if (submitPlusBtn) {
      submitPlusBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        (0, sharedState.submitPlus)(paneId);
      });
    }
    // cloneNode drops listeners and ids, so the [+] attach button in a split
    // pane needs its own binding, uploading into this pane's composer.
    (0, sharedState.attachFilePickerButton)(
      clone.querySelector('input[type="file"][multiple]'),
      clone.querySelector('.attach-btn'),
      () => clone.querySelector('.conv-input-bar textarea, .conv-input-bar input[type="text"]'),
    );
    const sendQueueBtn = clone.querySelector('.send-queue-btn');
    if (sendQueueBtn) {
      sendQueueBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        (0, sharedState.sendToTerminal)(paneId, 'send_queue');
      });
    }
    if (steerBtn) {
      steerBtn.classList.remove('visible');
      steerBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        (0, sharedState.sendToTerminal)(paneId, 'steer');
      });
    }
    if (compactBtn) {
      compactBtn.classList.remove('visible');
      compactBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        (0, sharedState.setActivePaneById)(paneId);
        (0, sharedState.compactCurrentSession)();
      });
    }
    if (escBtn) {
      escBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        (0, sharedState.setActivePaneById)(paneId);
        (0, sharedState.sendEscToTerminal)();
      });
    }
    if (ttsBtn) {
      ttsBtn.addEventListener('mousedown', (ev) => ev.preventDefault());
      ttsBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        (0, sharedState.readLastMessageAloud)(paneId);
      });
    }
    if (input) {
      // cloneNode does not copy listeners, so bind paste uploads explicitly
      // for the independent right-hand composer.
      (0, sharedState.attachImagePaste)(input);
      input.addEventListener('input', () => {
        const pane = (0, sharedState.paneByPaneId)(paneId);
        (0, sharedState.rememberInputDraft)(input, pane && pane.conversationId);
        (0, sharedState.refreshSlashCommandMenu)(input);
        (0, sharedState._autosizeTextareaLike)(input, input.closest('.conv-input-bar'));
      });
      input.addEventListener('focus', () => (0, sharedState.refreshSlashCommandMenu)(input));
      input.addEventListener('click', () => (0, sharedState.refreshSlashCommandMenu)(input));
      input.addEventListener('keydown', (ev) => {
        if ((0, sharedState.isImeKey)(ev)) return;
        if ((0, sharedState.handleSlashCommandKeydown)(input, ev)) return;
        if ((0, sharedState.recallLastComposerCommand)(input, ev)) return;
        if (ev.key === 'Enter' && !ev.shiftKey && !(0, sharedState.isTouchPrimary)()) {
          ev.preventDefault();
          if (!(0, sharedState.f2InterceptEnterSend)(paneId)) (0, sharedState.sendToTerminal)(paneId);
        }
      });
    }
    return clone;
  }

  function renderSplitLayout() {
    const $split = document.getElementById('convSplit');
    if (!$split) return;
    (0, sharedState.mountStatusRailForActivePane)();
    const bottomAnchors = captureConversationBottomAnchors();
    if (!sharedState.splitState.orientation || sharedState.splitState.panes.length < 2) {
      $split.setAttribute('data-orientation', '');
      // Drop any stray second pane elements (defensive — should already be 1).
      const extras = $split.querySelectorAll('.conv-pane:not([data-pane-id="p1"])');
      extras.forEach(n => n.remove());
      // Hide close buttons in single mode.
      $split.querySelectorAll('.conv-pane-close').forEach(b => b.style.display = 'none');
      // Remove any stray divider in unsplit mode.
      const oldDivider = $split.querySelector('.conv-split-divider');
      if (oldDivider) oldDivider.remove();
      // Clear the inline `flex: <ratio> 1 0` left over from the divider drag.
      // With a single flex child whose flex-grow < 1, the spec only distributes
      // that fraction of free space — the remainder stays empty, so the pane
      // would render at the dragged ratio (e.g. half height) instead of full.
      $split.querySelectorAll('.conv-pane').forEach(p => { p.style.flex = ''; });
      restoreConversationBottomAnchors(bottomAnchors);
      return;
    }
    $split.setAttribute('data-orientation', sharedState.splitState.orientation);
    // Ensure both panes exist in the DOM in order.
    sharedState.splitState.panes.forEach((p, idx) => {
      let el = $split.querySelector(`.conv-pane[data-pane-id="${p.id}"]`);
      if (!el) {
        el = buildPaneElement(p.id);
        $split.appendChild(el);
      }
      el.querySelectorAll('.conv-pane-close').forEach(b => b.style.display = '');
    });
    // Ensure the divider exists between the two panes.
    let divider = $split.querySelector('.conv-split-divider');
    if (!divider) {
      divider = document.createElement('div');
      divider.className = 'conv-split-divider';
      attachDividerDrag(divider);
    }
    // Reorder: panes[0], divider, panes[1].
    const p0 = $split.querySelector(`.conv-pane[data-pane-id="${sharedState.splitState.panes[0].id}"]`);
    const p1 = $split.querySelector(`.conv-pane[data-pane-id="${sharedState.splitState.panes[1].id}"]`);
    $split.append(p0, divider, p1);
    // Apply ratio.
    p0.style.flex = `${sharedState.splitState.ratio} 1 0`;
    p1.style.flex = `${1 - sharedState.splitState.ratio} 1 0`;
    (0, sharedState.syncActivePaneChrome)();
    (0, sharedState.attachAllPaneDropZones)();
    restoreConversationBottomAnchors(bottomAnchors);
  }

  function attachDividerDrag(divider) {
    let dragging = false;
    let startPos = 0;
    let startRatio = 0.5;
    let containerSize = 0;
    let isVertical = true;
    let bottomAnchors = [];

    divider.addEventListener('pointerdown', (ev) => {
      const $split = document.getElementById('convSplit');
      if (!$split) return;
      isVertical = $split.getAttribute('data-orientation') === 'vertical';
      containerSize = isVertical ? $split.clientWidth : $split.clientHeight;
      if (containerSize <= 0) return;
      dragging = true;
      startPos = isVertical ? ev.clientX : ev.clientY;
      startRatio = sharedState.splitState.ratio;
      bottomAnchors = captureConversationBottomAnchors();
      divider.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    divider.addEventListener('pointermove', (ev) => {
      if (!dragging) return;
      const cur = isVertical ? ev.clientX : ev.clientY;
      const delta = (cur - startPos) / containerSize;
      let next = startRatio + delta;
      next = Math.max(0.15, Math.min(0.85, next));
      sharedState.splitState.ratio = next;
      const p0el = document.querySelector(`.conv-pane[data-pane-id="${sharedState.splitState.panes[0].id}"]`);
      const p1el = document.querySelector(`.conv-pane[data-pane-id="${sharedState.splitState.panes[1].id}"]`);
      if (p0el) p0el.style.flex = `${next} 1 0`;
      if (p1el) p1el.style.flex = `${1 - next} 1 0`;
    });
    divider.addEventListener('pointerup', (ev) => {
      dragging = false;
      restoreConversationBottomAnchors(bottomAnchors);
      bottomAnchors = [];
      try { divider.releasePointerCapture(ev.pointerId); } catch (e) {}
      (0, sharedState.saveSplitState)();
    });
  }

  function closePane(paneId) {
    if (sharedState.splitState.panes.length < 2) return; // can't close the only pane
    const idx = (0, sharedState.paneIndexByPaneId)(paneId);
    if (idx < 0) return;
    const pane = sharedState.splitState.panes[idx];
    // Tear down SSE.
    if (pane.eventSource) {
      try { pane.eventSource.close(); } catch (e) {}
      pane.eventSource = null;
    }
    // Remove the DOM element.
    const el = document.querySelector(`.conv-pane[data-pane-id="${paneId}"]`);
    const survivor = sharedState.splitState.panes.find((_, i) => i !== idx);
    if (survivor && el && (el.contains(document.getElementById('statusRail')) || el.contains(document.getElementById('statusRailRestoreBtn')))) {
      (0, sharedState.mountStatusRailForPaneId)(survivor.id);
    }
    if (el) el.remove();
    // Splice state and collapse.
    sharedState.splitState.panes.splice(idx, 1);
    sharedState.splitState.orientation = null;
    sharedState.splitState.activeIndex = 0; // survivor is now the only pane
    renderSplitLayout();
    (0, sharedState.syncActivePaneChrome)();
    (0, sharedState.saveSplitState)();
  }

  function _sharedQueueHostElement(hostName) {
    return document.getElementById(hostName === 'sidebar' ? 'sidebarQueueHost' : 'statusRailQueueHost');
  }

  function _mountSharedQueuePanel() {
    const panel = document.getElementById('queuePanel');
    const host = _sharedQueueHostElement(sharedState._sharedQueuePanelHost);
    if (!panel || !host) return false;
    if (panel.parentElement !== host) host.appendChild(panel);
    if (sharedState._sharedQueuePanelHost === 'sidebar') (0, hostServices._renderQueuePanel)();
    return true;
  }

  function _setSharedQueuePanelHost(hostName) {
    sharedState._sharedQueuePanelHost = hostName === 'sidebar' ? 'sidebar' : 'rail';
    _mountSharedQueuePanel();
  }

  async function _fetchSystemServices(force) {
    if (!force && Date.now() - sharedState._sysServicesCache.ts < sharedState._SYS_SERVICES_DEDUPE_MS) {
      return sharedState._sysServicesCache;
    }
    if (sharedState._sysServicesPromise) return sharedState._sysServicesPromise;
    sharedState._sysServicesPromise = (async () => {
      try {
        const res = await (0, sharedState.backgroundApiFetch)('/api/system/services', { cache: 'no-store' });
        const data = await res.json();
        if (!res.ok || !data || !Array.isArray(data.services)) {
          throw new Error('bad payload');
        }
        sharedState._sysServicesCache = { ts: Date.now(), payload: data };
        sharedState._sysServicesFailures = 0;
      } catch (_) {
        // Keep the last good payload. One failure is a hiccup; two in a row
        // means the dashboard itself is not answering, which the chip paints
        // red as `unreachable`.
        sharedState._sysServicesFailures += 1;
      } finally { sharedState._sysServicesPromise = null; }
      return sharedState._sysServicesCache;
    })();
    return sharedState._sysServicesPromise;
  }

  function trackSpawnView(card) {
    if (!card || card._spawnViewTiming) return;
    const timing = card._spawnViewTiming = {
      at: Date.now(), hidden: document.hidden, selected: true,
      hiddenMs: 0, unselectedMs: 0,
    };
    const tick = () => {
      const now = Date.now(), elapsed = Math.max(0, now - timing.at);
      if (timing.hidden) timing.hiddenMs += elapsed;
      else if (!timing.selected) timing.unselectedMs += elapsed;
      timing.at = now;
      timing.hidden = document.hidden;
      timing.selected = pendingSpawnIsSelected(card);
      if (!timing.done && now - (card.spawn_requested_at || now) < 180000) {
        timing.timer = setTimeout(tick, 100);
      }
    };
    timing.sample = tick;
    timing.selected = !card.no_auto_select;
    timing.timer = setTimeout(tick, 100);
  }

  function spawnStatsBegin(sid, t0) {
    if (!sid || !(0, sharedState.spawnStatsEnabled)()) return;
    if (!sharedState.spawnStatsT0.has(sid)) sharedState.spawnStatsT0.set(sid, t0 || Date.now());
  }

  function setStatusRailTab(tab) {
    const rail = document.getElementById('statusRail');
    if (!rail) return;
    const queuePane = rail.querySelector('#statusRailQueuePane');
    const next = (tab === 'queue' || tab === 'orchestration' || tab === 'files' || tab === 'ask' || tab === 'log') ? tab : 'metadata';
    if (next === 'ask' && typeof sharedState.askWarmUp === 'function') (0, sharedState.askWarmUp)();
    rail.querySelectorAll('[data-rail-tab]').forEach(btn => {
      const active = btn.getAttribute('data-rail-tab') === next;
      btn.classList.toggle('is-active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    rail.querySelectorAll('[data-rail-pane]').forEach(pane => {
      const active = pane.getAttribute('data-rail-pane') === next;
      pane.classList.toggle('is-active', active);
      pane.hidden = !active;
    });
    if (next === 'queue' && queuePane) {
      _setSharedQueuePanelHost('rail');
      (0, hostServices._renderQueuePanel)();
    } else {
      let sidebarTab = 'inprogress';
      try { sidebarTab = window.CCCNavigation.getTab() || 'inprogress'; } catch (_) {}
      if (sidebarTab === 'queues') _setSharedQueuePanelHost('sidebar');
    }
    try { localStorage.setItem('ccc-status-rail-tab', next); } catch (_) {}
    if (next === 'orchestration' && typeof sharedState.updateOrchestrationPane === 'function') {
      // The pane was hidden until now — lay the lane map out for real.
      requestAnimationFrame(() => (0, sharedState.updateOrchestrationPane)(sharedState.currentConversation));
    }
    if (next === 'log' && typeof refreshRailLogPane === 'function') {
      // Pane was hidden until now (or the user switched back to it) — pull
      // fresh events immediately instead of waiting for the next poll tick.
      refreshRailLogPane();
    }
  }

  function _isSuccessfulHeartbeat(ev) {
    return String(ev.verb || '').toUpperCase() === 'BEAT'
      && /^ok(?:\s+\(\d+(?:\.\d+)?s\))?\s*$/i.test(String(ev.detail || '').trim());
  }

  function _isRoutineInjectEvent(ev) {
    const verb = String(ev.verb || '').toUpperCase();
    if (String(ev.category || '').toLowerCase() !== 'inject') return false;
    if (verb === 'INJECT' || verb === 'REQUEST' || verb === 'DEDUPE') return true;
    // A quoted message preview can contain words like receipt=delivered.
    // Only the metadata before text= describes the transport outcome.
    const detail = String(ev.detail || '').split(/(?:^|\s)text=/, 1)[0];
    if (verb === 'UDS-SKIP') {
      const reason = (detail.match(/(?:^|\s)reason=([^\s]+)/) || [])[1];
      return reason === 'no_socket_path' || reason === 'slash_command_needs_fifo';
    }
    if (verb === 'UDS') {
      const receipt = (detail.match(/(?:^|\s)receipt=([^\s]+)/) || [])[1];
      return receipt === 'delivered' || receipt === 'queued';
    }
    return false;
  }

  function _railLogPaneVisible() {
    const pane = document.getElementById('statusRailLogPane');
    return !!(pane && pane.classList.contains('is-active'));
  }

  function _renderRailLogPane() {
    const body = document.getElementById('railLogBody');
    if (!body) return;
    const needle = sharedState._railLogFilter.trim().toLowerCase();
    const events = sharedState._railLogEvents.filter(ev => {
      if (!sharedState._railLogShowHeartbeats && _isSuccessfulHeartbeat(ev)) return false;
      if (!sharedState._railLogShowInjects && _isRoutineInjectEvent(ev)) return false;
      const presentation = (0, sharedState._readableLogPresentation)(ev);
      if (presentation.hidden) return false;
      if (sharedState._railLogOnlyAttention && !['error', 'warning'].includes(presentation.level)) return false;
      return !needle || [presentation.headline, presentation.origin, ev.ts,
        ev.category, ev.verb, ev.detail, (0, sharedState._activityLogTimestampLocal)(ev.ts), (0, sharedState._readableLogTime)(ev.ts)]
        .some(value => String(value || '').toLowerCase().includes(needle));
    });
    if (!events.length) {
      body.innerHTML = '<div class="activity-log-' + (sharedState._railLogLoadError ? 'error' : 'empty') + '">'
        + (0, sharedState.escapeHtml)(sharedState._railLogEvents.length ? 'No events match the filter.' : sharedState._railLogEmptyMessage)
        + '</div>';
      _syncRailLogToggleAll();
      return;
    }
    // Newest first for reading — the API returns oldest-first (tail order).
    (0, sharedState._renderReadableActivityLog)(body, events);
    _syncRailLogToggleAll();
  }

  function _syncRailLogToggleAll() {
    const button = document.getElementById('railLogToggleAll');
    const body = document.getElementById('railLogBody');
    if (!button || !body) return;
    const entries = [...body.querySelectorAll('.activity-log-entry')];
    const open = entries.length > 0 && entries.every(entry => entry.open);
    button.textContent = open ? 'Collapse all' : 'Expand all';
    button.setAttribute('aria-label', (open ? 'Collapse' : 'Expand') + ' all visible log entries');
    button.disabled = !entries.length;
  }

  function _railLogContextKey() {
    if (sharedState._railLogScope !== 'session') return 'all';
    const sid = typeof sharedState.currentConversation === 'string' && sharedState.currentConversation !== '__new__' ? sharedState.currentConversation : '';
    return 'session:' + sid;
  }

  function _setRailLogMessage(message, error = false) {
    sharedState._railLogEvents = [];
    sharedState._railLogEmptyMessage = message;
    sharedState._railLogLoadError = error;
    _renderRailLogPane();
  }

  async function refreshRailLogPane() {
    const body = document.getElementById('railLogBody');
    if (!body) return;
    const key = _railLogContextKey();
    if (sharedState._railLogRequest && sharedState._railLogRequest.key === key) return;
    sharedState._railLogRequest?.controller.abort();
    sharedState._railLogRequest = null;
    if (sharedState._railLogContext !== key) {
      sharedState._railLogContext = key;
      _setRailLogMessage('Loading…');
    }
    let qs = '?limit=300';
    let emptyHint = 'No activity-log entries yet.';
    if (key !== 'all') {
      const sid = key.slice('session:'.length);
      if (!sid) {
        _setRailLogMessage('Select a session to scope the log, or switch to All.');
        return;
      }
      qs = '?session_id=' + encodeURIComponent(sid) + '&limit=300';
      emptyHint = 'No activity-log entries for this session yet.';
    }
    const request = { key, controller: new AbortController() };
    sharedState._railLogRequest = request;
    const isCurrent = () => sharedState._railLogRequest === request && _railLogContextKey() === key && _railLogPaneVisible();
    try {
      const res = await fetch('/api/activity-log' + qs, { cache: 'no-store', signal: request.controller.signal });
      const data = await res.json();
      if (!isCurrent()) return;
      if (!res.ok || !data || !data.ok) {
        _setRailLogMessage('Could not load the activity log.', true);
        return;
      }
      sharedState._railLogEvents = Array.isArray(data.events) ? data.events : [];
      sharedState._railLogEmptyMessage = emptyHint;
      sharedState._railLogLoadError = false;
      _renderRailLogPane();
    } catch (e) {
      if (isCurrent()) _setRailLogMessage('Could not load the activity log.', true);
    } finally {
      if (sharedState._railLogRequest === request) {
        sharedState._railLogRequest = null;
        // A conversation can change without a scope-button click.
        if (_railLogPaneVisible() && _railLogContextKey() !== key) refreshRailLogPane();
      }
    }
  }

  function publishVercelProgress(repoPath, deployment) {
    window.dispatchEvent(new CustomEvent('ccc-vercel-progress', {detail:{repoPath, deployment}}));
  }

  function setDeployPill({ dotClass, label, title, href }) {
    if (sharedState.$deployDot) sharedState.$deployDot.className = 'deploy-dot' + (dotClass ? ' ' + dotClass : '');
    if (sharedState.$deployLabel) sharedState.$deployLabel.textContent = label;
    if (sharedState.$deployPill) {
      sharedState.$deployPill.title = title;
      if (href) {
        sharedState.$deployPill.setAttribute('href', href);
        sharedState.$deployPill.style.cursor = 'pointer';
      } else {
        sharedState.$deployPill.removeAttribute('href');
        sharedState.$deployPill.style.cursor = 'default';
      }
    }
  }

  async function pollVercelDeploy() {
    const repoPath = (0, sharedState.activeConvRepoPath)();
    const request = ++sharedState._vercelProgressRequest;
    if (repoPath !== sharedState._vercelProgressRepo) {
      sharedState._vercelProgressRepo = repoPath;
      publishVercelProgress(repoPath, null);
    }
    if (!repoPath) {
      setDeployPill({
        dotClass: '',
        label: 'Vercel',
        title: 'Pick a repo to check Vercel deploy status.',
        href: '',
      });
      sharedState._vercelDeployUrl = '';
      return;
    }
    try {
      const res = await (0, sharedState.backgroundApiFetch)((0, sharedState.repoUrl)('/api/vercel-deploy', repoPath));
      const d = await res.json();
      if (request !== sharedState._vercelProgressRequest || repoPath !== (0, sharedState.activeConvRepoPath)()) return;
      publishVercelProgress(repoPath, res.ok && !d.error && !d.disabled ? d : null);
      if (d.disabled) {
        // No .vercel/project.json (and no $VERCEL_PROJECT env override).
        // Friendly hint moved to the pill's title attribute (hover).
        setDeployPill({
          dotClass: '',
          label: 'Vercel',
          title: 'Vercel deploy: not configured. Run `vercel link` in this project to enable.',
          href: '',
        });
        sharedState._vercelDeployUrl = '';
        return;
      }
      if (d.error) {
        setDeployPill({ dotClass: '', label: 'Vercel', title: 'Vercel deploy: ' + d.error, href: '' });
        sharedState._vercelDeployUrl = '';
        return;
      }
      const st = d.state || 'UNKNOWN';
      const stLower = st === 'READY' ? 'ready' : st === 'ERROR' || st === 'CANCELED' ? 'error' : st === 'BUILDING' || st === 'INITIALIZING' || st === 'QUEUED' ? 'building' : '';

      const age = d.created_at ? (0, sharedState.timeAgo)(d.created_at) : '';
      const dur = d.duration_s ? d.duration_s + 's' : '';
      const commitInfo = d.commit_sha ? d.commit_sha + (d.commit_ref ? ' (' + d.commit_ref + ')' : '') : '';
      const link = d.url ? 'https://' + d.url : '';
      sharedState._vercelDeployUrl = link;

      let titleParts = ['Vercel deploy: ' + st];
      if (age) titleParts.push(age);
      if (dur) titleParts.push(dur);
      if (commitInfo) titleParts.push(commitInfo);
      if (d.commit_msg) titleParts.push(d.commit_msg);
      if (link) titleParts.push(link);
      setDeployPill({
        dotClass: stLower,
        label: st,
        title: titleParts.join(' · '),
        href: link,
      });
      // Also update the compact kanban toolbar indicator
      const $kptDeploy = document.getElementById('kptDeployStatus');
      if ($kptDeploy) {
        const dotColor = stLower === 'ready' ? 'var(--green)' : stLower === 'error' ? 'var(--red)' : stLower === 'building' ? '#FBCA04' : 'var(--text-muted)';
        let khtml = '<span style="width:6px;height:6px;border-radius:50%;background:' + dotColor + ';display:inline-block;"></span>';
        khtml += '<span>' + st + '</span>';
        if (age) khtml += '<span style="opacity:0.6;"> &middot; ' + age + '</span>';
        if (link) khtml += '<a href="' + link + '" target="_blank" style="color:var(--accent);text-decoration:none;"> ↗</a>';
        $kptDeploy.innerHTML = khtml;
      }
    } catch {
      if (request === sharedState._vercelProgressRequest) publishVercelProgress(repoPath, null);
    }
  }

  function spawnUsesLogPlaceholder(engine) {
    return engine === 'codex' || engine === 'gemini' || engine === 'cursor' || engine === 'antigravity' || engine === 'kilo' || engine === 'hermes' || engine === 'opencode' || engine === 'devin';
  }

  function sysSetState(id, on, text) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = text;
    el.classList.toggle('on', on === true);
    el.classList.toggle('off', on === false);
    el.classList.remove('is-busy');
  }

  function relDuration(seconds, coarse) {
    const s = Math.max(0, Math.round(Number(seconds) || 0));  // clock skew -> just now
    if (s < 45) return 'just now';
    if (s < 90) return '1m ago';
    if (s < 3600) return Math.round(s / 60) + 'm ago';
    if (s < 86400) {
      const h = Math.floor(s / 3600);
      const m = coarse ? 0 : Math.floor((s % 3600) / 60);
      return h + 'h' + (m ? ' ' + m + 'm' : '') + ' ago';
    }
    if (s < 604800) {
      const d = Math.floor(s / 86400);
      const h = coarse ? 0 : Math.floor((s % 86400) / 3600);
      return d + 'd' + (h ? ' ' + h + 'h' : '') + ' ago';
    }
    return Math.floor(s / 86400) + 'd ago';
  }

  function absStamp(unix) {
    const d = new Date(Number(unix) * 1000);
    const sameYear = d.getFullYear() === new Date().getFullYear();
    const date = d.toLocaleDateString(undefined, sameYear
      ? { month: 'short', day: 'numeric' }
      : { month: 'short', day: 'numeric', year: 'numeric' });
    const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    return date + ' at ' + time;
  }

  function formatStarted(svc) {
    if (!svc) return 'Checking start time';
    if (svc.state === 'offline') return 'Not running';
    if (!svc.started_at) {
      // The Codex app-server reports uptime but no absolute start.
      if (svc.uptime_s != null) return 'Started about ' + relDuration(svc.uptime_s, true);
      return 'Start time not reported';
    }
    const approx = !!svc.started_at_approx;
    const rel = relDuration(Date.now() / 1000 - svc.started_at, approx);
    return 'Started ' + (approx ? 'about ' : '') + rel
      + ', ' + absStamp(svc.started_at);
  }

  function _plural(n, one, many) { return Number(n) === 1 ? one : many; }

  function sysDashBusySentence(svc) {
    if (svc.restart_delay_retries) {
      return 'Dashboard restart is still pending after ' + svc.restart_delay_retries + ' attempts.';
    }
    if (svc.restart_blocking) {
      return 'Busy: a Kimi turn is owned by this dashboard. A restart is '
        + 'refused until it finishes.';
    }
    const n = Number(svc.busy_count || 0);
    if (n > 0) {
      return 'Busy: ' + n + ' ' + _plural(n, 'turn is', 'turns are')
        + ' running inside the dashboard right now.'
        + _plural(n, ' Restarting would interrupt it.', ' Restarting would interrupt them.');
    }
    return 'Idle. Restarting is cheap: agent turns keep running inside the '
      + 'worker, and this page reloads itself in a few seconds.';
  }

  function sysWorkerBusySentence(svc) {
    let text;
    if (svc.state === 'offline') {
      text = 'Not running. Queued work is not being dispatched.';
    } else if (svc.state === 'degraded') {
      text = 'Running but not answering health checks. A restart usually clears it.';
    } else if (svc.restart_delay_retries) {
      text = 'Worker restart is still pending after ' + svc.restart_delay_retries + ' attempts.';
    } else if (Number(svc.active || 0) + Number(svc.queued || 0) > 0) {
      text = 'Busy: ' + Number(svc.active || 0) + ' running, '
        + Number(svc.queued || 0) + ' queued. Restarting now marks running '
        + 'queue items "needs reconciliation", and you clear them with one '
        + 'click on Reconcile.';
    } else {
      text = 'Idle. Nothing is running. Restarting now is safe and takes a few seconds.';
    }
    const uncertain = Number(svc.uncertain || 0);
    if (uncertain > 0 && svc.uncertain_stale) {
      text += ' ' + uncertain + ' '
        + _plural(uncertain, 'item already needs reconciliation.',
                  'items already need reconciliation.');
    } else if (uncertain > 0) {
      text += ' ' + uncertain + ' '
        + _plural(uncertain, 'item is settling after the restart',
                  'items are settling after the restart')
        + ' -- clears on its own within about 20 minutes.';
    }
    if (svc.drain_enabled) text += ' Dispatch is paused, so nothing new starts.';
    if (svc.version_stale) {
      text += ' This worker is running older code than the dashboard. '
        + 'Restart it to pick up the current server.py.';
    }
    return text;
  }

  function sysWtBusySentence(svc) {
    const workers = Number(svc.workers_live || 0);
    let text;
    if (!svc.installed) {
      return 'WatchTower is not installed on this machine. Queue tracking is off.';
    }
    if (svc.state === 'offline') {
      text = 'Not running. Queues are not being drained and worker health is '
        + 'not being tracked. Agent sessions keep working.';
    } else if (svc.state === 'degraded') {
      text = 'Running but the API on port ' + (svc.port || 8787) + ' is not '
        + 'answering. Queue data on this page may be stale. A restart usually '
        + 'clears it.';
    } else if (workers > 0) {
      text = 'Busy: ' + workers + ' queue ' + _plural(workers, 'worker is', 'workers are')
        + ' live. Restarting now stops them mid task and pauses draining '
        + 'until it is back.';
    } else {
      text = 'Idle. No queue worker is running, so restarting now is safe; '
        + 'draining pauses for a few seconds.';
    }
    const stuck = Number(svc.stuck_total || 0);
    if (stuck > 0) {
      text += ' ' + stuck + ' ' + _plural(stuck, 'queue is stuck.', 'queues are stuck.');
    }
    return text;
  }

  function computeServerRollup(payload) {
    const reasons = [];
    if (sharedState._sysServicesFailures >= 2 || (!payload && sharedState._sysFirstAttemptAt
        && Date.now() - sharedState._sysFirstAttemptAt > 10000)) {
      return {
        level: 'red', verdict: 'no answer', unreachable: true,
        count: 0, reasons: [], critical: false, down: [],
      };
    }
    if (!payload) {
      return {
        level: 'unknown', verdict: 'checking', count: 0, reasons: [],
        critical: false, down: [],
      };
    }
    const map = (0, sharedState._sysServiceMap)(payload);
    const dash = map.get('dashboard') || {};
    const worker = map.get('worker') || {};
    const wt = map.get('watchtower') || {};
    const appServer = map.get('app_server') || {};
    const wtCounts = wt.installed !== false;

    if (worker.state === 'offline') reasons.push('Execution worker is offline');
    if (worker.state === 'degraded') reasons.push('Execution worker is degraded');
    if (dash.state === 'degraded') reasons.push('Dashboard is reporting errors');
    if (worker.restart_delay_retries) reasons.push('Worker restart is still pending');
    if (dash.restart_delay_retries) reasons.push('Dashboard restart is still pending');
    if (wtCounts && wt.state === 'offline') reasons.push('WatchTower server is not running');
    if (wtCounts && wt.state === 'degraded') reasons.push('WatchTower server is degraded');
    const stuck = wtCounts ? Number(wt.stuck_total || 0) : 0;
    if (stuck > 0) {
      reasons.push(stuck + ' ' + _plural(stuck, 'queue stuck', 'queues stuck'));
    }
    // A worker restart marks in-flight work "uncertain" on purpose -- that
    // alone self-clears within a couple of sweep cycles and is not an
    // operator problem. Only flag it once it has survived a full sweep past
    // the retirement window (uncertain_stale), same signal the backend uses
    // to decide the item is actually stuck.
    const uncertain = Number(worker.uncertain || 0);
    if (uncertain > 0 && worker.uncertain_stale) {
      reasons.push(uncertain + ' ' + _plural(uncertain, 'item needs', 'items need')
        + ' reconciliation');
    }
    if (worker.drain_enabled) reasons.push('dispatch is paused');
    if (worker.version_stale) reasons.push('worker is running older code');
    // Crash-loop blindness: every service is KeepAlive-managed, so a process
    // that dies every 90 seconds reads "online, started just now" forever. The
    // backend counts distinct starts it has observed in a 10-minute window.
    for (const [svc, name] of [[worker, 'Execution worker'], [wt, 'WatchTower server']]) {
      if (!svc || (svc === wt && !wtCounts)) continue;
      const starts = Number(svc.starts_10m || 0);
      const limit = Number(svc.flap_threshold || 3);
      if (starts >= limit) {
        reasons.push(name + ' started ' + starts + ' times in 10 minutes');
      }
    }
    if (sharedState._sysRestartInFlight) reasons.push('restart in progress');
    if (Number(appServer.consecutive_liveness_misses || 0) > 0) {
      reasons.push('Codex app-server missed a liveness probe');
    }
    // The kap daemon is an opt-in routing dependency: with the flag off its
    // absence is normal, and even with it on, prompts fall back to ACP — a
    // downgrade, never an outage. Yellow-level reasons only; kap never joins
    // the `tracked` red-vote list below.
    const kap = map.get('kimi_kap') || {};
    if (kap.kap_enabled && kap.state === 'offline') {
      reasons.push('Kimi kap server is not running');
    }
    if (kap.kap_enabled && kap.state === 'degraded') {
      reasons.push('Kimi kap server is degraded');
    }

    const tracked = [dash, worker].concat(wtCounts ? [wt] : []);
    const notOnline = tracked.filter(s => s.state !== 'online');
    const workerDown = worker.state === 'offline';
    const red = workerDown || dash.state === 'offline' || notOnline.length >= 2;
    // One unit for the count badge in every non-green state: the number of
    // distinct things needing attention. It used to mean "reasons" when amber
    // and "services down" when red, so the same "3" meant two different
    // things depending on the colour.
    if (red) {
      return {
        level: 'red', verdict: 'down', count: reasons.length,
        reasons, critical: workerDown, down: notOnline,
      };
    }
    if (reasons.length) {
      return {
        level: 'yellow', verdict: 'attention', count: reasons.length,
        reasons, critical: false, down: [],
      };
    }
    return {
      level: 'green', verdict: 'online', count: 0, reasons: [],
      critical: false, down: [], tracked: tracked.length,
    };
  }

  function _sysCountLabel(n) { return n > 99 ? '99+' : String(n); }

  function paintServerChip(rollup) {
    if (!sharedState.$sysChip) return;
    sharedState.$sysChip.classList.remove('is-green', 'is-yellow', 'is-red', 'is-unknown', 'is-critical');
    sharedState.$sysChip.classList.add('is-' + rollup.level);
    if (rollup.critical) sharedState.$sysChip.classList.add('is-critical');
    if (sharedState.$sysChipVerdict) sharedState.$sysChipVerdict.textContent = rollup.verdict;
    // The chip itself is hidden; its signal rides the "..." menu LED and a
    // corner dot on the collapsed button so a degraded service is still
    // noticeable without opening the menu.
    const $moreLed = document.getElementById('sidebarMoreStatusLed');
    if ($moreLed) {
      $moreLed.classList.remove('is-green', 'is-yellow', 'is-red', 'is-unknown');
      $moreLed.classList.add('is-' + rollup.level);
    }
    const $moreBtn = document.getElementById('sidebarMoreBtn');
    if ($moreBtn) {
      $moreBtn.classList.toggle('has-alert', rollup.level === 'yellow' || rollup.level === 'red');
      $moreBtn.classList.toggle('is-red', rollup.level === 'red');
    }
    if (sharedState.$sysChipCount) {
      const show = rollup.level !== 'green' && rollup.count > 0;
      sharedState.$sysChipCount.hidden = !show;
      sharedState.$sysChipCount.textContent = show ? _sysCountLabel(rollup.count) : '';
    }
    // aria-label wins over the child text, so it must always carry the
    // verdict: a screen-reader user hears the answer before activating.
    let label;
    if (rollup.unreachable) {
      label = 'Server status: the dashboard is not answering. Open system status.';
    } else if (rollup.level === 'unknown') {
      label = 'Server status: checking. Open system status.';
    } else if (rollup.level === 'green') {
      // Do not claim three services when WatchTower is not installed.
      label = 'Server status: '
        + (rollup.tracked === 3
          ? 'all three services are online.'
          : 'the dashboard and the execution worker are online.')
        + ' Open system status.';
    } else {
      label = 'Server status: '
        + (rollup.level === 'red' ? 'something is down. ' : 'needs attention. ')
        + rollup.reasons.slice(0, 3).join(', ') + '. Open system status.';
    }
    sharedState.$sysChip.setAttribute('aria-label', label);
    sharedState.$sysChip.title = label;
  }

  function paintSysRollup(rollup) {
    if (!sharedState.$sysRollup) return;
    sharedState.$sysRollup.classList.remove('is-green', 'is-yellow', 'is-red', 'is-unknown');
    sharedState.$sysRollup.classList.add('is-' + rollup.level);
    let text;
    if (rollup.unreachable) text = 'Dashboard is not answering';
    else if (rollup.level === 'unknown') text = 'Checking services';
    else if (rollup.level === 'green') {
      // A not-installed WatchTower is excluded from the rollup, so "All
      // services online" would sit directly above a row reading "offline".
      // Name the two that are actually being vouched for.
      text = rollup.tracked === 3
        ? 'All services online'
        : 'Dashboard and worker online';
    }
    else if (rollup.level === 'red') {
      // Name the service when exactly one is down. "1 service is down" makes
      // someone open the panel to learn a word we already had.
      const down = rollup.down || [];
      text = down.length === 1
        ? ((down[0].label || 'A service') + ' is down')
        : (down.length + ' services are down');
    } else {
      text = rollup.count + ' '
        + _plural(rollup.count, 'thing needs attention', 'things need attention');
    }
    if (sharedState.$sysRollupText) sharedState.$sysRollupText.textContent = text;
    // Announce the verdict only when it changes; the panel polls every 5s.
    if (sharedState.$sysModal && sharedState.$sysModal.classList.contains('open')
        && rollup.level !== sharedState._sysLastRollupLevel && !sharedState._sysRestartInFlight) {
      (0, sharedState.sysSay)(text);
    }
    sharedState._sysLastRollupLevel = rollup.level;
  }

  function _sysStateFlag(state) {
    if (state === 'online') return true;
    if (state === 'offline') return false;
    return null;  // degraded / idle / unknown read as neutral
  }

  function renderSysRow(id, svc) {
    const ids = sharedState.SYS_ROWS[id];
    if (!ids) return;
    const row = document.getElementById(ids.row);
    const state = (svc && svc.state) || 'unknown';
    if (row) {
      row.classList.remove('is-checking', 'is-online', 'is-degraded', 'is-offline', 'is-idle');
      row.classList.add(svc ? 'is-' + state : 'is-checking');
    }
    if (sharedState._sysRestartInFlight === id) {
      // The 5s panel poll runs straight through a restart. Do not let it
      // repaint the pill back to the pre-restart verdict mid-flight.
      sysSetRestartingPill(id);
    } else {
      sysSetState(ids.state, _sysStateFlag(state), sharedState.SYS_STATE_PILL[state] || 'checking');
    }
    // The explainer is hidden by CSS while a row is online, so hover on the
    // row name has to carry it. Nothing is lost, it is just quiet at rest.
    const nameEl = row && row.querySelector('.sys-name');
    const detailEl = ids.detail && document.getElementById(ids.detail);
    if (nameEl && detailEl && !nameEl.title) nameEl.title = detailEl.textContent || '';
    const started = document.getElementById(ids.started);
    if (started) {
      started.textContent = svc ? formatStarted(svc) : 'Checking start time';
      // Say where an approximate number came from rather than hiding it.
      started.title = (svc && svc.started_at_approx)
        ? 'Derived from the WatchTower daemon pid file' : '';
    }
    const busy = document.getElementById(ids.busy);
    if (busy) {
      if (!svc) busy.textContent = 'Checking activity';
      else if (id === 'dashboard') busy.textContent = sysDashBusySentence(svc);
      else if (id === 'worker') busy.textContent = sysWorkerBusySentence(svc);
      else busy.textContent = sysWtBusySentence(svc);
    }
    (0, sharedState.sysPaintRestartButton)(id, svc);
    sysPaintSvcMeta(id, svc);
    if (id === 'watchtower' && typeof sharedState._wtReleasedRender === 'function') (0, sharedState._wtReleasedRender)(svc);
  }

  function sysPaintSvcMeta(id, svc) {
    const ids = sharedState.SYS_ROWS[id];
    const el = ids && document.getElementById(ids.meta);
    if (!el) return;
    el.textContent = '';
    if (!svc) return;
    const bits = [];
    if (svc.pid) bits.push('pid ' + svc.pid);
    if (svc.version) bits.push('v' + svc.version);
    if (svc.port) bits.push('port ' + svc.port);
    if (Number(svc.recent_errors || 0) > 0) {
      const n = Number(svc.recent_errors);
      bits.push(n + ' recent ' + _plural(n, 'error', 'errors'));
    }
    for (const bit of bits) {
      if (el.childNodes.length) el.appendChild(document.createTextNode(' · '));
      el.appendChild(document.createTextNode(bit));
    }
    // A KeepAlive-managed service that keeps dying reads "online, started just
    // now" forever. Say the count out loud when it crosses the flap line.
    const starts = Number(svc.starts_10m || 0);
    if (starts >= Number(svc.flap_threshold || 3)) {
      if (el.childNodes.length) el.appendChild(document.createTextNode(' · '));
      const warn = document.createElement('span');
      warn.className = 'sys-meta-warn';
      warn.textContent = 'started ' + starts + ' times in the last 10 minutes';
      el.appendChild(warn);
    }
  }

  function renderSysAppServerRow(svc, workerSvc) {
    const row = document.getElementById('sysRowAppServer');
    const meta = document.getElementById('sysAppServerDetail');
    const thr = document.getElementById('sysWedgedThreshold');
    const state = (svc && svc.state) || 'unknown';
    if (row) {
      row.classList.remove('is-checking', 'is-online', 'is-degraded', 'is-offline', 'is-idle');
      row.classList.add(svc ? 'is-' + state : 'is-checking');
    }
    const pill = { online: 'on', degraded: 'degraded', offline: 'off', idle: 'idle' };
    sysSetState('sysAppServerState', _sysStateFlag(state), pill[state] || 'checking');
    if (thr && svc && svc.miss_threshold) thr.textContent = ' ' + svc.miss_threshold + '×';
    if (!meta) return;
    let text = 'Shared by every Codex session. CCC probes it and replaces it '
      + 'if it stops answering.';
    if (svc && state === 'offline' && workerSvc && workerSvc.state === 'offline') {
      text = 'The worker is not running, so there is no app-server.';
    } else if (svc && state === 'idle') {
      text += ' Not started yet; it starts on the first Codex request.';
    }
    const misses = Number((svc && svc.consecutive_liveness_misses) || 0);
    if (misses > 0) {
      text += ' Missed ' + misses + ' of ' + ((svc && svc.miss_threshold) || '?')
        + ' liveness probes.';
    }
    meta.textContent = text;
  }

  function renderSysKapRow(svc) {
    const row = document.getElementById('sysRowKap');
    if (!row) return;
    if (!svc || !svc.relevant) { row.style.display = 'none'; return; }
    row.style.display = '';
    const state = svc.state || 'unknown';
    row.classList.remove('is-checking', 'is-online', 'is-degraded', 'is-offline', 'is-idle');
    row.classList.add('is-' + state);
    sysSetState('sysKapState', _sysStateFlag(state), sharedState.SYS_STATE_PILL[state] || 'checking');
    const started = document.getElementById('sysKapStarted');
    if (started) {
      started.textContent = svc.pid
        ? formatStarted(svc)
        : (state === 'offline' ? 'No kap daemon is running' : '');
    }
    const busy = document.getElementById('sysKapBusy');
    if (busy) {
      let text = '';
      if (state === 'online') {
        text = 'Routing target for kap-driven Kimi sessions.';
      } else if (state === 'offline') {
        text = 'kap routing is on but no daemon is live - Kimi sessions fall back to the ACP transport. Start one with `kimi web`.';
      } else if (state === 'degraded') {
        if (svc.pinned) {
          text = 'CCC_KIMI_KAP_SERVER pins a daemon that is not currently live.';
        } else if (!svc.token_present && svc.instances > 0) {
          text = 'The daemon is up but ~/.kimi-code/server.token is missing, so CCC cannot authenticate.';
        } else if (svc.instances > 1) {
          text = svc.instances + ' live daemons are registered; routing picks the newest heartbeat, which can be the TUI\u2019s embedded server. Pin one with CCC_KIMI_KAP_SERVER=<port>.';
        } else {
          text = 'A registered daemon has a stale heartbeat or a dead pid - restart `kimi web`.';
        }
      } else if (state === 'idle') {
        text = svc.kap_enabled
          ? ''
          : 'kap routing is off, so the daemon is not in use. Enable with CCC_KIMI_KAP=1.';
      }
      busy.textContent = text;
    }
    const meta = document.getElementById('sysKapMeta');
    if (meta) {
      const bits = [];
      if (svc.pid) bits.push('pid ' + svc.pid);
      if (svc.version) bits.push('v' + svc.version);
      if (svc.port) bits.push('port ' + svc.port);
      if (svc.pinned) bits.push('pinned via CCC_KIMI_KAP_SERVER');
      if (svc.instances > 1) bits.push(svc.instances + ' live daemons');
      meta.textContent = bits.join(' · ');
    }
  }

  function _sysFmtCountdown(msLeft) {
    if (msLeft <= 0) return 'expiring…';
    const s = Math.ceil(msLeft / 1000);
    if (s < 60) return s + 's';
    return Math.floor(s / 60) + 'm ' + (s % 60) + 's';
  }

  function _sysParseSpawnedTimestamp(raw) {
    const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})$/.exec(String(raw || ''));
    if (!m) return null;
    const d = new Date(
      Number(m[1]), Number(m[2]) - 1, Number(m[3]),
      Number(m[4]), Number(m[5]), Number(m[6]),
    );
    return isNaN(d.getTime()) ? null : d;
  }

  function renderSysSpawnedList(list) {
    const $list = document.getElementById('sysSpawnedList');
    const $count = document.getElementById('sysSpawnedCount');
    if (!$list) return;
    const rows = Array.isArray(list) ? list : [];
    if ($count) $count.textContent = String(rows.length);
    if (!rows.length) {
      $list.innerHTML = '<div class="sys-spawned-empty">No spawned processes right now.</div>';
      return;
    }
    const now = Date.now();
    const sorted = rows.slice().sort((a, b) => {
      const aExp = a.expires_at_epoch ? a.expires_at_epoch * 1000 : Infinity;
      const bExp = b.expires_at_epoch ? b.expires_at_epoch * 1000 : Infinity;
      return aExp - bExp;
    });
    $list.innerHTML = sorted.map(p => {
      const isPrewarm = !!p.prewarm;
      const expiresMs = p.expires_at_epoch ? (p.expires_at_epoch * 1000 - now) : null;
      const expiring = isPrewarm && expiresMs != null && expiresMs <= 15000;
      const ttlText = isPrewarm
        ? (expiresMs != null ? _sysFmtCountdown(expiresMs) : 'expiring…')
        : 'no scheduled kill';
      const startedDate = _sysParseSpawnedTimestamp(p.started);
      const startedText = startedDate
        ? relDuration((now - startedDate.getTime()) / 1000, false) : '';
      const proj = (p.repo_path || p.cwd || '').split('/').filter(Boolean).pop() || '-';
      const label = String(p.name || (isPrewarm ? 'prewarm' : p.engine || 'claude'));
      const killBtn = (p.killable !== false && p.pid)
        ? '<button type="button" class="pkood-kill-btn" style="margin-left: 8px;" data-pid="' + (0, sharedState.escapeHtml)(String(p.pid)) + '" onclick="sysKillNextServer(this)">STOP/KILL</button>'
        : '';
      const memText = (p.memory_mb && p.memory_mb > 0) ? ' · ' + (0, sharedState.escapeHtml)(String(p.memory_mb)) + ' MB' : '';
      return (
        '<div class="sys-spawned-row' + (isPrewarm ? ' is-prewarm' : '') + (expiring ? ' is-expiring' : '') + '">'
        + '<span class="sys-spawned-dot" aria-hidden="true"></span>'
        + '<span class="sys-spawned-main">'
        + '<span class="sys-spawned-name">' + (0, sharedState.escapeHtml)(label) + '</span>'
        + '<span class="sys-spawned-meta">' + (0, sharedState.escapeHtml)(proj) + ' · pid ' + (0, sharedState.escapeHtml)(String(p.pid || '?'))
        + (startedText ? ' · ' + (0, sharedState.escapeHtml)(startedText) : '') + memText + '</span>'
        + '</span>'
        + '<span class="sys-spawned-ttl">' + (0, sharedState.escapeHtml)(ttlText) + '</span>'
        + killBtn
        + '</div>'
      );
    }).join('');
  }

  function renderSysNextServers(list) {
    const $list = document.getElementById('sysNextList');
    const $count = document.getElementById('sysNextCount');
    if (!$list) return;
    const rows = Array.isArray(list) ? list : [];
    if ($count) $count.textContent = String(rows.length);
    if (!rows.length) {
      $list.innerHTML = '<div class="sys-spawned-empty">No running Next.js servers found.</div>';
      return;
    }
    const sorted = rows.slice().sort((a, b) => b.memory_mb - a.memory_mb);
    $list.innerHTML = sorted.map(s => {
      const displayPath = s.path;
      const pid = s.pid;
      const port = s.port;
      const project = s.project;
      const mem = s.memory_mb;
      return (
        '<div class="sys-spawned-row">'
        + '<span class="sys-spawned-dot" aria-hidden="true" style="background:#f4a261;"></span>'
        + '<span class="sys-spawned-main">'
        + '<span class="sys-spawned-name">' + (0, sharedState.escapeHtml)(project) + ' <span style="color:var(--text-muted); font-size:10px;">(port ' + (0, sharedState.escapeHtml)(String(port)) + ')</span></span>'
        + '<span class="sys-spawned-meta">' + (0, sharedState.escapeHtml)(displayPath) + ' · pid ' + (0, sharedState.escapeHtml)(String(pid)) + '</span>'
        + '</span>'
        + '<span class="sys-spawned-ttl" style="margin-right:12px;">' + (0, sharedState.escapeHtml)(String(mem)) + ' MB</span>'
        + '<button type="button" class="pkood-kill-btn" data-pid="' + (0, sharedState.escapeHtml)(String(pid)) + '" onclick="sysKillNextServer(this)">STOP/KILL</button>'
        + '</div>'
      );
    }).join('');
  }

  async function sysRefresh(force) {
    if (!sharedState._sysFirstAttemptAt) sharedState._sysFirstAttemptAt = Date.now();
    const cache = await _fetchSystemServices(force);
    const payload = cache && cache.payload;
    const map = (0, sharedState._sysServiceMap)(payload);
    renderSysRow('dashboard', map.get('dashboard'));
    renderSysRow('worker', map.get('worker'));
    renderSysRow('watchtower', map.get('watchtower'));
    renderSysAppServerRow(map.get('app_server'), map.get('worker'));
    renderSysKapRow(map.get('kimi_kap'));
    sharedState._sysSpawnedList = Array.isArray(payload && payload.spawned_processes) ? payload.spawned_processes : [];
    renderSysSpawnedList(sharedState._sysSpawnedList);
    const nextServers = Array.isArray(payload && payload.next_servers) ? payload.next_servers : [];
    renderSysNextServers(nextServers);
    const rollup = computeServerRollup(payload);
    paintSysRollup(rollup);
    paintServerChip(rollup);
    return payload;
  }

  function sysSetRestartingPill(id) {
    const ids = sharedState.SYS_ROWS[id];
    const pill = ids && document.getElementById(ids.state);
    if (!pill) return;
    pill.textContent = 'restarting';
    pill.classList.remove('on', 'off');
    pill.classList.add('is-busy');
  }

  function sysSetRestartInFlight(id, sentence) {
    sharedState._sysRestartInFlight = id;
    sharedState._sysArmedRestart = null;
    const ids = id ? sharedState.SYS_ROWS[id] : null;
    if (ids) {
      const row = document.getElementById(ids.row);
      if (row) row.classList.add('is-restarting');
      sysSetRestartingPill(id);
      (0, sharedState.sysRowSay)(id, sentence, 'busy');
    }
    if (sentence) (0, sharedState.sysSay)(sentence);
    (0, sharedState.sysApplyRestartLocks)();
  }

  function sysClearRestartInFlight(id) {
    sharedState._sysRestartInFlight = null;
    sharedState._sysArmedRestart = null;
    const ids = id ? sharedState.SYS_ROWS[id] : null;
    if (ids) {
      const row = document.getElementById(ids.row);
      if (row) row.classList.remove('is-restarting');
      // Repaint the pill from the last good payload rather than leaving the
      // word "restarting" next to a green LED and a success line.
      const svc = (0, sharedState._sysServiceMap)(sharedState._sysServicesCache.payload).get(id);
      const state = (svc && svc.state) || 'unknown';
      sysSetState(ids.state, _sysStateFlag(state), sharedState.SYS_STATE_PILL[state] || 'checking');
    }
    (0, sharedState.sysApplyRestartLocks)();
    sysRefresh(true);
  }

  async function sysRestartWorker(opts) {
    const o = opts || {};
    if (sharedState._sysRestartInFlight || sharedState._sysArmedRestart) {
      (0, sharedState.showOpToast)('Another restart is already running.', 'info');
      return false;
    }
    if (!window.confirm(sharedState.WORKER_RESTART_CONFIRM)) return false;
    const say = (text, kind) => {
      (0, sharedState.sysRowSay)('worker', text, kind);
      if (typeof o.echo === 'function') o.echo(text, kind);
      (0, sharedState.sysSay)(text);
    };
    sysSetRestartInFlight('worker', 'Restarting the execution worker…');
    if (typeof o.echo === 'function') o.echo('Restarting the execution worker…', 'busy');
    // The server now runs the whole safe sequence itself before responding
    // (drain, adopt, kickstart, wait for the worker to answer again,
    // reconcile) -- so the response is the authoritative outcome, not a
    // guess from comparing timestamps a few seconds later.
    let ok = false;
    try {
      const r = await fetch('/api/restart/worker', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok) {
        ok = true;
        say(d.note || 'Worker restarted.', d.worker_reachable ? 'ok' : 'error');
      } else {
        const msg = (d && (d.error || d.note)) || 'Worker restart failed.';
        say(msg, 'error');
        if (o.toastErrors) (0, sharedState.showOpToast)(msg, 'error');
      }
    } catch (e) {
      const msg = (e && e.message) ? ('Worker restart failed: ' + e.message)
        : 'Worker restart failed.';
      say(msg, 'error');
      if (o.toastErrors) (0, sharedState.showOpToast)(msg, 'error');
    }
    sysClearRestartInFlight('worker');
    if (ok) {
      sysRefresh(true);
      if (typeof refreshControlPlaneStatus === 'function') refreshControlPlaneStatus();
    }
    if (typeof o.onDone === 'function') o.onDone(ok);
    return ok;
  }

  function renderWorkerBadge(data) {
    if (!sharedState.$workerBadge) return;
    sharedState.$workerBadge.classList.remove(
      'is-checking', 'is-offline', 'is-paused', 'is-update', 'is-uncertain'
    );
    let label = 'Worker';
    let detail = 'Persistent execution worker online';
    if (!data || !data.ok) {
      sharedState.$workerBadge.classList.add('is-offline');
      label = 'Worker offline';
      detail = 'Execution worker unavailable';
    } else {
      const capabilities = (data.worker && data.worker.capabilities) || [];
      if (!capabilities.includes('engine-execution-v1')) {
        sharedState.$workerBadge.classList.add('is-update');
        label = 'Worker update';
        detail = 'Execution worker update pending';
      } else {
        const drain = data.drain || {};
        const active = Number(data.active || 0);
        const queued = Number(data.queued || 0);
        if (drain.enabled) {
          sharedState.$workerBadge.classList.add('is-paused');
          label = 'Worker paused';
          detail = [
            'Worker dispatch paused',
            active + ' active',
            queued + ' queued',
          ].join(' · ');
        } else {
          // Deliberately NOT "Worker restarted <time>". CCC cannot tell a cold
          // boot from a restart, and the System status row renders the same
          // timestamp as "Started ...". Two words for one fact, one of them a
          // guess, is exactly what this panel is supposed to stop doing.
          label = 'Worker online';
          detail = 'Persistent execution worker online';
        }
      }
    }
    if (sharedState.$workerWord) sharedState.$workerWord.textContent = label;
    if (sharedState.$workerCount) {
      sharedState.$workerCount.textContent = '';
      sharedState.$workerCount.hidden = true;
    }
    sharedState.$workerBadge.title = detail;
    // It is aria-hidden decoration inside the worker row, whose state pill,
    // started line and busy line already carry all of this. No aria-label.
  }

  function renderDrainPausedBanner() {
    if (!sharedState.$drainPausedBanner) return;
    if (!sharedState.controlPlaneDraining) {
      sharedState.$drainPausedBanner.hidden = true;
      if (sharedState.$drainPausedResumeBtn) sharedState.$drainPausedResumeBtn.disabled = false;
      return;
    }
    const reason = sharedState.controlPlaneDrainReason
      ? ' (' + sharedState.controlPlaneDrainReason.slice(0, 60) + ')'
      : '';
    const queued = sharedState.controlPlaneDrainQueued > 0
      ? ' ' + sharedState.controlPlaneDrainQueued + ' message(s) held.'
      : '';
    const restarting = /^(dashboard-restart|worker-restart|worker-compat-selfheal):/.test(sharedState.controlPlaneDrainReason);
    if (sharedState.$drainPausedBannerText) {
      sharedState.$drainPausedBannerText.textContent = restarting
        ? 'CCC is restarting. Please wait—your messages will send automatically when it’s ready. No action is needed.' + queued
        : 'Dispatch paused' + reason + '. New sends are queuing, not sending.' + queued;
    }
    if (sharedState.$drainPausedResumeBtn) sharedState.$drainPausedResumeBtn.hidden = restarting;
    sharedState.$drainPausedBanner.hidden = false;
  }

  function renderControlPlaneStatus(data) {
    renderWorkerBadge(data);
    sharedState.controlPlaneOnline = false;
    if (!data || !data.ok) {
      sharedState.controlPlaneDraining = false;
      renderDrainPausedBanner();
      if (sharedState.$controlPlaneStatus) {
        sharedState.$controlPlaneStatus.textContent =
          'Worker unavailable; active compatibility work may block dashboard restart.';
      }
      if (sharedState.$controlPlaneDrainBtn) {
        sharedState.$controlPlaneDrainBtn.disabled = false;
        sharedState.$controlPlaneDrainBtn.textContent = 'Start worker';
      }
      if (sharedState.$controlPlaneReconcileBtn) {
        sharedState.$controlPlaneReconcileBtn.hidden = true;
        sharedState.$controlPlaneReconcileBtn.disabled = true;
      }
      return;
    }
    const capabilities = (data.worker && data.worker.capabilities) || [];
    if (!capabilities.includes('engine-execution-v1')) {
      sharedState.controlPlaneDraining = false;
      renderDrainPausedBanner();
      if (sharedState.$controlPlaneStatus) {
        sharedState.$controlPlaneStatus.textContent =
          'Worker update pending; compatibility execution remains active.';
      }
      if (sharedState.$controlPlaneDrainBtn) sharedState.$controlPlaneDrainBtn.disabled = true;
      if (sharedState.$controlPlaneReconcileBtn) {
        sharedState.$controlPlaneReconcileBtn.hidden = true;
        sharedState.$controlPlaneReconcileBtn.disabled = true;
      }
      return;
    }
    sharedState.controlPlaneOnline = true;
    const drain = data.drain || {};
    sharedState.controlPlaneDraining = !!drain.enabled;
    sharedState.controlPlaneDrainReason = sharedState.controlPlaneDraining
      ? String(drain.reason || '') : '';
    const active = Number(data.active || 0);
    const queued = Number(data.queued || 0);
    sharedState.controlPlaneDrainQueued = sharedState.controlPlaneDraining ? queued : 0;
    const uncertain = Number(data.uncertain || 0);
    const parts = [
      'Persistent worker online',
      active + ' active',
      queued + ' queued',
    ];
    if (uncertain) parts.push(uncertain + ' need reconciliation');
    if (sharedState.controlPlaneDraining) parts.push('dispatch paused');
    if (sharedState.$controlPlaneStatus) sharedState.$controlPlaneStatus.textContent = parts.join(' · ');
    if (sharedState.$controlPlaneDrainBtn) {
      sharedState.$controlPlaneDrainBtn.disabled = false;
      sharedState.$controlPlaneDrainBtn.textContent =
        sharedState.controlPlaneDraining ? 'Resume dispatch' : 'Pause dispatch';
    }
    if (sharedState.$controlPlaneReconcileBtn) {
      sharedState.$controlPlaneReconcileBtn.hidden = uncertain === 0;
      sharedState.$controlPlaneReconcileBtn.disabled = false;
    }
    renderDrainPausedBanner();
  }

  async function refreshControlPlaneStatus() {
    if (!sharedState.$controlPlaneStatus && !sharedState.$workerBadge) return;
    try {
      const response = await fetch('/api/control-plane/status', {
        cache: 'no-store',
      });
      renderControlPlaneStatus(await response.json());
    } catch (_) {
      renderControlPlaneStatus(null);
    }
  }

  async function spawnCwdIsMissing(path) {
    const wanted = (0, sharedState.normalizeSpawnCwdPath)(path);
    if (!wanted || (0, sharedState.spawnCwdKnownToExist)(wanted)) return false;
    if (sharedState.spawnCwdMissing.has(wanted)) return true;
    try {
      const res = await fetch('/api/fs/list?path=' + encodeURIComponent(wanted), { cache: 'no-store' });
      const d = await res.json();
      return !!(d && d.ok === false && /^not a directory/.test(d.error || ''));
    } catch (_) {
      return false;  // can't tell: let the spawn itself report
    }
  }

  function offerCreateMissingSpawnCwd(path, body) {
    const target = (0, sharedState.normalizeSpawnCwdPath)(path);
    if (!target) return;
    (0, sharedState.showOpToast)('Folder doesn’t exist: ' + (0, sharedState.escapeHtml)(target) + '. Create it and start the session there?', 'error', {
      label: 'Create folder & start',
      onClick: () => createMissingSpawnCwdAndSpawn(target, body),
    });
  }

  async function createMissingSpawnCwdAndSpawn(target, body) {
    try {
      const res = await fetch('/api/project/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: target }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
      sharedState.spawnCwdMissing.delete(target);
      sharedState.spawnCwdMissing.delete(data.path);
      if (Array.isArray(data.repos)) sharedState.repoListState.repos = data.repos;
      (0, sharedState.populateSpawnCwdPicker)();
      (0, sharedState.setSpawnCwdInputValue)(data.path);
      (0, sharedState.showOpToast)('Folder created: ' + (0, sharedState.escapeHtml)(data.path), 'success');
    } catch (err) {
      (0, sharedState.showOpToast)('Could not create folder: ' + (0, sharedState.escapeHtml)((err && err.message) || 'unknown'), 'error');
      return;
    }
    // Start the session only if the composer is still on New Session; the
    // draft restored after the failure may have been edited since.
    if (typeof sharedState.currentConversation !== 'undefined' && sharedState.currentConversation !== '__new__') return;
    const text = (sharedState.$convInput && sharedState.$convInput.value.trim()) ? sharedState.$convInput.value : body;
    if (text && text.trim()) spawnFromInlineInput(text);
  }

  async function settleRepoGuessBeforeSend() {
    if (sharedState.currentConversation !== '__new__') return;
    if (sharedState.repoGuessTimer) { clearTimeout(sharedState.repoGuessTimer); (0, sharedState.runRepoGuess)(); }
    const pending = sharedState.repoGuessInflight;
    if (!pending) return;
    await Promise.race([pending, new Promise(resolve => setTimeout(resolve, 800))]);
  }

  function spawnFirstSentence(text) {
    const value = String(text || '');
    const chunks = value.split(/(?<=[.!?])\s+|\n+/).map(s => s.trim()).filter(Boolean);
    const first = chunks[0] || value.trim();
    return first.length > 120 ? first.slice(0, 120).trim() + '...' : first;
  }

  function syncNsModelPickerPillsSelection() {
    const pillsContainer = document.getElementById('nsModelPickerPills');
    if (!pillsContainer) return;
    const currentEngine = (0, sharedState.getSpawnEngine)();
    const currentModel = (typeof sharedState.$convInputModelSelect !== 'undefined' && sharedState.$convInputModelSelect && sharedState.$convInputModelSelect.style.display !== 'none')
      ? sharedState.$convInputModelSelect.value
      : (sharedState._defaultModelsByEngine[currentEngine] || '');

    pillsContainer.querySelectorAll('.orch-tier-chip').forEach(chip => {
      const cEngine = chip.getAttribute('data-engine');
      const cModel = chip.getAttribute('data-model');
      const isSel = cEngine === currentEngine && (0, sharedState._normalizeModelId)(cModel) === (0, sharedState._normalizeModelId)(currentModel)
        && (!chip.hasAttribute('data-rank') || (chip.getAttribute('data-effort') || '') === (sharedState.$convInputEffortSelect?.value || ''));
      chip.classList.toggle('is-selected', isSel);
      chip.setAttribute('aria-checked', isSel ? 'true' : 'false');
    });
  }

  async function awaitQueuedSpawnResult(data) {
    if (!data?.ok || !data.work_id || data.session_id || !data.queued) return data;
    const workId = data.work_id;
    const deadline = Date.now() + 180000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch('/api/control-plane/graph?root_id=' + encodeURIComponent(workId), {
          cache: 'no-store', cccPriority: 'user', signal: AbortSignal.timeout(10000),
        });
        const graph = await response.json();
        const work = graph?.graph?.items?.find(item => item.id === workId);
        if (work?.result?.session_id) return Object.assign({}, data, work.result, { queued: false });
        if (work && ['failed', 'cancelled', 'uncertain', 'interrupted'].includes(work.state)) {
          return { ok: false, error: work.last_error || ('Spawn work is ' + work.state) };
        }
      } catch (_) { /* A restart can interrupt a read; keep the same work id. */ }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    return { ok: false, error: 'Session startup is still queued. Check the worker before retrying.' };
  }

  async function spawnFromInlineInput(body) {
    const launchSelection = (0, sharedState.buildSpawnBody)({
      ...(0, sharedState.currentSpawnChoice)(),
      effortExplicit: sharedState.spawnEffortChoiceDirty,
    });
    const spawnPaneId = (0, sharedState.activePaneId)();
    const launchContext = { paneId: spawnPaneId, revision: sharedState.newSessionComposerRevision };
    const spawnInput = (0, sharedState.composerInputForPane)(spawnPaneId) || sharedState.$convInput;
    const spawnSendButton = sharedState.$convSendBtn;
    const spawnAskedAt = Date.now();
    const subject = spawnFirstSentence(body);
    const prompt = body;
    const $autoCompactInput = document.getElementById('convSendAutoCompactK');
    const autoCompactK = $autoCompactInput ? parseInt($autoCompactInput.value, 10) : NaN;
    const engine = launchSelection.engine;
    if (engine === 'codex' && newSessionLaunchStillCurrent(launchContext)) {
      const view = (0, sharedState.getConvViewForPane)(spawnPaneId);
      if (view) {
        view.innerHTML = '<div class="event user_text pending"><span class="label">User</span>'
          + '<div class="user-msg" dir="auto" data-raw-text="' + (0, sharedState.escapeAttr)(body) + '">'
          + (0, sharedState.linkifyPastedImages)((0, sharedState.escapeHtml)(body)) + '</div></div>';
        // Yield a paint before folder validation or sidebar work can run.
        await new Promise(resolve => {
          const fallback = setTimeout(resolve, 100);
          requestAnimationFrame(() => requestAnimationFrame(() => { clearTimeout(fallback); resolve(); }));
        });
      }
    }
    await settleRepoGuessBeforeSend();
    const spawnCwd = (typeof sharedState.getSpawnCwd === 'function') ? (0, sharedState.getSpawnCwd)() : '';
    const autoPickedCwd = (sharedState.repoGuessAuto && !sharedState.repoGuessUserPicked && sharedState.repoGuessAuto.path === spawnCwd) ? spawnCwd : '';
    const launchCwd = spawnCwd || (0, sharedState.popoutRepoPath)();
    const knownRepo = (0, sharedState.findSpawnCwdRepo)(launchCwd);
    const repoPath = knownRepo ? knownRepo.path : (spawnCwd ? '' : (0, sharedState.popoutRepoPath)());
    const displayPath = repoPath || launchCwd;
    if (!launchCwd) {
      (0, sharedState.showOpToast)('New session needs a folder. Pick one from the cwd field first.', 'error');
      (0, sharedState.populateSpawnCwdPicker)();
      const sel = document.getElementById('spawnCwdPicker');
      if (sel) {
        try { sel.focus(); } catch (_) {}
      }
      return;
    }
    // Catch a missing folder BEFORE spawning: the draft is still in the box
    // and nothing has been launched, so offering to create it is clean.
    if (!knownRepo && await spawnCwdIsMissing(launchCwd)) {
      (0, sharedState.forgetMissingSpawnCwd)(launchCwd);
      offerCreateMissingSpawnCwd(launchCwd, body);
      return;
    }
    const claudePrewarmPromise = engine === 'claude'
      ? (0, sharedState.requestClaudePrewarm)()
      : Promise.resolve(null);
    if (sharedState.$convSendBtn) sharedState.$convSendBtn.disabled = true;
    const flashRed = () => {
      if (!sharedState.$convInput) return;
      sharedState.$convInput.style.borderColor = 'var(--red)';
      setTimeout(() => { sharedState.$convInput.style.borderColor = ''; }, 1500);
    };
    const cardSource = (0, sharedState.spawnSourceForEngine)(engine);
    const tempPid = 'tmp-' + Date.now();
    const clearSubmittedComposer = newSessionLaunchStillCurrent(launchContext);
    insertPendingSpawnCard(tempPid, subject, cardSource, null, {
      first_message: body,
      repo_path: displayPath,
      folder_path: displayPath,
      spawn_cwd: launchCwd,
      cwd: launchCwd,
      session_cwd: launchCwd,
      session_cwd_exists: true,
      spawn_requested_at: spawnAskedAt,
      fast_path: engine === 'claude',
      spawn_pane_id: spawnPaneId,
      no_auto_select: !clearSubmittedComposer,
    });
    if (clearSubmittedComposer) {
      if (spawnInput) spawnInput.value = '';
      (0, sharedState.clearInputDraftForConversation)('__new__');
    }
    const restoreDraftAfterFailure = (reason) => {
      // Keep the exact failed request available for Retry/Edit without
      // replacing a newer draft or navigating back from another chat.
      (0, sharedState._failPendingSpawnCard)(tempPid, reason);
      if (sharedState.newSessionComposerRevision === launchContext.revision) {
        (0, sharedState.setInputDraftForKey)((0, sharedState.inputDraftKeyForConversation)('__new__'), body);
      }
    };
    try {
      const endpoint = (0, sharedState.spawnEndpointForEngine)(engine);
      const $inlineWorktree = document.getElementById('inlineWorktreeToggle');
      const useWorktree = !!($inlineWorktree && $inlineWorktree.checked);
      // buildSpawnBody owns the engine/model/effort triple (CCC-509 engine
      // pinning and CCC-503 cross-engine model filtering live there); the keys
      // below are specific to this call site.
      const spawnBody = Object.assign((0, sharedState.buildSpawnBody)({
        engine,
        model: launchSelection.model || '',
        effort: launchSelection.reasoning_effort || '',
        effortExplicit: Object.prototype.hasOwnProperty.call(launchSelection, 'reasoning_effort'),
        runtime: launchSelection.runtime || '',
        prompt,
        name: subject,
        cwd: launchCwd,
        repoPath,
        worktree: useWorktree,
      }), Object.assign({
        timeline_t0_epoch_ms: spawnAskedAt,
        idempotency_key: (0, sharedState.durableActionId)('spawn'),
        composer_form: 'ccc-composer-v1',
      }, Number.isFinite(autoCompactK) && autoCompactK > 0 ? { auto_compact_k: autoCompactK } : {}));
      if (engine === 'claude') (0, sharedState.abortBackgroundApiReadsForSpawn)();
      // A prewarm reservation boots with paid env - a $0 spawn must not claim it.
      if (engine === 'claude' && !useWorktree && spawnBody.runtime !== 'free') {
        const prewarm = await claudePrewarmPromise;
        if (prewarm && prewarm.prewarm_id) spawnBody.prewarm_id = prewarm.prewarm_id;
        sharedState._claudePrewarmKey = '';
        sharedState._claudePrewarmPromise = null;
      }
      const pendingCard = sharedState.pendingSpawns.get(tempPid);
      if (pendingCard) Object.assign(pendingCard, { spawn_endpoint: endpoint, spawn_body: spawnBody });
      const res = await fetch(endpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        priority: 'high',
        body: JSON.stringify(spawnBody),
      });
      let data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
      if (engine === 'codex') data = await awaitQueuedSpawnResult(data);
      if (data.ok) {
        // An auto-picked folder becomes the last-used one only now that a
        // session actually launched in it.
        if (autoPickedCwd) { try { localStorage.setItem(sharedState.SPAWN_CWD_KEY, autoPickedCwd); } catch (_) {} }
        if (newSessionLaunchStillCurrent(launchContext) || pendingSpawnIsSelected(sharedState.pendingSpawns.get(tempPid))) (0, sharedState.resetRepoGuess)(false);
        if (typeof sharedState.recordSpawnChoice === 'function') {
          (0, sharedState.recordSpawnChoice)(engine, spawnBody.model || launchSelection.model || '', spawnBody.reasoning_effort || '');
        }
        if (data.prewarm_fallback) showClaudePrewarmFallbackRecovery();
        if (data.session_id) {
          spawnStatsBegin(data.session_id, spawnAskedAt);
          (0, sharedState.spawnStatsMark)(data.session_id, 'response_received');
        }
        const placeholder = (0, sharedState.adoptPendingSpawnPid)(tempPid, data.spawn_id || data.pid, data.log, data.session_id);
        assignSpawnedSessionToDefaultObject(data, repoPath || launchCwd);
        if (pendingSpawnIsSelected(placeholder) && engine === 'claude' && data.session_id) {
          sharedState.claudeSpawnAwaitingFirstPaint.add(data.session_id);
          setTimeout(() => (0, sharedState.releaseClaudeSpawnPaintGate)(data.session_id), 30000);
          // The placeholder's canonical conversation stream has no durable
          // transcript yet and competes for the same HTTP/1.1 connection pool
          // as the useful spawn-log replay. Give the first-response stream the
          // slot; canonical polling resumes when the real row is rebound.
          (0, sharedState.stopConvStream)(spawnPaneId);
          (0, sharedState.startSpawnStream)(data.session_id, spawnPaneId, {
            pendingConversationId: placeholder.id,
            replay: true,
            knownLog: !!data.log,
          });
        }
        // Fire-and-watch engines can stream their spawn log once the real pid
        // is known. Re-select the same placeholder id so fetchConversationEvents
        // starts the log poller without making the user click the sidebar row.
        if (spawnUsesLogPlaceholder(engine)) refreshSelectedPendingSpawn(placeholder);
        if (engine === 'cursor') (0, sharedState.showOpToast)('Cursor headless run started.', 'ok');
        if (engine === 'antigravity') (0, sharedState.showOpToast)('Antigravity headless run started.', 'ok');
        if (engine !== 'claude') setTimeout(hostServices.refreshConversationList, 600);
        // ...then chase with a cheap probe rather than two more full-corpus
        // refreshes: for Codex the engine has written nothing at 3s, and each
        // of those refreshes is a multi-megabyte fetch of every row we have.
        (0, sharedState.chasePendingSpawn)(tempPid, { sessionId: data && data.session_id });
      } else {
        (0, sharedState.releaseClaudeSpawnPaintGate)('', tempPid);
        // The folder vanished after the picker was filled: drop it so the
        // retry does not fail the same way.
        const missingCwd = /^invalid cwd: (path does not exist|not a directory)/.test(data.error || '');
        if (missingCwd) (0, sharedState.forgetMissingSpawnCwd)(launchCwd);
        restoreDraftAfterFailure('Spawn failed: ' + (data.error || 'HTTP ' + res.status));
        flashRed();
        if (missingCwd) offerCreateMissingSpawnCwd(launchCwd, body);
        else if (data.error_code === 'engine_not_installed') {
          // Friendly notice with an Install button instead of raw error text;
          // the prompt was already restored above.
          sharedState._emnForced = data.engine || engine;
          sharedState._emnInstalled = Object.assign({}, sharedState._emnInstalled || {}, { [sharedState._emnForced]: false });
          setTimeout(() => { (0, sharedState.renderEngineMissingNotice)(); (0, sharedState.refreshEngineMissingNotice)({ fresh: true }); }, 120);
        }
        else (0, sharedState.showOpToast)('Spawn failed: ' + (data.error || 'HTTP ' + res.status), 'error');
        console.error('[New session] spawn failed', data);
      }
    } catch (err) {
      (0, sharedState.releaseClaudeSpawnPaintGate)('', tempPid);
      restoreDraftAfterFailure('Spawn failed: ' + (err && err.message || 'network'));
      flashRed();
      (0, sharedState.showOpToast)('Spawn failed: ' + (err && err.message || 'network'), 'error');
      console.error('[New session] submit error', err);
    }
    if (newSessionLaunchStillCurrent(launchContext)
        || pendingSpawnIsSelected(sharedState.pendingSpawns.get(tempPid))) {
      if (spawnSendButton) spawnSendButton.disabled = false;
      if (!document.hidden && spawnInput) spawnInput.focus();
    }
  }

  function storedConvRowStyle() {
    let id = null;
    try { id = localStorage.getItem(sharedState.CONV_ROWSTYLE_STORAGE_KEY); } catch (_) {}
    return sharedState.CONV_ROWSTYLE_VARIANTS.some(v => v.id === id) ? id : 'large-bright';
  }

  function updateConvRowStyleState(id) {
    document.querySelectorAll('[data-conv-rowstyle-id]').forEach(btn => {
      const active = btn.getAttribute('data-conv-rowstyle-id') === id;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-checked', String(active));
    });
  }

  function applyConvRowStyle(id, opts = {}) {
    const variant = sharedState.CONV_ROWSTYLE_VARIANTS.find(v => v.id === id) || sharedState.CONV_ROWSTYLE_VARIANTS[0];
    document.documentElement.setAttribute('data-conv-rowstyle', variant.id);
    if (opts.persist) {
      try { localStorage.setItem(sharedState.CONV_ROWSTYLE_STORAGE_KEY, variant.id); } catch (_) {}
    }
    updateConvRowStyleState(variant.id);
  }

  function rowDelineation() {
    let v = null;
    try { v = localStorage.getItem(sharedState.ROW_DELINEATION_KEY); } catch (_) {}
    return sharedState.CONV_ROWSTYLE_DELINEATION.some(d => d.id === v) ? v : 'divider';
  }

  function applyRowDelineation(id) {
    if (sharedState.$convList) sharedState.$convList.setAttribute('data-row-delineation', id);
  }

  function renderConvRowStylePalettes() {
    const current = document.documentElement.getAttribute('data-conv-rowstyle') || storedConvRowStyle();
    document.querySelectorAll('[data-role="conv-rowstyle-palette"]').forEach(host => {
      host.innerHTML = '';
      if (sharedState.SHOW_ROWSTYLE_FONT_AND_SPACING) {
      sharedState.CONV_ROWSTYLE_VARIANTS.forEach(variant => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'conv-rowstyle-swatch';
        btn.setAttribute('role', 'radio');
        btn.setAttribute('aria-checked', 'false');
        btn.setAttribute('aria-label', variant.label);
        btn.setAttribute('data-conv-rowstyle-id', variant.id);
        btn.title = 'Row style: ' + variant.label;
        if (variant.kind === 'font') {
          btn.classList.add('is-font');
          btn.textContent = variant.caps ? 'AA' : 'Aa';
          if (variant.font && variant.font !== 'inherit') btn.style.fontFamily = variant.font;
          if (variant.weight) btn.style.fontWeight = String(variant.weight);
          if (variant.caps) btn.style.letterSpacing = '0.04em';
        } else {
          btn.style.setProperty('--rs-op', String(variant.op));
          btn.style.setProperty('--rs-h', variant.h + 'px');
          btn.style.setProperty('--rs-gap', variant.gap + 'px');
        }
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          applyConvRowStyle(variant.id, { persist: true });
        });
        host.appendChild(btn);
      });
      const currentSpacing = rowsSpacing();
      sharedState.CONV_ROWSTYLE_SPACING.forEach(s => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'conv-rowstyle-swatch is-spacing' + (s.id === currentSpacing ? ' active' : '');
        btn.setAttribute('role', 'radio');
        btn.setAttribute('aria-checked', String(s.id === currentSpacing));
        btn.setAttribute('aria-label', s.label);
        btn.setAttribute('data-rows-spacing-id', s.id);
        btn.title = 'Row spacing: ' + s.label;
        btn.style.setProperty('--rs-op', String(s.op));
        btn.style.setProperty('--rs-h', s.h + 'px');
        btn.style.setProperty('--rs-gap', s.gap + 'px');
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          try { localStorage.setItem(sharedState.ROWS_SPACING_KEY, s.id); } catch (_) {}
          if (sharedState.$convList) sharedState.$convList.setAttribute('data-rows-spacing', s.id);
          host.querySelectorAll('[data-rows-spacing-id]').forEach(b => {
            const on = b.getAttribute('data-rows-spacing-id') === s.id;
            b.classList.toggle('active', on);
            b.setAttribute('aria-checked', String(on));
          });
        });
        host.appendChild(btn);
      });
      }
      const currentDelineation = rowDelineation();
      sharedState.CONV_ROWSTYLE_DELINEATION.forEach(d => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'conv-rowstyle-swatch is-delineation is-delineation-' + d.id + (d.id === currentDelineation ? ' active' : '');
        btn.setAttribute('role', 'radio');
        btn.setAttribute('aria-checked', String(d.id === currentDelineation));
        btn.setAttribute('aria-label', d.label);
        btn.setAttribute('data-row-delineation-id', d.id);
        btn.title = d.title;
        btn.style.setProperty('--rs-op', String(d.op));
        btn.style.setProperty('--rs-h', d.h + 'px');
        btn.style.setProperty('--rs-gap', d.gap + 'px');
        btn.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          try { localStorage.setItem(sharedState.ROW_DELINEATION_KEY, d.id); } catch (_) {}
          applyRowDelineation(d.id);
          host.querySelectorAll('[data-row-delineation-id]').forEach(b => {
            const on = b.getAttribute('data-row-delineation-id') === d.id;
            b.classList.toggle('active', on);
            b.setAttribute('aria-checked', String(on));
          });
        });
        host.appendChild(btn);
      });
    });
    updateConvRowStyleState(current);
  }

  function ticketViewOn() {
    if (!(0, sharedState.ideaTicketsOn)()) return false;
    if (ticketViewOn.pref === undefined) {
      try { ticketViewOn.pref = localStorage.getItem('ccc-ticket-view') === '1'; }
      catch (_) { ticketViewOn.pref = false; }
    }
    return ticketViewOn.pref;
  }

  function normalizeSidebarTabKey(t) {
    if (t === 'decisions' || t === 'decisions2') return window.cccDecisionTabKey();
    if (ticketViewOn()) {
      return (t === 'issues' || t === 'queues' || t === 'archived' || t === 'jobs') ? t : 'workers';
    }
    if ((0, sharedState.ideaTicketsOn)()) {
      return (t === 'alltickets' || t === 'ideas' || t === 'smallbugs' || t === 'issues' || t === 'queues' || t === 'archived' || t === 'workers' || t === 'jobs') ? t : 'alltickets';
    }
    return (t === 'issues' || t === 'queues' || t === 'inprogress' || t === 'archived' || t === 'coding' || t === 'workers' || t === 'jobs') ? t : 'coding';
  }
    return { renderSplitLayout, syncNsModelPickerPillsSelection, spawnFromInlineInput, stopGroupChatReader, insertPendingSpawnCard, pollVercelDeploy, loadConversationList, captureConversationBottomAnchors, restoreConversationBottomAnchors, buildPaneElement, attachDividerDrag, spawnFirstSentence, newSessionLaunchStillCurrent, settleRepoGuessBeforeSend, spawnCwdIsMissing, offerCreateMissingSpawnCwd, awaitQueuedSpawnResult, pendingSpawnIsSelected, showClaudePrewarmFallbackRecovery, spawnStatsBegin, assignSpawnedSessionToDefaultObject, spawnUsesLogPlaceholder, refreshSelectedPendingSpawn, _setSharedQueuePanelHost, _currentSidebarTabKey, trackSpawnView, publishVercelProgress, setDeployPill, followPendingSpawnTicket, carryFlowPendingSpawnNode, _signalSnapshot, _hasSid, stampFreshSessionsAsBorn, reconcilePendingNewSessionObjectAssignments, maybeSelectPopoutConversation, restoreLastViewOrConversation, ensureAllConversationEndAffordances, removeSplitPaneSingletonChrome, renderConvRowStylePalettes, closePane, createMissingSpawnCwdAndSpawn, sysRestartWorker, resolveAutoParentNodeForRepoPath, getNewSessionSelectedObject, assignSessionNodeToParent, rememberPendingNewSessionObjectAssignment, _mountSharedQueuePanel, normalizeSidebarTabKey, persistFlowNodeParents, persistFlowNodePositions, loadPendingNewSessionObjectAssignments, savePendingNewSessionObjectAssignments, renderPopoutMissingConversation, openGroupChatReader, restoreLastConversation, storedConvRowStyle, applyConvRowStyle, rowsSpacing, rowDelineation, applyRowDelineation, updateConvRowStyleState, sysSetRestartInFlight, sysClearRestartInFlight, sysRefresh, refreshControlPlaneStatus, loadNewSessionObjectByCwd, findActiveFlowObject, newSessionObjectRepoMatchIds, newSessionObjectScopeKey, ensureNewSessionDefaultObject, _objectsApiPost, _sharedQueueHostElement, ticketViewOn, syncObjectsToServer, stopFlowNodeInspector, setStatusRailTab, syncBodyGcReaderFlag, openGcParticipantPicker, startReplay, sendHumanGcPost, wireGcMentionAutocomplete, _gcMentionMenuHandleKeydown, pollGroupChatReader, addSessionToGroupChat, conversationRowsContainId, sysSetRestartingPill, sysSetState, _sysStateFlag, _fetchSystemServices, renderSysRow, renderSysAppServerRow, renderSysKapRow, renderSysSpawnedList, renderSysNextServers, computeServerRollup, paintSysRollup, paintServerChip, renderControlPlaneStatus, isArchivedFlowObjectId, persistFlowCustomObjects, _reconcileObjectsToServer, refreshRailLogPane, _gcReplayClearHero, renderReplayControls, extractHash, gcDisplayName, getParticipantColor, markSystemBlockquotes, _mergeReplayQueueEvents, exitReplayMode, playNextReplayStep, _gcMentionRefresh, _gcMentionClose, _gcMentionCommit, buildAgentFallbackNames, updateGcInfoBar, renderGroupChatMarkdown, _gcExpandHashIds, replaceParticipantMentions, updateOrchestratorPanel, showUpdatedJustNowTick, _gcShowNewPostsPill, markActiveGroupChatTrigger, pollGcActive, formatStarted, sysDashBusySentence, sysWorkerBusySentence, sysWtBusySentence, sysPaintSvcMeta, _sysFmtCountdown, _sysParseSpawnedTimestamp, relDuration, _plural, _sysCountLabel, renderWorkerBadge, renderDrainPausedBanner, _objectsLocalState, _objectsGet, loadDeletedFlowDraftSessionIds, clearDeletedFlowDraftSessionIds, mergeServerDraftSessions, _railLogContextKey, _setRailLogMessage, _railLogPaneVisible, _renderRailLogPane, stepReplayBack, gcFallbackName, _gcReplayParseWhen, _renderReplayQueueEvent, _gcReplayHumanStep, gcTimeChip, _gcReplayCreateHero, _gcReplayHoldThenLandHero, _gcReplayAlertsHtml, _gcReplayReceiptsHtml, _gcReplayMessagePauseMs, _gcMentionDetectToken, _gcMentionParticipants, gcRelativeTime, gcInitials, gcParseChatTimestamp, gcExtractPingedNames, formatWaitingOn, renameGroupChatParticipant, setGroupChatPaused, updateActiveGroupChatPill, _gcChatsKey, absStamp, saveDeletedFlowDraftSessionIds, _isSuccessfulHeartbeat, _isRoutineInjectEvent, _syncRailLogToggleAll, _gcReplayLandHero, getActiveGroupChatPillEl, activeGroupChatPillTarget, gcShouldShowActivePill, gcEpochMs, gcSortMs, gcTriggerMs };
  }
  root.CCCConversationServices = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
