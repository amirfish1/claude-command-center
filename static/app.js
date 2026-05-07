(function() {
  // App config from server — populated before anything else renders.
  let APP_CONFIG = {
    app_name: 'Claude Command Center',
    title_strip: [],
    repo: '',
    vercel_enabled: false,
    vercel_project: '',
    pkood_enabled: false,
    gh_enabled: true,
  };
  // Regex compiled from APP_CONFIG.title_strip at load; used to strip
  // user-configured prefixes like "[ACME ...]" from session titles.
  let _titleStripRe = null;  // null = no stripping until config loads
  function rebuildTitleStripRe(prefixes) {
    if (!prefixes || !prefixes.length) { _titleStripRe = null; return; }
    const alt = prefixes.map(p => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
    _titleStripRe = new RegExp('\\s*\\[(?:' + alt + ')[^\\]]*\\]\\s*', 'gi');
  }
  function stripTitle(s) {
    if (!s) return s;
    return _titleStripRe ? s.replace(_titleStripRe, ' ') : s;
  }
  function stripGhIssueProjectTag(s) {
    if (!s) return s;
    return String(s)
      .replace(/^(\s*(?:#\d+\s*[:\-\u2014]\s*)?)\[[^\]]*(?:Problem|Feature announcement)\]\s*/i, '$1')
      .trim();
  }
  // Strip the "Fix GitHub issue #N: ... Read the issue first with `gh issue view N`,
  // then implement the fix." boilerplate from older spawn prompts. Kept here so it
  // retroactively cleans cards spawned before the prompt-template cleanup.
  // Structured block every spawned session is asked to emit on its final reply
  // so the /attention panel can show exact state + next step without heuristics.
  const SESSION_STATE_INSTRUCTION = "\n\nBefore your final reply, end with a block formatted EXACTLY like this (the Claude Command Center dashboard parses it):\n<session-state>\nDID: <one sentence — what you actually changed/learned>\nINSIGHT: <one sentence — the main finding, root cause, or surprise>\nNEXT_STEP_USER: <one sentence — the exact next thing the user should do>\n</session-state>";

  // When a prompt starts with the sibling-orchestrator preamble ("You are
  // a sibling Claude Code session…"), the boilerplate that follows (your
  // sandbox, repo rules, footguns) is identical across every spawn —
  // showing it as the "Original ask" buries the actual task. Pull just
  // the heading + body out, preserving the "## Feature: …" / "## Task: …"
  // line so the user can still tell what kind of work this is. Returns
  // null when not a sibling spawn or when no recognized heading exists.
  const SIBLING_PROMPT_PREFIX_LC = 'you are a sibling claude code session';
  const SIBLING_HEADING_RE = /^##\s+(?:Feature|Task|Goal|Bug|Fix|Spec)\s*:\s*.+$/im;
  function extractSiblingTaskBody(s) {
    if (!s) return null;
    const head = String(s).replace(/^\s+/, '').slice(0, 80).toLowerCase();
    if (!head.startsWith(SIBLING_PROMPT_PREFIX_LC)) return null;
    const m = SIBLING_HEADING_RE.exec(s);
    if (!m) return null;
    // Slice from the heading to end-of-string. The body below the heading
    // is the actual task — keep it. The boilerplate before it is dropped.
    return String(s).slice(m.index).trim();
  }

  function cleanIssuePrompt(s) {
    if (!s) return s;
    let out = String(s);
    // Sibling-orchestrator spawn: strip the preamble down to the heading.
    // Done first so the rest of the cleanup applies to the real task body.
    const siblingBody = extractSiblingTaskBody(out);
    if (siblingBody) out = siblingBody;
    // Strip the session-state instruction CCC appends to spawn prompts.
    // Use the constant rather than a literal so future edits to the
    // template flow through automatically.
    if (SESSION_STATE_INSTRUCTION && out.indexOf(SESSION_STATE_INSTRUCTION) !== -1) {
      out = out.split(SESSION_STATE_INSTRUCTION).join('');
    }
    // Generic fallback: strip a "Before your final reply…<session-state>
    // …</session-state>" trailer even if the constant has shifted slightly
    // (whitespace, wording tweaks). Anchored to "Before your final reply"
    // so it can't eat unrelated user text.
    out = out.replace(/\n*Before your final reply[\s\S]*?<\/session-state>\s*$/i, '');
    return out
      // Old template
      .replace(/^\s*Fix GitHub issue #\d+:\s*/i, '')
      .replace(/\.?\s*Read the issue first with[^.]*?,\s*then implement the fix\.?\s*$/i, '')
      // Current template: "Fix issue #N — {title}\n\nRun `gh issue view N` …"
      .replace(/^\s*Fix issue #\d+\s*(?:—|-)\s*/i, '')
      .replace(/\n+Run `gh issue view \d+`[^\n]*(title may be truncated\)\.?)?\s*$/i, '')
      .trim();
  }

  function eventTextString(value) {
    if (typeof value === 'string') return value;
    if (value == null) return '';
    const blockText = (item) => {
      if (typeof item === 'string') return item;
      if (item == null) return '';
      if (typeof item !== 'object') return String(item);
      if (typeof item.text === 'string') return item.text;
      const blockType = item.type || item.kind || '';
      if (blockType === 'input_image' || blockType === 'image') return '[image]';
      if (blockType) return '[' + String(blockType).replace(/_/g, ' ') + ']';
      try { return JSON.stringify(item); } catch (_) { return String(item); }
    };
    if (Array.isArray(value)) {
      return value.map(blockText).filter(Boolean).join('\n');
    }
    if (typeof value.text === 'string') return value.text;
    const valueType = value.type || value.kind || '';
    if (valueType === 'input_image' || valueType === 'image') return '[image]';
    if (valueType) return '[' + String(valueType).replace(/_/g, ' ') + ']';
    try { return JSON.stringify(value, null, 2); } catch (_) { return String(value); }
  }

  // Strip a leading URL or absolute file path from a prompt, when followed by
  // more text. Drag-and-dropped screenshots / pasted links land at the start of
  // first_message and would otherwise dominate the auto-generated title.
  // Only strips when there's actual prose after the path.
  const LEADING_PATH_OR_URL_RE = /^(?:https?:\/\/\S+|file:\/\/\S+|\S*\/\S*\.[A-Za-z0-9]{1,8})\s+(?=\S)/;
  function stripLeadingPathOrUrl(s) {
    if (!s) return s;
    return String(s).replace(LEADING_PATH_OR_URL_RE, '');
  }
  // Extract the first sentence / line from a blob of text. Used to derive a
  // usable title when the original prompt is a multi-paragraph body.
  function firstSentenceOf(text, maxLen = 90) {
    if (!text) return '';
    const src = stripLeadingPathOrUrl(String(text).trim());
    const chunks = src.split(/(?<=[.!?])\s+|\n+/).map(s => s.trim()).filter(Boolean);
    let first = chunks[0] || src;
    if (first.length > maxLen) {
      // Back up to the last word boundary so we don't cut mid-word like
      // "documentation of al…". 8-char minimum stops the boundary search
      // from collapsing very-long-first-word strings to a useless stub.
      let cut = first.slice(0, maxLen);
      const lastSpace = cut.lastIndexOf(' ');
      if (lastSpace > 8) cut = cut.slice(0, lastSpace);
      first = cut.trim() + '…';
    }
    return first;
  }
  // Split a prompt into [first sentence, rest] so the sticky-header
  // "Original ask" box can render the first sentence normal-weight and
  // the remainder as small grey text. No truncation — the box wraps and
  // is height-clamped, no horizontal scroll.
  function splitFirstSentence(text) {
    if (!text) return ['', ''];
    const s = String(text).trim();
    // Split only on real sentence terminators (.!?) followed by whitespace.
    // A bare newline is NOT a split point — the user's first sentence often
    // wraps mid-clause (e.g. `btw the "Recap\nlatest assistant turn"`).
    const m = s.match(/^([\s\S]*?[.!?])\s+([\s\S]+)$/);
    if (!m) return [s, ''];
    return [m[1].trim(), m[2].trim()];
  }
  // Tiny inline-markdown renderer for card descriptions. Handles bold, code,
  // headings and bullet lines — enough to preserve Claude's summary formatting
  // without pulling in a full markdown lib.
  function renderInlineMd(raw) {
    if (!raw) return '';
    let s = String(raw)
      .replace(/```[\s\S]*?```/g, '')   // drop fenced code blocks
      .replace(/^#{1,6}\s+/gm, '')       // drop heading markers
      .trim();
    let html = escapeHtml(s)
      .replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>')
      .replace(/`([^`\n]+?)`/g, '<code>$1</code>');
    // Bullet lines → glyph; otherwise hard line breaks
    html = html.replace(/^\s*[-*]\s+/gm, '• ').replace(/\n/g, '<br>');
    return html;
  }
  async function loadAppConfig() {
    try {
      const res = await fetch('/api/config');
      if (res.ok) {
        const cfg = await res.json();
        APP_CONFIG = Object.assign(APP_CONFIG, cfg);
      }
    } catch (_) {}
    rebuildTitleStripRe(APP_CONFIG.title_strip);
    applyConfigToDom();
  }
  function applyConfigToDom() {
    // Vercel deploy panel stays visible always — pollVercelDeploy swaps in
    // a "Not configured" message when the project has no .vercel/project.json
    // (and no $VERCEL_PROJECT env override). Hiding it per-repo hid the
    // feature itself; keeping it visible nudges users to `vercel link`.
    const $kptDeploy = document.getElementById('kptDeployStatus');
    if ($kptDeploy && !APP_CONFIG.vercel_enabled) $kptDeploy.style.display = 'none';
    document.querySelectorAll('.pkood-toggle-label').forEach(el => {
      if (!APP_CONFIG.pkood_enabled) el.style.display = 'none';
    });
  }
  loadAppConfig();

  // ── Repo selection state ──
  // The repo dropdown is a local archive filter, not a server-side switch.
  // Keep this block early: worktrees, Vercel, terminal, and issue actions
  // can all run during startup and need a concrete selected repo when scoped.
  const ARCHIVE_FOLDER_ALL = '__all__';
  const ARCHIVE_FOLDER_FILTER_KEY = 'ccc-archive-folder-filter';
  const $convFolderFilter = document.getElementById('convFolderFilter');
  let repoListState = { repos: [], current: '', recent: [] };
  let archiveFolderFilter = (() => {
    try { return localStorage.getItem(ARCHIVE_FOLDER_FILTER_KEY) || ARCHIVE_FOLDER_ALL; }
    catch (_) { return ARCHIVE_FOLDER_ALL; }
  })();

  function _pathLeaf(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    return parts[parts.length - 1] || String(path || '');
  }

  function selectedRepoPath() {
    return archiveFolderFilter && archiveFolderFilter !== ARCHIVE_FOLDER_ALL
      ? archiveFolderFilter
      : '';
  }

  function selectedRepoLabel() {
    const repoPath = selectedRepoPath();
    if (!repoPath) return '';
    const match = (repoListState.repos || []).find(repo => repo.path === repoPath);
    return (match && (match.label || match.path)) || _pathLeaf(repoPath) || repoPath;
  }

  function requireSelectedRepo(actionLabel) {
    const repoPath = selectedRepoPath();
    if (repoPath) return repoPath;
    showOpToast((actionLabel || 'This action') + ' needs a concrete repo. Pick one from the repo dropdown first.', 'error');
    if ($convFolderFilter) {
      try { $convFolderFilter.focus(); } catch (_) {}
      $convFolderFilter.classList.add('needs-repo-attention');
      setTimeout(() => { $convFolderFilter.classList.remove('needs-repo-attention'); }, 1600);
    }
    return '';
  }

  function repoUrl(path, repoPath, extraParams) {
    const concrete = repoPath || selectedRepoPath();
    if (!concrete) return '';
    const u = new URL(path, window.location.href);
    u.searchParams.set('repo_path', concrete);
    if (extraParams) {
      for (const [k, v] of Object.entries(extraParams)) {
        if (v !== undefined && v !== null && v !== '') u.searchParams.set(k, v);
      }
    }
    return u.pathname + u.search;
  }

  function withRepoPath(body, repoPath) {
    const concrete = repoPath || selectedRepoPath();
    const out = Object.assign({}, body || {});
    if (concrete) out.repo_path = concrete;
    return out;
  }

  function rowRepoPath(row) {
    return (row && (row.repo_path || row.folder_path || row.spawn_cwd)) || '';
  }

  function repoPathForIssueNumber(issueNum) {
    const num = String(issueNum || '');
    const row = conversationsData.find(c =>
      c && c.source === 'backlog' && String(c.issue_number || '') === num
    );
    return rowRepoPath(row) || selectedRepoPath();
  }

  function _pathParentLeaf(path) {
    const parts = String(path || '').split('/').filter(Boolean);
    return parts.length > 1 ? parts[parts.length - 2] : '';
  }

  Object.assign(window, {
    selectedRepoPath,
    selectedRepoLabel,
    requireSelectedRepo,
    repoUrl,
    withRepoPath,
    rowRepoPath,
    repoPathForIssueNumber,
  });

  let sessionIdByConv = {}; // {convId: sessionId}
  let sessionCwdByConv = {}; // {convId: originalCwd}
  let sessionCwdExistsByConv = {}; // {convId: bool}
  let sessionSourceByConv = {}; // {convId: 'interactive'|'pkood'|'task'}
  let sessionSpawnPidByConv = {}; // {convId: pid of claude we spawned (stdin inject)}
  // Currently-focused session and its live-process state (per-pane, shimmed via window.currentSession)
  let liveStatus = { live: false, pid: null, tty: null, terminalApp: null, sidecarTool: null, sidecarFile: null, sidecarStatus: null, sidecarTs: 0, sidecarInFlight: false };
  let liveStatusTimer = null;
  // Separate 1s tick that just re-renders the live-tool strip + inline
  // indicator from the cached liveStatus. The 5s poller refreshes the
  // *data*; this ticker keeps the "running 3s / 4s / 5s" age label
  // changing every second so the user sees motion.
  let liveStatusRenderTicker = null;

  const $jumpBtnConv = document.getElementById('jumpBtnConv');
  const $launchWrapConv = document.getElementById('launchWrapConv');
  const $launchBtnConv = document.getElementById('launchBtnConv');
  const $launchChoiceBtnConv = document.getElementById('launchChoiceBtnConv');
  const $launchChoiceMenuConv = document.getElementById('launchChoiceMenuConv');
  const $convToolbar = document.getElementById('convToolbar');
  const $announceBtnConv = document.getElementById('announceBtnConv');
  const $pkoodKillBtn = document.getElementById('pkoodKillBtn');
  let pkoodTailPoller = null; // interval ID for polling pkood tail
  let codexLogPoller = null; // interval ID for tailing a codex spawn log
  const $convRefreshBtn = document.getElementById('convRefreshBtn');

  function shellQuote(s) {
    return "'" + String(s).replace(/'/g, "'\\''") + "'";
  }

  function buildResumeCommand(sid, cwd, cwdExists) {
    const engine = currentSession && currentSession.source;
    const resumeCmd = engine === 'codex'
      ? 'codex resume ' + sid
      : (engine === 'gemini' ? 'gemini --resume ' + sid : 'claude --resume ' + sid + ' --dangerously-skip-permissions');
    if (!cwd) return resumeCmd;
    // Derive worktree branch from a `.claude/worktrees/...` path:
    // e.g. /Users/.../.claude/worktrees/claude-fix/issue-88 -> branch "claude-fix/issue-88"
    const wtMatch = cwd.match(/\/\.claude\/worktrees\/(.+)$/);
    const quotedCwd = shellQuote(cwd);
    if (cwdExists) {
      return 'cd ' + quotedCwd + ' && ' + resumeCmd;
    }
    // Directory missing — try to recreate the worktree first, then resume.
    if (wtMatch) {
      const branch = wtMatch[1];
      const repoRoot = cwd.split('/.claude/worktrees/')[0];
      const quotedRepo = shellQuote(repoRoot);
      const quotedBranch = shellQuote(branch);
      return '(cd ' + quotedRepo + ' && git worktree add ' + quotedCwd + ' ' + quotedBranch +
             ' 2>/dev/null || git worktree add ' + quotedCwd + ' -b ' + quotedBranch + ' origin/main)' +
             ' && cd ' + quotedCwd + ' && ' + resumeCmd;
    }
    return 'cd ' + quotedCwd + ' && ' + resumeCmd;
  }

  function resumeButtonsForActiveTab() { return []; }
  function jumpButtonsForActiveTab() {
    if (activeTab === 'sessions') return [$jumpBtnConv];
    return [];
  }
  function launchButtonsForActiveTab() {
    if (activeTab === 'sessions') return [$launchBtnConv];
    return [];
  }
  function launchWrapsForActiveTab() {
    if (activeTab === 'sessions') return [$launchWrapConv];
    return [];
  }
  function desktopButtonsForActiveTab() { return []; }
  function allResumeButtons() { return []; }
  function allJumpButtons() { return [$jumpBtnConv].filter(Boolean); }
  function allLaunchButtons() { return [$launchBtnConv].filter(Boolean); }
  function allLaunchWraps() { return [$launchWrapConv].filter(Boolean); }
  function allLaunchChoiceButtons() { return [$launchChoiceBtnConv, document.getElementById('cpLaunchChoiceBtn')].filter(Boolean); }
  function allLaunchChoiceMenus() { return [$launchChoiceMenuConv, document.getElementById('cpLaunchChoiceMenu')].filter(Boolean); }
  function allDesktopButtons() { return []; }

  function launchTargetsForCurrentSession() {
    const isCodex = currentSession.source === 'codex';
    const isGemini = currentSession.source === 'gemini';
    const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(currentSession.id || '');
    return [
      { id: 'terminal', label: 'Terminal', hint: 'default', disabled: false },
      {
        id: 'desktop',
        label: 'Claude Desktop',
        hint: (!isCodex && !isGemini && isUuid) ? 'app' : 'Claude only',
        disabled: isCodex || isGemini || !isUuid,
      },
      {
        id: 'codex',
        label: 'Codex',
        hint: isCodex ? 'app' : 'Codex only',
        disabled: !isCodex,
      },
    ];
  }

  function renderLaunchChoiceMenu(menu) {
    if (!menu) return;
    menu.textContent = '';
    for (const target of launchTargetsForCurrentSession()) {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'launch-choice-item';
      item.setAttribute('role', 'menuitem');
      item.dataset.launchTarget = target.id;
      if (target.disabled) {
        item.disabled = true;
        item.title = target.hint;
      }
      const label = document.createElement('span');
      label.textContent = target.label;
      item.appendChild(label);
      const hint = document.createElement('span');
      hint.className = 'launch-choice-hint';
      hint.textContent = target.hint;
      item.appendChild(hint);
      menu.appendChild(item);
    }
  }

  function closeLaunchChoiceMenus() {
    for (const menu of allLaunchChoiceMenus()) {
      menu.classList.remove('open');
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
    renderLaunchChoiceMenu(menu);
    menu.classList.add('open');
    menu.setAttribute('aria-hidden', 'false');
    btn.setAttribute('aria-expanded', 'true');
  }

  function launchButtonFromTrigger(trigger) {
    if (trigger && trigger.closest && trigger.closest('#cpLaunchSplit')) {
      return document.getElementById('cpLaunchBtn');
    }
    return $launchBtnConv;
  }

  function actionButtonSnapshot(btn) {
    const label = btn && btn.querySelector ? btn.querySelector('.jump-label') : null;
    return {
      html: btn ? btn.innerHTML : '',
      label,
      text: label ? label.textContent : (btn ? btn.textContent : ''),
    };
  }

  function setActionButtonText(btn, text) {
    const label = btn && btn.querySelector ? btn.querySelector('.jump-label') : null;
    if (label) label.textContent = text;
    else if (btn) btn.textContent = text;
  }

  function restoreActionButtonText(btn, snapshot) {
    if (!btn || !snapshot) return;
    if (snapshot.label) snapshot.label.textContent = snapshot.text;
    else btn.innerHTML = snapshot.html;
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
    await launchTerminal({ currentTarget: btn });
  }

  // The Resume-in-CLI button used to live in convToolbar; it was removed
  // because every session row already exposes the same command via its
  // copy-button menu. Kept as a no-op shim so existing call sites stay valid.
  function updateResumeButton() {}

  function updateAnnounceButton() {
    if (!$announceBtnConv) return;
    const sid = currentSession.id;
    const isPkood = currentSession.source === 'pkood';
    const isCodex = currentSession.source === 'codex';
    const isGemini = currentSession.source === 'gemini';
    if (!sid || isPkood || isCodex || isGemini) {
      $announceBtnConv.style.display = 'none';
      delete $announceBtnConv.dataset.sessionId;
      return;
    }
    $announceBtnConv.style.display = 'inline-flex';
    $announceBtnConv.dataset.sessionId = sid;
    $announceBtnConv.disabled = false;
    $announceBtnConv.style.opacity = '';
    $announceBtnConv.textContent = 'Close & announce';
  }
  if ($announceBtnConv) {
    $announceBtnConv.addEventListener('click', async (e) => {
      e.stopPropagation();
      const sid = $announceBtnConv.dataset.sessionId;
      if (!sid) return;
      const feature = window.prompt('Announce what? (short feature name)\n\nLeave blank and /announce-feature will ask.', '');
      if (feature === null) return;
      $announceBtnConv.disabled = true;
      $announceBtnConv.textContent = 'Sending...';
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
        $announceBtnConv.textContent = 'Sent ✓';
        $announceBtnConv.style.opacity = '0.6';
      } catch (err) {
        const reason = (err && err.message) || 'unknown error';
        $announceBtnConv.textContent = 'Failed';
        showOpToast('Close & announce failed: ' + reason, 'error');
        setTimeout(() => {
          $announceBtnConv.textContent = 'Close & announce';
          $announceBtnConv.disabled = false;
          $announceBtnConv.style.opacity = '';
        }, 2000);
      }
    });
  }

  // ── ⋯ overflow menu (move-to-repo for now; designed to grow) ───────
  const $convOverflowBtn = document.getElementById('convOverflowBtn');
  const $convOverflowMenu = document.getElementById('convOverflowMenu');
  function _closeConvOverflow() {
    if (!$convOverflowMenu) return;
    $convOverflowMenu.classList.remove('open');
    $convOverflowMenu.setAttribute('aria-hidden', 'true');
    if ($convOverflowBtn) $convOverflowBtn.setAttribute('aria-expanded', 'false');
  }
  function _renderConvOverflowMenu() {
    if (!$convOverflowMenu) return;
    const sid = currentSession && currentSession.id;
    const isPkood = currentSession && currentSession.source === 'pkood';
    const isCodex = currentSession && currentSession.source === 'codex';
    const isGemini = currentSession && currentSession.source === 'gemini';
    const repos = (typeof repoListState !== 'undefined' && repoListState.repos) || [];
    const currentCwd = (currentSession && currentSession.cwd) || '';
    let html = '';
    html += '<div class="com-section-label">Move to repo</div>';
    if (!sid || isPkood || isCodex || isGemini) {
      html += '<div class="com-item com-current">No movable session selected</div>';
    } else if (!repos.length) {
      html += '<div class="com-item com-current">No known repos. Add one in the Repo picker.</div>';
    } else {
      for (const r of repos) {
        const isCurrent = r.path === currentCwd;
        const cls = 'com-item' + (isCurrent ? ' com-current' : '');
        const label = escapeHtml(r.label || r.path);
        const path = escapeHtml(r.path);
        const suffix = isCurrent ? ' <span class="com-repo-path">(current)</span>' : ' <span class="com-repo-path">' + path + '</span>';
        html += '<button type="button" class="' + cls + '" data-target="' + path + '"'
          + (isCurrent ? ' disabled' : '')
          + '>' + label + suffix + '</button>';
      }
    }
    $convOverflowMenu.innerHTML = html;
    $convOverflowMenu.querySelectorAll('button[data-target]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const target = btn.dataset.target;
        const sidNow = currentSession && currentSession.id;
        if (!sidNow || !target) return;
        if (!confirm('Move session to ' + target + '?\n\nThe JSONL file gets relocated; resume will then run in the new repo.')) return;
        btn.disabled = true;
        btn.textContent = 'Moving…';
        try {
          const res = await fetch('/api/sessions/' + encodeURIComponent(sidNow) + '/move', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ repo_path: target }),
          });
          const data = await res.json();
          if (data.ok) {
            _closeConvOverflow();
            // Refresh the conversation list so the moved session shows
            // in its new home (and disappears from the current view if
            // we're not pointed at the target).
            if (typeof refreshConversationList === 'function') refreshConversationList();
            else if (typeof loadConversationList === 'function') loadConversationList();
          } else {
            btn.textContent = 'Failed: ' + (data.error || 'unknown');
          }
        } catch (err) {
          btn.textContent = 'Failed: ' + err.message;
        }
      });
    });
  }
  if ($convOverflowBtn) {
    $convOverflowBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = $convOverflowMenu.classList.contains('open');
      if (isOpen) {
        _closeConvOverflow();
      } else {
        _renderConvOverflowMenu();
        $convOverflowMenu.classList.add('open');
        $convOverflowMenu.setAttribute('aria-hidden', 'false');
        $convOverflowBtn.setAttribute('aria-expanded', 'true');
      }
    });
    document.addEventListener('click', (e) => {
      if (!$convOverflowMenu) return;
      if (!$convOverflowMenu.classList.contains('open')) return;
      if (e.target.closest('.conv-overflow-wrap')) return;
      _closeConvOverflow();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') _closeConvOverflow();
    });
  }

  function updateJumpButton() {
    const live = liveStatus.live;
    const sid = currentSession.id;
    const isPkood = currentSession.source === 'pkood';
    const isCodex = currentSession.source === 'codex';
    const isGemini = currentSession.source === 'gemini';
    const canJump = live && liveStatus.tty && liveStatus.terminalApp;
    const canShowLaunch = !!sid && !isPkood;

    // Jump buttons
    const activeJump = jumpButtonsForActiveTab();
    for (const btn of allJumpButtons()) {
      if (!activeJump.includes(btn)) btn.style.display = 'none';
    }
    for (const btn of activeJump) {
      if (!btn) continue;
      if (canJump) {
        btn.style.display = 'inline-flex';
        btn.title = 'Focus ' + liveStatus.terminalApp + ' (' + liveStatus.tty + ') running this session';
        btn.querySelector('.jump-label').textContent = 'Terminal';
      } else {
        btn.style.display = 'none';
      }
    }

    // Launch split: Terminal remains the default action; the dropdown keeps
    // Claude Desktop / Codex discoverable even when Jump is also visible.
    const activeLaunch = launchButtonsForActiveTab();
    const activeLaunchWraps = launchWrapsForActiveTab();
    for (const wrap of allLaunchWraps()) {
      if (!activeLaunchWraps.includes(wrap)) wrap.style.display = 'none';
    }
    for (const wrap of activeLaunchWraps) {
      if (!wrap) continue;
      wrap.style.display = canShowLaunch ? 'inline-flex' : 'none';
    }
    for (const btn of allLaunchButtons()) {
      if (!activeLaunch.includes(btn)) btn.style.display = 'none';
    }
    for (const btn of activeLaunch) {
      if (!btn) continue;
      if (canShowLaunch) {
        btn.style.display = 'inline-flex';
        btn.title = isCodex ? 'Open a Terminal window and run codex resume'
          : (isGemini ? 'Open a Terminal window and run gemini --resume' : 'Open a Terminal window and run claude --resume');
        btn.querySelector('.jump-label').textContent = 'Launch';
        renderLaunchChoiceMenu($launchChoiceMenuConv);
      } else {
        btn.style.display = 'none';
      }
    }
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
    if (!liveStatus.live) return;
    const origLabel = btn.querySelector('.jump-label').textContent;
    btn.querySelector('.jump-label').textContent = 'Jumping...';
    try {
      const res = await fetch('/api/jump-terminal', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          session_id: currentSession.id,
          cwd: currentSession.cwd,
          tty: liveStatus.tty,
          terminal_app: liveStatus.terminalApp,
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

  async function refreshLiveStatus() {
    if (!currentSession.id) {
      liveStatus = { live: false, pid: null, tty: null, terminalApp: null, ambiguous: false, matchCount: 0 };
      updateJumpButton();
      updateInputBar();
      return;
    }
    try {
      const params = new URLSearchParams({
        session_id: currentSession.id,
        cwd: currentSession.cwd || '',
      });
      const res = await fetch('/api/session-status?' + params.toString());
      const data = await res.json();
      liveStatus = {
        live: !!data.live,
        pid: data.pid || null,
        tty: data.tty || null,
        terminalApp: data.terminal_app || null,
        ambiguous: !!data.ambiguous,
        matchCount: data.match_count || 0,
        sidecarTool: data.sidecar_tool || null,
        sidecarFile: data.sidecar_file || null,
        sidecarStatus: data.sidecar_status || null,
        sidecarTs: data.sidecar_ts || 0,
        sidecarInFlight: !!data.sidecar_in_flight,
      };
      // Mirror the freshly-fetched sidecar fields back into the cached
      // sidebar row so the left list catches up to the right pane on
      // the same 5s tick. Without this, the row's yellow live-tool pill
      // and truly-active glow lag by up to the bulk /api/sessions
      // interval (10s) — long enough that users notice the right pane
      // lighting up before the left does. Only the active session is
      // mirrored; other rows still update on the bulk poll.
      if (currentSession.id && Array.isArray(conversationsData)) {
        const row = conversationsData.find(
          (c) => c.session_id === currentSession.id || c.id === currentSession.id,
        );
        if (row) {
          row.is_live = !!data.live;
          row.sidecar_tool = data.sidecar_tool || null;
          row.sidecar_file = data.sidecar_file || null;
          row.sidecar_status = data.sidecar_status || null;
          row.sidecar_ts = data.sidecar_ts || 0;
          row.sidecar_in_flight = !!data.sidecar_in_flight;
          if (typeof renderSidebar === 'function' && typeof filterConversations === 'function' && typeof $convSearch !== 'undefined' && $convSearch) {
            renderSidebar(filterConversations($convSearch.value));
          }
        }
      }
    } catch (err) {
      liveStatus = { live: false, pid: null, tty: null, terminalApp: null, ambiguous: false, matchCount: 0, sidecarTool: null, sidecarFile: null, sidecarStatus: null, sidecarTs: 0, sidecarInFlight: false };
    }
    updateJumpButton();
    updateInputBar();
    updateLiveToolStrip();
    if (typeof updateSplitToolbar === 'function') updateSplitToolbar();
  }

  function startLiveStatusPolling() {
    if (liveStatusTimer) clearInterval(liveStatusTimer);
    if (liveStatusRenderTicker) clearInterval(liveStatusRenderTicker);
    refreshLiveStatus();
    liveStatusTimer = setInterval(refreshLiveStatus, 5000);
    liveStatusRenderTicker = setInterval(updateLiveToolStrip, 1000);
  }

  // Render a live "what's running right now" strip docked above the
  // conversation transcript. Reads from liveStatus (refreshed every 5s
  // by /api/session-status) so the chat pane shows the same in-progress
  // signal the kanban card now shows.
  // Optimistic "agent starting" indicator. Rendered the moment the user
  // hits send so they see motion before /api/session-status (5s poll)
  // catches up to the real sidecar event. Auto-removed once the real
  // indicator lands, or on a 60s safety timeout.

  // Session IDs that just had a message sent to them — used to render a
  // matching "Sending…" pill on the sidebar row so the left list mirrors
  // the right pane's optimistic indicator. Cleared when real sidecar data
  // lands (via clearOptimisticAgentIndicator) or after the 60s safety
  // timeout. Each entry: { ts, timer }.
  const _sendingSessions = new Map();
  const _optimisticSessionTouches = new Map();
  const _SENDING_TIMEOUT_MS = 60000;
  function _sessionRowMatches(c, sid) {
    if (!c || !sid) return false;
    return (c.session_id === sid) || (c.id === sid);
  }
  function _applyOptimisticTouchToRow(c, sid, ts) {
    if (!_sessionRowMatches(c, sid)) return false;
    const t = Number(ts) || Math.floor(Date.now() / 1000);
    c.last_interacted = Math.max(Number(c.last_interacted || 0), t);
    c.modified = Math.max(Number(c.modified || 0), t);
    if ('mtime' in c) c.mtime = Math.max(Number(c.mtime || 0), t);
    return true;
  }
  function _applyOptimisticTouches(rows) {
    if (!Array.isArray(rows) || !_optimisticSessionTouches.size) return rows;
    for (const c of rows) {
      const sid = (c && (c.session_id || c.id)) || '';
      const ts = _optimisticSessionTouches.get(sid);
      if (ts) _applyOptimisticTouchToRow(c, sid, ts);
    }
    return rows;
  }
  function touchSessionOptimistically(sid) {
    if (!sid) return;
    const ts = Math.floor(Date.now() / 1000);
    _optimisticSessionTouches.set(sid, ts);
    for (const rows of [conversationsData, archiveData]) {
      if (!Array.isArray(rows)) continue;
      for (const c of rows) _applyOptimisticTouchToRow(c, sid, ts);
    }
  }
  function markSessionSending(sid) {
    if (!sid) return;
    const existing = _sendingSessions.get(sid);
    if (existing && existing.timer) clearTimeout(existing.timer);
    const timer = setTimeout(() => clearSessionSending(sid), _SENDING_TIMEOUT_MS);
    _sendingSessions.set(sid, { ts: Date.now(), timer });
    touchSessionOptimistically(sid);
    if (typeof renderSidebar === 'function' && typeof filterConversations === 'function' && typeof $convSearch !== 'undefined' && $convSearch) {
      renderSidebar(filterConversations($convSearch.value));
    }
  }
  function clearSessionSending(sid) {
    if (!sid) return;
    const entry = _sendingSessions.get(sid);
    if (!entry) return;
    if (entry.timer) clearTimeout(entry.timer);
    _sendingSessions.delete(sid);
    if (typeof renderSidebar === 'function' && typeof filterConversations === 'function' && typeof $convSearch !== 'undefined' && $convSearch) {
      renderSidebar(filterConversations($convSearch.value));
    }
  }

  let _optimisticAgentTimer = null;
  function showOptimisticAgentIndicator($view) {
    if (!$view) return;
    let el = $view.querySelector('.conv-live-tool-inline.optimistic');
    if (!el) {
      el = document.createElement('div');
      el.className = 'conv-live-tool-inline optimistic';
    }
    el.innerHTML = '<span class="cl-pulse"></span>'
      + '<span class="cl-tool">Sending&hellip;</span>'
      + '<span class="cl-age">just now</span>';
    $view.appendChild(el);
    if (_optimisticAgentTimer) clearTimeout(_optimisticAgentTimer);
    _optimisticAgentTimer = setTimeout(() => {
      const stale = $view.querySelector('.conv-live-tool-inline.optimistic');
      if (stale) stale.remove();
      _optimisticAgentTimer = null;
    }, 60000);
  }
  function clearOptimisticAgentIndicator($view) {
    const el = ($view || document).querySelector('.conv-live-tool-inline.optimistic');
    if (el) el.remove();
    if (_optimisticAgentTimer) {
      clearTimeout(_optimisticAgentTimer);
      _optimisticAgentTimer = null;
    }
  }

  function updateLiveToolStrip() {
    const $view = (typeof getConvView === 'function') ? getConvView() : null;
    if (!$view) return;
    let strip = document.querySelector('.conv-live-tool-strip');
    // Match only the *real* inline indicator — leave the optimistic
    // twin alone so it lingers until either real data lands or its
    // own 60s safety timeout fires.
    let inline = $view.querySelector('.conv-live-tool-inline:not(.optimistic)');
    const tool = liveStatus.sidecarTool;
    const ts = liveStatus.sidecarTs || 0;
    const ageSec = ts ? Math.max(0, Math.floor(Date.now() / 1000 - ts)) : 9999;
    const shouldShow = liveStatus.live && tool && liveStatus.sidecarStatus === 'active' && ageSec < 300;
    if (!shouldShow) {
      if (strip) strip.remove();
      if (inline) inline.remove();
      return;
    }
    // Real data arrived — drop the optimistic placeholder (right pane)
    // and the matching "Sending..." pill on the sidebar row.
    clearOptimisticAgentIndicator($view);
    if (currentSession.id) clearSessionSending(currentSession.id);
    const file = liveStatus.sidecarFile || '';
    const shortFile = file
      ? (file.length > 60 ? '…' + file.slice(-59) : file)
      : '';
    const dur = ageSec < 2 ? '<1s' : ageSec < 60 ? ageSec + 's' : Math.floor(ageSec / 60) + 'm';
    const inFlight = !!liveStatus.sidecarInFlight;
    const ageLbl = inFlight ? 'running ' + dur : dur + ' ago';
    const html =
        '<span class="cl-pulse"></span>'
      + '<span class="cl-tool">' + (inFlight ? '▶ ' : '') + escapeHtml(tool) + '</span>'
      + (shortFile ? ' <span class="cl-file">' + escapeHtml(shortFile) + '</span>' : '')
      + '<span class="cl-age">' + ageLbl + '</span>';
    if (!strip) {
      strip = document.createElement('div');
      strip.className = 'conv-live-tool-strip';
      $view.insertBefore(strip, $view.firstChild);
    } else if (strip.parentElement !== $view) {
      $view.insertBefore(strip, $view.firstChild);
    }
    strip.classList.toggle('in-flight', inFlight);
    strip.innerHTML = html;
    // Inline twin at the bottom of the transcript. Re-append on every
    // refresh so it stays the last child even when new events have
    // streamed in since the last poll.
    if (!inline) {
      inline = document.createElement('div');
      inline.className = 'conv-live-tool-inline';
    }
    inline.classList.toggle('in-flight', inFlight);
    inline.innerHTML = html;
    if (inline.parentElement !== $view || inline !== $view.lastElementChild) {
      $view.appendChild(inline);
    }
  }

  const $convSessionId = document.getElementById('convSessionId');

  async function copyTextValue(text) {
    const value = String(text || '');
    if (!value) return false;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      try {
        await navigator.clipboard.writeText(value);
        return true;
      } catch (_) {}
    }
    const ta = document.createElement('textarea');
    ta.value = value;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) {}
    ta.remove();
    return ok;
  }

  function setCopyableSessionId(el, sid) {
    if (!el) return;
    const value = sid || '';
    el.textContent = value;
    if (value) {
      el.dataset.copySessionId = value;
      el.title = 'Click to copy session ID';
    } else {
      delete el.dataset.copySessionId;
      el.title = '';
    }
  }

  document.addEventListener('click', async (ev) => {
    const el = ev.target.closest('[data-copy-session-id]');
    if (!el) return;
    ev.stopPropagation();
    const sid = el.dataset.copySessionId || el.textContent || '';
    const ok = await copyTextValue(sid);
    if (!ok) {
      showOpToast('Copy failed — select and copy manually', 'error');
      return;
    }
    el.textContent = 'copied!';
    el.classList.add('copied');
    setTimeout(() => {
      if (el.dataset.copySessionId === sid) el.textContent = sid;
      el.classList.remove('copied');
    }, 1000);
  });

  function setCurrentSession(source, sid, cwd, cwdExists, spawnPid, repoPath) {
    currentSession = {
      id: sid || null,
      cwd: cwd || null,
      cwdExists: !!cwdExists,
      source: source,
      spawnPid: spawnPid || null,
      repoPath: repoPath || null,
    };
    // Leaving new-session mode (sid set) drops the .is-new-session class
    // so the spawn-cwd picker hides and the workspace pill returns. The
    // class is set in enterNewSessionMode(); this is the symmetric clear.
    if (sid) {
      const _cic = document.getElementById('convInputContext');
      if (_cic) _cic.classList.remove('is-new-session');
    }
    setCopyableSessionId($convSessionId, sid);
    if (source === 'pkood') {
      // Pkood sessions don't need live status polling or resume button
      if (liveStatusTimer) { clearInterval(liveStatusTimer); liveStatusTimer = null; }
      if (liveStatusRenderTicker) { clearInterval(liveStatusRenderTicker); liveStatusRenderTicker = null; }
      liveStatus = { live: false, pid: null, tty: null, terminalApp: null };
      updateResumeButton();
      updateAnnounceButton();
      updateJumpButton();
      updateInputBar();
    } else {
      updateResumeButton();
      updateAnnounceButton();
      startLiveStatusPolling();
    }
  }

  async function launchTerminal(ev) {
    const btn = ev && ev.currentTarget;
    if (!btn || !currentSession.id) return;
    const snapshot = actionButtonSnapshot(btn);
    btn.classList.add('launching');
    btn.disabled = true;
    setActionButtonText(btn, 'Launching...');
    try {
      const res = await fetch('/api/launch-terminal', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          session_id: currentSession.id,
          cwd: currentSession.cwd,
        }),
      });
      const data = await res.json();
      if (data.ok) {
        setActionButtonText(btn, 'Launched!');
        setTimeout(() => {
          btn.classList.remove('launching');
          btn.disabled = false;
          restoreActionButtonText(btn, snapshot);
        }, 1500);
        // The launched terminal will become live shortly — poll right away
        setTimeout(refreshLiveStatus, 1500);
      } else {
        setActionButtonText(btn, 'Failed: ' + (data.error || 'unknown'));
        setTimeout(() => {
          btn.classList.remove('launching');
          btn.disabled = false;
          restoreActionButtonText(btn, snapshot);
        }, 3000);
      }
    } catch (err) {
      btn.classList.remove('launching');
      setActionButtonText(btn, 'Error');
      setTimeout(() => {
        btn.disabled = false;
        restoreActionButtonText(btn, snapshot);
      }, 2000);
    }
  }

  async function openInClaudeDesktop(ev) {
    const btn = ev && ev.currentTarget;
    if (!btn || !currentSession.id) return;
    if (currentSession.source === 'codex' || currentSession.source === 'gemini') return;
    const snapshot = actionButtonSnapshot(btn);
    btn.classList.add('opening');
    btn.disabled = true;
    setActionButtonText(btn, 'Opening...');
    try {
      const res = await fetch('/api/open-in-desktop', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: currentSession.id }),
      });
      const data = await res.json();
      if (data.ok) {
        setActionButtonText(btn, 'Opened!');
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.disabled = false;
          restoreActionButtonText(btn, snapshot);
        }, 1500);
      } else {
        setActionButtonText(btn, 'Failed: ' + (data.error || 'unknown'));
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.disabled = false;
          restoreActionButtonText(btn, snapshot);
        }, 3000);
      }
    } catch (err) {
      btn.classList.remove('opening');
      setActionButtonText(btn, 'Error');
      setTimeout(() => {
        btn.disabled = false;
        restoreActionButtonText(btn, snapshot);
      }, 2000);
    }
  }

  async function openInCodexDesktop(ev) {
    const btn = ev && ev.currentTarget;
    if (!btn || !currentSession.id) return;
    if (currentSession.source !== 'codex') return;
    const snapshot = actionButtonSnapshot(btn);
    btn.classList.add('opening');
    btn.disabled = true;
    setActionButtonText(btn, 'Opening...');
    try {
      const res = await fetch('/api/open-in-codex', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: currentSession.id, cwd: currentSession.cwd }),
      });
      const data = await res.json();
      if (data.ok) {
        setActionButtonText(btn, 'Opened!');
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.disabled = false;
          restoreActionButtonText(btn, snapshot);
        }, 1500);
      } else {
        setActionButtonText(btn, 'Failed: ' + (data.error || 'unknown'));
        setTimeout(() => {
          btn.classList.remove('opening');
          btn.disabled = false;
          restoreActionButtonText(btn, snapshot);
        }, 3000);
      }
    } catch (err) {
      btn.classList.remove('opening');
      setActionButtonText(btn, 'Error');
      setTimeout(() => {
        btn.disabled = false;
        restoreActionButtonText(btn, snapshot);
      }, 2000);
    }
  }

  // ── Inject-to-terminal input bar ──
  const $convInputBar = document.getElementById('convInputBar');
  const $convInput = document.getElementById('convInput');
  const $convSendBtn = document.getElementById('convSendBtn');
  const $convEscBtn = document.getElementById('convEscBtn');
  const $convTtyLabel = document.getElementById('convTtyLabel');

  function issueInputPlaceholder(issueNum) {
    const $ia = document.getElementById('convInputIssueAction');
    const action = ($ia && $ia.value) || 'spawn';
    if (action === 'needs_attention') return 'Add a GitHub comment; submit to mark issue #' + issueNum + ' Needs-Attention…';
    if (action === 'close') return 'Add a GitHub comment; submit to close issue #' + issueNum + '…';
    if (action === 'duplicate') return 'Duplicate of issue # (enter the number)';
    if (action === 'completed' || action === 'not_planned') return 'Optional comment (or submit blank to close immediately)';
    return 'Add edit instructions; submit to start session for issue #' + issueNum + '…';
  }

  function updateInputBar() {
    if (!$convInputBar) return;
    const isPkood = currentSession.source === 'pkood';
    const isCodex = currentSession.source === 'codex';
    const isGemini = currentSession.source === 'gemini';
    const live = liveStatus.live && liveStatus.tty;
    const isConvTab = activeTab === 'sessions';
    const hasSession = !!currentSession.id;
    const isNewSession = currentConversation === '__new__';
    // Backlog GH issue: viewing an issue card in the right pane. Submitting
    // the input bar spawns a session equivalent to clicking "Edit & start"
    // on the kanban card, with the typed text appended to the standard
    // issue-context preamble.
    const currentBacklogRow = conversationsData.find(x => x.id === currentConversation && x.source === 'backlog');
    const isBacklogIssue = !!currentBacklogRow || (currentConversation || '').startsWith('backlog-issue-');
    // Show the bar for any selected session — /api/inject-input routes to
    // live-TTY keystroke injection when the session is alive and falls back
    // to headless `claude --resume` when dormant, so the user can always
    // type a follow-up. Previously hidden for dormant sessions, which left
    // the user with Resume/Launch buttons and no way to just send a message.
    // Also show in "new session" mode where the input doubles as the
    // prompt for spawning a fresh agent.
    if (isConvTab && (hasSession || isNewSession || isBacklogIssue)) {
      $convInputBar.classList.add('visible');
      if (isBacklogIssue) {
        const n = (currentBacklogRow && currentBacklogRow.issue_number) || currentConversation.replace('backlog-issue-', '');
        $convTtyLabel.textContent = '#' + n;
        const $issueAction = document.getElementById('convInputIssueAction');
        if ($issueAction) $issueAction.style.display = '';
        $convInput.placeholder = issueInputPlaceholder(n);
      } else if (isNewSession) {
        $convTtyLabel.textContent = 'new';
        const cwdForPrompt = (typeof getSpawnCwd === 'function' && getSpawnCwd()) || selectedRepoPath();
        const repoLabel = selectedRepoLabel() || _pathLeaf(cwdForPrompt);
        $convInput.placeholder = repoLabel
          ? 'Type a prompt to start a new session in ' + repoLabel + '…'
          : 'Pick a folder before starting a new session…';
      } else if (isPkood) {
        $convTtyLabel.textContent = 'pkood';
        $convInput.placeholder = 'Send to pkood agent...';
      } else if (isCodex) {
        $convTtyLabel.textContent = live ? (liveStatus.tty || 'codex') : 'codex';
        $convInput.placeholder = live ? 'Send to Codex terminal...' : 'Resume Codex and send...';
      } else if (isGemini) {
        $convTtyLabel.textContent = live ? (liveStatus.tty || 'gemini') : 'gemini';
        $convInput.placeholder = live ? 'Send to Gemini terminal...' : 'Resume Gemini and send...';
      } else if (live) {
        $convTtyLabel.textContent = liveStatus.tty;
        $convInput.placeholder = 'Send to terminal...';
      } else {
        $convTtyLabel.textContent = 'dormant';
        $convInput.placeholder = 'Resume and send…';
      }
      // Esc only makes sense when there's something live to interrupt — and
      // we don't support pkood interrupts. Hide it everywhere else so the
      // button doesn't tease an action that will just error.
      if ($convEscBtn) {
        const canEsc = hasSession && !isPkood && !isNewSession && !isBacklogIssue && !!liveStatus.live;
        $convEscBtn.style.display = canEsc ? '' : 'none';
      }
      // Issue action selector: only for backlog issues.
      { const $ia = document.getElementById('convInputIssueAction'); if ($ia && !isBacklogIssue) $ia.style.display = 'none'; }
      // Engine selector occupies the same slot as Esc but only matters
      // when the input is about to spawn a fresh session. Backlog-issue
      // mode also spawns, but its `/api/sessions/spawn` flow is
      // engine-fixed (claude) — don't show a misleading toggle there.
      if ($convInputEngineSelect) {
        $convInputEngineSelect.style.display = isNewSession ? '' : 'none';
      }
    } else {
      $convInputBar.classList.remove('visible');
    }
  }

  async function sendToTerminal(paneId) {
    if (paneId) {
      const idx = paneIndexByPaneId(paneId);
      if (idx >= 0) splitState.activeIndex = idx;
    }
    // Look up the input and send-button scoped to the target pane.
    // The static-HTML p1 element retains the global ids; cloned panes
    // (built by buildPaneElement) had their ids stripped, so we have
    // to query by class/tag scoped to the pane element.
    const _paneEl = document.querySelector(`.conv-pane[data-pane-id="${paneId || activePaneId()}"]`);
    const $input = (_paneEl && _paneEl.querySelector('input[type="text"]')) || $convInput;
    const $sendBtn = (_paneEl && _paneEl.querySelector('.send-btn')) || $convSendBtn;
    const text = ($input && $input.value || '').trim();
    // Backlog GH issue: dispatch based on the action selector.
    if ((currentConversation || '').startsWith('backlog-issue-') ||
        conversationsData.some(x => x.id === currentConversation && x.source === 'backlog')) {
      const $actionSel = document.getElementById('convInputIssueAction');
      const action = $actionSel ? $actionSel.value : 'fix';
      if (action === 'spawn') {
        if (!text) return;
        await spawnFromBacklogIssue(text);
      } else if (action === 'needs_attention' || action === 'close') {
        if (!text) return;
        await replyToIssueFromInputBar(action, text);
      } else {
        await closeIssueFromInputBar(action, text);
      }
      return;
    }
    if (!text) return;
    // New-session mode: input doubles as the prompt for a fresh spawn.
    if (currentConversation === '__new__') {
      await spawnFromInlineInput(text);
      return;
    }
    if (!currentSession.id) return;
    $sendBtn.disabled = true;
    const flashRed = () => {
      $input.style.borderColor = 'var(--red)';
      setTimeout(() => { $input.style.borderColor = ''; }, 1500);
    };
    try {
      let res;
      if (currentSession.source === 'pkood') {
        const agentId = currentConversation.replace(/^pkood-/, '');
        res = await fetch('/api/pkood/inject', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ agent_id: agentId, message: text }),
        });
      } else {
        res = await fetch('/api/inject-input', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ session_id: currentSession.id, text }),
        });
      }
      let data = {};
      try { data = await res.json(); } catch (_) {}
      if (res.ok && data.ok) {
        // Optimistic echo: append an italicized pending entry so the user sees
        // their message immediately. renderConversationEvents replaces it when
        // the real user_text event arrives from the jsonl tail.
        const $view = getConvView();
        if ($view) {
          const pendingDiv = document.createElement('div');
          pendingDiv.className = 'event user_text pending';
          pendingDiv.innerHTML = '<span class="label">User</span>'
            + '<div class="user-msg">' + escapeHtml(text) + '</div>';
          $view.appendChild(pendingDiv);
          // Optimistic "agent starting" indicator — yellow glowing dot in
          // the same shape as the live-tool inline indicator, so the user
          // gets immediate feedback that work has begun without waiting
          // for the next 5s liveStatus poll. Updated by updateLiveToolStrip
          // once a real sidecar event lands; otherwise self-removes after
          // 60s in case the agent never came up.
          showOptimisticAgentIndicator($view);
          // Mirror the optimistic "Sending..." indicator onto the
          // sidebar row so the left list shows the same yellow-glow
          // immediate feedback the right pane shows.
          if (currentSession.id) markSessionSending(currentSession.id);
          $view.scrollTop = $view.scrollHeight;
          _pendingSends.push({ text, element: pendingDiv });
        }
        $input.value = '';
      } else {
        flashRed();
        const reason = formatInjectFailure(data, res.status);
        showOpToast('Send failed: ' + reason, 'error');
      }
    } catch (err) {
      flashRed();
      showOpToast('Send failed: ' + (err.message || 'network error'), 'error');
    }
    $sendBtn.disabled = false;
    $input.focus();
  }

  function formatInjectFailure(data, status) {
    if (data && (data.code === 'macos_keystroke_permission' || data.code === 'macos_automation_permission')) {
      return data.error || 'macOS blocked CCC from sending input to the terminal.';
    }
    if (data && (data.error || data.message)) return data.error || data.message;
    return 'HTTP ' + status;
  }

  async function sendEscToTerminal() {
    if (!currentSession.id) return;
    if (currentSession.source === 'pkood') return;
    if (!$convEscBtn) return;
    $convEscBtn.disabled = true;
    $convEscBtn.classList.remove('sent', 'failed');
    const orig = $convEscBtn.textContent;
    try {
      const res = await fetch('/api/inject-esc', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: currentSession.id }),
      });
      let data = {};
      try { data = await res.json(); } catch (_) {}
      if (res.ok && data.ok) {
        $convEscBtn.classList.add('sent');
        $convEscBtn.textContent = data.via === 'spawn-sigint' ? 'Killed' : 'Esc ✓';
        if (data.note) showOpToast(data.note, 'info');
      } else {
        $convEscBtn.classList.add('failed');
        $convEscBtn.textContent = 'Esc ✗';
        showOpToast('Interrupt failed: ' + (data.error || ('HTTP ' + res.status)), 'error');
      }
    } catch (err) {
      $convEscBtn.classList.add('failed');
      $convEscBtn.textContent = 'Esc ✗';
      showOpToast('Interrupt failed: ' + (err && err.message || 'network error'), 'error');
    }
    setTimeout(() => {
      if (!$convEscBtn) return;
      $convEscBtn.classList.remove('sent', 'failed');
      $convEscBtn.textContent = orig;
      $convEscBtn.disabled = false;
    }, 1200);
  }

  if ($convEscBtn) $convEscBtn.addEventListener('click', sendEscToTerminal);
  if ($convSendBtn) $convSendBtn.addEventListener('click', () => sendToTerminal('p1'));
  if ($convInput) $convInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); sendToTerminal(); }
  });

  for (const btn of allResumeButtons()) btn.addEventListener('click', copyResumeCommand);
  for (const btn of allJumpButtons()) btn.addEventListener('click', jumpToTerminal);
  for (const btn of allLaunchButtons()) btn.addEventListener('click', launchTerminal);
  for (const btn of allDesktopButtons()) btn.addEventListener('click', openInClaudeDesktop);
  if ($launchChoiceBtnConv) {
    $launchChoiceBtnConv.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleLaunchChoiceMenu($launchChoiceBtnConv, $launchChoiceMenuConv);
    });
  }
  const $cpLaunchChoiceBtnGlobal = document.getElementById('cpLaunchChoiceBtn');
  const $cpLaunchChoiceMenuGlobal = document.getElementById('cpLaunchChoiceMenu');
  if ($cpLaunchChoiceBtnGlobal) {
    $cpLaunchChoiceBtnGlobal.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleLaunchChoiceMenu($cpLaunchChoiceBtnGlobal, $cpLaunchChoiceMenuGlobal);
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
    if (e.key === 'Escape') closeLaunchChoiceMenus();
  });

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Replace literal `.claude/pasted-images/paste-*.{png,jpg,…}` paths inside
  // an already-escapeHtml'd string with inline <img> tags. The server's
  // /api/pasted-image route only serves files actually under `.claude/
  // pasted-images/` inside the user's home, so a malformed match resolves
  // to a broken image rather than something dangerous. Reuses .msg-image
  // so the existing lightbox click handler picks them up for free.
  const PASTED_IMG_RE = /(\/[^\s<>"]*?\/\.claude\/pasted-images\/paste-[\w.-]+?\.(?:png|jpe?g|gif|webp))/gi;
  function linkifyPastedImages(escapedHtml) {
    if (!escapedHtml) return escapedHtml;
    return String(escapedHtml).replace(PASTED_IMG_RE, (m) =>
      '<img class="msg-image" src="/api/pasted-image?path=' + encodeURIComponent(m) + '" alt="pasted image">'
    );
  }

  // Subtle timestamp span shown next to line-num. Returns '' when ts is missing/unparseable.
  // Tiers:
  //   < 1m        → "just now"
  //   < 1h        → "N minutes ago"
  //   < 5h        → "N hours ago"
  //   same day    → "HH:MM"
  //   yesterday   → "Yesterday · HH:MM"
  //   older       → "MMM D · HH:MM"
  // Tooltip always shows the full localized date-time.
  function tsSpan(ts) {
    if (!ts) return '';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    const diffHour = Math.floor(diffMin / 60);
    const pad = n => String(n).padStart(2, '0');
    const hhmm = pad(d.getHours()) + ':' + pad(d.getMinutes());

    let label;
    if (diffMs < 60000) {
      // Also catches small negative skews — treat as "just now" rather than showing "N minutes ago" in the future.
      label = 'just now';
    } else if (diffMin < 60) {
      label = diffMin === 1 ? '1 minute ago' : diffMin + ' minutes ago';
    } else if (diffHour < 5) {
      label = diffHour === 1 ? '1 hour ago' : diffHour + ' hours ago';
    } else {
      const sameDay = d.getFullYear() === now.getFullYear()
        && d.getMonth() === now.getMonth()
        && d.getDate() === now.getDate();
      if (sameDay) {
        label = hhmm;
      } else {
        // Yesterday check — compare against a date object rolled back one day
        // rather than (now - 24h), so DST transitions don't misclassify.
        const yest = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
        const isYesterday = d.getFullYear() === yest.getFullYear()
          && d.getMonth() === yest.getMonth()
          && d.getDate() === yest.getDate();
        if (isYesterday) {
          label = 'Yesterday · ' + hhmm;
        } else {
          const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
          label = months[d.getMonth()] + ' ' + d.getDate() + ' · ' + hhmm;
        }
      }
    }
    const iso = d.toLocaleString();
    return '<span class="msg-ts" title="' + escapeHtml(iso) + '">' + escapeHtml(label) + '</span>';
  }

  // Minimal markdown renderer for assistant text — tables, inline code, bold, headers
  function renderSessionStateBlock(body) {
    // Parse `KEY: value` lines and emit a styled card. Unknown keys are
    // shown verbatim. NEXT_STEP_USER renders as "→ <value>" without
    // the key prefix because the arrow is the cue.
    const lines = body.split('\n').map(s => s.trim()).filter(Boolean);
    const rows = [];
    for (const ln of lines) {
      const m = ln.match(/^([A-Z_]+):\s*(.*)$/);
      if (!m) {
        rows.push('<div class="ssb-row ssb-other">' + escapeHtml(ln) + '</div>');
        continue;
      }
      const key = m[1];
      const val = escapeHtml(m[2]);
      if (key === 'NEXT_STEP_USER') {
        rows.push('<div class="ssb-row ssb-next"><span class="ssb-key">Next step user</span>' + val + '</div>');
      } else if (key === 'DID') {
        rows.push('<div class="ssb-row ssb-did"><span class="ssb-key">Did</span>' + val + '</div>');
      } else if (key === 'INSIGHT') {
        rows.push('<div class="ssb-row ssb-insight"><span class="ssb-key">Insight</span>' + val + '</div>');
      } else {
        // Fallback for any future keys: render the raw key name.
        rows.push('<div class="ssb-row ssb-other"><span class="ssb-key">' + escapeHtml(key.replace(/_/g, ' ').toLowerCase()) + '</span>' + val + '</div>');
      }
    }
    return '<div class="session-state-block">' + rows.join('') + '</div>';
  }

  function renderMarkdown(text) {
    const lines = text.split('\n');
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      // Session-state block emitted by the orchestration prompt:
      //   <session-state>
      //   DID: …
      //   INSIGHT: …
      //   NEXT_STEP_USER: …
      //   </session-state>
      // Render as a styled card instead of raw <session-state> tags + KEY:
      // pairs in the prose. Detect first so the block doesn't get partially
      // gobbled by the table or list logic below.
      if (/^\s*<session-state>\s*$/.test(line)) {
        i++;
        const start = i;
        while (i < lines.length && !/^\s*<\/session-state>\s*$/.test(lines[i])) i++;
        const body = lines.slice(start, i).join('\n');
        if (i < lines.length) i++;  // skip closing tag
        out.push(renderSessionStateBlock(body));
        continue;
      }
      // Fenced code block: ```lang\n...code...\n```
      // Runs BEFORE table/header detection so ``` inside code doesn't get
      // misinterpreted as markdown.
      const fence = line.match(/^\s*```\s*(\S*)\s*$/);
      if (fence) {
        const lang = fence[1] || '';
        i++;
        const start = i;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) i++;
        const code = lines.slice(start, i).join('\n');
        if (i < lines.length) i++;  // skip closing fence
        out.push(renderCodeBlock(code, lang));
        continue;
      }
      // Detect a table: header | sep | rows
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s\-:|]+\|\s*$/.test(lines[i + 1])) {
        const cells = (s) => s.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
        const header = cells(line);
        const rows = [];
        i += 2;
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          rows.push(cells(lines[i]));
          i++;
        }
        let html = '<table class="md-table"><thead><tr>';
        for (const h of header) html += '<th>' + renderInline(h) + '</th>';
        html += '</tr></thead><tbody>';
        for (const r of rows) {
          html += '<tr>';
          for (const c of r) html += '<td>' + renderInline(c) + '</td>';
          html += '</tr>';
        }
        html += '</tbody></table>';
        out.push(html);
        continue;
      }
      // Headers
      const h = line.match(/^(#{1,6})\s+(.+)$/);
      if (h) {
        const level = h[1].length;
        out.push('<h' + level + ' class="md-h">' + renderInline(h[2]) + '</h' + level + '>');
        i++;
        continue;
      }
      // Regular text line (preserve as-is with <br>)
      if (line.trim() === '') {
        out.push('<br>');
      } else {
        out.push('<div>' + renderInline(line) + '</div>');
      }
      i++;
    }
    return out.join('');
  }

  // ── Fenced-code-block rendering + tokenizer ─────────────────────────────
  // Basic language-aware syntax highlighting for the handful of langs that
  // actually show up in CCC conversation text. Not Prism-grade; deliberate
  // trade-off to keep the single-file "no-bundler" stance. Order of patterns
  // matters: comments/strings win over keywords, otherwise `// return`
  // would highlight `return` inside a comment.
  const _CB_LANG_ALIAS = {
    javascript: 'ts', js: 'ts', typescript: 'ts', tsx: 'ts', jsx: 'ts',
    python: 'py', py3: 'py',
    sh: 'bash', shell: 'bash', zsh: 'bash',
  };
  const _CB_LANG_PATTERNS = {
    ts: [
      { re: /\/\/[^\n]*|\/\*[\s\S]*?\*\//, cls: 'tok-comment' },
      { re: /`(?:\\.|[^`\\])*`|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/, cls: 'tok-string' },
      { re: /\b(?:const|let|var|function|return|if|else|for|while|do|switch|case|default|break|continue|class|extends|new|this|super|import|export|from|as|async|await|try|catch|finally|throw|typeof|instanceof|in|of|void|null|undefined|true|false|yield|enum|interface|type|public|private|protected|readonly|static|abstract|implements|namespace|declare|module|keyof|satisfies)\b/, cls: 'tok-keyword' },
      { re: /\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/, cls: 'tok-number' },
      { re: /\b[A-Z][A-Za-z0-9_]*\b/, cls: 'tok-type' },
      { re: /\b[a-zA-Z_][a-zA-Z0-9_]*(?=\s*\()/, cls: 'tok-function' },
    ],
    py: [
      { re: /#[^\n]*|'''[\s\S]*?'''|"""[\s\S]*?"""/, cls: 'tok-comment' },
      { re: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/, cls: 'tok-string' },
      { re: /\b(?:def|return|if|elif|else|for|while|break|continue|class|import|from|as|async|await|try|except|finally|raise|with|lambda|yield|global|nonlocal|pass|in|is|not|and|or|True|False|None|self|cls)\b/, cls: 'tok-keyword' },
      { re: /\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/, cls: 'tok-number' },
      { re: /\b[a-zA-Z_][a-zA-Z0-9_]*(?=\s*\()/, cls: 'tok-function' },
    ],
    bash: [
      { re: /#[^\n]*/, cls: 'tok-comment' },
      { re: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/, cls: 'tok-string' },
      { re: /\$\{[^}]+\}|\$[a-zA-Z_][a-zA-Z0-9_]*|\$[0-9@*#?!-]/, cls: 'tok-variable' },
      { re: /\b(?:if|then|else|elif|fi|for|do|done|while|until|case|esac|function|in|return|break|continue|export|local|readonly|declare|source|exit|trap|set|unset|eval|exec|shift)\b/, cls: 'tok-keyword' },
      { re: /\b\d+\b/, cls: 'tok-number' },
    ],
    json: [
      { re: /"(?:\\.|[^"\\])*"(?=\s*:)/, cls: 'tok-key' },
      { re: /"(?:\\.|[^"\\])*"/, cls: 'tok-string' },
      { re: /\b(?:true|false|null)\b/, cls: 'tok-keyword' },
      { re: /-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/, cls: 'tok-number' },
    ],
  };
  function highlightCode(code, lang) {
    const key = _CB_LANG_ALIAS[String(lang || '').toLowerCase()] || String(lang || '').toLowerCase();
    const patterns = _CB_LANG_PATTERNS[key];
    if (!patterns) return escapeHtml(code);
    let out = '';
    let idx = 0;
    while (idx < code.length) {
      let hit = null;
      let hitCls = '';
      for (const { re, cls } of patterns) {
        // Sticky regex match at current position only (no scanning ahead).
        const sticky = new RegExp(re.source, 'y');
        sticky.lastIndex = idx;
        const m = sticky.exec(code);
        if (m && m.index === idx) { hit = m[0]; hitCls = cls; break; }
      }
      if (hit) {
        out += '<span class="' + hitCls + '">' + escapeHtml(hit) + '</span>';
        idx += hit.length;
      } else {
        // Emit one char as-is; next iteration tries patterns from the next
        // position. Cheap; the patterns that matter are regex-fast and the
        // fall-through only runs on whitespace/punctuation.
        out += escapeHtml(code.charAt(idx));
        idx += 1;
      }
    }
    return out;
  }
  function renderCodeBlock(code, lang) {
    const langLabel = lang
      ? '<span class="cb-lang">' + escapeHtml(lang) + '</span>'
      : '<span class="cb-lang cb-lang-plain">code</span>';
    return (
      '<div class="cb-wrap">' +
        '<div class="cb-head">' + langLabel +
          '<button class="cb-copy" type="button" title="Copy">Copy</button>' +
        '</div>' +
        '<pre class="cb"><code>' + highlightCode(code, lang) + '</code></pre>' +
      '</div>'
    );
  }

  function renderInline(s) {
    // Escape HTML first
    s = escapeHtml(s);
    // Inline code `x` (also make paths inside code clickable)
    s = s.replace(/`([^`]+)`/g, (m, inner) => {
      if (/^(https?:\/\/\S+|\/[\w./@#:\-+]*|[\w./@#:\-+]+\.(md|ts|tsx|js|jsx|py|json|yaml|yml|css|html|sql|prisma|sh))$/.test(inner)) {
        return '<code class="md-code">' + linkifyPath(inner) + '</code>';
      }
      return '<code class="md-code">' + inner + '</code>';
    });
    // Bold **x**
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    // Markdown links [text](url)
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (m, text, url) => {
      if (/^https?:\/\//i.test(url)) {
        return '<a href="' + url + '" target="_blank" rel="noopener">' + text + '</a>';
      }
      return '<a role="button" tabindex="0" class="path-link" data-path="' + url + '">' + text + '</a>';
    });
    // Bare http(s) URLs
    s = s.replace(/(^|[\s(])((?:https?:\/\/)[^\s<>"')]+)/g,
      (m, pre, url) => pre + '<a href="' + url + '" target="_blank" rel="noopener">' + url + '</a>');
    // Bare file paths (relative like docs/foo/bar.md or absolute /Users/...)
    s = s.replace(/(^|[\s(])((?:\/|(?:[\w.\-]+\/)+)[\w.\-/]+\.(?:md|ts|tsx|js|jsx|py|json|yaml|yml|css|html|sql|prisma|sh))\b/g,
      (m, pre, p) => pre + '<a role="button" tabindex="0" class="path-link" data-path="' + p + '">' + p + '</a>');
    return s;
  }

  function linkifyPath(p) {
    if (/^https?:\/\//i.test(p)) {
      return '<a href="' + p + '" target="_blank" rel="noopener">' + p + '</a>';
    }
    return '<a role="button" tabindex="0" class="path-link" data-path="' + p + '">' + p + '</a>';
  }

  // Copy button on fenced code blocks. Reads plain text from the <code>
  // element (which survives as the rendered text, with token spans
  // unwound by textContent) so we don't need to stash the raw code
  // separately per block.
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest('.cb-copy');
    if (!btn) return;
    ev.preventDefault();
    const wrap = btn.closest('.cb-wrap');
    const code = wrap && wrap.querySelector('code') ? wrap.querySelector('code').textContent : '';
    if (!code) return;
    const flash = (ok) => {
      const orig = btn.textContent;
      btn.classList.toggle('copied', ok);
      btn.textContent = ok ? 'Copied' : 'Failed';
      setTimeout(() => { btn.classList.remove('copied'); btn.textContent = orig; }, 1200);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(code).then(() => flash(true)).catch(() => flash(false));
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

  let _imgLightbox = null;
  document.addEventListener('click', (ev) => {
    const img = ev.target.closest('img.msg-image');
    if (img) {
      ev.preventDefault();
      if (!_imgLightbox) {
        _imgLightbox = document.createElement('div');
        _imgLightbox.className = 'img-lightbox';
        _imgLightbox.innerHTML = '<img alt="">';
        _imgLightbox.addEventListener('click', () => _imgLightbox.classList.remove('open'));
        document.body.appendChild(_imgLightbox);
      }
      _imgLightbox.querySelector('img').src = img.src;
      _imgLightbox.classList.add('open');
      return;
    }
  });
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && _imgLightbox && _imgLightbox.classList.contains('open')) {
      _imgLightbox.classList.remove('open');
    }
  });

  // Inline-code paths in conversation messages get rendered as
  // `<a class="path-link">`. Two kinds in practice:
  //   • file paths       → open in editor via /api/open
  //   • web routes       → open in a browser tab on the project's deploy
  //                        URL (Vercel) when known, else the same origin
  // Heuristic: if it starts with `/` AND the last segment has a file
  // extension, treat as a file. Otherwise treat as a web route. Routes
  // like `/api/foo/bar` (no extension on the last segment) win the
  // browser-tab path; files like `static/index.html` win the editor path.
  function _isWebRoutePath(p) {
    if (!p.startsWith('/')) return false;
    const last = p.split('/').pop() || '';
    // Strip query/fragment before extension check.
    const cleanLast = last.split(/[?#]/)[0];
    return !/\.[a-zA-Z0-9]{1,8}$/.test(cleanLast);
  }
  function _pathLinkSessionContext(el) {
    try {
      const paneEl = el && el.closest ? el.closest('.conv-pane[data-pane-id]') : null;
      const pid = paneEl ? paneEl.getAttribute('data-pane-id') : '';
      if (pid && typeof paneByPaneId === 'function') {
        const pane = paneByPaneId(pid);
        if (pane && pane.currentSession) return pane.currentSession;
      }
    } catch (_) {}
    try { return currentSession || {}; } catch (_) { return {}; }
  }
  document.addEventListener('click', async (ev) => {
    const a = ev.target.closest('a.path-link');
    if (!a) return;
    ev.preventDefault();
    ev.stopPropagation();
    const p = a.dataset.path;
    if (!p) return;
    if (_isWebRoutePath(p)) {
      const base = _vercelDeployUrl || '';  // empty → relative to current origin
      const target = base ? base + p : p;
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
    a.style.opacity = '0.5';
    try {
      const ctx = _pathLinkSessionContext(a);
      const payload = { path: p };
      if (ctx && ctx.id) payload.session_id = ctx.id;
      if (ctx && ctx.cwd) payload.cwd = ctx.cwd;
      if (ctx && (ctx.repoPath || ctx.repo_path)) payload.repo_path = ctx.repoPath || ctx.repo_path;
      const res = await fetch('/api/open', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!data.ok) {
        a.title = data.error || 'open failed';
        a.style.color = 'var(--red)';
      }
    } catch (e) {
      a.title = String(e);
    } finally {
      setTimeout(() => { a.style.opacity = ''; }, 600);
    }
  });
  document.addEventListener('keydown', (ev) => {
    const a = ev.target.closest && ev.target.closest('a.path-link[role="button"]');
    if (!a) return;
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    ev.preventDefault();
    a.click();
  });

  // ── Tabs ──
  const $tabIssues = document.getElementById('tabIssues');
  const $tabSessions = document.getElementById('tabSessions');
  const $issuesView = document.getElementById('issuesView');
  const $issuesBadge = document.getElementById('issuesBadge');
  const $conversationsView = document.getElementById('conversationsView');
  const $convListPanel = document.getElementById('convListPanel');
  const $convList = document.getElementById('convList');
  const $convSearch = document.getElementById('convSearch');
  const $convSearchClear = document.getElementById('convSearchClear');
  const $kanbanBoard = document.getElementById('kanbanBoard');
  const $convKanbanToggle = document.getElementById('convKanbanToggle');

  // ── One-shot localStorage migration ──
  // Project rebrand: keys moved from `clv-*` (Claude Log Viewer) to `ccc-*`
  // (Claude Command Center) and Morning's dot-namespaced keys to dash style.
  // This loop runs every page load but only copies values when the new key
  // is empty, so it's idempotent. Drop the renames map after one release.
  (function migrateLocalStorageKeys() {
    const renames = {
      'clv-kanban-view': 'ccc-kanban-view',
      'clv-kanban-collapsed': 'ccc-kanban-collapsed',
      'clv-conv-panel-open': 'ccc-conv-panel-open',
      'clv-conv-font-scale': 'ccc-conv-font-scale',
      'clv-column-overrides': 'ccc-column-overrides',
      'clv-attention-collapsed': 'ccc-attention-collapsed',
      'clv-attention-height': 'ccc-attention-height',
      'clv-override-sync-done': 'ccc-override-sync-done',
      'clv-archived-backlog': 'ccc-archived-backlog',
      'clv-column-order': 'ccc-column-order',
      'clv-show-recent': 'ccc-show-recent',
      'clv-conv-sort': 'ccc-conv-sort',
      'clv-sticky-header-height': 'ccc-sticky-header-height',
      'clv-sidebar-width': 'ccc-sidebar-width',
      'clv-conv-width': 'ccc-conv-width',
      'clv-hide-descs': 'ccc-hide-descs',
      'clv-git-only': 'ccc-git-only',
      // Morning sub-feature: dot-namespaced → dash-prefixed for consistency.
      'ccc.morning.pane.width': 'ccc-morning-pane-width',
      'ccc.morning.braindump.lastAnalysis': 'ccc-morning-braindump-last-analysis',
      'ccc.morning.braindump.lastDump': 'ccc-morning-braindump-last-dump',
    };
    try {
      for (const oldKey in renames) {
        const newKey = renames[oldKey];
        if (localStorage.getItem(newKey) === null) {
          const v = localStorage.getItem(oldKey);
          if (v !== null) localStorage.setItem(newKey, v);
        }
      }
    } catch (_) { /* localStorage may be disabled — fail silently */ }
  })();

  let kanbanView = false;
  try { localStorage.setItem('ccc-kanban-view', 'false'); } catch (_) {}
  let kanbanCollapsed = {}; // column_key -> bool, tracks user collapse state
  try { kanbanCollapsed = JSON.parse(localStorage.getItem('ccc-kanban-collapsed') || '{}'); } catch (_) {}
  let kanbanShowAll = {};   // column_key -> bool, tracks "show all" state for large columns
  let activeTab = 'sessions';

  // ── Split layout elements ──
  const $kanbanLayout = document.getElementById('kanbanLayout');
  const $kanbanBoardSplit = document.getElementById('kanbanBoardSplit');
  const $convPanelView = document.getElementById('convPanelView');
  const $convPanelInput = document.getElementById('convPanelInput');
  const $cpInput = document.getElementById('cpInput');
  const $cpSendBtn = document.getElementById('cpSendBtn');
  const $cpTtyLabel = document.getElementById('cpTtyLabel');
  const $cpSessionId = document.getElementById('cpSessionId');
  const $splitResizer = document.getElementById('splitResizer');
  const $kanbanPanel = $kanbanLayout ? $kanbanLayout.querySelector('.kanban-panel') : null;
  const $convPanel = $kanbanLayout ? $kanbanLayout.querySelector('.conv-panel') : null;
  const $cpCloseBtn = document.getElementById('cpCloseBtn');
  const $coordBtn = document.getElementById('coordBtn');
  const $coordClearBtn = document.getElementById('coordClearBtn');
  if ($coordBtn) $coordBtn.addEventListener('click', () => openCoordModal());
  if ($coordClearBtn) $coordClearBtn.addEventListener('click', () => {
    selectedListIds.clear();
    document.querySelectorAll('.conv-item.list-selected').forEach(el => el.classList.remove('list-selected'));
    updateCoordToolbar();
  });
  const $coordModalCancel = document.getElementById('coordModalCancel');
  const $coordModalStart = document.getElementById('coordModalStart');
  const $coordModalBackdrop = document.getElementById('coordModalBackdrop');
  if ($coordModalCancel) $coordModalCancel.addEventListener('click', () => {
    if ($coordModalBackdrop) $coordModalBackdrop.classList.remove('visible');
  });
  if ($coordModalStart) $coordModalStart.addEventListener('click', () => startCoordination());
  const $coordTopicInput = document.getElementById('coordTopicInput');
  if ($coordTopicInput) {
    $coordTopicInput.addEventListener('keydown', ev => {
      if (ev.key === 'Enter') startCoordination();
      if (ev.key === 'Escape' && $coordModalBackdrop) $coordModalBackdrop.classList.remove('visible');
    });
  }
  if ($coordModalBackdrop) $coordModalBackdrop.addEventListener('click', ev => {
    if (ev.target === $coordModalBackdrop) $coordModalBackdrop.classList.remove('visible');
  });
  let convPanelOpen = true;

  const $kptOpenConvBtn = document.getElementById('kptOpenConvBtn');
  let savedKanbanWidth = '';
  function setConvPanelOpen(open) {
    convPanelOpen = open;
    if ($convPanel) $convPanel.style.display = open ? '' : 'none';
    if ($splitResizer) $splitResizer.style.display = open ? '' : 'none';
    if ($kanbanPanel) {
      if (open) {
        if (savedKanbanWidth) {
          $kanbanPanel.style.flex = 'none';
          $kanbanPanel.style.width = savedKanbanWidth;
        }
      } else {
        savedKanbanWidth = $kanbanPanel.style.width || '';
        $kanbanPanel.style.flex = '1';
        $kanbanPanel.style.width = 'auto';
      }
    }
    if ($kptOpenConvBtn) $kptOpenConvBtn.style.display = open ? 'none' : '';
    try { localStorage.setItem('ccc-conv-panel-open', open ? '1' : '0'); } catch (_) {}
  }
  // ── Mobile overlay helpers ──
  const _mobileMQ = window.matchMedia('(max-width: 768px)');
  function isMobile() { return _mobileMQ.matches; }
  function mobileShowMain(on) {
    document.body.classList.toggle('mobile-show-main', !!on);
  }
  function mobileShowConv(on) {
    document.body.classList.toggle('mobile-conv-open', !!on);
  }
  function mobileShowForCurrentMode() {
    if (!isMobile()) return;
    if (document.body.classList.contains('kanban-split')) mobileShowConv(true);
    else mobileShowMain(true);
  }
  const $mobileBackBtn = document.getElementById('mobileBackBtn');
  if ($mobileBackBtn) $mobileBackBtn.addEventListener('click', () => mobileShowMain(false));
  const $cpMobileBackBtn = document.getElementById('cpMobileBackBtn');
  if ($cpMobileBackBtn) $cpMobileBackBtn.addEventListener('click', () => mobileShowConv(false));
  const $mobileReloadBtn = document.getElementById('mobileReloadBtn');
  if ($mobileReloadBtn) $mobileReloadBtn.addEventListener('click', () => {
    // Bypass cache where supported (Firefox); Safari treats it as a normal reload.
    try { location.reload(true); } catch (_) { location.reload(); }
  });

  if ($cpCloseBtn) $cpCloseBtn.addEventListener('click', () => {
    if (isMobile()) { mobileShowConv(false); return; }
    setConvPanelOpen(!convPanelOpen);
  });
  if ($kptOpenConvBtn) $kptOpenConvBtn.addEventListener('click', () => setConvPanelOpen(true));
  // Restore last state
  try {
    if (localStorage.getItem('ccc-conv-panel-open') === '0') setConvPanelOpen(false);
  } catch (_) {}

  // Font scale controls for conversations view
  let convFontScale = parseFloat(localStorage.getItem('ccc-conv-font-scale') || '1');
  const $fontMinus = document.getElementById('fontMinus');
  const $fontPlus = document.getElementById('fontPlus');

  function applyConvFontScale() {
    if ($conversationsView) $conversationsView.style.zoom = convFontScale;
    if ($convPanelView) $convPanelView.style.zoom = convFontScale;
  }
  applyConvFontScale();

  if ($fontMinus) $fontMinus.addEventListener('click', () => {
    convFontScale = Math.max(0.7, +(convFontScale - 0.1).toFixed(1));
    localStorage.setItem('ccc-conv-font-scale', String(convFontScale));
    applyConvFontScale();
  });
  if ($fontPlus) $fontPlus.addEventListener('click', () => {
    convFontScale = Math.min(1.5, +(convFontScale + 0.1).toFixed(1));
    localStorage.setItem('ccc-conv-font-scale', String(convFontScale));
    applyConvFontScale();
  });

  // The Sessions/Issues tab bar was removed. switchTab() is kept as a
  // null-safe no-op so legacy call sites (e.g. fix-issue flow) don't crash;
  // behaviour is always the sessions view now.
  function switchTab(tab) {
    activeTab = tab;
    if (tab === 'sessions' && !conversationsLoaded) {
      loadConversationList();
    }
    if (tab !== 'sessions') {
      stopConvStream();
    }
    updateResumeButton();
    updateJumpButton();
    updateInputBar();
  }

  // ── Conversations ──
  let conversationsData = [];
  let conversationsLoaded = false;
  // Backlog cards (GH issues, TODO/PARKING, native tasks) are produced by
  // /api/sessions for the current repo, while /api/conversations/all is the
  // cross-folder JSONL archive. Keep the current repo's backlog rows around so
  // archive mode can still render the GH Issues section without switching repos.
  let currentRepoBacklogData = [];
  // ── Split-pane state ──
  // The conversation pane can show one or two conversations side-by-side
  // (vertical) or stacked (horizontal). Per-pane state lives in
  // splitState.panes[]; the *active* pane is the one keyboard/sidebar
  // actions target. The old single-instance globals (currentConversation,
  // convLastLine, convEventSource, _pendingSends, _firstUserMsgRendered)
  // are kept as compatibility-shim getters/setters on `window` that proxy
  // to splitState.panes[splitState.activeIndex].* so the thousands of
  // existing references compile against the active pane unchanged.
  // Only the renderer / SSE / composer entry points learn paneId.
  function _newPaneState(id) {
    return {
      id: id,
      conversationId: null,
      lastLine: 0,
      eventSource: null,
      pendingSends: [],
      firstUserMsgRendered: false,
      currentToolGroup: null,
      currentToolCount: 0,
      currentSession: { id: null, cwd: null, cwdExists: false, source: null, repoPath: null },
    };
  }
  const splitState = {
    orientation: null, // null | 'vertical' | 'horizontal'
    panes: [_newPaneState('p1')],
    activeIndex: 0,
    ratio: 0.5,
  };
  function activePaneId() { return splitState.panes[splitState.activeIndex].id; }
  function paneByPaneId(pid) { return splitState.panes.find(p => p.id === pid) || null; }
  function paneIndexByPaneId(pid) {
    for (let i = 0; i < splitState.panes.length; i++) if (splitState.panes[i].id === pid) return i;
    return -1;
  }

  // Compatibility shim — read/write the active pane via the old global names.
  // DO NOT remove without auditing every reference to currentConversation,
  // convLastLine, convEventSource, _pendingSends, _firstUserMsgRendered.
  Object.defineProperty(window, 'currentConversation', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].conversationId; },
    set(v) { splitState.panes[splitState.activeIndex].conversationId = v; },
  });
  Object.defineProperty(window, 'convLastLine', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].lastLine; },
    set(v) { splitState.panes[splitState.activeIndex].lastLine = v; },
  });
  Object.defineProperty(window, 'convEventSource', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].eventSource; },
    set(v) { splitState.panes[splitState.activeIndex].eventSource = v; },
  });

  let showArchived = false;  // false = show non-archived, true = show only archived

  // Track session_ids first observed *during* this page session (not on the
  // initial load — those aren't "new"). Powers the `.recently-born` glow that
  // signals a freshly-spawned card is still settling into its column. CSS
  // animation has a bounded iteration count so we don't need to clean up.
  const _firstSeenSessions = new Map();  // session_id → epoch ms
  let _convInitialLoadDone = false;

  // Sticky first-column pin for brand-new sessions. Prevents the "card appeared
  // in Planning then jumped to Review then back" bounce that happens while the
  // server is still settling on sidecar_status / live / stage for a session
  // that's mid-spawn. The sticky releases as soon as the session makes visible
  // progress (first edit/commit/push/tail event), or after _STICKY_TTL_MS.
  const _stickyInitialCol = new Map();  // session_id → { col, signals }
  const _STICKY_TTL_MS = 60000;
  function _signalSnapshot(c) {
    return {
      has_edit: !!c.has_edit,
      has_commit: !!c.has_commit,
      has_push: !!c.has_push,
      last_event_type: c.last_event_type || null,
    };
  }
  function stampFreshSessionsAsBorn(freshList) {
    if (!_convInitialLoadDone) return;
    const existing = new Set();
    for (const c of conversationsData) if (c.session_id) existing.add(c.session_id);
    const now = Date.now();
    let anyNew = false;
    for (const c of freshList) {
      const sid = c.session_id;
      if (sid && !existing.has(sid) && !_firstSeenSessions.has(sid)) {
        _firstSeenSessions.set(sid, now);
        anyNew = true;
      }
    }
    // The CSS animation has a bounded iteration count, but leaves the card
    // with a static shimmer-gradient background once it stops. Force a
    // re-render after the TTL so isRecentlyBorn() returns false and the
    // `.recently-born` class + gradient both come off cleanly.
    if (anyNew) {
      setTimeout(() => {
        if ($convSearch) renderSidebar(filterConversations($convSearch.value));
        else renderSidebar(conversationsData);
      }, 30500);
    }
  }
  function isRecentlyBorn(sessionId) {
    const born = _firstSeenSessions.get(sessionId);
    return !!born && (Date.now() - born) < 30000;
  }

  // Optimistic state overrides for archived/verified flags. When the user
  // archives or verifies a card we mutate the in-memory copy, but a /api/sessions
  // poll already in flight will return *pre-click* data and overwrite that
  // mutation when it lands — the card briefly reappears in its old column
  // before the next poll picks up the persisted change. This map shields the
  // optimistic value until the server's response agrees, with a 30s TTL so a
  // failed write doesn't pin a stale override forever.
  const _optimisticOverrides = new Map();  // sid -> {archived?, verified?, ts}
  const _OPTIMISTIC_TTL_MS = 30000;
  function setOptimisticOverride(sid, patch) {
    if (!sid) return;
    const existing = _optimisticOverrides.get(sid) || {};
    _optimisticOverrides.set(sid, Object.assign({}, existing, patch, { ts: Date.now() }));
  }
  function applyOptimisticOverrides(list) {
    if (!_optimisticOverrides.size) return;
    const now = Date.now();
    for (const [sid, entry] of _optimisticOverrides) {
      if (now - entry.ts > _OPTIMISTIC_TTL_MS) _optimisticOverrides.delete(sid);
    }
    for (const c of list) {
      const ov = _optimisticOverrides.get(c.session_id);
      if (!ov) continue;
      let allMatch = true;
      if ('archived' in ov) {
        if (c.archived !== ov.archived) { c.archived = ov.archived; allMatch = false; }
      }
      if ('verified' in ov) {
        if (c.verified !== ov.verified) { c.verified = ov.verified; allMatch = false; }
      }
      // Server now agrees on every field we were holding — stop overriding.
      if (allMatch) _optimisticOverrides.delete(c.session_id);
    }
  }

  // Pending spawn placeholders (optimistic UI) — keyed by pid.
  const pendingSpawns = new Map();

  function insertPendingSpawnCard(pid, subject, sourceOrEngine, logPath, meta) {
    if (!pid) return;
    const id = 'spawning-' + pid;
    // Backwards-compat: this used to take `usePkood: bool`. Accept
    // either the legacy boolean (true → 'pkood') or a new explicit
    // string ('claude' | 'codex' | 'pkood' | 'interactive').
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
      has_edit: false, has_commit: false, has_push: false,
      sidecar_status: 'active', sidecar_has_writes: false,
      modified: Date.now() / 1000, size: 0, branch: '',
      last_event_type: null, pending_tool: null, pending_file: null,
      name_overridden: false,
      // Fire-and-watch engines also get durable engine-native sessions; the
      // log path is only a fallback while the real row is materializing.
      agent_log_path: (source === 'codex' || source === 'gemini') ? (logPath || null) : null,
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
    pendingSpawns.set(pid, card);
    conversationsData = [card, ...conversationsData];
    // Pin to Working column for the moment
    columnOverrides[id] = 'working';
    try { localStorage.setItem('ccc-column-overrides', JSON.stringify(columnOverrides)); } catch (_) {}
    renderSidebar(filterConversations($convSearch.value));
    // Auto-select the placeholder so the right pane lands on the new
    // session immediately. Once the real session materializes, the
    // placeholder→real swap in loadConversationList re-binds the right
    // pane in place — selection follows the spawn end-to-end.
    if (typeof selectConversation === 'function') {
      selectConversation(id);
    }
    // Auto-cleanup after 30s for Claude placeholders. Fire-and-watch placeholders
    // stick around until the durable thread row appears, with the spawn log
    // as a fallback if the CLI exits before creating a thread.
    if (source !== 'codex' && source !== 'gemini') {
      setTimeout(() => {
        if (pendingSpawns.has(pid)) {
          pendingSpawns.delete(pid);
          delete columnOverrides[id];
          conversationsData = conversationsData.filter(x => x.id !== id);
          renderSidebar(filterConversations($convSearch.value));
        }
      }, 30000);
    }
  }

  // Hide the loading overlay once we've actually rendered a sessions response.
  // Idempotent — fires on every list refresh but only the first matters.
  function hideLoadingOverlay() {
    const $overlay = document.getElementById('cccLoadingOverlay');
    if (!$overlay || $overlay.classList.contains('gone')) return;
    $overlay.classList.add('fade-out');
    // Remove from layout after the fade so it doesn't intercept clicks.
    setTimeout(() => $overlay.classList.add('gone'), 300);
  }
  // Safety net — if /api/sessions hangs or the render path errors silently,
  // never leave the user staring at a spinner forever. 30s is generous given
  // the cache-warm scan can take ~10s on a big ~/.claude/projects dir.
  setTimeout(hideLoadingOverlay, 30000);

  // First-sessions-loaded promise. Resolves when /api/sessions returns
  // for the first time (or 30s safety timer fires). The archive boot
  // kick (cross-folder JSONL walk + git ops, can be ~12s on a cold cache)
  // awaits this so it doesn't compete for CPU with /api/sessions itself —
  // running both in parallel was making the user stare at the wrong
  // overlay copy for the duration of the slower request.
  let _firstSessionsResolve;
  const _firstSessionsLoaded = new Promise(res => { _firstSessionsResolve = res; });
  function _markFirstSessionsLoaded() {
    if (_firstSessionsResolve) { _firstSessionsResolve(); _firstSessionsResolve = null; }
  }
  // Same 30s ceiling as the overlay safety net.
  setTimeout(_markFirstSessionsLoaded, 30000);

  async function loadConversationList() {
    try {
      const repoPath = selectedRepoPath();
      if (!repoPath) {
        currentRepoBacklogData = [];
        conversationsLoaded = true;
        _convInitialLoadDone = true;
        loadAttentionList();
        hideLoadingOverlay();
        _markFirstSessionsLoaded();
        renderArchiveList($convSearch.value);
        return;
      }
      const res = await fetch(repoUrl('/api/sessions', repoPath));
      const fresh = await res.json();
      if (!res.ok) throw new Error((fresh && fresh.error) || ('HTTP ' + res.status));
      currentRepoBacklogData = Array.isArray(fresh)
        ? fresh.filter(c => c && c.source === 'backlog').map(c => Object.assign({}, c))
        : [];
      // Preserve user-set fields (e.g. display_name overrides) from the
      // previous snapshot — the server can re-derive a default name on the
      // next scan and clobber a rename otherwise.
      const oldBySessionId = {};
      for (const c of conversationsData) oldBySessionId[c.session_id] = c;
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
      const realPids = new Set(fresh.filter(c => c.spawn_pid).map(c => c.spawn_pid));
      for (const pid of Array.from(pendingSpawns.keys())) {
        if (realPids.has(pid)) {
          const placeholderId = 'spawning-' + pid;
          const placeholderCol = columnOverrides[placeholderId];
          const realCard = fresh.find(c => c.spawn_pid === pid);
          if (realCard && realCard.session_id) {
            _firstSeenSessions.set(realCard.session_id, Date.now());
            if (placeholderCol && !realCard.verified && !realCard.archived) {
              _stickyInitialCol.set(realCard.session_id, {
                col: placeholderCol,
                signals: _signalSnapshot(realCard),
              });
            }
            if (currentConversation === placeholderId) {
              _selectionSwap = { realCard };
            }
          }
          delete columnOverrides[placeholderId];
          pendingSpawns.delete(pid);
        }
      }
      // Stamp first-seen timestamps for any session_ids that weren't in the
      // previous snapshot — drives the `.recently-born` glow. No-op on the
      // very first load (flag below flips after this block).
      stampFreshSessionsAsBorn(fresh);
      // Keep still-pending placeholders on top until they materialize.
      const placeholders = Array.from(pendingSpawns.values());
      conversationsData = [...placeholders, ...fresh];
      // Re-apply any in-flight archive/verify overrides so an /api/sessions
      // response that started before a click can't briefly un-archive a card.
      applyOptimisticOverrides(conversationsData);
      conversationsLoaded = true;
      _convInitialLoadDone = true;
      // Cache session IDs / cwds / sources so Resume+Jump work
      sessionIdByConv = {};
      sessionCwdByConv = {};
      sessionCwdExistsByConv = {};
      sessionSourceByConv = {};
      sessionSpawnPidByConv = {};
      for (const c of conversationsData) {
        if (c.session_id) sessionIdByConv[c.id] = c.session_id;
        if (c.session_cwd) sessionCwdByConv[c.id] = c.session_cwd;
        sessionCwdExistsByConv[c.id] = !!c.session_cwd_exists;
        sessionSourceByConv[c.id] = c.source || 'interactive';
        if (c.spawn_pid) sessionSpawnPidByConv[c.id] = c.spawn_pid;
      }
      // Carry the right-pane selection across the placeholder→real swap.
      // selectConversation() would flash a "Loading..." empty state and feel
      // like a switch; instead we re-bind streams and session pointers in
      // place so the right pane reads as the same conversation throughout.
      if (_selectionSwap) {
        const real = _selectionSwap.realCard;
        currentConversation = real.id;
        try { localStorage.setItem('ccc-last-conv', real.id); } catch (_) {}
        if (typeof stopConvStream === 'function') stopConvStream();
        if (typeof stopSpawnStream === 'function') stopSpawnStream();
        setCurrentSession(
          real.source || 'interactive',
          real.session_id,
          real.session_cwd,
          real.session_cwd_exists,
          real.spawn_pid,
          rowRepoPath(real) || selectedRepoPath()
        );
        // Reset the per-conv tail cursor so fetchConversationEvents pulls the
        // real JSONL from the start. Tool-grouping state must reset too —
        // otherwise the next tool block tries to extend a group that belongs
        // to the placeholder's (empty) view.
        convLastLine = 0;
        _firstUserMsgRendered = false;
        _currentToolGroup = null;
        _currentToolCount = 0;
        setCopyableSessionId($cpSessionId, real.session_id || '');
        fetchConversationEvents();
        startConvStream();
        if (real.session_id && real.source !== 'codex') startSpawnStream(real.session_id);
        updateSplitInputBar();
        updateSplitToolbar();
      }
      if (archiveLoaded) renderArchiveList($convSearch.value);
      loadAttentionList();  // piggy-back on the same refresh cycle
      hideLoadingOverlay();  // first render has happened — reveal the UI
      _markFirstSessionsLoaded();  // unblock archive boot kick (see wireArchiveMode)
      // Auto-restore the last-opened card on first load. Only fires when
      // the user hasn't already clicked something — refresh-after-refresh
      // shouldn't yank a card out from under an active selection.
      if (!currentConversation) {
        let lastId = '';
        try { lastId = localStorage.getItem('ccc-last-conv') || ''; } catch (_) {}
        if (lastId && conversationsData.some(c => c.id === lastId)) {
          selectConversation(lastId);
        }
      }
    } catch (err) {
      $convList.innerHTML = '<div class="empty-state" style="height:auto;padding:20px;font-size:13px;">Failed to load sessions: ' + escapeHtml(err.message) + '</div>';
      hideLoadingOverlay();  // even on error — don't leave the user stuck on a spinner
      _markFirstSessionsLoaded();  // even on error — don't pin the archive load
    }
  }

  // ── Attention panel (top-of-kanban "what needs me") ─────────────────────
  let _nyaShowAll = false;
  async function loadAttentionList() {
    const $list = document.getElementById('attentionList');
    const $count = document.getElementById('attentionCount');
    const $seeAll = document.getElementById('attentionSeeAllBtn');
    if (!$list) return;
    const repoPath = selectedRepoPath();
    if (!repoPath) {
      if ($count) $count.textContent = '';
      if ($seeAll) $seeAll.textContent = 'See all';
      $list.innerHTML = '<div class="attention-empty">Pick a repo to see items that need attention.</div>';
      return;
    }
    try {
      const url = repoUrl('/api/attention', repoPath, _nyaShowAll ? { all: '1' } : null);
      const res = await fetch(url);
      const data = await res.json();
      const items = (data && data.items) || [];
      const shown = (data && data.shown) || items.length;
      const total = (data && data.total) || 0;
      const grand = (data && data.grand_total) || total;
      if ($count) {
        // "8 / 42" in default mode; "82 / 82" in see-all mode. If age-out
        // hid some, show "(+N stale)" so you know the See-all button matters.
        if (_nyaShowAll) {
          $count.textContent = items.length + ' / ' + grand;
        } else if (total > shown) {
          const staleHint = grand > total ? ' · +' + (grand - total) + ' stale' : '';
          $count.textContent = shown + ' / ' + total + staleHint;
        } else {
          const staleHint = grand > total ? ' · +' + (grand - total) + ' stale' : '';
          $count.textContent = shown ? String(shown) + staleHint : '';
        }
      }
      if ($seeAll) {
        $seeAll.textContent = _nyaShowAll ? 'See fewer' : ('See all' + (grand > total ? ' (' + grand + ')' : ''));
      }
      if (!items.length) {
        $list.innerHTML = '<div class="attention-empty">All clear — nothing needs you right now.</div>';
        return;
      }
      $list.innerHTML = items.map(it => {
        const kind = it.kind || '';
        const label = kind.replace(/_/g, ' ');
        // For each attention card, resolve the underlying conversation so we
        // can print which column the kanban classifier would put it in. This
        // surfaces discrepancies ("why is this in Needs Attention when the
        // board puts it in Working?") — the debug label lets the user see both
        // truths side-by-side.
        let classifiedCol = '';
        const sid = it.session_id || '';
        if (sid.startsWith('backlog-issue-')) {
          const conv = conversationsData.find(x => x.id === sid);
          classifiedCol = conv ? classifyKanbanColumn(conv) : '(no backlog card)';
        } else if (sid) {
          const conv = conversationsData.find(x => (x.session_id === sid) || (x.id === sid));
          classifiedCol = conv ? classifyKanbanColumn(conv) : '(no conv loaded)';
        } else {
          classifiedCol = '(no session_id)';
        }
        const shortSid = sid.startsWith('backlog-issue-')
          ? '#' + sid.replace('backlog-issue-', '')
          : sid.slice(0, 8);
        const sidChip = sid
          ? '<span class="att-sid" data-full="' + escapeHtml(sid) + '" title="' + escapeHtml(sid) + ' — click to copy">' + escapeHtml(shortSid) + '</span>'
          : '';
        // Verify button only makes sense for session rows. Backlog items
        // (needs_attention_label, open_backlog) don't have a session to verify.
        const isSessionRow = !sid.startsWith('backlog-issue-');
        const verifyBtn = isSessionRow
          ? '<button class="att-verify-btn" data-verify-sid="' + escapeHtml(sid) + '" title="Mark this session verified — moves card to Verified column and drops it from this list">&#10003; Verify</button>'
          : '';
        return '<div class="attention-row" data-sid="' + escapeHtml(sid) + '" data-kind="' + escapeHtml(kind) + '">'
          + '<div class="att-name">' + sidChip + escapeHtml(it.name || '(untitled)') + '</div>'
          + '<span class="att-kind k-' + escapeHtml(kind) + '">' + escapeHtml(label) + '</span>'
          + '<span class="att-col-debug" title="Column the kanban classifier would place this in">col: ' + escapeHtml(classifiedCol) + '</span>'
          + '<div class="att-where">' + escapeHtml(it.where || '') + '</div>'
          + (it.did     ? '<div class="att-did"><strong>Did:</strong> '     + escapeHtml(it.did)     + '</div>' : '')
          + (it.insight ? '<div class="att-insight"><strong>Insight:</strong> ' + escapeHtml(it.insight) + '</div>' : '')
          + '<div class="att-next">' + escapeHtml(it.next_step || '') + '</div>'
          + verifyBtn
        + '</div>';
      }).join('');
      // Verify buttons: call the same endpoint drag-to-Verified uses, then
      // optimistically remove the row. Full refresh follows so the kanban
      // card moves too.
      $list.querySelectorAll('.att-verify-btn').forEach(btn => {
        btn.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          const fullSid = btn.dataset.verifySid || '';
          if (!fullSid) return;
          const row = btn.closest('.attention-row');
          const conv = conversationsData.find(x => (x.session_id === fullSid) || (x.id === fullSid));
          const cardId = conv ? conv.id : fullSid;
          btn.disabled = true;
          btn.textContent = 'Verifying…';
          try {
            await ccPostJson('/api/conversations/' + cardId + '/verify', {
              verified: true, session_id: fullSid,
              display_name: (conv && conv.display_name) || '',
              cwd: (conv && conv.session_cwd) || '',
              linked_issue: (conv && (conv.linked_issue || conv.issue_number)) || '',
              tail_issue_number: (conv && conv.tail_issue_number) || '',
            });
            if (conv) conv.verified = true;
            if (row) row.remove();
            // If the session was live and waiting on the user's next prompt,
            // close the loop by answering "Yes" to whatever the LLM asked. Most
            // sidecar_waiting cases are "did this work / should I commit / done
            // — anything else?" — "Yes" is the safe default reply.
            const wasLive = conv && conv.is_live;
            if (wasLive) {
              fetch('/api/inject-input', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ session_id: fullSid, text: 'Yes' }),
              }).then(r => r.json()).then(data => {
                if (!data.ok) {
                  showOpToast('Verified, but couldn\'t reply "Yes" to the terminal: ' + (data.error || 'unknown'), 'error');
                }
              }).catch(() => {});
            }
            refreshConversationList();  // moves the kanban card to Verified
          } catch (err) {
            btn.disabled = false;
            btn.innerHTML = '&#10003; Verify';
            showOpToast('Verify failed: ' + err.message, 'error');
          }
        });
      });
      // ID chip: click copies full session_id (stopPropagation so the row click
      // doesn't also open the session). Brief green flash confirms the copy.
      $list.querySelectorAll('.att-sid').forEach(chip => {
        chip.addEventListener('click', async (ev) => {
          ev.stopPropagation();
          const full = chip.dataset.full || '';
          if (!full) return;
          try {
            await navigator.clipboard.writeText(full);
            chip.classList.add('copied');
            setTimeout(() => chip.classList.remove('copied'), 900);
          } catch (_) {
            showOpToast('Copy failed — select and copy manually', 'error');
          }
        });
      });
      // Wire clicks: open the underlying session AND scroll its column into view.
      $list.querySelectorAll('.attention-row').forEach(row => {
        row.addEventListener('click', () => {
          const sid = row.dataset.sid;
          if (!sid) return;
          if (sid.startsWith('backlog-issue-')) {
            const n = sid.replace('backlog-issue-', '');
            const row = conversationsData.find(x => x.id === sid);
            renderIssueInConvPane(n, rowRepoPath(row), sid);
          } else {
            const c = conversationsData.find(x => (x.session_id === sid) || (x.id === sid));
            selectConversation(c ? c.id : sid);
          }
          focusCardOnBoard(sid);
        });
      });
    } catch (_) {
      // Keep last-known contents on transient errors
    }
  }

  // Scroll the split kanban so the card for the given id (session_id or card id)
  // lands horizontally centered in view, its column expanded, and the card itself
  // visible within the column. Briefly flashes a ring on the card.
  function focusCardOnBoard(idOrSid) {
    const board = document.getElementById('kanbanBoardSplit');
    if (!board) return;
    // Support both session_id and card id (backlog ids are the card id).
    let card = board.querySelector('.kanban-card[data-session-id="' + CSS.escape(idOrSid) + '"]')
            || board.querySelector('.kanban-card[data-id="' + CSS.escape(idOrSid) + '"]');
    if (!card) {
      // Card may be past the 20-item truncation in its column. Find which
      // column it would land in, flip that column to show-all, and retry.
      const conv = conversationsData.find(x => x.session_id === idOrSid || x.id === idOrSid);
      if (conv) {
        const targetCol = classifyKanbanColumn(conv);
        if (targetCol && !kanbanShowAll[targetCol]) {
          kanbanShowAll[targetCol] = true;
          renderSidebar(filterConversations($convSearch.value));
          requestAnimationFrame(() => focusCardOnBoard(idOrSid));
        }
      }
      return;
    }
    // Expand the containing column if it was collapsed
    const column = card.closest('.kanban-column');
    if (column && column.dataset.col && kanbanCollapsed[column.dataset.col]) {
      kanbanCollapsed[column.dataset.col] = false;
      try { localStorage.setItem('ccc-kanban-collapsed', JSON.stringify(kanbanCollapsed)); } catch (_) {}
      renderSidebar(filterConversations($convSearch.value));
      // After re-render, grab the fresh node and try again
      requestAnimationFrame(() => focusCardOnBoard(idOrSid));
      return;
    }
    // Horizontal scroll: center the column within the board viewport.
    if (column) {
      const boardRect = board.getBoundingClientRect();
      const colRect = column.getBoundingClientRect();
      const colCenterInBoard = (colRect.left - boardRect.left) + (colRect.width / 2);
      const desired = board.scrollLeft + colCenterInBoard - (boardRect.width / 2);
      board.scrollTo({ left: Math.max(0, desired), behavior: 'smooth' });
    }
    // Vertical scroll within the column's cards container so the card is visible
    const cards = column && column.querySelector('.kanban-cards');
    if (cards) {
      const cardsRect = cards.getBoundingClientRect();
      const cardRect = card.getBoundingClientRect();
      if (cardRect.top < cardsRect.top || cardRect.bottom > cardsRect.bottom) {
        const offset = (cardRect.top - cardsRect.top) - (cardsRect.height / 2) + (cardRect.height / 2);
        cards.scrollTo({ top: cards.scrollTop + offset, behavior: 'smooth' });
      }
    }
    // Brief highlight so the user's eye catches it
    card.classList.remove('just-moved');
    // Force reflow to restart animation
    void card.offsetWidth;
    card.classList.add('just-moved');
  }

  // Collapse toggle for the attention panel (persisted).
  // NYA defaults to COLLAPSED — kanban gets the prime real estate. Users
  // expand on demand via the header bar (whole header is clickable when
  // collapsed) or the dedicated toggle. The collapsed state is sticky in
  // localStorage so a user who likes it open stays open across reloads.
  (function () {
    const $panel = document.getElementById('attentionPanel');
    const $toggle = document.getElementById('attentionToggle');
    if (!$panel || !$toggle) return;
    // Default behaviour: only EXPAND when user has explicitly chosen to.
    // We keep the panel collapsed by default (HTML starts with .collapsed).
    try {
      const stored = localStorage.getItem('ccc-attention-collapsed');
      if (stored === '0') {
        $panel.classList.remove('collapsed');
      } // else (null or '1') → stay collapsed (HTML default)
    } catch (_) {}
    function setCollapsed(collapsed) {
      $panel.classList.toggle('collapsed', collapsed);
      try {
        localStorage.setItem('ccc-attention-collapsed', collapsed ? '1' : '0');
      } catch (_) {}
    }
    $toggle.addEventListener('click', (e) => {
      e.stopPropagation();
      setCollapsed(!$panel.classList.contains('collapsed'));
    });
    // Whole header is a click-target when collapsed, so the user doesn't
    // have to aim at the small toggle chevron.
    const $header = $panel.querySelector('.attention-header');
    if ($header) {
      $header.addEventListener('click', (e) => {
        if (!$panel.classList.contains('collapsed')) return;
        // Don't hijack clicks on the inline buttons (refresh / see all / toggle).
        if (e.target.closest('button')) return;
        setCollapsed(false);
      });
    }
    const $seeAll = document.getElementById('attentionSeeAllBtn');
    if ($seeAll) {
      $seeAll.addEventListener('click', (e) => {
        e.stopPropagation();
        _nyaShowAll = !_nyaShowAll;
        loadAttentionList();
      });
    }
    const $refresh = document.getElementById('attentionRefreshBtn');
    if ($refresh) {
      $refresh.addEventListener('click', async (e) => {
        e.stopPropagation();
        $refresh.disabled = true;
        $refresh.style.opacity = '0.5';
        try {
          // Bust the 60s GH issue-state cache, then re-query so auto-verify
          // can reclassify any closed issues immediately.
          await ccPostJson('/api/bust-issue-state', {}).catch(() => {});
          await loadAttentionList();
          refreshConversationList();
        } finally {
          setTimeout(() => { $refresh.disabled = false; $refresh.style.opacity = ''; }, 400);
        }
      });
    }

    // Resize handle: drag DOWN to grow the NYA panel, shrinking kanban below.
    const ATT_HEIGHT_KEY = 'ccc-attention-height';
    const ATT_MIN_PX = 80;
    const $handle = document.getElementById('attentionResizeHandle');

    function attMaxPx() {
      const parent = $panel.parentElement;
      if (!parent) return 9999;
      return Math.max(ATT_MIN_PX, Math.floor(parent.clientHeight * 0.7));
    }
    function applyAttHeight(h) {
      const clamped = Math.max(ATT_MIN_PX, Math.min(attMaxPx(), h));
      $panel.style.height = clamped + 'px';
    }
    function resetAttHeight() {
      $panel.style.height = '';
      try { localStorage.removeItem(ATT_HEIGHT_KEY); } catch (_) {}
    }
    try {
      const stored = parseInt(localStorage.getItem(ATT_HEIGHT_KEY) || '', 10);
      if (!isNaN(stored) && stored > 0) {
        // Defer so the parent has laid out and clientHeight is meaningful.
        requestAnimationFrame(() => applyAttHeight(stored));
      }
    } catch (_) {}

    if ($handle) {
      let startY = 0;
      let startH = 0;
      let activePointerId = null;
      $handle.addEventListener('pointerdown', (e) => {
        if ($panel.classList.contains('collapsed')) return;
        e.preventDefault();
        e.stopPropagation();
        activePointerId = e.pointerId;
        startY = e.clientY;
        startH = $panel.getBoundingClientRect().height;
        $handle.classList.add('is-dragging');
        try { $handle.setPointerCapture(e.pointerId); } catch (_) {}
      });
      $handle.addEventListener('pointermove', (e) => {
        if (activePointerId !== e.pointerId) return;
        // Handle is on TOP of the bottom-mounted panel — drag UP grows the
        // panel (negative dy = bigger height). Inverted from the old
        // top-mounted layout where drag-down grew it.
        const dy = e.clientY - startY;
        applyAttHeight(startH - dy);
      });
      const endDrag = (e) => {
        if (activePointerId !== e.pointerId) return;
        $handle.classList.remove('is-dragging');
        try { $handle.releasePointerCapture(e.pointerId); } catch (_) {}
        activePointerId = null;
        const finalH = $panel.getBoundingClientRect().height;
        try { localStorage.setItem(ATT_HEIGHT_KEY, String(Math.round(finalH))); } catch (_) {}
      };
      $handle.addEventListener('pointerup', endDrag);
      $handle.addEventListener('pointercancel', endDrag);
      $handle.addEventListener('dblclick', (e) => {
        e.preventDefault();
        e.stopPropagation();
        resetAttHeight();
      });
    }
  })();

  function relativeTime(ts) {
    const nowSec = Date.now() / 1000;
    const diff = Math.max(0, nowSec - ts);
    if (diff < 60) return 'just now';
    if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
    if (diff < 604800) return Math.floor(diff / 86400) + 'd ago';
    return Math.floor(diff / 604800) + 'w ago';
  }

  // ── Unified render dispatcher: list vs kanban ──
  // Shared stage logic — simple watermark
  function sessionStage(c) {
    if (c.has_push) return 'pushed';
    if (c.has_commit) return 'committed';
    if (c.has_edit) return 'coding';
    return 'planning';
  }
  function stageClass(stage) {
    if (stage === 'pushed') return 'pushed';
    if (stage === 'committed') return 'committed';
    return 'stage';
  }

  // Set while the user is mid-rename so background refreshes don't yank
  // the input out from under them. Cleared by the commit/cancel path
  // *before* it asks for a re-render, so the legitimate post-save
  // refresh still flows through.
  let _renameInProgress = false;
  function isInlineRenameInProgress() {
    if (_renameInProgress) return true;
    const $list = document.getElementById('convList');
    return !!($list && $list.querySelector('.conv-title-input'));
  }
  function renderSidebar(convs) {
    if (_renameInProgress) return;
    const $kanbanBoard = document.getElementById('kanbanBoard');
    const $convList = document.getElementById('convList');
    if ($kanbanBoard) $kanbanBoard.style.display = 'none';
    if ($convList) $convList.style.display = '';
    const $search = document.getElementById('convSearch');
    try { renderArchiveList($search ? $search.value : ''); } catch (_) {}
  }

  // Legacy list-view scroll/kanban rendering — kept for reference but no
  // longer reachable since the sidebar is always in archive mode.
  function _renderSidebarLegacy(convs) {
    if (_renameInProgress) return;
    // Preserve scroll across ALL re-renders (board + per-column + list)
    const scrolls = {};
    document.querySelectorAll('.kanban-board, .kanban-board-split, #convList').forEach(el => {
      if (el.scrollLeft > 0 || el.scrollTop > 0) {
        const key = el.id || (el.className.split(' ')[0] + (el.closest('.kanban-panel') ? '-split' : ''));
        scrolls[key] = [el.scrollLeft, el.scrollTop];
      }
    });
    document.querySelectorAll('.kanban-column').forEach(el => {
      const cards = el.querySelector('.kanban-cards');
      if (cards && cards.scrollTop > 0) {
        const inSplit = el.closest('.kanban-panel') ? '-split' : '';
        scrolls['col-' + el.dataset.col + inSplit] = [0, cards.scrollTop];
      }
    });

    if (kanbanView) {
      $convList.style.display = 'none';
      $kanbanBoard.style.display = '';
      renderKanbanBoard(convs, $kanbanBoard, false);
      if ($kanbanBoardSplit) renderKanbanBoard(convs, $kanbanBoardSplit, true);
    } else {
      $convList.style.display = '';
      $kanbanBoard.style.display = 'none';
      renderConversationList(convs);
    }

    // Restore scroll
    requestAnimationFrame(() => {
      for (const [key, [left, top]] of Object.entries(scrolls)) {
        if (key.startsWith('col-')) {
          const isSplit = key.endsWith('-split');
          const colKey = isSplit ? key.slice(4, -6) : key.slice(4);
          const scope = isSplit ? '.kanban-panel' : '';
          const selector = scope + ' .kanban-column[data-col="' + colKey + '"] .kanban-cards';
          const cards = document.querySelector(selector);
          if (cards) cards.scrollTop = top;
        } else {
          const el = document.getElementById(key) || document.querySelector('.' + key.replace('-split', ''));
          if (el) { el.scrollLeft = left; el.scrollTop = top; }
        }
      }
      // First render on mobile: center the Working column so users land on
      // what they're most likely looking at.
      if (!_didInitialWorkingScroll && isMobile() && $kanbanBoardSplit && !scrolls['kanbanBoardSplit']) {
        const workingCol = $kanbanBoardSplit.querySelector('.kanban-column[data-col="working"]');
        if (workingCol) {
          workingCol.scrollIntoView({ inline: 'center', block: 'nearest' });
          _didInitialWorkingScroll = true;
        }
      }
      _applyCardTransitions();
    });
  }
  let _didInitialWorkingScroll = false;

  // Per-session column memory, used after each render to detect natural column
  // changes (classifier moved a card) versus drag-drop (already handles its
  // own highlight). Flash .just-moved so the user's eye catches the shift,
  // and card-enter on brand-new cards so they fade in instead of snap in.
  const _lastRenderedCol = new Map();  // session_id → last-rendered column key
  let _kanbanHasRenderedOnce = false;
  function _applyCardTransitions() {
    // Track which sids exist in this render so we know which to evict.
    const seenThisRender = new Set();
    // De-dupe across board + split — both panels render the same cards; we
    // only want to stamp the class once per sid to avoid doubling animations.
    const stampedJustMoved = new Set();
    const stampedEnter = new Set();
    document.querySelectorAll('.kanban-card[data-session-id]').forEach(el => {
      const sid = el.dataset.sessionId;
      const col = el.dataset.col;
      if (!sid || !col) return;
      seenThisRender.add(sid);
      const prev = _lastRenderedCol.get(sid);
      if (prev === undefined) {
        // First time we've seen this card — fade it in. Skipped on the very
        // first board render (_kanbanHasRenderedOnce=false) so the entire
        // board doesn't fade in at once on page load.
        if (_kanbanHasRenderedOnce && !stampedEnter.has(sid)) {
          stampedEnter.add(sid);
          el.classList.remove('card-enter');
          void el.offsetWidth;
          el.classList.add('card-enter');
          setTimeout(() => el.classList.remove('card-enter'), 260);
        }
      } else if (prev !== col && !stampedJustMoved.has(sid)) {
        stampedJustMoved.add(sid);
        el.classList.remove('just-moved');
        void el.offsetWidth;
        el.classList.add('just-moved');
        setTimeout(() => el.classList.remove('just-moved'), 2300);
      }
    });
    // Commit this render's column state to the memory map (first seen wins,
    // since board + split each stamp the same sid).
    const nextCols = new Map();
    document.querySelectorAll('.kanban-card[data-session-id]').forEach(el => {
      const sid = el.dataset.sessionId;
      if (sid && !nextCols.has(sid)) nextCols.set(sid, el.dataset.col);
    });
    // Evict sids that are no longer rendered (archived-out, filtered away, …)
    for (const sid of Array.from(_lastRenderedCol.keys())) {
      if (!seenThisRender.has(sid)) _lastRenderedCol.delete(sid);
    }
    for (const [sid, col] of nextCols) _lastRenderedCol.set(sid, col);
    _kanbanHasRenderedOnce = true;
  }

  // Manual column overrides (drag-and-drop) — session_id → column key
  let columnOverrides = {};
  try { columnOverrides = JSON.parse(localStorage.getItem('ccc-column-overrides') || '{}'); } catch (_) {}

  // One-time migration: older builds of the kanban stored drag-to-Verified
  // (and drag-to-Archived) as a client-only override without calling the
  // server's verify/archive endpoints. Now that those calls are wired, we
  // reconcile by pushing any stale `verified` / `archived` overrides to the
  // server (idempotent — the endpoint is a no-op if the flag is already set)
  // and then clearing them from localStorage. Runs once per browser; clear
  // the `clv-override-sync-done` key to re-run.
  (async function migrateStaleOverrides() {
    try {
      if (localStorage.getItem('ccc-override-sync-done') === '1') return;
      const keys = Object.keys(columnOverrides);
      if (!keys.length) {
        localStorage.setItem('ccc-override-sync-done', '1');
        return;
      }
      // cardId in the override key is either a UUID session_id, "issue-N",
      // or "pkood-*". Only those hit the server endpoints; everything else
      // (e.g. "spawning-*", "backlog-todo-*") stays client-only.
      const serverEligible = /^([a-f0-9-]{8,}|issue-\d+|pkood-.+)$/;
      let synced = 0;
      for (const cardId of keys) {
        const target = columnOverrides[cardId];
        if (target !== 'verified' && target !== 'archived') continue;
        if (!serverEligible.test(cardId)) continue;
        try {
          const endpoint = target === 'verified' ? 'verify' : 'archive';
          const body = target === 'verified'
            ? { verified: true, session_id: cardId }
            : { session_id: cardId };
          const r = await fetch('/api/conversations/' + cardId + '/' + endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
          if (!r.ok) throw new Error('HTTP ' + r.status);
          delete columnOverrides[cardId];
          synced++;
        } catch (e) {
          console.warn('override sync failed for', cardId, target, e);
          // Leave override in place so next run retries.
        }
      }
      try {
        localStorage.setItem('ccc-column-overrides', JSON.stringify(columnOverrides));
      } catch (_) {}
      localStorage.setItem('ccc-override-sync-done', '1');
      if (synced) {
        console.debug('[override-sync] pushed', synced, 'stale verified/archived override(s) to server');
      }
    } catch (e) {
      console.warn('override migration crashed:', e);
    }
  })();
  // Guard against double-spawn from rapid mobile taps. Key is "issue-<num>" or session name.
  const _spawningKeys = new Set();
  const _optimisticStartedIssueKeys = new Set();
  const _OPTIMISTIC_ISSUE_START_TTL_MS = 60000;

  function _issueStartKey(issueNum, repoPath) {
    const num = String(issueNum || '').trim();
    if (!num) return '';
    return String(repoPath || '').trim() + '#' + num;
  }

  function _markIssueOptimisticallyStarted(issueNum, repoPath) {
    const key = _issueStartKey(issueNum, repoPath);
    if (!key) return;
    _optimisticStartedIssueKeys.add(key);
    setTimeout(() => {
      _optimisticStartedIssueKeys.delete(key);
    }, _OPTIMISTIC_ISSUE_START_TTL_MS);
  }

  function _clearOptimisticStartedIssue(issueNum, repoPath) {
    const key = _issueStartKey(issueNum, repoPath);
    if (key) _optimisticStartedIssueKeys.delete(key);
  }

  function _isOptimisticallyStartedIssueRow(row) {
    if (!row || row.source !== 'backlog' || row.backlog_type !== 'github' || !row.issue_number) return false;
    const repoPath = rowRepoPath(row) || row.spawn_cwd || '';
    return _optimisticStartedIssueKeys.has(_issueStartKey(row.issue_number, repoPath));
  }

  function _pendingIssueSpawnMeta(issueNum, repoPath) {
    if (!issueNum) return {};
    const label = (repoPath && (
      ((repoListState.repos || []).find(repo => repo.path === repoPath) || {}).label ||
      _pathLeaf(repoPath)
    )) || '';
    const meta = {
      linked_issue: issueNum,
      issue_number: issueNum,
    };
    if (repoPath) {
      meta.repo_path = repoPath;
      meta.folder_path = repoPath;
      meta.folder_label = label || repoPath;
      meta.folder_label_chip = label || repoPath;
      meta.folder_chip_hue = _hashHue(label || repoPath);
      meta.folder_chip_orphan = false;
      meta.spawn_cwd = repoPath;
      meta.cwd = repoPath;
    }
    return meta;
  }

  function _removePendingSpawnCard(pid) {
    if (!pid) return;
    const id = 'spawning-' + pid;
    pendingSpawns.delete(pid);
    delete columnOverrides[id];
    conversationsData = conversationsData.filter(x => x && x.id !== id);
    try { localStorage.setItem('ccc-column-overrides', JSON.stringify(columnOverrides)); } catch (_) {}
    renderSidebar(filterConversations($convSearch.value));
  }
  // Client-side persistence for archived TODO.md / PARKING_LOT.md backlog cards.
  // (The server re-parses those files on every request, so archive state has to
  // live in the browser to stick.)
  let _archivedBacklogIds = new Set();
  try {
    const saved = JSON.parse(localStorage.getItem('ccc-archived-backlog') || '[]');
    if (Array.isArray(saved)) _archivedBacklogIds = new Set(saved);
  } catch (_) {}
  function _persistArchivedBacklog() {
    try { localStorage.setItem('ccc-archived-backlog', JSON.stringify([..._archivedBacklogIds])); } catch (_) {}
  }

  // Multi-select state for kanban cards
  let selectedCardIds = new Set();
  let selectedListIds = new Set();

  function updateCoordToolbar() {
    const toolbar = document.getElementById('coordToolbar');
    const countEl = document.getElementById('coordCount');
    if (!toolbar) return;
    if (selectedListIds.size >= 1) {
      toolbar.classList.add('visible');
      if (countEl) countEl.textContent = selectedListIds.size === 1 ? '1 session selected' : selectedListIds.size + ' sessions selected';
    } else {
      toolbar.classList.remove('visible');
    }
  }

  function openCoordModal() {
    if (selectedListIds.size < 1) return;
    const backdrop = document.getElementById('coordModalBackdrop');
    const topicInput = document.getElementById('coordTopicInput');
    const participantsList = document.getElementById('coordParticipantsList');
    const modeHint = document.getElementById('coordModeHint');
    const errorEl = document.getElementById('coordModalError');
    if (!backdrop || !participantsList) return;

    const selectedRows = Array.from(selectedListIds)
      .map(id => conversationsData.find(c => c.id === id))
      .filter(Boolean);

    const cwds = selectedRows.map(r => rowRepoPath(r)).filter(Boolean);
    const allSameRepo = cwds.length === selectedRows.length && cwds.length > 0 && cwds.every(p => p === cwds[0]);
    const someLackCwd = cwds.length < selectedRows.length;
    const autoMode = allSameRepo ? 'git' : 'topic';
    const gitRadio = document.getElementById('coordModeGitRadio');
    const topicRadio = document.getElementById('coordModeTopicRadio');
    if (autoMode === 'git' && gitRadio) gitRadio.checked = true;
    else if (topicRadio) topicRadio.checked = true;
    if (modeHint) modeHint.textContent = allSameRepo
      ? 'Auto-detected: all sessions share a repo.'
      : someLackCwd
        ? 'Auto-detected: one or more sessions have no repo context.'
        : 'Auto-detected: sessions span multiple repos.';

    participantsList.innerHTML = selectedRows.map(r => {
      const sid = r.session_id || r.id;
      const rawName = r.display_name || sid;
      const name = escapeHtml(rawName.length > 50 ? rawName.slice(0, 49) + '…' : rawName);
      const cwd = rowRepoPath(r) || r.session_cwd || '';
      const shortCwd = cwd.length > 40 ? '…' + cwd.slice(-39) : cwd;
      return '<div class="participant-row">'
        + '<input type="checkbox" checked data-sid="' + escapeAttr(sid) + '" data-name="' + escapeAttr(r.display_name || sid) + '" data-cwd="' + escapeAttr(cwd) + '">'
        + '<span class="p-name">' + name + '</span>'
        + '<span class="p-cwd">' + escapeHtml(shortCwd) + '</span>'
        + '</div>';
    }).join('') + '<div class="participant-row">'
      + '<input type="checkbox" checked id="coordHumanCheck">'
      + '<span class="p-name">You (human)</span>'
      + '<span class="p-cwd">posts directly to chat</span>'
      + '</div>';

    if (errorEl) { errorEl.textContent = ''; errorEl.classList.remove('visible'); }
    if (topicInput) { topicInput.value = ''; }
    backdrop.classList.add('visible');
    setTimeout(() => { if (topicInput) topicInput.focus(); }, 50);
  }

  async function startCoordination() {
    const backdrop = document.getElementById('coordModalBackdrop');
    const topicInput = document.getElementById('coordTopicInput');
    const errorEl = document.getElementById('coordModalError');
    const topic = (topicInput ? topicInput.value : '').trim();
    if (!topic) {
      if (errorEl) { errorEl.textContent = 'Topic is required.'; errorEl.classList.add('visible'); }
      if (topicInput) topicInput.focus();
      return;
    }
    const modeEl = document.querySelector('input[name="coordMode"]:checked');
    const mode = modeEl ? modeEl.value : 'topic';
    const humanCheckbox = document.getElementById('coordHumanCheck');
    const includeHuman = humanCheckbox ? humanCheckbox.checked : true;

    const checkedBoxes = Array.from(
      (document.getElementById('coordParticipantsList') || {querySelectorAll: () => []})
        .querySelectorAll('input[type="checkbox"][data-sid]')
    ).filter(cb => cb.checked);

    const sessionIds = checkedBoxes.map(cb => cb.dataset.sid);
    const sessionsMeta = checkedBoxes.map(cb => ({
      session_id: cb.dataset.sid,
      display_name: cb.dataset.name || cb.dataset.sid,
      cwd: cb.dataset.cwd || '',
    }));

    if (sessionIds.length < 2) {
      if (errorEl) { errorEl.textContent = 'Select at least 2 sessions.'; errorEl.classList.add('visible'); }
      return;
    }

    const startBtn = document.getElementById('coordModalStart');
    if (startBtn) startBtn.disabled = true;
    try {
      const result = await ccPostJson('/api/coordinate', {
        session_ids: sessionIds, topic, mode, sessions_meta: sessionsMeta, include_human: includeHuman,
      });
      if (!result.ok) {
        if (errorEl) { errorEl.textContent = result.error || 'Failed to start coordination.'; errorEl.classList.add('visible'); }
        return;
      }
      (result.results || []).forEach(r => {
        if (!r.ok) showOpToast('Could not reach session — check its terminal (' + (r.error || 'tty not found') + ')', 'error');
      });
      if (backdrop) backdrop.classList.remove('visible');
      selectedListIds.clear();
      document.querySelectorAll('.conv-item.list-selected').forEach(el => el.classList.remove('list-selected'));
      updateCoordToolbar();
      openGroupChatReader(result.chat_path, topic, mode, includeHuman);
    } catch (err) {
      if (errorEl) { errorEl.textContent = 'Request failed: ' + err.message; errorEl.classList.add('visible'); }
    } finally {
      if (startBtn) startBtn.disabled = false;
    }
  }

  let _gcReaderInterval = null;
  let _gcReaderPath = null;
  let _gcLastMtime = null;
  let _gcPollFailCount = 0;
  let _gcLastNudgeTime = 0;

  function openGroupChatReader(chatPath, topic, mode, includeHuman) {
    _gcReaderPath = chatPath;
    _gcLastMtime = null;
    _gcPollFailCount = 0;
    _gcLastNudgeTime = 0;

    const pane = document.querySelector('.conv-pane');
    if (!pane) return;

    const topicSafe = escapeHtml(topic);
    const modeSafe = escapeHtml(mode);
    pane.innerHTML = '<div class="gc-reader" id="gcReader">'
      + '<div class="gc-reader-header">'
        + '<button class="gc-close" id="gcCloseBtn" title="Close group chat reader">← Back</button>'
        + '<span class="gc-topic" title="' + topicSafe + '">' + topicSafe + '</span>'
        + '<span class="gc-mode-badge">' + modeSafe + '</span>'
      + '</div>'
      + '<div class="gc-reader-body" id="gcReaderBody">Loading…</div>'
      + (includeHuman
        ? '<div class="gc-reader-input-row" id="gcInputRow">'
            + '<input type="text" id="gcHumanInput" placeholder="Add to chat…" autocomplete="off">'
            + '<button id="gcSendBtn">Send</button>'
          + '</div>'
        : '')
      + '</div>';

    const closeBtn = document.getElementById('gcCloseBtn');
    if (closeBtn) closeBtn.addEventListener('click', closeGroupChatReader);

    if (includeHuman) {
      const gcSendBtn = document.getElementById('gcSendBtn');
      const gcHumanInput = document.getElementById('gcHumanInput');
      if (gcSendBtn) gcSendBtn.addEventListener('click', () => sendHumanGcPost());
      if (gcHumanInput) gcHumanInput.addEventListener('keydown', ev => {
        if (ev.key === 'Enter') sendHumanGcPost();
      });
    }

    if (_gcReaderInterval) clearInterval(_gcReaderInterval);
    pollGroupChatReader();
    _gcReaderInterval = setInterval(pollGroupChatReader, 3000);
  }

  async function pollGroupChatReader() {
    if (!_gcReaderPath) return;
    const body = document.getElementById('gcReaderBody');
    if (!body) return;
    try {
      const res = await fetch('/api/group-chat/read?path=' + encodeURIComponent(_gcReaderPath));
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();
      _gcPollFailCount = 0;
      const errBanner = body.querySelector('.gc-poll-error');
      if (errBanner) errBanner.remove();
      if (!data.ok) { body.innerHTML = renderMarkdown(data.error || 'File not found.'); return; }
      if (data.mtime !== _gcLastMtime) {
        const isFirstLoad = _gcLastMtime === null;
        _gcLastMtime = data.mtime;
        const atBottom = body.scrollHeight - body.scrollTop <= body.clientHeight + 40;
        body.innerHTML = renderMarkdown(data.content);
        if (atBottom) body.scrollTop = body.scrollHeight;
        // Nudge all participants when content changes (but not on first load, and debounced to 15s).
        if (!isFirstLoad) {
          const now = Date.now();
          if (now - _gcLastNudgeTime > 15000) {
            _gcLastNudgeTime = now;
            ccPostJson('/api/group-chat/nudge', { path: _gcReaderPath }).catch(() => {});
          }
        }
      }
    } catch (_err) {
      _gcPollFailCount++;
      if (_gcPollFailCount >= 3) {
        let errBanner = body.querySelector('.gc-poll-error');
        if (!errBanner) {
          errBanner = document.createElement('div');
          errBanner.className = 'gc-poll-error';
          body.prepend(errBanner);
        }
        errBanner.textContent = '⚠ Lost connection to chat file — retrying…';
      }
    }
  }

  async function sendHumanGcPost() {
    if (!_gcReaderPath) return;
    const input = document.getElementById('gcHumanInput');
    const text = input ? input.value.trim() : '';
    if (!text) return;
    try {
      await ccPostJson('/api/group-chat/post', { path: _gcReaderPath, text });
      if (input) input.value = '';
      await pollGroupChatReader();
    } catch (err) {
      showOpToast('Send failed: ' + err.message, 'error');
    }
  }

  function closeGroupChatReader() {
    if (_gcReaderInterval) { clearInterval(_gcReaderInterval); _gcReaderInterval = null; }
    _gcReaderPath = null;
    _gcLastMtime = null;
    _gcPollFailCount = 0;
    const pane = document.querySelector('.conv-pane');
    if (pane) pane.innerHTML = '';
    if (typeof currentConversation === 'string' && currentConversation) {
      try { selectConversation(currentConversation); } catch (_) {}
    }
  }

  async function moveCardsToColumn(cardIds, targetCol) {
    for (const cardId of cardIds) {
      await moveCardToColumn(cardId, targetCol);
    }
    selectedCardIds.clear();
    requestAnimationFrame(() => {
      cardIds.forEach(id => {
        document.querySelectorAll('.kanban-card[data-id="' + CSS.escape(id) + '"]').forEach(el => {
          el.classList.remove('just-moved');
          void el.offsetWidth;
          el.classList.add('just-moved');
          setTimeout(() => el.classList.remove('just-moved'), 2400);
        });
      });
    });
  }

  // Fetch helper: POSTs JSON, rejects on non-2xx. Callers must await and handle.
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

  function archivePayloadForRow(row, sessionId) {
    return withRepoPath({ session_id: sessionId }, rowRepoPath(row));
  }

  function showOpToast(msg, kind) {
    const toast = document.createElement('div');
    const color = kind === 'error' ? 'var(--red)' : 'var(--green)';
    const mark = kind === 'error' ? '!' : '\u2713';
    toast.style.cssText = 'position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--surface);border:1px solid var(--border);padding:8px 14px;border-radius:6px;font-size:12px;color:var(--text);z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.4);max-width:520px;';
    toast.innerHTML = '<span style="color:' + color + '">' + mark + '</span> ' + msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), kind === 'error' ? 5000 : 3000);
  }

  async function moveCardToColumn(cardId, targetCol) {
    const c = conversationsData.find(x => x.id === cardId);
    if (!c) return;
    const sid = c.session_id || cardId;

    // Special columns: flip backend flags instead of just overriding.
    // Drag-to-Verified passes {verified: true} explicitly so the (now
    // idempotent) endpoint can't toggle the wrong way on stale client state.
    // Backend call must land before we flip local state — otherwise NYA/
    // attention panels (which read server truth) won't agree with the UI.
    if (targetCol === 'verified') {
      try {
        await ccPostJson('/api/conversations/' + cardId + '/verify',
          { verified: true, session_id: sid, display_name: c.display_name || '', cwd: c.session_cwd || '' });
        c.verified = true;
        setOptimisticOverride(sid, { verified: true });
        delete columnOverrides[sid];
      } catch (err) {
        showOpToast('Verify failed — card stays put (' + err.message + ')', 'error');
        renderSidebar(filterConversations($convSearch.value));
        return;
      }
    }
    // Moving into working/icebox: set override + signal to GitHub
    if (targetCol === 'working' || targetCol === 'icebox') {
      // Unflip backend verified/archived if needed (must succeed before override)
      if (c.verified) {
        try {
          await ccPostJson('/api/conversations/' + cardId + '/verify',
            { verified: false, session_id: sid });
          c.verified = false;
          setOptimisticOverride(sid, { verified: false });
        } catch (err) {
          showOpToast('Un-verify failed — card stays in Verified (' + err.message + ')', 'error');
          renderSidebar(filterConversations($convSearch.value));
          return;
        }
      }
      if (c.archived) {
        try {
          await ccPostJson('/api/conversations/' + cardId + '/archive',
            { session_id: sid });
          c.archived = false;
          setOptimisticOverride(sid, { archived: false });
        } catch (err) {
          showOpToast('Un-archive failed — card stays in Archived (' + err.message + ')', 'error');
          renderSidebar(filterConversations($convSearch.value));
          return;
        }
      }
      columnOverrides[sid] = targetCol;
      const issueNum = c.linked_issue || c.issue_number || (() => {
        const m = /^issue-(\d+)$/.exec(c.display_name || '');
        return m ? m[1] : null;
      })() || ((c.id || '').startsWith('backlog-issue-') ? c.id.split('-').pop() : null);
      if (issueNum) {
        // Icebox → `icebox` label ("parked, not active"); Working → `claude-in-progress`.
        const endpoint = targetCol === 'icebox' ? 'mark-icebox' : 'mark-in-progress';
        fetch('/api/issues/' + issueNum + '/' + endpoint, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(withRepoPath({}, rowRepoPath(c) || repoPathForIssueNumber(issueNum))),
        })
          .then(() => setTimeout(refreshConversationList, 800))
          .catch(() => {});
      }
    } else if (targetCol === 'archived') {
      // TODO.md / PARKING_LOT.md cards have no server-side archive — persist locally
      // so the state sticks across reloads (server re-parses the files every time).
      if ((cardId || '').startsWith('backlog-todo-') || (cardId || '').startsWith('backlog-parking-')) {
        _archivedBacklogIds.add(cardId);
        _persistArchivedBacklog();
        c.archived = true;
        delete columnOverrides[sid];
      } else if (!c.archived) {
        try {
          await ccPostJson('/api/conversations/' + cardId + '/archive',
            { session_id: sid });
          c.archived = true;
          setOptimisticOverride(sid, { archived: true });
          delete columnOverrides[sid];
        } catch (err) {
          showOpToast('Archive failed — card stays put (' + err.message + ')', 'error');
          renderSidebar(filterConversations($convSearch.value));
          return;
        }
      } else {
        delete columnOverrides[sid];
      }
    } else {
      // Moving OUT of verified/archived? unflip the backend (must succeed first)
      if (c.verified) {
        try {
          await ccPostJson('/api/conversations/' + cardId + '/verify',
            { verified: false, session_id: sid });
          c.verified = false;
          setOptimisticOverride(sid, { verified: false });
        } catch (err) {
          showOpToast('Un-verify failed — card stays in Verified (' + err.message + ')', 'error');
          renderSidebar(filterConversations($convSearch.value));
          return;
        }
      }
      if (c.archived) {
        try {
          await ccPostJson('/api/conversations/' + cardId + '/archive',
            { session_id: sid });
          c.archived = false;
          setOptimisticOverride(sid, { archived: false });
        } catch (err) {
          showOpToast('Un-archive failed — card stays in Archived (' + err.message + ')', 'error');
          renderSidebar(filterConversations($convSearch.value));
          return;
        }
      }
      columnOverrides[sid] = targetCol;
    }
    try { localStorage.setItem('ccc-column-overrides', JSON.stringify(columnOverrides)); } catch (_) {}
    renderSidebar(filterConversations($convSearch.value));
  }

  function classifyKanbanColumn(c) {
    // Notification hook signal — Claude Code is explicitly asking for
    // permission. This is the strongest "human input required" signal we
    // have, so it overrides natural classification and the fresh-session
    // sticky alike. Cleared automatically when PostToolUse fires.
    // Server-truth terminal states (archived / verified) still win — an
    // archived session shouldn't get pinned to Waiting because a stale
    // _needs_approval marker is still on disk; otherwise the row-list
    // archive button toggles its icon but the row never leaves "In progress".
    if (c && c.needs_approval && !c.archived && !c.verified) return 'waiting';
    const raw = _classifyKanbanColumnNatural(c);
    return _applyFreshSessionSticky(c, raw);
  }

  // For a session first observed within _STICKY_TTL_MS, pin it to its initial
  // classification column until either (a) visible progress fires a signal
  // change or (b) the window expires. This stops the Planning↔Review bounce
  // that the user sees during the first minute of a fresh spawn, while still
  // honoring manual drag-drop (columnOverrides) and server truth (verify /
  // archive / backlog) unconditionally.
  function _applyFreshSessionSticky(c, raw) {
    const sid = c.session_id;
    if (!sid) return raw;
    const born = _firstSeenSessions.get(sid);
    if (!born || (Date.now() - born) >= _STICKY_TTL_MS) {
      if (_stickyInitialCol.has(sid)) _stickyInitialCol.delete(sid);
      return raw;
    }
    // Manual override and server-truth columns always win.
    if (columnOverrides[sid]) return raw;
    if (raw === 'verified' || raw === 'archived'
        || raw === 'backlog' || raw === 'needs-attention') {
      return raw;
    }
    let snap = _stickyInitialCol.get(sid);
    if (!snap) {
      snap = { col: raw, signals: _signalSnapshot(c) };
      _stickyInitialCol.set(sid, snap);
      return raw;
    }
    // If the session has made real progress since we pinned it, release the
    // sticky so normal classification takes over immediately.
    const s = snap.signals;
    const advanced = (c.has_edit && !s.has_edit)
      || (c.has_commit && !s.has_commit)
      || (c.has_push && !s.has_push)
      || (!!c.last_event_type && c.last_event_type !== s.last_event_type);
    if (advanced) {
      _stickyInitialCol.delete(sid);
      return raw;
    }
    return snap.col;
  }

  function _classifyKanbanColumnNatural(c) {
    // Manual override from drag-drop takes priority
    const ov = columnOverrides[c.session_id || c.id];
    if (ov) {
      // Auto-clear stale overrides only when the session has ADVANCED past the
      // override (e.g., pushed/committed while override=working). Do NOT clear
      // just because the session went idle — user explicitly placed it here.
      const stage = sessionStage(c);
      const ghClosed = (c.gh_state || '').toUpperCase() === 'CLOSED';
      const stale =
        // Migration: 'planning' was renamed to 'icebox' (planning→icebox refactor)
        // and the 'inactive' column was dropped (idle is now an attribute on
        // Working cards). Drop any leftover overrides from older builds.
        ov === 'planning' || ov === 'inactive' ||
        // Server truth wins: verified/archived cannot coexist with an active-work override.
        c.verified || c.archived ||
        // Only flag working/icebox as stale when work has truly shipped (pushed)
        // or the linked GH issue is closed. Committed-but-not-pushed sessions are
        // legitimately still "in-flight" work — keep the override.
        ((ov === 'working' || ov === 'icebox') && (stage === 'pushed' || ghClosed)) ||
        ((ov === 'archived') && c.id && c.id.startsWith('backlog-issue-') && (c.issue_state || '').toUpperCase() === 'OPEN');
      if (stale) {
        delete columnOverrides[c.session_id || c.id];
        try { localStorage.setItem('ccc-column-overrides', JSON.stringify(columnOverrides)); } catch (_) {}
      } else {
        return ov;
      }
    }
    if (c.verified) return 'verified';
    if (c.archived) return 'archived';

    // GitHub-label-driven routing (applies to backlog cards AND linked sessions
    // whose linked issue carries these labels)
    const ghLabels = c.gh_labels || c.issue_labels || [];
    const hasLabel = (name) => Array.isArray(ghLabels) && ghLabels.indexOf(name) >= 0;

    if (c.source === 'backlog') {
      if (c.issue_state === 'CLOSED') {
        const reason = (c.issue_state_reason || '').toUpperCase();
        if (reason === 'COMPLETED') return 'verified';
        return 'archived';  // not planned / duplicate / unspecified
      }
      if (hasLabel('needs-attention')) return 'needs-attention';
      if (hasLabel('icebox')) return 'icebox';
      return 'backlog';
    }

    // Tiebreak: explicit `icebox` label wins over implicit liveness. A live
    // session whose issue is parked stays in icebox until the label comes off.
    if (hasLabel('icebox')) return 'icebox';

    // All live sessions land in Working — sidecar / no-sidecar / has_writes /
    // no_writes / pkood / pre-tool — they're all live work in progress. The
    // pre-tool window used to be its own 'planning' column; that's now an
    // in-card status, not a column.
    if (c.is_live) return 'working';

    // From here on, the session is dead. Decide based on what it left behind.
    const stage = sessionStage(c);
    if (stage === 'pushed' || stage === 'committed') return 'review';
    // Dormant session that made edits and finished its last turn with an assistant
    // message (typically a "done, here's the summary" reply). The work is sitting
    // in the working tree waiting for human review — belongs in Review, not Inactive.
    if (stage === 'coding' && c.last_event_type === 'assistant') return 'review';
    if (hasLabel('needs-attention')) return 'needs-attention';
    // A session marked claude-in-progress but not actually running:
    // belongs in Working (the label says this is the active work).
    if (hasLabel('claude-in-progress')) return 'working';

    // Fallback: dead session with no commits/edits/labels. Used to land in a
    // separate 'inactive' column; now lives in Working. The "no edits" chip
    // (see hasNoEdits) flags any card — live or dead — that has yet to touch
    // a file, so the user can spot resumable shells without a separate column.
    return 'working';
  }

  // True when a finished session produced useful output but intentionally
  // left files alone. This keeps read-only helper/subagent work from looking
  // like an abandoned shell.
  function hasReadOnlyWork(c) {
    if (!c) return false;
    if (c.verified || c.archived) return false;
    if (c.source === 'backlog') return false;
    if (c.is_live || c.pending_spawn) return false;
    if (c.has_edit || c.has_commit || c.has_push || c.tail_pr_number) return false;
    const lastType = String(c.last_event_type || '').toLowerCase();
    return !!(
      String(c.last_assistant_text || '').trim()
      || c.pending_tool
      || lastType === 'result'
    );
  }

  // True when this session has never edited a file and has no completed
  // read-only output to show. Drives the "no edits" chip in both the list
  // view and the kanban card. Excludes verified / archived (terminal state,
  // chip is noise) and backlog (different render path).
  function hasNoEdits(c) {
    if (!c) return false;
    if (c.verified || c.archived) return false;
    if (c.source === 'backlog') return false;
    if (hasReadOnlyWork(c)) return false;
    return !c.has_edit;
  }

  function renderKanbanBoard(convs, targetEl, isSplit) {
    if (!targetEl) targetEl = $kanbanBoard;
    const defaultColumns = [
      { key: 'backlog',         label: 'GH Issues',       defaultExpanded: true,
        hint: 'Open GitHub issues and TODO.md / PARKING_LOT items with no session yet.' },
      { key: 'needs-attention', label: 'Needs attention', defaultExpanded: true,
        hint: 'Issues labeled `needs-attention` on GitHub — you flagged them for triage.' },
      { key: 'icebox',          label: 'Icebox',          defaultExpanded: true,
        hint: 'Parked. The `icebox` GitHub label is set, or you dragged it here. Active intent: don\'t work on this right now.' },
      { key: 'working',         label: 'In progress',     defaultExpanded: true,
        hint: 'Live or resumable sessions. Idle ones (no commits / no live process) get a blue Idle pill — pick one back up by jumping in.' },
      { key: 'waiting',         label: 'Waiting',         defaultExpanded: true,
        hint: 'Claude is asking for permission (Notification hook). Approve or deny in the terminal.' },
      { key: 'review',          label: 'Review',          defaultExpanded: true,
        hint: 'Committed or pushed work waiting for you to read and verify.' },
      { key: 'testing',         label: 'In Testing',      defaultExpanded: true,
        hint: 'Manually moved here — work is under human validation.' },
      { key: 'verified',        label: 'Verified',        defaultExpanded: false,
        hint: 'Marked done by you, or GitHub issue closed as completed.' },
      { key: 'archived',        label: 'Archived',        defaultExpanded: false,
        hint: 'Dismissed / not planned — kept for context, not actionable.' },
    ];
    // Apply user-defined column order from localStorage.
    let savedOrder = [];
    try { savedOrder = JSON.parse(localStorage.getItem('ccc-column-order') || '[]'); } catch (_) {}
    const byKey = Object.fromEntries(defaultColumns.map(c => [c.key, c]));
    const ordered = [];
    for (const k of savedOrder) if (byKey[k]) ordered.push(byKey[k]);
    for (const c of defaultColumns) if (!ordered.includes(c)) ordered.push(c);
    const columns = ordered;
    // Group sessions into columns
    const groups = {};
    for (const col of columns) groups[col.key] = [];
    for (const c of convs) {
      const col = classifyKanbanColumn(c);
      if (col && groups[col]) groups[col].push(c);
    }
    // Within "Working": actively-writing sessions sort to the top so glow
    // animations stay visible. Idle (resumable, no commits) cards otherwise
    // mix with live work in natural order — the blue Idle pill is enough
    // visual distinction; sinking them to the bottom would hide them behind
    // the column's maxVisible cap when Working is busy.
    if (groups.working) {
      groups.working.sort((a, b) => {
        const aActive = a.is_live && a.sidecar_status === 'active' && a.sidecar_has_writes ? 1 : 0;
        const bActive = b.is_live && b.sidecar_status === 'active' && b.sidecar_has_writes ? 1 : 0;
        return bActive - aActive;
      });
    }
    let html = '';
    for (const col of columns) {
      const items = groups[col.key];
      const isCollapsed = kanbanCollapsed.hasOwnProperty(col.key)
        ? kanbanCollapsed[col.key]
        : !col.defaultExpanded;
      const showAll = kanbanShowAll[col.key] || false;
      const maxVisible = 20;
      const visibleItems = (!showAll && items.length > maxVisible) ? items.slice(0, maxVisible) : items;
      const hasMore = !showAll && items.length > maxVisible;

      html += '<div class="kanban-column ' + col.key + (isCollapsed ? ' collapsed' : '') + '" data-col="' + col.key + '">';
      const colTitle = col.hint ? (col.label + ' — ' + col.hint) : col.label;
      html += '<div class="kanban-column-header' + (isCollapsed ? ' collapsed' : '') + '" data-col="' + col.key + '" draggable="true" title="' + escapeHtml(colTitle) + '">';
      html += '<span class="arrow">' + (isCollapsed ? '&#9656;' : '&#9662;') + '</span>';
      html += '<span>' + escapeHtml(col.label) + '</span>';
      html += '<span class="count">' + items.length + '</span>';
      html += '</div>';
      html += '<div class="kanban-cards">';
      // Empty-state placeholder — gives the user something to read instead of
      // a silent gap. Matches the morning kanban's pattern.
      if (items.length === 0) {
        const emptyMsg = {
          'backlog': 'No open issues or TODO items.',
          'needs-attention': 'Nothing flagged for triage.',
          'icebox': 'Nothing parked. Drag a card here, or label its issue `icebox`, to park it.',
          'working': 'No live or idle sessions.',
          'waiting': 'No sessions waiting for approval.',
          'review': 'Nothing waiting for review.',
          'testing': 'Drag cards here to mark as testing.',
          'verified': 'No verified work yet.',
          'archived': 'No archived items.',
        }[col.key] || 'Empty.';
        html += '<div class="kanban-column-empty" style="padding:14px 12px;color:var(--text-muted);font-size:11px;font-style:italic;text-align:center;opacity:0.6;">' + escapeHtml(emptyMsg) + '</div>';
      }
      // In Backlog only: group by org (if org tags are configured).
      // Sort by org so same-org cards cluster; emit a small sub-header on change.
      let sortedItems = visibleItems;
      const orgsOrder = APP_CONFIG.orgs || [];
      if (col.key === 'backlog' && orgsOrder.length > 0) {
        const rank = (c) => {
          const o = c.org || '';
          const idx = orgsOrder.indexOf(o);
          // Known orgs keep their configured order; unknowns sink to the bottom.
          return idx === -1 ? (orgsOrder.length + 1) : idx;
        };
        sortedItems = visibleItems.slice().sort((a, b) => rank(a) - rank(b));
      }
      let lastOrg = undefined;
      for (const c of sortedItems) {
        if (col.key === 'backlog' && orgsOrder.length > 0) {
          const o = c.org || null;
          if (o !== lastOrg) {
            const label = o || 'Project';
            html += '<div class="kanban-org-header">' + escapeHtml(label) + '</div>';
            lastOrg = o;
          }
        }
        // Prefer first_message for the title unless the user explicitly renamed it.
        // Auto-generated display_names (from claude /rename) tend to be awkwardly truncated.
        // For backlog cards, `first_message` is the issue body (which often
        // starts with a markdown heading like "## Feature request"), so using
        // it as a title fallback produces garbage. Prefer display_name there.
        const isBacklog = c.source === 'backlog';
        const rawFirst = (!isBacklog && c.first_message)
          ? firstSentenceOf(cleanIssuePrompt(c.first_message), 90)
          : '';
        let rawTitle = c.name_overridden
          ? c.display_name
          : (isBacklog
              ? (c.display_name || rawFirst || '(untitled)')
              : (rawFirst || c.display_name || '(untitled)'));
        if (c.backlog_type === 'github' || c.issue_number || c.linked_issue) {
          rawTitle = stripGhIssueProjectTag(rawTitle);
        }
        // Replace dashes with spaces for display (storage keeps the slug); strip user-configured title prefixes
        const title = stripTitle(rawTitle).replace(/-/g, ' ').trim();
        // Description: prefer the last assistant "outcome" message (so cards
        // show progress/result without opening them). Fall back to the original
        // ask if Claude hasn't responded yet.
        // descHtml is already escaped+rendered; descPlain is for the fallback path.
        let descHtml = '';
        if (c.last_assistant_text) {
          const raw = String(c.last_assistant_text).trim().slice(0, 700);
          descHtml = renderInlineMd(raw);
        } else if (c.first_message) {
          const fm = cleanIssuePrompt(stripTitle(c.first_message)).trim();
          const stripEllipsis = (s) => s.replace(/[.\u2026\s]+$/, '');
          const norm = (s) => stripEllipsis(s).toLowerCase().replace(/\s+/g, ' ').trim();
          const titleNorm = norm(title);
          const fmNorm = norm(fm).slice(0, titleNorm.length);
          let plain = '';
          if (titleNorm && fmNorm === titleNorm) {
            const cleanTitleLen = stripEllipsis(title).length;
            const rest = fm.slice(cleanTitleLen).replace(/^[\s\-–—:,.]+/, '');
            if (rest.length > 10) plain = rest.slice(0, 320);
          } else {
            plain = fm.slice(0, 320);
          }
          if (plain) descHtml = escapeHtml(plain);
        }

        // ── Backlog cards: special rendering ──
        if (c.source === 'backlog') {
          let stateBadge = '';
          if (c.backlog_type === 'github' && c.issue_state) {
            const isClosed = (c.issue_state || '').toUpperCase() === 'CLOSED';
            const reason = (c.issue_state_reason || '').toLowerCase().replace(/_/g, ' ');
            const label = isClosed ? ('closed' + (reason ? ' · ' + reason : '')) : 'open';
            const color = isClosed ? 'var(--red)' : 'var(--green)';
            const bg = isClosed ? 'rgba(248,81,73,0.12)' : 'rgba(63,185,80,0.12)';
            stateBadge = '<span title="GitHub state — if CLOSED appears in GH Issues, the cache is stale" style="font-size:10px;padding:1px 5px;border-radius:3px;background:' + bg + ';color:' + color + ';font-weight:600;margin-right:3px;text-transform:uppercase;">' + escapeHtml(label) + '</span>';
          }
          const labels = (c.issue_labels || []).filter(function(l) { return l !== 'bug'; }).map(function(l) {
            const isAttn = l === 'needs-attention';
            const color = isAttn ? 'var(--red)' : 'var(--accent)';
            const bg = isAttn ? 'rgba(248,81,73,0.15)' : 'rgba(139,148,158,0.15)';
            return '<span style="font-size:10px;padding:1px 5px;border-radius:3px;background:' + bg + ';color:' + color + ';font-weight:600;margin-right:3px;">' + escapeHtml(l) + '</span>';
          }).join('');
          // Source / origin chip (PARKING_LOT, TodoWrite task, TODO.md). For
          // GitHub backlog items, the source is conveyed by the GH #NNN chip
          // below — no separate sourceTag needed.
          let sourceTag = '';
          if (c.backlog_type === 'parking') {
            sourceTag = '<span style="font-size:10px;padding:1px 6px;border-radius:3px;background:rgba(57,210,192,0.15);color:var(--cyan);font-weight:600;">PARKING_LOT.md</span>';
          } else if (c.backlog_type === 'native_task') {
            const total = c.task_total || 0;
            const done = c.task_completed || 0;
            const ip = c.task_in_progress || 0;
            const countStr = total ? (' &middot; ' + done + '/' + total + ' done' + (ip ? ' &middot; ' + ip + ' in progress' : '')) : '';
            sourceTag = '<span title="From ~/.claude/tasks/ (TodoWrite)" style="font-size:10px;padding:1px 6px;border-radius:3px;background:rgba(188,140,255,0.15);color:var(--purple);font-weight:600;">&#128203; task' + countStr + '</span>';
          } else if (c.backlog_type !== 'github') {
            sourceTag = '<span style="font-size:10px;padding:1px 6px;border-radius:3px;background:rgba(88,166,255,0.15);color:var(--accent);font-weight:600;">TODO.md</span>';
          }
          // GH issue chip — clickable, opens the issue on GitHub. Same visual
          // language as session cards so backlog/working/review/verified all
          // read the same.
          let backlogIssueBadge = '';
          if (c.backlog_type === 'github' && c.issue_number) {
            backlogIssueBadge = '<span class="kanban-issue-badge" data-action="view-issue" data-issue="'
              + escapeHtml(c.issue_number)
              + '" title="Open issue #' + escapeHtml(c.issue_number) + ' on GitHub">GH #'
              + escapeHtml(c.issue_number) + '</span>';
          }
          // Strip the leading "#NNN" / "#NNN:" / "#NNN -" from the title when
          // the issue chip already carries that information.
          let renderBacklogTitle = title;
          if (c.backlog_type === 'github' && c.issue_number) {
            const stripRe = new RegExp('^#?' + c.issue_number + '\\s*[:\\-—]?\\s*', 'i');
            renderBacklogTitle = title.replace(stripRe, '');
            renderBacklogTitle = stripGhIssueProjectTag(renderBacklogTitle);
          }
          // Date + relative time
          let dateInfo = '';
          if (c.modified && c.modified > 0) {
            const d = new Date(c.modified * 1000);
            const abs = d.toLocaleDateString(undefined, {month:'short',day:'numeric'}) + ' ' +
                        d.toLocaleTimeString(undefined, {hour:'2-digit',minute:'2-digit'});
            dateInfo = '<span style="font-size:10px;color:var(--text-muted);">' +
                       escapeHtml(abs) + ' &middot; ' + escapeHtml(relativeTime(c.modified)) + '</span>';
          }
          html += '<div class="kanban-card backlog-card" draggable="true" data-id="' + c.id + '" data-session-id="' + escapeHtml(c.session_id || c.id) + '" data-col="backlog">';
          // Unified badges row — matches session-card layout: GH chip, state, labels, source.
          const badgeRow = backlogIssueBadge + stateBadge + labels + sourceTag;
          if (badgeRow) html += '<div class="kanban-card-badges">' + badgeRow + '</div>';
          html += '<div class="kanban-card-title">' + escapeHtml(renderBacklogTitle) + '</div>';
          if (dateInfo) html += '<div style="margin-top:6px;">' + dateInfo + '</div>';
          html += '<div style="display:flex;gap:4px;margin-top:6px;">';
          html += '<button class="kanban-start-btn" data-issue="' + escapeAttr(c.issue_number || '') + '" data-title="' + escapeAttr(c.display_name || c.first_message || '') + '" style="flex:1;padding:4px 10px;border-radius:4px;border:1px solid rgba(63,185,80,0.3);background:rgba(63,185,80,0.1);color:var(--green);font-size:11px;font-weight:600;cursor:pointer;">Start session</button>';
          html += '<button class="kanban-start-edit-btn" data-issue="' + escapeAttr(c.issue_number || '') + '" data-title="' + escapeAttr(c.display_name || c.first_message || '') + '" data-body="' + escapeAttr(c.first_message || '') + '" title="Edit the prompt before launching" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:var(--bg);color:var(--text-muted);font-size:11px;cursor:pointer;">Edit &amp; start</button>';
          // Per-card AI-summarize ✨ — only renders when this card hasn't been
          // summarized yet, so users can scan visually for cards that still
          // need a better title and click them one by one.
          if (!c.name_overridden && c.backlog_type === 'github' && c.issue_number) {
            html += '<button class="kanban-summarize-issue-btn" data-issue="' + escapeHtml(c.issue_number) + '" title="Generate a concise AI title for this issue" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--purple);font-size:14px;cursor:pointer;line-height:1;">&#10024;</button>';
          }
          // Give-up button — archives the backlog card (closes GH issue "not planned"
          // for github-type; client-side archive for todo/parking).
          html += '<button class="kanban-backlog-archive-btn" data-id="' + c.id + '" title="Archive / dismiss this item" style="padding:4px 8px;border-radius:4px;border:1px solid var(--border);background:transparent;color:var(--text-muted);font-size:14px;cursor:pointer;line-height:1;">&times;</button>';
          html += '</div>';
          html += '</div>';
          continue;
        }

        let titleClass = '';
        if (c.name_overridden) titleClass = ' user-renamed';
        const rel = relativeTime(c.modified);
        const sizeStr = formatSize(c.size);
        const branch = c.branch || '';
        // File size (sizeStr) dropped — not useful at a glance. Relative time +
        // branch are enough for scanning cards.
        const metaParts = ['<span class="meta-rel">' + escapeHtml(rel) + '</span>'];
        if (branch) metaParts.push('<span class="meta-branch">' + escapeHtml(branch) + '</span>');
        const colKey = classifyKanbanColumn(c);
        let stageLabel, stageCls;
        // Column-derived stage overrides the raw session stage
        if (colKey === 'testing') {
          stageLabel = 'testing'; stageCls = 'testing';
        } else if (colKey === 'verified') {
          stageLabel = 'verified'; stageCls = 'committed';
        } else if (colKey === 'archived') {
          stageLabel = 'archived'; stageCls = 'stage';
        } else if (c.source === 'pkood') {
          const ps = (c.pkood_status || '').toUpperCase();
          stageLabel = ps.toLowerCase() || 'unknown';
          stageCls = 'stage';
        } else {
          stageLabel = sessionStage(c);
          stageCls = stageClass(stageLabel);
        }
        const active = currentConversation === c.id ? ' active' : '';
        const archiveIcon = c.archived ? '&#8617;' : '&#128229;';
        const archiveTitle = c.archived ? 'Unarchive' : 'Archive';
        // "Truly active" = live + sidecar shows active work (glow animation)
        // Glow if sidecar was updated within last 5 min OR the session's last event
        // was an assistant response (Claude is mid-turn, possibly thinking without tools).
        const sidecarAge = c.sidecar_ts ? (Date.now() / 1000 - c.sidecar_ts) : 9999;
        const midTurn = c.last_event_type === 'assistant';
        const _isKanbanCodex = c.source === 'codex' || c.engine === 'codex';
        const _isKanbanGemini = c.source === 'gemini' || c.engine === 'gemini';
        const _kanbanActivityAge = c.sidecar_ts ? (Date.now() / 1000 - c.sidecar_ts) : (c.last_interacted ? (Date.now() / 1000 - c.last_interacted) : 9999);
        const _codexKanbanWip = _isKanbanCodex && !c.sidecar_status && (!!c.pending_tool || ((c.last_event_type === 'user' || c.last_event_type === 'assistant') && _kanbanActivityAge < 30 * 60));
        const _geminiKanbanWip = _isKanbanGemini && (c.last_event_type === 'user' || c.last_event_type === 'assistant') && _kanbanActivityAge < 30 * 60;
        const trulyActive = (c.is_live && c.sidecar_status === 'active' && (sidecarAge < 300 || midTurn)) || _codexKanbanWip || _geminiKanbanWip ? ' truly-active' : '';
        const pendingSpawn = c.pending_spawn ? ' pending-spawn' : '';
        const recentlyBorn = isRecentlyBorn(c.session_id) ? ' recently-born' : '';
        const noEditsAttr = hasNoEdits(c) ? ' no-edits' : '';
        const readOnlyAttr = hasReadOnlyWork(c) ? ' read-only' : '';
        html += '<div class="kanban-card' + active + trulyActive + pendingSpawn + recentlyBorn + noEditsAttr + readOnlyAttr + '" draggable="true" data-id="' + c.id + '" data-session-id="' + escapeHtml(c.session_id || c.id) + '" data-col="' + colKey + '">';
        // Create-issue button only when no issue is linked. The "view issue" link
        // was removed — tapping the #NNN badge in the title opens the issue.
        const linkedIssue = c.linked_issue || c.issue_number || '';
        const createIssueBtn = linkedIssue
          ? ''
          : '<button class="kanban-action-btn" data-action="create-issue" title="Create GitHub issue">&#128221;</button>';
        // Persistent per-card ✨: show on every card that has a first_message
        // (both un-summarized AND user-renamed). A user who rename-regretted
        // otherwise has no way back to an AI title short of clearing the
        // override by hand. On renamed cards we dim it + swap the tooltip so
        // the destructive intent (it replaces the manual rename) is obvious.
        let summarizeBtn = '';
        if (c.first_message) {
          if (c.name_overridden) {
            summarizeBtn = '<button class="kanban-action-btn" data-action="summarize" title="Regenerate title — replaces your manual rename" style="opacity:0.5;">&#10024;</button>';
          } else {
            summarizeBtn = '<button class="kanban-action-btn" data-action="summarize" title="Generate AI title for this card">&#10024;</button>';
          }
        }
        html += '<div class="kanban-card-actions">'
          + summarizeBtn
          + createIssueBtn
          + '<button class="kanban-action-btn" data-action="archive" title="' + archiveTitle + '">' + archiveIcon + '</button>'
          + '</div>';
        let issueBadge = '';
        if (linkedIssue) {
          const ghState = (c.gh_state || '').toUpperCase();
          const ghLabelsArr = c.gh_labels || c.issue_labels || [];
          const hasNA = Array.isArray(ghLabelsArr) && ghLabelsArr.indexOf('needs-attention') >= 0;
          // Single state chip — open/closed only. The "WIP / claude-in-progress"
          // signal was removed for visual simplicity (the GH #NNN chip itself
          // is enough to show the issue is linked to an active session).
          let stateChip = '';
          if (ghState === 'CLOSED') {
            stateChip = '<span class="kanban-gh-state gh-closed" title="GitHub issue state: closed">closed</span>';
          } else if (ghState) {
            stateChip = '<span class="kanban-gh-state gh-open" title="GitHub issue state: open">open</span>';
          }
          // Surface a visible needs-attention chip so it's not buried in the
          // GH-label array. The kanban column routing may put the card in
          // Review based on session state — the chip tells the user at a
          // glance that the reporter flagged the issue.
          const naChip = hasNA
            ? '<span class="kanban-na-chip" title="Reporter flagged this issue with needs-attention">needs-attention</span>'
            : '';
          const desktopChip = c.desktop_app
            ? '<span class="kanban-desktop-badge" title="Session is tracked by the Claude desktop app">Desktop</span>'
            : '';
          issueBadge = '<span class="kanban-issue-badge" title="Linked to GitHub issue">GH #' + escapeHtml(linkedIssue) + '</span>' + stateChip + naChip + desktopChip;
        }
        // Title cleanup before render:
        //   - When the card already has a linked-issue chip ("GH #194"), strip
        //     a leading "#194" / "#194:" / "#194 -" from the title text to
        //     avoid showing the issue number twice.
        //   - When there's NO linked issue but the AI-generated title contains
        //     a #NNN reference, wrap it in a small green-text span so the user
        //     sees at a glance it's a GitHub issue reference, not random text.
        let renderTitle = title;
        if (linkedIssue) {
          const stripRe = new RegExp('^#?' + linkedIssue + '\\s*[:\\-\u2014]?\\s*', 'i');
          renderTitle = title.replace(stripRe, '');
        }
        let titleHtml = escapeHtml(renderTitle);
        if (!linkedIssue) {
          // Wrap any #NNN in the title with a styled span for clarity.
          titleHtml = titleHtml.replace(/#(\d{1,5})\b/g,
            '<span class="kanban-issue-ref" title="GitHub issue reference">GH&nbsp;#$1</span>');
        }
        // Notification hook badge — Claude Code is asking for permission
        // (or otherwise needs the user). Sits above the title so it's the
        // first thing the eye lands on when scanning the Waiting column.
        if (c.needs_approval) {
          const msg = c.needs_approval_message || '';
          const shortMsg = msg && msg.length <= 80 ? msg : '';
          html += '<div class="kanban-needs-approval-badge" title="' + escapeHtml(msg || 'Claude is asking for permission') + '">'
            + '<span class="kanban-needs-approval-icon">🔔</span>'
            + '<span class="kanban-needs-approval-label">Needs approval</span>'
            + (shortMsg ? '<span class="kanban-needs-approval-msg">' + escapeHtml(shortMsg) + '</span>' : '')
            + '</div>';
        }
        if (issueBadge) {
          html += '<div class="kanban-card-badges">' + issueBadge + '</div>';
        }
        html += '<div class="kanban-card-title' + titleClass + '" data-action="edit-title" title="Click to open; click again to rename">' + titleHtml + '</div>';
        if (descHtml) {
          html += '<div class="kanban-card-desc">' + descHtml + '</div>';
        }
        html += '<div class="kanban-card-meta">' + metaParts.join(' \u00b7 ') + '</div>';
        if (c.last_interacted) {
          html += '<div class="kanban-card-interacted" title="Last time you typed a message or clicked a card button">'
            + 'Last interacted ' + escapeHtml(relativeTime(c.last_interacted))
            + '</div>';
        }
        html += '<div class="kanban-card-stage ' + stageCls + '">' + escapeHtml(stageLabel) + '</div>';
        if (noEditsAttr) {
          html += '<span class="kanban-card-stage no-edits" title="Agent has not edited any files in this session">no edits</span>';
        } else if (readOnlyAttr) {
          html += '<span class="kanban-card-stage read-only" title="Agent completed read-only work without file edits">read-only</span>';
        }

        // ── Task 8: Approve/Deny for waiting cards with pending_tool ──
        // Live "what's running right now" \u2014 render whenever sidecar shows
        // active work and the data is fresh (<5 min). Closes the Working
        // column gap where the card showed nothing while Claude was busy.
        if (c.is_live && c.sidecar_status === 'active' && c.sidecar_tool) {
          const sidecarAge = c.sidecar_ts ? Math.max(0, Math.floor(Date.now() / 1000 - c.sidecar_ts)) : 9999;
          if (sidecarAge < 300) {
            const shortFile = c.sidecar_file
              ? (c.sidecar_file.length > 40 ? '\u2026' + c.sidecar_file.slice(-39) : c.sidecar_file)
              : '';
            // In-flight = the PreToolUse marker says this tool is *currently*
            // running, so "running 5s" is honest. Otherwise it's the most
            // recent completion, so "5s ago" is honest.
            const dur = sidecarAge < 2 ? '<1s' : sidecarAge < 60 ? sidecarAge + 's' : Math.floor(sidecarAge / 60) + 'm';
            const ageLbl = c.sidecar_in_flight ? 'running ' + dur : dur + ' ago';
            const arrow = c.sidecar_in_flight ? '\u25b6 ' : '';
            html += '<div class="kanban-live-tool' + (c.sidecar_in_flight ? ' in-flight' : '') + '" title="' + (c.sidecar_in_flight ? 'Currently running' : 'Last completed') + '">'
              + '<span class="kanban-live-name">' + arrow + escapeHtml(c.sidecar_tool) + '</span>'
              + (shortFile ? ' <span class="kanban-live-file">' + escapeHtml(shortFile) + '</span>' : '')
              + '<span class="kanban-live-age">' + ageLbl + '</span>'
              + '</div>';
          }
        }

        if (colKey === 'waiting' && c.pending_tool && !c.sidecar_status) {
          const shortFile = c.pending_file ? c.pending_file.split('/').pop() : '';
          html += '<div class="kanban-tool-info">\u23f3 ' + escapeHtml(c.pending_tool) + (shortFile ? ': ' + escapeHtml(shortFile) : '') + '</div>';
          html += '<div class="kanban-approve-deny">'
            + '<button class="kanban-approve" data-action="approve" title="Approve (send y)">Approve</button>'
            + '<button class="kanban-deny" data-action="deny" title="Deny (send n)">Deny</button>'
            + '</div>';
        }

        // ── Task 7: Inline input on waiting cards ──
        if (colKey === 'waiting') {
          html += '<div class="kanban-inline-input">'
            + '<input type="text" placeholder="Send to terminal..." data-action="inline-input" autocomplete="off">'
            + '<button data-action="inline-send">&gt;</button>'
            + '</div>';
        }

        // ── Task 9: Review card actions ──
        if (colKey === 'review') {
          let reviewInfo = '';
          if (c.has_push) reviewInfo = '\u2713 Pushed' + (c.branch ? ' (' + c.branch + ')' : '');
          else if (c.has_commit) reviewInfo = '\u2713 Committed';
          if (reviewInfo) html += '<div class="kanban-review-info">' + escapeHtml(reviewInfo) + '</div>';
          html += '<div class="kanban-review-actions">'
            + '<button class="kanban-archive-btn" data-action="review-archive" title="Archive without verifying">Archive</button>'
            + '</div>';
        }

        html += '</div>';
      }
      if (hasMore) {
        html += '<div class="kanban-show-all" data-col="' + col.key + '">Show all ' + items.length + ' sessions</div>';
      }
      html += '</div></div>';
    }
    targetEl.innerHTML = html;

    // Attach event handlers
    let _headerDragSuppressClick = false;
    targetEl.querySelectorAll('.kanban-column-header').forEach(hdr => {
      hdr.addEventListener('click', () => {
        if (_headerDragSuppressClick) { _headerDragSuppressClick = false; return; }
        const colKey = hdr.dataset.col;
        const col = hdr.closest('.kanban-column');
        const nowCollapsed = !col.classList.contains('collapsed');
        kanbanCollapsed[colKey] = nowCollapsed;
        try { localStorage.setItem('ccc-kanban-collapsed', JSON.stringify(kanbanCollapsed)); } catch (_) {}
        col.classList.toggle('collapsed', nowCollapsed);
        hdr.classList.toggle('collapsed', nowCollapsed);
        hdr.querySelector('.arrow').innerHTML = nowCollapsed ? '&#9656;' : '&#9662;';
      });
      // Header drag-to-reorder columns
      hdr.addEventListener('dragstart', (ev) => {
        ev.stopPropagation();
        ev.dataTransfer.effectAllowed = 'move';
        ev.dataTransfer.setData('application/x-column-drag', hdr.dataset.col);
        hdr.classList.add('dragging-header');
      });
      hdr.addEventListener('dragover', (ev) => {
        const types = ev.dataTransfer.types;
        if (!types || !Array.from(types).includes('application/x-column-drag')) return;
        ev.preventDefault();
        ev.stopPropagation();
        ev.dataTransfer.dropEffect = 'move';
        hdr.classList.add('drop-target-header');
      });
      hdr.addEventListener('dragleave', () => hdr.classList.remove('drop-target-header'));
      hdr.addEventListener('drop', (ev) => {
        const src = ev.dataTransfer.getData('application/x-column-drag');
        hdr.classList.remove('drop-target-header');
        if (!src) return;
        ev.preventDefault();
        ev.stopPropagation();
        const dst = hdr.dataset.col;
        if (src === dst) return;
        _headerDragSuppressClick = true;
        // Build current order from DOM, move src before dst.
        const current = Array.from(targetEl.querySelectorAll(':scope > .kanban-column')).map(c => c.dataset.col);
        const srcIdx = current.indexOf(src);
        if (srcIdx >= 0) current.splice(srcIdx, 1);
        const dstIdx = current.indexOf(dst);
        current.splice(dstIdx, 0, src);
        try { localStorage.setItem('ccc-column-order', JSON.stringify(current)); } catch (_) {}
        renderSidebar(filterConversations($convSearch.value));
      });
      hdr.addEventListener('dragend', () => hdr.classList.remove('dragging-header'));
    });
    targetEl.querySelectorAll('.kanban-card').forEach(card => {
      card.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-action]')) return;
        // Ctrl/Cmd/Shift click: toggle multi-select instead of opening
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey) {
          ev.preventDefault();
          if (selectedCardIds.has(card.dataset.id)) {
            selectedCardIds.delete(card.dataset.id);
            card.classList.remove('selected');
          } else {
            selectedCardIds.add(card.dataset.id);
            card.classList.add('selected');
          }
          return;
        }
        // Clear multi-selection on normal click
        if (selectedCardIds.size > 0) {
          targetEl.querySelectorAll('.kanban-card.selected').forEach(el => el.classList.remove('selected'));
          selectedCardIds.clear();
        }
        // Re-open conv panel if closed
        if (!convPanelOpen && $cpCloseBtn) $cpCloseBtn.click();
        // Backlog GitHub issue cards: render full issue in the conv pane
        if (card.classList.contains('backlog-card') && card.dataset.id.startsWith('backlog-issue-')) {
          const issueNum = card.dataset.id.replace('backlog-issue-', '');
          const row = conversationsData.find(x => x.id === card.dataset.id);
          renderIssueInConvPane(issueNum, rowRepoPath(row), card.dataset.id);
          return;
        }
        // Backlog TODO.md / PARKING_LOT.md cards: no conversation yet — show the text.
        if (card.classList.contains('backlog-card') && (
              card.dataset.id.startsWith('backlog-todo-') ||
              card.dataset.id.startsWith('backlog-parking-'))) {
          renderTodoInConvPane(card.dataset.id);
          return;
        }
        selectConversation(card.dataset.id);
      });
      // Restore selected state after re-render
      if (selectedCardIds.has(card.dataset.id)) card.classList.add('selected');
      // Drag-and-drop between columns
      card.addEventListener('dragstart', (ev) => {
        // If this card is part of a multi-select, include all selected IDs
        const ids = selectedCardIds.has(card.dataset.id)
          ? Array.from(selectedCardIds)
          : [card.dataset.id];
        ev.dataTransfer.setData('text/plain', ids.join(','));
        ev.dataTransfer.effectAllowed = 'move';
        card.classList.add('dragging');
        // Mark all selected cards as dragging too
        if (ids.length > 1) {
          targetEl.querySelectorAll('.kanban-card.selected').forEach(el => el.classList.add('dragging'));
        }
      });
      card.addEventListener('dragend', () => {
        targetEl.querySelectorAll('.kanban-card.dragging').forEach(el => el.classList.remove('dragging'));
      });
      // Cards are not drop targets — columns are the only drop targets.
      // (Drags fall through the card to its parent column.)
    });
    // Column drop targets — see moveCardToColumn below
    targetEl.querySelectorAll('.kanban-column').forEach(col => {
      col.addEventListener('dragover', (ev) => {
        ev.preventDefault();
        ev.dataTransfer.dropEffect = 'move';
        col.classList.add('drag-over');
      });
      col.addEventListener('dragleave', (ev) => {
        // Only remove if leaving the column entirely
        if (!col.contains(ev.relatedTarget)) col.classList.remove('drag-over');
      });
      col.addEventListener('drop', async (ev) => {
        ev.preventDefault();
        col.classList.remove('drag-over');
        const ids = (ev.dataTransfer.getData('text/plain') || '').split(',').filter(Boolean);
        const targetCol = col.dataset.col;
        if (ids.length > 1) {
          await moveCardsToColumn(ids, targetCol);
        } else if (ids.length === 1) {
          await moveCardToColumn(ids[0], targetCol);
        }
        targetEl.querySelectorAll('.kanban-card.selected').forEach(el => el.classList.remove('selected'));
        selectedCardIds.clear();
      });
    });
    // Rename action. Title clicks are two-step: inactive card titles open
    // the conversation first; clicking the active title again starts rename.
    targetEl.querySelectorAll('[data-action="edit-title"]').forEach(el => {
      el.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const card = el.closest('.kanban-card');
        if (!card) return;
        const alreadyActive = card.classList.contains('active') || currentConversation === card.dataset.id;
        if (!convPanelOpen && $cpCloseBtn) $cpCloseBtn.click();
        if (!alreadyActive) {
          selectConversation(card.dataset.id);
          return;
        }
        const editBtn = card.querySelector('[data-action="edit"]');
        if (editBtn) editBtn.click();
      });
    });
    // View linked GitHub issue
    targetEl.querySelectorAll('[data-action="view-issue"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const issueNum = btn.dataset.issue;
        if (!issueNum) return;
        const card = btn.closest('.kanban-card');
        const row = card ? conversationsData.find(x => x.id === card.dataset.id) : null;
        renderIssueInConvPane(issueNum, rowRepoPath(row) || repoPathForIssueNumber(issueNum));
      });
    });
    // Create new GitHub issue for this session
    targetEl.querySelectorAll('[data-action="create-issue"]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        const c = conversationsData.find(x => x.id === card.dataset.id);
        if (!c) return;
        if (!confirm('Create a GitHub issue for this session?')) return;
        btn.disabled = true;
        const origHtml = btn.innerHTML;
        btn.innerHTML = '\u2026';
        try {
          const res = await fetch('/api/conversations/' + card.dataset.id + '/create-issue', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              session_id: c.session_id,
              display_name: c.display_name,
              first_message: c.first_message,
              last_prompt: c.last_prompt,
              branch: c.branch,
            }),
          });
          const data = await res.json();
          if (data.ok && data.issue_number) {
            c.linked_issue = data.issue_number;
            renderSidebar(filterConversations($convSearch.value));
          } else {
            showOpToast('Failed: ' + (data.error || 'unknown'), 'error');
            btn.disabled = false;
            btn.innerHTML = origHtml;
          }
        } catch (err) {
          showOpToast('Error: ' + err.message, 'error');
          btn.disabled = false;
          btn.innerHTML = origHtml;
        }
      });
    });
    targetEl.querySelectorAll('[data-action="edit"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        const titleEl = card.querySelector('.kanban-card-title');
        if (!titleEl || card.querySelector('.kanban-rename-input')) return;
        // Use the real stored display_name (with dashes), not the display-only version
        const c = conversationsData.find(x => x.id === card.dataset.id);
        const currentText = (c && c.display_name) || titleEl.textContent;
        const input = document.createElement('textarea');
        input.className = 'kanban-rename-input';
        input.value = currentText;
        input.rows = 2;
        input.style.cssText = 'width:100%;font-size:14px;font-weight:600;padding:6px 8px;border-radius:4px;border:1px solid var(--accent);background:var(--bg);color:var(--text);font-family:inherit;outline:none;resize:vertical;min-height:40px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word;';
        titleEl.replaceWith(input);
        // Auto-size to content
        input.style.height = 'auto';
        input.style.height = Math.max(40, input.scrollHeight) + 'px';
        input.focus(); input.select();
        input.addEventListener('input', () => {
          input.style.height = 'auto';
          input.style.height = Math.max(40, input.scrollHeight) + 'px';
        });
        let done = false;
        async function commit(save) {
          if (done) return; done = true;
          if (save) {
            const newName = input.value.trim();
            // Only call rename API if the name actually changed
            if (newName !== currentText) {
              try {
                await fetch('/api/conversations/' + card.dataset.id + '/rename', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({ session_id: card.dataset.sessionId, name: newName }),
                });
                const c = conversationsData.find(x => x.id === card.dataset.id);
                if (c) { c.display_name = newName || null; c.name_overridden = !!newName; }
              } catch (_) {}
            }
          }
          renderSidebar(filterConversations($convSearch.value));
        }
        input.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commit(true); }
          else if (e.key === 'Escape') { e.preventDefault(); commit(false); }
        });
        input.addEventListener('blur', () => commit(true));
      });
    });
    // Summarize-with-Claude action
    targetEl.querySelectorAll('[data-action="summarize"]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        const origText = btn.textContent;
        btn.disabled = true;
        btn.textContent = '⏳';
        try {
          const res = await fetch('/api/conversations/' + card.dataset.id + '/summarize', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ session_id: card.dataset.sessionId }),
          });
          const data = await res.json();
          if (data.ok && data.title) {
            const c = conversationsData.find(x => x.id === card.dataset.id);
            if (c) { c.display_name = data.title; c.name_overridden = true; }
            renderSidebar(filterConversations($convSearch.value));
          } else {
            btn.title = data.error || 'summarize failed';
            btn.textContent = '⚠';
            setTimeout(() => { btn.textContent = origText; btn.title = 'Regenerate title with Claude'; }, 2500);
          }
        } catch (e) {
          btn.title = String(e && e.message || e);
          btn.textContent = '⚠';
          setTimeout(() => { btn.textContent = origText; btn.title = 'Regenerate title with Claude'; }, 2500);
        } finally {
          btn.disabled = false;
        }
      });
    });
    // Archive action
    targetEl.querySelectorAll('[data-action="archive"]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        try {
          const data = await ccPostJson('/api/conversations/' + card.dataset.id + '/archive',
            { session_id: card.dataset.sessionId });
          const c = conversationsData.find(x => x.id === card.dataset.id);
          if (c) {
            c.archived = !!data.archived;
            setOptimisticOverride(c.session_id, { archived: c.archived });
          }
          renderSidebar(filterConversations($convSearch.value));
        } catch (err) {
          showOpToast('Archive failed (' + err.message + ')', 'error');
        }
      });
    });
    targetEl.querySelectorAll('.kanban-show-all').forEach(link => {
      link.addEventListener('click', (ev) => {
        ev.stopPropagation();
        kanbanShowAll[link.dataset.col] = true;
        renderKanbanBoard(convs, targetEl, isSplit);
      });
    });

    // ── Background drag: pan/scroll (default) or marquee multi-select (with modifier) ──
    // Plain drag on empty area = horizontal+vertical pan.
    // Shift/Cmd/Ctrl + drag = rubber-band multi-select.
    if (!targetEl._marqueeInstalled) {
      targetEl._marqueeInstalled = true;
      let marquee = null;
      let startX = 0, startY = 0;
      let isMarquee = false;
      let isPanning = false;
      let panStartX = 0, panStartY = 0;
      let panScrollLeft = 0, panScrollTop = 0;

      targetEl.addEventListener('mousedown', (ev) => {
        if (ev.target.closest('.kanban-card') || ev.target.closest('button') ||
            ev.target.closest('input') || ev.target.closest('textarea') ||
            ev.target.closest('.kanban-column-header')) return;
        if (ev.button !== 0) return;

        // Smart default:
        //   - Shift/Cmd/Ctrl held → marquee (the user clearly wants to extend selection)
        //   - Alt held → always pan
        //   - Plain drag close to a card → marquee (multi-select the cluster)
        //   - Plain drag in truly empty space → pan/scroll the board horizontally
        const PROXIMITY_PX = 50;
        let closeToCard = false;
        const cards = targetEl.querySelectorAll('.kanban-card');
        for (const card of cards) {
          const r = card.getBoundingClientRect();
          if (ev.clientX >= r.left - PROXIMITY_PX && ev.clientX <= r.right + PROXIMITY_PX &&
              ev.clientY >= r.top  - PROXIMITY_PX && ev.clientY <= r.bottom + PROXIMITY_PX) {
            closeToCard = true;
            break;
          }
        }
        const forceMarquee = ev.metaKey || ev.ctrlKey || ev.shiftKey;
        const wantPan = !forceMarquee && (ev.altKey || !closeToCard);
        if (!wantPan) {
          isMarquee = true;
          const rect = targetEl.getBoundingClientRect();
          startX = ev.clientX - rect.left + targetEl.scrollLeft;
          startY = ev.clientY - rect.top + targetEl.scrollTop;
          marquee = document.createElement('div');
          marquee.className = 'kanban-marquee';
          marquee.style.cssText = 'position:absolute;border:1px dashed var(--accent);background:rgba(88,166,255,0.08);pointer-events:none;z-index:1000;';
          targetEl.style.position = 'relative';
          targetEl.appendChild(marquee);
          if (!ev.metaKey && !ev.ctrlKey && !ev.shiftKey) {
            targetEl.querySelectorAll('.kanban-card.selected').forEach(el => el.classList.remove('selected'));
            selectedCardIds.clear();
          }
        } else {
          isPanning = true;
          panStartX = ev.clientX;
          panStartY = ev.clientY;
          panScrollLeft = targetEl.scrollLeft;
          panScrollTop = targetEl.scrollTop;
          targetEl.style.cursor = 'grabbing';
          targetEl.style.userSelect = 'none';
          ev.preventDefault();
        }
      });

      document.addEventListener('mousemove', (ev) => {
        if (isPanning) {
          targetEl.scrollLeft = panScrollLeft - (ev.clientX - panStartX);
          targetEl.scrollTop = panScrollTop - (ev.clientY - panStartY);
          return;
        }
        if (!isMarquee || !marquee) return;
        const rect = targetEl.getBoundingClientRect();
        const curX = ev.clientX - rect.left + targetEl.scrollLeft;
        const curY = ev.clientY - rect.top + targetEl.scrollTop;
        const left = Math.min(startX, curX);
        const top = Math.min(startY, curY);
        const width = Math.abs(curX - startX);
        const height = Math.abs(curY - startY);
        marquee.style.left = left + 'px';
        marquee.style.top = top + 'px';
        marquee.style.width = width + 'px';
        marquee.style.height = height + 'px';
        targetEl.querySelectorAll('.kanban-card').forEach(card => {
          const cr = card.getBoundingClientRect();
          const cardLeft = cr.left - rect.left + targetEl.scrollLeft;
          const cardTop = cr.top - rect.top + targetEl.scrollTop;
          const cardRight = cardLeft + cr.width;
          const cardBottom = cardTop + cr.height;
          const intersects = !(cardRight < left || cardLeft > left + width ||
                               cardBottom < top || cardTop > top + height);
          if (intersects) {
            card.classList.add('selected');
            selectedCardIds.add(card.dataset.id);
          }
        });
      });

      document.addEventListener('mouseup', () => {
        if (isPanning) {
          isPanning = false;
          targetEl.style.cursor = '';
          targetEl.style.userSelect = '';
        }
        if (!isMarquee) return;
        isMarquee = false;
        if (marquee) { marquee.remove(); marquee = null; }
      });

      // ── Auto-scroll horizontally during drag near edges ──
      let autoScrollRaf = null;
      let lastPointerX = 0;

      targetEl.addEventListener('dragover', (ev) => {
        lastPointerX = ev.clientX;
        if (autoScrollRaf) return;
        const tick = () => {
          const rect = targetEl.getBoundingClientRect();
          const EDGE = 80;
          const SPEED = 15;
          if (lastPointerX > rect.right - EDGE) {
            targetEl.scrollLeft += SPEED;
          } else if (lastPointerX < rect.left + EDGE) {
            targetEl.scrollLeft -= SPEED;
          }
          autoScrollRaf = requestAnimationFrame(tick);
        };
        autoScrollRaf = requestAnimationFrame(tick);
      });

      targetEl.addEventListener('dragleave', (ev) => {
        // Cancel auto-scroll when pointer leaves the container entirely
        if (!targetEl.contains(ev.relatedTarget)) {
          if (autoScrollRaf) { cancelAnimationFrame(autoScrollRaf); autoScrollRaf = null; }
        }
      });
      targetEl.addEventListener('drop', () => {
        if (autoScrollRaf) { cancelAnimationFrame(autoScrollRaf); autoScrollRaf = null; }
      });
      document.addEventListener('dragend', () => {
        if (autoScrollRaf) { cancelAnimationFrame(autoScrollRaf); autoScrollRaf = null; }
      });
    }

    // ── Task 7: Inline input send handler ──
    targetEl.querySelectorAll('[data-action="inline-send"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        const inp = card.querySelector('[data-action="inline-input"]');
        const text = (inp && inp.value || '').trim();
        if (!text) return;
        const sid = card.dataset.sessionId;
        injectToSession(sid, text, btn, inp);
      });
    });
    targetEl.querySelectorAll('[data-action="inline-input"]').forEach(inp => {
      inp.addEventListener('click', (ev) => ev.stopPropagation());
      inp.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') {
          ev.preventDefault(); ev.stopPropagation();
          const card = inp.closest('.kanban-card');
          const text = inp.value.trim();
          if (!text) return;
          const sid = card.dataset.sessionId;
          const btn = card.querySelector('[data-action="inline-send"]');
          injectToSession(sid, text, btn, inp);
        }
      });
    });

    // ── Task 8: Approve/Deny handlers ──
    targetEl.querySelectorAll('[data-action="approve"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        injectToSession(card.dataset.sessionId, 'y', btn);
      });
    });
    targetEl.querySelectorAll('[data-action="deny"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        injectToSession(card.dataset.sessionId, 'n', btn);
      });
    });

    // ── Task 9: Review archive + send-back handlers ──
    targetEl.querySelectorAll('[data-action="review-verify"]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        const conv = conversationsData.find(x => x.id === card.dataset.id) || {};
        try {
          const data = await ccPostJson('/api/conversations/' + card.dataset.id + '/verify',
            { session_id: card.dataset.sessionId, display_name: conv.display_name || '', cwd: conv.session_cwd || '' });
          const c = conversationsData.find(x => x.id === card.dataset.id);
          if (c) {
            c.verified = !!data.verified;
            setOptimisticOverride(c.session_id, { verified: c.verified });
          }
          renderSidebar(filterConversations($convSearch.value));
        } catch (err) {
          showOpToast('Verify failed (' + err.message + ')', 'error');
        }
      });
    });
    targetEl.querySelectorAll('[data-action="review-archive"]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        try {
          const data = await ccPostJson('/api/conversations/' + card.dataset.id + '/archive',
            { session_id: card.dataset.sessionId });
          const c = conversationsData.find(x => x.id === card.dataset.id);
          if (c) {
            c.archived = !!data.archived;
            setOptimisticOverride(c.session_id, { archived: c.archived });
          }
          renderSidebar(filterConversations($convSearch.value));
        } catch (err) {
          showOpToast('Archive failed (' + err.message + ')', 'error');
        }
      });
    });
    targetEl.querySelectorAll('[data-action="review-sendback"]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const card = btn.closest('.kanban-card');
        const sid = card.dataset.sessionId;
        const feedback = prompt('Enter feedback to send back:');
        if (!feedback) return;
        // Clear committed/pushed flags so card moves out of Review
        const c = conversationsData.find(x => x.id === card.dataset.id);
        if (c) {
          c.has_push = false;
          c.has_commit = false;
        }
        const isLive = c && c.is_live;
        if (!isLive) {
          btn.textContent = 'Launching...';
          try {
            await fetch('/api/launch-terminal', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ session_id: sid, cwd: c && c.session_cwd }),
            });
            // Wait for terminal to be ready
            await new Promise(r => setTimeout(r, 3000));
          } catch (_) {}
        }
        btn.textContent = 'Sending...';
        await injectToSession(sid, feedback, btn);
        // Re-render so card moves out of Review
        renderSidebar(filterConversations($convSearch ? $convSearch.value : ''));
      });
    });

    // ── Edit prompt before launching — opens the new-session modal pre-filled ──
    targetEl.querySelectorAll('.kanban-start-edit-btn').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const issueNum = btn.dataset.issue;
        const title = btn.dataset.title;
        const cleanTitle = (title || '').replace(/^#\d+:\s*/, '').replace(/\[[^\]]*\]\s*/g, '').trim();
        // GH issue titles truncate around ~94 chars, so always tell Claude to
        // read the full issue. Keep the short title as a hint.
        // (No SESSION_STATE_INSTRUCTION here — submitNewSessionModal appends it.)
        const card = btn.closest('.kanban-card');
        const conv = card ? conversationsData.find(x => x.id === card.dataset.id) : null;
        const body = issueNum
          ? 'Fix issue #' + issueNum + ' — ' + cleanTitle + '\n\nRun `gh issue view ' + issueNum + '` for the full body (title may be truncated).'
          : cleanTitle;
        openNewSessionModal(body, rowRepoPath(conv));
      });
    });
    // ── Per-card AI-summarize a GH backlog card ──
    targetEl.querySelectorAll('.kanban-summarize-issue-btn').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const issueNum = btn.dataset.issue;
        if (!issueNum) return;
        const card = btn.closest('.kanban-card');
        const conv = card ? conversationsData.find(x => x.id === card.dataset.id) : null;
        const repoPath = rowRepoPath(conv) || repoPathForIssueNumber(issueNum);
        const orig = btn.textContent;
        btn.disabled = true;
        btn.textContent = '…';
        try {
          const r = await fetch('/api/issues/' + encodeURIComponent(issueNum) + '/summarize-title', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(withRepoPath({}, repoPath)),
          });
          const d = await r.json();
          if (!d.ok) throw new Error(d.error || 'summarize failed');
          // Refresh so the new title renders. Button auto-hides because the
          // card now has name_overridden=true.
          refreshConversationList();
        } catch (e) {
          btn.disabled = false;
          btn.textContent = orig;
          showOpToast('Summarize failed: ' + (e.message || e), 'error');
        }
      });
    });
    // ── Give-up / archive a backlog card ──
    targetEl.querySelectorAll('.kanban-backlog-archive-btn').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const cardId = btn.dataset.id;
        if (!cardId) return;
        btn.disabled = true;
        btn.textContent = '…';
        // Optimistically hide the card in-place
        const card = btn.closest('.kanban-card');
        if (card) card.style.opacity = '0.45';
        try {
          // Reuse moveCardToColumn('archived') — correctly routes:
          //   - backlog-todo-* / backlog-parking-* → client-side archive
          //   - backlog-issue-* → server archive (closes GH issue "not planned")
          await moveCardToColumn(cardId, 'archived');
          setTimeout(refreshConversationList, 500);
        } catch (_) {
          btn.disabled = false;
          btn.textContent = '\u00d7';
          if (card) card.style.opacity = '';
        }
      });
    });
    // ── Start session from a backlog card ──
    targetEl.querySelectorAll('.kanban-start-btn').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const issueNum = btn.dataset.issue;
        const title = btn.dataset.title;
        const card = btn.closest('.kanban-card');
        const conv = card ? conversationsData.find(x => x.id === card.dataset.id) : null;
        const repoPath = rowRepoPath(conv) || repoPathForIssueNumber(issueNum);
        // Clean title for use as session name: strip #N: prefix, strip [bracket] tags, clean chars
        let cleanTitle = (title || '').replace(/^#\d+:\s*/, '').replace(/\[[^\]]*\]\s*/g, '').trim();
        const sessionName = issueNum ? ('issue-' + issueNum) : cleanTitle.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
        // See the Edit & start handler above — same rationale. Titles over
        // ~94 chars get truncated by GitHub, so always direct Claude to read
        // the full body via `gh issue view N`.
        const prompt = (issueNum
          ? 'Fix issue #' + issueNum + ' — ' + cleanTitle + '\n\nRun `gh issue view ' + issueNum + '` for the full body (title may be truncated).'
          : cleanTitle) + SESSION_STATE_INSTRUCTION;
        const spawnKey = issueNum ? (_issueStartKey(issueNum, repoPath) || ('issue-' + issueNum)) : sessionName;
        if (_spawningKeys.has(spawnKey)) return;  // already spawning — ignore duplicate tap
        _spawningKeys.add(spawnKey);
        btn.disabled = true;
        btn.textContent = 'Spawning…';
        // Visual "Spawning..." feedback on the backlog card — we KEEP it in the
        // DOM so the user sees the animation. _spawningKeys already guards
        // against duplicate taps; the server filter (active_issue_nums) hides
        // the backlog item once the real session shows up.
        if (card) card.classList.add('spawning');
        // Optimistically insert a pending-spawn card in Working using a temp id.
        // The real card replaces it once the spawn_pid matches (see loadConversationList).
        const tempPid = 'tmp-' + Date.now();
        if (issueNum) _markIssueOptimisticallyStarted(issueNum, repoPath);
        insertPendingSpawnCard(tempPid, sessionName, false, null, _pendingIssueSpawnMeta(issueNum, repoPath));
        // No need for a second render — insertPendingSpawnCard already renders.
        // Fire spawn in background so it doesn't block UI.
        (async () => { try {
          const res = await fetch('/api/sessions/spawn', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(withRepoPath({ prompt: prompt, name: sessionName }, repoPath)),
          });
          const data = await res.json().catch(() => ({}));
          if (!res.ok || !data.ok) throw new Error(data.error || 'spawn_failed');
          if (data.pid) {
            const placeholder = conversationsData.find(x => x.id === 'spawning-' + tempPid);
            if (placeholder) {
              placeholder.spawn_pid = data.pid;
              pendingSpawns.delete(tempPid);
              pendingSpawns.set(data.pid, placeholder);
            }
          }
          // Fire-and-forget: signal to GitHub that this issue is being worked on
          if (issueNum) {
            fetch('/api/issues/' + issueNum + '/mark-in-progress', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(withRepoPath({}, repoPath)),
            }).catch(() => {});
          }
        } catch (_) {
          _clearOptimisticStartedIssue(issueNum, repoPath);
          _removePendingSpawnCard(tempPid);
          btn.disabled = false;
          btn.textContent = 'Start session';
          if (card) card.classList.remove('spawning');
          showOpToast('Could not start issue session.', 'error');
        }
        setTimeout(function() { refreshConversationList(); }, 800);
        setTimeout(function() { refreshConversationList(); }, 2500);
        // Release the duplicate-tap guard after the refresh window closes.
        setTimeout(function() { _spawningKeys.delete(spawnKey); }, 8000);
        })();
      });
    });
  }

  // Shared helper: inject text to a session via API
  async function injectToSession(sessionId, text, feedbackEl, clearInput) {
    markSessionSending(sessionId);
    try {
      const res = await fetch('/api/inject-input', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: sessionId, text }),
      });
      const data = await res.json();
      if (data.ok) {
        // Optimistic: bump local last_interacted so the card jumps to the top
        // and the "Last interacted just now" line appears without a refresh.
        touchSessionOptimistically(sessionId);
        if (typeof renderSidebar === 'function' && typeof filterConversations === 'function' && typeof $convSearch !== 'undefined') {
          renderSidebar(filterConversations($convSearch.value));
        }
        if (clearInput) {
          const orig = clearInput.placeholder;
          clearInput.value = '';
          clearInput.placeholder = 'sent to terminal ✓';
          clearInput.style.color = 'var(--green)';
          setTimeout(() => {
            clearInput.placeholder = orig;
            clearInput.style.color = '';
          }, 1500);
        } else if (feedbackEl) {
          const orig = feedbackEl.textContent;
          feedbackEl.textContent = '\u2713';
          feedbackEl.style.color = 'var(--green)';
          setTimeout(() => { feedbackEl.textContent = orig; feedbackEl.style.color = ''; }, 1200);
        }
      } else {
        clearSessionSending(sessionId);
        showInjectError(clearInput, feedbackEl, data.error || 'failed');
      }
    } catch (e) {
      clearSessionSending(sessionId);
      showInjectError(clearInput, feedbackEl, String(e && e.message || e));
    }
  }

  function showInjectError(inp, btn, msg) {
    if (inp) {
      const orig = inp.placeholder;
      inp.placeholder = 'failed — ' + msg;
      inp.title = msg;
      inp.style.color = 'var(--text-muted)';
      setTimeout(() => { inp.placeholder = orig; inp.style.color = ''; inp.title = ''; }, 2500);
    }
  }

  function renderConversationList(convs) {
    convs = (Array.isArray(convs) ? convs : []).filter(c => !_isOptimisticallyStartedIssueRow(c));
    _applyOptimisticTouches(convs);
    if (!convs.length) {
      $convList.innerHTML =
        '<div class="empty-state first-run" style="height:auto;padding:28px 20px;font-size:13px;flex-direction:column;gap:10px;align-items:flex-start;color:var(--text-muted);">'
        + '<div style="font-size:14px;color:var(--text);font-weight:600;">No sessions yet</div>'
        + '<div style="line-height:1.5;">Open a terminal and run <code style="background:var(--bg);padding:2px 6px;border-radius:3px;color:var(--text);">claude</code> to start one — it\'ll show up here automatically.</div>'
        + '<div style="line-height:1.5;">Or type a prompt in the <strong style="color:var(--text);">New session prompt</strong> field above and click <strong style="color:var(--text);">Run</strong>.</div>'
        + '</div>';
      return;
    }
    // Threshold for inserting a date-gap separator between rows. The list
    // is sorted newest-first, so a "gap" is the seconds between this
    // card's modified timestamp and the previous (newer) card's. 6h
    // catches "morning vs evening" + "today vs yesterday" without
    // littering the list with separators between consecutive turns.
    const GAP_SEPARATOR_S = 6 * 3600;
    const _gapLabel = (newer, older) => {
      const gapH = (newer - older) / 3600;
      if (gapH < 24) return Math.round(gapH) + 'h gap';
      const days = Math.round(gapH / 24);
      return days + (days === 1 ? ' day gap' : ' days gap');
    };
    // Split rows into the active group (everything not classified as
    // archived in the kanban sense) and the archived group (everything
    // that would land in kanban's Archived column). The user's mental
    // model is "archive button drops the row to the bottom" — a fixed
    // section header makes that destination visible. Same source of
    // truth as the kanban so tapping archive in either view yields the
    // same resting place.
    // Four buckets so the row list mirrors the kanban's coarse categories
    // and reads top-to-bottom as a pipeline:
    //   - ghIssues: un-started GH issues + TODO/PARKING/native-task. 1:1
    //     with the kanban "GH Issues" column.
    //   - readyToMerge: sessions with an open PR waiting for human action
    //     (PR #N recorded). Lifted out of "In progress" because they're a
    //     distinct kind of work — the session is done; the user just needs
    //     to click merge. Without this they get lost in the active list.
    //   - sessions: every other live or resumable session. Sessions LINKED
    //     to a GH issue stay here with a "#N" chip rather than duplicating
    //     under GH Issues — server-side dedup hides the backlog card once
    //     work has started.
    //   - archived: same as before.
    const _sessionConvs = [];
    const _ghIssueConvs = [];
    const _readyToMergeConvs = [];
    const _archivedConvs = [];
    const _inGroupChatIds = new Set(_gcActiveChats.flatMap(c => c.session_ids || []));
    const _inGroupChatConvs = [];
    for (const c of convs) {
      const col = classifyKanbanColumn(c);
      if (col === 'archived') { _archivedConvs.push(c); continue; }
      if (col === 'backlog') { _ghIssueConvs.push(c); continue; }
      // Ready to merge: a recorded PR number is the strong signal — it
      // means `gh pr create` ran successfully in this session. Pkood
      // rows are excluded (they don't open PRs the same way).
      // pr_state ("OPEN" / "MERGED" / "CLOSED") comes from a TTL-cached
      // `gh pr view` lookup on the server. Drop merged/closed so this
      // bucket isn't a graveyard of completed work; treat null/'OPEN'
      // as still ready (gh failures shouldn't hide real PRs).
      const _prDone = c.pr_state === 'MERGED' || c.pr_state === 'CLOSED';
      if (c.source !== 'pkood' && c.tail_pr_number && !_prDone) {
        _readyToMergeConvs.push(c);
        continue;
      }
      const _sid = c.session_id || c.id || '';
      if (_sid && _inGroupChatIds.has(_sid)) {
        _inGroupChatConvs.push(c);
        continue;
      }
      _sessionConvs.push(c);
    }
    const _renderRow = (c, opts = {}) => {
      const isBacklogRow = c.source === 'backlog';
      const isGithubPrRow = c.source === 'github_pr';
      const cleanFirst = c.first_message ? cleanIssuePrompt(c.first_message) : '';
      let rawTitle = c.display_name || (cleanFirst ? firstSentenceOf(cleanFirst, 60) : '(untitled)');
      if (c.backlog_type === 'github' || c.issue_number || c.linked_issue) {
        rawTitle = stripGhIssueProjectTag(rawTitle);
      }
      const title = rawTitle.replace(/-/g, ' ');
      let titleClass = '';
      if (c.name_overridden) titleClass = 'user-renamed';
      else if (!c.display_name && !c.first_message) titleClass = 'untitled';
      // Prefer the last assistant "outcome" (summary) over the original ask —
      // mirrors the kanban card behavior so list view shows what the session did.
      let askHtml = '';
      if (c.last_assistant_text) {
        const raw = String(c.last_assistant_text).trim().slice(0, 140);
        askHtml = '<div class="conv-last">' + escapeHtml(raw) + '</div>';
      } else if (c.display_name && cleanFirst) {
        askHtml = '<div class="conv-last">' + escapeHtml(cleanFirst.slice(0, 100)) + '</div>';
      }
      // _hideAskHtml is set by the archive renderer (#6) — subtitle is
      // redundant with the title in cross-folder mode and the user has
      // explicitly opted not to see it. Backlog cards also stay one-line:
      // their first_message is usually the full GH issue body, which makes
      // the sidebar repeat the title/content preview on every issue row.
      const ask = (isBacklogRow || c._hideAskHtml) ? '' : askHtml;
      // Branch pill: shows where this session's edits actually land.
      //   1. effective_branch (tool-call inference) wins when present.
      //   2. else session_cwd_is_worktree → c.branch in worktree style.
      //   3. else c.branch in default purple badge.
      // Worktree flavour gets the orange palette + 🌿 leaf so a row
      // can be scanned at a glance.
      const isWorktree =
        c.effective_kind === 'worktree' ||
        (!c.effective_branch && c.session_cwd_is_worktree);
      const branchValue = c.effective_branch || c.branch || '';
      const branchTitleAttr = c.effective_branch
        ? 'git worktree (inferred from tool-call paths)'
        : (isWorktree ? 'git worktree' : 'shared clone');
      const branchCls = isWorktree ? 'branch-badge is-worktree' : 'branch-badge';
      const branchIcon = isWorktree ? '🌿 ' : '';
      const branch = branchValue
        ? '<span class="' + branchCls + '" title="' + branchTitleAttr + '">' + branchIcon + escapeHtml(branchValue) + '</span>'
        : '';
      // Live "what is the agent doing right now" pill — same data and
      // freshness rules as the kanban card's .kanban-live-tool, just
      // squeezed onto the row's title line so compact rows stay scannable.
      // Sending pill takes precedence: when the user just hit send and
      // we're waiting for the first sidecar event, show "Sending…" so
      // the row mirrors the right pane's optimistic indicator. Cleared
      // by clearSessionSending when real sidecar data lands.
      let liveToolHtml = '';
      const sidVal = c.session_id || c.id;
      if (sidVal && _sendingSessions.has(sidVal)) {
        liveToolHtml = '<span class="conv-live-tool sending" title="Sending — waiting for the first response from the agent">'
          + '<span class="conv-live-name">● Sending&hellip;</span>'
          + '</span>';
      } else if (c.is_live && c.sidecar_status === 'active' && c.sidecar_tool) {
        const sidecarAge = c.sidecar_ts ? Math.max(0, Math.floor(Date.now() / 1000 - c.sidecar_ts)) : 9999;
        if (sidecarAge < 300) {
          // Row view stays compact: tool name only, no file path. The
          // hover title still surfaces the file for quick reference.
          const shortFile = c.sidecar_file
            ? (c.sidecar_file.length > 40 ? '…' + c.sidecar_file.slice(-39) : c.sidecar_file)
            : '';
          const arrow = c.sidecar_in_flight ? '▶ ' : '';
          liveToolHtml = '<span class="conv-live-tool' + (c.sidecar_in_flight ? ' in-flight' : '') + '" '
            + 'title="' + (c.sidecar_in_flight ? 'Currently running' : 'Last completed') + ': '
            + escapeHtml(c.sidecar_tool) + (shortFile ? ' ' + escapeHtml(shortFile) : '') + '">'
            + '<span class="conv-live-name">' + arrow + escapeHtml(c.sidecar_tool) + '</span>'
            + '</span>';
        }
      }
      const isCodexRow = c.source === 'codex' || c.engine === 'codex';
      const isGeminiRow = c.source === 'gemini' || c.engine === 'gemini';
      let sourceBadge = '';
      if (c.source === 'pkood') sourceBadge = '<span class="source-badge pkood">pkood</span>';
      else if (isCodexRow) sourceBadge = '<span class="source-badge codex">codex</span>';
      else if (isGeminiRow) sourceBadge = '<span class="source-badge gemini">gemini</span>';
      const engineIcon = isCodexRow
        ? '<span class="conv-engine-icon codex" title="Codex session" aria-label="Codex session">C</span>'
        : isGeminiRow
          ? '<span class="conv-engine-icon gemini" title="Gemini session" aria-label="Gemini session">G</span>'
        : '';
      // Prefer "last interacted" (the user's last UI action) over "last
      // event" (which includes Claude's autonomous responses) so the row
      // time mirrors the user's mental model: when did *I* last touch this?
      const rel = relativeTime(c.last_interacted || c.modified);
      const active = currentConversation === c.id ? ' active' : '';

      // Session signals: additive context chips followed by exactly one
      // lifecycle chip per row. Priority chain (highest first):
      //   1. WIP: live active work, live pending tools, mid-turn thinking,
      //      or linked GitHub issue carrying the in-progress label. Codex
      //      uses synthesized sidecar fields so it gets the same yellow row
      //      signal as Claude without stale completed rollouts looking active.
      //   2. pkood rows have their own state machine (running / idle /
      //      blocked / stuck); keep that set.
      //   3. uncommitted: ground-truth `git status --porcelain` against
      //      the EFFECTIVE worktree (server already remaps for sessions
      //      launched in shared clone but editing a sibling worktree).
      //      Outranks PR / pushed / committed because it means there are
      //      changes that haven't landed yet.
      //   4. PR #N (recorded PRs; more actionable than "pushed").
      //   5. pushed.
      //   6. committed (local commits not yet pushed).
      //   7. read-only (agent produced output without touching files).
      //   8. no edits (agent has touched nothing this session).
      let signals = '';
      // GH-issue link chip — additive, lives at the front of the signal
      // chain. Sessions linked to a GH issue show "#N" so they're scannable
      // in the active list (where they live, since started issues moved
      // out of the GH Issues section). Skips backlog cards because their
      // title already starts with "#N:".
      const _linkedNum = (c.source !== 'backlog')
        ? (c.linked_issue || c.issue_number)
        : null;
      if (_linkedNum) {
        signals += '<span class="conv-signal gh-link" title="Linked to GitHub issue #' + escapeHtml(String(_linkedNum)) + '">#' + escapeHtml(String(_linkedNum)) + '</span>';
      }
      const _activityAge = c.sidecar_ts ? Math.max(0, Math.floor(Date.now() / 1000 - c.sidecar_ts)) : 9999;
      const _rowActivityTs = c.sidecar_ts || c.last_interacted || c.modified || 0;
      const _rowActivityAge = _rowActivityTs ? Math.max(0, Math.floor(Date.now() / 1000 - _rowActivityTs)) : 9999;
      const _midTurn = c.last_event_type === 'assistant' || ((isCodexRow || isGeminiRow) && c.last_event_type === 'user');
      const _isActiveSidecar = c.is_live && c.sidecar_status === 'active';
      const _knownActivityTool = c.sidecar_tool || c.pending_tool || '';
      const _hasLivePendingTool = c.is_live && !!c.pending_tool;
      const _codexHasOpenTool = isCodexRow && !c.sidecar_status && !!c.pending_tool;
      const _codexOpenTurn = isCodexRow
        && !c.sidecar_status
        && (_codexHasOpenTool
          || (!!(c.last_event_type === 'user' || c.last_event_type === 'assistant')
            && _rowActivityAge < (30 * 60)));
      const _isWip = !!c.gh_in_progress || !!c.pending_spawn
        || _hasLivePendingTool
        || _codexOpenTurn
        || (_isActiveSidecar && (_activityAge < 300 || _midTurn || !c.sidecar_ts));
      if (_isWip && !liveToolHtml) {
        const wipTitle = _knownActivityTool
          ? ((c.sidecar_in_flight ? 'Currently running' : 'Last known tool') + ': ' + _knownActivityTool)
          : (c.gh_in_progress
              ? 'Linked GitHub issue is marked in progress'
              : (isCodexRow ? 'Codex is working' : (isGeminiRow ? 'Gemini is working' : 'Agent is working')));
        const wipLabel = _codexOpenTurn ? 'WIP' : (_knownActivityTool || 'WIP');
        signals += '<span class="conv-signal activity-working" title="' + escapeHtml(wipTitle) + '">' + escapeHtml(wipLabel) + '</span>';
      }
      if (c.source === 'pkood') {
        const ps = (c.pkood_status || '').toUpperCase();
        if (ps === 'RUNNING') signals += '<span class="conv-signal pkood-running">running</span>';
        else if (ps === 'IDLE') signals += '<span class="conv-signal pkood-idle">idle</span>';
        else if (ps === 'BLOCKED') signals += '<span class="conv-signal pkood-blocked">blocked</span>';
        if (c.pkood_is_stuck) signals += '<span class="conv-signal pkood-stuck">stuck</span>';
      } else if (isWorktree && c.worktree_dirty) {
        signals += '<span class="conv-signal uncommitted" title="git status: this worktree has uncommitted changes">uncommitted</span>';
      } else if (c.tail_pr_number) {
        // Any session with a PR: surface it instead of the generic
        // committed/pushed chip — the PR is the actionable signal.
        // State-aware styling: merged → purple ✓, open → cyan ↗, closed
        // → muted ×. Falls back to neutral "pushed" if pr_state hasn't
        // been resolved yet (gh fetch in flight, missing, or errored).
        const ps = (c.pr_state || '').toUpperCase();
        let prCls, prGlyph, prTitle;
        if (ps === 'MERGED') {
          prCls = 'pr-merged'; prGlyph = '✓ '; prTitle = 'PR merged';
        } else if (ps === 'CLOSED') {
          prCls = 'pr-closed'; prGlyph = '× '; prTitle = 'PR closed without merge';
        } else if (ps === 'OPEN') {
          prCls = 'pr-open';   prGlyph = '↗ '; prTitle = 'PR open';
        } else {
          prCls = 'pushed';    prGlyph = '';   prTitle = 'PR opened by this session';
        }
        signals += '<span class="conv-signal ' + prCls + '" title="' + prTitle + '">' + prGlyph + 'PR #' + c.tail_pr_number + '</span>';
        if (Array.isArray(c.pr_notes)) {
          for (const note of c.pr_notes) {
            if (!note || !note.label) continue;
            const noteCls = note.kind === 'danger' ? 'pr-note-danger' : 'pr-note';
            signals += '<span class="conv-signal ' + noteCls + '" title="' + escapeHtml(note.title || note.label) + '">' + escapeHtml(note.label) + '</span>';
          }
        }
      } else if (c.has_push) {
        signals += '<span class="conv-signal pushed">pushed</span>';
      } else if (c.has_commit) {
        signals += '<span class="conv-signal committed">committed</span>';
      } else if (hasReadOnlyWork(c)) {
        signals += '<span class="conv-signal read-only" title="Agent completed read-only work without file edits">read-only</span>';
      } else if (hasNoEdits(c)) {
        signals += '<span class="conv-signal no-edits" title="Agent has not edited any files in this session">no edits</span>';
      }

      // Merge button: shown when this row plausibly has an open PR.
      // Strong signal: tail_pr_number (extracted from `gh pr create` output).
      // Weak fallback: pushed + a non-default feature branch — gh resolves
      // the PR by branch on click; if there is none, the toast surfaces it.
      const _MERGE_PROTECTED_BRANCHES = ['main', 'master', 'develop', 'trunk'];
      const _hasFeatureBranch = !!branchValue
        && _MERGE_PROTECTED_BRANCHES.indexOf(branchValue) === -1;
      const _showMerge = c.source !== 'pkood' && !c.archived
        && (!!c.tail_pr_number || (!!c.has_push && _hasFeatureBranch));
      const _mergeTitle = c.tail_pr_number
        ? 'Squash-merge PR #' + c.tail_pr_number
        : 'Squash-merge PR for ' + branchValue;
      const mergeBtn = _showMerge
        ? '<button class="conv-merge-btn" data-role="merge" title="' + escapeHtml(_mergeTitle) + '">&#128256;</button>'
        : '';

      let startBtn = '';
      let archiveBtn;
      if (isBacklogRow) {
        const _issueAttr = escapeAttr(c.issue_number || '');
        const _titleAttr = escapeAttr(c.display_name || c.first_message || '');
        // Issue rows carry their concrete repo so the spawn handler can target
        // the right folder without relying on server state.
        const _spawnCwdAttr = escapeAttr(c.spawn_cwd || c.folder_path || '');
        startBtn = '<button class="conv-start-btn" data-role="start" data-issue="' + _issueAttr + '" data-title="' + _titleAttr + '" data-spawn-cwd="' + _spawnCwdAttr + '" title="Spawn a session to work on this issue" aria-label="Start issue session">&#9654;</button>';
        archiveBtn = '<button class="conv-archive-btn is-close" data-role="archive" title="Archive issue (close as not planned)" aria-label="Archive issue">&#128229;</button>';
      } else if (isGithubPrRow) {
        archiveBtn = '';
      } else {
        archiveBtn = '<button class="conv-archive-btn" data-role="archive" title="' + (c.archived ? 'Unarchive' : 'Archive') + '">' + (c.archived ? '&#8617;' : '&#128229;') + '</button>';
      }
      const rowSizeHtml = (isBacklogRow || isGithubPrRow)
        ? ''
        : '<span class="conv-meta-inline">'
          + '<span>' + formatSize(c.size) + '</span>'
          + (sourceBadge ? '<span class="sep">&middot;</span>' + sourceBadge : '')
          + '</span>';
      const rowMetaHtml = (rowSizeHtml || liveToolHtml || signals || branch)
        ? '<span class="conv-row-meta">'
          + (rowSizeHtml || '<span class="conv-meta-inline"></span>')
          + '<span class="conv-status-slot">' + liveToolHtml + signals + '</span>'
          + '<span class="conv-branch-slot">' + branch + '</span>'
          + '</span>'
        : '';

      // Suppressed when the row sits under a folder
      // group header that already labels the folder — chip would be noise.
      const folderChipHtml = (c.folder_label_chip && !opts.suppressFolderChip)
        ? '<span class="conv-folder-chip' + (c.folder_chip_orphan ? ' is-orphan' : '')
            + '" style="--chip-hue:' + (c.folder_chip_hue | 0) + ';"'
            + ' title="' + escapeHtml(c.folder_path || c.session_cwd || '') + '">'
            + escapeHtml(c.folder_label_chip) + '</span>'
        : '';
      const folderChipBeforeTitle = opts.folderChipBeforeTitle !== false;
      const leftFolderChipHtml = folderChipBeforeTitle ? '' : folderChipHtml;
      const titleFolderChipHtml = folderChipBeforeTitle ? folderChipHtml : '';

      // Worktree badge — shown when the session came from a sibling worktree
      // dir (e.g. "claude-command-center-wt-gemini" → badge shows "wt-gemini").
      const worktreeBadgeHtml = c.worktree_label
        ? '<span class="conv-wt-badge" title="Worktree: wt-' + escapeAttr(c.worktree_label) + '">wt-' + escapeHtml(c.worktree_label) + '</span>'
        : '';

      // Live dot — sits in a reserved gutter before the title when this session
      // is actively being polled (sidecar marker exists). Helps the user
      // see at a glance which rows can change in real time.
      const liveDotHtml = c.is_live
        ? '<span class="conv-live-dot" title="Live — actively polled"></span>'
        : '';

      // Pin indicator — shown when the row's repo bucket has been
      // overridden by the user. Click unpins via /api/repo/pin.
      const pinnedHtml = c.pinned_repo
        ? '<button class="conv-repo-pin" data-role="unpin-repo" title="Pinned to this repo. Click to reset to the session’s real repo.">&#128204;</button>'
        : '';

      // History-search match indicators. `_historySnippet` (with HTML <mark>
      // already embedded) is set when this session was matched by the
      // claude-index FTS5 backend in addition to / instead of the local
      // session-list filter. Badge renders as a sibling of `.conv-title`
      // so the rename flow that reads the title's textContent doesn't
      // scoop the badge text into the edit box; the snippet previews why
      // the row matched.
      // Differentiate semantic vs lexical hits: "semantic history" badge
      // (purple) appears when this row matched via the vector path (vec or
      // fused). "history" (blue) for lexical-only.
      const _historyIsSemantic = c._historySource === 'vec' || c._historySource === 'fused';
      const historyBadgeHtml = c._historyMatch
        ? '<span class="conv-history-badge' + (_historyIsSemantic ? ' is-semantic' : '') + '" title="Matched in conversation history' + (_historyIsSemantic ? ' (semantic)' : '') + '">' + (_historyIsSemantic ? 'semantic history' : 'history') + '</span>'
        : '';
      const historySnippetHtml = c._historySnippet
        ? '<div class="conv-history-snippet">' + c._historySnippet + '</div>'
        : '';

      const groupedRowClass = opts.suppressFolderChip ? ' is-grouped-row' : '';
      const rowRepoAttr = escapeAttr(rowRepoPath(c) || '');
      return '<div class="conv-item' + active + groupedRowClass + (isCodexRow ? ' is-codex' : '') + (isGeminiRow ? ' is-gemini' : '') + (c.pinned_repo ? ' is-repo-pinned' : '') + (c._historyMatch ? ' is-history-match' : '') + (_historyIsSemantic ? ' is-semantic-match' : '') + '" draggable="true" data-id="' + c.id + '" data-session-id="' + escapeHtml(c.session_id || c.id) + '" data-repo-path="' + rowRepoAttr + '">'
        + '<span class="drag-handle" data-role="drag">&#10495;</span>'
        + '<div class="conv-title-row">'
          + '<div class="conv-main-row">'
            + '<span class="conv-live-slot" aria-hidden="true">' + liveDotHtml + '</span>'
            + leftFolderChipHtml
            + '<span class="conv-rel" data-role="rel" title="Last activity">' + escapeHtml(rel) + '</span>'
            + engineIcon
            + titleFolderChipHtml
            + '<div class="conv-title ' + titleClass + '" data-role="title" title="Click to open; click again to rename">' + escapeHtml(title) + '</div>'
            + historyBadgeHtml
            + worktreeBadgeHtml
            + pinnedHtml
            + rowMetaHtml
            + '<span class="conv-row-actions">' + mergeBtn + startBtn + archiveBtn + '</span>'
          + '</div>'
        + '</div>'
        + ask
        + historySnippetHtml
      + '</div>';
    };
    // Active list keeps the date-gap separators so morning/evening
    // boundaries stay visible while scanning.
    //
    // In multi-repo (archive) mode — detected by the presence of a
    // folder_label_chip on cards — rows can be grouped by folder so you can
    // scan active work by project without hopping repos. The window selector
    // chooses which active rows are shown; the project view groups every row
    // in that selected window.
    const _folderFilterEl = document.getElementById('convFolderFilter');
    // Folder picker no longer filters — it only groups. Always show folder
    // chips so sessions from other repos are clearly labelled regardless of
    // which folder chip is active in the picker.
    const _isSpecificFolderFilter = false;
    const _hasFolderChips = _sessionConvs.some(c => c.folder_label_chip);
    const _ipWindow = (() => {
      if (!_hasFolderChips) return 'all';
      try {
        const value = localStorage.getItem('ccc-inprogress-window');
        return (value === '1d' || value === '7d' || value === 'all') ? value : '1d';
      } catch (_) {
        return '1d';
      }
    })();
    const _ipWindowDays = _ipWindow === '7d' ? 7 : (_ipWindow === '1d' ? 1 : null);
    const _ipWindowCutoff = _ipWindowDays
      ? Math.floor(Date.now() / 1000) - (_ipWindowDays * 24 * 3600)
      : null;
    const _visibleSessionConvs = _hasFolderChips
      ? (_ipWindowCutoff ? _sessionConvs.filter(c => (c.modified || 0) >= _ipWindowCutoff) : _sessionConvs)
      : _sessionConvs;
    // User-controlled grouping preference for the In Progress section.
    // 'project' (default): group by folder. 'time': flat chrono
    // list with gap separators. Only meaningful when there are folder
    // chips (i.e. multi-repo / archive mode); the In Progress header's
    // by-project / by-time toggle (only shown when _hasFolderChips) writes
    // this key.
    const _ipGrouping = (() => {
      try { return localStorage.getItem('ccc-inprogress-grouping') || 'project'; }
      catch (_) { return 'project'; }
    })();
    const _shouldGroupByFolder = _hasFolderChips && _ipGrouping === 'project';
    const _flatRowsWithSeparators = (cards, opts = {}) => cards.map((c, i, arr) => {
      let separator = '';
      if (i > 0) {
        const newer = (arr[i - 1] && arr[i - 1].modified) || 0;
        const older = (c.modified) || 0;
        if (newer && older && (newer - older) >= GAP_SEPARATOR_S) {
          separator = '<div class="conv-gap-separator">'
            + '<span class="conv-gap-line"></span>'
            + '<span class="conv-gap-label">' + escapeHtml(_gapLabel(newer, older)) + '</span>'
            + '<span class="conv-gap-line"></span>'
            + '</div>';
        }
      }
      return separator + _renderRow(c, opts);
    }).join('');
    const _folderGroupStorageKey = (section, key) =>
      'ccc-folder-group-collapsed:' + section + ':' + String(key || '').slice(0, 180);
    const _isFolderGroupCollapsed = (section, key) => {
      try { return localStorage.getItem(_folderGroupStorageKey(section, key)) === '1'; }
      catch (_) { return false; }
    };
    const _folderGroupHeaderHtml = (section, folder, count, hue, orphan, collapseKey, extraAttrs = '') => {
      const collapsed = _isFolderGroupCollapsed(section, collapseKey);
      return '<div class="conv-folder-group-header" style="--chip-hue:' + hue + ';"'
        + ' role="button" tabindex="0" data-role="folder-group-toggle"'
        + ' data-collapse-key="' + escapeHtml(_folderGroupStorageKey(section, collapseKey)) + '"'
        + ' aria-expanded="' + (!collapsed) + '"' + extraAttrs + '>'
        + '<span class="conv-folder-group-arrow">' + (collapsed ? '▸' : '▾') + '</span>'
        + '<span class="conv-folder-group-chip' + orphan + '">' + escapeHtml(folder) + '</span>'
        + '<span class="conv-folder-group-count">' + count + '</span>'
        + '</div>';
    };
    const _GH_ISSUE_PREVIEW_LIMIT = 5;
    const _ghIssueExpandedStorageKey = (key) =>
      'ccc-ghissues-expanded:' + String(key || '').slice(0, 180);
    const _isGhIssueProjectExpanded = (key) => {
      try { return localStorage.getItem(_ghIssueExpandedStorageKey(key)) === '1'; }
      catch (_) { return false; }
    };
    const _ghIssueShowMoreHtml = (key, hiddenCount, expanded) => {
      if (hiddenCount <= 0) return '';
      return '<div class="conv-ghissues-more">'
        + '<button type="button" class="conv-ghissues-more-btn" data-role="ghissues-show-more"'
        + ' data-expanded="' + (expanded ? '1' : '0') + '"'
        + ' data-expanded-key="' + escapeHtml(_ghIssueExpandedStorageKey(key)) + '">'
        + (expanded ? 'Show fewer' : ('Show ' + hiddenCount + ' more'))
        + '</button>'
        + '</div>';
    };
    const _ghIssueProjectRowsHtml = (cards, key, opts = {}) => {
      const expanded = _isGhIssueProjectExpanded(key);
      const hiddenCount = Math.max(0, cards.length - _GH_ISSUE_PREVIEW_LIMIT);
      const visibleCards = expanded ? cards : cards.slice(0, _GH_ISSUE_PREVIEW_LIMIT);
      return visibleCards.map(c => _renderRow(c, opts)).join('')
        + _ghIssueShowMoreHtml(key, hiddenCount, expanded);
    };

    let _activeRowsHtml;
    if (_shouldGroupByFolder) {
      // Group cards by folder; preserve folder order by the most
      // recent card in each group (freshest folder appears first).
      const _byFolder = new Map();
      for (const c of _visibleSessionConvs) {
        const key = c.folder_label_chip || c.folder_path || '(unknown)';
        if (!_byFolder.has(key)) _byFolder.set(key, []);
        _byFolder.get(key).push(c);
      }
      const _folderEntries = Array.from(_byFolder.entries()).sort((a, b) => {
        const aMax = a[1].reduce((m, c) => Math.max(m, c.modified || 0), 0);
        const bMax = b[1].reduce((m, c) => Math.max(m, c.modified || 0), 0);
        return bMax - aMax;
      });
      const _renderFolderEntry = ([folder, cards]) => {
        if (cards.length === 1) {
          return _renderRow(cards[0], { folderChipBeforeTitle: true });
        }
        const hue = (cards[0].folder_chip_hue | 0);
        const orphan = cards[0].folder_chip_orphan ? ' is-orphan' : '';
        const dropPath = cards[0].folder_path || '';
        const collapseKey = dropPath || folder;
        const collapsed = _isFolderGroupCollapsed('inprogress', collapseKey);
        const headerAttrs = ' data-folder-path="' + escapeHtml(dropPath) + '"'
          + ' data-folder-label="' + escapeHtml(folder) + '"';
        return '<div class="conv-folder-group' + (collapsed ? ' collapsed' : '') + '">'
          + _folderGroupHeaderHtml('inprogress', folder, cards.length, hue, orphan, collapseKey, headerAttrs)
          + cards.map(c => _renderRow(c, { suppressFolderChip: true })).join('')
          + '</div>';
      };
      // Named groups (2+ sessions) first, single-session orphans below.
      const _namedGroups = _folderEntries.filter(([, cards]) => cards.length > 1);
      const _orphanEntries = _folderEntries.filter(([, cards]) => cards.length === 1);
      const _groupHtml = _namedGroups.map(_renderFolderEntry).join('')
        + (_orphanEntries.length ? _orphanEntries.map(_renderFolderEntry).join('') : '');
      _activeRowsHtml = _isSpecificFolderFilter
        ? _flatRowsWithSeparators(_visibleSessionConvs, { suppressFolderChip: true })
        : _groupHtml;
    } else {
      _activeRowsHtml = _flatRowsWithSeparators(_visibleSessionConvs, { suppressFolderChip: _isSpecificFolderFilter });
    }
    if (!_visibleSessionConvs.length) {
      const _emptyWindowLabel = _ipWindow === 'all' ? '' : (' in the last ' + (_ipWindow === '7d' ? '7 days' : 'day'));
      _activeRowsHtml = '<div class="archive-empty-state">No in-progress sessions' + _emptyWindowLabel + '.</div>';
    }
    // In progress section: every row that's not a backlog card or archived.
    // Mirrors the kanban "In progress" column (key: 'working' under the
    // hood — relabel only) so the two surfaces use the same vocabulary.
    // Hidden entirely when there are no active sessions; collapse state
    // persists in localStorage.
    let _inProgressHtml = '';
    if (_sessionConvs.length > 0) {
      const _ipCollapsed = localStorage.getItem('ccc-inprogress-collapsed') === '1';
      const _ipArrow = _ipCollapsed ? '▸' : '▾';
      // 1d / 7d / All and by-project / by-time toggles, only when there are
      // folder chips (otherwise the controls are not meaningful). Inline
      // spans avoid nested-button HTML; click handlers stop propagation.
      const _ipWindowToggle = _hasFolderChips
        ? '<span class="conv-grouping-toggle conv-window-toggle" data-role="window-toggle" title="Limit In progress rows by recent activity">'
            + '<span class="grouping-opt' + (_ipWindow === '1d' ? ' is-active' : '') + '" data-window="1d">1d</span>'
            + '<span class="grouping-opt' + (_ipWindow === '7d' ? ' is-active' : '') + '" data-window="7d">7d</span>'
            + '<span class="grouping-opt' + (_ipWindow === 'all' ? ' is-active' : '') + '" data-window="all">All</span>'
          + '</span>'
        : '';
      // by-project / by-time toggle, only when there are folder chips
      // (otherwise grouping is meaningless). Inline spans so the click
      // doesn't bubble to the section's collapse button (handler does
      // ev.stopPropagation when wired below).
      const _ipGroupingToggle = _hasFolderChips
        ? '<span class="conv-grouping-toggle" data-role="grouping-toggle">'
            + '<span class="grouping-opt' + (_ipGrouping === 'project' ? ' is-active' : '') + '" data-grouping="project">by project</span>'
            + '<span class="grouping-opt' + (_ipGrouping === 'time' ? ' is-active' : '') + '" data-grouping="time">by time</span>'
          + '</span>'
        : '';
      const _ipTools = (_ipWindowToggle || _ipGroupingToggle)
        ? '<span class="conv-inprogress-tools">' + _ipWindowToggle + _ipGroupingToggle + '</span>'
        : '';
      _inProgressHtml =
        '<div class="conv-inprogress-section' + (_ipCollapsed ? ' collapsed' : '') + '" data-role="inprogress-section">'
        + '<button type="button" class="conv-inprogress-header" data-role="inprogress-toggle" aria-expanded="' + (!_ipCollapsed) + '">'
        +   '<span class="conv-inprogress-arrow">' + _ipArrow + '</span>'
        +   '<span class="conv-inprogress-label">In progress</span>'
        +   '<span class="conv-inprogress-count" title="' + _sessionConvs.length + ' total in-progress sessions">' + _visibleSessionConvs.length + '</span>'
        +   _ipTools
        + '</button>'
        + '<div class="conv-inprogress-list">' + _activeRowsHtml + '</div>'
        + '</div>';
    }
    // Ready to merge section: sessions whose work has landed in a PR
    // and is now waiting for the user to click merge. Lifted out of
    // "In progress" so action items don't get buried under live work.
    // Expanded by default — these are the highest-leverage clicks.
    let _readyToMergeHtml = '';
    if (_readyToMergeConvs.length > 0) {
      const _rtmCollapsed = localStorage.getItem('ccc-readytomerge-collapsed') === '1';
      const _rtmArrow = _rtmCollapsed ? '▸' : '▾';
      const _rtmRows = _readyToMergeConvs.map(c => _renderRow(c, { suppressFolderChip: _isSpecificFolderFilter })).join('');
      _readyToMergeHtml =
        '<div class="conv-readytomerge-section' + (_rtmCollapsed ? ' collapsed' : '') + '" data-role="readytomerge-section">'
        + '<button type="button" class="conv-readytomerge-header" data-role="readytomerge-toggle" aria-expanded="' + (!_rtmCollapsed) + '">'
        +   '<span class="conv-readytomerge-arrow">' + _rtmArrow + '</span>'
        +   '<span class="conv-readytomerge-label">Ready to merge</span>'
        +   '<span class="conv-readytomerge-count">' + _readyToMergeConvs.length + '</span>'
        + '</button>'
        + '<div class="conv-readytomerge-list">' + _rtmRows + '</div>'
        + '</div>';
    }
    // GH Issues section: open issues + TODO/PARKING_LOT cards with no
    // session yet. Mirrors the kanban "GH Issues" column so the sidebar
    // scan matches the board. Expanded by default — these are pending
    // work the user might want to start. State persists in localStorage.
    let _ghIssuesHtml = '';
    if (_ghIssueConvs.length > 0) {
      const _ghCollapsed = localStorage.getItem('ccc-ghissues-collapsed') === '1';
      const _ghArrow = _ghCollapsed ? '▸' : '▾';
      const _ghHasFolderChips = _ghIssueConvs.some(c => c.folder_label_chip);
      const _ghGrouping = (() => {
        try { return localStorage.getItem('ccc-ghissues-grouping') || 'project'; }
        catch (_) { return 'project'; }
      })();
      const _ghShouldGroupByFolder = _ghHasFolderChips && _ghGrouping !== 'time' && !_isSpecificFolderFilter;
      let _ghRows;
      if (_ghShouldGroupByFolder) {
        const _byFolder = new Map();
        for (const c of _ghIssueConvs) {
          const key = c.folder_label_chip || c.folder_path || '(unknown)';
          if (!_byFolder.has(key)) _byFolder.set(key, []);
          _byFolder.get(key).push(c);
        }
        const _folderEntries = Array.from(_byFolder.entries()).sort((a, b) => {
          const aMax = a[1].reduce((m, c) => Math.max(m, c.modified || 0), 0);
          const bMax = b[1].reduce((m, c) => Math.max(m, c.modified || 0), 0);
          return bMax - aMax;
        });
        _ghRows = _folderEntries.map(([folder, cards]) => {
          const hue = (cards[0].folder_chip_hue | 0);
          const orphan = cards[0].folder_chip_orphan ? ' is-orphan' : '';
          const collapseKey = cards[0].folder_path || folder;
          const collapsed = _isFolderGroupCollapsed('ghissues', collapseKey);
          return '<div class="conv-folder-group' + (collapsed ? ' collapsed' : '') + '">'
            + _folderGroupHeaderHtml('ghissues', folder, cards.length, hue, orphan, collapseKey)
            + _ghIssueProjectRowsHtml(cards, collapseKey, { suppressFolderChip: true })
            + '</div>';
        }).join('');
      } else {
        const _ghFlatIsSingleProject = _isSpecificFolderFilter || !_ghHasFolderChips;
        if (_ghFlatIsSingleProject) {
          const _flatProjectKey = selectedRepoPath() || 'single-repo-gh-issues';
          _ghRows = _ghIssueProjectRowsHtml(
            _ghIssueConvs,
            _flatProjectKey,
            { suppressFolderChip: _isSpecificFolderFilter }
          );
        } else {
          _ghRows = _flatRowsWithSeparators(_ghIssueConvs, { suppressFolderChip: _isSpecificFolderFilter });
        }
      }
      const _ghGroupingToggle = _ghHasFolderChips
        ? '<span class="conv-grouping-toggle" data-role="ghissues-grouping-toggle">'
            + '<span class="grouping-opt' + (_ghGrouping !== 'time' ? ' is-active' : '') + '" data-grouping="project">by project</span>'
            + '<span class="grouping-opt' + (_ghGrouping === 'time' ? ' is-active' : '') + '" data-grouping="time">by time</span>'
          + '</span>'
        : '';
      const _ghRefresh = '<span class="conv-ghissues-refresh' + (ghIssuesRefreshing ? ' is-spinning' : '') + '" data-role="ghissues-refresh" role="button" tabindex="0" title="Refresh GitHub issues" aria-label="Refresh GitHub issues">&#8635;</span>';
      const _ghTools = '<span class="conv-ghissues-tools">' + _ghRefresh + _ghGroupingToggle + '</span>';
      _ghIssuesHtml =
        '<div class="conv-ghissues-section' + (_ghCollapsed ? ' collapsed' : '') + '" data-role="ghissues-section">'
        + '<button type="button" class="conv-ghissues-header" data-role="ghissues-toggle" aria-expanded="' + (!_ghCollapsed) + '">'
        +   '<span class="conv-ghissues-arrow">' + _ghArrow + '</span>'
        +   '<span class="conv-ghissues-label">GH Issues</span>'
        +   _ghTools
        +   '<span class="conv-ghissues-count">' + _ghIssueConvs.length + '</span>'
        + '</button>'
        + '<div class="conv-ghissues-list">' + _ghRows + '</div>'
        + '</div>';
    }
    // Archived section: always present so the destination is obvious,
    // collapsed by default so it doesn't crowd the active list. State
    // persists in localStorage. No gap separators inside — archived
    // rows are a single block, the user's done with timing.
    let _archivedHtml = '';
    if (_archivedConvs.length > 0) {
      const _arcCollapsed = localStorage.getItem('ccc-archived-collapsed') !== '0';
      const _arcArrow = _arcCollapsed ? '▸' : '▾';
      const _arcRows = _archivedConvs.map(c => _renderRow(c, { suppressFolderChip: _isSpecificFolderFilter })).join('');
      _archivedHtml =
        '<div class="conv-archived-section' + (_arcCollapsed ? ' collapsed' : '') + '" data-role="archived-section">'
        + '<button type="button" class="conv-archived-header" data-role="archived-toggle" aria-expanded="' + (!_arcCollapsed) + '">'
        +   '<span class="conv-archived-arrow">' + _arcArrow + '</span>'
        +   '<span class="conv-archived-label">Archived</span>'
        +   '<span class="conv-archived-count">' + _archivedConvs.length + '</span>'
        + '</button>'
        + '<div class="conv-archived-list">' + _arcRows + '</div>'
        + '</div>';
    }
    // In Group Chat section: shown whenever any coordination is active,
    // even if participants are from a different repo (cross-repo chats).
    let _inGroupChatHtml = '';
    if (_gcActiveChats.length > 0) {
      const _gcRows = _inGroupChatConvs.map(c => _renderRow(c, { suppressFolderChip: _isSpecificFolderFilter })).join('');
      const _gcFirst = _gcActiveChats[0] || null;
      const _gcTopic = _gcFirst ? _gcFirst.topic : '';
      const _gcTopicLabel = _gcTopic ? ' — ' + escapeHtml(_gcTopic.slice(0, 40)) : '';
      const _gcCount = _inGroupChatConvs.length || _gcFirst.session_ids?.length || '';
      _inGroupChatHtml =
        '<div class="conv-ingroupchat-section" data-role="ingroupchat-section">'
        + '<div class="conv-ingroupchat-header" data-role="ingroupchat-open"'
        +   (_gcFirst ? ' data-gc-path="' + escapeHtml(_gcFirst.path_tilde) + '"'
                      + ' data-gc-topic="' + escapeHtml(_gcFirst.topic) + '"'
                      + ' data-gc-mode="' + escapeHtml(_gcFirst.mode) + '"' : '')
        +   ' title="Click to open group chat reader" style="cursor:pointer;">'
        +   '<span class="conv-ingroupchat-icon">💬</span>'
        +   '<span class="conv-ingroupchat-label">In Group Chat</span>'
        +   '<span class="conv-ingroupchat-topic">' + _gcTopicLabel + '</span>'
        +   (_gcCount ? '<span class="conv-ingroupchat-count">' + _gcCount + '</span>' : '')
        + '</div>'
        + (_gcRows ? '<div class="conv-ingroupchat-list">' + _gcRows + '</div>' : '')
        + '</div>';
    }
    // Order: In Group Chat (live) → GH Issues (to start) → Ready to merge
    // (action) → In progress → Archived.
    $convList.innerHTML = _inGroupChatHtml + _ghIssuesHtml + _readyToMergeHtml + _inProgressHtml + _archivedHtml;
    // Toggle handler for the Archived section header.
    const $archivedToggle = $convList.querySelector('[data-role="archived-toggle"]');
    if ($archivedToggle) {
      $archivedToggle.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const section = $archivedToggle.closest('[data-role="archived-section"]');
        if (!section) return;
        const wasCollapsed = section.classList.toggle('collapsed');
        localStorage.setItem('ccc-archived-collapsed', wasCollapsed ? '1' : '0');
        const arrowEl = $archivedToggle.querySelector('.conv-archived-arrow');
        if (arrowEl) arrowEl.textContent = wasCollapsed ? '▸' : '▾';
        $archivedToggle.setAttribute('aria-expanded', String(!wasCollapsed));
      });
    }
    // Click handler for the "In Group Chat" section header — opens the reader.
    const $gcOpen = $convList.querySelector('[data-role="ingroupchat-open"]');
    if ($gcOpen) {
      $gcOpen.addEventListener('click', () => {
        const path = $gcOpen.dataset.gcPath;
        const topic = $gcOpen.dataset.gcTopic || '';
        const mode = $gcOpen.dataset.gcMode || 'topic';
        if (path) openGroupChatReader(path, topic, mode, true);
      });
    }
    // Toggle handler for the GH Issues section header.
    const $ghIssuesToggle = $convList.querySelector('[data-role="ghissues-toggle"]');
    if ($ghIssuesToggle) {
      $ghIssuesToggle.addEventListener('click', (ev) => {
        if (ev.target.closest('[data-role="ghissues-grouping-toggle"], [data-role="ghissues-refresh"]')) return;
        ev.stopPropagation();
        const section = $ghIssuesToggle.closest('[data-role="ghissues-section"]');
        if (!section) return;
        const wasCollapsed = section.classList.toggle('collapsed');
        localStorage.setItem('ccc-ghissues-collapsed', wasCollapsed ? '1' : '0');
        const arrowEl = $ghIssuesToggle.querySelector('.conv-ghissues-arrow');
        if (arrowEl) arrowEl.textContent = wasCollapsed ? '▸' : '▾';
        $ghIssuesToggle.setAttribute('aria-expanded', String(!wasCollapsed));
      });
    }
    const $ghGroupingToggle = $convList.querySelector('[data-role="ghissues-grouping-toggle"]');
    if ($ghGroupingToggle) {
      $ghGroupingToggle.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const opt = ev.target.closest('[data-grouping]');
        if (!opt) return;
        const value = opt.getAttribute('data-grouping') === 'time' ? 'time' : 'project';
        try { localStorage.setItem('ccc-ghissues-grouping', value); } catch (_) {}
        renderArchiveList(document.getElementById('convSearch')?.value || '');
      });
    }
    const $ghIssuesRefresh = $convList.querySelector('[data-role="ghissues-refresh"]');
    if ($ghIssuesRefresh) {
      const runGhIssuesRefresh = (ev) => {
        ev.stopPropagation();
        ev.preventDefault();
        refreshGhIssuesSection();
      };
      $ghIssuesRefresh.addEventListener('click', runGhIssuesRefresh);
      $ghIssuesRefresh.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' || ev.key === ' ') runGhIssuesRefresh(ev);
      });
    }
    $convList.querySelectorAll('[data-role="ghissues-show-more"]').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const key = btn.getAttribute('data-expanded-key') || '';
        if (!key) return;
        const expanded = btn.getAttribute('data-expanded') === '1';
        try {
          if (expanded) localStorage.removeItem(key);
          else localStorage.setItem(key, '1');
        } catch (_) {}
        renderArchiveList(document.getElementById('convSearch')?.value || '');
      });
    });
    // Toggle handler for the Ready to merge section header.
    const $rtmToggle = $convList.querySelector('[data-role="readytomerge-toggle"]');
    if ($rtmToggle) {
      $rtmToggle.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const section = $rtmToggle.closest('[data-role="readytomerge-section"]');
        if (!section) return;
        const wasCollapsed = section.classList.toggle('collapsed');
        localStorage.setItem('ccc-readytomerge-collapsed', wasCollapsed ? '1' : '0');
        const arrowEl = $rtmToggle.querySelector('.conv-readytomerge-arrow');
        if (arrowEl) arrowEl.textContent = wasCollapsed ? '▸' : '▾';
        $rtmToggle.setAttribute('aria-expanded', String(!wasCollapsed));
      });
    }
    // Toggle handler for the In progress section header.
    const $inProgressToggle = $convList.querySelector('[data-role="inprogress-toggle"]');
    if ($inProgressToggle) {
      $inProgressToggle.addEventListener('click', (ev) => {
        // The grouping toggle (project / time) lives inside this header
        // button — its own listener stops propagation, but be defensive.
        if (ev.target.closest('[data-role="grouping-toggle"], [data-role="window-toggle"]')) return;
        ev.stopPropagation();
        const section = $inProgressToggle.closest('[data-role="inprogress-section"]');
        if (!section) return;
        const wasCollapsed = section.classList.toggle('collapsed');
        localStorage.setItem('ccc-inprogress-collapsed', wasCollapsed ? '1' : '0');
        const arrowEl = $inProgressToggle.querySelector('.conv-inprogress-arrow');
        if (arrowEl) arrowEl.textContent = wasCollapsed ? '▸' : '▾';
        $inProgressToggle.setAttribute('aria-expanded', String(!wasCollapsed));
      });
    }
    // Grouping toggle (by project / by time). Triggers a re-render via the
    // same path the data came from: archive mode → renderArchiveList,
    // single-repo mode → loadConversationList. (In single-repo mode the
    // toggle isn't rendered anyway since folder chips are absent, but be
    // safe.)
    const $groupingToggle = $convList.querySelector('[data-role="grouping-toggle"]');
    if ($groupingToggle) {
      $groupingToggle.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const opt = ev.target.closest('[data-grouping]');
        if (!opt) return;
        const value = opt.getAttribute('data-grouping');
        try { localStorage.setItem('ccc-inprogress-grouping', value); } catch (_) {}
        renderArchiveList(document.getElementById('convSearch')?.value || '');
      });
    }
    const $windowToggle = $convList.querySelector('[data-role="window-toggle"]');
    if ($windowToggle) {
      $windowToggle.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const opt = ev.target.closest('[data-window]');
        if (!opt) return;
        const value = opt.getAttribute('data-window');
        if (value !== '1d' && value !== '7d' && value !== 'all') return;
        try { localStorage.setItem('ccc-inprogress-window', value); } catch (_) {}
        renderArchiveList(document.getElementById('convSearch')?.value || '');
      });
    }
    $convList.querySelectorAll('[data-role="folder-group-toggle"]').forEach(hdr => {
      const toggleFolderGroup = (ev) => {
        ev.stopPropagation();
        const group = hdr.closest('.conv-folder-group');
        if (!group) return;
        const wasCollapsed = group.classList.toggle('collapsed');
        const key = hdr.getAttribute('data-collapse-key') || '';
        if (key) {
          try { localStorage.setItem(key, wasCollapsed ? '1' : '0'); } catch (_) {}
        }
        const arrowEl = hdr.querySelector('.conv-folder-group-arrow');
        if (arrowEl) arrowEl.textContent = wasCollapsed ? '▸' : '▾';
        hdr.setAttribute('aria-expanded', String(!wasCollapsed));
      };
      hdr.addEventListener('click', toggleFolderGroup);
      hdr.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Enter' && ev.key !== ' ') return;
        ev.preventDefault();
        toggleFolderGroup(ev);
      });
    });
    $convList.querySelectorAll('.conv-item').forEach(el => {
      el.addEventListener('click', (ev) => {
        // Ignore clicks that started the inline editor, archive button,
        // or that landed on the title (which now triggers rename instead
        // of opening the conversation — the pencil's job moved here).
        if (ev.target.closest('[data-role="edit"]') || ev.target.closest('[data-role="archive"]') || ev.target.closest('[data-role="merge"]') || ev.target.closest('[data-role="start"]') || ev.target.closest('[data-role="unpin-repo"]') || ev.target.closest('.conv-title-input') || ev.target.closest('[data-role="title"]')) return;
        if (ev.metaKey || ev.ctrlKey || ev.shiftKey) {
          ev.preventDefault();
          if (selectedListIds.has(el.dataset.id)) {
            selectedListIds.delete(el.dataset.id);
            el.classList.remove('list-selected');
          } else {
            selectedListIds.add(el.dataset.id);
            el.classList.add('list-selected');
          }
          updateCoordToolbar();
          return;
        }
        if (selectedListIds.size > 0) {
          document.querySelectorAll('.conv-item.list-selected').forEach(n => n.classList.remove('list-selected'));
          selectedListIds.clear();
          updateCoordToolbar();
        }
        const row = conversationsData.find(c => c.id === el.dataset.id);
        if (row && row.source === 'github_pr') {
          if (row.tail_pr_url) window.open(row.tail_pr_url, '_blank');
          return;
        }
        selectConversation(el.dataset.id);
      });
      attachDragHandlers(el);
    });
    // Unpin-repo button on rows whose folder bucket the user pinned.
    $convList.querySelectorAll('[data-role="unpin-repo"]').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        ev.preventDefault();
        const item = btn.closest('.conv-item');
        const sid = item && item.dataset.sessionId;
        if (!sid) return;
        try {
          await fetch('/api/repo/pin', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ session_id: sid, path: '' }),
          });
        } catch (_) { /* swallow — UI refresh below will surface failure */ }
        await refreshArchiveData();
        renderArchiveList(document.getElementById('convSearch')?.value || '');
      });
    });
    // Drop targets — folder group headers in archive mode. Dropping a
    // row onto a header pins that session to the header's repo so the
    // row appears under it everywhere (single view + all-repos).
    $convList.querySelectorAll('.conv-folder-group-header[data-folder-path]').forEach(hdr => {
      const targetPath = hdr.getAttribute('data-folder-path');
      if (!targetPath) return;
      hdr.addEventListener('dragover', (ev) => {
        if (!dragSourceId) return;
        ev.preventDefault();
        try { ev.dataTransfer.dropEffect = 'link'; } catch (_) {}
        hdr.classList.add('drop-target');
      });
      hdr.addEventListener('dragleave', () => {
        hdr.classList.remove('drop-target');
      });
      hdr.addEventListener('drop', async (ev) => {
        ev.preventDefault();
        hdr.classList.remove('drop-target');
        const src = dragSourceId;
        if (!src) return;
        const card = conversationsData.find(c => c.id === src);
        const sid = card && (card.session_id || card.id);
        if (!sid) return;
        // Don't pin to the bucket the row is already in.
        if (card && card.folder_path === targetPath && !card.pinned_repo) return;
        try {
          await fetch('/api/repo/pin', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ session_id: sid, path: targetPath }),
          });
        } catch (_) { /* swallow */ }
        await refreshArchiveData();
        renderArchiveList(document.getElementById('convSearch')?.value || '');
      });
    });
    // Click on the title element: inactive row titles open the
    // conversation; clicking the already-active title starts inline rename.
    $convList.querySelectorAll('[data-role="title"]').forEach(el => {
      el.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const item = el.closest('.conv-item');
        if (!item || !item.dataset.id) return;
        const alreadyActive = item.classList.contains('active') || currentConversation === item.dataset.id;
        if (!alreadyActive) {
          selectConversation(item.dataset.id);
          return;
        }
        startInlineRename(item);
      });
    });
    $convList.querySelectorAll('.conv-archive-btn').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const item = btn.closest('.conv-item');
        const convId = item.dataset.id;
        const sessionId = item.dataset.sessionId;
        // If the user is archiving the currently-open row, pick its
        // neighbour now (next sibling, falling back to previous) so we can
        // jump there once the row vanishes from the active list. Skip
        // when un-archiving, or when archiving a row that isn't selected.
        const fromActiveList = !item.closest('.conv-archived-section');
        const wasSelected = currentConversation === convId;
        let nextSelectId = null;
        if (fromActiveList && wasSelected) {
          const findSibling = (start, dir) => {
            let probe = start;
            while (probe) {
              if (probe.classList && probe.classList.contains('conv-item') && probe.dataset && probe.dataset.id) {
                return probe.dataset.id;
              }
              probe = dir === 'next' ? probe.nextElementSibling : probe.previousElementSibling;
            }
            return null;
          };
          nextSelectId = findSibling(item.nextElementSibling, 'next')
                      || findSibling(item.previousElementSibling, 'prev');
        }
        try {
          const c = conversationsData.find(x => x.id === convId || x.session_id === sessionId);
          const repoPath = (c && rowRepoPath(c)) || item.dataset.repoPath || '';
          const data = await ccPostJson('/api/conversations/' + convId + '/archive',
            archivePayloadForRow(c || { repo_path: repoPath }, sessionId));
          if (!data.ok) {
            const ghError = data.github && data.github.stderr;
            throw new Error(data.error || ghError || 'archive failed');
          }
          if (c) {
            c.archived = data.archived;
            setOptimisticOverride(c.session_id, { archived: c.archived });
          }
          if ((convId || '').startsWith('xrepo-issue-') || (convId || '').startsWith('backlog-issue-')) {
            if (data.archived) _archivedBacklogIds.add(convId);
            else _archivedBacklogIds.delete(convId);
            _persistArchivedBacklog();
          }
          // Patch archiveData (Round 5 / #13) so the next archive
          // re-render reflects the new archived state — same reason
          // as the rename patch above.
          if (typeof archiveData !== 'undefined' && Array.isArray(archiveData)) {
            const ac = archiveData.find(x => x.session_id === sessionId);
            if (ac) ac.archived = data.archived;
          }
          if (typeof currentRepoBacklogData !== 'undefined' && Array.isArray(currentRepoBacklogData)) {
            const bc = currentRepoBacklogData.find(x => x.id === convId || x.session_id === sessionId);
            if (bc) bc.archived = data.archived;
          }
          renderSidebar(filterConversations($convSearch.value));
          if (data.archived && nextSelectId) {
            selectConversation(nextSelectId);
          }
        } catch (err) {
          showOpToast('Archive failed (' + err.message + ')', 'error');
        }
      });
    });
    // Start button on backlog rows — mirrors .kanban-start-btn so the same
    // spawn flow is reachable from the row list. Reuses _spawningKeys for
    // duplicate-tap guard and pendingSpawns for the optimistic placeholder.
    $convList.querySelectorAll('.conv-start-btn').forEach(btn => {
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        const issueNum = btn.dataset.issue;
        const rawTitle = btn.dataset.title;
        // spawnCwd points at the issue's concrete repo so the new session
        // lands in the right folder.
        const spawnCwd = btn.dataset.spawnCwd || '';
        const cleanTitle = (rawTitle || '').replace(/^#\d+:\s*/, '').replace(/\[[^\]]*\]\s*/g, '').trim();
        const sessionName = issueNum
          ? ('issue-' + issueNum)
          : cleanTitle.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
        const prompt = (issueNum
          ? 'Fix issue #' + issueNum + ' — ' + cleanTitle + '\n\nRun `gh issue view ' + issueNum + '` for the full body (title may be truncated).'
          : cleanTitle) + SESSION_STATE_INSTRUCTION;
        const spawnRepoPath = spawnCwd || repoPathForIssueNumber(issueNum);
        const spawnKey = issueNum ? (_issueStartKey(issueNum, spawnRepoPath) || ('issue-' + issueNum)) : sessionName;
        if (_spawningKeys.has(spawnKey)) return;
        _spawningKeys.add(spawnKey);
        btn.disabled = true;
        btn.textContent = '…';
        btn.title = 'Spawning session…';
        const tempPid = 'tmp-' + Date.now();
        if (issueNum) _markIssueOptimisticallyStarted(issueNum, spawnRepoPath);
        insertPendingSpawnCard(tempPid, sessionName, false, null, _pendingIssueSpawnMeta(issueNum, spawnRepoPath));
        (async () => {
          try {
            const spawnBody = { prompt: prompt, name: sessionName };
            if (spawnRepoPath) {
              spawnBody.cwd = spawnRepoPath;
              spawnBody.repo_path = spawnRepoPath;
            }
            const res = await fetch('/api/sessions/spawn', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify(spawnBody),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) throw new Error(data.error || 'spawn_failed');
            if (data.pid) {
              const placeholder = conversationsData.find(x => x.id === 'spawning-' + tempPid);
              if (placeholder) {
                placeholder.spawn_pid = data.pid;
                pendingSpawns.delete(tempPid);
                pendingSpawns.set(data.pid, placeholder);
              }
            }
            if (issueNum && spawnRepoPath) {
              fetch('/api/issues/' + issueNum + '/mark-in-progress', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ repo_path: spawnRepoPath }),
              }).catch(() => {});
            }
          } catch (_) {
            _clearOptimisticStartedIssue(issueNum, spawnRepoPath);
            _removePendingSpawnCard(tempPid);
            btn.disabled = false;
            btn.innerHTML = '&#9654;';
            btn.title = 'Spawn a session to work on this issue';
            showOpToast('Could not start issue session.', 'error');
          }
          setTimeout(refreshConversationList, 800);
          setTimeout(refreshConversationList, 2500);
          setTimeout(() => { _spawningKeys.delete(spawnKey); }, 8000);
        })();
      });
    });
    $convList.querySelectorAll('.conv-merge-btn').forEach(btn => {
      btn.addEventListener('click', async (ev) => {
        ev.stopPropagation();
        const item = btn.closest('.conv-item');
        const convId = item.dataset.id;
        const sessionId = item.dataset.sessionId;
        const c = conversationsData.find(x => x.id === convId);
        const branchName = (c && (c.effective_branch || c.branch)) || '';
        const prNumber = (c && c.tail_pr_number) || null;
        const prUrl = (c && c.tail_pr_url) || null;
        const target = prNumber ? ('PR #' + prNumber) : ('branch ' + branchName);
        if (!prNumber && !branchName) {
          showOpToast('No PR target found for this row', 'error');
          return;
        }
        if (!confirm('Squash-merge ' + target + '?')) return;
        btn.disabled = true;
        try {
          const data = await ccPostJson('/api/conversations/' + convId + '/merge-pr',
            { session_id: sessionId, branch: branchName, pr_number: prNumber, pr_url: prUrl, repo_path: rowRepoPath(c) || '' });
          if (data.ok) {
            if (data.via === 'session') {
              showOpToast('Asked session to merge ' + target + ' — see chat');
              selectConversation(convId);
            } else {
              if (data.archived && c) {
                c.archived = true;
                setOptimisticOverride(c.session_id || sessionId, { archived: true });
              }
              showOpToast('Merged ' + target + ' → archived');
              if (typeof loadConversationList === 'function') loadConversationList();
            }
          } else {
            const errMsg = data.error || (data.inject && data.inject.error) || 'unknown';
            // Conflict-recovery path: server's /merge-pr returns the
            // "merge conflicts" string for the GraphQL conflict case.
            // Offer to auto-rebase + force-push + retry. Force-push is
            // gated by an explicit confirm so the user opts in each time.
            const isConflict = /merge conflicts/i.test(errMsg);
            if (isConflict && confirm(
              target + ' has merge conflicts.\n\n' +
              'Auto-rebase against the PR base and retry?\n' +
              'This force-pushes the branch with --force-with-lease.'
            )) {
              try {
                const r2 = await ccPostJson('/api/conversations/' + convId + '/rebase-merge',
                  { session_id: sessionId, branch: branchName, pr_number: prNumber, pr_url: prUrl });
                if (r2.ok) {
                  if (r2.archived && c) {
                    c.archived = true;
                    setOptimisticOverride(c.session_id || sessionId, { archived: true });
                  }
                  showOpToast('Rebased onto ' + (r2.base || 'base') + ' + merged ' + target + ' → archived');
                  if (typeof loadConversationList === 'function') loadConversationList();
                } else {
                  const stepLabel = r2.step ? ' [' + r2.step + ']' : '';
                  showOpToast('Auto-rebase failed' + stepLabel + ': ' + (r2.error || 'unknown'), 'error');
                }
              } catch (err) {
                showOpToast('Auto-rebase failed (' + err.message + ')', 'error');
              }
            } else {
              showOpToast('Merge failed: ' + errMsg, 'error');
            }
          }
        } catch (err) {
          showOpToast('Merge failed (' + err.message + ')', 'error');
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  // ── Drag-to-reorder ──
  let dragSourceId = null;

  function attachDragHandlers(el) {
    el.addEventListener('dragstart', (ev) => {
      dragSourceId = el.dataset.id;
      el.classList.add('dragging');
      try { ev.dataTransfer.effectAllowed = 'move'; } catch (_) {}
      try { ev.dataTransfer.setData('text/plain', el.dataset.id); } catch (_) {}
    });
    el.addEventListener('dragend', () => {
      dragSourceId = null;
      $convList.querySelectorAll('.conv-item').forEach(n => {
        n.classList.remove('dragging', 'drop-above', 'drop-below');
      });
    });
    el.addEventListener('dragover', (ev) => {
      if (!dragSourceId || dragSourceId === el.dataset.id) return;
      ev.preventDefault();
      try { ev.dataTransfer.dropEffect = 'move'; } catch (_) {}
      const rect = el.getBoundingClientRect();
      const before = (ev.clientY - rect.top) < rect.height / 2;
      el.classList.toggle('drop-above', before);
      el.classList.toggle('drop-below', !before);
    });
    el.addEventListener('dragleave', () => {
      el.classList.remove('drop-above', 'drop-below');
    });
    el.addEventListener('drop', async (ev) => {
      ev.preventDefault();
      const src = dragSourceId;
      const dstId = el.dataset.id;
      if (!src || src === dstId) return;
      const before = el.classList.contains('drop-above');
      el.classList.remove('drop-above', 'drop-below');
      // When src and dst belong to different folder buckets, treat the
      // drop as a repo pin instead of a reorder.
      {
        const srcCard = conversationsData.find(c => c.id === src);
        const dstCard = conversationsData.find(c => c.id === dstId);
        if (srcCard && dstCard && dstCard.folder_path
            && srcCard.folder_path !== dstCard.folder_path) {
          const sid = srcCard.session_id || srcCard.id;
          try {
            await fetch('/api/repo/pin', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({ session_id: sid, path: dstCard.folder_path }),
            });
          } catch (_) { /* swallow */ }
          await refreshArchiveData();
          renderArchiveList(document.getElementById('convSearch')?.value || '');
          return;
        }
        // Same-bucket reorder in archive mode is meaningless (rows are
        // mtime-sorted), so no-op rather than persist a bogus order.
        return;
      }
      // Reorder conversationsData: remove src, insert relative to dst
      const srcIdx = conversationsData.findIndex(c => c.id === src);
      if (srcIdx < 0) return;
      const [moved] = conversationsData.splice(srcIdx, 1);
      const dstIdx = conversationsData.findIndex(c => c.id === dstId);
      const insertAt = before ? dstIdx : dstIdx + 1;
      conversationsData.splice(insertAt, 0, moved);
      // Save + re-render
      await saveConversationOrder();
      renderSidebar(filterConversations($convSearch.value));
    });
    const renderedIds = new Set();
    $convList.querySelectorAll('.conv-item').forEach(el => {
      if (el.dataset.id) renderedIds.add(el.dataset.id);
      if (selectedListIds.has(el.dataset.id)) el.classList.add('list-selected');
    });
    selectedListIds.forEach(id => { if (!renderedIds.has(id)) selectedListIds.delete(id); });
    updateCoordToolbar();
  }

  async function saveConversationOrder() {
    try {
      const order = conversationsData.map(c => c.session_id || c.id);
      await fetch('/api/conversations/order', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ order }),
      });
    } catch (err) { /* swallow */ }
  }

  // Sort toggle: "custom" (drag order) vs "recent" (by mtime)
  let convSortMode = 'recent';  // always latest-first
  // Alphabetical override — when on, sorts by title and ignores convSortMode.
  let convAlphaSort = localStorage.getItem('ccc-conv-alpha-sort') === '1';
  // Recency filter: '' (off) | '10h' | '7d'.
  // Backwards-compat: old '1' value (binary toggle) maps to '10h'.
  let recencyFilter = (function () {
    const raw = localStorage.getItem('ccc-show-recent') || '';
    if (raw === '1') return '10h';
    if (raw === '10h' || raw === '7d') return raw;
    return '';
  })();
  // Helpers — derived from recencyFilter so the rest of the code can stay
  // boolean-ish where it doesn't care about the actual window.
  const RECENCY_WINDOWS = { '10h': 10 * 3600, '7d': 7 * 24 * 3600 };
  function recencyCutoffSec() {
    const w = RECENCY_WINDOWS[recencyFilter];
    return w ? (Date.now() / 1000 - w) : 0;
  }
  let showRecentOnly = !!recencyFilter;  // legacy alias used by other branches
  const RECENT_HOURS = 10;  // legacy const, no longer the source of truth
  const $convSortBtn = document.getElementById('convSortBtn');
  const $convAlphaSortBtn = document.getElementById('convAlphaSortBtn');

  function _convTitleForSort(c) {
    const raw = c.display_name
      || (c.first_message ? String(c.first_message).slice(0, 80) : '')
      || '';
    return raw.replace(/-/g, ' ').trim().toLowerCase();
  }

  function applyConvSort(data) {
    if (convAlphaSort) {
      return [...data].sort((a, b) => {
        const ta = _convTitleForSort(a);
        const tb = _convTitleForSort(b);
        if (!ta && tb) return 1;
        if (ta && !tb) return -1;
        return ta.localeCompare(tb);
      });
    }
    if (convSortMode === 'recent') {
      // Sort by whichever is later: the user's last UI interaction or the
      // session's last meaningful event. Keeps the just-typed card on top
      // even before Claude responds.
      const score = (c) => c.last_interacted || c.modified || 0;
      return [...data].sort((a, b) => score(b) - score(a));
    }
    return data; // custom/default order from server
  }

  function updateSortBtn() {
    if ($convSortBtn) {
      const isRecent = convSortMode === 'recent';
      $convSortBtn.classList.toggle('active', isRecent && !convAlphaSort);
      $convSortBtn.title = convAlphaSort
        ? 'Chronological sort (disabled while A↓ is active)'
        : (isRecent
            ? 'Sorted by latest response (click for custom order)'
            : 'Sort by latest response (ignores custom order)');
    }
    if ($convAlphaSortBtn) {
      $convAlphaSortBtn.classList.toggle('active', convAlphaSort);
      $convAlphaSortBtn.title = convAlphaSort
        ? 'Alphabetical sort on (click to switch back to chronological)'
        : 'Sort alphabetically by title';
    }
  }

  if ($convSortBtn) {
    updateSortBtn();
    $convSortBtn.addEventListener('click', () => {
      convSortMode = convSortMode === 'recent' ? 'custom' : 'recent';
      localStorage.setItem('ccc-conv-sort', convSortMode);
      // Clicking the chronological toggle implies the user wants chronological
      // — turn alpha off so the new mode actually takes effect.
      if (convAlphaSort) {
        convAlphaSort = false;
        localStorage.setItem('ccc-conv-alpha-sort', '0');
      }
      updateSortBtn();
      renderSidebar(filterConversations($convSearch.value));
    });
  }
  if ($convAlphaSortBtn) {
    $convAlphaSortBtn.addEventListener('click', () => {
      convAlphaSort = !convAlphaSort;
      localStorage.setItem('ccc-conv-alpha-sort', convAlphaSort ? '1' : '0');
      updateSortBtn();
      renderSidebar(filterConversations($convSearch.value));
    });
  }

  // ── Kanban toggle ──
  function activateSplitMode(active) {
    if (active) {
      document.body.classList.add('kanban-split');
      if ($kanbanLayout) $kanbanLayout.classList.add('active');
    } else {
      document.body.classList.remove('kanban-split');
      if ($kanbanLayout) $kanbanLayout.classList.remove('active');
    }
    // Reset mobile overlays when layout changes so nothing lingers
    document.body.classList.remove('mobile-conv-open');
    document.body.classList.remove('mobile-show-main');
  }
  function updateKanbanToggle() {
    if ($convKanbanToggle) $convKanbanToggle.classList.toggle('active', kanbanView);
    // The split-pane kanban layout was retired in favour of swapping the
    // sidebar's list↔kanban-board view inline (so .main stays visible in
    // both modes). Always passing `false` here also clears any leftover
    // body.kanban-split class from older versions or in-flight FOIT.
    activateSplitMode(false);
  }
  updateKanbanToggle();
  // Apply initial state if kanban was persisted on
  if (kanbanView) {
    $convList.style.display = 'none';
    $kanbanBoard.style.display = '';
  }
  if ($convKanbanToggle) {
    $convKanbanToggle.addEventListener('click', () => {
      kanbanView = !kanbanView;
      localStorage.setItem('ccc-kanban-view', kanbanView ? 'true' : 'false');
      updateKanbanToggle();
      renderSidebar(filterConversations($convSearch.value));
    });
  }
  // Compact-rows toggle. Adds a class to the conv-list so the row
  // styles in CSS take over: titles truncate to a single line and the
  // ask preview disappears. Persists in localStorage so it sticks.
  const $convCompactToggle = document.getElementById('convCompactToggle');
  function applyCompactRowsState() {
    const compact = localStorage.getItem('ccc-compact-rows') === '1';
    if ($convList) $convList.classList.toggle('compact-rows', compact);
    if ($convCompactToggle) $convCompactToggle.classList.toggle('active', compact);
  }
  if ($convCompactToggle) {
    $convCompactToggle.addEventListener('click', () => {
      const next = localStorage.getItem('ccc-compact-rows') !== '1';
      localStorage.setItem('ccc-compact-rows', next ? '1' : '0');
      applyCompactRowsState();
    });
  }
  applyCompactRowsState();
  // Back-to-list button in split toolbar
  const $kptListViewBtn = document.getElementById('kptListViewBtn');
  if ($kptListViewBtn) {
    $kptListViewBtn.addEventListener('click', () => {
      kanbanView = false;
      localStorage.setItem('ccc-kanban-view', 'false');
      updateKanbanToggle();
      renderSidebar(filterConversations($convSearch.value));
    });
  }

  async function refreshConversationList() {
    if (isInlineRenameInProgress()) return;
    if ($convRefreshBtn) $convRefreshBtn.classList.add('spinning');
    conversationsLoaded = false;
    await refreshArchiveData();
    renderArchiveList($convSearch ? $convSearch.value : '');
    if (selectedRepoPath()) await loadConversationList();
    setTimeout(() => {
      if ($convRefreshBtn) $convRefreshBtn.classList.remove('spinning');
    }, 400);
  }

  if ($convRefreshBtn) $convRefreshBtn.addEventListener('click', refreshConversationList);

  // Archive toggle
  const $convArchiveToggle = document.getElementById('convArchiveToggle');
  if ($convArchiveToggle) {
    $convArchiveToggle.addEventListener('click', () => {
      showArchived = !showArchived;
      $convArchiveToggle.classList.toggle('active', showArchived);
      $convArchiveToggle.title = showArchived ? 'Show active' : 'Show archived';
      renderSidebar(filterConversations($convSearch.value));
    });
  }

  function startInlineRename(item) {
    if (!item) return;
    const titleEl = item.querySelector('[data-role="title"]');
    if (!titleEl || item.querySelector('.conv-title-input')) return;
    const currentText = titleEl.textContent;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'conv-title-input';
    input.value = currentText.startsWith('(') && currentText.endsWith(')') ? '' : currentText;
    input.placeholder = 'Session name…';
    titleEl.replaceWith(input);
    // hide edit button while editing
    const editBtn = item.querySelector('.conv-edit-btn');
    if (editBtn) editBtn.style.display = 'none';
    input.focus();
    input.select();
    _renameInProgress = true;  // freeze background re-renders

    let finished = false;
    async function commit(save) {
      if (finished) return;
      finished = true;
      _renameInProgress = false;  // clear before our own re-render below
      if (save) {
        const newName = input.value.trim();
        try {
          const res = await fetch('/api/conversations/' + item.dataset.id + '/rename', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ session_id: item.dataset.sessionId, name: newName }),
          });
          const data = await res.json();
          // Update cached data so next render reflects the change
          const c = conversationsData.find(x => x.id === item.dataset.id);
          if (c) {
            c.display_name = newName || null;
            // User just renamed from the command center → always teal regardless of
            // storage path (jsonl write-through vs. side-car fallback).
            c.name_overridden = !!newName;
          }
          // Also patch archiveData so the next archive re-render (periodic
          // poll or filter input) doesn't clobber the rename by rebuilding
          // shaped rows from the stale archive cache. Without this, the
          // user sees the rename appear briefly, then snap back to the
          // first-message title on the next 10s tick.
          if (typeof archiveData !== 'undefined' && Array.isArray(archiveData)) {
            const ac = archiveData.find(x => x.session_id === item.dataset.sessionId);
            if (ac) {
              ac.display_name = newName || null;
              ac.name_overridden = !!newName;
            }
          }
          // Brief toast on the item title
          const toast = document.createElement('div');
          toast.style.cssText = 'position:fixed;bottom:20px;left:20px;background:var(--surface);border:1px solid var(--border);padding:8px 14px;border-radius:6px;font-size:12px;color:var(--text);z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.4);';
          if (data.method === 'jsonl') {
            toast.innerHTML = '<span style="color:var(--green)">\u2713</span> Saved to .jsonl &mdash; <code>claude --resume</code> will see it';
          } else if (data.method === 'sidecar' && data.live) {
            toast.innerHTML = '<span style="color:var(--orange)">!</span> Session is currently running &mdash; use <code>/rename</code> inside claude for a persistent rename';
          } else {
            toast.innerHTML = '<span style="color:var(--accent)">i</span> Saved to side-car only (jsonl missing)';
          }
          document.body.appendChild(toast);
          setTimeout(() => toast.remove(), 3500);
        } catch (err) { /* swallow */ }
      }
      // Re-render
      renderSidebar(filterConversations($convSearch.value));
    }
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(true); }
      else if (e.key === 'Escape') { e.preventDefault(); commit(false); }
    });
    input.addEventListener('blur', () => commit(true));
  }

  // Return the active conversation view element for the active pane.
  // For single-pane mode this is `$conversationsView` (the original element,
  // re-parented into `.conv-pane[data-pane-id="p1"]` by Task 2). For split
  // mode each pane has its own `.conversations-view` inside it; we look
  // it up via the active pane's data-pane-id attribute.
  function getConvViewForPane(pid) {
    const pane = document.querySelector(`.conv-pane[data-pane-id="${pid}"]`);
    return pane ? pane.querySelector('.conversations-view') : null;
  }
  function getConvView() {
    return getConvViewForPane(activePaneId()) || $conversationsView;
  }
  function getConvInputBarForPane(pid) {
    const pane = document.querySelector(`.conv-pane[data-pane-id="${pid}"]`);
    return pane ? pane.querySelector('.conv-input-bar') : null;
  }

  // Build a fresh `.conv-pane` element for paneId, cloning the chrome of
  // pane "p1" so styling / wiring stays in lockstep. Called only when
  // splitting from one pane to two.
  function buildPaneElement(paneId) {
    const tmpl = document.querySelector('.conv-pane[data-pane-id="p1"]');
    const clone = tmpl.cloneNode(true);
    clone.setAttribute('data-pane-id', paneId);
    // Strip every id in the clone — HTML mandates id uniqueness, and any
    // future code that does getElementById('convInput') etc. would resolve
    // to p1's element, never the clone's. Task 6 will re-find the cloned
    // chrome elements via class/tag selectors scoped to the clone.
    clone.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
    // Replace the transcript with a Loading… empty state; the dropped
    // conversation will be loaded by selectConversation(id, paneId)
    // immediately after attach.
    const view = clone.querySelector('.conversations-view');
    if (view) {
      view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Loading…</div>';
    }
    // Hide the cloned workspace/usage strip — the pill renderers key off
    // the singular #convInputContext id (which only p1 keeps), so the
    // strip in cloned panes would render as empty space. Hiding it keeps
    // the pane chrome clean. Re-engaging per-pane workspace pills is a
    // potential follow-up; not required for v1.
    const ctxBar = clone.querySelector('.conv-input-context');
    if (ctxBar) ctxBar.style.display = 'none';
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
    const input = clone.querySelector('input[type="text"]');
    if (sendBtn) {
      sendBtn.addEventListener('click', (ev) => {
        ev.preventDefault();
        sendToTerminal(paneId);
      });
    }
    if (input) {
      input.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter' && !ev.shiftKey) {
          ev.preventDefault();
          sendToTerminal(paneId);
        }
      });
    }
    return clone;
  }

  // Toggle the split layout between single, vertical, horizontal.
  // Re-mounts panes inside `#convSplit` and updates orientation.
  function renderSplitLayout() {
    const $split = document.getElementById('convSplit');
    if (!$split) return;
    if (!splitState.orientation || splitState.panes.length < 2) {
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
      return;
    }
    $split.setAttribute('data-orientation', splitState.orientation);
    // Ensure both panes exist in the DOM in order.
    splitState.panes.forEach((p, idx) => {
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
    const p0 = $split.querySelector(`.conv-pane[data-pane-id="${splitState.panes[0].id}"]`);
    const p1 = $split.querySelector(`.conv-pane[data-pane-id="${splitState.panes[1].id}"]`);
    $split.append(p0, divider, p1);
    // Apply ratio.
    p0.style.flex = `${splitState.ratio} 1 0`;
    p1.style.flex = `${1 - splitState.ratio} 1 0`;
    // Mark the active pane.
    $split.querySelectorAll('.conv-pane').forEach(el => {
      el.classList.toggle('is-active', el.getAttribute('data-pane-id') === activePaneId());
    });
    attachAllPaneDropZones();
  }

  function attachDividerDrag(divider) {
    let dragging = false;
    let startPos = 0;
    let startRatio = 0.5;
    let containerSize = 0;
    let isVertical = true;

    divider.addEventListener('pointerdown', (ev) => {
      const $split = document.getElementById('convSplit');
      if (!$split) return;
      isVertical = $split.getAttribute('data-orientation') === 'vertical';
      containerSize = isVertical ? $split.clientWidth : $split.clientHeight;
      if (containerSize <= 0) return;
      dragging = true;
      startPos = isVertical ? ev.clientX : ev.clientY;
      startRatio = splitState.ratio;
      divider.setPointerCapture(ev.pointerId);
      ev.preventDefault();
    });
    divider.addEventListener('pointermove', (ev) => {
      if (!dragging) return;
      const cur = isVertical ? ev.clientX : ev.clientY;
      const delta = (cur - startPos) / containerSize;
      let next = startRatio + delta;
      next = Math.max(0.15, Math.min(0.85, next));
      splitState.ratio = next;
      const p0el = document.querySelector(`.conv-pane[data-pane-id="${splitState.panes[0].id}"]`);
      const p1el = document.querySelector(`.conv-pane[data-pane-id="${splitState.panes[1].id}"]`);
      if (p0el) p0el.style.flex = `${next} 1 0`;
      if (p1el) p1el.style.flex = `${1 - next} 1 0`;
    });
    divider.addEventListener('pointerup', (ev) => {
      dragging = false;
      try { divider.releasePointerCapture(ev.pointerId); } catch (e) {}
    });
  }

  // Returns true if the active drag carries a conversation card payload
  // (sidebar conv-item or kanban-card). Some browsers restrict
  // dataTransfer reads during dragenter/dragover; fall back to checking
  // dataTransfer.types for the payload key set by the source handlers.
  function dragHasConversationPayload(ev) {
    const types = (ev.dataTransfer && ev.dataTransfer.types) || [];
    return Array.from(types).some(t => t === 'text/plain');
  }

  // Read the conversation id out of the drop event. Both .conv-item drag
  // and .kanban-card drag set 'text/plain' to a comma-joined id list; we
  // take the first id. (Multi-select drag from kanban → split is not in
  // scope; the first id is the lead card.)
  function readConvIdFromDrop(ev) {
    const raw = ev.dataTransfer ? ev.dataTransfer.getData('text/plain') : '';
    if (!raw) return null;
    const first = String(raw).split(',')[0].trim();
    return first || null;
  }

  function attachDropZones(paneEl) {
    if (!paneEl || paneEl._dropZonesAttached) return;
    paneEl._dropZonesAttached = true;

    const overlay = document.createElement('div');
    overlay.className = 'conv-pane-drop-overlay';
    overlay.innerHTML = `
      <div class="drop-zone right"  data-zone="right">Open on the right</div>
      <div class="drop-zone bottom" data-zone="bottom">Open on the bottom</div>
    `;
    paneEl.appendChild(overlay);

    // Reject drops outright when a 2-pane split is already filled. The
    // overlay never activates, the pane shows no drop affordance, and
    // dragenter/over/leave/drop short-circuit to the pane's children.
    function splitIsFull() {
      return splitState.orientation && splitState.panes.length >= 2;
    }
    function viewportTooNarrow() {
      return window.innerWidth < 900;
    }

    // dragenter fires on every child element entry; track depth so the
    // overlay doesn't flicker when the cursor crosses internal nodes.
    let depth = 0;

    // `dragend` fires on the drag source after any outcome (drop, invalid
    // drop, ESC cancel). It's the canonical "drag is over" signal — use
    // it to reset depth defensively so an ESC-cancelled drag (which can
    // leave unbalanced enters) doesn't desync the next drag's overlay.
    window.addEventListener('dragend', () => {
      depth = 0;
      overlay.classList.remove('active');
      overlay.querySelectorAll('.drop-zone').forEach(z => z.classList.remove('over'));
    });

    paneEl.addEventListener('dragenter', (ev) => {
      if (!dragHasConversationPayload(ev)) return;
      if (splitIsFull() || viewportTooNarrow()) return;
      depth += 1;
      overlay.classList.add('active');
      ev.preventDefault();
    });
    paneEl.addEventListener('dragleave', (ev) => {
      if (depth === 0) return;
      depth -= 1;
      if (depth === 0) {
        overlay.classList.remove('active');
        overlay.querySelectorAll('.drop-zone').forEach(z => z.classList.remove('over'));
      }
    });
    paneEl.addEventListener('dragover', (ev) => {
      if (!overlay.classList.contains('active')) return;
      ev.preventDefault();          // required to enable drop
      // Must match the source's effectAllowed ('move' on conv-item and
      // kanban-card dragstart). A mismatch causes the browser to cancel
      // the drop silently — `drop` never fires.
      ev.dataTransfer.dropEffect = 'move';
    });

    overlay.querySelectorAll('.drop-zone').forEach(zone => {
      zone.addEventListener('dragenter', () => zone.classList.add('over'));
      zone.addEventListener('dragleave', () => zone.classList.remove('over'));
      zone.addEventListener('dragover', (ev) => { ev.preventDefault(); ev.dataTransfer.dropEffect = 'move'; });
      zone.addEventListener('drop', (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        depth = 0;
        overlay.classList.remove('active');
        zone.classList.remove('over');
        const convId = readConvIdFromDrop(ev);
        const targetPaneId = paneEl.getAttribute('data-pane-id');
        const orientation = zone.getAttribute('data-zone') === 'right' ? 'vertical' : 'horizontal';
        if (!convId) return;
        // If split is already full, caller shouldn't have shown the overlay,
        // but reject defensively.
        if (splitState.orientation && splitState.panes.length >= 2) return;
        // Same-conv guard: if convId is already open in any pane,
        // show a toast instead of silently returning.
        if (splitState.panes.some(p => p.conversationId === convId)) {
          showConvToast('Conversation is already open');
          return;
        }
        openConversationInPane(convId, targetPaneId, orientation);
      });
    });
    // Also reset on drop outside any zone.
    paneEl.addEventListener('drop', () => {
      depth = 0;
      overlay.classList.remove('active');
      overlay.querySelectorAll('.drop-zone').forEach(z => z.classList.remove('over'));
    });
  }

  // Wire drop zones on every existing pane after each layout change.
  function attachAllPaneDropZones() {
    document.querySelectorAll('.conv-pane').forEach(attachDropZones);
  }

  // Click anywhere inside a pane to mark it active (drives composer
  // routing via the shim, and the sidebar `.active` highlight).
  document.addEventListener('click', (ev) => {
    // Don't activate the pane on close-button clicks — the close handler
    // is about to destroy the pane anyway, and activating it first causes
    // a flicker (sidebar highlight briefly chases the doomed conv id).
    if (ev.target && ev.target.closest && ev.target.closest('[data-role="pane-close"]')) return;
    const pane = ev.target.closest && ev.target.closest('.conv-pane');
    if (!pane) return;
    const pid = pane.getAttribute('data-pane-id');
    const idx = paneIndexByPaneId(pid);
    if (idx < 0 || idx === splitState.activeIndex) return;
    splitState.activeIndex = idx;
    document.querySelectorAll('.conv-pane').forEach(el => {
      el.classList.toggle('is-active', el.getAttribute('data-pane-id') === pid);
    });
    // Sidebar highlight follows the new active pane. Mirrors the inline
    // toggle in selectConversation (~line 6906-6917) but reads the conv
    // id from the active pane (via the shim) instead of taking a param.
    const activeConvId = currentConversation;
    if ($convList) {
      $convList.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', el.dataset.id === activeConvId);
      });
    }
    if ($kanbanBoard) {
      $kanbanBoard.querySelectorAll('.kanban-card').forEach(el => {
        el.classList.toggle('active', el.dataset.id === activeConvId);
      });
    }
    if ($kanbanBoardSplit) {
      $kanbanBoardSplit.querySelectorAll('.kanban-card').forEach(el => {
        el.classList.toggle('active', el.dataset.id === activeConvId);
      });
    }
  }, true);

  // Open `convId` in a new pane, splitting the existing pane in the
  // requested orientation. Used by the drop handler. No-op if the same
  // conv is already open in the current pane (avoids a duplicate
  // SSE stream and a confusing UX).
  async function openConversationInPane(convId, targetPaneId, orientation) {
    if (!convId) return;
    if (splitState.orientation && splitState.panes.length >= 2) {
      // Split is already full — caller should not have invoked us, but
      // we guard anyway.
      return;
    }
    if (splitState.panes.length === 1 && splitState.panes[0].conversationId === convId) {
      // Same conversation as the only existing pane — no-op (visible
      // tooltip handled by the caller's UX in Task 8).
      return;
    }
    const newPane = _newPaneState('p2');
    splitState.orientation = orientation;
    splitState.panes.push(newPane);
    renderSplitLayout();          // creates the DOM for p2
    attachAllPaneDropZones();     // wire its drop overlay
    // Make p2 active and load the conversation in it.
    const newIdx = splitState.panes.length - 1;
    splitState.activeIndex = newIdx;
    await selectConversation(convId, newPane.id);
  }

  function closePane(paneId) {
    if (splitState.panes.length < 2) return; // can't close the only pane
    const idx = paneIndexByPaneId(paneId);
    if (idx < 0) return;
    const pane = splitState.panes[idx];
    // Tear down SSE.
    if (pane.eventSource) {
      try { pane.eventSource.close(); } catch (e) {}
      pane.eventSource = null;
    }
    // Remove the DOM element.
    const el = document.querySelector(`.conv-pane[data-pane-id="${paneId}"]`);
    if (el) el.remove();
    // Splice state and collapse.
    splitState.panes.splice(idx, 1);
    splitState.orientation = null;
    splitState.activeIndex = 0; // survivor is now the only pane
    renderSplitLayout();
    // Re-render sidebar highlight for the survivor pane (mirrors the
    // click-pane-active handler's three-block pattern).
    const activeConvId = currentConversation;
    if ($convList) {
      $convList.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', el.dataset.id === activeConvId);
      });
    }
    if ($kanbanBoard) {
      $kanbanBoard.querySelectorAll('.kanban-card').forEach(el => {
        el.classList.toggle('active', el.dataset.id === activeConvId);
      });
    }
    if ($kanbanBoardSplit) {
      $kanbanBoardSplit.querySelectorAll('.kanban-card').forEach(el => {
        el.classList.toggle('active', el.dataset.id === activeConvId);
      });
    }
  }

  // Show a 2-second floating message anchored to the bottom-center of the viewport.
  // Used when a drop is rejected because the conv is already open.
  let _convToastTimer = null;
  function showConvToast(msg) {
    let el = document.getElementById('convToast');
    if (!el) {
      el = document.createElement('div');
      el.id = 'convToast';
      el.className = 'conv-toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.add('visible');
    if (_convToastTimer) clearTimeout(_convToastTimer);
    _convToastTimer = setTimeout(() => el.classList.remove('visible'), 2000);
  }

  // Below ~900px the split layout doesn't fit. Collapse to single-pane
  // (active pane wins). Tear down the inactive pane's SSE. When the
  // viewport grows back, the user can re-split via drag.
  function handleViewportResize() {
    if (window.innerWidth >= 900) return;
    if (!splitState.orientation || splitState.panes.length < 2) return;
    const survivor = splitState.panes[splitState.activeIndex];
    const losers = splitState.panes.filter((_, i) => i !== splitState.activeIndex);
    losers.forEach(p => {
      if (p.eventSource) { try { p.eventSource.close(); } catch (e) {} }
      const el = document.querySelector(`.conv-pane[data-pane-id="${p.id}"]`);
      if (el && p.id !== 'p1') el.remove();
    });
    if (survivor.id !== 'p1') {
      // Move survivor into p1's slot (same logic as p1-close in Task 7).
      // Use selectConversation to ensure proper state setup (Task 7 fix).
      const survivorConvId = survivor.conversationId;
      if (survivor.eventSource) { try { survivor.eventSource.close(); } catch (e) {} }
      const survivorEl = document.querySelector(`.conv-pane[data-pane-id="${survivor.id}"]`);
      if (survivorEl) survivorEl.remove();
      if (splitState.panes[0].eventSource) {
        try { splitState.panes[0].eventSource.close(); } catch (e) {}
        splitState.panes[0].eventSource = null;
      }
      splitState.panes.splice(1);
      splitState.orientation = null;
      splitState.activeIndex = 0;
      Object.assign(splitState.panes[0], _newPaneState('p1'));
      renderSplitLayout();
      if (survivorConvId) selectConversation(survivorConvId, 'p1');
    } else {
      // Survivor is already p1; just splice and re-render.
      splitState.panes.splice(1);
      splitState.orientation = null;
      splitState.activeIndex = 0;
      renderSplitLayout();
    }
  }
  window.addEventListener('resize', handleViewportResize);

  const STICKY_HEADER_HEIGHT_KEY = 'ccc-sticky-header-height';
  const STICKY_HEADER_MIN_PX = 90;

  function getStickyMaxPx($view) {
    const vh = ($view && $view.clientHeight) || window.innerHeight || 800;
    return Math.max(STICKY_HEADER_MIN_PX + 40, Math.floor(vh * 0.6));
  }

  function applyStickyHeaderHeight(sticky, $view, h) {
    if (!sticky) return;
    const maxPx = getStickyMaxPx($view);
    const clamped = Math.max(STICKY_HEADER_MIN_PX, Math.min(maxPx, h));
    sticky.style.height = clamped + 'px';
    sticky.classList.add('is-resized');
  }

  function resetStickyHeaderHeight(sticky) {
    if (!sticky) return;
    sticky.style.height = '';
    sticky.classList.remove('is-resized');
    try { localStorage.removeItem(STICKY_HEADER_HEIGHT_KEY); } catch (_) {}
  }

  function attachStickyHeaderResize(sticky, $view) {
    if (!sticky || sticky._resizeAttached) return;
    sticky._resizeAttached = true;
    const handle = sticky.querySelector('[data-sticky-resize]');
    if (!handle) return;

    const stored = parseInt(localStorage.getItem(STICKY_HEADER_HEIGHT_KEY) || '', 10);
    if (!isNaN(stored) && stored > 0) {
      applyStickyHeaderHeight(sticky, $view, stored);
    }

    let startY = 0;
    let startH = 0;
    let activePointerId = null;

    handle.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      activePointerId = e.pointerId;
      startY = e.clientY;
      startH = sticky.getBoundingClientRect().height;
      handle.classList.add('is-dragging');
      try { handle.setPointerCapture(e.pointerId); } catch (err) { /* no-op */ }
    });

    handle.addEventListener('pointermove', (e) => {
      if (activePointerId !== e.pointerId) return;
      const dy = e.clientY - startY;
      applyStickyHeaderHeight(sticky, $view, startH + dy);
    });

    const endDrag = (e) => {
      if (activePointerId !== e.pointerId) return;
      handle.classList.remove('is-dragging');
      try { handle.releasePointerCapture(e.pointerId); } catch (err) { /* no-op */ }
      activePointerId = null;
      const finalH = sticky.getBoundingClientRect().height;
      try { localStorage.setItem(STICKY_HEADER_HEIGHT_KEY, String(Math.round(finalH))); } catch (err) { /* no-op */ }
    };
    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);
    // Double-click resets to auto-size (content-based height).
    handle.addEventListener('dblclick', (e) => {
      e.preventDefault();
      e.stopPropagation();
      resetStickyHeaderHeight(sticky);
    });
    // Title attribute for discoverability.
    handle.title = 'Drag to resize · double-click to reset';
  }

  async function renderIssueInConvPane(issueNum, repoPath, rowId) {
    const $view = getConvView();
    stopConvStream();
    const concreteRepo = repoPath || repoPathForIssueNumber(issueNum);
    currentConversation = rowId || 'backlog-issue-' + issueNum;
    // Drop any prior session so the bottom input bar routes to "spawn for
    // this issue" rather than injecting into the previously-viewed session.
    setCurrentSession(null, null, null, false, null);
    updateInputBar();
    $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Loading issue #' + issueNum + '...</div>';
    if (!concreteRepo) {
      $view.innerHTML = '<div class="empty-state" style="padding:40px;color:var(--red);">Pick a repo to load issue #' + escapeHtml(String(issueNum)) + '.</div>';
      return;
    }
    try {
      const res = await fetch(repoUrl('/api/issues/' + issueNum + '/details', concreteRepo));
      const data = await res.json();
      if (!data.ok) {
        $view.innerHTML = '<div class="empty-state" style="padding:40px;color:var(--red);">Failed to load issue #' + issueNum + ': ' + escapeHtml(data.error || 'unknown') + '</div>';
        return;
      }
      const issue = data.issue || {};
      // Self-heal: if the fresh GH state disagrees with what the cached card says,
      // patch the in-memory card and re-render so it routes to the right column
      // (verified/archived) without waiting for the 5-min server cache to expire.
      try {
        const card = conversationsData.find(x => x.id === currentConversation)
          || conversationsData.find(x => x.id === 'backlog-issue-' + issueNum);
        if (card) {
          const freshState = (issue.state || '').toUpperCase();
          const freshReason = (issue.stateReason || '').toUpperCase();
          const freshLabels = (issue.labels || []).map(l => l.name).filter(Boolean);
          const stateChanged = freshState && card.issue_state !== freshState;
          const reasonChanged = card.issue_state_reason !== freshReason;
          const labelsChanged = JSON.stringify(card.issue_labels || []) !== JSON.stringify(freshLabels);
          if (stateChanged || reasonChanged || labelsChanged) {
            card.issue_state = freshState;
            card.issue_state_reason = freshReason;
            card.issue_labels = freshLabels;
            renderSidebar(conversationsData);
          }
        }
      } catch (_) { /* render is a courtesy — never block detail render on failure */ }
      const labels = (issue.labels || []).map(l => '<span style="font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(139,148,158,0.2);color:var(--text-muted);margin-right:6px;">' + escapeHtml(l.name || '') + '</span>').join('');
      const created = issue.createdAt ? new Date(issue.createdAt).toLocaleString() : '';
      const url = issue.url || '';
      let html = '<div style="padding:20px;max-width:900px;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px;">';
      const issueTitle = stripGhIssueProjectTag(issue.title || '(no title)');
      html += '<div><h1 style="margin:0 0 6px;font-size:20px;">' + escapeHtml(issueTitle) + ' <span style="color:var(--text-muted);font-weight:400;">#' + escapeHtml(String(issueNum)) + '</span></h1>';
      html += '<div style="font-size:12px;color:var(--text-muted);">' + escapeHtml((issue.author && issue.author.login) || '') + ' &middot; ' + escapeHtml(created) + ' &middot; <span style="color:' + (issue.state === 'OPEN' ? 'var(--green)' : 'var(--text-muted)') + ';">' + escapeHtml(issue.state || '') + '</span></div></div>';
      if (url) html += '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener" style="font-size:12px;color:var(--accent);">Open on GitHub &#x2197;</a>';
      html += '</div>';
      if (labels) html += '<div style="margin-bottom:16px;">' + labels + '</div>';
      if ((issue.state || '').toUpperCase() === 'OPEN') {
        html += '<div id="issueCloseActions" data-issue-num="' + escapeHtml(String(issueNum)) + '" style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;">';
        html += '<button data-close-reason="completed" style="padding:6px 12px;font-size:12px;border-radius:6px;border:1px solid rgba(63,185,80,0.4);background:rgba(63,185,80,0.1);color:var(--green);cursor:pointer;font-weight:600;">Close as completed</button>';
        html += '<button data-close-reason="not planned" style="padding:6px 12px;font-size:12px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-muted);cursor:pointer;">Close as not planned</button>';
        html += '<button data-close-reason="duplicate" style="padding:6px 12px;font-size:12px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-muted);cursor:pointer;">Close as duplicate</button>';
        html += '<span data-close-status style="font-size:12px;color:var(--text-muted);align-self:center;"></span>';
        html += '</div>';
      }
      html += '<div class="assistant-text" style="font-size:14px;line-height:1.55;">' + renderIssueMarkdown(issue.body || '') + '</div>';
      const comments = issue.comments || [];
      html += '<div style="margin:24px 0 0;padding-top:16px;border-top:1px solid var(--border);">';
      html += '<div style="font-size:12px;font-weight:600;text-transform:uppercase;color:var(--text-muted);margin-bottom:8px;">All comments (' + comments.length + ')</div>';
      if (comments.length) {
        html += '<div style="display:flex;flex-direction:column;gap:10px;">';
        for (const cm of comments) {
          const who = (cm.author && cm.author.login) || '';
          const when = cm.createdAt ? new Date(cm.createdAt).toLocaleString() : '';
          html += '<div style="padding:12px;border:1px solid var(--border);border-radius:6px;">';
          html += '<div style="font-size:12px;color:var(--text-muted);margin-bottom:6px;">' + escapeHtml(who) + ' &middot; ' + escapeHtml(when) + '</div>';
          html += '<div class="assistant-text" style="font-size:13px;line-height:1.5;">' + renderIssueMarkdown(cm.body || '') + '</div>';
          html += '</div>';
        }
        html += '</div>';
      } else {
        html += '<div style="font-size:13px;color:var(--text-muted);">No comments yet.</div>';
      }
      html += '</div>';
      html += '</div>';
      $view.innerHTML = html;
      $view.scrollTop = 0;
      wireIssueCloseButtons($view, issueNum, concreteRepo);
    } catch (e) {
      $view.innerHTML = '<div class="empty-state" style="padding:40px;color:var(--red);">Failed to load issue: ' + escapeHtml(String(e && e.message || e)) + '</div>';
    }
  }

  function wireIssueCloseButtons($view, issueNum, repoPath) {
    const container = $view.querySelector('#issueCloseActions');
    if (!container) return;
    const status = container.querySelector('[data-close-status]');
    container.querySelectorAll('button[data-close-reason]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const reason = btn.dataset.closeReason;
        const body = { reason };
        if (reason === 'duplicate') {
          const ans = window.prompt('Duplicate of which issue number? (e.g. 42)');
          if (!ans) return;
          body.duplicate_of = ans.replace(/[^0-9]/g, '');
          if (!body.duplicate_of) { status.textContent = 'invalid number'; return; }
        }
        const orig = btn.textContent;
        container.querySelectorAll('button').forEach(b => b.disabled = true);
        btn.textContent = 'Closing...';
        status.textContent = '';
        try {
          const res = await fetch('/api/issues/' + issueNum + '/close', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(withRepoPath(body, repoPath)),
          });
          const data = await res.json();
          if (data.ok) {
            status.textContent = 'closed as ' + reason + ' ✓';
            status.style.color = 'var(--green)';
            // Find the next backlog card before refreshing
            const currentCard = document.querySelector('.kanban-card[data-id="backlog-issue-' + issueNum + '"]');
            let nextCard = currentCard && currentCard.nextElementSibling;
            // Skip non-card elements (e.g. "show all" links)
            while (nextCard && !nextCard.classList.contains('kanban-card')) nextCard = nextCard.nextElementSibling;
            // Fall back to previous card if no next
            if (!nextCard && currentCard) {
              nextCard = currentCard.previousElementSibling;
              while (nextCard && !nextCard.classList.contains('kanban-card')) nextCard = nextCard.previousElementSibling;
            }
            const nextIssueId = nextCard && nextCard.dataset.id;
            const nextIssueNum = nextIssueId && nextIssueId.startsWith('backlog-issue-') ? nextIssueId.replace('backlog-issue-', '') : null;
            // Refresh card list so closed issue drops from backlog
            setTimeout(() => refreshConversationList(), 600);
            // Navigate to next backlog issue (or re-render current in closed state)
            setTimeout(() => {
              if (nextIssueNum) {
                renderIssueInConvPane(nextIssueNum, repoPath);
              } else {
                renderIssueInConvPane(issueNum, repoPath, currentConversation);
              }
            }, 800);
          } else {
            status.textContent = 'Failed: ' + (data.error || 'unknown');
            status.style.color = 'var(--red)';
            btn.textContent = orig;
            container.querySelectorAll('button').forEach(b => b.disabled = false);
          }
        } catch (e) {
          status.textContent = 'error: ' + (e && e.message || e);
          status.style.color = 'var(--red)';
          btn.textContent = orig;
          container.querySelectorAll('button').forEach(b => b.disabled = false);
        }
      });
    });
  }

  // GitHub markdown tends to include image refs like ![alt](url) — let images render inline.
  function renderIssueMarkdown(md) {
    if (!md) return '<em style="color:var(--text-muted);">(no description)</em>';
    // Handle images first (before escape)
    const imgs = [];
    md = md.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, url) => {
      imgs.push({ alt, url });
      return '\u0000IMG' + (imgs.length - 1) + '\u0000';
    });
    let html = renderMarkdown(md);
    html = html.replace(/\u0000IMG(\d+)\u0000/g, (m, idx) => {
      const { alt, url } = imgs[+idx];
      return '<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(alt) + '" style="max-width:100%;border-radius:6px;margin:8px 0;" loading="lazy">';
    });
    return html;
  }

  let conversationPaneLoading = false;
  let conversationPaneLoadToken = 0;

  function scheduleSessionMetadataFetches(convId, sid) {
    if (!sid) return;
    setTimeout(() => {
      if (currentConversation !== convId) return;
      fetchSessionTimeline(sid);
      fetchSessionWorkspace(sid);
      fetchSessionUsage(sid);
    }, 150);
  }

  async function selectConversation(id, paneId) {
    paneId = paneId || activePaneId();
    const paneIdx = paneIndexByPaneId(paneId);
    const pane = paneByPaneId(paneId);
    if (!pane) return;
    const selectedConv = (conversationsData || []).find(x => x.id === id) || {};
    const source = sessionSourceByConv[id] || selectedConv.source || 'interactive';
    // Make this pane active so the existing globals (which proxy through
    // splitState.activeIndex) target the right pane while we run.
    splitState.activeIndex = paneIdx;
    // Stop any existing SSE stream or pollers
    stopConvStream(paneId);
    stopSpawnStream();
    stopCodexLogPoller();
    mobileShowForCurrentMode();
    currentConversation = id;
    // Remember which card was last opened so we can re-open it on the
    // next page load. Reads happen in loadConversationList once the list
    // is populated; misses (id no longer present) just fall through.
    try { if (id) localStorage.setItem('ccc-last-conv', id); } catch (_) {}
    convLastLine = 0;
    _firstUserMsgRendered = false;
    _dynamicAskState = null;  // sticky-header scroll tracker — repopulated when the new sticky is built
    _currentToolGroup = null;
    _currentToolCount = 0;
    _pendingSends = [];  // drop any optimistic sends from the previous conv
    const selectedRow = conversationsData.find(x => x.id === id) || null;
    if (selectedRow && selectedRow.source === 'backlog' && selectedRow.issue_number) {
      await renderIssueInConvPane(selectedRow.issue_number, rowRepoPath(selectedRow), selectedRow.id);
      return;
    }
    const $view = getConvView();
    $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Loading...</div>';
    const loadToken = ++conversationPaneLoadToken;
    conversationPaneLoading = true;
    const finishConversationPaneLoad = () => {
      if (conversationPaneLoadToken === loadToken) conversationPaneLoading = false;
    };
    // Update active state in sidebar (list view)
    $convList.querySelectorAll('.conv-item').forEach(el => {
      el.classList.toggle('active', el.dataset.id === id);
    });
    // Update active state in kanban views (sidebar + split)
    $kanbanBoard.querySelectorAll('.kanban-card').forEach(el => {
      el.classList.toggle('active', el.dataset.id === id);
    });
    if ($kanbanBoardSplit) {
      $kanbanBoardSplit.querySelectorAll('.kanban-card').forEach(el => {
        el.classList.toggle('active', el.dataset.id === id);
      });
    }
    // Update split panel session ID display
    if ($cpSessionId) {
      const sid = sessionIdByConv[id] || '';
      setCopyableSessionId($cpSessionId, sid);
    }
    const paneEl = document.querySelector(`.conv-pane[data-pane-id="${paneId}"]`);
    if (paneEl) {
      paneEl.classList.toggle('is-codex-session', source === 'codex');
      paneEl.classList.toggle('is-gemini-session', source === 'gemini');
    }
    if (source === 'backlog') {
      setCurrentSession(null, null, null, false, null);
    } else {
      setCurrentSession(
        source,
        sessionIdByConv[id],
        sessionCwdByConv[id],
        sessionCwdExistsByConv[id],
        sessionSpawnPidByConv[id],
        rowRepoPath(selectedConv) || selectedRepoPath()
      );
    }
    // Update split panel input bar visibility
    updateSplitInputBar();
    // Update split panel toolbar buttons
    updateSplitToolbar();

    try {
      if (source === 'pkood') {
        stopPkoodTailPoller();
        const agentId = id.replace(/^pkood-/, '');
        if ($pkoodKillBtn) {
          $pkoodKillBtn.style.display = '';
          $pkoodKillBtn.onclick = async () => {
            if (!confirm('Kill pkood agent "' + agentId + '"?')) return;
            try {
              const res = await fetch('/api/pkood/kill', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ agent_id: agentId }),
              });
              const data = await res.json();
              if (data.ok) {
                $pkoodKillBtn.textContent = 'Killed';
                setTimeout(() => { $pkoodKillBtn.textContent = 'Kill'; refreshConversationList(); }, 1500);
              }
            } catch (err) {}
          };
        }
        await loadPkoodTail(agentId);
        pkoodTailPoller = setInterval(() => loadPkoodTail(agentId), 2000);
      } else {
        stopPkoodTailPoller();
        if ($pkoodKillBtn) $pkoodKillBtn.style.display = 'none';
        await fetchConversationEvents(paneId);
        if (source !== 'backlog') startConvStream(paneId);
        // Block-level streaming from the spawn log — only succeeds if the
        // backend finds a CCC-spawned headless process for this session.
        // No-op for externally launched, IDE-launched, or pkood sessions.
        const sid = sessionIdByConv[id] || '';
        if (sid && source !== 'codex' && source !== 'backlog') startSpawnStream(sid);
      }
    } finally {
      finishConversationPaneLoad();
      if (source !== 'backlog') {
        const timelineSid = (selectedRow || selectedConv || {}).session_id || id;
        scheduleSessionMetadataFetches(id, timelineSid);
      }
    }
  }

  // ── Split panel input bar ──
  function updateSplitInputBar() {
    if (!$convPanelInput) return;
    const isPkood = currentSession.source === 'pkood';
    const isCodex = currentSession.source === 'codex';
    const isGemini = currentSession.source === 'gemini';
    const live = liveStatus.live && liveStatus.tty;
    const hasSession = !!currentSession.id;
    if (hasSession && kanbanView) {
      $convPanelInput.classList.add('visible');
      if ($cpTtyLabel) $cpTtyLabel.textContent = isPkood ? 'pkood' : (isCodex ? (liveStatus.tty || 'codex') : (isGemini ? (liveStatus.tty || 'gemini') : (liveStatus.tty || (live ? '' : 'offline'))));
      if ($cpInput) {
        if (isPkood) $cpInput.placeholder = 'Send to pkood agent...';
        else if (isCodex) $cpInput.placeholder = live ? 'Send to Codex terminal...' : 'Resume Codex and send...';
        else if (isGemini) $cpInput.placeholder = live ? 'Send to Gemini terminal...' : 'Resume Gemini and send...';
        else if (live) $cpInput.placeholder = 'Send to terminal...';
        else $cpInput.placeholder = 'Send to terminal (offline)...';
      }
    } else {
      $convPanelInput.classList.remove('visible');
    }
    // Enable/disable the send button based on new session state.
    if ($cpInput && $cpInput.__cpRefresh) $cpInput.__cpRefresh();
  }
  // ── Split panel toolbar buttons ──
  function updateSplitToolbar() {
    const $cpJumpBtn = document.getElementById('cpJumpBtn');
    const $cpLaunchSplit = document.getElementById('cpLaunchSplit');
    const $cpLaunchBtn = document.getElementById('cpLaunchBtn');
    const $cpLaunchChoiceMenu = document.getElementById('cpLaunchChoiceMenu');
    const $cpKillBtn = document.getElementById('cpKillBtn');
    if (!$cpJumpBtn) return;
    const sid = currentSession.id;
    const live = liveStatus.live;
    const isPkood = currentSession.source === 'pkood';
    // Jump is still useful for live terminals, while Launch now owns the
    // terminal/app resume destinations as a split button.
    const hasLiveTty = live && !!liveStatus.tty && !isPkood;
    $cpJumpBtn.style.display = hasLiveTty ? '' : 'none';
    $cpJumpBtn.onclick = async () => {
      if (!liveStatus.tty) return;
      try {
        await fetch('/api/jump-terminal', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ tty: liveStatus.tty, terminal_app: liveStatus.terminalApp }),
        });
      } catch (_) {}
    };
    if ($cpLaunchSplit) $cpLaunchSplit.style.display = (sid && !isPkood) ? 'inline-flex' : 'none';
    if ($cpLaunchBtn) {
      $cpLaunchBtn.style.display = '';
      $cpLaunchBtn.title = 'Launch in terminal';
      $cpLaunchBtn.innerHTML = '<span>&#43;</span> Launch';
      $cpLaunchBtn.disabled = false;
    }
    renderLaunchChoiceMenu($cpLaunchChoiceMenu);
    $cpLaunchBtn.onclick = async () => {
      if (!sid) return;
      await launchTerminal({ currentTarget: $cpLaunchBtn });
      // Refresh live status on a quick schedule so the button flips to Jump
      // without waiting for the 5s poll. Claude cold-start can take ~3-5s.
      setTimeout(refreshLiveStatus, 700);
      setTimeout(refreshLiveStatus, 2000);
      setTimeout(refreshLiveStatus, 4000);
      setTimeout(refreshLiveStatus, 7000);
    };
    // Kill: show for pkood
    $cpKillBtn.style.display = isPkood ? '' : 'none';
  }

  function startConvStream(paneId) {
    if (paneId) {
      const idx = paneIndexByPaneId(paneId);
      if (idx >= 0) splitState.activeIndex = idx;
    }
    stopConvStream(paneId);
    if (!currentConversation) return;
    // Snapshot the pane and conv id at stream-start time so the SSE
    // event-handler closures always target THIS pane's stream even if
    // the user switches the active pane between SSE events.
    const streamPaneId = activePaneId();
    const streamConvId = currentConversation;
    const url = '/api/conversations/' + streamConvId + '/stream?after=' + convLastLine;
    convEventSource = new EventSource(url);
    convEventSource.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.events && data.events.length > 0) {
          // Route into the pane that owns this stream, not the currently
          // active pane (which may have shifted to the other pane).
          const streamPane = paneByPaneId(streamPaneId);
          // Stale-stream guard: bail out if the pane was closed OR if its
          // conversation has changed since the stream started. Without this,
          // a queued message from the old conversation can write into the
          // new conversation's transcript when the user switches convs in
          // the same pane.
          if (!streamPane || streamPane.conversationId !== streamConvId) return;
          const savedIdx = splitState.activeIndex;
          splitState.activeIndex = paneIndexByPaneId(streamPaneId);
          try {
            // Invalidate the FFC cache so new file references picked up during
            // this streaming turn are reflected in the pill count on the next
            // ffcRefreshForCurrent() call (triggered by renderConversationEvents).
            ffcInvalidate(currentConversation);
            renderConversationEvents(data.events, streamPaneId);
            convLastLine = data.last_line;
          } finally {
            // Always restore activeIndex, even if renderConversationEvents
            // throws — otherwise the shim stays pointed at streamPaneId
            // and corrupts every subsequent shim read until another action
            // shifts it.
            splitState.activeIndex = savedIdx;
          }
        }
      } catch (err) {}
    };
    convEventSource.onerror = () => {
      stopConvStream(streamPaneId);
      setTimeout(() => startConvStream(streamPaneId), 2000);
    };
  }

  function stopConvStream(paneId) {
    if (paneId) {
      const idx = paneIndexByPaneId(paneId);
      if (idx >= 0) splitState.activeIndex = idx;
    }
    if (convEventSource) {
      convEventSource.close();
      convEventSource = null;
    }
  }

  // ── Spawn-log streaming ──
  // Live, block-level event stream from a CCC-spawned headless session's
  // stdout. Granularity is one `assistant` event per content block
  // (thinking/text/tool_use), not per token — claude `-p` doesn't surface
  // token deltas. Still meaningfully faster than the JSONL transcript,
  // which is end-of-turn only. The streaming bubble is transient: it
  // gets cleared when `result` lands (turn over) or when the user
  // switches conversations.
  let spawnEventSource = null;
  let _streamingBubble = null;       // DOM node for the in-flight bubble
  let _streamingMsgId = null;        // current message_id we're appending into
  let _spawnLiveSid = null;          // session_id whose spawn we're tailing

  // Diagnostic: HH:MM:SS.mmm for render-time comparison between stream and tail.
  function nowStamp() {
    const d = new Date();
    const p2 = (n) => (n < 10 ? '0' + n : '' + n);
    const p3 = (n) => (n < 10 ? '00' + n : n < 100 ? '0' + n : '' + n);
    return p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds()) + '.' + p3(d.getMilliseconds());
  }

  // ----------------------------------------------------------------
  // Files-from-conversation: per-conversation cache + pill rendering.
  // The pill lives in .conv-sticky-header and only renders when the
  // count is > 0. Click → openFfcModal (Task 8).
  // ----------------------------------------------------------------
  const _ffcCache = new Map(); // conversation_id -> {count, truncated, groups}

  async function ffcFetch(convId) {
    if (!convId || convId === '__new__' || convId.startsWith('backlog-') || convId.startsWith('pkood-') || convId.startsWith('issue-')) {
      return null;
    }
    if (_ffcCache.has(convId)) {
      return _ffcCache.get(convId);
    }
    try {
      const r = await fetch('/api/conversations/' + encodeURIComponent(convId) + '/files');
      if (!r.ok) {
        _ffcCache.set(convId, {count: 0, truncated: false, groups: {}});
        return _ffcCache.get(convId);
      }
      const data = await r.json();
      _ffcCache.set(convId, data);
      return data;
    } catch (e) {
      // Network / parse failure — silent. Pill just stays hidden.
      _ffcCache.set(convId, {count: 0, truncated: false, groups: {}});
      return _ffcCache.get(convId);
    }
  }

  function ffcInvalidate(convId) {
    if (convId) _ffcCache.delete(convId);
  }

  function ffcEnsurePill(stickyEl, data) {
    if (!stickyEl) return;
    let pill = stickyEl.querySelector('.ffc-pill');
    const hasFiles = data && data.count > 0;
    if (!hasFiles) {
      if (pill) pill.hidden = true;
      return;
    }
    if (!pill) {
      pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'ffc-pill';
      pill.title = 'Files mentioned in this conversation';
      pill.innerHTML = '📎 Files <span class="ffc-pill-count"></span>';
      pill.addEventListener('click', () => {
        const cur = currentConversation;
        const cached = cur ? _ffcCache.get(cur) : null;
        if (cached && typeof openFfcModal === 'function') openFfcModal(cached);
      });
      const host = stickyEl.querySelector('.csh-col-activity') || stickyEl;
      host.insertBefore(pill, host.firstChild || null);
    }
    pill.hidden = false;
    pill.querySelector('.ffc-pill-count').textContent = '(' + data.count + ')';
  }

  async function ffcRefreshForCurrent() {
    const cur = currentConversation;
    if (!cur) return;
    const data = await ffcFetch(cur);
    // The conversation may have switched while we were fetching. Bail
    // if so — the new conversation's renderer will trigger its own.
    if (currentConversation !== cur) return;
    const sticky = document.querySelector('.conversations-view .conv-sticky-header');
    ffcEnsurePill(sticky, data);
  }

  const FFC_CATEGORY_ORDER = [
    {key: 'images',        label: 'Images',        icon: '📷'},
    {key: 'pdfs',          label: 'PDFs',          icon: '📕'},
    {key: 'docs',          label: 'Docs',          icon: '📄'},
    {key: 'presentations', label: 'Presentations', icon: '📊'},
    {key: 'videos',        label: 'Videos',        icon: '🎬'},
    {key: 'markdown',      label: 'Markdown',      icon: '📝'},
    {key: 'html',          label: 'HTML',          icon: '🌐'},
  ];

  function ffcEscapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function ffcRenderRow(row, icon) {
    const div = document.createElement('div');
    div.className = 'ffc-row';

    const labelEl = document.createElement('div');
    labelEl.className = 'ffc-row-label';
    labelEl.innerHTML = '<span class="ffc-row-icon">' + icon + '</span>' +
                       '<span>' + ffcEscapeHtml(row.label) + '</span>';
    if (row.kind === 'url') {
      const a = document.createElement('a');
      a.href = row.target;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.style.color = 'inherit';
      a.style.textDecoration = 'none';
      a.appendChild(labelEl);
      div.appendChild(a);
    } else {
      div.appendChild(labelEl);
      div.addEventListener('click', async (e) => {
        if (e.target.classList.contains('ffc-row-target')) return; // don't double-trigger on path-copy
        try {
          const r = await fetch('/api/reveal-file', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: row.target, session_id: currentSession && currentSession.id}),
          });
          if (!r.ok) {
            const j = await r.json().catch(() => ({error: 'open failed'}));
            ffcShowRowToast(div, j.error || ('HTTP ' + r.status));
          }
        } catch (err) {
          ffcShowRowToast(div, 'network error');
        }
      });
    }

    const tgt = document.createElement('div');
    tgt.className = 'ffc-row-target';
    tgt.textContent = row.target;
    tgt.title = 'Click to copy';
    tgt.addEventListener('click', (e) => {
      e.stopPropagation();
      navigator.clipboard.writeText(row.target).then(() => {
        const orig = tgt.textContent;
        tgt.textContent = 'copied';
        setTimeout(() => { tgt.textContent = orig; }, 900);
      }).catch(() => {});
    });
    div.appendChild(tgt);

    return div;
  }

  function ffcShowRowToast(rowEl, msg) {
    let toast = rowEl.querySelector('.ffc-row-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.className = 'ffc-row-toast';
      rowEl.appendChild(toast);
    }
    toast.textContent = msg;
    setTimeout(() => {
      if (toast.parentNode === rowEl) rowEl.removeChild(toast);
    }, 3000);
  }

  function openFfcModal(data) {
    const overlay = document.getElementById('ffcOverlay');
    const body    = document.getElementById('ffcBody');
    const footer  = document.getElementById('ffcFooter');
    if (!overlay || !body || !footer) return;
    body.innerHTML = '';

    if (!data || !data.count) {
      const empty = document.createElement('div');
      empty.className = 'ffc-empty';
      empty.textContent = 'No files mentioned in this conversation.';
      body.appendChild(empty);
    } else {
      for (const cat of FFC_CATEGORY_ORDER) {
        const rows = (data.groups || {})[cat.key];
        if (!rows || !rows.length) continue;
        const section = document.createElement('div');
        section.className = 'ffc-section';
        const title = document.createElement('div');
        title.className = 'ffc-section-title';
        title.textContent = cat.label + ' (' + rows.length + ')';
        section.appendChild(title);
        for (const row of rows) {
          section.appendChild(ffcRenderRow(row, cat.icon));
        }
        body.appendChild(section);
      }
    }

    if (data && data.truncated) {
      footer.hidden = false;
      footer.textContent = 'Showing first 500 — conversation contains more.';
    } else {
      footer.hidden = true;
      footer.textContent = '';
    }

    overlay.hidden = false;
    document.addEventListener('keydown', _ffcEscHandler);
  }

  function closeFfcModal() {
    const overlay = document.getElementById('ffcOverlay');
    if (overlay) overlay.hidden = true;
    document.removeEventListener('keydown', _ffcEscHandler);
  }

  function _ffcEscHandler(e) {
    if (e.key === 'Escape') closeFfcModal();
  }

  // Wire close handlers — defensive against DOMContentLoaded already fired.
  function _ffcWireCloseHandlers() {
    const backdrop = document.getElementById('ffcBackdrop');
    const close    = document.getElementById('ffcClose');
    if (backdrop) backdrop.addEventListener('click', closeFfcModal);
    if (close)    close.addEventListener('click', closeFfcModal);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _ffcWireCloseHandlers);
  } else {
    _ffcWireCloseHandlers();
  }

  // Parse a JSONL event's ISO timestamp ("ts" field, set server-side from
  // the original event's `timestamp`) into the same HH:MM:SS.mmm format
  // nowStamp() produces, but in the user's local timezone. Falls back to
  // null when the input is missing or unparseable so callers can decide
  // whether to use nowStamp() or hide the prefix entirely.
  function eventStamp(isoTs) {
    if (!isoTs) return null;
    const d = new Date(isoTs);
    if (isNaN(d.getTime())) return null;
    const p2 = (n) => (n < 10 ? '0' + n : '' + n);
    const p3 = (n) => (n < 10 ? '00' + n : n < 100 ? '0' + n : '' + n);
    return p2(d.getHours()) + ':' + p2(d.getMinutes()) + ':' + p2(d.getSeconds()) + '.' + p3(d.getMilliseconds());
  }

  function setLiveBadgeVisible(visible) {
    const a = document.getElementById('liveBadgeConv');
    const b = document.getElementById('cpLiveBadge');
    if (a) a.style.display = visible ? '' : 'none';
    if (b) b.style.display = visible ? '' : 'none';
  }

  async function startSpawnStream(sid) {
    stopSpawnStream();
    if (!sid) return;
    let info;
    try {
      const res = await fetch('/api/session/' + encodeURIComponent(sid) + '/spawn-info');
      info = await res.json();
    } catch (_) { return; }
    // Race guard: user may have switched away while we were fetching.
    if (currentSession.id !== sid) return;
    if (!info || !info.has_log || !info.alive) return;
    setLiveBadgeVisible(true);
    _spawnLiveSid = sid;
    const url = '/api/session/' + encodeURIComponent(sid) + '/spawn-stream';
    spawnEventSource = new EventSource(url);
    spawnEventSource.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data && Array.isArray(data.events)) handleSpawnEvents(data.events);
      } catch (_) {}
    };
    spawnEventSource.onerror = () => {
      // Subprocess exited or server hiccup — drop the bubble + badge.
      // No auto-retry: a finished headless session has no more output to stream.
      stopSpawnStream();
    };
  }

  function stopSpawnStream() {
    if (spawnEventSource) {
      spawnEventSource.close();
      spawnEventSource = null;
    }
    _spawnLiveSid = null;
    setLiveBadgeVisible(false);
    clearStreamingBubble();
  }

  let _streamingBubbleLingerTimer = null;

  function clearStreamingBubble(opts) {
    if (_streamingBubbleLingerTimer) {
      clearTimeout(_streamingBubbleLingerTimer);
      _streamingBubbleLingerTimer = null;
    }
    const linger = opts && typeof opts.lingerMs === 'number' ? opts.lingerMs : 0;
    const doRemove = () => {
      if (_streamingBubble && _streamingBubble.parentNode) {
        _streamingBubble.parentNode.removeChild(_streamingBubble);
      }
      _streamingBubble = null;
      _streamingMsgId = null;
      _streamingBubbleLingerTimer = null;
    };
    if (linger > 0 && _streamingBubble) {
      // Mark the bubble as "settled" so the dashed border + pulsing dot
      // stop animating during the linger — visually distinct from the
      // active streaming state.
      _streamingBubble.classList.add('stream-bubble-settled');
      _streamingBubbleLingerTimer = setTimeout(doRemove, linger);
    } else {
      doRemove();
    }
  }

  function ensureStreamingBubble(msgId) {
    const $view = getConvView();
    if (!$view) return null;
    // Hand-off short-circuit: if the JSONL renderer already painted this
    // message (formatted, with markdown / tool detail / result output), skip
    // the low-fidelity bubble entirely.
    if (msgId) {
      const escId = (window.CSS && CSS.escape) ? CSS.escape(msgId) : msgId;
      if ($view.querySelector('.event.assistant[data-msg-id="' + escId + '"]')) {
        return null;
      }
    }
    // New message id → reset (the JSONL renderer is responsible for
    // persisting whatever the previous message produced).
    if (_streamingBubble && _streamingMsgId && msgId && _streamingMsgId !== msgId) {
      clearStreamingBubble();
    }
    if (!_streamingBubble) {
      const node = document.createElement('div');
      node.className = 'stream-bubble';
      if (msgId) node.dataset.msgId = msgId;
      node.innerHTML =
        '<div class="stream-bubble-header">'
        + '<span class="live-badge-dot"></span>'
        + '<span>streaming</span>'
        + '</div>'
        + '<div class="stream-bubble-blocks"></div>';
      $view.appendChild(node);
      _streamingBubble = node;
      _streamingMsgId = msgId || null;
    } else if (msgId && !_streamingBubble.dataset.msgId) {
      // First spawn event sometimes lacks a message_id; backfill so the
      // JSONL hand-off can find this bubble when it arrives.
      _streamingBubble.dataset.msgId = msgId;
    }
    // Re-anchor to the bottom in case JSONL events were appended after us.
    if (_streamingBubble.parentNode === $view && _streamingBubble !== $view.lastElementChild) {
      $view.appendChild(_streamingBubble);
    }
    return _streamingBubble.querySelector('.stream-bubble-blocks');
  }

  function handleSpawnEvents(events) {
    if (!Array.isArray(events)) return;
    const $view = getConvView();
    const wasAtBottom = $view && ($view.scrollHeight - $view.scrollTop - $view.clientHeight) < 80;
    for (const ev of events) {
      if (!ev || typeof ev !== 'object') continue;
      if (ev.type === 'result') {
        // Turn finished. The JSONL hand-off (in renderConversationEvents)
        // is the primary trigger that drops the bubble; this is the
        // fallback when JSONL is slow or never publishes.
        clearStreamingBubble();
        continue;
      }
      if (ev.type !== 'assistant_block') continue;
      const slot = ensureStreamingBubble(ev.message_id);
      if (!slot) continue;
      _streamingMsgId = ev.message_id || _streamingMsgId;
      for (const b of (ev.blocks || [])) {
        if (b.type === 'text') {
          // Append-or-merge: consecutive text blocks for the same message
          // accumulate in one element so the bubble reads naturally.
          let last = slot.lastElementChild;
          if (last && last.classList.contains('stream-block-text')) {
            last.textContent += b.text || '';
          } else {
            const div = document.createElement('div');
            div.className = 'stream-block-text';
            div.dataset.renderTs = nowStamp();
            div.textContent = b.text || '';
            slot.appendChild(div);
          }
        } else if (b.type === 'tool_use') {
          const div = document.createElement('div');
          div.className = 'stream-block-tool';
          div.dataset.renderTs = nowStamp();
          const summary = b.summary ? ' — ' + b.summary : '';
          div.innerHTML = '<span>⚙</span> <span class="stream-tool-name">'
            + escapeHtml(b.name || 'tool') + '</span>'
            + '<span style="opacity:0.8;">' + escapeHtml(summary) + '</span>';
          slot.appendChild(div);
        } else if (b.type === 'thinking') {
          // Don't bother rendering empty thinking blocks — they appear
          // as the first block of every assistant turn and are noisy.
          if (!slot.querySelector('.stream-block-thinking')) {
            const div = document.createElement('div');
            div.className = 'stream-block-thinking';
            div.dataset.renderTs = nowStamp();
            div.textContent = '(thinking…)';
            slot.appendChild(div);
          }
        }
      }
    }
    if (wasAtBottom && $view) $view.scrollTop = $view.scrollHeight;
  }

  function stopPkoodTailPoller() {
    if (pkoodTailPoller) {
      clearInterval(pkoodTailPoller);
      pkoodTailPoller = null;
    }
  }

  async function loadPkoodTail(agentId) {
    const $view = getConvView();
    try {
      const res = await fetch('/api/pkood/tail?id=' + encodeURIComponent(agentId));
      const data = await res.json();
      if (data.ok) {
        const atBottom = $view.scrollHeight - $view.scrollTop - $view.clientHeight < 40;
        $view.innerHTML = '<pre class="pkood-tail-output">' + escapeHtml(data.output || '(no output yet)') + '</pre>';
        if (atBottom) $view.scrollTop = $view.scrollHeight;
      } else {
        $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Failed to load agent output: ' + escapeHtml(data.error || 'unknown') + '</div>';
      }
    } catch (err) {
      // Keep existing content on transient fetch errors
    }
  }

  function stopCodexLogPoller() {
    if (codexLogPoller) {
      clearInterval(codexLogPoller);
      codexLogPoller = null;
    }
  }

  // Render a codex spawn log into the right pane while the durable Codex
  // thread row is still materializing. Once /api/sessions returns a real
  // codex card, normal conversation rendering takes over.
  async function loadCodexLog(card) {
    if (!card || typeof card.spawn_pid !== 'number') {
      // Pre-swap (toolbar Run): spawn_pid is still 'tmp-...'. Show a
      // placeholder until the spawn POST returns and re-selects this card.
      const $view = getConvView();
      const engine = card && card.source === 'gemini' ? 'gemini' : 'codex';
      $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Spawning ' + engine + ' run…</div>';
      return;
    }
    const $view = getConvView();
    const engine = card.source === 'gemini' ? 'gemini' : 'codex';
    try {
      const res = await fetch('/api/sessions/spawned/' + encodeURIComponent(card.spawn_pid) + '/log?_=' + Date.now());
      const data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
      if (!data.ok) {
        $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Failed to load ' + engine + ' log: ' + escapeHtml(data.error || ('HTTP ' + res.status)) + '</div>';
        stopCodexLogPoller();
        return;
      }
      const atBottom = $view.scrollHeight - $view.scrollTop - $view.clientHeight < 40;
      $view.innerHTML = (data.engine === 'gemini' || engine === 'gemini')
        ? renderGeminiLogHtml(data)
        : renderCodexLogHtml(data);
      if (atBottom) $view.scrollTop = $view.scrollHeight;
      // Process exited — stop polling. Final render already in place.
      if (!data.running) stopCodexLogPoller();
    } catch (err) {
      // Transient — keep existing content; the next tick will retry.
    }
  }

  function isBenignCodexStderrLine(line) {
    const text = (line || '').trim();
    return text === 'Reading additional input from stdin...'
      || text === 'Reading prompt from stdin...'
      || /\bWARN codex_core_plugins::manifest: ignoring interface\.defaultPrompt:/.test(text)
      || /\bERROR codex_core::session: failed to record rollout items: thread [0-9a-f-]+ not found$/.test(text);
  }

  // Build the right-pane HTML for a codex run from /api/sessions/spawned/<pid>/log.
  function renderCodexLogHtml(data) {
    const lines = (data.text || '').split('\n').filter(Boolean);
    const messages = [];
    let usage = null;
    let threadId = '';
    const stderrLines = [];
    for (const line of lines) {
      try {
        const ev = JSON.parse(line);
        if (ev.type === 'thread.started' && ev.thread_id) threadId = ev.thread_id;
        else if (ev.type === 'item.completed' && ev.item) {
          if (ev.item.type === 'agent_message' && ev.item.text) {
            messages.push({ role: 'assistant', text: ev.item.text });
          } else if (ev.item.type) {
            // Tool calls / other items — render a compact summary line.
            messages.push({ role: 'system', text: '[' + ev.item.type + ']' });
          }
        } else if (ev.type === 'turn.completed' && ev.usage) {
          usage = ev.usage;
        }
      } catch (_) {
        // Non-JSON line (codex CLI stderr leaks). Show in a small footer.
        if (!isBenignCodexStderrLine(line)) stderrLines.push(line);
      }
    }
    const status = data.running
      ? '<span class="codex-status running" style="color:var(--green);">running</span>'
      : ('<span class="codex-status finished" style="color:var(--text-muted);">finished' + (data.exit_code != null ? ' (exit ' + data.exit_code + ')' : '') + '</span>');
    const usageHtml = usage
      ? ('<div class="codex-usage" style="margin-top:12px;padding:8px 10px;background:rgba(139,148,158,0.08);border-radius:6px;font-size:12px;color:var(--text-muted);font-family:var(--font-mono,monospace);">'
        + 'in ' + (usage.input_tokens || 0)
        + (usage.cached_input_tokens ? ' (' + usage.cached_input_tokens + ' cached)' : '')
        + ' · out ' + (usage.output_tokens || 0)
        + (usage.reasoning_output_tokens ? ' (' + usage.reasoning_output_tokens + ' reasoning)' : '')
        + '</div>')
      : '';
    const msgsHtml = messages.length
      ? messages.map(m => {
          if (m.role === 'assistant') {
            return '<div class="codex-msg assistant" style="margin-bottom:14px;padding:10px 12px;background:rgba(63,185,80,0.06);border-left:2px solid var(--green);border-radius:4px;white-space:pre-wrap;line-height:1.55;">' + escapeHtml(m.text) + '</div>';
          }
          return '<div class="codex-msg system" style="margin-bottom:6px;font-size:12px;color:var(--text-muted);font-family:var(--font-mono,monospace);">' + escapeHtml(m.text) + '</div>';
        }).join('')
      : '<div class="empty-state" style="height:auto;padding:24px;color:var(--text-muted);">codex is thinking…</div>';
    const stderrHtml = stderrLines.length
      ? ('<details style="margin-top:14px;font-size:12px;color:var(--text-muted);"><summary style="cursor:pointer;">codex stderr (' + stderrLines.length + ' line' + (stderrLines.length === 1 ? '' : 's') + ')</summary>'
        + '<pre style="margin:8px 0 0;padding:8px;background:rgba(139,148,158,0.08);border-radius:4px;white-space:pre-wrap;font-family:var(--font-mono,monospace);">' + escapeHtml(stderrLines.join('\n')) + '</pre>'
        + '</details>')
      : '';
    const headerHtml = '<div class="codex-header" style="display:flex;align-items:center;gap:10px;padding-bottom:10px;margin-bottom:14px;border-bottom:1px solid var(--border);font-size:12px;color:var(--text-muted);">'
      + '<span class="source-badge codex" style="background:rgba(63,185,80,0.2);color:var(--green);padding:2px 8px;border-radius:10px;font-weight:600;">codex</span>'
      + status
      + (threadId ? '<span style="font-family:var(--font-mono,monospace);">thread ' + escapeHtml(threadId.slice(0, 8)) + '…</span>' : '')
      + '<span style="margin-left:auto;font-family:var(--font-mono,monospace);">pid ' + escapeHtml(String(data.pid)) + '</span>'
      + '</div>';
    return '<div class="codex-log" style="padding:16px 20px;">' + headerHtml + msgsHtml + usageHtml + stderrHtml + '</div>';
  }

  function renderGeminiLogHtml(data) {
    const lines = (data.text || '').split('\n').filter(Boolean);
    const messages = [];
    let usage = null;
    let sessionId = '';
    const stderrLines = [];
    for (const line of lines) {
      try {
        const ev = JSON.parse(line);
        if (ev.type === 'init' && ev.session_id) {
          sessionId = ev.session_id;
        } else if (ev.type === 'message' && ev.role === 'assistant' && ev.content) {
          const last = messages[messages.length - 1];
          if (last && last.role === 'assistant') last.text += ev.content;
          else messages.push({ role: 'assistant', text: ev.content });
        } else if (ev.type === 'tool_use') {
          const params = ev.parameters || {};
          const detail = params.description || params.command || '';
          messages.push({ role: 'system', text: '[' + (ev.tool_name || 'tool') + (detail ? ': ' + detail : '') + ']' });
        } else if (ev.type === 'tool_result') {
          messages.push({ role: 'system', text: '[' + (ev.status || 'tool result') + ']' });
        } else if (ev.type === 'result' && ev.stats) {
          usage = ev.stats;
        }
      } catch (_) {
        const text = (line || '').trim();
        if (text && !/^YOLO mode is enabled\./.test(text) && text !== 'MCP issues detected. Run /mcp list for status.') {
          stderrLines.push(line);
        }
      }
    }
    const status = data.running
      ? '<span class="codex-status running" style="color:var(--green);">running</span>'
      : ('<span class="codex-status finished" style="color:var(--text-muted);">finished' + (data.exit_code != null ? ' (exit ' + data.exit_code + ')' : '') + '</span>');
    const usageHtml = usage
      ? ('<div class="codex-usage" style="margin-top:12px;padding:8px 10px;background:rgba(139,148,158,0.08);border-radius:6px;font-size:12px;color:var(--text-muted);font-family:var(--font-mono,monospace);">'
        + 'in ' + (usage.input_tokens || usage.input || 0)
        + (usage.cached ? ' (' + usage.cached + ' cached)' : '')
        + ' · out ' + (usage.output_tokens || 0)
        + '</div>')
      : '';
    const msgsHtml = messages.length
      ? messages.map(m => {
          if (m.role === 'assistant') {
            return '<div class="codex-msg assistant" style="margin-bottom:14px;padding:10px 12px;background:rgba(122,162,255,0.07);border-left:2px solid #7aa2ff;border-radius:4px;white-space:pre-wrap;line-height:1.55;">' + escapeHtml(m.text) + '</div>';
          }
          return '<div class="codex-msg system" style="margin-bottom:6px;font-size:12px;color:var(--text-muted);font-family:var(--font-mono,monospace);">' + escapeHtml(m.text) + '</div>';
        }).join('')
      : '<div class="empty-state" style="height:auto;padding:24px;color:var(--text-muted);">gemini is thinking…</div>';
    const stderrHtml = stderrLines.length
      ? ('<details style="margin-top:14px;font-size:12px;color:var(--text-muted);"><summary style="cursor:pointer;">gemini stderr (' + stderrLines.length + ' line' + (stderrLines.length === 1 ? '' : 's') + ')</summary>'
        + '<pre style="margin:8px 0 0;padding:8px;background:rgba(139,148,158,0.08);border-radius:4px;white-space:pre-wrap;font-family:var(--font-mono,monospace);">' + escapeHtml(stderrLines.join('\n')) + '</pre>'
        + '</details>')
      : '';
    const headerHtml = '<div class="codex-header" style="display:flex;align-items:center;gap:10px;padding-bottom:10px;margin-bottom:14px;border-bottom:1px solid var(--border);font-size:12px;color:var(--text-muted);">'
      + '<span class="source-badge gemini" style="background:rgba(122,162,255,0.16);color:#9bb7ff;padding:2px 8px;border-radius:10px;font-weight:600;">gemini</span>'
      + status
      + (sessionId ? '<span style="font-family:var(--font-mono,monospace);">session ' + escapeHtml(sessionId.slice(0, 8)) + '…</span>' : '')
      + '<span style="margin-left:auto;font-family:var(--font-mono,monospace);">pid ' + escapeHtml(String(data.pid)) + '</span>'
      + '</div>';
    return '<div class="codex-log gemini-log" style="padding:16px 20px;">' + headerHtml + msgsHtml + usageHtml + stderrHtml + '</div>';
  }

  async function fetchConversationEvents(paneId) {
    if (paneId) {
      const idx = paneIndexByPaneId(paneId);
      if (idx >= 0) splitState.activeIndex = idx;
    }
    const fetchPaneId = activePaneId();
    if (!currentConversation) return;
    const id = currentConversation;
    const $view = getConvViewForPane(fetchPaneId) || $conversationsView;
    // Backlog cards (open GH issues + TODO/PARKING/native-task) have no
    // session JSONL — /api/conversations/<id> returns 404. Render the
    // issue body directly from the card's already-loaded fields so the
    // detail pane isn't a permanent "Loading…".
    const backlogCard = (conversationsData || []).find(x => x.id === id && x.source === 'backlog');
    if (backlogCard || id.startsWith('backlog-')) {
      const c = backlogCard || (conversationsData || []).find(x => x.id === id);
      if (c) {
        $view.innerHTML = _renderBacklogDetail(c);
        return;
      }
    }
    // Codex placeholders use the spawn log only until the real ~/.codex
    // thread row appears in /api/sessions.
    if (id.startsWith('spawning-')) {
      const c = (conversationsData || []).find(x => x.id === id);
      if (c && (c.source === 'codex' || c.source === 'gemini')) {
        await loadCodexLog(c);
        stopCodexLogPoller();
        codexLogPoller = setInterval(() => {
          if (currentConversation !== id) { stopCodexLogPoller(); return; }
          loadCodexLog(c);
        }, 1500);
        return;
      }
    }
    try {
      const res = await fetch('/api/conversations/' + id + '?after=' + convLastLine);
      const data = await res.json();
      // Guard: if the pane's conv id shifted (e.g. user navigated away
      // while the fetch was in-flight), discard the stale response.
      const currentPane = paneByPaneId(fetchPaneId);
      if (!currentPane || currentPane.conversationId !== id) return;
      // Re-anchor activeIndex to fetchPaneId — the user may have clicked
      // another conv (in either pane) while this fetch was in-flight, shifting
      // splitState.activeIndex away. Mirror the savedIdx/try/finally pattern
      // used in startConvStream's onmessage handler.
      const savedIdx = splitState.activeIndex;
      splitState.activeIndex = paneIndexByPaneId(fetchPaneId);
      try {
        if (convLastLine === 0) {
          $view.innerHTML = '';
        }
        renderConversationEvents(data.events, fetchPaneId);
        convLastLine = data.last_line;
      } finally {
        splitState.activeIndex = savedIdx;
      }
      // Re-fetch session usage whenever new events landed. Usage rollups
      // arrive sporadically (in some session paths only the final
      // assistant event of each turn carries the `usage` block — many
      // delta events in between have none), so the initial selection-
      // time fetch can come back empty and the user sees no context %
      // until they reselect. Tying this to event arrival means the pill
      // updates as soon as the next turn lands.
      if (data.events && data.events.length > 0) {
        const sid = (conversationsData.find(x => x.id === id) || {}).session_id || id;
        fetchSessionUsage(sid);
      }
    } catch (err) {
      if (convLastLine === 0) {
        $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Failed to load conversation: ' + escapeHtml(err.message) + '</div>';
      }
    }
  }

  Object.defineProperty(window, '_firstUserMsgRendered', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].firstUserMsgRendered; },
    set(v) { splitState.panes[splitState.activeIndex].firstUserMsgRendered = v; },
  });

  // Render a read-only detail panel for backlog cards (GH issues, TODO.md
  // entries, PARKING_LOT items, native-task summaries). No session events
  // exist for these — only the card's own metadata + issue body.
  function _renderBacklogDetail(c) {
    const title = (c.backlog_type === 'github' || c.issue_number)
      ? stripGhIssueProjectTag(c.display_name || '')
      : (c.display_name || '');
    const body = c.first_message || '';
    const labels = (c.issue_labels || c.gh_labels || []).map(function(l) {
      return '<span class="conv-signal" style="margin-right:4px;background:rgba(139,148,158,0.15);color:var(--text-muted);">' + escapeHtml(l) + '</span>';
    }).join('');
    let stateChip = '';
    if (c.issue_state) {
      const reason = (c.issue_state_reason || '').toUpperCase();
      const isClosed = c.issue_state === 'CLOSED';
      const label = isClosed ? ('closed' + (reason ? ' · ' + reason.toLowerCase() : '')) : 'open';
      const bg = isClosed ? 'rgba(248,81,73,0.12)' : 'rgba(63,185,80,0.12)';
      const color = isClosed ? 'var(--red)' : 'var(--green)';
      stateChip = '<span class="conv-signal" style="background:' + bg + ';color:' + color + ';font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">' + escapeHtml(label) + '</span>';
    }
    let issueLink = '';
    if (c.issue_number) {
      if (c.issue_url) {
        issueLink = '<a href="' + escapeHtml(c.issue_url) + '" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:12px;margin-left:auto;">View on GitHub ↗</a>';
      } else {
        // Single-repo backlog cards still rely on the active repo slug
        // from /api/config. Cross-repo cards carry issue_url directly.
        const repo = (window._cccConfig && window._cccConfig.repo) || '';
        if (repo) {
          issueLink = '<a href="https://github.com/' + escapeHtml(repo) + '/issues/' + escapeHtml(String(c.issue_number)) + '" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:12px;margin-left:auto;">View on GitHub ↗</a>';
        }
      }
    }
    const created = c.issue_created_at
      ? '<div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Opened ' + escapeHtml(new Date(c.issue_created_at).toLocaleString()) + '</div>'
      : '';
    // Use the in-house renderMarkdown — handles headers, lists, tables,
    // inline code, fenced code blocks. Same renderer used for assistant
    // messages, so backlog body styling matches the rest of the app.
    const bodyHtml = body
      ? renderMarkdown(body)
      : '<em style="color:var(--text-muted);">(no body)</em>';
    return '<div class="backlog-detail" style="padding:24px 28px;max-width:780px;">'
      + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
      +   stateChip
      +   labels
      +   issueLink
      + '</div>'
      + '<h2 style="margin:0 0 4px;font-size:18px;line-height:1.35;">' + escapeHtml(title) + '</h2>'
      +   created
      + '<div style="margin-top:18px;line-height:1.55;color:var(--text);">' + bodyHtml + '</div>'
      + '</div>';
  }
  // Sticky-header dynamic ask tracker. The .conv-sticky-header normally
  // shows the first user message ("Original ask"). As the user scrolls past
  // later user messages, this state machine swaps the sticky's body to
  // whichever user message they've most recently scrolled past (label
  // flips to "Earlier ask"). Reset on conv switch; the scroll listener is
  // attached once per .conversations-view node (guarded by a property on
  // the node itself so we don't stack listeners across renders).
  let _dynamicAskState = null;
  function _dynAskItems() {
    const st = _dynamicAskState;
    if (!st) return [];
    return Array.from(st.view.querySelectorAll(':scope > .event.user_text'));
  }
  function _dynAskApply(idx, items) {
    const st = _dynamicAskState;
    if (!st || st.currentIdx === idx) return;
    // Unhide whichever later item was visually hidden during the previous
    // swap so it pops back into view as soon as the sticky moves on. Index 0
    // (the original ask) keeps `is-pinned-in-sticky` permanently — that
    // display:none hide is owned by the sticky-header init code, not by this
    // tracker, so we never touch it here.
    if (st.currentIdx > 0 && items[st.currentIdx]) {
      items[st.currentIdx].classList.remove('is-dynamic-pinned-in-sticky');
    }
    if (idx <= 0) {
      // No earlier ask scrolled past — clear the slot entirely so
      // `_updateStickyAskSlots` can collapse it and let the original
      // ask take the full left column.
      st.currentIdx = 0;
      if (st.earlierFirst) st.earlierFirst.innerHTML = '';
      _updateStickyAskSlots();
      return;
    }
    const item = items[idx];
    if (!item) return;
    const msgEl = item.querySelector('.user-msg');
    // Prefer data-raw-text (the original prose, captured before pasted-image
    // paths were swapped for <img> tags); fall back to textContent for
    // bubbles that pre-date the data-raw-text attr.
    const text = msgEl ? ((msgEl.dataset && msgEl.dataset.rawText) || msgEl.textContent || '').trim() : '';
    if (!text) {  // image-only message — fall back rather than going blank
      _dynAskApply(0, items);
      return;
    }
    // Original stays put, earlier-block shows the most-recent user message
    // that's scrolled behind the sticky. Hide the in-conv bubble visually
    // so the user doesn't see the same text twice, but keep its layout space
    // so scrollHeight does not change mid-scroll.
    st.currentIdx = idx;
    item.classList.add('is-dynamic-pinned-in-sticky');
    if (st.earlierFirst) st.earlierFirst.innerHTML = linkifyPastedImages(escapeHtml(text));
    _updateStickyAskSlots();
  }
  // Coordinator: decides where the .csh-ask-earlier block lives based on
  // whether (a) the earlier slot has any text and (b) the activity column
  // has any timeline rows. Four cases:
  //   - earlier empty + activity empty:  hide earlier, hide activity →
  //                                      original takes the full panel.
  //   - earlier empty + activity full:   hide earlier, show activity →
  //                                      original takes full left column.
  //   - earlier full  + activity full:   stack earlier under original
  //                                      (default unified-panel layout).
  //   - earlier full  + activity empty:  promote earlier into the activity
  //                                      column, replacing the empty
  //                                      timeline so we don't waste the
  //                                      right half of the panel.
  function _updateStickyAskSlots() {
    const sticky = document.querySelector('.conv-sticky-header');
    if (!sticky) return;
    const askCol = sticky.querySelector('.csh-col-ask');
    const actCol = sticky.querySelector('.csh-col-activity');
    const earlier = sticky.querySelector('.csh-ask-earlier');
    if (!askCol || !actCol || !earlier) return;
    const earlierFirst = earlier.querySelector('[data-earlier-first]');
    const earlierHasText = !!(earlierFirst && (earlierFirst.textContent || '').trim());
    const timeline = actCol.querySelector('[data-timeline]');
    const activityHasContent = !!(timeline && timeline.querySelector('.stl-row'));
    earlier.style.display = earlierHasText ? '' : 'none';
    if (earlierHasText && !activityHasContent) {
      if (earlier.parentNode !== actCol) actCol.appendChild(earlier);
      actCol.style.display = '';
    } else {
      if (earlier.parentNode !== askCol) askCol.appendChild(earlier);
      actCol.style.display = activityHasContent ? '' : 'none';
    }
  }
  function _dynAskUpdate() {
    const st = _dynamicAskState;
    if (!st) return;
    st.rafPending = false;
    const items = _dynAskItems();
    if (!items.length) return;
    // Use the original-ask sub-block's bottom as a stable threshold rather
    // than the full sticky bottom. When earlier-ask appears the sticky grows,
    // and naively measuring sticky.bottom cascades: pinning one bubble pushes
    // the next bubble behind the now-taller sticky, which pins it too, so the
    // user's just-typed message vanishes.
    const ref = st.sticky.querySelector('.csh-ask-original') || st.sticky;
    const threshold = ref.getBoundingClientRect().bottom;
    // The currently-pinned dynamic item remains in layout, but temporarily
    // clear the visual-hide class before measurement so any legacy state
    // from older renders cannot poison the bounding rect calculation.
    let prevPinned = null;
    if (st.currentIdx > 0 && items[st.currentIdx]) {
      prevPinned = items[st.currentIdx];
      prevPinned.classList.remove('is-dynamic-pinned-in-sticky');
    }
    let idx = 0;
    for (let i = 0; i < items.length; i++) {
      if (items[i].getBoundingClientRect().bottom <= threshold) idx = i;
      else break;
    }
    if (prevPinned && idx === st.currentIdx) {
      prevPinned.classList.add('is-dynamic-pinned-in-sticky');
    }
    _dynAskApply(idx, items);
  }
  function _dynAskSchedule() {
    const st = _dynamicAskState;
    if (!st || st.rafPending) return;
    st.rafPending = true;
    requestAnimationFrame(_dynAskUpdate);
  }
  // Tool-call grouping state — consecutive `.event.tool-only` events fuse
  // into one collapsed `.tool-call-group` with a "Ran N commands ▾" header,
  // matching Claude Desktop's chat pane. Reset whenever the conv view is
  // cleared (see _firstUserMsgRendered = false sites).
  // NB: these are shim-routed per-pane (see Object.defineProperty below) —
  // do NOT re-add `let` declarations here.
  // Optimistic pending sends: messages the user submitted via the input bar
  // that haven't yet appeared in the conversation jsonl. Each entry is
  // { text, element } — we strip pending styling (or remove the div) when
  // the real user_text event lands via the render loop.
  Object.defineProperty(window, '_pendingSends', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].pendingSends; },
    set(v) { splitState.panes[splitState.activeIndex].pendingSends = v; },
  });
  Object.defineProperty(window, '_currentToolGroup', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].currentToolGroup; },
    set(v) { splitState.panes[splitState.activeIndex].currentToolGroup = v; },
  });
  Object.defineProperty(window, '_currentToolCount', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].currentToolCount; },
    set(v) { splitState.panes[splitState.activeIndex].currentToolCount = v; },
  });
  Object.defineProperty(window, 'currentSession', {
    configurable: true,
    get() { return splitState.panes[splitState.activeIndex].currentSession; },
    set(v) { splitState.panes[splitState.activeIndex].currentSession = v; },
  });
  const _normSend = (s) => eventTextString(s).replace(/\s+/g, ' ').trim();

  // Render a TODO.md backlog card in the conv pane. No session exists yet —
  // just show the TODO text and the two spawn buttons so the user can start one.
  function renderTodoInConvPane(backlogId) {
    const $view = getConvView();
    stopConvStream();
    currentConversation = backlogId;
    const c = conversationsData.find(x => x.id === backlogId) || {};
    const title = stripTitle(c.display_name || '(todo)').replace(/-/g, ' ').trim();
    const body = c.first_message || '';
    const sourceLabel = backlogId.startsWith('backlog-parking-') ? 'PARKING_LOT.md' : 'TODO.md';
    let html = '<div style="padding:20px;max-width:900px;">';
    html += '<div style="font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">' + sourceLabel + '</div>';
    html += '<h1 style="margin:0 0 12px;font-size:20px;line-height:1.3;">' + escapeHtml(title) + '</h1>';
    if (body) {
      html += '<div class="assistant-text" style="font-size:14px;line-height:1.55;white-space:pre-wrap;">' + escapeHtml(body) + '</div>';
    }
    html += '<div style="margin-top:20px;color:var(--text-muted);font-size:13px;">No conversation yet — tap <strong>Start session</strong> on the card to spawn one.</div>';
    html += '</div>';
    $view.innerHTML = html;
    $view.scrollTop = 0;
  }

  // Session-workspace panel — fetched per conversation, rendered in
  // the third column of the sticky header. Tells you whether the session
  // is editing in the shared clone or a worktree, what branch, ahead/
  // behind counts, and whether other sessions are sharing the same cwd.
  let _workspaceSessionId = null;
  let _workspaceData = null;
  function getInputContextSlot() {
    // Conversation transcripts can contain literal CCC HTML snippets with
    // ids like `convInputContext`; scope to pane chrome, not message content.
    return document.querySelector('#convSplit > .conv-pane[data-pane-id="p1"] > .conv-input-context[data-role="input-context"]');
  }
  async function fetchSessionWorkspace(sid) {
    _workspaceSessionId = sid;
    _workspaceData = null;
    // Clear the strip immediately so a stale workspace from the previous
    // session doesn't flash before the new fetch lands.
    const slot = getInputContextSlot();
    const wsSlot = slot && slot.querySelector('[data-workspace]');
    if (wsSlot) wsSlot.innerHTML = '';
    if (slot) slot.classList.remove('visible');
    if (!sid) return;
    try {
      const res = await fetch('/api/session/' + encodeURIComponent(sid) + '/workspace');
      const data = await res.json();
      if (_workspaceSessionId !== sid) return;
      _workspaceData = data;
      renderSessionWorkspaceIntoSticky();
    } catch (_) {}
  }

  function renderSessionWorkspaceIntoSticky() {
    // Target the input-context strip above the conv input bar; matches
    // Claude Desktop's pattern of "branch · base · diff · PR" right where
    // you type. Single row, no third column.
    const slot = getInputContextSlot();
    const wsSlot = slot && slot.querySelector('[data-workspace]');
    if (!slot || !wsSlot || !_workspaceData) return;
    const w = _workspaceData;
    if (!w.cwd) {
      wsSlot.innerHTML = '';
      // Don't hide the whole strip — usage pill might still want to show.
      slot.classList.toggle('visible', !!_usageData);
      return;
    }
    // Show ONE pill per session: where Claude's edits are actually
    // landing. Prefer the tool-call-inferred effective cwd (the truth
    // when subagents / `cd` move the session out of its launch dir);
    // fall back to the launch cwd when no inference applies. The
    // launch cwd is no longer surfaced as a separate pill — the strip
    // is supposed to answer "where does this session's Edit go?", not
    // "where was it started from?".
    const useEff = !!w.effective_cwd;
    const pillPath = useEff ? w.effective_cwd : w.cwd;
    const tilde = pillPath.replace(/^\/Users\/[^/]+/, '~');
    const pillBranch = useEff ? w.effective_branch : w.branch;
    const pillAhead = useEff ? w.effective_commits_ahead : w.commits_ahead;
    const pillBehind = useEff ? w.effective_commits_behind : w.commits_behind;
    const inferTitle = useEff
      ? ' (inferred from ' + (w.effective_path_count || 0) + '/' + (w.effective_total_paths || 0) + ' tool-call paths)'
      : '';
    const parts = [];
    let kindCls, kindLabel, kindTitle;
    if (useEff) {
      if (w.effective_kind === 'worktree') {
        kindCls = 'wp-kind-worktree'; kindLabel = 'worktree';
        kindTitle = 'git worktree' + inferTitle;
      } else {
        kindCls = 'wp-kind-clone'; kindLabel = 'shared clone';
        kindTitle = 'shared clone — main repo working tree' + inferTitle;
      }
    } else if (w.is_worktree) {
      kindCls = 'wp-kind-worktree'; kindLabel = 'worktree';
      kindTitle = 'worktree (not the shared clone)';
    } else if (w.is_repo) {
      kindCls = 'wp-kind-clone'; kindLabel = 'shared clone';
      kindTitle = 'shared clone — main repo working tree';
    } else if (w.exists) {
      kindCls = 'wp-kind-other'; kindLabel = 'not a git repo';
      kindTitle = "cwd exists but is not a git repo — Claude's git commands will fail unless it shells into a repo";
    } else {
      kindCls = 'wp-kind-other'; kindLabel = 'cwd missing';
      kindTitle = 'cwd does not exist on disk';
    }
    parts.push('<span class="wp-kind ' + kindCls + '" title="' + escapeHtml(kindTitle) + '">' + kindLabel + '</span>');
    if (pillBranch) {
      parts.push('<span class="wp-icon">⎇</span><span class="wp-branch">' + escapeHtml(pillBranch) + '</span>');
    }
    if (pillAhead != null || pillBehind != null) {
      const a = pillAhead || 0;
      const b = pillBehind || 0;
      if (a > 0) parts.push('<span class="wp-ahead">↑' + a + '</span>');
      if (b > 0) parts.push('<span class="wp-behind">↓' + b + '</span>');
      if (a === 0 && b === 0 && pillBranch) parts.push('<span class="wp-even">in sync</span>');
    }
    // Co-tenants only meaningful for the literal cwd; skip when we
    // moved to an inferred cwd via tool calls.
    if (!useEff) {
      const co = w.co_tenants || 0;
      if (co > 0) {
        parts.push('<span class="wp-cotenants" title="' + co + ' other live session(s) editing the same cwd">⚠ ' + co + ' other session' + (co === 1 ? '' : 's') + ' here</span>');
      }
    }
    parts.push('<span class="wp-path" title="' + escapeHtml(pillPath) + '">' + escapeHtml(tilde) + '</span>');

    // Sibling-worktrees pill removed — topbar Worktrees button is the
    // single entry point. Per-session repetition was just noise.

    wsSlot.innerHTML = parts.join(' ');
    slot.classList.add('visible');
  }

  // Shared renderer for the worktrees modal. Used by both the per-session
  // 🌿 pill (sibling worktrees of the active session's repo) and the
  // topbar Worktrees button (every worktree in the watched repo, with a
  // dirty marker for those carrying uncommitted changes).
  function _renderWorktreesModal({ worktrees, summaryItems, emptyText, orphanPrs }) {
    const $modal = document.getElementById('worktreesModal');
    const $summary = document.getElementById('worktreesSummary');
    const $list = document.getElementById('worktreesList');
    if (!$modal || !$summary || !$list) return;
    $summary.innerHTML = (summaryItems || []).join(' ');
    let html = '';
    if (!worktrees || worktrees.length === 0) {
      html += '<div class="wt-empty">' + escapeHtml(emptyText || 'No worktrees.') + '</div>';
    } else {
      const rows = worktrees.map(function (wt) {
        const tildePath = (wt.path || '').replace(/^\/Users\/[^/]+/, '~');
        const branch = wt.branch || '';
        const tags = [];
        if (wt.dirty) tags.push('<span class="wt-tag wt-tag-dirty" title="Has uncommitted changes">uncommitted</span>');
        if (wt.is_agent) tags.push('<span class="wt-tag wt-tag-agent">subagent</span>');
        else if (wt.locked) tags.push('<span class="wt-tag wt-tag-locked">locked</span>');
        if (wt.detached) tags.push('<span class="wt-tag wt-tag-detached">detached</span>');
        if (wt.pr && wt.pr.number) {
          const prUrl = wt.pr.url || '';
          const draftCls = wt.pr.isDraft ? ' wt-tag-pr-draft' : '';
          const prTitle = (wt.pr.title || '').trim();
          const tipBase = (wt.pr.isDraft ? 'Draft PR' : 'Open PR') + ' #' + wt.pr.number;
          const tip = prTitle ? tipBase + ' — ' + prTitle : tipBase;
          tags.push('<a class="wt-tag wt-tag-pr' + draftCls + '" href="' + escapeHtml(prUrl)
            + '" target="_blank" rel="noopener" title="' + escapeHtml(tip) + '">'
            + (wt.pr.isDraft ? 'draft ' : '') + 'PR #' + wt.pr.number + '</a>');
        }
        const branchHtml = branch
          ? '<span class="wt-row-branch"><span class="wt-icon">⎇</span>' + escapeHtml(branch) + '</span>'
          : '<span class="wt-row-branch" style="opacity:0.5;">—</span>';
        const reason = (wt.lock_reason || '').trim();
        const reasonHtml = (reason && !wt.is_agent)
          ? '<div class="wt-lock-reason">' + escapeHtml(reason) + '</div>'
          : '';
        let cls = 'wt-row';
        if (wt.is_agent) cls += ' wt-row-agent';
        if (wt.dirty) cls += ' wt-row-dirty';
        return '<div class="' + cls + '">'
          + '<span class="wt-row-path" title="' + escapeHtml(wt.path || '') + '">' + escapeHtml(tildePath) + '</span>'
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
          + '<a class="wt-pr-num" href="' + escapeHtml(pr.url || '') + '" target="_blank" rel="noopener">#'
          + pr.number + '</a>'
          + '<span class="wt-pr-title" title="' + escapeHtml(pr.title || '') + '">'
          + escapeHtml(pr.title || '') + '</span>'
          + '<span class="wt-pr-branch">⎇ ' + escapeHtml(pr.headRefName || '') + ' ' + draftLabel + '</span>'
          + '</div>';
      }).join('');
    }
    $list.innerHTML = html;
    $modal.classList.add('open');
  }

  // Per-session worktrees modal — opened by clicking the 🌿 pill. Reads
  // from _workspaceData (already fetched for the current session) so we
  // don't refetch on click.
  function openWorktreesModal() {
    const w = _workspaceData;
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
      items.push('<span style="margin-left:auto;opacity:0.7;">this session: ' + escapeHtml(here) + '</span>');
    }
    _renderWorktreesModal({
      worktrees: wts,
      summaryItems: items,
      emptyText: 'No sibling worktrees.',
    });
  }

  // Repo-wide worktrees modal — opened from the topbar "🌿 Worktrees"
  // button. Fetches /api/repo/worktrees on every click so the dirty/clean
  // state is fresh; the per-worktree `git status` calls there have a 2s
  // timeout each so a hung worktree can't block the modal.
  async function openRepoWorktreesModal() {
    const repoPath = requireSelectedRepo('Worktrees');
    if (!repoPath) return;
    _renderWorktreesModal({
      worktrees: [],
      summaryItems: ['<span style="opacity:0.7;">loading…</span>'],
      emptyText: 'Loading…',
    });
    let data;
    try {
      const res = await fetch(repoUrl('/api/repo/worktrees', repoPath));
      data = await res.json();
    } catch (_) { return; }
    const wts = (data && data.worktrees) || [];
    const orphanPrs = (data && data.orphan_prs) || [];
    const openPrCount = (data && data.open_prs_count) || 0;
    const items = ['<span><strong>' + wts.length + '</strong> worktree' + (wts.length === 1 ? '' : 's') + '</span>'];
    if (data && data.dirty_count) {
      items.push('<span style="color:var(--orange);"><strong>' + data.dirty_count + '</strong> uncommitted</span>');
    }
    if (data && data.agent_count) {
      items.push('<span><strong>' + data.agent_count + '</strong> subagent</span>');
    }
    if (openPrCount) {
      items.push('<span><strong>' + openPrCount + '</strong> open PR' + (openPrCount === 1 ? '' : 's') + '</span>');
    }
    _renderWorktreesModal({
      worktrees: wts,
      summaryItems: items,
      emptyText: 'No worktrees in this repo.',
      orphanPrs: orphanPrs,
    });
  }
  function closeWorktreesModal() {
    const $modal = document.getElementById('worktreesModal');
    if ($modal) $modal.classList.remove('open');
  }
  // Delegated click on the input-context strip — survives re-renders
  // of [data-workspace] without rebinding.
  (function () {
    const slot = getInputContextSlot();
    if (!slot) return;
    slot.addEventListener('click', function (ev) {
      const btn = ev.target && ev.target.closest && ev.target.closest('[data-action="open-worktrees"]');
      if (!btn) return;
      ev.preventDefault();
      openWorktreesModal();
    });
  })();
  document.addEventListener('keydown', function (e) {
    const $modal = document.getElementById('worktreesModal');
    if (e.key === 'Escape' && $modal && $modal.classList.contains('open')) {
      closeWorktreesModal();
    }
  });
  (function () {
    const $backdrop = document.getElementById('worktreesBackdrop');
    const $closeBtn = document.getElementById('worktreesCloseBtn');
    if ($backdrop) $backdrop.addEventListener('click', closeWorktreesModal);
    if ($closeBtn) $closeBtn.addEventListener('click', closeWorktreesModal);
    const $topbarBtn = document.getElementById('kptWorktreesBtn');
    if ($topbarBtn) $topbarBtn.addEventListener('click', openRepoWorktreesModal);
  })();

  // Slow poll of /api/repo/worktrees so the topbar Worktrees button can
  // flag subagent-spawned forks (locked agent worktrees that the user
  // may not realise exist). 60s cadence is plenty — subagent worktrees
  // accumulate on the timescale of multi-step orchestration tasks.
  async function refreshWorktreesBadge() {
    const $btn = document.getElementById('kptWorktreesBtn');
    if (!$btn) return;
    const repoPath = selectedRepoPath();
    if (!repoPath) {
      $btn.classList.remove('has-agent-worktrees');
      $btn.title = 'Pick a repo to show worktrees.';
      return;
    }
    try {
      const res = await fetch(repoUrl('/api/repo/worktrees', repoPath));
      const data = await res.json();
      const agentN = (data && data.agent_count) || 0;
      $btn.classList.toggle('has-agent-worktrees', agentN > 0);
      const baseTitle = 'Show all worktrees of this repo, with an indicator for those that have uncommitted changes.';
      $btn.title = agentN > 0
        ? baseTitle + ' (' + agentN + ' subagent worktree' + (agentN === 1 ? '' : 's') + ' currently active)'
        : baseTitle;
    } catch (_) { /* network blip — leave the badge state alone */ }
  }
  refreshWorktreesBadge();
  setInterval(refreshWorktreesBadge, 60000);

  // ── Usage stats overlay ────────────────────────────────────────────
  // Loads /api/stats and renders an Overview/Models view in a modal.
  // Cold first-load can take a few seconds for users with many transcripts;
  // every subsequent range switch is instant (server caches per-file aggs).
  (function() {
    const $modal = document.getElementById('statsModal');
    const $body = document.getElementById('statsBody');
    if (!$modal || !$body) return;

    let currentTab = 'overview';
    let currentRange = '30d';
    const cache = {};  // range -> stats payload

    function compactNum(n) {
      if (n == null) return '—';
      n = Number(n) || 0;
      if (n < 1000) return String(n);
      if (n < 10_000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
      if (n < 1_000_000) return Math.round(n / 1000) + 'k';
      if (n < 10_000_000) return (n / 1_000_000).toFixed(1).replace(/\.0$/, '') + 'M';
      if (n < 1_000_000_000) return Math.round(n / 1_000_000) + 'M';
      return (n / 1_000_000_000).toFixed(1).replace(/\.0$/, '') + 'B';
    }

    function escapeHtml(s) {
      return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function renderCards(s) {
      const cards = [
        ['Sessions',       compactNum(s.sessions)],
        ['Messages',       compactNum(s.messages)],
        ['Total tokens',   compactNum(s.total_tokens)],
        ['Active days',    compactNum(s.active_days)],
        ['Current streak', (s.current_streak || 0) + 'd'],
        ['Longest streak', (s.longest_streak || 0) + 'd'],
        ['Peak hour',      s.peak_hour || '—'],
        ['Favorite model', s.favorite_model || '—', true],
      ];
      return '<div class="stats-cards">' + cards.map(c => {
        const valClass = c[2] ? 'value muted' : 'value';
        return '<div class="stats-card">'
             + '<div class="label">' + escapeHtml(c[0]) + '</div>'
             + '<div class="' + valClass + '">' + escapeHtml(c[1]) + '</div>'
             + '</div>';
      }).join('') + '</div>';
    }

    function renderHeatmap(heatmap) {
      // heatmap[dow][hour] — rows: Mon..Sun, cols: 0..23
      // Reorder rows so Sun is first (matches GitHub-style heatmaps).
      const order = [6, 0, 1, 2, 3, 4, 5];  // Sun, Mon, Tue, Wed, Thu, Fri, Sat
      let max = 0;
      for (const row of heatmap) for (const v of row) if (v > max) max = v;
      const html = ['<div class="stats-heatmap-wrap"><div class="stats-heatmap">'];
      for (const dow of order) {
        const row = heatmap[dow] || [];
        for (let h = 0; h < 24; h++) {
          const v = row[h] || 0;
          let lvl = 0;
          if (max > 0 && v > 0) {
            const r = v / max;
            if (r >= 0.75) lvl = 4;
            else if (r >= 0.5) lvl = 3;
            else if (r >= 0.25) lvl = 2;
            else lvl = 1;
          }
          html.push('<div class="stats-heatmap-cell lvl-' + lvl + '" title="'
                    + escapeHtml(v + ' messages') + '"></div>');
        }
      }
      html.push('</div></div>');
      return html.join('');
    }

    function renderOverview(s) {
      let html = renderCards(s);
      html += renderHeatmap(s.heatmap || []);
      if (s.comparison) {
        html += '<div class="stats-comparison">' + escapeHtml(s.comparison) + '</div>';
      }
      return html;
    }

    function renderModels(s) {
      const rows = (s.models || []);
      if (!rows.length) {
        return '<div class="stats-loading">No assistant turns in this range yet.</div>';
      }
      const total = rows.reduce((a, r) => a + (r.messages || 0), 0) || 1;
      const max = Math.max.apply(null, rows.map(r => r.messages || 0)) || 1;
      const html = ['<div class="stats-models-list">'];
      for (const r of rows) {
        const pct = ((r.messages / total) * 100).toFixed(1) + '%';
        const w = ((r.messages / max) * 100).toFixed(1) + '%';
        html.push(
          '<div class="stats-models-row">'
          + '<div class="name">' + escapeHtml(r.label) + '</div>'
          + '<div class="bar"><div class="bar-fill" style="width:' + w + ';"></div></div>'
          + '<div class="pct">' + escapeHtml(compactNum(r.messages) + ' · ' + pct) + '</div>'
          + '</div>'
        );
      }
      html.push('</div>');
      return html.join('');
    }

    function render() {
      const s = cache[currentRange];
      if (!s) {
        $body.innerHTML = '<div class="stats-loading">Loading…</div>';
        return;
      }
      $body.innerHTML = currentTab === 'models' ? renderModels(s) : renderOverview(s);
    }

    async function load(range) {
      if (cache[range]) { render(); return; }
      $body.innerHTML = '<div class="stats-loading">Scanning transcripts…</div>';
      try {
        const res = await fetch('/api/stats?range=' + encodeURIComponent(range));
        const data = await res.json();
        cache[range] = data;
        if (range === currentRange) render();
      } catch (e) {
        $body.innerHTML = '<div class="stats-loading">Failed to load stats.</div>';
      }
    }

    function open() {
      $modal.classList.add('open');
      load(currentRange);
    }
    function close() { $modal.classList.remove('open'); }

    document.getElementById('statsBtn').addEventListener('click', open);
    document.getElementById('statsCloseBtn').addEventListener('click', close);
    $modal.addEventListener('click', (e) => {
      if (e.target && e.target.dataset && e.target.dataset.statsClose) close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && $modal.classList.contains('open')) close();
    });

    $modal.querySelectorAll('[data-stats-tab]').forEach(btn => {
      btn.addEventListener('click', () => {
        currentTab = btn.dataset.statsTab;
        $modal.querySelectorAll('[data-stats-tab]').forEach(b => {
          b.classList.toggle('active', b === btn);
        });
        render();
      });
    });
    $modal.querySelectorAll('[data-stats-range]').forEach(btn => {
      btn.addEventListener('click', () => {
        currentRange = btn.dataset.statsRange;
        $modal.querySelectorAll('[data-stats-range]').forEach(b => {
          b.classList.toggle('active', b === btn);
        });
        load(currentRange);
      });
    });
  })();

  // Token-usage pill — sibling of the workspace info in the same input-
  // context row. Shows latest input tokens out of the model's context
  // limit, with a peak indicator if it's higher than the latest.
  let _usageSessionId = null;
  let _usageData = null;
  async function fetchSessionUsage(sid) {
    _usageSessionId = sid;
    _usageData = null;
    const slot = getInputContextSlot();
    const uSlot = slot && slot.querySelector('[data-usage]');
    if (uSlot) uSlot.innerHTML = '';
    if (!sid) return;
    try {
      // cache-buster: usage rollups land sporadically in the JSONL, so the
      // first fetch after selection often returns latest=0 and the browser
      // would happily serve that zero response on every subsequent call.
      // Forcing a fresh request each time is fine — the server-side
      // computation is fast and uses no cache.
      const res = await fetch('/api/session/' + encodeURIComponent(sid) + '/usage?_t=' + Date.now(), { cache: 'no-store' });
      const data = await res.json();
      if (_usageSessionId !== sid) return;
      _usageData = data;
      renderSessionUsageIntoStrip();
    } catch (_) {}
  }

  function _formatTokens(n) {
    if (!n) return '0';
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
    if (n >= 1_000) return Math.round(n / 1000) + 'k';
    return String(n);
  }

  function _getCtxLimitOverride() {
    const v = parseInt(localStorage.getItem('ccc-context-limit') || '0', 10);
    return v === 1_000_000 || v === 200_000 ? v : 0;
  }

  function renderSessionUsageIntoStrip() {
    const slot = getInputContextSlot();
    const uSlot = slot && slot.querySelector('[data-usage]');
    if (!slot || !uSlot) return;
    const u = _usageData || {};
    const latest = u.latest_input_tokens || 0;
    const peak = u.peak_input_tokens || 0;
    // Model pill — shown when we know the model. Strips the "claude-"
    // prefix so it reads as `opus-4-7` / `sonnet-4-6`. Adds a `1M` suffix
    // when the session is provably on the 1M-context variant (any turn
    // exceeded 200k tokens — only possible on the 1M variant). The 1M
    // beta is delivered via the anthropic-beta header, not the model
    // name in the JSONL, so peak > 200k is the only honest signal.
    let modelPill = '';
    // The pill renders the *override* model (when set) so the user sees
    // the new value immediately. The actual `u.model` from the JSONL only
    // catches up after the next assistant turn. We show a "→ next" chip
    // whenever the override has been set but the JSONL hasn't recorded
    // that model yet (or when the inject explicitly returned applied=queued).
    const ovr = u.override || null;
    const displayModel = ovr ? ovr.model : (u.model || '');
    const engine = u.engine || (ovr && ovr.engine) || 'claude';
    const ovrNorm = ovr ? _normalizeModelId(ovr.model) : '';
    const liveNorm = _normalizeModelId(u.model || '');
    const queued = !!ovr && (ovr.applied === 'queued' || (ovrNorm && ovrNorm !== liveNorm));
    if (displayModel) {
      // 1M pill lights up when override.context_1m or peak provably exceeded
      // 200k (only possible on the 1M variant). Non-Claude engines never
      // show the 1M pill.
      const ovrIsOneM = !!(ovr && ovr.context_1m);
      const isOneM = engine === 'claude' && (ovrIsOneM || peak > 200000 || displayModel.toLowerCase().includes('[1m]'));
      const shortModel = displayModel.replace(/^claude-/, '').replace(/\[1m\]/i, '').trim();
      const modelTip = displayModel
        + (isOneM ? '\n(1M context window — anthropic-beta: context-1m)' : '')
        + (queued ? '\n(Applied on next ask — change is queued)' : '')
        + '\n\nClick to change model';
      modelPill = ' <button type="button" class="wp-model-pill" data-model-picker'
        + ' data-engine="' + escapeHtml(engine) + '"'
        + ' data-current="' + escapeHtml(displayModel) + '"'
        + ' data-1m="' + (isOneM ? '1' : '0') + '"'
        + ' title="' + escapeHtml(modelTip) + '">'
        + escapeHtml(shortModel)
        + (isOneM ? ' <span class="wp-model-1m">1M</span>' : '')
        + (queued ? ' <span class="wp-model-pending">→ next</span>' : '')
        + '</button>';
    }
    // Click-toggle override: if you're on the 1M variant the server's
    // 200k default is wrong; one click flips and persists.
    const override = _getCtxLimitOverride();
    const limit = override || u.context_limit || 200000;
    if (!latest && !peak) {
      if (!modelPill) {
        uSlot.innerHTML = '';
        return;
      }
      const title = 'No token usage samples were found for this session.\n'
        + 'Model: ' + u.model;
      uSlot.innerHTML = '<span class="wp-usage-pill wp-usage-missing" title="' + escapeHtml(title) + '">'
        + 'ctx unavailable'
        + '</span>' + modelPill;
      slot.classList.add('visible');
      return;
    }
    const pct = Math.round((latest / limit) * 100);
    let cls = 'wp-usage-pill wp-usage-clickable';
    if (pct >= 85) cls += ' wp-usage-hot';
    else if (pct >= 60) cls += ' wp-usage-warm';
    const peakNote = peak > latest
      ? ' <span class="wp-usage-peak" title="Peak across the session">peak ' + _formatTokens(peak) + '</span>'
      : '';
    const overrideNote = override ? ' (override)' : '';
    const title = 'Latest assistant turn: ' + latest.toLocaleString() + ' tokens / '
      + limit.toLocaleString() + ' context limit' + overrideNote
      + ' (' + (u.model || 'model unknown') + ')\n\nClick to toggle between 200k and 1M.';
    // Cost pill — Anthropic API list-price equivalent. Subscription users
    // (Pro/Max) pay flat, but the figure is still the cleanest cross-model
    // comparison of "how expensive was this session" so we surface it.
    const cost = u.cost_usd || 0;
    const breakdown = u.cost_breakdown_usd || {};
    let costPill = '';
    if (cost > 0) {
      const fmt = (n) => n >= 1 ? '$' + n.toFixed(2) : '$' + n.toFixed(4);
      const costTip = 'API list-price equivalent for ' + (u.model || 'unknown model') + '\n'
        + '  Input:        ' + fmt(breakdown.input || 0) + '  (' + (u.total_input_tokens || 0).toLocaleString() + ' tok)\n'
        + '  Cache write:  ' + fmt(breakdown.cache_creation || 0) + '  (' + (u.total_cache_creation_tokens || 0).toLocaleString() + ' tok)\n'
        + '  Cache read:   ' + fmt(breakdown.cache_read || 0) + '  (' + (u.total_cache_read_tokens || 0).toLocaleString() + ' tok)\n'
        + '  Output:       ' + fmt(breakdown.output || 0) + '  (' + (u.total_output_tokens || 0).toLocaleString() + ' tok)\n\n'
        + 'Subscription users (Claude Pro/Max) pay flat — this is the\n'
        + 'list-price equivalent if metered against the API directly.';
      costPill = ' <span class="wp-cost-pill" title="' + escapeHtml(costTip) + '">' + fmt(cost) + '</span>';
    }
    uSlot.innerHTML = '<span class="' + cls + '" title="' + escapeHtml(title) + '">'
      + 'ctx ' + _formatTokens(latest) + ' / ' + _formatTokens(limit)
      + ' <span class="wp-usage-pct">(' + pct + '%)</span>'
      + '</span>' + peakNote + costPill + modelPill;
    slot.classList.add('visible');
    const pill = uSlot.querySelector('.wp-usage-clickable');
    if (pill) {
      pill.addEventListener('click', () => {
        // Toggle between 200k, 1M, and clear (back to server-detected).
        const cur = _getCtxLimitOverride();
        const next = cur === 0 ? 1_000_000 : (cur === 1_000_000 ? 200_000 : 0);
        if (next === 0) localStorage.removeItem('ccc-context-limit');
        else localStorage.setItem('ccc-context-limit', String(next));
        renderSessionUsageIntoStrip();
      });
    }
    const modelBtn = uSlot.querySelector('[data-model-picker]');
    if (modelBtn) {
      modelBtn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        openModelPicker(modelBtn);
      });
    }
  }

  // Curated per-engine model lists. Free-text "Other…" handles unreleased
  // models without a code change. The `oneM` flag controls whether the
  // 1M-context toggle is offered for that model (Claude only — opus and
  // sonnet support the 1M-context beta header; haiku does not).
  const MODEL_OPTIONS_BY_ENGINE = {
    claude: [
      { id: 'opus-4-7',   label: 'opus-4-7',   oneM: true },
      { id: 'sonnet-4-6', label: 'sonnet-4-6', oneM: true },
      { id: 'haiku-4-5',  label: 'haiku-4-5',  oneM: false },
    ],
    codex: [
      { id: 'gpt-5.5',      label: 'gpt-5.5 (default)' },
      { id: 'gpt-5-codex',  label: 'gpt-5-codex' },
      { id: 'o3',           label: 'o3' },
      { id: 'o3-mini',      label: 'o3-mini' },
    ],
    gemini: [
      { id: 'gemini-2.5-pro',   label: 'gemini-2.5-pro' },
      { id: 'gemini-2.5-flash', label: 'gemini-2.5-flash' },
    ],
  };

  function _normalizeModelId(s) {
    return (s || '').replace(/^claude-/, '').replace(/\[1m\]/i, '').trim().toLowerCase();
  }

  let _modelPickerEl = null;
  let _modelPickerCloseHandler = null;

  function closeModelPicker() {
    if (_modelPickerEl) {
      _modelPickerEl.remove();
      _modelPickerEl = null;
    }
    if (_modelPickerCloseHandler) {
      document.removeEventListener('click', _modelPickerCloseHandler, true);
      document.removeEventListener('keydown', _modelPickerCloseHandler, true);
      _modelPickerCloseHandler = null;
    }
  }

  function openModelPicker(btn) {
    closeModelPicker();
    const sid = _usageSessionId || '';
    if (!sid) return;
    const engine = btn.dataset.engine || 'claude';
    const currentModel = btn.dataset.current || '';
    const currentIs1M = btn.dataset['1m'] === '1';
    const options = MODEL_OPTIONS_BY_ENGINE[engine] || MODEL_OPTIONS_BY_ENGINE.claude;
    const currentNorm = _normalizeModelId(currentModel);

    const pop = document.createElement('div');
    pop.className = 'model-picker-pop open';
    let html = '<div class="mp-header">Switch model — ' + escapeHtml(engine) + '</div>';
    options.forEach((opt) => {
      const isActive = _normalizeModelId(opt.id) === currentNorm;
      const oneM = !!opt.oneM;
      const oneMOn = isActive && currentIs1M;
      html += '<button type="button" class="mp-row' + (isActive ? ' active' : '') + '" data-model="' + escapeHtml(opt.id) + '">'
        + escapeHtml(opt.label || opt.id);
      if (oneM) {
        html += '<span class="mp-1m-toggle' + (oneMOn ? ' on' : '') + '" data-1m-toggle title="1M context (anthropic-beta: context-1m)">1M</span>';
      }
      html += '</button>';
    });
    html += '<div class="mp-divider"></div>';
    html += '<div class="mp-other">'
      + '<input type="text" placeholder="Other model…" data-mp-other-input>'
      + '<button type="button" data-mp-other-apply>Apply</button>'
      + '</div>';
    html += '<div class="mp-divider"></div>';
    html += '<button type="button" class="mp-row mp-reset" data-mp-reset>↺ Reset to session default</button>';
    html += '<div class="mp-status" data-mp-status></div>';
    pop.innerHTML = html;

    // Anchor: align below the pill, but clamp to the viewport so it doesn't
    // get clipped at the right edge of the input strip.
    document.body.appendChild(pop);
    const r = btn.getBoundingClientRect();
    const popW = pop.getBoundingClientRect().width || 240;
    let left = r.left;
    if (left + popW > window.innerWidth - 8) left = window.innerWidth - popW - 8;
    pop.style.left = Math.max(8, left) + 'px';
    pop.style.top = (r.bottom + 4) + 'px';
    _modelPickerEl = pop;

    const statusEl = pop.querySelector('[data-mp-status]');
    const setStatus = (txt, kind) => {
      if (!statusEl) return;
      statusEl.textContent = txt || '';
      statusEl.className = 'mp-status' + (kind ? ' ' + kind : '');
    };

    async function applyModel(model, context_1m) {
      if (!model) return;
      setStatus('Applying…');
      try {
        const r2 = await fetch('/api/session/' + encodeURIComponent(sid) + '/model', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model, context_1m: !!context_1m }),
        });
        const data = await r2.json();
        if (!data.ok) {
          setStatus(data.error || 'Failed', 'err');
          return;
        }
        // Optimistic local update so the pill rerenders immediately.
        _usageData = _usageData || {};
        _usageData.override = {
          model,
          context_1m: !!context_1m,
          engine: data.engine || engine,
          applied: data.applied || 'queued',
        };
        renderSessionUsageIntoStrip();
        setStatus(data.applied === 'live' ? 'Switched live ✓' : 'Queued for next ask', 'ok');
        setTimeout(closeModelPicker, 800);
      } catch (err) {
        setStatus('Network error', 'err');
      }
    }

    pop.querySelectorAll('.mp-row[data-model]').forEach((row) => {
      row.addEventListener('click', (ev) => {
        const t = ev.target;
        if (t && t.matches && t.matches('[data-1m-toggle]')) {
          // Click the 1M chip without selecting the row: just toggle the chip.
          // Selecting + 1M happens when clicking the row itself with the 1M chip on.
          ev.stopPropagation();
          t.classList.toggle('on');
          return;
        }
        const model = row.dataset.model;
        const oneMChip = row.querySelector('[data-1m-toggle]');
        const oneM = !!(oneMChip && oneMChip.classList.contains('on'));
        applyModel(model, oneM);
      });
    });
    const otherInput = pop.querySelector('[data-mp-other-input]');
    const otherApply = pop.querySelector('[data-mp-other-apply]');
    if (otherApply && otherInput) {
      otherApply.addEventListener('click', () => {
        const v = (otherInput.value || '').trim();
        if (v) applyModel(v, false);
      });
      otherInput.addEventListener('keydown', (ev) => {
        if (ev.key === 'Enter') {
          ev.preventDefault();
          otherApply.click();
        }
      });
    }
    const resetBtn = pop.querySelector('[data-mp-reset]');
    if (resetBtn) {
      resetBtn.addEventListener('click', async () => {
        setStatus('Clearing…');
        try {
          const r2 = await fetch('/api/session/' + encodeURIComponent(sid) + '/model/clear', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
          });
          const data = await r2.json();
          if (!data.ok) {
            setStatus(data.error || 'Failed', 'err');
            return;
          }
          _usageData = _usageData || {};
          _usageData.override = null;
          renderSessionUsageIntoStrip();
          setStatus('Cleared ✓', 'ok');
          setTimeout(closeModelPicker, 600);
        } catch (err) {
          setStatus('Network error', 'err');
        }
      });
    }

    _modelPickerCloseHandler = (ev) => {
      if (ev.type === 'keydown' && ev.key === 'Escape') {
        closeModelPicker();
        return;
      }
      if (ev.type === 'click' && pop && !pop.contains(ev.target) && ev.target !== btn) {
        closeModelPicker();
      }
    };
    document.addEventListener('click', _modelPickerCloseHandler, true);
    document.addEventListener('keydown', _modelPickerCloseHandler, true);
  }

  // Session-activity timeline — fetched per conversation, rendered into
  // the sticky header's `[data-timeline]` slot. Shows commit / push / PR
  // events with their assistant-turn position and the gap to the next.
  let _timelineSessionId = null;
  let _timelineData = null;
  async function fetchSessionTimeline(sid) {
    _timelineSessionId = sid;
    _timelineData = null;
    if (!sid) return;
    try {
      const res = await fetch('/api/session/' + encodeURIComponent(sid) + '/timeline');
      const data = await res.json();
      // Drop the response if the user already moved to a different session
      // before our fetch landed.
      if (_timelineSessionId !== sid) return;
      _timelineData = data;
      renderSessionTimelineIntoSticky();
    } catch (_) {}
  }

  function renderSessionTimelineIntoSticky() {
    const slot = document.querySelector('.conv-sticky-header [data-timeline]');
    if (!slot || !_timelineData) return;
    const evs = _timelineData.events || [];
    const total = _timelineData.total_turns || 0;
    // Visibility of the right column is owned by `_updateStickyAskSlots`
    // — when the timeline is empty AND the earlier-ask slot has content,
    // earlier is promoted into this column instead of the column being
    // hidden, so we can't unconditionally hide here.
    const col = slot.closest('.csh-col-activity');
    if (!evs.length) {
      slot.innerHTML = '';
      _updateStickyAskSlots();
      return;
    }
    const iconFor = (k) => ({commit: '●', push: '↑', pr: '⊕'}[k] || '·');
    const labelFor = (e) => {
      if (e.kind === 'commit') {
        const sha = e.sha ? '<code>' + escapeHtml(e.sha.slice(0, 7)) + '</code> ' : '';
        const subj = e.subject ? escapeHtml(e.subject.slice(0, 70)) : 'commit';
        return 'commit ' + sha + subj;
      }
      if (e.kind === 'push') return 'push' + (e.success === false ? ' (failed)' : '');
      if (e.kind === 'pr') {
        const num = e.pr_number ? '#' + e.pr_number : '';
        const subj = e.subject ? ' ' + escapeHtml(e.subject.slice(0, 60)) : '';
        return 'PR ' + num + (num ? ' opened' : 'opened') + subj;
      }
      return escapeHtml(e.kind);
    };
    let html = '<div class="stl-header"><span class="stl-title">Session activity</span><span class="stl-counter">' + evs.length + ' event' + (evs.length === 1 ? '' : 's') + ' · ' + total + ' turns</span></div>';
    html += '<div class="stl-body">';
    let prevTurn = 0;
    for (let i = 0; i < evs.length; i++) {
      const e = evs[i];
      const gap = e.turn - prevTurn;
      if (i > 0 && gap > 0) {
        html += '<div class="stl-gap">↓ ' + gap + ' turn' + (gap === 1 ? '' : 's') + ' later</div>';
      }
      const failed = e.success === false ? ' stl-failed' : '';
      html += '<div class="stl-row stl-' + e.kind + failed + '">'
        + '<span class="stl-turn" title="Assistant turn">T' + e.turn + '</span>'
        + '<span class="stl-icon">' + iconFor(e.kind) + '</span>'
        + '<span class="stl-text">' + labelFor(e) + '</span>'
        + '</div>';
      prevTurn = e.turn;
    }
    const tail = total - (evs[evs.length - 1].turn);
    if (tail > 0) {
      html += '<div class="stl-gap stl-final">↓ ' + tail + ' more turn' + (tail === 1 ? '' : 's') + ' since</div>';
    }
    html += '</div>';
    slot.innerHTML = html;
    // Activity now has rows — make sure earlier-ask, if it was hosted in
    // this column during an earlier empty-activity render, returns to its
    // default home stacked under the original.
    _updateStickyAskSlots();
    // Pin the activity column to its bottom — newest events live at the
    // tail of the list; if it overflows the fixed-height panel we want
    // the user looking at the most recent commit/push/PR, not whatever
    // happened on turn 1.
    if (col) col.scrollTop = col.scrollHeight;
  }

  // Produce a human-readable summary for a single tool-only assistant event,
  // used as the collapsed-group header label when the group has exactly one
  // command. Reads the rendered .tool-call's .tool-name and .tool-detail
  // text so we don't need to re-parse the underlying ev. Falls back to
  // "Ran <Tool>" if the shape isn't what we expect.
  function summarizeToolCall(div) {
    const tc = div.querySelector('.tool-call');
    if (!tc) return 'Ran 1 command';
    const name = (tc.querySelector('.tool-name')?.textContent || '').trim();
    const detail = (tc.querySelector('.tool-detail')?.textContent || '').trim();
    const basename = (s) => {
      if (!s) return '';
      const cleaned = s.replace(/^[\"\'\s]+|[\"\'\s]+$/g, '');
      const parts = cleaned.split('/');
      return parts[parts.length - 1] || cleaned;
    };
    const trunc = (s, n) => s.length > n ? s.slice(0, n - 1) + '…' : s;
    switch (name) {
      case 'Read':         return detail ? 'Read ' + basename(detail) : 'Ran Read';
      case 'Edit':         return detail ? 'Edited ' + basename(detail) : 'Ran Edit';
      case 'Write':        return detail ? 'Wrote ' + basename(detail) : 'Ran Write';
      case 'NotebookEdit': return detail ? 'Edited ' + basename(detail) : 'Ran NotebookEdit';
      case 'Bash':         return detail ? 'Ran ' + trunc(detail, 60) : 'Ran Bash';
      case 'Grep':         return detail ? 'Searched ' + trunc(detail, 50) : 'Ran Grep';
      case 'Glob':         return detail ? 'Globbed ' + trunc(detail, 50) : 'Ran Glob';
      case 'WebFetch':     return detail ? 'Fetched ' + trunc(detail, 60) : 'Ran WebFetch';
      case 'WebSearch':    return detail ? 'Searched the web for ' + trunc(detail, 50) : 'Ran WebSearch';
      case 'TodoWrite':    return 'Updated todos';
      case 'Task':         return detail ? 'Spawned subagent: ' + trunc(detail, 50) : 'Spawned subagent';
      case 'TaskCreate':   return 'Created task';
      case 'TaskUpdate':   return 'Updated task';
      case 'AskUserQuestion': return 'Asked a question';
      case 'ExitPlanMode': return 'Exited plan mode';
      default:             return detail ? 'Ran ' + name + ': ' + trunc(detail, 40) : 'Ran ' + (name || 'tool');
    }
  }

  function renderConversationEvents(events, paneId) {
    if (!Array.isArray(events)) return;  // defensive: backlog/unknown responses
    paneId = paneId || activePaneId();
    const $view = getConvViewForPane(paneId) || $conversationsView;
    // Stick-to-bottom only when the user is *already* near the bottom.
    // If they've scrolled up to read, leave the scroll position alone so
    // newly-streamed events don't yank them back down. 80px tolerance is
    // generous enough to absorb typical line-height jitter.
    const wasAtBottom = ($view.scrollHeight - $view.scrollTop - $view.clientHeight) < 80;
    for (const ev of events) {
      const div = document.createElement('div');
      div.className = 'event ' + ev.type;
      // Use the event's own JSONL timestamp (when it was actually written)
      // rather than render time; fall back to render time only when the
      // event has no ts (synthetic / very old logs).
      div.dataset.renderTs = eventStamp(ev.ts) || nowStamp();
      // Tag assistant rows with the source message_id so the streaming
      // bubble can detect "JSONL already won" and hand off cleanly.
      if (ev.type === 'assistant' && ev.message_id) {
        div.dataset.msgId = ev.message_id;
        const escId = (window.CSS && CSS.escape) ? CSS.escape(ev.message_id) : ev.message_id;
        const liveBubble = $view.querySelector('.stream-bubble[data-msg-id="' + escId + '"]');
        if (liveBubble) {
          if (liveBubble.parentNode) liveBubble.parentNode.removeChild(liveBubble);
          if (_streamingBubble === liveBubble) {
            _streamingBubble = null;
            _streamingMsgId = null;
          }
        }
      }

      // Make the first user message sticky at the top
      if (ev.type === 'user_text' && !_firstUserMsgRendered) {
        _firstUserMsgRendered = true;
        // The first user message lives permanently in the sticky header as
        // "Original ask" — hide its in-conversation bubble so we don't show
        // the same text twice (sticky + bubble) when no scrolling has
        // happened yet. The dynamic-ask tracker still walks DOM and treats
        // index 0 as "viewing the original," so the hidden node is harmless.
        div.classList.add('is-pinned-in-sticky');
        let sticky = $view.querySelector('.conv-sticky-header');
        if (!sticky) {
          sticky = document.createElement('div');
          sticky.className = 'conv-sticky-header';
          sticky.style.position = sticky.style.position || 'sticky';
          const hidden = localStorage.getItem('hideToolCalls') === '1';
          if (hidden) $view.classList.add('hide-tools');
          // Resolve linked issue number for this conversation (if any)
          const conv = conversationsData.find(x => x.id === currentConversation) || {};
          let issueNum = conv.linked_issue || conv.issue_number || '';
          if (!issueNum) {
            const dm = /^issue-(\d+)$/.exec(conv.display_name || '');
            if (dm) issueNum = dm[1];
          }
          const issueBtn = issueNum
            ? '<button class="tools-toggle issue-link-btn" data-issue="' + escapeHtml(String(issueNum)) + '" title="View GitHub issue #' + escapeHtml(String(issueNum)) + '" style="right:120px;color:var(--green);border-color:rgba(63,185,80,0.4);">Issue #' + escapeHtml(String(issueNum)) + ' &#x2197;</button>'
            : '';
          const resolveBtn = issueNum
            ? '<button class="tools-toggle resolve-btn" data-issue="' + escapeHtml(String(issueNum)) + '" data-session="' + escapeHtml(conv.session_id || '') + '" title="Commit changes and close issue #' + escapeHtml(String(issueNum)) + '" style="right:240px;color:#fff;background:var(--green);border-color:var(--green);">Commit &amp; resolve</button>'
            : '';
          // The "Close & announce" button used to live as an absolute
          // overlay in this sticky header; it's now a real button in
          // convToolbar (see #announceBtnConv) so it stays in reach
          // without having to scroll up.
          // Always start in the default (170px) state when entering a
          // conversation. The chevron toggles to `.is-expanded` (50vh)
          // and back; that state is ephemeral, scoped to this sticky DOM
          // node, and resets every time the user switches conversations.
          sticky.innerHTML = resolveBtn + issueBtn
            + '<button type="button" class="conv-sticky-header__close" data-csh-close title="Hide this panel completely">×</button>'
            + '<div class="csh-row">'
            +   '<div class="csh-col csh-col-ask">'
            +     '<div class="csh-ask-original">'
            +       '<div class="label">Original ask</div>'
            +       (function () {
                      const cleaned = cleanIssuePrompt(ev.text);
                      const parts = splitFirstSentence(cleaned);
                      let h = '<div class="user-msg">';
                      h += '<span class="ask-first">' + linkifyPastedImages(escapeHtml(parts[0])) + '</span>';
                      h += '<span class="ask-rest"' + (parts[1] ? '' : ' style="display:none"') + '>' + linkifyPastedImages(escapeHtml(parts[1] || '')) + '</span>';
                      h += '</div>';
                      return h;
                    })()
            +     '</div>'
            +     '<div class="csh-ask-earlier" data-earlier-block>'
            +       '<div class="label">Earlier ask</div>'
            +       '<div class="user-msg"><span class="earlier-first" data-earlier-first></span></div>'
            +     '</div>'
            +   '</div>'
            +   '<div class="csh-col csh-col-activity">'
            +     '<div class="session-timeline" data-timeline></div>'
            +   '</div>'
            + '</div>'
            + '<button type="button" class="conv-sticky-header__collapse" data-csh-collapse '
            +   'title="Expand to half-screen / shrink back" aria-label="Toggle expanded sticky panel">'
            +   '<span class="conv-sticky-header__collapse-icon">▴</span>'
            + '</button>';
          $view.insertBefore(sticky, $view.firstChild);
          // Wire the chevron toggle. Two states:
          //   default (170px) → click → expanded (50vh)
          //   expanded (50vh) → click → default (170px)
          // State is ephemeral; switching conversations rebuilds the
          // sticky in the default state.
          const collapseBtn = sticky.querySelector('[data-csh-collapse]');
          if (collapseBtn) {
            collapseBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              const nowExpanded = !sticky.classList.contains('is-expanded');
              sticky.classList.toggle('is-expanded', nowExpanded);
              const icon = collapseBtn.querySelector('.conv-sticky-header__collapse-icon');
              if (icon) icon.textContent = nowExpanded ? '▾' : '▴';
            });
          }
          // The sticky ask slots keep a stable height while their message
          // bodies scroll internally; no resize handle or stored height.
          // Wire the dynamic-ask scroll tracker. The helpers (_dynAskItems,
          // _dynAskApply, _dynAskUpdate, _dynAskSchedule) only do work when
          // _dynamicAskState is populated and a scroll listener fires
          // _dynAskSchedule. Without this block they sit dormant and the
          // sticky never swaps to "Earlier ask".
          {
            _dynamicAskState = {
              view: $view,
              sticky,
              earlierBox: sticky.querySelector('[data-earlier-block]'),
              earlierFirst: sticky.querySelector('[data-earlier-first]'),
              currentIdx: 0,
              rafPending: false,
            };
            if (!$view._dynAskListenerAttached) {
              $view._dynAskListenerAttached = true;
              $view.addEventListener('scroll', _dynAskSchedule, { passive: true });
            }
            _dynAskSchedule();  // settle initial state in case view mounts pre-scrolled
            // Decide initial slot layout (earlier collapsed / activity hidden)
            // before the timeline fetch lands; the timeline render will
            // rerun the coordinator once events arrive.
            _updateStickyAskSlots();
          }
          // Populate the timeline + workspace panels if the fetches already
          // landed; otherwise the fetch handlers will populate them.
          renderSessionTimelineIntoSticky();
          renderSessionWorkspaceIntoSticky();
          const closeBtn = sticky.querySelector('[data-csh-close]');
          if (closeBtn) {
            closeBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              sticky.style.display = 'none';
            });
          }
          const resolveClickBtn = sticky.querySelector('.resolve-btn');
          if (resolveClickBtn) {
            resolveClickBtn.addEventListener('click', async (e) => {
              e.stopPropagation();
              const n = resolveClickBtn.dataset.issue;
              const sid = resolveClickBtn.dataset.session;
              resolveClickBtn.disabled = true;
              resolveClickBtn.textContent = 'Sending...';
              const msg = 'Please commit all your changes with a clear commit message referencing issue #' + n + ', then push. Use /commit if available. Once pushed, the issue will be closed automatically.';
              try {
                await fetch('/api/inject-input', {
                  method: 'POST',
                  headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({ session_id: sid, text: msg }),
                });
                resolveClickBtn.textContent = 'Sent ✓';
                resolveClickBtn.style.opacity = '0.6';
              } catch (err) {
                resolveClickBtn.textContent = 'Failed';
                setTimeout(() => { resolveClickBtn.textContent = 'Commit & resolve'; resolveClickBtn.disabled = false; }, 2000);
              }
            });
          }
          const issueLinkBtn = sticky.querySelector('.issue-link-btn');
          if (issueLinkBtn) {
            issueLinkBtn.addEventListener('click', (e) => {
              e.stopPropagation();
              const n = issueLinkBtn.dataset.issue;
              if (e.metaKey || e.ctrlKey || e.shiftKey) {
                renderIssueInConvPane(n, repoPathForIssueNumber(n));
              } else {
                renderIssueInConvPane(n, repoPathForIssueNumber(n));
              }
            });
          }
        }
      }

      if (ev.type === 'system') {
        div.innerHTML = '<span class="label">System</span>'
          + '<span class="line-num">L' + ev.line + '</span>'
          + tsSpan(ev.ts)
          + '<span>' + escapeHtml(ev.subtype || '') + (ev.model ? ' &middot; ' + escapeHtml(ev.model) : '') + (ev.session ? ' &middot; ' + escapeHtml(ev.session) : '') + '</span>';
      } else if (ev.type === 'user_text') {
        // If this matches an outstanding optimistic send, drop the pending stub
        // (the real event lands in its place below).
        const normed = _normSend(ev.text);
        const pIdx = _pendingSends.findIndex(p => _normSend(p.text) === normed);
        if (pIdx >= 0) {
          const p = _pendingSends[pIdx];
          if (p.element && p.element.parentNode) p.element.parentNode.removeChild(p.element);
          _pendingSends.splice(pIdx, 1);
        }
        let imagesHtml = '';
        if (Array.isArray(ev.images) && ev.images.length) {
          for (const img of ev.images) {
            let src = '';
            if (img.kind === 'path' && img.session_id && img.filename) {
              src = '/image-cache/' + encodeURIComponent(img.session_id) + '/' + encodeURIComponent(img.filename);
            } else if (img.kind === 'base64' && img.data) {
              src = 'data:' + (img.media_type || 'image/png') + ';base64,' + img.data;
            } else {
              continue;
            }
            imagesHtml += '<img class="msg-image" src="' + src + '" alt="pasted image">';
          }
        }
        const cleanedText = cleanIssuePrompt(ev.text || '');
        // data-raw-text preserves the original prose so _dynAskApply can pin
        // the same wording in the "Earlier ask" sticky — reading textContent
        // back would lose any pasted-image path that's been replaced with <img>.
        const textHtml = cleanedText
          ? '<div class="user-msg" data-raw-text="' + escapeHtml(cleanedText) + '">' + linkifyPastedImages(escapeHtml(cleanedText)) + '</div>'
          : '';
        div.innerHTML = '<span class="label">User</span>'
          + '<span class="line-num">L' + ev.line + '</span>'
          + tsSpan(ev.ts)
          + textHtml
          + imagesHtml;
      } else if (ev.type === 'assistant') {
        let html = '<span class="line-num">L' + ev.line + '</span>' + tsSpan(ev.ts);
        let hasNonTool = false;
        for (const b of ev.blocks) {
          if (b.kind === 'tool_use') {
            html += '<div class="tool-call"><span class="arrow">-></span> <span class="tool-name">' + escapeHtml(b.name) + '</span> <span class="tool-detail">' + escapeHtml(b.detail) + '</span></div>';
          } else if (b.kind === 'text') {
            html += '<div class="assistant-text">' + renderMarkdown(b.text) + '</div>';
            hasNonTool = true;
          } else if (b.kind === 'thinking') {
            html += '<div class="thinking-block" style="display:none"><span class="thinking-toggle" onclick="this.parentElement.querySelector(\'.t-body\').style.display=this.parentElement.querySelector(\'.t-body\').style.display===\'none\'?\'block\':\'none\'">Thinking</span><div class="t-body">' + escapeHtml(b.text) + '</div></div>';
            hasNonTool = true;
          }
        }
        if (!hasNonTool) div.classList.add('tool-only');
        div.innerHTML = html;
      } else if (ev.type === 'result') {
        const dur = typeof ev.duration_ms === 'number' ? (ev.duration_ms / 1000).toFixed(1) + 's' : ev.duration_ms;
        let statsHtml = '';
        if (ev.token_usage && typeof ev.token_usage === 'object') {
          const u = ev.token_usage;
          const input = Number(u.input_tokens || 0);
          const cached = Number(u.cached_input_tokens || 0);
          const output = Number(u.output_tokens || 0);
          const reasoning = Number(u.reasoning_output_tokens || 0);
          const total = Number(u.total_tokens || 0);
          const cachedText = cached ? ' (' + _formatTokens(cached) + ' cached)' : '';
          const reasoningText = reasoning ? ' (' + _formatTokens(reasoning) + ' reasoning)' : '';
          const tokenTip = 'Input:           ' + input.toLocaleString() + ' tokens\n'
            + 'Cached input:    ' + cached.toLocaleString() + ' tokens\n'
            + 'Output:          ' + output.toLocaleString() + ' tokens\n'
            + 'Reasoning:       ' + reasoning.toLocaleString() + ' tokens'
            + (total ? '\nTotal:           ' + total.toLocaleString() + ' tokens' : '');
          statsHtml += '<span title="' + escapeHtml(tokenTip) + '">Tokens: in ' + escapeHtml(_formatTokens(input) + cachedText)
            + ' · out ' + escapeHtml(_formatTokens(output) + reasoningText) + '</span>';
        } else if (ev.cost_usd != null && ev.cost_usd !== '') {
          const cost = typeof ev.cost_usd === 'number' ? '$' + ev.cost_usd.toFixed(4) : ev.cost_usd;
          statsHtml += '<span>Cost: ' + escapeHtml(String(cost)) + '</span>';
        }
        statsHtml += '<span>Duration: ' + escapeHtml(String(dur)) + '</span>';
        div.innerHTML = '<span class="label">Done</span>'
          + '<span class="line-num">L' + ev.line + '</span>'
          + tsSpan(ev.ts)
          + '<div class="stats">' + statsHtml + '</div>';
      } else if (ev.type === 'tool_result') {
        // Inline the result text under the most recent tool_call in the
        // current group. If we can't find one, drop the marker silently —
        // empty .event.tool_result rows are hidden by CSS anyway.
        const text = eventTextString(ev.text).trim();
        if (text && _currentToolGroup) {
          const calls = _currentToolGroup.querySelectorAll('.tool-call');
          const last = calls[calls.length - 1];
          if (last && !last.querySelector('.tool-result-output')) {
            const out = document.createElement('pre');
            out.className = 'tool-result-output' + (ev.is_error ? ' is-error' : '');
            // Stamp this output with the tool_result event's own ts (when
            // the result actually landed in the JSONL), not render time.
            // The parent .event.tool_result row is display:none, so the
            // CSS ::before never renders — we bake the prefix into the
            // <pre>'s text content instead.
            const _toolTs = eventStamp(ev.ts) || nowStamp();
            out.dataset.renderTs = _toolTs;
            out.textContent = '[J ' + _toolTs + ']  ' + text;
            // Bump the group's header stamp too so it reflects the most
            // recent activity (tool_use OR tool_result), not just tool_use.
            // CSS ::before reads attr() from its own element, so the header
            // node needs the attribute set, not just the parent group.
            if (_currentToolGroup) {
              _currentToolGroup.dataset.renderTs = _toolTs;
              const $h = _currentToolGroup.querySelector('.tool-call-group-header');
              if ($h) $h.dataset.renderTs = _toolTs;
            }
            last.appendChild(out);
          }
        }
        continue;
      }

      // Route tool-only assistant events into a fused group so the chat
      // pane reads "Ran N commands ▾" instead of one row per Bash/Read/Edit.
      const isToolOnly = div.classList.contains('tool-only');
      if (isToolOnly) {
        if (!_currentToolGroup || _currentToolGroup !== $view.lastElementChild) {
          // No open group, or another event closed it — start a new one.
          const grp = document.createElement('div');
          grp.className = 'tool-call-group collapsed';
          const _grpTs = eventStamp(ev.ts) || nowStamp();
          grp.dataset.renderTs = _grpTs;
          grp.innerHTML =
              '<div class="tool-call-group-header" data-render-ts="' + _grpTs + '">'
            + '<span class="tcg-arrow">▶</span> <span class="tcg-label">Ran 1 command</span>'
            + '</div>'
            + '<div class="tool-call-group-body"></div>';
          $view.appendChild(grp);
          grp.querySelector('.tool-call-group-header')
             .addEventListener('click', () => grp.classList.toggle('collapsed'));
          _currentToolGroup = grp;
          _currentToolCount = 0;
        }
        _currentToolGroup.querySelector('.tool-call-group-body').appendChild(div);
        // Bump both stamps to this tool's own ts so the visible group
        // header reflects when the latest tool actually ran. The header's
        // own data-render-ts is what the CSS ::before reads.
        const _ts = eventStamp(ev.ts) || nowStamp();
        _currentToolGroup.dataset.renderTs = _ts;
        _currentToolGroup.querySelector('.tool-call-group-header').dataset.renderTs = _ts;
        _currentToolCount += 1;
        // Update the header label. Single-command groups get a smart
        // tool-specific summary ("Read foo.py"); multi-command groups
        // collapse to "Ran N commands".
        const $lbl = _currentToolGroup.querySelector('.tcg-label');
        if (_currentToolCount === 1) {
          $lbl.textContent = summarizeToolCall(div);
        } else {
          $lbl.textContent = 'Ran ' + _currentToolCount + ' commands';
        }
      } else {
        // Any non-tool-only event closes the current group.
        _currentToolGroup = null;
        _currentToolCount = 0;
        $view.appendChild(div);
      }
    }
    if (events.length > 0 && wasAtBottom) {
      $view.scrollTop = $view.scrollHeight;
    }
    // Re-anchor the inline live-tool indicator at the bottom; new events
    // just appended would otherwise push past it.
    if (typeof updateLiveToolStrip === 'function') updateLiveToolStrip();
    // Same story for the spawn-log streaming bubble — keep it pinned to
    // the tail so it doesn't end up sandwiched between older JSONL events
    // and newer ones.
    if (_streamingBubble && _streamingBubble.parentNode === $view
        && _streamingBubble !== $view.lastElementChild) {
      $view.appendChild(_streamingBubble);
    }
    // The optimistic "Sending…" pill needs two things:
    //   1) If the agent has already produced a real reply (assistant /
    //      tool_use / result event in this batch), kill it — "starting up"
    //      is over. Otherwise it sticks forever when the agent answers
    //      before the 5s sidecar poll catches a tool in flight.
    //   2) Otherwise (still waiting), re-anchor it to the tail so the
    //      newly-appended user_text bubble doesn't end up below the pill.
    const _agentReplied = events.some(e =>
      e && (e.type === 'assistant' || e.type === 'result' || e.type === 'tool_result')
    );
    if (_agentReplied) {
      clearOptimisticAgentIndicator($view);
      if (currentSession.id) clearSessionSending(currentSession.id);
    } else {
      const _optimistic = $view.querySelector('.conv-live-tool-inline.optimistic');
      if (_optimistic && _optimistic !== $view.lastElementChild) {
        $view.appendChild(_optimistic);
      }
    }
    // Files-from-conversation pill: the sticky header may have just
    // been created (above) or may already exist. Fire-and-forget; the
    // pill stays hidden if the conversation has no qualifying files.
    ffcRefreshForCurrent();
  }

  function filterConversations(q) {
    q = (q || '').toLowerCase();
    const recentCutoff = recencyCutoffSec();
    // Re-apply persisted local archive for TODO/parking cards on every filter pass
    // (each server response sets c.archived=false for them; we flip it back).
    for (const c of conversationsData) {
      if (_archivedBacklogIds.has(c.id)) c.archived = true;
    }
    const filtered = conversationsData.filter(c => {
      // Recent-only filter (last N hours) — applies to everything, backlog included.
      // Backlog items use issue_created_at (falls back to modified).
      if (showRecentOnly) {
        const ts = (c.source === 'backlog')
          ? (c.issue_created_at || c.modified || 0)
          : (c.modified || 0);
        if (ts < recentCutoff) return false;
      }
      // Archive filter retired in list view: archived rows now sit in
      // a dedicated Archived section at the bottom of the list, so the
      // user always sees both groups (active above, archived below).
      // Kanban has its own Archived column.
      // Git-only filter: cards without a linked issue are hidden
      if (window._gitOnlyFilter) {
        const hasIssue = c.linked_issue || c.issue_number || (c.id || '').startsWith('backlog-issue-');
        if (!hasIssue) return false;
      }
      // Text search
      if (!q) return true;
      return (c.display_name || '').toLowerCase().includes(q)
        || (c.first_message || '').toLowerCase().includes(q)
        || (c.last_prompt || '').toLowerCase().includes(q)
        || (c.branch || '').toLowerCase().includes(q)
        || (c.source || '').toLowerCase().includes(q)
        || (c.session_id || '').toLowerCase().includes(q)
        || (c.id || '').toLowerCase().includes(q);
    });
    const sorted = applyConvSort(filtered);
    return _decorateWithHistoryMatches(sorted, q);
  }

  // ── History-search augmentation ──────────────────────────────────────
  // The session-list filter above only sees sessions already loaded into
  // conversationsData. The local claude-index FTS5 store covers every
  // conversation on this Mac, including ones not currently loaded. We
  // keep the two paths independent: the existing filter is unchanged,
  // and history matches are OR-unioned in via decorations + synthetic
  // rows below. If the index is missing or the request fails, the local
  // filter still works as today (zero degradation).
  //
  // _historyState.map: session_id → { snippet, ts, cwd, type }
  // _historyState.query: the query the map was built for; stale queries
  //                       are ignored to avoid showing decorations from a
  //                       previous keystroke after the user has typed more.
  const _historyState = { query: '', map: new Map(), broaden: false };
  let _historyFetchSeq = 0;
  let _historyFetchTimer = null;

  // Strip <mark> tags so we can use snippet text as a synthetic title
  // without leaking HTML through escapeHtml later.
  function _stripHistoryHtml(s) {
    return (s || '').replace(/<mark>/g, '').replace(/<\/mark>/g, '').replace(/…/g, '');
  }

  function _decorateWithHistoryMatches(localSorted, qLower) {
    // Empty query or stale state → strip any leftover decorations and bail.
    if (!qLower || _historyState.query !== qLower) {
      for (const c of localSorted) {
        if (c._historyMatch) {
          c._historyMatch = false;
          c._historySnippet = '';
          c._historySource = '';
        }
      }
      return localSorted;
    }
    const map = _historyState.map;
    if (!map.size) return localSorted;
    // Decorate locally-known sessions that also matched in history.
    const seen = new Set();
    for (const c of localSorted) {
      const sid = c.session_id || c.id;
      if (sid && map.has(sid)) {
        const hit = map.get(sid);
        c._historyMatch = true;
        c._historySnippet = hit.snippet;
        c._historySource = hit.source || 'bm25';
        seen.add(sid);
      } else if (c._historyMatch) {
        // Was decorated for a previous query; clear.
        c._historyMatch = false;
        c._historySnippet = '';
        c._historySource = '';
      }
    }
    // Append synthetic rows for sessions that the local filter doesn't
    // know about. Minimal fields: enough for the row renderer to fall
    // back gracefully. Click handler opens by data-session-id, which
    // CCC's session-loader knows how to resolve.
    const synthetic = [];
    for (const [sid, hit] of map.entries()) {
      if (seen.has(sid)) continue;
      const titleSrc = _stripHistoryHtml(hit.snippet).trim();
      synthetic.push({
        id: sid,
        session_id: sid,
        display_name: titleSrc.slice(0, 80) || ('Session ' + sid.slice(0, 8)),
        first_message: '',
        last_prompt: '',
        branch: '',
        source: 'history',
        cwd: hit.cwd || '',
        session_cwd: hit.cwd || '',
        modified: (hit.ts || 0) * 1000,
        size: 0,
        archived: false,
        _historyMatch: true,
        _historySnippet: hit.snippet,
        _historySource: hit.source || 'bm25',
        _historyOnly: true,
      });
    }
    // History-only rows trail the local matches. Within themselves they
    // keep history-search BM25 order (already sorted by score in the
    // server response, so insertion order suffices).
    return localSorted.concat(synthetic);
  }

  function _fetchHistoryAugment(query) {
    const q = (query || '').trim();
    const qLower = q.toLowerCase();
    const seq = ++_historyFetchSeq;
    if (!q) {
      _historyState.query = '';
      _historyState.map = new Map();
      return Promise.resolve();
    }
    // Default scope = current repo, mirroring the rest of the list view.
    // The "Broaden outside of repo" toggle (when wired) will flip this.
    const params = new URLSearchParams({ q, limit: '50', since: '90d' });
    if (!_historyState.broaden) {
      const cwd = (typeof selectedRepoPath === 'function' && selectedRepoPath()) || '';
      if (cwd) params.set('cwd', cwd);
    }
    // Auto-enable semantic when the local index has embeddings — the user
    // explicitly asked: "use semantic if it's installed." Status is fetched
    // and cached at startup; we just read the flag here.
    if (window._historyIndexStatus && window._historyIndexStatus.semantic && window._historyIndexStatus.semantic.available) {
      params.set('semantic', '1');
    }
    return fetch('/api/search-history?' + params.toString())
      .then(r => r.ok ? r.json() : { results: [] })
      .catch(() => ({ results: [] }))
      .then(data => {
        // Drop stale responses — newer keystroke already in flight.
        if (seq !== _historyFetchSeq) return;
        const results = (data && data.results) || [];
        const bySession = new Map();
        for (const r of results) {
          const sid = r.session_id;
          if (!sid) continue;
          // First hit per session wins. Server returns RRF-sorted when
          // semantic is on, BM25-sorted when off. Either way, first hit
          // is the best per session.
          if (!bySession.has(sid)) {
            bySession.set(sid, {
              snippet: r.snippet || '',
              ts: r.ts_unix || 0,
              cwd: r.cwd || '',
              type: r.type || '',
              source: r._source || 'bm25',
            });
          }
        }
        _historyState.query = qLower;
        _historyState.map = bySession;
      });
  }

  function updateConversationSearchClear() {
    if (!$convSearch || !$convSearchClear) return;
    const hasValue = Boolean($convSearch.value);
    $convSearchClear.closest('.search-wrap')?.classList.toggle('has-value', hasValue);
  }

  // ── History-index topbar status + OOBE prompt ─────────────────────────
  // Polls /api/history/status. Shows:
  //   - spinner when an ingest pass is in flight
  //   - freshness ("12m ago") of the most-recently-indexed message
  //   - a sparkle (✨) when semantic embeddings exist, with a separate
  //     freshness for the embedding stream
  // Click → kicks a manual /api/history/setup so the user can force a
  // refresh without waiting for the next scheduled pass.
  window._historyIndexStatus = null;
  let _hiPollTimer = null;
  let _hiOobePromptDismissed = false;

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
      title = 'No history index yet. Click to build one — runs in the background.';
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
    $label.textContent = label;
    $pill.title = title;
  }

  async function _hiRefreshStatus() {
    try {
      const r = await fetch('/api/history/status');
      if (!r.ok) return;
      const st = await r.json();
      window._historyIndexStatus = st;
      _hiRenderPill(st);
    } catch (_) {}
  }

  function _hiStartPolling() {
    _hiRefreshStatus();
    if (_hiPollTimer) clearInterval(_hiPollTimer);
    // 8s during indexing, 60s otherwise — adaptive interval set after each tick.
    _hiPollTimer = setInterval(() => {
      const st = window._historyIndexStatus;
      const fast = st && (st.indexing || st.embedding);
      _hiRefreshStatus();
      // Re-arm with appropriate cadence.
      if ((fast && _hiPollTimer.__cadence !== 'fast')
          || (!fast && _hiPollTimer.__cadence !== 'slow')) {
        clearInterval(_hiPollTimer);
        _hiPollTimer = setInterval(() => _hiRefreshStatus(), fast ? 4000 : 60000);
        _hiPollTimer.__cadence = fast ? 'fast' : 'slow';
      }
    }, 8000);
    _hiPollTimer.__cadence = 'slow';
  }

  async function _hiTriggerSetup() {
    try {
      const r = await fetch('/api/history/setup', { method: 'POST' });
      if (!r.ok) return false;
      _hiRefreshStatus();
      return true;
    } catch (_) { return false; }
  }

  // OOBE: when the user types a query and the index doesn't exist yet,
  // surface a one-time prompt under the search input. Dismissing it sets
  // a session-only flag so it doesn't reappear during this CCC session.
  function _hiMaybeShowOobe() {
    const st = window._historyIndexStatus;
    if (!st || st.exists || st.indexing || _hiOobePromptDismissed) return;
    if (document.getElementById('hiOobePrompt')) return;
    // Anchor the prompt under convSearch's wrapper so it scrolls with the
    // sidebar and is visually adjacent to the input the user just typed in.
    const $wrap = document.querySelector('.search-wrap') || $convSearch?.parentElement;
    if (!$wrap) return;
    const $oobe = document.createElement('div');
    $oobe.id = 'hiOobePrompt';
    $oobe.className = 'hi-oobe';
    $oobe.innerHTML =
      '<div class="hi-oobe-title">&#128218; Build a history index?</div>'
      + '<div>Indexes every Claude Code &amp; Codex conversation on this Mac so search can find them. Runs locally; ~1.5 GB on disk after a few months.</div>'
      + '<div class="hi-oobe-actions">'
      +   '<button type="button" class="primary" data-role="hi-enable">Enable</button>'
      +   '<button type="button" data-role="hi-dismiss">Not now</button>'
      + '</div>';
    $wrap.parentElement.insertBefore($oobe, $wrap.nextSibling);
    $oobe.querySelector('[data-role="hi-enable"]').addEventListener('click', async () => {
      $oobe.querySelector('[data-role="hi-enable"]').textContent = 'Starting…';
      const ok = await _hiTriggerSetup();
      if (ok) {
        $oobe.innerHTML = '<div class="hi-oobe-title">&#128218; Indexing started</div>'
          + '<div>This may take a few minutes for the first run. Watch the topbar pill for progress.</div>';
        setTimeout(() => $oobe.remove(), 6000);
      } else {
        $oobe.querySelector('[data-role="hi-enable"]').textContent = 'Failed — retry';
      }
    });
    $oobe.querySelector('[data-role="hi-dismiss"]').addEventListener('click', () => {
      _hiOobePromptDismissed = true;
      $oobe.remove();
    });
  }

  // Wire pill click + start polling once on first JS load.
  document.addEventListener('DOMContentLoaded', () => {
    const $pill = document.getElementById('historyStatusPill');
    if ($pill) {
      $pill.addEventListener('click', () => _hiTriggerSetup());
      $pill.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _hiTriggerSetup(); }
      });
    }
    _hiStartPolling();
  });

  updateConversationSearchClear();

  if ($convSearchClear) {
    $convSearchClear.addEventListener('click', () => {
      if (!$convSearch || !$convSearch.value) return;
      $convSearch.value = '';
      updateConversationSearchClear();
      $convSearch.dispatchEvent(new Event('input', { bubbles: true }));
      $convSearch.focus();
    });
  }

  // Conversation search filter
  $convSearch.addEventListener('input', () => {
    updateConversationSearchClear();
    // Re-render immediately with the local filter — instant response,
    // no waiting on the network.
    renderSidebar(filterConversations($convSearch.value));
    // OOBE: first-time search with no index → offer to build one. Only
    // fires when the user actually typed something (not on focus / clear).
    if ($convSearch.value.trim().length >= 2) {
      _hiMaybeShowOobe();
    }
    // Debounced history fetch; on completion, re-render with augmentation.
    if (_historyFetchTimer) clearTimeout(_historyFetchTimer);
    const q = $convSearch.value;
    _historyFetchTimer = setTimeout(() => {
      _fetchHistoryAugment(q).then(() => {
        // Only re-render if the input still holds this query — user may
        // have already typed more, in which case a newer fetch is queued.
        if ($convSearch.value === q) {
          renderSidebar(filterConversations($convSearch.value));
        }
      });
    }, 180);
  });

  // ── Issues dashboard ──
  let issuesPolling = null;
  let issuesData = [];

  async function loadIssues() {
    // Issues tab was removed; the dedicated /api/issues view no longer
    // exists as a visible UI element. Issues now surface as kanban cards
    // with an inline "Fix" button instead of a dedicated panel.
    if (!$issuesView) return;
    const repoPath = selectedRepoPath();
    if (!repoPath) {
      issuesData = [];
      renderIssues(issuesData);
      return;
    }
    try {
      const res = await fetch(repoUrl('/api/issues', repoPath));
      issuesData = await res.json();
      renderIssues(issuesData);
    } catch (err) {
      $issuesView.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Failed to load issues: ' + escapeHtml(err.message) + '</div>';
    }
  }

  function renderIssues(issues) {
    if (!$issuesView) return;
    if (!issues.length) {
      $issuesView.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;line-height:1.6;">'
        + 'No GitHub issues found.<br><br>'
        + 'If this repo just has no open issues, that\'s fine — they\'ll show up here when you create one.<br><br>'
        + 'If you expected to see issues, install <a href="https://cli.github.com/" target="_blank" rel="noopener" style="color:var(--accent);">gh</a> and run <code style="background:#1a1d23;padding:2px 6px;border-radius:3px;">gh auth login</code> from this repo, then refresh.'
        + '</div>';
      return;
    }

    // Count active (in_progress + queued)
    const activeCount = issues.filter(i => i.claude_status === 'in_progress' || i.claude_status === 'queued').length;
    if (activeCount > 0) {
      $issuesBadge.textContent = activeCount;
      $issuesBadge.style.display = '';
    } else {
      $issuesBadge.style.display = 'none';
    }

    // Group issues
    const open = issues.filter(i => i.state === 'open');
    const closed = issues.filter(i => i.state === 'closed');

    let html = '';
    if (open.length) {
      html += '<div class="issues-section-header">Open Issues (' + open.length + ')</div>';
      html += open.map(renderIssueRow).join('');
    }
    if (closed.length) {
      html += '<div class="issues-section-header">Recently Closed (' + closed.length + ')</div>';
      html += closed.map(renderIssueRow).join('');
    }
    $issuesView.innerHTML = html;

    // Bind events
    $issuesView.querySelectorAll('.fix-btn').forEach(btn => {
      btn.addEventListener('click', () => addFixLabel(btn.dataset.num, btn));
    });
    $issuesView.querySelectorAll('.spawn-fix-btn').forEach(btn => {
      btn.addEventListener('click', () => spawnIssueFix(btn.dataset.num, btn));
    });
    $issuesView.querySelectorAll('.log-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        // Find the issue's session card in the unified list and select it.
        const issueId = 'issue-' + btn.dataset.num;
        switchTab('sessions');
        selectConversation(issueId);
      });
    });
    $issuesView.querySelectorAll('.issue-num').forEach(el => {
      el.addEventListener('click', () => {
        renderIssueInConvPane(el.dataset.num, selectedRepoPath());
      });
    });
    $issuesView.querySelectorAll('.summary-toggle').forEach(btn => {
      btn.addEventListener('click', () => toggleSummary(btn.dataset.num));
    });
  }

  function renderIssueRow(issue) {
    const statusLabels = { in_progress: 'Working', queued: 'Queued', failed: 'Failed', open: 'Open', closed: 'Closed' };
    const labels = issue.labels
      .filter(l => !['claude-fix','claude-in-progress','claude-failed'].includes(l))
      .map(l => '<span class="issue-label other">' + escapeHtml(l) + '</span>')
      .join('');
    const claudeLabels = issue.labels
      .filter(l => ['claude-fix','claude-in-progress','claude-failed'].includes(l))
      .map(l => '<span class="issue-label ' + l + '">' + escapeHtml(l) + '</span>')
      .join('');

    const canFix = issue.state === 'open' && issue.claude_status === 'open';
    const hasLog = issue.has_log;
    const showSummary = issue.state === 'closed' || issue.claude_status === 'failed';

    let actions = '';
    if (canFix) actions += '<button class="issue-action-btn fix fix-btn" data-num="' + issue.number + '">Fix (worktree)</button>';
    if (canFix) actions += '<button class="issue-action-btn fix spawn-fix-btn" data-num="' + issue.number + '">Fix (inline)</button>';
    if (hasLog) actions += '<button class="issue-action-btn log log-btn" data-num="' + issue.number + '">View Log</button>';
    if (showSummary) actions += '<button class="issue-action-btn summary-toggle" data-num="' + issue.number + '">Summary</button>';

    return '<div class="issue-row" data-num="' + issue.number + '">'
      + '<span class="issue-num" data-num="' + issue.number + '">#' + issue.number + '</span>'
      + '<span class="issue-title">' + escapeHtml(issue.title) + '</span>'
      + '<span class="issue-labels">' + claudeLabels + labels + '</span>'
      + '<span class="issue-status ' + issue.claude_status + '">' + statusLabels[issue.claude_status] + '</span>'
      + '<span class="issue-actions">' + actions + '</span>'
      + '</div>'
      + '<div class="issue-summary" id="summary-' + issue.number + '"></div>';
  }

  async function addFixLabel(num, btn) {
    btn.disabled = true;
    btn.textContent = 'Adding...';
    try {
      const repoPath = repoPathForIssueNumber(num);
      const res = await fetch('/api/issues/' + num + '/add-label', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(withRepoPath({}, repoPath)),
      });
      const data = await res.json();
      if (data.ok) {
        btn.textContent = 'Queued';
        btn.classList.remove('fix');
        // Refresh issues
        setTimeout(loadIssues, 500);
      } else {
        btn.textContent = data.error || 'Error';
        setTimeout(() => { btn.textContent = 'Fix (worktree)'; btn.disabled = false; }, 2000);
      }
    } catch (err) {
      btn.textContent = 'Error';
      setTimeout(() => { btn.textContent = 'Fix (worktree)'; btn.disabled = false; }, 2000);
    }
  }

  async function spawnIssueFix(num, btn) {
    btn.disabled = true;
    btn.textContent = 'Spawning...';
    try {
      const repoPath = repoPathForIssueNumber(num);
      const res = await fetch('/api/issues/' + num + '/spawn', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(withRepoPath({}, repoPath)),
      });
      const data = await res.json();
      if (data.ok) {
        btn.textContent = 'Running';
        btn.classList.remove('fix');
        // Refresh issues + sessions after a short delay
        setTimeout(() => { loadIssues(); loadConversationList(); }, 1000);
      } else {
        btn.textContent = data.error || 'Error';
        setTimeout(() => { btn.textContent = 'Fix (inline)'; btn.disabled = false; }, 2000);
      }
    } catch (err) {
      btn.textContent = 'Error';
      setTimeout(() => { btn.textContent = 'Fix (inline)'; btn.disabled = false; }, 2000);
    }
  }

  async function toggleSummary(num) {
    const el = document.getElementById('summary-' + num);
    if (!el) return;
    if (el.classList.contains('visible')) {
      el.classList.remove('visible');
      return;
    }
    el.textContent = 'Loading...';
    el.classList.add('visible');
    try {
      const repoPath = repoPathForIssueNumber(num);
      const url = repoUrl('/api/issues/' + num + '/summary', repoPath);
      if (!url) throw new Error('repo required');
      const res = await fetch(url);
      const data = await res.json();
      el.textContent = data.summary || 'No Claude summary found on this issue.';
    } catch {
      el.textContent = 'Failed to load summary.';
    }
  }

  // Start polling issues + log list every 10s for real-time status
  function startIssuesPolling() {
    if (issuesPolling) return;
    issuesPolling = setInterval(() => {
      loadIssues();
    }, 10000);
  }

  // ── Vercel deploy status ──
  const $deployDot = document.getElementById('deployDot');
  const $deployPill = document.getElementById('deployPill');
  const $deployLabel = document.getElementById('deployStateLabel');
  // Latest production deploy URL — stashed so the inline-code path-link
  // handler can open URL-shaped paths (`/api/foo/bar`) on the right
  // domain instead of localhost (or, for file paths, the editor).
  let _vercelDeployUrl = '';

  function setDeployPill({ dotClass, label, title, href }) {
    if ($deployDot) $deployDot.className = 'deploy-dot' + (dotClass ? ' ' + dotClass : '');
    if ($deployLabel) $deployLabel.textContent = label;
    if ($deployPill) {
      $deployPill.title = title;
      if (href) {
        $deployPill.setAttribute('href', href);
        $deployPill.style.cursor = 'pointer';
      } else {
        $deployPill.removeAttribute('href');
        $deployPill.style.cursor = 'default';
      }
    }
  }

  async function pollVercelDeploy() {
    const repoPath = selectedRepoPath();
    if (!repoPath) {
      setDeployPill({
        dotClass: '',
        label: 'Vercel',
        title: 'Pick a repo to check Vercel deploy status.',
        href: '',
      });
      _vercelDeployUrl = '';
      return;
    }
    try {
      const res = await fetch(repoUrl('/api/vercel-deploy', repoPath));
      const d = await res.json();
      if (d.disabled) {
        // No .vercel/project.json (and no $VERCEL_PROJECT env override).
        // Friendly hint moved to the pill's title attribute (hover).
        setDeployPill({
          dotClass: '',
          label: 'Vercel',
          title: 'Vercel deploy: not configured. Run `vercel link` in this project to enable.',
          href: '',
        });
        _vercelDeployUrl = '';
        return;
      }
      if (d.error) {
        setDeployPill({ dotClass: '', label: 'Vercel', title: 'Vercel deploy: ' + d.error, href: '' });
        _vercelDeployUrl = '';
        return;
      }
      const st = d.state || 'UNKNOWN';
      const stLower = st === 'READY' ? 'ready' : st === 'ERROR' || st === 'CANCELED' ? 'error' : st === 'BUILDING' || st === 'INITIALIZING' || st === 'QUEUED' ? 'building' : '';

      const age = d.created_at ? timeAgo(d.created_at) : '';
      const dur = d.duration_s ? d.duration_s + 's' : '';
      const commitInfo = d.commit_sha ? d.commit_sha + (d.commit_ref ? ' (' + d.commit_ref + ')' : '') : '';
      const link = d.url ? 'https://' + d.url : '';
      _vercelDeployUrl = link;

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
    } catch {}
  }

  function timeAgo(ts) {
    const s = Math.floor((Date.now() - ts) / 1000);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  // ── Sidebar resizer ──
  const $sidebar = document.querySelector('.sidebar');
  const $resizer = document.getElementById('sidebarResizer');
  if ($sidebar && $resizer) {
    const saved = parseInt(localStorage.getItem('ccc-sidebar-width') || '0', 10);
    // Cap restored width so the conversation pane keeps at least 200px,
    // matching the live drag clamp below.
    const sidebarMax = () => Math.max(240, window.innerWidth - 200);
    if (saved >= 240 && saved <= sidebarMax()) {
      $sidebar.style.width = saved + 'px';
    }
    let startX = 0, startWidth = 0;
    $resizer.addEventListener('mousedown', (e) => {
      e.preventDefault();
      startX = e.clientX;
      startWidth = $sidebar.getBoundingClientRect().width;
      $resizer.classList.add('dragging');
      document.body.classList.add('resizing');
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
    function onMove(e) {
      // Allow sidebar to grow until conv-pane has only 200px left, so the
      // user can squeeze the conversation pane down to a narrow column.
      const w = Math.max(240, Math.min(sidebarMax(), startWidth + (e.clientX - startX)));
      $sidebar.style.width = w + 'px';
    }
    function onUp() {
      $resizer.classList.remove('dragging');
      document.body.classList.remove('resizing');
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      const w = parseInt($sidebar.style.width, 10);
      if (w) localStorage.setItem('ccc-sidebar-width', String(w));
    }
  }

  // ── Split resizer ──
  // conv-panel has a fixed width (stays constant on window resize),
  // kanban-panel flexes to fill remaining space.
  if ($splitResizer && $convPanel && $kanbanPanel) {
    $kanbanPanel.style.flex = '1';
    $kanbanPanel.style.width = '';
    const savedConv = parseInt(localStorage.getItem('ccc-conv-width') || '0', 10);
    if (savedConv >= 40) {
      $convPanel.style.width = savedConv + 'px';
    }
    let splitStartX = 0, splitStartW = 0;
    $splitResizer.addEventListener('mousedown', (e) => {
      e.preventDefault();
      splitStartX = e.clientX;
      splitStartW = $convPanel.getBoundingClientRect().width;
      $splitResizer.classList.add('dragging');
      document.body.classList.add('resizing');
      document.addEventListener('mousemove', onSplitMove);
      document.addEventListener('mouseup', onSplitUp);
    });
    function onSplitMove(e) {
      // Moving mouse right shrinks conv-panel, left grows it.
      // Floor is 40px — anything narrower is unusable and the X button
      // exists for "actually closed".
      const raw = splitStartW - (e.clientX - splitStartX);
      const w = Math.max(40, Math.min(window.innerWidth - 300, raw));
      $convPanel.style.width = w + 'px';
    }
    function onSplitUp() {
      $splitResizer.classList.remove('dragging');
      document.body.classList.remove('resizing');
      document.removeEventListener('mousemove', onSplitMove);
      document.removeEventListener('mouseup', onSplitUp);
      const w = parseInt($convPanel.style.width, 10);
      // No snap-to-close: dragging leaves the pane at whatever width the
      // user picked. The X button (⌘\) is the way to actually hide it.
      if (w) localStorage.setItem('ccc-conv-width', String(w));
    }
  }

  // ── Archive folder filter ──
  function _archiveFolderValue(c) {
    return String((c && (c.folder_path || c.folder_label || c.slug)) || '');
  }

  function _currentRepoArchiveFolder() {
    const current = selectedRepoPath();
    if (current) {
      const match = (repoListState.repos || []).find(repo => repo.path === current);
      return {
        path: current,
        label: (match && match.label) || _pathLeaf(current) || current,
      };
    }
    return { path: '', label: '' };
  }

  function _currentRepoBacklogArchiveRows() {
    if (!Array.isArray(currentRepoBacklogData) || !currentRepoBacklogData.length) return [];
    const folder = _currentRepoArchiveFolder();
    if (!folder.path) return [];
    return currentRepoBacklogData.map(item => {
      const row = Object.assign({}, item);
      row.folder_path = folder.path;
      row.folder_label = folder.label;
      row.slug = folder.path;
      row.git_branch = row.git_branch || row.branch || '';
      row.mtime = row.modified || 0;
      row.size = row.size || 0;
      if (_archivedBacklogIds.has(row.id)) row.archived = true;
      return row;
    });
  }

  // Cross-repo issues → backlog-card-shaped rows. ID format keeps repo
  // identity in the client row so actions can pass a concrete repo_path
  // without switching global server state.
  function _crossRepoIssueArchiveRows() {
    if (!Array.isArray(crossRepoIssuesData) || !crossRepoIssuesData.length) return [];
    return crossRepoIssuesData.map(issue => {
      const repoPath = issue.repo_path || '';
      const repoLabel = issue.repo_label || _pathLeaf(repoPath) || repoPath;
      const slug = repoPath.replace(/[^A-Za-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
      const number = String(issue.number || '');
      const id = `xrepo-issue-${slug}-${number}`;
      const labels = (issue.labels || []).map(l => (l && l.name) || '').filter(Boolean);
      const title = issue.title || '';
      const createdAtStr = issue.createdAt || issue.updatedAt || '';
      let createdTs = 0;
      try { createdTs = createdAtStr ? new Date(createdAtStr).getTime() / 1000 : 0; } catch (_) {}
      const archived = _archivedBacklogIds.has(id);
      return {
        id,
        session_id: id,
        display_name: `#${number}: ${title}`,
        first_message: issue.body || '',
        name_overridden: false,
        source: 'backlog',
        backlog_type: 'github',
        issue_number: number,
        issue_url: issue.url || '',
        issue_labels: labels,
        issue_created_at: createdAtStr,
        issue_state: issue.state || 'OPEN',
        issue_state_reason: issue.stateReason || null,
        modified: createdTs,
        size: 0,
        branch: '',
        is_live: false,
        archived,
        verified: false,
        has_edit: false,
        has_commit: false,
        has_push: false,
        last_event_type: null,
        pending_tool: null,
        pending_file: null,
        sidecar_status: null,
        sidecar_tool: null,
        sidecar_file: null,
        sidecar_has_writes: false,
        sidecar_ts: 0,
        // Folder chip — same fields the conversation rows use.
        folder_path: repoPath,
        folder_label: repoLabel,
        slug: repoPath,
        // Used by the Start handler to spawn into the right folder.
        spawn_cwd: repoPath,
      };
    });
  }

  function _archiveRowsWithBacklog() {
    const rows = Array.isArray(archiveData) ? archiveData.slice() : [];
    const seen = new Set(rows.map(c => c && (c.session_id || c.id)).filter(Boolean));
    // Cross-repo issues take precedence over single-repo backlog injection
    // — they cover the selected repo's issues too, so dropping the single-
    // repo version avoids duplicate rows when /api/issues/all responds.
    const issueRows = _crossRepoIssueArchiveRows();
    const sourceRows = issueRows.length ? issueRows : _currentRepoBacklogArchiveRows();
    for (const row of sourceRows) {
      const key = row.session_id || row.id;
      if (key && seen.has(key)) continue;
      rows.push(row);
      if (key) seen.add(key);
    }
    return rows;
  }

  function _archiveFolderOptions() {
    const knownRepos = (repoListState && Array.isArray(repoListState.repos)) ? repoListState.repos : [];
    if (knownRepos.length) {
      return knownRepos.map(repo => ({
        value: repo.path,
        path: repo.path,
        baseLabel: repo.label || _pathLeaf(repo.path) || repo.path,
        label: repo.label || _pathLeaf(repo.path) || repo.path,
      }));
    }

    // Fallback for the narrow window before /api/repo/list returns, or if it
    // fails: derive options from the archive itself. This preserves behavior
    // without making the primary UI depend on server-side switching.
    const byPath = new Map();
    for (const c of (Array.isArray(archiveData) ? archiveData : [])) {
      const value = _archiveFolderValue(c);
      if (!value || byPath.has(value)) continue;
      byPath.set(value, {
        value,
        path: value,
        baseLabel: c.folder_label || _pathLeaf(value) || value,
      });
    }
    const counts = new Map();
    for (const item of byPath.values()) {
      counts.set(item.baseLabel, (counts.get(item.baseLabel) || 0) + 1);
    }
    const options = Array.from(byPath.values()).map(item => {
      let label = item.baseLabel;
      if ((counts.get(item.baseLabel) || 0) > 1) {
        const parent = _pathParentLeaf(item.path);
        label = parent ? `${item.baseLabel} (${parent})` : item.path;
      }
      return { ...item, label };
    });
    options.sort((a, b) => a.label.localeCompare(b.label));
    return options;
  }

  function renderArchiveFolderFilter() {
    if (!$convFolderFilter) return;
    const options = _archiveFolderOptions();
    const hasCurrent = archiveFolderFilter === ARCHIVE_FOLDER_ALL
      || options.some(opt => opt.value === archiveFolderFilter);
    if (!hasCurrent) {
      archiveFolderFilter = ARCHIVE_FOLDER_ALL;
      try { localStorage.setItem(ARCHIVE_FOLDER_FILTER_KEY, archiveFolderFilter); } catch (_) {}
    }

    $convFolderFilter.innerHTML = '';
    const all = document.createElement('option');
    all.value = ARCHIVE_FOLDER_ALL;
    all.textContent = 'All';
    all.title = 'Show conversations from every folder';
    $convFolderFilter.appendChild(all);

    for (const optData of options) {
      const opt = document.createElement('option');
      opt.value = optData.value;
      opt.textContent = optData.label;
      opt.title = optData.path;
      $convFolderFilter.appendChild(opt);
    }
    $convFolderFilter.value = archiveFolderFilter;
  }

  function setArchiveFolderFilter(value, opts = {}) {
    archiveFolderFilter = value || ARCHIVE_FOLDER_ALL;
    try { localStorage.setItem(ARCHIVE_FOLDER_FILTER_KEY, archiveFolderFilter); } catch (_) {}
    renderArchiveFolderFilter();
    renderRepoSidebar();
    updateRepoPickerVisibility();
    if (opts.render !== false) {
      renderArchiveList(document.getElementById('convSearch')?.value || '');
    }
    loadAttentionList();
    refreshWorktreesBadge();
    pollVercelDeploy();
    pollGcActive();
    setInterval(pollGcActive, 15000);
  }

  const $gcActiveBtn = document.getElementById('gcActiveBtn');
  let _gcActiveChats = [];
  async function pollGcActive() {
    try {
      const data = await fetch('/api/group-chats/active').then(r => r.json());
      const prev = _gcActiveChats.length;
      _gcActiveChats = (data.chats || []);
      if ($gcActiveBtn) {
        if (_gcActiveChats.length === 0) {
          $gcActiveBtn.style.display = 'none';
        } else {
          const n = _gcActiveChats.length;
          $gcActiveBtn.textContent = n === 1
            ? '💬 1 active coordination'
            : `💬 ${n} active coordinations`;
          $gcActiveBtn.style.display = '';
        }
      }
      // Re-render the list whenever the active-chat set changes so the
      // "In Group Chat" section appears/disappears without waiting for
      // the next sessions poll.
      if (_gcActiveChats.length !== prev) {
        renderConversationList(conversationsData);
      }
    } catch (_) {}
  }
  if ($gcActiveBtn) {
    $gcActiveBtn.addEventListener('click', () => {
      if (!_gcActiveChats.length) return;
      if (_gcReaderPath) {
        closeGroupChatReader();
      } else {
        const c = _gcActiveChats[0];
        openGroupChatReader(c.path_tilde, c.topic, c.mode, true);
      }
    });
  }

  // ── Sidebar repo picker ──
  // Legacy switcher code is intentionally dormant in the archive-filter UI.
  // It stays for Phase C cleanup, but no control is rendered for it now.
  const ALL_REPOS_SENTINEL = '__all_repos__';
  const PICKER_SENTINEL = '__pick__';
  const $sbRepoPicker = document.getElementById('sbRepoPicker');

  // ── Multi-repo: peer registry ────────────────────────────────────────────
  // The dropdown is a local filter: All or one concrete repo path. The peer
  // registry still powers discoverability elsewhere, but selecting a repo no
  // longer mutates server state or navigates away.
  let peerState = { peers: [], identity: null };

  async function loadPeerRegistry() {
    let peers = [];
    let identity = null;
    try {
      const [r1, r2] = await Promise.all([
        fetch('/api/registry'),
        fetch('/api/identity'),
      ]);
      if (r1.ok) {
        const d1 = await r1.json();
        peers = Array.isArray(d1.peers) ? d1.peers : [];
      }
      if (r2.ok) identity = await r2.json();
    } catch (_) { /* best-effort — picker is decorative on failure */ }
    peerState = { peers, identity };
    return peerState;
  }

  function _appendAllReposPickerOption() {
    if (!$sbRepoPicker) return;
    const opt = document.createElement('option');
    opt.value = ALL_REPOS_SENTINEL;
    opt.textContent = 'All';
    opt.title = "Browse conversations from every folder you've used with Claude Code.";
    opt.selected = true;
    $sbRepoPicker.appendChild(opt);
  }

  function _syncRepoPickerSelection() {
    if (!$sbRepoPicker) return;
    $sbRepoPicker.setAttribute('aria-hidden', 'false');
    const desired = selectedRepoPath() || ALL_REPOS_SENTINEL;
    if (desired && Array.from($sbRepoPicker.options).some(opt => opt.value === desired)) {
      $sbRepoPicker.value = desired;
    }
    $sbRepoPicker.dataset.prev = $sbRepoPicker.value;
  }

  function _withArchiveModeOverride(url, on) {
    try {
      const u = new URL(url, window.location.href);
      u.searchParams.set('ccc_archive', on ? '1' : '0');
      return u.toString();
    } catch (_) {
      return url;
    }
  }

  function renderPeerPickerSelect() {
    if (!$sbRepoPicker) return;
    $sbRepoPicker.innerHTML = '';
    _appendAllReposPickerOption();

    const knownRepos = (repoListState && repoListState.repos) ? repoListState.repos : [];
    if (knownRepos.length) {
      const grpRepos = document.createElement('optgroup');
      grpRepos.label = 'Repos';
      for (const repo of knownRepos) {
        const opt = document.createElement('option');
        opt.value = repo.path;
        opt.textContent = repo.label || repo.path;
        opt.title = repo.path;
        grpRepos.appendChild(opt);
      }
      $sbRepoPicker.appendChild(grpRepos);
    }

    if (!knownRepos.length) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No repos found';
      opt.disabled = true;
      $sbRepoPicker.appendChild(opt);
    }

    _syncRepoPickerSelection();
  }

  function updateRepoPickerVisibility() {
    _syncRepoPickerSelection();
  }

  // ── Repo sidebar (vertical icon rail) ──────────────────────────────────
  // Same data as the folder filter, different presentation: persistent left
  // strip with one circle per folder. Clicks filter archive rows locally.
  // Hidden when nothing to show by toggling body.has-repo-rail.
  function _railInitial(label, path) {
    const src = String(label || path || '?').replace(/[^A-Za-z0-9]+/g, ' ').trim();
    return src.charAt(0).toUpperCase() || '?';
  }

  function renderRepoSidebar() {
    const $rail = document.getElementById('ccRepoSidebar');
    if (!$rail) return;
    const folders = _archiveFolderOptions();
    $rail.innerHTML = '';

    for (const folder of folders) {
      const btn = document.createElement('button');
      btn.type = 'button';
      const isActive = archiveFolderFilter === folder.value;
      btn.className = 'rail-btn' + (isActive ? ' is-active' : '');
      btn.textContent = _railInitial(folder.label, folder.path);
      btn.title = `${folder.label}\n${folder.path}`;
      btn.dataset.path = folder.value;
      if (isActive) {
        btn.setAttribute('aria-current', 'true');
      }
      btn.addEventListener('click', () => setArchiveFolderFilter(folder.value));
      $rail.appendChild(btn);
    }

    // Toggle layout class only when there's something to show.
    document.body.classList.toggle('has-repo-rail', folders.length > 0);
  }

  // ── All-repos archive ───────────────────────────────────────────────────
  // Read-only browse of every conversation across every folder under
  // ~/.claude/projects/. The sidebar always renders this view; the folder
  // dropdown narrows the archive locally instead of switching repos.
  let archiveData = [];
  let archiveLoaded = false;

  async function loadArchiveAll() {
    try {
      const r = await fetch('/api/conversations/all');
      if (!r.ok) return [];
      const d = await r.json();
      return Array.isArray(d.conversations) ? d.conversations : [];
    } catch (_) { return []; }
  }

  // Cross-repo open GH issues — populates the archive view's GH Issues
  // section across every known repo. Each issue is transformed to a
  // backlog-card-shaped row by _crossRepoIssueArchiveRows() so the
  // existing classifier renders it under the same section as single-repo
  // issues. Closed issues are filtered out client-side here to keep the
  // section focused on actionable work.
  let crossRepoIssuesData = [];
  let ghIssuesRefreshing = false;
  async function loadCrossRepoIssues() {
    try {
      const r = await fetch('/api/issues/all');
      if (!r.ok) return [];
      const d = await r.json();
      const all = Array.isArray(d.issues) ? d.issues : [];
      // Only OPEN issues in v1 — closed adds noise. The server returns
      // both so the future "view recently completed" toggle has data.
      return all.filter(i => (i.state || '').toUpperCase() === 'OPEN');
    } catch (_) { return []; }
  }

  let _archiveRefreshPromise = null;
  async function refreshArchiveData() {
    if (_archiveRefreshPromise) return _archiveRefreshPromise;
    _archiveRefreshPromise = (async () => {
      try {
        const [convs, issues] = await Promise.all([
          loadArchiveAll(),
          loadCrossRepoIssues(),
          repoListState.repos.length ? Promise.resolve(null) : loadRepoList().catch(() => null),
        ]);
        archiveData = convs;
        crossRepoIssuesData = issues || [];
        archiveLoaded = true;
        renderArchiveFolderFilter();
        renderRepoSidebar();
        return archiveData;
      } finally {
        _archiveRefreshPromise = null;
      }
    })();
    return _archiveRefreshPromise;
  }

  async function refreshGhIssuesSection() {
    if (ghIssuesRefreshing) return;
    ghIssuesRefreshing = true;
    const query = document.getElementById('convSearch')?.value || '';
    try {
      renderArchiveList(query);
      crossRepoIssuesData = await loadCrossRepoIssues();
      archiveLoaded = true;
      renderRepoSidebar();
      renderArchiveList(query);
      showOpToast('GH issues refreshed');
    } catch (err) {
      showOpToast('Could not refresh GH issues: ' + ((err && err.message) || 'unknown error'), 'error');
      renderArchiveList(query);
    } finally {
      ghIssuesRefreshing = false;
      renderArchiveList(document.getElementById('convSearch')?.value || query);
    }
  }

  // Stable hue 0..359 from a string. Used to color folder chips so the
  // same folder always gets the same color across renders.
  function _hashHue(s) {
    let h = 0;
    const str = String(s || '');
    for (let i = 0; i < str.length; i++) {
      h = ((h << 5) - h) + str.charCodeAt(i);
      h |= 0;
    }
    return Math.abs(h) % 360;
  }

  function renderArchiveList(filter) {
    const $list = document.getElementById('convList');
    if (!$list) return;
    if (_renameInProgress) return;
    const q = (filter || '').trim().toLowerCase();
    const archiveRows = _archiveRowsWithBacklog();
    // Never filter by folder — the folder picker controls grouping and the
    // active-chip highlight only. Hiding sessions from other repos breaks
    // worktree sessions and "by time" cross-repo views.
    const byFolder = archiveRows;
    let rows = q ? byFolder.filter(c =>
      // session_id + id let users paste a session UUID into the search box
      // and find the conversation directly — useful when CCC tooling, logs
      // or external scripts surface a UUID without a title.
      ((c.display_name || '') + ' ' + (c.first_message || '') + ' ' + (c.folder_label || '') + ' ' + (c.git_branch || '') + ' ' + (c.branch || '') + ' ' + (c.session_id || '') + ' ' + (c.id || ''))
        .toLowerCase().includes(q)
    ) : byFolder;

    // OR-union with history-search results. The local substring filter
    // above only sees title/branch/folder text; the indexer covers full
    // message content. If a session in the archive matched the indexer
    // for this same query, include it here even if the substring filter
    // rejected it. Decorations (badge + snippet) are applied below in the
    // post-shape pass. Synthetic rows for sessions outside archiveRows
    // are handled in non-archive list mode only for now.
    if (q && _historyState.query === q && _historyState.map.size) {
      const seen = new Set(rows.map(c => c.session_id || c.id));
      const extra = [];
      for (const c of byFolder) {
        const sid = c.session_id || c.id;
        if (!seen.has(sid) && _historyState.map.has(sid)) extra.push(c);
      }
      if (extra.length) rows = rows.concat(extra);
    }

    if (!archiveLoaded && !archiveRows.length) {
      $list.innerHTML = '<div class="archive-empty-state">Loading archive&hellip;</div>';
      return;
    }
    if (!archiveRows.length) {
      $list.innerHTML = '<div class="archive-empty-state">No conversations on disk.</div>';
      return;
    }
    if (!byFolder.length) {
      $list.innerHTML = '<div class="archive-empty-state">No conversations in this folder.</div>';
      return;
    }
    if (!rows.length) {
      $list.innerHTML = '<div class="archive-empty-state">No conversations match your filter.</div>';
      return;
    }

    // Shape archive entries to look like /api/sessions entries so the
    // existing renderConversationList renders them with the regular row
    // template — same font, time format, dividers, hover, click hooks.
    // Cold archive defaults (is_live: false, no sidecar) keep the live-
    // pill code paths quiet; when a peer server is later running for one
    // of these folders, supercharging the row with live data is just a
    // matter of merging the peer's session record into this shape.
    const shaped = rows.map(c => {
      if (c.source === 'backlog') {
        const folderOrphan = !c.folder_path;
        return Object.assign({}, c, {
          id: c.id || c.session_id,
          session_id: c.session_id || c.id,
          first_message: c.first_message || '',
          display_name: c.display_name || '',
          name_overridden: !!c.name_overridden,
          last_assistant_text: '',
          modified: c.modified || c.mtime || 0,
          last_interacted: c.modified || c.mtime || 0,
          size: c.size || 0,
          is_live: !!c.is_live,
          archived: !!c.archived,
          worktree_dirty: !!c.worktree_dirty,
          has_commit: !!c.has_commit,
          has_push: !!c.has_push,
          has_edit: !!c.has_edit,
          tail_pr_number: c.tail_pr_number || null,
          tail_pr_url: c.tail_pr_url || null,
          pr_state: c.pr_state || null,
          pr_notes: Array.isArray(c.pr_notes) ? c.pr_notes : [],
          sidecar_status: c.sidecar_status || null,
          sidecar_tool: c.sidecar_tool || null,
          sidecar_file: c.sidecar_file || null,
          sidecar_ts: c.sidecar_ts || 0,
          sidecar_in_flight: !!c.sidecar_in_flight,
          sidecar_has_writes: !!c.sidecar_has_writes,
          needs_approval: !!c.needs_approval,
          needs_approval_message: c.needs_approval_message || '',
          session_cwd: c.session_cwd || c.folder_path,
          session_cwd_exists: !!c.folder_path,
          session_cwd_is_worktree: !!c.session_cwd_is_worktree,
          branch: c.branch || c.git_branch || '',
          effective_branch: c.effective_branch || null,
          effective_kind: c.effective_kind || null,
          folder_label_chip: c.folder_label,
          folder_chip_hue: _hashHue(c.folder_label || c.slug),
          folder_chip_orphan: folderOrphan,
          folder_path: c.folder_path,
          worktree_label: c.worktree_label || null,
          pinned_repo: !!c.pinned_repo,
        });
      }
      const folderOrphan = (c.folder_path === c.slug);
      return {
        id: c.session_id,
        session_id: c.session_id,
        first_message: c.first_message,
        // #1 rename overrides + #9 archived set come from server-side
        // global state files (session-names.json / archived-conversations.json).
        display_name: c.display_name || '',
        name_overridden: !!c.name_overridden,
        last_assistant_text: '',
        modified: c.mtime,
        last_interacted: c.mtime,
        size: c.size || 0,
        // Round 2: live flag + state pills, all derived server-side
        // from sidecar / git status / JSONL events with mtime-based
        // caching. Powers the blue live dot + uncommitted/committed/
        // pushed/no-edits chips + PR #N + Ready-to-merge bucket.
        is_live: !!c.is_live,
        archived: !!c.archived,
        worktree_dirty: !!c.worktree_dirty,
        has_commit: !!c.has_commit,
        has_push: !!c.has_push,
        has_edit: !!c.has_edit,
        tail_pr_number: c.tail_pr_number || null,
        tail_pr_url: c.tail_pr_url || null,
        pr_state: c.pr_state || null,
        pr_notes: Array.isArray(c.pr_notes) ? c.pr_notes : [],
        // Sidecar overlay (Round 3) — only meaningful for live rows.
        // The renderer reads these directly to draw the live tool pill
        // ("▶ Editing foo.ts"), sending pulse, and needs-approval signal.
        sidecar_status: c.sidecar_status || null,
        sidecar_tool: c.sidecar_tool || null,
        sidecar_file: c.sidecar_file || null,
        sidecar_ts: c.sidecar_ts || 0,
        sidecar_in_flight: !!c.sidecar_in_flight,
        sidecar_has_writes: !!c.sidecar_has_writes,
        pending_tool: c.pending_tool || null,
        pending_file: c.pending_file || null,
        last_event_type: c.last_event_type || null,
        needs_approval: !!c.needs_approval,
        needs_approval_message: c.needs_approval_message || '',
        source: c.source || 'interactive',
        session_cwd: c.session_cwd || c.folder_path,
        session_cwd_exists: !folderOrphan,
        // #4 worktree leaf — the renderer reads session_cwd_is_worktree.
        session_cwd_is_worktree: !!c.session_cwd_is_worktree,
        branch: c.git_branch || '',
        // Tool-call-inferred effective branch + kind. The renderer reads
        // these to decide the 🌿 worktree leaf and which branch label to
        // show on the chip. Without them, archive rows whose session was
        // launched in a clone but edited a sibling worktree would show
        // the launch branch (e.g. "main") with no leaf, hiding where
        // the actual work happened.
        effective_branch: c.effective_branch || null,
        effective_kind: c.effective_kind || null,
        // #6 — hide the subtitle (last-assistant / first-message preview)
        // in archive mode. The user doesn't read it cross-folder; redundant
        // with the title and just doubles row height.
        _hideAskHtml: true,
        // Folder chip — special fields read by _renderRow.
        folder_label_chip: c.folder_label,
        folder_chip_hue: _hashHue(c.folder_label || c.slug),
        folder_chip_orphan: folderOrphan,
        folder_path: c.folder_path,
        // When the session lives in a sibling worktree dir, this carries
        // the suffix (e.g. "gemini") so the row can render a wt-badge.
        worktree_label: c.worktree_label || null,
        // Pinned-to-this-repo flag — server overrides the row's folder
        // bucket. Renderer adds a 📌 indicator + click-to-unpin handler.
        pinned_repo: !!c.pinned_repo,
      };
    });

    // History-augmentation: badge + snippet for archive entries that the
    // indexer matched for this same query. The conv-item template reads
    // c._historyMatch / c._historySnippet / c._historySource. Skipped
    // when state is stale (debounced fetch hasn't completed for this
    // keystroke yet).
    if (q && _historyState.query === q && _historyState.map.size) {
      for (const s of shaped) {
        const sid = s.session_id || s.id;
        if (sid && _historyState.map.has(sid)) {
          const hit = _historyState.map.get(sid);
          s._historyMatch = true;
          s._historySnippet = hit.snippet;
          s._historySource = hit.source || 'bm25';
          // Archive mode normally hides the ask preview to keep rows
          // single-line; re-enable it for matched rows so the snippet
          // line has somewhere to render.
          s._hideAskHtml = true;
        }
      }
    }

    // Make sure the list is visible (kanban is hidden in setArchiveMode).
    const $kanban = document.getElementById('kanbanBoard');
    if ($kanban) $kanban.style.display = 'none';
    $list.style.display = '';

    // The click-to-currentSession bridge uses sessionIdByConv et al., which
    // are normally populated inside loadConversationList() — a path archive
    // mode bypasses. Without this, clicking an archive row passes undefined
    // to setCurrentSession, currentSession.id stays null, and the input bar
    // hides because hasSession is false. Populate the maps here so the
    // existing right-pane code path works for archive entries.
    for (const c of shaped) {
      if (c.session_id) sessionIdByConv[c.id] = c.session_id;
      if (c.session_cwd) sessionCwdByConv[c.id] = c.session_cwd;
      sessionCwdExistsByConv[c.id] = !!c.session_cwd_exists;
      sessionSourceByConv[c.id] = c.source || 'interactive';
    }
    // Also keep conversationsData in sync so downstream code (selection
    // restore, etc.) sees a non-empty list while in archive mode. This
    // gets reset on toggle-off via loadConversationList().
    conversationsData = applyConvSort(_applyOptimisticTouches(shaped));

    renderConversationList(conversationsData);
  }

  async function setArchiveMode() {
    try { localStorage.setItem(_ARCHIVE_MODE_KEY, '1'); } catch (_) {}
    updateRepoPickerVisibility();
    const $list = document.getElementById('convList');
    const $kanban = document.getElementById('kanbanBoard');
    // Hide kanban if it was visible; the archive renders into the same
    // #convList slot.
    if ($kanban) $kanban.style.display = 'none';
    if ($list) {
      $list.style.display = '';
      $list.innerHTML = '<div class="archive-empty-state">Loading archive…</div>';
    }
    await refreshArchiveData();
    renderArchiveList(document.getElementById('convSearch')?.value || '');
  }

  (function wireArchiveMode() {
    updateRepoPickerVisibility();
    renderArchiveFolderFilter();
    if ($convFolderFilter) {
      $convFolderFilter.addEventListener('change', () => {
        setArchiveFolderFilter($convFolderFilter.value || ARCHIVE_FOLDER_ALL);
      });
    }
    const $search = document.getElementById('convSearch');
    if ($search) {
      $search.addEventListener('input', () => {
        renderArchiveList($search.value);
      });
    }
    // Boot kick: wait for the first /api/sessions response before kicking
    // the archive walk. Both endpoints share CPU/subprocess slots in the
    // same Python process; running the slow one (cross-folder JSONL + git
    // ops, ~12s on a cold cache) in parallel with /api/sessions starved it.
    // Waiting until sessions returns means the selected repo is interactive
    // in <1s, then the archive populates the sidebar.
    {
      // Show the placeholder immediately so the sidebar isn't blank
      // during the wait — the actual fetch fires after sessions land.
      const $list = document.getElementById('convList');
      const $kanban = document.getElementById('kanbanBoard');
      if ($kanban) $kanban.style.display = 'none';
      if ($list) {
        $list.style.display = '';
        $list.innerHTML = '<div class="archive-empty-state">Loading archive&hellip;</div>';
      }
      _firstSessionsLoaded.then(() => setArchiveMode());
    }
  })();

  // ── Legacy repo-list (used by the modal + custom-repos browser) ─────────
  // Shared state so the modal can reuse what the dropdown already fetched
  // and the dropdown can refresh itself after an add-via-modal.
  async function loadRepoList() {
    const r = await fetch('/api/repo/list');
    const d = await r.json();
    repoListState = {
      repos: d.repos || [],
      current: d.current || '',
      recent: d.recent || [],
    };
    return repoListState;
  }

  function renderPickerSelect() {
    if (!$sbRepoPicker) return;
    const { repos } = repoListState;
    $sbRepoPicker.innerHTML = '';
    _appendAllReposPickerOption();
    for (const repo of repos) {
      const opt = document.createElement('option');
      opt.value = repo.path;
      opt.textContent = repo.label || repo.path;
      opt.title = repo.path;
      $sbRepoPicker.appendChild(opt);
    }
    // Sentinel option — selecting it opens the modal instead of switching.
    const sep = document.createElement('option');
    sep.disabled = true;
    sep.textContent = '──────────';
    $sbRepoPicker.appendChild(sep);
    const pick = document.createElement('option');
    pick.value = PICKER_SENTINEL;
    pick.textContent = 'Pick a repo…';
    $sbRepoPicker.appendChild(pick);
    // Remember current selection so we can restore on failure / cancel.
    _syncRepoPickerSelection();
  }

  // Shared repo-picker handler used by both the dropdown and the modal.
  // It only changes local UI filter state; the server has no active repo.
  async function switchToRepo(targetPath, targetLabel) {
    if (!targetPath) return;
    setArchiveFolderFilter(targetPath);
    showOpToast('Filtered to ' + (targetLabel || _pathLeaf(targetPath) || targetPath));
  }

  if ($sbRepoPicker) {
    // Initial: load both the peer registry (running CCC servers) and the
    // legacy repo list (all known repos, including not-running ones for the
    // "switch this server to…" group). Render once both are in.
    (async () => {
      try {
        await Promise.all([loadPeerRegistry(), loadRepoList()]);
        renderPeerPickerSelect();
        renderRepoSidebar();
      } catch (e) { /* picker is best-effort — failure shouldn't break the page */ }
    })();

    // Poll the registry every 10s so a sibling server starting after page
    // load shows up without a manual refresh. Pause when the tab is hidden.
    // The repo list is comparatively static — we only refresh it on visibility
    // change, not on every 10s tick.
    let _peerPollId = null;
    const _peerStartPoll = () => {
      if (_peerPollId) return;
      _peerPollId = setInterval(async () => {
        await loadPeerRegistry();
        renderPeerPickerSelect();
        renderRepoSidebar();
      }, 10000);
    };
    const _peerStopPoll = () => {
      if (!_peerPollId) return;
      clearInterval(_peerPollId);
      _peerPollId = null;
    };
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        Promise.all([loadPeerRegistry(), loadRepoList()]).then(() => {
          renderPeerPickerSelect();
          renderRepoSidebar();
        });
        _peerStartPoll();
      } else {
        _peerStopPoll();
      }
    });
    if (document.visibilityState === 'visible') _peerStartPoll();

    $sbRepoPicker.addEventListener('change', async () => {
      const target = $sbRepoPicker.value;
      const selectedLabel = $sbRepoPicker.options[$sbRepoPicker.selectedIndex]?.textContent || target;
      if (!target) return;
      if (target === ALL_REPOS_SENTINEL) {
        await setArchiveMode();
        return;
      }
      if (target === PICKER_SENTINEL) {
        await openRepoPickerModal();
        _syncRepoPickerSelection();
        return;
      }
      setArchiveFolderFilter(target);
    });

    // Stash the current value so we can restore on failure.
    $sbRepoPicker.addEventListener('focus', () => {
      $sbRepoPicker.dataset.prev = $sbRepoPicker.value;
    });
  }

  // ── Repo picker modal ──
  const $rpm = document.getElementById('repoPickerModal');
  const $rpmBackdrop = document.getElementById('rpmBackdrop');
  const $rpmCancelBtn = document.getElementById('rpmCancelBtn');
  const $rpmBrowseBtn = document.getElementById('rpmBrowseBtn');
  const $rpmRecentLabel = document.getElementById('rpmRecentLabel');
  const $rpmRecentList = document.getElementById('rpmRecentList');
  const $rpmOtherLabel = document.getElementById('rpmOtherLabel');
  const $rpmOtherList = document.getElementById('rpmOtherList');
  const $rpmError = document.getElementById('rpmError');

  function rpmShowError(msg) {
    if (!$rpmError) return;
    $rpmError.textContent = msg;
    $rpmError.classList.add('visible');
  }
  function rpmClearError() {
    if (!$rpmError) return;
    $rpmError.textContent = '';
    $rpmError.classList.remove('visible');
  }
  function renderRpmList($el, repos, current) {
    $el.innerHTML = '';
    if (!repos.length) {
      const empty = document.createElement('div');
      empty.className = 'rpm-empty';
      empty.textContent = 'No repos here yet.';
      $el.appendChild(empty);
      return;
    }
    for (const repo of repos) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'rpm-item';
      if (repo.path === current) btn.classList.add('active');
      const label = document.createElement('div');
      label.className = 'rpm-item-label';
      label.textContent = repo.label || repo.path;
      if (repo.path === current) {
        const badge = document.createElement('span');
        badge.className = 'rpm-item-current';
        badge.textContent = 'selected';
        label.appendChild(badge);
      }
      const path = document.createElement('div');
      path.className = 'rpm-item-path';
      path.textContent = repo.path;
      btn.appendChild(label);
      btn.appendChild(path);
      btn.addEventListener('click', () => {
        if (repo.path === current) { closeRepoPickerModal(); return; }
        closeRepoPickerModal();
        switchToRepo(repo.path, repo.label || repo.path);
      });
      $el.appendChild(btn);
    }
  }

  function renderRpmLists() {
    const { repos, recent } = repoListState;
    const current = selectedRepoPath();
    const recentSet = new Set(recent || []);
    const byPath = {};
    for (const r of repos) byPath[r.path] = r;
    // Recent: intersection of recent[] and repos[], preserving recent order.
    const recentRepos = [];
    for (const p of recent) {
      if (byPath[p]) recentRepos.push(byPath[p]);
    }
    // Other: everything else, alphabetical by label (load_known_repos already
    // returns them alphabetical but recency-sort may have reordered).
    const other = repos.filter(r => !recentSet.has(r.path))
      .slice()
      .sort((a, b) => (a.label || a.path).localeCompare(b.label || b.path));
    if (recentRepos.length) {
      $rpmRecentLabel.style.display = '';
      $rpmRecentList.style.display = '';
      renderRpmList($rpmRecentList, recentRepos, current);
    } else {
      $rpmRecentLabel.style.display = 'none';
      $rpmRecentList.style.display = 'none';
    }
    renderRpmList($rpmOtherList, other, current);
  }

  async function openRepoPickerModal() {
    if (!$rpm) return;
    rpmClearError();
    // Refresh from server so the modal reflects any repos added elsewhere.
    try { await loadRepoList(); } catch (_) { /* use stale state */ }
    renderRpmLists();
    $rpm.classList.add('open');
  }
  function closeRepoPickerModal() {
    if (!$rpm) return;
    $rpm.classList.remove('open');
  }

  if ($rpmBackdrop) $rpmBackdrop.addEventListener('click', closeRepoPickerModal);
  if ($rpmCancelBtn) $rpmCancelBtn.addEventListener('click', closeRepoPickerModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $rpm && $rpm.classList.contains('open')) {
      closeRepoPickerModal();
    }
  });

  if ($rpmBrowseBtn) {
    $rpmBrowseBtn.addEventListener('click', async () => {
      rpmClearError();
      $rpmBrowseBtn.disabled = true;
      const prevText = $rpmBrowseBtn.textContent;
      $rpmBrowseBtn.textContent = 'Waiting for folder selection…';
      try {
        // Native macOS folder chooser via osascript, server-side. Blocks
        // until the user picks or cancels — that's fine because the server
        // is threaded.
        const r = await fetch('/api/fs/pick-folder', { method: 'POST' });
        const d = await r.json();
        if (d.cancelled) return;  // user clicked Cancel — no-op
        if (!d.ok) { rpmShowError(d.error || 'Could not open folder picker.'); return; }
        // Persist the new path so it appears in the picker, then filter to it.
        const addRes = await fetch('/api/repo/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: d.path }),
        });
        const addD = await addRes.json();
        if (!addD.ok) { rpmShowError(addD.error || 'Could not register the picked folder.'); return; }
        closeRepoPickerModal();
        const picked = (addD.repos || []).find(r => r.path === addD.path);
        switchToRepo(addD.path, picked ? (picked.label || picked.path) : addD.path);
      } catch (e) {
        rpmShowError(String(e.message || e));
      } finally {
        $rpmBrowseBtn.disabled = false;
        $rpmBrowseBtn.textContent = prevText;
      }
    });
  }

  // View menu — open/close popover containing the secondary toggles.
  // Closes on outside click (Escape closes too). Active dot on the trigger
  // when any of the contained toggles is currently on.
  (function () {
    const $btn = document.getElementById('kptViewMenuBtn');
    const $menu = document.getElementById('kptViewMenu');
    if (!$btn || !$menu) return;
    function setOpen(open) {
      $menu.style.display = open ? 'flex' : 'none';
      $btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    }
    $btn.addEventListener('click', (e) => {
      e.stopPropagation();
      setOpen($menu.style.display === 'none');
    });
    document.addEventListener('click', (e) => {
      if ($menu.style.display === 'flex' && !$menu.contains(e.target) && e.target !== $btn) {
        setOpen(false);
      }
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && $menu.style.display === 'flex') setOpen(false);
    });
    // Hover style for items inside the menu
    $menu.querySelectorAll('button').forEach(b => {
      b.addEventListener('mouseenter', () => { b.style.background = 'rgba(255,255,255,0.05)'; });
      b.addEventListener('mouseleave', () => { b.style.background = 'transparent'; });
    });
  })();

  // Setup banner — surface failed healthcheck checks so first-time users
  // see exactly what's missing instead of an empty UI with no explanation.
  // Lives in the layout-agnostic top bar so it shows in both list mode and
  // kanban-split mode. Re-runs on page load only; click Refresh to re-probe.
  (async function renderSetupBanner() {
    const $banner = document.getElementById('setupBanner');
    if (!$banner) return;
    try {
      const r = await fetch('/api/healthcheck');
      const d = await r.json();
      const checks = d.checks || [];
      const iconFor = (s) => s === 'ok' ? '✓' : (s === 'warn' ? '⚠' : '✗');
      // Multi-repo mode makes the watched-repo line noisy; only surface checks
      // that need attention. Healthy dependency state can stay out of the way.
      const visibleChecks = checks.filter(c => c.status !== 'ok');
      if (!visibleChecks.length) {
        $banner.innerHTML = '';
        $banner.classList.remove('has-issues', 'is-ok');
        return;
      }
      const rows = visibleChecks.map(c => {
        const tt = (c.hint && c.status !== 'ok') ? `${c.message} — ${c.hint}` : c.message;
        return `<span class="ccc-setup-row ${c.status}" title="${escapeHtml(tt)}">
          <span class="icon">${iconFor(c.status)}</span>
          <span>${escapeHtml(c.label === 'Watched repo' ? c.message : c.label)}</span>
        </span>`;
      }).join('');
      $banner.innerHTML = `<div class="ccc-setup-banner-rows">${rows}</div>`;
      $banner.classList.toggle('is-ok', d.overall === 'ok');
      $banner.classList.add('has-issues');
    } catch (e) {
      // Healthcheck failed — keep banner hidden rather than showing a scary error.
      $banner.style.display = 'none';
    }
  })();

  const $kptSearch = document.getElementById('kptSearch');
  const $kptRefreshBtn = document.getElementById('kptRefreshBtn');
  const $kptRecentBtn = document.getElementById('kptRecentBtn');
  // Spawn-engine state. Source of truth = localStorage. Three DOM nodes
  // mirror it: the inline bottom-bar selector (new-session mode only),
  // the Kanban toolbar selector, and the new-session modal selector.
  // setSpawnEngine() persists + propagates to all three; getSpawnEngine()
  // is the canonical read used by every spawn handler.
  const $convInputEngineSelect = document.getElementById('convInputEngineSelect');
  const $kptToolbarEngineSelect = document.getElementById('kptToolbarEngineSelect');
  function getSpawnEngine() {
    try {
      const v = localStorage.getItem('ccc.spawnEngine');
      if (v === 'claude' || v === 'codex' || v === 'gemini') return v;
    } catch (_) {}
    return 'claude';
  }
  function setSpawnEngine(v) {
    if (v !== 'claude' && v !== 'codex' && v !== 'gemini') return;
    try { localStorage.setItem('ccc.spawnEngine', v); } catch (_) {}
    // $nsmEngineSelect is declared further down in this script; by the
    // time any user interaction calls setSpawnEngine() it will exist.
    [$convInputEngineSelect, $kptToolbarEngineSelect,
     (typeof $nsmEngineSelect !== 'undefined' ? $nsmEngineSelect : null)]
      .forEach(s => { if (s && s.value !== v) s.value = v; });
  }
  [$convInputEngineSelect, $kptToolbarEngineSelect].forEach(sel => {
    if (!sel) return;
    sel.value = getSpawnEngine();
    sel.addEventListener('change', () => setSpawnEngine(sel.value));
  });
  { const $ia = document.getElementById('convInputIssueAction');
    if ($ia) $ia.addEventListener('change', () => updateInputBar()); }
  // Probe alternate-engine availability so a user with no CLI install sees the
  // option greyed out instead of getting a 503 mid-spawn. Polled on
  // window focus too — handles "I just installed it; refresh the UI"
  // without a hard reload.
  async function refreshEngineAvailability() {
    async function probe(engine, endpoint, label) {
      try {
        const r = await fetch(endpoint);
        const d = await r.json();
        const reason = d.available ? '' : (d.reason || (label + ' CLI not found'));
        const selectors = [$convInputEngineSelect, $kptToolbarEngineSelect,
          (typeof $nsmEngineSelect !== 'undefined' ? $nsmEngineSelect : null)];
        selectors.forEach(sel => {
          const opt = sel && sel.querySelector('option[value="' + engine + '"]');
          if (!opt) return;
          opt.disabled = !d.available;
          opt.title = reason;
          opt.textContent = d.available ? engine : (engine + ' (unavailable)');
        });
        if (!d.available && getSpawnEngine() === engine) setSpawnEngine('claude');
      } catch (_) {}
    }
    await Promise.all([
      probe('codex', '/api/sessions/spawn-codex/availability', 'Codex'),
      probe('gemini', '/api/sessions/spawn-gemini/availability', 'Gemini'),
    ]);
  }
  async function refreshCodexAvailability() {
    try {
      await refreshEngineAvailability();
    } catch (_) {}
  }
  refreshCodexAvailability();
  window.addEventListener('focus', refreshCodexAvailability);
  // Hide-descriptions toggle
  const $kptDescToggle = document.getElementById('kptDescToggle');
  if ($kptDescToggle) {
    const applyDescHidden = (hidden) => {
      document.body.classList.toggle('hide-descs', hidden);
      $kptDescToggle.classList.toggle('active', hidden);
      $kptDescToggle.textContent = hidden ? 'Expand' : 'Compact';
    };
    applyDescHidden(localStorage.getItem('ccc-hide-descs') === '1');
    $kptDescToggle.addEventListener('click', () => {
      const nowHidden = !document.body.classList.contains('hide-descs');
      localStorage.setItem('ccc-hide-descs', nowHidden ? '1' : '0');
      applyDescHidden(nowHidden);
    });
  }
  // Git-only toggle: hide cards not linked to a GitHub issue
  const $kptGitOnlyToggle = document.getElementById('kptGitOnlyToggle');
  if ($kptGitOnlyToggle) {
    const applyGitOnly = (on) => {
      window._gitOnlyFilter = on;
      $kptGitOnlyToggle.classList.toggle('active', on);
      renderSidebar(filterConversations($convSearch.value));
    };
    $kptGitOnlyToggle.addEventListener('click', () => {
      applyGitOnly(!window._gitOnlyFilter);
      localStorage.setItem('ccc-git-only', window._gitOnlyFilter ? '1' : '0');
    });
    if (localStorage.getItem('ccc-git-only') === '1') applyGitOnly(true);
  }
  const $kptNewSession = document.getElementById('kptNewSession');
  const $kptRunBtn = document.getElementById('kptRunBtn');

  // Search: sync with sidebar search
  if ($kptSearch) {
    $kptSearch.addEventListener('input', () => {
      $convSearch.value = $kptSearch.value;
      updateConversationSearchClear();
      renderSidebar(filterConversations($kptSearch.value));
    });
  }
  // Recency cycle: Off → Last 10h → Last 7d → Off.
  // One button, three states. Label and active-style update each cycle.
  const RECENCY_LABELS = { '': 'Recency: off', '10h': 'Last 10h', '7d': 'Last 7d' };
  const RECENCY_NEXT = { '': '10h', '10h': '7d', '7d': '' };
  function updateRecentBtn() {
    if (!$kptRecentBtn) return;
    $kptRecentBtn.textContent = RECENCY_LABELS[recencyFilter];
    $kptRecentBtn.classList.toggle('active', !!recencyFilter);
    $kptRecentBtn.title = recencyFilter
      ? 'Showing only sessions/issues from the last ' + (recencyFilter === '10h' ? '10 hours' : '7 days') + '. Click to cycle.'
      : 'Click to limit to last 10h, then 7d, then off.';
  }
  updateRecentBtn();
  if ($kptRecentBtn) {
    $kptRecentBtn.addEventListener('click', () => {
      recencyFilter = RECENCY_NEXT[recencyFilter];
      showRecentOnly = !!recencyFilter;
      localStorage.setItem('ccc-show-recent', recencyFilter || '0');
      updateRecentBtn();
      renderSidebar(filterConversations($convSearch.value));
    });
  }
  // Refresh
  const $kanbanReloadBtn = document.getElementById('kanbanReloadBtn');
  if ($kanbanReloadBtn) {
    $kanbanReloadBtn.addEventListener('click', () => {
      try { location.reload(true); } catch (_) { location.reload(); }
    });
  }
  if ($kptRefreshBtn) {
    $kptRefreshBtn.addEventListener('click', refreshConversationList);
  }

  // ── Bulk summarize titles ──
  // Walk every conversation card whose name hasn't been user-touched and
  // whose first_message reads like a long prompt, fire the per-card
  // summarize endpoint with limited parallelism. Per-card status is
  // surfaced via a toast; full pass refreshes the list at the end.
  const $kptSummarizeAllBtn = document.getElementById('kptSummarizeAllBtn');
  if ($kptSummarizeAllBtn) {
    $kptSummarizeAllBtn.addEventListener('click', async () => {
      // Respect the current recency filter — if the user narrowed to "Last 7d"
      // for a screenshot, only summarize the visible cards instead of all 200.
      const cutoff = recencyCutoffSec();
      let candidates = (conversationsData || []).filter(c => {
        if (c.name_overridden) return false;
        if (cutoff !== 0 && (c.modified || 0) < cutoff) return false;
        // Real Claude sessions OR GitHub-backed backlog cards. Both can be
        // summarized; the bulk worker picks the right endpoint per card.
        const isSession = (c.session_id || '').length >= 32;
        const isGhBacklog = c.source === 'backlog' && c.backlog_type === 'github' && c.issue_number;
        if (!isSession && !isGhBacklog) return false;
        // Skip session cards whose first_message is already short.
        if (isSession && (c.first_message || '').length < 60) return false;
        return true;
      });
      if (candidates.length === 0) {
        showOpToast('No untitled cards to summarize.', 'info');
        return;
      }
      // Cap at 30 by default — larger batches take minutes and most users
      // just want a clean batch for triage / screenshots. They can re-run
      // to chew through the backlog.
      const SOFT_CAP = 30;
      let capped = false;
      if (candidates.length > SOFT_CAP) {
        // Sort newest-first so the cap takes the most relevant ones.
        candidates.sort((a, b) => (b.modified || 0) - (a.modified || 0));
        const proceed = confirm(
          candidates.length + ' untitled cards found. That\'s ~'
          + Math.ceil(candidates.length / 8 * 8 / 60) + ' minutes of `claude -p` calls.\n\n'
          + 'OK = generate titles for the ' + SOFT_CAP + ' most recent only (~30s).\n'
          + 'Cancel = do nothing.\n\n'
          + 'Tip: turn on the "Last 7d" recency filter (View ▾ menu) to narrow first.'
        );
        if (!proceed) return;
        candidates = candidates.slice(0, SOFT_CAP);
        capped = true;
      } else {
        if (!confirm('Generate AI titles for ' + candidates.length + ' cards? '
                     + 'Each call uses claude haiku (~8s, $0.001). Total ~'
                     + Math.ceil(candidates.length / 8 * 8) + 's at 8 in parallel.')) {
          return;
        }
      }
      const total = candidates.length;
      let done = 0, failed = 0, inflight = 0;
      const startedAt = Date.now();
      $kptSummarizeAllBtn.disabled = true;
      const origText = $kptSummarizeAllBtn.textContent;
      const updateBtn = () => {
        // Show "running…" before any have completed so it's clear something
        // is happening. ~8s per call at 8 parallel = first completion at ~8s.
        if (done === 0 && failed === 0) {
          $kptSummarizeAllBtn.textContent = '✨ Running… 0/' + total
            + (inflight ? ' (' + inflight + ' in flight)' : '');
        } else {
          const elapsedS = Math.round((Date.now() - startedAt) / 1000);
          $kptSummarizeAllBtn.textContent = '✨ ' + done + '/' + total
            + (failed ? ' (' + failed + ' failed)' : '')
            + ' · ' + elapsedS + 's';
        }
      };
      updateBtn();

      // Throttled queue — 8 in flight. Modern Macs handle parallel `claude -p`
      // subprocesses fine; the bottleneck is API rate-limit, not local CPU.
      // If you start hitting Anthropic 429s, lower this.
      const PARALLEL = 8;
      const queue = candidates.slice();
      async function worker() {
        while (queue.length) {
          const c = queue.shift();
          inflight++;
          updateBtn();
          // Route to the right endpoint based on card type:
          //   - session cards → /api/conversations/<sid>/summarize (reads jsonl)
          //   - GH backlog cards → /api/issues/<n>/summarize-title (reads gh)
          const isGhBacklog = c.source === 'backlog' && c.issue_number;
          const url = isGhBacklog
            ? '/api/issues/' + encodeURIComponent(c.issue_number) + '/summarize-title'
            : '/api/conversations/' + encodeURIComponent(c.session_id) + '/summarize';
          const body = isGhBacklog ? withRepoPath({}, rowRepoPath(c)) : {};
          try {
            const r = await fetch(url, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            });
            const d = await r.json();
            if (d.ok) done++; else failed++;
          } catch (_) { failed++; }
          inflight--;
          updateBtn();
        }
      }
      const workers = Array.from({ length: PARALLEL }, () => worker());
      await Promise.all(workers);

      $kptSummarizeAllBtn.disabled = false;
      $kptSummarizeAllBtn.textContent = origText;
      showOpToast('Summarized ' + done + '/' + total + (failed ? ' (' + failed + ' failed)' : ''), failed ? 'error' : 'ok');
      // Refresh so the new titles render.
      refreshConversationList();
    });
  }

  // New session + Run
  if ($kptRunBtn) {
    $kptRunBtn.addEventListener('click', async () => {
      let prompt = ($kptNewSession && $kptNewSession.value || '').trim();
      if (!prompt) return;
      const isPkoodPrefix = prompt.startsWith('pkood:');
      const engine = isPkoodPrefix ? 'pkood' : getSpawnEngine();
      const repoPath = requireSelectedRepo('New session');
      if (!repoPath) return;
      const $kptWorktreeToggle = document.getElementById('kptWorktreeToggle');
      const useWorktree = !!($kptWorktreeToggle && $kptWorktreeToggle.checked);
      if (isPkoodPrefix) prompt = prompt.slice(6).trim();
      if (!prompt) return;
      // Show the placeholder immediately — don't wait for the spawn POST to
      // return. Users were staring at a blank board for a beat because the
      // placeholder only materialized after /api/sessions/spawn responded.
      const subject = prompt.length > 60 ? prompt.slice(0, 60) + '…' : prompt;
      const tempPid = 'tmp-' + Date.now();
      // Source for the optimistic card: alternate engines render their chip;
      // 'pkood' keeps the orange pkood chip; 'claude' uses
      // 'interactive' (no chip).
      const cardSource = engine === 'codex' ? 'codex'
                       : engine === 'gemini' ? 'gemini'
                       : engine === 'pkood' ? 'pkood'
                       : 'interactive';
      insertPendingSpawnCard(tempPid, subject, cardSource);
      $kptRunBtn.disabled = true;
      $kptRunBtn.textContent = 'Spawning...';
      try {
        const endpoint = engine === 'pkood' ? '/api/pkood/spawn'
                       : engine === 'codex' ? '/api/sessions/spawn-codex'
                       : engine === 'gemini' ? '/api/sessions/spawn-gemini'
                       : '/api/sessions/spawn';
        // pkood and codex don't support the worktree flag — drop it for those.
        const body = (engine === 'claude' || engine === 'gemini')
          ? { prompt, repo_path: repoPath, worktree: useWorktree }
          : { prompt, repo_path: repoPath };
        const res = await fetch(endpoint, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body),
        });
        const data = await res.json();
        if (data.ok) {
          $kptNewSession.value = '';
          $kptRunBtn.textContent = 'Spawned!';
          // Swap the temp-pid key for the real pid so the next /api/sessions
          // poll can match by spawn_pid and replace the placeholder with the
          // real card. Without this the placeholder sits on the board until
          // the 30s auto-cleanup fires.
          if (data.pid) {
            const placeholder = conversationsData.find(x => x.id === 'spawning-' + tempPid);
            if (placeholder) {
              placeholder.spawn_pid = data.pid;
              // Fire-and-watch engines stash the log path so the
              // right-pane renderer can fetch /api/sessions/spawned/<pid>/log.
              if ((engine === 'codex' || engine === 'gemini') && data.log) placeholder.agent_log_path = data.log;
              pendingSpawns.delete(tempPid);
              pendingSpawns.set(data.pid, placeholder);
            }
          }
          // Tight poll schedule so the real card replaces the placeholder fast.
          // (No-op for codex — no real card materializes — but harmless.)
          setTimeout(refreshConversationList, 600);
          setTimeout(refreshConversationList, 1500);
          setTimeout(refreshConversationList, 3000);
          // If the user picked a fire-and-watch engine, refocus the right pane on the
          // placeholder so log rendering kicks in for the new pid
          // (the auto-select fired when tempPid was still in play).
          if ((engine === 'codex' || engine === 'gemini') && typeof selectConversation === 'function') {
            selectConversation('spawning-' + tempPid);
          }
        } else {
          $kptRunBtn.textContent = data.error ? 'Failed' : 'Failed';
          // Spawn failed — drop the optimistic placeholder so the board doesn't
          // lie about an in-flight session.
          pendingSpawns.delete(tempPid);
          delete columnOverrides['spawning-' + tempPid];
          conversationsData = conversationsData.filter(x => x.id !== 'spawning-' + tempPid);
          renderSidebar(filterConversations($convSearch.value));
        }
      } catch (err) {
        $kptRunBtn.textContent = 'Error';
        pendingSpawns.delete(tempPid);
        delete columnOverrides['spawning-' + tempPid];
        conversationsData = conversationsData.filter(x => x.id !== 'spawning-' + tempPid);
        renderSidebar(filterConversations($convSearch.value));
      }
      setTimeout(() => { $kptRunBtn.disabled = false; $kptRunBtn.textContent = 'Run'; }, 2000);
    });
  }
  // ── New-session modal ──
  // Single-field: just the prompt body. Card title is derived from the first
  // sentence at submit time. Subject was redundant — users almost never typed
  // something different from the body's lead, and the ✨ Titles button + the
  // post-spawn rename flow cover the "I want a cleaner title" case.
  const $nsm = document.getElementById('newSessionModal');
  const $nsmBody = document.getElementById('nsmBody');
  const $nsmSubmit = document.getElementById('nsmSubmit');
  const $nsmCancel = document.getElementById('nsmCancel');
  const $nsmBackdrop = document.getElementById('nsmBackdrop');
  const $nsmEngineSelect = document.getElementById('nsmEngineSelect');
  // Modal selector participates in the same shared-state sync as the
  // inline ones — change it here and the inline selectors update too.
  if ($nsmEngineSelect) {
    $nsmEngineSelect.addEventListener('change', () => setSpawnEngine($nsmEngineSelect.value));
  }
  function openNewSessionModal(body = '', repoPath = '') {
    if (!$nsm) return;
    const targetRepoPath = repoPath || selectedRepoPath() || requireSelectedRepo('New session');
    if (!targetRepoPath) return;
    _clearNsmError();
    $nsmBody.value = body || '';
    $nsm.dataset.repoPath = targetRepoPath;
    // Sync from shared state so the modal opens on the same engine the
    // user just picked from any other selector.
    if ($nsmEngineSelect) $nsmEngineSelect.value = getSpawnEngine();
    const $nsmWorktree = document.getElementById('nsmWorktree');
    const $kptWorktreeToggle = document.getElementById('kptWorktreeToggle');
    if ($nsmWorktree && $kptWorktreeToggle) $nsmWorktree.checked = $kptWorktreeToggle.checked;
    $nsm.style.display = 'flex';
    setTimeout(() => { $nsmBody.focus(); }, 30);
  }
  function closeNewSessionModal() {
    if ($nsm) $nsm.style.display = 'none';
    if ($kptNewSession) $kptNewSession.value = '';
  }
  async function submitNewSessionModal() {
    const body = ($nsmBody.value || '').trim();
    if (!body) return;
    // Card title = first sentence (or line) of the prompt, capped at 120 chars.
    function firstSentence(text) {
      const chunks = text.split(/(?<=[.!?])\s+|\n+/).map(s => s.trim()).filter(Boolean);
      const first = chunks[0] || text.trim();
      return first.length > 120 ? first.slice(0, 120).trim() + '...' : first;
    }
    const effectiveSubject = firstSentence(body);
    const prompt = body + SESSION_STATE_INSTRUCTION;
    const engine = ($nsmEngineSelect && $nsmEngineSelect.value) || 'claude';
    const repoPath = ($nsm && $nsm.dataset.repoPath) || requireSelectedRepo('New session');
    if (!repoPath) return;
    const $nsmWorktree = document.getElementById('nsmWorktree');
    const useWorktree = !!($nsmWorktree && $nsmWorktree.checked);
    $nsmSubmit.disabled = true;
    $nsmSubmit.textContent = 'Launching...';
    try {
      const endpoint = engine === 'codex' ? '/api/sessions/spawn-codex'
                     : engine === 'gemini' ? '/api/sessions/spawn-gemini'
                     : '/api/sessions/spawn';
      const body = engine === 'codex'
        ? { prompt, name: effectiveSubject, repo_path: repoPath }
        : { prompt, name: effectiveSubject, repo_path: repoPath, worktree: useWorktree };
      const res = await fetch(endpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
      if (data.ok) {
        closeNewSessionModal();
        const cardSource = engine === 'codex' ? 'codex'
                         : engine === 'gemini' ? 'gemini'
                         : 'interactive';
        insertPendingSpawnCard(data.pid, effectiveSubject, cardSource);
        // Tight poll schedule so the real card replaces the placeholder fast.
        setTimeout(refreshConversationList, 600);
        setTimeout(refreshConversationList, 1500);
        setTimeout(refreshConversationList, 3000);
      } else {
        _showNsmError('Spawn failed — status ' + res.status + '\n' + JSON.stringify(data, null, 2));
        console.error('[New session] spawn failed', data);
      }
    } catch (err) {
      _showNsmError('Request error: ' + (err && err.message || 'network') + '\n' + (err && err.stack || ''));
      console.error('[New session] submit error', err);
    }
    // Reset button label but keep any visible error until the user dismisses it.
    setTimeout(() => { $nsmSubmit.disabled = false; $nsmSubmit.textContent = 'Launch'; }, 1500);
  }
  // Show/hide a persistent copyable error box inside the new-session modal.
  function _showNsmError(text) {
    const el = document.getElementById('nsmError');
    if (!el) return;
    el.textContent = text;
    el.style.display = '';
  }
  function _clearNsmError() {
    const el = document.getElementById('nsmError');
    if (el) { el.textContent = ''; el.style.display = 'none'; }
  }
  // ── Paste-image support ──
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
  function insertAtCursor(el, text) {
    if (el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && el.type === 'text')) {
      const start = el.selectionStart || 0;
      const end = el.selectionEnd || 0;
      el.value = el.value.slice(0, start) + text + el.value.slice(end);
      el.selectionStart = el.selectionEnd = start + text.length;
      el.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
  function attachImagePaste(el) {
    if (!el || el._imgPasteBound) return;
    el._imgPasteBound = true;
    el.addEventListener('paste', async (ev) => {
      const items = ev.clipboardData && ev.clipboardData.items;
      if (!items) return;
      for (const it of items) {
        if (it.kind === 'file' && it.type.startsWith('image/')) {
          ev.preventDefault();
          const blob = it.getAsFile();
          const placeholder = ' [uploading image...] ';
          insertAtCursor(el, placeholder);
          try {
            const p = await uploadPastedImage(blob);
            el.value = el.value.replace(placeholder, ' ' + p + ' ');
            el.dispatchEvent(new Event('input', { bubbles: true }));
          } catch (e) {
            el.value = el.value.replace(placeholder, ' [upload failed: ' + e.message + '] ');
          }
          return;
        }
      }
    });
  }
  [document.getElementById('nsmBody'),
   document.getElementById('kptNewSession'), document.getElementById('cpInput'),
   document.getElementById('convInput')].forEach(attachImagePaste);

  if ($kptNewSession) {
    // Open modal the moment the user starts typing (or focuses)
    $kptNewSession.addEventListener('focus', () => openNewSessionModal($kptNewSession.value));
    $kptNewSession.addEventListener('input', () => {
      if (!$nsm || $nsm.style.display === 'none') openNewSessionModal($kptNewSession.value);
    });
    $kptNewSession.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); openNewSessionModal($kptNewSession.value); }
    });
  }
  if ($nsmCancel) $nsmCancel.addEventListener('click', closeNewSessionModal);
  if ($nsmBackdrop) $nsmBackdrop.addEventListener('click', closeNewSessionModal);
  if ($nsmSubmit) $nsmSubmit.addEventListener('click', submitNewSessionModal);
  document.addEventListener('keydown', (e) => {
    if (!$nsm || $nsm.style.display === 'none') return;
    if (e.key === 'Escape') { e.preventDefault(); closeNewSessionModal(); }
    else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submitNewSessionModal(); }
  });

  // ── Split panel input bar send handler ──
  if ($cpSendBtn && $cpInput) {
    async function sendToSplitTerminal() {
      const text = ($cpInput.value || '').trim();
      if (!text || !currentSession.id) return;
      $cpSendBtn.disabled = true;
      const flashRed = () => {
        $cpInput.style.borderColor = 'var(--red)';
        setTimeout(() => { $cpInput.style.borderColor = ''; }, 1500);
      };
      try {
        let res;
        if (currentSession.source === 'pkood') {
          const agentId = currentConversation.replace(/^pkood-/, '');
          res = await fetch('/api/pkood/inject', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ agent_id: agentId, message: text }),
          });
        } else if (currentSession.spawnPid && currentSession.source !== 'codex' && currentSession.source !== 'gemini') {
          // Headless session we spawned — push via stdin pipe
          res = await fetch('/api/sessions/spawned/' + currentSession.spawnPid + '/inject', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ text }),
          });
        } else {
          res = await fetch('/api/inject-input', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ session_id: currentSession.id, text }),
          });
        }
        let data = {};
        try { data = await res.json(); } catch (_) {}
        if (res.ok && data.ok) {
          // Optimistic echo into the conv view (replaced by the real user_text
          // event when the jsonl tail catches up).
          const $view = getConvView();
          if ($view) {
            const pendingDiv = document.createElement('div');
            pendingDiv.className = 'event user_text pending';
            pendingDiv.innerHTML = '<span class="label">User</span>'
              + '<div class="user-msg">' + escapeHtml(text) + '</div>';
            $view.appendChild(pendingDiv);
            showOptimisticAgentIndicator($view);
            if (currentSession.id) markSessionSending(currentSession.id);
            $view.scrollTop = $view.scrollHeight;
            _pendingSends.push({ text, element: pendingDiv });
          }
          $cpInput.value = '';
          // Collapse the auto-grown textarea back to one row and refresh the
          // send-button disabled state.
          $cpInput.style.height = '';
          if ($cpInput.__cpRefresh) $cpInput.__cpRefresh();
        } else {
          flashRed();
          const reason = formatInjectFailure(data, res.status);
          showOpToast('Send failed: ' + reason, 'error');
        }
      } catch (err) {
        flashRed();
        showOpToast('Send failed: ' + (err.message || 'network error'), 'error');
      }
      $cpSendBtn.disabled = false;
    }
    // Grow the textarea to fit content (capped by CSS max-height, which kicks
    // in scrolling). Runs on every input; cheap enough.
    function cpInputAutoResize() {
      $cpInput.style.height = '0px';
      $cpInput.style.height = Math.min($cpInput.scrollHeight, 160) + 'px';
    }
    // Enable the send button only when there's text AND we have an active session.
    // Exposed on the element so sendToSplitTerminal can re-run it after clearing value.
    $cpInput.__cpRefresh = function () {
      const hasText = ($cpInput.value || '').trim().length > 0;
      $cpSendBtn.disabled = !hasText || !currentSession.id;
    };
    $cpSendBtn.addEventListener('click', sendToSplitTerminal);
    $cpInput.addEventListener('input', () => { cpInputAutoResize(); $cpInput.__cpRefresh(); });
    $cpInput.addEventListener('keydown', (e) => {
      // Enter submits, Shift+Enter inserts a newline. Guard against IME
      // composition (Chinese/Japanese input methods dispatch Enter to commit
      // candidate text — we'd otherwise send the prompt mid-composition).
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        sendToSplitTerminal();
      }
    });
    // Disabled until the user types.
    $cpInput.__cpRefresh();
  }

  // ── Split panel font controls ──
  const $cpFontMinus = document.getElementById('cpFontMinus');
  const $cpFontPlus = document.getElementById('cpFontPlus');
  if ($cpFontMinus) $cpFontMinus.addEventListener('click', () => {
    convFontScale = Math.max(0.7, +(convFontScale - 0.1).toFixed(1));
    localStorage.setItem('ccc-conv-font-scale', String(convFontScale));
    if ($convPanelView) $convPanelView.style.zoom = convFontScale;
    applyConvFontScale();
  });
  if ($cpFontPlus) $cpFontPlus.addEventListener('click', () => {
    convFontScale = Math.min(1.5, +(convFontScale + 0.1).toFixed(1));
    localStorage.setItem('ccc-conv-font-scale', String(convFontScale));
    if ($convPanelView) $convPanelView.style.zoom = convFontScale;
    applyConvFontScale();
  });
  // Apply initial font scale to split view
  if ($convPanelView) $convPanelView.style.zoom = convFontScale;

  // Auto-refresh session data every 10s — keeps kanban columns, signals,
  // and relative times up to date without manual refresh. Routes through
  // loadConversationList() so the merge logic (placeholder preservation,
  // display-name override carry-over, sticky columns) lives in one place —
  // divergence here was what caused new-session rows to flicker.
	  setInterval(async () => {
	    if (activeTab !== 'sessions') return;
	    if (isInlineRenameInProgress()) return;
	    if (conversationPaneLoading) return;
    // Refresh /api/conversations/all and re-render — cheap because
    // _extract_tail_meta is mtime-cached server-side; only changed
    // JSONLs get re-scanned.
    try {
      await refreshArchiveData();
      const $search = document.getElementById('convSearch');
      renderArchiveList($search ? $search.value : '');
    } catch (_) { /* best-effort */ }
  }, 10000);

  // The convToolbar new-session input + Run/pkood toggle were removed —
  // spawning now flows through the sidebar's "+ New session" button (which
  // opens the new-session modal) or the kanban panel's #kptNewSession input.

  // ── In-app update check ─────────────────────────────────────────
  // Hit /api/version/check on page load. If we're behind the latest
  // GitHub release, show the topbar pill. Clicking → modal → POST
  // /api/self-update → show loading overlay → poll /api/version until
  // the new process is up → location.reload().
  const $updPill = document.getElementById('updPill');
  const $updPillText = document.getElementById('updPillText');
  const $updModal = document.getElementById('updModal');
  const $updBackdrop = document.getElementById('updBackdrop');
  const $updVersions = document.getElementById('updVersions');
  const $updCompareLink = document.getElementById('updCompareLink');
  const $updError = document.getElementById('updError');
  const $updLaterBtn = document.getElementById('updLaterBtn');
  const $updNowBtn = document.getElementById('updNowBtn');
  let updCheckData = null;

  function updOpenModal() {
    if (!updCheckData || !updCheckData.behind) return;
    if ($updVersions) {
      $updVersions.innerHTML =
        'Current: <strong>v' + String(updCheckData.current).replace(/[<>&]/g, '') + '</strong> &middot; ' +
        'Latest: <strong>v' + String(updCheckData.latest).replace(/[<>&]/g, '') + '</strong>';
    }
    if ($updCompareLink && updCheckData.changelog_url) {
      $updCompareLink.href = updCheckData.changelog_url;
    }
    if ($updError) {
      $updError.textContent = '';
      $updError.classList.remove('visible');
    }
    if ($updNowBtn) $updNowBtn.disabled = false;
    if ($updModal) $updModal.classList.add('open');
  }
  function updCloseModal() {
    if ($updModal) $updModal.classList.remove('open');
  }
  if ($updPill) $updPill.addEventListener('click', updOpenModal);
  if ($updLaterBtn) $updLaterBtn.addEventListener('click', updCloseModal);
  if ($updBackdrop) $updBackdrop.addEventListener('click', updCloseModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $updModal && $updModal.classList.contains('open')) {
      updCloseModal();
    }
  });

  async function updWaitForServer(maxSeconds) {
    // Poll /api/version until the restarted server answers. Returns true on
    // success, false if it never came back within the budget.
    const deadline = Date.now() + maxSeconds * 1000;
    while (Date.now() < deadline) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const r = await fetch('/api/version', { cache: 'no-store' });
        if (r.ok) return true;
      } catch (_) { /* keep waiting */ }
    }
    return false;
  }

  async function updRunSelfUpdate() {
    if (!$updNowBtn) return;
    $updNowBtn.disabled = true;
    if ($updLaterBtn) $updLaterBtn.disabled = true;
    if ($updError) { $updError.textContent = ''; $updError.classList.remove('visible'); }

    const $overlay = document.getElementById('cccLoadingOverlay');
    const $label = document.getElementById('cccLoadingLabel');
    if ($overlay) {
      $overlay.classList.remove('fade-out', 'gone');
      if ($label) {
        $label.innerHTML =
          '<strong>Updating Claude Command Center&hellip;</strong>' +
          '<div class="ccc-loading-detail">Pulling latest from GitHub and restarting the server.</div>';
      }
    }
    try { sessionStorage.setItem('ccc-updating', '1'); } catch (_) {}

    let data;
    try {
      const r = await fetch('/api/self-update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      data = await r.json();
    } catch (e) {
      // Socket may drop at the moment execvp fires — treat that as success
      // and start polling for the new process.
      data = { ok: true, _restartRace: true };
    }

    if (!data.ok) {
      // Pre-flight failed (dirty tree, wrong branch, etc). Hide the overlay
      // and surface the reason in the modal so the user can act on it.
      try { sessionStorage.removeItem('ccc-updating'); } catch (_) {}
      if ($overlay) $overlay.classList.add('fade-out', 'gone');
      if ($updError) {
        let msg = data.error || 'Update failed';
        if (data.paths && data.paths.length) {
          msg += '\n\nUncommitted paths:\n  ' + data.paths.slice(0, 8).join('\n  ');
        }
        if ((data.error || '').indexOf('local changes') !== -1) {
          msg += '\n\nCommit or stash the changes in the install directory and try again.';
        } else if ((data.error || '').indexOf('not main') !== -1) {
          msg += '\n\nSwitch the install clone to the main branch and try again.';
        } else if ((data.error || '').indexOf('not a git clone') !== -1) {
          msg += '\n\nSelf-update only works for installs created with `git clone`.';
        }
        $updError.textContent = msg;
        $updError.classList.add('visible');
      }
      showOpToast('Update failed: ' + (data.error || 'unknown'), 'error');
      $updNowBtn.disabled = false;
      if ($updLaterBtn) $updLaterBtn.disabled = false;
      return;
    }

    // Server is restarting. Poll /api/version until it answers, then reload.
    if ($label) {
      $label.innerHTML =
        '<strong>Restarting&hellip;</strong>' +
        '<div class="ccc-loading-detail">Waiting for the new process to bind the port.</div>';
    }
    const ok = await updWaitForServer(30);
    if (ok) {
      // Keep the 'ccc-updating' flag set — the post-reload page reads it to
      // show a transient "Updated" toast, then clears it.
      location.reload();
    } else {
      try { sessionStorage.removeItem('ccc-updating'); } catch (_) {}
      if ($overlay) $overlay.classList.add('fade-out', 'gone');
      showOpToast("Server didn't come back within 30s — try reloading manually", 'error');
      $updNowBtn.disabled = false;
      if ($updLaterBtn) $updLaterBtn.disabled = false;
    }
  }
  if ($updNowBtn) $updNowBtn.addEventListener('click', updRunSelfUpdate);

  (async () => {
    try {
      const r = await fetch('/api/version/check', { cache: 'no-store' });
      const d = await r.json();
      if (d && d.ok && d.behind) {
        updCheckData = d;
        if ($updPillText) {
          $updPillText.textContent = 'Update → v' + String(d.latest).replace(/[<>&]/g, '');
        }
        if ($updPill) $updPill.classList.add('visible');
      }
    } catch (_) { /* silent — the pill just stays hidden */ }
  })();

  // ── In-app bug reporting ────────────────────────────────────────
  // Topbar link → modal → POST /api/bug-report → server shells out
  // to `gh issue create`. On gh failure the handler returns the
  // rendered markdown so we can offer a copy-to-clipboard fallback.
  const $bugLink = document.getElementById('bugReportLink');
  const $bugModal = document.getElementById('bugReportModal');
  const $bugBackdrop = document.getElementById('bugReportBackdrop');
  const $bugTitleInput = document.getElementById('bugReportTitleInput');
  const $bugDescInput = document.getElementById('bugReportDescInput');
  const $bugMeta = document.getElementById('bugReportMeta');
  const $bugError = document.getElementById('bugReportError');
  const $bugSuccess = document.getElementById('bugReportSuccess');
  const $bugFallback = document.getElementById('bugReportFallback');
  const $bugCancelBtn = document.getElementById('bugReportCancelBtn');
  const $bugSubmitBtn = document.getElementById('bugReportSubmitBtn');
  const $bugCopyBtn = document.getElementById('bugReportCopyBtn');
  const $bugShotBtn = document.getElementById('bugReportShotBtn');
  const $bugShotBtnLabel = document.getElementById('bugReportShotBtnLabel');
  const $bugShotPreview = document.getElementById('bugReportShotPreview');
  const $bugShotImg = document.getElementById('bugReportShotImg');
  const $bugShotRetakeBtn = document.getElementById('bugReportShotRetakeBtn');
  const $bugShotRemoveBtn = document.getElementById('bugReportShotRemoveBtn');
  let bugCachedVersion = null;
  let bugFallbackMarkdown = '';
  let bugShotB64 = '';  // raw base64 PNG (no data: prefix), '' when no screenshot

  function bugEscape(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }

  async function bugFetchVersion() {
    if (bugCachedVersion) return bugCachedVersion;
    try {
      const r = await fetch('/api/version', { cache: 'no-store' });
      const d = await r.json();
      bugCachedVersion = d && d.version ? String(d.version) : '';
    } catch (_) {
      bugCachedVersion = '';
    }
    return bugCachedVersion;
  }

  async function bugRenderMeta() {
    const version = await bugFetchVersion();
    const ua = (typeof navigator !== 'undefined' && navigator.userAgent) || '';
    const sid = (typeof currentSession !== 'undefined' && currentSession && currentSession.id) || '';
    if (!$bugMeta) return;
    $bugMeta.innerHTML =
      '<div><strong>CCC version:</strong> <code>' + bugEscape(version || '—') + '</code></div>' +
      '<div><strong>Session:</strong> <code>' + bugEscape(sid || '—') + '</code></div>' +
      '<div><strong>User agent:</strong> <code>' + bugEscape(ua || '—') + '</code></div>';
  }

  function bugClearShot() {
    bugShotB64 = '';
    if ($bugShotImg) $bugShotImg.src = '';
    if ($bugShotPreview) $bugShotPreview.classList.remove('visible');
    if ($bugShotBtnLabel) $bugShotBtnLabel.textContent = 'Add screenshot';
    if ($bugShotBtn) $bugShotBtn.disabled = false;
  }

  function bugResetState() {
    if ($bugError) { $bugError.textContent = ''; $bugError.classList.remove('visible'); }
    if ($bugSuccess) { $bugSuccess.innerHTML = ''; $bugSuccess.classList.remove('visible'); }
    if ($bugFallback) { $bugFallback.textContent = ''; $bugFallback.classList.remove('visible'); }
    if ($bugCopyBtn) $bugCopyBtn.style.display = 'none';
    bugFallbackMarkdown = '';
    bugClearShot();
    if ($bugSubmitBtn) {
      $bugSubmitBtn.disabled = false;
      $bugSubmitBtn.textContent = 'Send report';
    }
    if ($bugCancelBtn) { $bugCancelBtn.disabled = false; $bugCancelBtn.textContent = 'Cancel'; }
  }

  async function bugCaptureScreenshot() {
    // Hands off to the server, which shells out to `screencapture -i` and
    // blocks until the user finishes drawing or hits Esc. The button stays
    // in a "spinner" state for the entire window so it's clear something
    // is waiting on the user.
    if (!$bugShotBtn) return;
    if ($bugError) { $bugError.textContent = ''; $bugError.classList.remove('visible'); }
    $bugShotBtn.disabled = true;
    if ($bugShotBtnLabel) $bugShotBtnLabel.textContent = 'Drawing…';
    let data;
    try {
      const r = await fetch('/api/bug-report/capture', { method: 'POST' });
      data = await r.json().catch(() => ({}));
    } catch (e) {
      if ($bugError) {
        $bugError.textContent = 'Capture failed: ' + (e && e.message ? e.message : 'unknown');
        $bugError.classList.add('visible');
      }
      bugClearShot();
      return;
    }
    if (data && data.cancelled) {
      // User pressed Esc — they bailed on purpose, no error noise.
      bugClearShot();
      return;
    }
    if (!data || !data.ok || !data.image_b64) {
      if ($bugError) {
        $bugError.textContent = (data && data.error) || 'Could not capture screenshot.';
        $bugError.classList.add('visible');
      }
      bugClearShot();
      return;
    }
    bugShotB64 = data.image_b64;
    if ($bugShotImg) $bugShotImg.src = 'data:' + (data.mime || 'image/png') + ';base64,' + data.image_b64;
    if ($bugShotPreview) $bugShotPreview.classList.add('visible');
    if ($bugShotBtnLabel) $bugShotBtnLabel.textContent = 'Replace screenshot';
    $bugShotBtn.disabled = false;
  }

  if ($bugShotBtn) $bugShotBtn.addEventListener('click', bugCaptureScreenshot);
  if ($bugShotRetakeBtn) $bugShotRetakeBtn.addEventListener('click', bugCaptureScreenshot);
  if ($bugShotRemoveBtn) $bugShotRemoveBtn.addEventListener('click', bugClearShot);

  function bugOpenModal() {
    if (!$bugModal) return;
    bugResetState();
    if ($bugTitleInput) $bugTitleInput.value = '';
    if ($bugDescInput) $bugDescInput.value = '';
    bugRenderMeta();
    $bugModal.classList.add('open');
    setTimeout(() => { if ($bugTitleInput) $bugTitleInput.focus(); }, 0);
  }
  function bugCloseModal() {
    if ($bugModal) $bugModal.classList.remove('open');
  }

  if ($bugLink) $bugLink.addEventListener('click', bugOpenModal);
  if ($bugBackdrop) $bugBackdrop.addEventListener('click', bugCloseModal);
  if ($bugCancelBtn) $bugCancelBtn.addEventListener('click', bugCloseModal);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $bugModal && $bugModal.classList.contains('open')) {
      bugCloseModal();
    }
  });

  async function bugSubmit() {
    if (!$bugTitleInput || !$bugDescInput) return;
    const title = $bugTitleInput.value.trim();
    const desc = $bugDescInput.value.trim();
    bugResetState();
    if (!title) {
      if ($bugError) { $bugError.textContent = 'Please add a title.'; $bugError.classList.add('visible'); }
      $bugTitleInput.focus();
      return;
    }
    if (!desc) {
      if ($bugError) { $bugError.textContent = 'Please describe the bug.'; $bugError.classList.add('visible'); }
      $bugDescInput.focus();
      return;
    }
    if ($bugSubmitBtn) {
      $bugSubmitBtn.disabled = true;
      // Sending an image takes a couple of seconds (push to a public branch
      // before gh issue create) — surface that explicitly so the user
      // doesn't think the submit hung.
      $bugSubmitBtn.textContent = bugShotB64 ? 'Sending (uploading screenshot)…' : 'Sending…';
    }
    if ($bugCancelBtn) $bugCancelBtn.disabled = true;
    const version = await bugFetchVersion();
    const sid = (typeof currentSession !== 'undefined' && currentSession && currentSession.id) || '';
    const payload = {
      title,
      description: desc,
      ccc_version: version || '',
      user_agent: (navigator && navigator.userAgent) || '',
      session_id: sid,
    };
    if (bugShotB64) payload.screenshot_b64 = bugShotB64;
    let data;
    try {
      const r = await fetch('/api/bug-report', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      data = await r.json().catch(() => ({}));
    } catch (e) {
      if ($bugError) {
        $bugError.textContent = 'Network error: ' + (e && e.message ? e.message : 'unknown');
        $bugError.classList.add('visible');
      }
      if ($bugSubmitBtn) { $bugSubmitBtn.disabled = false; $bugSubmitBtn.textContent = 'Send report'; }
      if ($bugCancelBtn) $bugCancelBtn.disabled = false;
      return;
    }
    if (data && data.ok && data.url) {
      const safeUrl = bugEscape(data.url);
      let html = 'Thanks — issue filed: <a href="' + safeUrl + '" target="_blank" rel="noopener">' + safeUrl + '</a>';
      if (data.screenshot_needs_manual && data.screenshot_path) {
        // Push to bug-screenshots branch failed (typical for OSS users
        // without write access). Show the local path with a clear
        // drag-drop instruction, AND fire-and-forget a /reveal so Finder
        // pops to the file. The user finishes the attachment manually.
        const safePath = bugEscape(data.screenshot_path);
        html += '<div style="margin-top:8px;line-height:1.5;">'
              + 'Screenshot upload failed — saved locally at '
              + '<code style="font-family:\'SF Mono\',monospace;font-size:11px;">' + safePath + '</code>. '
              + 'Drag this file into a comment on the issue to attach it. '
              + 'Finder should be opening to it now.'
              + '</div>';
        try {
          fetch('/api/bug-report/reveal', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: data.screenshot_path }),
          });
        } catch (_) { /* best effort */ }
        try { window.open(data.url, '_blank', 'noopener'); } catch (_) {}
      }
      if ($bugSuccess) {
        $bugSuccess.innerHTML = html;
        $bugSuccess.classList.add('visible');
      }
      if ($bugSubmitBtn) { $bugSubmitBtn.disabled = true; $bugSubmitBtn.textContent = 'Sent'; }
      if ($bugCancelBtn) { $bugCancelBtn.disabled = false; $bugCancelBtn.textContent = 'Close'; }
      try { showOpToast('Bug report filed', 'success'); } catch (_) {}
      return;
    }
    // Failure path. If the server returned `markdown` we offer a copy-
    // to-clipboard fallback so the user can still file the issue manually.
    const errMsg = (data && data.error) || 'Failed to submit bug report.';
    if ($bugError) {
      let msg = errMsg;
      if (data && data.repo_url) {
        msg += '\n\nFile manually at: ' + data.repo_url;
      }
      $bugError.textContent = msg;
      $bugError.classList.add('visible');
    }
    if (data && data.markdown) {
      bugFallbackMarkdown = String(data.markdown);
      if ($bugFallback) {
        $bugFallback.textContent = bugFallbackMarkdown;
        $bugFallback.classList.add('visible');
      }
      if ($bugCopyBtn) $bugCopyBtn.style.display = 'inline-block';
    }
    if ($bugSubmitBtn) { $bugSubmitBtn.disabled = false; $bugSubmitBtn.textContent = 'Try again'; }
    if ($bugCancelBtn) $bugCancelBtn.disabled = false;
  }

  if ($bugSubmitBtn) $bugSubmitBtn.addEventListener('click', bugSubmit);
  if ($bugCopyBtn) $bugCopyBtn.addEventListener('click', async () => {
    const md = bugFallbackMarkdown;
    if (!md) return;
    try {
      await navigator.clipboard.writeText(md);
      if ($bugCopyBtn) { $bugCopyBtn.textContent = 'Copied'; setTimeout(() => { if ($bugCopyBtn) $bugCopyBtn.textContent = 'Copy markdown'; }, 1500); }
    } catch (_) {
      try { showOpToast('Copy failed — select the text and copy manually', 'error'); } catch (__) {}
    }
  });

  // Network access modal — opt in to non-loopback access via Tailscale or
  // arbitrary trusted origins. POSTs to /api/network-config which writes
  // the JSON config and triggers an in-place restart.
  const $networkBtn = document.getElementById('networkAccessBtn');
  const $networkModal = document.getElementById('networkModal');
  const $networkBackdrop = document.getElementById('networkBackdrop');
  const $networkBindAll = document.getElementById('networkBindAll');
  const $networkTrustTailnet = document.getElementById('networkTrustTailnet');
  const $networkTailnetSummary = document.getElementById('networkTailnetSummary');
  const $networkExtraOrigins = document.getElementById('networkExtraOrigins');
  const $networkEnvNotice = document.getElementById('networkEnvNotice');
  const $networkError = document.getElementById('networkError');
  const $networkCancelBtn = document.getElementById('networkCancelBtn');
  const $networkSaveBtn = document.getElementById('networkSaveBtn');

  function networkEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[c]);
  }
  function networkResetState() {
    if ($networkError) { $networkError.textContent = ''; $networkError.classList.remove('visible'); }
    if ($networkSaveBtn) { $networkSaveBtn.disabled = false; $networkSaveBtn.textContent = 'Save & restart'; }
    if ($networkCancelBtn) { $networkCancelBtn.disabled = false; }
  }
  function networkRenderTailnet(tn) {
    if (!$networkTailnetSummary) return;
    if (!tn || !tn.available) {
      $networkTailnetSummary.innerHTML = 'Tailscale CLI not found on PATH — install <a href="https://tailscale.com/download" target="_blank" rel="noopener">tailscale</a> to enable.';
      if ($networkTrustTailnet) $networkTrustTailnet.disabled = true;
      return;
    }
    if (!tn.running) {
      $networkTailnetSummary.textContent = 'Tailscale is installed but not running. Start it and reopen this dialog.';
      if ($networkTrustTailnet) $networkTrustTailnet.disabled = true;
      return;
    }
    if ($networkTrustTailnet) $networkTrustTailnet.disabled = false;
    const host = tn.hostname ? '<code>' + networkEsc(tn.hostname) + '</code>' : '(no MagicDNS hostname)';
    const originsList = (tn.origins || []).map(o => '<code>' + networkEsc(o) + '</code>').join(', ');
    $networkTailnetSummary.innerHTML =
      'Detected: ' + host + '. When trusted, these origins are allowed: ' + (originsList || '(none)');
  }
  function networkRenderEnvNotice(env) {
    if (!$networkEnvNotice) return;
    const pinned = [];
    if (env && env.bind_host) pinned.push('CCC_BIND_HOST');
    if (env && env.allowed_origins) pinned.push('CCC_ALLOWED_ORIGIN');
    if (env && env.trust_tailnet) pinned.push('CCC_TRUST_TAILNET');
    if (pinned.length === 0) {
      $networkEnvNotice.style.display = 'none';
      return;
    }
    $networkEnvNotice.style.display = '';
    $networkEnvNotice.innerHTML =
      'Some values are pinned by environment variables for this run: <code>' +
      pinned.map(networkEsc).join('</code>, <code>') +
      '</code>. Saving here will not change them — clear the env to take control from the UI.';
  }

  async function networkOpen() {
    if (!$networkModal) return;
    networkResetState();
    if ($networkExtraOrigins) $networkExtraOrigins.value = '';
    if ($networkBindAll) $networkBindAll.checked = false;
    if ($networkTrustTailnet) $networkTrustTailnet.checked = false;
    if ($networkTailnetSummary) $networkTailnetSummary.textContent = 'Detecting Tailscale…';
    $networkModal.classList.add('open');
    let data;
    try {
      const r = await fetch('/api/network-config', { cache: 'no-store' });
      data = await r.json();
    } catch (e) {
      if ($networkError) {
        $networkError.textContent = 'Could not load current settings: ' + (e && e.message || 'unknown');
        $networkError.classList.add('visible');
      }
      return;
    }
    const stored = data.stored || {};
    const runtime = data.runtime || {};
    if ($networkBindAll) $networkBindAll.checked = (stored.bind_host === '0.0.0.0') || (runtime.bind_host && runtime.bind_host !== '127.0.0.1' && runtime.bind_host !== 'localhost');
    if ($networkTrustTailnet) $networkTrustTailnet.checked = !!stored.trust_tailnet;
    // Show only the user-managed origins (those in the stored config).
    // Auto-detected tailnet origins and env-supplied ones are not shown
    // here — listing them would invite "delete = remove trust", which the
    // env / auto-detect layers don't honour. Tailnet origins are summarized
    // separately via the checkbox.
    if ($networkExtraOrigins) {
      $networkExtraOrigins.value = (stored.allowed_origins || []).join('\n');
    }
    networkRenderTailnet(data.tailnet);
    networkRenderEnvNotice(runtime.env_overrides);
  }
  function networkClose() {
    if ($networkModal) $networkModal.classList.remove('open');
  }

  async function networkSave() {
    if (!$networkSaveBtn) return;
    networkResetState();
    const bindAll = !!($networkBindAll && $networkBindAll.checked);
    const trust = !!($networkTrustTailnet && $networkTrustTailnet.checked);
    const extra = ($networkExtraOrigins && $networkExtraOrigins.value || '')
      .split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    const bad = extra.find(o => !/^https?:\/\/[^\s]+$/.test(o));
    if (bad) {
      if ($networkError) {
        $networkError.textContent = 'Origin must look like http://host:port — got: ' + bad;
        $networkError.classList.add('visible');
      }
      return;
    }
    $networkSaveBtn.disabled = true;
    $networkSaveBtn.textContent = 'Saving…';
    if ($networkCancelBtn) $networkCancelBtn.disabled = true;
    try { sessionStorage.setItem('ccc-updating', '1'); } catch (_) {}
    let data;
    try {
      const r = await fetch('/api/network-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bind_host: bindAll ? '0.0.0.0' : '127.0.0.1',
          allowed_origins: extra,
          trust_tailnet: trust,
        }),
      });
      data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) {
        throw new Error(data && data.error ? data.error : 'save failed (' + r.status + ')');
      }
    } catch (e) {
      try { sessionStorage.removeItem('ccc-updating'); } catch (_) {}
      if ($networkError) {
        $networkError.textContent = (e && e.message) || 'Save failed.';
        $networkError.classList.add('visible');
      }
      $networkSaveBtn.disabled = false;
      $networkSaveBtn.textContent = 'Save & restart';
      if ($networkCancelBtn) $networkCancelBtn.disabled = false;
      return;
    }
    $networkSaveBtn.textContent = 'Restarting…';
    setTimeout(() => { try { location.reload(); } catch (_) {} }, 1200);
  }

  if ($networkBtn) $networkBtn.addEventListener('click', () => {
    if ($settingsPopover) $settingsPopover.classList.remove('open');
    networkOpen();
  });
  if ($networkBackdrop) $networkBackdrop.addEventListener('click', networkClose);
  if ($networkCancelBtn) $networkCancelBtn.addEventListener('click', networkClose);
  if ($networkSaveBtn) $networkSaveBtn.addEventListener('click', networkSave);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && $networkModal && $networkModal.classList.contains('open')) {
      networkClose();
    }
  });

  // If we just finished restarting after a self-update, briefly acknowledge.
  try {
    if (sessionStorage.getItem('ccc-updating')) {
      sessionStorage.removeItem('ccc-updating');
      showOpToast('Updated to the latest version', 'ok');
    }
  } catch (_) {}

  // ── Sidebar primary "+ New session" button ─────────────────────
  // Opens an empty conversation pane on the right with the input bar
  // ready to receive a prompt — pressing Enter spawns a fresh agent.
  const $sidebarNewBtn = document.getElementById('sidebarNewBtn');
  if ($sidebarNewBtn) {
    $sidebarNewBtn.addEventListener('click', () => enterNewSessionMode());
  }

  // ── Spawn cwd picker (new-session mode) ──────────────────────────────
  // Populates a folder dropdown above the input box so the user picks
  // exactly where the new session will land. This is the explicit repo
  // context for All-repos mode.
  const SPAWN_CWD_KEY = 'ccc-spawn-cwd';

  function populateSpawnCwdPicker() {
    const sel = document.getElementById('spawnCwdPicker');
    if (!sel) return;

    // Source list: known repos (recent ∪ pinned). Reuse the dropdown's
    // own option-builder for label disambiguation but strip the "All"
    // sentinel — spawning needs an exact folder.
    let options = [];
    try {
      options = (typeof _archiveFolderOptions === 'function')
        ? _archiveFolderOptions()
        : [];
    } catch (_) {}
    if (!options.length && repoListState && Array.isArray(repoListState.repos)) {
      options = repoListState.repos.map(r => ({
        value: r.path, path: r.path, label: r.label || r.path,
      }));
    }
    // Deduplicate by path — folder filter and repoListState can overlap.
    const seen = new Set();
    options = options.filter(o => {
      const key = o.value || o.path;
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    // Default selection priority:
    //   1. User's last spawn cwd (localStorage)
    //   2. Active folder filter, if it's a specific folder (not "All")
    //   3. First known repo option
    let saved = '';
    try { saved = localStorage.getItem(SPAWN_CWD_KEY) || ''; } catch (_) {}
    let defaultPath = saved;
    if (!defaultPath || !options.some(o => (o.value || o.path) === defaultPath)) {
      const filterVal = (typeof archiveFolderFilter !== 'undefined' && typeof ARCHIVE_FOLDER_ALL !== 'undefined' && archiveFolderFilter !== ARCHIVE_FOLDER_ALL)
        ? archiveFolderFilter : '';
      defaultPath = filterVal
        || (options[0] && (options[0].value || options[0].path))
        || '';
    }

    const prevValue = sel.value;
    sel.innerHTML = '';
    for (const opt of options) {
      const o = document.createElement('option');
      o.value = opt.value || opt.path;
      o.textContent = opt.label || opt.value || opt.path;
      o.title = opt.path || o.value;
      sel.appendChild(o);
    }
    if (prevValue && options.some(o => (o.value || o.path) === prevValue)) {
      sel.value = prevValue;
    } else if (defaultPath && options.some(o => (o.value || o.path) === defaultPath)) {
      sel.value = defaultPath;
    }
  }

  // Persist the user's choice the moment they change it.
  document.addEventListener('change', (ev) => {
    if (ev.target && ev.target.id === 'spawnCwdPicker') {
      try { localStorage.setItem(SPAWN_CWD_KEY, ev.target.value || ''); } catch (_) {}
    }
  });

  function getSpawnCwd() {
    const sel = document.getElementById('spawnCwdPicker');
    return (sel && sel.value) || '';
  }

  function enterNewSessionMode() {
    if (typeof stopConvStream === 'function') stopConvStream();
    if (typeof stopSpawnStream === 'function') stopSpawnStream();
    if (typeof stopPkoodTailPoller === 'function') stopPkoodTailPoller();
    currentConversation = '__new__';
    setCurrentSession(null, null, null, false, null);
    // Drop the previous session's workspace/usage data so the input-context
    // strip (WORKTREE pill, ctx/peak token pill) doesn't linger above the
    // empty "Start a new session" prompt.
    if (typeof fetchSessionWorkspace === 'function') fetchSessionWorkspace(null);
    if (typeof fetchSessionUsage === 'function') fetchSessionUsage(null);
    if ($convList) $convList.querySelectorAll('.conv-item.active').forEach(el => el.classList.remove('active'));
    if ($kanbanBoard) $kanbanBoard.querySelectorAll('.kanban-card.active').forEach(el => el.classList.remove('active'));
    if ($kanbanBoardSplit) $kanbanBoardSplit.querySelectorAll('.kanban-card.active').forEach(el => el.classList.remove('active'));
    populateSpawnCwdPicker();
    const spawnCwd = getSpawnCwd();
    const $view = getConvView();
    if ($view) {
      const spawnEngine = getSpawnEngine();
      const engineLabel = spawnEngine === 'codex' ? 'Codex' : (spawnEngine === 'gemini' ? 'Gemini' : 'Claude');
      const repoLabel = selectedRepoLabel() || _pathLeaf(spawnCwd) || spawnCwd || 'pick a folder below';
      $view.innerHTML = '<div class="empty-state" style="height:auto;padding:48px 32px;flex-direction:column;gap:10px;text-align:center;">'
        + '<div style="font-size:16px;color:var(--text);">Start a new session</div>'
        + '<div style="font-size:12px;color:var(--text-muted);">Repo: <span style="color:var(--text);">' + escapeHtml(repoLabel) + '</span></div>'
        + '<div style="font-size:13px;color:var(--text-muted);max-width:480px;line-height:1.5;">Type a prompt below and press Enter to spawn a fresh ' + engineLabel + ' agent. The new session will appear in the sidebar.</div>'
        + '</div>';
    }
    updateInputBar();
    // Toggle the context strip into new-session mode so the picker is
    // visible and the workspace pill is hidden (the workspace pill
    // describes an existing session and has no meaning before spawn).
    const _cic = document.getElementById('convInputContext');
    if (_cic) {
      _cic.classList.add('is-new-session');
      _cic.classList.add('visible');
    }
    if (typeof mobileShowForCurrentMode === 'function') mobileShowForCurrentMode();
    setTimeout(() => { if ($convInput) { $convInput.value = ''; $convInput.focus(); } }, 30);
  }

  // Spawn a session from the bottom input bar while viewing a backlog GH
  // issue. Mirrors the kanban "Edit & start" flow: builds the same standard
  // preamble ("Fix issue #N — TITLE\n\nRun `gh issue view N` …") and
  // appends whatever the user typed as their edit instructions.
  async function spawnFromBacklogIssue(userText) {
    const row = conversationsData.find(x => x.id === currentConversation) || {};
    const issueNum = row.issue_number || (currentConversation || '').replace('backlog-issue-', '');
    if (!issueNum) return;
    const conv = row.issue_number ? row : (conversationsData.find(x => x.id === 'backlog-issue-' + issueNum) || {});
    const repoPath = rowRepoPath(conv) || repoPathForIssueNumber(issueNum);
    const title = conv.display_name || conv.first_message || '';
    const cleanTitle = (title || '').replace(/^#\d+:\s*/, '').replace(/\[[^\]]*\]\s*/g, '').trim();
    const preamble = 'Fix issue #' + issueNum + ' — ' + cleanTitle
      + '\n\nRun `gh issue view ' + issueNum + '` for the full body (title may be truncated).';
    const body = preamble + '\n\n' + userText;
    const subject = 'issue-' + issueNum;
    const prompt = body + SESSION_STATE_INSTRUCTION;
    if ($convSendBtn) $convSendBtn.disabled = true;
    const flashRed = () => {
      if (!$convInput) return;
      $convInput.style.borderColor = 'var(--red)';
      setTimeout(() => { $convInput.style.borderColor = ''; }, 1500);
    };
    try {
      const res = await fetch('/api/sessions/spawn', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(withRepoPath({ prompt, name: subject, cwd: repoPath || undefined }, repoPath)),
      });
      const data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
      if (data.ok) {
        insertPendingSpawnCard(data.pid, subject, false);
        if ($convInput) $convInput.value = '';
        // Mirror kanban-start-btn: tell GitHub the issue is being worked on.
        fetch('/api/issues/' + issueNum + '/mark-in-progress', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(withRepoPath({}, repoPath)),
        }).catch(() => {});
        const $view = getConvView();
        if ($view) {
          $view.innerHTML = '<div class="empty-state" style="height:auto;padding:48px 32px;flex-direction:column;gap:10px;text-align:center;">'
            + '<div style="font-size:15px;color:var(--text);">Spawning session for issue #' + escapeHtml(String(issueNum)) + '…</div>'
            + '<div style="font-size:13px;color:var(--text-muted);max-width:480px;line-height:1.5;">It will appear in the sidebar in a moment — click it to follow along.</div>'
            + '</div>';
        }
        setTimeout(refreshConversationList, 600);
        setTimeout(refreshConversationList, 1500);
        setTimeout(refreshConversationList, 3000);
      } else {
        flashRed();
        showOpToast('Spawn failed: ' + (data.error || 'HTTP ' + res.status), 'error');
      }
    } catch (err) {
      flashRed();
      showOpToast('Spawn failed: ' + (err && err.message || 'network'), 'error');
    }
    if ($convSendBtn) $convSendBtn.disabled = false;
    if ($convInput) $convInput.focus();
  }

  async function closeIssueFromInputBar(action, text) {
    const row = conversationsData.find(x => x.id === currentConversation) || {};
    const issueNum = row.issue_number || (currentConversation || '').replace('backlog-issue-', '');
    if (!issueNum) return;
    const conv = row.issue_number ? row : (conversationsData.find(x => x.id === 'backlog-issue-' + issueNum) || {});
    const repoPath = rowRepoPath(conv) || repoPathForIssueNumber(issueNum);
    const reason = action === 'not_planned' ? 'not planned' : action; // "not_planned" → "not planned"
    const body = { reason };
    if (action === 'duplicate') {
      const dupNum = text.replace(/[^0-9]/g, '');
      if (!dupNum) { showOpToast('Enter the duplicate issue number in the text field', 'error'); return; }
      body.duplicate_of = dupNum;
    }
    if ($convSendBtn) $convSendBtn.disabled = true;
    try {
      const res = await fetch('/api/issues/' + issueNum + '/close', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(withRepoPath(body, repoPath)),
      });
      const data = await res.json().catch(() => ({}));
      if (data.ok) {
        showOpToast('Issue #' + issueNum + ' closed');
        if ($convInput) $convInput.value = '';
        const $actionSel = document.getElementById('convInputIssueAction');
        if ($actionSel) $actionSel.value = 'spawn';
        renderIssueInConvPane(issueNum, repoPath);
        updateInputBar();
      } else {
        showOpToast('Close failed: ' + (data.error || 'HTTP ' + res.status), 'error');
      }
    } catch (err) {
      showOpToast('Close failed: ' + (err && err.message || 'network'), 'error');
    }
    if ($convSendBtn) $convSendBtn.disabled = false;
    if ($convInput) $convInput.focus();
  }

  async function replyToIssueFromInputBar(action, text) {
    const row = conversationsData.find(x => x.id === currentConversation) || {};
    const issueNum = row.issue_number || (currentConversation || '').replace('backlog-issue-', '');
    if (!issueNum) return;
    const conv = row.issue_number ? row : (conversationsData.find(x => x.id === 'backlog-issue-' + issueNum) || {});
    const repoPath = rowRepoPath(conv) || repoPathForIssueNumber(issueNum);
    if ($convSendBtn) $convSendBtn.disabled = true;
    try {
      const res = await fetch('/api/issues/' + issueNum + '/reply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(withRepoPath({ body: text, action }, repoPath)),
      });
      const data = await res.json().catch(() => ({}));
      if (data.ok) {
        const label = action === 'needs_attention' ? 'flagged needs-attention' : 'commented + closed';
        showOpToast('Issue #' + issueNum + ' ' + label);
        if ($convInput) $convInput.value = '';
        const $actionSel = document.getElementById('convInputIssueAction');
        if ($actionSel) $actionSel.value = 'spawn';
        renderIssueInConvPane(issueNum, repoPath);
        updateInputBar();
      } else {
        showOpToast('Reply failed: ' + (data.error || 'HTTP ' + res.status), 'error');
      }
    } catch (err) {
      showOpToast('Reply failed: ' + (err && err.message || 'network'), 'error');
    }
    if ($convSendBtn) $convSendBtn.disabled = false;
    if ($convInput) $convInput.focus();
  }

  async function spawnFromInlineInput(body) {
    function firstSentence(text) {
      const chunks = text.split(/(?<=[.!?])\s+|\n+/).map(s => s.trim()).filter(Boolean);
      const first = chunks[0] || text.trim();
      return first.length > 120 ? first.slice(0, 120).trim() + '...' : first;
    }
    const subject = firstSentence(body);
    const prompt = body + SESSION_STATE_INSTRUCTION;
    const engine = getSpawnEngine();
    const spawnCwd = (typeof getSpawnCwd === 'function') ? getSpawnCwd() : '';
    const repoPath = spawnCwd || selectedRepoPath();
    if (!repoPath) {
      showOpToast('New session needs a folder. Pick one from the cwd dropdown first.', 'error');
      populateSpawnCwdPicker();
      const sel = document.getElementById('spawnCwdPicker');
      if (sel) {
        try { sel.focus(); } catch (_) {}
      }
      return;
    }
    if ($convSendBtn) $convSendBtn.disabled = true;
    const flashRed = () => {
      if (!$convInput) return;
      $convInput.style.borderColor = 'var(--red)';
      setTimeout(() => { $convInput.style.borderColor = ''; }, 1500);
    };
    try {
      const endpoint = engine === 'codex' ? '/api/sessions/spawn-codex'
                     : engine === 'gemini' ? '/api/sessions/spawn-gemini'
                     : '/api/sessions/spawn';
      const $inlineWorktree = document.getElementById('inlineWorktreeToggle');
      const useWorktree = !!($inlineWorktree && $inlineWorktree.checked);
      const spawnBody = engine === 'codex'
        ? { prompt, name: subject, repo_path: repoPath, cwd: repoPath }
        : { prompt, name: subject, repo_path: repoPath, cwd: repoPath, worktree: useWorktree };
      const res = await fetch(endpoint, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(spawnBody),
      });
      const data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
      if (data.ok) {
        insertPendingSpawnCard(data.pid, subject, engine === 'codex' ? 'codex' : (engine === 'gemini' ? 'gemini' : 'interactive'), data.log);
        if ($convInput) $convInput.value = '';
        // Fire-and-watch engines need the right pane to switch to the new
        // placeholder so the spawn-log poller (`/api/sessions/spawned/<pid>/log`)
        // kicks in. Without this the pane stays on the "Spawning new session…"
        // empty state and the user can't see the agent running. Mirrors the
        // kanban-toolbar dispatch path.
        if ((engine === 'codex' || engine === 'gemini') && typeof selectConversation === 'function') {
          selectConversation('spawning-' + data.pid);
        } else {
          const $view = getConvView();
          if ($view) {
            $view.innerHTML = '<div class="empty-state" style="height:auto;padding:48px 32px;flex-direction:column;gap:10px;text-align:center;">'
              + '<div style="font-size:15px;color:var(--text);">Spawning new session…</div>'
              + '<div style="font-size:13px;color:var(--text-muted);max-width:480px;line-height:1.5;">It will appear in the sidebar in a moment — click it to follow along.</div>'
              + '</div>';
          }
        }
        setTimeout(refreshConversationList, 600);
        setTimeout(refreshConversationList, 1500);
        setTimeout(refreshConversationList, 3000);
      } else {
        flashRed();
        showOpToast('Spawn failed: ' + (data.error || 'HTTP ' + res.status), 'error');
        console.error('[New session] spawn failed', data);
      }
    } catch (err) {
      flashRed();
      showOpToast('Spawn failed: ' + (err && err.message || 'network'), 'error');
      console.error('[New session] submit error', err);
    }
    if ($convSendBtn) $convSendBtn.disabled = false;
    if ($convInput) $convInput.focus();
  }

  // ── Appearance picker (theme + font) ───────────────────────────
  // Persists to ccc-theme / ccc-font and applies via [data-theme] /
  // [data-font] attributes on <html>. The synchronous FOIT guard at
  // the top of <body> already applied the saved choice — this block
  // just wires the picker UI and live updates.
  const $appearanceBtn = document.getElementById('appearanceBtn');
  const $appearancePopover = document.getElementById('appearancePopover');
  const $settingsBtn = document.getElementById('settingsBtn');
  const $settingsPopover = document.getElementById('settingsPopover');
  const _systemThemeMQ = window.matchMedia('(prefers-color-scheme: light)');

  function getThemePref() { return localStorage.getItem('ccc-theme') || 'system'; }
  function getFontPref() { return localStorage.getItem('ccc-font') || 'system'; }

  function applyTheme(pref) {
    let resolved = pref;
    if (pref === 'system') {
      resolved = _systemThemeMQ.matches ? 'light' : 'dark';
    }
    if (resolved === 'light') {
      document.documentElement.setAttribute('data-theme', 'light');
    } else {
      document.documentElement.removeAttribute('data-theme');
    }
  }
  function applyFont(pref) {
    if (pref === 'mono') {
      document.documentElement.setAttribute('data-font', 'mono');
    } else {
      document.documentElement.removeAttribute('data-font');
    }
  }
  function refreshAppearanceChecks() {
    const t = getThemePref();
    const f = getFontPref();
    $appearancePopover.querySelectorAll('[data-check-theme]').forEach(el => {
      el.textContent = el.getAttribute('data-check-theme') === t ? '✓' : '';
    });
    $appearancePopover.querySelectorAll('[data-check-font]').forEach(el => {
      el.textContent = el.getAttribute('data-check-font') === f ? '✓' : '';
    });
  }
  // Live-update when the user has 'system' selected and OS theme flips.
  _systemThemeMQ.addEventListener && _systemThemeMQ.addEventListener('change', () => {
    if (getThemePref() === 'system') applyTheme('system');
  });

  // Generic popover open/close helper. Closes other popovers first so
  // only one is ever open at a time.
  function openOnlyPopover(target) {
    [$appearancePopover, $settingsPopover].forEach(p => {
      if (p && p !== target) p.classList.remove('open');
    });
    if (target) target.classList.toggle('open');
    if ($appearanceBtn) $appearanceBtn.setAttribute('aria-expanded', $appearancePopover && $appearancePopover.classList.contains('open') ? 'true' : 'false');
    if ($settingsBtn) $settingsBtn.setAttribute('aria-expanded', $settingsPopover && $settingsPopover.classList.contains('open') ? 'true' : 'false');
    if (target && target.classList.contains('open')) refreshAppearanceChecks();
  }

  if ($appearanceBtn) {
    $appearanceBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openOnlyPopover($appearancePopover);
    });
  }
  if ($settingsBtn) {
    $settingsBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openOnlyPopover($settingsPopover);
    });
  }
  if ($appearancePopover) {
    $appearancePopover.addEventListener('click', (e) => {
      const themeBtn = e.target.closest('[data-theme]');
      const fontBtn = e.target.closest('[data-font]');
      if (themeBtn) {
        const v = themeBtn.getAttribute('data-theme');
        localStorage.setItem('ccc-theme', v);
        applyTheme(v);
        refreshAppearanceChecks();
      } else if (fontBtn) {
        const v = fontBtn.getAttribute('data-font');
        localStorage.setItem('ccc-font', v);
        applyFont(v);
        refreshAppearanceChecks();
      }
    });
  }
  // Click-outside / Esc closes the popovers.
  document.addEventListener('click', (e) => {
    if ($appearancePopover && $appearancePopover.classList.contains('open')
        && !$appearancePopover.contains(e.target) && e.target !== $appearanceBtn
        && !$appearanceBtn.contains(e.target)) {
      $appearancePopover.classList.remove('open');
      if ($appearanceBtn) $appearanceBtn.setAttribute('aria-expanded', 'false');
    }
    if ($settingsPopover && $settingsPopover.classList.contains('open')
        && !$settingsPopover.contains(e.target) && e.target !== $settingsBtn
        && !$settingsBtn.contains(e.target)) {
      $settingsPopover.classList.remove('open');
      if ($settingsBtn) $settingsBtn.setAttribute('aria-expanded', 'false');
    }
  });
  // Initial state — already applied synchronously, but refresh checks
  // and re-apply (no-op) for clarity.
  applyTheme(getThemePref());
  applyFont(getFontPref());

  // ── ⌘K / ⌘P session search modal ──────────────────────────────
  const $cmdkModal = document.getElementById('cmdkModal');
  const $cmdkBackdrop = document.getElementById('cmdkBackdrop');
  const $cmdkInput = document.getElementById('cmdkInput');
  const $cmdkList = document.getElementById('cmdkList');
  let _cmdkItems = [];      // currently-rendered filtered list
  let _cmdkIndex = 0;       // selected row index

  function openCmdk() {
    if (!$cmdkModal) return;
    $cmdkModal.classList.add('open');
    $cmdkInput.value = '';
    renderCmdkList('');
    setTimeout(() => $cmdkInput.focus(), 20);
  }
  function closeCmdk() {
    if (!$cmdkModal) return;
    $cmdkModal.classList.remove('open');
  }
  function _cmdkLabel(c) {
    if (!c) return '';
    if (typeof stripTitle === 'function' && c.display_name) return stripTitle(c.display_name) || c.display_name;
    return c.display_name || c.id || '';
  }
  function renderCmdkList(query) {
    if (!$cmdkList) return;
    const q = (query || '').toLowerCase().trim();
    // Pull from the same in-memory store the sidebar uses; sort by mtime
    // (recency) and exclude archived / pending placeholders for clarity.
    const data = (Array.isArray(conversationsData) ? conversationsData : [])
      .filter(c => c && !c.archived && !c.pending);
    const matches = data.filter(c => {
      if (!q) return true;
      const name = (c.display_name || '').toLowerCase();
      const first = (c.first_message || '').toLowerCase();
      const branch = (c.branch || '').toLowerCase();
      return name.includes(q) || first.includes(q) || branch.includes(q);
    });
    matches.sort((a, b) => (b.modified || 0) - (a.modified || 0));
    _cmdkItems = matches.slice(0, 100);
    _cmdkIndex = 0;
    if (!_cmdkItems.length) {
      $cmdkList.innerHTML = '<div class="cmdk-empty">' + (q ? 'No sessions match.' : 'No sessions yet.') + '</div>';
      return;
    }
    const rows = _cmdkItems.map((c, i) => {
      const title = _cmdkLabel(c) || '(untitled)';
      const meta = [c.branch, c.source, c.first_message].filter(Boolean)
        .map(s => String(s).slice(0, 80)).join(' · ');
      return '<div class="cmdk-item' + (i === 0 ? ' selected' : '') + '" role="option" data-idx="' + i + '">'
        + '<div class="cmdk-title">' + escapeHtml(title) + '</div>'
        + (meta ? '<div class="cmdk-meta">' + escapeHtml(meta) + '</div>' : '')
        + '</div>';
    }).join('');
    $cmdkList.innerHTML = rows;
  }
  function moveCmdkSelection(delta) {
    if (!_cmdkItems.length) return;
    _cmdkIndex = (_cmdkIndex + delta + _cmdkItems.length) % _cmdkItems.length;
    const rows = $cmdkList.querySelectorAll('.cmdk-item');
    rows.forEach((r, i) => r.classList.toggle('selected', i === _cmdkIndex));
    const sel = rows[_cmdkIndex];
    if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: 'nearest' });
  }
  function commitCmdkSelection() {
    const c = _cmdkItems[_cmdkIndex];
    if (!c) return;
    closeCmdk();
    if (typeof selectConversation === 'function') selectConversation(c.id);
  }
  if ($cmdkInput) {
    $cmdkInput.addEventListener('input', (e) => renderCmdkList(e.target.value));
    $cmdkInput.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); moveCmdkSelection(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); moveCmdkSelection(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); commitCmdkSelection(); }
      else if (e.key === 'Escape') { e.preventDefault(); closeCmdk(); }
    });
  }
  if ($cmdkList) {
    $cmdkList.addEventListener('click', (e) => {
      const row = e.target.closest('.cmdk-item');
      if (!row) return;
      _cmdkIndex = parseInt(row.getAttribute('data-idx') || '0', 10);
      commitCmdkSelection();
    });
  }
  if ($cmdkBackdrop) $cmdkBackdrop.addEventListener('click', closeCmdk);

  // ── Global keyboard shortcuts ─────────────────────────────────
  // ⌘K / ⌘P → open search; ⌘\ → toggle conversation pane; ⌘N → new session.
  document.addEventListener('keydown', (e) => {
    const meta = e.metaKey || e.ctrlKey;
    if (meta && (e.key === 'k' || e.key === 'K' || e.key === 'p' || e.key === 'P')) {
      // Don't hijack the browser address bar combo when not focused on us.
      e.preventDefault();
      if ($cmdkModal && $cmdkModal.classList.contains('open')) closeCmdk();
      else openCmdk();
      return;
    }
    if (meta && e.key === '\\') {
      e.preventDefault();
      // Reuse cpCloseBtn handler (toggles when in kanban-split mode); fall
      // back to direct setConvPanelOpen for safety.
      if (typeof setConvPanelOpen === 'function') setConvPanelOpen(!convPanelOpen);
    }
  });

  // Settings popover: clicking the ⌘K row also opens the search modal.
  const $settingsCmdkBtn = document.getElementById('settingsCmdkBtn');
  if ($settingsCmdkBtn) {
    $settingsCmdkBtn.addEventListener('click', () => {
      if ($settingsPopover) $settingsPopover.classList.remove('open');
      openCmdk();
    });
  }

  // Init
  loadConversationList();
  attachAllPaneDropZones();

  // Wire the static p1 close button. Closing p1 when split is engaged:
  // keep p2 alive, slide its conversation/state into p1's slot so the
  // static-HTML element ids (#conversationsView, #convInputBar) stay live.
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
      if (splitState.panes.length < 2) return;
      const survivor = splitState.panes.find(p => p.id !== 'p1');
      if (!survivor) return;
      const survivorConvId = survivor.conversationId;
      // Tear down both panes' SSE before we mutate state.
      if (survivor.eventSource) { try { survivor.eventSource.close(); } catch (e) {} }
      if (splitState.panes[0].eventSource) {
        try { splitState.panes[0].eventSource.close(); } catch (e) {}
        splitState.panes[0].eventSource = null;
      }
      // Remove survivor's DOM (p1's static element stays put).
      const survivorEl = document.querySelector(`.conv-pane[data-pane-id="${survivor.id}"]`);
      if (survivorEl) survivorEl.remove();
      // Collapse splitState back to single-pane mode and reset p1's
      // per-pane data so selectConversation runs against a clean slate.
      splitState.panes.splice(1);
      splitState.orientation = null;
      splitState.activeIndex = 0;
      Object.assign(splitState.panes[0], _newPaneState('p1'));
      renderSplitLayout();
      // Open the survivor's conv in p1 — fully exercised code path
      // that refreshes every UI surface (transcript, sticky header,
      // workspace pills, session id, pkood polling, etc.).
      if (survivorConvId) selectConversation(survivorConvId, 'p1');
    });
  });

  pollVercelDeploy();
  setInterval(pollVercelDeploy, 15000);
})();
