/* Shared conversation controls with explicit live-state and host adapters. */
(function (root) {
  function createController({ state: sharedState, helpers: hostServices }) {
  function f2ClearComposer() {
    try {
      const el = document.getElementById('convInput');
      if (el) el.value = '';
      if (typeof sharedState.updateInputBar === 'function') (0, sharedState.updateInputBar)();
    } catch (_) {}
  }

  function f2RetrievalPrompt(ctx, gate) {
    const sid = ctx.sid;
    const task = String(ctx.text || '').trim();
    const worthSelectiveRetrieval = gate.tokens >= sharedState.F2_TOKEN_THRESHOLD;
    const locate = sharedState.F2_ENGINE_LOCATE[gate.engine] || sharedState.F2_ENGINE_LOCATE.claude;
    const lines = [
      'You are continuing a task from an earlier ' + locate.label + ' session'
        + (worthSelectiveRetrieval ? ', which ran long' : '') + '.',
      '',
      'Origin session id: ' + sid,
      locate.note,
      locate.cmd(sid),
      '',
      'Task: ' + task,
      '',
    ];
    if (worthSelectiveRetrieval) {
      lines.push(
        'Retrieve context SELECTIVELY. Never open or Read the whole transcript',
        '(it is ~' + (0, sharedState.f2FmtTokens)(gate.tokens) + ' tokens). Pull only the slice you need:',
        '  - run `ccc brief ' + sid + '` first - it already has the last asks, last',
        '    reply, commits, and files touched without reading the transcript',
        '  - tail -n 80 the transcript for the most recent turns (cut by',
        '    lines, not bytes: a byte cut splits a JSON line), then jq the',
        '    message text',
        '  - grep/rg the transcript for specific strings',
        '  - ccc recall "<terms>" / ccc shipped "<topic>" for related work',
        '',
        'Load the minimum slice that answers the task, then proceed.',
      );
    } else {
      lines.push(
        'The transcript is small (~' + (0, sharedState.f2FmtTokens)(gate.tokens) + ' tokens). Read it directly for context, then proceed.',
      );
    }
    return lines.join('\n');
  }

  async function f2RunContinue(paneId, st, btn) {
    const input = (0, sharedState.composerInputForPane)(paneId) || sharedState.$convInput;
    if (!(0, sharedState.guardComposerSend)(input)) return;
    if (btn) btn.disabled = true;
    const sid = st.sid;
    const paneKey = (0, sharedState.f2PaneKey)(paneId);
    const ctx = Object.assign({}, st.ctx || {}, { text: st.promptEdited ? st.promptDraft : (0, sharedState.f2ComposerText)(paneId) });
    const launch = st.launch;
    // The parent row already has the human-facing title. Keep it on the
    // continuation instead of exposing an opaque session-id fragment. No
    // "Continue " prefix: the row's ⤴ badge (keyed off the Origin-marker
    // continuation edge, not the name) carries that meaning now.
    const parentTitle = (0, sharedState.sidebarRowDisplayTitle)(
      (ctx.row && (ctx.row.display_name || ctx.row.name || ctx.row.title)) || ''
    ) || 'session';
    const subject = parentTitle;
    const tempPid = 'tmp-f2-' + Date.now();
    try {
      const cwd = (0, sharedState.f2ResolveSpawnCwd)(ctx);
      // The chip already told the user "Launches on <engine> <model> at
      // <effort> effort". Send the whole spec, or that sentence is a lie for
      // every engine whose effort we used to drop on the floor.
      const body = buildSpawnBody({
        engine: launch.engine,
        model: launch.model,
        effort: launch.effort,
        prompt: f2RetrievalPrompt(ctx, st.gate),
        name: subject,
        cwd,
        repoPath: cwd,
      });
      body.parent_session_id = sid;
      let endpoint = '/api/sessions/spawn';
      try { if (typeof spawnEndpointForEngine === 'function') endpoint = spawnEndpointForEngine(launch.engine); } catch (_) {}
      // Continue New is a navigation action as well as a spawn. Use the
      // ordinary pending-card handoff so this pane moves immediately, then
      // lets the existing placeholder reconciliation bind the real session.
      const source = (typeof sharedState.spawnSourceForEngine === 'function')
        ? (0, sharedState.spawnSourceForEngine)(launch.engine) : launch.engine;
      (0, hostServices.insertPendingSpawnCard)(tempPid, subject, source, null, {
        first_message: body.prompt,
        repo_path: cwd || '',
        folder_path: cwd || '',
        spawn_cwd: cwd || '',
        cwd: cwd || '',
        session_cwd: cwd || '',
        session_cwd_exists: !!cwd,
        model: launch.model || '',
        reasoning_effort: launch.effort || '',
        parent_session_id: sid,
        spawn_composer_text: ctx.text || '',
        // The exact request, so a failed spawn's Retry re-runs it verbatim
        // (prompt/engine/model/effort/cwd/origin all preserved) instead of
        // routing through whatever the global spawn controls show by then.
        spawn_endpoint: endpoint,
        spawn_body: body,
      });
      const r = await fetch(endpoint, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await r.json().catch(() => ({}));
      if (r.ok && d.ok !== false && (d.session_id || d.spawn_id || d.pid)) {
        // Only now unpin the manual Continue panel: on failure the pin must
        // survive so going back to the origin session reopens the panel
        // exactly as it was (OPS-935).
        sharedState.f2ManualPanes.delete(paneKey);
        st.promptEdited = false; st.promptDraft = '';
        const newSid = d.session_id || '';
        const spawnId = d.spawn_id || d.pid || '';
        const placeholder = spawnId
          ? (0, sharedState.adoptPendingSpawnPid)(tempPid, spawnId, d.log, newSid)
          : sharedState.pendingSpawns.get(tempPid);
        if (placeholder && newSid) placeholder.expected_session_id = newSid;
        (0, sharedState.f2RecordContinuationLineage)(sid, newSid);
        if (launch.engine === 'codex') {
          (0, sharedState._watchCodexSpawnRegistration)(
            spawnId || tempPid,
            'spawning-' + tempPid,
            newSid,
          );
        }
      if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Continuing in a new session. It will pull only the slice it needs.', 'success');
        f2ClearComposer();
        setTimeout(hostServices.refreshConversationList, 600);
        // ...then chase with a cheap probe rather than two more full-corpus
        // refreshes: for Codex the engine has written nothing at 3s, and each
        // of those refreshes is a multi-megabyte fetch of every row we have.
        (0, sharedState.chasePendingSpawn)(tempPid, { sessionId: d && d.session_id });
      } else {
        // Immediate rejection: keep a persistent failed card with the real
        // error and Retry/Edit/Dismiss controls instead of deleting the
        // placeholder and freezing the pane on its ghost id (OPS-935).
        (0, sharedState._failPendingSpawnCard)(tempPid, 'Spawn failed: ' + ((d && d.error) || ('HTTP ' + r.status)));
        if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Spawn failed: ' + ((d && d.error) || ('HTTP ' + r.status)), 'error');
      }
    } catch (e) {
      (0, sharedState._failPendingSpawnCard)(tempPid, 'Spawn failed: ' + ((e && e.message) || 'network'));
      if (typeof sharedState.showOpToast === 'function') (0, sharedState.showOpToast)('Spawn failed: ' + ((e && e.message) || 'network'), 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function f2GateStateForPane(paneId) {
    const st = sharedState.f2PaneState.get((0, sharedState.f2PaneKey)(paneId));
    if (!st || !st.gate || st.gate.manual || st.gate.idleOnly) return null;
    return st;
  }

  function f2ConfirmStaleSend(st) {
    return new Promise((resolve) => {
      const gate = st.gate;
      const tokensLabel = (0, sharedState.f2FmtTokens)(gate.tokens);
      const ageLabel = (0, sharedState.f2FmtAge)(gate.ageMin);
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:10000;';
      const box = document.createElement('div');
      box.style.cssText = 'background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:20px 24px;max-width:440px;width:90%;box-shadow:0 8px 24px rgba(0,0,0,0.4);';
      const title = document.createElement('div');
      title.style.cssText = 'font-size:14px;font-weight:600;color:var(--text);margin-bottom:8px;';
      title.textContent = 'Large and stale session';
      const body = document.createElement('div');
      body.style.cssText = 'font-size:13px;color:var(--text-muted);margin-bottom:16px;line-height:1.5;';
      body.textContent = 'Idle ' + ageLabel + ' with ~' + tokensLabel + ' tokens of context'
        + (gate.cache && gate.cache.verdictNote ? ' - ' + gate.cache.verdictNote + '.' : '. Resuming here likely reloads most of it.');
      const btnRow = document.createElement('div');
      btnRow.style.cssText = 'display:flex;gap:8px;justify-content:flex-end;';
      const mkBtn = (label, kind) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.textContent = label;
        const isPrimary = kind === 'primary';
        btn.style.cssText = 'cursor:pointer;border-radius:4px;padding:6px 14px;font-size:12px;border:1px solid ' + (isPrimary ? 'var(--accent, #5b8def)' : 'var(--border)') + ';background:' + (isPrimary ? 'var(--accent, #5b8def)' : 'transparent') + ';color:' + (isPrimary ? 'var(--button-text, #fff)' : 'var(--text)') + ';';
        return btn;
      };
      const newBtn = mkBtn('Start new session', 'primary');
      const hereBtn = mkBtn('Send to this session', 'secondary');
      const cancelBtn = mkBtn('Cancel', 'secondary');
      const cleanup = (val) => { overlay.remove(); resolve(val); };
      newBtn.addEventListener('click', () => cleanup('new'));
      hereBtn.addEventListener('click', () => cleanup('here'));
      cancelBtn.addEventListener('click', () => cleanup(null));
      overlay.addEventListener('click', (e) => { if (e.target === overlay) cleanup(null); });
      btnRow.appendChild(cancelBtn);
      btnRow.appendChild(hereBtn);
      btnRow.appendChild(newBtn);
      box.appendChild(title);
      box.appendChild(body);
      box.appendChild(btnRow);
      overlay.appendChild(box);
      document.body.appendChild(overlay);
      newBtn.focus();
    });
  }

  function f2InterceptEnterSend(paneId) {
    const st = f2GateStateForPane(paneId);
    if (!st) return false;
    f2ConfirmStaleSend(st).then((choice) => {
      if (choice === 'here') (0, sharedState.sendToTerminal)(paneId);
      else if (choice === 'new') f2RunContinue(paneId, st, null);
    });
    return true;
  }

  function allResumeButtons() { return []; }

  function allLaunchChoiceButtons() { return [sharedState.$launchChoiceBtnConv, document.getElementById('cpLaunchChoiceBtn')].filter(Boolean); }

  function allLaunchChoiceMenus() { return [sharedState.$launchChoiceMenuConv, document.getElementById('cpLaunchChoiceMenu')].filter(Boolean); }

  function allDesktopButtons() { return []; }

  function positionLaunchChoiceMenu(menu) {
    if (!menu) return;
    const rect = menu.getBoundingClientRect();
    // A Launch control can live at the bottom of a narrow window. Keep its
    // targets inside the dashboard instead of rendering them below the edge.
    menu.classList.toggle('opens-up', rect.bottom > window.innerHeight && rect.top > rect.height);
  }

  function closeLaunchChoiceMenus() {
    for (const menu of allLaunchChoiceMenus()) {
      menu.classList.remove('open');
      menu.classList.remove('opens-up');
      menu.setAttribute('aria-hidden', 'true');
    }
    for (const btn of allLaunchChoiceButtons()) {
      btn.setAttribute('aria-expanded', 'false');
    }
  }

  function toggleLaunchChoiceMenu(btn, menu) {
    if (!btn || !menu) return;
    const willOpen = !menu.classList.contains('open');
    closeLaunchChoiceMenus();
    if (!willOpen) return;
    (0, sharedState.renderLaunchChoiceMenu)(menu);
    menu.classList.add('open');
    positionLaunchChoiceMenu(menu);
    menu.setAttribute('aria-hidden', 'false');
    btn.setAttribute('aria-expanded', 'true');
  }

  function launchButtonFromTrigger(trigger) {
    if (trigger && trigger.closest && trigger.closest('#cpLaunchSplit')) {
      return document.getElementById('cpLaunchBtn');
    }
    return sharedState.$launchBtnConv;
  }

  async function launchTarget(target, trigger) {
    const btn = launchButtonFromTrigger(trigger);
    closeLaunchChoiceMenus();
    if (target === 'desktop') {
      await openInClaudeDesktop({ currentTarget: btn });
      return;
    }
    if (target === 'codex') {
      await openInCodexDesktop({ currentTarget: btn });
      return;
    }
    await (0, sharedState.launchTerminal)({ currentTarget: btn });
  }

  async function copyResumeCommand(ev) {
    const btn = ev && ev.currentTarget;
    if (!btn) return;
    const sid = btn.dataset.sessionId;
    if (!sid) return;
    const cmd = btn.dataset.resumeCmd || ('claude --resume ' + sid);
    try {
      await navigator.clipboard.writeText(cmd);
    } catch (err) {
      // Fallback for non-secure contexts
      const ta = document.createElement('textarea');
      ta.value = cmd;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch (_) {}
      document.body.removeChild(ta);
    }
    btn.classList.add('copied');
    const originalLabel = btn.querySelector('.resume-label').textContent;
    btn.querySelector('.resume-label').textContent = 'Copied!';
    setTimeout(() => {
      btn.classList.remove('copied');
      btn.querySelector('.resume-label').textContent = originalLabel;
    }, 1500);
  }

  async function jumpToTerminal(ev) {
    const btn = ev && ev.currentTarget;
    if (!btn) return;
    if (!sharedState.liveStatus.live) return;
    const origLabel = btn.querySelector('.jump-label').textContent;
    btn.querySelector('.jump-label').textContent = 'Jumping...';
    try {
      const res = await fetch('/api/jump-terminal', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          session_id: sharedState.currentSession.id,
          cwd: sharedState.currentSession.cwd,
          tty: sharedState.liveStatus.tty,
          terminal_app: sharedState.liveStatus.terminalApp,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        btn.querySelector('.jump-label').textContent = 'Focused!';
        setTimeout(() => { btn.querySelector('.jump-label').textContent = origLabel; }, 1500);
      } else {
        btn.querySelector('.jump-label').textContent = 'Failed: ' + (data.error || 'unknown');
        setTimeout(() => { btn.querySelector('.jump-label').textContent = origLabel; }, 3000);
      }
    } catch (err) {
      btn.querySelector('.jump-label').textContent = 'Error';
      setTimeout(() => { btn.querySelector('.jump-label').textContent = origLabel; }, 2000);
    }
  }

  async function openInClaudeDesktop(ev) {
    const btn = ev && ev.currentTarget;
    if (!btn || !sharedState.currentSession.id) return;
    if (sharedState.currentSession.source === 'codex' || sharedState.currentSession.source === 'gemini' || sharedState.currentSession.source === 'cursor' || sharedState.currentSession.source === 'antigravity') return;
    const snapshot = (0, sharedState.actionButtonSnapshot)(btn);
    btn.classList.add('opening');
    btn.disabled = true;
    (0, sharedState.setActionButtonText)(btn, 'Opening...');
    try {
      const res = await fetch('/api/open-in-desktop', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: sharedState.currentSession.id }),
      });
      const data = await res.json();
      if (data.ok) {
        (0, sharedState.setActionButtonText)(btn, 'Opened!');
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.disabled = false;
          (0, sharedState.restoreActionButtonText)(btn, snapshot);
        }, 1500);
      } else {
        (0, sharedState.setActionButtonText)(btn, 'Failed: ' + (data.error || 'unknown'));
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.disabled = false;
          (0, sharedState.restoreActionButtonText)(btn, snapshot);
        }, 3000);
      }
    } catch (err) {
      btn.classList.remove('opening');
      (0, sharedState.setActionButtonText)(btn, 'Error');
      setTimeout(() => {
        btn.disabled = false;
        (0, sharedState.restoreActionButtonText)(btn, snapshot);
      }, 2000);
    }
  }

  async function openInCodexDesktop(ev) {
    const btn = ev && ev.currentTarget;
    if (!btn || !sharedState.currentSession.id) return;
    if (sharedState.currentSession.source !== 'codex') return;
    const snapshot = (0, sharedState.actionButtonSnapshot)(btn);
    btn.classList.add('opening');
    btn.disabled = true;
    (0, sharedState.setActionButtonText)(btn, 'Opening...');
    try {
      const res = await fetch('/api/open-in-codex', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: sharedState.currentSession.id, cwd: sharedState.currentSession.cwd }),
      });
      const data = await res.json();
      if (data.ok) {
        (0, sharedState.setActionButtonText)(btn, 'Opened!');
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.disabled = false;
          (0, sharedState.restoreActionButtonText)(btn, snapshot);
        }, 1500);
      } else {
        (0, sharedState.setActionButtonText)(btn, 'Failed: ' + (data.error || 'unknown'));
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.disabled = false;
          (0, sharedState.restoreActionButtonText)(btn, snapshot);
        }, 3000);
      }
    } catch (err) {
      btn.classList.remove('opening');
      (0, sharedState.setActionButtonText)(btn, 'Error');
      setTimeout(() => {
        btn.disabled = false;
        (0, sharedState.restoreActionButtonText)(btn, snapshot);
      }, 2000);
    }
  }

  function slashQueryForInput(input) {
    if (!input) return null;
    const value = input.value || '';
    const pos = input.selectionStart == null ? value.length : input.selectionStart;
    if (pos !== value.length) return null;
    if (!value.startsWith('/')) return null;
    if (/\s/.test(value)) return null;
    return value.slice(1).toLowerCase();
  }

  function activatePaneFromComposer(input) {
    const pane = input && input.closest && input.closest('.conv-pane[data-pane-id]');
    const paneId = pane && pane.getAttribute('data-pane-id');
    if (!paneId) return;
    (0, sharedState.setActivePaneById)(paneId);
  }

  function slashFallbackCommandsForSource(source) {
    if (source === 'codex') return sharedState.CODEX_SLASH_FALLBACK_COMMANDS;
    if (source === 'antigravity') {
      return [
        { name: '/autocompact', description: 'Open the auto-compact threshold setting' },
        { name: '/compact', description: 'Summarize the conversation to free up context' },
      ];
    }
    // Kimi/Grok expose their own ACP-reported catalog (fetched below) —
    // Claude's fallback list would show commands like /context that don't
    // exist for those engines, so start empty rather than misleading.
    if (source === 'kimi' || source === 'grok') return [];
    return sharedState.SLASH_FALLBACK_COMMANDS;
  }

  function slashCommandUnavailableReason() {
    if ((sharedState.currentConversation || '').startsWith('backlog-issue-')) return 'Issue actions do not use slash commands';
    if (sharedState.conversationsData.some(x => x.id === sharedState.currentConversation && x.source === 'backlog')) return 'Issue actions do not use slash commands';
    const sess = (0, sharedState.activeConvSession)();
    const source = sess && sess.source;
    if (source === 'gemini') return 'Slash commands are not wired for Gemini sessions';
    if (source === 'cursor') return 'Slash commands are not wired for Cursor sessions';
    if (source === 'pkood') return 'pkood agents do not use slash commands';
    if (sharedState.currentConversation === '__new__') {
      const engine = (typeof sharedState.getSpawnEngine === 'function') ? (0, sharedState.getSpawnEngine)() : 'claude';
      return (engine === 'claude' || engine === 'codex')
        ? ''
        : 'Slash commands are not wired for ' + engine + ' sessions';
    }
    return (sess && sess.id) ? '' : 'Select a session first';
  }

  async function slashCommandsForCurrentContext() {
    const unavailable = slashCommandUnavailableReason();
    if (unavailable) {
      return [{ name: '/slash', description: unavailable, disabled: true }];
    }
    if (sharedState.currentConversation === '__new__') {
      const engine = (typeof sharedState.getSpawnEngine === 'function') ? (0, sharedState.getSpawnEngine)() : 'claude';
      return slashFallbackCommandsForSource(engine);
    }
    const sess = (0, sharedState.activeConvSession)();
    const sid = sess && sess.id;
    if (!sid) return [];
    if (sharedState._slashCommandCache.has(sid)) return sharedState._slashCommandCache.get(sid);
    let commands = slashFallbackCommandsForSource(sess && sess.source);
    try {
      const res = await fetch('/api/session/' + encodeURIComponent(sid) + '/slash-commands', { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      // The local server can misidentify an Antigravity conversation owned by a peer.
      if (res.ok && data && (sess.source !== 'antigravity' || data.engine === 'antigravity')
          && Array.isArray(data.commands) && data.commands.length) {
        commands = data.commands
          .map(c => ({
            name: String((c && c.name) || '').trim(),
            description: String((c && c.description) || '').trim(),
          }))
          .filter(c => c.name.startsWith('/'));
      }
    } catch (_) {}
    sharedState._slashCommandCache.set(sid, commands);
    return commands;
  }

  function positionSlashCommandMenu(input) {
    if (!sharedState._slashMenuEl || !input) return;
    const rect = input.getBoundingClientRect();
    const width = Math.min(Math.max(rect.width, 260), 520);
    sharedState._slashMenuEl.style.width = width + 'px';
    sharedState._slashMenuEl.style.left = Math.max(8, Math.min(rect.left, window.innerWidth - width - 8)) + 'px';
    const below = rect.bottom + 6;
    const above = rect.top - sharedState._slashMenuEl.offsetHeight - 6;
    sharedState._slashMenuEl.style.top = (below + sharedState._slashMenuEl.offsetHeight < window.innerHeight || above < 8
      ? below
      : above) + 'px';
  }

  function selectSlashCommandMenuItemFromEvent(ev) {
    const target = ev && ev.target;
    const el = target && target.closest ? target : (target && target.parentElement);
    const btn = el && el.closest ? el.closest('.slash-command-item') : null;
    if (!btn || !sharedState._slashMenuEl || !sharedState._slashMenuEl.contains(btn)) return false;
    ev.preventDefault();
    ev.stopPropagation();
    const idx = parseInt(btn.dataset.idx || '0', 10);
    if (Number.isFinite(idx)) {
      sharedState._slashMenuIndex = Math.max(0, Math.min(idx, sharedState._slashMenuItems.length - 1));
    }
    return commitSlashCommandSelection(sharedState._slashMenuInput);
  }

  function syncSlashCommandMenuSelection() {
    if (!sharedState._slashMenuEl) return;
    let selectedBtn = null;
    sharedState._slashMenuEl.querySelectorAll('.slash-command-item').forEach(btn => {
      const idx = parseInt(btn.dataset.idx || '0', 10);
      const selected = Number.isFinite(idx) && idx === sharedState._slashMenuIndex;
      btn.classList.toggle('selected', selected);
      if (selected) selectedBtn = btn;
    });
    if (selectedBtn && selectedBtn.scrollIntoView) {
      selectedBtn.scrollIntoView({ block: 'nearest' });
    }
  }

  function renderSlashCommandMenu(input, commands, query) {
    const q = (query || '').toLowerCase();
    // Score each candidate so name matches beat description matches.
    // Without this, typing "/context" was ranked equal between /context
    // (name match) and /compact (whose description "Compact conversation
    // CONTEXT" includes the substring), and the alphabetically-first one
    // (/compact) won by accident. Lower score = better.
    const scored = (commands || []).reduce((acc, c) => {
      const name = (c.name || '').toLowerCase();
      const nameNoSlash = name.startsWith('/') ? name.slice(1) : name;
      const desc = (c.description || '').toLowerCase();
      let score = -1;
      if (!q) {
        score = 9;
      } else if (nameNoSlash === q) {
        score = 0;                                 // exact name (sans slash)
      } else if (nameNoSlash.startsWith(q)) {
        score = 1;                                 // name prefix - strongest fuzzy
      } else if (name.includes(q)) {
        score = 2;                                 // name substring
      } else if (desc.includes(q)) {
        score = 3;                                 // description substring
      }
      if (score >= 0) acc.push({ cmd: c, score });
      return acc;
    }, []);
    scored.sort((a, b) => {
      if (a.score !== b.score) return a.score - b.score;
      return (a.cmd.name || '').localeCompare(b.cmd.name || '');
    });
    const matches = scored.map(x => x.cmd);
    if (!matches.length) {
      (0, sharedState.hideSlashCommandMenu)();
      return;
    }
    if (!sharedState._slashMenuEl) {
      sharedState._slashMenuEl = document.createElement('div');
      sharedState._slashMenuEl.className = 'slash-command-menu';
      sharedState._slashMenuEl.addEventListener('pointerdown', (ev) => {
        if (!selectSlashCommandMenuItemFromEvent(ev)) ev.preventDefault();
      });
      sharedState._slashMenuEl.addEventListener('mousedown', (ev) => {
        if (!selectSlashCommandMenuItemFromEvent(ev)) ev.preventDefault();
      });
      sharedState._slashMenuEl.addEventListener('touchstart', (ev) => {
        if (selectSlashCommandMenuItemFromEvent(ev)) return;
      }, { passive: false });
      sharedState._slashMenuEl.addEventListener('click', (ev) => {
        selectSlashCommandMenuItemFromEvent(ev);
      });
      document.body.appendChild(sharedState._slashMenuEl);
    }
    sharedState._slashMenuInput = input;
    sharedState._slashMenuItems = matches;
    sharedState._slashMenuIndex = Math.min(sharedState._slashMenuIndex, matches.length - 1);
    sharedState._slashMenuEl.innerHTML = matches.map((cmd, idx) => (
      '<button type="button" class="slash-command-item'
      + (idx === sharedState._slashMenuIndex ? ' selected' : '')
      + (cmd.disabled ? ' disabled' : '')
      + '" data-idx="' + idx + '">'
      + '<span class="slash-command-name">' + (0, sharedState.escapeHtml)(cmd.name || '') + '</span>'
      + (cmd.description ? '<span class="slash-command-desc">' + (0, sharedState.escapeHtml)(cmd.description) + '</span>' : '')
      + '</button>'
    )).join('');
    sharedState._slashMenuEl.querySelectorAll('.slash-command-item').forEach(btn => {
      btn.addEventListener('mouseenter', () => {
        const idx = parseInt(btn.dataset.idx || '0', 10);
        if (Number.isFinite(idx)) sharedState._slashMenuIndex = idx;
        syncSlashCommandMenuSelection();
      });
      btn.addEventListener('click', () => commitSlashCommandSelection(input));
    });
    positionSlashCommandMenu(input);
  }

  async function refreshSlashCommandMenu(input) {
    const query = slashQueryForInput(input);
    const req = ++sharedState._slashMenuReq;
    if (query == null) {
      (0, sharedState.hideSlashCommandMenu)();
      return;
    }
    activatePaneFromComposer(input);
    const commands = await slashCommandsForCurrentContext();
    if (req !== sharedState._slashMenuReq) return;
    renderSlashCommandMenu(input, commands, query);
  }

  function commitSlashCommandSelection(input) {
    if (!sharedState._slashMenuItems.length || !input) return false;
    const selected = sharedState._slashMenuItems[sharedState._slashMenuIndex] || sharedState._slashMenuItems[0];
    if (!selected || !selected.name) return false;
    if (selected.disabled) return false;
    input.value = selected.name + ' ';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.focus();
    (0, sharedState.hideSlashCommandMenu)();
    return true;
  }

  function moveSlashCommandSelection(delta) {
    if (!sharedState._slashMenuEl || !sharedState._slashMenuItems.length) return false;
    sharedState._slashMenuIndex = (sharedState._slashMenuIndex + delta + sharedState._slashMenuItems.length) % sharedState._slashMenuItems.length;
    syncSlashCommandMenuSelection();
    return true;
  }

  function handleSlashCommandKeydown(input, ev) {
    if ((0, sharedState.isImeKey)(ev)) return false;
    if (!sharedState._slashMenuEl || sharedState._slashMenuInput !== input) return false;
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      moveSlashCommandSelection(1);
      return true;
    }
    if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      moveSlashCommandSelection(-1);
      return true;
    }
    if (ev.key === 'Tab') {
      ev.preventDefault();
      return commitSlashCommandSelection(input);
    }
    if (ev.key === 'Escape') {
      ev.preventDefault();
      (0, sharedState.hideSlashCommandMenu)();
      return true;
    }
    if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
      const query = slashQueryForInput(input);
      const selected = sharedState._slashMenuItems[sharedState._slashMenuIndex] || sharedState._slashMenuItems[0];
      if (selected && selected.disabled) return false;
      const selectedName = selected && selected.name ? selected.name.slice(1).toLowerCase() : '';
      if (!query || (selectedName && query !== selectedName)) {
        ev.preventDefault();
        return commitSlashCommandSelection(input);
      }
    }
    return false;
  }

  function _armPhoneModeRead(paneId, sessionId) {
    sharedState._phoneModePending = {
      paneId: paneId,
      convId: sharedState.currentConversation,
      sessionId: sessionId || (sharedState.currentSession && sharedState.currentSession.id) || sharedState.sessionIdByConv[sharedState.currentConversation] || sharedState.currentConversation,
      baselineLine: null,   // tail cursor captured at arm time; set async below
      fired: false,
    };
    if (sharedState._phoneModeTimer) clearTimeout(sharedState._phoneModeTimer);
    if (sharedState._phoneModeSettleTimer) { clearTimeout(sharedState._phoneModeSettleTimer); sharedState._phoneModeSettleTimer = null; }
    if (sharedState._phoneModePollTimer) { clearInterval(sharedState._phoneModePollTimer); sharedState._phoneModePollTimer = null; }
    // Safety disarm: if the turn never completes, don't blurt out a reply
    // minutes later for a session the user has moved on from.
    sharedState._phoneModeTimer = setTimeout(() => { (0, sharedState._disarmPhoneModeRead)(); }, 240000);
    // Capture a baseline so the background poll can tell "new reply after we
    // armed" from "the reply that was already there". Then start the poll —
    // it fires the read even when the user has navigated AWAY from this
    // conversation (the focused-render path, _firePhoneModeReadIfDone, only
    // works while the Submit+ conversation stays focused).
    _armPhoneModeBackgroundPoll(sharedState._phoneModePending);
  }

  async function _armPhoneModeBackgroundPoll(pend) {
    const sid = pend && pend.sessionId;
    if (!sid) return;
    // Baseline the current tail so we only react to a NEW assistant turn.
    try {
      const res = await fetch('/api/conversations/' + encodeURIComponent(sid) + '?tail=1');
      const data = await res.json();
      if (sharedState._phoneModePending === pend) pend.baselineLine = (data && typeof data.last_line === 'number') ? data.last_line : 0;
    } catch (_) {
      if (sharedState._phoneModePending === pend) pend.baselineLine = 0;
    }
    if (sharedState._phoneModePending !== pend) return;  // superseded / disarmed while baselining
    if (sharedState._phoneModePollTimer) clearInterval(sharedState._phoneModePollTimer);
    sharedState._phoneModePollTimer = setInterval(() => { _phoneModeBackgroundPollTick(pend); }, 2000);
  }

  async function _phoneModeBackgroundPollTick(pend) {
    if (sharedState._phoneModePending !== pend || pend.fired) return;
    // While focused, defer to the render-driven path entirely.
    if (pend.convId === sharedState.currentConversation) return;
    const sid = pend.sessionId;
    if (!sid) return;
    let data;
    try {
      const after = (typeof pend.baselineLine === 'number') ? pend.baselineLine : 0;
      const res = await fetch('/api/conversations/' + encodeURIComponent(sid) + '?after=' + after);
      data = await res.json();
    } catch (_) { return; }
    if (sharedState._phoneModePending !== pend || pend.fired) return;
    if (pend.convId === sharedState.currentConversation) return;  // user came back; let render path handle it
    const events = (data && data.events) || [];
    if (!(0, sharedState._batchHasSpeakableReply)(events)) return;
    // Extract the spoken text of the latest assistant text turn, stripping the
    // session-state footer (same exclusion as the DOM path).
    const text = _spokenTextFromEvents(events);
    if (!text) return;
    pend.fired = true;
    const paneId = pend.paneId;
    (0, sharedState._disarmPhoneModeRead)();
    try { speakTextDirect(text, pend.convId, paneId); } catch (_) {}
  }

  function _spokenTextFromEvents(events) {
    if (!Array.isArray(events)) return '';
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (!e || e.type !== 'assistant' || !Array.isArray(e.blocks)) continue;
      const parts = e.blocks.filter(b => b && b.kind === 'text').map(b => String(b.text || ''));
      if (!parts.length) continue;
      const raw = parts.join('\n\n');
      const clean = _stripSessionStateFromText(raw);
      if (clean) return clean;
    }
    return '';
  }

  async function submitPlus(paneId) {
    paneId = paneId || (0, sharedState.activePaneId)();
    // Activate the pane before arming the read below: _armPhoneModeRead keys
    // off currentSession, which follows the active pane.
    (0, sharedState.setActivePaneById)(paneId);
    const _paneEl = document.querySelector(`.conv-pane[data-pane-id="${paneId}"]`);
    const $input = (_paneEl && _paneEl.querySelector('.conv-input-bar textarea, .conv-input-bar input[type="text"]')) || sharedState.$convInput;
    if (!$input) return;
    if (!(0, sharedState.guardComposerSend)($input)) return;
    const base = ($input.value || '').trim();
    if (!base) return;
    // Augment the message, arm the auto-read, then reuse the normal send path
    // so queueing / echo / engine routing all behave identically.
    $input.value = base + sharedState.PHONE_MODE_SUFFIX;
    _armPhoneModeRead(paneId, (sharedState.currentSession && sharedState.currentSession.id) || sharedState.sessionIdByConv[sharedState.currentConversation] || sharedState.currentConversation);
    await (0, sharedState.sendToTerminal)(paneId, 'send');
  }

  async function toggleNativeSpeechRecognition(paneId) {
    const SR = window.Capacitor.Plugins.SpeechRecognition;
    if (sharedState._sttRecording) { (0, sharedState.stopSpeechRecognition)(); return; }
    let textarea = null;
    if (paneId === 'gc') textarea = document.getElementById('gcHumanInput');
    else if (paneId === 'simple-home') textarea = document.getElementById('simpleComposerInput');
    else textarea = (0, sharedState.composerInputForPane)(paneId) || sharedState.$convInput;
    if (!textarea) { (0, sharedState.showOpToast)('Input target not found.', 'error'); return; }
    try {
      const avail = await SR.available();
      if (!avail || !avail.available) { (0, sharedState.showOpToast)('Speech recognition unavailable on this device.', 'error'); return; }
      if (SR.requestPermissions) {
        const p = await SR.requestPermissions();
        if (p && (p.speechRecognition === 'denied' || p.microphone === 'denied')) {
          (0, sharedState.showOpToast)('<strong>Speech access blocked.</strong> Allow Microphone and Speech Recognition for CCC in the iPhone Settings app.', 'error');
          return;
        }
      }
      sharedState._sttPreText = (textarea.value || '').slice(0, textarea.selectionStart || 0);
      sharedState._sttPostText = (textarea.value || '').slice(textarea.selectionEnd || 0);
      if (sharedState._nativeSttListener) { try { sharedState._nativeSttListener.remove(); } catch (_) {} sharedState._nativeSttListener = null; }
      sharedState._nativeSttListener = await SR.addListener('partialResults', (data) => {
        const matches = (data && (data.matches || data.value)) || [];
        let t = matches[0] || '';
        if (sharedState._sttPreText && t && !/[\s.,!?;:]$/.test(sharedState._sttPreText) && !/^\s/.test(t)) t = ' ' + t;
        sharedState._sttUpdatingText = true;
        textarea.value = sharedState._sttPreText + t + sharedState._sttPostText;
        textarea.selectionStart = textarea.selectionEnd = sharedState._sttPreText.length + t.length;
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        setTimeout(() => { sharedState._sttUpdatingText = false; }, 0);
      });
      await SR.start({ language: 'en-US', maxResults: 1, partialResults: true, popup: false });
      sharedState._sttRecording = true;
      sharedState._sttActivePaneId = paneId;
      (0, sharedState.updateMicButtonsState)();
    } catch (err) {
      (0, sharedState.showOpToast)('Speech recognition failed: ' + (err && err.message || err), 'error');
      sharedState._sttRecording = false;
      (0, sharedState.updateMicButtonsState)();
    }
  }

  function toggleSpeechRecognition(paneId) {
    const _nativeSR = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.SpeechRecognition;
    if (_nativeSR && /CCC-iOS/.test(navigator.userAgent)) {
      return toggleNativeSpeechRecognition(paneId);
    }
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      // The Web Speech API is the usual culprit on mobile: iOS exposes it only
      // in Safari (not Chrome/Firefox, which are WebKit shells without it), and
      // any browser needs a secure context — so reaching CCC over plain http
      // through Tailscale/LAN (not https, not localhost) also disables it.
      // Point the user at the actual fix instead of a dead "not supported".
      const insecure = (typeof window.isSecureContext === 'boolean') && !window.isSecureContext;
      const msg = insecure
        ? 'Voice input needs a secure connection - open CCC over https or on localhost (plain http over Tailscale/LAN blocks the mic).'
        : 'Voice input isn’t available in this browser. On iPhone/iPad it only works in Safari - open CCC in Safari to dictate.';
      (0, sharedState.showOpToast)(msg, 'error');
      return;
    }

    if (sharedState._sttRecording) {
      const samePane = (paneId === sharedState._sttActivePaneId);
      (0, sharedState.stopSpeechRecognition)();
      if (samePane) return;
    }

    sharedState._sttActivePaneId = paneId;
    sharedState._sttActiveConvId = sharedState.currentConversation;
    sharedState._sttCommittedIndex = 0;
    sharedState._sttResultsLength = 0;
    sharedState._sttUpdatingText = false;

    let textarea = null;
    if (paneId === 'gc') {
      textarea = document.getElementById('gcHumanInput');
    } else if (paneId === 'simple-home') {
      textarea = document.getElementById('simpleComposerInput');
    } else {
      textarea = (0, sharedState.composerInputForPane)(paneId) || sharedState.$convInput;
    }

    if (!textarea) {
      (0, sharedState.showOpToast)('Input target not found.', 'error');
      return;
    }

    const start = textarea.selectionStart || 0;
    const end = textarea.selectionEnd || 0;
    const val = textarea.value || '';
    sharedState._sttPreText = val.slice(0, start);
    sharedState._sttPostText = val.slice(end);

    try {
      sharedState._sttRecognition = new SpeechRecognition();
      sharedState._sttRecognition.continuous = true;
      sharedState._sttRecognition.interimResults = true;
      sharedState._sttRecognition.lang = 'en-US';

      sharedState._sttRecognition.onstart = () => {
        sharedState._sttRecording = true;
        (0, sharedState.updateMicButtonsState)();
      };

      sharedState._sttRecognition.onresult = (event) => {
        sharedState._sttResultsLength = event.results.length;

        // Commit final results
        for (let i = sharedState._sttCommittedIndex; i < event.results.length; ++i) {
          if (event.results[i].isFinal) {
            let text = event.results[i][0].transcript;
            if (sharedState._sttPreText && !/[\s.,!?;:]$/.test(sharedState._sttPreText) && !/^\s/.test(text)) {
              sharedState._sttPreText += ' ';
            }
            sharedState._sttPreText += text;
            sharedState._sttCommittedIndex = i + 1;
          }
        }

        // Gather interim results
        let interimText = '';
        for (let i = sharedState._sttCommittedIndex; i < event.results.length; ++i) {
          let text = event.results[i][0].transcript;
          if ((sharedState._sttPreText || interimText) &&
              !/[\s.,!?;:]$/.test(sharedState._sttPreText + interimText) &&
              !/^\s/.test(text)) {
            interimText += ' ';
          }
          interimText += text;
        }

        sharedState._sttUpdatingText = true;
        textarea.value = sharedState._sttPreText + interimText + sharedState._sttPostText;
        textarea.selectionStart = textarea.selectionEnd = sharedState._sttPreText.length + interimText.length;
        textarea.focus();
        textarea.dispatchEvent(new Event('input', { bubbles: true }));
        setTimeout(() => { sharedState._sttUpdatingText = false; }, 0);
      };

      sharedState._sttRecognition.onerror = (event) => {
        if (event.error === 'not-allowed') {
          if (typeof isCccMacApp === 'function' && isCccMacApp()) {
            (0, sharedState.showOpToast)('<strong>Microphone access blocked.</strong> To fix this:<br>1. Open <strong>System Settings &gt; Privacy &amp; Security &gt; Microphone</strong>.<br>2. Toggle <strong>Command Center for Claude+</strong> ON.<br>3. Restart the application.', 'error');
          } else {
            (0, sharedState.showOpToast)('<strong>Microphone access blocked.</strong> To fix this:<br>1. Click the lock/settings icon next to the browser URL bar.<br>2. Set <strong>Microphone</strong> to <strong>Allow</strong>.<br>3. Reload the page.', 'error');
          }
        } else if (event.error === 'service-not-allowed') {
          if (typeof isCccMacApp === 'function' && isCccMacApp()) {
            (0, sharedState.showOpToast)('<strong>Speech recognition service blocked.</strong> To fix this:<br>1. Open <strong>System Settings &gt; Privacy &amp; Security &gt; Speech Recognition</strong>.<br>2. Toggle <strong>Command Center for Claude+</strong> ON.<br>3. Restart the application.', 'error');
          } else {
            (0, sharedState.showOpToast)('<strong>Speech recognition service blocked.</strong> Please check your browser or operating system Speech/Dictation restrictions.', 'error');
          }
        } else if (event.error !== 'aborted') {
          (0, sharedState.showOpToast)('Speech recognition error: ' + event.error, 'error');
        }
        (0, sharedState.stopSpeechRecognition)();
      };

      sharedState._sttRecognition.onend = () => {
        sharedState._sttRecording = false;
        (0, sharedState.updateMicButtonsState)();
      };

      sharedState._sttRecognition.start();
    } catch (err) {
      (0, sharedState.showOpToast)('Could not start speech recognition: ' + err.message, 'error');
      (0, sharedState.stopSpeechRecognition)();
    }
  }

  function _handleSttUserInteraction(e) {
    if (!sharedState._sttRecording || sharedState._sttUpdatingText) return;
    const target = e.target;
    if (!target) return;
    const isTextarea = (target.tagName === 'TEXTAREA');
    const isTextInput = (target.tagName === 'INPUT' && target.type === 'text');
    if (!isTextarea && !isTextInput) return;

    const val = target.value || '';
    const start = target.selectionStart || 0;
    const end = target.selectionEnd || 0;
    sharedState._sttPreText = val.slice(0, start);
    sharedState._sttPostText = val.slice(end);
    sharedState._sttCommittedIndex = sharedState._sttResultsLength;
  }

  function initConversationBindings6() {
document.addEventListener('focusin', _handleSttUserInteraction);
document.addEventListener('click', _handleSttUserInteraction);
document.addEventListener('input', _handleSttUserInteraction);
document.addEventListener('keydown', _handleSttUserInteraction);
  }

  function _wireTopbarTtsControl() {
    const el = document.getElementById('topbarTtsControl');
    if (!el) return;
    const toggle = el.querySelector('.tts-topbar-toggle');
    const stop = el.querySelector('.tts-topbar-stop');
    if (toggle) {
      toggle.addEventListener('mousedown', (ev) => ev.preventDefault());
      toggle.addEventListener('click', () => {
        (0, sharedState.readLastMessageAloud)(sharedState._ttsActivePaneId || (0, sharedState.activePaneId)());
      });
    }
    if (stop) {
      stop.addEventListener('mousedown', (ev) => ev.preventDefault());
      stop.addEventListener('click', () => { (0, sharedState.stopTextToSpeech)(); (0, sharedState.updateTopbarTtsControl)(); });
    }
  }

  function _wireTtsFloatingControl() {
    const el = document.getElementById('ttsFloatingControl');
    if (!el) return;
    const toggle = el.querySelector('.tts-fc-toggle');
    const stop = el.querySelector('.tts-fc-stop');
    if (toggle) {
      toggle.addEventListener('mousedown', (ev) => ev.preventDefault());
      toggle.addEventListener('click', () => {
        // Reuse the existing play/pause toggle on the same utterance. Pass the
        // pane that owns the active read so the in-bar button state also tracks.
        (0, sharedState.readLastMessageAloud)(sharedState._ttsActivePaneId || (0, sharedState.activePaneId)());
      });
    }
    if (stop) {
      stop.addEventListener('mousedown', (ev) => ev.preventDefault());
      stop.addEventListener('click', () => { (0, sharedState.stopTextToSpeech)(); });
    }
  }

  function _stripSessionStateFromText(text) {
    let out = String(text || '');
    const _ssm = /(?:<!--\s*)?<session-state>/.exec(out);
    if (_ssm) out = out.slice(0, _ssm.index);
    // Belt-and-suspenders: also drop the "Before your final reply…" trailer
    // some prompts emit without the tags.
    out = out.replace(/\n*Before your final reply[\s\S]*$/i, '');
    return out.trim();
  }

  function speakTextDirect(text, convId, paneId, sourceBtn, domElements) {
    if (typeof sharedState._ttsEnabled !== 'undefined' && !sharedState._ttsEnabled) return false;
    if (!window.speechSynthesis && !window.cccBrowserVoice?.ready) return false;
    sharedState._ttsSelectionReadText = '';
    let domData = null;
    if (domElements && domElements.length) {
      domData = (0, sharedState.buildTtsDataFromElements)(domElements);
      if (!domData.text.trim()) domData = null;
    }
    const clean = domData
      ? (0, sharedState._sanitizeTtsText)(domData.text, true)
      : (0, sharedState._sanitizeTtsText)(_stripSessionStateFromText(text));
    if (!clean.trim()) return false;
    // Starting a fresh read supersedes any prior utterance.
    if (sharedState._ttsActive || sharedState._ttsPaused || sharedState._ttsUtterance) {
      sharedState._ttsChunkState = null;
      try { (0, sharedState._ttsEngineCancel)(); } catch (_) {}
    }
    (0, sharedState._setTtsDirectBtnState)(sourceBtn || null, sourceBtn ? 'speaking' : null);
    (0, sharedState.clearTtsHighlight)();
    sharedState._ttsTextMapping = domData ? domData.mapping : [];
    sharedState._ttsLastCharIndex = 0;
    sharedState._ttsPaused = false;
    const clipped = clean.length > sharedState.TTS_TEXT_MAX_CHARS ? clean.slice(0, sharedState.TTS_TEXT_MAX_CHARS) : clean;
    sharedState._ttsBoundUtteranceText = clipped;
    sharedState._ttsActiveConvId = convId || null;
    sharedState._ttsPerfTarget = sourceBtn ? 'turn' : 'direct';
    if (!(0, sharedState._ttsStartChunkedSpeech)(clipped, paneId, 0, false)) return false;
    // Reflect state immediately in case onstart is slow to fire.
    (0, sharedState.setTtsButtonsState)(true, false, paneId || sharedState._ttsActivePaneId || (0, sharedState.activePaneId)());
    return true;
  }

  function initConversationBindings1() {
if (sharedState.$convEscBtn) sharedState.$convEscBtn.addEventListener('click', sharedState.sendEscToTerminal);
if (sharedState.$convSendBtn) sharedState.$convSendBtn.addEventListener('click', () => (0, sharedState.sendToTerminal)('p1'));
if (sharedState.$convSubmitPlusBtn) sharedState.$convSubmitPlusBtn.addEventListener('click', () => submitPlus('p1'));
if (sharedState.$convSendQueueBtn) sharedState.$convSendQueueBtn.addEventListener('click', () => (0, sharedState.sendToTerminal)('p1', 'send_queue'));
(function _initConvSendMenu() {
    const caret = document.getElementById('convSendCaret');
    const menu = document.getElementById('convSendMenu');
    if (!caret || !menu) return;
    const hide = () => {
      menu.style.display = 'none';
      caret.setAttribute('aria-expanded', 'false');
    };
    const toggle = () => {
      const opening = menu.style.display === 'none';
      menu.style.display = opening ? 'block' : 'none';
      caret.setAttribute('aria-expanded', opening ? 'true' : 'false');
    };
    caret.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      toggle();
    });
    menu.addEventListener('click', (ev) => {
      // CCC-923: the auto-compact/token-sitter rows take input rather than
      // firing an action — don't close the menu when they're clicked.
      if (ev.target && ev.target.closest && ev.target.closest('.new-session-only')) return;
      if (ev.target && ev.target.closest && ev.target.closest('.conv-send-menu-item')) hide();
    });
    document.addEventListener('click', (ev) => {
      if (menu.style.display === 'none') return;
      const group = menu.closest('.conv-send-group');
      if (group && group.contains(ev.target)) return;
      hide();
    });
  })();
