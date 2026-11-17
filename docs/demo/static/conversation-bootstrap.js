/* Shared conversation bootstrap with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices }) {
    const { popoutConversationIdForRow } = root.CCCConversationDetails
      ? root.CCCConversationDetails.createController({
          state: sharedState, helpers: hostServices,
          functions: {

          },
        }, "conversation-bootstrap.js")
      : {};

  function popoutTargetMatches(row) {
    if (!row || !sharedState.CONV_POPOUT_TARGET) return false;
    return row.id === sharedState.CONV_POPOUT_TARGET || row.session_id === sharedState.CONV_POPOUT_TARGET;
  }

  function findPopoutConversationRow() {
    if (!sharedState.CONV_POPOUT_TARGET) return null;
    return (sharedState.conversationsData || []).find(popoutTargetMatches) || null;
  }

  function setPopoutTitle(row) {
    if (!sharedState.CONV_POPOUT_MODE) return;
    const title = (row && (row.display_name || row.ai_title || (0, sharedState.firstSentenceOf)(row.first_message || '', 70)))
      || sharedState.CONV_POPOUT_TARGET.slice(0, 8)
      || 'Conversation';
    document.title = title + ' - Command Center for Claude, Codex, Cursor, and Anti-Gravity';
  }

  function popoutParam(name) {
    return (sharedState._bootUrlParams.get(name) || '').trim();
  }

  function popoutIntParam(name) {
    const raw = popoutParam(name);
    if (!raw) return null;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
  }

  function buildSyntheticPopoutRow() {
    if (!sharedState.CONV_POPOUT_MODE || !sharedState.CONV_POPOUT_TARGET) return null;
    const source = popoutParam('source') || 'interactive';
    const sessionId = popoutParam('session_id') || (source === 'backlog' ? '' : sharedState.CONV_POPOUT_TARGET);
    const issueNumber = popoutIntParam('issue_number');
    const spawnPid = popoutIntParam('spawn_pid');
    return {
      id: sharedState.CONV_POPOUT_TARGET,
      session_id: sessionId || undefined,
      source,
      engine: popoutParam('engine') || undefined,
      display_name: popoutParam('title') || '',
      status_rail_title: popoutParam('status_rail_title') || '',
      first_message: popoutParam('first_message') || '',
      folder_label: popoutParam('folder_label') || '',
      folder_label_chip: popoutParam('folder_label') || '',
      repo_path: sharedState.CONV_POPOUT_REPO_PATH || '',
      session_cwd: popoutParam('cwd') || '',
      session_cwd_exists: popoutParam('cwd_exists') === '1',
      spawn_pid: spawnPid === null ? undefined : spawnPid,
      issue_number: issueNumber === null ? undefined : issueNumber,
    };
  }

  function installSyntheticPopoutRow(row) {
    if (!row) return;
    sharedState.conversationsData = [row];
    sharedState.conversationsLoaded = true;
    sharedState._convInitialLoadDone = true;
    sharedState.currentRepoBacklogData = row.source === 'backlog' ? [row] : [];
    sharedState.sessionIdByConv = {};
    sharedState.sessionCwdByConv = {};
    sharedState.sessionCwdExistsByConv = {};
    sharedState.sessionSourceByConv = {};
    sharedState.sessionSpawnPidByConv = {};
    if (row.session_id) sharedState.sessionIdByConv[row.id] = row.session_id;
    if (row.session_cwd) sharedState.sessionCwdByConv[row.id] = row.session_cwd;
    sharedState.sessionCwdExistsByConv[row.id] = !!row.session_cwd_exists;
    sharedState.sessionSourceByConv[row.id] = row.source || 'interactive';
    if (row.spawn_pid) sharedState.sessionSpawnPidByConv[row.id] = row.spawn_pid;
  }

  async function bootConversationPopoutDirect() {
    if (!sharedState.CONV_POPOUT_MODE || !sharedState.CONV_POPOUT_TARGET) return false;
    // CCC-872: '__new__' is the not-yet-spawned "new session" composer, not a
    // real conversation — selectConversation() would try to stream a
    // conversation that doesn't exist and leave the popout blank. Boot
    // straight into the same composer the main window shows instead.
    if (sharedState.CONV_POPOUT_TARGET === '__new__') {
      document.title = 'New session - Command Center for Claude, Codex, Cursor, and Anti-Gravity';
      (0, sharedState.hideLoadingOverlay)();
      _markFirstSessionsLoaded();
      (0, sharedState.enterNewSessionMode)();
      return true;
    }
    const row = findPopoutConversationRow() || buildSyntheticPopoutRow();
    installSyntheticPopoutRow(row);
    setPopoutTitle(row);
    (0, sharedState.updatePaneHeader)((0, sharedState.activePaneId)(), row, {
      category: popoutParam('category') || (0, sharedState.paneCategoryForRow)(row),
      title: popoutParam('title') || (0, sharedState.paneTitleForRow)(row),
    });
    (0, sharedState.hideLoadingOverlay)();
    _markFirstSessionsLoaded();
    try {
      await (0, sharedState.selectConversation)(row.id, (0, sharedState.activePaneId)());
    } finally {
      // The conversation is on screen: let the held boot reads go, and tell
      // a host page (an app measuring its own load) that the pane is ready.
      // A pane behind a tab (an app on a phone) gets no animation frame, so
      // a short timer stands in for the paint.
      let embedReadySent = false;
      const embedReady = () => {
        if (embedReadySent) return;
        embedReadySent = true;
        (0, sharedState._releaseStartupApiReads)();
        if (window.parent !== window && !sharedState.APP_EMBED_MODE) {
          try {
            window.parent.postMessage({ type: 'ccc:embed-ready', conv: sharedState.CONV_POPOUT_TARGET, ms: Math.round(performance.now()) }, window.location.origin);
          } catch (_) {}
        }
      };
      requestAnimationFrame(() => setTimeout(embedReady, 0));
      setTimeout(embedReady, 250);
    }
    return true;
  }

  function embedHostOriginTrusted(origin) {
    if (!origin || origin === 'null') return false;
    if (origin === window.location.origin) return true;
    let host;
    try { host = new URL(origin).hostname; } catch (_) { return false; }
    if (host === '127.0.0.1' || host === 'localhost' || host === '[::1]') return true;
    if (/^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}$/.test(host)) return true;
    const tailnet = window.location.hostname.match(/\.[a-z0-9-]+\.ts\.net$/i);
    return !!(tailnet && host.toLowerCase().endsWith(tailnet[0].toLowerCase()));
  }

  function switchPopoutConversation(conv) {
    if (conv === sharedState.CONV_POPOUT_TARGET && sharedState.currentConversation === conv) return;
    sharedState.CONV_POPOUT_TARGET = conv;
    ['conv', 'conversation', 'session_id', 'source', 'engine', 'title', 'cwd', 'repo_path'].forEach(k => sharedState._bootUrlParams.delete(k));
    sharedState._bootUrlParams.set('conv', conv);
    try {
      const u = new URL(window.location.href);
      u.search = sharedState._bootUrlParams.toString();
      history.replaceState(history.state, '', u.toString());
    } catch (_) {}
    document.body.classList.toggle('subagent-readonly', conv.includes(':agent-'));
    sharedState._popoutMissingShown = false;
    const row = buildSyntheticPopoutRow();
    installSyntheticPopoutRow(row);
    setPopoutTitle(row);
    (0, sharedState.updatePaneHeader)((0, sharedState.activePaneId)(), row, { category: (0, sharedState.paneCategoryForRow)(row), title: (0, sharedState.paneTitleForRow)(row) });
    Promise.resolve((0, sharedState.selectConversation)(row.id, (0, sharedState.activePaneId)())).catch(err => {
      const view = (0, sharedState.getConvView)();
      if (view) {
        view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Failed to load conversation: '
          + (0, sharedState.escapeHtml)(err && err.message ? err.message : String(err))
          + '</div>';
      }
    });
  }

  function wireEmbedHostMessages() {
    if (!sharedState.CONV_POPOUT_MODE || window.parent === window) return;
    window.addEventListener('message', (ev) => {
      if (ev.source !== window.parent || !embedHostOriginTrusted(ev.origin)) return;
      const d = ev.data;
      if (!d || typeof d !== 'object') return;
      if (d.type === 'ccc:embed-hello') {
        window.parent.postMessage({ type: 'ccc:embed-hello', open: true }, ev.origin);
        return;
      }
      if (d.type !== 'ccc:embed-open') return;
      const conv = typeof d.conv === 'string' ? d.conv.trim() : '';
      if (!/^[A-Za-z0-9_.:-]{1,200}$/.test(conv) || conv === '__new__') return;
      switchPopoutConversation(conv);
      window.parent.postMessage({ type: 'ccc:embed-opened', conv }, ev.origin);
    });
  }

  function _markFirstSessionsLoaded() {
    if (sharedState._firstSessionsResolve) { (0, sharedState._firstSessionsResolve)(); sharedState._firstSessionsResolve = null; }
  }
    return { bootConversationPopoutDirect, findPopoutConversationRow, popoutTargetMatches, buildSyntheticPopoutRow, installSyntheticPopoutRow, setPopoutTitle, popoutParam, popoutIntParam, popoutConversationIdForRow, embedHostOriginTrusted, switchPopoutConversation, wireEmbedHostMessages, _markFirstSessionsLoaded };
  }
  root.CCCConversationBootstrap = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
