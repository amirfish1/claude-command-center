/* Shared conversation input with explicit live-state and host adapters. */
(function (root) {
  'use strict';
  function createController({ state, helpers }) {
  const INPUT_DRAFTS_KEY = 'ccc-input-drafts-v1';
  const INPUT_DRAFTS_MAX = 200;
  const INPUT_DRAFT_MAX_CHARS = 50000;
  const INPUT_DRAFTS_SAVE_DEBOUNCE_MS = 400;
  const COMPOSER_HISTORY_KEY = 'ccc-composer-history';
  const COMPOSER_HISTORY_LIMIT = 50;
  let _inputDraftsSaveTimer = 0;
  const _inputDraftKeyCache = new Map();
  let inputDrafts = (() => {
    try {
      const raw = JSON.parse(localStorage.getItem(INPUT_DRAFTS_KEY) || '{}');
      const out = {};
      if (raw && typeof raw === 'object') {
        for (const [key, value] of Object.entries(raw)) {
          if (!key) continue;
          if (value && typeof value === 'object' && typeof value.text === 'string') {
            out[key] = {
              text: value.text,
              updated: Number(value.updated) || Date.now(),
            };
          } else if (typeof value === 'string') {
            out[key] = { text: value, updated: Date.now() };
          }
        }
      }
      return out;
    } catch (_) {
      return {};
    }
  })();



  function setCurrentSession(source, sid, cwd, cwdExists, spawnPid, repoPath, transcriptPath) {
    const row = (Array.isArray(state.conversationsData) ? state.conversationsData : []).find(c => (
      c && (
        (sid && (c.session_id === sid || c.id === sid))
        || (state.currentConversation && c.id === state.currentConversation)
      )
    ))
      // Cross-repo rows (e.g. a devincli session in another workspace) live in
      // archiveData — same fallback as selectedConv above, or their
      // devin_acp_ready flag reads as absent and acp_steer_ready stays false.
      || (Array.isArray(state.archiveData) ? state.archiveData.find(c => (
        c && (
          (sid && (c.session_id === sid || c.id === sid))
          || (state.currentConversation && c.id === state.currentConversation)
        )
      )) : null)
      || null;
    state.currentSession = {
      id: sid || null,
      cwd: cwd || null,
      cwdExists: !!cwdExists,
      source: source,
      // Read at the queued-card Steer decision (queuedSteerSource): source
      // alone says 'interactive' for a CCC-spawned Codex session.
      engine: (row && row.engine) || null,
      spawnPid: spawnPid || null,
      repoPath: repoPath || null,
      can_headless_resume: source === 'antigravity' ? !!(row && row.can_headless_resume === true) : true,
      can_app_resume: source === 'antigravity' ? !!(row && row.can_app_resume === true) : false,
      // Signal for the experimental Devin ACP live-steer path (see
      // ccc_server/acp.py's _devin_acp_steer_capable and
      // ccc_server/devin.py's `devin_acp_ready` row field): "could a steer
      // be attempted" — opt-in flag on and `devin` binary resolvable. The
      // ACP connection itself is attached lazily by the first steer.
      acp_steer_ready: source === 'devin-cli' ? !!(row && row.devin_acp_ready === true) : false,
      // Codex cloud (dot/aeon) thread: transcript renders read-only; the
      // composer is disabled because the cloud backend is never written to.
      codex_cloud: !!(row && row.codex_cloud === true),
    };
    // Leaving new-session mode (sid set) drops the .is-new-session class
    // so the spawn-cwd picker hides and the workspace pill returns. The
    // class is set in enterNewSessionMode(); this is the symmetric clear.
    if (sid || state.currentConversation !== '__new__') {
      // Return the CWD picker row + quick chips home if the new-session
      // chooser borrowed them (see _adoptCwdControlsIntoChooser, CCC-86).
      try { (0, helpers._restoreCwdControlsToInputBar)(); } catch (_) {}
      // Re-home the new-session chrome: to another pane still composing a
      // new session (split view), else back to p1 with .is-new-session off.
      if (typeof helpers.mountNewSessionChrome === 'function') {
        try { (0, helpers.mountNewSessionChrome)(); } catch (_) {}
      }
      if (typeof helpers.syncAllPaneHasFlags === 'function') (0, helpers.syncAllPaneHasFlags)();
    }
    const resolvedTranscriptPath = transcriptPath || (row && row.jsonl_path) || '';
    (0, helpers.setCopyableSessionId)(state.$convSessionId, sid, resolvedTranscriptPath);
    // A live or deep-link session may not yet be in conversationsData even
    // though its transcript is already on disk. Ask the server's resolver for
    // the concrete engine-specific path instead of guessing it in the UI.
    if (sid && !resolvedTranscriptPath) {
      fetch('/api/conversations/' + encodeURIComponent(sid) + '/transcript-path', { signal: (0, helpers.convScopeSignal)() })
        .then(res => res.ok ? res.json() : null)
        .then(data => {
          const path = data && String(data.transcript_path || '').trim();
          if (path && state.$convSessionId && state.$convSessionId.dataset.copySessionId === sid) {
            (0, helpers.setCopyableSessionId)(state.$convSessionId, sid, path);
          }
        })
        .catch(() => {});
    }
    (0, helpers.updateConvOverflowButton)();
    if (source === 'pkood') {
      // Pkood sessions don't need live status polling or resume button
      if (state.liveStatusTimer) { clearInterval(state.liveStatusTimer); state.liveStatusTimer = null; }
      if (state.liveStatusRenderTicker) { clearInterval(state.liveStatusRenderTicker); state.liveStatusRenderTicker = null; }
      state.liveStatus = { forSessionId: sid, live: false, pid: null, tty: null, terminalApp: null, staleToolCall: false, staleToolAgeS: 0, needsApproval: false, needsApprovalMessage: '', acpPendingPermission: null, questionWaiting: false, questionText: '', questionHeader: '', questionPreamble: '', questionOptions: [], questionOptionDetails: [] };
      (0, helpers.updateResumeButton)();
      (0, helpers.updateAnnounceButton)();
      (0, helpers.updateJumpButton)();
      updateInputBar();
    } else {
      (0, helpers.updateResumeButton)();
      (0, helpers.updateAnnounceButton)();
      (0, helpers.startLiveStatusPolling)();
    }
    // The localhost / dev-server pill is bound to the open session's repo. Its
    // cached probe result and target path belong to the PREVIOUS session until
    // the next poll lands. Forget them immediately, but let selectConversation
    // start the subprocess-backed probe only after the transcript is painted;
    // the status pill must never compete with the foreground session open.
    try {
      if (typeof helpers.resetLocalhostPill === 'function') (0, helpers.resetLocalhostPill)();
    } catch (_) {}
  }

  function inputControlsForBar(bar) {
    const root = bar || state.$convInputBar;
    const q = (sel) => root ? root.querySelector(sel) : null;
    return {
      input: q('textarea, input[type="text"]') || (root === state.$convInputBar ? state.$convInput : null),
      ttyLabel: q('#convTtyLabel, .tty-label, [data-role="tty-label"]') || state.$convTtyLabel,
      sendBtn: q('.send-btn') || (root === state.$convInputBar ? state.$convSendBtn : null),
      steerBtn: q('.steer-btn') || (root === state.$convInputBar ? state.$convSteerBtn : null),
      compactBtn: q('.compact-btn') || (root === state.$convInputBar ? state.$convCompactBtn : null),
      escBtn: q('.esc-btn') || (root === state.$convInputBar ? state.$convEscBtn : null),
      issueAction: q('#convInputIssueAction, select[title="Action to take on this issue"]'),
      announcedFromControl: q('.conv-announced-from') || (root === state.$convInputBar ? state.$convAnnouncedFromControl : null),
      codexAppSrv: q('.codex-appsrv') || (root === state.$convInputBar ? state.$convCodexAppSrv : null),
      codexAppSrvLabel: q('.codex-appsrv-label') || (root === state.$convInputBar ? state.$convCodexAppSrvLabel : null),
      engineSelect: q('.engine-select') || (root === state.$convInputBar ? document.getElementById('convInputEngineSelect') : null),
      modelSelect: q('.model-select') || (root === state.$convInputBar ? document.getElementById('convInputModelSelect') : null),
      effortSelect: q('.effort-select') || (root === state.$convInputBar ? document.getElementById('convInputEffortSelect') : null),
    };
  }

  function saveInputDrafts() {
    try {
      const entries = Object.entries(inputDrafts)
        .filter(([, value]) => value && typeof value.text === 'string' && value.text.length)
        .sort((a, b) => (Number(b[1].updated) || 0) - (Number(a[1].updated) || 0))
        .slice(0, INPUT_DRAFTS_MAX);
      inputDrafts = Object.fromEntries(entries);
      localStorage.setItem(INPUT_DRAFTS_KEY, JSON.stringify(inputDrafts));
    } catch (_) {}
  }

  function scheduleSaveInputDrafts() {
    if (_inputDraftsSaveTimer) clearTimeout(_inputDraftsSaveTimer);
    _inputDraftsSaveTimer = setTimeout(() => {
      _inputDraftsSaveTimer = 0;
      saveInputDrafts();
    }, INPUT_DRAFTS_SAVE_DEBOUNCE_MS);
  }

  function flushSaveInputDrafts() {
    if (_inputDraftsSaveTimer) {
      clearTimeout(_inputDraftsSaveTimer);
      _inputDraftsSaveTimer = 0;
    }
    saveInputDrafts();
  }

  function clearInputDraftKeyCache() {
    _inputDraftKeyCache.clear();
  }

  function inputDraftKeyForConversation(convId) {
    const id = String(convId || '');
    if (!id) return '';
    if (_inputDraftKeyCache.has(id)) return _inputDraftKeyCache.get(id);
    let key;
    if (id === '__new__') {
      key = 'new-session';
    } else {
      const row = (Array.isArray(state.conversationsData) ? state.conversationsData : [])
        .find(c => c && c.id === id);
      if ((row && row.source === 'backlog') || id.startsWith('backlog-issue-')) {
        key = 'repo:' + ((0, helpers.rowRepoPath)(row) || (0, helpers.popoutRepoPath)() || 'unknown') + ':conv:' + id;
      } else {
        key = 'conv:' + id;
      }
    }
    _inputDraftKeyCache.set(id, key);
    return key;
  }

  function setInputDraftForKey(key, value) {
    if (!key) return;
    const text = String(value == null ? '' : value);
    if (!text) {
      if (inputDrafts[key]) {
        delete inputDrafts[key];
        flushSaveInputDrafts();
      }
      return;
    }
    inputDrafts[key] = {
      text: text.length > INPUT_DRAFT_MAX_CHARS ? text.slice(0, INPUT_DRAFT_MAX_CHARS) : text,
      updated: Date.now(),
    };
    scheduleSaveInputDrafts();
  }

  function clearInputDraftForConversation(convId) {
    setInputDraftForKey(inputDraftKeyForConversation(convId), '');
  }

  function inputDraftForConversation(convId) {
    const draft = inputDrafts[inputDraftKeyForConversation(convId)];
    return draft && typeof draft.text === 'string' ? draft.text : '';
  }

  function rememberInputDraft(input, convId) {
    if (!input) return;
    setInputDraftForKey(inputDraftKeyForConversation(convId), input.value || '');
  }

  function loadComposerHistory() {
    try {
      const raw = JSON.parse(localStorage.getItem(COMPOSER_HISTORY_KEY) || '[]');
      if (!Array.isArray(raw)) return [];
      return raw
        .filter(item => typeof item === 'string' && item.trim())
        .slice(-COMPOSER_HISTORY_LIMIT);
    } catch (_) {
      return [];
    }
  }

  function saveComposerHistory(history) {
    try {
      localStorage.setItem(COMPOSER_HISTORY_KEY, JSON.stringify((history || []).slice(-COMPOSER_HISTORY_LIMIT)));
    } catch (_) {}
  }

  function rememberComposerCommand(text) {
    const command = String(text || '').trim();
    if (!command) return;
    const history = loadComposerHistory().filter(item => item !== command);
    history.push(command);
    saveComposerHistory(history);
  }

  function recallLastComposerCommand(input, ev) {
    if (!input || !ev) return false;
    if ((0, helpers.isImeKey)(ev)) return false;
    if (ev.key !== 'ArrowUp') return false;
    if (ev.shiftKey || ev.altKey || ev.ctrlKey || ev.metaKey || ev.isComposing) return false;
    if (input.readOnly || input.disabled) return false;
    if ((input.value || '').trim()) return false;
    const history = loadComposerHistory();
    const command = history[history.length - 1] || '';
    if (!command) return false;
    ev.preventDefault();
    input.value = command;
    moveInputCaretToEnd(input);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }

  function composerInputForPane(paneId) {
    const pane = (0, state.convPaneElById)(paneId || (0, state.activePaneId)());
    if (!pane) return paneId === 'p1' ? state.$convInput : null;
    return pane.querySelector('.conv-input-bar textarea, .conv-input-bar input[type="text"]');
  }

  function announcedFromInputForPane(paneId) {
    const pane = document.querySelector(`.conv-pane[data-pane-id="${paneId || (0, state.activePaneId)()}"]`);
    if (!pane) return paneId === 'p1' ? state.$convAnnouncedFrom : null;
    return pane.querySelector('.conv-announced-from-input');
  }

  function announcedFromForPane(paneId) {
    const input = announcedFromInputForPane(paneId || (0, state.activePaneId)());
    return (input && input.value || '').trim();
  }

  function announcedInjectionPreview(text, announcedFrom) {
    const label = String(announcedFrom || '').trim();
    if (!label) return text;
    return 'Announced from: ' + label + '\n\n' + text;
  }

  function rememberComposerDraftForPane(paneId) {
    const pane = (0, state.paneByPaneId)(paneId || (0, state.activePaneId)());
    if (!pane || !pane.conversationId) return;
    rememberInputDraft(composerInputForPane(pane.id), pane.conversationId);
  }

  function restoreInputDraft(input, convId) {
    if (!input) return;
    input.value = inputDraftForConversation(convId);
    moveInputCaretToEnd(input);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function moveInputCaretToEnd(input) {
    if (!input) return;
    const end = (input.value || '').length;
    try { input.setSelectionRange(end, end); } catch (_) {}
  }

  function restoreComposerDraftForPane(paneId, convId) {
    restoreInputDraft(composerInputForPane(paneId), convId);
  }

  function restoreSplitPanelDraft(convId) {
    const input = document.getElementById('cpInput');
    if (!input) return;
    restoreInputDraft(input, convId);
    if (input.__cpRefresh) input.__cpRefresh();
  }

  function issueInputPlaceholder(issueNum) {
    const $ia = document.getElementById('convInputIssueAction');
    const action = ($ia && $ia.value) || 'spawn';
    if (action === 'needs_attention') return 'Add a GitHub comment; submit to mark issue #' + issueNum + ' Needs-Attention…';
    if (action === 'close') return 'Add a GitHub comment; submit to close issue #' + issueNum + '…';
    if (action === 'duplicate') return 'Duplicate of issue # (enter the number)';
    if (action === 'completed' || action === 'not_planned') return 'Optional comment (or submit blank to close immediately)';
    return 'Add edit instructions; submit to start session for issue #' + issueNum + '…';
  }

  function currentConversationRow() {
    const id = state.currentConversation;
    if (!id) return null;
    return (state.conversationsData || []).find(x => x && x.id === id)
      || (Array.isArray(state.archiveData)
        ? state.archiveData.find(x => x && ((x.id || x.session_id) === id))
        : null)
      || null;
  }

  function updateInputBar() {
    (0, helpers.syncCodexAccountUi)();
    (0, helpers.syncAntigravityAccountUi)();
    if (!state.$convInputBar) return;
    // In split-pane mode the active pane may not be p1; find its input bar so
    // we toggle 'visible' on the right element (p1's #convInputBar is always
    // correct when p1 is active; for other panes it has no id and must be
    // found by pane selector). Falls back to $convInputBar (p1) when not split.
    const _activeInputBar = (typeof state.getConvInputBarForPane === 'function')
      ? ((0, state.getConvInputBarForPane)((0, state.activePaneId)()) || state.$convInputBar)
      : state.$convInputBar;
    (0, helpers.syncConversationArchiveButton)(_activeInputBar);
    const activeInputControls = inputControlsForBar(_activeInputBar);
    const activeInput = activeInputControls.input;
    const activeSendBtn = activeInputControls.sendBtn;
    const activeSteerBtn = activeInputControls.steerBtn;
    const activeCompactBtn = activeInputControls.compactBtn;
    const activeEscBtn = activeInputControls.escBtn;
    const activeIssueAction = activeInputControls.issueAction;
    const activeAnnouncedFromControl = activeInputControls.announcedFromControl;
    const activeCodexAppSrv = activeInputControls.codexAppSrv;
    const activeCodexAppSrvLabel = activeInputControls.codexAppSrvLabel;
    const activeEngineSelect = activeInputControls.engineSelect;
    const activeModelSelect = activeInputControls.modelSelect;
    const activeEffortSelect = activeInputControls.effortSelect;
    const isPkood = state.currentSession.source === 'pkood';
    const isCodex = state.currentSession.source === 'codex';
    // Codex cloud (dot/aeon) thread: read-only. Its transcript lives in
    // OpenAI's cloud and CCC never writes to that backend. The flag is not
    // gated on source: popout/synthetic rows default source to 'interactive'
    // while the marker arrives with the transcript payload.
    const isCodexCloud = !!state.currentSession.codex_cloud;
    const isGemini = state.currentSession.source === 'gemini';
    const isCursor = state.currentSession.source === 'cursor';
    const isAntigravity = state.currentSession.source === 'antigravity';
    const isHermes = state.currentSession.source === 'hermes';
    const isKimi = state.currentSession.source === 'kimi';
    const isOpencode = state.currentSession.source === 'opencode';
    const isDevin = state.currentSession.source === 'devin';
    const isDevinCli = state.currentSession.source === 'devin-cli';
    // A foreign host (Devin Desktop / Next, a sibling CCC) holds this
    // devin-cli session's lock: sends still queue and drain on release, but
    // the composer reads "not ours right now" instead of fully live.
    const devinCliExtOwned = isDevinCli && (0, helpers.liveStatusMatchesOpenConv)()
      && !!(state.liveStatus && state.liveStatus.devinExternalOwner);
    const antigravityCanSendNow = (0, helpers.antigravityCanSend)(state.currentSession);
    // liveStatus lags a conversation switch by up to one poll; until it matches
    // the open conversation, treat the session as not-live so we never show the
    // PREVIOUS session's terminal/tty in the freshly-opened one.
    const live = (0, helpers.liveStatusMatchesOpenConv)() && state.liveStatus.live && state.liveStatus.tty;
    const isConvTab = state.activeTab === 'sessions';
    const hasSession = !!state.currentSession.id;
    const isNewSession = state.currentConversation === '__new__';
    if (_activeInputBar) {
      _activeInputBar.classList.toggle('is-new-session-launch', isNewSession);
      // devin-cli session whose lock a foreign host holds: dim the composer —
      // sends still queue (they drain on release), but the bar reads "not
      // ours right now" instead of looking fully live.
      _activeInputBar.classList.toggle('is-externally-managed', devinCliExtOwned);
    }
    (0, helpers.renderNewSessionObjectContext)();
    // Backlog GH issue: viewing an issue card in the right pane. Submitting
    // the input bar spawns a session equivalent to clicking "Edit & start"
    // on the kanban card, with the typed text appended to the standard
    // issue-context preamble.
    const currentBacklogRow = state.conversationsData.find(x => x.id === state.currentConversation && x.source === 'backlog');
    const isBacklogIssue = !!currentBacklogRow || (state.currentConversation || '').startsWith('backlog-issue-');
    const showInputBar = isConvTab && (hasSession || isNewSession || isBacklogIssue);
    (0, helpers.renderConversationGoalStrip)((0, state.activePaneId)(), showInputBar ? currentConversationRow() : null);
    // F2 cold-session composer. This is the "focus" trigger: the rail depends
    // only on the session row, so it must paint the moment a session is
    // selected — not at submit time, when the user has already committed.
    // f2RenderComposer clears itself for new-session/backlog/no-session modes.
    try { (0, helpers.f2RenderComposer)((0, state.activePaneId)()); } catch (_) {}
    // Show the bar for any selected session — /api/inject-input routes to
    // live-TTY keystroke injection when the session is alive and falls back
    // to headless `claude --resume` when dormant, so the user can always
    // type a follow-up. Previously hidden for dormant sessions, which left
    // the user with Resume/Launch buttons and no way to just send a message.
    // Also show in "new session" mode where the input doubles as the
    // prompt for spawning a fresh agent.
    if (showInputBar) {
      _activeInputBar.classList.add('visible');
      if (isBacklogIssue) {
        const n = (currentBacklogRow && currentBacklogRow.issue_number) || state.currentConversation.replace('backlog-issue-', '');
        activeInputControls.ttyLabel.textContent = '#' + n;
        if (activeIssueAction) activeIssueAction.style.display = '';
        if (activeInput) activeInput.placeholder = issueInputPlaceholder(n);
      } else if (isNewSession) {
        activeInputControls.ttyLabel.textContent = 'new';
        const cwdForPrompt = (typeof helpers.getSpawnCwd === 'function' && (0, helpers.getSpawnCwd)()) || (0, helpers.activeConvRepoPath)();
        const repoLabel = (typeof helpers.spawnCwdLabel === 'function' && (0, helpers.spawnCwdLabel)(cwdForPrompt)) || (0, helpers._pathLeaf)(cwdForPrompt);
        const spawnEngine = (0, helpers.getSpawnEngine)();
        if (spawnEngine === 'antigravity') {
          if (activeInput) activeInput.placeholder = repoLabel
              ? 'Type a prompt to start a headless Antigravity run in ' + repoLabel + '…'
              : 'Pick a folder before starting Antigravity…';
        } else {
          if (activeInput) activeInput.placeholder = repoLabel
              ? 'Type a prompt to start a new session in ' + repoLabel + '…'
              : 'Pick a folder before starting a new session…';
        }
      } else if (isPkood) {
        activeInputControls.ttyLabel.textContent = 'pkood';
        if (activeInput) activeInput.placeholder = 'Send to pkood agent...';
      } else if (isCodexCloud) {
        activeInputControls.ttyLabel.textContent = 'cloud';
        if (activeInput) activeInput.placeholder = 'Codex cloud thread, read-only in CCC - reply in the Codex app…';
      } else if (isCodex) {
        activeInputControls.ttyLabel.textContent = live ? (state.liveStatus.tty || 'codex') : 'codex';
        if (activeInput) activeInput.placeholder = live ? 'Send to Codex terminal...' : 'Resume Codex and send...';
      } else if (isGemini) {
        activeInputControls.ttyLabel.textContent = live ? (state.liveStatus.tty || 'gemini') : 'gemini';
        if (activeInput) activeInput.placeholder = live ? 'Send to Gemini terminal...' : 'Resume Gemini and send...';
      } else if (isCursor) {
        activeInputControls.ttyLabel.textContent = live ? (state.liveStatus.tty || 'cursor') : 'cursor';
        if (activeInput) activeInput.placeholder = live ? 'Send to Cursor terminal...' : 'Resume Cursor and send...';
      } else if (isAntigravity) {
        activeInputControls.ttyLabel.textContent = state.liveStatus.live ? (state.liveStatus.tty || 'antigravity') : 'antigravity';
        if (activeInput) activeInput.placeholder = antigravityCanSendNow
            ? (0, helpers.antigravityInputPlaceholder)(state.currentSession)
            : 'Type a follow-up - Antigravity will resume headless…';
      } else if (isHermes) {
        activeInputControls.ttyLabel.textContent = 'hermes';
        if (activeInput) activeInput.placeholder = 'Resume Hermes and send...';
      } else if (isOpencode) {
        activeInputControls.ttyLabel.textContent = 'opencode';
        if (activeInput) activeInput.placeholder = 'Resume OpenCode and send...';
      } else if (isKimi) {
        activeInputControls.ttyLabel.textContent = 'kimi';
        if (activeInput) activeInput.placeholder = state.liveStatus.live ? 'Send to Kimi session…' : 'Resume Kimi and send…';
      } else if (isDevin) {
        // Devin cloud sessions are read-only: transcripts render here, but
        // replies belong in the Devin app. Block the composer instead of
        // failing the send with repo_required.
        activeInputControls.ttyLabel.textContent = 'devin';
        if (activeInput) activeInput.placeholder = 'Devin cloud sessions are read-only in CCC - reply at app.devin.ai…';
      } else if (isDevinCli) {
        activeInputControls.ttyLabel.textContent = devinCliExtOwned ? 'external' : 'devin';
        if (activeInput) activeInput.placeholder = devinCliExtOwned
          ? 'Managed externally - open in another client (e.g. Devin Desktop); sends queue until it lets go…'
          : (state.liveStatus.live ? 'Send to Devin session…' : 'Resume Devin and send…');
      } else if (live) {
        activeInputControls.ttyLabel.textContent = state.liveStatus.tty;
        if (activeInput) activeInput.placeholder = 'Send to terminal...';
      } else if (state.liveStatus.live && state.liveStatus.bgPresent) {
        // Live in a Claude-app-managed terminal (no system tty): not dormant
        // — the process is up, CCC just has no input channel to it (CCC-115).
        activeInputControls.ttyLabel.textContent = 'Claude app';
        if (activeInput) activeInput.placeholder = 'Session is open in the Claude app - messages park until it closes…';
      } else if (state.liveStatus.live && state.liveStatus.pid) {
        // Live process without a tty (e.g. CCC headless): also not dormant.
        activeInputControls.ttyLabel.textContent = 'headless';
        if (activeInput) activeInput.placeholder = 'Send to headless…';
      } else {
        activeInputControls.ttyLabel.textContent = 'dormant';
        if (activeInput) activeInput.placeholder = 'Resume and send…';
      }
      // Antigravity sessions always accept input — the server's
      // resume_session_antigravity() routes to CLI headless / running
      // app / a fresh app launch with the prompt prefilled, in priority
      // order. Previously we readOnly'd the input when both
      // can_headless_resume and can_app_resume were false, leaving the
      // user with a dead input bar and no way to type a follow-up.
      const canSend = !isDevin && !isCodexCloud && (!isAntigravity || antigravityCanSendNow);
      if (activeInput) {
        const blockTyping = !canSend && !isAntigravity;
        activeInput.readOnly = blockTyping;
        activeInput.classList.toggle('is-readonly', blockTyping);
      }
      if (activeSendBtn) {
        const blockSend = !canSend && !isAntigravity;
        activeSendBtn.disabled = blockSend;
        activeSendBtn.title = blockSend
          ? (isCodexCloud
              ? 'Codex cloud thread, read-only in CCC'
              : isDevin
              ? 'Devin cloud sessions are read-only in CCC'
              : 'Open Antigravity to continue this app session')
          : (devinCliExtOwned
              ? 'Queue message - delivers when the external client releases this session'
              : (isAntigravity && !antigravityCanSendNow
                  ? 'Send - runs AGY headless on this session'
                  : 'Send'));
      }
      // CCC-46: when a relayed AskUserQuestion card is showing for this
      // session there must be exactly ONE place to answer. The bottom composer
      // otherwise invites a second input that does NOT answer the question —
      // it queues a follow-up the blocked turn never reads. Redirect the user
      // up to the card while it's open.
      if (activeInput && state.liveStatus.questionWaiting
          && typeof helpers._relayedQuestionInlineEl === 'function' && (0, helpers._relayedQuestionInlineEl)()) {
        activeInput.readOnly = true;
        activeInput.classList.add('is-readonly');
        activeInput.placeholder = '↑ Answer the question above to continue';
        if (activeSendBtn) {
          activeSendBtn.disabled = true;
          activeSendBtn.title = 'Answer the question above first';
        }
      }
      if (activeSteerBtn) {
        // Claude headless is steerable too (CCC): the server writes an
        // `interrupt` control request to the spawn's FIFO, which aborts the
        // in-flight tool and ends the turn. Gate on a tool actually being in
        // flight — that's the case a plain Send cannot reach, because a turn
        // wedged on a long tool child never hits the boundary where queued
        // input lands.
        const isClaudeHeadless = !isCodex && !isKimi && !isCursor && !isHermes
          && !isPkood && !isGemini && !isAntigravity && !isDevinCli
          && !!state.liveStatus.live && !!state.liveStatus.headlessPresent && !state.liveStatus.tty;
        const claudeSteerable = isClaudeHeadless && !!state.liveStatus.sidecarInFlight;
        // Devin CLI sends/steers over a `devin acp` ACP connection that the
        // first send attaches lazily — the row field devin_acp_ready mirrors
        // server-side "could attempt" (binary resolves), not "a connection
        // is already up" (nothing else ever creates one).
        const devinSteerable = (0, helpers.devinSteerCapableNow)();
        // Codex resolves the active turn on the server and falls back to Send
        // when idle. A stale status snapshot must not prevent that attempt.
        const canSteer = canSend && hasSession && !isNewSession && !isBacklogIssue
          && (isCodex || isKimi || devinSteerable || claudeSteerable);
        activeSteerBtn.classList.toggle('visible', canSteer);
        activeSteerBtn.disabled = !canSteer;
        activeSteerBtn.title = canSteer
          ? (isKimi
              ? 'Steer running Kimi turn now'
              : (isCodex
                  ? 'Steer Codex now (send if no turn is running)'
                  : (isDevinCli
                      ? 'Steer this Devin session (attaches a live devin acp connection on first use)'
                      : 'Interrupt the running tool and send this now')))
          : (isCodex
              ? 'No running Codex turn can be steered from CCC; use Send to resume or follow up'
              : (isKimi
                  ? 'Steer is available while a Kimi turn is running'
                  : (isDevinCli
                      ? 'Devin steer needs the devin binary on PATH'
                      : (isClaudeHeadless
                          ? 'Steer is available while a tool is running'
                          : 'Steer needs a live headless, Codex, Kimi, or Devin session'))));
      }
      // Compact button — only for compaction-capable open sessions (Claude
      // AND Codex). cursor/gemini/antigravity/kimi return
      // compact_unsupported_engine server-side, so we don't show it for them.
      // Clicking runs the same path
      // as typing /compact (routed via postRunCompactForSession).
      if (activeCompactBtn) {
        const canCompact = hasSession && !isNewSession && !isBacklogIssue && !isPkood
          && (0, helpers.isCompactionCapableSource)(state.currentSession.source);
        const pendingCompactEcho = (0, helpers.hasPendingSendEchoBeforeCompact)();
        const compactAlreadyRunning = !!(state.currentSession && (0, helpers._compactRunBlocksResend)(state.currentSession.id));
        activeCompactBtn.classList.toggle('visible', canCompact);
        if (!state._compactInFlight) activeCompactBtn.disabled = !canCompact || pendingCompactEcho || compactAlreadyRunning;
        activeCompactBtn.title = compactAlreadyRunning
          ? 'Already compacting this session.'
          : (pendingCompactEcho
              ? 'Wait for the pending message to land in the transcript before compacting.'
              : 'Compact conversation context');
      }
      // Codex app-server indicator — CCC-968: this duplicated the same state
      // already shown by the top breadcrumb's connection-type chip
      // (.status-rail-conn-proc), so it's removed rather than shown.
      if (activeCodexAppSrv) {
        const showAppSrv = false;
        activeCodexAppSrv.classList.toggle('visible', showAppSrv);
        if (!showAppSrv) activeCodexAppSrv.classList.remove('live');
        if (showAppSrv) {
          const appLive = !!state.liveStatus.codexAppServer;
          const managed = state.liveStatus.codexAppServerTransport === 'managed' || !!state.liveStatus.codexManagedAppServer;
          const label = appLive ? (managed ? 'managed app-server' : 'app-server') : 'exec';
          activeCodexAppSrv.classList.toggle('live', appLive);
          if (activeCodexAppSrvLabel) activeCodexAppSrvLabel.textContent = label;
          activeCodexAppSrv.title = appLive
            ? (managed
                ? "CCC is driving this Codex session via Codex's managed app-server Unix socket."
                : 'CCC is driving this Codex session via its private app-server fallback.')
            : 'No live CCC Codex app-server; Codex actions fall back to one-shot exec until one starts.';
        }
      }
      // Esc only makes sense when there's something live to interrupt — and
      // we don't support pkood interrupts. Hide it everywhere else so the
      // button doesn't tease an action that will just error.
      // Codex app-server sessions signal liveness via codexState='working',
      // not liveStatus.live (which stays false for pool-model Codex.app runs).
      if (activeEscBtn) {
        const codexWorking = isCodex && (0, helpers.liveStatusMatchesOpenConv)() && state.liveStatus.codexState === 'working';
        const canEsc = hasSession && !isPkood && !isNewSession && !isBacklogIssue && (!!state.liveStatus.live || codexWorking);
        activeEscBtn.style.display = canEsc ? '' : 'none';
      }
      // Issue action selector: only for backlog issues.
      if (activeIssueAction && !isBacklogIssue) activeIssueAction.style.display = 'none';
      if (activeAnnouncedFromControl) {
        const showAnnouncedFrom = hasSession && !isNewSession && !isBacklogIssue && !isPkood;
        activeAnnouncedFromControl.classList.toggle('visible', showAnnouncedFrom);
      }
      // Engine selector occupies the same slot as Esc but only matters
      // when the input is about to spawn a fresh session. Backlog-issue
      // mode also spawns, but its `/api/sessions/spawn` flow is
      // engine-fixed (claude) — don't show a misleading toggle there.
      if (activeEngineSelect) {
        activeEngineSelect.style.display = isNewSession ? '' : 'none';
      }
      if (activeModelSelect) {
        if (!isNewSession) {
          activeModelSelect.style.display = 'none';
        } else {
          const engine = (0, helpers.getSpawnEngine)();
          const defaultModel = state._defaultModelsByEngine[engine] || '';
          const options = (0, helpers.modelOptionsForSpawnEngine)(engine, defaultModel, true);
          activeModelSelect.style.display = (options.length === 0 && !defaultModel) ? 'none' : '';
          // syncSpawnEngineDependentUi only fills the primary composer's
          // select; a cloned split-pane bar kept whatever options it was
          // cloned with (often none), so its model name was blank. Mirror the
          // primary's options and pick, and route the pane's pick back to it
          // since the spawn path reads the primary select.
          const primaryModelSelect = document.getElementById('convInputModelSelect');
          if (primaryModelSelect && activeModelSelect !== primaryModelSelect) {
            const want = Array.from(primaryModelSelect.options).map(o => o.value + '\u0000' + o.textContent).join('\u0001');
            const have = Array.from(activeModelSelect.options).map(o => o.value + '\u0000' + o.textContent).join('\u0001');
            if (want !== have) {
              activeModelSelect.innerHTML = '';
              Array.from(primaryModelSelect.options).forEach(o => activeModelSelect.appendChild(o.cloneNode(true)));
            }
            if (activeModelSelect.value !== primaryModelSelect.value) activeModelSelect.value = primaryModelSelect.value;
            const _selOpt = activeModelSelect.options[activeModelSelect.selectedIndex];
            activeModelSelect.title = _selOpt ? (_selOpt.textContent || '') : '';
            if (!activeModelSelect.dataset.mirrorBound) {
              activeModelSelect.dataset.mirrorBound = '1';
              activeModelSelect.addEventListener('change', () => {
                primaryModelSelect.value = activeModelSelect.value;
                primaryModelSelect.dispatchEvent(new Event('change'));
              });
            }
          }
        }
      }
      if (activeEffortSelect) {
        // Every engine with a reasoning ladder, not just Codex.
        const spawnEngine = (0, helpers.getSpawnEngine)();
        const canPickEffort = (0, helpers.engineSupportsEffort)(spawnEngine);
        // syncSpawnEngineDependentUi rebuilds the primary bar's ladder; a
        // cloned pane bar has its own .effort-select that it never sees. Only
        // touch it when the rungs actually differ, so a repaint mid-typing
        // cannot reset a pick the user already made in that pane.
        if (canPickEffort && activeEffortSelect.id !== 'convInputEffortSelect') {
          const levels = (0, helpers.effortLevelsForEngine)(spawnEngine);
          const want = levels.map(lvl => lvl.id).join(',');
          const have = Array.from(activeEffortSelect.options).map(o => o.value).filter(Boolean).join(',');
          if (want !== have) (0, helpers.renderEffortOptions)(activeEffortSelect, levels, activeEffortSelect.value, 'Default effort');
        }
        activeEffortSelect.style.display = isNewSession && canPickEffort ? '' : 'none';
      }
      const pickerStrip = document.getElementById('convModelPickerStrip');
      // Another split pane may be the one composing the new session.
      const _stripOn = isNewSession || !!(0, helpers.newSessionPaneId)();
      if (pickerStrip) {
        pickerStrip.style.display = _stripOn ? 'flex' : 'none';
        pickerStrip.classList.toggle('is-new-session', _stripOn);
        if (isNewSession && typeof helpers.syncNsModelPickerPillsSelection === 'function') {
          (0, helpers.syncNsModelPickerPillsSelection)();
        }
      }
    } else {
      const pickerStrip = document.getElementById('convModelPickerStrip');
      if (pickerStrip && !(0, helpers.newSessionPaneId)()) {
        pickerStrip.style.display = 'none';
        pickerStrip.classList.remove('is-new-session');
      }
      _activeInputBar.classList.remove('visible');
      _activeInputBar.classList.remove('is-new-session-launch');
      if (activeInput) {
        activeInput.readOnly = false;
        activeInput.classList.remove('is-readonly');
      }
      if (activeSendBtn) {
        activeSendBtn.disabled = false;
        activeSendBtn.title = 'Send';
      }
      if (activeSteerBtn) {
        activeSteerBtn.classList.remove('visible');
        activeSteerBtn.disabled = true;
      }
      if (activeCompactBtn) {
        activeCompactBtn.classList.remove('visible');
        activeCompactBtn.disabled = true;
      }
      if (activeAnnouncedFromControl) activeAnnouncedFromControl.classList.remove('visible');
      if (activeCodexAppSrv) activeCodexAppSrv.classList.remove('visible', 'live');
    }
  }

  function composerUserEditRevision(input) {
    if (!input.__composerEditTracking) {
      input.__composerEditTracking = true;
      input.__composerEditRevision = 0;
      input.addEventListener('input', event => {
        if (event.isTrusted) input.__composerEditRevision++;
      });
    }
    return input.__composerEditRevision;
  }

  function clearAcceptedComposerInput(input, text, convId, revision) {
    // A draft restore can repopulate the textarea while the send is pending.
    // Retire only that submitted copy, preserving subsequent human edits and
    // composers that have moved to a different conversation.
    if (composerUserEditRevision(input) !== revision) return;
    const paneEl = input.closest('.conv-pane');
    const pane = paneEl && paneEl.dataset && (0, state.paneByPaneId)(paneEl.dataset.paneId);
    if ((pane ? pane.conversationId : state.currentConversation) !== convId) return;
    if ((input.value || '').trim() !== text) return;
    input.value = '';
    clearInputDraftForConversation(convId);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    _autosizeTextareaLike(input, input.closest('.conv-input-bar'));
  }

  function restoreInputAfterSendFailure($input, text) {
    if (!$input) return;
    if (($input.value || '').trim()) return;
    $input.value = text;
    moveInputCaretToEnd($input);
    $input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function _autosizeTextareaLike(el, barEl) {
    if (!el || el.tagName !== 'TEXTAREA') return;
    // iMessage-style composer on phones: CSS shows the mic in Send's slot
    // until the first character is typed (.has-text on the bar).
    if (barEl) barEl.classList.toggle('has-text', !!el.value);
    // Deferred to the next animation frame. The 'auto' write + scrollHeight
    // read is a forced synchronous reflow; doing it inline on every keystroke
    // re-lays-out the whole (large) page mid-keystroke — cheap in Blink, but
    // costly in WebKit (the Mac app), so typed characters paint late. Running
    // it in rAF takes it off the keystroke's critical path (char paints first,
    // textarea resizes a frame later — imperceptible) and coalesces bursts to
    // one reflow per frame.
    if (el._autosizeRaf) return;
    el._autosizeRaf = requestAnimationFrame(() => {
      el._autosizeRaf = 0;
      // The 'auto' write briefly collapses a multi-line draft, so the
      // conversation above grows and the browser clamps its scrollTop down
      // by the lost rows; once the height is restored the transcript sits
      // that far above the bottom until something re-pins it, flickering
      // the tail (status pill, Last/Previous) on every keystroke (CCC-223).
      const pane = el.closest('.conv-pane');
      const view = pane && pane.querySelector('.conversations-view');
      const viewTop = view ? view.scrollTop : 0;
      el.style.height = 'auto';
      const mode = (barEl && barEl.dataset.composerMode) || 'regular';
      const max = mode === 'expand' ? Math.round(window.innerHeight * 0.7)
        : 240;  // ~10 rows at our current font/line-height
      el.style.height = Math.min(el.scrollHeight, max) + 'px';
      if (view && view.scrollTop !== viewTop) view.scrollTop = viewTop;
      if (view) (0, helpers.positionConversationEndAffordance)(view);
      // A long paste can leave the caret below the capped box (iOS does not
      // re-scroll after the height changes). Typing/pasting at the end is the
      // common case: pin the scroll to the bottom so the caret stays visible.
      if (document.activeElement === el && el.selectionEnd === el.value.length) {
        el.scrollTop = el.scrollHeight;
      }
    });
  }

  function seedSessionInputContext(sid, row, paneId) {
    const oldSid = state._workspaceSessionIdByPane[paneId];
    if (oldSid) {
      state._sessionStripCache.delete(oldSid);
      state._sessionStripCache.set(oldSid, {
        workspace: state._workspaceDataByPane[paneId],
        usage: state._usageSessionIdByPane[paneId] === oldSid ? state._usageDataByPane[paneId] : null,
      });
      if (state._sessionStripCache.size > 50) state._sessionStripCache.delete(state._sessionStripCache.keys().next().value);
    }
    const cached = sid && state._sessionStripCache.get(sid);
    const cwd = row.cwd || row.spawn_cwd || state.sessionCwdByConv[row.id] || (0, helpers.rowRepoPath)(row);
    state._workspaceSessionIdByPane[paneId] = sid;
    state._workspaceDataByPane[paneId] = (cached && cached.workspace) || (cwd ? { cwd, _initial: true } : null);
    state._usageRequestGenerationByPane[paneId] = (state._usageRequestGenerationByPane[paneId] || 0) + 1;
    delete state._usageRequestByPane[paneId];
    if (state._usageSessionIdByPane[paneId] !== sid) delete state._usageLastFetchByPane[paneId];
    state._usageSessionIdByPane[paneId] = sid;
    state._usageDataByPane[paneId] = Object.assign({}, cached && cached.usage, row, {
      _initial: !(cached && cached.usage && !cached.usage._initial),
    });
    const slot = (0, helpers.getInputContextSlot)(paneId);
    const wsSlot = slot && slot.querySelector('[data-workspace]');
    if (wsSlot) wsSlot.innerHTML = '';
    (0, helpers.renderSessionWorkspaceIntoSticky)(paneId);
    (0, helpers.renderSessionUsageIntoStrip)(paneId);
    (0, helpers.syncInputContextVisibility)(slot);
  }

  function updateSplitInputBar() {
    if (!state.$convPanelInput) return;
    const isPkood = state.currentSession.source === 'pkood';
    const isCodex = state.currentSession.source === 'codex';
    // Codex cloud (dot/aeon) thread: read-only, same as the main composer.
    // Not gated on source: popout rows can carry 'interactive'.
    const isCodexCloud = !!state.currentSession.codex_cloud;
    const isGemini = state.currentSession.source === 'gemini';
    const isCursor = state.currentSession.source === 'cursor';
    const isAntigravity = state.currentSession.source === 'antigravity';
    const isHermes = state.currentSession.source === 'hermes';
    const isKimi = state.currentSession.source === 'kimi';
    const isOpencode = state.currentSession.source === 'opencode';
    const antigravityCanSendNow = (0, helpers.antigravityCanSend)(state.currentSession);
    // liveStatus lags a conversation switch by up to one poll — ignore it until
    // it matches the open conversation so we never show the prior session's tty.
    const ls = (0, helpers.liveStatusMatchesOpenConv)() ? state.liveStatus : {};
    const live = ls.live && ls.tty;
    const hasSession = !!state.currentSession.id;
    if (hasSession && state.kanbanView) {
      state.$convPanelInput.classList.add('visible');
      if (state.$cpTtyLabel) state.$cpTtyLabel.textContent = isPkood ? 'pkood' : (isCodexCloud ? 'cloud' : (isCodex ? (ls.tty || 'codex') : (isGemini ? (ls.tty || 'gemini') : (isCursor ? (ls.tty || 'cursor') : (isAntigravity ? (ls.tty || 'antigravity') : (isHermes ? 'hermes' : (isOpencode ? 'opencode' : (isKimi ? 'kimi' : (ls.tty || (live ? '' : 'offline'))))))))));
      if (state.$cpInput) {
        if (isPkood) state.$cpInput.placeholder = 'Send to pkood agent...';
        else if (isCodexCloud) state.$cpInput.placeholder = 'Codex cloud thread, read-only in CCC - reply in the Codex app…';
        else if (isCodex) state.$cpInput.placeholder = live ? 'Send to Codex terminal...' : 'Resume Codex and send...';
        else if (isGemini) state.$cpInput.placeholder = live ? 'Send to Gemini terminal...' : 'Resume Gemini and send...';
        else if (isCursor) state.$cpInput.placeholder = live ? 'Send to Cursor terminal...' : 'Resume Cursor and send...';
        else if (isAntigravity) state.$cpInput.placeholder = (0, helpers.antigravityInputPlaceholder)(state.currentSession);
        else if (isHermes) state.$cpInput.placeholder = 'Resume Hermes and send...';
        else if (isOpencode) state.$cpInput.placeholder = 'Resume OpenCode and send...';
        else if (isKimi) state.$cpInput.placeholder = ls.live ? 'Send to Kimi session…' : 'Resume Kimi and send…';
        else if (live) state.$cpInput.placeholder = 'Send to terminal...';
        else state.$cpInput.placeholder = 'Send to terminal (offline)...';
        const readOnly = isCodexCloud || (isAntigravity && !antigravityCanSendNow);
        state.$cpInput.readOnly = readOnly;
        state.$cpInput.classList.toggle('is-readonly', readOnly);
      }
    } else {
      state.$convPanelInput.classList.remove('visible');
      if (state.$cpInput) {
        state.$cpInput.readOnly = false;
        state.$cpInput.classList.remove('is-readonly');
      }
    }
    // Enable/disable the send button based on new session state.
    if (state.$cpInput && state.$cpInput.__cpRefresh) state.$cpInput.__cpRefresh();
  }

  function guardComposerSend(el) {
    if (!(0, helpers.composerUploadIsPending)(el)) return true;
    if (typeof helpers.showOpToast === 'function') {
      (0, helpers.showOpToast)('Waiting for attachment upload to finish.', 'info');
    }
    return false;
  }
  function initConversationActionsMenu() {
    const btn = document.getElementById('convActionsBtn');
    const menu = document.getElementById('convActionsMenu');
    if (!btn || !menu) return;
    const bar = btn.closest('.conv-input-bar');
    // Container-width trigger (not viewport): an app pane is a narrow box in a
    // wide window. Embedded panes always use the menu.
    if (bar) {
      const NARROW_PX = 600;
      const apply = () => {
        const embedded = document.body.classList.contains('ccc-app-embed');
        const w = bar.getBoundingClientRect().width;
        bar.classList.toggle('actions-in-menu', embedded || (w > 0 && w < NARROW_PX));
      };
      apply();
      if (typeof ResizeObserver === 'function') new ResizeObserver(apply).observe(bar);
      else window.addEventListener('resize', apply);
    }
    const hide = () => { menu.style.display = 'none'; btn.setAttribute('aria-expanded', 'false'); };
    btn.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      const opening = menu.style.display === 'none';
      if (opening) {
        // Touch keeps Esc in this menu (CSS hides the composer button); offer
        // it whenever the Esc button itself would be live.
        const escItem = menu.querySelector('[data-act="esc"]');
        if (escItem) escItem.hidden = !(state.$convEscBtn && state.$convEscBtn.style.display !== 'none' && !state.$convEscBtn.disabled);
        // Compact and Steer only move into this menu when the composer is in
        // actions-in-menu mode (narrow pane / app embed); show them while their
        // own button would be live.
        const inMenu = !!(bar && bar.classList.contains('actions-in-menu'));
        const live = (b) => !!(b && b.classList.contains('visible') && !b.disabled);
        const cItem = menu.querySelector('[data-act="compact"]');
        if (cItem) cItem.hidden = !(inMenu && live(state.$convCompactBtn) && !state.$convCompactBtn.classList.contains('in-header'));
        const sItem = menu.querySelector('[data-act="steer"]');
        if (sItem) sItem.hidden = !(inMenu && live(state.$convSteerBtn));
      }
      menu.style.display = opening ? 'block' : 'none';
      btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
    });
    menu.addEventListener('click', (ev) => {
      const item = ev.target.closest && ev.target.closest('[data-act]');
      if (!item) return;
      hide();
      if (item.dataset.act === 'esc') helpers.sendEscToTerminal();
      else if (item.dataset.act === 'compact') helpers.compactCurrentSession();
      else if (item.dataset.act === 'steer') helpers.sendToTerminal('p1', 'steer');
      else if (item.dataset.act === 'attach') { const i = document.getElementById('convAttachInput'); if (i) i.click(); }
      else if (item.dataset.act === 'continue') { const c = document.querySelector('#convSendMenu .continue-new-btn'); if (c) c.click(); }
    });
    document.addEventListener('click', (ev) => {
      if (menu.style.display === 'none') return;
      const group = menu.closest('.conv-actions-group');
      if (group && group.contains(ev.target)) return;
      hide();
    });
  }


    return { initConversationActionsMenu, setCurrentSession, inputControlsForBar, saveInputDrafts, scheduleSaveInputDrafts, flushSaveInputDrafts, clearInputDraftKeyCache, inputDraftKeyForConversation, setInputDraftForKey, clearInputDraftForConversation, inputDraftForConversation, rememberInputDraft, loadComposerHistory, saveComposerHistory, rememberComposerCommand, recallLastComposerCommand, composerInputForPane, announcedFromInputForPane, announcedFromForPane, announcedInjectionPreview, rememberComposerDraftForPane, restoreInputDraft, moveInputCaretToEnd, restoreComposerDraftForPane, restoreSplitPanelDraft, issueInputPlaceholder, currentConversationRow, updateInputBar, composerUserEditRevision, clearAcceptedComposerInput, restoreInputAfterSendFailure, _autosizeTextareaLike, seedSessionInputContext, updateSplitInputBar, guardComposerSend };
  }
  root.CCCConversationInput = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