(function _initSendLongPress() {
    const btn = document.getElementById('convSendBtn');
    const caret = document.getElementById('convSendCaret');
    if (!btn || !caret) return;
    let timer = 0, fired = false;
    const clear = () => { clearTimeout(timer); timer = 0; };
    btn.addEventListener('touchstart', () => {
      fired = false;
      timer = setTimeout(() => {
        fired = true;
        if (navigator.vibrate) navigator.vibrate(10);
        caret.click();
      }, 450);
    }, { passive: true });
    ['touchend', 'touchmove', 'touchcancel'].forEach(t => btn.addEventListener(t, clear, { passive: true }));
    // Swallow the click that follows a long-press so it doesn't also send.
    btn.addEventListener('click', (ev) => {
      if (!fired) return;
      fired = false;
      ev.preventDefault();
      ev.stopImmediatePropagation();
    }, true);
    btn.addEventListener('contextmenu', (ev) => { if ((0, sharedState.isTouchPrimary)()) ev.preventDefault(); });
  })();
(function _initHeaderLongPressCopy() {
    const bc = document.getElementById('cccBreadcrumb');
    if (!bc) return;
    let timer = 0, fired = false, sx = 0, sy = 0;
    const clear = () => { clearTimeout(timer); timer = 0; };
    bc.addEventListener('touchstart', (ev) => {
      fired = false;
      clear();
      const t = ev.touches && ev.touches[0];
      sx = t ? t.clientX : 0; sy = t ? t.clientY : 0;
      timer = setTimeout(async () => {
        const src = document.getElementById('convSessionId');
        const sid = (src && src.dataset.copySessionId) || '';
        if (!sid) return;
        fired = true;
        try { if (navigator.vibrate) navigator.vibrate(10); } catch (_) {}
        const path = ((src && src.dataset.copyTranscriptPath) || '').trim();
        const ok = await (0, sharedState.copyTextValue)(path ? (sid + ' (' + path + ')') : sid);
        (0, sharedState.showOpToast)(ok ? (path ? 'Copied session ID + transcript path' : 'Copied session ID')
                       : 'Copy failed - select and copy manually', ok ? 'ok' : 'error');
      }, 500);
    }, { passive: true });
    bc.addEventListener('touchmove', (ev) => {
      const t = ev.touches && ev.touches[0];
      if (t && Math.abs(t.clientX - sx) + Math.abs(t.clientY - sy) < 10) return;
      clear();
    }, { passive: true });
    ['touchend', 'touchcancel'].forEach(n => bc.addEventListener(n, clear, { passive: true }));
    bc.addEventListener('click', (ev) => {
      if (!fired) return;
      fired = false;
      ev.preventDefault();
      ev.stopImmediatePropagation();
    }, true);
    bc.addEventListener('contextmenu', (ev) => { if ((0, sharedState.isTouchPrimary)()) ev.preventDefault(); });
  })();
