/* Shared conversation services with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices }) {
  function rowsSpacing() {
    let v = null;
    try { v = localStorage.getItem(sharedState.ROWS_SPACING_KEY); } catch (_) {}
    return (v === 'cozy' || v === 'airy') ? v : 'roomy';
  }

  function _currentSidebarTabKey() {
    try { return normalizeSidebarTabKey(window.CCCNavigation.getTab()); }
    catch (_) { return ''; }
  }

  function restoreLastViewOrConversation() {
    if (sharedState.CONV_POPOUT_MODE) { return; }
    if (sharedState._suppressRestoreLastView) return;  // mid-transition, not a real boot/refresh (CCC-508)
    if (sharedState._gcReaderPath || sharedState._gcReaderId) return;  // a reader is open - leave it
    // Archive/session boot can finish long after the page became interactive.
    // Once the user has opened New Session—or submitted a fast Claude spawn—
    // that explicit intent owns the pane; a late restore must not replace it
    // with yesterday's row and steal the first-response stream.
    if (sharedState.currentConversation === '__new__'
        || String(sharedState.currentConversation || '').startsWith('spawning-')
        || (0, sharedState.claudeFastSpawnAwaitingPaint)()) return;
    try {
      const raw = localStorage.getItem('ccc-last-view');
      const view = raw ? JSON.parse(raw) : null;
      if (view && view.type === 'gc' && (view.path || view.id)
          && typeof openGroupChatReader === 'function') {
        openGroupChatReader(view.path || '', view.topic || '', view.mode || 'topic',
          !!view.includeHuman, view.id || '');
        return;
      }
    } catch (_) {}
    restoreLastConversation();
  }

  function conversationRowsContainId(rows, id) {
    return !!id && Array.isArray(rows)
      && rows.some(c => c && (c.id === id || c.session_id === id));
  }

  async function restoreLastConversation() {
    if (sharedState.CONV_POPOUT_MODE) return;
    if (!sharedState.conversationsLoaded) return;
    sharedState._qfBootRestoreDepth++;
    try {
      const deepSid = (sharedState._bootUrlParams.get('session') || '').trim();
      if (deepSid && !sharedState._deepLinkedSession) {
        const found = conversationRowsContainId(sharedState.conversationsData, deepSid)
          || (sharedState.archiveLoaded && conversationRowsContainId(sharedState.archiveData, deepSid));
        if (found) {
          sharedState._deepLinkedSession = true;
          await (0, sharedState.selectConversation)(deepSid);
          return;
        }
        if (sharedState.archiveLoaded) sharedState._deepLinkedSession = true;
      }
      let anyRestored = false;
      const savedActiveIndex = sharedState.splitState.activeIndex;

      for (let i = 0; i < sharedState.splitState.panes.length; i++) {
        const pane = sharedState.splitState.panes[i];
        if (!pane.conversationId || pane.restored) continue;

        const exists = conversationRowsContainId(sharedState.conversationsData, pane.conversationId)
          || conversationRowsContainId(sharedState.archiveData, pane.conversationId);
        if (exists) {
          pane.restored = true;
          anyRestored = true;
          await (0, sharedState.selectConversation)(pane.conversationId, pane.id);
        } else if (sharedState.archiveLoaded) {
          pane.restored = true;
        }
      }

      if (anyRestored) {
        const activePane = sharedState.splitState.panes[savedActiveIndex];
        if (activePane) {
          (0, sharedState.setActivePaneById)(activePane.id);
        }
      }
    } finally {
      sharedState._qfBootRestoreDepth--;
    }
  }

  function _signalSnapshot(c) {
    return {
      has_edit: !!c.has_edit,
      has_commit: !!c.has_commit,
      has_push: !!c.has_push,
      last_event_type: c.last_event_type || null,
    };
  }

  function stampFreshSessionsAsBorn(freshList) {
    if (!sharedState._convInitialLoadDone) return;
    const existing = new Set();
    for (const c of sharedState.conversationsData) if (c.session_id) existing.add(c.session_id);
    const now = Date.now();
    let anyNew = false;
    for (const c of freshList) {
      const sid = c.session_id;
      if (sid && !existing.has(sid) && !sharedState._firstSeenSessions.has(sid)) {
        sharedState._firstSeenSessions.set(sid, now);
        anyNew = true;
      }
    }
    // The CSS animation has a bounded iteration count, but leaves the card
    // with a static shimmer-gradient background once it stops. Force a
    // re-render after the TTL so isRecentlyBorn() returns false and the
    // `.recently-born` class + gradient both come off cleanly.
    if (anyNew) {
      setTimeout(() => {
        if (sharedState.$convSearch) (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value));
        else (0, hostServices.renderSidebar)(sharedState.conversationsData);
      }, 30500);
    }
  }

  function renderPopoutMissingConversation() {
    if (sharedState._popoutMissingShown || sharedState.currentConversation) return;
    const view = (0, sharedState.getConvView)();
    if (!view) return;
    sharedState._popoutMissingShown = true;
    (0, sharedState.updatePaneHeader)((0, sharedState.activePaneId)(), null, {
      category: 'not found',
      title: (sharedState.CONV_POPOUT_TARGET || '').slice(0, 8) || 'Conversation',
    });
    view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Conversation not found: '
      + (0, sharedState.escapeHtml)(sharedState.CONV_POPOUT_TARGET || '(missing id)')
      + '</div>';
  }

  function maybeSelectPopoutConversation(opts = {}) {
    if (!sharedState.CONV_POPOUT_MODE || !sharedState.CONV_POPOUT_TARGET) return false;
    const row = (0, sharedState.findPopoutConversationRow)();
    if (!row) {
      if (opts.allowMissing) renderPopoutMissingConversation();
      return false;
    }
    const convId = (0, sharedState.popoutConversationIdForRow)(row);
    (0, sharedState.setPopoutTitle)(row);
    if (sharedState.currentConversation === convId || sharedState._popoutSelectInFlight) return true;
    sharedState._popoutMissingShown = false;
    sharedState._popoutSelectInFlight = true;
    Promise.resolve((0, sharedState.selectConversation)(convId, (0, sharedState.activePaneId)()))
      .catch(err => {
        const view = (0, sharedState.getConvView)();
        if (view) {
          view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Failed to load conversation: '
            + (0, sharedState.escapeHtml)(err && err.message ? err.message : String(err))
            + '</div>';
        }
      })
      .finally(() => { sharedState._popoutSelectInFlight = false; });
    return true;
  }

  function carryFlowPendingSpawnNode(placeholder, realCard) {
    if (!placeholder || !realCard) return;
    try {
      const placeholderSessionId = placeholder.session_id || placeholder.id;
      const realSessionId = realCard.session_id || realCard.id;
      if (!placeholderSessionId || !realSessionId) return;
      const placeholderNodeId = (0, sharedState.flowNodeKey)('session', placeholderSessionId);
      const realNodeId = (0, sharedState.flowNodeKey)('session', realSessionId);
      const savedParent = sharedState.flowNodeParents[placeholderNodeId] || placeholder.flow_parent_node_id || '';
      const savedPos = sharedState.flowNodePositions[placeholderNodeId] || placeholder.flow_node_position || null;
      if (savedParent) sharedState.flowNodeParents[realNodeId] = savedParent;
      if (savedPos && Number.isFinite(Number(savedPos.x)) && Number.isFinite(Number(savedPos.y))) {
        sharedState.flowNodePositions[realNodeId] = {
          x: Math.round(Number(savedPos.x)),
          y: Math.round(Number(savedPos.y)),
        };
      }
      delete sharedState.flowNodeParents[placeholderNodeId];
      delete sharedState.flowNodePositions[placeholderNodeId];
      persistFlowNodeParents();
      persistFlowNodePositions();
    } catch (_) {}
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

  function followPendingSpawnTicket(rows) {
    if (!(0, sharedState.ideaTicketsOn)()) return;
    const previous = [...sharedState.conversationsData, ...sharedState.pendingSpawns.values()];
    const selected = previous.find(c => c && (c.id === sharedState.currentConversation
      || c.session_id === sharedState.currentConversation));
    if (!selected || !selected.idea_ticket || !selected.idea_ticket.pending
        || selected.no_auto_select || selected.external_spawn) return;
    const real = rows.find(c => c && (
      (c.session_id && (c.session_id === selected.session_id
        || c.session_id === selected.expected_session_id))
      || (c.spawn_pid && selected.spawn_pid && String(c.spawn_pid) === String(selected.spawn_pid))));
    const ticket = real && real.idea_ticket;
    if (!ticket || !ticket.ref || ticket.pending) return;
    const target = ticket.queue === 'SMALL-BUGS' ? 'smallbugs' : 'ideas';
    // Follow only the selected launch while its provisional tab is still
    // open. A background classification must not override manual navigation.
    if (_currentSidebarTabKey() !== 'ideas' || target === 'ideas') return;
    try { window.CCCNavigation.setTab(target); } catch (_) {}
    (0, sharedState.showConvToast)('Filed in Small bugs');
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
    if (source === 'codex') trackSpawnView(card);
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

  async function loadConversationList() {
    try {
      const repoPath = (0, sharedState.popoutRepoPath)();
      if (!repoPath) {
        sharedState.currentRepoBacklogData = [];
        sharedState.conversationsLoaded = true;
        sharedState._convInitialLoadDone = true;
        (0, sharedState.loadAttentionList)();
        (0, sharedState.hideLoadingOverlay)();
        (0, sharedState._markFirstSessionsLoaded)();
        (0, hostServices.renderArchiveList)(sharedState.$convSearch.value);
        return;
      }
      const res = await (0, sharedState.backgroundApiFetch)((0, sharedState.repoUrl)('/api/sessions', repoPath));
      const fresh = await res.json();
      if (!res.ok) throw new Error((fresh && fresh.error) || ('HTTP ' + res.status));
      if ((0, sharedState.claudeFastSpawnAwaitingPaint)()) return;
      sharedState.currentRepoBacklogData = Array.isArray(fresh)
        ? fresh.filter(c => c && c.source === 'backlog').map(c => Object.assign({}, c))
        : [];
      // Preserve user-set fields (e.g. display_name overrides) from the
      // previous snapshot — the server can re-derive a default name on the
      // next scan and clobber a rename otherwise.
      const oldBySessionId = {};
      for (const c of (sharedState.conversationsData || [])) oldBySessionId[c.session_id] = c;
      followPendingSpawnTicket(fresh);
      for (const c of fresh) {
        const old = oldBySessionId[c.session_id];
        if (old && old.name_overridden && !c.name_overridden) {
          c.display_name = old.display_name;
          c.name_overridden = true;
        }
      }
      // Drop pending placeholders whose real session has appeared (matched by spawn_pid).
      // When we do the swap, carry the placeholder's column preference over to
      // the real session via the sticky mechanism so the card doesn't jump on
      // first render. Also pre-stamp _firstSeenSessions so `.recently-born`
      // kicks in on the very first render of the real card (no frame where the
      // card has neither pending-spawn nor recently-born).
      // If the user has a placeholder selected, capture the placeholder→real
      // transition so we can carry the right-pane selection over in place
      // (handled below, after the session-id maps are rebuilt).
      let _selectionSwap = null;
      const realPids = new Set(fresh.filter(c => c.spawn_pid).map(c => String(c.spawn_pid)));
      const realSids = new Set(fresh.map(c => String(c.session_id || c.id || '')));
      for (const pid of Array.from(sharedState.pendingSpawns.keys())) {
        const placeholder = sharedState.pendingSpawns.get(pid);
        // In-band-id engines (devin acp, kimi) return the real session_id in
        // the spawn response, and the row can surface before it carries a
        // matching spawn_pid — reconcile on the expected id too, or the
        // placeholder sits next to the real row until the not-ack deadline.
        const expectedSid = String((placeholder && placeholder.expected_session_id) || '');
        const sidMatched = !!(expectedSid && realSids.has(expectedSid));
        if (realPids.has(String(pid)) || sidMatched) {
          const defaultPlaceholderId = 'spawning-' + pid;
          const placeholderId = (placeholder && placeholder.id) || defaultPlaceholderId;
          const placeholderCol = sharedState.columnOverrides[placeholderId] || sharedState.columnOverrides[defaultPlaceholderId];
          const realCard = sidMatched
            ? fresh.find(c => String(c.session_id || c.id || '') === expectedSid)
            : fresh.find(c => String(c.spawn_pid) === String(pid));
          if (realCard && realCard.session_id) {
            carryFlowPendingSpawnNode(placeholder, realCard);
            sharedState._firstSeenSessions.set(realCard.session_id, Date.now());
            if (placeholderCol && !realCard.verified && !realCard.archived) {
              sharedState._stickyInitialCol.set(realCard.session_id, {
                col: placeholderCol,
                signals: _signalSnapshot(realCard),
              });
            }
            if (sharedState.currentConversation === placeholderId) {
              _selectionSwap = { realCard };
            }
            (0, sharedState.resolvePlaceholderPanes)([placeholderId, defaultPlaceholderId], realCard);
          }
          delete sharedState.columnOverrides[placeholderId];
          delete sharedState.columnOverrides[defaultPlaceholderId];
          try {
            (0, sharedState._clientLog)('[EXT-SPAWN] live-handoff ' + placeholderId + ' -> '
              + ((realCard && realCard.session_id) || '?')
              + ' inArchive=' + _hasSid(sharedState.archiveData, (realCard && realCard.session_id) || ''));
          } catch (_) {}
          sharedState.pendingSpawns.delete(pid);
        }
      }
      // Stamp first-seen timestamps for any session_ids that weren't in the
      // previous snapshot — drives the `.recently-born` glow. No-op on the
      // very first load (flag below flips after this block).
      stampFreshSessionsAsBorn(fresh);
      // Keep still-pending placeholders on top until they materialize.
      const placeholders = Array.from(sharedState.pendingSpawns.values());
      sharedState.conversationsData = [...placeholders, ...fresh];
      reconcilePendingNewSessionObjectAssignments();
      (0, sharedState.clearInputDraftKeyCache)();
      // Re-apply any in-flight archive/verify overrides so an /api/sessions
      // response that started before a click can't briefly un-archive a card.
      (0, sharedState.applyOptimisticOverrides)(sharedState.conversationsData);
      sharedState.conversationsLoaded = true;
      sharedState._convInitialLoadDone = true;
      // Cache session IDs / cwds / sources so Resume+Jump work
      sharedState.sessionIdByConv = {};
      sharedState.sessionCwdByConv = {};
      sharedState.sessionCwdExistsByConv = {};
      sharedState.sessionSourceByConv = {};
      sharedState.sessionSpawnPidByConv = {};
      for (const c of sharedState.conversationsData) {
        if (c.session_id) sharedState.sessionIdByConv[c.id] = c.session_id;
        if (c.session_cwd) sharedState.sessionCwdByConv[c.id] = c.session_cwd;
        sharedState.sessionCwdExistsByConv[c.id] = !!c.session_cwd_exists;
        sharedState.sessionSourceByConv[c.id] = c.source || 'interactive';
        if (c.spawn_pid) sharedState.sessionSpawnPidByConv[c.id] = c.spawn_pid;
      }
      // Carry the right-pane selection across the placeholder→real swap.
      // selectConversation() would flash a "Loading..." empty state and feel
      // like a switch; instead we re-bind streams and session pointers in
      // place so the right pane reads as the same conversation throughout.
      if (_selectionSwap) {
        const real = _selectionSwap.realCard;
        const keepFirstResponseStream = !!(real.session_id
          && sharedState._spawnLiveSid === real.session_id
          && sharedState.claudeSpawnAwaitingFirstPaint.has(real.session_id));
        sharedState.currentConversation = real.id;
        try { if (!sharedState.CONV_POPOUT_MODE) localStorage.setItem('ccc-last-conv', real.id); } catch (_) {}
        if (typeof sharedState.stopConvStream === 'function') (0, sharedState.stopConvStream)();
        if (!keepFirstResponseStream) (0, sharedState.stopSpawnStream)();
        (0, sharedState.setCurrentSession)(
          real.source || 'interactive',
          real.session_id,
          real.session_cwd,
          real.session_cwd_exists,
          real.spawn_pid,
          (0, sharedState.rowRepoPath)(real) || (0, sharedState.popoutRepoPath)()
        );
        // Reset the per-conv tail cursor so fetchConversationEvents pulls the
        // real JSONL from the start. Tool-grouping state must reset too —
        // otherwise the next tool block tries to extend a group that belongs
        // to the placeholder's (empty) view.
        sharedState.convLastLine = 0;
        sharedState._firstUserMsgRendered = false;
        sharedState._currentToolGroup = null;
        sharedState._currentToolCount = 0;
        (0, sharedState.setCopyableSessionId)(sharedState.$cpSessionId, real.session_id || '');
        (0, sharedState.fetchConversationEvents)();
        (0, sharedState.startConvStream)();
        if (!keepFirstResponseStream
            && real.session_id && real.source !== 'codex' && real.source !== 'cursor') {
          (0, sharedState.startSpawnStream)(
            real.session_id,
            (0, sharedState.activePaneId)(),
            sharedState.claudeSpawnAwaitingFirstPaint.has(real.session_id) ? { replay: true } : undefined
          );
        }
        (0, sharedState.updateSplitInputBar)();
        (0, sharedState.updateSplitToolbar)();
      }
      if (sharedState.archiveLoaded) (0, hostServices.renderArchiveList)(sharedState.$convSearch.value);
      (0, sharedState.loadAttentionList)();  // piggy-back on the same refresh cycle
      (0, sharedState.hideLoadingOverlay)();  // first render has happened - reveal the UI
      (0, sharedState._markFirstSessionsLoaded)();  // unblock archive boot kick (see wireArchiveMode)
      // Auto-restore the last-opened card on first load. Only fires when
      // the user hasn't already clicked something — refresh-after-refresh
      // shouldn't yank a card out from under an active selection.
      if (sharedState.CONV_POPOUT_MODE) {
        maybeSelectPopoutConversation();
      } else {
        restoreLastViewOrConversation();
      }
    } catch (err) {
      sharedState._convListRenderSig = null;
      sharedState.$convList.innerHTML = '<div class="empty-state" style="height:auto;padding:20px;font-size:13px;">Failed to load sessions: ' + (0, sharedState.escapeHtml)(err.message) + '</div>';
      (0, sharedState.hideLoadingOverlay)();  // even on error - don't leave the user stuck on a spinner
      (0, sharedState._markFirstSessionsLoaded)();  // even on error - don't pin the archive load
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

  function persistFlowNodePositions() {
    try { localStorage.setItem('ccc-flow-node-positions', JSON.stringify(sharedState.flowNodePositions)); } catch (_) {}
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

  function reconcilePendingNewSessionObjectAssignments() {
    const pending = loadPendingNewSessionObjectAssignments();
    const keys = Object.keys(pending);
    if (!keys.length) return;
    const now = Date.now();
    let changed = false;
    for (const key of keys) {
      const rec = pending[key] || {};
      if (now - Number(rec.created_at || 0) > 30 * 60 * 1000) {
        delete pending[key];
        changed = true;
        continue;
      }
      const row = (sharedState.conversationsData || []).find(c => String(c.spawn_pid || c.pid || '') === key);
      if (!row || row.pending_spawn) continue;
      const sid = row.session_id || row.id;
      if (!sid || /^spawning-/.test(String(sid))) continue;
      assignSessionNodeToParent(sid, rec.parent_node || (0, sharedState.flowNodeKey)('object', sharedState.NEW_SESSION_DEFAULT_OBJECT_ID));
      delete pending[key];
      changed = true;
    }
    if (changed) savePendingNewSessionObjectAssignments(pending);
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

  function gcFallbackName(shortSid) {
    const key = String(shortSid || '').substring(0, 8).toLowerCase();
    if (!key) return 'Agent-?';
    if (!sharedState._gcAgentFallbackNames[key]) {
      sharedState._gcAgentFallbackNames[key] = 'Agent-' + (Object.keys(sharedState._gcAgentFallbackNames).length + 1);
    }
    return sharedState._gcAgentFallbackNames[key];
  }

  function gcDisplayName(sid) {
    if (!sid) return 'Agent-?';
    const sidStr = String(sid).trim();
    if (sidStr.toLowerCase() === 'human') return 'Human';
    if (sidStr.toLowerCase() === 'system') return 'system';
    if (/^agent-\d+$/i.test(sidStr)) return sidStr;

    let fullSid = (sharedState._gcReaderSessionIds || []).find(id => id.toLowerCase() === sidStr.toLowerCase() || id.toLowerCase().startsWith(sidStr.toLowerCase())) || sidStr;
    const shortSid = fullSid.substring(0, 8).toLowerCase();

    if (sharedState._gcReaderNameMap && sharedState._gcReaderNameMap[fullSid]) {
      const name = sharedState._gcReaderNameMap[fullSid];
      const looksLikeShortHash = /^[0-9a-f]{8}$/i.test(String(name).trim());
      const looksLikeUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(String(name).trim());
      const looksLikeHashUuid = /^[0-9a-f]{8}:\s*[0-9a-f]{8}-[0-9a-f]{4}-/i.test(String(name).trim());
      if (!looksLikeShortHash && !looksLikeUuid && !looksLikeHashUuid) {
        return name;
      }
    }

    const conv = (sharedState.conversationsData || []).find(c => c && (c.session_id === fullSid || c.id === fullSid));
    if (conv) {
      if (conv.display_name && !/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(conv.display_name)) {
        return conv.display_name;
      }
      if (conv.ai_title && !/^[0-9a-f]{8}-[0-9a-f]{4}-/i.test(conv.ai_title)) {
        return conv.ai_title;
      }
    }

    if (sharedState._gcAgentFallbackNames[shortSid]) {
      return sharedState._gcAgentFallbackNames[shortSid];
    }

    return gcFallbackName(shortSid);
  }

  function gcInitials(name) {
    if (!name) return '?';
    const nameStr = String(name).trim();
    if (nameStr.toLowerCase() === 'human') return 'H';
    if (nameStr.toLowerCase() === 'system') return 'SYS';
    const clean = nameStr.replace(/\(.*?\)/g, '').replace(/[^a-zA-Z0-9\s\-_]/g, '').trim();
    const words = clean.split(/[\s\-_]+/);
    if (words.length >= 2) {
      return (words[0][0] + words[1][0]).toUpperCase();
    }
    if (words.length === 1 && words[0]) {
      const w = words[0];
      if (w.length >= 2) {
        const m = w.match(/^Agent(\d+)$/i);
        if (m) return 'A' + m[1];
        return w.slice(0, 2).toUpperCase();
      }
      return w.toUpperCase();
    }
    return '?';
  }

  function extractHash(speakerOrHeading) {
    if (!speakerOrHeading) return '';
    const s = String(speakerOrHeading).trim();
    const parenMatch = s.match(/\(([0-9a-fA-F]{8})\)/);
    if (parenMatch) return parenMatch[1];
    const colonMatch = s.match(/^([0-9a-fA-F]{8}):/);
    if (colonMatch) return colonMatch[1];
    if (/^[0-9a-fA-F]{8}$/.test(s) || /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-/.test(s)) {
      return s;
    }
    const trailingMatch = s.match(/\b([0-9a-fA-F]{8})\b/);
    if (trailingMatch) return trailingMatch[1];
    return s;
  }

  function buildAgentFallbackNames(content, sessionIds) {
    const text = String(content || '');
    const matches = Array.from(text.matchAll(
      /^#{2,3}[ \t]+(.+?(?:—[ \t]+(?:[0-9a-fA-F]{8}(?::|\b)|Human\b|system(?::|\b)).*|.*\([0-9a-fA-F]{8}\)))[ \t]*$/gm
    ));
    const firstSeenHashes = [];

    const extractHashFromHeading = (heading) => {
      if (/\s+—\s+/.test(heading)) {
        const parts = heading.split(/\s+—\s+/);
        const lastPart = parts[parts.length - 1] || '';
        const clean = lastPart.split(':')[0].trim();
        return clean;
      } else {
        const m = heading.match(/\(([0-9a-fA-F]{8})\)\s*$/);
        if (m) return m[1];
      }
      return '';
    };

    matches.forEach(m => {
      const heading = (m[1] || '').trim();
      const hash = extractHashFromHeading(heading);
      if (hash && hash.toLowerCase() !== 'human' && hash.toLowerCase() !== 'system') {
        const short = hash.toLowerCase().substring(0, 8);
        if (!firstSeenHashes.includes(short)) {
          firstSeenHashes.push(short);
        }
      }
    });

    (sessionIds || []).forEach(sid => {
      const short = sid.substring(0, 8).toLowerCase();
      if (!firstSeenHashes.includes(short)) {
        firstSeenHashes.push(short);
      }
    });

    const fallbackNames = {};
    firstSeenHashes.forEach((short, idx) => {
      fallbackNames[short] = 'Agent-' + (idx + 1);
    });

    sharedState._gcAgentFallbackNames = fallbackNames;
  }

  function getParticipantColor(sid, name) {
    if (name && name.toLowerCase() === 'human') return 'green';
    const sids = sharedState._gcReaderSessionIds || [];
    const sidStr = sid ? String(sid).toLowerCase() : '';
    const idx = sidStr
      ? sids.findIndex(id => id.toLowerCase() === sidStr || id.toLowerCase().startsWith(sidStr))
      : sids.findIndex(id => gcDisplayName(id) === name);
    const colorPalette = ['accent', 'purple', 'orange', 'cyan', 'red', 'yellow'];
    if (idx === -1) return 'accent';
    return colorPalette[idx % colorPalette.length];
  }

  function formatWaitingOn(names) {
    if (!names || names.length === 0) return 'All participants have replied';
    if (names.length === 1) return `Waiting on ${names[0]} to reply`;
    if (names.length === 2) return `Waiting on ${names[0]} and ${names[1]} to reply`;
    return `Waiting on ${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]} to reply`;
  }

  function updateGcInfoBar(data) {
    const infoBar = document.getElementById('gcInfoBar');
    if (!infoBar) return;

    const now = Date.now();
    const lastAct = data.orchestrator_last_activity_at || data.mtime || (now / 1000);
    const isActive = data.status === 'active';
    const minutesSinceActivity = (now / 1000 - lastAct) / 60;

    let liveHtml = '';
    if (isActive && minutesSinceActivity < 5) {
      liveHtml = '<span class="gc-live-indicator is-live" title="Active and updating"><span class="gc-live-dot"></span>LIVE</span>';
    } else {
      const relTime = gcRelativeTime(new Date(lastAct * 1000).toISOString());
      liveHtml = `<span class="gc-live-indicator is-idle" title="Last activity was ${relTime}">Idle · ${relTime}</span>`;
    }

    let startedHtml = '';
    const text = String(data.content || '');
    const match = text.match(/^##\s+(.+?)—\s+(?:[0-9a-fA-F]{8}|Human\b).*$/m);
    if (match && match[1]) {
      const startedAt = match[1].trim();
      const relativeStarted = gcRelativeTime(startedAt);
      startedHtml = `<span class="gc-started-time" title="${(0, sharedState.escapeAttr)(startedAt)}">Started ${relativeStarted}</span>`;
    }

    const modeBadge = `<span class="gc-mode-badge">${(0, sharedState.escapeHtml)(data.mode || 'topic')}</span>`;

    const sids = data.session_ids || [];
    const waiting = data.waiting || {};
    const waitingOnHashes = waiting.waiting_on_hashes || [];

    const chipsHtml = sids.map(sid => {
      const name = gcDisplayName(sid);
      const color = getParticipantColor(sid, name);
      const isOnline = !waitingOnHashes.some(h => sid.toLowerCase().startsWith(h.toLowerCase()));
      const onlineDot = `<span class="gc-online-dot ${isOnline ? 'is-online' : 'is-waiting'}" title="${isOnline ? 'Online / Idle' : 'Waiting for reply'}"></span>`;
      const initials = gcInitials(name);

      return `<button type="button" class="gc-part-chip" data-gc-part-sid="${(0, sharedState.escapeAttr)(sid)}" title="Scroll to participant details / Nudge">`
        + `<span class="gc-part-dot" style="background-color: var(--${color})"></span>`
        + `<span class="gc-part-initials">${(0, sharedState.escapeHtml)(initials)}</span>`
        + `<span class="gc-part-name">${(0, sharedState.escapeHtml)(name)}</span>`
        + onlineDot
        + `</button>`;
    }).join('');

    infoBar.innerHTML = `<div class="gc-info-left">${liveHtml} · ${modeBadge} · ${startedHtml}</div>`
      + `<div class="gc-info-right"><div class="gc-part-strip">${chipsHtml}</div></div>`;

    infoBar.querySelectorAll('.gc-part-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        const sid = btn.getAttribute('data-gc-part-sid');
        const card = document.querySelector(`.gco-part-card[data-gc-part-sid="${CSS.escape(sid)}"]`);
        if (card) {
          card.scrollIntoView({ behavior: 'smooth', block: 'center' });
          card.classList.remove('gco-highlight');
          void card.offsetWidth;
          card.classList.add('gco-highlight');
          setTimeout(() => card.classList.remove('gco-highlight'), 2000);
        }
      });
    });
  }

  function showUpdatedJustNowTick() {
    const header = document.querySelector('.gc-reader-header');
    if (!header) return;
    let tick = header.querySelector('.gc-updated-tick');
    if (tick) tick.remove();
    tick = document.createElement('span');
    tick.className = 'gc-updated-tick';
    tick.textContent = 'updated just now';
    header.appendChild(tick);
    setTimeout(() => {
      tick.classList.add('fade-out');
      setTimeout(() => tick.remove(), 500);
    }, 2500);
  }

  function startReplay(data) {
    if (!data || !data.content) return;
    _gcReplayClearHero();
    sharedState._gcReplayActive = true;
    sharedState._gcReplayPaused = false;
    sharedState._gcReplaySpeed = 1;
    sharedState._gcReplayMsgIndex = 0;
    sharedState._gcReplayWordIndex = 0;
    sharedState._gcReplayHumanTypeIndex = 0;
    sharedState._gcReplayHumanSendPending = false;
    sharedState._gcReplayHumanSent = false;
    sharedState._gcReplayData = data;

    const reader = document.getElementById('gcReader');
    if (!reader) return;

    let controls = document.getElementById('gcReplayControls');
    if (!controls) {
      controls = document.createElement('div');
      controls.id = 'gcReplayControls';
      controls.className = 'gc-replay-controls';
      const infoBar = document.getElementById('gcInfoBar');
      if (infoBar) infoBar.after(controls);
      else reader.prepend(controls);
    }

    renderReplayControls();

    const body = document.getElementById('gcReaderBody');
    if (body) {
      body.innerHTML = '';
      body.classList.add('gc-in-replay');
    }

    const text = String(data.content || '');
    const headingMatches = Array.from(text.matchAll(
      /^#{2,3}[ \t]+(.+?(?:—[ \t]+(?:[0-9a-fA-F]{8}(?::|\b)|Human\b|system(?::|\b)).*|.*\([0-9a-fA-F]{8}\)))[ \t]*$/gm
    ));

    sharedState._gcReplayMessages = [];

    let preamble = '';
    if (headingMatches.length > 0) {
      preamble = text.slice(0, headingMatches[0].index).trim();
    } else {
      preamble = text.trim();
    }

    if (preamble) {
      if (body) {
        body.innerHTML = '<details class="gc-preamble-disclosure" open><summary class="gc-preamble-summary">Chat details</summary><div class="gc-chat-preamble">' + (0, sharedState.renderMarkdown)(preamble) + '</div></details>';
      }
    }

    const speakerToSide = new Map();
    const speakerToColor = new Map();
    let nextAgentSlot = 0;
    const sideForSpeaker = (speakerLabel) => {
      const isHuman = /\bHuman\b/i.test(speakerLabel);
      if (isHuman) {
        speakerToColor.set(speakerLabel, 'green');
        return 'right';
      }
      if (speakerToSide.has(speakerLabel)) return speakerToSide.get(speakerLabel);
      const side = (nextAgentSlot % 2 === 0) ? 'left' : 'right-agent';
      const colorPalette = ['accent', 'purple', 'orange', 'cyan', 'red', 'yellow'];
      speakerToColor.set(speakerLabel, colorPalette[nextAgentSlot % colorPalette.length]);
      nextAgentSlot += 1;
      speakerToSide.set(speakerLabel, side);
      return side;
    };
    const colorForSpeaker = (speakerLabel) => speakerToColor.get(speakerLabel) || 'accent';

    for (let i = 0; i < headingMatches.length; i++) {
      const match = headingMatches[i];
      const heading = (match[1] || '').trim();
      const nextIndex = i + 1 < headingMatches.length ? headingMatches[i + 1].index : text.length;
      const rawBody = text.slice(match.index + match[0].length, nextIndex);
      const bodyText = rawBody.replace(/^\s*---\s*$/gm, '').trim();

      let when = '';
      let speaker = heading;
      if (/\s+—\s+/.test(heading)) {
        const parts = heading.split(/\s+—\s+/);
        when = parts.length > 1 ? parts.shift() : '';
        speaker = parts.length ? parts.join(' — ') : heading;
      } else {
        speaker = heading.replace(/\s*\([0-9a-fA-F]{8}\)\s*$/, '').replace(/`/g, '').trim() || heading;
      }
      const isSystem = /^\s*system\b/i.test(speaker);
      const speakerHash = extractHash(speaker);
      const displayName = gcDisplayName(speakerHash);
      const isHuman = !isSystem && /\bHuman\b/i.test(displayName);
      const side = isSystem ? 'full' : sideForSpeaker(displayName);
      const color = isSystem ? 'muted' : getParticipantColor(speakerHash, displayName);
      const isPing = isSystem && /pinged/i.test(bodyText);

      const rawHtml = bodyText ? markSystemBlockquotes((0, sharedState.renderMarkdown)(bodyText)) : '<em class="gc-message-empty">(no text)</em>';
      const { html: wrappedHtml, count: wordCount } = (0, sharedState._wrapReplayWordsInHtml)(rawHtml, 'gc-replay-word');

      sharedState._gcReplayMessages.push({
        heading,
        speaker,
        when,
        isSystem,
        isHuman,
        side,
        color,
        isPing,
        wrappedHtml,
        wordCount,
        rawBody,
        originalIndex: i
      });
    }

    const prePass = sharedState._gcReplayMessages.map(m => ({ speaker: m.speaker, isSystem: m.isSystem }));
    const allNonSysSet = new Set();
    prePass.forEach(p => { if (!p.isSystem) allNonSysSet.add(p.speaker); });
    sharedState._gcReplayAllNonSys = Array.from(allNonSysSet);
    sharedState._gcReplaySpeakersAfter = prePass.map((_, idx) => {
      const after = new Set();
      for (let j = idx + 1; j < prePass.length; j++) {
        if (!prePass[j].isSystem) after.add(prePass[j].speaker);
      }
      return after;
    });

    // Interleave queue/ticket-appearance events (W22/B4) onto the timeline.
    // Done AFTER the speaker look-ahead sets are built (those index real
    // messages by originalIndex, so splicing pseudo-messages in is safe).
    try {
      sharedState._gcReplayMessages = _mergeReplayQueueEvents(
        sharedState._gcReplayMessages, data.queue_events);
    } catch (_) { /* queue events are best-effort; never block the replay */ }

    sharedState._onReplayKeyDownRef = (e) => {
      if ((0, sharedState.isImeKey)(e)) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        exitReplayMode();
      }
    };
    document.addEventListener('keydown', sharedState._onReplayKeyDownRef, true);

    playNextReplayStep();
  }

  function renderReplayControls() {
    const controls = document.getElementById('gcReplayControls');
    if (!controls) return;

    controls.innerHTML = `
      <button type="button" id="gcReplayStepBackBtn" class="gc-replay-btn gc-replay-stepback-btn" title="Step Back" ${sharedState._gcReplayMsgIndex <= 0 ? 'disabled' : ''}>&lt;</button>
      <button type="button" id="gcReplayPlayPauseBtn" class="gc-replay-btn" title="Play / Pause">
        ${sharedState._gcReplayPaused ? '▶ Play' : '⏸ Pause'}
      </button>
      <div class="gc-replay-speed-group">
        <button type="button" class="gc-replay-speed-btn ${sharedState._gcReplaySpeed === 1 ? 'is-active' : ''}" data-speed="1">1×</button>
        <button type="button" class="gc-replay-speed-btn ${sharedState._gcReplaySpeed === 2 ? 'is-active' : ''}" data-speed="2">2×</button>
        <button type="button" class="gc-replay-speed-btn ${sharedState._gcReplaySpeed === 4 ? 'is-active' : ''}" data-speed="4">4×</button>
      </div>
      <button type="button" id="gcReplaySkipBtn" class="gc-replay-btn gc-replay-skip-btn" title="Skip to Live">Skip to Live</button>
    `;

    const stepBackBtn = controls.querySelector('#gcReplayStepBackBtn');
    stepBackBtn.addEventListener('click', () => {
      stepReplayBack();
    });

    const playPauseBtn = controls.querySelector('#gcReplayPlayPauseBtn');
    playPauseBtn.addEventListener('click', () => {
      sharedState._gcReplayPaused = !sharedState._gcReplayPaused;
      playPauseBtn.textContent = sharedState._gcReplayPaused ? '▶ Play' : '⏸ Pause';
      if (!sharedState._gcReplayPaused) {
        playNextReplayStep();
      } else {
        if (sharedState._gcReplayTimeout) {
          clearTimeout(sharedState._gcReplayTimeout);
          sharedState._gcReplayTimeout = null;
        }
      }
    });

    controls.querySelectorAll('.gc-replay-speed-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        sharedState._gcReplaySpeed = Number(btn.getAttribute('data-speed'));
        controls.querySelectorAll('.gc-replay-speed-btn').forEach(b => b.classList.remove('is-active'));
        btn.classList.add('is-active');
      });
    });

    const skipBtn = controls.querySelector('#gcReplaySkipBtn');
    skipBtn.addEventListener('click', () => {
      exitReplayMode();
    });
  }

  function stepReplayBack() {
    if (!sharedState._gcReplayActive || sharedState._gcReplayMsgIndex <= 0) return;

    if (sharedState._gcReplayTimeout) {
      clearTimeout(sharedState._gcReplayTimeout);
      sharedState._gcReplayTimeout = null;
    }
    sharedState._gcReplayPaused = true;
    _gcReplayClearHero();

    const humanInput = document.getElementById('gcHumanInput');
    if (humanInput && humanInput.classList.contains('gc-replay-typing')) {
      humanInput.value = '';
      humanInput.classList.remove('gc-replay-typing');
    }
    sharedState._gcReplayHumanTypeIndex = 0;
    sharedState._gcReplayHumanSendPending = false;
    sharedState._gcReplayHumanSent = false;

    const targetIdx = Math.max(0, sharedState._gcReplayMsgIndex - 1);
    const body = document.getElementById('gcReaderBody');
    if (body) {
      body.querySelectorAll('[data-replay-idx]').forEach((el) => {
        if (Number(el.getAttribute('data-replay-idx')) >= targetIdx) el.remove();
      });
    }

    sharedState._gcReplayMsgIndex = targetIdx;
    sharedState._gcReplayWordIndex = 0;

    renderReplayControls();
  }

  function exitReplayMode() {
    sharedState._gcReplayActive = false;
    sharedState._gcReplayPaused = false;
    _gcReplayClearHero();
    if (sharedState._gcReplayTimeout) {
      clearTimeout(sharedState._gcReplayTimeout);
      sharedState._gcReplayTimeout = null;
    }
    const controls = document.getElementById('gcReplayControls');
    if (controls) controls.remove();

    if (sharedState._onReplayKeyDownRef) {
      document.removeEventListener('keydown', sharedState._onReplayKeyDownRef, true);
      sharedState._onReplayKeyDownRef = null;
    }

    const body = document.getElementById('gcReaderBody');
    if (body) {
      body.classList.remove('gc-in-replay');
    }

    // Skipping/exiting mid "human is typing" animation shouldn't leave a
    // half-typed draft sitting in the real reply box.
    const humanInput = document.getElementById('gcHumanInput');
    if (humanInput && humanInput.classList.contains('gc-replay-typing')) {
      humanInput.value = '';
      humanInput.classList.remove('gc-replay-typing');
    }
    sharedState._gcReplayHumanTypeIndex = 0;
    sharedState._gcReplayHumanSendPending = false;
    sharedState._gcReplayHumanSent = false;

    sharedState._gcLastMtime = null;
    pollGroupChatReader();
  }

  function _gcReplayClearHero() {
    if (!sharedState._gcReplayHero) return;
    if (sharedState._gcReplayHero.fallbackTimer) clearTimeout(sharedState._gcReplayHero.fallbackTimer);
    if (sharedState._gcReplayHero.target) sharedState._gcReplayHero.target.classList.remove('gc-replay-hero-target');
    if (sharedState._gcReplayHero.backdrop) sharedState._gcReplayHero.backdrop.remove();
    sharedState._gcReplayHero = null;
  }

  function _gcReplayCreateHero(msgEl, replayIdx) {
    if (!msgEl || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches)) return;
    _gcReplayClearHero();

    const rect = msgEl.getBoundingClientRect();
    const backdrop = document.createElement('div');
    backdrop.className = 'gc-replay-hero-backdrop';
    backdrop.setAttribute('aria-hidden', 'true');

    const hero = msgEl.cloneNode(true);
    hero.removeAttribute('data-replay-idx');
    hero.classList.remove('gc-replay-hero-target');
    hero.classList.add('gc-replay-hero');
    hero.style.width = rect.width + 'px';
    backdrop.appendChild(hero);

    msgEl.classList.add('gc-replay-hero-target');
    document.body.appendChild(backdrop);
    sharedState._gcReplayHero = { backdrop, hero, target: msgEl, replayIdx, holding: false, landing: false, fallbackTimer: null };
  }

  function _gcReplayMessagePauseMs(msg) {
    const totalChars = msg && msg.rawBody ? msg.rawBody.length : 100;
    return Math.max(2500, Math.min(4500, totalChars * 6)) / sharedState._gcReplaySpeed;
  }

  function _gcReplayHoldThenLandHero(msgEl, msg, replayIdx) {
    const state = sharedState._gcReplayHero;
    if (!state || state.target !== msgEl || state.replayIdx !== replayIdx || state.landing) return;

    // Pausing replay clears the shared timeout. On resume, restart this
    // readability hold instead of leaving the fully-revealed hero stranded.
    if (state.holding && sharedState._gcReplayTimeout) return;
    state.holding = true;
    sharedState._gcReplayTimeout = setTimeout(() => {
      sharedState._gcReplayTimeout = null;
      if (sharedState._gcReplayHero !== state || !sharedState._gcReplayActive || sharedState._gcReplayPaused) return;
      state.holding = false;
      _gcReplayLandHero(msgEl, replayIdx);
    }, _gcReplayMessagePauseMs(msg));
  }

  function _gcReplayLandHero(msgEl, replayIdx) {
    const state = sharedState._gcReplayHero;
    if (!state || state.target !== msgEl || state.replayIdx !== replayIdx || state.landing) return;
    state.landing = true;

    const heroRect = state.hero.getBoundingClientRect();
    const targetRect = msgEl.getBoundingClientRect();
    const heroCenterX = heroRect.left + heroRect.width / 2;
    const targetCenterX = targetRect.left + targetRect.width / 2;
    const dx = heroCenterX - targetCenterX;
    const dy = heroRect.top - targetRect.top;
    const duration = Math.max(180, 620 / sharedState._gcReplaySpeed);

    // Rebase the lower-third clone onto the target's coordinate system without
    // moving it visually. Both use a top-center origin, so the top edge remains
    // stable while text reveals and then follows a clean path into the slot.
    state.hero.style.transition = 'none';
    state.hero.style.left = targetRect.left + 'px';
    state.hero.style.top = targetRect.top + 'px';
    state.hero.style.width = targetRect.width + 'px';
    state.hero.style.transform = `translate(${dx}px, ${dy}px) scale(2)`;
    void state.hero.offsetWidth;

    let finished = false;
    const finish = () => {
      if (finished || sharedState._gcReplayHero !== state) return;
      finished = true;
      state.target.dataset.replayHeroLanded = 'true';
      _gcReplayClearHero();
      if (sharedState._gcReplayActive) playNextReplayStep();
    };
    state.hero.addEventListener('transitionend', (event) => {
      if (event.propertyName === 'transform') finish();
    }, { once: true });
    state.fallbackTimer = setTimeout(finish, duration + 120);

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (sharedState._gcReplayHero !== state) return;
        state.backdrop.classList.add('is-landing');
        state.hero.style.transition = `transform ${duration}ms cubic-bezier(.2,.8,.2,1), box-shadow ${duration}ms ease`;
        state.hero.style.transform = 'translate(0, 0) scale(1)';
        state.hero.style.boxShadow = '0 4px 14px rgba(0, 0, 0, 0.28)';
      });
    });
  }

  function _gcReplayHumanStep(msg) {
    const plainText = (msg.rawBody || '').trim();
    const inputEl = document.getElementById('gcHumanInput');
    if (!inputEl || !plainText) return true;  // nothing to animate - just show the bubble

    if (sharedState._gcReplayHumanSent) {
      // Already typed + sent this message on a prior tick — reset for the
      // NEXT human message and let the caller build the bubble now.
      sharedState._gcReplayHumanSent = false;
      return true;
    }

    if (sharedState._gcReplayHumanTypeIndex < plainText.length) {
      if (sharedState._gcReplayHumanTypeIndex === 0) {
        inputEl.value = '';
        inputEl.classList.add('gc-replay-typing');
        try { inputEl.focus({ preventScroll: true }); } catch (_) { try { inputEl.focus(); } catch (_) {} }
      }
      // A couple characters per tick at 1x reads as typing without
      // crawling on longer messages; speed multiplier shortens the delay.
      const step = sharedState._gcReplaySpeed >= 4 ? 4 : (sharedState._gcReplaySpeed >= 2 ? 2 : 1);
      sharedState._gcReplayHumanTypeIndex = Math.min(plainText.length, sharedState._gcReplayHumanTypeIndex + step);
      inputEl.value = plainText.slice(0, sharedState._gcReplayHumanTypeIndex);
      const charDelay = Math.max(12, Math.min(45, 900 / plainText.length)) / sharedState._gcReplaySpeed;
      sharedState._gcReplayTimeout = setTimeout(playNextReplayStep, charDelay);
      return false;
    }

    if (!sharedState._gcReplayHumanSendPending) {
      // Finished typing — hold a beat so the full text is readable, then
      // "send": clear the box and flash the send button.
      sharedState._gcReplayHumanSendPending = true;
      sharedState._gcReplayTimeout = setTimeout(() => {
        inputEl.value = '';
        inputEl.classList.remove('gc-replay-typing');
        const sendBtn = document.getElementById('gcSendBtn');
        if (sendBtn) {
          sendBtn.classList.add('gc-replay-send-flash');
          setTimeout(() => sendBtn.classList.remove('gc-replay-send-flash'), 240);
        }
        sharedState._gcReplayHumanTypeIndex = 0;
        sharedState._gcReplayHumanSendPending = false;
        sharedState._gcReplayHumanSent = true;
        playNextReplayStep();
      }, Math.max(400, 700 / sharedState._gcReplaySpeed));
      return false;
    }

    // sendPending was already true when we got here — the scheduled timeout
    // above got cancelled mid-flight (a pause landing in that ~700ms beat).
    // Finish the send synchronously rather than stranding the typed text.
    inputEl.value = '';
    inputEl.classList.remove('gc-replay-typing');
    sharedState._gcReplayHumanTypeIndex = 0;
    sharedState._gcReplayHumanSendPending = false;
    return true;
  }

  function _gcReplayParseWhen(when) {
    if (!when) return NaN;
    const s = String(when);
    const dm = s.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (!dm) return NaN;
    const tm = s.match(/(\d{1,2}):(\d{2})(?::(\d{2}))?/);
    const y = +dm[1], mo = +dm[2], d = +dm[3];
    const h = tm ? +tm[1] : 0, mi = tm ? +tm[2] : 0, sec = tm && tm[3] ? +tm[3] : 0;
    const t = new Date(y, mo - 1, d, h, mi, sec).getTime();
    return Number.isFinite(t) ? t : NaN;
  }

  function _mergeReplayQueueEvents(messages, queueEvents) {
    if (!Array.isArray(queueEvents) || !queueEvents.length) return messages;
    // Forward-filled per-message epoch so a message with an unparseable heading
    // inherits the previous message's time and ordering stays monotonic.
    const epochs = [];
    let last = NaN;
    for (const m of messages) {
      const e = _gcReplayParseWhen(m.when);
      if (Number.isFinite(e)) last = e;
      epochs.push(last);
    }
    const finite = epochs.filter(Number.isFinite);
    if (!finite.length) return messages;  // can't interleave without a clock
    const firstEpoch = finite[0];
    const lastEpoch = finite[finite.length - 1];
    const pseudo = queueEvents
      .map(ev => ({
        isQueueEvent: true,
        kind: ev.kind,
        ref: ev.ref || '',
        note: ev.note || '',
        queue: ev.queue || '',
        depthAfter: ev.depth_after,
        when: ev.iso || '',
        _epoch: (typeof ev.ts === 'number' ? ev.ts * 1000 : NaN),
      }))
      .filter(p => Number.isFinite(p._epoch)
        && p._epoch >= firstEpoch && p._epoch <= lastEpoch)
      .sort((a, b) => a._epoch - b._epoch);
    if (!pseudo.length) return messages;
    const merged = [];
    let qi = 0;
    for (let i = 0; i < messages.length; i++) {
      const mEpoch = epochs[i];
      while (qi < pseudo.length && Number.isFinite(mEpoch)
             && pseudo[qi]._epoch <= mEpoch) {
        merged.push(pseudo[qi]); qi++;
      }
      merged.push(messages[i]);
    }
    while (qi < pseudo.length) { merged.push(pseudo[qi]); qi++; }
    return merged;
  }

  function _renderReplayQueueEvent(msg, body) {
    const meta = sharedState._GC_QUEUE_EVENT_META[msg.kind] || { icon: '•', verb: msg.kind || 'event' };
    let el = document.querySelector(`.gc-replay-queue-event[data-replay-idx="${sharedState._gcReplayMsgIndex}"]`);
    if (!el) {
      el = document.createElement('article');
      el.className = 'gc-message gc-replay-queue-event gc-queue-ev-' + (0, sharedState.escapeAttr)(msg.kind || 'event');
      el.setAttribute('data-replay-idx', sharedState._gcReplayMsgIndex);
      const depthChip = (typeof msg.depthAfter === 'number')
        ? '<span class="gc-queue-ev-depth" title="Open tickets in this queue after the event">queue ' + (0, sharedState.escapeHtml)(String(msg.depthAfter)) + '</span>'
        : '';
      const noteHtml = msg.note
        ? '<span class="gc-queue-ev-note" title="' + (0, sharedState.escapeAttr)(msg.note) + '">' + (0, sharedState.escapeHtml)(msg.note) + '</span>'
        : '';
      el.innerHTML = '<div class="gc-queue-ev-inner">'
          + '<span class="gc-queue-ev-icon" aria-hidden="true">' + meta.icon + '</span>'
          + (msg.ref ? '<span class="gc-queue-ev-ref">' + (0, sharedState.escapeHtml)(msg.ref) + '</span>' : '')
          + '<span class="gc-queue-ev-verb">' + (0, sharedState.escapeHtml)(meta.verb) + '</span>'
          + (msg.queue ? '<span class="gc-queue-ev-queue">' + (0, sharedState.escapeHtml)(msg.queue) + '</span>' : '')
          + depthChip
        + '</div>'
        + (noteHtml ? '<div class="gc-queue-ev-body">' + noteHtml + '</div>' : '');
      body.appendChild(el);
      // Fade/slide in on the next frame.
      requestAnimationFrame(() => el.classList.add('is-shown'));
      body.scrollTop = body.scrollHeight;
    }
    const delay = Math.max(150, 950 / sharedState._gcReplaySpeed);
    sharedState._gcReplayMsgIndex++;
    sharedState._gcReplayTimeout = setTimeout(() => { playNextReplayStep(); }, delay);
  }

  function playNextReplayStep() {
    if (!sharedState._gcReplayActive || sharedState._gcReplayPaused) return;

    const body = document.getElementById('gcReaderBody');
    if (!body) return;

    if (sharedState._gcReplayMsgIndex >= sharedState._gcReplayMessages.length) {
      exitReplayMode();
      return;
    }

    const msg = sharedState._gcReplayMessages[sharedState._gcReplayMsgIndex];

    // Queue/ticket-appearance events (W22/B4) interleave on the same timeline
    // as conversation messages, but render as a compact queue chip card with a
    // fixed dwell — no hero clone, no word-by-word reveal.
    if (msg && msg.isQueueEvent) {
      _renderReplayQueueEvent(msg, body);
      return;
    }

    let msgEl = document.querySelector(`.gc-message[data-replay-idx="${sharedState._gcReplayMsgIndex}"]`);
    if (!msgEl) {
      // Human messages: simulate typing into the real reply box, then
      // "send" (clear the box, flash Send) before the bubble appears fully
      // formed in one shot — instead of the word-by-word bubble reveal
      // used for agent messages (annotation: "make the typing look like
      // it's happening in the input box, and then it submits").
      if (msg.isHuman && !_gcReplayHumanStep(msg)) return;

      const displayName = gcDisplayName(extractHash(msg.speaker));

      msgEl = document.createElement('article');
      msgEl.className = 'gc-message' + (msg.isSystem ? ' gc-system' : '') + (msg.isPing ? ' gc-system-ping-msg' : '');
      msgEl.setAttribute('data-speaker-side', msg.side);
      msgEl.setAttribute('data-speaker-color', msg.color);
      msgEl.setAttribute('data-replay-idx', sharedState._gcReplayMsgIndex);

      msgEl.innerHTML = '<div class="gc-message-meta">'
          + '<span class="gc-message-speaker" title="' + (0, sharedState.escapeAttr)(extractHash(msg.speaker)) + '">' + (0, sharedState.escapeHtml)(displayName) + '</span>'
          + (msg.when ? '<span class="gc-message-time">' + gcTimeChip(msg.when) + '</span>' : '')
        + '</div>'
        + '<div class="gc-message-body assistant-text">'
          + msg.wrappedHtml
        + '</div>'
        + '<div class="gc-message-footer"></div>';

      body.appendChild(msgEl);
      if (msg.isHuman) {
        // Already "typed" it into the input box above — the bubble itself
        // appears complete at once (one-shot submit), not word-by-word.
        (0, sharedState._replayRevealAllShells)(msgEl);
        msgEl.querySelectorAll('.gc-replay-word').forEach(w => { (0, sharedState._replayRevealRun)(w); });
        sharedState._gcReplayWordIndex = msg.wordCount;
      } else {
        sharedState._gcReplayWordIndex = 0;
      }
      body.scrollTop = body.scrollHeight;
      if (!msg.isSystem) _gcReplayCreateHero(msgEl, sharedState._gcReplayMsgIndex);
    }

    if (sharedState._gcReplayWordIndex < msg.wordCount) {
      const wordSpan = msgEl.querySelector(`.gc-replay-word[data-run-id="${sharedState._gcReplayWordIndex}"]`);
      if (wordSpan) {
        (0, sharedState._replayRevealRun)(wordSpan);
        wordSpan.classList.add('gc-typing-shimmer');
        const heroWord = sharedState._gcReplayHero && sharedState._gcReplayHero.replayIdx === sharedState._gcReplayMsgIndex
          ? sharedState._gcReplayHero.hero.querySelector(`.gc-replay-word[data-run-id="${sharedState._gcReplayWordIndex}"]`)
          : null;
        if (heroWord) {
          (0, sharedState._replayRevealRun)(heroWord);
          heroWord.classList.add('gc-typing-shimmer');
        }
        body.scrollTop = body.scrollHeight;

        // Budget the whole message's type-in to roughly its old flat
        // per-message delay, spread across its words — same tuning as
        // conv-replay's word reveal (short messages stay readable, long
        // ones don't crawl one word at a time).
        const totalMs = Math.max(600, Math.min(3000, (msg.rawBody || '').length * 3));
        const delay = Math.max(12, Math.min(160, totalMs / Math.max(1, msg.wordCount))) / sharedState._gcReplaySpeed;

        sharedState._gcReplayTimeout = setTimeout(() => {
          wordSpan.classList.remove('gc-typing-shimmer');
          if (heroWord) heroWord.classList.remove('gc-typing-shimmer');
          sharedState._gcReplayWordIndex++;
          playNextReplayStep();
        }, delay);
      } else {
        sharedState._gcReplayWordIndex++;
        playNextReplayStep();
      }
    } else {
      if (sharedState._gcReplayHero && sharedState._gcReplayHero.replayIdx === sharedState._gcReplayMsgIndex) {
        if (!sharedState._gcReplayHero.landing && (!sharedState._gcReplayHero.holding || !sharedState._gcReplayTimeout)) {
          body.scrollTop = body.scrollHeight;
          _gcReplayHoldThenLandHero(msgEl, msg, sharedState._gcReplayMsgIndex);
        }
        return;
      }

      const footer = msgEl.querySelector('.gc-message-footer');
      if (footer && !footer.innerHTML) {
        const alertsHtml = msg.isSystem ? '' : _gcReplayAlertsHtml(msg.rawBody, msg.originalIndex, msg.when);
        const receiptsHtml = msg.isSystem ? '' : _gcReplayReceiptsHtml(msg.originalIndex, msg.speaker, msg.when);
        footer.innerHTML = alertsHtml + receiptsHtml;
      }

      body.scrollTop = body.scrollHeight;

      // Participant cards already held at full size before flying into place;
      // don't repeat the old between-turn reading pause after they land.
      const heroLanded = msgEl.dataset.replayHeroLanded === 'true';
      if (heroLanded) delete msgEl.dataset.replayHeroLanded;
      const delay = heroLanded ? Math.max(60, 140 / sharedState._gcReplaySpeed) : _gcReplayMessagePauseMs(msg);

      sharedState._gcReplayMsgIndex++;
      sharedState._gcReplayTimeout = setTimeout(() => {
        playNextReplayStep();
      }, delay);
    }
  }

  function _gcReplayReceiptsHtml(idx, curSpeaker, when) {
    if (sharedState._gcReplayAllNonSys.length < 2) return '';
    const after = sharedState._gcReplaySpeakersAfter[idx];
    const curSpeakerName = gcDisplayName(extractHash(curSpeaker));

    const repliedChips = sharedState._gcReplayAllNonSys.map(p => {
      const pName = gcDisplayName(extractHash(p));
      if (pName === curSpeakerName) return '';

      const responded = after.has(p);
      if (!responded) return '';

      const initials = gcInitials(pName);
      const color = getParticipantColor(null, pName);
      return `<span class="gc-receipt-avatar" style="background-color: var(--${color}); color: var(--bg);" title="${(0, sharedState.escapeAttr)(pName)} - replied after">${(0, sharedState.escapeHtml)(initials)}</span>`;
    }).filter(Boolean).join('');

    let seenHtml = '';
    if (sharedState._gcReplayData && sharedState._gcReplayData.read_state) {
      const seenChips = Object.entries(sharedState._gcReplayData.read_state).map(([sid, lastReadTs]) => {
        const name = gcDisplayName(sid);
        if (name === curSpeakerName) return '';

        const msgDate = gcParseChatTimestamp(when);
        const readDate = new Date(lastReadTs);
        if (msgDate && !isNaN(readDate.getTime()) && readDate >= msgDate) {
          const initials = gcInitials(name);
          const color = getParticipantColor(sid, name);
          return `<span class="gc-seen-avatar" style="background-color: var(--${color}); color: var(--bg);" title="${(0, sharedState.escapeAttr)(name)} - seen message">${(0, sharedState.escapeHtml)(initials)}</span>`;
        }
        return '';
      }).filter(Boolean).join('');

      if (seenChips) {
        seenHtml = `<span class="gc-seen-label">👁 Seen:</span>${seenChips}`;
      }
    }

    if (!repliedChips && !seenHtml) return '';

    let html = '<div class="gc-message-receipts-row">';
    if (repliedChips) {
      html += `<span class="gc-row-label">✓ Replied after:</span><div class="gc-receipt-chips">${repliedChips}</div>`;
    }
    if (seenHtml) {
      html += seenHtml;
    }
    html += '</div>';
    return html;
  }

  function _gcReplayAlertsHtml(rawBody, idx, when) {
    const parsedNudges = [];
    const re = /^>\s*_(.+?)\s+—\s+system:\s*pinged\s+(.+?)_?\s*$/gm;
    let m;
    while ((m = re.exec(rawBody))) {
      const names = (m[2] || '')
        .replace(/\s*\([0-9a-fA-F]{8}\)/g, '')
        .replace(/`/g, '')
        .replace(/_+\s*$/, '')
        .trim();
      if (names) {
        names.split(/,\s*/).forEach(name => {
          if (name && !parsedNudges.includes(name)) {
            parsedNudges.push(name);
          }
        });
      }
    }

    if (sharedState._gcReplayData && sharedState._gcReplayData.nudge_log) {
      const logEntries = sharedState._gcReplayData.nudge_log[idx] || sharedState._gcReplayData.nudge_log[when] || [];
      logEntries.forEach(entry => {
        const name = entry.name || gcDisplayName(entry.sid);
        if (name && !parsedNudges.includes(name)) {
          parsedNudges.push(name);
        }
      });
    }

    if (parsedNudges.length === 0) return '';
    return '<div class="gc-message-nudged-row">'
      + '<span class="gc-row-label">📨 Nudged:</span>'
      + parsedNudges.map(name => {
          const color = getParticipantColor(null, name);
          return `<span class="gc-nudged-chip" style="border-left: 3px solid var(--${color});" title="${(0, sharedState.escapeAttr)(name)}">${(0, sharedState.escapeHtml)(name)}</span>`;
        }).join('')
      + '</div>';
  }

  function _gcMentionClose() {
    if (sharedState._gcMentionMenuEl) {
      sharedState._gcMentionMenuEl.remove();
      sharedState._gcMentionMenuEl = null;
    }
    sharedState._gcMentionItems = [];
    sharedState._gcMentionIndex = 0;
    sharedState._gcMentionInput = null;
    sharedState._gcMentionTokenStart = -1;
  }

  function _gcMentionParticipants() {
    const seen = new Set();
    const participants = [];
    (sharedState._gcReaderSessionIds || []).forEach(sid => {
      if (!sid || seen.has(sid)) return;
      seen.add(sid);
      participants.push({ sid, name: gcDisplayName(sid), short: String(sid).slice(0, 8) });
    });
    Object.keys(sharedState._gcReaderNameMap || {}).forEach(sid => {
      if (!sid || seen.has(sid)) return;
      seen.add(sid);
      participants.push({ sid, name: gcDisplayName(sid), short: String(sid).slice(0, 8) });
    });
    return participants;
  }

  function _gcMentionDetectToken(input) {
    if (!input) return null;
    const pos = input.selectionStart == null ? input.value.length : input.selectionStart;
    const upto = input.value.slice(0, pos);
    // Find the most recent '@' that's at a word boundary (start of
    // string OR after whitespace) and hasn't been followed by a space.
    const m = upto.match(/(^|[\s\n])@([\w-]*)$/);
    if (!m) return null;
    return {
      start: pos - m[2].length - 1,   // index of the '@'
      query: m[2].toLowerCase(),
    };
  }

  function _gcMentionRefresh(input) {
    const token = _gcMentionDetectToken(input);
    if (!token) { _gcMentionClose(); return; }
    const all = _gcMentionParticipants();
    if (!all.length) { _gcMentionClose(); return; }
    const q = token.query;
    const matches = all.filter(p =>
      !q || p.name.toLowerCase().startsWith(q) || p.short.startsWith(q) || p.name.toLowerCase().includes(q)
    ).slice(0, 8);
    if (!matches.length) { _gcMentionClose(); return; }
    sharedState._gcMentionInput = input;
    sharedState._gcMentionTokenStart = token.start;
    sharedState._gcMentionItems = matches;
    if (sharedState._gcMentionIndex >= matches.length) sharedState._gcMentionIndex = 0;
    if (!sharedState._gcMentionMenuEl) {
      sharedState._gcMentionMenuEl = document.createElement('div');
      sharedState._gcMentionMenuEl.className = 'gc-mention-menu';
      sharedState._gcMentionMenuEl.addEventListener('mousedown', ev => ev.preventDefault());
      document.body.appendChild(sharedState._gcMentionMenuEl);
    }
    sharedState._gcMentionMenuEl.innerHTML = matches.map((p, idx) => (
      '<button type="button" class="gc-mention-item' + (idx === sharedState._gcMentionIndex ? ' selected' : '') + '" data-idx="' + idx + '">'
      +   '<span class="gc-mention-item-name">' + (0, sharedState.escapeHtml)(p.name) + '</span>'
      +   '<span class="gc-mention-item-id">' + (0, sharedState.escapeHtml)(p.short) + '</span>'
      + '</button>'
    )).join('');
    sharedState._gcMentionMenuEl.querySelectorAll('.gc-mention-item').forEach(btn => {
      btn.addEventListener('mouseenter', () => {
        sharedState._gcMentionIndex = parseInt(btn.dataset.idx || '0', 10);
        _gcMentionRefresh(input);
      });
      btn.addEventListener('click', () => _gcMentionCommit());
    });
    const rect = input.getBoundingClientRect();
    sharedState._gcMentionMenuEl.style.left = Math.max(8, rect.left) + 'px';
    sharedState._gcMentionMenuEl.style.bottom = (window.innerHeight - rect.top + 6) + 'px';
    sharedState._gcMentionMenuEl.style.minWidth = Math.min(280, rect.width) + 'px';
  }

  function _gcMentionCommit() {
    if (!sharedState._gcMentionInput || !sharedState._gcMentionItems.length || sharedState._gcMentionTokenStart < 0) {
      _gcMentionClose();
      return;
    }
    const item = sharedState._gcMentionItems[sharedState._gcMentionIndex] || sharedState._gcMentionItems[0];
    const pos = sharedState._gcMentionInput.selectionStart == null
      ? sharedState._gcMentionInput.value.length
      : sharedState._gcMentionInput.selectionStart;
    const before = sharedState._gcMentionInput.value.slice(0, sharedState._gcMentionTokenStart);
    const after = sharedState._gcMentionInput.value.slice(pos);
    const insert = '@' + item.name + ' ';
    sharedState._gcMentionInput.value = before + insert + after;
    const newCaret = before.length + insert.length;
    try { sharedState._gcMentionInput.setSelectionRange(newCaret, newCaret); } catch (_) {}
    sharedState._gcMentionInput.dispatchEvent(new Event('input', { bubbles: true }));
    _gcMentionClose();
    sharedState._gcMentionInput && sharedState._gcMentionInput.focus();
  }

  function _gcMentionMenuHandleKeydown(ev) {
    if (!sharedState._gcMentionMenuEl) return false;
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      sharedState._gcMentionIndex = (sharedState._gcMentionIndex + 1) % sharedState._gcMentionItems.length;
      _gcMentionRefresh(sharedState._gcMentionInput);
      return true;
    }
    if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      sharedState._gcMentionIndex = (sharedState._gcMentionIndex - 1 + sharedState._gcMentionItems.length) % sharedState._gcMentionItems.length;
      _gcMentionRefresh(sharedState._gcMentionInput);
      return true;
    }
    if (ev.key === 'Enter' || ev.key === 'Tab') {
      ev.preventDefault();
      _gcMentionCommit();
      return true;
    }
    if (ev.key === 'Escape') {
      ev.preventDefault();
      _gcMentionClose();
      return true;
    }
    return false;
  }

  function wireGcMentionAutocomplete(input) {
    if (!input || input._gcMentionWired) return;
    input._gcMentionWired = true;
    input.addEventListener('input', () => _gcMentionRefresh(input));
    input.addEventListener('keyup', ev => {
      // Caret moved via arrow keys without changing text — re-check.
      if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight' || ev.key === 'Home' || ev.key === 'End') {
        _gcMentionRefresh(input);
      }
    });
    input.addEventListener('blur', () => setTimeout(_gcMentionClose, 120));
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

  function markSystemBlockquotes(html) {
    if (!html) return html;
    let out = html;
    // "pinged …" entries are the orchestrator nudging an agent — a meaningful
    // event the user wants to follow. Tag them with gc-system-ping so CSS can
    // surface them (📨, less muting) instead of burying them with the noisier
    // removed/re-injected lifecycle lines.
    const _pingCls = (flat) => /pinged/i.test(flat) ? ' gc-system-ping' : '';
    if (out.indexOf('<blockquote') !== -1) {
      out = out.replace(/<blockquote\b([^>]*)>([\s\S]*?)<\/blockquote>/g, (full, attrs, inner) => {
        const flat = inner.replace(/<[^>]+>/g, '').trim();
        if (!/—\s*system[:\s]/i.test(flat)) return full;
        const cls = / class="([^"]*)"/.exec(attrs);
        const newCls = 'gc-system-note' + _pingCls(flat);
        const newAttrs = cls
          ? attrs.replace(/ class="([^"]*)"/, ' class="$1 ' + newCls + '"')
          : attrs + ' class="' + newCls + '"';
        return `<blockquote${newAttrs}>${inner}</blockquote>`;
      });
    }
    if (out.indexOf('<div>') !== -1) {
      out = out.replace(/<div>(\s*<em>[\s\S]*?<\/em>\s*)<\/div>/g, (full, inner) => {
        const flat = inner.replace(/<[^>]+>/g, '').trim();
        if (!/—\s*system[:\s]/i.test(flat)) return full;
        return `<div class="gc-system-note${_pingCls(flat)}">${inner}</div>`;
      });
    }
    return out;
  }

  function gcExtractPingedNames(rawBody) {
    const names = [];
    const re = /^>\s*_(.+?)\s+—\s+system:\s*pinged\s+(.+?)_?\s*$/gm;
    let m;
    while ((m = re.exec(rawBody || ''))) {
      const pinged = (m[2] || '')
        .replace(/\s*\([0-9a-fA-F]{8}\)/g, '')
        .replace(/`/g, '')
        .replace(/_+\s*$/, '')
        .trim();
      if (pinged) {
        pinged.split(/,\s*/).forEach(name => {
          if (name && !names.includes(name)) names.push(name);
        });
      }
    }
    return names;
  }

  function renderGroupChatMarkdown(content) {
    const text = String(content || '');
    const matches = Array.from(text.matchAll(
      /^#{2,3}[ \t]+(.+?(?:—[ \t]+(?:[0-9a-fA-F]{8}(?::|\b)|Human\b|system(?::|\b)).*|.*\([0-9a-fA-F]{8}\)))[ \t]*$/gm
    ));

    const _parseSpeaker = (heading) => {
      let s = heading;
      if (/\s+—\s+/.test(s)) {
        const p = s.split(/\s+—\s+/); p.shift(); s = p.length ? p.join(' — ') : s;
      } else {
        s = s.replace(/\s*\([0-9a-fA-F]{8}\)\s*$/, '').replace(/`/g, '').trim() || s;
      }
      return s;
    };
    const _prePass = matches.map(m => {
      const s = _parseSpeaker((m[1] || '').trim());
      return { speaker: s, isSystem: /^\s*system\b/i.test(s) };
    });
    const _allNonSysSet = new Set();
    _prePass.forEach(p => { if (!p.isSystem) _allNonSysSet.add(p.speaker); });
    const _allNonSys = Array.from(_allNonSysSet);

    const _speakersAfter = _prePass.map((_, idx) => {
      const after = new Set();
      for (let j = idx + 1; j < _prePass.length; j++) {
        if (!_prePass[j].isSystem) after.add(_prePass[j].speaker);
      }
      return after;
    });

    const _gcReceiptHtml = (idx, curSpeaker, when) => {
      if (_allNonSys.length < 2) return '';
      const after = _speakersAfter[idx];
      const curSpeakerName = gcDisplayName(extractHash(curSpeaker));

      const repliedChips = _allNonSys.map(p => {
        const pName = gcDisplayName(extractHash(p));
        if (pName === curSpeakerName) return '';

        const responded = after.has(p);
        if (!responded) return '';

        const initials = gcInitials(pName);
        const color = getParticipantColor(null, pName);
        return `<span class="gc-receipt-avatar" style="background-color: var(--${color}); color: var(--bg);" title="${(0, sharedState.escapeAttr)(pName)} - replied after">${(0, sharedState.escapeHtml)(initials)}</span>`;
      }).filter(Boolean).join('');

      let seenHtml = '';
      if (sharedState._gcReplayData && sharedState._gcReplayData.read_state) {
        const seenChips = Object.entries(sharedState._gcReplayData.read_state).map(([sid, lastReadTs]) => {
          const name = gcDisplayName(sid);
          if (name === curSpeakerName) return '';

          const msgDate = gcParseChatTimestamp(when);
          const readDate = new Date(lastReadTs);
          if (msgDate && !isNaN(readDate.getTime()) && readDate >= msgDate) {
            const initials = gcInitials(name);
            const color = getParticipantColor(sid, name);
            return `<span class="gc-seen-avatar" style="background-color: var(--${color}); color: var(--bg);" title="${(0, sharedState.escapeAttr)(name)} - seen message">${(0, sharedState.escapeHtml)(initials)}</span>`;
          }
          return '';
        }).filter(Boolean).join('');

        if (seenChips) {
          seenHtml = `<span class="gc-seen-label">👁 Seen:</span>${seenChips}`;
        }
      }

      if (!repliedChips && !seenHtml) return '';

      let html = '<div class="gc-message-receipts-row">';
      if (repliedChips) {
        html += `<span class="gc-row-label">✓ Replied after:</span><div class="gc-receipt-chips">${repliedChips}</div>`;
      }
      if (seenHtml) {
        html += seenHtml;
      }
      html += '</div>';
      return html;
    };

    const _gcAlertsHtml = (rawBody, idx, when) => {
      const parsedNudges = gcExtractPingedNames(rawBody);

      if (sharedState._gcReplayData && sharedState._gcReplayData.nudge_log) {
        const logEntries = sharedState._gcReplayData.nudge_log[idx] || sharedState._gcReplayData.nudge_log[when] || [];
        logEntries.forEach(entry => {
          const name = entry.name || gcDisplayName(entry.sid);
          if (name && !parsedNudges.includes(name)) {
            parsedNudges.push(name);
          }
        });
      }

      if (parsedNudges.length === 0) return '';
      return '<div class="gc-message-nudged-row">'
        + '<span class="gc-row-label">📨 Nudged:</span>'
        + parsedNudges.map(name => {
            const color = getParticipantColor(null, name);
            return `<span class="gc-nudged-chip" style="border-left: 3px solid var(--${color});" title="${(0, sharedState.escapeAttr)(name)}">${(0, sharedState.escapeHtml)(name)}</span>`;
          }).join('')
        + '</div>';
    };

    if (!matches.length) {
      return '<div class="assistant-text gc-chat-doc" dir="auto">' + (0, sharedState.renderMarkdown)(text) + '</div>';
    }

    let html = '';
    const preamble = text.slice(0, matches[0].index).trim();
    if (preamble) {
      html += '<details class="gc-preamble-disclosure">'
        + '<summary class="gc-preamble-summary">Chat details</summary>'
        + '<div class="gc-chat-preamble">' + (0, sharedState.renderMarkdown)(preamble) + '</div>'
        + '</details>';
    }

    const speakerToSide = new Map();
    const speakerToColor = new Map();
    let nextAgentSlot = 0;
    const sideForSpeaker = (speakerLabel) => {
      const isHuman = /\bHuman\b/i.test(speakerLabel);
      if (isHuman) {
        speakerToColor.set(speakerLabel, 'green');
        return 'right';
      }
      if (speakerToSide.has(speakerLabel)) return speakerToSide.get(speakerLabel);
      const side = (nextAgentSlot % 2 === 0) ? 'left' : 'right-agent';
      const colorPalette = ['accent', 'purple', 'orange', 'cyan', 'red', 'yellow'];
      speakerToColor.set(speakerLabel, colorPalette[nextAgentSlot % colorPalette.length]);
      nextAgentSlot += 1;
      speakerToSide.set(speakerLabel, side);
      return side;
    };
    const colorForSpeaker = (speakerLabel) => speakerToColor.get(speakerLabel) || 'accent';

    for (let i = 0; i < matches.length; i++) {
      const match = matches[i];
      const heading = (match[1] || '').trim();
      const nextIndex = i + 1 < matches.length ? matches[i + 1].index : text.length;
      const rawBody = text.slice(match.index + match[0].length, nextIndex);
      const body = rawBody.replace(/^\s*---\s*$/gm, '').trim();

      let when = '';
      let speaker = heading;
      if (/\s+—\s+/.test(heading)) {
        const parts = heading.split(/\s+—\s+/);
        when = parts.length > 1 ? parts.shift() : '';
        speaker = parts.length ? parts.join(' — ') : heading;
      } else {
        speaker = heading.replace(/\s*\([0-9a-fA-F]{8}\)\s*$/, '').replace(/`/g, '').trim() || heading;
      }
      const isSystem = /^\s*system\b/i.test(speaker);
      const speakerHash = extractHash(speaker);
      const displayName = gcDisplayName(speakerHash);

      const side = isSystem ? 'full' : sideForSpeaker(displayName);
      const color = isSystem ? 'muted' : getParticipantColor(speakerHash, displayName);
      const isPing = isSystem && /pinged/i.test(body);

      html += '<article class="gc-message' + (isSystem ? ' gc-system' : '') + (isPing ? ' gc-system-ping-msg' : '') + '" data-speaker-side="' + side + '" data-speaker-color="' + color + '">'
        + '<div class="gc-message-meta">'
          + '<span class="gc-message-number" title="Message L' + (i + 1) + '">L' + (i + 1) + '</span>'
          + '<span class="gc-message-speaker" title="' + (0, sharedState.escapeAttr)(speakerHash) + '">' + (0, sharedState.escapeHtml)(displayName) + '</span>'
          + (when ? '<span class="gc-message-time">' + gcTimeChip(when) + '</span>' : '')
        + '</div>'
        + '<div class="gc-message-body assistant-text">'
          + (body ? markSystemBlockquotes((0, sharedState.renderMarkdown)(body)) : '<em class="gc-message-empty">(no text)</em>')
        + '</div>'
        + '<div class="gc-message-footer">'
          + (isSystem ? '' : _gcAlertsHtml(rawBody, i, when))
          + (isSystem ? '' : _gcReceiptHtml(i, speaker, when))
        + '</div>'
      + '</article>';
    }

    return html;
  }

  function openGcParticipantPicker(chatRef) {
    if (!chatRef || (!chatRef.chat_id && !chatRef.chat_path)) return;
    const existing = document.getElementById('gcAddPartModal');
    if (existing) existing.remove();
    const candidates = (sharedState.conversationsData || [])
      .filter(c => c && (c.session_id || c.id)
        && c.source !== 'backlog' && c.source !== 'github_pr' && !c.archived)
      .sort((a, b) => (b.modified || 0) - (a.modified || 0));
    const engineEmoji = (c) => (
      c.source === 'codex' ? '🟦' : c.source === 'antigravity' || c.source === 'gemini' ? '✨'
      : c.source === 'cursor' ? '🖱️' : '🤖');
    const rowTitle = (c) => (c.display_name || c.ai_title
      || (0, sharedState.firstSentenceOf)((0, sharedState.cleanIssuePrompt)(c.first_message || '') || '', 48) || '(untitled)');
    const modal = document.createElement('div');
    modal.id = 'gcAddPartModal';
    modal.className = 'gc-addpart-modal';
    modal.innerHTML = '<div class="gc-addpart-dialog">'
      + '<div class="gc-addpart-header">Add participant</div>'
      + '<div class="gc-addpart-body">'
        + '<input type="text" class="gc-addpart-search" placeholder="Search sessions…">'
        + '<div class="gc-addpart-list"></div>'
      + '</div>'
    + '</div>';
    document.body.appendChild(modal);
    const input = modal.querySelector('.gc-addpart-search');
    const list = modal.querySelector('.gc-addpart-list');
    let filtered = [];
    let sel = 0;
    const close = () => { modal.remove(); document.removeEventListener('keydown', onKey, true); };
    const paint = () => {
      const q = (input.value || '').trim().toLowerCase();
      if (q) {
        filtered = candidates.filter(c => (rowTitle(c) + ' ' + (c.folder_label_chip || '') + ' ' + (c.session_id || '')).toLowerCase().indexOf(q) !== -1).slice(0, 25);
      } else {
        const nowSec = Date.now() / 1000;
        const fewHours = 4 * 3600;
        const recentSessions = candidates.filter(c => c.modified && (nowSec - c.modified) < fewHours);
        filtered = recentSessions.length > 0 ? recentSessions : candidates.slice(0, 10);
      }
      sel = Math.max(0, Math.min(sel, filtered.length - 1));
      list.innerHTML = filtered.map((c, i) =>
        '<div class="gc-addpart-row' + (i === sel ? ' is-selected' : '') + '" data-idx="' + i + '">'
        + '<span class="gc-addpart-emoji">' + engineEmoji(c) + '</span>'
        + '<span class="gc-addpart-name">' + (0, sharedState.escapeHtml)(rowTitle(c)) + '</span>'
        + (c.folder_label_chip ? '<span class="gc-addpart-repo">' + (0, sharedState.repoAvatarHtml)(c.folder_path || c.session_cwd, c.folder_label_chip) + (0, sharedState.escapeHtml)(c.folder_label_chip) + '</span>' : '')
        + '<span class="gc-addpart-when">' + (0, sharedState.escapeHtml)(c.modified ? (0, sharedState.timeAgo)(c.modified * 1000) : '') + '</span>'
        + '</div>'
      ).join('') || '<div class="gc-addpart-empty">No matching sessions.</div>';
    };
    const add = async (c) => {
      if (!c) return;
      const sid = c.session_id || c.id;
      close();
      try {
        const body = Object.assign({ session_id: sid, display_name: rowTitle(c) }, chatRef);
        const res = await fetch('/api/group-chat/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.ok) {
          (0, sharedState.showOpToast)('🎉 ' + rowTitle(c).slice(0, 40) + ' joined the chat', 'success');
          sharedState._gcLastMtime = null;
          if (typeof hostServices.refreshConversationList === 'function') (0, hostServices.refreshConversationList)();
        } else {
          (0, sharedState.showOpToast)('Add failed: ' + ((data && data.error) || ('HTTP ' + res.status)), 'error');
        }
      } catch (err) {
        (0, sharedState.showOpToast)('Add failed: ' + ((err && err.message) || 'network'), 'error');
      }
    };
    const onKey = (e) => {
      if ((0, sharedState.isImeKey)(e)) return;
      if (e.key === 'Escape') { e.preventDefault(); close(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); sel++; paint(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); sel--; paint(); }
      else if (e.key === 'Enter') { e.preventDefault(); add(filtered[sel]); }
    };
    document.addEventListener('keydown', onKey, true);
    modal.addEventListener('click', (e) => { if (e.target === modal) close(); });
    list.addEventListener('click', (e) => {
      const row = e.target.closest('[data-idx]');
      if (row) add(filtered[Number(row.dataset.idx)]);
    });
    input.addEventListener('input', () => { sel = 0; paint(); });
    paint();
    input.focus();
  }

  function openGroupChatReader(chatPath, topic, mode, includeHuman, chatId) {
    if (sharedState._flowInspectorState) {
      try { stopFlowNodeInspector(); } catch (_) {}
    }
    sharedState._gcReaderPath = chatPath;
    sharedState._gcReaderId = chatId || null;
    sharedState._gcReaderTopic = topic || '';
    sharedState._gcReaderMode = mode || 'topic';
    sharedState._gcLastMtime = null;
    sharedState._gcPollFailCount = 0;
    sharedState._gcLastNudgeTime = 0;

    if (!sharedState.GROUPCHAT_POPOUT_MODE && !sharedState.CONV_POPOUT_MODE) {
      try {
        const payload = {
          type: 'gc',
          path: chatPath || '',
          id: chatId || '',
          topic: topic || '',
          mode: mode || 'topic',
          includeHuman: !!includeHuman,
          at: Date.now(),
        };
        localStorage.setItem('ccc-last-view', JSON.stringify(payload));
      } catch (_) {}
    }
    sharedState.currentConversation = null;
    // The status rail is global to the active pane. Leaving its title and
    // selection intact makes a group chat look like it still belongs to the
    // previously opened session, especially when that session had Queue open.
    // Give the reader its own identity and expose its orchestration context.
    if (typeof sharedState.updatePaneHeader === 'function') {
      (0, sharedState.updatePaneHeader)((0, sharedState.activePaneId)(), null, {
        category: 'Group chat',
        title: topic || 'Group chat',
      });
    }
    if (typeof setStatusRailTab === 'function') setStatusRailTab('metadata');
    if (typeof sharedState.ffcUpdateSidebar === 'function') (0, sharedState.ffcUpdateSidebar)(null);
    if (typeof sharedState.syncActivePaneChrome === 'function') (0, sharedState.syncActivePaneChrome)(null);
    if (typeof hostServices.renderSidebar === 'function' && typeof sharedState.filterConversations === 'function' && typeof sharedState.$convSearch !== 'undefined' && sharedState.$convSearch) {
      (0, hostServices.renderSidebar)((0, sharedState.filterConversations)(sharedState.$convSearch.value));
    }

    try { if (typeof sharedState.stopConvStream === 'function') (0, sharedState.stopConvStream)(); } catch (_) {}
    try { if (typeof sharedState.stopSpawnStream === 'function') (0, sharedState.stopSpawnStream)(); } catch (_) {}
    try { if (typeof sharedState.setCurrentSession === 'function') (0, sharedState.setCurrentSession)(null, null, null, false, null); } catch (_) {}
    document.querySelectorAll('.conv-live-tool-inline, .conv-live-tool-strip').forEach(n => n.remove());

    const view = document.getElementById('conversationsView');
    if (!view) return;

    const topicSafe = (0, sharedState.escapeHtml)(topic);
    const modeSafe = (0, sharedState.escapeHtml)(mode);
    view.innerHTML = '<div class="gc-reader" id="gcReader">'
      + '<div class="gc-reader-header">'
        + '<span class="gc-topic" title="' + topicSafe + '">' + topicSafe + '</span>'
        + '<span class="gc-mode-badge" style="display:none;">' + modeSafe + '</span>'
        + '<button type="button" class="gc-join-link-btn" id="gcAddPartBtn"'
        + ' title="Add a participant - search recent sessions, Enter to add">'
        + '＋ Add participant</button>'
        + '<button type="button" class="gc-join-link-btn" id="gcJoinLinkBtn"'
        + ' title="Copy a join link - paste it into any session\'s composer in CCC and that session joins this chat">'
        + '🔗 Join link</button>'
        + '<button type="button" class="gc-join-link-btn" id="gcReplayBtn" title="Replay conversation">▶ Replay</button>'
        + (sharedState.GROUPCHAT_POPOUT_MODE ? ''
          : '<button type="button" class="gc-join-link-btn" id="gcPopoutBtn"'
            + ' data-role="ccc-gc-popout"'
            + ' title="Pop this group chat out to its own window" aria-label="Pop out">'
            + '↗ Pop out</button>')
      + '</div>'
      + '<div class="gc-info-bar" id="gcInfoBar"></div>'
      + '<div class="gc-reader-body" id="gcReaderBody" tabindex="0">Loading…</div>'
      + (includeHuman
        ? '<div class="gc-reader-input-row" id="gcInputRow">'
            + '<textarea id="gcHumanInput" rows="1" placeholder="Add to chat…" autocomplete="off" autocapitalize="sentences" autocorrect="on" spellcheck="true"></textarea>'
            + '<button class="tts-btn gc-tts-btn" id="gcTtsBtn" type="button" title="Read last message" aria-label="Read last message" aria-pressed="false">'
              + '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                + '<path d="M11 5 6 9H3v6h3l5 4V5Z"></path>'
                + '<path d="M15.5 8.5a5 5 0 0 1 0 7"></path>'
                + '<path d="M18.5 5.5a9 9 0 0 1 0 13"></path>'
              + '</svg>'
            + '</button>'
            + '<button class="mic-btn gc-mic-btn" id="gcMicBtn" type="button" title="Record speech to input" aria-label="Record speech to input" aria-pressed="false">'
              + '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                + '<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path>'
                + '<path d="M19 10v2a7 7 0 0 1-14 0v-2"></path>'
                + '<line x1="12" y1="19" x2="12" y2="23"></line>'
                + '<line x1="8" y1="23" x2="16" y2="23"></line>'
              + '</svg>'
            + '</button>'
            + '<button id="gcSendBtn" class="gc-send-btn" type="button" title="Send (Enter)" aria-label="Send to group chat">'
              + '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
                + '<path d="M12 19 L12 5 M6 11 L12 5 L18 11"></path>'
              + '</svg>'
            + '</button>'
          + '</div>'
        : '')
      + '</div>';
    if (typeof syncBodyGcReaderFlag === 'function') syncBodyGcReaderFlag();

    const inputBar = document.getElementById('convInputBar');
    const inputCtx = document.getElementById('convInputContext');
    if (inputBar) inputBar.style.display = 'none';
    if (inputCtx) inputCtx.style.display = 'none';
    sharedState._gcReaderHiddenInputBar = true;

    const rail = document.getElementById('statusRail');
    if (rail) {
      rail.querySelectorAll('.csh-ask-original, .csh-col-activity').forEach(el => el.remove());
      const filesPanel = rail.querySelector('#filesPanel');
      if (filesPanel) filesPanel.style.display = 'none';
      const railActions = rail.querySelector('#railActions');
      if (railActions) railActions.style.display = 'none';

      let orchPanel = document.getElementById('gcOrchestratorPanel');
      if (!orchPanel) {
        orchPanel = document.createElement('div');
        orchPanel.id = 'gcOrchestratorPanel';
        orchPanel.className = 'gc-orchestrator-panel';
        rail.appendChild(orchPanel);
      }
      orchPanel.style.display = '';
      orchPanel.innerHTML = '<div class="gco-section"><div class="gco-title">Orchestrator</div><div class="gco-body">Loading...</div></div>';
      sharedState._gcReaderHiddenRailItems = true;
    }

    const gcAddPartBtn = document.getElementById('gcAddPartBtn');
    if (gcAddPartBtn) {
      gcAddPartBtn.addEventListener('click', () => {
        openGcParticipantPicker(sharedState._gcReaderId ? { chat_id: sharedState._gcReaderId } : { chat_path: sharedState._gcReaderPath });
      });
    }
    const gcReplayBtn = document.getElementById('gcReplayBtn');
    if (gcReplayBtn) {
      gcReplayBtn.addEventListener('click', () => {
        if (sharedState._gcReplayData) {
          startReplay(sharedState._gcReplayData);
        }
      });
    }
    const gcJoinBtn = document.getElementById('gcJoinLinkBtn');
    if (gcJoinBtn) {
      gcJoinBtn.addEventListener('click', async () => {
        const token = 'ccc:join-gc:' + (sharedState._gcReaderId || sharedState._gcReaderPath || '');
        try {
          await navigator.clipboard.writeText(token);
          (0, sharedState.showOpToast)('Join link copied - paste it into any session’s composer and send', 'success');
        } catch (_) {
          prompt('Copy this join link, then paste it into any session’s composer:', token);
        }
      });
    }
    if (includeHuman) {
      const gcSendBtn = document.getElementById('gcSendBtn');
      const gcHumanInput = document.getElementById('gcHumanInput');
      const gcTtsBtn = document.getElementById('gcTtsBtn');
      if (gcSendBtn) gcSendBtn.addEventListener('click', () => sendHumanGcPost());
      if (gcTtsBtn) {
        gcTtsBtn.addEventListener('mousedown', (ev) => ev.preventDefault());
        gcTtsBtn.addEventListener('click', (ev) => {
          ev.preventDefault();
          (0, sharedState.readLastMessageAloud)((0, sharedState.activePaneId)());
        });
      }
      const gcMicBtn = document.getElementById('gcMicBtn');
      if (gcMicBtn) {
        gcMicBtn.addEventListener('mousedown', (ev) => ev.preventDefault());
        gcMicBtn.addEventListener('click', (ev) => {
          ev.preventDefault();
          (0, sharedState.toggleSpeechRecognition)('gc');
        });
      }
      if (gcHumanInput) {
        wireGcMentionAutocomplete(gcHumanInput);
        if (typeof window.__cccAttachImagePaste === 'function') {
          window.__cccAttachImagePaste(gcHumanInput);
        }
        gcHumanInput.addEventListener('keydown', ev => {
          if ((0, sharedState.isImeKey)(ev)) return;
          if (_gcMentionMenuHandleKeydown(ev)) return;
          if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing && !(0, sharedState.isTouchPrimary)()) {
            ev.preventDefault();
            sendHumanGcPost();
          }
        });
        const _autosizeGc = () => {
          gcHumanInput.style.height = 'auto';
          const max = 240;
          gcHumanInput.style.height = Math.min(gcHumanInput.scrollHeight, max) + 'px';
        };
        gcHumanInput.addEventListener('input', _autosizeGc);
        const ownDesc = Object.getOwnPropertyDescriptor(gcHumanInput, 'value');
        const desc = ownDesc || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
        if (desc && desc.get && desc.set) {
          Object.defineProperty(gcHumanInput, 'value', {
            configurable: true,
            get() { return desc.get.call(this); },
            set(v) { desc.set.call(this, v); _autosizeGc(); },
          });
        }
        _autosizeGc();
      }
    }

    if (sharedState._gcReaderInterval) clearInterval(sharedState._gcReaderInterval);
    // Self-rescheduling poll with inactivity backoff: 3s while the transcript
    // changes, stretching to 10s after 5 unchanged reads and 30s after 20 —
    // a reader left open on a finished chat used to burn a full read (with
    // per-participant liveness probes) every 3s forever. The generation guard
    // kills a stale chain when the reader is closed or reopened mid-flight.
    sharedState._gcUnchangedPolls = 0;
    sharedState._gcReaderGeneration += 1;
    const _gcTickGen = sharedState._gcReaderGeneration;
    const _gcReaderTick = async () => {
      if (_gcTickGen !== sharedState._gcReaderGeneration) return;
      try { await pollGroupChatReader(); } catch (_) {}
      if (!sharedState._gcReaderPath && !sharedState._gcReaderId) return;
      const delay = sharedState._gcUnchangedPolls >= 20 ? 30000 : (sharedState._gcUnchangedPolls >= 5 ? 10000 : 3000);
      sharedState._gcReaderInterval = setTimeout((0, sharedState._gated)('gcReader', _gcReaderTick), delay);
    };
    _gcReaderTick();

    // Space → jump to the top of the next message in the gc reader.
    // Each message starts with `## ts — hash: name` which renders as
    // <h2 class="md-h">. Listener is GLOBAL (document) and gated on the
    // reader being live + the user not typing into the reply textarea.
    // Idempotent: we mark the document so we only attach once even if
    // openGroupChatReader is called multiple times.
    if (!document._gcSpaceHandlerAttached) {
      document._gcSpaceHandlerAttached = true;
      // Capture phase so we fire BEFORE the browser's default Space-page-
      // scroll triggers. Without `true` the default kicks in first and
      // bubbles to us, producing a compound jump.
      document.addEventListener('keydown', (ev) => {
        if ((0, sharedState.isImeKey)(ev)) return;
        if (ev.key !== ' ' && ev.key !== 'Spacebar') return;
        if (ev.shiftKey || ev.ctrlKey || ev.metaKey || ev.altKey) return;
        const gcBody = document.getElementById('gcReaderBody');
        if (!gcBody) return;
        if (gcBody.offsetParent === null) return;
        const ae = document.activeElement;
        if (ae && (ae.tagName === 'TEXTAREA' || ae.tagName === 'INPUT' || ae.isContentEditable)) return;
        // Claim Space fully so the browser's default page-down doesn't
        // also fire and produce a compound jump.
        ev.preventDefault();
        ev.stopPropagation();
        const headings = gcBody.querySelectorAll('h2.md-h');
        if (!headings.length) return;
        const bodyRect = gcBody.getBoundingClientRect();
        const slop = 4;
        let target = null;
        for (const h of headings) {
          if (h.getBoundingClientRect().top - bodyRect.top > slop) { target = h; break; }
        }
        if (!target) return;
        // Use native scrollIntoView — the browser handles margin/padding/
        // box math correctly. block:'start' aligns the element's start
        // edge with the scroll container's start.
        target.scrollIntoView({ block: 'start', behavior: 'auto' });
      }, true);
    }
    // Defer focus a tick so we don't fight with selectConversation flow.
    const gcBodyForFocus = document.getElementById('gcReaderBody');
    if (gcBodyForFocus) setTimeout(() => gcBodyForFocus.focus(), 0);

    // Drop target: dragging an in-progress session row onto the reader
    // pane itself adds that session as a participant. Mirrors the
    // sidebar drag-onto-chat-row affordance — useful when the reader
    // is already open and the user wants to bring more sessions in
    // without scrolling back to the chat's sidebar row.
    const gcReaderEl = document.getElementById('gcReader');
    if (gcReaderEl) {
      gcReaderEl.addEventListener('dragover', (ev) => {
        if (!sharedState.dragSourceId || !sharedState._gcReaderPath) return;
        ev.preventDefault();
        try { ev.dataTransfer.dropEffect = 'move'; } catch (_) {}
        gcReaderEl.classList.add('gc-drop-target');
      });
      gcReaderEl.addEventListener('dragleave', (ev) => {
        // Browsers fire dragleave when crossing into child elements;
        // only clear the highlight when the cursor actually exits the
        // reader's bounding box.
        if (ev.relatedTarget && gcReaderEl.contains(ev.relatedTarget)) return;
        gcReaderEl.classList.remove('gc-drop-target');
      });
      gcReaderEl.addEventListener('drop', (ev) => {
        ev.preventDefault();
        gcReaderEl.classList.remove('gc-drop-target');
        if (!sharedState._gcReaderPath || !sharedState.dragSourceId) return;
        const draggedConv = (sharedState.conversationsData || []).find(c => c.id === sharedState.dragSourceId);
        if (draggedConv && (draggedConv.source === 'backlog' || draggedConv.source === 'github_pr')) {
          (0, sharedState.showOpToast)?.('Drag a real session row, not a backlog/issue card', 'error');
          return;
        }
        const sid = (draggedConv && (draggedConv.session_id || draggedConv.id)) || sharedState.dragSourceId;
        const displayName = draggedConv ? (draggedConv.display_name || '') : '';
        addSessionToGroupChat(sharedState._gcReaderPath, sid, displayName, sharedState._gcReaderId);
      });
    }
  }

  function _gcExpandHashIds(text) {
    if (!text || !sharedState._gcActiveChats || !sharedState._gcActiveChats.length) return text;
    const byShort = {};
    for (const chat of sharedState._gcActiveChats) {
      const nm = chat.name_map || {};
      for (const fullSid of Object.keys(nm)) {
        const short = String(fullSid).slice(0, 8).toLowerCase();
        if (short && !byShort[short]) byShort[short] = nm[fullSid];
      }
    }
    if (!Object.keys(byShort).length) return text;
    // Match the chat-message author marker: " — <8 hex chars>" NOT
    // already followed by a colon (which would indicate the new
    // "hash: name" format that should pass through unchanged).
    return text.replace(
      /(\s—\s)([0-9a-fA-F]{8})(?!:)\b/g,
      (m, dash, hash) => {
        const name = byShort[hash.toLowerCase()];
        return name ? `${dash}${hash}: ${name}` : m;
      }
    );
  }

  function replaceParticipantMentions(container, nameMap) {
    const shortToName = {};
    const sids = sharedState._gcReaderSessionIds || [];
    for (const fullSid of sids) {
      const short = fullSid.substring(0, 8).toLowerCase();
      shortToName[short] = gcDisplayName(short);
    }
    for (const fullSid of Object.keys(sharedState._gcReaderNameMap || {})) {
      const short = fullSid.substring(0, 8).toLowerCase();
      if (!shortToName[short]) {
        shortToName[short] = gcDisplayName(short);
      }
    }
    const shortIds = Object.keys(shortToName);
    if (!shortIds.length) return;

    const regexStr = '(?:@)?\\b(' + shortIds.join('|') + ')(?:-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?\\b';
    const regex = new RegExp(regexStr, 'gi');

    const walker = document.createTreeWalker(
      container,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode(node) {
          let parent = node.parentElement;
          while (parent && parent !== container) {
            const tag = parent.tagName.toLowerCase();
            if (tag === 'pre' || tag === 'code' || tag === 'a') return NodeFilter.FILTER_REJECT;
            parent = parent.parentElement;
          }
          return NodeFilter.FILTER_ACCEPT;
        }
      }
    );

    const textNodes = [];
    let currentNode = walker.nextNode();
    while (currentNode) {
      textNodes.push(currentNode);
      currentNode = walker.nextNode();
    }

    for (const node of textNodes) {
      const text = node.textContent;
      if (!regex.test(text)) continue;

      regex.lastIndex = 0;
      const fragment = document.createDocumentFragment();
      let lastIndex = 0;
      let match;
      while ((match = regex.exec(text)) !== null) {
        const matchIndex = match.index;
        const shortId = match[1].toLowerCase();
        const name = shortToName[shortId];

        if (matchIndex > lastIndex) {
          fragment.appendChild(document.createTextNode(text.substring(lastIndex, matchIndex)));
        }

        const span = document.createElement('span');
        span.className = 'gc-mention';
        const _MENTION_MAX = 20;
        const _displayName = name.length > _MENTION_MAX
          ? name.slice(0, _MENTION_MAX - 1) + '…'
          : name;
        span.textContent = '@' + _displayName;
        span.title = `${name}\nSession: ${shortId}`;
        fragment.appendChild(span);

        lastIndex = regex.lastIndex;
      }

      if (lastIndex < text.length) {
        fragment.appendChild(document.createTextNode(text.substring(lastIndex)));
      }

      node.replaceWith(fragment);
    }
  }

  function updateOrchestratorPanel(data, content) {
    const panel = document.getElementById('gcOrchestratorPanel');
    if (!panel || panel.style.display === 'none') return;

    const nm = data.name_map || {};
    const sids = data.session_ids || [];
    const waiting = data.waiting || {};

    const lastSpoken = {};
    const lastMentioned = {};
    const lastPinged = {};

    const text = String(content || '');
    const matches = Array.from(text.matchAll(
      /^#{2,3}[ \t]+(.+?(?:—[ \t]+(?:[0-9a-fA-F]{8}(?::|\b)|Human\b|system(?::|\b)).*|.*\([0-9a-fA-F]{8}\)))[ \t]*$/gm
    ));

    for (let i = 0; i < matches.length; i++) {
      const match = matches[i];
      const heading = (match[1] || '').trim();
      const nextIndex = i + 1 < matches.length ? matches[i + 1].index : text.length;
      const bodyText = text.slice(match.index + match[0].length, nextIndex);

      let when = '';
      let speaker = heading;
      if (/\s+—\s+/.test(heading)) {
        const parts = heading.split(/\s+—\s+/);
        when = parts.length > 1 ? parts.shift() : '';
        speaker = parts.length ? parts.join(' — ') : heading;
      } else {
        speaker = heading.replace(/\s*\([0-9a-fA-F]{8}\)\s*$/, '').replace(/`/g, '').trim() || heading;
      }

      const rawHash = extractHash(speaker);
      const authorHash = rawHash ? rawHash.substring(0, 8).toLowerCase() : '';
      if (authorHash) {
        lastSpoken[authorHash] = when;
      }

      const pingedNames = gcExtractPingedNames(bodyText);
      if (pingedNames.length) {
        for (const [fullSid, name] of Object.entries(nm)) {
          const short = fullSid.substring(0, 8).toLowerCase();
          const displayName = gcDisplayName(fullSid).toLowerCase();
          if (pingedNames.some(ping => {
            const candidate = ping.toLowerCase();
            return candidate.includes(displayName) || candidate.includes(name.toLowerCase())
              || candidate.includes('@' + short);
          })) {
            lastPinged[short] = { number: i + 1, when };
          }
        }
      }

      for (const [fullSid, name] of Object.entries(nm)) {
        const short = fullSid.substring(0, 8).toLowerCase();
        const regex = new RegExp('(?:@)?\\b' + short + '\\b', 'i');
        const mentionMatch = bodyText.match(regex);
        if (mentionMatch) {
          const idx = mentionMatch.index;
          const start = Math.max(0, idx - 20);
          const end = Math.min(bodyText.length, idx + short.length + 20);
          let snippet = bodyText.substring(start, end).replace(/\s+/g, ' ');
          if (start > 0) snippet = '…' + snippet;
          if (end < bodyText.length) snippet += '…';
          lastMentioned[short] = { when, snippet, by: authorHash };
        }
      }
    }

    let html = '';

    // A3: Participants TOP section
    html += '<div class="gco-section"><div class="gco-title">Participants</div>';
    for (const sid of sids) {
      const short = sid.substring(0, 8).toLowerCase();
      const name = gcDisplayName(sid);
      const color = getParticipantColor(sid, name);
      const waitingOnHashes = waiting.waiting_on_hashes || [];
      const isOnline = !waitingOnHashes.some(h => sid.toLowerCase().startsWith(h.toLowerCase()));

      const spoken = lastSpoken[short];
      const lastSpokeText = spoken ? `last spoke ${gcRelativeTime(spoken)}` : 'never spoke';

      const mention = lastMentioned[short];
      let mentionText = '';
      if (mention) {
        mentionText = `<span class="gco-part-mention-link" title="Last mentioned ${gcRelativeTime(mention.when)} by ${gcDisplayName(mention.by)}:\n&quot;${(0, sharedState.escapeAttr)(mention.snippet)}&quot;">Last mentioned</span>`;
      } else {
        mentionText = '<span class="gco-part-mention-link is-never">Never mentioned</span>';
      }

      const ping = lastPinged[short];
      const pingText = ping
        ? `<span class="gco-part-ping-link" title="Last pinged ${gcRelativeTime(ping.when)}">Last pinged L${ping.number}</span>`
        : '<span class="gco-part-ping-link is-never">Never pinged</span>';

      html += `<div class="gco-part-card" data-gc-part-sid="${(0, sharedState.escapeAttr)(sid)}">`;
      html += `  <div class="gco-part-header">`;
      html += `    <span class="gco-part-swatch" style="background-color: var(--${color})"></span>`;
      html += `    <span class="gco-part-name-title" title="${(0, sharedState.escapeAttr)(sid)}">${(0, sharedState.escapeHtml)(name)}</span>`;
      html += `    <button type="button" class="gco-part-rename-btn" data-gc-rename="${(0, sharedState.escapeAttr)(sid)}" data-gc-rename-name="${(0, sharedState.escapeAttr)(name)}" title="Rename this participant">✎</button>`;
      html += `    <span class="gc-online-dot ${isOnline ? 'is-online' : 'is-waiting'}" title="${isOnline ? 'Online / Idle' : 'Waiting for reply'}"></span>`;
      html += `  </div>`;
      html += `  <div class="gco-part-body">`;
      html += `    <div class="gco-part-info">`;
      html += `      <span class="gco-part-spokentime">${(0, sharedState.escapeHtml)(lastSpokeText)}</span>`;
      html += `      <span class="gco-part-divider">·</span>`;
      html += `      ${mentionText}`;
      html += `      <span class="gco-part-divider">·</span>`;
      html += `      ${pingText}`;
      html += `    </div>`;
      html += `    <button type="button" class="gco-nudge-btn" data-gc-nudge="${(0, sharedState.escapeAttr)(sid)}" data-gc-nudge-name="${(0, sharedState.escapeAttr)(name)}" title="Re-inject the /group-chat prompt into ${(0, sharedState.escapeAttr)(name)}'s session - wakes this agent specifically.">Nudge</button>`;
      html += `  </div>`;
      html += `</div>`;
    }
    html += '</div>';

    // A3: Merge/dedupe ORCHESTRATOR block
    const _gcPaused = data.status === 'paused' || !!data.paused;
    const _gcActiveNow = data.status === 'active';
    const _gcLive = data.participant_live_count || 0;
    const _gcParts = data.participant_count || sids.length;
    const _gcPoll = data.orchestrator_poll_interval || 30;
    const _gcNudge = data.orchestrator_nudge_interval || 60;
    const waitingNames = (waiting.waiting_on_hashes || []).map(h => {
      const full = sids.find(s => s.toLowerCase().startsWith(h.toLowerCase())) || h;
      return gcDisplayName(full);
    });
    const waitingOnText = formatWaitingOn(waitingNames);

    html += '<div class="gco-section">';
    html += '  <div class="gco-title">Orchestration</div>';
    html += '  <div class="gco-orchestrator-status-row">';
    html += `    <span class="gco-nudge-pill ${data.orchestrator_timer_active ? 'is-active' : 'is-inactive'}">Auto-nudge ${data.orchestrator_timer_active ? 'ON' : 'OFF'}</span>`;
    if (_gcActiveNow) {
      html += `    <span class="gco-live-badge" style="font-size: 10px; font-weight: 600; padding: 2px 8px; border-radius: 12px; background: var(--surface); border: 1px solid var(--border); color: var(--text-muted);">${_gcLive} / ${_gcParts} active</span>`;
    }
    html += '  </div>';
    html += '  <div class="gco-waiting-row">';
    html += `    <span class="gco-waiting-text">${(0, sharedState.escapeHtml)(waitingOnText)}</span>`;
    html += '  </div>';

    const chatId = (typeof sharedState._gcReaderId !== 'undefined' && sharedState._gcReaderId) ? sharedState._gcReaderId : '';
    const chatPath = (typeof sharedState._gcReaderPath !== 'undefined' && sharedState._gcReaderPath) ? sharedState._gcReaderPath : '';

    if (_gcPaused) {
      html += `<button type="button" class="gco-power-btn gco-enable" data-gc-enable data-gc-path="${(0, sharedState.escapeAttr)(chatPath)}" data-gc-id="${(0, sharedState.escapeAttr)(chatId)}">▶ Enable orchestration</button>`;
    } else {
      html += `<button type="button" class="gco-power-btn gco-stop" data-gc-stop data-gc-path="${(0, sharedState.escapeAttr)(chatPath)}" data-gc-id="${(0, sharedState.escapeAttr)(chatId)}">■ Stop orchestration</button>`;
    }
    html += '</div>';

    // A3: CHAT ID + File path move into a collapsed "Details" disclosure at the bottom
    if (chatId || chatPath) {
      html += `<details class="gco-details-disclosure">`;
      html += `  <summary class="gco-details-summary">Details</summary>`;
      html += `  <div class="gco-details-content">`;
      if (chatId) {
        html += `    <div class="gco-row"><span class="gco-label">ID:</span> <span class="gco-val gco-copy" title="Click to select - Cmd+C to copy">${(0, sharedState.escapeHtml)(chatId)}</span></div>`;
      }
      if (chatPath) {
        html += `    <div class="gco-row"><span class="gco-label">File:</span> <span class="gco-val gco-copy" title="${(0, sharedState.escapeAttr)(chatPath)}">${(0, sharedState.escapeHtml)(chatPath)}</span></div>`;
      }
      if (_gcActiveNow) {
        const _gcLastNudge = data.orchestrator_last_nudge_at
          ? (0, sharedState.timeAgo)(data.orchestrator_last_nudge_at * 1000)
          : 'not yet';
        html += `    <div class="gco-row"><span class="gco-label">Poll Interval:</span> <span class="gco-val">${_gcPoll}s</span></div>`;
        html += `    <div class="gco-row"><span class="gco-label">Nudge Max:</span> <span class="gco-val">${_gcNudge}s</span></div>`;
        html += `    <div class="gco-row"><span class="gco-label">Last Nudge:</span> <span class="gco-val">${_gcLastNudge}</span></div>`;
      }
      html += `  </div>`;
      html += `</details>`;
    }

    panel.innerHTML = html;

    // Wire per-participant Nudge buttons. Chat path lives on the panel
    // data attrs set above (the same ones the Stop / Enable buttons
    // already use). Failure toasts the error; success briefly disables
    // the button so a frustrated user doesn't double-fire.
    const chatPathForNudge = (panel.querySelector('[data-gc-stop]') || panel.querySelector('[data-gc-enable]') || {}).dataset || {};
    panel.querySelectorAll('[data-gc-nudge]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const sid = btn.getAttribute('data-gc-nudge') || '';
        if (!sid) return;
        btn.disabled = true;
        const restore = btn.textContent;
        btn.textContent = 'Nudging…';
        try {
          const res = await fetch('/api/group-chat/nudge', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              path: chatPathForNudge.gcPath || '',
              id: chatPathForNudge.gcId || '',
              target_sid: sid,
            }),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) throw new Error((data && data.error) || ('HTTP ' + res.status));
          btn.textContent = 'Nudged ✓';
          const _nudgedName = btn.getAttribute('data-gc-nudge-name') || sid.slice(0, 8);
          if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Nudged ' + _nudgedName, 'success');
          const _gcBodyEl = document.getElementById('gcReaderBody');
          if (_gcBodyEl) {
            const _notice = document.createElement('div');
            _notice.className = 'gc-nudge-notice';
            _notice.textContent = '↗ Pinged ' + _nudgedName;
            _gcBodyEl.appendChild(_notice);
            setTimeout(() => { if (_notice.parentElement) _notice.remove(); }, 8000);
          }
        } catch (err) {
          btn.textContent = 'Nudge failed';
          if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Nudge failed: ' + (err && err.message || 'unknown'), 'error');
        } finally {
          setTimeout(() => { btn.disabled = false; btn.textContent = restore; }, 1800);
        }
      });
    });

    panel.querySelectorAll('[data-gc-rename]').forEach(btn => {
      btn.addEventListener('click', () => {
        const sid = btn.getAttribute('data-gc-rename') || '';
        if (!sid) return;
        const currentName = btn.getAttribute('data-gc-rename-name') || '';
        renameGroupChatParticipant(chatPathForNudge.gcPath || '', chatPathForNudge.gcId || '', sid, currentName);
      });
    });

    const stopBtn = panel.querySelector('[data-gc-stop]');
    if (stopBtn) {
      stopBtn.addEventListener('click', () => {
        setGroupChatPaused(stopBtn.dataset.gcPath || '', stopBtn.dataset.gcId || '', true);
      });
    }
    const enableBtn = panel.querySelector('[data-gc-enable]');
    if (enableBtn) {
      enableBtn.addEventListener('click', () => {
        setGroupChatPaused(enableBtn.dataset.gcPath || '', enableBtn.dataset.gcId || '', false);
      });
    }
  }

  function gcParseChatTimestamp(s) {
    if (!s) return null;
    const cleaned = String(s).trim().replace(/\s+(?:Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday)\s+/i, ' ');
    const m = cleaned.match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\s+([A-Z]{2,5}))?$/);
    if (!m) {
      const d = new Date(cleaned);
      return isNaN(d.getTime()) ? null : d;
    }
    // Treat the parsed pieces as local-clock and let the browser apply
    // the host TZ. The trailing PDT/UTC token is informational — we don't
    // try to reapply it because the wall time was already written for that
    // zone (whoever wrote the file), and the difference is usually <1 day.
    const d = new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
    return isNaN(d.getTime()) ? null : d;
  }

  function gcRelativeTime(s) {
    const d = gcParseChatTimestamp(s);
    if (!d) return String(s || '');
    const delta = Date.now() - d.getTime();
    const abs = Math.abs(delta);
    const sec = Math.round(abs / 1000);
    if (sec < 45) return 'just now';
    const min = Math.round(sec / 60);
    if (min < 60) return min + 'm ago';
    const hr = Math.round(min / 60);
    if (hr < 24) return hr + 'h ago';
    const day = Math.round(hr / 24);
    if (day < 14) return day + 'd ago';
    const wk = Math.round(day / 7);
    if (wk < 8) return wk + 'w ago';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  }

  function gcTimeChip(s) {
    const rel = gcRelativeTime(s);
    if (rel === String(s || '')) return (0, sharedState.escapeHtml)(rel);
    return `<span class="gco-time" title="${(0, sharedState.escapeAttr)(String(s))}">${(0, sharedState.escapeHtml)(rel)}</span>`;
  }

  function _gcShowNewPostsPill(body) {
    const host = body.parentElement || body;
    if (host.querySelector('.gc-new-posts-pill')) return;
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'gc-new-posts-pill';
    pill.textContent = '↓ New posts';
    pill.addEventListener('click', () => {
      body.scrollTop = body.scrollHeight;
      pill.remove();
    });
    const onScroll = () => {
      if (body.scrollHeight - body.scrollTop <= body.clientHeight + 40) {
        pill.remove();
        body.removeEventListener('scroll', onScroll);
      }
    };
    body.addEventListener('scroll', onScroll);
    host.appendChild(pill);
  }

  async function pollGroupChatReader() {
    if (!sharedState._gcReaderPath && !sharedState._gcReaderId) return;
    const body = document.getElementById('gcReaderBody');
    if (!body) return;
    if (sharedState._gcReplayActive) return;
    try {
      const params = new URLSearchParams();
      if (sharedState._gcReaderPath) params.set('path', sharedState._gcReaderPath);
      if (sharedState._gcReaderId) params.set('id', sharedState._gcReaderId);
      const res = await fetch('/api/group-chat/read?' + params.toString());
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      sharedState._gcPollFailCount = 0;
      const errBanner = body.querySelector('.gc-poll-error');
      if (errBanner) errBanner.remove();
      if (!data.ok) { body.innerHTML = '<div class="assistant-text gc-chat-doc" dir="auto">' + (0, sharedState.renderMarkdown)(data.error || 'File not found.') + '</div>'; return; }
      sharedState._gcReaderSessionIds = data.session_ids || [];
      sharedState._gcReaderNameMap = data.name_map || {};
      buildAgentFallbackNames(data.content, sharedState._gcReaderSessionIds);
      sharedState._gcReplayData = data;
      try { updateGcInfoBar(data); } catch (_) {}
      // Backfill topic/mode from the authoritative read. A group-chat
      // popout opened by id alone boots with a placeholder topic; once
      // the real one arrives, sync the header, in-memory state and (in
      // popout mode) the window title so the tab stops reading "Group
      // chat".
      if (data.topic && data.topic !== sharedState._gcReaderTopic) {
        sharedState._gcReaderTopic = data.topic;
        const topicEl = document.querySelector('#gcReader .gc-reader-header .gc-topic');
        if (topicEl) {
          topicEl.textContent = data.topic;
          topicEl.setAttribute('title', data.topic);
        }
        if (sharedState.GROUPCHAT_POPOUT_MODE) {
          try { document.title = data.topic; } catch (_) {}
        }
      }
      if (data.mode && data.mode !== sharedState._gcReaderMode) {
        sharedState._gcReaderMode = data.mode;
        const modeEl = document.querySelector('#gcReader .gc-reader-header .gc-mode-badge');
        if (modeEl) modeEl.textContent = data.mode;
      }
      const shouldRenderTranscript = data.mtime !== sharedState._gcLastMtime;
      if (shouldRenderTranscript) {
        sharedState._gcUnchangedPolls = 0;
      } else {
        // Drives the reader poll's inactivity backoff (3s → 10s → 30s).
        sharedState._gcUnchangedPolls += 1;
      }
      if (shouldRenderTranscript) {
        const isFirstLoad = sharedState._gcLastMtime === null;
        const atBottom = body.scrollHeight - body.scrollTop <= body.clientHeight + 40;

        // Render into a local FIRST. Previously _gcLastMtime was advanced
        // before this line, so if renderGroupChatMarkdown threw even once
        // (e.g. a markdown/regex path some engines choke on) the mtime guard
        // would then skip the render on every later poll — leaving the body
        // stuck on "Loading…" forever with no error and no retry. Commit the
        // mtime only after a successful render, and surface the real error
        // instead of a silent placeholder (mac-app WKWebView has no devtools).
        let _html;
        try {
          _html = renderGroupChatMarkdown(_gcExpandHashIds(data.content));
        } catch (renderErr) {
          body.innerHTML = '<div class="gc-poll-error">⚠ Could not render chat: '
            + (0, sharedState.escapeHtml)((renderErr && renderErr.message) || String(renderErr)) + '</div>';
          return;  // leave _gcLastMtime unchanged so the next poll retries
        }
        sharedState._gcLastMtime = data.mtime;
        body.innerHTML = _html;
        if (data.name_map || sharedState._gcReaderSessionIds.length) {
          try { replaceParticipantMentions(body, data.name_map); } catch (_) {}
        }
        try { updateOrchestratorPanel(data, data.content); } catch (_) {}

        if (!isFirstLoad) {
          try {
            showUpdatedJustNowTick();
            const lastMsg = body.querySelector('.gc-message:last-of-type');
            if (lastMsg) lastMsg.classList.add('gc-message-new-flash');
          } catch (_) {}
        }
        if (atBottom) body.scrollTop = body.scrollHeight;
        else if (!isFirstLoad) _gcShowNewPostsPill(body);

        // Nudge all participants when content changes (but not on first load, and debounced to 15s).
        if (!isFirstLoad) {
          const now = Date.now();
          if (now - sharedState._gcLastNudgeTime > 15000) {
            sharedState._gcLastNudgeTime = now;
            markActiveGroupChatTrigger(sharedState._gcReaderPath, sharedState._gcReaderId, sharedState._gcReaderTopic, sharedState._gcReaderMode);
            (0, sharedState.ccPostJson)('/api/group-chat/nudge', {
              path: sharedState._gcReaderPath,
              id: sharedState._gcReaderId,
            }).catch(() => {});
          }
        }
      } else {
        try { updateOrchestratorPanel(data, data.content); } catch (_) {}
      }
    } catch (_err) {
      sharedState._gcPollFailCount++;
      if (sharedState._gcPollFailCount >= 3) {
        let errBanner = body.querySelector('.gc-poll-error');
        if (!errBanner) {
          errBanner = document.createElement('div');
          errBanner.className = 'gc-poll-error';
          body.prepend(errBanner);
        }
        errBanner.textContent = '⚠ Lost connection to chat file - retrying…';
      }
    }
  }

  async function sendHumanGcPost() {
    if (!sharedState._gcReaderPath && !sharedState._gcReaderId) return;
    const input = document.getElementById('gcHumanInput');
    if (!(0, sharedState.guardComposerSend)(input)) return;
    const text = input ? input.value.trim() : '';
    if (!text) return;
    try {
      await (0, sharedState.ccPostJson)('/api/group-chat/post', { path: sharedState._gcReaderPath, id: sharedState._gcReaderId, text });
      if (input) input.value = '';
      sharedState._gcLastMtime = null;  // force re-render even if file mtime hasn't updated yet
      await pollGroupChatReader();
    } catch (err) {
      (0, sharedState.showOpToast)('Send failed: ' + err.message, 'error');
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
    clone.querySelectorAll('.conv-scroll-end-btn').forEach(el => el.remove());
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

  function getActiveGroupChatPillEl() {
    if (!sharedState._activeGroupChatPillEl) {
      sharedState._activeGroupChatPillEl = document.getElementById('activeGroupChatPill');
    }
    return sharedState._activeGroupChatPillEl;
  }

  function gcEpochMs(value) {
    if (value === null || value === undefined || value === '') return 0;
    const n = Number(value);
    if (Number.isFinite(n) && n > 0) return n > 100000000000 ? n : n * 1000;
    const parsed = Date.parse(String(value));
    return Number.isFinite(parsed) ? parsed : 0;
  }

  function gcTriggerMs(chat) {
    if (!chat) return 0;
    return Math.max(
      gcEpochMs(chat.orchestrator_last_trigger_at),
      gcEpochMs(chat.orchestrator_last_nudge_at),
      gcEpochMs(chat.last_reminder_at)
    );
  }

  function gcSortMs(chat) {
    if (!chat) return 0;
    return Math.max(
      gcTriggerMs(chat),
      gcEpochMs(chat.last_activity),
      gcEpochMs(chat.last_mtime),
      gcEpochMs(chat.started_at)
    );
  }

  function gcShouldShowActivePill(chat, now) {
    if (!chat) return false;
    // Paused / closed / orchestrator-explicitly-off → never show. The
    // pill claims "active right now"; if the user just clicked Stop the
    // pill must respect that immediately, not coast on the freshness
    // window. Without this, a chat that the user stopped seconds ago
    // kept saying "auto-pinged · just now" because the trigger
    // timestamp from before the stop was still within the freshness
    // window.
    if (chat.status === 'paused' || chat.status === 'closed') return false;
    if (chat.paused === true) return false;
    if (chat.orchestrator_timer_active === false) return false;
    // Even when the server still has it in the watcher (status==='active'
    // for up to 45 min after the last file change), the pill should drop
    // off if there hasn't been a real trigger/activity recently. The
    // user reads "Active Group chat" as "happening right now", not "in
    // the watcher backlog". last_mtime is intentionally OMITTED — it's
    // the chat file's stat mtime which the server bumps on metadata
    // writes (name_map updates, polled sidecar writes), not real
    // message arrivals. The label-side code already filtered it out
    // for the same reason; the show/hide gate should too.
    const freshness = Math.max(
      gcTriggerMs(chat),
      gcEpochMs(chat.last_activity)
    );
    if (freshness > 0 && (now - freshness) <= sharedState.GC_ACTIVE_PILL_FRESHNESS_MS) return true;
    // Allow the immediate post-trigger window even if the freshness
    // fields are momentarily stale on the next poll tick.
    const triggerAt = gcTriggerMs(chat);
    return triggerAt > 0 && (now - triggerAt) <= sharedState.GC_ACTIVE_TRIGGER_RECENT_MS;
  }

  function activeGroupChatPillTarget() {
    const now = Date.now();
    const matches = (sharedState._gcActiveChats || [])
      .filter(chat => gcShouldShowActivePill(chat, now))
      .sort((a, b) => gcSortMs(b) - gcSortMs(a));
    if (matches.length) return matches[0];
    if (sharedState._gcLastLocalTrigger && (now - sharedState._gcLastLocalTrigger.at) <= sharedState.GC_ACTIVE_TRIGGER_RECENT_MS) {
      return sharedState._gcLastLocalTrigger.chat;
    }
    return null;
  }

  function updateActiveGroupChatPill() {
    const $activeGroupChatPill = getActiveGroupChatPillEl();
    if (!$activeGroupChatPill) return;
    const chat = activeGroupChatPillTarget();
    if (!chat) {
      $activeGroupChatPill.hidden = true;
      $activeGroupChatPill.removeAttribute('data-chat-id');
      return;
    }
    const chatId = chat.uuid || chat.id || '';
    const topic = chat.topic || 'Group chat';
    const now = Date.now();
    const activeCount = (sharedState._gcActiveChats || []).filter(c => gcShouldShowActivePill(c, now)).length;
    const label = $activeGroupChatPill.querySelector('.active-group-chat-label');
    // Surface the actual trigger reason on the pill itself (annotation:
    // "what is it that triggered RIGHT NOW — put it on the UI even if
    // the pill becomes long"). Pick the freshest of nudge / trigger /
    // file-activity timestamps and label accordingly. Truncate target
    // names so a 5-agent chat doesn't run off the screen.
    const _nudgeMs = gcEpochMs(chat.orchestrator_last_nudge_at)
      || gcEpochMs(chat.last_reminder_at);
    const _triggerMs = gcEpochMs(chat.orchestrator_last_trigger_at);
    // ONLY last_activity counts as a real "new message" signal.
    // last_mtime is just the chat file's stat mtime, which the server bumps
    // on metadata writes (name_map updates, polled sidecar writes, etc.)
    // every poll cycle — that's not a message arrival, so using it here
    // made the pill say "new message · just now" forever while the actual
    // last message was minutes/hours old (user-annotated 4.5min stale).
    const _activityMs = gcEpochMs(chat.last_activity);
    const _freshAt = Math.max(_nudgeMs, _triggerMs, _activityMs);
    let _reason = '';
    if (_freshAt > 0) {
      // orchestrator_last_reminder_targets only carries short (8-char) hash
      // fragments off the sidecar — resolve them to a friendly name via this
      // chat's own name_map so "who got pinged" is legible instead of hex
      // (annotation: "let's include ... the session who was pinged").
      const _nm = chat.name_map || {};
      const _sids = chat.session_ids || [];
      const targets = (chat.orchestrator_last_reminder_targets || [])
        .filter(t => t && String(t).trim())
        .map(t => {
          const shortId = String(t);
          const fullSid = _sids.find(id => id && id.toLowerCase().startsWith(shortId.toLowerCase()));
          const name = fullSid ? _nm[fullSid] : '';
          return (name && !/^[0-9a-f]{8}$/i.test(String(name).trim())) ? name : shortId;
        });
      const targetStr = targets.length
        ? (targets.length <= 3
            ? targets.join(', ')
            : targets.slice(0, 2).join(', ') + ' +' + (targets.length - 2))
        : '';
      if (_freshAt === _nudgeMs && targetStr) {
        _reason = 'nudged ' + targetStr;
      } else if (_freshAt === _triggerMs) {
        _reason = targetStr ? 'auto-pinged ' + targetStr : 'auto-pinged';
      } else if (_freshAt === _activityMs) {
        _reason = 'new message';
      }
    }
    const _ageMin = _freshAt > 0 ? Math.max(0, Math.round((now - _freshAt) / 60000)) : null;
    // Pair the relative age with a wall-clock time right on the pill —
    // "just now" alone isn't verifiable (annotation: "i don't believe it,
    // let's include the timestamp"); the tooltip already had this but only
    // on hover.
    const _ageHint = _ageMin == null
      ? ''
      : (_ageMin === 0 ? 'just now' : _ageMin + 'm ago') + ' (' + new Date(_freshAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' }) + ')';
    // Build label: "<topic> · <reason> · <age>" (single) or
    // "N chats — <topic> · <reason> · <age>" (multiple). Reason/age
    // get filtered out cleanly if either is missing.
    const segments = [];
    if (activeCount > 1) segments.push(activeCount + ' chats - ' + topic);
    else segments.push(topic);
    if (_reason) segments.push(_reason);
    if (_ageHint) segments.push(_ageHint);
    if (label) label.textContent = segments.join(' · ');
    $activeGroupChatPill.hidden = false;
    if (chatId) $activeGroupChatPill.dataset.chatId = chatId;
    // Tooltip keeps the verbose breakdown for hover.
    const _tipLines = [
      activeCount > 1
        ? activeCount + ' active group-chat orchestrations are looping (tokens in use). Latest: ' + topic
        : 'Active group-chat orchestration: ' + topic,
    ];
    if (_reason) _tipLines.push('Latest trigger: ' + _reason);
    if (_freshAt > 0) _tipLines.push('At ' + new Date(_freshAt).toLocaleTimeString());
    _tipLines.push('Click to open the latest active chat.');
    $activeGroupChatPill.title = _tipLines.join(' · ');
  }

  function markActiveGroupChatTrigger(chatPath, chatId, topic, mode) {
    sharedState._gcLastLocalTrigger = {
      at: Date.now(),
      chat: {
        id: chatId || '',
        uuid: chatId || '',
        path: chatPath || '',
        path_tilde: chatPath || '',
        topic: topic || 'Group chat',
        mode: mode || 'topic',
        orchestrator_timer_active: true,
        orchestrator_last_trigger_at: Date.now() / 1000,
      },
    };
    updateActiveGroupChatPill();
  }

  function _gcChatsKey(chats) {
    // STRUCTURAL fields only — what determines whether the group-chat rows
    // need to be laid out differently. Deliberately excludes volatile activity
    // counters (last_mtime, message_count, orchestrator_last_trigger_at,
    // last_reminder_at, waiting_on_hashes): those advance on every poll for a
    // live chat, and including them made pollGcActive re-render the whole list
    // every 15s — shoving all ~500 rows down (the reshuffle). Live counts/times
    // still refresh via the regular sidebar render + in-place time patching.
    return (chats || [])
      .map(c => JSON.stringify([
        c.uuid || c.id || c.path || c.path_tilde || '',
        c.status || '',
        c.topic || '',
        c.orchestrator_timer_active ? 1 : 0,
        (c.session_ids || []).join(','),
      ]))
      .sort()
      .join('|');
  }

  function pollGcActive() {
    if (sharedState._gcActivePollPromise) return sharedState._gcActivePollPromise;
    sharedState._gcActivePollPromise = (async () => {
    try {
      const data = await (0, sharedState.backgroundApiFetch)('/api/group-chats/active').then(r => r.json());
      // Compare a metadata key, not just length, so renames, participant
      // changes, and activity updates redraw the group-chat rows.
      const prevKey = _gcChatsKey(sharedState._gcActiveChats);
      sharedState._gcActiveChats = (data.chats || []).slice().sort((a, b) => (b.last_mtime || 0) - (a.last_mtime || 0));
      // Persist so the next page load seeds the first render from this set and
      // avoids the empty→populated reshuffle.
      try { localStorage.setItem(sharedState._GC_ACTIVE_CACHE_KEY, JSON.stringify(sharedState._gcActiveChats)); } catch (_) {}
      const nextKey = _gcChatsKey(sharedState._gcActiveChats);
      const activeCount = sharedState._gcActiveChats.filter(c => c.status === 'active').length;
      if (activeCount && !sharedState._gcActivePollTimer) {
        sharedState._gcActivePollTimer = setInterval(
          (0, sharedState._gated)('gcActive', () => { pollGcActive(); }),
          15000,
        );
      } else if (!activeCount && sharedState._gcActivePollTimer) {
        clearInterval(sharedState._gcActivePollTimer);
        sharedState._gcActivePollTimer = null;
      }
      updateActiveGroupChatPill();
      if (sharedState.$gcActiveBtn) {
        if (activeCount === 0) {
          sharedState.$gcActiveBtn.style.display = 'none';
        } else {
          sharedState.$gcActiveBtn.textContent = activeCount === 1
            ? '💬 1 active coordination'
            : `💬 ${activeCount} active coordinations`;
          sharedState.$gcActiveBtn.style.display = '';
        }
      }
      // Re-render the list whenever the active-chat set changes so the
      // "In Group Chat" header appears/disappears without waiting for
      // the next archive poll.
      if (nextKey !== prevKey) {
        (0, sharedState._scheduleSidebarRender)();
      }
    } catch (_) {
      updateActiveGroupChatPill();
    } finally { sharedState._gcActivePollPromise = null; }
    })();
    return sharedState._gcActivePollPromise;
  }

  async function setGroupChatPaused(chatPath, chatId, paused) {
    if (!chatPath && !chatId) return;
    try {
      const res = await fetch('/api/group-chats/pause', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: chatPath || '', id: chatId || '', paused: !!paused }),
      });
      const data = await res.json().catch(() => ({}));
      if (!data || !data.ok) {
        (0, sharedState.showOpToast)?.('Could not ' + (paused ? 'disable' : 'enable') + ' group chat: ' + ((data && data.error) || 'unknown'), 'error');
        return;
      }
      // Optimistic local update so the red pill drops instantly when
      // the user clicks Stop, instead of lingering until the next
      // gcActive poll lands ~15s later.
      if (Array.isArray(sharedState._gcActiveChats)) {
        const match = sharedState._gcActiveChats.find(c =>
          (chatPath && (c.path_tilde === chatPath || c.path === chatPath))
          || (chatId && (c.uuid === chatId || c.id === chatId))
        );
        if (match) {
          match.paused = !!paused;
          match.orchestrator_timer_active = !paused;
          if (paused) match.status = 'paused';
          else if (match.status === 'paused') match.status = 'active';
        }
      }
      try { updateActiveGroupChatPill(); } catch (_) {}
      try { await pollGcActive(); } catch (_) {}
      const $s = document.getElementById('convSearch');
      (0, hostServices.renderArchiveList)($s ? $s.value : '');
      // If this chat's reader is open, refresh its orchestrator panel.
      if ((sharedState._gcReaderPath && (sharedState._gcReaderPath === chatPath)) || (sharedState._gcReaderId && chatId && sharedState._gcReaderId === chatId)) {
        try { pollGroupChatReader(); } catch (_) {}
      }
      (0, sharedState.showOpToast)?.(paused ? 'Orchestration disabled - no more nudges' : 'Orchestration enabled');
    } catch (err) {
      (0, sharedState.showOpToast)?.('Could not update group chat: ' + ((err && err.message) || 'network error'), 'error');
    }
  }

  async function addSessionToGroupChat(chatPath, sessionId, displayName, chatId) {
    if ((!chatPath && !chatId) || !sessionId) return;
    try {
      const res = await fetch('/api/group-chats/add-participant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: chatPath || '', id: chatId || '', session_id: sessionId, display_name: displayName || '',
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!data || !data.ok) {
        (0, sharedState.showOpToast)?.('Could not add session: ' + ((data && data.error) || 'unknown'), 'error');
        return;
      }
      try { await pollGcActive(); } catch (_) {}
      if (data.already_participant) {
        (0, sharedState.showOpToast)?.('Already in this chat');
      } else {
        (0, sharedState.showOpToast)?.('Session added to chat');
      }
    } catch (err) {
      (0, sharedState.showOpToast)?.('Could not add session: ' + ((err && err.message) || 'network error'), 'error');
    }
  }

  async function renameGroupChatParticipant(chatPath, chatId, sid, currentName) {
    if ((!chatPath && !chatId) || !sid) return;
    let name = '';
    try { name = (window.prompt('New name for this participant:', currentName || '') || '').trim(); } catch (_) {}
    if (!name || name === currentName) return;
    try {
      const res = await fetch('/api/group-chats/rename-participant', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: chatPath || '', id: chatId || '', session_id: sid, name }),
      });
      const data = await res.json().catch(() => ({}));
      if (!data || !data.ok) {
        (0, sharedState.showOpToast)?.('Could not rename: ' + ((data && data.error) || 'unknown'), 'error');
        return;
      }
      try { await pollGroupChatReader(); } catch (_) {}
      (0, sharedState.showOpToast)?.('Participant renamed to ' + name);
    } catch (err) {
      (0, sharedState.showOpToast)?.('Could not rename: ' + ((err && err.message) || 'network error'), 'error');
    }
  }

  function _hasSid(arr, sid) {
    return Array.isArray(arr) && arr.some(r => r && (r.session_id || r.id) === sid);
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
