/* Canonical archive reads, session navigation and ticket details. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices, functions: deferredServices }, owner) {
  function popoutConversationIdForRow(row) {
    return (row && (row.id || row.session_id)) || sharedState.CONV_POPOUT_TARGET;
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
      (0, deferredServices.persistFlowNodeParents)();
      persistFlowNodePositions();
    } catch (_) {}
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
    if ((0, deferredServices._currentSidebarTabKey)() !== 'ideas' || target === 'ideas') return;
    try { window.CCCNavigation.setTab(target); } catch (_) {}
    (0, sharedState.showConvToast)('Filed in Small bugs');
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

  function persistFlowNodePositions() {
    try { localStorage.setItem('ccc-flow-node-positions', JSON.stringify(sharedState.flowNodePositions)); } catch (_) {}
  }

  function reconcilePendingNewSessionObjectAssignments() {
    const pending = (0, deferredServices.loadPendingNewSessionObjectAssignments)();
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
      (0, deferredServices.assignSessionNodeToParent)(sid, rec.parent_node || (0, sharedState.flowNodeKey)('object', sharedState.NEW_SESSION_DEFAULT_OBJECT_ID));
      delete pending[key];
      changed = true;
    }
    if (changed) (0, deferredServices.savePendingNewSessionObjectAssignments)(pending);
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
      try { (0, deferredServices.stopFlowNodeInspector)(); } catch (_) {}
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
    if (typeof deferredServices.setStatusRailTab === 'function') (0, deferredServices.setStatusRailTab)('metadata');
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
    if (typeof deferredServices.syncBodyGcReaderFlag === 'function') (0, deferredServices.syncBodyGcReaderFlag)();

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

  function _uxqSyncTicketMeta(items) {
    const previous = sharedState.uxFixesQueueMeta && sharedState.uxFixesQueueMeta._sig;
    const meta = (0, sharedState._setUxFixesQueueMeta)(items);
    if (meta._sig !== previous) (0, sharedState._scheduleSidebarRender)({ lifecycle: true });
  }

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

  const closeAttempts = new Map();

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
        const cres = await fetch('/api/ux-fixes/item?cached=1&ref=' + encodeURIComponent(ref), {
          cache: 'no-store', cccPriority: 'user',
        });
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
    const fallbackModal = fallback && document.getElementById('uxqItemModal');
    const attempt = closeAttempts.get(ref);
    const wasClosing = attempt?.pending;
    try {
      const res = await fetch('/api/ux-fixes/item?ref=' + encodeURIComponent(ref), {
        cache: 'no-store', cccPriority: 'user',
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok && data.item) {
        if (!fallback) {
          _uxqOpenItemModal(data.item);
          return;
        }
        // Hydrate only if this ticket's modal is still open and the user
        // hasn't started typing into it — a rebuild would wipe their input.
        const modal = document.getElementById('uxqItemModal');
        if (!modal || modal !== fallbackModal || (0, sharedState._uxqItemRef)(data.item) !== ref
            || (wasClosing && !attempt.pending)) return;
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

  function _uxqTicketNotice(ref, message) {
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
      const notice = _uxqTicketNotice(ref, 'Sending answer in the background…');
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
            _uxqTicketNotice(ref, 'Answer recorded; worker notification could not be confirmed. Check Activity before sending again.');
          } else {
            (0, sharedState.showOpToast)((0, sharedState.escapeHtml)(ref + (data.images_stripped
              ? ': Answer recorded; images were omitted for this GitHub ticket.'
              : ': Answer recorded.')), 'success');
          }
        } catch (error) {
          _uxqTicketNotice(ref, 'Answer not confirmed: ' + (error.message || error)
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
    const canRequestRun = status === 'open' && !hasActiveClaim && !closeAttempts.get(ref)?.pending;
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
        + '<div class="uxq-td-answer-hint" data-close-status role="status"></div>'
        + '</div>'
      : '';

    // Build modal DOM
    const modal = document.createElement('div');
    modal.id = 'uxqItemModal';
    modal.dataset.ticketRef = ref;
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
      const previous = closeAttempts.get(ref);
      if (previous) {
        markClosedInput.value = previous.draft;
        markClosedInput.disabled = markClosedBtn.disabled = previous.pending;
        markClosedBtn.textContent = previous.pending ? 'Closing…' : 'Mark as closed';
        modal.querySelector('[data-close-status]').textContent = previous.message;
      }

      const doMarkClosed = async () => {
        if (markClosedBtn.disabled || closeAttempts.get(ref)?.pending) return;
        const draft = markClosedInput.value;
        const attempt = { draft, pending: true, message: 'Closing in the background…' };
        closeAttempts.set(ref, attempt);
        close();
        const notice = _uxqTicketNotice(ref, attempt.message);
        let d;
        try {
          const body = JSON.stringify({ ref, note: draft.trim() });
          const res = await fetch('/api/ux-fixes/close', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
            keepalive: new TextEncoder().encode(body).length < 60000,
            cccRetryHandled: true,
          });
          d = await res.json();
          if (!res.ok || d.ok === false) {
            throw new Error(d.error || ('HTTP ' + res.status));
          }
          if (!d.ok || d.item?.ref !== ref || !d.item.status) {
            throw new Error('No ticket status in the response.');
          }
        } catch (error) {
          attempt.message = 'Close unconfirmed. Check Activity before trying again. ' + error.message;
          d = null;
        } finally {
          attempt.pending = false;
          notice?.remove();
        }
        const status = d?.item.status;
        const confirmed = status === 'closed' || status === 'in_review';
        if (confirmed) {
          closeAttempts.delete(ref);
          attempt.message = status === 'closed' ? 'Ticket closed' : 'Sent for review';
          (0, sharedState.showOpToast)((0, sharedState.escapeHtml)(ref + ': ' + attempt.message), 'success');
        } else {
          if (d) attempt.message = 'Ticket is still open. ' + (d.item.gate_feedback || 'Check Activity for details.');
          _uxqTicketNotice(ref, attempt.message);
        }
        // The user may have opened this ticket again while the close ran.
        const current = document.getElementById('uxqItemModal');
        if (current?.dataset.ticketRef === ref) {
          const button = current.querySelector('.uxq-td-mark-closed-confirm');
          if (button) {
            button.disabled = confirmed;
            button.textContent = confirmed ? attempt.message : 'Mark as closed';
            current.querySelector('.uxq-td-mark-closed-input').disabled = confirmed;
            current.querySelector('[data-close-status]').textContent = attempt.message;
          }
        }
        if (d) {
          sharedState._uxqHealthCache.ts = 0;
          if (_uxqReplaceCachedItem(d.item)) {
            (0, hostServices._renderQueuePanel)({ allowStale: true });
          } else {
            sharedState._uxqItemsCache.ts = 0;
            (0, hostServices._renderQueuePanel)();
          }
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
    const groups = {
      "conversation-bootstrap.js": { popoutConversationIdForRow },
      "conversation-services.js": { restoreLastViewOrConversation, conversationRowsContainId, restoreLastConversation, _signalSnapshot, stampFreshSessionsAsBorn, renderPopoutMissingConversation, maybeSelectPopoutConversation, carryFlowPendingSpawnNode, followPendingSpawnTicket, loadConversationList, persistFlowNodePositions, reconcilePendingNewSessionObjectAssignments, gcFallbackName, gcDisplayName, gcInitials, extractHash, buildAgentFallbackNames, getParticipantColor, formatWaitingOn, updateGcInfoBar, showUpdatedJustNowTick, startReplay, renderReplayControls, stepReplayBack, exitReplayMode, _gcReplayClearHero, _gcReplayCreateHero, _gcReplayMessagePauseMs, _gcReplayHoldThenLandHero, _gcReplayLandHero, _gcReplayHumanStep, _gcReplayParseWhen, _mergeReplayQueueEvents, _renderReplayQueueEvent, playNextReplayStep, _gcReplayReceiptsHtml, _gcReplayAlertsHtml, _gcMentionClose, _gcMentionParticipants, _gcMentionDetectToken, _gcMentionRefresh, _gcMentionCommit, _gcMentionMenuHandleKeydown, wireGcMentionAutocomplete, markSystemBlockquotes, gcExtractPingedNames, renderGroupChatMarkdown, openGcParticipantPicker, openGroupChatReader, _gcExpandHashIds, replaceParticipantMentions, updateOrchestratorPanel, gcParseChatTimestamp, gcRelativeTime, gcTimeChip, _gcShowNewPostsPill, pollGroupChatReader, sendHumanGcPost, getActiveGroupChatPillEl, gcEpochMs, gcTriggerMs, gcSortMs, gcShouldShowActivePill, activeGroupChatPillTarget, updateActiveGroupChatPill, markActiveGroupChatTrigger, _gcChatsKey, pollGcActive, setGroupChatPaused, addSessionToGroupChat, renameGroupChatParticipant, _hasSid },
      "conversation-views.js": { _uxqSyncTicketMeta, _uxqReviewLabel, _uxqWorkerSid, _uxqGateName, _uxqChecksHtml, _uxqWithMachine, _uxqReplaceCachedItem, _uxqOpenItemDetail, _uxqItemPrompt, _uxqLoadLinkedConversation, _uxqItemTitle, _uxqSaveField, _uxqRelTime, _uxqExtractImages, _uxqCloseNoteModal, _uxqOpenNoteModal, _uxqInitTicketZoom, _uxqAnswerDraft, _uxqTicketNotice, _uxqWireTicketAnswer, _uxqSessionActivityHtml, _uxqBindSessionActivity, _uxqOpenItemModal }
    };
    return owner ? groups[owner] : Object.assign({}, ...Object.values(groups));
  }
  function createArchiveController({ state: sharedState, helpers: hostServices, dependencies }) {
    const {
      _archiveWindow, _archiveRowStableKey, _archiveQuery, _queueDashboardArchiveRender,
      _renderArchiveIfLoaded, _clientLog, _releaseStartupApiReads, _whenDashboardUncovered,
      refreshLiveSessionsActivity, _mergeIntoMasterRows, _gated, backgroundApiFetch,
      refreshUxFixesQueueMeta, loadCrossRepoIssues, refreshArchivedGroupChats, loadRepoList,
      _dashboardCoveredByApp, shouldPauseSidebarRender, _perfEvent, _perfActiveElapsed,
      _perfLongTaskMs, _convResourceTiming, hideLoadingOverlay,
    } = dependencies;

  function _markArchiveFirstLoaded() {
    hideLoadingOverlay();
    // Cached/recent rows make the sidebar usable, but optional startup probes
    // still compete with the complete archive read. Keep readiness separate
    // from releasing that network backlog; user actions retain their bypass.
    const progress = window.__cccArchiveProgress;
    if (!sharedState._archiveFromLocalCache && (!progress || progress.complete || progress.failed)) {
      _releaseStartupApiReads();
    }
    if (!sharedState._archiveFirstLoadedResolve) return;
    (0, sharedState._archiveFirstLoadedResolve)();
    sharedState._archiveFirstLoadedResolve = null;
  }

  function _applyPendingDashboardConversationPatches(rows) {
    if (!Array.isArray(rows) || !sharedState._dashboardEventState.pendingConversationPatches.size) return rows;
    return rows.map(row => {
      const id = String(row && (row.session_id || row.id) || '');
      const entry = id && sharedState._dashboardEventState.pendingConversationPatches.get(id);
      if (entry && entry.expiresAt <= Date.now()) {
        sharedState._dashboardEventState.pendingConversationPatches.delete(id);
        return row;
      }
      const patch = entry && entry.patch;
      return patch ? Object.assign({}, row, patch) : row;
    });
  }

  function _resetArchiveUnknownPatchIds() { sharedState._archiveUnknownPatchIds = new Set(); }

  function _mergeArchivePrSnapshot(freshRows, previousRows) {
    const fresh = Array.isArray(freshRows) ? freshRows : [];
    const previous = Array.isArray(previousRows) ? previousRows : [];
    const prevByKey = new Map();
    for (const row of previous) {
      const key = _archiveRowStableKey(row);
      if (key) prevByKey.set(key, row);
    }

    const seen = new Set();
    const out = fresh.map(row => {
      const key = _archiveRowStableKey(row);
      if (key) seen.add(key);
      const prev = key ? prevByKey.get(key) : null;
      // Keep the network snapshot independent without growing a wide row
      // property by property into V8's slower dictionary representation.
      const merged = { ...row };
      if (key) {
        const override = sharedState._recentRenameOverrides.get(key);
        if (override) {
          if (merged.display_name === override.display_name) {
            sharedState._recentRenameOverrides.delete(key);
          } else if (Date.now() - override.at < sharedState.RENAME_OVERRIDE_MAX_AGE_MS) {
            merged.display_name = override.display_name;
            merged.name_overridden = override.name_overridden;
          } else {
            sharedState._recentRenameOverrides.delete(key);
          }
        }
      }
      if (!prev) return merged;
      const copyIfMissing = (field) => {
        const val = merged[field];
        const emptyArray = Array.isArray(val) && val.length === 0;
        if (val === undefined || val === null || val === '' || emptyArray) {
          if (prev[field] !== undefined && prev[field] !== null) merged[field] = prev[field];
        }
      };
      [
        'tail_pr_number', 'tail_pr_url', 'pr_state', 'pr_notes',
        'pr_is_draft', 'pr_mergeable', 'pr_review_decision',
      ].forEach(copyIfMissing);
      return merged;
    });

    for (const row of previous) {
      const key = _archiveRowStableKey(row);
      if (!key || seen.has(key)) continue;
      if (row.source === 'github_pr') out.push(row);
    }
    return out;
  }

  function _archiveSidebarCacheKey(windowName) {
    return sharedState.ARCHIVE_SIDEBAR_CACHE_KEY + ':' + windowName;
  }

  function _archiveSidebarCacheRow(row) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) return null;
    if (typeof (row.session_id || row.id) !== 'string') return null;
    const out = {};
    for (const key of sharedState.ARCHIVE_SIDEBAR_CACHE_FIELDS) {
      const value = row[key];
      if (typeof value === 'string') {
        out[key] = (key === 'first_message' || key === 'ai_title' || key === 'display_name')
          ? value.slice(0, 400) : value;
      } else if (typeof value === 'boolean' || (typeof value === 'number' && Number.isFinite(value))) {
        out[key] = value;
      }
    }
    // Ticket identity determines which session tab owns this row. Without
    // it, cached sessions all fall into Other until the network list lands.
    // Keep only routing/lifecycle fields, never handoff questions or actions.
    const ticket = row.idea_ticket;
    if (ticket && typeof ticket === 'object' && !Array.isArray(ticket)) {
      out.idea_ticket = {};
      for (const key of ['ref', 'queue']) {
        if (typeof ticket[key] === 'string') out.idea_ticket[key] = ticket[key].slice(0, 120);
      }
      for (const key of ['closed', 'pending', 'explicitly_archived']) {
        if (typeof ticket[key] === 'boolean') out.idea_ticket[key] = ticket[key];
      }
      if (Number.isFinite(ticket.moved_at)) out.idea_ticket.moved_at = ticket.moved_at;
    }
    return out;
  }

  function _writeArchiveSidebarCache(rows, windowName) {
    try {
      const entries = [];
      // Leave room for the small envelope; count serialized characters so
      // UTF-16 localStorage usage stays below 512 KiB even with long titles.
      let chars = 256;
      // Reserve the bounded preview for ticket rows first: a busy worker
      // fleet must not crowd every Features/Small bugs session out of cache.
      const ordered = rows.filter(row => row && row.idea_ticket)
        .concat(rows.filter(row => !row || !row.idea_ticket));
      const idOf = row => String(row && (row.session_id || row.id) || '');
      const byId = new Map(rows.filter(Boolean).map(row => [idOf(row), row]));
      const saved = new Set();
      const stringFields = ['id', 'session_id', 'folder_path', 'session_cwd',
        'first_message', 'display_name', 'ai_title'];
      const clipped = new Set(['first_message', 'display_name', 'ai_title']);
      for (const row of ordered) {
        if (entries.length >= sharedState.ARCHIVE_SIDEBAR_CACHE_MAX_ROWS) break;
        const firstId = idOf(row);
        if (!firstId || saved.has(firstId)) continue;
        // Once the preview is nearly full, most remaining rows cannot fit.
        // These required strings alone bound their JSON size from below;
        // escaping, other fields and ancestors can only increase it. Skip
        // impossible rows before projecting/serializing their whole family,
        // while still considering later smaller rows for the remaining space.
        let minimum = 2; // braces; omitted commas keep this conservative
        const remaining = sharedState.ARCHIVE_SIDEBAR_CACHE_MAX_CHARS - chars;
        for (const key of stringFields) {
          const value = row[key];
          if (typeof value === 'string') {
            minimum += key.length + 5 + (clipped.has(key)
              ? Math.min(400, value.length) : value.length);
          }
        }
        if (minimum + 1 > remaining) continue;
        // Admit a child with its loaded ancestors as one bounded batch. A
        // busy fleet must not evict the parent and leave an unexplained lane.
        const batch = [], batchSeen = new Set();
        let candidate = row;
        while (candidate) {
          const cid = idOf(candidate);
          if (!cid || saved.has(cid) || batchSeen.has(cid)) break;
          batchSeen.add(cid);
          const projected = _archiveSidebarCacheRow(candidate);
          if (!projected) break;
          batch.push({ id: cid, text: JSON.stringify(projected) });
          candidate = byId.get(candidate.parent_session_id || candidate.hermes_parent_session_id
            || candidate.continued_from_session_id);
        }
        const cost = batch.reduce((n, item) => n + item.text.length + 1, 0);
        if (entries.length + batch.length > sharedState.ARCHIVE_SIDEBAR_CACHE_MAX_ROWS
            || chars + cost > sharedState.ARCHIVE_SIDEBAR_CACHE_MAX_CHARS) continue;
        for (const item of batch) { entries.push(item.text); saved.add(item.id); }
        chars += cost;
      }
      const envelope = JSON.stringify({ version: 1, window: windowName, savedAt: Date.now() });
      localStorage.removeItem(sharedState.ARCHIVE_SIDEBAR_CACHE_KEY);  // pre-per-window snapshot
      localStorage.setItem(_archiveSidebarCacheKey(windowName),
        envelope.slice(0, -1) + ',"rows":[' + entries.join(',') + ']}');
    } catch (_) { /* Storage disabled/full: the network list still works. */ }
  }

  function _scheduleArchiveSidebarCache(rows, windowName) {
    sharedState._archiveSidebarCachePending = { rows, windowName };
    if (sharedState._archiveSidebarCacheScheduled) return;
    sharedState._archiveSidebarCacheScheduled = true;
    const arm = () => {
      if (window.requestIdleCallback) window.requestIdleCallback(save, { timeout: 2000 });
      else setTimeout(save, 0);
    };
    const save = () => {
      const ae = typeof document !== 'undefined' && document && document.activeElement;
      const typingInComposer = !!ae && (ae.tagName === 'TEXTAREA'
        || (ae.tagName === 'INPUT' && ae.id !== 'convSearch' && /^(text|search|email|url|tel|password)$/i.test(ae.type || 'text')))
        && typeof sharedState._lastTextKeyTs === 'number'
        && Date.now() - sharedState._lastTextKeyTs < 1500;
      if (typingInComposer) {
        if (typeof setTimeout === 'function') {
          setTimeout(arm, Math.max(120, 1520 - (Date.now() - sharedState._lastTextKeyTs)));
        } else {
          arm();
        }
        return;
      }
      sharedState._archiveSidebarCacheScheduled = false;
      const pending = sharedState._archiveSidebarCachePending;
      sharedState._archiveSidebarCachePending = null;
      if (pending && pending.windowName === _archiveWindow()) {
        _writeArchiveSidebarCache(pending.rows, pending.windowName);
      }
    };
    arm();
  }

  function _mergeArchiveListDelta(prev, d, savedOrder) {
    if (!Array.isArray(prev) || !Array.isArray(d.ids) || !Array.isArray(d.conversations)) return null;
    const sameOrder = d.same_order === true;
    if (sameOrder && (!savedOrder || d.order_token !== savedOrder || d.count !== prev.length)) return null;
    const byId = new Map();
    for (const row of prev) {
      const id = row && (row.session_id || row.id);
      if (id) byId.set(String(id), row);
    }
    if (sameOrder && byId.size !== prev.length) return null;
    for (const row of d.conversations) {
      const id = row && (row.session_id || row.id);
      if (!id || (sameOrder && !byId.has(String(id)))) return null;
      byId.set(String(id), row);
    }
    if (sameOrder) return [...byId.values()];
    if (Object.hasOwn(d, 'order_spans')) {
      // Offsets only describe the exact pre-delta array validated by the server.
      if (d.ids.length || !savedOrder || d.base_order !== savedOrder
          || !Array.isArray(d.order_spans) || d.order_spans.length > 256
          || !Number.isSafeInteger(d.count) || d.count < 0
          || d.count > prev.length + d.conversations.length) return null;
      const rows = [], seen = new Set();
      const append = id => {
        if (!byId.has(id) || seen.has(id) || rows.length >= d.count) return false;
        seen.add(id);
        rows.push(byId.get(id));
        return true;
      };
      for (const span of d.order_spans) {
        if (typeof span === 'string') {
          if (!append(span)) return null;
          continue;
        }
        if (!Array.isArray(span) || span.length !== 2) return null;
        const [start, count] = span;
        if (!Number.isSafeInteger(start) || !Number.isSafeInteger(count)
            || start < 0 || count < 1 || start + count > prev.length) return null;
        for (let i = start; i < start + count; i++) {
          const row = prev[i];
          if (!row || !append(String(row.session_id || row.id || ''))) return null;
        }
      }
      return rows.length === d.count ? rows : null;
    }
    const out = new Array(d.ids.length);
    for (let i = 0; i < d.ids.length; i++) {
      const row = byId.get(String(d.ids[i]));
      if (!row) return null;
      out[i] = row;
    }
    return out;
  }

  async function loadArchiveAll(opts = {}) {
    const params = new URLSearchParams();
    params.set('window', opts.window || _archiveWindow());
    if (opts.staleOk !== false) {
      params.set('stale_ok', '1');
    }
    if (opts.includePrs) {
      params.set('include_prs', '1');
      params.set('resolve_prs', '1');
      params.set('resolve_effective', '1');
      params.set('resolve_worktrees', '1');
      params.set('background', '1');
    }
    if (opts.initial) params.set('initial', '1');
    const url = '/api/conversations/list' + (params.toString() ? '?' + params.toString() : '');
    const requestUrl = !opts.initial && !opts.includePrs && params.get('window') === 'all'
      ? url + '&row_layouts=1&order_spans=1' : url;
    const pending = sharedState._archiveAllInflight.get(url);
    if (pending) return pending;
    let _perfT0 = performance.now();
    // Stage split for the list_read tail (CCC-229): headers arrive after
    // client queueing + server work; body covers download + JSON parse,
    // which stretches when a fresh page's main thread is busy booting.
    let _perfHdrAt = 0;
    let _perfBodyMs = 0;
    let _perfPaintWaitMs = 0;
    // Server list handler time (Server-Timing list;dur) and the fetched URL
    // (its ?since= varies) for the Resource Timing split (CCC-229).
    let _perfServer = null;
    let _perfFetchUrl = url;
    const decodeRows = payload => {
      if (!payload || !Object.hasOwn(payload, 'row_layouts')) return payload;
      const {row_layouts: layouts, conversations, ...metadata} = payload;
      if (!Array.isArray(layouts) || !Array.isArray(conversations)
          || layouts.some(keys => !Array.isArray(keys) || keys.some(key => typeof key !== 'string' || key === '__proto__'))) {
        throw new Error('Invalid archive row layouts');
      }
      const rows = conversations.map(encoded => {
        const keys = Array.isArray(encoded) && Number.isInteger(encoded[0]) && layouts[encoded[0]];
        if (!keys || !Array.isArray(encoded[1]) || keys.length !== encoded[1].length) {
          throw new Error('Invalid archive row');
        }
        const row = {};
        for (let i = 0; i < keys.length; i++) row[keys[i]] = encoded[1][i];
        return row;
      });
      return {...metadata, conversations:rows};
    };
    const _perfListLog = function (bytes, status, extra) {
      try {
        if (typeof window.__cccPerfRow !== 'function') return;
        // Subtract hidden/suspended time like conv_open/archive_load: a
        // background tab's throttled fetch otherwise reads as a multi-minute
        // "slow list" (CCC-142). Wall time still ships as detail.wall_ms.
        const _perfNow = performance.now();
        const stoppedAt = opts.controller?._cccArchiveSupersededAt;
        // Aborting a redundant preview ends its read immediately. Its promise
        // rejection can run only after the complete archive's synchronous
        // render; retain that later handler delay separately.
        const _perfEnd = status === 'superseded' && Number.isFinite(stoppedAt)
          ? Math.min(_perfNow, Math.max(_perfT0, stoppedAt)) : _perfNow;
        const wallMs = Math.round(_perfEnd - _perfT0);
        const ms = Math.round(_perfActiveElapsed(_perfT0, _perfEnd));
        const size = bytes ? (bytes >= 1048576 ? (bytes / 1048576).toFixed(1) + 'MB'
          : Math.round(bytes / 1024) + 'KB')
          : (status === 304 ? '304'
            : (typeof status === 'number' ? 'HTTP' + status : String(status)));
        window.__cccPerfRow('list', {
          at: Date.now(),
          text: ms + 'ms ' + size + (extra ? ' ' + extra : ''),
          title: url + ' → ' + status,
          ms: ms,
          status: status,
        });
        // Structured beacon too — the perf watchdog only sees /api/perf-event
        // samples, so list-read latency feeds the same breach evaluation and
        // (maintainer-box) self-filing as conv_open/archive_load.
        if (typeof _perfEvent === 'function') {
          _perfEvent('list_read', ms, {
            detail: {
              bytes: bytes || 0, status: status, extra: extra || '',
              wall_ms: wallMs, hidden_ms: wallMs - ms,
              settle_delay_ms: Math.round(_perfNow - _perfEnd),
              ttfb_ms: _perfHdrAt ? Math.round(_perfHdrAt - _perfT0) : null,
              body_ms: _perfBodyMs ? Math.round(_perfBodyMs) : null,
              paint_wait_ms: Math.round(_perfPaintWaitMs),
              start_nav_ms: Math.round(_perfT0),
              server_ms: _perfServer ? _perfServer.ms : null,
              server_cpu_ms: _perfServer ? _perfServer.cpu : null,
              longtask_ms: _perfLongTaskMs(_perfT0, _perfEnd),
              rt: _perfHdrAt ? _convResourceTiming(_perfFetchUrl, _perfT0) : null,
            },
          });
        }
      } catch (_) {}
    };
    const p = (async () => {
      try {
        if (!opts.initial && !opts.includePrs && !sharedState._archiveAllData.has(url)) {
          const snapshot = await window.__cccArchiveSnapshot?.read;
          if (snapshot?.url === url && !sharedState._archiveAllData.has(url)) {
            sharedState._archiveAllData.set(url, snapshot.rows);
            if (snapshot.etag) sharedState._archiveAllEtag.set(url, snapshot.etag);
            if (snapshot.delta) sharedState._archiveAllDelta.set(url, {
              rows: snapshot.rows, token: snapshot.delta, order: snapshot.order,
            });
          }
        }
        const prevEtag = sharedState._archiveAllEtag.get(url);
        const requestBase = sharedState._archiveAllData.get(url);
        const deltaBase = sharedState._archiveAllDelta.get(url);
        // The token and order proof describe this particular array of rows.
        const deltaToken = Array.isArray(requestBase) && deltaBase?.rows === requestBase ? deltaBase.token : null;
        const baseOrder = deltaToken && /^[0-9a-f]{64}$/.test(deltaBase.order) ? deltaBase.order : '';
        // Hard timeout: without one, a wedged request (accepted connection,
        // response never arrives) hangs this promise forever — and every
        // caller dedups onto it (_archiveAllInflight, _archiveRefreshPromise),
        // so the sidebar stays on "Archive loaded" with no path to recovery.
        const controller = opts.controller || new AbortController();
        const timeoutId = setTimeout(() => {
          try { controller.abort(); } catch (_) {}
        }, sharedState.ARCHIVE_ALL_FETCH_TIMEOUT_MS);
        let r, requestedOrder = '';
        try {
          const init = { signal: controller.signal };
          if (prevEtag) init.headers = { 'If-None-Match': prevEtag };
          if (deltaToken && !opts.initial && !opts.includePrs && opts.staleOk !== false
              && params.get('window') === 'all') {
            (init.headers ||= {})['X-CCC-Abort-On-Disconnect'] = '1';
          }
          if (!sharedState._archiveBootstrapConsumed && url === sharedState._archiveBootstrapUrl && !prevEtag) {
            sharedState._archiveBootstrapConsumed = true;
            const snapshot = window.__cccArchiveSnapshot;
            if (snapshot?.url + '&initial=1' === url && snapshot.cancelRecent) {
              // A validated persisted base can hold the boot preview. Ask
              // for it as soon as the active view has no saved rows to show.
              if (!opts.useSavedPreview) snapshot.startRecent?.();
              if (Number.isFinite(snapshot.recentStart)) _perfT0 = Math.min(_perfT0, snapshot.recentStart);
              if (controller.signal.aborted) snapshot.cancelRecent();
              else controller.signal.addEventListener('abort', () => snapshot.cancelRecent(), {once:true});
            }
            r = await sharedState._archiveBootstrapFetchPromise;
            if (controller.signal.aborted) throw controller.signal.reason;
          }
          if (!r && window.__cccArchiveSnapshot && !window.__cccArchiveSnapshot.consumed && url === window.__cccArchiveSnapshot.url) {
            window.__cccArchiveSnapshot.consumed = true;
            r = await window.__cccArchiveSnapshot.fetch;
            if (r) {
              _perfFetchUrl = r.url || url;
              // Only boot can attest that an early response used this saved order.
              requestedOrder = r._cccArchiveSavedOrder === baseOrder ? baseOrder : '';
            }
          }
          // No preview request was sent when the early complete response won.
          // The normal full loader still validates and applies that response.
          if (!r && opts.initial && window.__cccArchiveSnapshot?.skippedRecent
              && url === sharedState._archiveBootstrapUrl) return null;
          if (!r) {
            _perfFetchUrl = deltaToken ? requestUrl + '&since=' + encodeURIComponent(deltaToken) : requestUrl;
            requestedOrder = baseOrder;
            if (requestedOrder) _perfFetchUrl += '&saved_order=' + requestedOrder;
            r = await backgroundApiFetch(_perfFetchUrl, init);
          }
          if (controller.signal.aborted) throw controller.signal.reason;
        } finally {
          clearTimeout(timeoutId);
        }
        // Include the early read's full lifetime instead of reporting only
        // the time the initialized application spent awaiting its response.
        const earlyTiming = r._cccArchiveTiming;
        if (earlyTiming && Number.isFinite(earlyTiming.start)) {
          _perfT0 = Math.min(_perfT0, earlyTiming.start);
        }
        _perfHdrAt = earlyTiming && Number.isFinite(earlyTiming.headers)
          ? earlyTiming.headers : performance.now();
        try {
          const st = r.headers.get('Server-Timing') || '';
          const m = st.match(/(?:^|,)\s*list;dur=([0-9.]+)/), c = st.match(/listcpu;dur=([0-9.]+)/);
          if (m) _perfServer = { ms: Math.round(+m[1]), cpu: c ? Math.round(+c[1]) : null };
        } catch (_) {}
        sharedState._lastArchiveListHttpStatus = r.status;
        // 304 → the server confirms our cached payload is still current, so we
        // skip re-parsing ~500KB and reuse the last conversations as-is. The
        // caller re-renders, but the flicker guard skips the rebuild since the
        // structural signature is unchanged.
        if (r.status === 304) {
          _perfListLog(0, 304);
          const cached = sharedState._archiveAllData.get(url);
          if (!Array.isArray(cached)) {
            _clientLog('[ARCHIVE-DIAG] conversations/list 304 with no cached body');
            return null;
          }
          // Archive body unchanged, but live tool/WIP chips still move —
          // refresh the cheap overlay and let the next render merge it in.
          if (typeof refreshLiveSessionsActivity === 'function') {
            refreshLiveSessionsActivity();
          }
          if (typeof opts.onValidated === 'function') opts.onValidated();
          return cached.slice();
        }
        if (!r.ok) {
          _perfListLog(0, r.status, 'err');
          _clientLog('[ARCHIVE-DIAG] conversations/list not-ok: HTTP ' + r.status + ' ' + url.split('?')[1]);
          return null;
        }
        let etag = r.headers.get('ETag');
        let _perfLen = parseInt(r.headers.get('content-length') || '0', 10) || 0;
        // Ordinary history parsing waits for recent rows to paint. The early
        // conditional body is already draining and can validate first.
        if (opts.afterPaint && !r._cccArchiveParsedBody) {
          const waitStart = performance.now();
          await opts.afterPaint;
          _perfPaintWaitMs = performance.now() - waitStart;
        }
        let raw = await (r._cccArchiveParsedBody || r.json());
        let d = decodeRows(raw);
        _perfBodyMs = performance.now() - _perfHdrAt - _perfPaintWaitMs;
        let _merged = null;
        if (d && d.delta) {
          const baseRows = sharedState._archiveAllData.get(url);
          const savedOrder = baseRows === requestBase && d.since === deltaToken ? requestedOrder : null;
          _merged = _mergeArchiveListDelta(baseRows, d, savedOrder);
          if (!_merged) {
            // Ids we never had: resync with one full body.
            _clientLog('[ARCHIVE-DIAG] conversations/list delta unmergeable; full refetch');
            // A skipped saved preview is useful again while a full resync
            // runs, including transport retries before a failure is final.
            if (window.__cccArchiveSnapshot?.url === url) window.__cccArchiveSnapshot.onResync?.();
            sharedState._archiveAllDelta.delete(url);
            sharedState._archiveAllEtag.delete(url);
            const r2 = await backgroundApiFetch(requestUrl, { signal: controller.signal });
            if (!r2.ok) {
              _perfListLog(0, r2.status, 'err');
              return null;
            }
            etag = r2.headers.get('ETag');
            _perfLen = parseInt(r2.headers.get('content-length') || '0', 10) || 0;
            raw = await r2.json();
            d = decodeRows(raw);
          }
        }
        try {
          if (d && d.queue_health && typeof window.__cccPerfQueueHealth === 'function') {
            window.__cccPerfQueueHealth(d.queue_health);
          }
        } catch (_) {}
        if (!window.__cccPerfFirstListAt && d && Array.isArray(d.conversations)) {
          window.__cccPerfFirstListAt = true;
          window.__cccPerfRow && window.__cccPerfRow('list', {
            at: Date.now(),
            text: 'first-list ' + Math.round(performance.now()) + 'ms',
            ms: Math.round(performance.now()),
            firstList: true,
            title: 'page load → first conversation list response parsed',
          });
        }
        _perfListLog(_perfLen, r.status, d && Array.isArray(d.conversations)
          ? (_merged ? 'delta ' + d.conversations.length + '/' + _merged.length : d.conversations.length) + 'rows'
            + ((d.queue_health && d.queue_health.stale_queued_items)
              ? ' stale-steer:' + d.queue_health.stale_queued_items : '')
          : '');
        if (d && d.cached && d.stale && d.refreshing) {
          _scheduleArchiveStaleRetry();
        }
        if (!d || !Array.isArray(d.conversations)) {
          _clientLog('[ARCHIVE-DIAG] conversations/list missing conversations array');
          return null;
        }
        const convs = _merged || d.conversations;
        _clientLog('[ARCHIVE-DIAG] conversations/list ok: ' + convs.length + ' rows' + (_merged ? ' (delta)' : ''));
        // Cache the ETag only after the body parsed and landed — otherwise a
        // truncated/aborted download leaves an ETag whose next 304 replays
        // an empty list.
        sharedState._archiveAllData.set(url, convs);
        // A merged list matches no full body's ETag; the delta token alone
        // validates it from here on.
        if (_merged) sharedState._archiveAllEtag.delete(url);
        else if (etag) sharedState._archiveAllEtag.set(url, etag);
        if (d.delta_token) sharedState._archiveAllDelta.set(url, {
          rows: convs, token: d.delta_token, order: d.order_token,
        });
        else sharedState._archiveAllDelta.delete(url);
        if (!opts.includePrs && !opts.initial) {
          _scheduleArchiveSidebarCache(convs, params.get('window'));
          window.__cccArchiveSnapshot?.save({url, rows:convs,
            etag:sharedState._archiveAllEtag.get(url), delta:d.delta_token,
            order:d.order_token},
            _merged ? {from: deltaToken,
              ids:d.same_order ? null : Object.hasOwn(d, 'order_spans')
                ? _merged.map(row => String(row.session_id || row.id)) : d.ids,
              changes: d.conversations} : null,
            !_merged && !d.delta && raw.row_layouts ? [raw.row_layouts, raw.conversations] : null);
        }
        if (typeof opts.onValidated === 'function') opts.onValidated();
        if (typeof opts.onPage === 'function') opts.onPage(d);
        return convs;
      } catch (e) {
        if (opts.initial && opts.controller?.signal.reason === 'archive-complete') {
          _perfListLog(0, 'superseded');
          return null;
        }
        _perfListLog(0, 'threw');
        _clientLog('[ARCHIVE-DIAG] conversations/list threw: ' + ((e && e.name) || '?') + ' ' + String((e && e.message) || '').slice(0, 120));
        return null;
      }
    })();
    sharedState._archiveAllInflight.set(url, p);
    p.finally(() => sharedState._archiveAllInflight.delete(url));
    return p;
  }

  function _renderArchiveLoadingStages(snapshot) {
    const $list = document.getElementById('convList');
    if (!$list) return;
    // Don't overwrite a populated list — only swap when we're still in
    // the placeholder state. Once renderArchiveList() runs with real
    // data, this poll's job is done.
    const isPlaceholder = $list.querySelector('.archive-loading-placeholder, .archive-loading-stages');
    if (!isPlaceholder) return;
    const steps = (snapshot && snapshot.steps) || [];
    if (!steps.length) return;
    const glyphFor = (state) => {
      if (state === 'done')    return '<span class="als-glyph als-done">✓</span>';
      if (state === 'running') return '<span class="als-glyph als-running">●</span>';
      if (state === 'error')   return '<span class="als-glyph als-error">!</span>';
      if (state === 'skipped') return '<span class="als-glyph als-skipped">-</span>';
      return '<span class="als-glyph als-pending">○</span>';
    };
    const escAls = (s) => String(s || '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    let html = '<div class="archive-loading-stages">';
    html += '<div class="als-title">' + escAls(snapshot.title || 'Loading archive…') + '</div>';
    for (const s of steps) {
      const det = s.detail || '';
      const count = (typeof s.count === 'number' && typeof s.total === 'number')
        ? ' <span class="als-count">' + s.count + ' / ' + s.total + '</span>'
        : '';
      html += '<div class="als-row als-state-' + escAls(s.state || 'pending') + '">'
        +    glyphFor(s.state)
        +    '<span class="als-label">' + escAls(s.label) + '</span>'
        +    count
        +    (det ? '<div class="als-detail">' + escAls(det) + '</div>' : '')
        +    '</div>';
    }
    html += '</div>';
    $list.innerHTML = html;
  }

  function _archiveListStillShowsLoader() {
    const $list = document.getElementById('convList');
    return !!($list && $list.querySelector('.archive-loading-placeholder, .archive-loading-stages'));
  }

  function _scheduleArchiveStuckRetry() {
    if (sharedState._archiveStuckRetryId) return;
    if (sharedState._archiveStuckRetryCount >= sharedState.ARCHIVE_STUCK_RETRY_MAX) return;
    sharedState._archiveStuckRetryCount++;
    sharedState._archiveStuckRetryId = setTimeout(() => {
      sharedState._archiveStuckRetryId = null;
      _recoverArchiveRenderIfStuck();
    }, sharedState.ARCHIVE_STUCK_RETRY_MS);
  }

  function _archiveListFetchInflight(win) {
    if (!sharedState._archiveBootstrapConsumed && sharedState._archiveBootstrapWindow === win) return sharedState._archiveBootstrapFetchPromise;
    for (const [url, p] of sharedState._archiveAllInflight) {
      if (new URLSearchParams(url.split('?')[1] || '').get('window') === win) return p;
    }
    return null;
  }

  function _recoverArchiveRenderIfStuck() {
    // The loader is expected under an open app; closing the app paints it.
    if (_dashboardCoveredByApp()) return Promise.resolve();
    if (!_archiveListStillShowsLoader()) {
      sharedState._archiveStuckRetryCount = 0;
      return Promise.resolve();
    }
    if (sharedState.archiveLoaded && Array.isArray(sharedState.archiveData) && sharedState.archiveData.length) {
      sharedState._archiveStuckRetryCount = 0;
      // force: the pause-sidebar guard (typing recency, find bar, a restored
      // '__new__'/spawning conversation state) defers renders indefinitely,
      // which is precisely how a fully-loaded archive stays on the placeholder
      // forever. Recovery is not a periodic re-render; it must paint.
      (0, hostServices.renderArchiveList)(_archiveQuery(), { force: true });
      sharedState._archiveRecoveryRendered = { data: sharedState.archiveData, query: _archiveQuery().trim().toLowerCase() };
      return Promise.resolve();
    }
    _clientLog('[ARCHIVE-DIAG] recoverIfStuck: loader showing, archiveLoaded=' + sharedState.archiveLoaded
      + ' rows=' + (Array.isArray(sharedState.archiveData) ? sharedState.archiveData.length : -1)
      + ' retry=' + sharedState._archiveStuckRetryCount
      + ' pause=' + (typeof shouldPauseSidebarRender === 'function' ? shouldPauseSidebarRender() : '?')
      + ' conv=' + String(window.currentConversation || '')
      + ' hidden=' + document.hidden);
    if (sharedState._archiveStuckRenderRecoveryPromise) return sharedState._archiveStuckRenderRecoveryPromise;
    const currentWin = _archiveWindow();
    // The loader is still up because the first fetch for this window has not
    // landed yet (a 4k-row 'all' payload takes seconds to parse under load),
    // not because it is wedged. Wait on that fetch: demoting to 7d here fired
    // a second list request + full re-render on every 'all' boot (CCC-118).
    const inflightWin = _archiveListFetchInflight(currentWin);
    if (inflightWin) {
      sharedState._archiveStuckRenderRecoveryPromise = inflightWin.catch(() => null).finally(() => {
        sharedState._archiveStuckRenderRecoveryPromise = null;
        if (_archiveListStillShowsLoader()) _scheduleArchiveStuckRetry();
      });
      return sharedState._archiveStuckRenderRecoveryPromise;
    }
    const targetWin = (currentWin === 'all') ? '7d' : currentWin;
    if (currentWin === 'all') {
      try { localStorage.setItem(sharedState.ARCHIVE_WINDOW_KEY, '7d'); } catch (_) {}
    }
    sharedState._archiveStuckRenderRecoveryPromise = loadArchiveAll({ staleOk: true, window: targetWin }).then(convs => {
      if (!Array.isArray(convs) || !convs.length || !_archiveListStillShowsLoader()) return;
      sharedState.archiveData = _mergeArchivePrSnapshot(convs, sharedState.archiveData);
      sharedState.archiveDataWindow = targetWin;
      sharedState.archiveLoaded = true;
      sharedState._archiveStuckRetryCount = 0;
      (0, hostServices.renderArchiveList)(_archiveQuery(), { force: true });
      sharedState._archiveRecoveryRendered = { data: sharedState.archiveData, query: _archiveQuery().trim().toLowerCase() };
    }).finally(() => {
      sharedState._archiveStuckRenderRecoveryPromise = null;
      // One-shot recovery used to give up here: if the fetch failed (or, before
      // the loadArchiveAll timeout, hung forever) the sidebar stayed on the
      // "Archive loaded" checklist permanently. Retry on a slow cadence while
      // the loader is still on screen.
      if (_archiveListStillShowsLoader()
          && !(sharedState.archiveLoaded && Array.isArray(sharedState.archiveData) && sharedState.archiveData.length)) {
        _scheduleArchiveStuckRetry();
      }
    });
    return sharedState._archiveStuckRenderRecoveryPromise;
  }

  function _scheduleArchiveStaleRetry() {
    if (sharedState._archiveStaleRetryId) return;
    sharedState._archiveStaleRetryId = setTimeout(() => {
      sharedState._archiveStaleRetryId = null;
      if (document.hidden || sharedState.activeTab !== 'sessions' || sharedState.conversationPaneLoading) return;
      refreshArchiveData({ staleOk: true })
        .then(() => (0, hostServices.renderArchiveList)(_archiveQuery()))
        .catch(() => {});
    }, sharedState.ARCHIVE_STALE_RETRY_MS);
  }

  function _hydrateArchiveSideData(force = false) {
    if (sharedState._archiveSideDataPromise) return sharedState._archiveSideDataPromise;
    if (!force && sharedState._archiveSideDataHydratedAt && (Date.now() - sharedState._archiveSideDataHydratedAt) < sharedState.ARCHIVE_HYDRATE_TTL_MS) {
      return Promise.resolve();
    }
    sharedState._archiveSideDataPromise = Promise.all([
      loadCrossRepoIssues(),
      refreshArchivedGroupChats().catch(() => {}),
      sharedState.repoListState.repos.length ? Promise.resolve(null) : loadRepoList().catch(() => null),
      refreshUxFixesQueueMeta({ force }).catch(() => null),
    ]).then(([issues]) => {
      sharedState.crossRepoIssuesData = issues || [];
      sharedState._archiveSideDataHydratedAt = Date.now();
      _renderArchiveIfLoaded();
    }).finally(() => {
      sharedState._archiveSideDataPromise = null;
    });
    return sharedState._archiveSideDataPromise;
  }

  function _startArchiveProgressPoll() {
    if (sharedState._archiveProgressPollId) return;
    const tick = async () => {
      try {
        const r = await backgroundApiFetch('/api/archive/loading-status');
        if (!r.ok) {
          _clientLog('[ARCHIVE-DIAG] loading-status not-ok: HTTP ' + r.status);
          return;
        }
        const snap = await r.json();
        _renderArchiveLoadingStages(snap);
        if (!snap.active) {
          _stopArchiveProgressPoll();
          _recoverArchiveRenderIfStuck();
        }
      } catch (e) {
        _clientLog('[ARCHIVE-DIAG] loading-status fetch threw: ' + ((e && e.name) || '?') + ' ' + ((e && e.message) || ''));
      }
    };
    tick();  // immediate first paint
    sharedState._archiveProgressPollId = setInterval(_gated('archiveProgress', tick), 250);
  }

  function _stopArchiveProgressPoll() {
    if (sharedState._archiveProgressPollId) {
      clearInterval(sharedState._archiveProgressPollId);
      sharedState._archiveProgressPollId = null;
    }
  }

  function _setArchiveCompleteness(win, complete, count, failed = false, ready = false) {
    window.__cccArchiveProgress = { window: win, complete, count, failed, ready: ready || complete };
    let notice = document.getElementById('archiveCompleteness');
    if (!notice) {
      notice = document.createElement('div');
      notice.id = 'archiveCompleteness';
      notice.setAttribute('role', 'status');
      notice.style.cssText = 'padding:8px 12px;font-size:12px;color:var(--text-secondary);background:var(--surface-2);flex-shrink:0';
      document.getElementById('convList')?.before(notice);
    }
    notice.hidden = complete;
    notice.textContent = complete ? '' : (failed
      ? 'Full archive could not load. Search and counts are incomplete; retrying on refresh.'
      : 'Loading full archive… Search and counts are incomplete (' + count + ' sessions loaded).');
  }

  async function refreshArchiveData(opts = {}) {
    if (sharedState._archiveRefreshPromise) {
      _clientLog('[ARCHIVE-DIAG] refreshArchiveData deduped onto in-flight promise');
      return sharedState._archiveRefreshPromise;
    }
    sharedState._archiveRefreshPromise = (async () => {
      await _whenDashboardUncovered();
      _startArchiveProgressPoll();
      _clientLog('[ARCHIVE-DIAG] refreshArchiveData start window=' + (opts.window || _archiveWindow()) + ' staleOk=' + (opts.staleOk !== false && !opts.force));
      let archiveSettled = false;
      let fullRead = null;
      try {
      // While a sidebar search is active the corpus must stay at the 'all'
      // window: renderArchiveList widens to 'all' so every session is
      // searchable, and a background refresh that re-fetches with the stored
      // ('1d'/'7d') window would swap the data back under the user's feet —
      // the visible "result count flips between small and full" flicker.
      const requestedWindow = opts.window || (_archiveQuery().trim() ? 'all' : _archiveWindow());
      const isCurrentWindow = () => requestedWindow === (_archiveQuery().trim() ? 'all' : _archiveWindow());
      const progress = window.__cccArchiveProgress;
      const fillingArchive = !opts.includePrs && (!progress || progress.window !== requestedWindow || !progress.complete);
      if (fillingArchive) {
        _setArchiveCompleteness(requestedWindow, false, sharedState.archiveData.length);
        if (sharedState._archiveFromLocalCache) {
          // A ready full response can replace the preview in this microtask
          // turn. Paint cached rows and their incomplete-search notice first.
          document.getElementById('cccLoadingOverlay')?.classList.add('gone');
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(resolve, 0))));
        }
        let validatedReady;
        const validatedFull = new Promise(resolve => { validatedReady = resolve; });
        let releaseInitialPaint;
        const afterPaint = new Promise(resolve => { releaseInitialPaint = resolve; });
        const initialController = new AbortController();
        const useSavedPreview = sharedState._archiveFromLocalCache && requestedWindow === _archiveWindow()
          && !_archiveQuery().trim() && opts.staleOk !== false && !opts.force;
        const initialRead = loadArchiveAll({ staleOk: true, window: requestedWindow,
          initial: true, controller: initialController, useSavedPreview });
        // Start the full download while the recent-row request is pending.
        // Ordinary response parsing waits for recent rows to paint; the early
        // conditional response can already be parsed while the app loads.
        fullRead = loadArchiveAll({ staleOk: opts.staleOk !== false && !opts.force,
          window: requestedWindow, afterPaint, onValidated: () => { validatedReady({validated:true}); } });
        try {
          // A validated complete response can be ready before the recent-row
          // request. Its rows already include the preview and all ancestors.
          const result = await Promise.race([initialRead.then(first => ({first})), validatedFull]);
          if (result.validated) {
            initialController._cccArchiveSupersededAt = performance.now();
            initialController.abort('archive-complete');
          }
          const first = result.first;
          if (Array.isArray(first) && isCurrentWindow()) {
            sharedState.archiveData = _applyPendingDashboardConversationPatches(
              _mergeArchivePrSnapshot(first, sharedState.archiveData));
            sharedState.archiveDataWindow = requestedWindow;
            sharedState.archiveLoaded = true;
            _setArchiveCompleteness(requestedWindow, false, first.length, false, true);
            (0, hostServices.renderArchiveList)(_archiveQuery(), { force: true, skipReorderAnimation: true });
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          }
        } finally {
          releaseInitialPaint();
        }
      }
      if (!isCurrentWindow()) {
        sharedState._archiveRefreshAfterInflight = true;
        return sharedState.archiveData;
      }
      const convs = await (fullRead || loadArchiveAll({ staleOk: opts.staleOk !== false && !opts.force, window: requestedWindow }));
      if (!isCurrentWindow()) {
        sharedState._archiveRefreshAfterInflight = true;
        return sharedState.archiveData;
      }
      archiveSettled = true;
      if (!Array.isArray(convs)) {
        _setArchiveCompleteness(requestedWindow, false, sharedState.archiveData.length, true);
        _scheduleArchiveStaleRetry();
        _clientLog('[ARCHIVE-DIAG] refreshArchiveData keeping previous rows='
          + (Array.isArray(sharedState.archiveData) ? sharedState.archiveData.length : -1)
          + ' (fetch failed)');
        return sharedState.archiveData;
      }
      if (!convs.length && Array.isArray(sharedState.archiveData) && sharedState.archiveData.length) {
        if (!sharedState._archiveFromLocalCache) {
          _clientLog('[ARCHIVE-DIAG] refreshArchiveData keeping previous rows='
            + sharedState.archiveData.length + ' (empty payload)');
          return sharedState.archiveData;
        }
      }
      sharedState._archiveFromLocalCache = false;
      _setArchiveCompleteness(requestedWindow, true, convs.length);
      sharedState.archiveData = _applyPendingDashboardConversationPatches(
        _mergeArchivePrSnapshot(convs, sharedState.archiveData)
      );
      _mergeIntoMasterRows(sharedState.archiveData);
      if (opts.force || requestedWindow !== sharedState.archiveDataWindow) _resetArchiveUnknownPatchIds();
      sharedState.archiveDataWindow = requestedWindow;
        sharedState.archiveLoaded = true;
        _clientLog('[ARCHIVE-DIAG] refreshArchiveData merged ' + (Array.isArray(sharedState.archiveData) ? sharedState.archiveData.length : -1) + ' rows, archiveLoaded=true');
        // Filling in an incomplete list changes its membership and grouping.
        // Keep scroll anchoring without measuring every row for a reorder.
        (0, hostServices.renderArchiveList)(_archiveQuery(), { force: true, skipReorderAnimation: fillingArchive });
        // Base rows are useful immediately. Let the live/approval overlay land
        // afterward instead of holding first paint behind a contended status
        // read; the unified session watcher also pushes those state changes.
        refreshLiveSessionsActivity().then(() => {
          _queueDashboardArchiveRender();
          if (typeof window.__cccRenderThroughputActivity === 'function') {
            window.__cccRenderThroughputActivity();
          }
        }).catch(() => {});
        if (typeof window.__cccRenderThroughputActivity === 'function') {
          window.__cccRenderThroughputActivity();
        }
        setTimeout(() => {
          _hydrateArchiveSideData();
        }, 0);
        return sharedState.archiveData;
      } finally {
        sharedState._archiveRefreshPromise = null;
        _stopArchiveProgressPoll();
        _markArchiveFirstLoaded();
        if (archiveSettled) {
          _releaseStartupApiReads();
          window.__cccArchiveSettled = true;
          window.dispatchEvent?.(new Event('ccc:archive-settled'));
        }
        if (sharedState._archiveRefreshAfterInflight) {
          sharedState._archiveRefreshAfterInflight = false;
          queueMicrotask(() => {
            refreshArchiveData({ staleOk: true })
              .then(_queueDashboardArchiveRender)
              .catch(() => {});
          });
        }
      }
    })();
    return sharedState._archiveRefreshPromise;
  }

    return {
      _markArchiveFirstLoaded, _applyPendingDashboardConversationPatches, _resetArchiveUnknownPatchIds, _mergeArchivePrSnapshot, _archiveSidebarCacheKey, _archiveSidebarCacheRow,
      _writeArchiveSidebarCache, _scheduleArchiveSidebarCache, _mergeArchiveListDelta, loadArchiveAll, _renderArchiveLoadingStages, _archiveListStillShowsLoader,
      _scheduleArchiveStuckRetry, _archiveListFetchInflight, _recoverArchiveRenderIfStuck, _scheduleArchiveStaleRetry, _hydrateArchiveSideData, _startArchiveProgressPoll, _stopArchiveProgressPoll, refreshArchiveData,
    };
  }

  root.CCCConversationDetails = Object.freeze({ createController, createArchiveController });
})(typeof window === 'undefined' ? globalThis : window);