(function _initOneTapComposerSend() {
    const SEL = '.send-btn, .submit-plus-btn, .send-queue-btn, .steer-btn';
    let start = null;
    document.addEventListener('touchstart', (ev) => {
      start = null;
      if (ev.touches.length !== 1) return;
      const btn = ev.target.closest && ev.target.closest(SEL);
      const bar = btn && btn.closest('.conv-input-bar');
      const active = document.activeElement;
      if (!bar || !active || active.tagName !== 'TEXTAREA' || !bar.contains(active)) return;
      const t = ev.touches[0];
      start = { btn, x: t.clientX, y: t.clientY };
    }, { passive: true, capture: true });
    document.addEventListener('touchend', (ev) => {
      const s = start;
      start = null;
      if (!s || !s.btn.isConnected || s.btn.disabled) return;
      const t = ev.changedTouches && ev.changedTouches[0];
      if (!t || Math.abs(t.clientX - s.x) > 12 || Math.abs(t.clientY - s.y) > 12) return;
      ev.preventDefault();  // no synthetic mousedown/click, so no blur
      s.btn.click();
    }, { passive: false, capture: true });
    document.addEventListener('touchcancel', () => { start = null; }, { passive: true, capture: true });
  })();
document.addEventListener('click', (ev) => {
    if (!(0, sharedState.isTouchPrimary)()) return;
    const pill = ev.target.closest && ev.target.closest('.conv-input-context .wp-usage-pill:not(.wp-usage-missing):not(.wp-spawn-ctx-badge)');
    if (!pill) return;
    const c = document.getElementById('convCompactBtn');
    if (!c || !c.classList.contains('visible') || c.disabled) return;
    ev.preventDefault();
    ev.stopPropagation();
    const label = (pill.textContent || '').replace(/\s+/g, ' ').trim();
    if (window.confirm('Compact this conversation to free context?' + (label ? '\n\nNow: ' + label : ''))) (0, sharedState.compactCurrentSession)();
  }, true);
