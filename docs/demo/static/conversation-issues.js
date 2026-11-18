/* Canonical issue details and composer actions, with explicit live state. */
(function (root) {
  function createController({ state }) {
  async function spawnFromBacklogIssue(userText) {
    const row = (0, state.openConvRow)() || {};
    const issueNum = row.issue_number || (state.currentConversation || '').replace('backlog-issue-', '');
    if (!issueNum) return;
    const conv = row.issue_number ? row : ((state.conversationsData || []).find(x => x.id === 'backlog-issue-' + issueNum) || {});
    const repoPath = (0, state.rowRepoPath)(conv) || (0, state.repoPathForIssueNumber)(issueNum);
    const title = conv.display_name || conv.first_message || '';
    const cleanTitle = (title || '').replace(/^#\d+:\s*/, '').replace(/\[[^\]]*\]\s*/g, '').trim();
    const preamble = 'Fix issue #' + issueNum + ' - ' + cleanTitle
      + '\n\nRun `gh issue view ' + issueNum + '` for the full body (title may be truncated).';
    const body = preamble + '\n\n' + userText;
    const subject = 'issue-' + issueNum;
    const prompt = body;
    if (state.$convSendBtn) state.$convSendBtn.disabled = true;
    const flashRed = () => {
      if (!state.$convInput) return;
      state.$convInput.style.borderColor = 'var(--red)';
      setTimeout(() => { state.$convInput.style.borderColor = ''; }, 1500);
    };
    try {
      const res = await fetch('/api/sessions/spawn', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify((0, state.withRepoPath)({ prompt, name: subject, cwd: repoPath || undefined }, repoPath)),
      });
      const data = await res.json().catch(() => ({ ok: false, error: 'invalid JSON response' }));
      if (data.ok) {
        (0, state.insertPendingSpawnCard)(data.spawn_id || data.pid, subject, false, null, {
          first_message: body,
          repo_path: repoPath,
          folder_path: repoPath,
          spawn_cwd: repoPath,
          cwd: repoPath,
          session_cwd: repoPath,
          session_cwd_exists: true,
        });
        if (state.$convInput) state.$convInput.value = '';
        // Mirror kanban-start-btn: tell GitHub the issue is being worked on.
        fetch('/api/issues/' + issueNum + '/mark-in-progress', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify((0, state.withRepoPath)({}, repoPath)),
        }).catch(() => {});
        (0, state.clearInputDraftForConversation)(state.currentConversation);
        setTimeout(state.refreshConversationList, 600);
        // ...then chase with a cheap probe rather than two more full-corpus
        // refreshes: for Codex the engine has written nothing at 3s, and each
        // of those refreshes is a multi-megabyte fetch of every row we have.
        (0, state.chasePendingSpawn)((data.spawn_id || data.pid), { sessionId: data && data.session_id });
      } else {
        flashRed();
        (0, state.showOpToast)('Spawn failed: ' + (data.error || 'HTTP ' + res.status), 'error');
      }
    } catch (err) {
      flashRed();
      (0, state.showOpToast)('Spawn failed: ' + (err && err.message || 'network'), 'error');
    }
    if (state.$convSendBtn) state.$convSendBtn.disabled = false;
    if (state.$convInput) state.$convInput.focus();
  }

  async function closeIssueFromInputBar(action, text) {
    const row = (0, state.openConvRow)() || {};
    const issueNum = row.issue_number || (state.currentConversation || '').replace('backlog-issue-', '');
    if (!issueNum) return;
    const conv = row.issue_number ? row : ((state.conversationsData || []).find(x => x.id === 'backlog-issue-' + issueNum) || {});
    const repoPath = (0, state.rowRepoPath)(conv) || (0, state.repoPathForIssueNumber)(issueNum);
    const reason = action === 'not_planned' ? 'not planned' : action; // "not_planned" → "not planned"
    const body = { reason };
    if (action === 'duplicate') {
      const dupNum = text.replace(/[^0-9]/g, '');
      if (!dupNum) { (0, state.showOpToast)('Enter the duplicate issue number in the text field', 'error'); return; }
      body.duplicate_of = dupNum;
    }
    if (state.$convSendBtn) state.$convSendBtn.disabled = true;
    try {
      const res = await fetch('/api/issues/' + issueNum + '/close', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify((0, state.withRepoPath)(body, repoPath)),
      });
      const data = await res.json().catch(() => ({}));
      if (data.ok) {
        (0, state.showOpToast)('Issue #' + issueNum + ' closed');
        if (state.$convInput) state.$convInput.value = '';
        (0, state.clearInputDraftForConversation)(state.currentConversation);
        const $actionSel = document.getElementById('convInputIssueAction');
        if ($actionSel) $actionSel.value = 'spawn';
        renderIssueInConvPane(issueNum, repoPath);
        (0, state.updateInputBar)();
      } else {
        (0, state.showOpToast)('Close failed: ' + (data.error || 'HTTP ' + res.status), 'error');
      }
    } catch (err) {
      (0, state.showOpToast)('Close failed: ' + (err && err.message || 'network'), 'error');
    }
    if (state.$convSendBtn) state.$convSendBtn.disabled = false;
    if (state.$convInput) state.$convInput.focus();
  }

  async function replyToIssueFromInputBar(action, text) {
    const row = (0, state.openConvRow)() || {};
    const issueNum = row.issue_number || (state.currentConversation || '').replace('backlog-issue-', '');
    if (!issueNum) return;
    const conv = row.issue_number ? row : ((state.conversationsData || []).find(x => x.id === 'backlog-issue-' + issueNum) || {});
    const repoPath = (0, state.rowRepoPath)(conv) || (0, state.repoPathForIssueNumber)(issueNum);
    if (state.$convSendBtn) state.$convSendBtn.disabled = true;
    try {
      const res = await fetch('/api/issues/' + issueNum + '/reply', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify((0, state.withRepoPath)({ body: text, action }, repoPath)),
      });
      const data = await res.json().catch(() => ({}));
      if (data.ok) {
        const label = action === 'needs_attention' ? 'flagged needs-attention' : 'commented + closed';
        (0, state.showOpToast)('Issue #' + issueNum + ' ' + label);
        if (state.$convInput) state.$convInput.value = '';
        (0, state.clearInputDraftForConversation)(state.currentConversation);
        const $actionSel = document.getElementById('convInputIssueAction');
        if ($actionSel) $actionSel.value = 'spawn';
        renderIssueInConvPane(issueNum, repoPath);
        (0, state.updateInputBar)();
      } else {
        (0, state.showOpToast)('Reply failed: ' + (data.error || 'HTTP ' + res.status), 'error');
      }
    } catch (err) {
      (0, state.showOpToast)('Reply failed: ' + (err && err.message || 'network'), 'error');
    }
    if (state.$convSendBtn) state.$convSendBtn.disabled = false;
    if (state.$convInput) state.$convInput.focus();
  }

  async function renderIssueInConvPane(issueNum, repoPath, rowId) {
    // CCC-495: this can be invoked directly from a kanban card click while a
    // group-chat reader is open, bypassing selectConversation's teardown.
    // Without this, #convInputBar stays at the inline `style.display:none`
    // openGroupChatReader set — updateInputBar() only toggles the `.visible`
    // class, which can't override that inline style, so the composer would
    // stay invisible for the rest of the session.
    if (state._gcReaderInterval || state._gcReaderPath || state._gcReaderHiddenInputBar) {
      try { (0, state.stopGroupChatReader)({ rerenderSidebar: true }); } catch (_) {}
    }
    const $view = (0, state.getConvView)();
    (0, state.stopConvStream)();
    const concreteRepo = repoPath || (0, state.repoPathForIssueNumber)(issueNum);
    state.currentConversation = rowId || 'backlog-issue-' + issueNum;
    (0, state.refreshConversationBackgroundForPane)((0, state.activePaneId)());
    const issueRow = state.conversationsData.find(x => x.id === state.currentConversation)
      || state.conversationsData.find(x => x.id === 'backlog-issue-' + issueNum)
      || { source: 'backlog', issue_number: issueNum, display_name: 'Issue #' + issueNum };
    (0, state.updatePaneHeader)((0, state.activePaneId)(), issueRow, {
      category: (0, state.paneCategoryForRow)(issueRow) || 'issue',
      title: (0, state.paneTitleForRow)(issueRow) || ('Issue #' + issueNum),
    });
    // Drop any prior session so the bottom input bar routes to "spawn for
    // this issue" rather than injecting into the previously-viewed session.
    (0, state.setCurrentSession)(null, null, null, false, null);
    (0, state.updateInputBar)();
    (0, state.restoreComposerDraftForPane)((0, state.activePaneId)(), state.currentConversation);
    $view.innerHTML = '<div class="empty-state" style="height:auto;padding:40px;">Loading issue #' + issueNum + '...</div>';
    if (!concreteRepo) {
      $view.innerHTML = '<div class="empty-state" style="padding:40px;color:var(--red);">Pick a repo to load issue #' + (0, state.escapeHtml)(String(issueNum)) + '.</div>';
      return;
    }
    try {
      const res = await fetch((0, state.repoUrl)('/api/issues/' + issueNum + '/details', concreteRepo));
      const data = await res.json();
      if (!data.ok) {
        $view.innerHTML = '<div class="empty-state" style="padding:40px;color:var(--red);">Failed to load issue #' + issueNum + ': ' + (0, state.escapeHtml)(data.error || 'unknown') + '</div>';
        return;
      }
      const issue = data.issue || {};
      // Self-heal: if the fresh GH state disagrees with what the cached card says,
      // patch the in-memory card and re-render so it routes to the right column
      // (verified/archived) without waiting for the 5-min server cache to expire.
      try {
        const card = state.conversationsData.find(x => x.id === state.currentConversation)
          || state.conversationsData.find(x => x.id === 'backlog-issue-' + issueNum);
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
            (0, state.renderSidebar)(state.conversationsData);
          }
        }
      } catch (_) { /* render is a courtesy - never block detail render on failure */ }
      const labels = (issue.labels || []).map(l => '<span style="font-size:11px;padding:2px 8px;border-radius:10px;background:rgba(139,148,158,0.2);color:var(--text-muted);margin-right:6px;">' + (0, state.escapeHtml)(l.name || '') + '</span>').join('');
      const created = issue.createdAt ? new Date(issue.createdAt).toLocaleString() : '';
      const url = issue.url || '';
      let html = '<div style="padding:20px;max-width:900px;">';
      html += '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:12px;">';
      const issueTitle = (0, state.stripGhIssueProjectTag)(issue.title || '(no title)');
      (0, state.updatePaneHeader)((0, state.activePaneId)(), Object.assign({}, issueRow, {
        display_name: issueTitle,
        issue_number: issueNum,
        source: 'backlog',
      }), { category: (0, state.paneCategoryForRow)(issueRow) || 'issue', title: issueTitle + ' #' + issueNum });
      html += '<div><h1 style="margin:0 0 6px;font-size:20px;">' + (0, state.escapeHtml)(issueTitle) + ' <span style="color:var(--text-muted);font-weight:400;">#' + (0, state.escapeHtml)(String(issueNum)) + '</span></h1>';
      html += '<div style="font-size:12px;color:var(--text-muted);">' + (0, state.escapeHtml)((issue.author && issue.author.login) || '') + ' &middot; ' + (0, state.escapeHtml)(created) + ' &middot; <span style="color:' + (issue.state === 'OPEN' ? 'var(--green)' : 'var(--text-muted)') + ';">' + (0, state.escapeHtml)(issue.state || '') + '</span></div></div>';
      if (url) html += '<a href="' + (0, state.escapeHtml)(url) + '" target="_blank" rel="noopener" style="font-size:12px;color:var(--accent);">Open on GitHub &#x2197;</a>';
      html += '</div>';
      if (labels) html += '<div style="margin-bottom:16px;">' + labels + '</div>';
      if ((issue.state || '').toUpperCase() === 'OPEN') {
        html += '<div id="issueCloseActions" data-issue-num="' + (0, state.escapeHtml)(String(issueNum)) + '" style="display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap;">';
        html += '<button data-close-reason="completed" style="padding:6px 12px;font-size:12px;border-radius:6px;border:1px solid rgba(63,185,80,0.4);background:rgba(63,185,80,0.1);color:var(--green);cursor:pointer;font-weight:600;">Close as completed</button>';
        html += '<button data-close-reason="not planned" style="padding:6px 12px;font-size:12px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-muted);cursor:pointer;">Close as not planned</button>';
        html += '<button data-close-reason="duplicate" style="padding:6px 12px;font-size:12px;border-radius:6px;border:1px solid var(--border);background:transparent;color:var(--text-muted);cursor:pointer;">Close as duplicate</button>';
        html += '<span data-close-status style="font-size:12px;color:var(--text-muted);align-self:center;"></span>';
        html += '</div>';
      }
      html += '<div class="assistant-text" dir="auto" style="font-size:14px;line-height:1.55;">' + renderIssueMarkdown(issue.body || '') + '</div>';
      const comments = issue.comments || [];
      html += '<div style="margin:24px 0 0;padding-top:16px;border-top:1px solid var(--border);">';
      html += '<div style="font-size:12px;font-weight:600;text-transform:uppercase;color:var(--text-muted);margin-bottom:8px;">All comments (' + comments.length + ')</div>';
      if (comments.length) {
        html += '<div style="display:flex;flex-direction:column;gap:10px;">';
        for (const cm of comments) {
          const who = (cm.author && cm.author.login) || '';
          const when = cm.createdAt ? new Date(cm.createdAt).toLocaleString() : '';
          html += '<div style="padding:12px;border:1px solid var(--border);border-radius:6px;">';
          html += '<div style="font-size:12px;color:var(--text-muted);margin-bottom:6px;">' + (0, state.escapeHtml)(who) + ' &middot; ' + (0, state.escapeHtml)(when) + '</div>';
          html += '<div class="assistant-text" dir="auto" style="font-size:13px;line-height:1.5;">' + renderIssueMarkdown(cm.body || '') + '</div>';
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
      (0, state.updateConversationEndAffordance)($view);
      wireIssueCloseButtons($view, issueNum, concreteRepo);

      // Issue-view layout: route the issue header + close actions into the
      // right rail, hide the redundant inline copies in the body, and tag
      // the body so CSS can hide pieces irrelevant to issues (the
      // workspace context strip — branch / cwd / cost — doesn't apply).
      document.body.classList.add('is-issue-view');
      const $rail = document.getElementById('statusRail');
      const $railActions = document.getElementById('railActions');
      // Move the Close-as-* buttons to the rail — these are the actions
      // the user actually takes on an issue, so they belong with other
      // session-level actions, not buried at the top of the body.
      const $closeActions = $view.querySelector('#issueCloseActions');
      if ($closeActions && $railActions) {
        $railActions.appendChild($closeActions);
      }
      // Replace the rail's "Original ask" slot with an issue-header
      // (title + #N + GitHub link). The previous session's original-ask
      // (if any) was a stale leak; we wipe it. The reconciler in
      // `_applyStatusRailLayout` treats whatever node is in the rail as
      // the live one, so this synthetic .csh-ask-original is stable.
      if ($rail) {
        $rail.querySelectorAll('.csh-ask-original').forEach(n => n.remove());
        const $issueHeader = document.createElement('div');
        $issueHeader.className = 'csh-ask-original is-issue-header';
        const titleSafe = (0, state.escapeHtml)(issueTitle);
        const numSafe = (0, state.escapeHtml)(String(issueNum));
        const urlSafe = (0, state.escapeHtml)(url || '');
        let inner =
          '<div class="label">Issue</div>' +
          '<div class="user-msg" dir="auto">' +
            '<div class="ask-first">' + titleSafe + ' <span class="issue-num">#' + numSafe + '</span></div>';
        if (urlSafe) {
          inner += '<a class="ask-rest" href="' + urlSafe + '" target="_blank" rel="noopener">Open on GitHub &#x2197;</a>';
        }
        inner += '</div>';
        $issueHeader.innerHTML = inner;
        const $metadataPane = $rail.querySelector('#statusRailMetadataPane') || $rail;
        const $railActions = $metadataPane.querySelector('#railActions');
        if ($railActions) $metadataPane.insertBefore($issueHeader, $railActions);
        else $metadataPane.appendChild($issueHeader);
      }
      // Strip the now-duplicated title + GH-link from the body. They live
      // in the rail; keeping them in the body too would just be noise.
      const $bodyHeader = $view.querySelector('h1');
      if ($bodyHeader) {
        const $bodyHeaderRow = $bodyHeader.closest('div[style*="justify-content"]');
        if ($bodyHeaderRow) $bodyHeaderRow.remove();
        else $bodyHeader.remove();
      }
    } catch (e) {
      $view.innerHTML = '<div class="empty-state" style="padding:40px;color:var(--red);">Failed to load issue: ' + (0, state.escapeHtml)(String(e && e.message || e)) + '</div>';
    }
  }

  function _renderBacklogDetail(c) {
    const title = (c.backlog_type === 'github' || c.issue_number)
      ? (0, state.stripGhIssueProjectTag)(c.display_name || '')
      : (c.display_name || '');
    const body = c.first_message || '';
    const labels = (c.issue_labels || c.gh_labels || []).map(function(l) {
      return '<span class="conv-signal" style="margin-right:4px;background:rgba(139,148,158,0.15);color:var(--text-muted);">' + (0, state.escapeHtml)(l) + '</span>';
    }).join('');
    let stateChip = '';
    if (c.issue_state) {
      const reason = (c.issue_state_reason || '').toUpperCase();
      const isClosed = c.issue_state === 'CLOSED';
      const label = isClosed ? ('closed' + (reason ? ' · ' + reason.toLowerCase() : '')) : 'open';
      const bg = isClosed ? 'rgba(248,81,73,0.12)' : 'rgba(63,185,80,0.12)';
      const color = isClosed ? 'var(--red)' : 'var(--green)';
      stateChip = '<span class="conv-signal" style="background:' + bg + ';color:' + color + ';font-weight:600;text-transform:uppercase;letter-spacing:0.5px;">' + (0, state.escapeHtml)(label) + '</span>';
    }
    let issueLink = '';
    if (c.issue_number) {
      if (c.issue_url) {
        issueLink = '<a href="' + (0, state.escapeHtml)(c.issue_url) + '" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:12px;margin-left:auto;">View on GitHub ↗</a>';
      } else {
        // Single-repo backlog cards still rely on the active repo slug
        // from /api/config. Cross-repo cards carry issue_url directly.
        const repo = (window._cccConfig && window._cccConfig.repo) || '';
        if (repo) {
          issueLink = '<a href="https://github.com/' + (0, state.escapeHtml)(repo) + '/issues/' + (0, state.escapeHtml)(String(c.issue_number)) + '" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:12px;margin-left:auto;">View on GitHub ↗</a>';
        }
      }
    }
    const created = c.issue_created_at
      ? '<div style="font-size:11px;color:var(--text-muted);margin-top:4px;">Opened ' + (0, state.escapeHtml)(new Date(c.issue_created_at).toLocaleString()) + '</div>'
      : '';
    // Use the in-house renderMarkdown — handles headers, lists, tables,
    // inline code, fenced code blocks. Same renderer used for assistant
    // messages, so backlog body styling matches the rest of the app.
    const bodyHtml = body
      ? (0, state.renderMarkdown)(body)
      : '<em style="color:var(--text-muted);">(no body)</em>';
    return '<div class="backlog-detail" style="padding:24px 28px;max-width:780px;">'
      + '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">'
      +   stateChip
      +   labels
      +   issueLink
      + '</div>'
      + '<h2 style="margin:0 0 4px;font-size:18px;line-height:1.35;">' + (0, state.escapeHtml)(title) + '</h2>'
      +   created
      + '<div style="margin-top:18px;line-height:1.55;color:var(--text);">' + bodyHtml + '</div>'
      + '</div>';
  }

  function _renderGithubPrDetail(c) {
    const prNum = c.tail_pr_number;
    const prUrl = c.tail_pr_url;
    const title = c.display_name || c.ai_title || (prNum ? 'PR #' + prNum : 'Pull Request');
    const branch = c.worktree_label ? ('wt-' + c.worktree_label) : '';
    const ps = (c.pr_state || '').toUpperCase();
    let stateChip = '';
    if (ps === 'MERGED') {
      stateChip = '<span class="conv-signal pr-merged" style="font-weight:600;">✓ merged</span>';
    } else if (ps === 'CLOSED') {
      stateChip = '<span class="conv-signal pr-closed" style="font-weight:600;">× closed</span>';
    } else {
      stateChip = '<span class="conv-signal pr-open" style="font-weight:600;">↗ open</span>';
    }
    let notesHtml = '';
    if (Array.isArray(c.pr_notes)) {
      for (const note of c.pr_notes) {
        if (!note || !note.label) continue;
        const noteCls = note.kind === 'danger' ? 'pr-note-danger' : 'pr-note';
        notesHtml += '<span class="conv-signal ' + noteCls + '" title="' + (0, state.escapeAttr)(note.title || note.label) + '">' + (0, state.escapeHtml)(note.label) + '</span>';
      }
    }
    const ghLink = prUrl
      ? '<a href="' + (0, state.escapeHtml)(prUrl) + '" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;font-size:12px;margin-left:auto;">Open on GitHub ↗</a>'
      : '';
    const firstMsg = c.first_message
      ? '<div style="margin-top:18px;line-height:1.55;color:var(--text);">' + (0, state.renderMarkdown)(String(c.first_message).slice(0, 600)) + '</div>'
      : '';
    const lastSummary = c.last_assistant_text
      ? '<div style="margin-top:10px;font-size:12.5px;color:var(--text-muted);line-height:1.5;">' + (0, state.escapeHtml)(String(c.last_assistant_text).slice(0, 200)) + '…</div>'
      : '';
    return '<div class="backlog-detail" style="padding:24px 28px;max-width:780px;">'
      + '<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap;">'
      +   stateChip + notesHtml + ghLink
      + '</div>'
      + '<h2 style="margin:0 0 4px;font-size:18px;line-height:1.35;">' + (0, state.escapeHtml)(title) + '</h2>'
      + (prNum ? '<div style="font-size:12px;color:var(--text-muted);margin-bottom:4px;">PR #' + (0, state.escapeHtml)(String(prNum)) + (branch ? ' · ' + (0, state.escapeHtml)(branch) : '') + '</div>' : '')
      + firstMsg
      + lastSummary
      + '</div>';
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
            body: JSON.stringify((0, state.withRepoPath)(body, repoPath)),
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
            setTimeout(() => (0, state.refreshConversationList)(), 600);
            // Navigate to next backlog issue (or re-render current in closed state)
            setTimeout(() => {
              if (nextIssueNum) {
                renderIssueInConvPane(nextIssueNum, repoPath);
              } else {
                renderIssueInConvPane(issueNum, repoPath, state.currentConversation);
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

  function renderIssueMarkdown(md) {
    if (!md) return '<em style="color:var(--text-muted);">(no description)</em>';
    // Handle images first (before escape)
    const imgs = [];
    md = md.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, url) => {
      imgs.push({ alt, url });
      return '\u0000IMG' + (imgs.length - 1) + '\u0000';
    });
    let html = (0, state.renderMarkdown)(md);
    html = html.replace(/\u0000IMG(\d+)\u0000/g, (m, idx) => {
      const { alt, url } = imgs[+idx];
      return '<img src="' + (0, state.escapeHtml)(url) + '" alt="' + (0, state.escapeHtml)(alt) + '" style="max-width:100%;border-radius:6px;margin:8px 0;" loading="lazy">';
    });
    return html;
  }
    return { spawnFromBacklogIssue, closeIssueFromInputBar, replyToIssueFromInputBar, renderIssueInConvPane, _renderBacklogDetail, _renderGithubPrDetail, wireIssueCloseButtons, renderIssueMarkdown };
  }
  root.CCCConversationIssues = Object.freeze({ createController });
})(typeof window === 'undefined' ? globalThis : window);