(0, sharedState.initConversationActionsMenu)();
if (sharedState.$convSteerBtn) sharedState.$convSteerBtn.addEventListener('click', () => (0, sharedState.sendToTerminal)('p1', 'steer'));
if (sharedState.$convCompactBtn) sharedState.$convCompactBtn.addEventListener('click', () => (0, sharedState.compactCurrentSession)());
(function _liftCompactToHeaderOnMobile() {
    try {
      if (typeof sharedState.isTouchPrimary === 'function' && !(0, sharedState.isTouchPrimary)()) return;
      const toolbar = document.getElementById('convToolbar');
      if (sharedState.$convCompactBtn && toolbar && sharedState.$convCompactBtn.parentElement !== toolbar) {
        sharedState.$convCompactBtn.classList.add('in-header');
        toolbar.appendChild(sharedState.$convCompactBtn);
      }
    } catch (_) {}
  })();
if (sharedState.$convTtsBtn) {
    sharedState.$convTtsBtn.addEventListener('mousedown', (ev) => ev.preventDefault());
    sharedState.$convTtsBtn.addEventListener('click', () => (0, sharedState.readLastMessageAloud)(sharedState._ttsActivePaneId || (0, sharedState.activePaneId)()));
  }
document.addEventListener('pointerdown', (ev) => {
    if (!ev.target || !ev.target.closest || !ev.target.closest('.tts-btn, .conv-scroll-speak-btn, [data-read-assistant-message], [data-read-agent-answer]')) return;
    const sel = window.getSelection && window.getSelection();
    if (sel && !sel.isCollapsed && sel.rangeCount && String(sel).trim()) {
      try { sharedState._ttsCapturedRange = sel.getRangeAt(0).cloneRange(); sharedState._ttsCapturedAt = Date.now(); }
      catch (_) { sharedState._ttsCapturedRange = null; }
    }
  }, true);
if (sharedState.$convMicBtn) {
    sharedState.$convMicBtn.addEventListener('mousedown', (ev) => ev.preventDefault());
    sharedState.$convMicBtn.addEventListener('click', () => toggleSpeechRecognition('p1'));
  }
  }

  function initConversationBindings2() {
document.addEventListener('click', (ev) => {
    if (ev.target.closest('.tts-rate-down')) { (0, sharedState._stepTtsRate)(-0.05); return; }
    if (ev.target.closest('.tts-rate-up')) { (0, sharedState._stepTtsRate)(0.05); return; }
    if (ev.target.closest('.tts-rate-next')) { (0, sharedState._ttsJumpToNextSentence)(); }
  });
(0, sharedState._syncTtsRateUi)();
(0, sharedState._refreshTtsRateBtns)();
_wireTtsFloatingControl();
_wireTopbarTtsControl();
  }

  function _autosizeConvInput() {
    (0, sharedState._autosizeTextareaLike)(sharedState.$convInput, sharedState.$convInputBar);
  }

  function initConversationBindings3() {
if (!window.__cccComposerDeferFlushBound) {
    window.__cccComposerDeferFlushBound = true;
    document.addEventListener('focusout', (e) => {
      if (!(0, sharedState._isComposerElement)(e.target)) return;
      requestAnimationFrame(() => {
        if (!(0, sharedState._isComposerFocused)()) _flushComposerDeferredUpdates();
      });
    }, true);
    window.addEventListener('beforeunload', () => {
      (0, sharedState.flushSaveInputDrafts)();
      _flushComposerDeferredUpdates();
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        (0, sharedState.flushSaveInputDrafts)();
        _flushComposerDeferredUpdates();
      }
    });
  }
if (sharedState.$convInput) {
    sharedState.$convInput.addEventListener('input', () => {
      (0, sharedState.rememberInputDraft)(sharedState.$convInput, sharedState.currentConversation);
      _autosizeConvInput();
      refreshSlashCommandMenu(sharedState.$convInput);
      if (sharedState.currentConversation === '__new__') { (0, sharedState.scheduleClaudePrewarm)(); scheduleRepoGuess(); }
    });
    sharedState.$convInput.addEventListener('focus', () => refreshSlashCommandMenu(sharedState.$convInput));
    sharedState.$convInput.addEventListener('click', () => refreshSlashCommandMenu(sharedState.$convInput));
    // Clicking anywhere in the input bar — the padding, the gap above/below
    // the textarea, or the empty stretch of the bottom selector row — should
    // drop the caret in the composer. Without this, only a direct hit on the
    // 1-row textarea glyph area focuses it, so the bar feels "dead" on the
    // first click (you click, no caret; you have to click again, on the text).
    if (sharedState.$convInputBar) {
      sharedState.$convInputBar.addEventListener('mousedown', (e) => {
        if (e.target === sharedState.$convInput) return;  // native caret placement
        // Don't steal clicks from real controls (send/esc/tts buttons, the
        // engine/model/issue selects, custom-select menus, links, the badge).
        if (e.target.closest && e.target.closest(
              'button, select, a, input, textarea, [role="button"], '
              + '.custom-select-container, .custom-select-menu')) return;
        e.preventDefault();  // stop the caret being dropped on a non-focusable node
        try { sharedState.$convInput.focus({ preventScroll: true }); } catch (_) { sharedState.$convInput.focus(); }
      });
    }
    sharedState.$convInput.addEventListener('keydown', (e) => {
      if ((0, sharedState.isImeKey)(e)) return;
      if (handleSlashCommandKeydown(sharedState.$convInput, e)) return;
      if ((0, sharedState.recallLastComposerCommand)(sharedState.$convInput, e)) return;
      // Ctrl/Cmd+S steers the running turn with the composer text (kimi-web
      // parity) — same flow as the ⚡ steer button. Suppresses the browser's
      // Save-page dialog whenever the composer has focus.
      if (e.key === 's' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        if ((sharedState.$convInput.value || '').trim()) (0, sharedState.sendToTerminal)('p1', 'steer');
        return;
      }
      // In expanded-composer mode Enter inserts a newline (multi-line
      // editing); Ctrl/Cmd+Enter sends. Collapsed: Enter sends, Shift+Enter
      // newlines. On TOUCH, Enter must insert a newline instead — the
      // on-screen keyboard's return key is for line breaks, and Enter-to-send
      // fires constantly mid-typing (and when accepting autocorrect).
      const _expanded = sharedState.$convInputBar && sharedState.$convInputBar.classList.contains('is-composer-expanded');
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && !(0, sharedState.isTouchPrimary)() && !(_expanded && !(e.ctrlKey || e.metaKey))) {
        e.preventDefault();
        if (!f2InterceptEnterSend((0, sharedState.activePaneId)())) (0, sharedState.sendToTerminal)();
      } else if (e.key === 'Escape') {
        const presentationPane = (0, sharedState.presentationPaneElement)((0, sharedState.activePaneId)());
        if (presentationPane
            && (0, sharedState.normalizePresentationMode)(presentationPane.dataset.presentationMode) !== 'off') return;
        if (sharedState.$convEscBtn && sharedState.$convEscBtn.style.display !== 'none' && !sharedState.$convEscBtn.disabled) {
          e.preventDefault();
          (0, sharedState.sendEscToTerminal)();
        }
      }
    });
    // Various callsites do `$convInput.value = ''` to clear after send;
    // hook the value setter so the textarea auto-shrinks back to 1 row
    // on each clear without having to touch every callsite. Clearing to
    // empty also collapses the expanded composer (kimi collapses after send).
    if (sharedState.$convInput.tagName === 'TEXTAREA') {
      const desc = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
      if (desc && desc.set && desc.get) {
        Object.defineProperty(sharedState.$convInput, 'value', {
          configurable: true,
          get() { return desc.get.call(this); },
          set(v) {
            desc.set.call(this, v);
            if (!v && sharedState.$convInputBar) setComposerExpanded(false);
            _autosizeConvInput();
          },
        });
      }
    }
    _autosizeConvInput();
  }
  }

  function setComposerMode(mode) {
    const bar = sharedState.$convInputBar;
    if (!bar) return;
    bar.dataset.composerMode = mode === 'regular' ? '' : mode;
    bar.classList.toggle('is-composer-expanded', mode === 'expand');
    _autosizeConvInput();
  }

  function setComposerExpanded(on) { setComposerMode(on ? 'expand' : 'regular'); }

  function initConversationBindings4() {
for (const btn of allResumeButtons()) btn.addEventListener('click', copyResumeCommand);
for (const btn of (0, sharedState.allJumpButtons)()) btn.addEventListener('click', jumpToTerminal);
for (const btn of (0, sharedState.allLaunchButtons)()) btn.addEventListener('click', sharedState.launchTerminal);
for (const btn of allDesktopButtons()) btn.addEventListener('click', openInClaudeDesktop);
if (sharedState.$launchChoiceBtnConv) {
    sharedState.$launchChoiceBtnConv.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleLaunchChoiceMenu(sharedState.$launchChoiceBtnConv, sharedState.$launchChoiceMenuConv);
    });
  }
  }

  function initConversationBindings5() {
if (sharedState.$cpLaunchChoiceBtnGlobal) {
    sharedState.$cpLaunchChoiceBtnGlobal.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleLaunchChoiceMenu(sharedState.$cpLaunchChoiceBtnGlobal, sharedState.$cpLaunchChoiceMenuGlobal);
    });
  }
for (const menu of allLaunchChoiceMenus()) {
    menu.addEventListener('click', (e) => {
      const item = e.target.closest('[data-launch-target]');
      if (!item) return;
      e.preventDefault();
      e.stopPropagation();
      launchTarget(item.dataset.launchTarget, item);
    });
  }
document.addEventListener('click', (e) => {
    if (e.target.closest('.launch-split')) return;
    closeLaunchChoiceMenus();
  });
document.addEventListener('keydown', (e) => {
    if ((0, sharedState.isImeKey)(e)) return;
    if (e.key === 'Escape') closeLaunchChoiceMenus();
  });
  }

  function isCccMacApp() {
    if (window.__CCC_MAC_APP__ === true) return true;
    const ua = (typeof navigator !== 'undefined' && navigator.userAgent) || '';
    if (ua.indexOf('CCC-macOS') !== -1) return true;
    const bridge = window.webkit
      && window.webkit.messageHandlers
      && window.webkit.messageHandlers.cccNative;
    return !!(bridge && typeof bridge.postMessage === 'function');
  }

  function _flushComposerDeferredUpdates() {
    const batches = sharedState._deferredStreamBatches.splice(0);
    for (const batch of batches) {
      (0, sharedState.renderConversationEvents)(batch.events, batch.paneId);
    }
    if (sharedState._deferredUsageSid) {
      const sid = sharedState._deferredUsageSid;
      const pid = sharedState._deferredUsagePaneId;
      sharedState._deferredUsageSid = null;
      sharedState._deferredUsagePaneId = '';
      (0, sharedState.fetchSessionUsage)(sid, pid || undefined);
    }
  }

  function spawnEndpointForEngine(engine) {
    if (engine === 'pkood') return '/api/pkood/spawn';
    if (engine === 'codex') return '/api/sessions/spawn-codex';
    if (engine === 'gemini') return '/api/sessions/spawn-gemini';
    if (engine === 'cursor') return '/api/sessions/spawn-cursor';
    if (engine === 'antigravity') return '/api/sessions/spawn-antigravity';
    if (engine === 'kilo') return '/api/sessions/spawn-kilo';
    if (engine === 'hermes') return '/api/sessions/spawn-hermes';
    if (engine === 'kimi') return '/api/sessions/spawn-kimi';
    if (engine === 'opencode') return '/api/sessions/spawn-opencode';
    if (engine === 'grok') return '/api/sessions/spawn-grok';
    return '/api/sessions/spawn';
  }

  function buildSpawnBody(opts) {
    const o = opts || {};
    const engine = String(o.engine || '').trim();
    // CCC-509: always send the engine the UI actually shows. Omitting it lets
    // the server's own persisted spawn-defaults engine win, which silently
    // spawned a Claude-picked model under Codex.
    const body = { prompt: o.prompt, engine };
    const accountSelect = document.getElementById("codexAccountSelect");
    if (engine === "codex" && accountSelect && accountSelect.value) body.codex_account_id = accountSelect.value;
    if (engine === 'antigravity') {
      const selected = document.getElementById('antigravityAccountSelect');
      body.antigravity_account_id = selected?.value || sharedState.spawnDefaultsState.antigravity_account_id || 'primary';
    }
    if (o.name) body.name = o.name;
    if (o.cwd) body.cwd = o.cwd;
    if (o.repoPath) body.repo_path = o.repoPath;
    const model = String(o.model || '').trim();
    if (model) {
      // CCC-503: a select can still hold another engine's model id right after
      // an engine switch. Known-elsewhere ids are dropped; unknown custom ids
      // pass through for the engines that accept free-form model strings.
      if ((0, sharedState._modelAllowedForEngine)(engine, model)) {
        body.model = model;
      } else {
        console.warn('[spawn] dropping model "' + model + '" not valid for engine "' + engine + '"; using server default instead');
      }
    }
    const effort = String(o.effort || '').trim();
    if ((0, sharedState.engineSupportsEffort)(engine) && (effort || o.effortExplicit)) {
      body.reasoning_effort = effort;
    }
    if (o.worktree !== undefined && (0, sharedState.spawnSupportsWorktree)(engine)) {
      body.worktree = !!o.worktree;
    }
    // $0 runtime: an explicit o.runtime wins; otherwise the composer's Free
    // chip decides. runtimeForSpawn returns '' for unsupported engines, so
    // a chip left on while switching engines never breaks a paid spawn.
    const spawnRuntime = (o.runtime !== undefined)
      ? String(o.runtime || '').trim()
      : ((window.CCCFreeRuntime && typeof CCCFreeRuntime.runtimeForSpawn === 'function')
          ? CCCFreeRuntime.runtimeForSpawn(engine) : '');
    if (spawnRuntime) body.runtime = spawnRuntime;
    if (spawnRuntime === 'free' && o.runtime === undefined) {
      // The composer's "$0 Free" pick: the free router chooses its best free
      // model, so the paid model/effort shown before the pick must not leak.
      delete body.model;
      delete body.reasoning_effort;
    }
    return body;
  }

  function initConversationBindings7() {
[sharedState.$convInputEngineSelect, sharedState.$kptToolbarEngineSelect].forEach(sel => {
    if (!sel) return;
    sel.value = (0, sharedState.getSpawnEngine)();
    sel.addEventListener('change', () => { sharedState._spawnEngineChosenByUser = true; (0, sharedState.setSpawnEngine)(sel.value); });
  });
if (sharedState.$convInputModelSelect) {
    sharedState.$convInputModelSelect.addEventListener('change', () => {
      const engine = (0, sharedState.getSpawnEngine)();
      if (sharedState.$convInputModelSelect.value === sharedState.SPAWN_DEFAULT_OTHER) {
        let model = '';
        try {
          model = (window.prompt('Model ID for ' + (0, sharedState.spawnEngineLabel)(engine) + ':', sharedState._defaultModelsByEngine[engine] || '') || '').trim();
        } catch (_) {
          model = '';
        }
        if (model) (0, sharedState.setSpawnDefaultModel)(engine, model);
        else (0, sharedState.syncSpawnEngineDependentUi)();
        return;
      }
      (0, sharedState.setSpawnDefaultModel)(engine, sharedState.$convInputModelSelect.value);
      const _selOpt = sharedState.$convInputModelSelect.options[sharedState.$convInputModelSelect.selectedIndex];
      sharedState.$convInputModelSelect.title = _selOpt ? (_selOpt.textContent || '') : '';
      if (sharedState.currentConversation === '__new__') (0, sharedState.scheduleClaudePrewarm)();
    });
  }
if (sharedState.$convInputEffortSelect) {
    sharedState.$convInputEffortSelect.addEventListener('change', () => {
      (0, sharedState.markNsAdvisorManual)();
      sharedState.spawnEffortChoiceDirty = true;
      // Effort is part of the prewarm reservation's identity, so a change here
      // has to re-reserve exactly like a model change does — otherwise the
      // pending warm process no longer matches what submit will ask for.
      if (sharedState.currentConversation === '__new__') (0, sharedState.scheduleClaudePrewarm)();
      const _effortEngine = (0, sharedState.getSpawnEngine)();
      const _effortModel = (typeof sharedState.$convInputModelSelect !== 'undefined' && sharedState.$convInputModelSelect && sharedState.$convInputModelSelect.style.display !== 'none')
        ? sharedState.$convInputModelSelect.value
        : (sharedState._defaultModelsByEngine[_effortEngine] || '');
      (0, sharedState.recordSpawnChoice)(_effortEngine, _effortModel, sharedState.$convInputEffortSelect.value);
      (0, sharedState.renderNsAdvisor)();
    });
  }
  }

  function initConversationBindings8() {
{ const $ia = document.getElementById('convInputIssueAction');
    if ($ia) $ia.addEventListener('change', () => (0, sharedState.updateInputBar)()); }
  }

  async function uploadPastedImage(blob) {
    const res = await fetch('/api/upload-image', {
      method: 'POST',
      headers: {'Content-Type': blob.type || 'image/png'},
      body: blob,
    });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) throw new Error(data.error || 'upload failed');
    return data.path;
  }

  async function uploadManagedAttachment(file, fallbackName) {
    const res = await fetch('/api/upload-attachment', {
      method: 'POST',
      headers: {
        'Content-Type': file.type || 'application/octet-stream',
        'X-CCC-Attachment-Name': encodeURIComponent(file.name || fallbackName || 'attachment'),
      },
      body: file,
    });
    const data = await res.json().catch(() => ({}));
    if (!data.ok) throw new Error(data.error || 'upload failed');
    return data.path;
  }

  function insertAtCursor(el, text, emitInput = true) {
    if (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && el.type === 'text')) {
      const start = el.selectionStart || 0;
      const end = el.selectionEnd || 0;
      el.value = el.value.slice(0, start) + text + el.value.slice(end);
      el.selectionStart = el.selectionEnd = start + text.length;
      if (emitInput) el.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  function getClipboardImageFile(ev) {
    const dt = ev.clipboardData;
    if (!dt) return null;
    const items = dt.items || [];
    for (const it of items) {
      if (it.kind === 'file' && it.type && it.type.startsWith('image/')) {
        const file = it.getAsFile();
        if (file) return file;
      }
    }
    const files = dt.files || [];
    for (const file of files) {
      if (file && file.type && file.type.startsWith('image/')) return file;
    }
    return null;
  }

  function _pastedImageHost(el) {
    return (el && el.closest && el.closest(
      '.conv-input-bar, .gc-reader-input-row, .new-session-modal-body, .ann-editor, .ann-screen-dialog, .ann-ux-preview-card, .fq-ticket-body'
    )) || (el && el.parentElement) || null;
  }

  function _pastedImageThumbsContainer(el) {
    // Find or create a thumbnails strip adjacent to the input element.
    // Lives in the input's container so it travels with the composer
    // regardless of which pane / which composer fired the paste.
    const host = _pastedImageHost(el);
    if (!host) return null;
    let strip = host.querySelector(':scope > .paste-thumb-strip');
    if (!strip) {
      strip = document.createElement('div');
      strip.className = 'paste-thumb-strip';
      strip.setAttribute('aria-label', 'Pasted images');
      host.insertBefore(strip, host.firstChild);
    }
    return strip;
  }

  function _addPastedImageThumb(el, path, blobUrl) {
    const strip = _pastedImageThumbsContainer(el);
    if (!strip) return;
    const thumb = document.createElement('div');
    thumb.className = 'paste-thumb';
    thumb.dataset.path = path;
    const img = document.createElement('img');
    img.src = blobUrl || ('/api/pasted-image?path=' + encodeURIComponent(path));
    img.alt = 'Pasted image';
    img.loading = 'lazy';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'paste-thumb-remove';
    remove.innerHTML = '&times;';
    remove.title = 'Remove this attachment from the message';
    remove.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      // Strip the path token from the input value so the send doesn't
      // carry an orphan reference.
      try {
        const re = new RegExp('\\s*' + path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*', 'g');
        el.value = el.value.replace(re, ' ').replace(/\s{2,}/g, ' ').trim();
        el.dispatchEvent(new Event('input', { bubbles: true }));
      } catch (_) {}
      thumb.remove();
      if (!strip.querySelector('.paste-thumb')) strip.remove();
    });
    thumb.appendChild(img);
    thumb.appendChild(remove);
    strip.appendChild(thumb);
  }

  function _clearPastedImageThumbs(el) {
    const host = _pastedImageHost(el);
    const strip = host && host.querySelector(':scope > .paste-thumb-strip');
    if (strip) strip.remove();
  }

  function _addAttachmentChip(el, path, filename) {
    const strip = _pastedImageThumbsContainer(el);
    if (!strip) return;
    const chip = document.createElement('div');
    chip.className = 'paste-thumb paste-thumb-file';
    chip.dataset.path = path;
    chip.title = filename || path;
    const label = document.createElement('span');
    label.className = 'paste-thumb-file-name';
    label.textContent = filename || path.split('/').pop();
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'paste-thumb-remove';
    remove.innerHTML = '&times;';
    remove.title = 'Remove this attachment from the message';
    remove.addEventListener('click', (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      try {
        const re = new RegExp('\\s*' + path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*', 'g');
        el.value = el.value.replace(re, ' ').replace(/\s{2,}/g, ' ').trim();
        el.dispatchEvent(new Event('input', { bubbles: true }));
      } catch (_) {}
      chip.remove();
      if (!strip.querySelector('.paste-thumb')) strip.remove();
    });
    chip.appendChild(label);
    chip.appendChild(remove);
    strip.appendChild(chip);
  }

  function beginComposerUpload(el) {
    if (!el) return;
    el._cccPendingUploadCount = (el._cccPendingUploadCount || 0) + 1;
  }

  function finishComposerUpload(el) {
    if (!el) return;
    el._cccPendingUploadCount = Math.max(0, (el._cccPendingUploadCount || 0) - 1);
  }

  function shouldUsePastedImageUpload(file) {
    const type = String((file && file.type) || '').split(';')[0].trim().toLowerCase();
    return type === 'image/png' || type === 'image/jpeg' || type === 'image/jpg'
      || type === 'image/gif' || type === 'image/webp';
  }

  function attachmentNameForClipboardImage(file) {
    if (file && file.name) return file.name;
    const type = String((file && file.type) || '').split(';')[0].trim().toLowerCase();
    const extByType = {
      'image/svg+xml': 'svg',
      'image/heic': 'heic',
      'image/heif': 'heif',
    };
    const ext = extByType[type] || (type.startsWith('image/')
      ? type.slice('image/'.length).replace(/[^a-z0-9]/g, '') : '');
    return ext ? 'pasted-image.' + ext : 'pasted-image';
  }

  async function uploadFilesToComposer(el, files) {
    for (const file of files) {
      const isImage = shouldUsePastedImageUpload(file);
      const placeholder = ' [uploading ' + (file.name || 'attachment') + '...] ';
      beginComposerUpload(el);
      insertAtCursor(el, placeholder, false);
      try {
        if (isImage) {
          const path = await uploadPastedImage(file);
          el.value = el.value.replace(placeholder, ' ' + path + ' ');
          el.dispatchEvent(new Event('input', { bubbles: true }));
          _addPastedImageThumb(el, path, null);
        } else {
          const path = await uploadManagedAttachment(file);
          el.value = el.value.replace(placeholder, ' ' + path + ' ');
          el.dispatchEvent(new Event('input', { bubbles: true }));
          _addAttachmentChip(el, path, file.name);
        }
      } catch (e) {
        el.value = el.value.replace(placeholder, ' [upload failed: ' + e.message + '] ');
        el.dispatchEvent(new Event('input', { bubbles: true }));
      } finally {
        finishComposerUpload(el);
      }
    }
  }

  function attachFileDrop(el) {
    if (!el || el._fileDropBound) return;
    el._fileDropBound = true;
    el.addEventListener('dragover', (ev) => {
      if (ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files.length) {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'copy';
      }
    });
    el.addEventListener('drop', async (ev) => {
      const files = Array.from((ev.dataTransfer && ev.dataTransfer.files) || []).filter(Boolean);
      if (!files.length) return;
      ev.preventDefault();
      await uploadFilesToComposer(el, files);
    });
  }

  function attachFilePickerButton(inputEl, buttonEl, getTarget) {
    if (!inputEl || !buttonEl || buttonEl._filePickerBound) return;
    buttonEl._filePickerBound = true;
    const targetEl = getTarget || (() => document.getElementById('convInput'));
    buttonEl.addEventListener('click', (ev) => {
      ev.preventDefault();
      inputEl.click();
    });
    inputEl.addEventListener('change', async () => {
      const files = Array.from(inputEl.files || []).filter(Boolean);
      const el = targetEl();
      try {
        if (files.length && el) await uploadFilesToComposer(el, files);
      } finally {
        // Reset AFTER the upload finishes, not before — clearing the file
        // input's value can tear down the browser's handle backing the
        // still-in-flight File/Blob reads on some engines, which silently
        // stalls the upload fetch forever instead of erroring. Reset here
        // only to allow re-selecting the same file next time.
        inputEl.value = '';
      }
    });
  }

  function attachImagePaste(el) {
    attachFileDrop(el);
    if (!el || el._imgPasteBound) return;
    el._imgPasteBound = true;
    el.addEventListener('paste', async (ev) => {
      const blob = getClipboardImageFile(ev);
      if (!blob) return;
      ev.preventDefault();
      const canPreview = shouldUsePastedImageUpload(blob);
      const placeholder = ' [uploading image...] ';
      beginComposerUpload(el);
      insertAtCursor(el, placeholder, false);
      // Show an immediate thumbnail from the browser-side blob so the
      // user sees their image instantly — before the upload returns.
      // Once the server returns the real path, swap data-path to the
      // canonical value so the remove button can strip the token.
      let immediateBlobUrl = null;
      let pendingThumb = null;
      if (canPreview) {
        try {
          immediateBlobUrl = URL.createObjectURL(blob);
          const strip = _pastedImageThumbsContainer(el);
          if (strip) {
            pendingThumb = document.createElement('div');
            pendingThumb.className = 'paste-thumb paste-thumb-pending';
            const im = document.createElement('img');
            im.src = immediateBlobUrl;
            im.alt = 'Pasted image (uploading)';
            pendingThumb.appendChild(im);
            strip.appendChild(pendingThumb);
          }
        } catch (_) {}
      }
      try {
        const p = canPreview
          ? await uploadPastedImage(blob)
          : await uploadManagedAttachment(blob, attachmentNameForClipboardImage(blob));
        el.value = el.value.replace(placeholder, ' ' + p + ' ');
        el.dispatchEvent(new Event('input', { bubbles: true }));
        if (pendingThumb) pendingThumb.remove();
        if (canPreview) _addPastedImageThumb(el, p, immediateBlobUrl);
        else _addAttachmentChip(el, p, blob.name || 'pasted image');
      } catch (e) {
        el.value = el.value.replace(placeholder, ' [upload failed: ' + e.message + '] ');
        el.dispatchEvent(new Event('input', { bubbles: true }));
        if (pendingThumb) pendingThumb.remove();
        if (immediateBlobUrl) try { URL.revokeObjectURL(immediateBlobUrl); } catch (_) {}
      } finally {
        finishComposerUpload(el);
      }
    });
    // Clear thumbnails when the input is cleared. Send paths call
    // `el.value = ''` via direct assignment, which does NOT fire an
    // input event — so the listener alone wouldn't catch the post-send
    // clear and the thumbnails would linger. Hook the value setter so
    // EVERY assignment (whether from user input, programmatic clear,
    // or autoresize callsite) triggers thumb cleanup when the new
    // value is empty.
    el.addEventListener('input', () => {
      if (!el.value || !el.value.trim()) _clearPastedImageThumbs(el);
    });
    try {
      const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const ownDesc = Object.getOwnPropertyDescriptor(el, 'value');
      const protoDesc = Object.getOwnPropertyDescriptor(proto, 'value');
      const desc = ownDesc || protoDesc;
      if (desc && desc.set && desc.get && !el._imgPasteValueHook) {
        el._imgPasteValueHook = true;
        const origGet = desc.get;
        const origSet = desc.set;
        Object.defineProperty(el, 'value', {
          configurable: true,
          get() { return origGet.call(this); },
          set(v) {
            origSet.call(this, v);
            if (!v || !String(v).trim()) _clearPastedImageThumbs(el);
          },
        });
      }
    } catch (_) { /* defensive - keep paste working even if the hook fails */ }
  }

  function initConversationBindings9() {
[document.getElementById('nsmBody'),
   document.getElementById('kptNewSession'), document.getElementById('cpInput'),
   document.getElementById('convInput')].forEach(attachImagePaste);
window.__cccAttachImagePaste = attachImagePaste;
window.__cccAttachFilePicker = attachFilePickerButton;
attachFilePickerButton(document.getElementById('convAttachInput'), document.getElementById('convAttachBtn'));
  }

  function repoGuessDecide(guess, state) {
    const none = { action: 'none', path: '' };
    if (!guess || !guess.repo_path) return none;
    const conf = Number(guess.confidence);
    if (!(conf >= 0.5)) return none;
    if (guess.repo_path === state.current) return none;
    // Only Jev auto-switches: local path/name matches were the work's repo
    // only ~70% of the time, so they stay a suggestion at any confidence.
    if (guess.source === 'jev' && conf >= 0.8 && !state.userPicked) return { action: 'auto', path: guess.repo_path };
    return { action: 'suggest', path: guess.repo_path };
  }

  function applyRepoGuess(data) {
    const d = repoGuessDecide(
      data && { repo_path: (0, sharedState.normalizeSpawnCwdPath)(data.repo_path), confidence: data.confidence, source: data.source },
      { current: (0, sharedState.getSpawnCwd)(), userPicked: sharedState.repoGuessUserPicked });
    if (d.action === 'auto') {
      const prev = sharedState.repoGuessAuto ? sharedState.repoGuessAuto.prev : (0, sharedState.getSpawnCwd)();
      sharedState.repoGuessAuto = { path: d.path, prev };
      sharedState.repoGuessSuggestion = '';
      (0, sharedState.setRepoGuessPickerValue)(d.path);
    } else if (d.action === 'suggest') {
      sharedState.repoGuessSuggestion = d.path;
    } else {
      sharedState.repoGuessSuggestion = '';
    }
    (0, sharedState.renderRepoGuessChips)();
  }

  function runRepoGuess() {
    sharedState.repoGuessTimer = null;
    if (sharedState.currentConversation !== '__new__' || !sharedState.$convInput) return sharedState.repoGuessInflight;
    const prompt = String(sharedState.$convInput.value || '').trim();
    if (prompt.length < 15 || prompt === sharedState.repoGuessLastPrompt) return sharedState.repoGuessInflight;
    sharedState.repoGuessLastPrompt = prompt;
    const seq = ++sharedState.repoGuessSeq;
    const current = sharedState.repoGuessAuto ? sharedState.repoGuessAuto.prev : (0, sharedState.getSpawnCwd)();
    const p = fetch('/api/repo/guess', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, current_repo: current }),
    }).then(r => r.json()).then(data => {
      if (seq !== sharedState.repoGuessSeq || sharedState.currentConversation !== '__new__') return;  // stale
      applyRepoGuess(data);
    }).catch(() => {}).then(() => {
      if (sharedState.repoGuessInflight === p) sharedState.repoGuessInflight = null;
    });
    sharedState.repoGuessInflight = p;
    return p;
  }

  function scheduleRepoGuess() {
    if (sharedState.currentConversation !== '__new__') return;
    clearTimeout(sharedState.repoGuessTimer);
    sharedState.repoGuessTimer = setTimeout(runRepoGuess, 600);
  }
  function bindConversationControls() {
    initConversationBindings1();
    initConversationBindings2();
    initConversationBindings3();
    initConversationBindings4();
    initConversationBindings5();
    initConversationBindings6();
    initConversationBindings7();
    initConversationBindings8();
    initConversationBindings9();
  }

    return { bindConversationControls, initConversationBindings1, initConversationBindings2, initConversationBindings3, initConversationBindings4, initConversationBindings5, initConversationBindings6, initConversationBindings7, initConversationBindings8, initConversationBindings9, toggleSpeechRecognition, toggleNativeSpeechRecognition, submitPlus, _autosizeConvInput, setComposerMode, setComposerExpanded, attachImagePaste, attachFilePickerButton, _wireTtsFloatingControl, _wireTopbarTtsControl, _flushComposerDeferredUpdates, refreshSlashCommandMenu, scheduleRepoGuess, handleSlashCommandKeydown, f2InterceptEnterSend, allResumeButtons, copyResumeCommand, jumpToTerminal, allDesktopButtons, openInClaudeDesktop, toggleLaunchChoiceMenu, allLaunchChoiceMenus, launchTarget, closeLaunchChoiceMenus, _handleSttUserInteraction, isCccMacApp, _armPhoneModeRead, attachFileDrop, getClipboardImageFile, shouldUsePastedImageUpload, beginComposerUpload, insertAtCursor, _pastedImageThumbsContainer, uploadPastedImage, uploadManagedAttachment, attachmentNameForClipboardImage, _addPastedImageThumb, _addAttachmentChip, finishComposerUpload, _clearPastedImageThumbs, uploadFilesToComposer, slashQueryForInput, activatePaneFromComposer, slashCommandsForCurrentContext, renderSlashCommandMenu, runRepoGuess, moveSlashCommandSelection, commitSlashCommandSelection, f2GateStateForPane, f2ConfirmStaleSend, f2RunContinue, positionLaunchChoiceMenu, launchButtonFromTrigger, openInCodexDesktop, allLaunchChoiceButtons, _armPhoneModeBackgroundPoll, _pastedImageHost, slashCommandUnavailableReason, slashFallbackCommandsForSource, selectSlashCommandMenuItemFromEvent, syncSlashCommandMenuSelection, positionSlashCommandMenu, applyRepoGuess, buildSpawnBody, f2RetrievalPrompt, spawnEndpointForEngine, f2ClearComposer, _phoneModeBackgroundPollTick, repoGuessDecide, _spokenTextFromEvents, speakTextDirect, _stripSessionStateFromText };
  }
  root.CCCConversationControls = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
